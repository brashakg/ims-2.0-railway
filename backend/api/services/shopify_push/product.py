"""Shopify push -- product

The product push: `push_product` (create/update, metafields,
variant seeding, photos, publish) and `push_product_delist`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from agents.nexus_providers import _as_shopify_gid

from ._shared import (
    MODE_BLOCKED,
    MODE_LIVE,
    MODE_SIMULATED,
    PRICE_NOT_SYNCED,
    _PRICE_NOT_SYNCED_MSG,
    PushResult,
    _blocked_result,
    _live_or_reason,
    is_variant_of,
    price_on_update_enabled,
    push_lock_reason,
)
from .transport import _graphql, _user_errors
from .queries import _PRODUCT_CREATE, _PRODUCT_UPDATE
from .product_input import (
    _has_publishable_price,
    _set_product_metafields,
    _variants_for_price_push,
    build_product_input,
    build_product_metafields,
    build_variant_price_inputs,
    ims_product_tags,
)
from .tags import plan_product_tags, sync_product_tags
from .variants import (
    _needs_repair,
    _seed_variants_after_write,
    plan_variant_seed,
    push_variant_prices,
)
from .publish import _publish_to_online_store
from .inventory import (
    _set_variant_tracking,
    plan_product_stock,
    resolve_online_location_id,
    set_inventory_quantities,
    sync_product_stock,
    zero_stock_ledger_entry,
)
from .media import plan_product_media, product_photo_urls, sync_product_media
from .writeback import _requeue_unpublished, _writeback_product

# ===========================================================================
# PUSH FUNCTIONS -- one per entity. DARK by default; LIVE only behind the gates.
# ===========================================================================


async def push_product(
    db,
    product: Dict[str, Any],
    variants: Optional[List[Dict[str, Any]]] = None,
    blocked: Optional[bool] = None,
) -> PushResult:
    """Push a catalog product (+ its ecom sub-doc + variants) to Shopify.

    DARK by default -> returns a SIMULATED dry-run plan with the full ProductInput
    and NO network call. LIVE only when all three gates pass: then productCreate
    (no stored gid) or productUpdate (gid present), with the new gid written back
    for idempotency. Never raises.

    ``blocked`` is an OPTIONAL precomputed block classification:
      * ``None`` (default, single-push route): classify HERE via the STRICT
        variant. An UNKNOWN (block config unreadable) FAILS CLOSED -- the push is
        skipped so a DB blip can never let a contractually-banned product reach
        Shopify (finding #18).
      * ``True`` / ``False``: supplied by the /all-pending sweep, which resolves
        the blocked set ONCE for the whole batch (finding #20 -- no per-product
        re-scan). The sweep passes ``None`` (not ``False``) when it could not
        verify the config, so this fail-closed skip still applies."""
    pid = product.get("id") or product.get("product_id")
    # Hub Phase 5: push-lock is the FIRST gate -- a locked brand is NEVER pushed,
    # before the dark/live gate (fail-closed).
    _lock = push_lock_reason(db, "product", product)
    if _lock:
        return _blocked_result("product", pid, _lock)
    # VARIANT-OF (owner ruling 2026-09-06): a size variant owns NO listing. Its
    # price, barcode and stock ride the PARENT's push (push_variant_prices /
    # sync_product_stock over the parent's catalog_variants rows); every
    # listing-level field is the parent's alone. Refused HERE, the one
    # chokepoint every product-push door funnels through -- before the block
    # classifier, the photo refusal, productCreate/productUpdate, tags,
    # metafields, seeding, media, stock and publish -- so no path, dry-run or
    # live, can ever mint a second listing for a size. Same result shape as
    # the no_photo refusal, so the screen renders it the same way.
    if is_variant_of(product):
        link = (product.get("ecom") or {}).get("variant_of") or {}
        parent = link.get("sku") if isinstance(link, dict) else link
        return PushResult(
            mode=MODE_BLOCKED,
            entity="product",
            action="skip",
            target_id=pid,
            ok=False,
            error=(
                f"size variant of {parent}: its price, barcode and stock ride "
                "the parent's listing -- push the parent"
            ),
            reason="variant_of",
        )
    # SUPERADMIN "block collection from online" (BVI-retirement): a product that
    # belongs to AT LEAST ONE online_sync_blocked collection is a HARD block --
    # it must NEVER be created/updated on Shopify regardless of its other
    # (unblocked) collection memberships (a brand ban wins). This single guard is
    # the ONE chokepoint, so BOTH the per-product push route AND the /all-pending
    # product sweep are covered. FAIL-CLOSED: the strict classifier returns None
    # (UNKNOWN) on a block-config read error and we SKIP the push (never a false
    # 'clean' that ships a banned product -- finding #18). Delisting an
    # already-synced blocked product is done separately by push_product_delist,
    # which is NOT gated here (it IS the block action).
    if blocked is None:
        try:
            from ..online_block import is_blocked_from_online_strict

            blocked = is_blocked_from_online_strict(product, db)
        except Exception:  # noqa: BLE001 -- classifier must never break a push
            blocked = None
    if blocked is None:
        return PushResult(
            mode=MODE_BLOCKED,
            entity="product",
            action="skip",
            target_id=pid,
            ok=False,
            error="block status unverifiable (block-config read error) -- "
            "push skipped (fail-closed)",
            reason="block_status_unverifiable",
        )
    if blocked:
        return PushResult(
            mode=MODE_BLOCKED,
            entity="product",
            action="skip",
            target_id=pid,
            ok=False,
            error="blocked from online (member of an online_sync_blocked collection)",
            reason="online_sync_blocked",
        )
    variants = variants or []
    ecom = product.get("ecom") or {}

    # THE PHOTO RULE (owner ruling 2026-08-25): "Refuse -- no photo, no
    # publish." A listing with a name, a price and an empty grey box is worse
    # for the brand than absence. Since a press now PUBLISHES (see
    # build_product_input), a push with no photograph is refused outright,
    # BEFORE the dark/live branch -- so there is no path, dry-run or live, on
    # which a photo-less product reaches Shopify at all. The row is NOT
    # de-queued (nothing here writes back), so adding a photo and pressing
    # again ships it.
    photos = product_photo_urls(product)
    if not photos:
        return PushResult(
            mode=MODE_BLOCKED,
            entity="product",
            action="skip",
            target_id=pid,
            ok=False,
            shopify_id=ecom.get("shopify_product_id"),
            error="refused: no photograph -- a product with no photograph is "
            "never published to the storefront",
            reason="no_photo",
        )

    existing_gid = ecom.get("shopify_product_id")
    payload = build_product_input(product, variants)
    # THE TAGS (sync audit gap #4): the create input carries the full list;
    # an update carries none -- the diff against what IMS last sent runs
    # after the write (tags.sync_product_tags), so hand-added admin tags
    # survive. Computed once so the plan, the input and the ledger agree.
    ims_tags = ims_product_tags(product)
    # Attribute -> metafield side channel (owner 2026-07-05): planned in the
    # dry-run, upserted via metafieldsSet after a LIVE product write succeeds.
    metafields = build_product_metafields(product)
    action = "update" if existing_gid else "create"
    # full_reseed: touch EVERY variant row (CREATE always does; an UPDATE only
    # when the owner explicitly opted in). repair_only: an UPDATE that is NOT
    # a full reseed, but at least one row/the pseudo-variant has never been
    # seeded -- restricted to ONLY that unseeded subset (#955 fix-round:
    # _needs_repair is ROW-aware so a partially-harvested product keeps
    # getting a chance to finish, and build_repair_seed_rows/repair_only=True
    # keep an already-seeded row from ever being re-touched by the repair).
    full_reseed = action == "create" or price_on_update_enabled()
    repair_only = (not full_reseed) and _needs_repair(product, variants)

    live, reason = _live_or_reason(db)
    if not live:
        # Variant price/barcode plan rides on the dry-run too (owner priority:
        # a price change must be visibly part of the push plan). Covers the
        # product-level pseudo-variant of a no-variant-row product (OS-016).
        vp_plan = None
        vp_variants = _variants_for_price_push(product, variants)
        if vp_variants:
            vp_rows, vp_skipped = build_variant_price_inputs(product, vp_variants)
            vp_plan = {"variants": vp_rows, "skipped_no_gid_or_price": vp_skipped}
        # The CREATE-side seeding plan (price + SKU for the variants Shopify will
        # mint) rides on the dry-run too, so the owner can SEE the money before
        # anything goes live. On an update it appears when the opt-in flag is on
        # (every row) OR when the product still needs repair (only the
        # still-unseeded row(s)) -- mirroring exactly what the LIVE branch
        # below would actually do.
        seed_plan = None
        if full_reseed:
            seed_plan = plan_variant_seed(product, variants)
        elif repair_only:
            seed_plan = plan_variant_seed(product, variants, repair_only=True)
        return PushResult(
            mode=MODE_SIMULATED,
            entity="product",
            action=action,
            target_id=pid,
            ok=True,
            shopify_id=existing_gid,
            payload=payload,
            reason=reason,
            metafields=metafields or None,
            variant_prices=vp_plan,
            variants_seeded=seed_plan,
            stock=plan_product_stock(db, product, variants),
            photos=plan_product_media(product, photos),
            tags=plan_product_tags(product, ims_tags),
        )

    query = _PRODUCT_UPDATE if existing_gid else _PRODUCT_CREATE
    field_name = "productUpdate" if existing_gid else "productCreate"
    try:
        body = await _graphql(db, query, {"input": payload})
        err = _user_errors(body, field_name)
        if err:
            return PushResult(
                mode=MODE_LIVE,
                entity="product",
                action=action,
                target_id=pid,
                ok=False,
                payload=payload,
                error=err,
            )
        prod = ((body.get("data") or {}).get(field_name) or {}).get("product") or {}
        new_gid = prod.get("id") or existing_gid
        if not new_gid:
            # A 200 with no errors, no userErrors AND no product id. Outside
            # Shopify's documented contract, but everything downstream is gated
            # on the gid -- metafields, variant seeding, the photo attach and
            # the publish block all skip -- so pub_summary stays None and
            # published_ok would collapse to True: a green "1 processed" over a
            # product that does not exist. The row IS left queued, so the next
            # press creates it properly; this only stops the press LYING about
            # the one that failed. "Processed means visible" is the invariant
            # this whole change rests on, and it has no exceptions.
            return PushResult(
                mode=MODE_LIVE,
                entity="product",
                action=action,
                target_id=pid,
                ok=False,
                reason="no_product_id",
                payload=payload,
                error=(
                    "Shopify returned success but no product -- nothing was "
                    "created. The product is still queued; press again."
                ),
            )
        if new_gid and pid:
            _writeback_product(db, pid, new_gid)
        # TAGS, RIGHT AFTER THE WRITE. The update sent no `tags`, so the
        # response's tag list is what Shopify holds now; the pass adds /
        # removes only the tags IMS itself sent and records the ledger.
        # Fail-soft: reported on the result, never flips ok, never withholds
        # the publish.
        tag_summary = None
        if new_gid:
            tag_summary = await sync_product_tags(
                db, product, new_gid, ims_tags, prod.get("tags")
            )
        # Metafields ride AFTER the product write so the gid always exists.
        # Fail-soft: their errors are reported on the result, never flip ok.
        mf_summary = None
        if metafields and new_gid:
            mf_summary = await _set_product_metafields(db, new_gid, metafields)
        # VARIANT SEEDING -- the price-0.00 / no-SKU fix. ProductInput carries
        # neither, so on a CREATE the variants Shopify just minted are priced +
        # SKU'd here and their gids written back -- BOTH the ProductVariant gid
        # (the price push's handle) and the InventoryItem gid (the oversell-
        # guard stock write-back's target: catalog_variants.
        # shopify_inventory_item_id per row, ecom.shopify_inventory_item_id for
        # a no-variant-row product). On an UPDATE the FULL reseed only runs
        # when the owner opts in (SHOPIFY_PUSH_PRICE_ON_UPDATE); otherwise a
        # ROW-AWARE repair-only pass runs whenever ANY row still lacks a gid
        # (_needs_repair), restricted to ONLY that unseeded subset
        # (repair_only=True -> build_repair_seed_rows) so an already-seeded
        # row can never be re-priced/re-touched by the repair (#955
        # fix-round: the previous product-wide any-gid check permanently
        # masked a partially-harvested product's remaining stranded row from
        # ever being retried). A fully-seeded product is left untouched, so
        # the ~4,400 already-live products are never silently re-priced.
        # Idempotent + fail-soft.
        seed_summary = None
        seeded = False
        variant_nodes = ((prod.get("variants") or {}).get("nodes")) or []
        if new_gid and (full_reseed or repair_only):
            seed_summary = await _seed_variants_after_write(
                db, product, variants, new_gid, variant_nodes,
                repair_only=repair_only,
            )
            seeded = seed_summary is not None
            if seed_summary and seed_summary.get("default_variant_gid") and pid:
                _writeback_product(
                    db,
                    pid,
                    new_gid,
                    variant_gid=seed_summary["default_variant_gid"],
                    # Only ever set for the product-level pseudo-variant (the
                    # product has NO catalog_variants rows) -- the resolver's
                    # documented ecom fallback. None otherwise, which leaves
                    # ecom untouched (set-only).
                    inventory_item_gid=seed_summary.get(
                        "product_level_inventory_item_gid"
                    ),
                )
        # THE PHOTOGRAPHS, IN THIS SAME PRESS. "Has a photo in IMS" and "has a
        # photo on Shopify" are different questions, and only the second one
        # protects the storefront -- photographs used to push on a SEPARATE,
        # LATER press (push_image over the design queue), so a product could go
        # visible before its photo arrived. The refusal above proved IMS has a
        # photograph; this puts it on Shopify before anything is published.
        #
        # Sync audit gap #3 (owner 2026-09-06): the pass now DIFFS IMS's photo
        # list against the media IMS owns on Shopify (read straight off the
        # create/update response's media selection -- no extra query) --
        # attaching what is missing, deleting what IMS dropped, reordering to
        # IMS order -- instead of attaching only onto a bare product. The
        # ownership rule (media.py) keeps hand-uploaded media untouched.
        existing_media = ((prod.get("media") or {}).get("nodes")) or []
        photo_summary = None
        if new_gid:
            photo_summary = await sync_product_media(
                db, product, new_gid, photos, existing_media
            )
        photo_on_shopify = bool((photo_summary or {}).get("on_shopify"))
        # STOCK, IN THIS SAME PRESS, BEFORE THE PUBLISH (owner ruling
        # 2026-09-07 -- the website sells only what the shops can ship). Every
        # variant gid this press knows -- the response nodes, whatever seeding
        # just created, the stored ones -- gets tracked=true + the DENY policy,
        # and each SKU's pooled quantity is written at the online location.
        # Fail-soft side channel: reported on the result and the audit row,
        # never flips ok and never withholds the publish (first-publish
        # behaviour is unchanged; the stock pass retries it on the next sync).
        stock_summary = None
        if new_gid:
            stock_summary = await sync_product_stock(
                db,
                product,
                variants,
                new_gid,
                extra_variant_gids=[n.get("id") for n in variant_nodes if isinstance(n, dict)]
                + list((seed_summary or {}).get("variant_gids") or []),
            )
        # SALES-CHANNEL PUBLISH -- the third shut door. An ACTIVE product
        # published to NO channel is invisible on bettervision.in. This used to
        # be gated behind SHOPIFY_PUBLISH_ON_CREATE (default OFF) AND restricted
        # to a CREATE, so it effectively never ran -- and re-pressing one of the
        # rows already mapped to Shopify (all of which got there as DRAFTs) could
        # never make it visible. Owner ruling 2026-08-25 "one press, goes live":
        # it now runs on every product push, create or update. publishablePublish
        # is idempotent, so re-publishing an already-published product is a no-op.
        #
        # PUBLISH PRECONDITION (adversarial-panel must-fix 1, still enforced):
        # publishing is WITHHELD unless the price is provably right, because
        # seeding is fail-SOFT (a bulk-update userError or a priceless product
        # still leaves ok=True) and a 0.00 listing is worse than no listing.
        #   * a press that SEEDED (create, or an update that repaired/reseeded):
        #     the seeding must have run clean AND priced at least one row.
        #   * a press that needed NO seeding: every variant already carries the
        #     gid an earlier successful seed wrote, so its price is already on
        #     Shopify -- but IMS must still hold a positive price for every row.
        pub_summary = None
        if new_gid and payload.get("status") == "ACTIVE":
            if seed_summary is not None:
                priced_ok = (
                    not seed_summary.get("errors")
                    and int(seed_summary.get("priced_rows") or 0) > 0
                )
            elif full_reseed or repair_only:
                # Seeding was due but produced nothing to seed -- no price and
                # no SKU anywhere. Never publish that.
                priced_ok = False
            else:
                priced_ok = _has_publishable_price(product, variants)
            if priced_ok and photo_on_shopify:
                pub_summary = await _publish_to_online_store(db, new_gid)
                if pub_summary.get("published") and pid:
                    # IMS must agree with the storefront (see _writeback_product
                    # `status`): the DRAFT/PUBLISHED cards and every
                    # storefront-visibility helper read ecom.status.
                    _writeback_product(db, pid, new_gid, status="PUBLISHED")
            elif not photo_on_shopify:
                # The attach is fail-soft, so a media error must not leave the
                # product VISIBLE without its photograph -- that is exactly the
                # grey box the rule exists to prevent.
                pub_summary = {
                    "published": False,
                    "error": "publish withheld: the photograph did not reach Shopify",
                }
            else:
                pub_summary = {
                    "published": False,
                    "error": "publish withheld: variant unpriced or seeding failed",
                }
        # The press reached Shopify but the product is NOT visible. Leave it in
        # the queue so pressing again retries it once the price / photograph is
        # fixed -- see _requeue_unpublished for why this is not ping-pong.
        #
        # AND SAY SO. "One press, goes live" makes visibility the definition of
        # success, so a press whose publish was withheld did NOT do what it was
        # pressed for and must never come back ok. Reporting ok=True here was
        # the owner's original bug one layer down: with the publication
        # unresolvable a sweep answered "pushed: 5, failed: 0" over five
        # invisible products and the toast was green. The withholding gets its
        # OWN reason (never `failed`, which would read as a Shopify breakage)
        # so the sweep buckets and shows it exactly like refused_no_photo.
        published_ok = pub_summary is None or bool(pub_summary.get("published"))
        # AN ARCHIVED ROW IS NOT A LISTING. The Shopify write succeeded and the
        # retirement is deliberate, so this is neither a failure nor a
        # withholding -- but no shopper can find the product, and "N processed"
        # is rendered to the owner as "these are live on bettervision.in now".
        # This is the last path where `pushed` would not mean `visible`, so it
        # gets its own reason and its own line, exactly like a refusal.
        archived_not_listed = published_ok and payload.get("status") == "ARCHIVED"
        # Variant price/barcode push rides after the product write too (same
        # fail-soft side-channel contract: an error is reported on the result,
        # never flips the product push's ok). push_variant_prices never raises.
        # Skipped when the seeding step above already wrote these exact prices.
        # Also covers a no-variant-row product's default variant (via the
        # stored ecom.shopify_variant_id pseudo-variant) so a queued price
        # change is actually CARRIED by the update push (OS-016).
        vp_summary = None
        if _variants_for_price_push(product, variants) and new_gid and not seeded:
            vp_product = dict(product)
            vp_ecom = dict(vp_product.get("ecom") or {})
            vp_ecom["shopify_product_id"] = new_gid
            vp_product["ecom"] = vp_ecom
            vp_res = await push_variant_prices(db, vp_product, variants)
            vp_summary = {
                "ok": vp_res.ok,
                "action": vp_res.action,
                "pushed": len((vp_res.payload or {}).get("variants") or []),
                "skipped_no_gid_or_price": (vp_res.payload or {}).get(
                    "skipped_no_gid_or_price", 0
                ),
                "error": vp_res.error,
            }
        # THE PRICE IS PART OF THE PRESS (sync audit gap #7). A failed price
        # push used to ride out silently: the product write-back had already
        # cleared locally_modified, the result said ok, and the website kept
        # selling at the OLD price with nothing left in the queue to retry it
        # -- the twice-daily live sync selects by that flag, so it never saw
        # the product again. ok stays True (the product IS live), but the row
        # is re-queued and the result carries PRICE_NOT_SYNCED so the sync
        # page / audit say so and the next press or scheduled run retries.
        price_not_synced = bool(vp_summary) and not vp_summary["ok"]
        # THE ONE RE-QUEUE RULE. The press reached Shopify but did not do all
        # it was pressed for -- the product is not visible, or it is visible at
        # the wrong price. Either way the row goes BACK in the queue so the next
        # press / scheduled sync retries it. See _requeue_unpublished for why
        # this is not the ping-pong hazard.
        if pid and (not published_ok or price_not_synced):
            _requeue_unpublished(db, pid)
        return PushResult(
            mode=MODE_LIVE,
            entity="product",
            action=action,
            target_id=pid,
            ok=published_ok,
            shopify_id=new_gid,
            payload=payload,
            error=(
                (_PRICE_NOT_SYNCED_MSG if price_not_synced else None)
                if published_ok
                else (
                    (pub_summary or {}).get("message")
                    or (pub_summary or {}).get("error")
                    or "publish withheld"
                )
            ),
            code=(
                (PRICE_NOT_SYNCED if price_not_synced else None)
                if published_ok
                else (pub_summary or {}).get("code")
            ),
            reason=(
                ("archived_not_listed" if archived_not_listed else None)
                if published_ok
                else "publish_withheld"
            ),
            metafields=mf_summary,
            variant_prices=vp_summary,
            variants_seeded=seed_summary,
            publication=pub_summary,
            photos=photo_summary,
            stock=stock_summary,
            tags=tag_summary,
        )
    except Exception as e:  # noqa: BLE001 -- fail-soft, never propagate
        return PushResult(
            mode=MODE_LIVE,
            entity="product",
            action=action,
            target_id=pid,
            ok=False,
            payload=payload,
            error=str(e),
        )


async def push_product_delist(db, product: Dict[str, Any]) -> PushResult:
    """DELIST a product from the Shopify storefront: set its Shopify status to
    DRAFT (unpublished / not sellable). Used by the SUPERADMIN "block collection
    from online" cutover to take an already-synced, now-blocked product OFF the
    storefront.

    REVERSIBLE by design: the Shopify product is NEVER deleted and its gid is
    KEPT, so putting it back is a normal push (and can never mint a duplicate
    listing). Unlike push_product this is NOT gated by
    is_blocked_from_online (it IS the block action). Obeys the same three dark
    gates: SIMULATED plan when dark, LIVE productUpdate only behind the gates.
    Only acts when the product already carries a Shopify gid (else a clean noop --
    nothing to delist). Never raises.

    AFTER A SUCCESSFUL LIVE DELIST the IMS row is brought into line with the
    storefront (owner ruling 2026-08-25 -- take-down is the reversibility that
    makes one-press publishing survivable):
      * ecom.status -> DRAFT. ecom.status is READ in six places (the Online
        Store screen's DRAFT/PUBLISHED cards, the storefront-visibility
        helpers); leaving it PUBLISHED would have IMS insisting a product is
        on a storefront it was just pulled from. This does NOT re-shut the
        publish door: build_product_input maps everything except ARCHIVED to
        ACTIVE, so pressing publish again puts it straight back.
      * ecom.locally_modified -> False. Without this a taken-down product
        that happened to be dirty would be RESURRECTED by the very next
        sweep, seconds later. Taking one down has to stick until a human
        asks for it back.
    Both live in HERE rather than in the caller because both callers -- the
    block cutover and the take-down button -- need the same truth: this row
    is off the storefront.

    The gid is deliberately re-written unchanged: _writeback_product is
    set-only for the mapping, so the take-down can never lose the Shopify id
    (losing it would make the next push CREATE A DUPLICATE live product)."""
    pid = product.get("id") or product.get("product_id")
    ecom = product.get("ecom") or {}
    existing_gid = ecom.get("shopify_product_id")
    if is_variant_of(product):
        # A size variant has no listing to draft, and a manual take-down of
        # ONE size has no persistent marker of its own: the next stock pass
        # would put it straight back (its spine is still active, so the
        # quantity rule reports its units). So this door REPORTS instead of
        # writing: the ONE way to stop selling a size is to deactivate the
        # product (online_delist.delist_if_live -> _delist_variant_row, where
        # is_active is the marker and the quantity rule lists 0), or to take
        # down the parent. Zero network. NEVER productUpdate on the parent.
        return PushResult(
            mode=MODE_SIMULATED,
            entity="product",
            action="noop",
            target_id=pid,
            ok=True,
            reason="variant_of",
            error=(
                "size variant rides the parent's listing -- deactivate the "
                "product to stop selling this size, or take down the parent"
            ),
        )
    if not existing_gid:
        # Not on Shopify -> nothing to take down (a clean no-op, not an error).
        return PushResult(
            mode=MODE_SIMULATED,
            entity="product",
            action="noop",
            target_id=pid,
            ok=True,
            reason="not on Shopify -- nothing to delist",
        )
    payload: Dict[str, Any] = {
        "id": _as_shopify_gid(existing_gid, "Product"),
        "status": "DRAFT",
    }

    live, reason = _live_or_reason(db)
    if not live:
        return PushResult(
            mode=MODE_SIMULATED,
            entity="product",
            action="delist",
            target_id=pid,
            ok=True,
            shopify_id=existing_gid,
            payload=payload,
            reason=reason,
        )

    try:
        body = await _graphql(db, _PRODUCT_UPDATE, {"input": payload})
        err = _user_errors(body, "productUpdate")
        if err:
            return PushResult(
                mode=MODE_LIVE,
                entity="product",
                action="delist",
                target_id=pid,
                ok=False,
                shopify_id=existing_gid,
                payload=payload,
                error=err,
            )
        if pid:
            _writeback_product(db, pid, existing_gid, status="DRAFT")
        return PushResult(
            mode=MODE_LIVE,
            entity="product",
            action="delist",
            target_id=pid,
            ok=True,
            shopify_id=existing_gid,
            payload=payload,
        )
    except Exception as e:  # noqa: BLE001 -- fail-soft, never propagate
        return PushResult(
            mode=MODE_LIVE,
            entity="product",
            action="delist",
            target_id=pid,
            ok=False,
            shopify_id=existing_gid,
            payload=payload,
            error=str(e),
        )



async def _delist_variant_row(db, product: Dict[str, Any]) -> PushResult:
    """Take ONE size variant off sale WITHOUT touching the parent's listing:
    inventoryPolicy DENY + quantity 0 on the child's own Shopify variant (the
    parent's listing stays ACTIVE, every other size keeps selling). The
    retire hook's door for a variant-of product (online_delist.delist_if_live
    when the child spine's is_active flips off) -- is_active is then the ONLY
    marker, and the quantity rule (online_stock_writeback._on_hand_for_skus:
    an inactive spine lists 0) keeps every later stock pass at 0 until the
    product is reactivated. NEVER productUpdate, never a status change.

    Reads the bridge, never a second link: the child's catalog_variants row
    by ``sku`` gives the variant + inventory-item gids AND parent_product_id
    (the parent twin), whose ecom.shopify_product_id is the productId the
    bulk-update needs. Any of the three missing -> the same clean noop as an
    un-pushed product (nothing on Shopify to take down). DARK -> SIMULATED
    plan, zero network. LIVE -> the two existing stock primitives
    (_set_variant_tracking, set_inventory_quantities) at the resolved online
    location, then the child's entry in the PARENT's stock ledger is zeroed
    so a reactivation (pooled 1 vs sent 0) diffs and is re-sent. Fail-soft."""
    pid = product.get("id") or product.get("product_id")
    sku = str(product.get("sku") or "").strip()
    row: Dict[str, Any] = {}
    parent_gid = None
    parent_twin_id = None
    try:
        row = (db["catalog_variants"].find_one({"sku": sku}) if sku else None) or {}
        parent_twin_id = row.get("parent_product_id")
        parent = (
            db["catalog_products"].find_one({"id": parent_twin_id}) if parent_twin_id else None
        ) or {}
        parent_gid = (parent.get("ecom") or {}).get("shopify_product_id")
    except Exception as exc:  # noqa: BLE001 -- a lookup blip is reported, never raised
        return PushResult(
            mode=MODE_SIMULATED,
            entity="variant",
            action="delist",
            target_id=pid,
            ok=False,
            error=f"variant lookup failed: {exc}",
        )
    variant_gid = row.get("shopify_variant_id")
    inventory_item_gid = row.get("shopify_inventory_item_id")
    if not (parent_gid and variant_gid and inventory_item_gid):
        return PushResult(
            mode=MODE_SIMULATED,
            entity="variant",
            action="noop",
            target_id=pid,
            ok=True,
            reason="not on Shopify -- nothing to delist",
        )
    variant_gid = _as_shopify_gid(variant_gid, "ProductVariant")
    payload: Dict[str, Any] = {
        "productId": _as_shopify_gid(parent_gid, "Product"),
        "variantId": variant_gid,
        "inventoryItemId": _as_shopify_gid(inventory_item_gid, "InventoryItem"),
        "inventoryPolicy": "DENY",
        "quantity": 0,
    }
    live, reason = _live_or_reason(db)
    if not live:
        return PushResult(
            mode=MODE_SIMULATED,
            entity="variant",
            action="delist",
            target_id=pid,
            ok=True,
            shopify_id=variant_gid,
            payload=payload,
            reason=reason,
        )
    try:
        loc = await resolve_online_location_id(db)
        if not loc.get("location_id"):
            return PushResult(
                mode=MODE_LIVE,
                entity="variant",
                action="delist",
                target_id=pid,
                ok=False,
                shopify_id=variant_gid,
                payload=payload,
                code=loc.get("code"),
                error=loc.get("error"),
            )
        payload["locationId"] = loc["location_id"]
        tracked = await _set_variant_tracking(db, payload["productId"], [variant_gid], "DENY")
        written = await set_inventory_quantities(
            db, loc["location_id"], {payload["inventoryItemId"]: 0}
        )
        errors = list(tracked.get("errors") or []) + list(written.get("errors") or [])
        if errors:
            return PushResult(
                mode=MODE_LIVE,
                entity="variant",
                action="delist",
                target_id=pid,
                ok=False,
                shopify_id=variant_gid,
                payload=payload,
                error="; ".join(str(e) for e in errors[:3]),
            )
        if parent_twin_id and sku:
            zero_stock_ledger_entry(db, parent_twin_id, sku)
        return PushResult(
            mode=MODE_LIVE,
            entity="variant",
            action="delist",
            target_id=pid,
            ok=True,
            shopify_id=variant_gid,
            payload=payload,
        )
    except Exception as e:  # noqa: BLE001 -- fail-soft, never propagate
        return PushResult(
            mode=MODE_LIVE,
            entity="variant",
            action="delist",
            target_id=pid,
            ok=False,
            shopify_id=variant_gid,
            payload=payload,
            error=str(e),
        )

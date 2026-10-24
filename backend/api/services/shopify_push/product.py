"""Shopify push -- product

The product push: `push_product` (create/update, metafields,
variant seeding, photos, publish) and `push_product_delist`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from agents.nexus_providers import _as_shopify_gid

from ._shared import (
    MODE_BLOCKED,
    MODE_LIVE,
    MODE_SIMULATED,
    PRICE_NOT_SYNCED,
    _PRICE_NOT_SYNCED_MSG,
    PHOTOS_NOT_SYNCED,
    PHOTOS_SYNC_FAILED,
    _PHOTOS_NOT_SYNCED_MSG,
    PushResult,
    _blocked_result,
    _live_or_reason,
    is_variant_of,
    price_on_update_enabled,
    product_push_refusal,
)
from ..catalog_ecom import EDIT_REV
from ..web_page_type import effective_page_type
from .transport import ShopifyOutcomeUnknown, _graphql, _user_errors
from .create_claim import (
    CREATE_ID_NOT_SAVED,
    CREATE_UNCERTAIN,
    adopt_if_on_shopify,
    identity_metafield,
    prepare_identity,
    resolve_held,
    settle_claim,
    take_claim,
)
from .queries import _PRODUCT_CREATE, _PRODUCT_UPDATE
from .listing import FAMILY_UNREADABLE, HELD, listing_view, model_listing, plan_summary
from .product_input import (
    _has_publishable_price,
    _set_product_metafields,
    _set_variant_metafields,
    unpriced_size_names,
    _variants_for_price_push,
    build_product_input,
    build_product_metafields,
    build_removed_metafields,
    build_variant_price_inputs,
    ims_product_tags,
    with_spine_weights,
)
from .tags import plan_product_tags, sync_product_tags
from .variants import (
    _needs_repair,
    _row_has_gid,
    _seed_variants_after_write,
    all_variant_nodes,
    plan_variant_seed,
    push_variant_prices,
)
from .publish import _publish_to_online_store
from .inventory import (
    STOCK_TRACKING_FAILED,
    _set_variant_tracking,
    inventory_policy_for,
    listing_visible,
    plan_product_stock,
    push_skus_stock,
    sku_by_variant_gid,
    sync_product_stock,
)
from .media import (
    VARIANT_PHOTOS_PENDING,
    plan_product_media,
    product_photo_urls,
    sync_product_media,
    sync_variant_media,
)
from .writeback import _requeue_unpublished, _writeback_product, _writeback_variant

# A first publish / reseed / repair refused because a size has no price (SF-05).
UNPRICED_SIZE = "UNPRICED_SIZE"
# How many runs a listing whose photo step keeps failing is re-queued for.
PHOTO_RETRY_CAP = 6

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
    # FIRST gate, before the dark/live gate (fail-closed): a push-locked brand,
    # or a brand Brand Master keeps off the website (owner D6, read live).
    _refusal = product_push_refusal(db, product)
    if _refusal:
        return _blocked_result("product", pid, _refusal)
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
    raw_variants = variants  # what the caller read: a re-entry re-derives the model
    ecom = product.get("ecom") or {}

    # ONE LISTING PER MODEL (G2, owner 2026-10-18). A lead of colours / sizes
    # pushes as its MODEL: every member a variant (lead first), the options
    # its members vary on, the model's title / SEO / body / photos / tags.
    # Unreadable -> refused (never a one-colour listing by accident); a model
    # whose new variant could not be told apart or has no option value ->
    # refused before anything is sent (listing.model_listing).
    listing = None
    try:
        listing = model_listing(db, product, variants)
    except Exception as exc:  # noqa: BLE001 -- fail-closed
        return PushResult(
            mode=MODE_BLOCKED,
            entity="product",
            action="skip",
            target_id=pid,
            ok=False,
            shopify_id=ecom.get("shopify_product_id"),
            error=(
                f"refused: this product's colours and sizes could not be read ({exc}) -- "
                "nothing was sent; press again"
            ),
            reason="publish_withheld",
            code=FAMILY_UNREADABLE,
        )
    if listing is not None and listing.get("variant_of"):
        return PushResult(
            mode=MODE_BLOCKED,
            entity="product",
            action="skip",
            target_id=pid,
            ok=False,
            error=(
                f"variant of {listing['variant_of']}: its price, barcode and stock ride "
                "its model's listing -- push the model"
            ),
            reason="variant_of",
        )
    if listing is not None:
        if listing["refusal"]:
            code, message = listing["refusal"]
            return PushResult(
                mode=MODE_BLOCKED,
                entity="product",
                action="skip",
                target_id=pid,
                ok=False,
                shopify_id=ecom.get("shopify_product_id"),
                error=message,
                reason="publish_withheld",
                code=code,
                listing=plan_summary(listing),
            )
        product = listing_view(product, listing)
        variants = listing["rows"]
        ecom = product.get("ecom") or {}
    listing_plan = plan_summary(listing) if listing is not None else None
    # THE WEIGHT (grams) of every variant, read off its SPINE -- the one copy
    # (the twin holds none): the seeding rows and the price push both carry
    # it as inventoryItem.measurement.weight, only when one is known.
    product = with_spine_weights(db, product, variants)

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
    payload = build_product_input(product, variants, page_type=effective_page_type(db, product))
    # THE TAGS (sync audit gap #4): the create input carries the full list;
    # an update carries none -- the diff against what IMS last sent runs
    # after the write (tags.sync_product_tags), so hand-added admin tags
    # survive. Computed once so the plan, the input and the ledger agree.
    ims_tags = ims_product_tags(product)  # a model's: the union (listing_view)
    # Attribute -> metafield side channel (owner 2026-07-05): planned in the
    # dry-run, upserted via metafieldsSet after a LIVE product write succeeds.
    # A model's listing carries what EVERY colour shares; the rest rides
    # each variant (listing.model_listing).
    metafields = listing["metafields"] if listing is not None else build_product_metafields(product)
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
    # The rows already on Shopify as the press starts (a repair press re-sends
    # only THEIR price -- see the price step).
    pre_seeded = {id(v) for v in variants if _row_has_gid(v, ecom)}
    # EVERY SIZE PRICED (audit SF-05, owner 18 Oct 2026). A press that
    # creates or (re)seeds sizes -- first publish, full reseed, repair -- is
    # REFUSED before any write, dry-run or live, while any size has no
    # positive selling price: a size seeded without a price sits at 0.00 on
    # Shopify. One rule (unpriced_size_names, the same one the no-seeding
    # publish gate below uses); nothing is written back, so the row stays
    # queued and pricing the size + pressing again ships it.
    # ponytail: refuses the WHOLE press (title/photo edits of a live listing
    # wait too) -- seeding only the priced sizes would split the one rule.
    # A LIVE model listing: a colour at fault was already held back by
    # model_listing (review of #1233 -- one bad colour never freezes a live
    # listing), so only the variants this press SEEDS are asked here.
    seed_targets = variants
    if listing is not None and existing_gid:
        seed_targets = [
            v for v in variants
            if not v.get(HELD) and (full_reseed or not _row_has_gid(v, ecom))
        ]
    unpriced = unpriced_size_names(product, seed_targets) if (full_reseed or repair_only) else []
    if unpriced:
        return PushResult(
            mode=MODE_BLOCKED,
            entity="product",
            action="skip",
            target_id=pid,
            ok=False,
            shopify_id=existing_gid,
            error=(
                "refused: every size needs a selling price before it goes on the "
                f"website -- no price on {', '.join(unpriced)}. Nothing was sent; "
                "price these sizes and press again"
            ),
            # Counted with the other "not put on the website" presses (never
            # `failed` -- the operator can act on it), told apart by its code.
            reason="publish_withheld",
            code=UNPRICED_SIZE,
        )

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
            stock=await plan_product_stock(db, product, variants),
            photos=plan_product_media(product, photos),
            tags=plan_product_tags(product, ims_tags),
            listing=listing_plan,
        )

    query = _PRODUCT_UPDATE if existing_gid else _PRODUCT_CREATE
    field_name = "productUpdate" if existing_gid else "productCreate"
    # Asked BEFORE any write-back touches the row: is this listing on the
    # website as the press starts? (A create never is.)
    was_live = bool(existing_gid) and listing_visible(product)
    # A listing NOT on the website goes on only once Shopify's answer has
    # confirmed tracking on every size (the root rule below). A listing taken
    # down is Draft on Shopify with its Online Store publication kept, so an
    # ACTIVE in this first write would put it back on sale before the
    # tracking re-send is even answered: it is written Draft here, and ACTIVE
    # goes out with the publish, once everything is confirmed. (A create is on
    # no sales channel until publishablePublish.)
    hold_active = bool(existing_gid) and not was_live and payload.get("status") == "ACTIVE"
    send_input = {**payload, "status": "DRAFT"} if hold_active else payload
    # The edit revision this press is sending (SF-04): the write-backs clear
    # the queue flag only while the row still carries it.
    sent_rev = ecom.get(EDIT_REV)
    # THE FIRST-PUBLISH CLAIM (SF-02, create_claim.py): one create per
    # product, ever, unless Shopify is shown not to have it.
    claim = None
    if not existing_gid and pid:
        # The unique identity definition goes in before the first value does
        # (once per process; never raises).
        await prepare_identity(db)
        got = None
        try:
            verdict, claim = take_claim(db, pid)
            if verdict == "held":
                verdict, got = await resolve_held(db, pid, claim)
        except Exception as exc:  # noqa: BLE001 -- no claim, no create
            return PushResult(
                mode=MODE_LIVE,
                entity="product",
                action=action,
                target_id=pid,
                ok=False,
                payload=payload,
                error=f"could not record the send before creating it ({exc}) -- nothing was sent",
            )
        adopted = got if verdict == "adopted" else None
        if verdict == "refuse":
            return PushResult(
                mode=MODE_LIVE,
                entity="product",
                action=action,
                target_id=pid,
                ok=False,
                payload=payload,
                code=got[0],
                error=got[1],
            )
        if verdict == "send" and got is not None:
            claim = got  # a settled claim this press took over
        if verdict in ("has_id", "adopted"):
            # The listing exists (another press made it, or an earlier send
            # whose answer was lost): carry on as an UPDATE of that listing.
            fresh = db["catalog_products"].find_one({"id": pid}) or product
            res = await push_product(db, fresh, raw_variants, blocked=blocked)
            if adopted and adopted[1] > 1:
                res.error = (
                    (res.error + " -- ALSO: ") if res.error else ""
                ) + (
                    f"Shopify holds {adopted[1]} copies of this product from earlier "
                    "sends; IMS linked the first -- delete the others in Shopify admin"
                )
            return res
        send_input = {**send_input, "metafields": [identity_metafield(pid)]}
    try:
        try:
            body = await _graphql(db, query, {"input": send_input})
        except ShopifyOutcomeUnknown as exc:
            settle_claim(db, pid, claim, uncertain=True)
            return PushResult(
                mode=MODE_LIVE,
                entity="product",
                action=action,
                target_id=pid,
                ok=False,
                payload=payload,
                code=CREATE_UNCERTAIN,
                error=(
                    f"{exc}. The product stays queued; the next press looks for "
                    "it on Shopify first and links it if it is there"
                ),
            )
        except Exception:
            settle_claim(db, pid, claim, uncertain=False)
            raise
        err = _user_errors(body, field_name)
        made = ((body.get("data") or {}).get(field_name) or {}).get("product")
        if err and not existing_gid and body.get("errors") and not made:
            # A 200 carrying top-level errors and no product: Shopify may
            # still have run it. Keep the claim -- the next press looks first.
            settle_claim(db, pid, claim, uncertain=True)
            return PushResult(
                mode=MODE_LIVE,
                entity="product",
                action=action,
                target_id=pid,
                ok=False,
                payload=payload,
                code=CREATE_UNCERTAIN,
                error=(
                    f"Shopify answered with errors and no product ({err}), so IMS "
                    "cannot tell whether it was created. Nothing was sent again; the "
                    "next press looks for it on Shopify first"
                ),
            )
        if err:
            settle_claim(db, pid, claim, uncertain=False)
            if not existing_gid and pid and await adopt_if_on_shopify(db, pid):
                # Refused because this product IS already on Shopify (the
                # unique identity says so): link it and carry on as an update.
                fresh = db["catalog_products"].find_one({"id": pid}) or product
                return await push_product(db, fresh, raw_variants, blocked=blocked)
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
            settle_claim(db, pid, claim, uncertain=False)
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
            saved = _writeback_product(
                db, pid, new_gid, sent_rev=sent_rev, clear_claim=claim is not None,
                template_suffix=payload.get("templateSuffix"),
            )
            if claim and not saved:
                # Made on Shopify, link not saved: the claim stays, so the next
                # press finds this listing and links it -- never a second one.
                return PushResult(
                    mode=MODE_LIVE,
                    entity="product",
                    action=action,
                    target_id=pid,
                    ok=False,
                    shopify_id=new_gid,
                    payload=payload,
                    code=CREATE_ID_NOT_SAVED,
                    error=(
                        "created on Shopify, but IMS could not save the link to it. "
                        "Nothing more was sent; the next press finds this listing on "
                        "Shopify and links it instead of creating another"
                    ),
                )
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
        # A removed manufacturer barcode also leaves its ims.* metafield (only
        # an existing product can hold one).
        if listing is not None:
            removed = listing["removed_metafields"] if existing_gid else []
        else:
            removed = build_removed_metafields(product) if existing_gid else []
        mf_summary = None
        if (metafields or removed) and new_gid:
            mf_summary = await _set_product_metafields(db, new_gid, metafields, removed)
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
        # EVERY variant of the listing, page by page (G2: never truncated at
        # the first 100). A page that could not be read stops any create.
        variant_nodes, nodes_complete = (
            await all_variant_nodes(db, new_gid, prod.get("variants") or {}) if new_gid else ([], True)
        )
        if new_gid and (full_reseed or repair_only):
            seed_summary = await _seed_variants_after_write(
                db, product, variants, new_gid, variant_nodes,
                repair_only=repair_only,
                # A live listing's options: a new colour/size first gets the
                # option it is chosen by (G2). None (not read) -> as before.
                listing_options=prod.get("options") if existing_gid else None,
                nodes_complete=nodes_complete,
            )
            seeded = seed_summary is not None
            if seed_summary and seed_summary.get("default_variant_gid") and pid:
                _writeback_product(
                    db,
                    pid,
                    new_gid,
                    sent_rev=sent_rev,
                    variant_gid=seed_summary["default_variant_gid"],
                    # Only ever set for the product-level pseudo-variant (the
                    # product has NO catalog_variants rows) -- the resolver's
                    # documented ecom fallback. None otherwise, which leaves
                    # ecom untouched (set-only).
                    inventory_item_gid=seed_summary.get(
                        "product_level_inventory_item_gid"
                    ),
                )
        # A SIZE CREATED ON A LIVE LISTING is on sale the moment it exists. Its
        # create call carried tracked + the stop-at-0 policy; when Shopify's
        # answer does not confirm both, the size may sell without limit, so the
        # listing comes OFF the website (Draft) before anything else is written
        # and the press says so. The take-down marks it taken down, so no sweep
        # re-lists it; the next human press re-sends tracking to every size.
        new_unconfirmed = list((seed_summary or {}).get("unconfirmed_variant_gids") or [])
        take_down = None
        if new_unconfirmed and was_live:
            take_down = await push_product_delist(db, product)
        # Off the website ONLY when the Draft went out LIVE and Shopify took
        # it. A refused one -- or one that ran dark because a gate input (the
        # creds) went soft mid-press, a SIMULATED ok with nothing sent --
        # leaves the listing on the website.
        taken_down = take_down is not None and take_down.ok and take_down.mode == MODE_LIVE
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
        # EACH COLOUR'S OWN (G2): its first photo on its variant (choosing the
        # colour shows it) and its own attributes -- lens colour, polarization,
        # materials, its maker barcode -- as metafields on its variant. Both
        # fail-soft; a photo not attached re-queues the listing (below).
        variant_photo_summary = None
        listed_rows = variants
        if listing is not None and new_gid:
            # A lead whose row predates its listing's ids (or has no row):
            # the row now carries the listing's default variant, as the
            # seeding write-back would have written it.
            for r in variants:
                if r.get("_listing_set") and r.get("shopify_variant_id") and id(r) in pre_seeded:
                    _writeback_variant(db, r, r["shopify_variant_id"], r.get("shopify_inventory_item_id"))
            gid_by_sku = {
                str(r.get("sku")): _as_shopify_gid(r["shopify_variant_id"], "ProductVariant")
                for r in variants
                if r.get("sku") and r.get("shopify_variant_id")
            }
            gid_by_sku.update((seed_summary or {}).get("gid_by_sku") or {})
            variant_photo_summary = await sync_variant_media(
                db, product, new_gid, listing["variant_photos"], gid_by_sku, variant_nodes
            )
            if photo_summary is not None and variant_photo_summary is not None:
                photo_summary["variants"] = variant_photo_summary
            variant_mfs = await _set_variant_metafields(
                db,
                {
                    gid_by_sku[sku]: rows
                    for sku, rows in listing["variant_metafields"].items()
                    if rows and gid_by_sku.get(sku)
                },
            )
            mf_summary = {**(mf_summary or {}), "variants": variant_mfs}
            # A colour this press could not add is no part of the listing's
            # stock (it has no Shopify item; the press already said why).
            listed_rows = [r for r in variants if r.get("shopify_variant_id") or str(r.get("sku")) in gid_by_sku]
        # STOCK, IN THIS SAME PRESS, BEFORE THE PUBLISH (owner ruling
        # 2026-09-07 -- the website sells only what the shops can ship). Every
        # variant gid this press knows -- the response nodes, whatever seeding
        # just created, the stored ones -- gets tracked=true + the DENY policy,
        # and each SKU's own on-hand PER SHOP is written at that shop's Shopify
        # location (one row per mapped shop, an explicit 0 included).
        # Fail-soft side channel: reported on the result and the audit row.
        # A QUANTITY failure never flips ok and never withholds the publish
        # (the stock pass retries it on the next sync); a TRACKING failure
        # withholds it -- see `tracking_ok` below.
        stock_summary = None
        if new_gid:
            stock_summary = await sync_product_stock(
                db,
                product,
                listed_rows,
                new_gid,
                extra_variant_gids=[n.get("id") for n in variant_nodes if isinstance(n, dict)]
                + list((seed_summary or {}).get("variant_gids") or [])
                + new_unconfirmed,
                answered=(seed_summary or {}).get("tracking_verdicts"),
            )
        # THE ROOT RULE (owner, rounds 4 + 6) -- one rule for every press, first
        # or re-press, new size or old: a listing is put or kept ON the website
        # only when Shopify's answer confirmed tracked=true + the listing's
        # policy on EVERY size. The stock pass re-sent both to every size and
        # read each size's verdict, remembered per size: an answer echoing a
        # size untracked / on another policy unconfirms it; a refused or
        # throttled re-send keeps what Shopify last confirmed (the listing
        # stays live, the next stock pass re-sends); a size never confirmed is
        # not. Nothing below keeps a test of its own.
        tracking_ok = bool((stock_summary or {}).get("tracking_confirmed"))
        unconfirmed = new_unconfirmed + [
            g for g in (stock_summary or {}).get("tracking_unconfirmed") or [] if g not in new_unconfirmed
        ]
        # Live and not confirmed -> OFF the website (Draft), whatever the size.
        if was_live and not tracking_ok and take_down is None:
            take_down = await push_product_delist(db, product)
            taken_down = take_down.ok and take_down.mode == MODE_LIVE
        # A take-down that did not land, while the re-send's answer confirmed
        # every size after all: the listing is live AND tracked -- the normal
        # path below, never "STILL LIVE".
        if take_down is not None and not taken_down and tracking_ok:
            take_down = None
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
        #   * EVERY press: IMS must hold a positive price for EVERY size
        #     (_has_publishable_price -- the one rule; a seeding press with an
        #     unpriced size was already refused before any write, SF-05).
        #   * a press that SEEDED (create, or an update that repaired/reseeded):
        #     the seeding must also have run clean.
        #   * TRACKED + THE POLICY BEFORE PUBLISH (design 4.2, the root rule
        #     above): the listing is published only when `tracking_ok` --
        #     Shopify's answer confirmed every size. Anything less and the
        #     listing stays OFF the website (Draft, see `hold_active`); the
        #     result is ok=False / publish_withheld with the stock pass's own
        #     code and line, and the row stays queued for the retry. A LIVE
        #     listing never reaches this branch unconfirmed: it is taken down.
        pub_summary = None
        if take_down is not None:
            # Each size by its IMS SKU, never a Shopify id.
            names_of = {**sku_by_variant_gid(product, variants), **((seed_summary or {}).get("size_names") or {})}
            names = ", ".join(sorted({names_of.get(g) or "a new size" for g in unconfirmed})) or "its sizes"
            rule = "stop selling at 0" if inventory_policy_for(db, product) == "DENY" else "keep selling at 0"
            why = f"Shopify did not confirm stock tracking + {rule} on {names}"
            pub_summary = {
                "published": False,
                "code": STOCK_TRACKING_FAILED,
                "error": (
                    f"{why} -- the listing was taken OFF the website (Draft) so they "
                    f"cannot oversell; press Send to website again"
                ) if taken_down else (
                    f"{why}, and taking the listing off the website FAILED "
                    f"({take_down.error or take_down.reason}) -- it is STILL LIVE and "
                    f"they may sell without limit; set it to Draft in Shopify admin now"
                ),
                # Off the website = withheld; still live = a failure, never
                # "not made visible".
                "reason": "publish_withheld" if taken_down else None,
            }
        elif new_gid and payload.get("status") == "ACTIVE":
            # ONE price rule for every press (SF-05): every size priced. A
            # seeding press must also have seeded clean.
            # A colour held back (no price of its own) keeps its last price
            # on a live listing: it never withholds the rest (review of #1233).
            priced_ok = _has_publishable_price(product, [v for v in variants if not v.get(HELD)]) and not (
                seed_summary or {}
            ).get("errors")
            if seed_summary is None and (full_reseed or repair_only):
                # Seeding was due but produced nothing to seed. Never publish that.
                priced_ok = False
            if priced_ok and photo_on_shopify and tracking_ok:
                pub_summary = await _publish_to_online_store(db, new_gid)
                if pub_summary.get("published") and hold_active:
                    # The ACTIVE the first write held back -- only now, every
                    # size confirmed, priced and photographed, and only after
                    # the publication (a Draft stays off the website).
                    pub_summary = await _set_active(db, new_gid, pub_summary)
                if pub_summary.get("published") and pid:
                    # IMS must agree with the storefront (see _writeback_product
                    # `status`): the DRAFT/PUBLISHED cards and every
                    # storefront-visibility helper read ecom.status.
                    _writeback_product(db, pid, new_gid, status="PUBLISHED", sent_rev=sent_rev)
            elif not photo_on_shopify:
                # The attach is fail-soft, so a media error must not leave the
                # product VISIBLE without its photograph -- that is exactly the
                # grey box the rule exists to prevent.
                pub_summary = {
                    "published": False,
                    "error": "publish withheld: the photograph did not reach Shopify",
                }
            elif not priced_ok:
                pub_summary = {
                    "published": False,
                    "error": "publish withheld: variant unpriced or seeding failed",
                }
                held = (seed_summary or {}).get("held")
                if held:
                    # G2: a new colour / size that could not be added says why.
                    pub_summary["error"] += f" -- {held}"
                    pub_summary["code"] = (seed_summary or {}).get("code")
            else:
                # Priced and photographed, but tracking + DENY did not stick:
                # the stock pass's own line and code, under "withheld".
                pub_summary = {
                    "published": False,
                    "code": STOCK_TRACKING_FAILED,
                    "error": (
                        f"publish withheld -- the listing stays OFF the website (Draft): "
                        f"{(stock_summary or {}).get('error')}"
                    ),
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
        # A REPAIR press seeded only the NEW sizes / colours: the ones that were
        # already on the listing before it still take today's price, exactly
        # as in a press with no seeding (G2: a price edit in the same press as
        # a new colour must not be dropped). The ones it just seeded are not
        # sent twice.
        vp_rows = variants
        if seeded and repair_only:
            vp_rows = [v for v in variants if id(v) in pre_seeded]
        if (
            new_gid
            and (not seeded or (repair_only and vp_rows))
            and _variants_for_price_push(product, vp_rows)
        ):
            vp_product = dict(product)
            vp_ecom = dict(vp_product.get("ecom") or {})
            vp_ecom["shopify_product_id"] = new_gid
            vp_product["ecom"] = vp_ecom
            vp_res = await push_variant_prices(db, vp_product, vp_rows)
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
        # ...AND SO IS THE STOCK (same rule, same shape). The stock pass is a
        # fail-soft side channel that never withholds the publish -- but on a
        # fresh catalogue with no shop mapped yet it returns STORE_UNMAPPED
        # having written nothing, while the press still switched tracking on
        # with the DENY policy and published. That is a listing LIVE on
        # bettervision.in reading sold out at every location, and every screen
        # said it worked: nothing renders PushResult.stock, and formatPushResult
        # reads only the top-level ok / code / error. ok stays True (the product
        # IS live, exactly like PRICE_NOT_SYNCED) but the code and the plain
        # line come out where the toast, the audit row and the sync page read
        # them. No re-queue: the stock diff still sees this product as changed
        # (nothing reached its baseline), so the next pass retries it.
        stock_not_written = bool(stock_summary) and not stock_summary.get("ok")
        # ...AND SO ARE THE PHOTOS (owner 2026-10-12: photo 1 is the main photo
        # on the website). A failed attach / delete / reorder is reported only
        # in PushResult.photos, and the product write-back above already
        # cleared the queue flag -- so a reorder that did not land was never
        # sent again and the website kept the old photo 1. Same rule as the
        # price: live, so ok stays True, but re-queued with its own code.
        photo_errors = [
            x for x in (photo_summary, variant_photo_summary) if (x or {}).get("error")
        ]
        photos_not_synced = bool(photo_errors)
        # ONLY "Shopify is still processing" (or gave no answer) is worth
        # waiting for (review of #1277). A refusal -- the 250-media limit, a
        # photo Shopify could not fetch, an attach / delete / reorder / colour
        # userError -- never fixes itself, so it is red on the first press
        # and the line carries Shopify's own reason.
        photos_transient = photos_not_synced and all(_photo_error_is_transient(x) for x in photo_errors)
        photo_cause = "; ".join(str(x["error"]) for x in photo_errors)
        # ...BUT NOT FOR EVER (review of #1233): a photo step that keeps
        # failing re-queues the listing for PHOTO_RETRY_CAP runs, then the
        # press says so and stops re-queueing it (a person must look). A
        # clean photo pass resets the count.
        photo_line = (
            _PHOTOS_NOT_SYNCED_MSG
            if photos_transient
            else f"Shopify refused the photos ({photo_cause}) -- check the photographs; the next sync retries them."
        )
        photo_requeue = photos_not_synced
        tries = 0
        if pid and new_gid:
            tries = _count_photo_tries(db, pid, failed=photos_not_synced)
            if photos_transient and tries >= 2:
                photo_line = (
                    f"Photos did not finish after {tries} sync runs ({photo_cause}) -- "
                    "check the photographs; the next sync retries them."
                )
            if photos_not_synced and tries >= PHOTO_RETRY_CAP:
                photo_requeue = False
                photo_line = (
                    f"Photos did not finish ({photo_cause}) -- tried {tries} times; it is no longer "
                    "re-queued for its photos: check the photographs, then press Send to website"
                )
        photo_failed = photos_not_synced and (not photos_transient or tries >= 2)
        photo_summary = {
            **(photo_summary or {}),
            "sync_state": "failed" if photo_failed else "pending" if photos_not_synced else "synced",
            "consecutive_incomplete": tries,
            "message": photo_line if photos_not_synced else None,
            "variant_error": (variant_photo_summary or {}).get("error"),
        }
        # A colour held back from this LIVE listing (no price, no colour or
        # size, looks the same as another): named by SKU, the rest was sent.
        held = list((listing or {}).get("held") or [])
        if (seed_summary or {}).get("held_data"):
            held.append({"sku": "new colours/sizes", "code": seed_summary.get("code"), "reason": seed_summary["held"]})
        held_line = (
            "not on the website yet: "
            + ", ".join(f"{h['sku']} ({h['reason']})" for h in held)
            + " -- fix them and press again; everything else was sent"
            if held
            else None
        )
        # THE ONE RE-QUEUE RULE. The press reached Shopify but did not do all
        # it was pressed for -- the product is not visible, or it is visible at
        # the wrong price or with the wrong photos. Either way the row goes
        # BACK in the queue so the next press / scheduled sync retries it. See
        # _requeue_unpublished for why this is not the ping-pong hazard.
        # Missing-option siblings have no independent push door: the lead
        # must stay queued until their next family plan includes them.
        option_requeue = any(
            h.get("code") == "OPTION_MISSING" and str(h.get("reason", "")).startswith("no ")
            for h in held
        )
        if pid and (not published_ok or price_not_synced or photo_requeue or option_requeue):
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
                # BOTH said when both are true (recheck round 1): the price
                # line used to win outright, and the stock line -- SOLD OUT,
                # and every data-defect rung riding under it -- lived only in
                # PushResult.stock, which nothing renders.
                (
                    " -- ALSO: ".join(
                        line
                        for line in (
                            _PRICE_NOT_SYNCED_MSG if price_not_synced else None,
                            photo_line if photos_not_synced else None,
                            (stock_summary or {}).get("error") if stock_not_written else None,
                            held_line,
                        )
                        if line
                    )
                    or None
                )
                if published_ok
                else (
                    (pub_summary or {}).get("message")
                    or (pub_summary or {}).get("error")
                    or "publish withheld"
                )
            ),
            code=(
                (
                    PRICE_NOT_SYNCED
                    if price_not_synced
                    else PHOTOS_SYNC_FAILED
                    if photo_failed
                    else ((stock_summary or {}).get("code") if stock_not_written else None)
                )
                or (held[0]["code"] if held else None)
                or (PHOTOS_NOT_SYNCED if photos_not_synced else None)
                if published_ok
                else (pub_summary or {}).get("code")
            ),
            reason=(
                ("archived_not_listed" if archived_not_listed else None)
                if published_ok
                else (pub_summary or {}).get("reason", "publish_withheld")
            ),
            metafields=mf_summary,
            variant_prices=vp_summary,
            variants_seeded=seed_summary,
            publication=pub_summary,
            photos=photo_summary,
            stock=stock_summary,
            tags=tag_summary,
            listing=listing_plan,
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


def _photo_error_is_transient(summary: Dict[str, Any]) -> bool:
    """A photo error worth waiting for: Shopify still processing the photo
    (VARIANT_PHOTOS_PENDING / NON_READY_MEDIA) or no answer at all."""
    return bool(summary.get("no_answer")) or summary.get("code") == VARIANT_PHOTOS_PENDING


# A press inside this window after the last counted one is the SAME run
# (pressing Send again while Shopify still processes), never a second one.
# ponytail: a fixed gap, not a run id; the scheduled slots are hours apart.
PHOTO_TRY_GAP = timedelta(minutes=30)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _count_photo_tries(db, pid: str, *, failed: bool) -> int:
    """ecom.photo_sync_tries: +1 for a sync run whose photo step failed, back
    to 0 for a clean one. A failed press within PHOTO_TRY_GAP of the last
    counted one does not count again (review of #1277: presses are not runs).
    Returns the count after this press (0 on a read or write failure --
    never a reason to stop re-queueing)."""
    from ..catalog_ecom import update_ecom

    box = {"n": 0}
    now = _now()

    def _apply(ecom: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        held = int(ecom.get("photo_sync_tries") or 0)
        if not failed:
            box["n"] = 0
            if not held and "photo_sync_tried_at" not in ecom:
                return None
            ecom.pop("photo_sync_tried_at", None)
            return {**ecom, "photo_sync_tries": 0}
        try:
            last = datetime.fromisoformat(str(ecom.get("photo_sync_tried_at")))
        except ValueError:
            last = None
        if held and last and now - last < PHOTO_TRY_GAP:
            box["n"] = held
            return None
        box["n"] = held + 1
        return {**ecom, "photo_sync_tries": box["n"], "photo_sync_tried_at": now.isoformat()}

    try:
        update_ecom(db, pid, _apply)
    except Exception:  # noqa: BLE001 -- the count is advisory
        return 0
    return box["n"]


async def _set_active(db, product_gid: str, published: Dict[str, Any]) -> Dict[str, Any]:
    """productUpdate {id, status: ACTIVE} after the publish: ``published`` when
    Shopify took it, else a withheld publication saying the listing stayed
    Draft. Never raises."""
    try:
        body = await _graphql(
            db, _PRODUCT_UPDATE,
            {"input": {"id": _as_shopify_gid(product_gid, "Product"), "status": "ACTIVE"}},
        )
        err = _user_errors(body, "productUpdate")
    except Exception as exc:  # noqa: BLE001 -- fail-soft, reported on the result
        err = str(exc)
    if not err:
        return published
    return {
        "published": False,
        "error": f"publish withheld: setting the listing Active failed ({err}) -- it stays "
        f"OFF the website (Draft); press Send to website again",
    }


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
            _writeback_product(
                db, pid, existing_gid, status="DRAFT",
                sent_rev=(product.get("ecom") or {}).get(EDIT_REV),
            )
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
    inventoryPolicy DENY + the RULE's number (0 once the spine is inactive)
    on the child's own Shopify variant AT EVERY MAPPED SHOP'S LOCATION (the
    parent's listing stays ACTIVE, every other size keeps selling). The
    retire hook's door for a variant-of product (online_delist.delist_if_live
    when the child spine's is_active flips off) -- is_active is the ONLY
    off-sale marker, and the quantity rule (online_stock_writeback.
    _on_hand_for_skus: an inactive spine lists 0) is what writes the 0 here
    AND keeps every later stock pass at 0 until the product is reactivated.
    NEVER productUpdate, never a status change.

    Reads the bridge, never a second link: the child's catalog_variants row
    by ``sku`` gives the variant + inventory-item gids AND parent_product_id
    (the parent twin), whose ecom.shopify_product_id is the productId the
    bulk-update needs. Any of the three missing -> the same clean noop as an
    un-pushed product (nothing on Shopify to take down). DARK -> SIMULATED
    plan, zero network. LIVE -> ``_set_variant_tracking`` (DENY) then THE ONE
    writer, ``push_skus_stock`` with no precomputed quantities: one row per
    MAPPED location (a size retired at one location would keep selling from
    the other shops), and the PARENT's nested baseline gets the 0 through the
    writer's own write-back so a reactivation (on-hand 1 vs sent 0) diffs
    and is re-sent. A row above 0 means the spine is still ACTIVE -- the
    size is NOT off sale -- and the result says so. Fail-soft."""
    pid = product.get("id") or product.get("product_id")
    sku = str(product.get("sku") or "").strip()
    row: Dict[str, Any] = {}
    parent_gid = None
    parent_twin_id = None
    try:
        from ..online_catalog import _parent_from, _parents_for_variants

        row = (db["catalog_variants"].find_one({"sku": sku}) if sku else None) or {}
        # The parent through BOTH links (parent_product_id OR parent_sku), the
        # way the sweep and the target reader resolve it -- a row keyed on the
        # SPINE id resolved to nothing here, the delist was a green noop and
        # the size kept selling until the next sweep found the row through
        # parent_sku (recheck round 1). STRICT: a dead read is reported,
        # never "not on Shopify".
        parent = _parent_from(_parents_for_variants(db, [row], strict=True), row) if row else {}
        parent_twin_id = parent.get("id")
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
        tracked = await _set_variant_tracking(db, payload["productId"], [variant_gid], "DENY")
        # THE RULE decides the number (recheck round 2: one rule, one
        # implementation). The spine's is_active is the only off-sale marker
        # and the writer's rule reads it -- an inactive spine lists 0 at every
        # shop it could read, so the writer's own holders question (the same
        # rule at buffer 0) finds no holder either: an unmapped shop that still
        # keeps the size on its shelf (Gangadham Pune, today) is not asked to
        # sell it. This door used to hand the writer a forced 0 plus a
        # `delisting` exemption from that question, so the door and the rule
        # could disagree about the same SKU: the DELETE door deactivates the
        # spine fail-soft, a swallowed error there left the spine ACTIVE, 0
        # went out green, and the next stock pass wrote the shelf straight
        # back. Now what goes out IS the rule's number.
        written = await push_skus_stock(
            db,
            [sku],
            source="variant_delist",
            product_id=str(parent_twin_id) if parent_twin_id else None,
            policy="DENY",
            # The LISTING's tracked flag is not this door's to write (recheck
            # round 3): it confirms DENY on ONE size. Writing False for a
            # refused re-send recorded the whole LIVE parent as untracked (its
            # next press then read "publish withheld / NOT made visible" over
            # a visible, tracked listing); writing True for an accepted one
            # cleared the flag a size minted untracked had left, so nothing
            # ever re-sent that size its tracking. None leaves the flag alone.
            tracked=None,
        )
        payload["rows"] = written.get("quantities") or {}
        payload["stores_mapped"] = written.get("stores_mapped", 0)
        own = list(tracked.get("errors") or [])
        # DENY stuck only when Shopify's ANSWER says so (`tracking_verdicts`,
        # the one rule) -- an accepted call that echoes the size untracked, on
        # another policy or not at all confirms nothing.
        if not own and variant_gid not in tracked["confirmed"]:
            own.append(f"Shopify's answer did not confirm {sku} tracked + stop selling at 0")
        # A row above 0 is the rule saying the size is NOT off sale -- its
        # spine is still active -- and this door says so instead of reporting
        # a green take-down the next pass would undo.
        on_sale = {
            sid: int(q or 0)
            for sid, q in (payload["rows"].get(sku) or {}).items()
            if int(q or 0) > 0
        }
        if on_sale:
            own.append(
                f"{sku} is still on sale ("
                + ", ".join(f"{s}: {q}" for s, q in sorted(on_sale.items()))
                + ") -- its product spine is still ACTIVE, the only off-sale "
                "marker the quantity rule reads; deactivate the product and "
                "press again"
            )
        # The take-down LANDED when DENY stuck and EVERY mapped shop's row was
        # accepted -- nothing mapped, a shop unknown, a location conflicted or
        # a refused write is not landed. The writer's STANDING verdict (a
        # mapped location that cannot sell online, a stray one, a data
        # defect) rides as code + error only, exactly as on the parent's
        # press: under the day-1 configuration (the Jharkhand locations
        # unticked) it stamped DELIST_FAILED / "Still live" over three
        # accepted 0s, on every size delist, for as long as the locations
        # stayed unticked (recheck round 1). "No shop mapped" stays the
        # WRITER's rule (STORE_UNMAPPED, nothing accepted -> not landed).
        accepted = payload["rows"].get(sku) or {}
        landed = (
            not own
            and not written.get("errors")
            and bool(accepted)
            and len(accepted) == int(written.get("stores_mapped") or 0)
        )
        # The writer's own line already leads with its refusal when it has
        # one and carries the ladder under it: one line each, never twice.
        lines = own + ([written["error"]] if written.get("error") else [])
        return PushResult(
            mode=MODE_LIVE,
            entity="variant",
            action="delist",
            target_id=pid,
            ok=landed,
            shopify_id=variant_gid,
            payload=payload,
            code=written.get("code"),
            error="; ".join(str(e) for e in lines[:3]) or None,
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

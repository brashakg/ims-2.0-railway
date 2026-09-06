"""Shopify push -- variants

CREATE-side variant seeding (the price-0.00 / no-SKU fix):
seed-row building, planning, repair and the post-write seeding call --
plus `push_variant_prices`, the separately gated variant price push.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from agents.nexus_providers import _as_shopify_gid

from ._shared import (
    MODE_LIVE,
    MODE_SIMULATED,
    PushResult,
    _blocked_result,
    _live_or_reason,
    push_lock_reason,
)
from .transport import _graphql, _user_errors
from .queries import _VARIANTS_BULK_CREATE, _VARIANTS_BULK_UPDATE, _VARIANTS_PER_CALL
from .product_input import (
    _publishable_gtin,
    _resolve_variant_pricing,
    _variants_for_price_push,
    build_variant_price_inputs,
)
from .writeback import _writeback_variant

# ---------------------------------------------------------------------------
# CREATE-side variant seeding (the price-0.00 / no-SKU fix)
# ---------------------------------------------------------------------------
# ProductInput has no price/sku, so a bare productCreate lands a product whose
# variant is unsellable-looking (0.00) and unjoinable (no SKU). These pure
# builders describe the variant state we WANT; _seed_variants_after_write applies
# it to the variants Shopify actually materialised.


def _norm_opt(value: Any) -> str:
    """Normalised option token used to join an IMS variant to a Shopify one."""
    return str(value or "").strip().lower()


def _variant_option_key(variant: Optional[Dict[str, Any]]) -> Tuple[str, str]:
    """(color, size) join key for an IMS catalog_variants row. The product-level
    pseudo-variant (None) keys to ("", ""), which is exactly what Shopify's
    "Default Title" variant keys to -- so a no-variant product maps 1:1 onto the
    single default variant."""
    v = variant or {}
    return (_norm_opt(v.get("option_color")), _norm_opt(v.get("option_size")))


def _node_option_key(node: Dict[str, Any]) -> Tuple[str, str]:
    """(color, size) join key for a Shopify ProductVariant node, read off its
    selectedOptions. Any option we do not model (incl. "Title"/"Default Title")
    contributes nothing, so a default variant keys to ("", "")."""
    color = size = ""
    for so in (node or {}).get("selectedOptions") or []:
        name = _norm_opt((so or {}).get("name"))
        if name == "color":
            color = _norm_opt(so.get("value"))
        elif name == "size":
            size = _norm_opt(so.get("value"))
    return (color, size)


def _node_inventory_item_gid(node: Optional[Dict[str, Any]]) -> Optional[str]:
    """The InventoryItem gid off a returned ProductVariant node
    (inventoryItem { id }), normalised to a full gid -- the oversell-guard
    stock-write-back target. None when the response does not carry it (an old
    canned body, a partial node): the write-back then leaves any existing
    mapping untouched (set-only, never cleared)."""
    inv = (node or {}).get("inventoryItem")
    if not isinstance(inv, dict):
        return None
    raw = inv.get("id")
    if not raw:
        return None
    return _as_shopify_gid(raw, "InventoryItem") or None


def _variant_option_values(variant: Optional[Dict[str, Any]]) -> List[Dict[str, str]]:
    """VariantOptionValueInput rows (used only when CREATING a variant Shopify
    did not auto-create). Empty for a product-level / option-less variant --
    such a variant can never be created, only updated."""
    v = variant or {}
    out: List[Dict[str, str]] = []
    if v.get("option_color"):
        out.append({"optionName": "Color", "name": str(v["option_color"])})
    if v.get("option_size"):
        out.append({"optionName": "Size", "name": str(v["option_size"])})
    return out


def build_variant_seed_rows(
    product: Dict[str, Any], variants: Optional[List[Dict[str, Any]]] = None
) -> List[Dict[str, Any]]:
    """The DESIRED Shopify variant state for a product being created. Pure.

    One entry per IMS variant, or exactly ONE product-level entry when the
    product carries no catalog_variants row (the common eyewear case: a single
    Shopify "Default Title" variant). Each entry is
    {key, option_values, row, variant} where `row` is the price/sku part of a
    ProductVariantsBulkInput:
        price            the resolved selling price, "0.00" is NEVER sent
        compareAtPrice   the MRP when it is strictly above the price, else an
                         EXPLICIT None (GraphQL null) so a stale strikethrough
                         on Shopify is CLEARED -- the same contract as
                         build_variant_price_inputs
        barcode          the GTIN (variant gtin/barcode, else the product's) --
                         `store_barcode` is the physical join key and is never pushed
        inventoryItem.sku the IMS SKU (variant sku, else the product sku) -- in
                         the 2024-04+ product model the SKU lives on the
                         inventory item, NOT on the variant

    An entry with NEITHER a usable price NOR a SKU is dropped: there would be
    nothing to say about that variant."""
    rows: List[Dict[str, Any]] = []
    source: List[Optional[Dict[str, Any]]] = list(variants or []) or [None]
    for v in source:
        vd = v or {}
        price, mrp = _resolve_variant_pricing(product, vd)
        sku = str(vd.get("sku") or product.get("sku") or "").strip()
        barcode = _publishable_gtin(
            vd.get("gtin"),
            vd.get("barcode"),
            product.get("gtin"),
            product.get("barcode"),
        )
        row: Dict[str, Any] = {}
        if price > 0:
            row["price"] = f"{price:.2f}"
            if mrp > price:
                row["compareAtPrice"] = f"{mrp:.2f}"
            else:
                # EXPLICIT GraphQL null, byte-matching build_variant_price_inputs'
                # contract: when the MRP no longer exceeds the selling price, a
                # stale strikethrough on Shopify must be CLEARED, not kept. On a
                # flag-ON update the seeding step REPLACES push_variant_prices
                # (seeded=True skips it), so omitting the field here would
                # silently lose today's clearing behaviour and leave a fake
                # "was <old MRP>" above the real price -- an MRP-display
                # compliance issue (adversarial-panel must-fix 3).
                row["compareAtPrice"] = None
        if sku:
            row["inventoryItem"] = {"sku": sku}
        if barcode:
            row["barcode"] = str(barcode)
        if not row.get("price") and not row.get("inventoryItem"):
            # No price and no SKU -> nothing worth a mutation for this variant.
            continue
        rows.append(
            {
                "key": _variant_option_key(v),
                "option_values": _variant_option_values(v),
                "row": row,
                "variant": v,  # None for the product-level pseudo-variant
            }
        )
    return rows


def plan_variant_seed(
    product: Dict[str, Any],
    variants: Optional[List[Dict[str, Any]]] = None,
    *,
    repair_only: bool = False,
) -> Optional[Dict[str, Any]]:
    """The SIMULATED (dry-run) view of the seeding step: the exact rows that
    WOULD be applied to the new Shopify variants, with no gid yet (Shopify mints
    those at create time). None when there is nothing to seed.

    `repair_only=True` restricts the plan to the UNSEEDED subset
    (build_repair_seed_rows) instead of every row -- the repair-only update
    path (independent of SHOPIFY_PUSH_PRICE_ON_UPDATE) must never show/apply
    a re-price for a row that already carries a stored gid."""
    rows = (
        build_repair_seed_rows(product, variants)
        if repair_only
        else build_variant_seed_rows(product, variants)
    )
    if not rows:
        return None
    planned: List[Dict[str, Any]] = []
    for r in rows:
        entry = dict(r["row"])
        if r["option_values"]:
            entry["optionValues"] = r["option_values"]
        planned.append(entry)
    return {
        "variants": planned,
        "note": (
            "price/compareAtPrice/barcode/sku are applied to the variants "
            "Shopify creates (productVariantsBulkUpdate), and any remaining IMS "
            "variant is created (productVariantsBulkCreate)"
        ),
    }


def _row_has_gid(v: Optional[Dict[str, Any]], ecom: Dict[str, Any]) -> bool:
    """True when this ONE seed-row target already carries a stored gid: a real
    IMS catalog_variants row's own shopify_variant_id/shopify_inventory_item_id,
    or -- for the product-level pseudo-variant (v is None, a no-variant-row
    product) -- the product's ecom fallback fields. Pure, row-level (never
    aggregated across a product's other rows)."""
    if v is not None:
        return bool(
            str(v.get("shopify_variant_id") or "").strip()
            or str(v.get("shopify_inventory_item_id") or "").strip()
        )
    return bool(
        str(ecom.get("shopify_variant_id") or "").strip()
        or str(ecom.get("shopify_inventory_item_id") or "").strip()
    )


def _needs_repair(
    product: Dict[str, Any], variants: Optional[List[Dict[str, Any]]]
) -> bool:
    """True when AT LEAST ONE variant target of this product -- a real
    catalog_variants row, or the product-level pseudo-variant for a
    no-variant-row product -- has NEVER been seeded (carries no stored
    shopify_variant_id / shopify_inventory_item_id anywhere).

    "Never seeded" == the create-side seeding either never ran or its bulk
    write failed for that specific row, so its Shopify variant still sits at
    the auto-created 0.00 / no-SKU / no-stock-target.

    ROW-aware, deliberately NOT `all(...)`/product-aware (panel fix-round,
    #955): a multi-variant product where a PARTIAL bulk-create leaves some
    rows seeded and others not (see _seed_variants_after_write's harvest of a
    partially-successful productVariantsBulkCreate response) must still be
    flagged for repair on every later push until EVERY row has a gid. The
    earlier `not any(...)` formulation flipped False the moment ANY single
    row acquired a gid, permanently masking the remaining stranded row(s)
    from ever being retried without the owner globally re-arming
    SHOPIFY_PUSH_PRICE_ON_UPDATE -- exactly the ~4,400-product blast radius
    this repair-only mechanism exists to avoid. Pure.

    Used to run REPAIR-only seeding on an UPDATE, INDEPENDENT of
    SHOPIFY_PUSH_PRICE_ON_UPDATE. The caller pairs this with
    build_repair_seed_rows / plan_variant_seed(repair_only=True) /
    _seed_variants_after_write(repair_only=True) so ONLY the still-unseeded
    row(s) are ever submitted for (re)seeding -- an already-seeded row is
    never re-touched (re-priced/re-barcoded) by a repair pass."""
    ecom = product.get("ecom") or {}
    source: List[Optional[Dict[str, Any]]] = list(variants or []) or [None]
    return any(not _row_has_gid(v, ecom) for v in source)


def build_repair_seed_rows(
    product: Dict[str, Any], variants: Optional[List[Dict[str, Any]]] = None
) -> List[Dict[str, Any]]:
    """Like build_variant_seed_rows, but restricted to the row(s) that have
    NEVER been seeded (see _row_has_gid / _needs_repair). Used by the
    repair-only update path so it can (re)seed a stranded variant WITHOUT
    re-touching -- re-pricing, re-barcoding -- any row that already carries a
    stored gid. Pure.

    An already-fully-seeded product (every row/the pseudo-variant has a gid)
    returns [] here even though build_variant_seed_rows(product, variants)
    would still happily rebuild a full plan for it -- that full-reseed
    behaviour is reserved for the CREATE path and the owner's explicit
    SHOPIFY_PUSH_PRICE_ON_UPDATE opt-in, both of which call
    build_variant_seed_rows directly instead of this function."""
    ecom = product.get("ecom") or {}
    source: List[Optional[Dict[str, Any]]] = list(variants or []) or [None]
    unseeded = [v for v in source if not _row_has_gid(v, ecom)]
    if not unseeded:
        return []
    # `unseeded` is either a sublist of real IMS variant dicts, or exactly
    # [None] (the pseudo-variant case) -- both shapes are exactly what
    # build_variant_seed_rows expects as its own `variants` argument (a bare
    # [None] round-trips through its `list(variants or []) or [None]` guard
    # identically to passing None/[] outright), so no special-casing needed.
    return build_variant_seed_rows(product, unseeded)


def _assign_seed_rows(
    seed_rows: List[Dict[str, Any]], nodes: Optional[List[Dict[str, Any]]]
) -> Tuple[
    List[Dict[str, Any]],
    List[Dict[str, Any]],
    List[Optional[Dict[str, Any]]],
    List[Tuple[Optional[Dict[str, Any]], str, Optional[str]]],
    int,
]:
    """Split the desired rows against the variants Shopify actually created.
    Pure. Returns (update_rows, create_rows, create_variants, pairs, skipped):

      update_rows      ProductVariantsBulkInput rows carrying the matched gid
      create_rows      rows for IMS variants Shopify did NOT create (productCreate
                       only ever materialises one variant)
      create_variants  the IMS variant docs aligned 1:1 with create_rows
      pairs            (ims_variant_or_None, gid, inventory_item_gid_or_None)
                       for the matched ones -> write-back. The third member is
                       the node's inventoryItem gid -- the oversell-guard stock
                       target -- None when the response did not carry it.
      skipped          rows we can neither update nor create (no gid, no options)

    MATCHING ORDER (adversarial-panel must-fix 2): a row whose IMS variant
    already stores a shopify_variant_id that appears among the returned nodes
    is paired on that GID FIRST, regardless of option-label drift (an IMS
    option rename, BVI-era spelling like Grey/Gray). Only rows without a
    stored-and-present gid fall through to (color,size) option-key matching.
    A row whose stored gid is present in the nodes can therefore NEVER reach
    productVariantsBulkCreate -- the drift case previously minted a duplicate
    live variant and re-pointed the row's stock target at it, leaving the old
    variant sellable at a stale price with its stock never synced again."""
    pool: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    unmatched: List[Dict[str, Any]] = []
    node_by_gid: Dict[str, Dict[str, Any]] = {}
    for n in nodes or []:
        if isinstance(n, dict) and n.get("id"):
            pool.setdefault(_node_option_key(n), []).append(n)
            unmatched.append(n)
            node_by_gid[_as_shopify_gid(n.get("id"), "ProductVariant")] = n

    update_rows: List[Dict[str, Any]] = []
    create_rows: List[Dict[str, Any]] = []
    create_variants: List[Optional[Dict[str, Any]]] = []
    pairs: List[Tuple[Optional[Dict[str, Any]], str, Optional[str]]] = []
    skipped = 0

    # PASS 1 -- gid-first. matched_by_row keeps seed-row order so pairs /
    # update_rows are emitted in the same order as before (pairs[0] stays the
    # first seed row -- the default-variant contract used by the ecom
    # write-back).
    matched_by_row: Dict[int, Tuple[str, Optional[str]]] = {}
    remaining: List[Tuple[int, Dict[str, Any]]] = []
    for idx, r in enumerate(seed_rows):
        stored = (
            (r["variant"] or {}).get("shopify_variant_id")
            if r["variant"] is not None
            else None
        )
        gid = _as_shopify_gid(stored, "ProductVariant") if stored else ""
        node = node_by_gid.pop(gid, None) if gid else None
        if node is not None:
            # Consume the node everywhere so option matching cannot reuse it.
            if node in unmatched:
                unmatched.remove(node)
            bucket = pool.get(_node_option_key(node))
            if bucket and node in bucket:
                bucket.remove(node)
            matched_by_row[idx] = (gid, _node_inventory_item_gid(node))
        else:
            remaining.append((idx, r))

    # Single remaining row / single remaining node: trust the 1:1 pairing even
    # if the option labels do not line up (e.g. Shopify kept a "Title" option).
    lone = len(remaining) == 1 and len(unmatched) == 1

    # PASS 2 -- option-key matching for the rows without a stored-and-present
    # gid (unchanged semantics); only these may fall through to create/skip.
    for idx, r in remaining:
        bucket = pool.get(r["key"]) or []
        node = bucket.pop(0) if bucket else (unmatched[0] if lone else None)
        if node is not None:
            if node in unmatched:
                unmatched.remove(node)
            gid = _as_shopify_gid(node.get("id"), "ProductVariant")
            matched_by_row[idx] = (gid, _node_inventory_item_gid(node))
        elif r["option_values"]:
            create_rows.append({"optionValues": r["option_values"], **r["row"]})
            create_variants.append(r["variant"])
        else:
            skipped += 1

    for idx, r in enumerate(seed_rows):
        got = matched_by_row.get(idx)
        if got is None:
            continue
        gid, inv_gid = got
        update_rows.append({"id": gid, **r["row"]})
        pairs.append((r["variant"], gid, inv_gid))
    return update_rows, create_rows, create_variants, pairs, skipped


async def _seed_variants_after_write(
    db,
    product: Dict[str, Any],
    variants: Optional[List[Dict[str, Any]]],
    product_gid: str,
    nodes: Optional[List[Dict[str, Any]]],
    *,
    repair_only: bool = False,
) -> Optional[Dict[str, Any]]:
    """LIVE-only: give the freshly created Shopify variants their price / MRP /
    barcode / SKU, create any remaining IMS variant, and write every gid back --
    BOTH the ProductVariant gid (a later price push's handle) and the
    InventoryItem gid (the oversell-guard stock write-back's target).

    Fail-SOFT side channel, exactly like metafields: an error is reported in the
    returned summary and NEVER flips the product push's ok (the product itself
    was created successfully; a re-push repairs the variants). Returns None when
    there is nothing to seed (no price and no SKU anywhere).

    `repair_only=True` (the flag-independent update path, #955 fix-round)
    restricts `seed_rows` to build_repair_seed_rows' UNSEEDED subset instead
    of every row -- so a row that already carries a stored gid can never be
    re-priced/re-barcoded by a repair pass, and only the still-stranded
    row(s) go through _assign_seed_rows' matching this call."""
    seed_rows = (
        build_repair_seed_rows(product, variants)
        if repair_only
        else build_variant_seed_rows(product, variants)
    )
    if not seed_rows:
        return None
    update_rows, create_rows, create_variants, pairs, skipped = _assign_seed_rows(
        seed_rows, nodes
    )
    summary: Dict[str, Any] = {
        "updated": 0,
        "created": 0,
        "skipped_unmatched": skipped,
        "errors": [],
        "default_variant_gid": None,
        "variant_gids": [],
        # Oversell-guard capture: every InventoryItem gid persisted (aligned
        # 1:1 with variant_gids; None where the response carried none), plus
        # the PRODUCT-LEVEL one (set ONLY for a no-variant-row product, whose
        # single default variant is the product itself -- see push_product's
        # ecom fallback write-back).
        "inventory_item_gids": [],
        "product_level_inventory_item_gid": None,
        # How many seed rows actually carried a selling price. The publish
        # precondition (must-fix 1) requires this to be > 0: a SKU-only seed
        # means Shopify's variant is still at 0.00 and must never go visible.
        "priced_rows": sum(1 for r in seed_rows if r["row"].get("price")),
    }

    for i in range(0, len(update_rows), _VARIANTS_PER_CALL):
        chunk = update_rows[i : i + _VARIANTS_PER_CALL]
        try:
            body = await _graphql(
                db, _VARIANTS_BULK_UPDATE, {"productId": product_gid, "variants": chunk}
            )
            err = _user_errors(body, "productVariantsBulkUpdate")
            if err:
                summary["errors"].append(err)
            else:
                summary["updated"] += len(chunk)
        except Exception as e:  # noqa: BLE001 -- fail-soft side channel
            summary["errors"].append(str(e))

    created_nodes: List[Dict[str, Any]] = []
    for i in range(0, len(create_rows), _VARIANTS_PER_CALL):
        chunk = create_rows[i : i + _VARIANTS_PER_CALL]
        try:
            body = await _graphql(
                db, _VARIANTS_BULK_CREATE, {"productId": product_gid, "variants": chunk}
            )
            err = _user_errors(body, "productVariantsBulkCreate")
            if err:
                summary["errors"].append(err)
            # Harvest whatever the call DID create even on a partial-error
            # response. productVariantsBulkCreate can return BOTH userErrors AND
            # a non-empty productVariants list (some rows rejected, others
            # created). The old `continue` on any error discarded that list, so
            # the variants Shopify HAD created were orphaned: their
            # ProductVariant / InventoryItem gids were never written back,
            # leaving live variants IMS could neither re-price nor oversell-guard
            # again. We now always read the returned nodes (empty on a total
            # failure -> nothing harvested), then match + write them back below.
            made = (
                (body.get("data") or {}).get("productVariantsBulkCreate") or {}
            ).get("productVariants") or []
            created_nodes.extend(n for n in made if isinstance(n, dict) and n.get("id"))
        except Exception as e:  # noqa: BLE001 -- fail-soft side channel
            summary["errors"].append(str(e))
    summary["created"] = len(created_nodes)

    # Match the newly created variants back to their IMS rows (same option key)
    # so their gids are persisted too.
    if created_nodes:
        by_key: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        for n in created_nodes:
            by_key.setdefault(_node_option_key(n), []).append(n)
        for v in create_variants:
            bucket = by_key.get(_variant_option_key(v)) or []
            if bucket:
                made_node = bucket.pop(0)
                pairs.append(
                    (
                        v,
                        _as_shopify_gid(made_node.get("id"), "ProductVariant"),
                        _node_inventory_item_gid(made_node),
                    )
                )

    for variant_doc, gid, inventory_item_gid in pairs:
        summary["variant_gids"].append(gid)
        summary["inventory_item_gids"].append(inventory_item_gid)
        if variant_doc:
            _writeback_variant(db, variant_doc, gid, inventory_item_gid)
        elif inventory_item_gid:
            # The product-level pseudo-variant (product has NO catalog_variants
            # rows): there is no variant row to stamp, so its InventoryItem gid
            # goes onto the PRODUCT's ecom sub-doc instead (push_product passes
            # it to _writeback_product). NEVER set for a product that HAS
            # variant rows -- stamping variant #1's inventory item on the
            # parent would hand a parent-sku stock lookup the WRONG variant's
            # inventory target.
            summary["product_level_inventory_item_gid"] = inventory_item_gid
    if pairs:
        # The FIRST pair is the default variant (the product-level row for a
        # no-variant product) -- the one a later price push needs.
        summary["default_variant_gid"] = pairs[0][1]
    return summary



async def push_variant_prices(
    db, product: Dict[str, Any], variants: Optional[List[Dict[str, Any]]] = None
) -> PushResult:
    """Push variant price / compareAtPrice / barcode for ONE product's mapped
    variants via productVariantsBulkUpdate (owner priority: "change MRP in IMS
    -> website updates").

    UPDATE-only by design: a variant with no stored shopify_variant_id is
    SKIPPED (counted in the payload) -- variants get their gid when the
    product's variants first sync; creating variants here would fork that
    ownership. DARK by default -> SIMULATED plan with the exact
    ProductVariantsBulkInput rows and NO network call; LIVE only behind the
    same three gates. Never raises (fail-soft PushResult contract)."""
    pid = product.get("id") or product.get("product_id")
    # Hub Phase 5 push-lock, FIRST gate (fail-closed): a locked brand's prices
    # must never reach Shopify either.
    _lock = push_lock_reason(db, "product", product)
    if _lock:
        return _blocked_result("variant-prices", pid, _lock)

    ecom = product.get("ecom") or {}
    raw_gid = ecom.get("shopify_product_id")
    product_gid = _as_shopify_gid(raw_gid, "Product") if raw_gid else None
    # No catalog_variants rows -> the product-level pseudo-variant (the stored
    # default-variant gid), so a single-variant product's price change can
    # still be re-pushed (OS-016 / OS-017). [] when no gid -> clean noop.
    rows, skipped = build_variant_price_inputs(
        product, _variants_for_price_push(product, variants)
    )
    payload: Dict[str, Any] = {
        "productId": product_gid,
        "variants": rows,
        "skipped_no_gid_or_price": skipped,
    }
    action = "update" if rows else "noop"

    live, reason = _live_or_reason(db)
    if not live:
        return PushResult(
            mode=MODE_SIMULATED,
            entity="variant-prices",
            action=action,
            target_id=pid,
            ok=True,
            shopify_id=product_gid,
            payload=payload,
            reason=reason,
        )

    if not rows:
        # Nothing mapped (or nothing priced) -> a clean no-op, not an error.
        return PushResult(
            mode=MODE_LIVE,
            entity="variant-prices",
            action="noop",
            target_id=pid,
            ok=True,
            shopify_id=product_gid,
            payload=payload,
        )
    if not product_gid:
        return PushResult(
            mode=MODE_LIVE,
            entity="variant-prices",
            action="skip",
            target_id=pid,
            ok=False,
            payload=payload,
            error="parent product not on Shopify yet (push the product first)",
        )
    try:
        for i in range(0, len(rows), _VARIANTS_PER_CALL):
            chunk = rows[i : i + _VARIANTS_PER_CALL]
            body = await _graphql(
                db,
                _VARIANTS_BULK_UPDATE,
                {"productId": product_gid, "variants": chunk},
            )
            err = _user_errors(body, "productVariantsBulkUpdate")
            if err:
                return PushResult(
                    mode=MODE_LIVE,
                    entity="variant-prices",
                    action=action,
                    target_id=pid,
                    ok=False,
                    shopify_id=product_gid,
                    payload=payload,
                    error=err,
                )
        return PushResult(
            mode=MODE_LIVE,
            entity="variant-prices",
            action=action,
            target_id=pid,
            ok=True,
            shopify_id=product_gid,
            payload=payload,
        )
    except Exception as e:  # noqa: BLE001 -- fail-soft, never propagate
        return PushResult(
            mode=MODE_LIVE,
            entity="variant-prices",
            action=action,
            target_id=pid,
            ok=False,
            shopify_id=product_gid,
            payload=payload,
            error=str(e),
        )


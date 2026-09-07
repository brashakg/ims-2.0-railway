"""Shopify push -- inventory (make the website's QUANTITIES real, per shop)

Owner ruling 2026-09-06: "Product will be shipped from whichever store holds
the inventory, new stores need to be created in shopify to match our app."
Every physical shop is a Shopify location (``stores.shopify_location_id``, set
on the Organization page; ``stores_util.physical_stores`` is the ONE reader).
Each location shows exactly what that shop has on its shelf; Shopify's order
routing picks the shop that ships. IMS stays the master of every number.

THE ONE RULE (online_stock_writeback.online_quantities_for_skus):

    quantity Shopify shows for SKU s at location L
      = recommend_allocation( on_hand(s, store(L)), safety_buffer )
      (0 at every L for a SKU in a SUPERADMIN online-blocked collection)

THE ONE WRITER (``set_inventory_quantities(db, rows)``, rows = one
``(inventory_item_gid, location_gid, qty)`` per mapped shop and SKU, an
explicit 0 included). Everything that changes a website quantity goes through
``push_skus_stock``: the product push (``sync_product_stock``), the Push-stock
button / all-pending sweep / 01:00+09:00 schedule (``sync_stock_levels``) and
the POS-sale / ingest / restock / transfer-ship / quarantine / write-off
write-back (``online_stock_writeback.writeback_skus``). Nothing else computes
a quantity or talks to inventorySetQuantities.

Fail loud, never pool, never silently skip:
  * STORE_UNMAPPED -- a shop that HOLDS a listed unit has no location. The run
    is ok=False, one deduped SYSTEM task per shop is filed, and the MAPPED
    shops' rows are STILL written (withholding them leaves the website at its
    last numbers -- after a sale that is an oversell). A shop holding nothing
    never blocks.
  * STOCK_ONHAND_UNKNOWN -- a shop whose on-hand read failed is written
    NOWHERE in that pass (unknown is never written as 0); every other shop's
    true numbers still go out, and the baseline omits the unknown shop so the
    next pass re-sends it.
  * STOCK_TARGET_MISSING -- the SKU has no Shopify inventory item yet.
  * STOCK_ACTIVATION_FAILED -- Shopify said ITEM_NOT_STOCKED_AT_LOCATION, the
    item was activated at the chunk's locations, and the retry still failed.
    Split per location first, like any other refusal: bulk activation is ONE
    call per ITEM across all of its locations, so one dead location poisons the
    activation for every location, and unsplit it froze every other shop.
  * STOCK_WRITE_FAILED -- Shopify refused the write for a reason of its own (a
    location deleted, deactivated or renamed under a live mapping). The call
    is re-sent SPLIT PER LOCATION first, so one dead location costs only its
    own rows instead of freezing every other shop's number after a sale.
  * SHOPIFY_LOCATION_UNMAPPED -- a Shopify location that FULFILS ONLINE ORDERS
    maps to no IMS shop: Shopify routes orders there and sells whatever number
    it holds, and IMS never writes it.
  * STORE_LOCATION_DUPLICATE -- two shops claim one location. Neither is
    written (one shop's count would silently become the other's).
  * STOCK_STORE_ORPHAN -- on-hand parked at a store_id no shop record matches
    (the UUID-vs-code hazard): those units reach no location at all.
  * STORE_UNMAPPED with NO shop mapped at all -- nothing is written and the
    variant's tracking is left alone (tracked=true + DENY behind no quantity
    is a listing that reads SOLD OUT).

The last-sent baseline is ``ecom.online_stock.quantities = {sku: {store_id:
qty}}`` over the MAPPED shops only, diffed by ``stock_changed`` (nested dicts
compare deep; the old flat ``{sku: qty}`` never equals the nested shape, so
the first pass after deploy re-sends every listed product -- the wanted
seeding, no backfill script).

WHY A DIFF AND NOT A DIRTY FLAG: on-hand is written by fourteen files and the
POS sell path explicitly refuses the item_events ledger, so there is NO single
choke point to hook a flag into. Diffing per store against the last number
sent is one rule in one place and catches every writer.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from agents.nexus_providers import _as_shopify_gid

from ._shared import (
    MODE_LIVE,
    MODE_SIMULATED,
    PushResult,
    _live_or_reason,
    is_variant_of,
    logger,
)
from .transport import _graphql, _now, _user_error_codes, _user_errors
from .queries import (
    _INVENTORY_ACTIVATE,
    _INVENTORY_SET_MAX,
    _INVENTORY_SET_QUANTITIES,
    _LOCATIONS_LIST_QUERY,
    _VARIANTS_INVENTORY_UPDATE,
    _VARIANTS_PER_CALL,
)

# Stable machine codes (the #1105 pattern: `code` for the operator, `error`
# for the plain-language line).
STOCK_ONHAND_UNKNOWN = "STOCK_ONHAND_UNKNOWN"
STOCK_TARGET_MISSING = "STOCK_TARGET_MISSING"
STORE_UNMAPPED = "STORE_UNMAPPED"
STOCK_ACTIVATION_FAILED = "STOCK_ACTIVATION_FAILED"
# Two shops claiming ONE Shopify location: Shopify takes one quantity per
# (item, location), so one shop's count would silently become the other's.
STORE_LOCATION_DUPLICATE = "STORE_LOCATION_DUPLICATE"
# On-hand parked at a store_id the `stores` collection does not know (the
# UUID-vs-code hazard): those units are published NOWHERE.
STOCK_STORE_ORPHAN = "STOCK_STORE_ORPHAN"

# Shopify refused the write for a reason of its own (a location the owner
# deleted / deactivated, an invalid quantity): the rows in that call were NOT
# applied. Promoted so a refused write never reads as a codeless green run.
STOCK_WRITE_FAILED = "STOCK_WRITE_FAILED"
# A Shopify location that FULFILS ONLINE ORDERS but maps to no IMS shop:
# Shopify keeps routing and selling its own number and IMS never writes it.
SHOPIFY_LOCATION_UNMAPPED = "SHOPIFY_LOCATION_UNMAPPED"

# Shopify's own userErrors code when an inventory item is not stocked at the
# location a quantity was set for (InventorySetQuantitiesUserErrorCode).
ITEM_NOT_STOCKED_AT_LOCATION = "ITEM_NOT_STOCKED_AT_LOCATION"

_POLICY_DENY = "DENY"
_POLICY_CONTINUE = "CONTINUE"

# Dedupe ref of the per-shop "map me" task (the parity pattern).
_UNMAPPED_TASK_REF = "shopify-store-unmapped:{store_id}"
# ...and of the per-LOCATION one (a Shopify location no shop claims).
_UNMAPPED_LOCATION_TASK_REF = "shopify-location-unmapped:{location_id}"


# ---------------------------------------------------------------------------
# The shop list (one reader) and the locations read (one read)
# ---------------------------------------------------------------------------


def _stores(db) -> List[Dict[str, Any]]:
    """``physical_stores(db)`` -- propagates a Mongo error so the caller can
    treat the whole batch as UNKNOWN (an unknown shop list must never read as
    'no shops')."""
    from ..stores_util import physical_stores

    return physical_stores(db)


def _location_conflicts(stores: Iterable[Dict[str, Any]]) -> Dict[str, List[str]]:
    """``{location_gid: [store_id, ...]}`` for a location claimed by MORE THAN
    ONE shop. Shopify takes exactly one quantity per (inventory item,
    location): two shops on one gid put a DUPLICATE pair in the same
    inventorySetQuantities call, and whichever survives becomes that
    location's number AND the baseline's -- the other shop's true count is
    lost and the diff re-sends the product forever. The Organization page
    refuses it (409, routers/stores.py ``_location_holder``); this is the
    writer's own backstop for a doc written around that door."""
    by_gid: Dict[str, List[str]] = {}
    for s in stores:
        gid = str(s.get("shopify_location_id") or "").strip()
        sid = str(s.get("store_id") or "").strip()
        if sid and gid:
            by_gid.setdefault(_as_shopify_gid(gid, "Location"), []).append(sid)
    return {gid: sids for gid, sids in by_gid.items() if len(sids) > 1}


def _mapped(stores: Iterable[Dict[str, Any]]) -> Dict[str, str]:
    """``{store_id: location_gid}`` for the mapped shops -- THE definition of
    "this shop has a usable Shopify location" (``unmapped_holders`` asks it
    too). A location two shops claim maps NEITHER of them: unwritten and
    reported beats one shop's number silently overwriting the other's."""
    stores = list(stores)
    conflicted = set(_location_conflicts(stores))
    out: Dict[str, str] = {}
    for s in stores:
        gid = str(s.get("shopify_location_id") or "").strip()
        sid = str(s.get("store_id") or "").strip()
        if sid and gid:
            gid = _as_shopify_gid(gid, "Location")
            if gid not in conflicted:
                out[sid] = gid
    return out


async def list_locations(db) -> Dict[str, Any]:
    """Every Shopify location, for the Organization page's per-store "Shopify
    location" dropdown (GET /online-store/push/locations) and the store save
    that copies the display name: ``{mode, reason, locations: [{id, name,
    isActive, fulfillsOnlineOrders, shipsInventory, city, province}]}``.

    DARK (any of the three gates off) -> ``locations == []`` plus the gate
    reason and ZERO network. A Shopify error -> ``[]`` plus the error text;
    never raises. Read-only (read_locations); nothing is cached or persisted
    -- the mapping lives on the store record, not here.
    """
    live, reason = _live_or_reason(db)
    if not live:
        return {"mode": MODE_SIMULATED, "reason": reason, "locations": []}
    try:
        body = await _graphql(db, _LOCATIONS_LIST_QUERY, {})
    except Exception as exc:  # noqa: BLE001 -- fail-soft read
        return {"mode": MODE_LIVE, "reason": f"location lookup failed: {exc}", "locations": []}
    nodes = ((body.get("data") or {}).get("locations") or {}).get("nodes") or []
    out: List[Dict[str, Any]] = []
    for n in nodes:
        if not isinstance(n, dict) or not n.get("id"):
            continue
        addr = n.get("address") if isinstance(n.get("address"), dict) else {}
        out.append(
            {
                "id": _as_shopify_gid(n["id"], "Location"),
                "name": n.get("name"),
                "isActive": bool(n.get("isActive")),
                "fulfillsOnlineOrders": bool(n.get("fulfillsOnlineOrders")),
                "shipsInventory": bool(n.get("shipsInventory")),
                "city": addr.get("city"),
                "province": addr.get("province"),
            }
        )
    return {"mode": MODE_LIVE, "reason": None, "locations": out}


# ---------------------------------------------------------------------------
# Per-product helpers (pure)
# ---------------------------------------------------------------------------


def inventory_policy_for(product: Dict[str, Any]) -> str:
    """DENY (never oversell) unless the product explicitly opts out via
    ``ecom.allow_oversell`` (a made-to-order line, say)."""
    return _POLICY_CONTINUE if (product.get("ecom") or {}).get("allow_oversell") else _POLICY_DENY


def product_skus(product: Dict[str, Any], variants: Optional[List[Dict[str, Any]]]) -> List[str]:
    """The SKUs whose quantities this product lists: one per variant row,
    plus the product's own SKU when the product carries its OWN Shopify
    inventory item (``ecom.shopify_inventory_item_id`` -- the standalone
    variant that was seeded before any size rows existed and stays
    purchasable beside them; left out, its number on Shopify would survive
    every pass) or has no variant rows at all."""
    out: List[str] = []
    for v in variants or []:
        sku = str((v or {}).get("sku") or "").strip()
        if sku and sku not in out:
            out.append(sku)
    own = str(product.get("sku") or "").strip()
    own_item = (product.get("ecom") or {}).get("shopify_inventory_item_id")
    if own and own not in out and (not out or own_item):
        out.append(own)
    return out


def product_variant_gids(
    product: Dict[str, Any],
    variants: Optional[List[Dict[str, Any]]],
    extra: Optional[List[Optional[str]]] = None,
) -> List[str]:
    """Every ProductVariant gid known for this product: the stored default
    (ecom.shopify_variant_id), each variant row's, plus whatever the caller just
    got back from Shopify. De-duplicated, gid-normalised."""
    raw: List[Optional[str]] = [(product.get("ecom") or {}).get("shopify_variant_id")]
    raw += [(v or {}).get("shopify_variant_id") for v in variants or []]
    raw += list(extra or [])
    out: List[str] = []
    for r in raw:
        if not r:
            continue
        gid = _as_shopify_gid(r, "ProductVariant")
        if gid and gid not in out:
            out.append(gid)
    return out


def _last_sent(product: Dict[str, Any]) -> Dict[str, Any]:
    stock = (product.get("ecom") or {}).get("online_stock")
    return stock if isinstance(stock, dict) else {}


def stock_changed(
    product: Dict[str, Any],
    quantities: Dict[str, Dict[str, int]],
    skus: Optional[Iterable[str]] = None,
) -> bool:
    """True when the per-store quantities (the MAPPED slice -- the caller
    builds it with ``mapped_slice``) differ from the ones last sent, or the
    product was never sent / never had tracking switched on. Nested dicts
    compare deep; the pre-per-store flat ``{sku: qty}`` baseline never equals
    the nested shape, so the first pass after deploy re-sends everything.

    ``skus`` is the product's CURRENT SKU list and BOTH sides of the compare
    are restricted to it. The baseline is written per SKU (a POS write-back
    replaces one row and leaves the rest), so it outlives the SKU: a size row
    retired off the parent, or a SKU Shopify never accepted, would otherwise
    sit in the baseline forever, never appear in the slice, and mark the
    product changed on EVERY 01:00 / 09:00 pass -- the noise that hides a real
    STORE_UNMAPPED report and is exactly the "changed products only" property
    the schedule rests on. A SKU the pass could not read is still absent from
    the slice while present in the baseline, so it still re-sends."""
    last = _last_sent(product)
    if not last.get("tracked"):
        return True
    prev = dict(last.get("quantities") or {})
    if skus is not None:
        keep = set(skus)
        prev = {s: q for s, q in prev.items() if s in keep}
    return prev != dict(quantities)


def mapped_slice(
    quantities: Dict[str, Dict[str, int]], mapped: Dict[str, str], skus: Iterable[str]
) -> Dict[str, Dict[str, int]]:
    """``{sku: {store_id: qty}}`` restricted to ``skus`` and to the MAPPED
    shops -- the shape the baseline holds and the diff compares. An unmapped
    holder is reported (STORE_UNMAPPED), never diffed: otherwise no pass could
    ever noop while one shop stays unmapped."""
    out: Dict[str, Dict[str, int]] = {}
    for sku in skus:
        per = quantities.get(sku)
        if per is None:
            continue
        out[sku] = {sid: int(q) for sid, q in per.items() if sid in mapped}
    return out


def unmapped_holders(
    db,
    quantities: Dict[str, Dict[str, int]],
    stores: Iterable[Dict[str, Any]],
    skus: Iterable[str],
    mapped: Dict[str, str],
) -> List[Dict[str, Any]]:
    """The shops with no usable location that HOLD at least one unit of any of
    ``skus``: ``[{store_id, store_code, store_name, units}]``. A shop holding
    nothing is not listed (it never blocks). ``mapped`` is the ONE definition
    of "has a location", so a shop sharing its gid with another shop (written
    nowhere) is reported here rather than vanishing from every guard.

    HOLDS is the shelf, not the published number: ``quantities`` is
    POST-allocation, so with a safety buffer of B a shop sitting on exactly B
    units reads 0 there and would be neither named nor tasked -- a fully green
    run over a shop whose shelf is invisible online (invariant 6). The raw
    shelf is the SAME rule read with the buffer at 0, so there is still only
    one on-hand spelling. ponytail: one extra buffer-0 read, and ONLY while
    some shop is unmapped -- zero cost in the steady state where every shop
    has its location.

    UNKNOWN IS NEVER SCORED 0 (round-4 P3 + P2). When that buffer-0 read
    fails, or completes without the shop in it (the rule omits a shop whose
    aggregate died), the shelf is UNKNOWN -- and falling back to the
    post-allocation numbers re-creates exactly the buffer bug above, silently.
    Such a shop is reported with ``units=None`` ("an unknown number"), so it is
    named, tasked and not-ok instead of quietly reading as holding nothing."""
    skus = list(skus)
    unmapped = [s for s in stores if str(s.get("store_id") or "") not in mapped]
    if not unmapped or not skus:
        return []
    held: Optional[Dict[str, Dict[str, int]]] = None
    try:
        from ..online_stock_writeback import online_quantities_for_skus

        held = online_quantities_for_skus(db, skus, safety_buffer=0) or None
    except Exception as exc:  # noqa: BLE001 -- a report never raises
        logger.warning("[SHOPIFY_STOCK] raw on-hand read failed for the holders: %s", exc)
    out: List[Dict[str, Any]] = []
    for s in unmapped:
        sid = str(s.get("store_id") or "")
        read = held is not None and any(sid in (held.get(sku) or {}) for sku in skus)
        units: Optional[int] = (
            sum(int((held.get(sku) or {}).get(sid, 0) or 0) for sku in skus) if read else None
        )
        if units is None or units > 0:
            out.append(
                {
                    "store_id": sid,
                    "store_code": s.get("store_code"),
                    "store_name": s.get("store_name"),
                    "units": units,
                }
            )
    return out


def _unknown_stores(
    quantities: Dict[str, Dict[str, int]], store_ids: Iterable[str], skus: Iterable[str]
) -> List[str]:
    """Shops absent from ANY listed SKU's row -- their on-hand read failed this
    pass (the rule omits a shop it could not read). ANY, not EVERY: one SKU
    that carries the shop (a SUPERADMIN-blocked row, say) must not clear a shop
    the pass could not read for the others.

    EVERY active physical shop, not only the MAPPED ones (round-4 P2): the rule
    loops every shop, so a shop with no location whose aggregate died is
    equally unknown -- and scored as "holds nothing" it was named by NO guard
    at all (``unmapped_holders`` saw 0 units, ``orphan_stock_stores`` only
    matches store ids no shop record has), so its units stayed invisible online
    under a fully green run. Invariant 6 is one rule: a shop the pass could not
    read is named, mapped or not."""
    skus = [s for s in skus if s in quantities]
    if not skus:
        return []
    return sorted(
        sid for sid in {str(s or "") for s in store_ids if s}
        if any(sid not in quantities[s] for s in skus)
    )


def plan_product_stock(db, product: Dict[str, Any], variants: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    """The dry-run stock plan (SIMULATED branch): policy, the per-store
    quantity rows that WOULD be written at each mapped shop, and the shops
    that hold listed units but have no location. Read-only, zero network.

    It carries the WRITER'S OWN code/error (round 2 fixed this preview-vs-press
    divergence for ``sync_stock_levels`` and left the per-product plan behind):
    this dict is the ``stock`` block of the dry-run product push -- the preview
    an operator reads before a first publish -- so a shop list that could not
    be read, a duplicated location, an unmapped holder or "no shop mapped at
    all" must say so here exactly as the live press would, instead of a
    silently green plan over a press that writes nothing. ``stores_total`` is
    None (not 0) when the shop list itself is unknown."""
    from ..online_stock_writeback import online_quantities_for_skus

    skus = product_skus(product, variants)
    quantities = online_quantities_for_skus(db, skus) if skus else {}
    stores: List[Dict[str, Any]] = []
    read_error: Optional[str] = None
    try:
        stores = _stores(db)
    except Exception as exc:  # noqa: BLE001 -- a plan must never raise
        logger.warning("[SHOPIFY_STOCK] store list unknown for the plan: %s", exc)
        read_error = f"shop list unknown (store read failed) -- nothing written: {exc}"
    mapped = _mapped(stores)
    conflicts = _location_conflicts(stores)
    holders = unmapped_holders(db, quantities, stores, skus, mapped)
    code: Optional[str] = None
    error: Optional[str] = None
    if read_error:
        code, error = STOCK_ONHAND_UNKNOWN, read_error
    elif conflicts:
        code, error = STORE_LOCATION_DUPLICATE, _duplicate_error(conflicts)
    elif holders:
        code, error = STORE_UNMAPPED, _unmapped_error(holders)
    elif not mapped and skus:
        code, error = STORE_UNMAPPED, _no_mapping_error()
    return {
        "ok": code is None,
        "code": code,
        "error": error,
        "tracked": True,
        "policy": inventory_policy_for(product),
        "quantities": mapped_slice(quantities, mapped, skus),
        "stores_mapped": len(mapped),
        "stores_total": None if read_error else len(stores),
        "unmapped_stores": holders,
    }


# ---------------------------------------------------------------------------
# LIVE writers
# ---------------------------------------------------------------------------


async def _set_variant_tracking(
    db, product_gid: str, variant_gids: List[str], policy: str
) -> Dict[str, Any]:
    """tracked=true + inventoryPolicy on every variant, chunked. Fail-soft."""
    out: Dict[str, Any] = {"updated": 0, "errors": []}
    rows = [
        {"id": g, "inventoryPolicy": policy, "inventoryItem": {"tracked": True}}
        for g in variant_gids
    ]
    for i in range(0, len(rows), _VARIANTS_PER_CALL):
        chunk = rows[i : i + _VARIANTS_PER_CALL]
        try:
            body = await _graphql(
                db, _VARIANTS_INVENTORY_UPDATE, {"productId": product_gid, "variants": chunk}
            )
            err = _user_errors(body, "productVariantsBulkUpdate")
            if err:
                out["errors"].append(err)
            else:
                out["updated"] += len(chunk)
        except Exception as exc:  # noqa: BLE001 -- fail-soft side channel
            out["errors"].append(str(exc))
    return out


# Not a Shopify code: the marker _set_chunk puts in the code set when the call
# never reached Shopify at all. A transport failure is about the CONNECTION,
# not about one location, so it is never worth re-sending per location.
_TRANSPORT_FAILURE = "__transport__"


async def _set_chunk(db, chunk: List[Dict[str, Any]]) -> Tuple[Optional[str], set]:
    """ONE inventorySetQuantities call: ``(error_or_None, userError codes)``."""
    variables = {
        "input": {
            "name": "available",
            "reason": "correction",
            "ignoreCompareQuantity": True,
            "quantities": chunk,
        }
    }
    try:
        body = await _graphql(db, _INVENTORY_SET_QUANTITIES, variables)
    except Exception as exc:  # noqa: BLE001 -- fail-soft side channel
        return str(exc), {_TRANSPORT_FAILURE}
    return _user_errors(body, "inventorySetQuantities"), _user_error_codes(body, "inventorySetQuantities")


async def _activate_chunk(db, chunk: List[Dict[str, Any]]) -> List[str]:
    """inventoryBulkToggleActivation: each distinct item in the chunk, at the
    chunk's locations for that item, in one call per item. Returns the error
    strings (empty = every activation accepted)."""
    by_item: Dict[str, List[str]] = {}
    for row in chunk:
        locs = by_item.setdefault(row["inventoryItemId"], [])
        if row["locationId"] not in locs:
            locs.append(row["locationId"])
    errors: List[str] = []
    for inv, locs in by_item.items():
        try:
            body = await _graphql(
                db,
                _INVENTORY_ACTIVATE,
                {
                    "inventoryItemId": inv,
                    "inventoryItemUpdates": [{"locationId": l, "activate": True} for l in locs],
                },
            )
            err = _user_errors(body, "inventoryBulkToggleActivation")
            if err:
                errors.append(f"{inv}: {err}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{inv}: {exc}")
    return errors


async def set_inventory_quantities(
    db, rows: Iterable[Tuple[str, str, int]]
) -> Dict[str, Any]:
    """THE writer. ABSOLUTE available quantity per ``(inventory_item_gid,
    location_gid, qty)`` row via inventorySetQuantities (ignoreCompareQuantity:
    IMS is the master), chunked at Shopify's cap. A chunk Shopify refuses with
    ITEM_NOT_STOCKED_AT_LOCATION gets every item in it activated at that
    chunk's locations, then ONE retry. ANY refusal Shopify answered with -- that
    one included -- is then re-sent SPLIT PER LOCATION, so only the bad
    location's rows are lost and only it carries STOCK_ACTIVATION_FAILED /
    STOCK_WRITE_FAILED; see ``_write_chunk``. Stateless:
    no "activated" bookkeeping, so it
    self-heals when the owner adds or re-enables a location by hand and costs
    zero extra calls in steady state. Fail-soft ``{set, written, activated,
    errors, code}`` -- ``written`` is the rows Shopify accepted."""
    out: Dict[str, Any] = {"set": 0, "written": [], "activated": 0, "errors": [], "code": None}
    entries = [
        {
            "inventoryItemId": _as_shopify_gid(inv, "InventoryItem"),
            "locationId": _as_shopify_gid(loc, "Location"),
            "quantity": max(0, int(qty)),
        }
        for inv, loc, qty in rows
    ]
    for i in range(0, len(entries), _INVENTORY_SET_MAX):
        await _write_chunk(db, entries[i : i + _INVENTORY_SET_MAX], out)
    return out


def _by_location(chunk: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for r in chunk:
        out.setdefault(r["locationId"], []).append(r)
    return out


async def _write_chunk(db, chunk: List[Dict[str, Any]], out: Dict[str, Any], *, split: bool = True) -> None:
    """ONE inventorySetQuantities call plus its two recoveries, accumulating
    into ``out``.

    1. ITEM_NOT_STOCKED_AT_LOCATION -> activate every item in the chunk at the
       chunk's locations, then ONE retry (unchanged).
    2. ANY OTHER refusal Shopify actually ANSWERED with -> it applied NOT ONE
       row of the call, so a chunk spanning several locations is re-sent SPLIT
       PER LOCATION and only the bad location's rows are lost. Without it,
       one location the owner deleted, deactivated or renamed freezes every
       OTHER shop's number at whatever it last was -- after a sale that is the
       website selling a unit that has walked out, repeated by every later sale
       and every 01:00 / 09:00 pass until a human reads the sync_runs row. The
       split is one level deep and DOES re-run recovery 1 inside each
       per-location call (the docstring used to claim it never re-activates and
       cost ``locations + 2``; it always did), so a failing chunk costs three
       calls for the chunk plus three per location -- ``3 * (locations + 1)``
       for a single-item chunk. A TRANSPORT failure is about the connection, not about one
       location: never split.
       A FAILED ACTIVATION splits too, and that is the branch that matters
       most: inventoryBulkToggleActivation is ONE call per ITEM across all of
       that item's locations, so a single location the owner deleted under a
       live mapping poisons the activation for EVERY location and, unsplit,
       froze every other shop's number after a sale. Split, only the dead
       location's own activation fails.
    A refusal that survives both is named AND coded (STOCK_WRITE_FAILED, or
    STOCK_ACTIVATION_FAILED from recovery 1): a write Shopify refused must
    never reach the operator as a codeless green run."""
    err, codes = await _set_chunk(db, chunk)
    activation = False
    if err and ITEM_NOT_STOCKED_AT_LOCATION in codes:
        act_errors = await _activate_chunk(db, chunk)
        out["activated"] += len({r["inventoryItemId"] for r in chunk})
        err, codes = await _set_chunk(db, chunk)
        if err:
            activation = True
            err = f"{STOCK_ACTIVATION_FAILED}: {err}" + (
                f" (activation: {'; '.join(act_errors)})" if act_errors else ""
            )
    if not err:
        out["set"] += len(chunk)
        out["written"].extend((r["inventoryItemId"], r["locationId"], r["quantity"]) for r in chunk)
        return
    per_location = _by_location(chunk)
    if split and _TRANSPORT_FAILURE not in codes and len(per_location) > 1:
        # The survivors land; the refusals collapse into ONE error for the
        # chunk, so a split never inflates the caller's failure COUNT
        # (writeback_skus reports len(errors)).
        sub: Dict[str, Any] = {"set": 0, "written": [], "activated": 0, "errors": [], "code": None}
        for rows_at in per_location.values():
            await _write_chunk(db, rows_at, sub, split=False)
        out["set"] += sub["set"]
        out["written"].extend(sub["written"])
        out["activated"] += sub["activated"]
        if sub["errors"]:
            out["code"] = out["code"] or sub["code"]
            out["errors"].append("; ".join(str(e) for e in sub["errors"]))
        return
    out["code"] = out["code"] or (STOCK_ACTIVATION_FAILED if activation else STOCK_WRITE_FAILED)
    out["errors"].append(err)


def _writeback_stock(
    db,
    product_id: str,
    per_sku: Dict[str, Dict[str, int]],
    *,
    policy: Optional[str] = None,
    tracked: Optional[bool] = None,
) -> None:
    """Persist what was just sent (ecom.online_stock) so the next levels pass
    can diff against it: ``quantities = {sku: {store_id: qty}}``, read-merge-
    write per SKU -- a POS write-back for one SKU REPLACES only that SKU's
    per-store row, and a shop whose read failed is simply absent from it so
    the next pass re-sends that shop. NEVER touches locally_modified.
    Fail-soft."""
    try:
        coll = db["catalog_products"]
        doc = coll.find_one({"id": product_id})
        if doc is None:
            return
        ecom = dict(doc.get("ecom") or {})
        prev = ecom.get("online_stock") if isinstance(ecom.get("online_stock"), dict) else {}
        # Only nested rows survive the merge: a flat pre-per-store number is
        # not a per-shop fact and must not masquerade as one.
        quantities = {
            sku: dict(rows)
            for sku, rows in dict(prev.get("quantities") or {}).items()
            if isinstance(rows, dict)
        }
        for sku, rows in per_sku.items():
            quantities[sku] = {sid: int(q) for sid, q in rows.items()}
        ecom["online_stock"] = {
            "quantities": quantities,
            "policy": policy if policy is not None else prev.get("policy"),
            "tracked": bool(tracked) or bool(prev.get("tracked")),
            "synced_at": _now(),
        }
        coll.update_one({"id": product_id}, {"$set": {"ecom": ecom}})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOPIFY_STOCK] stock write-back failed %s: %s", product_id, exc)


def _file_task(db, *, title: str, description: str, ref: str, payload: Dict[str, Any], store_id=None) -> None:
    """ONE deduped P1 SYSTEM task. Fail-soft -- a tripwire never blocks a
    write."""
    try:
        from ..task_triggers import create_system_task
        from database.repositories.task_repository import TaskRepository

        coll = db.get_collection("tasks") if hasattr(db, "get_collection") else db["tasks"]
        if coll is None:
            return
        create_system_task(
            TaskRepository(coll),
            title=title,
            description=description,
            priority="P1",
            category="Inventory",
            store_id=store_id,
            dedupe_ref=ref,
            extra={"payload": payload},
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[SHOPIFY_STOCK] system task skipped: %s", exc)


def _file_unmapped_task(db, store: Dict[str, Any]) -> None:
    """ONE deduped SYSTEM task per unmapped shop that holds listed stock
    (source_ref shopify-store-unmapped:<store_id>)."""
    label = store.get("store_code") or store.get("store_name") or store.get("store_id")
    _file_task(
        db,
        title=f"Map {label} to a Shopify location",
        description=(
            f"{label} holds {_units_phrase(store.get('units'))} of stock that is "
            f"listed on the website, but the shop has no Shopify location, so its "
            f"stock is invisible online until it is mapped: Organization page > edit "
            f"the shop > Shopify location. The mapped shops' quantities were still "
            f"written."
        ),
        ref=_UNMAPPED_TASK_REF.format(store_id=store.get("store_id")),
        payload={"code": STORE_UNMAPPED, "units": store.get("units", 0)},
        store_id=store.get("store_id"),
    )


def _file_unmapped_location_task(db, location: Dict[str, Any]) -> None:
    """ONE deduped SYSTEM task per Shopify location that FULFILS ONLINE ORDERS
    and maps to no IMS shop (source_ref shopify-location-unmapped:<gid>)."""
    label = location.get("name") or location.get("id")
    _file_task(
        db,
        title=f"Shopify location {label} sells online but maps to no shop",
        description=(
            f"Shopify routes online orders to {label} and sells whatever number it "
            f"holds there, but no IMS shop carries that location, so IMS never "
            f"writes it and never zeroes it. Map it to its shop on the Organization "
            f"page, or untick 'Fulfill online orders' on it in Shopify admin > "
            f"Locations."
        ),
        ref=_UNMAPPED_LOCATION_TASK_REF.format(location_id=location.get("id")),
        payload={"code": SHOPIFY_LOCATION_UNMAPPED, "location_id": location.get("id")},
    )


async def unmapped_fulfilling_locations(db, mapped: Dict[str, str]) -> List[Dict[str, Any]]:
    """Shopify locations that FULFIL ONLINE ORDERS but map to NO IMS shop:
    ``[{id, name}]``. Shopify keeps routing and selling their own numbers
    there while IMS -- which writes per shop and never a pooled total -- never
    touches them, so the website oversells from a shelf nothing updates. This
    rule used to live ONLY in the React sync page (an amber line a SUPERADMIN
    had to be looking at), so the 01:00 / 09:00 pass recorded a fully green run
    beside it. It also covers a MAPPED but DEACTIVATED shop: physical_stores
    drops it, so its location is written by nobody and reported by no other
    guard -- but Shopify still lists it.

    ONE read-only locations query (``list_locations``: [] and ZERO network when
    any gate is DARK). Fail-soft -> [] (a tripwire never blocks a write)."""
    try:
        read = await list_locations(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOPIFY_STOCK] locations read failed for the verdict: %s", exc)
        return []
    have = set(mapped.values())
    return [
        {"id": loc.get("id"), "name": loc.get("name")}
        for loc in read.get("locations") or []
        if is_stray_fulfilling(loc, have)
    ]


def is_stray_fulfilling(location: Dict[str, Any], mapped_gids: Iterable[str]) -> bool:
    """THE predicate: this Shopify location is ACTIVE, FULFILS ONLINE ORDERS and
    maps to no IMS shop. Spelled once so the backend verdict, the deduped task
    and the sync page's amber line cannot drift -- they already had: the page's
    TypeScript copy read ``isActive !== false`` where this reads truthy, so a
    location with no ``isActive`` at all was "stray" on the page and "fine" in
    the verdict."""
    return bool(
        location.get("id")
        and location.get("isActive")
        and location.get("fulfillsOnlineOrders")
        and location.get("id") not in set(mapped_gids)
    )


# The LAST LIVE sweep's stray-location verdict, so a per-sale write-back can
# carry it without reading Shopify's locations on every sale.
_SYNC_STATE_COLLECTION = "online_sync_state"
_STRAY_LOCATIONS_DOC = "shopify_stray_locations"


def record_stray_locations(db, locations: List[Dict[str, Any]]) -> None:
    """Remember what the sweep just found (an empty list CLEARS it, so the
    verdict is only ever as old as the last LIVE pass). Fail-soft."""
    try:
        db[_SYNC_STATE_COLLECTION].update_one(
            {"_id": _STRAY_LOCATIONS_DOC},
            {"$set": {"locations": list(locations or []), "at": _now()}},
            upsert=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[SHOPIFY_STOCK] stray-location state not recorded: %s", exc)


def last_stray_locations(db) -> List[Dict[str, Any]]:
    """The last LIVE sweep's stray-location verdict, for a caller that must not
    make a Shopify read of its own -- the POS write-back runs once per sale, and
    a stray location only appears when a human edits Shopify admin, so a fresh
    read per sale buys nothing. Carrying the sweep's verdict is what stops the
    sale's own sync_runs row from saying the website was corrected while Shopify
    keeps routing orders to a location IMS never writes. Fail-soft -> []."""
    try:
        doc = db[_SYNC_STATE_COLLECTION].find_one({"_id": _STRAY_LOCATIONS_DOC})
    except Exception as exc:  # noqa: BLE001
        logger.debug("[SHOPIFY_STOCK] stray-location state unreadable: %s", exc)
        return []
    rows = (doc or {}).get("locations") or []
    return [r for r in rows if isinstance(r, dict)]


def _labels(stores: Iterable[Dict[str, Any]], store_ids: Iterable[str]) -> List[str]:
    """Shop CODES for a list of store_ids (Pune's id is a UUID; the owner reads
    codes). ``unknown_stores`` itself stays the machine list of ids."""
    by = {
        str(s.get("store_id") or ""): str(s.get("store_code") or s.get("store_name") or s.get("store_id") or "")
        for s in stores
    }
    return [by.get(sid) or sid for sid in store_ids]


def _unknown_error(names: List[str]) -> str:
    return (
        f"on-hand unknown at {', '.join(names)} -- written nowhere this pass "
        f"(never as 0); the other shops were written"
    )


def _duplicate_error(conflicts: Dict[str, List[str]]) -> str:
    pairs = "; ".join(f"{gid} <- {', '.join(sorted(sids))}" for gid, sids in sorted(conflicts.items()))
    return (
        f"two shops share one Shopify location ({pairs}) -- neither was written "
        f"(one shop's count would overwrite the other's); give each shop its own "
        f"location on the Organization page"
    )


def _orphan_error(store_ids: List[str]) -> str:
    return (
        f"on-hand units sit at store id(s) no shop record matches "
        f"({', '.join(store_ids)}) -- they are published NOWHERE, so the website "
        f"under-sells; move them to a real shop or add the shop"
    )


def _stray_location_error(locations: List[Dict[str, Any]]) -> str:
    names = ", ".join(str(loc.get("name") or loc.get("id")) for loc in locations)
    return (
        f"Shopify location(s) that fulfil online orders but map to no shop: {names} "
        f"-- Shopify keeps selling whatever number they hold and IMS never writes "
        f"them; map each to its shop on the Organization page, or untick 'Fulfill "
        f"online orders' on it in Shopify admin > Locations"
    )


def _units_phrase(units: Optional[int]) -> str:
    """``units=None`` means the shelf could not be read this pass -- say so,
    never "0 unit(s)" (that is the silent phantom-stock line this whole module
    exists to refuse)."""
    return "an unknown number of units" if units is None else f"{units} unit(s)"


def _no_mapping_error() -> str:
    return (
        "no shop has a Shopify location -- nothing written; the listing keeps its "
        "last quantity on Shopify until a shop is mapped on the Organization page"
    )


def _unmapped_error(holders: List[Dict[str, Any]]) -> str:
    names = ", ".join(str(h.get("store_code") or h.get("store_name") or h.get("store_id")) for h in holders)
    return (
        f"shops holding listed stock with no Shopify location: {names} -- map them "
        f"on the Organization page (their stock is invisible online until then)"
    )


def _verdict_for(
    *,
    no_mapping: bool,
    conflicts: Dict[str, List[str]],
    holders: List[Dict[str, Any]],
    stray_locations: Optional[List[Dict[str, Any]]] = None,
    unknown_error: Optional[str] = None,
    orphans: Optional[List[str]] = None,
    missing: Optional[List[str]] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """THE priority ladder behind every stock verdict -- the per-product plan,
    the writer (``push_skus_stock``) and the sweep (``sync_stock_levels``) all
    read it HERE. It used to be spelled twice, and the two spellings disagreed
    on the very first rung: the writer assigned with ``or``, so the holders line
    always won and "no shop mapped at all" -- the one state where NOTHING was
    written -- could not be said. On a fresh catalogue with no shop mapped yet
    that is every press: the operator was told to map the shops holding stock
    (true), and never told the press had written nothing anywhere (the true
    thing), while the listing had already gone tracked=true + DENY, i.e. live
    and sold out.

    NOTHING WRITABLE first, then a duplicated location (neither shop written),
    then an unmapped holder (the mapped shops WERE written), then a stray
    Shopify location, an unreadable shop, an orphan store id and last a missing
    Shopify target."""
    if no_mapping:
        return STORE_UNMAPPED, _no_mapping_error()
    if conflicts:
        return STORE_LOCATION_DUPLICATE, _duplicate_error(conflicts)
    if holders:
        return STORE_UNMAPPED, _unmapped_error(holders)
    if stray_locations:
        return SHOPIFY_LOCATION_UNMAPPED, _stray_location_error(list(stray_locations))
    if unknown_error:
        return STOCK_ONHAND_UNKNOWN, unknown_error
    if orphans:
        return STOCK_STORE_ORPHAN, _orphan_error(list(orphans))
    if missing:
        missing = list(missing)
        return (
            STOCK_TARGET_MISSING,
            f"no Shopify inventory item mapped for: {', '.join(missing[:5])}",
        )
    return None, None


def _rows_ok(
    summary: Dict[str, Any],
    holders: List[Dict[str, Any]],
    conflicts: Dict[str, List[str]],
    mapped: Dict[str, str],
) -> bool:
    """ONE verdict for the SIMULATED and the LIVE branch: every guard counts,
    not just the two lists (a preview that reads green must mean a press would
    too, and "no shop mapped" means nothing was written at all)."""
    return bool(mapped) and not (
        summary["errors"]
        or holders
        or conflicts
        or summary["unknown_stores"]
        or summary["orphan_stores"]
    )


async def push_skus_stock(
    db,
    skus: List[str],
    *,
    quantities: Optional[Dict[str, Dict[str, int]]] = None,
    source: str,
    dry_run: bool = False,
    product_id: Optional[str] = None,
    policy: Optional[str] = None,
    tracked: Optional[bool] = None,
) -> Dict[str, Any]:
    """THE quantity path. For every listed SKU, one row per MAPPED shop (an
    explicit 0 included) at that shop's location, through ``set_inventory_
    quantities``. ``quantities`` is the rule's output unless a batch caller
    precomputed it. Gate: ``_live_or_reason`` -- SIMULATED plan with zero
    network when any gate is off or ``dry_run``.

    Summary: ``{ok, mode, source, candidates, quantities (rows written or
    planned, {sku: {store_id: qty}}), set, errors, code, error, stores_total,
    stores_mapped, unmapped_stores, unknown_stores, target_missing}``.
    ``ok`` is False on any error OR an unmapped holder (STORE_UNMAPPED) --
    the mapped rows are written either way. Never raises."""
    from ..online_catalog import inventory_items_for_skus, listings_for_skus
    from ..online_stock_writeback import online_quantities_for_skus, orphan_stock_stores

    distinct = [s for s in dict.fromkeys(skus or []) if s]
    summary: Dict[str, Any] = {
        "ok": False,
        "mode": MODE_SIMULATED,
        "source": source,
        "candidates": len(distinct),
        "quantities": {},
        "set": 0,
        "errors": [],
        "code": None,
        "error": None,
        "stores_total": 0,
        "stores_mapped": 0,
        "unmapped_stores": [],
        "unknown_stores": [],
        "orphan_stores": [],
        "target_missing": [],
    }
    if not distinct:
        summary["ok"] = True
        return summary
    try:
        stores = _stores(db)
    except Exception as exc:  # noqa: BLE001
        summary["code"] = STOCK_ONHAND_UNKNOWN
        summary["error"] = f"shop list unknown (store read failed) -- nothing written: {exc}"
        return summary
    mapped = _mapped(stores)
    summary["stores_total"] = len(stores)
    summary["stores_mapped"] = len(mapped)
    conflicts = _location_conflicts(stores)

    if quantities is None:
        quantities = online_quantities_for_skus(db, distinct)
    if not quantities:
        # STRICT: an absolute writer never fails soft to 0 for a whole batch.
        summary["code"] = STOCK_ONHAND_UNKNOWN
        summary["error"] = (
            "on-hand unknown for every listed SKU at every shop (spine/stock read "
            "failed) -- nothing written"
        )
        return summary

    holders = unmapped_holders(db, quantities, stores, distinct, mapped)
    summary["unmapped_stores"] = holders
    summary["unknown_stores"] = _unknown_stores(
        quantities, [s.get("store_id") for s in stores], distinct
    )
    orphans = orphan_stock_stores(db, distinct)
    summary["orphan_stores"] = orphans
    # ONE priority ladder, shared with sync_stock_levels. "No shop mapped at
    # all" wins outright (the guard hoisted out of the delist door so the
    # button, the sweep, the schedule, every product push and the POS
    # write-back get it): with no location to write there is nothing to say but
    # "nothing written", and a green run here is how a caller comes to flip
    # tracked=true + DENY behind no quantity and report "1 written".
    summary["code"], summary["error"] = _verdict_for(
        no_mapping=not mapped,
        conflicts=conflicts,
        holders=holders,
        unknown_error=(
            _unknown_error(_labels(stores, summary["unknown_stores"]))
            if summary["unknown_stores"]
            else None
        ),
        orphans=orphans,
    )

    targets = inventory_items_for_skus(db, distinct)
    rows: List[Tuple[str, str, int]] = []
    key_of: Dict[Tuple[str, str], Tuple[str, str]] = {}
    item_of: Dict[str, str] = {}
    for sku in distinct:
        inv = targets.get(sku)
        if not inv:
            summary["target_missing"].append(sku)
            summary["code"] = summary["code"] or STOCK_TARGET_MISSING
            summary["errors"].append(f"{sku}: no Shopify inventory item mapped")
            continue
        per = quantities.get(sku)
        if per is None:
            summary["code"] = summary["code"] or STOCK_ONHAND_UNKNOWN
            summary["errors"].append(f"{sku}: on-hand unknown -- not written")
            continue
        inv_gid = _as_shopify_gid(inv, "InventoryItem")
        if inv_gid in item_of:
            # Two SKUs on one inventory item (a mis-stamped mapping): Shopify
            # refuses a duplicate (item, location) pair and the whole chunk
            # would fail, so the second SKU is named and skipped instead.
            summary["errors"].append(
                f"{sku}: inventory item {inv_gid} is already carried by {item_of[inv_gid]} "
                f"-- not written twice"
            )
            continue
        item_of[inv_gid] = sku
        for sid, loc in mapped.items():
            if sid not in per:
                # That shop's read failed: written nowhere this pass (named in
                # unknown_stores + the code, never counted as a failed push).
                summary["code"] = summary["code"] or STOCK_ONHAND_UNKNOWN
                continue
            rows.append((inv_gid, loc, int(per[sid])))
            key_of[(inv_gid, loc)] = (sku, sid)
            summary["quantities"].setdefault(sku, {})[sid] = int(per[sid])

    live, reason = _live_or_reason(db)
    if not live or dry_run:
        summary["mode"] = MODE_SIMULATED
        summary["reason"] = reason if not live else "dry_run (Preview first)"
        summary["ok"] = _rows_ok(summary, holders, conflicts, mapped)
        if summary["errors"] and not summary["error"]:
            summary["error"] = "; ".join(str(e) for e in summary["errors"][:5])
        return summary

    summary["mode"] = MODE_LIVE
    for h in holders:
        _file_unmapped_task(db, h)
    if not mapped:
        return summary  # nothing writable -- code + error already say so
    written_per_sku: Dict[str, Dict[str, int]] = {}
    if rows:
        written = await set_inventory_quantities(db, rows)
        summary["set"] = written["set"]
        summary["errors"].extend(written["errors"])
        if written.get("code"):
            summary["code"] = summary["code"] or written["code"]
        for inv_gid, loc, qty in written["written"]:
            sku, sid = key_of[(inv_gid, loc)]
            written_per_sku.setdefault(sku, {})[sid] = qty
    # What was accepted goes to the baseline -- per listing, only the SKUs
    # written, only the shops written (a failed or unknown shop is omitted so
    # the next pass re-sends it).
    if written_per_sku:
        by_product = (
            {product_id: list(written_per_sku)}
            if product_id
            else listings_for_skus(db, list(written_per_sku))
        )
        for pid, pid_skus in by_product.items():
            rows_for = {s: written_per_sku[s] for s in pid_skus if s in written_per_sku}
            if rows_for:
                _writeback_stock(db, pid, rows_for, policy=policy, tracked=tracked)
    # What Shopify ACCEPTED, not what was planned: the sync page prints these
    # as the per-shop "last written" numbers, and a refused chunk must not
    # read as written (the baseline above already only takes the accepted rows).
    summary["quantities"] = written_per_sku
    summary["ok"] = _rows_ok(summary, holders, conflicts, mapped)
    if summary["errors"] and not summary["error"]:
        summary["error"] = "; ".join(str(e) for e in summary["errors"][:5])
    return summary


def _forget_store_baseline(db, store_id: str, skus: List[str]) -> int:
    """Drop ``store_id`` from the last-sent baseline of every listing carrying
    one of ``skus``, so the next pass re-sends that shop.

    The baseline is keyed by STORE id, not by LOCATION. Remapping a shop to a
    different Shopify location therefore changes nothing the diff can see, and
    the NEW location would never be written at all -- it would sit at whatever
    Shopify had (usually nothing, i.e. sold out) until some unrelated edit
    happened to move that shop's number. Fail-soft; returns the listings
    touched."""
    from ..online_catalog import listings_for_skus

    touched = 0
    try:
        by_product = listings_for_skus(db, list(skus)) if skus else {}
        coll = db["catalog_products"]
        for pid, pid_skus in by_product.items():
            doc = coll.find_one({"id": pid})
            if doc is None:
                continue
            ecom = dict(doc.get("ecom") or {})
            stock = ecom.get("online_stock")
            if not isinstance(stock, dict):
                continue
            quantities = {
                sku: {sid: q for sid, q in dict(rows).items() if sid != store_id}
                for sku, rows in dict(stock.get("quantities") or {}).items()
                if isinstance(rows, dict)
            }
            if quantities == dict(stock.get("quantities") or {}):
                continue
            ecom["online_stock"] = {**stock, "quantities": quantities}
            coll.update_one({"id": pid}, {"$set": {"ecom": ecom}})
            touched += 1
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOPIFY_STOCK] baseline reset failed for %s: %s", store_id, exc)
    return touched


async def release_store_location(db, store_id: str, location_gid: str) -> Dict[str, Any]:
    """Write 0 at ``location_gid`` for every listed SKU ``store_id`` holds, and
    forget that shop's baseline -- the supported way to CORRECT a mis-mapped
    shop.

    Without it the Organization page could only refuse the correction ("transfer
    the units out first"), because the old location, once out of ``_mapped``, is
    written by nobody and keeps advertising the shop's units forever. On a fresh
    setup that made the first wrong pick from a dropdown of similarly-named
    locations permanent: the owner maps BV-BOK-02 to Gangadham Pune, presses
    Push stock, and after the first GRN of a listed product has no way back.

    DARK -> ok with zero network (a dark system never published a quantity).
    Returns ``{ok, mode, set, skus, error}``; never raises."""
    from ..online_catalog import inventory_items_for_skus, listed_skus_on_hand_at

    out: Dict[str, Any] = {"ok": True, "mode": MODE_SIMULATED, "set": 0, "skus": [], "error": None}
    gid = _as_shopify_gid(location_gid, "Location") if location_gid else ""
    if not gid or not store_id:
        return out
    try:
        skus = listed_skus_on_hand_at(db, store_id)
    except Exception as exc:  # noqa: BLE001 -- STRICT read: refuse, never "nothing"
        out["ok"] = False
        out["error"] = f"could not read this shop's listed stock: {exc}"
        return out
    out["skus"] = skus
    if not skus:
        return out
    live, reason = _live_or_reason(db)
    if not live:
        out["reason"] = reason
        _forget_store_baseline(db, store_id, skus)
        return out
    out["mode"] = MODE_LIVE
    targets = inventory_items_for_skus(db, skus)
    rows = [(_as_shopify_gid(inv, "InventoryItem"), gid, 0) for inv in dict.fromkeys(targets.values()) if inv]
    if rows:
        written = await set_inventory_quantities(db, rows)
        out["set"] = written["set"]
        if written["errors"]:
            out["ok"] = False
            out["error"] = "; ".join(str(e) for e in written["errors"][:3])
            return out
    _forget_store_baseline(db, store_id, skus)
    return out


async def sync_product_stock(
    db,
    product: Dict[str, Any],
    variants: Optional[List[Dict[str, Any]]],
    product_gid: str,
    *,
    extra_variant_gids: Optional[List[Optional[str]]] = None,
) -> Dict[str, Any]:
    """LIVE-only (the caller has passed the gates): tracking + policy on every
    known variant, then ``push_skus_stock`` for this product's SKUs -- one row
    per mapped shop per SKU. The rule is computed HERE, immediately before
    the write, never from a batch snapshot: the whole-catalogue pass loops
    ~70 network writes, and a POS sale that lands mid-loop must not be
    overwritten with the pre-sale number (its own write-back would be undone
    until the next tick). Fail-soft summary, never raises; a shop whose
    on-hand is UNKNOWN is never written as 0."""
    pid = product.get("id") or product.get("product_id")
    policy = inventory_policy_for(product)
    gids = product_variant_gids(product, variants, extra_variant_gids)
    tracked: Dict[str, Any] = {"updated": 0, "errors": []}
    # Tracking + DENY go on even when no quantity can follow (no shop mapped):
    # an UNTRACKED item sells without limit, which is the worse failure. The
    # listing then reads sold out and push_skus_stock says so, loudly, instead
    # of reporting a write that never happened.
    if gids:
        tracked = await _set_variant_tracking(db, product_gid, gids, policy)
    else:
        tracked["errors"].append("no variant gid known -- tracking not set")

    skus = product_skus(product, variants)
    summary = await push_skus_stock(
        db,
        skus,
        source="product_push",
        product_id=str(pid) if pid else None,
        policy=policy,
        tracked=tracked["updated"] > 0,
    )
    summary["policy"] = policy
    summary["tracked"] = tracked["updated"]
    summary["errors"] = list(tracked["errors"]) + list(summary["errors"])
    summary["ok"] = summary["ok"] and not tracked["errors"]
    if summary["errors"] and not summary.get("error"):
        summary["error"] = "; ".join(str(e) for e in summary["errors"][:5])
    return summary


# ---------------------------------------------------------------------------
# The whole-catalogue pass
# ---------------------------------------------------------------------------


def _gid_products_with_variants(db) -> List[Tuple[Dict[str, Any], List[Dict[str, Any]]]]:
    """Every catalog product already on Shopify, with its variant rows."""
    out: List[Tuple[Dict[str, Any], List[Dict[str, Any]]]] = []
    try:
        # A size variant (is_variant_of) never owns a listing: its SKU rides
        # the parent's row set below. Filtered even if a repair script ever
        # stamps the parent gid on the child twin (a double stock write and a
        # second ledger otherwise).
        products = [
            d
            for d in db["catalog_products"].find({})
            if (d.get("ecom") or {}).get("shopify_product_id") and not is_variant_of(d)
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOPIFY_STOCK] catalog read failed: %s", exc)
        return []
    if not products:
        return []
    by_pid: Dict[str, List[Dict[str, Any]]] = {}
    by_sku: Dict[str, List[Dict[str, Any]]] = {}
    try:
        for v in db["catalog_variants"].find({}):
            if v.get("parent_product_id"):
                by_pid.setdefault(str(v["parent_product_id"]), []).append(v)
            if v.get("parent_sku"):
                by_sku.setdefault(str(v["parent_sku"]), []).append(v)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOPIFY_STOCK] variant read failed: %s", exc)
    from ..online_catalog import merge_variant_rows

    for p in products:
        pid = str(p.get("id") or p.get("product_id") or "")
        # UNION, never `or`: the two indexes are not alternatives. A size row is
        # keyed on `parent.pim_product_id or parent.product_id`
        # (product_master._variant_row), so a size created before the parent's
        # catalog twin existed carries the SPINE id and one created after
        # carries the CATALOG id -- a mixed set for one parent. With `or`, the
        # ONE row that landed in by_pid hid every row that only landed in
        # by_sku: that size's inventory item was written at NO location, the
        # run reported ok=True / synced=1, and its Shopify number froze while
        # IMS still sold it.
        rows = merge_variant_rows(by_pid.get(pid), by_sku.get(str(p.get("sku") or "")))
        out.append((p, rows))
    return out


async def sync_stock_levels(db, *, dry_run: bool = False) -> PushResult:
    """Send every product on Shopify whose per-store numbers CHANGED since they
    were last sent (or was never sent / never tracked). ONE call of the rule
    for every listed SKU decides the DIFF over the MAPPED shops; each changed
    product's rows are then RECOMPUTED immediately before its own write
    (``sync_product_stock``), so a POS sale landing while the loop is
    mid-way is written, not overwritten with the snapshot. Residual: one
    round-trip and cross-worker (a sale between a product's recompute and
    its inventorySetQuantities); closing it needs a per-SKU version, named
    in the PR body, not built here. DARK -> a SIMULATED plan and zero
    network; ``dry_run=True`` returns that SIMULATED plan EVEN WHEN LIVE (the
    "Preview first" press) -- with the SAME ok / code / error the live pass
    would report, so a mapped shop whose read failed is named in the preview
    too -- and a LIVE run, PREVIEW INCLUDED, makes the ONE read-only locations
    query invariant 2 needs and no other call in the preview, so the verdict
    cannot depend on which button was pressed.
    Never raises. entity="stock", action="sync" (or "noop" when nothing
    changed). The ONE function behind the Push-stock button, the all-pending
    sweep and the 01:00 / 09:00 scheduled sync."""
    from ..online_catalog import inventory_items_for_skus
    from ..online_stock_writeback import online_quantities_for_skus, orphan_stock_stores

    pairs = _gid_products_with_variants(db)
    all_skus: List[str] = []
    for product, variants in pairs:
        for sku in product_skus(product, variants):
            if sku not in all_skus:
                all_skus.append(sku)
    try:
        stores = _stores(db)
    except Exception as exc:  # noqa: BLE001
        return PushResult(
            mode=MODE_SIMULATED,
            entity="stock",
            action="sync",
            ok=False,
            code=STOCK_ONHAND_UNKNOWN,
            error=f"shop list unknown (store read failed) -- nothing written: {exc}",
            payload={"candidates": len(pairs)},
        )
    mapped = _mapped(stores)
    quantities = online_quantities_for_skus(db, all_skus) if all_skus else {}
    if all_skus and not quantities:
        # STRICT: an absolute writer never fails soft to 0 for a whole batch.
        return PushResult(
            mode=MODE_SIMULATED,
            entity="stock",
            action="sync",
            ok=False,
            code=STOCK_ONHAND_UNKNOWN,
            error="on-hand unknown for every listed SKU at every shop (spine/stock "
            "read failed) -- nothing written",
            payload={"candidates": len(pairs), "stores_total": len(stores), "stores_mapped": len(mapped)},
        )
    changed = []
    for product, variants in pairs:
        skus = product_skus(product, variants)
        mine = mapped_slice(quantities, mapped, skus)
        if stock_changed(product, mine, skus):
            changed.append((product, variants, skus, mine))
    holders = unmapped_holders(db, quantities, stores, all_skus, mapped)
    conflicts = _location_conflicts(stores)
    orphans = orphan_stock_stores(db, all_skus)
    # What the LIVE press would refuse, resolved in the PREVIEW too: the
    # dry-run branch never talks to Shopify, so a listing with no inventory
    # item can only be caught here (over the CHANGED products -- the ones a
    # press would actually send).
    changed_skus = [s for _p, _v, sks, _q in changed for s in sks]
    try:
        have = inventory_items_for_skus(db, changed_skus) if changed_skus else {}
    except Exception as exc:  # noqa: BLE001 -- a plan never raises
        logger.warning("[SHOPIFY_STOCK] target lookup failed for the plan: %s", exc)
        have = {}
    missing = sorted({s for s in changed_skus if not have.get(s)})
    # INVARIANT 2, in the backend verdict and not only in the React page: a
    # Shopify location that fulfils online orders and maps to no shop keeps
    # selling its own stale number. ONE read; zero network when DARK, and the
    # preview runs it too so "Preview first" and the press agree.
    live, reason = _live_or_reason(db)
    stray_locations = await unmapped_fulfilling_locations(db, mapped) if live else []
    payload: Dict[str, Any] = {
        "candidates": len(pairs),
        "changed": len(changed),
        "unchanged": len(pairs) - len(changed),
        "stores_total": len(stores),
        "stores_mapped": len(mapped),
        "unmapped_stores": holders,
        "unknown_stores": _unknown_stores(
            quantities, [s.get("store_id") for s in stores], all_skus
        ),
        "orphan_stores": orphans,
        "target_missing": missing,
        "unmapped_locations": stray_locations,
        "plan": [
            {"product_id": p.get("id") or p.get("product_id"), "quantities": q}
            for p, _v, _s, q in changed[:50]
        ],
    }
    unknown = set(payload["unknown_stores"])
    no_mapping = bool(changed) and not mapped

    def _verdict() -> Tuple[Optional[str], Optional[str]]:
        # ONE ladder, shared with push_skus_stock -- the SAME verdict for the
        # preview, the press and the POS write-back.
        return _verdict_for(
            no_mapping=no_mapping,
            conflicts=conflicts,
            holders=holders,
            stray_locations=stray_locations,
            unknown_error=(
                _unknown_error(_labels(stores, sorted(unknown))) if unknown else None
            ),
            orphans=orphans,
            missing=missing,
        )

    def _all_ok() -> bool:
        return not (
            no_mapping or conflicts or holders or stray_locations or unknown or orphans or missing
        )

    if not live or dry_run:
        code, error = _verdict()
        return PushResult(
            mode=MODE_SIMULATED,
            entity="stock",
            action="sync" if changed else "noop",
            ok=_all_ok(),
            payload=payload,
            reason=reason if not live else "dry_run (Preview first)",
            code=code,
            error=error,
        )
    # The mitigation the design promises is a TASK, not a summary line: a
    # steady state where nothing changed still has to reach the task board
    # (push_skus_stock only files one for a product it is actually sending).
    record_stray_locations(db, stray_locations)
    for h in holders:
        _file_unmapped_task(db, h)
    # The stray-location TASK (not the verdict) waits until there is something
    # to sell: with zero listings nothing can oversell from a location no shop
    # claims, and the owner's first 01:00 tick after the 2026-09-07 catalogue
    # deletion would otherwise hand him two unassigned P1s about a system with
    # nothing on it. The run still reports SHOPIFY_LOCATION_UNMAPPED.
    if pairs:
        for loc in stray_locations:
            _file_unmapped_location_task(db, loc)
    synced = 0
    failed = 0
    errors: List[str] = []
    product_code: Optional[str] = None
    accepted: List[Dict[str, Any]] = []
    for product, variants, _skus, _mine in changed:
        gid = (product.get("ecom") or {}).get("shopify_product_id")
        # No snapshot: the rule runs again inside, right before this write.
        res = await sync_product_stock(db, product, variants, _as_shopify_gid(gid, "Product"))
        unknown.update(res.get("unknown_stores") or [])
        accepted.append(
            {
                "product_id": product.get("id") or product.get("product_id"),
                "quantities": res.get("quantities") or {},
            }
        )
        # Nothing writable is a FAILED product, never a "synced" one: with no
        # mapped shop the page used to report "1 of 1 written" having written
        # nothing at all.
        if res.get("errors") or not res.get("stores_mapped"):
            failed += 1
            pid = product.get("id") or product.get("product_id")
            errors.append(f"{pid}: {res.get('error') or 'stock not written'}")
        else:
            synced += 1
        product_code = product_code or res.get("code")
    payload.update({
        "synced": synced,
        "failed": failed,
        "errors": errors[:20],
        "unknown_stores": sorted(unknown),
        # What Shopify ACCEPTED, not what was planned -- the sync page renders
        # `plan` under "Per shop:" as the numbers that reached the website, and
        # push_skus_stock has replaced its own summary["quantities"] with the
        # accepted rows since round 2. A location refused mid-pass must not
        # print as written here either.
        "plan": accepted[:50],
    })
    code, error = _verdict()
    code = code or product_code
    if errors and not error:
        error = "; ".join(errors[:3])
    return PushResult(
        mode=MODE_LIVE,
        entity="stock",
        action="sync" if changed else "noop",
        ok=failed == 0 and _all_ok(),
        payload=payload,
        code=code,
        error=error,
    )

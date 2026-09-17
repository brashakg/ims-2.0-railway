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
  * SHOPIFY_LOCATION_NOT_SELLING -- the MIRROR: a shop IS mapped, but its
    Shopify location is not ticked to fulfil online orders, is deactivated, or
    is gone from Shopify's list. Shopify counts online availability only at
    ticked locations, so every number IMS writes there is invisible and the
    listing reads SOLD OUT (and a vanished location refuses the write outright).
    ONE read answers both questions (``location_verdict``).
  * STOCK_TARGET_DUPLICATE -- two SKUs on one Shopify inventory item. Neither
    is written (Shopify holds one quantity per item and location, so one SKU's
    count would become the other's).
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

from datetime import datetime, timezone
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
# Two IMS SKUs stamped on ONE Shopify inventory item: Shopify holds exactly one
# quantity per (item, location), so one SKU's shelf would become the item's
# number for both. NEITHER is written -- the same answer as two shops on one
# location (STORE_LOCATION_DUPLICATE).
STOCK_TARGET_DUPLICATE = "STOCK_TARGET_DUPLICATE"
STORE_UNMAPPED = "STORE_UNMAPPED"
STOCK_ACTIVATION_FAILED = "STOCK_ACTIVATION_FAILED"
# Two shops claiming ONE Shopify location: Shopify takes one quantity per
# (item, location), so one shop's count would silently become the other's.
STORE_LOCATION_DUPLICATE = "STORE_LOCATION_DUPLICATE"
# On-hand parked at a store_id the `stores` collection does not know (the
# UUID-vs-code hazard): those units are published NOWHERE.
STOCK_STORE_ORPHAN = "STOCK_STORE_ORPHAN"
# A SKU the last-sent baseline still shows a POSITIVE number for that the
# listing no longer lists (a size row deleted off its parent): Shopify keeps
# selling it, IMS writes it nowhere and can no longer zero it -- the gid went
# with the row. Named every pass; only a human can close it.
STOCK_BASELINE_STRAY = "STOCK_BASELINE_STRAY"

# Shopify refused the write for a reason of its own (a location the owner
# deleted / deactivated, an invalid quantity): the rows in that call were NOT
# applied. Promoted so a refused write never reads as a codeless green run.
STOCK_WRITE_FAILED = "STOCK_WRITE_FAILED"
# A Shopify location that FULFILS ONLINE ORDERS but maps to no IMS shop:
# Shopify keeps routing and selling its own number and IMS never writes it.
SHOPIFY_LOCATION_UNMAPPED = "SHOPIFY_LOCATION_UNMAPPED"
# The MIRROR of it: an IMS shop IS mapped, but the Shopify location it points
# at cannot sell online (not ticked for online orders, deactivated, or gone
# from Shopify's list). Every number IMS writes there is invisible to the
# storefront, which reads SOLD OUT -- and for a location Shopify no longer
# lists, the write itself is refused, so a green preview would become a failed
# press.
SHOPIFY_LOCATION_NOT_SELLING = "SHOPIFY_LOCATION_NOT_SELLING"
# Shopify could not be asked what the verdict needs. Two doors raise it:
#   * the release door -- a RETRACTION could not be sent because the system is
#     DARK: everywhere else dark is a legitimate no-op, but a shop whose
#     location is being taken away while Shopify is still advertising its units
#     has a number that MUST come down first, and "we are dark" is not "there
#     is nothing to retract" for a system that was live yesterday;
#   * every LIVE stock pass (recheck round 1) -- Shopify's LOCATION LIST could
#     not be read (a throttle after 121 product pushes, a token blip, an empty
#     answer), so whether the mapped locations sell online, and whether an
#     unmapped one does, is UNKNOWN. It used to score as "no stray, no dead" --
#     green -- on the preview, the press and the sweep alike; unknown is
#     named, never scored as all clear. The mapped rows are still written.
SHOPIFY_UNREACHABLE = "SHOPIFY_UNREACHABLE"
# The retraction reached Shopify but IMS could not reset its OWN last-sent
# record for that shop. Transient and retryable -- and never a silent ok: with
# a stale baseline every listing that still carries the shop noops on the diff
# and the NEW location is written by nobody.
STOCK_BASELINE_NOT_RESET = "STOCK_BASELINE_NOT_RESET"

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


def baseline_strays(product: Dict[str, Any], skus: Iterable[str]) -> List[str]:
    """The SKUs this listing's last-sent baseline still shows a POSITIVE number
    for at some shop, and which the listing no longer lists -- i.e. what
    bettervision.in is advertising that IMS has stopped writing.

    ``stock_changed`` deliberately drops such a SKU from BOTH sides of the diff
    (permanent "changed" noise would bury a live STORE_UNMAPPED report), and
    that trade is right -- but dropped from the diff it was also dropped from
    the REPORT, so a size row deleted off a parent froze its Shopify number
    under a permanently green noop, and stayed on sale after its unit was sold
    at the counter. The number cannot be retracted from here (the row that
    carried the Shopify gid is gone), so it is NAMED instead, every pass, until
    a human re-adds the row or removes the variant in Shopify admin.

    A SKU the delist door zeroed properly is advertising nothing and is not
    named: only a positive last-sent number is a phantom."""
    keep = set(skus)
    out: List[str] = []
    for sku, rows in dict(_last_sent(product).get("quantities") or {}).items():
        if not sku or sku in keep or not isinstance(rows, dict):
            continue
        if any(int(q or 0) > 0 for q in rows.values()):
            out.append(str(sku))
    return sorted(out)


def listing_strays(db, listing_ids: Iterable[str]) -> List[str]:
    """``baseline_strays`` over the LISTINGS a batch touches -- the resolver
    for a door that holds only SKUs (the POS sale, the ingest claim, the
    restock, the transfer ship...) and for the press, whose listing id is in
    hand. The sweep asks the same question of every listing it loops
    (round-7 P2); asked NOWHERE on the per-product doors, the 'Send to
    website' press, the drawer preview and the sale's own run row all read
    green over a size the site kept selling (recheck round 1). Fail-soft: a
    report never blocks a write, and the next sweep names what a failed read
    here could not."""
    from ..online_catalog import variant_rows_for_product

    out: set = set()
    try:
        coll = db["catalog_products"]
        for pid in listing_ids:
            doc = coll.find_one({"id": pid})
            if doc:
                out.update(baseline_strays(doc, product_skus(doc, variant_rows_for_product(db, doc))))
    except Exception as exc:  # noqa: BLE001 -- a report never raises
        logger.warning("[SHOPIFY_STOCK] stray read failed for the listing(s): %s", exc)
    return sorted(out)


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


async def plan_product_stock(db, product: Dict[str, Any], variants: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    """The dry-run stock plan (SIMULATED branch): policy, the per-store
    quantity rows that WOULD be written at each mapped shop, and the shops
    that hold listed units but have no location. Read-only; zero network
    except the ONE locations read the press would make on day 1.

    It carries the WRITER'S OWN code/error (round 2 fixed this preview-vs-press
    divergence for ``sync_stock_levels`` and left the per-product plan behind):
    this dict is the ``stock`` block of the dry-run product push -- the preview
    an operator reads before a first publish -- so a shop list that could not
    be read, a duplicated location, an unmapped holder or "no shop mapped at
    all" must say so here exactly as the live press would, instead of a
    silently green plan over a press that writes nothing. ``stores_total`` is
    None (not 0) when the shop list itself is unknown.

    EVERY rung the press reads, not only the four mapping ones (recheck round
    1): the plan skipped the data-defect rungs -- a duplicated inventory item,
    a shop whose read died, an orphan store id, a stray baseline SKU, the
    whole-batch unknown -- and PRINTED both SKUs of a duplicated item as rows
    that would be written while the press wrote neither and coded
    STOCK_TARGET_DUPLICATE. The one rung deliberately NOT carried is a missing
    Shopify target: a never-pushed product has none yet by definition, and the
    press creates it."""
    from ..online_catalog import inventory_items_for_skus
    from ..online_stock_writeback import online_quantities_for_skus, orphan_stock_stores

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
    unknown_stores = _unknown_stores(quantities, [s.get("store_id") for s in stores], skus)
    orphans = orphan_stock_stores(db, skus)
    targets: Dict[str, str] = {}
    try:
        targets = inventory_items_for_skus(db, skus) if skus else {}
        duplicate_targets, unknown_read = _duplicates_or_error(db, targets)
    except Exception as exc:  # noqa: BLE001 -- a plan never raises; it says UNKNOWN
        duplicate_targets, unknown_read = {}, _target_error(exc)
    # EXACTLY the press's line (round-6 P3): the plan used to read the recorded
    # verdict with NO day-1 fallback while the press read Shopify itself when
    # nothing had ever been recorded, so on the rebuilt catalogue the preview
    # came back green (ok=True, code=None) over a press that codes
    # SHOPIFY_LOCATION_UNMAPPED. This module promises twice that a preview
    # reading green means a press would too; it has to ask the same question of
    # the same source.
    live, _reason = _live_or_reason(db)
    locations = await writer_location_verdict(db, mapped) if live else {}
    if read_error:
        # An unknown SHOP LIST outranks the ladder, exactly as it does in the
        # writer (which returns before it): with no list, "no shop is mapped"
        # would be a guess dressed as a fact.
        code: Optional[str] = STOCK_ONHAND_UNKNOWN
        error: Optional[str] = read_error
    else:
        code, error = _verdict_for(
            no_mapping=bool(skus) and not mapped,
            conflicts=conflicts,
            holders=holders,
            stray_locations=list(locations.get("stray") or []),
            dead_locations=list(locations.get("dead") or []),
            locations_unread=bool(live and not locations.get("read")),
            unknown_error=unknown_read
            or (_whole_batch_unknown_error() if skus and not quantities else None)
            or (_unknown_error(_labels(stores, unknown_stores)) if unknown_stores else None),
            orphans=orphans,
            stray_skus=baseline_strays(product, skus),
            duplicate_targets=duplicate_targets,
        )
    # The rows the press WOULD write: neither SKU of a duplicated item.
    planned = mapped_slice(quantities, mapped, skus)
    for sku in list(planned):
        inv = targets.get(sku)
        if inv and _as_shopify_gid(inv, "InventoryItem") in duplicate_targets:
            del planned[sku]
    return {
        "ok": code is None,
        "code": code,
        "error": error,
        "tracked": True,
        "policy": inventory_policy_for(product),
        "quantities": planned,
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


async def location_verdict(db, mapped: Dict[str, str]) -> Dict[str, Any]:
    """ONE read-only locations query, BOTH questions it can answer:

      * ``stray`` -- Shopify locations that FULFIL ONLINE ORDERS but map to NO
        IMS shop (``is_stray_fulfilling``). Shopify keeps routing and selling
        their own numbers there while IMS -- which writes per shop and never a
        pooled total -- never touches them, so the website oversells from a
        shelf nothing updates. It also covers a MAPPED but DEACTIVATED shop:
        physical_stores drops it, so its location is written by nobody and
        reported by no other guard -- but Shopify still lists it.
      * ``dead`` -- the MIRROR, which used to be asked NOWHERE in the backend
        (round-5 P1): a shop IS mapped, but its Shopify location cannot sell
        online (``dead_mapped_reason``). The design's own section 6 step 4 tells
        the owner to tick "fulfil online orders" for Gangadham Pune ONLY, and
        prod's three mapped shops are the Jharkhand ones -- so the state the
        runbook creates was a fully GREEN run (ok=True, code=None, no task)
        over a storefront showing all 121 products SOLD OUT, because Shopify
        counts online availability only at ticked locations.

    ``read`` is False when the query was dark, failed, or came back with no
    locations at all: a shop always has at least one location, so an empty list
    means the read told us NOTHING -- concluding "Shopify does not list any of
    your mapped locations" from it would be a guess, and a loud one.

    Fail-soft (a tripwire never blocks a write); zero network when DARK."""
    try:
        read = await list_locations(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOPIFY_STOCK] locations read failed for the verdict: %s", exc)
        return score_locations([], mapped)
    if read.get("reason"):
        return score_locations([], mapped)
    return score_locations(read.get("locations") or [], mapped)


def score_locations(
    rows: Iterable[Dict[str, Any]], mapped: Dict[str, str]
) -> Dict[str, Any]:
    """THE scorer, spelled ONCE: Shopify's own location list + the CURRENT
    mapping -> ``{stray, dead, read, rows}``. Pure -- no db, no network.

    Both halves are a function of the rows AND of ``mapped``, which is why the
    recorded verdict is re-scored here on every read instead of being replayed
    (round-6 P1/P2): a shop mapped AFTER the sweep was reported STRAY on every
    later press and POS write-back, and -- the silent direction -- a shop
    mapped after the sweep to a location that cannot sell online came back with
    ``dead: []``, a fully green press over a storefront reading SOLD OUT. The
    Shopify half only a human can change; the IMS half changes on the
    Organization page between two sweeps.

    ``read`` is False when the list is EMPTY: a shop always has at least one
    location, so an empty list means the read told us NOTHING -- concluding
    "Shopify does not list any of your mapped locations" from it would be a
    guess, and a loud one."""
    rows = [r for r in (rows or []) if isinstance(r, dict) and r.get("id")]
    if not rows:
        return {"stray": [], "dead": [], "read": False, "rows": []}
    have = set(mapped.values())
    by_gid = {r["id"]: r for r in rows}
    dead: List[Dict[str, Any]] = []
    for sid, gid in sorted(mapped.items()):
        why = dead_mapped_reason(by_gid.get(gid))
        if why:
            dead.append(
                {
                    "store_id": sid,
                    "location_id": gid,
                    "name": (by_gid.get(gid) or {}).get("name"),
                    "reason": why,
                }
            )
    return {
        "stray": [
            {"id": r.get("id"), "name": r.get("name")} for r in rows if is_stray_fulfilling(r, have)
        ],
        "dead": dead,
        "read": True,
        # What Shopify said, kept so a later caller can re-score it against ITS
        # OWN mapping without a second network read.
        "rows": [
            {
                "id": r.get("id"),
                "name": r.get("name"),
                "isActive": bool(r.get("isActive")),
                "fulfillsOnlineOrders": bool(r.get("fulfillsOnlineOrders")),
            }
            for r in rows
        ],
    }


def dead_mapped_reason(location: Optional[Dict[str, Any]]) -> Optional[str]:
    """THE mirror of ``is_stray_fulfilling``, spelled once: why this MAPPED
    shop's Shopify location cannot sell what IMS writes there, else None.

    ``location`` is the row from Shopify's own list, or None when Shopify does
    not list that gid at all -- which is the sharpest case, because the write
    itself will be refused and a green preview would then turn into a failed
    press (this module promises twice that a preview reading green means a
    press would too)."""
    if location is None:
        return "Shopify does not list this location any more"
    if not location.get("isActive"):
        return "deactivated in Shopify"
    if not location.get("fulfillsOnlineOrders"):
        return "not ticked to fulfil online orders, so the storefront reads sold out"
    return None


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


# The LAST LIVE sweep's location verdict, so a per-product press or a per-sale
# write-back can carry it without reading Shopify's locations every time.
_SYNC_STATE_COLLECTION = "online_sync_state"
_STRAY_LOCATIONS_DOC = "shopify_stray_locations"


# How long a recorded Shopify location list may be replayed before a caller
# re-reads it. The owner flips "Fulfill online orders" in Shopify admin between
# presses on the same afternoon; a verdict older than this is a guess. ONE
# MINUTE (recheck round 1): at ten minutes a press five minutes after an untick
# was still green and still wrote at the dead location. A minute is shorter
# than any human round-trip to Shopify admin and back, and still keeps a
# 200-product sweep to at most one read per minute of looping.
_LOCATION_VERDICT_TTL_SECONDS = 60


def _stale(at: Any) -> bool:
    """True when a recorded ``at`` is missing, unreadable or older than the TTL.
    A naive datetime is read as UTC (older records predate the tz-aware rule);
    anything that is not a datetime at all is stale, never fresh."""
    if not isinstance(at, datetime):
        return True
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return (_now() - at).total_seconds() > _LOCATION_VERDICT_TTL_SECONDS


def record_location_verdict(db, verdict: Dict[str, Any]) -> None:
    """Remember what SHOPIFY said -- the location rows, never the scored
    verdict (round-6 P1/P2). A verdict is rows PLUS the IMS mapping, and the
    mapping changes on the Organization page between two sweeps: replaying a
    scored snapshot is how a shop mapped after the sweep read as STRAY on every
    press, and how a shop remapped to a location that cannot sell online read
    as ``dead: []``. Stored raw, ``last_location_verdict`` re-scores.

    An unread verdict stores no rows, which reads back as "nothing recorded" --
    so the next caller does the one read itself instead of inheriting a failed
    read as "all clear". Fail-soft."""
    try:
        db[_SYNC_STATE_COLLECTION].update_one(
            {"_id": _STRAY_LOCATIONS_DOC},
            {"$set": {"rows": list(verdict.get("rows") or []), "at": _now()}},
            upsert=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[SHOPIFY_STOCK] location verdict not recorded: %s", exc)


def last_location_verdict(db, mapped: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """The last LIVE pass's Shopify location list, RE-SCORED against ``mapped``
    -- or None when no pass has ever recorded one. For a caller that must not
    read Shopify's locations every time: the POS write-back runs once per sale
    and the product press once per publish, while the SHOPIFY half of the
    verdict only changes when a human edits Shopify admin. The IMS half is the
    caller's own ``mapped``, read fresh every time, so a mapping change between
    two sweeps is seen immediately (round-6 P1/P2).

    None (not an empty verdict) matters: on day 1 no sweep has run yet, and
    "nothing recorded" must make the first publish press do the one read itself
    rather than read as "all clear". Fail-soft -> None.

    AND None once the rows are STALE (round-7 first-push). ``at`` was stamped by
    every recording and read by nobody, so the SHOPIFY half of the verdict was
    frozen until the next sweep -- up to 12 hours between the 01:00 and 09:00
    ticks. The owner unticks "Fulfill online orders" on a location in Shopify
    admin and the presses five minutes later come back ok=True, code=None,
    dead_locations=[] while writing numbers the storefront cannot sell, which is
    exactly the silent direction SHOPIFY_LOCATION_NOT_SELLING exists to close;
    the mirror is a false red that survives a fix. Design section 6 has the
    owner creating locations and flipping that tick interleaved with presses on
    the same afternoon, so the cache has to expire faster than he works.
    ponytail: a flat TTL, not a change feed -- one read per ten minutes of
    pressing is cheap; if it ever is not, record the verdict on the mapping
    save too."""
    try:
        doc = db[_SYNC_STATE_COLLECTION].find_one({"_id": _STRAY_LOCATIONS_DOC})
    except Exception as exc:  # noqa: BLE001
        logger.debug("[SHOPIFY_STOCK] location verdict unreadable: %s", exc)
        return None
    rows = [r for r in ((doc or {}).get("rows") or []) if isinstance(r, dict)]
    if not rows or _stale((doc or {}).get("at")):
        return None
    return score_locations(rows, mapped)


def last_stray_locations(db) -> List[Dict[str, Any]]:
    """Just the stray half of ``last_location_verdict`` (the POS write-back's
    sync_runs line), scored against the mapping as it is NOW. Fail-soft -> []."""
    try:
        mapped = _mapped(_stores(db))
    except Exception as exc:  # noqa: BLE001 -- a log line never blocks a write
        logger.debug("[SHOPIFY_STOCK] store list unknown for the stray line: %s", exc)
        return []
    return (last_location_verdict(db, mapped) or {}).get("stray") or []


async def writer_location_verdict(db, mapped: Dict[str, str]) -> Dict[str, Any]:
    """The verdict for a writer that runs PER PRODUCT or PER SALE: the last
    LIVE pass's recorded location rows scored against THIS caller's ``mapped``,
    or -- when nothing has ever been recorded (day 1, the owner's first "Send
    to website" press) -- ONE read of its own, recorded for the next caller.
    Zero network in the steady state, because the sweep records before it
    loops."""
    stored = last_location_verdict(db, mapped)
    if stored is not None:
        return stored
    verdict = await location_verdict(db, mapped)
    if verdict.get("read"):
        record_location_verdict(db, verdict)
    return verdict


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


def _whole_batch_unknown_error() -> str:
    """The STRICT whole-batch abort, spelled once for the press, the sweep and
    the plan: an absolute writer never fails soft to 0 for a whole batch."""
    return (
        "on-hand unknown for every listed SKU at every shop (spine/stock read "
        "failed) -- nothing written"
    )


def _claim_error(exc: Exception) -> str:
    """The claim read (``skus_claiming_inventory_items``) could not be made.
    {} from a swallowed exception reads as "nobody else claims this item" --
    the one answer that lets an absolute writer overwrite another SKU's shelf
    -- so it is UNKNOWN and nothing is written, on the press AND the sweep, in
    these words."""
    return (
        f"the Shopify inventory-item claim check could not be read -- nothing "
        f"written (two SKUs on one item would overwrite each other): {exc}"
    )


def _target_error(exc: Exception) -> str:
    """The TARGET read (``inventory_items_for_skus``) could not be made. It was
    fail-soft to {} -- and {} reads as "this SKU is not online", so a POS sale
    during a Mongo blip vanished as ``skipped_no_mapping`` with no run row, no
    task, while the sweep coded every changed SKU STOCK_TARGET_MISSING ("no
    Shopify inventory item mapped", a false statement about the data). One
    answer on every door: UNKNOWN, nothing written, these words."""
    return (
        f"the Shopify inventory-item mapping could not be read -- nothing "
        f"written (a SKU that cannot be read is not a SKU that is not online): {exc}"
    )


def _duplicates_or_error(db, targets: Dict[str, Any]) -> Tuple[Dict[str, List[str]], Optional[str]]:
    """``(duplicate_targets, claim_error)`` -- the STRICT claim read, spelled
    once for the preview, the press and the sweep: an unreadable guard is
    UNKNOWN in ``_claim_error``'s words, never "nobody else claims this"."""
    try:
        return (duplicate_inventory_items(db, targets) if targets else {}), None
    except Exception as exc:  # noqa: BLE001 -- the doors never raise; they refuse
        return {}, _claim_error(exc)


def _unread_locations_error() -> str:
    return (
        "Shopify's location list could not be read this pass, so whether the "
        "mapped locations sell online (and whether an unmapped one does) is "
        "unknown -- the mapped shops' numbers are still written; try again, or "
        "check Shopify admin > Locations"
    )


def _unknown_sku_error(skus: List[str]) -> str:
    """The SKU axis of the same unknown (the shop axis is ``_unknown_error``):
    no spine row, or the read for it failed."""
    return (
        f"on-hand unknown for {', '.join(skus[:5])} -- that listing is written "
        f"NOWHERE this pass (never as 0); the other listings were written"
    )


def _duplicate_error(conflicts: Dict[str, List[str]]) -> str:
    pairs = "; ".join(f"{gid} <- {', '.join(sorted(sids))}" for gid, sids in sorted(conflicts.items()))
    return (
        f"two shops share one Shopify location ({pairs}) -- neither was written "
        f"(one shop's count would overwrite the other's); give each shop its own "
        f"location on the Organization page"
    )


def duplicate_inventory_items(db, targets: Dict[str, Any]) -> Dict[str, List[str]]:
    """``{inventory_item_gid: [sku, ...]}`` for a Shopify inventory item claimed
    by MORE THAN ONE SKU -- the mirror of ``_location_conflicts`` on the other
    axis of the same (item, location) pair, spelled once so the writer and the
    preview cannot drift.

    ASKED OF THE WHOLE CATALOGUE, never of the batch (round-7 P1, oversell).
    The question used to be "do the SKUs in THIS call collide?", while the
    invariant is database-GLOBAL: Shopify holds one quantity per (item,
    location) whoever writes it. So every single-SKU door -- a POS sale, an
    ingest claim, a return restock, a transfer ship, a quarantine, a write-off
    -- saw one SKU, no collision, and wrote the SIZE's per-shop numbers onto the
    PARENT's inventory item, fully green, while the sweep over the SAME database
    refused that exact state as unwritable. ``_location_conflicts`` has always
    asked its axis of the whole shop list; this one now asks the reverse
    question of the catalogue (``skus_claiming_inventory_items``), which is the
    same shape and one indexed read.

    Shopify takes ONE quantity per (inventory item, location). The writer used
    to name the SECOND SKU and skip it, so the winner was decided by iteration
    order -- ``product_skus`` returns the variant rows (sorted by sku) before
    the product's own SKU, so an alphabetically earlier size row won by
    accident and Shopify showed ITS count for a variant IMS holds more units
    for. The baseline then held only the winner while the diff compared both,
    so the listing was "changed" with ok=False on every 01:00 / 09:00 pass
    forever and never self-healed."""
    claimed: Dict[str, List[str]] = {}
    spellings: set = set()
    for sku, inv in (targets or {}).items():
        if not inv:
            continue
        gid = _as_shopify_gid(inv, "InventoryItem")
        claimed.setdefault(gid, []).append(str(sku))
        # BOTH spellings of the item, whatever THIS row carries: the reverse
        # read matches the STORED string of the OTHER row, which may be the
        # bare id while this one is the full gid (a hand / repair-script
        # stamp). {gid, str(inv)} alone was both spellings only when this row
        # was the bare one -- the parent's sale wrote through a bare-id size.
        spellings.update({gid, str(inv), gid.rsplit("/", 1)[-1]})
    if db is not None and claimed:
        from ..online_catalog import skus_claiming_inventory_items

        for stored, skus in skus_claiming_inventory_items(db, sorted(spellings)).items():
            gid = _as_shopify_gid(stored, "InventoryItem")
            for sku in skus:
                if gid in claimed and sku not in claimed[gid]:
                    claimed[gid].append(sku)
    return {gid: sorted(skus) for gid, skus in claimed.items() if len(skus) > 1}


def _duplicate_target_error(duplicates: Dict[str, List[str]]) -> str:
    pairs = "; ".join(f"{gid} <- {', '.join(skus)}" for gid, skus in sorted(duplicates.items()))
    return (
        f"two SKUs share one Shopify inventory item ({pairs}) -- neither was "
        f"written (Shopify holds one quantity per item and location, so one "
        f"SKU's count would become the other's); give each SKU its own Shopify "
        f"variant, or clear the duplicated shopify_inventory_item_id"
    )


def _stray_sku_error(skus: List[str]) -> str:
    return (
        f"the website is still showing a quantity for {', '.join(skus[:5])}, which "
        f"this listing no longer lists -- IMS writes those numbers nowhere and can "
        f"no longer zero them (the row that carried the Shopify id is gone), so the "
        f"site keeps selling them; re-add the size row in IMS, or delete the variant "
        f"in Shopify admin"
    )


def _orphan_error(store_ids: List[str]) -> str:
    return (
        f"on-hand units sit at store id(s) no shop record matches "
        f"({', '.join(store_ids)}) -- they are published NOWHERE, so the website "
        f"under-sells; move them to a real shop or add the shop"
    )


def _dead_location_error(dead: List[Dict[str, Any]]) -> str:
    names = "; ".join(
        f"{d.get('store_id')} -> {d.get('name') or d.get('location_id')} "
        f"({d.get('reason')})"
        for d in dead
    )
    return (
        f"mapped shop(s) whose Shopify location cannot sell online: {names} -- the "
        f"numbers IMS writes there are invisible to bettervision.in (Shopify counts "
        f"online availability only at locations ticked to fulfil online orders), so "
        f"the listings read SOLD OUT; tick 'Fulfil online orders' and re-activate "
        f"the location in Shopify admin > Locations, or re-map the shop on the "
        f"Organization page"
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
    dead_locations: Optional[List[Dict[str, Any]]] = None,
    unknown_error: Optional[str] = None,
    orphans: Optional[List[str]] = None,
    stray_skus: Optional[List[str]] = None,
    duplicate_targets: Optional[Dict[str, List[str]]] = None,
    missing: Optional[List[str]] = None,
    locations_unread: bool = False,
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
    then an unmapped holder (the mapped shops WERE written), then the LOCATION
    rung -- a stray Shopify location and a MAPPED location that cannot sell
    online are ONE rung that names both, because on day 1 both are true at once
    -- then an unreadable shop, an orphan store id, a SKU the site still shows
    a number for that IMS no longer lists, a duplicated Shopify inventory item
    and last a missing Shopify target. The data-defect rungs stay at the BOTTOM
    on purpose: they are permanent until a human fixes the data, and a permanent
    code must never outrank -- and so hide -- a live STORE_UNMAPPED report."""
    # THE TWO LOCATION RUNGS ARE ONE RUNG (round-7 first-push). They are not
    # alternatives -- the state the design's own runbook creates on day 1 has
    # BOTH (Gangadham Pune ticked and deliberately mapped to no shop, the three
    # mapped Jharkhand locations not ticked yet) -- and a press surfaces exactly
    # one code + one error line. With the stray rung on top, that line sent the
    # owner to fix the location holding nothing and never told him why all 121
    # listings read SOLD OUT. Both are said; the storefront-wide one leads.
    lines = []
    if dead_locations:
        lines.append(_dead_location_error(list(dead_locations)))
    if stray_locations:
        lines.append(_stray_location_error(list(stray_locations)))
    location_line = " -- ALSO: ".join(lines)
    if no_mapping:
        return STORE_UNMAPPED, _no_mapping_error()
    if conflicts:
        return STORE_LOCATION_DUPLICATE, _duplicate_error(conflicts)
    if holders:
        # The location line rides under the holders rung too (recheck round 1,
        # the same shape one rung up): prod's one stock unit sits at Pune, Pune
        # is unmapped, and the runbook unticks the three Jharkhand locations --
        # so the press said "map BV-PUN-01" and nothing about every listing
        # reading SOLD OUT until the owner had mapped it and pressed again.
        return STORE_UNMAPPED, _unmapped_error(holders) + (
            f" -- ALSO: {location_line}" if location_line else ""
        )
    if locations_unread:
        # The location question was ASKED and not ANSWERED: stray and dead are
        # both unknown, and unknown is never "no stray, no dead".
        return SHOPIFY_UNREACHABLE, _unread_locations_error()
    if location_line:
        return (
            SHOPIFY_LOCATION_NOT_SELLING if dead_locations else SHOPIFY_LOCATION_UNMAPPED,
            location_line,
        )
    if unknown_error:
        return STOCK_ONHAND_UNKNOWN, unknown_error
    if orphans:
        return STOCK_STORE_ORPHAN, _orphan_error(list(orphans))
    if stray_skus:
        return STOCK_BASELINE_STRAY, _stray_sku_error(list(stray_skus))
    if duplicate_targets:
        return STOCK_TARGET_DUPLICATE, _duplicate_target_error(dict(duplicate_targets))
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
    locations: Optional[Dict[str, Any]] = None,
) -> bool:
    """ONE verdict for the SIMULATED and the LIVE branch: every guard counts,
    not just the two lists (a preview that reads green must mean a press would
    too, and "no shop mapped" means nothing was written at all). ``locations``
    is the stray/dead verdict -- invariant 2 was in the sweep's ``_all_ok``
    only, so the product press (the door all 121 first publishes go through)
    and the POS write-back reported green beside a Shopify location IMS never
    writes."""
    locations = locations or {}
    return bool(mapped) and not (
        summary["errors"]
        or holders
        or conflicts
        or summary["unknown_stores"]
        or summary["orphan_stores"]
        or summary.get("stray_skus")
        or locations.get("stray")
        or locations.get("dead")
        or summary.get("locations_unread")
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
    delisting: bool = False,
) -> Dict[str, Any]:
    """THE quantity path. For every listed SKU, one row per MAPPED shop (an
    explicit 0 included) at that shop's location, through ``set_inventory_
    quantities``. ``quantities`` is the rule's output unless a batch caller
    precomputed it. Gate: ``_live_or_reason`` -- SIMULATED plan with zero
    network when any gate is off or ``dry_run``.

    Summary: ``{ok, mode, source, candidates, quantities (rows written or
    planned, {sku: {store_id: qty}}), set, errors, code, error, stores_total,
    stores_mapped, unmapped_stores, unknown_stores, target_missing,
    unmapped_locations, dead_locations}``. ``ok`` is False on any error, an
    unmapped holder (STORE_UNMAPPED) or either location verdict -- the mapped
    rows are written either way. A LIVE call carries the last sweep's location
    verdict (zero network) and makes the ONE read-only locations query itself
    only when no pass has ever recorded one. Never raises."""
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
        "unmapped_locations": [],
        "dead_locations": [],
        "locations_unread": False,
        "stray_skus": [],
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
        summary["error"] = _whole_batch_unknown_error()
        return summary

    # ``delisting`` is the ONE exception to the holders question, and it is the
    # honest one (round-6 P6): the caller is taking this SKU OFF the website at
    # every shop, so "a shop holds it and has no location" is the INTENT, not a
    # fault. The delist door forced 0 everywhere and still got ok=False +
    # STORE_UNMAPPED + a deduped P1 task for a fully successful delist of any
    # size Gangadham Pune happens to hold -- a false P1 on prod today, because
    # `unmapped_holders` re-reads the rule at buffer 0 instead of reading the
    # caller's forced quantities.
    holders = [] if delisting else unmapped_holders(db, quantities, stores, distinct, mapped)
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
    # The TARGET read is STRICT too (recheck round 1): fail-soft to {} it read
    # as "not online" and a POS sale during a Mongo blip vanished with no run
    # row at all. Unknown is named, exactly as an unreadable shop list is.
    try:
        targets = inventory_items_for_skus(db, distinct)
    except Exception as exc:  # noqa: BLE001 -- the door never raises; it refuses
        summary["code"] = STOCK_ONHAND_UNKNOWN
        summary["error"] = _target_error(exc)
        return summary
    # Two SKUs on ONE Shopify inventory item: NEITHER is written. Naming the
    # second and writing the first let iteration order pick which shelf the
    # website showed, and left the loser out of the baseline while the diff
    # compared both -- ok=False and "changed" on every pass, forever.
    # The claim read is STRICT (round-7 P1): {} from a swallowed exception reads
    # as "nobody else claims this item", which is precisely the answer that lets
    # this absolute writer overwrite another SKU's shelf. An unreadable guard
    # aborts the batch, exactly as an unreadable shop list does above.
    duplicate_targets, unknown_read = _duplicates_or_error(db, targets)
    if unknown_read:
        summary["code"] = STOCK_ONHAND_UNKNOWN
        summary["error"] = unknown_read
        return summary
    for _gid, _skus in sorted(duplicate_targets.items()):
        summary["errors"].append(
            f"{', '.join(_skus)}: one Shopify inventory item ({_gid}) -- neither written"
        )
    # THE LISTING(S) this batch lands on, resolved ONCE: the baseline is
    # written per listing below, and the stray question (a SKU the site still
    # shows a number for that the listing no longer lists) is asked per listing
    # HERE -- on the press, the drawer preview's twin, and the sale's own run
    # row, not only on the sweep (recheck round 1).
    by_product = {product_id: list(distinct)} if product_id else listings_for_skus(db, distinct)
    summary["stray_skus"] = listing_strays(db, by_product)
    # INVARIANT 2, on THIS door too (round-5 P1 + first-push P1). This is the
    # door every first publish goes through (sync_product_stock) and every POS
    # sale goes through (writeback_skus), and it never asked Shopify's own
    # location list at all: a location that fulfils online orders with no shop
    # behind it, or a mapped shop whose location cannot sell online, came out
    # here as ok=True / code=None. The sweep's recorded verdict is carried (no
    # extra network) and read once when nothing was ever recorded.
    live, reason = _live_or_reason(db)
    locations = await writer_location_verdict(db, mapped) if live else {}
    summary["unmapped_locations"] = list(locations.get("stray") or [])
    summary["dead_locations"] = list(locations.get("dead") or [])
    summary["locations_unread"] = bool(live and not locations.get("read"))
    summary["code"], summary["error"] = _verdict_for(
        no_mapping=not mapped,
        conflicts=conflicts,
        holders=holders,
        stray_locations=summary["unmapped_locations"],
        dead_locations=summary["dead_locations"],
        locations_unread=summary["locations_unread"],
        unknown_error=(
            _unknown_error(_labels(stores, summary["unknown_stores"]))
            if summary["unknown_stores"]
            else None
        ),
        orphans=orphans,
        stray_skus=summary["stray_skus"],
        duplicate_targets=duplicate_targets,
    )

    rows: List[Tuple[str, str, int]] = []
    key_of: Dict[Tuple[str, str], Tuple[str, str]] = {}
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
        if inv_gid in duplicate_targets:
            continue
        for sid, loc in mapped.items():
            if sid not in per:
                # That shop's read failed: written nowhere this pass (named in
                # unknown_stores + the code, never counted as a failed push).
                summary["code"] = summary["code"] or STOCK_ONHAND_UNKNOWN
                continue
            rows.append((inv_gid, loc, int(per[sid])))
            key_of[(inv_gid, loc)] = (sku, sid)
            summary["quantities"].setdefault(sku, {})[sid] = int(per[sid])

    if not live or dry_run:
        summary["mode"] = MODE_SIMULATED
        summary["reason"] = reason if not live else "dry_run (Preview first)"
        summary["ok"] = _rows_ok(summary, holders, conflicts, mapped, locations)
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
        for pid, pid_skus in by_product.items():
            rows_for = {s: written_per_sku[s] for s in pid_skus if s in written_per_sku}
            if rows_for:
                _writeback_stock(db, pid, rows_for, policy=policy, tracked=tracked)
    # What Shopify ACCEPTED, not what was planned: the sync page prints these
    # as the per-shop "last written" numbers, and a refused chunk must not
    # read as written (the baseline above already only takes the accepted rows).
    summary["quantities"] = written_per_sku
    summary["ok"] = _rows_ok(summary, holders, conflicts, mapped, locations)
    if summary["errors"] and not summary["error"]:
        summary["error"] = "; ".join(str(e) for e in summary["errors"][:5])
    return summary


def _forget_store_baseline(db, store_id: str) -> int:
    """Drop ``store_id`` from the last-sent baseline of EVERY listing, so the
    next pass re-sends that shop at whatever location it now carries.

    The baseline is keyed by STORE id, not by LOCATION. Remapping a shop to a
    different Shopify location therefore changes nothing the diff can see, and
    the NEW location would never be written at all -- it would sit at whatever
    Shopify had (usually nothing, i.e. sold out) until some unrelated edit
    happened to move that shop's number.

    EVERY listing, never "the SKUs the shop happens to hold" (round-5 P2): the
    baseline carries a row for every listed SKU at that shop INCLUDING an
    explicit 0 (``_writeback_stock`` writes one per mapped shop and SKU), so
    forgetting only the HELD SKUs left the store key in place on every other
    listing -- those listings then matched the diff, noop'd, and the new
    location received no row for them at all. On the rebuilt 121-listing
    catalogue a corrected remap would re-arm one listing and leave ~120
    advertising the old location's numbers.

    STRICT -- it RAISES, and ``release_store_location`` (its only caller) turns
    that into a refused save. Fail-soft it was the same silent class one more
    layer down: the zeroing lands on Shopify, the reset dies on a Mongo blip,
    the door still answers ok, the save moves the gid -- and every listing whose
    baseline still carries this shop then matches the diff, noops, and the NEW
    location is never written at all. Returns the listings touched."""
    touched = 0
    coll = db["catalog_products"]
    # Keyed by STORE, so the store id is the only thing to look for -- no SKU
    # list, no listing resolver.
    docs = list(coll.find({"ecom.online_stock.quantities": {"$exists": True}}))
    for doc in docs:
        pid = doc.get("id")
        ecom = dict(doc.get("ecom") or {})
        stock = ecom.get("online_stock")
        if pid is None or not isinstance(stock, dict):
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
    return touched


def _baseline_skus_at(db, store_id: str) -> List[str]:
    """The SKUs the last-sent baseline says are showing a POSITIVE number at
    ``store_id`` -- i.e. what Shopify is ADVERTISING at that shop's location
    right now. STRICT: a read failure raises (the caller refuses the save).

    The SHELF is not that record (round-6 oversell P1). A write-back is
    fail-soft by design: after three units go SOLD, if Shopify refused the
    chunk or the token lapsed for ten minutes, the shelf reads 0 while
    ``ecom.online_stock.quantities`` still says 3 -- and 3 is what the website
    is selling. Releasing on the shelf alone wrote NOTHING, dropped the shop
    from every baseline and left the gid in no store's map, so ``_mapped``
    never targeted that location again: three phantom units, for ever, with the
    stray-location guard merely REPORTING it at the next 01:00 sweep."""
    out: set = set()
    for doc in db["catalog_products"].find({"ecom.online_stock.quantities": {"$exists": True}}):
        rows = ((doc.get("ecom") or {}).get("online_stock") or {}).get("quantities") or {}
        for sku, per in dict(rows).items():
            if isinstance(per, dict) and int(per.get(store_id, 0) or 0) > 0 and sku:
                out.add(str(sku))
    return sorted(out)


def _rearm_or_refuse(db, store_id: str, out: Dict[str, Any]) -> bool:
    """Re-arm the baseline and say whether the caller may save. The reset is
    STRICT, so a Mongo blip here is a REFUSED save, not a silent one: the
    zeroing has already landed on Shopify, and a stale baseline means every
    listing that still carries this shop noops on the diff and the NEW location
    is never written at all (the round-5 P2 hole, re-entered through a swallowed
    exception). Re-running the door is idempotent -- it writes 0 again."""
    try:
        out["forgot"] = _forget_store_baseline(db, store_id)
        return True
    except Exception as exc:  # noqa: BLE001 -- the door never raises; it refuses
        logger.warning("[SHOPIFY_STOCK] baseline reset failed for %s: %s", store_id, exc)
        out["ok"] = False
        out["code"] = STOCK_BASELINE_NOT_RESET
        out["error"] = (
            f"the location was cleared on Shopify but this shop's last-sent record "
            f"could not be reset ({exc}) -- nothing was saved; try again"
        )
        return False


async def release_store_location(db, store_id: str, location_gid: str) -> Dict[str, Any]:
    """Write 0 at ``location_gid`` for every listed SKU ``store_id`` holds, and
    ALWAYS forget that shop's baseline -- the ONE door behind every change or
    clear of ``stores.shopify_location_id``.

    Two separate jobs, two different conditions (round-5 P1/P2 -- they were one
    condition, and it was the wrong one):
      * the WRITE of 0 at the OLD location happens only when the shop holds
        listed units there; nothing else could leave a phantom. Without it the
        Organization page could only refuse the correction ("transfer the units
        out first"), so on a fresh setup the first wrong pick from a dropdown of
        similarly-named locations became permanent.
      * the FORGET happens on EVERY mapping change, whatever the shelf holds.
        The baseline is keyed by STORE, so the diff cannot see a remap: gated on
        the shelf, a re-map of a shop holding no listed stock was a fully green
        NOOP that never wrote the NEW location at all.

    WHAT TO ZERO is the UNION of the shelf and the baseline (round-6 oversell
    P1): the shelf is what the shop has, the baseline is what Shopify is
    showing, and a fail-soft write-back is exactly how the two come apart.

    DARK: ok with zero network only while the baseline says NOTHING was ever
    published at this shop (a dark system that never published has nothing to
    retract). With a live baseline behind it, DARK is an UNREACHABLE Shopify,
    not "nothing to release" (round-6 oversell P2) -- the creds read is itself
    fail-soft to False, so a ten-minute vault blip would otherwise save the new
    mapping, forget the baseline and leave the old location selling for ever.
    The baseline is re-armed only on a release that actually happened.

    Returns ``{ok, mode, set, skus, forgot, code, error}``; never raises.
    ``code`` is STOCK_ONHAND_UNKNOWN when the shelf could not be read (the
    caller refuses the save), SHOPIFY_UNREACHABLE when the system is dark with
    units published, STOCK_TARGET_MISSING when a SKU the location is
    advertising resolves to no Shopify inventory item (it cannot be retracted
    at all), STOCK_BASELINE_NOT_RESET when the zeroing landed but the last-sent
    record could not be re-armed, and the writer's own code when Shopify refused
    the zeroing."""
    from ..online_catalog import inventory_items_for_skus, listed_skus_on_hand_at

    out: Dict[str, Any] = {
        "ok": True, "mode": MODE_SIMULATED, "set": 0, "skus": [],
        "forgot": 0, "code": None, "error": None,
    }
    gid = _as_shopify_gid(location_gid, "Location") if location_gid else ""
    if not gid or not store_id:
        return out
    try:
        published = _baseline_skus_at(db, store_id)
        skus = sorted(set(listed_skus_on_hand_at(db, store_id)) | set(published))
    except Exception as exc:  # noqa: BLE001 -- STRICT read: refuse, never "nothing"
        out["ok"] = False
        out["code"] = STOCK_ONHAND_UNKNOWN
        out["error"] = f"could not read this shop's listed stock: {exc}"
        return out
    out["skus"] = skus
    live, reason = _live_or_reason(db)
    if not live:
        out["reason"] = reason
        if published:
            out["ok"] = False
            out["code"] = SHOPIFY_UNREACHABLE
            out["error"] = (
                f"Shopify is unreachable ({reason}), so the {len(published)} SKU(s) this "
                f"shop is advertising at that location cannot be zeroed -- try again once "
                f"the connection is back"
            )
            return out
        _rearm_or_refuse(db, store_id, out)
        return out
    out["mode"] = MODE_LIVE
    if skus:
        # ``inventory_items_for_skus`` is fail-SOFT ({} on a bad read), and {}
        # here reads as "nothing to zero" -- the round-6 oversell P1 failure
        # rebuilt one layer down, on the very path that closed it: the union
        # NAMES the SKU the old location is advertising and then writes no row
        # for it, forgets the baseline and lets the gid walk away. A SKU with no
        # Shopify inventory item cannot be retracted by ANY means, so the door
        # refuses BEFORE a single row goes out (half-released then forgotten is
        # the worst of the three outcomes) and the shop keeps its location, so
        # the 01:00 sweep still writes it. The read itself is STRICT now
        # (recheck round 1); a failed read is UNKNOWN, refused in its own words.
        try:
            targets = inventory_items_for_skus(db, skus)
        except Exception as exc:  # noqa: BLE001 -- the door never raises; it refuses
            out["ok"] = False
            out["code"] = STOCK_ONHAND_UNKNOWN
            out["error"] = _target_error(exc)
            return out
        unreachable = [s for s in skus if not targets.get(s)]
        if unreachable:
            out["ok"] = False
            out["code"] = STOCK_TARGET_MISSING
            out["error"] = (
                f"{', '.join(unreachable[:5])} has no Shopify inventory item, so what that "
                f"location is showing for it cannot be zeroed -- nothing was released"
            )
            return out
        rows = [
            (_as_shopify_gid(inv, "InventoryItem"), gid, 0)
            for inv in dict.fromkeys(targets.values())
            if inv
        ]
        if rows:
            written = await set_inventory_quantities(db, rows)
            out["set"] = written["set"]
            if written["errors"]:
                out["ok"] = False
                out["code"] = written.get("code") or STOCK_WRITE_FAILED
                out["error"] = "; ".join(str(e) for e in written["errors"][:3])
                return out
    _rearm_or_refuse(db, store_id, out)
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
    mid-way is written, not overwritten with the snapshot. Residual, known
    and carried over, not built here: one round-trip and cross-worker (a
    sale between a product's recompute and its inventorySetQuantities);
    closing it needs a per-SKU version, and until then the next sale's own
    write-back or the next tick re-sends the true number. DARK -> a SIMULATED plan and zero
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
            error=_whole_batch_unknown_error(),
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
    # ...and the other half of the same (item, location) pair: a Shopify
    # inventory item claimed by more than one SKU ANYWHERE in the catalogue.
    # Asked of the whole catalogue, the way the press asks it, so the preview
    # and the press agree exactly -- and so two SEPARATE listings stamped with
    # one item (ok=True, synced=2, the second call overwriting the first) are
    # caught, which a per-listing question never could.
    duplicate_targets: Dict[str, List[str]] = {}
    have: Dict[str, str] = {}
    # A failed TARGET read is UNKNOWN in the press's words (recheck round 1),
    # never "no Shopify inventory item mapped" for every changed SKU -- a false
    # statement about the data; and the CLAIM read has its own answer for the
    # same reason (round-7 P1). Neither fills `missing`.
    try:
        have = inventory_items_for_skus(db, changed_skus) if changed_skus else {}
        duplicate_targets, unknown_read = _duplicates_or_error(db, have)
    except Exception as exc:  # noqa: BLE001 -- a plan never raises; it says UNKNOWN
        unknown_read = _target_error(exc)
    missing = [] if unknown_read else sorted({s for s in changed_skus if not have.get(s)})
    # ...and the question NO guard asked: which SKU could not be READ at all.
    # `unknown_stores` answers "which SHOP failed" and `target_missing` answers
    # "which SKU has no Shopify item"; a SKU with no spine row -- the
    # catalogue-stray class this repo documents, and what a transient Mongo
    # failure in _sku_to_pid looks like -- is absent from `quantities`
    # ENTIRELY, so it fell through both and the preview read green over a press
    # that aborts the product with STOCK_ONHAND_UNKNOWN (round-6 first-push
    # P3). The tell was already in the payload and unacted on: a `plan` row
    # with `quantities: {}`.
    #
    # EVERY LISTED SKU, not only the changed ones (round-7 one-rule P1). Scoped
    # to `changed_skus` this named the SKU LOUDLY on the first pass and went
    # SILENT for ever after: `stock_changed` restricts both sides of the diff to
    # the product's current SKUs, so an unreadable SKU keeps the listing at
    # "unchanged", drops out of `changed_skus`, and is named by NOTHING again --
    # while the press has already set tracked=true + DENY on its Shopify variant
    # and nobody is writing its number. Invariant 5/6 is one rule: unknown is
    # named on EVERY pass, mapped or not, changed or not.
    unknown_skus = sorted({s for s in all_skus if s not in quantities})
    # ...and the SKUs that left the catalogue while Shopify still shows a
    # POSITIVE number for them (round-7 P2, phantom stock): dropped from BOTH
    # sides of the diff, they froze under a permanently green noop. IMS can no
    # longer zero them -- the gid went with the row -- so naming them every pass
    # is the whole of what is left to do.
    stray_skus = sorted({s for p, v in pairs for s in baseline_strays(p, product_skus(p, v))})
    # INVARIANT 2, in the backend verdict and not only in the React page: a
    # Shopify location that fulfils online orders and maps to no shop keeps
    # selling its own stale number. ONE read; zero network when DARK, and the
    # preview runs it too so "Preview first" and the press agree.
    live, reason = _live_or_reason(db)
    locations = await location_verdict(db, mapped) if live else {}
    # Recorded HERE, before the dry-run return (round-6 P4): "Preview first" is
    # the step the first-push runbook makes mandatory, and it computed the
    # truth and threw it away -- the owner read the correct red verdict, then
    # pressed "Send to website" and that press carried the stale cache. The one
    # button the runbook tells him to press could not fix what it had just
    # measured.
    if live:
        record_location_verdict(db, locations)
    stray_locations = list(locations.get("stray") or [])
    dead_locations = list(locations.get("dead") or [])
    # ASKED and not ANSWERED is its own line (recheck round 1): a raised read,
    # a Shopify error body or an empty node list all came back stray=[] dead=[]
    # and nothing downstream looked at `read`, so a throttled read after 121
    # product pushes made the mandatory "Preview first" green over Pune stray
    # and three unticked Jharkhand locations.
    locations_unread = bool(live and not locations.get("read"))
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
        "unknown_skus": unknown_skus,
        "stray_skus": stray_skus,
        "unmapped_locations": stray_locations,
        "dead_locations": dead_locations,
        "locations_read": not locations_unread,
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
            dead_locations=dead_locations,
            locations_unread=locations_unread,
            unknown_error=unknown_read
            or (
                _unknown_error(_labels(stores, sorted(unknown)))
                if unknown
                else (_unknown_sku_error(unknown_skus) if unknown_skus else None)
            ),
            orphans=orphans,
            stray_skus=stray_skus,
            duplicate_targets=duplicate_targets,
            missing=missing,
        )

    def _all_ok() -> bool:
        return not (
            no_mapping
            or conflicts
            or holders
            or stray_locations
            or dead_locations
            or locations_unread
            or unknown_read
            or unknown
            or unknown_skus
            or orphans
            or stray_skus
            or duplicate_targets
            or missing
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
        #
        # `set` -- rows Shopify ACCEPTED -- is the only honest measure
        # (round-6 first-push P2). The two flags above miss the WHOLE-BATCH
        # abort inside push_skus_stock, which sets `code` + `error` and returns
        # with `errors == []` and `stores_mapped` ALREADY stamped: a
        # single-SKU listing whose SKU has no spine row, or any transient Mongo
        # failure mid-loop, scored as `synced`, and the sweep came back
        # ok=True / "N of N written, 0 failed" with an empty transcript. The
        # 121-product catalogue is single-SKU listings -- exactly the shape
        # that takes that branch (a mixed-SKU product hits the per-SKU
        # `errors.append` and was caught).
        if res.get("errors") or not res.get("stores_mapped") or not res.get("set"):
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

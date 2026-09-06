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

# Shopify's own userErrors code when an inventory item is not stocked at the
# location a quantity was set for (InventorySetQuantitiesUserErrorCode).
ITEM_NOT_STOCKED_AT_LOCATION = "ITEM_NOT_STOCKED_AT_LOCATION"

_POLICY_DENY = "DENY"
_POLICY_CONTINUE = "CONTINUE"

# Dedupe ref of the per-shop "map me" task (the parity pattern).
_UNMAPPED_TASK_REF = "shopify-store-unmapped:{store_id}"


# ---------------------------------------------------------------------------
# The shop list (one reader) and the locations read (one read)
# ---------------------------------------------------------------------------


def _stores(db) -> List[Dict[str, Any]]:
    """``physical_stores(db)`` -- propagates a Mongo error so the caller can
    treat the whole batch as UNKNOWN (an unknown shop list must never read as
    'no shops')."""
    from ..stores_util import physical_stores

    return physical_stores(db)


def _mapped(stores: Iterable[Dict[str, Any]]) -> Dict[str, str]:
    """``{store_id: location_gid}`` for the mapped shops."""
    out: Dict[str, str] = {}
    for s in stores:
        gid = str(s.get("shopify_location_id") or "").strip()
        sid = str(s.get("store_id") or "").strip()
        if sid and gid:
            out[sid] = _as_shopify_gid(gid, "Location")
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
    """The SKUs whose quantities this product lists: one per variant row, or
    the product's own SKU when it has no variant rows."""
    out: List[str] = []
    for v in variants or []:
        sku = str((v or {}).get("sku") or "").strip()
        if sku and sku not in out:
            out.append(sku)
    if not out:
        sku = str(product.get("sku") or "").strip()
        if sku:
            out.append(sku)
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


def stock_changed(product: Dict[str, Any], quantities: Dict[str, Dict[str, int]]) -> bool:
    """True when the per-store quantities (the MAPPED slice -- the caller
    builds it with ``mapped_slice``) differ from the ones last sent, or the
    product was never sent / never had tracking switched on. Nested dicts
    compare deep; the pre-per-store flat ``{sku: qty}`` baseline never equals
    the nested shape, so the first pass after deploy re-sends everything."""
    last = _last_sent(product)
    if not last.get("tracked"):
        return True
    return dict(last.get("quantities") or {}) != dict(quantities)


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
    quantities: Dict[str, Dict[str, int]],
    stores: Iterable[Dict[str, Any]],
    skus: Iterable[str],
) -> List[Dict[str, Any]]:
    """The UNMAPPED shops that hold at least one unit of any of ``skus``:
    ``[{store_id, store_code, store_name, units}]``. A shop holding nothing
    is not listed (it never blocks)."""
    skus = list(skus)
    out: List[Dict[str, Any]] = []
    for s in stores:
        if str(s.get("shopify_location_id") or "").strip():
            continue
        sid = str(s.get("store_id") or "")
        units = sum(int((quantities.get(sku) or {}).get(sid, 0) or 0) for sku in skus)
        if units > 0:
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
    quantities: Dict[str, Dict[str, int]], mapped: Dict[str, str], skus: Iterable[str]
) -> List[str]:
    """Mapped shops absent from EVERY listed SKU's row -- their on-hand read
    failed this pass (the rule omits a shop it could not read)."""
    skus = [s for s in skus if s in quantities]
    if not skus:
        return []
    return sorted(sid for sid in mapped if all(sid not in quantities[s] for s in skus))


def plan_product_stock(db, product: Dict[str, Any], variants: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    """The dry-run stock plan (SIMULATED branch): policy, the per-store
    quantity rows that WOULD be written at each mapped shop, and the shops
    that hold listed units but have no location. Read-only, zero network."""
    from ..online_stock_writeback import online_quantities_for_skus

    skus = product_skus(product, variants)
    quantities = online_quantities_for_skus(db, skus) if skus else {}
    try:
        stores = _stores(db)
    except Exception as exc:  # noqa: BLE001 -- a plan must never raise
        logger.warning("[SHOPIFY_STOCK] store list unknown for the plan: %s", exc)
        stores = []
    mapped = _mapped(stores)
    return {
        "tracked": True,
        "policy": inventory_policy_for(product),
        "quantities": mapped_slice(quantities, mapped, skus),
        "stores_mapped": len(mapped),
        "stores_total": len(stores),
        "unmapped_stores": unmapped_holders(quantities, stores, skus),
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
        return str(exc), set()
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
    chunk's locations, then ONE retry; a second failure is the chunk's error
    under STOCK_ACTIVATION_FAILED. Stateless: no "activated" bookkeeping, so it
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
        chunk = entries[i : i + _INVENTORY_SET_MAX]
        err, codes = await _set_chunk(db, chunk)
        if err and ITEM_NOT_STOCKED_AT_LOCATION in codes:
            act_errors = await _activate_chunk(db, chunk)
            out["activated"] += len({r["inventoryItemId"] for r in chunk})
            err, _codes = await _set_chunk(db, chunk)
            if err:
                out["code"] = STOCK_ACTIVATION_FAILED
                err = f"{STOCK_ACTIVATION_FAILED}: {err}" + (
                    f" (activation: {'; '.join(act_errors)})" if act_errors else ""
                )
        if err:
            out["errors"].append(err)
            continue
        out["set"] += len(chunk)
        out["written"].extend((r["inventoryItemId"], r["locationId"], r["quantity"]) for r in chunk)
    return out


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


def _products_for_skus(db, skus: List[str]) -> Dict[str, List[str]]:
    """``{catalog product id: [skus]}`` -- which listing(s) carry each SKU: the
    product whose own sku it is, or the PARENT of the catalog_variants row
    that carries it (a size variant's row rides its parent's listing).
    Fail-soft ``{}``."""
    out: Dict[str, List[str]] = {}
    if not skus:
        return out
    try:
        coll = db["catalog_products"]
        for d in coll.find({"sku": {"$in": list(skus)}}):
            pid = str(d.get("id") or d.get("product_id") or "")
            sku = str(d.get("sku") or "").strip()
            if pid and sku and sku not in out.setdefault(pid, []):
                out[pid].append(sku)
        parent_skus: Dict[str, List[str]] = {}
        for v in db["catalog_variants"].find({"sku": {"$in": list(skus)}}):
            sku = str(v.get("sku") or "").strip()
            pid = str(v.get("parent_product_id") or "")
            if pid:
                if sku and sku not in out.setdefault(pid, []):
                    out[pid].append(sku)
            elif v.get("parent_sku"):
                parent_skus.setdefault(str(v["parent_sku"]), []).append(sku)
        if parent_skus:
            for d in coll.find({"sku": {"$in": list(parent_skus)}}):
                pid = str(d.get("id") or d.get("product_id") or "")
                for sku in parent_skus.get(str(d.get("sku") or ""), []):
                    if pid and sku and sku not in out.setdefault(pid, []):
                        out[pid].append(sku)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOPIFY_STOCK] sku -> listing lookup failed: %s", exc)
    return out


def _file_unmapped_task(db, store: Dict[str, Any]) -> None:
    """ONE deduped SYSTEM task per unmapped shop that holds listed stock
    (source_ref shopify-store-unmapped:<store_id>). Fail-soft."""
    try:
        from ..task_triggers import create_system_task
        from database.repositories.task_repository import TaskRepository

        coll = db.get_collection("tasks") if hasattr(db, "get_collection") else db["tasks"]
        if coll is None:
            return
        label = store.get("store_code") or store.get("store_name") or store.get("store_id")
        create_system_task(
            TaskRepository(coll),
            title=f"Map {label} to a Shopify location",
            description=(
                f"{label} holds {store.get('units', 0)} unit(s) of stock that is listed "
                f"on the website, but the shop has no Shopify location, so its stock "
                f"is invisible online until it is mapped: Organization page > edit the "
                f"shop > Shopify location. The mapped shops' quantities were still "
                f"written."
            ),
            priority="P1",
            category="Inventory",
            store_id=store.get("store_id"),
            dedupe_ref=_UNMAPPED_TASK_REF.format(store_id=store.get("store_id")),
            extra={"payload": {"code": STORE_UNMAPPED, "units": store.get("units", 0)}},
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[SHOPIFY_STOCK] unmapped-store task skipped: %s", exc)


def _unmapped_error(holders: List[Dict[str, Any]]) -> str:
    names = ", ".join(str(h.get("store_code") or h.get("store_name") or h.get("store_id")) for h in holders)
    return (
        f"shops holding listed stock with no Shopify location: {names} -- map them "
        f"on the Organization page (their stock is invisible online until then)"
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
    from ..online_catalog import inventory_items_for_skus
    from ..online_stock_writeback import online_quantities_for_skus

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

    holders = unmapped_holders(quantities, stores, distinct)
    summary["unmapped_stores"] = holders
    summary["unknown_stores"] = _unknown_stores(quantities, mapped, distinct)
    if holders:
        summary["code"] = STORE_UNMAPPED
        summary["error"] = _unmapped_error(holders)

    targets = inventory_items_for_skus(db, distinct)
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
        for sid, loc in mapped.items():
            if sid not in per:
                # That shop's read failed: written nowhere this pass (named in
                # unknown_stores + the code, never counted as a failed push).
                summary["code"] = summary["code"] or STOCK_ONHAND_UNKNOWN
                continue
            rows.append((inv_gid, loc, int(per[sid])))
            key_of[(inv_gid, loc)] = (sku, sid)
            summary["quantities"].setdefault(sku, {})[sid] = int(per[sid])

    if summary["unknown_stores"] and not summary["error"]:
        summary["error"] = (
            f"on-hand unknown at {', '.join(summary['unknown_stores'])} -- written "
            f"nowhere this pass (never as 0); the other shops were written"
        )
    live, reason = _live_or_reason(db)
    if not live or dry_run:
        summary["mode"] = MODE_SIMULATED
        summary["reason"] = reason if not live else "dry_run (Preview first)"
        summary["ok"] = not summary["errors"] and not holders and not summary["unknown_stores"]
        if summary["errors"] and not summary["error"]:
            summary["error"] = "; ".join(str(e) for e in summary["errors"][:5])
        return summary

    summary["mode"] = MODE_LIVE
    for h in holders:
        _file_unmapped_task(db, h)
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
            else _products_for_skus(db, list(written_per_sku))
        )
        for pid, pid_skus in by_product.items():
            rows_for = {s: written_per_sku[s] for s in pid_skus if s in written_per_sku}
            if rows_for:
                _writeback_stock(db, pid, rows_for, policy=policy, tracked=tracked)
    summary["ok"] = not summary["errors"] and not holders and not summary["unknown_stores"]
    if summary["errors"] and not summary["error"]:
        summary["error"] = "; ".join(str(e) for e in summary["errors"][:5])
    return summary


async def sync_product_stock(
    db,
    product: Dict[str, Any],
    variants: Optional[List[Dict[str, Any]]],
    product_gid: str,
    *,
    extra_variant_gids: Optional[List[Optional[str]]] = None,
    quantities: Optional[Dict[str, Dict[str, int]]] = None,
) -> Dict[str, Any]:
    """LIVE-only (the caller has passed the gates): tracking + policy on every
    known variant, then ``push_skus_stock`` for this product's SKUs -- one row
    per mapped shop per SKU. ``quantities`` may be precomputed by a batch
    caller (the rule once for the whole catalogue); else computed here.
    Fail-soft summary, never raises; a shop whose on-hand is UNKNOWN is never
    written as 0."""
    pid = product.get("id") or product.get("product_id")
    policy = inventory_policy_for(product)
    gids = product_variant_gids(product, variants, extra_variant_gids)
    tracked: Dict[str, Any] = {"updated": 0, "errors": []}
    if gids:
        tracked = await _set_variant_tracking(db, product_gid, gids, policy)
    else:
        tracked["errors"].append("no variant gid known -- tracking not set")

    skus = product_skus(product, variants)
    summary = await push_skus_stock(
        db,
        skus,
        quantities=quantities,
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
    for p in products:
        pid = str(p.get("id") or p.get("product_id") or "")
        rows = by_pid.get(pid) or by_sku.get(str(p.get("sku") or "")) or []
        out.append((p, rows))
    return out


async def sync_stock_levels(db, *, dry_run: bool = False) -> PushResult:
    """Send every product on Shopify whose per-store numbers CHANGED since they
    were last sent (or was never sent / never tracked). ONE call of the rule
    for every listed SKU, the diff over the MAPPED shops, then one tracking +
    one quantity write per changed product. DARK -> a SIMULATED plan and zero
    network; ``dry_run=True`` returns that SIMULATED plan EVEN WHEN LIVE (the
    "Preview first" press). Never raises. entity="stock", action="sync" (or
    "noop" when nothing changed). The ONE function behind the Push-stock
    button, the all-pending sweep and the 01:00 / 09:00 scheduled sync."""
    from ..online_stock_writeback import online_quantities_for_skus

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
        if stock_changed(product, mine):
            changed.append((product, variants, skus, mine))
    holders = unmapped_holders(quantities, stores, all_skus)
    payload: Dict[str, Any] = {
        "candidates": len(pairs),
        "changed": len(changed),
        "unchanged": len(pairs) - len(changed),
        "stores_total": len(stores),
        "stores_mapped": len(mapped),
        "unmapped_stores": holders,
        "unknown_stores": _unknown_stores(quantities, mapped, all_skus),
        "plan": [
            {"product_id": p.get("id") or p.get("product_id"), "quantities": q}
            for p, _v, _s, q in changed[:50]
        ],
    }
    code: Optional[str] = STORE_UNMAPPED if holders else None
    error: Optional[str] = _unmapped_error(holders) if holders else None
    live, reason = _live_or_reason(db)
    if not live or dry_run:
        return PushResult(
            mode=MODE_SIMULATED,
            entity="stock",
            action="sync" if changed else "noop",
            ok=not holders,
            payload=payload,
            reason=reason if not live else "dry_run (Preview first)",
            code=code,
            error=error,
        )
    synced = 0
    failed = 0
    errors: List[str] = []
    for product, variants, skus, _mine in changed:
        gid = (product.get("ecom") or {}).get("shopify_product_id")
        res = await sync_product_stock(
            db,
            product,
            variants,
            _as_shopify_gid(gid, "Product"),
            quantities={s: quantities[s] for s in skus if s in quantities},
        )
        if res.get("errors"):
            failed += 1
            code = code or res.get("code")
            pid = product.get("id") or product.get("product_id")
            errors.append(f"{pid}: {res.get('error') or 'stock not written'}")
        else:
            synced += 1
            code = code or res.get("code")
    payload.update({"synced": synced, "failed": failed, "errors": errors[:20]})
    unknown = payload["unknown_stores"]
    if unknown:
        code = code or STOCK_ONHAND_UNKNOWN
        error = error or (
            f"on-hand unknown at {', '.join(unknown)} -- written nowhere this pass "
            f"(never as 0); the other shops were written"
        )
    if errors and not error:
        error = "; ".join(errors[:3])
    return PushResult(
        mode=MODE_LIVE,
        entity="stock",
        action="sync" if changed else "noop",
        ok=failed == 0 and not holders and not unknown,
        payload=payload,
        code=code,
        error=error,
    )

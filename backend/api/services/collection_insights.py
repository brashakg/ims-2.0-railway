"""
IMS 2.0 - Collection Insights  (Collections Phase 1, Track 2)
=============================================================
Read-only analytics over the materialised `collection_products` view: how much
stock a collection is holding, what it is worth, and how fast it is selling.

DATA SOURCES
  * `collection_products`  -- the materialised membership view written by
    collection_materializer ({collection_id, handle, sku, position,
    computed_at}). A parallel track is adding `product_id` onto those rows;
    this module CODES AGAINST product_id BEING PRESENT and falls back to a
    sku -> products join when it is null/missing.
  * `stock_units`          -- one row per physical unit (or batch). Status
    'AVAILABLE' means on-hand; `quantity` may be absent (= 1); `store_id`
    scopes a unit to a store; `product_id` links to the spine.
  * `orders`               -- sales. `created_at` is UTC; status CANCELLED /
    DRAFT rows are EXCLUDED; `items[]` carry product_id, qty/quantity,
    item_total and (when known) cost_at_sale.
  * `products`             -- the billing spine (product_id, sku, brand, model,
    mrp, offer_price, cost_price, images, category).

VALUATION (owner decision)
  Stock value uses cost_price when present, else offer_price -- and the mix is
  ALWAYS labelled via `value_basis`: 'cost' when every member holding stock has
  a cost, 'offer' when none do, 'mixed' otherwise (None when nothing is in
  stock). Selling-price valuation is SHOWN with a label, never hidden or
  silently mixed. `stock_value_mrp` is reported alongside.

TIME WINDOWS
  Movement runs as ONE 90-day orders aggregation whose day sub-groups are
  $dateTrunc'd WITH timezone 'Asia/Kolkata' -- orders are stored in UTC and an
  Indian evening sale (e.g. 20:30 IST = 15:00 UTC) must not split across days.
  d7/d30/d90 buckets are then derived in Python from those IST days.

CONTRACT
  * Every function takes the raw db object (real pymongo Database or the test
    MockDatabase -- both support db[name]) and is FAIL-SOFT: no DB / a failed
    aggregation -> zeros/empty, never a raise.
  * `$in` lists are chunked at 1000 ids per query.
  * Membership cap: the materializer stores at most 5000 members; a collection
    at exactly that count (or carrying a `membership_capped` flag) is reported
    `capped=True` so the consumer knows the analytics cover a truncated set.
  * No emoji (Windows cp1252).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import ecom_smart_rules

logger = logging.getLogger(__name__)

# $in chunk size for id lists (Mongo handles more, but 1000 keeps every query
# bounded and mirrors the repo convention).
_CHUNK = 1000

# The materializer's membership cap (_MEMBER_MAX in collection_materializer).
# A membership at exactly this count was almost certainly truncated.
_MEMBER_CAP = 5000

# days_of_cover ceiling -- "more than ~3 years of stock" reads as "not moving".
_COVER_CAP = 999.0

# IST is UTC+5:30 (no DST).
_IST_OFFSET = timedelta(hours=5, minutes=30)
_IST_TZ = "Asia/Kolkata"

_VIEW = "collection_products"


# ---------------------------------------------------------------------------
# small utilities
# ---------------------------------------------------------------------------


def _chunks(items: List, size: int = _CHUNK) -> Iterable[List]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _num(value: Any) -> Optional[float]:
    """Positive-number coercion; None for absent / non-numeric / <=0 values.
    A zero or negative cost/offer is junk data for valuation purposes -- treat
    it as absent rather than silently valuing stock at 0."""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    return n


def _utcnow() -> datetime:
    # Naive UTC -- matches how pymongo hands datetimes back and how the order
    # writers stamp created_at.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _ist_date(dt: datetime) -> date:
    """The IST calendar date of a UTC instant."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return (dt + _IST_OFFSET).date()


def _iso(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str) and value:
        return value
    return None


def _coll(db, name: str):
    try:
        return db[name]
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# membership
# ---------------------------------------------------------------------------


def _pids_by_sku(db, skus: List[str]) -> Dict[str, str]:
    """sku -> product_id from the `products` spine, chunked $in at 1000.
    Fail-soft -> {} (unresolvable SKUs simply stay unresolved)."""
    out: Dict[str, str] = {}
    if db is None or not skus:
        return out
    coll = _coll(db, "products")
    if coll is None:
        return out
    for chunk in _chunks(skus):
        try:
            for d in coll.find({"sku": {"$in": chunk}}):
                sku = d.get("sku")
                pid = d.get("product_id")
                if sku and pid and sku not in out:
                    out[sku] = str(pid)
        except Exception:  # noqa: BLE001
            continue
    return out


def _fold_member_rows(rows: List[Dict]) -> Tuple[List[str], List[str], List[str]]:
    """(product_ids, skus, skus_missing_pid) from raw view rows, de-duped in
    row order. Rows without a product_id contribute their sku to the fallback
    list."""
    pids: List[str] = []
    skus: List[str] = []
    missing: List[str] = []
    seen_pid: set = set()
    seen_sku: set = set()
    for r in rows:
        if not isinstance(r, dict):
            continue
        sku = r.get("sku")
        pid = r.get("product_id")
        if sku and sku not in seen_sku:
            skus.append(sku)
            seen_sku.add(sku)
        if pid:
            pid = str(pid)
            if pid not in seen_pid:
                pids.append(pid)
                seen_pid.add(pid)
        elif sku:
            missing.append(sku)
    return pids, skus, missing


def member_ids(db, collection_id: str, collection: Optional[Dict] = None) -> Dict:
    """A collection's member ids from the materialised view.

    Returns {"product_ids": [...], "skus": [...], "capped": bool}.

      * product_id is read straight off the view row when present; rows
        without one are resolved sku -> products (chunked $in at 1000).
      * capped = membership at the materializer's 5000 cap, OR an explicit
        `membership_capped` flag on the collection doc (another track may
        stamp it; we only read it).

    Fail-soft -> empty lists."""
    out = {"product_ids": [], "skus": [], "capped": False}
    if db is None or not collection_id:
        return out
    view = _coll(db, _VIEW)
    if view is None:
        return out
    try:
        rows = list(view.find({"collection_id": collection_id}))
    except Exception:  # noqa: BLE001
        return out

    pids, skus, missing = _fold_member_rows(rows)
    if missing:
        resolved = _pids_by_sku(db, missing)
        seen = set(pids)
        for sku in missing:
            pid = resolved.get(sku)
            if pid and pid not in seen:
                pids.append(pid)
                seen.add(pid)

    capped = len(rows) >= _MEMBER_CAP
    doc = collection
    if doc is None:
        ecom = _coll(db, "ecom_collections")
        if ecom is not None:
            try:
                doc = ecom.find_one({"collection_id": collection_id})
            except Exception:  # noqa: BLE001
                doc = None
    if isinstance(doc, dict) and doc.get("membership_capped"):
        capped = True

    return {"product_ids": pids, "skus": skus, "capped": capped}


def members_by_collection(db, collection_ids: List[str]) -> Dict[str, Dict]:
    """BATCHED membership for many collections: one chunked read over the view
    grouped by collection_id in Python, plus ONE sku-fallback join over the
    union of unresolved SKUs. Returns
    {collection_id: {"product_ids": [...], "skus": [...], "row_count": int}}.
    Fail-soft -> {}."""
    out: Dict[str, Dict] = {}
    if db is None or not collection_ids:
        return out
    view = _coll(db, _VIEW)
    if view is None:
        return out
    grouped: Dict[str, List[Dict]] = {}
    for chunk in _chunks(list(collection_ids)):
        try:
            for r in view.find({"collection_id": {"$in": chunk}}):
                cid = r.get("collection_id")
                if cid:
                    grouped.setdefault(cid, []).append(r)
        except Exception:  # noqa: BLE001
            continue

    folded: Dict[str, Tuple[List[str], List[str], List[str]]] = {}
    all_missing: List[str] = []
    seen_missing: set = set()
    for cid, rows in grouped.items():
        pids, skus, missing = _fold_member_rows(rows)
        folded[cid] = (pids, skus, missing)
        for sku in missing:
            if sku not in seen_missing:
                all_missing.append(sku)
                seen_missing.add(sku)

    resolved = _pids_by_sku(db, all_missing) if all_missing else {}
    for cid, (pids, skus, missing) in folded.items():
        seen = set(pids)
        for sku in missing:
            pid = resolved.get(sku)
            if pid and pid not in seen:
                pids.append(pid)
                seen.add(pid)
        out[cid] = {
            "product_ids": pids,
            "skus": skus,
            "row_count": len(grouped.get(cid, [])),
        }
    return out


# ---------------------------------------------------------------------------
# stock
# ---------------------------------------------------------------------------


def _stock_rollup(
    db, product_ids: List[str], store_id: Optional[str] = None, by_store: bool = False
) -> Dict:
    """On-hand units from stock_units (status 'AVAILABLE', quantity ifNull 1).

    Returns {product_id: units} -- or {(product_id, store_id): units} when
    by_store. Chunked $in at 1000. Fail-soft -> {} (and defensive against the
    MockCollection aggregate stub, which echoes raw docs)."""
    out: Dict = {}
    if db is None or not product_ids:
        return out
    coll = _coll(db, "stock_units")
    if coll is None:
        return out
    for chunk in _chunks(product_ids):
        match: Dict[str, Any] = {
            "product_id": {"$in": chunk},
            "status": "AVAILABLE",
        }
        if store_id:
            match["store_id"] = store_id
        gid: Dict[str, Any] = {"product_id": "$product_id"}
        if by_store:
            gid["store_id"] = "$store_id"
        pipeline = [
            {"$match": match},
            {"$group": {"_id": gid, "on_hand": {"$sum": {"$ifNull": ["$quantity", 1]}}}},
        ]
        try:
            rows = list(coll.aggregate(pipeline))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[COLL-INSIGHTS] stock rollup failed: %s", exc)
            continue
        for r in rows:
            if not isinstance(r, dict):
                continue
            rid = r.get("_id")
            qty = r.get("on_hand")
            if not isinstance(rid, dict) or not isinstance(qty, (int, float)):
                continue  # raw-doc echo from a mock aggregate stub -> skip
            pid = rid.get("product_id")
            if not pid:
                continue
            key = (str(pid), rid.get("store_id")) if by_store else str(pid)
            out[key] = out.get(key, 0) + int(qty)
    return out


def _product_price_map(db, product_ids: List[str]) -> Dict[str, Dict]:
    """product_id -> {cost_price, offer_price, mrp} from the spine (chunked)."""
    out: Dict[str, Dict] = {}
    if db is None or not product_ids:
        return out
    coll = _coll(db, "products")
    if coll is None:
        return out
    for chunk in _chunks(product_ids):
        try:
            for d in coll.find({"product_id": {"$in": chunk}}):
                pid = d.get("product_id")
                if pid and str(pid) not in out:
                    out[str(pid)] = {
                        "cost_price": d.get("cost_price"),
                        "offer_price": d.get("offer_price"),
                        "mrp": d.get("mrp"),
                    }
        except Exception:  # noqa: BLE001
            continue
    return out


def _value_stock(on_hand_by_pid: Dict[str, int], price_map: Dict[str, Dict]) -> Dict:
    """Value a {product_id: units} holding. Cost-first per product, offer as
    the labelled fallback (owner decision: selling-price valuation is SHOWN
    with a label, never hidden).

    Returns {"units_on_hand", "stock_value", "value_basis", "stock_value_mrp"}
    where value_basis is 'cost' | 'offer' | 'mixed' | None (None = nothing in
    stock)."""
    units = 0
    value = 0.0
    value_mrp = 0.0
    n_stocked = 0
    n_cost = 0
    for pid, qty in on_hand_by_pid.items():
        try:
            qty = int(qty)
        except (TypeError, ValueError):
            continue
        if qty <= 0:
            continue
        units += qty
        n_stocked += 1
        prices = price_map.get(pid, {}) or {}
        cost = _num(prices.get("cost_price"))
        offer = _num(prices.get("offer_price"))
        mrp = _num(prices.get("mrp"))
        if cost is not None:
            n_cost += 1
            value += cost * qty
        elif offer is not None:
            value += offer * qty
        value_mrp += (mrp or 0.0) * qty
    if n_stocked == 0:
        basis = None
    elif n_cost == n_stocked:
        basis = "cost"
    elif n_cost == 0:
        basis = "offer"
    else:
        basis = "mixed"
    return {
        "units_on_hand": units,
        "stock_value": round(value, 2),
        "value_basis": basis,
        "stock_value_mrp": round(value_mrp, 2),
    }


def stock_summary(db, members: Dict, store_id: Optional[str] = None) -> Dict:
    """Per-product + total on-hand and valuation for a membership dict (the
    output of member_ids / members_by_collection).

    Returns {
        "per_product": {pid: {"on_hand": int}},
        "units_on_hand": int,
        "stock_value": float, "value_basis": 'cost'|'offer'|'mixed'|None,
        "stock_value_mrp": float,
    }"""
    pids = list((members or {}).get("product_ids") or [])
    on_hand = _stock_rollup(db, pids, store_id=store_id)
    price_map = _product_price_map(db, [p for p, q in on_hand.items() if q > 0])
    valued = _value_stock(on_hand, price_map)
    return {
        "per_product": {pid: {"on_hand": qty} for pid, qty in on_hand.items()},
        **valued,
    }


# ---------------------------------------------------------------------------
# movement (sales)
# ---------------------------------------------------------------------------

# items[] may stamp qty or quantity; absent -> 1 (a line is at least one unit).
_QTY_EXPR = {"$ifNull": ["$items.qty", {"$ifNull": ["$items.quantity", 1]}]}
# cost_at_sale null/missing -> that line contributes NO cogs (and no cogs
# units, so margin coverage can be judged honestly). {$gt: [x, None]} is the
# standard "field is non-null" expression under BSON type ordering.
_HAS_COST = {"$gt": ["$items.cost_at_sale", None]}


def _movement_pipeline(
    chunk: List[str],
    cutoff: datetime,
    store_id: Optional[str],
    by_store: bool,
) -> List[Dict]:
    match: Dict[str, Any] = {
        "created_at": {"$gte": cutoff},
        "status": {"$nin": ["CANCELLED", "DRAFT", "HISTORICAL"]},
        "items.product_id": {"$in": chunk},
    }
    if store_id:
        match["store_id"] = store_id
    gid: Dict[str, Any] = {
        "product_id": "$items.product_id",
        # IST day: orders are UTC and Indian evenings (e.g. 20:30 IST = 15:00
        # UTC) must not split across days.
        "day": {"$dateTrunc": {"date": "$created_at", "unit": "day", "timezone": _IST_TZ}},
    }
    if by_store:
        gid["store_id"] = "$store_id"
    return [
        {"$match": match},
        {"$unwind": "$items"},
        {"$match": {"items.product_id": {"$in": chunk}}},
        {
            "$group": {
                "_id": gid,
                "units": {"$sum": _QTY_EXPR},
                "revenue": {"$sum": {"$ifNull": ["$items.item_total", 0]}},
                "cogs": {
                    "$sum": {
                        "$cond": [
                            _HAS_COST,
                            {"$multiply": ["$items.cost_at_sale", _QTY_EXPR]},
                            0,
                        ]
                    }
                },
                "cogs_units": {"$sum": {"$cond": [_HAS_COST, _QTY_EXPR, 0]}},
                "last_sold": {"$max": "$created_at"},
            }
        },
    ]


def _blank_windows() -> Dict[str, Any]:
    return {
        "units_d7": 0,
        "units_d30": 0,
        "units_d90": 0,
        "revenue_d30": 0.0,
        "revenue_d90": 0.0,
        "cogs_d30": 0.0,
        "cogs_units_d30": 0,
        "last_sold": None,
    }


def movement_summary(
    db,
    members: Dict,
    store_id: Optional[str] = None,
    days: int = 90,
    by_store: bool = False,
) -> Dict:
    """ONE orders aggregation over the last `days` (default 90) IST days for
    the membership, day-sub-grouped in Mongo ($dateTrunc, tz Asia/Kolkata) and
    folded into d7/d30/d90 windows in Python.

    Returns {
        "per_product": {pid: {units_d7, units_d30, units_d90, revenue_d30,
                              revenue_d90, cogs_d30, cogs_units_d30, last_sold}},
        "totals": {same keys aggregated},
        "per_store": {store_id: {"units_d30": int, "units_d90": int}},  # by_store only
    }
    Fail-soft -> zeros."""
    pids = list((members or {}).get("product_ids") or [])
    per_product: Dict[str, Dict] = {}
    per_store: Dict[Optional[str], Dict] = {}
    totals = _blank_windows()

    if db is None or not pids:
        return {"per_product": per_product, "totals": totals, "per_store": per_store}
    coll = _coll(db, "orders")
    if coll is None:
        return {"per_product": per_product, "totals": totals, "per_store": per_store}

    now = _utcnow()
    ist_today = _ist_date(now)
    # Window opens at IST midnight (days-1) days back, expressed in UTC, so the
    # d90 window is exactly `days` complete IST calendar days including today.
    window_start_ist = datetime.combine(ist_today - timedelta(days=days - 1), time.min)
    cutoff = window_start_ist - _IST_OFFSET  # naive UTC instant of that IST midnight

    for chunk in _chunks(pids):
        pipeline = _movement_pipeline(chunk, cutoff, store_id, by_store)
        try:
            rows = list(coll.aggregate(pipeline))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[COLL-INSIGHTS] movement aggregation failed: %s", exc)
            continue
        for r in rows:
            if not isinstance(r, dict):
                continue
            rid = r.get("_id")
            if not isinstance(rid, dict):
                continue  # raw-doc echo from a mock aggregate stub -> skip
            pid = rid.get("product_id")
            if not pid:
                continue
            pid = str(pid)
            day = rid.get("day")
            age = (ist_today - _ist_date(day)).days if isinstance(day, datetime) else 0
            units = r.get("units") or 0
            revenue = float(r.get("revenue") or 0)
            cogs = float(r.get("cogs") or 0)
            cogs_units = r.get("cogs_units") or 0
            last_sold = r.get("last_sold")

            bucket = per_product.setdefault(pid, _blank_windows())
            for tgt in (bucket, totals):
                tgt["units_d90"] += units
                tgt["revenue_d90"] += revenue
                if age < 30:
                    tgt["units_d30"] += units
                    tgt["revenue_d30"] += revenue
                    tgt["cogs_d30"] += cogs
                    tgt["cogs_units_d30"] += cogs_units
                if age < 7:
                    tgt["units_d7"] += units
                if isinstance(last_sold, datetime) and (
                    tgt["last_sold"] is None or last_sold > tgt["last_sold"]
                ):
                    tgt["last_sold"] = last_sold
            if by_store:
                sid = rid.get("store_id")
                srow = per_store.setdefault(sid, {"units_d30": 0, "units_d90": 0})
                srow["units_d90"] += units
                if age < 30:
                    srow["units_d30"] += units

    for tgt in [totals] + list(per_product.values()):
        tgt["revenue_d30"] = round(tgt["revenue_d30"], 2)
        tgt["revenue_d90"] = round(tgt["revenue_d90"], 2)
        tgt["cogs_d30"] = round(tgt["cogs_d30"], 2)
    return {"per_product": per_product, "totals": totals, "per_store": per_store}


# ---------------------------------------------------------------------------
# derived KPI math (pure, unit-tested)
# ---------------------------------------------------------------------------


def sell_through(sold_30d: int, on_hand: int) -> Optional[float]:
    """sold30 / (sold30 + on_hand). None when both are 0 (no signal)."""
    sold_30d = max(int(sold_30d or 0), 0)
    on_hand = max(int(on_hand or 0), 0)
    denom = sold_30d + on_hand
    if denom == 0:
        return None
    return round(sold_30d / denom, 4)


def days_of_cover(on_hand: int, sold_30d: int) -> Optional[float]:
    """on_hand / (sold30/30), capped at 999. None when there is neither stock
    nor sales (no signal); 999 when stock exists but nothing sold in 30d."""
    sold_30d = max(int(sold_30d or 0), 0)
    on_hand = max(int(on_hand or 0), 0)
    if on_hand == 0 and sold_30d == 0:
        return None
    if sold_30d == 0:
        return _COVER_CAP
    return min(round(on_hand / (sold_30d / 30.0), 1), _COVER_CAP)


def margin_30d(revenue_30d: float, cogs_30d: float, units_30d: int, cogs_units_30d: int) -> Optional[float]:
    """revenue - cogs over the 30d window -- but ONLY when every sold unit
    carried a cost_at_sale (100% cogs coverage). Anything less would silently
    overstate margin, so it is labelled honestly as None."""
    units_30d = int(units_30d or 0)
    cogs_units_30d = int(cogs_units_30d or 0)
    if units_30d <= 0 or cogs_units_30d < units_30d:
        return None
    return round(float(revenue_30d or 0) - float(cogs_30d or 0), 2)


# ---------------------------------------------------------------------------
# composition
# ---------------------------------------------------------------------------


def kpis(db, collection: Dict, store_id: Optional[str] = None) -> Dict:
    """The full KPI block for one collection (see the router for the response
    envelope). Fail-soft -> zeroed block."""
    collection = collection if isinstance(collection, dict) else {}
    cid = collection.get("collection_id") or collection.get("id")
    members = member_ids(db, cid, collection=collection)
    stock = stock_summary(db, members, store_id=store_id)
    move = movement_summary(db, members, store_id=store_id, days=90)
    t = move["totals"]
    sold30 = t["units_d30"]
    on_hand = stock["units_on_hand"]
    return {
        "members": len(members["skus"]),
        "units_on_hand": on_hand,
        "stock_value": stock["stock_value"],
        "value_basis": stock["value_basis"],
        "stock_value_mrp": stock["stock_value_mrp"],
        "sold": {"d7": t["units_d7"], "d30": sold30, "d90": t["units_d90"]},
        "revenue_30d": t["revenue_d30"],
        "margin_30d": margin_30d(
            t["revenue_d30"], t["cogs_d30"], sold30, t["cogs_units_d30"]
        ),
        "sell_through_30d": sell_through(sold30, on_hand),
        "days_of_cover": days_of_cover(on_hand, sold30),
        "membership_capped": members["capped"],
        "materialized_at": _iso(collection.get("materialized_at")),
    }


def _store_names(db, store_ids: List[str]) -> Dict[str, str]:
    """store_id -> display name from the `stores` collection; fail-soft to the
    id itself."""
    out = {sid: sid for sid in store_ids if sid}
    if db is None or not store_ids:
        return out
    coll = _coll(db, "stores")
    if coll is None:
        return out
    try:
        for d in coll.find({"store_id": {"$in": [s for s in store_ids if s]}}):
            sid = d.get("store_id")
            if sid:
                out[sid] = str(
                    d.get("store_name") or d.get("name") or sid
                )
    except Exception:  # noqa: BLE001
        pass
    return out


def store_breakdown(db, collection: Dict, store_id: Optional[str] = None) -> List[Dict]:
    """Per-store rows for one collection: on-hand, valuation (+basis), 30-day
    sales, sell-through and days-of-cover. `store_id` (when set, e.g. a forced
    store scope) restricts the breakdown to that store. Fail-soft -> []."""
    collection = collection if isinstance(collection, dict) else {}
    cid = collection.get("collection_id") or collection.get("id")
    members = member_ids(db, cid, collection=collection)
    pids = members["product_ids"]

    stock_by = _stock_rollup(db, pids, store_id=store_id, by_store=True)
    move = movement_summary(db, members, store_id=store_id, days=90, by_store=True)

    # Union of stores seen in stock OR sales.
    sids: List[str] = []
    seen: set = set()
    for (_pid, sid) in stock_by.keys():
        if sid and sid not in seen:
            sids.append(sid)
            seen.add(sid)
    for sid in move["per_store"].keys():
        if sid and sid not in seen:
            sids.append(sid)
            seen.add(sid)

    price_map = _product_price_map(
        db, list({pid for (pid, _sid) in stock_by.keys()})
    )
    names = _store_names(db, sids)

    rows: List[Dict] = []
    for sid in sids:
        holding = {
            pid: qty for (pid, s), qty in stock_by.items() if s == sid and qty > 0
        }
        valued = _value_stock(holding, price_map)
        sold30 = int(move["per_store"].get(sid, {}).get("units_d30", 0))
        rows.append(
            {
                "store_id": sid,
                "store_name": names.get(sid, sid),
                "on_hand": valued["units_on_hand"],
                "stock_value": valued["stock_value"],
                "value_basis": valued["value_basis"],
                "sold_30d": sold30,
                "sell_through": sell_through(sold30, valued["units_on_hand"]),
                "days_of_cover": days_of_cover(valued["units_on_hand"], sold30),
            }
        )
    rows.sort(key=lambda r: (-(r["stock_value"] or 0), r["store_id"] or ""))
    return rows


def summary(db, collections: List[Dict], store_id: Optional[str] = None) -> List[Dict]:
    """BATCHED per-collection roll-up (no N+1): one chunked membership read
    grouped by collection_id, one stock rollup over the UNION of member
    product_ids, one movement aggregation over the same union -- then composed
    per collection in Python. A product in several collections counts in each.

    Returns rows sorted by sold_30d desc:
      [{collection_id, title, collection_type, published, members, on_hand,
        stock_value, value_basis, sold_30d}]
    Fail-soft -> []."""
    docs = [c for c in (collections or []) if isinstance(c, dict)]
    cids = [c.get("collection_id") or c.get("id") for c in docs]
    cids = [c for c in cids if c]
    if db is None or not cids:
        return []

    membership = members_by_collection(db, cids)

    union_pids: List[str] = []
    seen: set = set()
    for m in membership.values():
        for pid in m["product_ids"]:
            if pid not in seen:
                union_pids.append(pid)
                seen.add(pid)

    on_hand_by_pid = _stock_rollup(db, union_pids, store_id=store_id)
    price_map = _product_price_map(
        db, [p for p, q in on_hand_by_pid.items() if q > 0]
    )
    move = movement_summary(
        db,
        {"product_ids": union_pids},
        store_id=store_id,
        days=30,  # the summary surface only reports sold_30d
    )
    sold30_by_pid = {
        pid: row["units_d30"] for pid, row in move["per_product"].items()
    }

    rows: List[Dict] = []
    for doc in docs:
        cid = doc.get("collection_id") or doc.get("id")
        if not cid:
            continue
        m = membership.get(cid, {"product_ids": [], "skus": [], "row_count": 0})
        holding = {
            pid: on_hand_by_pid.get(pid, 0)
            for pid in m["product_ids"]
            if on_hand_by_pid.get(pid, 0) > 0
        }
        valued = _value_stock(holding, price_map)
        sold30 = sum(int(sold30_by_pid.get(pid, 0)) for pid in m["product_ids"])
        rows.append(
            {
                "collection_id": cid,
                "title": doc.get("title") or doc.get("name") or doc.get("handle"),
                "collection_type": (doc.get("collection_type") or "CUSTOM").upper(),
                "published": bool(doc.get("published", True)),
                "members": len(m["skus"]),
                "on_hand": valued["units_on_hand"],
                "stock_value": valued["stock_value"],
                "value_basis": valued["value_basis"],
                "sold_30d": sold30,
            }
        )
    rows.sort(key=lambda r: (-r["sold_30d"], -(r["stock_value"] or 0), str(r["title"] or "")))
    return rows


# ---------------------------------------------------------------------------
# rule preview (unsaved rules -> would-be membership)
# ---------------------------------------------------------------------------

_SAMPLE_MAX = 12


def _preview_row(doc: Dict) -> Dict:
    attrs = doc.get("attributes") if isinstance(doc.get("attributes"), dict) else {}
    images = doc.get("images")
    image = images[0] if isinstance(images, list) and images else doc.get("image")
    pricing = doc.get("pricing") if isinstance(doc.get("pricing"), dict) else {}
    return {
        "sku": doc.get("sku"),
        "brand": doc.get("brand") or attrs.get("brand") or attrs.get("brand_name"),
        "model": doc.get("model") or attrs.get("model") or attrs.get("model_no"),
        "title": doc.get("title") or doc.get("name"),
        "mrp": doc.get("mrp") or pricing.get("mrp"),
        "image": image or None,
    }


def preview(
    db,
    rules: Any,
    disjunctive: bool = False,
    store_id: Optional[str] = None,
) -> Dict:
    """Evaluate UNSAVED rules over the live catalogue -- the same haystack the
    materializer resolves against (products spine + catalog_products union,
    spine-wins, scan capped at 5000 by collection_materializer._SCAN_MAX) --
    via ecom_smart_rules.matches_product. Nothing is persisted.

    Returns {"match_count": int, "units_on_hand": int,
             "sample": [up to 12 {sku, brand, model, title, mrp, image}],
             "scanned": int, "scan_capped": bool}."""
    out = {
        "match_count": 0,
        "units_on_hand": 0,
        "sample": [],
        "scanned": 0,
        "scan_capped": False,
    }
    if db is None:
        return out
    norm_rules = ecom_smart_rules.normalize_rules(rules or [])
    try:
        from . import collection_materializer as _mat

        products = _mat._all_products(db)  # capped at _SCAN_MAX (5000)
        scan_cap = getattr(_mat, "_SCAN_MAX", 5000)
    except Exception:  # noqa: BLE001
        return out

    matched: List[Dict] = []
    seen: set = set()
    for p in products:
        if not isinstance(p, dict):
            continue
        sku = p.get("sku")
        if not sku or sku in seen:
            continue
        if ecom_smart_rules.matches_product(p, norm_rules, disjunctive=bool(disjunctive)):
            matched.append(p)
            seen.add(sku)

    # product_ids for the stock rollup: straight off the doc, sku-fallback for
    # catalogue rows that lack one.
    pids: List[str] = []
    seen_pid: set = set()
    missing: List[str] = []
    for p in matched:
        pid = p.get("product_id")
        if pid:
            pid = str(pid)
            if pid not in seen_pid:
                pids.append(pid)
                seen_pid.add(pid)
        elif p.get("sku"):
            missing.append(p["sku"])
    if missing:
        resolved = _pids_by_sku(db, missing)
        for sku in missing:
            pid = resolved.get(sku)
            if pid and pid not in seen_pid:
                pids.append(pid)
                seen_pid.add(pid)

    on_hand = _stock_rollup(db, pids, store_id=store_id)
    out["match_count"] = len(matched)
    out["units_on_hand"] = int(sum(q for q in on_hand.values() if q > 0))
    out["sample"] = [_preview_row(p) for p in matched[:_SAMPLE_MAX]]
    out["scanned"] = len(products)
    out["scan_capped"] = len(products) >= scan_cap
    return out

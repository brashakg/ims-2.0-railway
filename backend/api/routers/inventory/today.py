"""S7 (owner approved 2026-10-23): Stock today -- what one shop's manager has
to do today, on one read-only page (/inventory/today).

Nothing here writes and nothing here keeps a rule of its own: every section
asks the rule main already has.

  incoming     transfers TO the shop that have left the sender (in_transit,
               partially_received); pieces = what was shipped
               (transfers._line_expected_qty) minus what is scanned in.
  requests     transfers FROM the shop not shipped yet: the pre-ship statuses
               (transfers.CANCELLABLE_TRANSFER_STATUSES) minus draft/rejected.
  count        the shop's stock counts (/inventory/audit, stock_counts): any
               still open, and how many finished today (IST day).
  quarantine   QUARANTINED pieces here and how many have no red label yet
               (the Quarantine queue's label_printed=false filter).
  deliveries   open purchase orders delivering here with pieces still due
               (purchase_recon._stock_yet_to_receive), vendor named.
  stock_value  stock_value.shelf_units (THE stock value rule): total + pieces
               with no cost, for the roles that see stock at cost only
               (stock_value.COST_CONTEXT); None for everyone else.
  running_low  reorder_policy.low_stock_rows (the shop's own reorder level),
               with the pieces at every other shop (reorder_policy.on_hand)
               and units sold here in 30 days (collection_insights.
               movement_summary), fastest sellers first.

TODO(S3): transfers become piece-by-piece (scan out / scan in); incoming and
requests then read the scanned pieces instead of line quantities.
TODO(S4): one average cost per product; the stock value then reads it
through stock_value, with no change here.
TODO(S5): one Reorder list (shop level if set, else 30-day sales); running_low
then reads that rule instead of low_stock_rows.

The shop is store_in_view(one_shop=True): a manager's own shop; an admin's
top-bar shop, or every shop when none is picked (owner ruling 2026-10-18).
"""

from collections import defaultdict
from typing import Dict, List, Optional

from ._shared import (
    STOCK_STATUS_QUARANTINED,
    Depends,
    Query,
    get_product_repository,
    get_stock_repository,
    logger,
    require_roles,
    router,
)
from .helpers import _get_db
from ...dependencies import store_in_view
from ...services import collection_insights, reorder_policy, stock_value
from ...services.cost_mask import PURCHASE_ROLES, can_see_cost
from ...services.name_resolver import store_name_map, vendor_name_map
from ...utils.ist import ist_date_str_from_stored, ist_today

# How many running-low rows the page shows; the total says how many more.
RUNNING_LOW_SHOWN = 10


def _at(field: str, shop: Optional[str]) -> Dict:
    return {field: shop} if shop else {}


def _transfers(db, shop: Optional[str]) -> Dict[str, List[Dict]]:
    from ..transfers import (
        CANCELLABLE_TRANSFER_STATUSES,
        TransferStatus,
        _line_expected_qty,
        _qty,
    )

    on_the_way = {TransferStatus.IN_TRANSIT.value, TransferStatus.PARTIALLY_RECEIVED.value}
    to_pack = set(CANCELLABLE_TRANSFER_STATUSES) - {
        TransferStatus.DRAFT.value,
        TransferStatus.REJECTED.value,
    }
    coll = db.get_collection("stock_transfers")
    incoming, requests = [], []
    for t in coll.find({**_at("to_location_id", shop), "status": {"$in": sorted(on_the_way)}}, {"_id": 0}):
        pieces = sum(
            max(0, _line_expected_qty(ln) - _qty(ln.get("quantity_received")))
            for ln in t.get("items") or []
        )
        incoming.append({
            "transfer_id": t.get("id"),
            "transfer_number": t.get("transfer_number"),
            "from_store_id": t.get("from_location_id"),
            "from_store_name": t.get("from_location_name"),
            "to_store_id": t.get("to_location_id"),
            "pieces": pieces,
            "shipped_at": t.get("shipped_at"),
        })
    for t in coll.find({**_at("from_location_id", shop), "status": {"$in": sorted(to_pack)}}, {"_id": 0}):
        lines = [
            {"product_name": ln.get("product_name") or ln.get("sku") or "", "quantity": _qty(ln.get("quantity_requested"))}
            for ln in t.get("items") or []
        ]
        requests.append({
            "transfer_id": t.get("id"),
            "transfer_number": t.get("transfer_number"),
            "from_store_id": t.get("from_location_id"),
            "to_store_id": t.get("to_location_id"),
            "to_store_name": t.get("to_location_name"),
            "status": t.get("status"),
            "pieces": sum(ln["quantity"] for ln in lines),
            "lines": lines,
        })
    incoming.sort(key=lambda r: str(r.get("shipped_at") or ""))
    requests.sort(key=lambda r: str(r.get("transfer_number") or ""))
    return {"incoming": incoming, "requests": requests}


def _count(db, shop: Optional[str]) -> Dict:
    today = ist_today().isoformat()
    in_progress, counted_today = [], 0
    for c in db.get_collection("stock_counts").find(_at("store_id", shop), {"_id": 0}).sort("created_at", -1).limit(50):
        if c.get("status") == "in_progress":
            in_progress.append({
                "count_id": c.get("count_id"),
                "audit_number": c.get("audit_number"),
                "store_id": c.get("store_id"),
                "zone": c.get("zone"),
                "category": c.get("category"),
                "started_at": c.get("created_at"),
            })
        elif c.get("completed_at") and ist_date_str_from_stored(c.get("completed_at")) == today:
            counted_today += 1
    return {"in_progress": in_progress, "counted_today": counted_today}


def _quarantine(db, shop: Optional[str]) -> Dict[str, int]:
    coll = db.get_collection("stock_units")
    match = {**_at("store_id", shop), "status": STOCK_STATUS_QUARANTINED}
    return {
        "pieces": coll.count_documents(match),
        "unlabeled": coll.count_documents({**match, "quarantine_label_printed": {"$ne": True}}),
    }


def _deliveries(db, shop: Optional[str]) -> List[Dict]:
    from ..purchase_recon import _stock_yet_to_receive

    pos = _stock_yet_to_receive(db, shop)
    names = vendor_name_map(db, [p.get("vendor_id") for p in pos])
    rows = [
        {
            "po_id": p.get("po_id"),
            "po_number": p.get("po_number"),
            "vendor_id": p.get("vendor_id"),
            "vendor_name": names.get(str(p.get("vendor_id"))) or p.get("vendor_id"),
            "pieces_due": p.get("total_pending_qty") or 0,
            "expected_date": p.get("expected_date"),
        }
        for p in pos
    ]
    # Soonest first; an order with no date goes last.
    rows.sort(key=lambda r: (not r["expected_date"], str(r["expected_date"] or "")))
    return rows


def _running_low(db, shop: Optional[str]) -> Dict:
    products, units = db.get_collection("products"), db.get_collection("stock_units")
    rows = reorder_policy.low_stock_rows(products, units, store_id=shop)
    if not rows:
        return {"rows": [], "total": 0}
    pids = sorted({r["product_id"] for r in rows})
    everywhere = reorder_policy.on_hand(units, store_id=None, product_ids=pids)
    held = defaultdict(int)
    for (pid, _s), n in everywhere.items():
        held[pid] += n
    sold: Dict[tuple, int] = {}
    by_shop = defaultdict(list)
    for r in rows:
        by_shop[r["store_id"]].append(r["product_id"])
    for s, ids in by_shop.items():
        per = collection_insights.movement_summary(db, {"product_ids": ids}, store_id=s, days=30)["per_product"]
        for pid in ids:
            sold[(s, pid)] = int((per.get(pid) or {}).get("units_d30") or 0)
    shops = store_name_map(db, list(by_shop))
    out = [
        {
            "product_id": r["product_id"],
            "sku": r.get("sku"),
            "name": r.get("name"),
            "store_id": r["store_id"],
            "store_name": shops.get(r["store_id"]) or r["store_id"],
            "here": r["quantity"],
            "other_shops": max(0, held[r["product_id"]] - r["quantity"]),
            "sold_30d": sold.get((r["store_id"], r["product_id"]), 0),
            "reorder_level": r.get("reorder_point"),
        }
        for r in rows
    ]
    out.sort(key=lambda r: (-r["sold_30d"], r["here"], str(r["name"] or "")))
    return {"rows": out[:RUNNING_LOW_SHOWN], "total": len(out)}


@router.get("/today")
async def stock_today(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*PURCHASE_ROLES)),
):
    """The Stock today page (S7): one shop's stock jobs for today."""
    shop = store_in_view(store_id, current_user, one_shop=True)
    show_cost = can_see_cost(current_user, stock_value.COST_CONTEXT)
    body: Dict = {
        "store_id": shop,
        "store_name": None,
        "incoming": [],
        "requests": [],
        "count": {"in_progress": [], "counted_today": 0},
        "quarantine": {"pieces": 0, "unlabeled": 0},
        "deliveries": [],
        "stock_value": None,
        "running_low": [],
        "running_low_total": 0,
    }
    db = _get_db()
    if db is None:
        return body
    if shop:
        body["store_name"] = store_name_map(db, [shop]).get(shop)
    # Each card stands alone: one section failing to read must not blank the
    # page, and it never reads as "nothing to do" -- its key says so.
    failed: List[str] = []

    def part(name, fn):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[STOCK-TODAY] %s failed: %s", name, exc)
            failed.append(name)
            return None

    t = part("transfers", lambda: _transfers(db, shop))
    if t:
        body.update(t)
    body["count"] = part("count", lambda: _count(db, shop)) or body["count"]
    body["quarantine"] = part("quarantine", lambda: _quarantine(db, shop)) or body["quarantine"]
    body["deliveries"] = part("deliveries", lambda: _deliveries(db, shop)) or []
    if show_cost:
        def _value():
            units = stock_value.shelf_units(get_stock_repository(), get_product_repository(), shop)
            return {"total": stock_value.total(units), "uncosted_pieces": stock_value.uncosted(units)}
        body["stock_value"] = part("stock_value", _value)
    low = part("running_low", lambda: _running_low(db, shop))
    if low:
        body["running_low"], body["running_low_total"] = low["rows"], low["total"]
    body["unavailable"] = failed
    return body

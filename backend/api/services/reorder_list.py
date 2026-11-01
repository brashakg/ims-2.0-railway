"""
IMS 2.0 - THE Reorder list (stock redesign S5, owner 2026-10-22/23)
====================================================================
One list replaces Buy Desk, Replenishment and Low stock: per product AND shop,
what to buy, by THE buy rule (reorder_policy.buy_qty: the shop's level if set,
else its 30-day sales). The screen (GET /inventory/reorder-list), Rebalance
(GET /inventory/transfer-recommendations) and ORACLE read their rows from
build(); the stock alerts, Jarvis, the purchase report and the analytics
forecast ask buy_qty with the same inputs -- never a cover formula of their own.

Every (product, physical shop) with a target (reorder_policy.buy_target) is
one of:
  * a ROW: `buy` is the rule's number (0 when stock or sent orders cover it --
    a healthy, tracked line the 'All' view shows, as the board does).
    `transfer_from` is the other shops' spare pieces SHARED OUT over this
    product's short rows (reorder_policy.share_spares, worst row first, one
    donor's spares never offered twice); `transfer_instead` = they cover the
    whole buy. `vendor_id` is the product's preferred vendor, else the vendor
    of its last purchase order or receipt (vendor_source says which), else
    None: the screen asks for one before drafting;
  * or SKIPPED (short, but kept off) with the reason: discontinued,
    auto-reorder off, catalogue incomplete (can't purchase).

A read that fails RAISES (the route answers 503): a stock or open-order read
must never read as 'none', or the list would suggest buying everything.
Value is at the product's cost (stock_value.master_fallback_cost), never MRP;
the caller masks it for roles that do not see cost. Reads only. No emojis.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import reorder_policy as rp


def _name(p: Dict[str, Any]) -> str:
    attrs = p.get("attributes") or {}
    return p.get("name") or attrs.get("name") or p.get("sku") or str(p.get("product_id") or "")


def _skip_reason(p: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Why this product is never suggested, or None. Order matters: the first
    rule that applies is the one the screen names. A missing vendor is NOT a
    reason: the row asks for one (the retired screens picked a vendor at
    drafting time)."""
    from . import product_master as pm

    if rp.discontinued(p):
        return {"reason": "discontinued"}
    if rp.auto_reorder_disabled(p):
        return {"reason": "auto-reorder off"}
    try:
        ready = pm.catalog_readiness(p)
    except Exception:  # noqa: BLE001
        ready = {"complete": False, "missing": [], "blockers": []}
    # complete, not purchasable: purchasable also needs is_active, and an
    # inactive product is discontinued() (above) or a PO-door buy nobody has
    # switched on yet -- new, and still reordered.
    if not ready.get("complete"):
        return {
            "reason": "catalogue incomplete",
            "missing": [*(ready.get("blockers") or []), *(ready.get("missing") or [])],
        }
    return None


def _preferred_vendor(p: Dict[str, Any]) -> Optional[str]:
    return p.get("preferred_vendor_id") or p.get("default_vendor_id") or p.get("vendor_id") or None


def _last_vendors(db) -> Dict[str, str]:
    """{product_id: vendor_id} from the newest purchase order (not cancelled)
    or goods receipt that names a vendor for it."""
    # ponytail: reads every PO and receipt; read per product with an index if
    # either collection reaches hundreds of thousands of documents.
    seen: Dict[str, tuple] = {}
    for name, flt in (("purchase_orders", {"status": {"$ne": "CANCELLED"}}), ("grns", {})):
        for d in db.get_collection(name).find(flt, {"vendor_id": 1, "created_at": 1, "items": 1}):
            vid = d.get("vendor_id")
            if not vid:
                continue
            # created_at is an ISO string or a datetime: str() orders both alike.
            when = str(d.get("created_at") or "")
            for it in d.get("items") or []:
                pid = str((it or {}).get("product_id") or "")
                if pid and (pid not in seen or when > seen[pid][0]):
                    seen[pid] = (when, str(vid))
    return {pid: v for pid, (_w, v) in seen.items()}


def build(db, *, store_id: Optional[str] = None, now=None) -> Dict[str, Any]:
    """{rows, skipped, auto_reorder_off} for one shop (store_id) or every
    physical shop (None). `db` is a pymongo-style database; `now` dates the
    30-day sales (default: the clock). Empty when db is None. Raises when a
    read fails."""
    from .stock_value import master_fallback_cost
    from .stores_util import is_online_store

    if db is None:
        return {"rows": [], "skipped": [], "auto_reorder_off": 0}
    # ponytail: reads the whole catalogue and every open order in one pass;
    # filter in the query if the catalogue reaches tens of thousands.
    products = [p for p in db.get_collection("products").find({}) if p.get("product_id")]
    hand = rp.on_hand(db.get_collection("stock_units"), store_id=None, strict=True)
    sold = rp.sold_by_shop(db.get_collection("orders"), now=now, strict=True)
    sent, draft = rp.open_orders(db.get_collection("purchase_orders"), strict=True)
    last_vendor = _last_vendors(db)
    shop_names = {
        str(s.get("store_id")): s.get("store_name") or s.get("name") or str(s.get("store_id"))
        for s in db.get_collection("stores").find({}, {"store_id": 1, "store_name": 1, "name": 1})
        if s.get("store_id")
    }
    online: Dict[str, bool] = {}

    def physical(shop: str) -> bool:
        if shop not in online:
            online[shop] = bool(shop) and shop != "UNKNOWN" and not is_online_store(db, shop)
        return online[shop]

    vendors = {
        str(v.get("vendor_id")): v.get("trade_name") or v.get("legal_name") or str(v.get("vendor_id"))
        for v in db.get_collection("vendors").find({}, {"vendor_id": 1, "trade_name": 1, "legal_name": 1})
        if v.get("vendor_id")
    }
    sold_shops: Dict[str, set] = {}
    for (pid, shop), n in sold.items():
        if n > 0:
            sold_shops.setdefault(pid, set()).add(shop)
    held_shops: Dict[str, set] = {}
    for (pid, shop), n in hand.items():
        if n > 0:
            held_shops.setdefault(pid, set()).add(shop)

    rows: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    auto_off = 0
    for p in products:
        pid = str(p["product_id"])
        if rp.auto_reorder_disabled(p) and not rp.discontinued(p):
            auto_off += 1
        levels = p.get(rp.LEVELS_FIELD)
        shops = {s for s in (levels if isinstance(levels, dict) else {})
                 if rp.reorder_level(p, store_id=s) is not None}
        shops |= sold_shops.get(pid, set())
        why = None
        mine: List[Dict[str, Any]] = []
        for shop in sorted(s for s in shops if physical(s)):
            on_hand = hand.get((pid, shop), 0)
            sold_30d = sold.get((pid, shop), 0)
            target, basis = rp.buy_target(p, store_id=shop, sold_30d=sold_30d)
            if target is None:
                continue
            if why is None:
                why = _skip_reason(p) or {}
            base = {"product_id": pid, "sku": p.get("sku") or "", "name": _name(p),
                    "store_id": shop, "store_name": shop_names.get(shop, shop),
                    "level": rp.reorder_level(p, store_id=shop),
                    # The product-wide settings the reorder-settings form edits.
                    "reorder_quantity": rp.whole_number(p.get("reorder_quantity")),
                    "max_stock": rp.whole_number(p.get("max_stock")),
                    "lead_time_days": rp.whole_number(p.get("lead_time_days"))}
            if why:
                if on_hand < target:  # a healthy line of a skipped product says nothing
                    skipped.append({**base, **why})
                continue
            on_order = sent.get((pid, shop), 0)
            buy, _ = rp.buy_qty(p, store_id=shop, on_hand=on_hand, on_order=on_order, sold_30d=sold_30d)
            cost = master_fallback_cost(p) or None
            vendor_id = _preferred_vendor(p)
            source = "preferred" if vendor_id else None
            if not vendor_id and last_vendor.get(pid):
                vendor_id, source = last_vendor[pid], "last order"
            mine.append({
                **base,
                "brand": p.get("brand") or (p.get("attributes") or {}).get("brand_name") or "",
                "vendor_id": vendor_id,
                "vendor_name": vendors.get(str(vendor_id), "") if vendor_id else "",
                "vendor_source": source,
                "basis": basis,
                "sold_30d": sold_30d,
                "on_hand": on_hand,
                "on_order": on_order,
                "in_draft": draft.get((pid, shop), 0),
                # Days of stock at the 30-day pace; None = no sales to go by.
                "runs_out_days": (on_hand * rp.SALES_DAYS // sold_30d) if sold_30d > 0 else None,
                "band": rp.band(on_hand, target, basis),
                "buy": buy,
                "transfer_from": [],
                "transfer_qty": 0,
                "transfer_instead": False,
                "unit_cost": cost,
                "est_value": round(cost * buy, 2) if cost else None,
            })
        # Other shops' spare pieces, shared out over this product's short
        # rows, worst first (fewest in stock, then the biggest buy).
        short = sorted((r for r in mine if r["buy"] > 0),
                       key=lambda r: (r["on_hand"], -r["buy"], r["store_id"]))
        if short:
            spares = {s: rp.spare(p, store_id=s, on_hand=hand.get((pid, s), 0),
                                  sold_30d=sold.get((pid, s), 0))
                      for s in held_shops.get(pid, set()) if physical(s)}
            shared = rp.share_spares([((pid, r["store_id"]), r["buy"]) for r in short], spares)
            for r in short:
                got = shared.get((pid, r["store_id"]), [])
                r["transfer_from"] = [{"store_id": s, "store_name": shop_names.get(s, s), "qty": n}
                                      for s, n in got]
                r["transfer_qty"] = sum(n for _s, n in got)
                r["transfer_instead"] = r["transfer_qty"] >= r["buy"]
        rows.extend(mine)
    if store_id:
        rows = [r for r in rows if r["store_id"] == store_id]
        skipped = [s for s in skipped if s["store_id"] == store_id]
    rows.sort(key=lambda r: (r["buy"] <= 0, r["on_hand"] > 0, -r["buy"], r["store_id"], r["name"]))
    skipped.sort(key=lambda r: (r["reason"], r["name"]))
    return {"rows": rows, "skipped": skipped, "auto_reorder_off": auto_off}

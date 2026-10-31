"""
IMS 2.0 - THE Reorder list (stock redesign S5, owner 2026-10-22/23)
====================================================================
One list replaces Buy Desk, Replenishment and Low stock: per product AND shop,
what to buy, by THE buy rule (reorder_policy.buy_qty: the shop's level if set,
else its 30-day sales). The screen (GET /inventory/reorder-list), the purchase
recommendations report, Jarvis and ORACLE all read their numbers from here or
from buy_qty -- never a cover formula of their own.

A (product, shop) is on the list when the shop holds fewer pieces than its
target (reorder_policy.buy_target). Then either:
  * a ROW: `buy` is the rule's number (0 when sent orders already cover it);
    `spares` names other shops holding pieces beyond their own target, and
    `transfer_instead` says one shop's spares cover the whole buy;
  * or SKIPPED with the reason that keeps it off: discontinued, auto-reorder
    off, catalogue incomplete (can't purchase), no vendor set.

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
    rule that applies is the one the screen names."""
    from . import product_master as pm

    if rp.discontinued(p):
        return {"reason": "discontinued"}
    if rp.auto_reorder_disabled(p):
        return {"reason": "auto-reorder off"}
    try:
        ready = pm.catalog_readiness(p)
    except Exception:  # noqa: BLE001
        ready = {"purchasable": False, "missing": [], "blockers": []}
    if not ready.get("purchasable"):
        return {
            "reason": "catalogue incomplete",
            "missing": [*(ready.get("blockers") or []), *(ready.get("missing") or [])],
        }
    if not p.get("preferred_vendor_id"):
        return {"reason": "no vendor set"}
    return None


def build(db, *, store_id: Optional[str] = None) -> Dict[str, Any]:
    """{rows, skipped} for one shop (store_id) or every physical shop (None).
    `db` is a pymongo-style database. Empty lists when it is None."""
    from .stock_value import master_fallback_cost
    from .stores_util import is_online_store

    if db is None:
        return {"rows": [], "skipped": []}
    # ponytail: reads the whole catalogue and every open order in one pass;
    # filter in the query if the catalogue reaches tens of thousands.
    products = [p for p in db.get_collection("products").find({}) if p.get("product_id")]
    hand = rp.on_hand(db.get_collection("stock_units"), store_id=None)
    sold = rp.sold_by_shop(db.get_collection("orders"))
    sent, draft = rp.open_orders(db.get_collection("purchase_orders"))
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
    for p in products:
        pid = str(p["product_id"])
        levels = p.get(rp.LEVELS_FIELD)
        shops = {s for s in (levels if isinstance(levels, dict) else {})
                 if rp.reorder_level(p, store_id=s) is not None}
        shops |= sold_shops.get(pid, set())
        if store_id:
            shops &= {store_id}
        for shop in sorted(s for s in shops if physical(s)):
            on_hand = hand.get((pid, shop), 0)
            sold_30d = sold.get((pid, shop), 0)
            target, basis = rp.buy_target(p, store_id=shop, sold_30d=sold_30d)
            if target is None or on_hand >= target:
                continue
            base = {"product_id": pid, "sku": p.get("sku") or "", "name": _name(p),
                    "store_id": shop, "store_name": shop_names.get(shop, shop),
                    "level": rp.reorder_level(p, store_id=shop),
                    # The product-wide settings the reorder-settings form edits.
                    "reorder_quantity": rp.whole_number(p.get("reorder_quantity")),
                    "max_stock": rp.whole_number(p.get("max_stock")),
                    "lead_time_days": rp.whole_number(p.get("lead_time_days"))}
            why = _skip_reason(p)
            if why:
                skipped.append({**base, **why})
                continue
            on_order = sent.get((pid, shop), 0)
            buy, _ = rp.buy_qty(p, store_id=shop, on_hand=on_hand, on_order=on_order, sold_30d=sold_30d)
            spares = sorted(
                (
                    {"store_id": s, "store_name": shop_names.get(s, s), "spare": n}
                    for s in held_shops.get(pid, set())
                    if s != shop and physical(s)
                    for n in [rp.spare(p, store_id=s, on_hand=hand.get((pid, s), 0),
                                       sold_30d=sold.get((pid, s), 0))]
                    if n > 0
                ),
                key=lambda x: (-x["spare"], x["store_id"]),
            )
            cost = master_fallback_cost(p) or None
            rows.append({
                **base,
                "brand": p.get("brand") or (p.get("attributes") or {}).get("brand_name") or "",
                "vendor_id": p.get("preferred_vendor_id"),
                "vendor_name": vendors.get(str(p.get("preferred_vendor_id")), ""),
                "basis": basis,
                "sold_30d": sold_30d,
                "on_hand": on_hand,
                "on_order": on_order,
                "in_draft": draft.get((pid, shop), 0),
                # Days of stock at the 30-day pace; None = no sales to go by.
                "runs_out_days": (on_hand * rp.SALES_DAYS // sold_30d) if sold_30d > 0 else None,
                "band": rp.stock_status(target - 1, on_hand),
                "buy": buy,
                "spares": spares,
                "transfer_instead": bool(buy and spares and spares[0]["spare"] >= buy),
                "unit_cost": cost,
                "est_value": round(cost * buy, 2) if cost else None,
            })
    rows.sort(key=lambda r: (r["on_hand"] > 0, -r["buy"], r["store_id"], r["name"]))
    skipped.sort(key=lambda r: (r["reason"], r["name"]))
    return {"rows": rows, "skipped": skipped}

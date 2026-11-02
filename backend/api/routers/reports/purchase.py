"""TechCherry R2: purchase recommendations."""

from fastapi import Depends, Query
from typing import Optional
from datetime import datetime, timedelta
from ...utils.ist import (
    now_ist_naive,
)
from ..auth import get_current_user
from ...dependencies import store_in_view
from ...dependencies import (
    get_db,
    validate_store_access,
)
from ...services.cost_mask import mask_cost, mask_cost_list
from ...services.margin_basis import price_ex_gst, product_gst_rate
from ...services.reorder_policy import (
    buy_qty,
    is_low_stock,
    on_hand,
    open_orders,
    reorder_level,
    sold_by_shop,
    stock_status,
)
from ._shared import router

# ----------------------------------------------------------------------------
# R2 — Purchase Recommendations (TechCherry port)
#
# Spec: docs/TECHCHERRY_PORT_SCOPE.md §6.
# Algo:  the SKUs that sold in the lookback window, each with THE buy rule's
# number (reorder_policy.buy_qty: the shop's level if set, else its 30-day
# sales; S5) + ranked revenue/margin impact.
# ----------------------------------------------------------------------------

def _confidence_for(velocity_90d: int) -> str:
    """High/Medium/Low confidence band based on demand signal strength.
    Below 5 units in 90 days = noise; 5-29 = soft signal; 30+ = robust."""
    if velocity_90d >= 30:
        return "HIGH"
    if velocity_90d >= 5:
        return "MEDIUM"
    return "LOW"


@router.get("/purchase/recommendations")
async def purchase_recommendations(
    store_id: Optional[str] = Query(
        None, description="Defaults to current user's active store"
    ),
    lookback_days: int = Query(90, ge=14, le=365, description="Sales velocity window"),
    min_velocity: int = Query(
        2,
        ge=0,
        le=100,
        description="Skip SKUs that sold fewer than this many units in the window (noise filter)",
    ),
    limit: int = Query(100, ge=1, le=500),
    current_user: dict = Depends(get_current_user),
):
    """Ranked list of products the buyer should reorder.

    The SKUs that sold at least `min_velocity` in the last `lookback_days`,
    each with THE buy rule's number (reorder_policy.buy_qty, owner S5: the
    shop's level if set, else its last 30 days' sales, minus stock and sent
    orders) -- the same number the Reorder list shows.

    Ranking key is `suggested_order_qty × avg_selling_price` so the highest
    revenue-at-risk SKUs surface first — that's where the buyer's time
    matters most.

    Spec: docs/TECHCHERRY_PORT_SCOPE.md §6.
    """
    active_store = store_in_view(store_id, current_user, one_shop=True) or "store-001"
    params = {"store_id": active_store, "lookback_days": lookback_days, "min_velocity": min_velocity}
    db = get_db()
    if db is None:
        return {
            "recommendations": [],
            "summary": mask_cost(
                {
                    "total_recommendations": 0,
                    "total_suggested_units": 0,
                    "estimated_revenue_at_risk": 0.0,
                    "estimated_purchase_cost": 0.0,
                },
                current_user,
                "product",
            ),
            "params": params,
            "as_of": datetime.now().isoformat(),
        }

    now = now_ist_naive()
    cutoff = now - timedelta(days=lookback_days)

    # 1. Sales velocity per product over the lookback window.
    sku_stats: dict = {}
    try:
        orders_coll = db.get_collection("orders")
        pipeline = [
            {
                "$match": {
                    "store_id": active_store,
                    "status": {"$nin": ["CANCELLED", "DRAFT", "HISTORICAL"]},
                    "created_at": {"$gte": cutoff},
                }
            },
            {"$unwind": "$items"},
            {
                "$group": {
                    "_id": "$items.product_id",
                    "units_sold": {"$sum": {"$ifNull": ["$items.quantity", 1]}},
                    "revenue": {
                        "$sum": {"$ifNull": ["$items.item_total", "$items.total"]}
                    },
                    "avg_price": {"$avg": "$items.unit_price"},
                    "sample_name": {"$first": "$items.product_name"},
                    "sample_brand": {"$first": "$items.brand"},
                    "sample_category": {"$first": "$items.category"},
                }
            },
            {"$match": {"units_sold": {"$gte": min_velocity}}},
        ]
        for doc in orders_coll.aggregate(pipeline):
            pid = doc.get("_id")
            if not pid:
                continue
            sku_stats[str(pid)] = {
                "units_sold": int(doc.get("units_sold") or 0),
                "revenue": float(doc.get("revenue") or 0),
                "avg_price": float(doc.get("avg_price") or 0),
                "sample_name": doc.get("sample_name") or "",
                "sample_brand": doc.get("sample_brand") or "",
                "sample_category": doc.get("sample_category") or "",
            }
    except Exception as e:
        # Aggregation can fail on empty/unindexed collections — degrade
        # to empty rec list, never raise.
        return {
            "recommendations": [],
            "summary": mask_cost(
                {
                    "total_recommendations": 0,
                    "total_suggested_units": 0,
                    "estimated_revenue_at_risk": 0.0,
                    "estimated_purchase_cost": 0.0,
                },
                current_user,
                "product",
            ),
            "params": params,
            "error": f"aggregation failed: {type(e).__name__}",
            "as_of": now.isoformat(),
        }

    if not sku_stats:
        return {
            "recommendations": [],
            "summary": mask_cost(
                {
                    "total_recommendations": 0,
                    "total_suggested_units": 0,
                    "estimated_revenue_at_risk": 0.0,
                    "estimated_purchase_cost": 0.0,
                },
                current_user,
                "product",
            ),
            "params": params,
            "as_of": now.isoformat(),
        }

    # 2. Pull product master rows for the SKUs that have movement so we
    # know current stock + reorder point + cost + selling price.
    from bson import ObjectId  # local import; only needed here

    product_ids = list(sku_stats.keys())
    object_ids: list = []
    string_ids: list = []
    for pid in product_ids:
        try:
            object_ids.append(ObjectId(pid))
        except Exception:
            string_ids.append(pid)

    products: dict = {}
    try:
        prod_coll = db.get_collection("products")
        flt = {"$or": []}
        if object_ids:
            flt["$or"].append({"_id": {"$in": object_ids}})
        if string_ids:
            flt["$or"].append({"_id": {"$in": string_ids}})
            flt["$or"].append({"product_id": {"$in": string_ids}})
            flt["$or"].append({"sku": {"$in": string_ids}})
        if flt["$or"]:
            for p in prod_coll.find(flt):
                pid = str(p.get("_id"))
                products[pid] = p
                # Allow lookup by alternate id fields too.
                if p.get("product_id"):
                    products[str(p["product_id"])] = p
                if p.get("sku"):
                    products[str(p["sku"])] = p
    except Exception:
        products = {}

    # Units on hand at THIS shop (the TechCherry-era products.stock_quantity is
    # never maintained), compared with the shop's own level (D12).
    stock_here = {
        pid: qty
        for (pid, _shop), qty in on_hand(
            db.get_collection("stock_units"),
            store_id=active_store,
            product_ids=product_ids,
        ).items()
    }
    # THE buy rule's other inputs: 30-day sales and sent orders at this shop.
    sold_30d = sold_by_shop(db.get_collection("orders"), store_id=active_store)
    sent, _drafts = open_orders(db.get_collection("purchase_orders"), product_ids)

    # 3. Build per-SKU recommendation rows.
    recs: list = []
    for pid, stats in sku_stats.items():
        prod = products.get(pid, {})
        velocity_90d = stats["units_sold"]
        daily_v = velocity_90d / float(lookback_days) if lookback_days else 0.0
        current_stock = stock_here.get(pid, 0)
        # None = no level at this shop: its 30-day sales decide.
        reorder_point = reorder_level(prod, store_id=active_store)
        # THE buy rule (0 when auto-reorder is off, discontinued or covered).
        suggested_qty, basis = buy_qty(
            prod, store_id=active_store, on_hand=current_stock,
            on_order=sent.get((pid, active_store), 0),
            sold_30d=sold_30d.get((pid, active_store), 0),
        )
        if suggested_qty <= 0:
            continue

        avg_price = float(
            prod.get("offer_price") or prod.get("mrp") or stats["avg_price"] or 0.0
        )
        cost_price = float(prod.get("cost_price") or prod.get("landed_cost") or 0.0)
        # avg_price is GST-inclusive, cost_price ex-GST: the margin is on the
        # price net of GST (services/margin_basis).
        unit_margin = max(
            0.0, price_ex_gst(avg_price, product_gst_rate(prod)) - cost_price
        )
        est_revenue_impact = round(suggested_qty * avg_price, 2)
        est_purchase_cost = round(suggested_qty * cost_price, 2)
        est_margin = round(suggested_qty * unit_margin, 2)

        category = prod.get("category") or stats["sample_category"] or "OTHER"
        brand = prod.get("brand") or stats["sample_brand"] or ""
        name = (
            prod.get("name") or prod.get("product_name") or stats["sample_name"] or ""
        )

        recs.append(
            {
                "product_id": pid,
                "name": name,
                "brand": brand,
                "category": category,
                "velocity_90d": velocity_90d,
                "daily_velocity": round(daily_v, 2),
                "current_stock": current_stock,
                "reorder_point": reorder_point,
                # The server's verdict for this shop; the screen only renders it.
                "low_stock": is_low_stock(prod, current_stock, store_id=active_store),
                "stock_status": stock_status(reorder_point, current_stock),
                "basis": basis,
                "suggested_order_qty": suggested_qty,
                "avg_selling_price": round(avg_price, 2),
                "cost_price": round(cost_price, 2),
                "unit_margin": round(unit_margin, 2),
                "estimated_revenue_impact": est_revenue_impact,
                "estimated_purchase_cost": est_purchase_cost,
                "estimated_margin": est_margin,
                "confidence": _confidence_for(velocity_90d),
                "reason": (
                    f"Sold {velocity_90d} in {lookback_days}d "
                    f"(~{round(daily_v, 1)}/day). Stock {current_stock}, "
                    + (f"reorder at {reorder_point}. " if reorder_point is not None
                       else "reorder level not set. ")
                    + f"Buy {suggested_qty}: "
                    + ("back above the shop's level." if basis == "level"
                       else "the last 30 days' sales.")
                ),
            }
        )

    # 4. Rank by revenue-at-risk × confidence weight.
    confidence_weight = {"HIGH": 1.0, "MEDIUM": 0.7, "LOW": 0.4}
    recs.sort(
        key=lambda r: r["estimated_revenue_impact"]
        * confidence_weight[r["confidence"]],
        reverse=True,
    )
    recs = recs[:limit]

    summary = {
        "total_recommendations": len(recs),
        "total_suggested_units": sum(r["suggested_order_qty"] for r in recs),
        "estimated_revenue_at_risk": round(
            sum(r["estimated_revenue_impact"] for r in recs), 2
        ),
        "estimated_purchase_cost": round(
            sum(r["estimated_purchase_cost"] for r in recs), 2
        ),
        "estimated_margin": round(sum(r["estimated_margin"] for r in recs), 2),
    }

    # By-category roll-up — useful for the UI's category badges.
    by_category: dict = {}
    for r in recs:
        slot = by_category.setdefault(
            r["category"],
            {
                "category": r["category"],
                "count": 0,
                "suggested_units": 0,
                "estimated_revenue_impact": 0.0,
            },
        )
        slot["count"] += 1
        slot["suggested_units"] += r["suggested_order_qty"]
        slot["estimated_revenue_impact"] += r["estimated_revenue_impact"]
    by_category_list = sorted(
        (
            {**v, "estimated_revenue_impact": round(v["estimated_revenue_impact"], 2)}
            for v in by_category.values()
        ),
        key=lambda x: x["estimated_revenue_impact"],
        reverse=True,
    )

    # Per-unit cost, the buy's cost and the margin on it answer to the one
    # product-cost rule (services/cost_mask "product", owner rulings D7 /
    # 2026-09-28): the managers see them, counter roles get the velocity and
    # quantities only.
    return {
        "recommendations": mask_cost_list(recs, current_user, "product"),
        "by_category": by_category_list,
        "summary": mask_cost(summary, current_user, "product"),
        "params": params,
        "as_of": now.isoformat(),
    }



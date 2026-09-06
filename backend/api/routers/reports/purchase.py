"""TechCherry R2: purchase recommendations."""

from fastapi import Depends, Query
from typing import Optional
from datetime import datetime, timedelta
from ...utils.ist import (
    now_ist_naive,
)
from ..auth import get_current_user
from ...dependencies import (
    get_db,
    validate_store_access,
)
from ...services.reorder_policy import auto_reorder_disabled as _auto_reorder_disabled
from ._shared import router

# ----------------------------------------------------------------------------
# R2 — Purchase Recommendations (TechCherry port)
#
# Spec: docs/TECHCHERRY_PORT_SCOPE.md §6.
# Algo:  per-SKU velocity over the last 90 days × current stock × reorder
# point → suggested order qty + ranked revenue/margin impact.
# ----------------------------------------------------------------------------

# Optical retail buying cycle defaults — tunable via query params.
# Lead time + reorder cycle = 60 days of cover the buyer wants on shelf.
_DEFAULT_LEAD_TIME_DAYS = 30  # typical for frame imports / vendor cycles
_DEFAULT_REORDER_CYCLE_DAYS = 30  # monthly purchase cadence
_DEFAULT_SAFETY_BUFFER_DAYS = 7  # ~1 week extra to absorb demand spikes


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
    lead_time_days: int = Query(
        _DEFAULT_LEAD_TIME_DAYS,
        ge=0,
        le=180,
        description="Vendor lead time used in suggested-qty math",
    ),
    reorder_cycle_days: int = Query(
        _DEFAULT_REORDER_CYCLE_DAYS,
        ge=7,
        le=180,
        description="How often the buyer re-evaluates purchases",
    ),
    safety_buffer_days: int = Query(
        _DEFAULT_SAFETY_BUFFER_DAYS,
        ge=0,
        le=30,
        description="Extra cover above lead+cycle to absorb spikes",
    ),
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

    Combines per-SKU sales velocity (last `lookback_days`) with current
    stock and reorder point. For each SKU the algo computes the desired
    cover (lead time + cycle + safety buffer days × daily velocity) and
    suggests an order qty if current stock is below that.

    Ranking key is `gap_units × avg_selling_price` so the highest
    revenue-at-risk SKUs surface first — that's where the buyer's time
    matters most.

    Spec: docs/TECHCHERRY_PORT_SCOPE.md §6.
    """
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id") or "store-001"
    db = get_db()
    if db is None:
        return {
            "recommendations": [],
            "summary": {
                "total_recommendations": 0,
                "total_suggested_units": 0,
                "estimated_revenue_at_risk": 0.0,
                "estimated_purchase_cost": 0.0,
            },
            "params": {
                "store_id": active_store,
                "lookback_days": lookback_days,
                "lead_time_days": lead_time_days,
                "reorder_cycle_days": reorder_cycle_days,
                "safety_buffer_days": safety_buffer_days,
                "cover_days_total": lead_time_days
                + reorder_cycle_days
                + safety_buffer_days,
                "min_velocity": min_velocity,
            },
            "as_of": datetime.now().isoformat(),
        }

    now = now_ist_naive()
    cutoff = now - timedelta(days=lookback_days)
    cover_days = lead_time_days + reorder_cycle_days + safety_buffer_days

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
            "summary": {
                "total_recommendations": 0,
                "total_suggested_units": 0,
                "estimated_revenue_at_risk": 0.0,
                "estimated_purchase_cost": 0.0,
            },
            "params": {
                "store_id": active_store,
                "lookback_days": lookback_days,
                "lead_time_days": lead_time_days,
                "reorder_cycle_days": reorder_cycle_days,
                "safety_buffer_days": safety_buffer_days,
                "cover_days_total": cover_days,
                "min_velocity": min_velocity,
            },
            "error": f"aggregation failed: {type(e).__name__}",
            "as_of": now.isoformat(),
        }

    if not sku_stats:
        return {
            "recommendations": [],
            "summary": {
                "total_recommendations": 0,
                "total_suggested_units": 0,
                "estimated_revenue_at_risk": 0.0,
                "estimated_purchase_cost": 0.0,
            },
            "params": {
                "store_id": active_store,
                "lookback_days": lookback_days,
                "lead_time_days": lead_time_days,
                "reorder_cycle_days": reorder_cycle_days,
                "safety_buffer_days": safety_buffer_days,
                "cover_days_total": cover_days,
                "min_velocity": min_velocity,
            },
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

    # 3. Build per-SKU recommendation rows.
    recs: list = []
    for pid, stats in sku_stats.items():
        prod = products.get(pid, {})
        # Owner decision (2026-07-04): reorder_quantity <= 0 (the new -1
        # default) disables auto-reorder for the product -- it never appears
        # in the purchase recommendations (api/services/reorder_policy.py).
        if _auto_reorder_disabled(prod):
            continue
        velocity_90d = stats["units_sold"]
        daily_v = velocity_90d / float(lookback_days) if lookback_days else 0.0
        desired_cover = round(daily_v * cover_days)
        current_stock = int(
            prod.get("stock_quantity")
            or prod.get("quantity")
            or prod.get("current_stock")
            or 0
        )
        reorder_point = int(prod.get("reorder_point") or 0)
        gap_units = max(0, desired_cover - current_stock)
        if gap_units <= 0 and current_stock > reorder_point:
            # No buying needed — skip.
            continue
        # If reorder_point breached even when desired_cover would tolerate
        # current stock, still recommend a minimum top-up of (reorder_point - current_stock).
        suggested_qty = max(
            gap_units,
            reorder_point - current_stock if reorder_point > current_stock else 0,
        )
        if suggested_qty <= 0:
            continue

        avg_price = float(
            prod.get("offer_price") or prod.get("mrp") or stats["avg_price"] or 0.0
        )
        cost_price = float(prod.get("cost_price") or prod.get("landed_cost") or 0.0)
        unit_margin = max(0.0, avg_price - cost_price)
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
                "desired_cover": desired_cover,
                "gap_units": gap_units,
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
                    f"reorder at {reorder_point}. Buy {suggested_qty} to cover "
                    f"{cover_days} days."
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

    return {
        "recommendations": recs,
        "by_category": by_category_list,
        "summary": summary,
        "params": {
            "store_id": active_store,
            "lookback_days": lookback_days,
            "lead_time_days": lead_time_days,
            "reorder_cycle_days": reorder_cycle_days,
            "safety_buffer_days": safety_buffer_days,
            "cover_days_total": cover_days,
            "min_velocity": min_velocity,
        },
        "as_of": now.isoformat(),
    }



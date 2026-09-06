"""Expense-vs-revenue, customer acquisition, brand sell-through, targets."""

from fastapi import Depends, Query
from typing import Optional
from datetime import date, datetime, timedelta
from ...utils.ist import (
    now_ist,
    ist_day_start_utc,
)
from ..auth import get_current_user, require_roles
from ...dependencies import (
    get_order_repository,
    get_stock_repository,
    get_customer_repository,
    get_db,
    validate_store_access,
)
from ._shared import (
    _REPORT_FINANCE_ROLES,
    _item_revenue,
    _order_revenue,
    _orders_in_window,
    router,
)

# ============================================================================
# FINANCE & CUSTOMER REPORTS
# ============================================================================


@router.get("/finance/expense-vs-revenue")
async def expense_vs_revenue(
    store_id: Optional[str] = Query(None),
    from_date: date = Query(...),
    to_date: date = Query(...),
    current_user: dict = Depends(require_roles(*_REPORT_FINANCE_ROLES)),
):
    """Expense vs revenue comparison"""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    order_repo = get_order_repository()

    if order_repo is None:
        return {"revenue": 0, "cost": 0, "profit": 0, "margin_percent": 0}

    # BUG-104, BOUND rule: found in the round-3 closing sweep -- same
    # typed-IST-range defect as /sales/by-salesperson (see the comment there).
    from_dt = ist_day_start_utc(from_date)
    to_dt = ist_day_start_utc(to_date + timedelta(days=1)) - timedelta(
        microseconds=1
    )
    orders = _orders_in_window(
        order_repo,
        store_id=active_store,
        start_dt=from_dt,
        end_dt=to_dt,
    )

    revenue = sum(_order_revenue(o) for o in orders)
    cost = 0.0

    for order in orders:
        for item in order.get("items", []):
            try:
                unit_cost = float(item.get("cost_price") or 0)
                qty = float(item.get("quantity") or 1)
                cost += unit_cost * qty
            except (TypeError, ValueError):
                continue

    profit = revenue - cost
    margin_percent = (profit / revenue * 100) if revenue > 0 else 0

    return {
        "revenue": round(revenue, 2),
        "cost": round(cost, 2),
        "profit": round(profit, 2),
        "margin_percent": round(margin_percent, 2),
    }


@router.get("/customers/acquisition")
async def customer_acquisition(
    store_id: Optional[str] = Query(None),
    from_date: date = Query(...),
    to_date: date = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Customer acquisition/retention report"""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    customer_repo = get_customer_repository()
    order_repo = get_order_repository()

    if customer_repo is None:
        return {
            "new_customers": 0,
            "returning_customers": 0,
            "total_customers": 0,
            "retention_percent": 0,
        }

    # BUG-104, BOUND rule (the bound moves BACKWARD). from_date/to_date are
    # IST calendar days the caller typed; created_at is a stored naive-UTC
    # instant. Naive-midnight bounds dropped every customer/order created
    # 00:00-05:30 IST on the first requested day (they fell before the
    # window) and mis-claimed the same IST window of the day after to_date.
    from_dt = ist_day_start_utc(from_date)
    to_dt = ist_day_start_utc(to_date + timedelta(days=1)) - timedelta(
        microseconds=1
    )

    # Get all customers (small enough N to walk in-process)
    all_customers = customer_repo.find_many({"store_id": active_store}, limit=0) or []

    # New customers — created_at within window. Mongo stamps `created_at`
    # as a real datetime, but legacy seeds may have it as ISO string.
    def _in_window(ca) -> bool:
        if isinstance(ca, datetime):
            return from_dt <= ca <= to_dt
        if isinstance(ca, str) and len(ca) >= 10:
            # Legacy string rows carry no reliable frame: compare their raw
            # day against the REQUESTED calendar days, exactly as before
            # (deliberately NOT the shifted bounds above).
            return from_date.isoformat() <= ca[:10] <= to_date.isoformat()
        return False

    new_customers = len([c for c in all_customers if _in_window(c.get("created_at"))])

    # Returning customers: placed >1 order in the window.
    returning_customers = 0
    total_buyers = 0
    if order_repo:
        orders = _orders_in_window(
            order_repo,
            store_id=active_store,
            start_dt=from_dt,
            end_dt=to_dt,
        )
        repeat_customers: dict = {}
        for order in orders:
            cust_id = order.get("customer_id")
            if cust_id:
                repeat_customers[cust_id] = repeat_customers.get(cust_id, 0) + 1
        total_buyers = len(repeat_customers)
        returning_customers = sum(1 for n in repeat_customers.values() if n > 1)

    # Retention% = returning customers as a share of all unique buyers
    # in the window. The previous formula divided by `new_customers`,
    # which produced > 100% whenever a returning buyer wasn't also a
    # new signup.
    retention_percent = (
        round(returning_customers / total_buyers * 100, 2) if total_buyers > 0 else 0.0
    )

    return {
        "new_customers": new_customers,
        "returning_customers": returning_customers,
        "total_customers": len(all_customers),
        "retention_percent": retention_percent,
    }


@router.get("/inventory/brand-sellthrough")
async def brand_sellthrough(
    store_id: Optional[str] = Query(None),
    from_date: date = Query(...),
    to_date: date = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Brand-wise sell-through report"""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    order_repo = get_order_repository()
    stock_repo = get_stock_repository()

    if order_repo is None:
        return {"data": [], "summary": {}}

    # BUG-104, BOUND rule: found in the round-3 closing sweep -- same
    # typed-IST-range defect as /sales/by-salesperson (see the comment there).
    from_dt = ist_day_start_utc(from_date)
    to_dt = ist_day_start_utc(to_date + timedelta(days=1)) - timedelta(
        microseconds=1
    )
    orders = _orders_in_window(
        order_repo,
        store_id=active_store,
        start_dt=from_dt,
        end_dt=to_dt,
    )

    # Track brand sales (uses _item_revenue helper to handle the
    # current item_total/unit_price schema + legacy fall-throughs).
    by_brand = {}
    for order in orders:
        for item in order.get("items", []):
            brand = item.get("brand", "Unbranded")
            if brand not in by_brand:
                by_brand[brand] = {
                    "brand": brand,
                    "quantity_sold": 0,
                    "revenue": 0,
                    "avg_price": 0,
                    "sellthrough_percent": 0,
                }
            by_brand[brand]["quantity_sold"] += int(item.get("quantity") or 1)
            by_brand[brand]["revenue"] += _item_revenue(item)

    # Calculate average price
    for brand in by_brand.values():
        if brand["quantity_sold"] > 0:
            brand["avg_price"] = round(brand["revenue"] / brand["quantity_sold"], 2)

    # Get current stock by brand
    if stock_repo:
        current_stock = stock_repo.find_many({"store_id": active_store}, limit=0)
        by_brand_stock = {}
        for item in current_stock:
            brand = item.get("brand", "Unbranded")
            if brand not in by_brand_stock:
                by_brand_stock[brand] = 0
            by_brand_stock[brand] += item.get("quantity", 0)

        # Calculate sell-through percent
        for brand in by_brand.values():
            if brand["brand"] in by_brand_stock:
                total_stock = brand["quantity_sold"] + by_brand_stock[brand["brand"]]
                brand["sellthrough_percent"] = (
                    round((brand["quantity_sold"] / total_stock * 100), 2)
                    if total_stock > 0
                    else 0
                )

    return {
        "data": list(by_brand.values()),
        "summary": {
            "total_brands": len(by_brand),
            "total_quantity_sold": sum(b["quantity_sold"] for b in by_brand.values()),
            "total_revenue": round(sum(b["revenue"] for b in by_brand.values()), 2),
        },
    }


# ============================================================================
# SALES TARGETS ENDPOINT
# ============================================================================


@router.get("/targets")
async def get_targets(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """
    Get sales targets for a store (daily and monthly).
    Returns configurable defaults or targets from database.
    """
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id") or "store-001"

    # Default targets
    targets = {
        "store_id": active_store,
        "daily_target": 50000,
        "monthly_target": 1500000,
        "currency": "INR",
        "period": now_ist().strftime("%Y-%m"),
        "created_at": datetime.now().isoformat(),
    }

    # Fetch from targets collection in database if available
    try:
        db = get_db()
        if db:
            targets_coll = db.get_collection("targets")
            stored = targets_coll.find_one({"store_id": active_store})
            if stored:
                stored.pop("_id", None)
                targets.update(stored)
    except Exception:
        pass  # Fall back to defaults

    return targets



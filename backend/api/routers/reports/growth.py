"""Sales comparison, growth, profit and discount analysis reports."""

from fastapi import Depends, Query
from typing import Optional
from datetime import date, timedelta
from ...utils.ist import (
    ist_day_start_utc,
    ist_month_window_utc,
)
from ..auth import get_current_user, require_roles
from ...dependencies import (
    get_order_repository,
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
# SALES COMPARISON & GROWTH REPORTS
# ============================================================================


@router.get("/sales/comparison")
async def sales_comparison(
    store_id: Optional[str] = Query(None),
    from_date: date = Query(...),
    to_date: date = Query(...),
    period_type: str = Query("daily"),  # daily, monthly, yearly
    current_user: dict = Depends(get_current_user),
):
    """Daily/Monthly/Yearly sales comparison (current vs previous period)"""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    order_repo = get_order_repository()

    if order_repo is None:
        return {"current_period": {}, "previous_period": {}, "comparison": {}}

    # BUG-104, BOUND rule -- BOTH windows together. The typed range is IST
    # calendar days; created_at is a stored naive-UTC instant, so each bound
    # moves BACKWARD 5h30m. The derived previous window must move the SAME
    # way or the seam leaks: prev_to_date + 1 day == from_date, so
    # prev_to_dt == from_dt - 1 microsecond by construction -- no order can
    # fall between (or into both of) adjacent periods.
    from_dt = ist_day_start_utc(from_date)
    to_dt = ist_day_start_utc(to_date + timedelta(days=1)) - timedelta(
        microseconds=1
    )

    # Calculate period difference
    period_days = (to_date - from_date).days
    prev_from_date = from_date - timedelta(days=period_days + 1)
    prev_to_date = from_date - timedelta(days=1)

    prev_from_dt = ist_day_start_utc(prev_from_date)
    prev_to_dt = ist_day_start_utc(prev_to_date + timedelta(days=1)) - timedelta(
        microseconds=1
    )

    current_orders = _orders_in_window(
        order_repo,
        store_id=active_store,
        start_dt=from_dt,
        end_dt=to_dt,
    )
    prev_orders = _orders_in_window(
        order_repo,
        store_id=active_store,
        start_dt=prev_from_dt,
        end_dt=prev_to_dt,
    )

    current_sales = sum(_order_revenue(o) for o in current_orders)
    prev_sales = sum(_order_revenue(o) for o in prev_orders)

    change = ((current_sales - prev_sales) / prev_sales * 100) if prev_sales > 0 else 0

    return {
        "current_period": {
            "sales": round(current_sales, 2),
            "orders": len(current_orders),
            "avg_order_value": (
                round(current_sales / len(current_orders), 2) if current_orders else 0
            ),
        },
        "previous_period": {
            "sales": round(prev_sales, 2),
            "orders": len(prev_orders),
            "avg_order_value": (
                round(prev_sales / len(prev_orders), 2) if prev_orders else 0
            ),
        },
        "comparison": {
            "sales_change_percent": round(change, 2),
            "sales_change_amount": round(current_sales - prev_sales, 2),
            "order_change": len(current_orders) - len(prev_orders),
        },
    }


@router.get("/sales/growth")
async def sales_growth(
    store_id: Optional[str] = Query(None),
    year: int = Query(...),
    month: int = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """MoM (Month-over-Month) and YoY (Year-over-Year) growth percentages"""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    order_repo = get_order_repository()

    if order_repo is None:
        return {"current_month": {}, "mom_growth": {}, "yoy_growth": {}}

    # Current month
    # BUG-104: IST month BOUNDS against naive-UTC `created_at` -- the same
    # window payout and budgets use, from the one shared definition, so a
    # growth figure and a payout figure for the same month cannot disagree.
    current_start, current_end = ist_month_window_utc(year, month)
    # Previous month (MoM)
    mom_start, mom_end = ist_month_window_utc(
        year - 1 if month == 1 else year, 12 if month == 1 else month - 1
    )
    # Previous year (YoY)
    yoy_start, yoy_end = ist_month_window_utc(year - 1, month)

    current_orders = _orders_in_window(
        order_repo,
        store_id=active_store,
        start_dt=current_start,
        end_dt=current_end,
    )
    mom_orders = _orders_in_window(
        order_repo,
        store_id=active_store,
        start_dt=mom_start,
        end_dt=mom_end,
    )
    yoy_orders = _orders_in_window(
        order_repo,
        store_id=active_store,
        start_dt=yoy_start,
        end_dt=yoy_end,
    )

    current_sales = sum(_order_revenue(o) for o in current_orders)
    mom_sales = sum(_order_revenue(o) for o in mom_orders)
    yoy_sales = sum(_order_revenue(o) for o in yoy_orders)

    mom_growth = ((current_sales - mom_sales) / mom_sales * 100) if mom_sales > 0 else 0
    yoy_growth = ((current_sales - yoy_sales) / yoy_sales * 100) if yoy_sales > 0 else 0

    return {
        "current_month": {
            "sales": round(current_sales, 2),
            "orders": len(current_orders),
        },
        "mom_growth": {
            "percent": round(mom_growth, 2),
            "previous_month_sales": round(mom_sales, 2),
        },
        "yoy_growth": {
            "percent": round(yoy_growth, 2),
            "previous_year_sales": round(yoy_sales, 2),
        },
    }


# ============================================================================
# PROFIT & DISCOUNT REPORTS
# ============================================================================


@router.get("/profit/by-category")
async def profit_by_category(
    store_id: Optional[str] = Query(None),
    from_date: date = Query(...),
    to_date: date = Query(...),
    current_user: dict = Depends(require_roles(*_REPORT_FINANCE_ROLES)),
):
    """Profit by product category"""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    order_repo = get_order_repository()

    if order_repo is None:
        return {"data": [], "total_profit": 0}

    # BUG-104, BOUND rule: found in the round-3 closing sweep -- same
    # typed-IST-range defect as /sales/by-salesperson (see the comment there).
    from_dt = ist_day_start_utc(from_date)
    to_dt = ist_day_start_utc(to_date + timedelta(days=1)) - timedelta(
        microseconds=1
    )

    # Datetime objects, NOT .isoformat() strings -- created_at is a BSON Date
    # so a string filter never matched and this report came back empty.
    orders = order_repo.find_many(
        {
            "store_id": active_store,
            "created_at": {"$gte": from_dt, "$lte": to_dt},
            "status": {"$nin": ["CANCELLED", "DRAFT", "HISTORICAL"]},
        },
        limit=0,
    )

    profit_by_cat = {}
    for order in orders:
        for item in order.get("items", []):
            category = item.get("category", "Other")
            if category not in profit_by_cat:
                profit_by_cat[category] = {
                    "category": category,
                    "revenue": 0,
                    "cost": 0,
                    "profit": 0,
                    "margin_percent": 0,
                }
            # _item_revenue reads item_total first (the field orders.py
            # actually stamps), falling back to legacy total / price*qty.
            selling_price = _item_revenue(item)
            cost_price = item.get("cost_price", 0) * item.get("quantity", 1)
            profit = selling_price - cost_price

            profit_by_cat[category]["revenue"] += selling_price
            profit_by_cat[category]["cost"] += cost_price
            profit_by_cat[category]["profit"] += profit

    # Calculate margin percentages
    for cat in profit_by_cat.values():
        if cat["revenue"] > 0:
            cat["margin_percent"] = round((cat["profit"] / cat["revenue"] * 100), 2)

    total_profit = sum(c["profit"] for c in profit_by_cat.values())

    return {
        "data": list(profit_by_cat.values()),
        "total_profit": round(total_profit, 2),
    }


@router.get("/profit/by-store")
async def profit_by_store(
    from_date: date = Query(...),
    to_date: date = Query(...),
    current_user: dict = Depends(require_roles(*_REPORT_FINANCE_ROLES)),
):
    """Profit by store (if multi-store)"""
    order_repo = get_order_repository()

    if order_repo is None:
        return {"data": [], "total_profit": 0}

    # BUG-104, BOUND rule: found in the round-3 closing sweep -- same
    # typed-IST-range defect as /sales/by-salesperson (see the comment there).
    from_dt = ist_day_start_utc(from_date)
    to_dt = ist_day_start_utc(to_date + timedelta(days=1)) - timedelta(
        microseconds=1
    )

    # Datetime objects, NOT .isoformat() strings -- created_at is a BSON Date
    # so a string filter never matched and this report came back empty.
    orders = order_repo.find_many(
        {
            "created_at": {"$gte": from_dt, "$lte": to_dt},
            "status": {"$nin": ["CANCELLED", "DRAFT", "HISTORICAL"]},
        },
        limit=0,
    )

    # Store-scope: _REPORT_FINANCE_ROLES includes the single-store STORE_MANAGER,
    # so without this a store manager saw EVERY store's revenue/cost/profit.
    # Cross-store roles (ADMIN/AREA_MANAGER/SUPERADMIN) keep the all-store view.
    from ...dependencies import user_store_scope

    is_cross, allowed_stores = user_store_scope(current_user)

    profit_by_st = {}
    for order in orders:
        store = order.get("store_id", "Unknown")
        if not is_cross and store not in allowed_stores:
            continue
        if store not in profit_by_st:
            profit_by_st[store] = {
                "store_id": store,
                "revenue": 0,
                "cost": 0,
                "profit": 0,
                "orders": 0,
            }
        profit_by_st[store]["orders"] += 1
        # Canonical revenue reader (grand_total first).
        order_amount = _order_revenue(order)
        profit_by_st[store]["revenue"] += order_amount

        # Calculate cost from items
        cost = sum(
            item.get("cost_price", 0) * item.get("quantity", 1)
            for item in order.get("items", [])
        )
        profit_by_st[store]["cost"] += cost
        profit_by_st[store]["profit"] += order_amount - cost

    total_profit = sum(s["profit"] for s in profit_by_st.values())

    return {
        "data": list(profit_by_st.values()),
        "total_profit": round(total_profit, 2),
    }


@router.get("/discount/analysis")
async def discount_analysis(
    store_id: Optional[str] = Query(None),
    from_date: date = Query(...),
    to_date: date = Query(...),
    current_user: dict = Depends(require_roles(*_REPORT_FINANCE_ROLES)),
):
    """Discount average by category and store"""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    order_repo = get_order_repository()

    if order_repo is None:
        return {"by_category": [], "by_store": [], "summary": {}}

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

    # Aggregate per-category: sum item-level discount_amount + line
    # revenue. The avg_discount_percent is per-category (line-discount
    # / pre-discount line revenue), not the previous tortured formula
    # which divided by (total_discount + per-category-share-of-revenue).
    by_category: dict = {}
    total_discount = 0.0
    total_revenue = 0.0

    for order in orders:
        # Cart-level discount is split proportionally across items by
        # taxable value when the order was created. Reading per-item
        # `discount_amount` already reflects the item-level discount
        # only; the order's `total_discount` includes cart-level too.
        for item in order.get("items", []):
            category = item.get("category") or item.get("item_type") or "Other"
            if category not in by_category:
                by_category[category] = {
                    "category": category,
                    "total_discount": 0.0,
                    "total_revenue": 0.0,
                    "total_items": 0,
                    "avg_discount_percent": 0.0,
                }
            item_discount = float(
                item.get("discount_amount") or item.get("discount") or 0
            )
            line_revenue = _item_revenue(item)
            by_category[category]["total_discount"] += item_discount
            by_category[category]["total_revenue"] += line_revenue
            by_category[category]["total_items"] += int(item.get("quantity") or 1)
            total_discount += item_discount
            total_revenue += line_revenue

    for cat in by_category.values():
        # Pre-discount line revenue = post-discount + discount itself
        gross = cat["total_revenue"] + cat["total_discount"]
        cat["avg_discount_percent"] = (
            round(cat["total_discount"] / gross * 100, 2) if gross > 0 else 0.0
        )
        cat["total_discount"] = round(cat["total_discount"], 2)
        cat["total_revenue"] = round(cat["total_revenue"], 2)

    gross_total = total_revenue + total_discount
    return {
        "by_category": sorted(by_category.values(), key=lambda c: -c["total_discount"]),
        "summary": {
            "total_discount": round(total_discount, 2),
            "total_revenue": round(total_revenue, 2),
            "discount_percent": (
                round(total_discount / gross_total * 100, 2) if gross_total > 0 else 0.0
            ),
        },
    }



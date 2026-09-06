"""Reports root, the Hub dashboard and the headline sales reports."""

from fastapi import Depends, Query
from typing import Optional
from datetime import date, datetime, timedelta
from ...utils.ist import (
    now_ist,
    ist_date_str,
    ist_day_start_utc,
    ist_today,
)
from ..auth import get_current_user, require_roles
from ...dependencies import (
    get_order_repository,
    get_stock_repository,
    get_customer_repository,
    get_task_repository,
    get_db,
    validate_store_access,
)
from ...services.name_resolver import order_actor_id, order_actor_name_map
from ._shared import (
    _REPORT_FINANCE_ROLES,
    _category_breakdown,
    _daily_trend,
    _order_revenue,
    _orders_in_window,
    _summarise_orders,
    router,
)

@router.get("")
@router.get("/")
async def get_reports_root():
    """Root endpoint for available reports"""
    return {
        "module": "reports",
        "status": "active",
        "message": "reports overview endpoint ready",
    }


@router.get("/dashboard")
async def dashboard_stats(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get dashboard statistics for a store - fetched from database"""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id") or "store-001"

    order_repo = get_order_repository()
    stock_repo = get_stock_repository()
    customer_repo = get_customer_repository()
    task_repo = get_task_repository()

    # Get today's date range (IST business day, not UTC box clock)
    today = now_ist().replace(hour=0, minute=0, second=0, microsecond=0)
    today_str = today.strftime("%Y-%m-%d")
    yesterday_str = (today - timedelta(days=1)).strftime("%Y-%m-%d")

    # Initialize stats
    total_sales = 0
    pending_orders = 0
    ready_orders = 0
    low_stock_items = 0
    today_orders = 0
    today_deliveries = 0
    today_new_customers = 0
    payments_received = 0
    # Today-vs-yesterday sales for the real `change` delta (see below).
    today_sales = 0.0
    yesterday_sales = 0.0

    # Fetch orders data
    if order_repo is not None:
        # Get all orders for store
        all_orders = order_repo.find_by_store(active_store)

        # Calculate totals
        for order in all_orders:
            status = order.get("status", "")
            order_date = ist_date_str(order.get("created_at"))

            # Today's orders
            if order_date == today_str:
                today_orders += 1
                payments_received += order.get("amount_paid", 0)

            # Status-based counts
            if status == "CONFIRMED" or status == "PROCESSING":
                pending_orders += 1
            elif status == "READY":
                ready_orders += 1
            elif status == "DELIVERED" and order_date == today_str:
                today_deliveries += 1

            # Total sales (completed orders)
            if status not in ["CANCELLED", "DRAFT"]:
                rev = _order_revenue(order)
                total_sales += rev
                if order_date == today_str:
                    today_sales += rev
                elif order_date == yesterday_str:
                    yesterday_sales += rev

    # Fetch inventory data
    if stock_repo is not None:
        low_stock = stock_repo.find_low_stock(active_store, threshold=5)
        low_stock_items = len(low_stock) if low_stock else 0

    # Fetch customer data
    if customer_repo is not None:
        # Count customers created today
        all_customers = customer_repo.find_many({"store_id": active_store}, limit=0)
        for customer in all_customers:
            created_date = ist_date_str(customer.get("created_at"))
            if created_date == today_str:
                today_new_customers += 1

    # Fetch task/appointment data
    open_tasks = 0
    if task_repo is not None:
        task_summary = task_repo.get_task_summary(active_store)
        if task_summary:
            open_tasks = task_summary.get("OPEN", 0) + task_summary.get(
                "IN_PROGRESS", 0
            )

    # Real today-vs-yesterday sales change (same delta math as
    # /sales/comparison + /sales/growth). null when yesterday had no sales
    # to compare against -- the frontend renders "-" for null rather than a
    # fabricated 12.5%.
    if yesterday_sales > 0:
        change = round((today_sales - yesterday_sales) / yesterday_sales * 100, 2)
    else:
        change = None

    return {
        "totalSales": total_sales,
        "change": change,
        "pendingOrders": pending_orders,
        "urgentOrders": ready_orders,
        "appointmentsToday": open_tasks,
        "upcomingAppointments": 0,
        "lowStockItems": low_stock_items,
        "todaySummary": {
            "totalOrders": today_orders,
            "deliveries": today_deliveries,
            "eyeTests": 0,
            "newCustomers": today_new_customers,
            "paymentsReceived": payments_received,
        },
    }


@router.get("/inventory")
async def inventory_report(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get inventory report for a store - fetched from database"""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id") or "store-001"
    stock_repo = get_stock_repository()

    if stock_repo is not None:
        all_stock = stock_repo.find_many({"store_id": active_store}, limit=0)
        low_stock = stock_repo.find_low_stock(active_store, threshold=5)

        total_items = len(all_stock)
        total_value = sum(
            (s.get("quantity", 0) * s.get("cost_price", 0)) for s in all_stock
        )
        low_stock_count = len(low_stock) if low_stock else 0
        out_of_stock = len([s for s in all_stock if s.get("quantity", 0) <= 0])

        # Group by category
        categories = {}
        for item in all_stock:
            cat = item.get("category", "Other")
            if cat not in categories:
                categories[cat] = {"name": cat, "count": 0, "value": 0}
            categories[cat]["count"] += 1
            categories[cat]["value"] += item.get("quantity", 0) * item.get(
                "cost_price", 0
            )

        return {
            "totalItems": total_items,
            "totalValue": round(total_value, 2),
            "lowStock": low_stock_count,
            "outOfStock": out_of_stock,
            "categories": list(categories.values()),
        }

    return {
        "totalItems": 0,
        "totalValue": 0,
        "lowStock": 0,
        "outOfStock": 0,
        "categories": [],
    }


@router.get("/sales/summary")
async def sales_summary(
    store_id: Optional[str] = Query(None),
    from_date: date = Query(...),
    to_date: date = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Sales summary + daily trend + category breakdown for the
    requested window. Single endpoint that the frontend Reports page
    reads off the same response."""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    order_repo = get_order_repository()
    empty = {
        "summary": {
            "total_sales": 0,
            "total_orders": 0,
            "avg_order_value": 0,
            "total_tax": 0,
            "total_discount": 0,
        },
        "dailyTrend": [],
        "categoryBreakdown": [],
    }
    if order_repo is None:
        return empty

    # BUG-104: the BOUNDS must be IST days too. _daily_trend now labels each
    # bucket with the IST day, so a naive-local window let a 22:30-UTC order in
    # and then labelled it with TOMORROW -- ask for June, get a bar dated 1
    # July. Bound and label have to share one frame or the chart contradicts
    # the range the reader asked for.
    from_dt = ist_day_start_utc(from_date)
    to_dt = ist_day_start_utc(to_date + timedelta(days=1)) - timedelta(microseconds=1)
    orders = _orders_in_window(
        order_repo,
        store_id=active_store,
        start_dt=from_dt,
        end_dt=to_dt,
    )
    return {
        "summary": _summarise_orders(orders),
        "dailyTrend": _daily_trend(orders),
        "categoryBreakdown": _category_breakdown(orders),
    }


@router.get("/sales/daily")
async def daily_sales(
    store_id: Optional[str] = Query(None),
    days: int = Query(30),
    current_user: dict = Depends(get_current_user),
):
    """Daily sales for the last N days (chart data)."""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    order_repo = get_order_repository()
    if order_repo is None:
        return {"data": []}
    # BUG-104: `now_ist_naive()` is the IST wall clock as a naive value, but
    # `created_at` is naive-UTC -- comparing them mixes frames and pushed the
    # window 5h30m into the future, so the last bar could be dated tomorrow.
    # `datetime.now()` is "now" in the SAME frame as the stored instant; the
    # start is pinned to an IST midnight so the bars are whole IST days.
    end_dt = datetime.now()
    start_dt = ist_day_start_utc(ist_today() - timedelta(days=days))
    orders = _orders_in_window(
        order_repo,
        store_id=active_store,
        start_dt=start_dt,
        end_dt=end_dt,
    )
    return {"data": _daily_trend(orders)}


@router.get("/sales/by-salesperson")
async def sales_by_salesperson(
    store_id: Optional[str] = Query(None),
    from_date: date = Query(...),
    to_date: date = Query(...),
    current_user: dict = Depends(require_roles(*_REPORT_FINANCE_ROLES)),
):
    """Get sales grouped by salesperson (management report; store-scoped)."""
    active_store = validate_store_access(store_id, current_user)
    order_repo = get_order_repository()

    if order_repo is None:
        return {"data": []}

    # BUG-104, BOUND rule (the bound moves BACKWARD). from_date/to_date are
    # IST calendar days the operator typed; created_at is a stored naive-UTC
    # instant, so an IST day starts 5h30m EARLIER in that frame. The old
    # naive-midnight window dropped every 00:00-05:30-IST sale on the FIRST
    # requested day and claimed the same band from the day AFTER to_date --
    # so this staff-sales report disagreed with the payout month window
    # (payout.py _month_window) and both leaderboard twins, which already
    # shift: two screens, two rosters, same month. Same shape as
    # /customers/acquisition below and /sales/summary above.
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

    # Group by salesperson. The credit rule and the name lookup are shared with
    # /staff/ranking below -- this report used to read `sales_person_id`, a key
    # an order has never carried (it is the walkouts spelling), so it fell
    # straight through to created_by and credited every sale to the biller,
    # then printed that raw user id as the "name".
    names = order_actor_name_map(get_db(), orders)
    by_person = {}
    for order in orders:
        person = order_actor_id(order)
        person_name = names.get(person) or person
        if person not in by_person:
            by_person[person] = {
                "id": person,
                "name": person_name,
                "sales": 0,
                "orders": 0,
            }
        # Use the canonical revenue reader (grand_total first) so the legacy
        # final_amount/total_amount-only sum doesn't zero out modern orders.
        by_person[person]["sales"] += _order_revenue(order)
        by_person[person]["orders"] += 1

    return {"data": list(by_person.values())}


@router.get("/sales/by-category")
async def sales_by_category(
    store_id: Optional[str] = Query(None),
    from_date: date = Query(...),
    to_date: date = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Sales grouped by product category for the requested window."""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    order_repo = get_order_repository()
    if order_repo is None:
        return {"data": []}
    # BUG-104, BOUND rule: same typed-IST-range defect as /sales/by-salesperson
    # above -- the naive-midnight window started 5h30m late in the stored
    # naive-UTC frame.
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
    return {"data": _category_breakdown(orders)}



"""
IMS 2.0 - Reports Router
=========================
Real database queries for dashboard and reports
"""

from fastapi import APIRouter, Depends, Query
from typing import Optional
from datetime import date, datetime, timedelta
from calendar import monthrange
from .auth import get_current_user
from ..dependencies import (
    get_order_repository,
    get_stock_repository,
    get_customer_repository,
    get_task_repository,
    get_attendance_repository,
    get_db,
)

router = APIRouter()



@router.get("/")
async def get_reports_root():
    """Root endpoint for available reports"""
    return {"module": "reports", "status": "active", "message": "reports overview endpoint ready"}


@router.get("/dashboard")
async def dashboard_stats(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get dashboard statistics for a store - fetched from database"""
    active_store = store_id or current_user.get("active_store_id") or "store-001"

    order_repo = get_order_repository()
    stock_repo = get_stock_repository()
    customer_repo = get_customer_repository()
    task_repo = get_task_repository()

    # Get today's date range
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_str = today.strftime("%Y-%m-%d")

    # Initialize stats
    total_sales = 0
    pending_orders = 0
    ready_orders = 0
    low_stock_items = 0
    today_orders = 0
    today_deliveries = 0
    today_new_customers = 0
    payments_received = 0

    # Fetch orders data
    if order_repo is not None:
        # Get all orders for store
        all_orders = order_repo.find_by_store(active_store)

        # Calculate totals
        for order in all_orders:
            status = order.get("status", "")
            order_date = order.get("created_at", "")[:10]

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
                total_sales += (
                    order.get("final_amount", 0)
                    or order.get("grand_total", 0)
                    or order.get("total_amount", 0)
                )

    # Fetch inventory data
    if stock_repo is not None:
        low_stock = stock_repo.find_low_stock(active_store, threshold=5)
        low_stock_items = len(low_stock) if low_stock else 0

    # Fetch customer data
    if customer_repo is not None:
        # Count customers created today
        all_customers = customer_repo.find_many({"store_id": active_store})
        for customer in all_customers:
            created_date = customer.get("created_at", "")[:10]
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

    return {
        "totalSales": total_sales,
        "change": 12.5,  # Would need historical data for comparison
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
    active_store = store_id or current_user.get("active_store_id") or "store-001"
    stock_repo = get_stock_repository()

    if stock_repo is not None:
        all_stock = stock_repo.find_many({"store_id": active_store})
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
    """Get sales summary for date range"""
    active_store = store_id or current_user.get("active_store_id")
    order_repo = get_order_repository()

    if order_repo is None:
        return {
            "summary": {
                "total_sales": 0,
                "total_orders": 0,
                "avg_order_value": 0,
                "total_tax": 0,
                "total_discount": 0,
            }
        }

    # Get orders in date range
    from_dt = datetime.combine(from_date, datetime.min.time())
    to_dt = datetime.combine(to_date, datetime.max.time())

    orders = order_repo.find_many(
        {
            "store_id": active_store,
            "created_at": {"$gte": from_dt.isoformat(), "$lte": to_dt.isoformat()},
            "status": {"$nin": ["CANCELLED", "DRAFT"]},
        }
    )

    total_sales = sum(
        o.get("final_amount", 0) or o.get("total_amount", 0) for o in orders
    )
    total_tax = sum(o.get("tax_amount", 0) for o in orders)
    total_discount = sum(o.get("discount_amount", 0) for o in orders)

    return {
        "summary": {
            "total_sales": total_sales,
            "total_orders": len(orders),
            "avg_order_value": round(total_sales / len(orders), 2) if orders else 0,
            "total_tax": total_tax,
            "total_discount": total_discount,
        }
    }


@router.get("/sales/daily")
async def daily_sales(
    store_id: Optional[str] = Query(None),
    days: int = Query(30),
    current_user: dict = Depends(get_current_user),
):
    """Get daily sales data for chart"""
    active_store = store_id or current_user.get("active_store_id")
    order_repo = get_order_repository()

    if order_repo is None:
        return {"data": []}

    # Get orders for last N days
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)

    orders = order_repo.find_many(
        {
            "store_id": active_store,
            "created_at": {"$gte": start_date.isoformat()},
            "status": {"$nin": ["CANCELLED", "DRAFT"]},
        }
    )

    # Group by date
    daily_data = {}
    for order in orders:
        order_date = order.get("created_at", "")[:10]  # Get just the date part
        if order_date:
            if order_date not in daily_data:
                daily_data[order_date] = {"date": order_date, "sales": 0, "orders": 0}
            daily_data[order_date]["sales"] += order.get(
                "final_amount", 0
            ) or order.get("total_amount", 0)
            daily_data[order_date]["orders"] += 1

    # Convert to sorted list
    data = sorted(daily_data.values(), key=lambda x: x["date"])

    return {"data": data}


@router.get("/sales/by-salesperson")
async def sales_by_salesperson(
    store_id: Optional[str] = Query(None),
    from_date: date = Query(...),
    to_date: date = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Get sales grouped by salesperson"""
    active_store = store_id or current_user.get("active_store_id")
    order_repo = get_order_repository()

    if order_repo is None:
        return {"data": []}

    from_dt = datetime.combine(from_date, datetime.min.time())
    to_dt = datetime.combine(to_date, datetime.max.time())

    orders = order_repo.find_many(
        {
            "store_id": active_store,
            "created_at": {"$gte": from_dt.isoformat(), "$lte": to_dt.isoformat()},
            "status": {"$nin": ["CANCELLED", "DRAFT"]},
        }
    )

    # Group by salesperson
    by_person = {}
    for order in orders:
        person = order.get("sales_person_id") or order.get("created_by") or "Unknown"
        person_name = order.get("sales_person_name", person)
        if person not in by_person:
            by_person[person] = {
                "id": person,
                "name": person_name,
                "sales": 0,
                "orders": 0,
            }
        by_person[person]["sales"] += order.get("final_amount", 0) or order.get(
            "total_amount", 0
        )
        by_person[person]["orders"] += 1

    return {"data": list(by_person.values())}


@router.get("/sales/by-category")
async def sales_by_category(
    store_id: Optional[str] = Query(None),
    from_date: date = Query(...),
    to_date: date = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Get sales grouped by product category"""
    active_store = store_id or current_user.get("active_store_id")
    order_repo = get_order_repository()

    if order_repo is None:
        return {"data": []}

    from_dt = datetime.combine(from_date, datetime.min.time())
    to_dt = datetime.combine(to_date, datetime.max.time())

    orders = order_repo.find_many(
        {
            "store_id": active_store,
            "created_at": {"$gte": from_dt.isoformat(), "$lte": to_dt.isoformat()},
            "status": {"$nin": ["CANCELLED", "DRAFT"]},
        }
    )

    # Aggregate by category from order items
    by_category = {}
    for order in orders:
        for item in order.get("items", []):
            category = item.get("category", "Other")
            if category not in by_category:
                by_category[category] = {
                    "category": category,
                    "sales": 0,
                    "quantity": 0,
                }
            by_category[category]["sales"] += item.get("total", 0) or (
                item.get("price", 0) * item.get("quantity", 1)
            )
            by_category[category]["quantity"] += item.get("quantity", 1)

    return {"data": list(by_category.values())}


# ============================================================================
# INVENTORY REPORTS
# ============================================================================


@router.get("/inventory/summary")
async def inventory_summary(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get inventory summary"""
    active_store = store_id or current_user.get("active_store_id")
    stock_repo = get_stock_repository()

    if stock_repo is None:
        return {
            "summary": {
                "total_items": 0,
                "total_quantity": 0,
                "total_value": 0,
                "low_stock_count": 0,
                "out_of_stock_count": 0,
            }
        }

    # Get all stock
    all_stock = stock_repo.find_many({"store_id": active_store})
    low_stock = stock_repo.find_low_stock(active_store, threshold=5)

    total_value = sum(
        (s.get("quantity", 0) * s.get("cost_price", 0)) for s in all_stock
    )

    out_of_stock = [s for s in all_stock if s.get("quantity", 0) <= 0]

    return {
        "summary": {
            "total_items": len(all_stock),
            "total_quantity": sum(s.get("quantity", 0) for s in all_stock),
            "total_value": round(total_value, 2),
            "low_stock_count": len(low_stock) if low_stock else 0,
            "out_of_stock_count": len(out_of_stock),
        }
    }


@router.get("/inventory/valuation")
async def inventory_valuation(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get inventory valuation by category"""
    active_store = store_id or current_user.get("active_store_id")
    stock_repo = get_stock_repository()

    if stock_repo is None:
        return {"valuation": {"by_category": [], "total": 0}}

    all_stock = stock_repo.find_many({"store_id": active_store})

    # Group by category
    by_category = {}
    for item in all_stock:
        category = item.get("category", "Other")
        if category not in by_category:
            by_category[category] = {"category": category, "quantity": 0, "value": 0}
        by_category[category]["quantity"] += item.get("quantity", 0)
        by_category[category]["value"] += item.get("quantity", 0) * item.get(
            "cost_price", 0
        )

    total = sum(c["value"] for c in by_category.values())

    return {
        "valuation": {
            "by_category": list(by_category.values()),
            "total": round(total, 2),
        }
    }


# ============================================================================
# CLINICAL REPORTS
# ============================================================================


@router.get("/clinical/eye-tests")
async def eye_test_report(
    store_id: Optional[str] = Query(None),
    from_date: date = Query(...),
    to_date: date = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Get eye test report"""
    # Would need clinical repository - return empty for now
    return {"data": [], "total": 0}


# ============================================================================
# HR REPORTS
# ============================================================================


@router.get("/hr/attendance")
async def attendance_report(
    store_id: Optional[str] = Query(None),
    year: int = Query(...),
    month: int = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Get attendance report for month"""
    active_store = store_id or current_user.get("active_store_id")
    attendance_repo = get_attendance_repository()

    if attendance_repo is None:
        return {"data": [], "summary": {"total_present": 0, "total_absent": 0}}

    # Get attendance for month
    start_date = datetime(year, month, 1)
    if month == 12:
        end_date = datetime(year + 1, 1, 1)
    else:
        end_date = datetime(year, month + 1, 1)

    records = attendance_repo.find_many(
        {
            "store_id": active_store,
            "date": {
                "$gte": start_date.isoformat()[:10],
                "$lt": end_date.isoformat()[:10],
            },
        }
    )

    return {
        "data": records,
        "summary": {
            "total_present": len([r for r in records if r.get("status") == "PRESENT"]),
            "total_absent": len([r for r in records if r.get("status") == "ABSENT"]),
        },
    }


# ============================================================================
# FINANCE REPORTS
# ============================================================================


@router.get("/finance/outstanding")
async def outstanding_report(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get outstanding payments report"""
    active_store = store_id or current_user.get("active_store_id")
    order_repo = get_order_repository()

    if order_repo is None:
        return {"data": [], "total_outstanding": 0}

    # Get orders with balance due
    orders = order_repo.find_many(
        {
            "store_id": active_store,
            "balance_due": {"$gt": 0},
            "status": {"$nin": ["CANCELLED", "DRAFT"]},
        }
    )

    outstanding_data = []
    total = 0
    now = datetime.now()
    aging_buckets = {"0_30": 0.0, "31_60": 0.0, "61_90": 0.0, "90_plus": 0.0}

    for order in orders:
        balance = order.get("balance_due", 0)
        if balance > 0:
            total += balance

            # Calculate aging bucket from order creation date
            created_str = order.get("created_at", "")
            days_old = 0
            try:
                if isinstance(created_str, str) and created_str:
                    created_dt = datetime.fromisoformat(created_str.replace("Z", "+00:00").replace("+00:00", ""))
                    days_old = (now - created_dt).days
                elif isinstance(created_str, datetime):
                    days_old = (now - created_str).days
            except (ValueError, TypeError):
                days_old = 0

            if days_old <= 30:
                bucket = "0-30 days"
                aging_buckets["0_30"] += balance
            elif days_old <= 60:
                bucket = "31-60 days"
                aging_buckets["31_60"] += balance
            elif days_old <= 90:
                bucket = "61-90 days"
                aging_buckets["61_90"] += balance
            else:
                bucket = "90+ days"
                aging_buckets["90_plus"] += balance

            outstanding_data.append(
                {
                    "order_id": order.get("order_id"),
                    "order_number": order.get("order_number"),
                    "customer_name": order.get("customer_name"),
                    "customer_phone": order.get("customer_phone"),
                    "total_amount": order.get("final_amount", 0),
                    "paid_amount": order.get("paid_amount", 0),
                    "balance_due": balance,
                    "created_at": order.get("created_at"),
                    "days_outstanding": days_old,
                    "aging_bucket": bucket,
                }
            )

    # Sort by age (oldest first)
    outstanding_data.sort(key=lambda x: x.get("days_outstanding", 0), reverse=True)

    return {
        "data": outstanding_data,
        "total_outstanding": total,
        "aging_summary": {
            "0-30 days": round(aging_buckets["0_30"], 2),
            "31-60 days": round(aging_buckets["31_60"], 2),
            "61-90 days": round(aging_buckets["61_90"], 2),
            "90+ days": round(aging_buckets["90_plus"], 2),
        },
        "count_by_aging": {
            "0-30 days": sum(1 for d in outstanding_data if d["aging_bucket"] == "0-30 days"),
            "31-60 days": sum(1 for d in outstanding_data if d["aging_bucket"] == "31-60 days"),
            "61-90 days": sum(1 for d in outstanding_data if d["aging_bucket"] == "61-90 days"),
            "90+ days": sum(1 for d in outstanding_data if d["aging_bucket"] == "90+ days"),
        },
    }


@router.get("/finance/gst")
async def gst_report(
    from_date: date = Query(...),
    to_date: date = Query(...),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get GST report for date range"""
    active_store = store_id or current_user.get("active_store_id")
    order_repo = get_order_repository()

    if order_repo is None:
        return {
            "data": [],
            "summary": {"total_cgst": 0, "total_sgst": 0, "total_igst": 0},
        }

    from_dt = datetime.combine(from_date, datetime.min.time())
    to_dt = datetime.combine(to_date, datetime.max.time())

    orders = order_repo.find_many(
        {
            "store_id": active_store,
            "created_at": {"$gte": from_dt.isoformat(), "$lte": to_dt.isoformat()},
            "status": {"$nin": ["CANCELLED", "DRAFT"]},
        }
    )

    total_cgst = sum(o.get("cgst_amount", 0) for o in orders)
    total_sgst = sum(o.get("sgst_amount", 0) for o in orders)
    total_igst = sum(o.get("igst_amount", 0) for o in orders)

    return {
        "data": [
            {
                "order_number": o.get("order_number"),
                "date": o.get("created_at", "")[:10],
                "taxable_amount": o.get("taxable_amount", 0),
                "cgst": o.get("cgst_amount", 0),
                "sgst": o.get("sgst_amount", 0),
                "igst": o.get("igst_amount", 0),
                "total": o.get("final_amount", 0),
            }
            for o in orders
        ],
        "summary": {
            "total_cgst": total_cgst,
            "total_sgst": total_sgst,
            "total_igst": total_igst,
            "total_tax": total_cgst + total_sgst + total_igst,
        },
    }


# ============================================================================
# TASK REPORTS
# ============================================================================


@router.get("/tasks/summary")
async def task_summary(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get task summary"""
    active_store = store_id or current_user.get("active_store_id")
    task_repo = get_task_repository()

    if task_repo is None:
        return {
            "summary": {
                "open": 0,
                "in_progress": 0,
                "completed": 0,
                "overdue": 0,
            }
        }

    summary = task_repo.get_task_summary(active_store)
    overdue_count = task_repo.get_overdue_count(active_store)

    return {
        "summary": {
            "open": summary.get("OPEN", 0) if summary else 0,
            "in_progress": summary.get("IN_PROGRESS", 0) if summary else 0,
            "completed": summary.get("COMPLETED", 0) if summary else 0,
            "overdue": overdue_count or 0,
        }
    }


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
    active_store = store_id or current_user.get("active_store_id")
    order_repo = get_order_repository()

    if order_repo is None:
        return {"current_period": {}, "previous_period": {}, "comparison": {}}

    from_dt = datetime.combine(from_date, datetime.min.time())
    to_dt = datetime.combine(to_date, datetime.max.time())

    # Calculate period difference
    period_days = (to_date - from_date).days
    prev_from_date = from_date - timedelta(days=period_days + 1)
    prev_to_date = from_date - timedelta(days=1)

    prev_from_dt = datetime.combine(prev_from_date, datetime.min.time())
    prev_to_dt = datetime.combine(prev_to_date, datetime.max.time())

    # Get current period orders
    current_orders = order_repo.find_many(
        {
            "store_id": active_store,
            "created_at": {"$gte": from_dt.isoformat(), "$lte": to_dt.isoformat()},
            "status": {"$nin": ["CANCELLED", "DRAFT"]},
        }
    )

    # Get previous period orders
    prev_orders = order_repo.find_many(
        {
            "store_id": active_store,
            "created_at": {"$gte": prev_from_dt.isoformat(), "$lte": prev_to_dt.isoformat()},
            "status": {"$nin": ["CANCELLED", "DRAFT"]},
        }
    )

    current_sales = sum(o.get("final_amount", 0) or o.get("total_amount", 0) for o in current_orders)
    prev_sales = sum(o.get("final_amount", 0) or o.get("total_amount", 0) for o in prev_orders)

    change = ((current_sales - prev_sales) / prev_sales * 100) if prev_sales > 0 else 0

    return {
        "current_period": {
            "sales": round(current_sales, 2),
            "orders": len(current_orders),
            "avg_order_value": round(current_sales / len(current_orders), 2) if current_orders else 0,
        },
        "previous_period": {
            "sales": round(prev_sales, 2),
            "orders": len(prev_orders),
            "avg_order_value": round(prev_sales / len(prev_orders), 2) if prev_orders else 0,
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
    active_store = store_id or current_user.get("active_store_id")
    order_repo = get_order_repository()

    if order_repo is None:
        return {"current_month": {}, "mom_growth": {}, "yoy_growth": {}}

    # Current month
    current_start = datetime(year, month, 1)
    if month == 12:
        current_end = datetime(year + 1, 1, 1) - timedelta(seconds=1)
    else:
        current_end = datetime(year, month + 1, 1) - timedelta(seconds=1)

    # Previous month (MoM)
    if month == 1:
        mom_start = datetime(year - 1, 12, 1)
        mom_end = datetime(year, 1, 1) - timedelta(seconds=1)
    else:
        mom_start = datetime(year, month - 1, 1)
        mom_end = datetime(year, month, 1) - timedelta(seconds=1)

    # Previous year (YoY)
    yoy_start = datetime(year - 1, month, 1)
    if month == 12:
        yoy_end = datetime(year, 1, 1) - timedelta(seconds=1)
    else:
        yoy_end = datetime(year - 1, month + 1, 1) - timedelta(seconds=1)

    current_orders = order_repo.find_many({
        "store_id": active_store,
        "created_at": {"$gte": current_start.isoformat(), "$lte": current_end.isoformat()},
        "status": {"$nin": ["CANCELLED", "DRAFT"]},
    })

    mom_orders = order_repo.find_many({
        "store_id": active_store,
        "created_at": {"$gte": mom_start.isoformat(), "$lte": mom_end.isoformat()},
        "status": {"$nin": ["CANCELLED", "DRAFT"]},
    })

    yoy_orders = order_repo.find_many({
        "store_id": active_store,
        "created_at": {"$gte": yoy_start.isoformat(), "$lte": yoy_end.isoformat()},
        "status": {"$nin": ["CANCELLED", "DRAFT"]},
    })

    current_sales = sum(o.get("final_amount", 0) or o.get("total_amount", 0) for o in current_orders)
    mom_sales = sum(o.get("final_amount", 0) or o.get("total_amount", 0) for o in mom_orders)
    yoy_sales = sum(o.get("final_amount", 0) or o.get("total_amount", 0) for o in yoy_orders)

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
    current_user: dict = Depends(get_current_user),
):
    """Profit by product category"""
    active_store = store_id or current_user.get("active_store_id")
    order_repo = get_order_repository()

    if order_repo is None:
        return {"data": [], "total_profit": 0}

    from_dt = datetime.combine(from_date, datetime.min.time())
    to_dt = datetime.combine(to_date, datetime.max.time())

    orders = order_repo.find_many({
        "store_id": active_store,
        "created_at": {"$gte": from_dt.isoformat(), "$lte": to_dt.isoformat()},
        "status": {"$nin": ["CANCELLED", "DRAFT"]},
    })

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
            selling_price = item.get("total", 0) or (item.get("price", 0) * item.get("quantity", 1))
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
    current_user: dict = Depends(get_current_user),
):
    """Profit by store (if multi-store)"""
    order_repo = get_order_repository()

    if order_repo is None:
        return {"data": [], "total_profit": 0}

    from_dt = datetime.combine(from_date, datetime.min.time())
    to_dt = datetime.combine(to_date, datetime.max.time())

    orders = order_repo.find_many({
        "created_at": {"$gte": from_dt.isoformat(), "$lte": to_dt.isoformat()},
        "status": {"$nin": ["CANCELLED", "DRAFT"]},
    })

    profit_by_st = {}
    for order in orders:
        store = order.get("store_id", "Unknown")
        if store not in profit_by_st:
            profit_by_st[store] = {
                "store_id": store,
                "revenue": 0,
                "cost": 0,
                "profit": 0,
                "orders": 0,
            }
        profit_by_st[store]["orders"] += 1
        order_amount = order.get("final_amount", 0) or order.get("total_amount", 0)
        profit_by_st[store]["revenue"] += order_amount
        
        # Calculate cost from items
        cost = sum(item.get("cost_price", 0) * item.get("quantity", 1) for item in order.get("items", []))
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
    current_user: dict = Depends(get_current_user),
):
    """Discount average by category and store"""
    active_store = store_id or current_user.get("active_store_id")
    order_repo = get_order_repository()

    if order_repo is None:
        return {"by_category": [], "by_store": [], "summary": {}}

    from_dt = datetime.combine(from_date, datetime.min.time())
    to_dt = datetime.combine(to_date, datetime.max.time())

    orders = order_repo.find_many({
        "store_id": active_store,
        "created_at": {"$gte": from_dt.isoformat(), "$lte": to_dt.isoformat()},
        "status": {"$nin": ["CANCELLED", "DRAFT"]},
    })

    by_category = {}
    total_discount = 0
    total_revenue = 0
    
    for order in orders:
        for item in order.get("items", []):
            category = item.get("category", "Other")
            if category not in by_category:
                by_category[category] = {
                    "category": category,
                    "total_discount": 0,
                    "total_items": 0,
                    "avg_discount_percent": 0,
                }
            item_discount = item.get("discount", 0)
            item_price = item.get("price", 0) * item.get("quantity", 1)
            by_category[category]["total_discount"] += item_discount
            by_category[category]["total_items"] += item.get("quantity", 1)
            total_discount += item_discount
            total_revenue += item_price

    for cat in by_category.values():
        if cat["total_items"] > 0:
            cat["avg_discount_percent"] = round((cat["total_discount"] / (cat["total_discount"] + (total_revenue / len(by_category)))) * 100, 2)

    return {
        "by_category": list(by_category.values()),
        "summary": {
            "total_discount": round(total_discount, 2),
            "discount_percent": round((total_discount / (total_discount + total_revenue) * 100), 2) if (total_discount + total_revenue) > 0 else 0,
        },
    }


# ============================================================================
# STAFF & CLINICAL REPORTS
# ============================================================================


@router.get("/staff/ranking")
async def staff_ranking(
    store_id: Optional[str] = Query(None),
    from_date: date = Query(...),
    to_date: date = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Staff performance ranking (sales, orders, avg bill)"""
    active_store = store_id or current_user.get("active_store_id")
    order_repo = get_order_repository()

    if order_repo is None:
        return {"data": []}

    from_dt = datetime.combine(from_date, datetime.min.time())
    to_dt = datetime.combine(to_date, datetime.max.time())

    orders = order_repo.find_many({
        "store_id": active_store,
        "created_at": {"$gte": from_dt.isoformat(), "$lte": to_dt.isoformat()},
        "status": {"$nin": ["CANCELLED", "DRAFT"]},
    })

    staff_data = {}
    for order in orders:
        staff_id = order.get("sales_person_id") or order.get("created_by") or "Unknown"
        staff_name = order.get("sales_person_name", staff_id)
        if staff_id not in staff_data:
            staff_data[staff_id] = {
                "staff_id": staff_id,
                "staff_name": staff_name,
                "total_sales": 0,
                "order_count": 0,
                "avg_bill": 0,
            }
        order_amount = order.get("final_amount", 0) or order.get("total_amount", 0)
        staff_data[staff_id]["total_sales"] += order_amount
        staff_data[staff_id]["order_count"] += 1

    for staff in staff_data.values():
        if staff["order_count"] > 0:
            staff["avg_bill"] = round(staff["total_sales"] / staff["order_count"], 2)

    # Sort by total sales descending
    ranked = sorted(staff_data.values(), key=lambda x: x["total_sales"], reverse=True)

    return {"data": ranked}


@router.get("/clinical/eye-tests")
async def eye_tests_report(
    store_id: Optional[str] = Query(None),
    from_date: date = Query(...),
    to_date: date = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Eye test count report (by optometrist, by store)"""
    active_store = store_id or current_user.get("active_store_id")
    task_repo = get_task_repository()

    if task_repo is None:
        return {"by_optometrist": [], "by_store": [], "total": 0}

    from_dt = datetime.combine(from_date, datetime.min.time())
    to_dt = datetime.combine(to_date, datetime.max.time())

    # Get all tasks/appointments (eye tests)
    tasks = task_repo.find_many({
        "store_id": active_store,
        "created_at": {"$gte": from_dt.isoformat(), "$lte": to_dt.isoformat()},
        "task_type": "eye_test",
    })

    by_optometrist = {}
    for task in tasks:
        optom_id = task.get("assigned_to", "Unknown")
        optom_name = task.get("assigned_to_name", optom_id)
        if optom_id not in by_optometrist:
            by_optometrist[optom_id] = {
                "optometrist_id": optom_id,
                "optometrist_name": optom_name,
                "test_count": 0,
            }
        by_optometrist[optom_id]["test_count"] += 1

    return {
        "by_optometrist": list(by_optometrist.values()),
        "total": len(tasks),
    }


# ============================================================================
# WORKSHOP & STOCK REPORTS
# ============================================================================


@router.get("/workshop/pending-jobs")
async def pending_workshop_jobs(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Pending workshop jobs report"""
    active_store = store_id or current_user.get("active_store_id")
    task_repo = get_task_repository()

    if task_repo is None:
        return {"data": [], "total_pending": 0}

    pending_tasks = task_repo.find_many({
        "store_id": active_store,
        "task_type": "workshop_job",
        "status": {"$in": ["OPEN", "IN_PROGRESS"]},
    })

    data = []
    for task in pending_tasks:
        data.append({
            "job_id": task.get("task_id"),
            "description": task.get("title"),
            "status": task.get("status"),
            "priority": task.get("priority"),
            "assigned_to": task.get("assigned_to_name"),
            "due_date": task.get("due_date"),
            "created_at": task.get("created_at"),
        })

    return {
        "data": data,
        "total_pending": len(pending_tasks),
    }


@router.get("/stock/count")
async def daily_stock_count(
    store_id: Optional[str] = Query(None),
    from_date: date = Query(...),
    to_date: date = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Daily stock count report"""
    active_store = store_id or current_user.get("active_store_id")
    stock_repo = get_stock_repository()

    if stock_repo is None:
        return {"data": [], "summary": {}}

    all_stock = stock_repo.find_many({"store_id": active_store})

    by_category = {}
    total_items = 0
    total_value = 0
    
    for item in all_stock:
        category = item.get("category", "Other")
        if category not in by_category:
            by_category[category] = {
                "category": category,
                "item_count": 0,
                "total_quantity": 0,
                "total_value": 0,
            }
        by_category[category]["item_count"] += 1
        by_category[category]["total_quantity"] += item.get("quantity", 0)
        by_category[category]["total_value"] += item.get("quantity", 0) * item.get("cost_price", 0)
        total_items += 1
        total_value += item.get("quantity", 0) * item.get("cost_price", 0)

    return {
        "data": list(by_category.values()),
        "summary": {
            "total_items": total_items,
            "total_value": round(total_value, 2),
            "total_quantity": sum(item.get("quantity", 0) for item in all_stock),
        },
    }


# ============================================================================
# FINANCE & CUSTOMER REPORTS
# ============================================================================


@router.get("/finance/expense-vs-revenue")
async def expense_vs_revenue(
    store_id: Optional[str] = Query(None),
    from_date: date = Query(...),
    to_date: date = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Expense vs revenue comparison"""
    active_store = store_id or current_user.get("active_store_id")
    order_repo = get_order_repository()

    if order_repo is None:
        return {"revenue": 0, "cost": 0, "profit": 0, "margin_percent": 0}

    from_dt = datetime.combine(from_date, datetime.min.time())
    to_dt = datetime.combine(to_date, datetime.max.time())

    orders = order_repo.find_many({
        "store_id": active_store,
        "created_at": {"$gte": from_dt.isoformat(), "$lte": to_dt.isoformat()},
        "status": {"$nin": ["CANCELLED", "DRAFT"]},
    })

    revenue = sum(o.get("final_amount", 0) or o.get("total_amount", 0) for o in orders)
    cost = 0
    
    for order in orders:
        for item in order.get("items", []):
            cost += item.get("cost_price", 0) * item.get("quantity", 1)

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
    active_store = store_id or current_user.get("active_store_id")
    customer_repo = get_customer_repository()
    order_repo = get_order_repository()

    if customer_repo is None:
        return {
            "new_customers": 0,
            "returning_customers": 0,
            "total_customers": 0,
            "retention_percent": 0,
        }

    from_dt = datetime.combine(from_date, datetime.min.time())
    to_dt = datetime.combine(to_date, datetime.max.time())

    # Get all customers
    all_customers = customer_repo.find_many({"store_id": active_store})

    # Count new customers in period
    new_customers = len([
        c for c in all_customers
        if from_dt.isoformat() <= c.get("created_at", "") <= to_dt.isoformat()
    ])

    # Count returning customers (placed orders in period but created before)
    if order_repo:
        orders = order_repo.find_many({
            "store_id": active_store,
            "created_at": {"$gte": from_dt.isoformat(), "$lte": to_dt.isoformat()},
        })
        repeat_customers = {}
        for order in orders:
            cust_id = order.get("customer_id")
            if cust_id:
                repeat_customers[cust_id] = repeat_customers.get(cust_id, 0) + 1
        
        returning_customers = len([c for c in repeat_customers.values() if c > 1])
    else:
        returning_customers = 0

    retention_percent = ((returning_customers / new_customers * 100) if new_customers > 0 else 0)

    return {
        "new_customers": new_customers,
        "returning_customers": returning_customers,
        "total_customers": len(all_customers),
        "retention_percent": round(retention_percent, 2),
    }


@router.get("/inventory/brand-sellthrough")
async def brand_sellthrough(
    store_id: Optional[str] = Query(None),
    from_date: date = Query(...),
    to_date: date = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Brand-wise sell-through report"""
    active_store = store_id or current_user.get("active_store_id")
    order_repo = get_order_repository()
    stock_repo = get_stock_repository()

    if order_repo is None:
        return {"data": [], "summary": {}}

    from_dt = datetime.combine(from_date, datetime.min.time())
    to_dt = datetime.combine(to_date, datetime.max.time())

    orders = order_repo.find_many({
        "store_id": active_store,
        "created_at": {"$gte": from_dt.isoformat(), "$lte": to_dt.isoformat()},
        "status": {"$nin": ["CANCELLED", "DRAFT"]},
    })

    # Track brand sales
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
            by_brand[brand]["quantity_sold"] += item.get("quantity", 1)
            by_brand[brand]["revenue"] += item.get("total", 0) or (item.get("price", 0) * item.get("quantity", 1))

    # Calculate average price
    for brand in by_brand.values():
        if brand["quantity_sold"] > 0:
            brand["avg_price"] = round(brand["revenue"] / brand["quantity_sold"], 2)

    # Get current stock by brand
    if stock_repo:
        current_stock = stock_repo.find_many({"store_id": active_store})
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
                brand["sellthrough_percent"] = round(
                    (brand["quantity_sold"] / total_stock * 100), 2
                ) if total_stock > 0 else 0

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
    active_store = store_id or current_user.get("active_store_id") or "store-001"
    
    # Default targets
    targets = {
        "store_id": active_store,
        "daily_target": 50000,
        "monthly_target": 1500000,
        "currency": "INR",
        "period": datetime.now().strftime("%Y-%m"),
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


# ============================================================================
# GST RETURNS - GSTR-1 (Outward Supplies)
# ============================================================================


def _get_raw_db():
    """Get raw MongoDB database object for aggregation queries."""
    try:
        conn = get_db()
        if conn is not None and conn.is_connected:
            return conn.db
    except Exception:
        pass
    return None


@router.get("/gstr1")
async def gstr1_report(
    month: str = Query(..., description="Tax period in YYYY-MM format"),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """
    GSTR-1 report: outward supplies aggregated from the orders collection.

    Classifies invoices into:
      - B2B  : orders where the customer has a GSTIN on file
      - B2CL : orders to consumers with invoice value > 250000
      - B2CS : consolidated summary of remaining consumer invoices
    Returns empty lists/summaries when no data exists for the period.
    """
    active_store = store_id or current_user.get("active_store_id") or "store-001"

    # Parse month to date range
    try:
        year, mon = int(month[:4]), int(month[5:7])
        _, last_day = monthrange(year, mon)
        from_dt = datetime(year, mon, 1, 0, 0, 0)
        to_dt = datetime(year, mon, last_day, 23, 59, 59)
    except Exception:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="month must be in YYYY-MM format")

    from_iso = from_dt.isoformat()
    to_iso = to_dt.isoformat()

    b2b: list = []
    b2cl: list = []
    b2cs_map: dict = {}

    db = _get_raw_db()
    if db is not None:
        try:
            orders_col = db["orders"]
            customers_col = db["customers"]

            # Build a GSTIN lookup from customers collection
            gstin_map: dict = {}
            try:
                for cust in customers_col.find(
                    {"gstin": {"$exists": True, "$nin": [None, ""]}},
                    {"customer_id": 1, "gstin": 1, "name": 1},
                ):
                    gstin_map[str(cust.get("customer_id", ""))] = {
                        "gstin": cust.get("gstin", ""),
                        "name": cust.get("name", ""),
                    }
            except Exception:
                pass

            # Fetch completed orders in the date range
            query = {
                "store_id": active_store,
                "status": {"$nin": ["CANCELLED", "DRAFT", "cancelled", "draft"]},
                "created_at": {"$gte": from_iso, "$lte": to_iso},
            }

            for order in orders_col.find(query):
                cust_id = str(order.get("customer_id", ""))
                cust_info = gstin_map.get(cust_id, {})
                customer_gstin = cust_info.get("gstin", "")
                customer_name = cust_info.get("name", "") or order.get("customer_name", "Walk-in Customer")

                invoice_value = float(order.get("total_amount", 0))
                taxable_value = float(order.get("taxable_amount", 0))
                cgst = float(order.get("cgst_amount", 0))
                sgst = float(order.get("sgst_amount", 0))
                igst = float(order.get("igst_amount", 0))
                total_tax = cgst + sgst + igst

                bill_number = order.get("bill_number", order.get("order_number", ""))
                created_raw = order.get("created_at", "")
                invoice_date = str(created_raw)[:10] if created_raw else month + "-01"

                # Determine place of supply (intra = CGST+SGST, inter = IGST)
                is_igst = igst > 0
                place_of_supply = "Inter-State" if is_igst else "Maharashtra"

                base_invoice = {
                    "invoiceNumber": bill_number,
                    "invoiceDate": invoice_date,
                    "customerName": customer_name,
                    "placeOfSupply": place_of_supply,
                    "invoiceValue": round(invoice_value, 2),
                    "taxableValue": round(taxable_value, 2),
                    "cgst": round(cgst, 2),
                    "sgst": round(sgst, 2),
                    "igst": round(igst, 2),
                    "totalTax": round(total_tax, 2),
                    "hsnCode": "9004",
                    "gstRate": 5,
                }

                if customer_gstin:
                    # B2B: registered business with GSTIN
                    b2b.append({
                        **base_invoice,
                        "customerGSTIN": customer_gstin,
                        "customerState": "Maharashtra" if not is_igst else "Other State",
                    })
                elif invoice_value > 250000:
                    # B2CL: large consumer invoice
                    b2cl.append({
                        **base_invoice,
                        "customerState": place_of_supply,
                    })
                else:
                    # B2CS: small consumer invoice — consolidate by place_of_supply + gst_rate
                    # Determine effective GST rate (approximate from amounts)
                    if taxable_value > 0:
                        effective_rate = round((total_tax / taxable_value) * 100)
                        # Snap to standard rates
                        if effective_rate <= 6:
                            effective_rate = 5
                        elif effective_rate <= 14:
                            effective_rate = 12
                        else:
                            effective_rate = 18
                    else:
                        effective_rate = 5

                    key = f"{place_of_supply}|{effective_rate}"
                    if key not in b2cs_map:
                        b2cs_map[key] = {
                            "placeOfSupply": place_of_supply,
                            "gstRate": effective_rate,
                            "taxableValue": 0.0,
                            "cgst": 0.0,
                            "sgst": 0.0,
                            "igst": 0.0,
                            "totalTax": 0.0,
                        }
                    b2cs_map[key]["taxableValue"] += taxable_value
                    b2cs_map[key]["cgst"] += cgst
                    b2cs_map[key]["sgst"] += sgst
                    b2cs_map[key]["igst"] += igst
                    b2cs_map[key]["totalTax"] += total_tax

        except Exception:
            pass

    b2cs = [
        {
            **v,
            "taxableValue": round(v["taxableValue"], 2),
            "cgst": round(v["cgst"], 2),
            "sgst": round(v["sgst"], 2),
            "igst": round(v["igst"], 2),
            "totalTax": round(v["totalTax"], 2),
        }
        for v in b2cs_map.values()
    ]

    total_invoices = len(b2b) + len(b2cl) + len(b2cs_map)
    total_taxable = (
        sum(i["taxableValue"] for i in b2b)
        + sum(i["taxableValue"] for i in b2cl)
        + sum(v["taxableValue"] for v in b2cs)
    )
    total_tax = (
        sum(i["totalTax"] for i in b2b)
        + sum(i["totalTax"] for i in b2cl)
        + sum(v["totalTax"] for v in b2cs)
    )

    return {
        "period": month,
        "gstin": "",
        "legalName": "",
        "totalInvoices": total_invoices,
        "totalTaxableValue": round(total_taxable, 2),
        "totalTax": round(total_tax, 2),
        "b2b": b2b,
        "b2cl": b2cl,
        "b2cs": b2cs,
    }


# ============================================================================
# GST RETURNS - GSTR-3B (Summary Return)
# ============================================================================


@router.get("/gstr3b")
async def gstr3b_report(
    month: str = Query(..., description="Tax period in YYYY-MM format"),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """
    GSTR-3B summary return: aggregates output tax from the orders collection.

    Table 3.1 - Outward taxable supplies: derived from completed sales invoices.
    Table 4   - ITC available: derived from purchase GRNs (grns collection).
                Returns zeros when no purchase data is present.
    Table 6.1 - Payment of tax: net cash liability = output tax - ITC.
    Returns all-zero figures when no data exists for the period.
    """
    active_store = store_id or current_user.get("active_store_id") or "store-001"

    try:
        year, mon = int(month[:4]), int(month[5:7])
        _, last_day = monthrange(year, mon)
        from_dt = datetime(year, mon, 1, 0, 0, 0)
        to_dt = datetime(year, mon, last_day, 23, 59, 59)
    except Exception:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="month must be in YYYY-MM format")

    from_iso = from_dt.isoformat()
    to_iso = to_dt.isoformat()

    # Output tax accumulators
    out_igst = 0.0
    out_cgst = 0.0
    out_sgst = 0.0
    out_taxable = 0.0

    # ITC accumulators (from purchase GRNs)
    itc_igst = 0.0
    itc_cgst = 0.0
    itc_sgst = 0.0

    db = _get_raw_db()
    if db is not None:
        try:
            # --- Output tax from orders ---
            orders_col = db["orders"]
            pipeline = [
                {
                    "$match": {
                        "store_id": active_store,
                        "status": {"$nin": ["CANCELLED", "DRAFT", "cancelled", "draft"]},
                        "created_at": {"$gte": from_iso, "$lte": to_iso},
                    }
                },
                {
                    "$group": {
                        "_id": None,
                        "igst": {"$sum": "$igst_amount"},
                        "cgst": {"$sum": "$cgst_amount"},
                        "sgst": {"$sum": "$sgst_amount"},
                        "taxable": {"$sum": "$taxable_amount"},
                    }
                },
            ]
            result = list(orders_col.aggregate(pipeline))
            if result:
                agg = result[0]
                out_igst = float(agg.get("igst", 0.0))
                out_cgst = float(agg.get("cgst", 0.0))
                out_sgst = float(agg.get("sgst", 0.0))
                out_taxable = float(agg.get("taxable", 0.0))
        except Exception:
            pass

        try:
            # --- ITC from purchase GRNs (goods received notes) ---
            grns_col = db["grns"]
            itc_pipeline = [
                {
                    "$match": {
                        "store_id": active_store,
                        "status": {"$nin": ["CANCELLED", "cancelled"]},
                        "created_at": {"$gte": from_iso, "$lte": to_iso},
                    }
                },
                {
                    "$group": {
                        "_id": None,
                        "igst": {"$sum": "$igst_amount"},
                        "cgst": {"$sum": "$cgst_amount"},
                        "sgst": {"$sum": "$sgst_amount"},
                    }
                },
            ]
            itc_result = list(grns_col.aggregate(itc_pipeline))
            if itc_result:
                itc_agg = itc_result[0]
                itc_igst = float(itc_agg.get("igst", 0.0))
                itc_cgst = float(itc_agg.get("cgst", 0.0))
                itc_sgst = float(itc_agg.get("sgst", 0.0))
        except Exception:
            # grns collection may not exist or have no GST fields
            pass

    # Net cash liability = output tax - ITC (floor at 0 per component)
    cash_igst = max(0.0, out_igst - itc_igst)
    cash_cgst = max(0.0, out_cgst - itc_cgst)
    cash_sgst = max(0.0, out_sgst - itc_sgst)

    def _r(v: float) -> float:
        return round(v, 2)

    return {
        "period": month,
        "gstin": "",
        "legalName": "",
        "outwardTaxableValue": _r(out_taxable),
        "outwardTaxableSupplies": {
            "integratedTax": _r(out_igst),
            "centralTax": _r(out_cgst),
            "stateTax": _r(out_sgst),
            "cess": 0.0,
        },
        "zeroRatedValue": 0.0,
        "zeroRatedSupplies": {
            "integratedTax": 0.0,
            "centralTax": 0.0,
            "stateTax": 0.0,
            "cess": 0.0,
        },
        "itcAvailable": {
            "integratedTax": _r(itc_igst),
            "centralTax": _r(itc_cgst),
            "stateTax": _r(itc_sgst),
            "cess": 0.0,
        },
        "exemptSupplies": 0.0,
        "taxPayable": {
            "integratedTax": _r(out_igst),
            "centralTax": _r(out_cgst),
            "stateTax": _r(out_sgst),
            "cess": 0.0,
        },
        "itcUtilized": {
            "integratedTax": _r(itc_igst),
            "centralTax": _r(itc_cgst),
            "stateTax": _r(itc_sgst),
            "cess": 0.0,
        },
        "taxPaidCash": {
            "integratedTax": _r(cash_igst),
            "centralTax": _r(cash_cgst),
            "stateTax": _r(cash_sgst),
            "cess": 0.0,
        },
        "interest": {
            "integratedTax": 0.0,
            "centralTax": 0.0,
            "stateTax": 0.0,
            "cess": 0.0,
        },
        "lateFee": 0.0,
    }


# ============================================================================
# NON-MOVING STOCK REPORT (Phase 6.3)
# ============================================================================
# "Which SKUs are tying up cash without turning over?" — core question for
# an optical retailer doing monthly clearance decisions. Anything that
# hasn't sold in 90+ days is a candidate for discount, transfer, or return.
# Finance dashboards traditionally pull this as "dead stock" aging.


@router.get("/inventory/non-moving-stock")
async def non_moving_stock(
    store_id: Optional[str] = Query(None),
    days: int = Query(90, ge=1, le=365, description="Products with no sale in the last N days"),
    limit: int = Query(200, ge=1, le=1000),
    current_user: dict = Depends(get_current_user),
):
    """
    Products that haven't sold in the last N days (default 90).

    Returns one row per stale product, sorted by most stale first. A
    product that has NEVER sold is surfaced at the top with
    `never_sold: true` and `days_since_sold: null`. `last_sold_at` is the
    ISO timestamp of the most recent non-cancelled order containing the
    product.

    Response shape:
        {
            "data": [ {product_id, sku, brand, model, category, mrp,
                        last_sold_at, days_since_sold, never_sold,
                        total_sold_all_time} ... ],
            "count": int,
            "as_of": ISO timestamp,
            "days_threshold": int,
            "store_id": str,
        }

    Edge cases:
      - DB unavailable -> returns empty data + 0 count. Does not raise.
      - Product has sale timestamp in a format we can't parse -> treated
        as never_sold (conservative: surface it rather than hide it).
      - `days=1` gives you yesterday's dead pile; `days=365` gives you
        the full year of no-movement.
    """
    from datetime import timezone  # local import, keeps module-top imports minimal
    active_store = store_id or current_user.get("active_store_id")
    db = get_db()

    if db is None or not getattr(db, "is_connected", True):
        return {
            "data": [],
            "count": 0,
            "as_of": datetime.now(timezone.utc).isoformat(),
            "days_threshold": days,
            "store_id": active_store,
        }

    # 1. Build per-product sales summary (last_sold_at + total units sold)
    # from the orders collection using a single aggregation.
    sales_map = {}
    try:
        orders_coll = db.get_collection("orders")
        pipeline = [
            {"$match": {
                "store_id": active_store,
                "status": {"$nin": ["CANCELLED", "DRAFT"]},
            }},
            {"$unwind": "$items"},
            {"$group": {
                "_id": "$items.product_id",
                "last_sold_at": {"$max": "$created_at"},
                "total_sold": {"$sum": {"$ifNull": ["$items.quantity", 1]}},
            }},
        ]
        for doc in orders_coll.aggregate(pipeline):
            pid = doc.get("_id")
            if pid:
                sales_map[str(pid)] = {
                    "last_sold_at": doc.get("last_sold_at"),
                    "total_sold": doc.get("total_sold") or 0,
                }
    except Exception:
        # If aggregation fails (e.g., no orders collection yet), treat
        # sales_map as empty and every product falls into "never_sold".
        sales_map = {}

    # 2. Walk active products, classify each as "stale" or not.
    try:
        products_coll = db.get_collection("products")
        products = list(products_coll.find({"is_active": True}))
    except Exception:
        products = []

    now = datetime.now(timezone.utc)
    results = []
    for p in products:
        pid = str(p.get("product_id") or p.get("_id") or "")
        s = sales_map.get(pid, {})
        last_sold_at = s.get("last_sold_at")
        total_sold = s.get("total_sold", 0)

        days_since = None
        never_sold = last_sold_at is None
        if last_sold_at is not None:
            try:
                # Mongo may return a datetime or an ISO string depending on
                # how the order was inserted. Handle both.
                if isinstance(last_sold_at, datetime):
                    last_dt = last_sold_at
                else:
                    last_dt = datetime.fromisoformat(str(last_sold_at).replace("Z", "+00:00"))
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                days_since = (now - last_dt).days
            except (ValueError, TypeError):
                days_since = None
                never_sold = True

        if never_sold or (days_since is not None and days_since >= days):
            results.append({
                "product_id": pid or None,
                "sku": p.get("sku"),
                "brand": p.get("brand"),
                "model": p.get("model"),
                "category": p.get("category"),
                "mrp": p.get("mrp") or 0,
                "last_sold_at": last_sold_at if isinstance(last_sold_at, str) else (
                    last_sold_at.isoformat() if isinstance(last_sold_at, datetime) else None
                ),
                "days_since_sold": days_since,
                "never_sold": never_sold,
                "total_sold_all_time": total_sold,
            })

    # 3. Sort — never-sold first (infinite staleness), then by days desc.
    results.sort(key=lambda r: (
        0 if r["never_sold"] else 1,
        -(r["days_since_sold"] or 0),
    ))
    results = results[:limit]

    return {
        "data": results,
        "count": len(results),
        "as_of": now.isoformat(),
        "days_threshold": days,
        "store_id": active_store,
    }

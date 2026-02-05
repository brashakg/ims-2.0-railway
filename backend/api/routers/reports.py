"""
IMS 2.0 - Reports Router
=========================
Real database queries for dashboard and reports
"""
from fastapi import APIRouter, Depends, Query
from typing import Optional
from datetime import date, datetime, timedelta
from .auth import get_current_user
from ..dependencies import (
    get_order_repository,
    get_stock_repository,
    get_customer_repository,
    get_task_repository,
    get_attendance_repository,
)

router = APIRouter()


# ============================================================================
# DASHBOARD STATS (used by Dashboard page)
# ============================================================================

@router.get("/dashboard")
async def get_dashboard_stats(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user)
):
    """Get dashboard statistics for the main dashboard page"""
    active_store = store_id or current_user.get("active_store_id")

    order_repo = get_order_repository()
    stock_repo = get_stock_repository()
    task_repo = get_task_repository()

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday = today - timedelta(days=1)

    # Initialize response with defaults
    stats = {
        "total_sales": 0,
        "sales_change": 0,
        "pending_orders": 0,
        "urgent_orders": 0,
        "appointments_today": 0,
        "upcoming_appointments": 0,
        "low_stock_items": 0,
        "today_orders": 0,
        "today_deliveries": 0,
        "new_customers_today": 0,
    }

    if order_repo:
        # Get today's sales
        today_orders = order_repo.find_many({
            "store_id": active_store,
            "created_at": {"$gte": today.isoformat()},
            "status": {"$nin": ["CANCELLED", "DRAFT"]}
        })

        stats["total_sales"] = sum(
            o.get("final_amount", 0) or o.get("total_amount", 0)
            for o in today_orders
        )
        stats["today_orders"] = len(today_orders)

        # Yesterday's sales for comparison
        yesterday_orders = order_repo.find_many({
            "store_id": active_store,
            "created_at": {
                "$gte": yesterday.isoformat(),
                "$lt": today.isoformat()
            },
            "status": {"$nin": ["CANCELLED", "DRAFT"]}
        })
        yesterday_sales = sum(
            o.get("final_amount", 0) or o.get("total_amount", 0)
            for o in yesterday_orders
        )

        if yesterday_sales > 0:
            stats["sales_change"] = round(
                ((stats["total_sales"] - yesterday_sales) / yesterday_sales) * 100, 1
            )

        # Pending orders
        pending = order_repo.find_many({
            "store_id": active_store,
            "status": {"$in": ["CONFIRMED", "PROCESSING", "READY"]}
        })
        stats["pending_orders"] = len(pending)

        # Urgent (overdue) orders - orders that should have been delivered
        urgent = [o for o in pending if o.get("delivery_date") and
                  datetime.fromisoformat(o["delivery_date"].replace("Z", "")) < datetime.now()]
        stats["urgent_orders"] = len(urgent)

        # Today's deliveries
        delivered_today = order_repo.find_many({
            "store_id": active_store,
            "status": "DELIVERED",
            "delivered_at": {"$gte": today.isoformat()}
        })
        stats["today_deliveries"] = len(delivered_today)

    if stock_repo:
        # Low stock items
        low_stock = stock_repo.find_low_stock(active_store, threshold=5)
        stats["low_stock_items"] = len(low_stock) if low_stock else 0

    if task_repo:
        # Get today's tasks count
        today_tasks = task_repo.find_many({
            "store_id": active_store,
            "due_at": {"$gte": today.isoformat(), "$lt": (today + timedelta(days=1)).isoformat()}
        })
        stats["appointments_today"] = len(today_tasks) if today_tasks else 0

    return stats


# ============================================================================
# SALES REPORTS
# ============================================================================

@router.get("/sales/summary")
async def sales_summary(
    store_id: Optional[str] = Query(None),
    from_date: date = Query(...),
    to_date: date = Query(...),
    current_user: dict = Depends(get_current_user)
):
    """Get sales summary for date range"""
    active_store = store_id or current_user.get("active_store_id")
    order_repo = get_order_repository()

    if not order_repo:
        return {"summary": {
            "total_sales": 0,
            "total_orders": 0,
            "avg_order_value": 0,
            "total_tax": 0,
            "total_discount": 0,
        }}

    # Get orders in date range
    from_dt = datetime.combine(from_date, datetime.min.time())
    to_dt = datetime.combine(to_date, datetime.max.time())

    orders = order_repo.find_many({
        "store_id": active_store,
        "created_at": {"$gte": from_dt.isoformat(), "$lte": to_dt.isoformat()},
        "status": {"$nin": ["CANCELLED", "DRAFT"]}
    })

    total_sales = sum(o.get("final_amount", 0) or o.get("total_amount", 0) for o in orders)
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
    current_user: dict = Depends(get_current_user)
):
    """Get daily sales data for chart"""
    active_store = store_id or current_user.get("active_store_id")
    order_repo = get_order_repository()

    if not order_repo:
        return {"data": []}

    # Get orders for last N days
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)

    orders = order_repo.find_many({
        "store_id": active_store,
        "created_at": {"$gte": start_date.isoformat()},
        "status": {"$nin": ["CANCELLED", "DRAFT"]}
    })

    # Group by date
    daily_data = {}
    for order in orders:
        order_date = order.get("created_at", "")[:10]  # Get just the date part
        if order_date:
            if order_date not in daily_data:
                daily_data[order_date] = {"date": order_date, "sales": 0, "orders": 0}
            daily_data[order_date]["sales"] += order.get("final_amount", 0) or order.get("total_amount", 0)
            daily_data[order_date]["orders"] += 1

    # Convert to sorted list
    data = sorted(daily_data.values(), key=lambda x: x["date"])

    return {"data": data}


@router.get("/sales/by-salesperson")
async def sales_by_salesperson(
    store_id: Optional[str] = Query(None),
    from_date: date = Query(...),
    to_date: date = Query(...),
    current_user: dict = Depends(get_current_user)
):
    """Get sales grouped by salesperson"""
    active_store = store_id or current_user.get("active_store_id")
    order_repo = get_order_repository()

    if not order_repo:
        return {"data": []}

    from_dt = datetime.combine(from_date, datetime.min.time())
    to_dt = datetime.combine(to_date, datetime.max.time())

    orders = order_repo.find_many({
        "store_id": active_store,
        "created_at": {"$gte": from_dt.isoformat(), "$lte": to_dt.isoformat()},
        "status": {"$nin": ["CANCELLED", "DRAFT"]}
    })

    # Group by salesperson
    by_person = {}
    for order in orders:
        person = order.get("sales_person_id") or order.get("created_by") or "Unknown"
        person_name = order.get("sales_person_name", person)
        if person not in by_person:
            by_person[person] = {"id": person, "name": person_name, "sales": 0, "orders": 0}
        by_person[person]["sales"] += order.get("final_amount", 0) or order.get("total_amount", 0)
        by_person[person]["orders"] += 1

    return {"data": list(by_person.values())}


@router.get("/sales/by-category")
async def sales_by_category(
    store_id: Optional[str] = Query(None),
    from_date: date = Query(...),
    to_date: date = Query(...),
    current_user: dict = Depends(get_current_user)
):
    """Get sales grouped by product category"""
    active_store = store_id or current_user.get("active_store_id")
    order_repo = get_order_repository()

    if not order_repo:
        return {"data": []}

    from_dt = datetime.combine(from_date, datetime.min.time())
    to_dt = datetime.combine(to_date, datetime.max.time())

    orders = order_repo.find_many({
        "store_id": active_store,
        "created_at": {"$gte": from_dt.isoformat(), "$lte": to_dt.isoformat()},
        "status": {"$nin": ["CANCELLED", "DRAFT"]}
    })

    # Aggregate by category from order items
    by_category = {}
    for order in orders:
        for item in order.get("items", []):
            category = item.get("category", "Other")
            if category not in by_category:
                by_category[category] = {"category": category, "sales": 0, "quantity": 0}
            by_category[category]["sales"] += item.get("total", 0) or (item.get("price", 0) * item.get("quantity", 1))
            by_category[category]["quantity"] += item.get("quantity", 1)

    return {"data": list(by_category.values())}


# ============================================================================
# INVENTORY REPORTS
# ============================================================================

@router.get("/inventory/summary")
async def inventory_summary(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user)
):
    """Get inventory summary"""
    active_store = store_id or current_user.get("active_store_id")
    stock_repo = get_stock_repository()

    if not stock_repo:
        return {"summary": {
            "total_items": 0,
            "total_quantity": 0,
            "total_value": 0,
            "low_stock_count": 0,
            "out_of_stock_count": 0,
        }}

    # Get all stock
    all_stock = stock_repo.find_many({"store_id": active_store})
    low_stock = stock_repo.find_low_stock(active_store, threshold=5)

    total_value = sum(
        (s.get("quantity", 0) * s.get("cost_price", 0))
        for s in all_stock
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
    current_user: dict = Depends(get_current_user)
):
    """Get inventory valuation by category"""
    active_store = store_id or current_user.get("active_store_id")
    stock_repo = get_stock_repository()

    if not stock_repo:
        return {"valuation": {"by_category": [], "total": 0}}

    all_stock = stock_repo.find_many({"store_id": active_store})

    # Group by category
    by_category = {}
    for item in all_stock:
        category = item.get("category", "Other")
        if category not in by_category:
            by_category[category] = {"category": category, "quantity": 0, "value": 0}
        by_category[category]["quantity"] += item.get("quantity", 0)
        by_category[category]["value"] += item.get("quantity", 0) * item.get("cost_price", 0)

    total = sum(c["value"] for c in by_category.values())

    return {
        "valuation": {
            "by_category": list(by_category.values()),
            "total": round(total, 2)
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
    current_user: dict = Depends(get_current_user)
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
    current_user: dict = Depends(get_current_user)
):
    """Get attendance report for month"""
    active_store = store_id or current_user.get("active_store_id")
    attendance_repo = get_attendance_repository()

    if not attendance_repo:
        return {"data": [], "summary": {"total_present": 0, "total_absent": 0}}

    # Get attendance for month
    start_date = datetime(year, month, 1)
    if month == 12:
        end_date = datetime(year + 1, 1, 1)
    else:
        end_date = datetime(year, month + 1, 1)

    records = attendance_repo.find_many({
        "store_id": active_store,
        "date": {"$gte": start_date.isoformat()[:10], "$lt": end_date.isoformat()[:10]}
    })

    return {
        "data": records,
        "summary": {
            "total_present": len([r for r in records if r.get("status") == "PRESENT"]),
            "total_absent": len([r for r in records if r.get("status") == "ABSENT"]),
        }
    }


# ============================================================================
# FINANCE REPORTS
# ============================================================================

@router.get("/finance/outstanding")
async def outstanding_report(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user)
):
    """Get outstanding payments report"""
    active_store = store_id or current_user.get("active_store_id")
    order_repo = get_order_repository()

    if not order_repo:
        return {"data": [], "total_outstanding": 0}

    # Get orders with balance due
    orders = order_repo.find_many({
        "store_id": active_store,
        "balance_due": {"$gt": 0},
        "status": {"$nin": ["CANCELLED", "DRAFT"]}
    })

    outstanding_data = []
    total = 0
    for order in orders:
        balance = order.get("balance_due", 0)
        if balance > 0:
            total += balance
            outstanding_data.append({
                "order_id": order.get("order_id"),
                "order_number": order.get("order_number"),
                "customer_name": order.get("customer_name"),
                "customer_phone": order.get("customer_phone"),
                "total_amount": order.get("final_amount", 0),
                "paid_amount": order.get("paid_amount", 0),
                "balance_due": balance,
                "created_at": order.get("created_at"),
            })

    return {"data": outstanding_data, "total_outstanding": total}


@router.get("/finance/gst")
async def gst_report(
    from_date: date = Query(...),
    to_date: date = Query(...),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user)
):
    """Get GST report for date range"""
    active_store = store_id or current_user.get("active_store_id")
    order_repo = get_order_repository()

    if not order_repo:
        return {"data": [], "summary": {"total_cgst": 0, "total_sgst": 0, "total_igst": 0}}

    from_dt = datetime.combine(from_date, datetime.min.time())
    to_dt = datetime.combine(to_date, datetime.max.time())

    orders = order_repo.find_many({
        "store_id": active_store,
        "created_at": {"$gte": from_dt.isoformat(), "$lte": to_dt.isoformat()},
        "status": {"$nin": ["CANCELLED", "DRAFT"]}
    })

    total_cgst = sum(o.get("cgst_amount", 0) for o in orders)
    total_sgst = sum(o.get("sgst_amount", 0) for o in orders)
    total_igst = sum(o.get("igst_amount", 0) for o in orders)

    return {
        "data": [{
            "order_number": o.get("order_number"),
            "date": o.get("created_at", "")[:10],
            "taxable_amount": o.get("taxable_amount", 0),
            "cgst": o.get("cgst_amount", 0),
            "sgst": o.get("sgst_amount", 0),
            "igst": o.get("igst_amount", 0),
            "total": o.get("final_amount", 0),
        } for o in orders],
        "summary": {
            "total_cgst": total_cgst,
            "total_sgst": total_sgst,
            "total_igst": total_igst,
            "total_tax": total_cgst + total_sgst + total_igst,
        }
    }


# ============================================================================
# TASK REPORTS
# ============================================================================

@router.get("/tasks/summary")
async def task_summary(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user)
):
    """Get task summary"""
    active_store = store_id or current_user.get("active_store_id")
    task_repo = get_task_repository()

    if not task_repo:
        return {"summary": {
            "open": 0,
            "in_progress": 0,
            "completed": 0,
            "overdue": 0,
        }}

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

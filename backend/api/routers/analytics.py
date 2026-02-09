"""
Enterprise Analytics Router
Provides comprehensive analytics and business intelligence endpoints
"""

from fastapi import APIRouter, Depends, Query, HTTPException
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from .auth import get_current_user
from ..dependencies import (
    get_order_repository,
    get_stock_repository,
    get_customer_repository,
    get_task_repository,
)

router = APIRouter(prefix="", tags=["Analytics"])

# ============================================================================
# Types
# ============================================================================


class KPIData:
    """Key Performance Indicator Data"""

    pass


# ============================================================================
# Helper Functions
# ============================================================================


def get_date_range(period: str) -> tuple[datetime, datetime]:
    """Get start and end date for a given period"""
    end_date = datetime.now()

    if period == "today":
        start_date = end_date.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        start_date = end_date - timedelta(days=7)
    elif period == "month":
        start_date = end_date - timedelta(days=30)
    elif period == "quarter":
        start_date = end_date - timedelta(days=90)
    elif period == "year":
        start_date = end_date - timedelta(days=365)
    else:
        start_date = end_date - timedelta(days=30)  # Default to month

    return start_date, end_date


def calculate_metrics_for_period(
    order_repo, store_id: Optional[str], start_date: datetime, end_date: datetime
) -> Dict[str, Any]:
    """Calculate all metrics for a given period"""

    if order_repo is None:
        return {
            "total_revenue": 0.0,
            "total_orders": 0,
            "avg_order_value": 0.0,
            "revenue_change": 0.0,
            "period_start": start_date.isoformat(),
            "period_end": end_date.isoformat(),
        }

    # Get all orders and filter in memory
    all_orders = order_repo.find_by_store(store_id) if store_id else []

    # Filter by date range
    orders = [
        o for o in all_orders
        if start_date <= datetime.fromisoformat(o.get("created_at", "").replace("Z", "+00:00")) <= end_date
    ]

    # Calculate metrics
    total_revenue = sum(float(o.get("total_amount", 0) or 0) for o in orders)
    total_orders = len(orders)
    avg_order_value = total_revenue / total_orders if total_orders > 0 else 0

    # Calculate previous period for comparison
    prev_start = start_date - (end_date - start_date)
    prev_end = start_date

    prev_orders = [
        o for o in all_orders
        if prev_start <= datetime.fromisoformat(o.get("created_at", "").replace("Z", "+00:00")) <= prev_end
    ]

    prev_revenue = sum(float(o.get("total_amount", 0) or 0) for o in prev_orders)

    revenue_change = (
        ((total_revenue - prev_revenue) / prev_revenue * 100) if prev_revenue > 0 else 0
    )

    return {
        "total_revenue": float(total_revenue),
        "total_orders": total_orders,
        "avg_order_value": float(avg_order_value),
        "revenue_change": float(revenue_change),
        "period_start": start_date.isoformat(),
        "period_end": end_date.isoformat(),
    }


# ============================================================================
# Endpoints
# ============================================================================


@router.get("/dashboard-summary")
async def get_dashboard_summary(
    current_user: dict = Depends(get_current_user),
    period: str = Query("month", regex="^(today|week|month|quarter|year)$"),
):
    """
    Get comprehensive dashboard summary with all KPI data
    Returns: Revenue, Orders, Margin, Inventory, Customer metrics
    """
    try:
        start_date, end_date = get_date_range(period)
        store_id = current_user.get("active_store_id") or "store-001"

        order_repo = get_order_repository()
        stock_repo = get_stock_repository()
        customer_repo = get_customer_repository()

        metrics = calculate_metrics_for_period(order_repo, store_id, start_date, end_date)

        # Get inventory metrics
        inventory = stock_repo.find_by_store(store_id) if stock_repo is not None else []

        total_inventory_value = sum(
            (i.get("quantity", 0) or 0) * (i.get("unit_price", 0) or 0) for i in inventory
        )
        low_stock_items = len([i for i in inventory if (i.get("quantity", 0) or 0) <= (i.get("reorder_point", 0) or 0)])
        out_of_stock = len([i for i in inventory if (i.get("quantity", 0) or 0) == 0])

        # Get customer metrics
        customers = customer_repo.find_many({"store_id": store_id}) if customer_repo is not None else []

        new_customers = len([
            c for c in customers
            if c.get("created_at", "")[:10] >= start_date.date().isoformat()
        ])

        return {
            "period": period,
            "timestamp": datetime.now().isoformat(),
            # Revenue metrics
            "total_revenue": metrics["total_revenue"],
            "revenue_change": metrics["revenue_change"],
            "avg_order_value": metrics["avg_order_value"],
            "total_orders": metrics["total_orders"],
            # Gross margin (placeholder - would be calculated from actual order line items)
            "gross_margin_percent": 40.5,
            "margin_target": 42,
            # Inventory metrics
            "inventory_value": float(total_inventory_value),
            "low_stock_items": low_stock_items,
            "out_of_stock_items": out_of_stock,
            "inventory_turnover_ratio": 8.5,  # Would be calculated from COGS and avg inventory
            # Customer metrics
            "total_customers": len(customers),
            "new_customers": new_customers,
            "customer_acquisition_rate": new_customers,
            # Optical-specific metrics
            "prescription_renewals_pending": 32,  # Placeholder
            # Performance indicators
            "stores_count": 1,
        }

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error calculating dashboard summary: {str(e)}"
        )


@router.get("/revenue-trends")
async def get_revenue_trends(
    current_user: dict = Depends(get_current_user),
    period: str = Query("daily", regex="^(daily|weekly|monthly)$"),
    days: int = Query(30, ge=7, le=365),
):
    """
    Get revenue trend data for charting
    Returns: Time-series revenue data with YoY comparison
    """
    try:
        store_id = current_user.get("active_store_id") or "store-001"
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        order_repo = get_order_repository()
        if order_repo is None:
            return {"period": period, "days": days, "data": []}

        all_orders = order_repo.find_by_store(store_id)

        # Get orders for the period
        current_period = [
            o for o in all_orders
            if start_date <= datetime.fromisoformat(o.get("created_at", "").replace("Z", "+00:00")) <= end_date
        ]

        # Get previous period for YoY
        prev_start = start_date - timedelta(days=days)
        prev_end = start_date

        previous_period = [
            o for o in all_orders
            if prev_start <= datetime.fromisoformat(o.get("created_at", "").replace("Z", "+00:00")) <= prev_end
        ]

        # Group by period
        def group_by_period(orders: List[Dict[str, Any]], period_type: str):
            grouped = {}
            for order in orders:
                created_at = datetime.fromisoformat(order.get("created_at", "").replace("Z", "+00:00"))
                if period_type == "daily":
                    key = created_at.date().isoformat()
                elif period_type == "weekly":
                    week_start = created_at - timedelta(days=created_at.weekday())
                    key = week_start.date().isoformat()
                else:  # monthly
                    key = created_at.strftime("%Y-%m")

                if key not in grouped:
                    grouped[key] = 0
                grouped[key] += float(order.get("total_amount", 0) or 0)

            return grouped

        current_grouped = group_by_period(current_period, period)
        previous_grouped = group_by_period(previous_period, period)

        # Build timeline
        timeline_data = []
        current_date = start_date

        while current_date <= end_date:
            if period == "daily":
                key = current_date.date().isoformat()
                next_date = current_date + timedelta(days=1)
            elif period == "weekly":
                week_start = current_date - timedelta(days=current_date.weekday())
                key = week_start.date().isoformat()
                next_date = current_date + timedelta(days=7)
            else:  # monthly
                key = current_date.strftime("%Y-%m")
                if current_date.month == 12:
                    next_date = current_date.replace(
                        year=current_date.year + 1, month=1
                    )
                else:
                    next_date = current_date.replace(month=current_date.month + 1)

            timeline_data.append(
                {
                    "label": key,
                    "value": float(current_grouped.get(key, 0)),
                    "previous_value": float(previous_grouped.get(key, 0)),
                }
            )

            current_date = next_date

        return {
            "period": period,
            "days": days,
            "current_period_start": start_date.isoformat(),
            "current_period_end": end_date.isoformat(),
            "comparison_period_start": prev_start.isoformat(),
            "comparison_period_end": prev_end.isoformat(),
            "data": timeline_data,
        }

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error fetching revenue trends: {str(e)}"
        )


@router.get("/store-performance")
async def get_store_performance(
    current_user: dict = Depends(get_current_user),
    period: str = Query("month", regex="^(today|week|month|quarter|year)$"),
):
    """
    Get performance metrics for all stores (for multi-location admins)
    Returns: Store-wise revenue, orders, margins, staff, inventory metrics
    """
    try:
        start_date, end_date = get_date_range(period)

        order_repo = get_order_repository()
        stock_repo = get_stock_repository()

        # Get all orders
        all_orders = order_repo.find_many({}) if order_repo is not None else []

        # Group by store
        stores = {}
        for order in all_orders:
            store_id = order.get("store_id", "store-001")
            if store_id not in stores:
                stores[store_id] = {"store_id": store_id, "store_name": f"Store {store_id}"}

        store_metrics = []

        for store_id, store_info in stores.items():
            # Orders for this store
            orders = [
                o for o in all_orders
                if o.get("store_id") == store_id and
                start_date <= datetime.fromisoformat(o.get("created_at", "").replace("Z", "+00:00")) <= end_date
            ]

            revenue = sum(float(o.get("total_amount", 0) or 0) for o in orders)
            order_count = len(orders)
            avg_order_value = revenue / order_count if order_count > 0 else 0

            # Previous period comparison
            prev_start = start_date - (end_date - start_date)
            prev_orders = [
                o for o in all_orders
                if o.get("store_id") == store_id and
                prev_start <= datetime.fromisoformat(o.get("created_at", "").replace("Z", "+00:00")) <= start_date
            ]

            prev_revenue = sum(float(o.get("total_amount", 0) or 0) for o in prev_orders)
            revenue_change = (
                ((revenue - prev_revenue) / prev_revenue * 100)
                if prev_revenue > 0
                else 0
            )

            # Inventory metrics
            inventory = stock_repo.find_by_store(store_id) if stock_repo is not None else []

            stock_value = sum(
                (i.get("quantity", 0) or 0) * (i.get("unit_price", 0) or 0) for i in inventory
            )

            # Staff count (placeholder - would come from actual staff table)
            staff_count = 10  # Placeholder

            # Metrics calculation
            store_metrics.append(
                {
                    "store_id": store_id,
                    "store_name": store_info.get("store_name", store_id),
                    "revenue": float(revenue),
                    "orders": order_count,
                    "avg_order_value": float(avg_order_value),
                    "margin_percent": 40.5,  # Placeholder
                    "stock_value": float(stock_value),
                    "staff_count": staff_count,
                    "revenue_per_sqft": (
                        float(revenue / 1000) if revenue > 0 else 0
                    ),  # Placeholder
                    "revenue_trend": float(revenue_change),
                }
            )

        return {
            "period": period,
            "timestamp": datetime.now().isoformat(),
            "stores": store_metrics,
            "summary": {
                "total_stores": len(stores),
                "total_revenue": sum(s["revenue"] for s in store_metrics),
                "avg_revenue_per_store": (
                    sum(s["revenue"] for s in store_metrics) / len(stores)
                    if stores
                    else 0
                ),
                "avg_margin": 40.5,  # Placeholder
            },
        }

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error fetching store performance: {str(e)}"
        )


@router.get("/inventory-intelligence")
async def get_inventory_intelligence(
    current_user: dict = Depends(get_current_user),
):
    """
    Get inventory intelligence: low stock, dead stock, fast-moving items
    """
    try:
        store_id = current_user.get("active_store_id") or "store-001"

        stock_repo = get_stock_repository()
        inventory = stock_repo.find_by_store(store_id) if stock_repo is not None else []

        # Categorize items
        low_stock = [
            i for i in inventory
            if (i.get("quantity", 0) or 0) <= (i.get("reorder_point", 0) or 0)
        ]
        dead_stock = [
            i for i in inventory
            if (i.get("quantity", 0) or 0) > (i.get("reorder_point", 0) or 0) * 2
        ]
        fast_moving = [
            i for i in inventory
            if (i.get("quantity", 0) or 0) < (i.get("reorder_point", 0) or 0) / 2
        ]

        return {
            "low_stock": {
                "count": len(low_stock),
                "items": [
                    {
                        "sku": i.get("sku", ""),
                        "name": i.get("name", ""),
                        "quantity": i.get("quantity", 0),
                        "reorder_point": i.get("reorder_point", 0),
                    }
                    for i in low_stock[:10]
                ],
                "total_value": sum(
                    (i.get("quantity", 0) or 0) * (i.get("unit_price", 0) or 0) for i in low_stock
                ),
            },
            "dead_stock": {
                "count": len(dead_stock),
                "items": [
                    {
                        "sku": i.get("sku", ""),
                        "name": i.get("name", ""),
                        "quantity": i.get("quantity", 0),
                        "value": (i.get("quantity", 0) or 0) * (i.get("unit_price", 0) or 0),
                    }
                    for i in dead_stock[:10]
                ],
                "total_value": sum(
                    (i.get("quantity", 0) or 0) * (i.get("unit_price", 0) or 0) for i in dead_stock
                ),
            },
            "fast_moving": {
                "count": len(fast_moving),
                "items": [
                    {
                        "sku": i.get("sku", ""),
                        "name": i.get("name", ""),
                        "quantity": i.get("quantity", 0),
                        "velocity": "high",
                    }
                    for i in fast_moving[:10]
                ],
            },
            "total_inventory": {
                "items": len(inventory),
                "value": sum(
                    (i.get("quantity", 0) or 0) * (i.get("unit_price", 0) or 0) for i in inventory
                ),
            },
        }

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error fetching inventory intelligence: {str(e)}"
        )


@router.get("/customer-insights")
async def get_customer_insights(
    current_user: dict = Depends(get_current_user),
    period: str = Query("month", regex="^(today|week|month|quarter|year)$"),
):
    """
    Get customer insights: composition, top customers, lifetime value
    """
    try:
        start_date, end_date = get_date_range(period)
        store_id = current_user.get("active_store_id") or "store-001"

        customer_repo = get_customer_repository()
        order_repo = get_order_repository()

        # Total customers
        all_customers = customer_repo.find_many({"store_id": store_id}) if customer_repo is not None else []

        # New customers this period
        new_customers = [
            c for c in all_customers
            if c.get("created_at", "")[:10] >= start_date.date().isoformat()
        ]
        returning_customers = [
            c for c in all_customers
            if c.get("created_at", "")[:10] < start_date.date().isoformat()
        ]

        # Top customers by spend
        orders = order_repo.find_by_store(store_id) if order_repo is not None else []

        customer_spend = {}
        for order in orders:
            customer_id = order.get("customer_id")
            if customer_id:
                if customer_id not in customer_spend:
                    customer_spend[customer_id] = {"spend": 0, "orders": 0}
                customer_spend[customer_id]["spend"] += float(order.get("total_amount", 0) or 0)
                customer_spend[customer_id]["orders"] += 1

        top_customers = sorted(
            [{"customer_id": cid, **data} for cid, data in customer_spend.items()],
            key=lambda x: x["spend"],
            reverse=True,
        )[:10]

        return {
            "period": period,
            "total_customers": len(all_customers),
            "new_customers": len(new_customers),
            "returning_customers": len(returning_customers),
            "retention_rate": (
                (len(returning_customers) / len(all_customers) * 100)
                if all_customers
                else 0
            ),
            "acquisition_rate": len(new_customers),
            "top_customers": top_customers,
            "avg_customer_lifetime_value": (
                sum(c["spend"] for c in top_customers) / len(top_customers)
                if top_customers
                else 0
            ),
        }

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error fetching customer insights: {str(e)}"
        )


# ============================================================================
# ENTERPRISE PHASE 6: COMPREHENSIVE KPI ENDPOINT
# ============================================================================


@router.get("/enterprise-kpis")
async def get_enterprise_kpis(
    current_user: dict = Depends(get_current_user),
    period: str = Query("today", regex="^(today|week|month|year)$"),
):
    """
    Comprehensive enterprise KPI dashboard with SAP/Power BI style metrics
    Returns: Revenue, margins, footfall, inventory, products, cash register, store comparison
    """
    try:
        store_id = current_user.get("active_store_id") or "store-001"
        start_date, end_date = get_date_range(period)

        # Get repositories
        order_repo = get_order_repository()
        stock_repo = get_stock_repository()
        customer_repo = get_customer_repository()

        if order_repo is None:
            raise HTTPException(status_code=500, detail="Database connection failed")

        # ===== REVENUE METRICS =====
        all_orders = order_repo.find_by_store(store_id)
        current_orders = [
            o for o in all_orders
            if start_date <= datetime.fromisoformat(o.get("created_at", "").replace("Z", "+00:00")) <= end_date
        ]

        total_revenue = sum(float(o.get("total_amount", 0) or 0) for o in current_orders)
        total_orders_count = len(current_orders)

        # Previous period for comparison
        prev_start = start_date - (end_date - start_date)
        prev_end = start_date
        prev_orders = [
            o for o in all_orders
            if prev_start <= datetime.fromisoformat(o.get("created_at", "").replace("Z", "+00:00")) <= prev_end
        ]
        prev_revenue = sum(float(o.get("total_amount", 0) or 0) for o in prev_orders)
        revenue_change = (((total_revenue - prev_revenue) / prev_revenue * 100) if prev_revenue > 0 else 0)

        # ===== MARGIN METRICS =====
        # Calculate from order line items
        total_cost = sum(
            float(item.get("quantity", 0) or 0) * float(item.get("cost_price", 0) or 0)
            for order in current_orders
            for item in order.get("items", [])
        )
        total_cogs = total_cost
        gross_profit = total_revenue - total_cogs
        gross_margin_percent = (gross_profit / total_revenue * 100) if total_revenue > 0 else 0

        # Net margin (assuming 10% operating expenses as placeholder)
        operating_expenses = total_revenue * 0.10
        net_profit = gross_profit - operating_expenses
        net_margin_percent = (net_profit / total_revenue * 100) if total_revenue > 0 else 0

        # ===== TRANSACTION METRICS =====
        avg_transaction_value = total_revenue / total_orders_count if total_orders_count > 0 else 0
        customer_footfall = len(set(o.get("customer_id") for o in current_orders if o.get("customer_id")))

        # ===== INVENTORY METRICS =====
        inventory = stock_repo.find_by_store(store_id) if stock_repo is not None else []

        # Calculate inventory turnover (COGS / Average Inventory Value)
        avg_inventory_value = sum(
            (i.get("quantity", 0) or 0) * (i.get("unit_price", 0) or 0) for i in inventory
        ) / 2  # Simplified average
        inventory_turnover = (total_cogs / avg_inventory_value) if avg_inventory_value > 0 else 0

        low_stock_count = len([i for i in inventory if (i.get("quantity", 0) or 0) <= (i.get("reorder_point", 0) or 0)])

        # ===== TOP 5 PRODUCTS =====
        product_sales = {}
        for order in current_orders:
            for item in order.get("items", []):
                product_id = item.get("product_id", "unknown")
                if product_id not in product_sales:
                    product_sales[product_id] = {
                        "name": item.get("product_name", "Unknown"),
                        "units": 0,
                        "revenue": 0.0,
                        "sku": item.get("sku", "")
                    }
                product_sales[product_id]["units"] += int(item.get("quantity", 0) or 0)
                product_sales[product_id]["revenue"] += float(item.get("total_amount", 0) or 0)

        top_products = sorted(
            [{"product_id": pid, **data} for pid, data in product_sales.items()],
            key=lambda x: x["revenue"],
            reverse=True
        )[:5]

        # ===== CASH REGISTER SUMMARY =====
        # Opening balance (from previous day closing or default 0)
        opening_balance = 5000.0  # Placeholder - should fetch from actual cash register
        sales_amount = total_revenue
        expenses_amount = 500.0  # Placeholder - should fetch actual expenses
        closing_balance = opening_balance + sales_amount - expenses_amount

        # ===== STORE COMPARISON (if applicable) =====
        all_store_orders = order_repo.find_many({})
        store_comparison = []
        stores_by_id = {}

        for order in all_store_orders:
            order_store_id = order.get("store_id", "store-001")
            if order_store_id not in stores_by_id:
                stores_by_id[order_store_id] = {"store_id": order_store_id, "revenue": 0.0, "orders": 0}
            order_date_str = order.get("created_at", "")[:10]
            current_date_str = end_date.date().isoformat()
            if order_date_str == current_date_str:  # Same day for today
                stores_by_id[order_store_id]["revenue"] += float(order.get("total_amount", 0) or 0)
                stores_by_id[order_store_id]["orders"] += 1

        store_comparison = sorted(
            list(stores_by_id.values()),
            key=lambda x: x["revenue"],
            reverse=True
        )[:5]

        return {
            "period": period,
            "timestamp": datetime.now().isoformat(),
            "store_id": store_id,

            # Revenue metrics
            "revenue": {
                "total": float(total_revenue),
                "change_percent": float(revenue_change),
                "avg_transaction_value": float(avg_transaction_value),
                "total_orders": total_orders_count,
            },

            # Margin metrics
            "margins": {
                "gross_margin_percent": float(gross_margin_percent),
                "net_margin_percent": float(net_margin_percent),
                "gross_profit": float(gross_profit),
                "net_profit": float(net_profit),
            },

            # Customer metrics
            "customers": {
                "footfall": customer_footfall,
                "avg_order_value": float(avg_transaction_value),
            },

            # Inventory metrics
            "inventory": {
                "turnover_ratio": float(inventory_turnover),
                "low_stock_items": low_stock_count,
                "total_items": len(inventory),
            },

            # Top products
            "top_products": top_products,

            # Cash register
            "cash_register": {
                "opening_balance": float(opening_balance),
                "sales": float(sales_amount),
                "expenses": float(expenses_amount),
                "closing_balance": float(closing_balance),
            },

            # Store comparison
            "store_comparison": store_comparison,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error fetching enterprise KPIs: {str(e)}"
        )

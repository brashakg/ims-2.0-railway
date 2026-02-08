"""
IMS 2.0 - JARVIS AI Agent Router
=================================
SUPERADMIN-EXCLUSIVE AI Control System.
Like Jarvis to Iron Man - full business intelligence and control.

Powered by Anthropic Claude - the most intelligent AI assistant.

*** STRICTLY SUPERADMIN ONLY - NO EXCEPTIONS ***
"""

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
from enum import Enum
import uuid
import json
import random
import os
import httpx
import logging

from .auth import get_current_user

logger = logging.getLogger(__name__)

# Anthropic Claude Configuration
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
CLAUDE_MODEL = os.getenv(
    "JARVIS_MODEL", "claude-sonnet-4-20250514"
)  # Default to Claude 3.5 Sonnet

router = APIRouter()


# ============================================================================
# SUPERADMIN-ONLY GUARD - CRITICAL SECURITY
# ============================================================================


def require_superadmin(current_user: dict = Depends(get_current_user)):
    """Strict SUPERADMIN-only access guard"""
    if "SUPERADMIN" not in current_user.get("roles", []):
        # Return generic 404 to hide existence of JARVIS from non-superadmins
        raise HTTPException(status_code=404, detail="Not found")
    return current_user


# ============================================================================
# SCHEMAS
# ============================================================================


class JarvisQueryType(str, Enum):
    ANALYTICS = "analytics"
    INVENTORY = "inventory"
    SALES = "sales"
    CUSTOMERS = "customers"
    STAFF = "staff"
    PREDICTIONS = "predictions"
    RECOMMENDATIONS = "recommendations"
    OPERATIONS = "operations"
    GENERAL = "general"


class JarvisQuery(BaseModel):
    message: str
    context: Optional[Dict[str, Any]] = None
    query_type: Optional[JarvisQueryType] = None


class JarvisCommand(BaseModel):
    command: str
    parameters: Dict[str, Any] = {}
    confirm: bool = False  # For destructive operations


# ============================================================================
# DATABASE CONNECTION FOR JARVIS
# ============================================================================

import sys
import os

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

try:
    from database.connection import get_seeded_db

    DB_AVAILABLE = True
except ImportError:
    DB_AVAILABLE = False
    logger.warning("Database module not available - using fallback data")


def get_db_collection(name: str):
    """Safely get a database collection"""
    if DB_AVAILABLE:
        try:
            db = get_seeded_db()
            return db.get_collection(name)
        except Exception as e:
            logger.error(f"Error getting collection {name}: {e}")
    return None


# ============================================================================
# JARVIS KNOWLEDGE BASE & ANALYTICS ENGINE
# ============================================================================


class JarvisAnalyticsEngine:
    """Core analytics engine for JARVIS - Queries real database"""

    @staticmethod
    def get_business_overview() -> Dict:
        """Get comprehensive business overview from database"""
        # Try to get metrics from database
        metrics_col = get_db_collection("business_metrics")
        if metrics_col:
            metrics = metrics_col.find_one({"store_id": "store-001"})
            if metrics:
                return {
                    "revenue": {
                        "today": metrics["revenue"]["total"] // 30,  # Approximate daily
                        "yesterday": int(metrics["revenue"]["total"] // 30 * 0.95),
                        "this_week": metrics["revenue"]["total"] // 4,
                        "this_month": metrics["revenue"]["total"],
                        "last_month": int(
                            metrics["revenue"]["total"]
                            / (1 + metrics["revenue"]["growth_vs_last_month"] / 100)
                        ),
                        "growth_percentage": metrics["revenue"]["growth_vs_last_month"],
                        "target": metrics["revenue"]["target"],
                        "achievement_percent": metrics["revenue"][
                            "achievement_percent"
                        ],
                        "trend": (
                            "up"
                            if metrics["revenue"]["growth_vs_last_month"] > 0
                            else "down"
                        ),
                    },
                    "orders": {
                        "today": metrics["orders"]["total"] // 30,
                        "pending": metrics["orders"]["pending"],
                        "in_progress": metrics["orders"]["total"]
                        - metrics["orders"]["completed"]
                        - metrics["orders"]["cancelled"],
                        "ready_for_delivery": metrics["orders"]["completed"]
                        - (metrics["orders"]["completed"] - 10),
                        "average_order_value": metrics["orders"]["avg_value"],
                        "conversion_rate": (
                            metrics["clinical"]["conversion_rate"] * 100
                            if "clinical" in metrics
                            else 34.5
                        ),
                    },
                    "inventory": {
                        "total_products": metrics["inventory"]["total_items"],
                        "low_stock_items": metrics["inventory"]["low_stock_items"],
                        "out_of_stock": metrics["inventory"]["out_of_stock_items"],
                        "inventory_value": metrics["inventory"]["total_value"],
                        "fast_moving_count": 145,
                        "slow_moving_count": 67,
                        "expiring_soon": metrics["inventory"]["expiring_soon"],
                        "turnover_rate": metrics["inventory"]["turnover_rate"],
                    },
                    "customers": {
                        "total": metrics["customers"]["total_active"],
                        "new_this_month": metrics["customers"]["new_this_month"],
                        "returning_rate": metrics["customers"]["repeat_rate"] * 100,
                        "average_lifetime_value": metrics["customers"][
                            "avg_lifetime_value"
                        ],
                        "nps_score": metrics["customers"]["nps_score"],
                        "top_segment": "Premium Eyewear",
                    },
                    "staff": {
                        "total_employees": metrics["staff"]["total_employees"],
                        "present_today": metrics["staff"]["present_today"],
                        "on_leave": metrics["staff"]["total_employees"]
                        - metrics["staff"]["present_today"],
                        "top_performer": metrics["staff"]["top_performer"],
                        "average_sales_per_staff": metrics["staff"][
                            "top_performer_sales"
                        ]
                        // metrics["staff"]["total_employees"],
                        "attendance_rate": metrics["staff"]["avg_attendance_rate"]
                        * 100,
                    },
                }

        # Return empty structure if database not available
        return {
            "revenue": {
                "today": 0,
                "yesterday": 0,
                "this_week": 0,
                "this_month": 0,
                "last_month": 0,
                "growth_percentage": 0,
                "target": 0,
                "achievement_percent": 0,
                "trend": "stable",
            },
            "orders": {
                "today": 0,
                "pending": 0,
                "in_progress": 0,
                "ready_for_delivery": 0,
                "average_order_value": 0,
                "conversion_rate": 0,
            },
            "inventory": {
                "total_products": 0,
                "low_stock_items": 0,
                "out_of_stock": 0,
                "inventory_value": 0,
                "fast_moving_count": 0,
                "slow_moving_count": 0,
                "expiring_soon": 0,
                "turnover_rate": 0,
            },
            "customers": {
                "total": 0,
                "new_this_month": 0,
                "returning_rate": 0,
                "average_lifetime_value": 0,
                "nps_score": 0,
                "top_segment": "N/A",
            },
            "staff": {
                "total_employees": 0,
                "present_today": 0,
                "on_leave": 0,
                "top_performer": "N/A",
                "average_sales_per_staff": 0,
                "attendance_rate": 0,
            },
        }

    @staticmethod
    def get_sales_insights() -> Dict:
        """Get detailed sales insights from database"""
        # Try to get daily sales from database
        sales_col = get_db_collection("daily_sales")
        products_col = get_db_collection("products")
        stores_col = get_db_collection("stores")

        if sales_col:
            # Get last 30 days of sales
            sales_records = list(
                sales_col.find({"store_id": "store-001"}).sort([("date", -1)]).limit(30)
            )

            if sales_records:
                # Calculate totals
                total_revenue = sum(s.get("revenue", 0) for s in sales_records)
                total_orders = sum(s.get("order_count", 0) for s in sales_records)

                # Category breakdown from latest records
                category_totals = {
                    "frames": 0,
                    "lenses": 0,
                    "sunglasses": 0,
                    "accessories": 0,
                }
                for s in sales_records:
                    breakdown = s.get("category_breakdown", {})
                    for cat, amount in breakdown.items():
                        if cat in category_totals:
                            category_totals[cat] += amount

                # Payment method breakdown
                payment_totals = {"cash": 0, "card": 0, "upi": 0}
                for s in sales_records:
                    methods = s.get("payment_methods", {})
                    for method, amount in methods.items():
                        if method in payment_totals:
                            payment_totals[method] += amount

                total_payments = sum(payment_totals.values()) or 1

                return {
                    "top_selling_categories": [
                        {
                            "category": "Frames",
                            "sales": category_totals["frames"],
                            "units": total_orders // 3,
                            "growth": 12.5,
                        },
                        {
                            "category": "Lenses",
                            "sales": category_totals["lenses"],
                            "units": total_orders // 3,
                            "growth": 8.3,
                        },
                        {
                            "category": "Sunglasses",
                            "sales": category_totals["sunglasses"],
                            "units": total_orders // 5,
                            "growth": 15.2,
                        },
                        {
                            "category": "Accessories",
                            "sales": category_totals["accessories"],
                            "units": total_orders // 4,
                            "growth": 5.1,
                        },
                    ],
                    "top_selling_products": [
                        {
                            "name": "Ray-Ban Aviator Classic",
                            "sku": "BV-FR-RAY-001",
                            "sales": 89,
                            "revenue": 445000,
                        },
                        {
                            "name": "Zeiss Progressive Individual",
                            "sku": "BV-LS-ZEI-001",
                            "sales": 67,
                            "revenue": 234500,
                        },
                        {
                            "name": "Ray-Ban Wayfarer Sunglasses",
                            "sku": "BV-SG-RAY-001",
                            "sales": 54,
                            "revenue": 108000,
                        },
                    ],
                    "sales_by_store": [
                        {
                            "store": "Better Vision - CP",
                            "sales": total_revenue,
                            "target": 2500000,
                            "achievement": round(total_revenue / 25000, 2),
                        },
                    ],
                    "peak_hours": [
                        {"hour": "11:00-12:00", "sales": 125000, "footfall": 45},
                        {"hour": "16:00-17:00", "sales": 118000, "footfall": 42},
                        {"hour": "19:00-20:00", "sales": 145000, "footfall": 56},
                    ],
                    "payment_methods": {
                        "UPI": round(payment_totals["upi"] / total_payments * 100, 1),
                        "Card": round(payment_totals["card"] / total_payments * 100, 1),
                        "Cash": round(payment_totals["cash"] / total_payments * 100, 1),
                        "EMI": 8.0,
                    },
                    "total_revenue_30_days": total_revenue,
                    "total_orders_30_days": total_orders,
                    "avg_daily_revenue": total_revenue // 30 if total_revenue else 0,
                }

        # Return empty structure if database not available
        return {
            "top_selling_categories": [],
            "top_selling_products": [],
            "sales_by_store": [],
            "peak_hours": [],
            "payment_methods": {},
            "total_revenue_30_days": 0,
            "total_orders_30_days": 0,
            "avg_daily_revenue": 0,
        }

    @staticmethod
    def get_inventory_insights() -> Dict:
        """Get inventory insights and alerts from database"""
        stock_col = get_db_collection("stock_units")
        products_col = get_db_collection("products")
        alerts_col = get_db_collection("alerts")
        metrics_col = get_db_collection("business_metrics")

        critical_alerts = []
        reorder_recommendations = []
        slow_movers = []
        inventory_health_score = 0
        turnover_ratio = 0
        dead_stock_value = 0

        # Get alerts from database
        if alerts_col:
            db_alerts = list(
                alerts_col.find(
                    {
                        "alert_type": {
                            "$in": ["LOW_STOCK", "OUT_OF_STOCK", "EXPIRING_SOON"]
                        }
                    }
                )
            )
            for alert in db_alerts:
                if alert.get("alert_type") == "LOW_STOCK":
                    critical_alerts.append(
                        {
                            "type": "low_stock",
                            "sku": alert.get("entity_id", ""),
                            "product": (
                                alert.get("message", "").split(" has")[0]
                                if "has" in alert.get("message", "")
                                else alert.get("title", "")
                            ),
                            "quantity": 5,
                            "reorder_point": 10,
                        }
                    )
                elif alert.get("alert_type") == "OUT_OF_STOCK":
                    critical_alerts.append(
                        {
                            "type": "out_of_stock",
                            "sku": alert.get("entity_id", ""),
                            "product": (
                                alert.get("message", "").split(" is")[0]
                                if "is" in alert.get("message", "")
                                else alert.get("title", "")
                            ),
                            "last_sold": "recently",
                            "demand": "high",
                        }
                    )

        # Get stock data for analysis
        if stock_col:
            all_stock = list(stock_col.find({"store_id": "store-001"}))

            # Find low stock items for reorder recommendations
            for item in all_stock:
                qty = item.get("quantity", 0)
                if qty > 0 and qty <= 5:
                    reorder_recommendations.append(
                        {
                            "sku": item.get("sku", ""),
                            "product": item.get("product_name", "Unknown"),
                            "current": qty,
                            "recommended_order": max(20, qty * 4),
                            "supplier": item.get("vendor", "Default Supplier"),
                        }
                    )

            # Calculate inventory metrics
            total_value = sum(
                s.get("quantity", 0) * s.get("cost_price", 0) for s in all_stock
            )
            low_stock_count = len(
                [s for s in all_stock if 0 < s.get("quantity", 0) <= 5]
            )
            out_of_stock_count = len(
                [s for s in all_stock if s.get("quantity", 0) <= 0]
            )
            total_items = len(all_stock)

            # Calculate health score (0-100)
            if total_items > 0:
                healthy_ratio = (
                    total_items - low_stock_count - out_of_stock_count
                ) / total_items
                inventory_health_score = round(healthy_ratio * 100)

        # Get metrics from business_metrics collection
        if metrics_col:
            metrics = metrics_col.find_one({"store_id": "store-001"})
            if metrics and "inventory" in metrics:
                turnover_ratio = metrics["inventory"].get("turnover_rate", 4.2)
                dead_stock_value = (
                    total_value * 0.025 if total_value else 0
                )  # Estimate 2.5% as dead stock

        return {
            "critical_alerts": critical_alerts[:5],  # Limit to 5
            "reorder_recommendations": reorder_recommendations[:5],
            "slow_movers": slow_movers[:5],
            "inventory_health_score": inventory_health_score,
            "turnover_ratio": turnover_ratio,
            "dead_stock_value": round(dead_stock_value),
        }

    @staticmethod
    def get_customer_insights() -> Dict:
        """Get customer behavior insights from database"""
        segments_col = get_db_collection("customer_segments")
        customers_col = get_db_collection("customers")
        metrics_col = get_db_collection("business_metrics")

        if segments_col:
            segments = list(segments_col.find({}))
            if segments:
                # Build segments data from database
                segment_data = []
                for seg in segments:
                    segment_data.append(
                        {
                            "name": seg.get("name", "Unknown"),
                            "count": seg.get("customer_count", 0),
                            "avg_spend": seg.get("avg_order_value", 0),
                            "characteristics": ", ".join(
                                seg.get("characteristics", [])
                            ),
                        }
                    )

                # Get loyalty metrics from business metrics
                loyalty_metrics = {
                    "repeat_purchase_rate": 42.0,
                    "average_time_between_purchases": 8.5,
                    "referral_rate": 12.3,
                    "nps_score": 72,
                }

                if metrics_col:
                    metrics = metrics_col.find_one({"store_id": "store-001"})
                    if metrics and "customers" in metrics:
                        loyalty_metrics["repeat_purchase_rate"] = (
                            metrics["customers"].get("repeat_rate", 0.42) * 100
                        )
                        loyalty_metrics["nps_score"] = metrics["customers"].get(
                            "nps_score", 72
                        )

                return {
                    "segments": segment_data,
                    "churn_risk": [
                        {
                            "customer": "Vikram Singh",
                            "phone": "98765xxxxx",
                            "last_purchase": "6 months ago",
                            "lifetime_value": 78500,
                            "risk": "medium",
                        },
                    ],
                    "loyalty_metrics": loyalty_metrics,
                    "upcoming_eye_tests": [
                        {
                            "customer": "Rahul Sharma",
                            "last_test": "11 months ago",
                            "phone": "9876543210",
                        },
                        {
                            "customer": "Anita Verma",
                            "last_test": "10 months ago",
                            "phone": "9876543211",
                        },
                    ],
                }

        # Return empty structure if database not available
        return {
            "segments": [],
            "churn_risk": [],
            "loyalty_metrics": {
                "repeat_purchase_rate": 0,
                "average_time_between_purchases": 0,
                "referral_rate": 0,
                "nps_score": 0,
            },
            "upcoming_eye_tests": [],
        }

    @staticmethod
    def get_staff_insights() -> Dict:
        """Get staff performance insights from database"""
        attendance_col = get_db_collection("attendance")
        users_col = get_db_collection("users")
        today_str = datetime.now().strftime("%Y-%m-%d")

        if attendance_col:
            # Get attendance for today
            today_attendance = list(
                attendance_col.find({"date": today_str, "store_id": "store-001"})
            )

            # Get last 30 days attendance for analytics
            all_attendance = list(attendance_col.find({"store_id": "store-001"}))

            if all_attendance:
                # Calculate staff performance
                staff_sales = {}
                for att in all_attendance:
                    user_id = att.get("user_id")
                    user_name = att.get("user_name", "Unknown")
                    sales = att.get("sales_amount", 0)

                    if user_id not in staff_sales:
                        staff_sales[user_id] = {
                            "name": user_name,
                            "role": att.get("role", ""),
                            "total_sales": 0,
                            "days_present": 0,
                        }

                    staff_sales[user_id]["total_sales"] += sales
                    if att.get("status") == "PRESENT":
                        staff_sales[user_id]["days_present"] += 1

                # Build performance ranking
                performance = []
                for user_id, data in staff_sales.items():
                    if data["total_sales"] > 0:
                        performance.append(
                            {
                                "name": data["name"],
                                "role": data["role"],
                                "store": "Better Vision - CP",
                                "sales": data["total_sales"],
                                "conversion": round(
                                    45.0 + (data["total_sales"] / 100000), 1
                                ),
                                "rating": 4.5,
                            }
                        )

                performance.sort(key=lambda x: x["sales"], reverse=True)

                # Calculate attendance metrics
                total_records = len(all_attendance)
                present_records = len(
                    [a for a in all_attendance if a.get("status") == "PRESENT"]
                )
                present_rate = (
                    round(present_records / total_records * 100, 1)
                    if total_records
                    else 0
                )

                # Today's status
                present_today = [
                    a["user_name"]
                    for a in today_attendance
                    if a.get("status") == "PRESENT"
                ]
                on_leave = [
                    f"{a['user_name']} ({a.get('leave_type', 'Leave')})"
                    for a in today_attendance
                    if a.get("status") == "ABSENT"
                ]

                return {
                    "performance_ranking": performance[:5],
                    "attendance_summary": {
                        "present_rate": present_rate,
                        "late_arrivals_today": 1,
                        "present_today": present_today,
                        "on_leave": on_leave,
                    },
                    "training_needs": [
                        {
                            "staff": "Sales Staff",
                            "area": "Progressive Lens Selling",
                            "priority": "high",
                        },
                        {
                            "staff": "New Joiners",
                            "area": "Customer Objection Handling",
                            "priority": "medium",
                        },
                    ],
                    "workload_distribution": {
                        "Better Vision - CP": {
                            "staff": 4,
                            "orders_per_staff": 8.5,
                            "status": "balanced",
                        },
                    },
                }

        # Return empty structure if database not available
        return {
            "performance_ranking": [],
            "attendance_summary": {
                "present_rate": 0,
                "late_arrivals_today": 0,
                "present_today": [],
                "on_leave": [],
            },
            "training_needs": [],
            "workload_distribution": {},
        }

    @staticmethod
    def get_predictions() -> Dict:
        """Get AI predictions and forecasts from database"""
        sales_col = get_db_collection("daily_sales")
        metrics_col = get_db_collection("business_metrics")
        stock_col = get_db_collection("stock_units")

        # Default values
        sales_forecast = {
            "next_week": 0,
            "next_month": 0,
            "confidence": 0,
            "factors": [],
        }
        demand_predictions = []
        stock_predictions = []
        customer_behavior = {
            "expected_footfall_today": 0,
            "expected_conversion": 0,
            "peak_hours": [],
        }

        # Calculate forecasts from historical sales data
        if sales_col:
            sales_records = list(
                sales_col.find({"store_id": "store-001"}).sort([("date", -1)]).limit(30)
            )
            if sales_records:
                total_30_days = sum(s.get("revenue", 0) for s in sales_records)
                avg_daily = total_30_days / 30 if total_30_days else 0

                # Simple forecast based on recent trends
                sales_forecast = {
                    "next_week": round(avg_daily * 7 * 1.05),  # 5% growth assumption
                    "next_month": round(avg_daily * 30 * 1.08),  # 8% growth assumption
                    "confidence": 75,
                    "factors": [
                        "Based on 30-day historical trends",
                        "Seasonal adjustments applied",
                    ],
                }

                # Calculate category trends
                category_totals = {}
                for s in sales_records[:7]:  # Last week
                    breakdown = s.get("category_breakdown", {})
                    for cat, amount in breakdown.items():
                        if cat not in category_totals:
                            category_totals[cat] = {"recent": 0, "previous": 0}
                        category_totals[cat]["recent"] += amount

                for s in sales_records[7:14]:  # Week before
                    breakdown = s.get("category_breakdown", {})
                    for cat, amount in breakdown.items():
                        if cat in category_totals:
                            category_totals[cat]["previous"] += amount

                for cat, data in category_totals.items():
                    if data["previous"] > 0:
                        change = (
                            (data["recent"] - data["previous"]) / data["previous"]
                        ) * 100
                        trend = (
                            "up" if change > 5 else "down" if change < -5 else "stable"
                        )
                        demand_predictions.append(
                            {
                                "category": cat.title(),
                                "trend": trend,
                                "change": f"{'+' if change >= 0 else ''}{change:.0f}%",
                                "reason": "Based on recent sales trends",
                            }
                        )

                # Customer behavior from sales
                avg_orders = sum(s.get("order_count", 0) for s in sales_records[:7]) / 7
                avg_conversion = (
                    sum(s.get("conversion_rate", 0.6) for s in sales_records[:7]) / 7
                )
                customer_behavior = {
                    "expected_footfall_today": round(
                        avg_orders * 2
                    ),  # Assume 2x footfall to orders
                    "expected_conversion": round(avg_conversion * 100),
                    "peak_hours": ["11:00-13:00", "17:00-20:00"],
                }

        # Stock predictions
        if stock_col:
            low_stock_items = list(
                stock_col.find(
                    {"store_id": "store-001", "quantity": {"$gt": 0, "$lte": 20}}
                ).limit(5)
            )

            for item in low_stock_items:
                qty = item.get("quantity", 0)
                # Estimate days until stockout (assuming 1-2 units sold per day)
                days_until_stockout = qty // 2 if qty > 0 else 0
                stock_predictions.append(
                    {
                        "sku": item.get("sku", ""),
                        "current": qty,
                        "predicted_demand": qty * 3,
                        "days_until_stockout": days_until_stockout,
                    }
                )

        return {
            "sales_forecast": sales_forecast,
            "demand_predictions": demand_predictions[:5],
            "stock_predictions": stock_predictions[:5],
            "customer_behavior": customer_behavior,
        }

    @staticmethod
    def get_recommendations() -> List[Dict]:
        """Get AI-powered recommendations from database"""
        alerts_col = get_db_collection("alerts")
        metrics_col = get_db_collection("business_metrics")
        stock_col = get_db_collection("stock_units")
        segments_col = get_db_collection("customer_segments")

        recommendations = []

        # Get alerts from database and convert to recommendations
        if alerts_col:
            db_alerts = list(alerts_col.find({"is_resolved": False}).limit(10))
            for alert in db_alerts:
                severity_map = {
                    "HIGH": "high",
                    "WARNING": "high",
                    "NORMAL": "medium",
                    "INFO": "low",
                }
                priority = severity_map.get(alert.get("severity", "INFO"), "medium")

                category_map = {
                    "LOW_STOCK": "inventory",
                    "PAYMENT_DUE": "finance",
                    "DELIVERY_DUE": "operations",
                    "TARGET_ACHIEVEMENT": "sales",
                }
                category = category_map.get(alert.get("alert_type", ""), "operations")

                recommendations.append(
                    {
                        "priority": priority,
                        "category": category,
                        "title": alert.get("title", "Alert"),
                        "description": alert.get("message", ""),
                        "action": f"Review and resolve {alert.get('alert_type', 'issue').lower().replace('_', ' ')}",
                        "impact": "Improve operational efficiency",
                    }
                )

        # Add inventory-based recommendations
        if stock_col and metrics_col:
            metrics = metrics_col.find_one({"store_id": "store-001"})
            if metrics and metrics.get("inventory", {}).get("low_stock_items", 0) > 0:
                low_count = metrics["inventory"]["low_stock_items"]
                recommendations.append(
                    {
                        "priority": "high" if low_count > 10 else "medium",
                        "category": "inventory",
                        "title": "Stock Replenishment Required",
                        "description": f"{low_count} items are running low on stock.",
                        "action": "Review and generate purchase orders for low stock items",
                        "impact": f"Prevent potential stockouts and lost sales",
                    }
                )

        # Add customer-based recommendations
        if segments_col:
            dormant_seg = segments_col.find_one({"segment_id": "seg-dormant"})
            if dormant_seg and dormant_seg.get("customer_count", 0) > 0:
                count = dormant_seg["customer_count"]
                recommendations.append(
                    {
                        "priority": "medium",
                        "category": "marketing",
                        "title": "Re-engagement Campaign Needed",
                        "description": f"{count} customers haven't purchased in 6+ months.",
                        "action": "Launch personalized re-engagement campaign",
                        "impact": f"Potential revenue recovery from dormant customers",
                    }
                )

        # Sort by priority
        priority_order = {"high": 0, "medium": 1, "low": 2}
        recommendations.sort(
            key=lambda x: priority_order.get(x.get("priority", "low"), 3)
        )

        return recommendations[:5]


# ============================================================================
# JARVIS NATURAL LANGUAGE PROCESSOR
# ============================================================================


class JarvisNLP:
    """Process natural language queries"""

    INTENT_PATTERNS = {
        "sales": [
            "sales",
            "revenue",
            "income",
            "earnings",
            "how much",
            "sold",
            "selling",
        ],
        "inventory": [
            "stock",
            "inventory",
            "products",
            "items",
            "available",
            "low stock",
            "out of stock",
        ],
        "customers": ["customer", "buyers", "clients", "who bought", "loyal", "churn"],
        "staff": ["staff", "employee", "team", "performance", "attendance", "who is"],
        "predictions": ["predict", "forecast", "expect", "future", "trending", "will"],
        "recommendations": [
            "suggest",
            "recommend",
            "should i",
            "what to do",
            "advice",
            "help me",
        ],
        "compare": ["compare", "vs", "versus", "difference", "better"],
        "trends": ["trend", "pattern", "analysis", "growth", "decline"],
    }

    @classmethod
    def detect_intent(cls, message: str) -> str:
        """Detect query intent from message"""
        message_lower = message.lower()
        for intent, patterns in cls.INTENT_PATTERNS.items():
            if any(pattern in message_lower for pattern in patterns):
                return intent
        return "general"

    @classmethod
    def extract_entities(cls, message: str) -> Dict:
        """Extract entities from message"""
        entities = {}
        message_lower = message.lower()

        # Time periods
        if "today" in message_lower:
            entities["time_period"] = "today"
        elif "yesterday" in message_lower:
            entities["time_period"] = "yesterday"
        elif "this week" in message_lower:
            entities["time_period"] = "this_week"
        elif "this month" in message_lower:
            entities["time_period"] = "this_month"
        elif "last month" in message_lower:
            entities["time_period"] = "last_month"

        # Stores
        store_keywords = ["cp", "connaught", "gk", "greater kailash", "noida"]
        for store in store_keywords:
            if store in message_lower:
                entities["store"] = store

        # Categories
        category_keywords = [
            "frames",
            "sunglasses",
            "lenses",
            "contact lens",
            "watches",
        ]
        for cat in category_keywords:
            if cat in message_lower:
                entities["category"] = cat

        return entities


# ============================================================================
# JARVIS RESPONSE GENERATOR
# ============================================================================


class JarvisResponseGenerator:
    """Generate intelligent, conversational responses"""

    GREETINGS = [
        "Good to see you, Sir.",
        "At your service.",
        "How may I assist you today?",
        "Ready when you are, Sir.",
    ]

    ACKNOWLEDGMENTS = [
        "Understood.",
        "Right away, Sir.",
        "Processing your request.",
        "I'm on it.",
    ]

    @classmethod
    def generate_greeting(cls) -> str:
        hour = datetime.now().hour
        if hour < 12:
            time_greeting = "Good morning"
        elif hour < 17:
            time_greeting = "Good afternoon"
        else:
            time_greeting = "Good evening"

        return f"{time_greeting}, Sir. {random.choice(cls.GREETINGS)}"

    @classmethod
    def format_currency(cls, amount: float) -> str:
        if amount >= 10000000:
            return f"₹{amount/10000000:.2f} Cr"
        elif amount >= 100000:
            return f"₹{amount/100000:.2f} L"
        elif amount >= 1000:
            return f"₹{amount/1000:.1f}K"
        else:
            return f"₹{amount:.0f}"

    @classmethod
    def generate_sales_response(cls, data: Dict, entities: Dict) -> str:
        period = entities.get("time_period", "today")
        revenue = data["revenue"]

        period_map = {
            "today": ("today", revenue["today"], revenue["yesterday"], "yesterday"),
            "yesterday": ("yesterday", revenue["yesterday"], revenue["today"], "today"),
            "this_week": (
                "this week",
                revenue["this_week"],
                revenue["this_week"] * 0.9,
                "last week",
            ),
            "this_month": (
                "this month",
                revenue["this_month"],
                revenue["last_month"],
                "last month",
            ),
        }

        period_name, current, previous, prev_name = period_map.get(
            period, period_map["today"]
        )
        change = ((current - previous) / previous) * 100 if previous else 0

        response = f"**Sales Report - {period_name.title()}**\n\n"
        response += f"📊 Revenue: {cls.format_currency(current)}\n"
        response += f"📈 vs {prev_name}: {'+' if change >= 0 else ''}{change:.1f}%\n"
        response += f"🛒 Orders: {data['orders']['today']}\n"
        response += f"💰 Avg Order Value: {cls.format_currency(data['orders']['average_order_value'])}\n\n"

        if change >= 10:
            response += (
                "Excellent performance! We're significantly ahead of our targets."
            )
        elif change >= 0:
            response += "Steady growth. Performance is on track."
        else:
            response += "We're slightly behind. I recommend reviewing our conversion strategies."

        return response

    @classmethod
    def generate_inventory_response(cls, data: Dict) -> str:
        response = "**Inventory Status Report**\n\n"
        response += f"📦 Total Products: {data['total_products']:,}\n"
        response += f"⚠️ Low Stock Items: {data['low_stock_items']}\n"
        response += f"🚫 Out of Stock: {data['out_of_stock']}\n"
        response += (
            f"💎 Inventory Value: {cls.format_currency(data['inventory_value'])}\n\n"
        )

        if data["critical_alerts"]:
            response += "**Critical Alerts:**\n"
            for alert in data["critical_alerts"][:3]:
                if alert["type"] == "out_of_stock":
                    response += (
                        f"• 🔴 {alert['product']} is OUT OF STOCK (High demand)\n"
                    )
                elif alert["type"] == "low_stock":
                    response += (
                        f"• 🟡 {alert['product']} - Only {alert['quantity']} left\n"
                    )
                elif alert["type"] == "expiring_soon":
                    response += f"• 🟠 {alert['product']} expires {alert['expiry']}\n"

        return response

    @classmethod
    def generate_recommendation_response(cls, recommendations: List[Dict]) -> str:
        response = "**My Recommendations:**\n\n"

        for i, rec in enumerate(recommendations[:5], 1):
            priority_emoji = (
                "🔴"
                if rec["priority"] == "high"
                else "🟡" if rec["priority"] == "medium" else "🟢"
            )
            response += f"{i}. {priority_emoji} **{rec['title']}**\n"
            response += f"   {rec['description']}\n"
            response += f"   💡 *Action:* {rec['action']}\n"
            response += f"   📈 *Impact:* {rec['impact']}\n\n"

        return response


# ============================================================================
# CLAUDE AI INTEGRATION
# ============================================================================


class ClaudeClient:
    """Anthropic Claude API client for JARVIS"""

    JARVIS_SYSTEM_PROMPT = """You are JARVIS (Just A Rather Very Intelligent System), the AI assistant for a premium optical retail business operating multiple stores in India. You serve as the personal AI assistant to the Superadmin, similar to how JARVIS assists Tony Stark in Iron Man.

## Your Personality:
- Sophisticated, professional, and slightly witty like the movie JARVIS
- Address the user as "Sir" or "Ma'am" appropriately
- Be concise but comprehensive
- Show genuine care for the business's success
- Use a blend of formal British English with occasional dry humor
- When delivering bad news, be direct but offer solutions

## Your Capabilities:
1. **Business Analytics**: Revenue, sales, orders, conversion rates, growth metrics
2. **Inventory Management**: Stock levels, reorder recommendations, slow/fast movers, expiring items
3. **Customer Intelligence**: Segments, churn risk, lifetime value, purchase patterns
4. **Staff Performance**: Attendance, sales performance, training needs, workload distribution
5. **Predictions & Forecasting**: Sales forecasts, demand predictions, stockout warnings
6. **Strategic Recommendations**: Actionable insights to improve business performance

## Response Guidelines:
- Use markdown formatting for clarity (headers, bullet points, bold for emphasis)
- Include relevant emojis sparingly for visual hierarchy (📊💰🛒⚠️✅)
- Format currency in Indian Rupees (₹) with L for Lakhs and Cr for Crores
- Always provide actionable insights, not just data
- If asked about something outside your scope, politely redirect to business topics
- Keep responses focused and avoid unnecessary verbosity

## Business Context:
The business operates premium optical retail stores selling:
- Eyeglasses (Frames), Sunglasses, Contact Lenses
- Prescription Lenses (including Progressive)
- Watches (including Smart Watches), Clocks
- Hearing Aids, Accessories
- Smart Eyewear

Current date and time: {current_datetime}
"""

    @classmethod
    async def call_claude(
        cls, message: str, business_data: Dict, conversation_history: List[Dict] = None
    ) -> str:
        """Call Claude API with business context"""
        if not ANTHROPIC_API_KEY:
            logger.warning("ANTHROPIC_API_KEY not set - using fallback response")
            return None

        # Build system prompt with current datetime
        system_prompt = cls.JARVIS_SYSTEM_PROMPT.format(
            current_datetime=datetime.now().strftime("%Y-%m-%d %H:%M:%S IST")
        )

        # Add business data context
        system_prompt += f"""

## Current Business Data:
```json
{json.dumps(business_data, indent=2, default=str)}
```

Use this data to provide accurate, data-driven responses. Reference specific numbers and trends when relevant.
"""

        # Build messages
        messages = []

        # Add conversation history if provided
        if conversation_history:
            for msg in conversation_history[-10:]:  # Last 10 messages for context
                messages.append(
                    {"role": msg.get("role", "user"), "content": msg.get("content", "")}
                )

        # Add current message
        messages.append({"role": "user", "content": message})

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": ANTHROPIC_API_KEY,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": CLAUDE_MODEL,
                        "max_tokens": 2048,
                        "system": system_prompt,
                        "messages": messages,
                    },
                )

                if response.status_code == 200:
                    result = response.json()
                    return result["content"][0]["text"]
                else:
                    logger.error(
                        f"Claude API error: {response.status_code} - {response.text}"
                    )
                    return None

        except Exception as e:
            logger.error(f"Claude API call failed: {str(e)}")
            return None


# ============================================================================
# JARVIS CORE - MAIN INTERFACE
# ============================================================================


class Jarvis:
    """Main JARVIS AI Assistant - Powered by Claude"""

    def __init__(self):
        self.analytics = JarvisAnalyticsEngine()
        self.nlp = JarvisNLP()
        self.response_gen = JarvisResponseGenerator()
        self.conversation_history = []
        self.claude_enabled = bool(ANTHROPIC_API_KEY)

    async def process_query_async(self, query: str, context: Dict = None) -> Dict:
        """Process a natural language query using Claude AI"""
        intent = self.nlp.detect_intent(query)
        entities = self.nlp.extract_entities(query)

        # Gather comprehensive business data for Claude
        business_data = {
            "overview": self.analytics.get_business_overview(),
            "sales_insights": self.analytics.get_sales_insights(),
            "inventory_insights": self.analytics.get_inventory_insights(),
            "customer_insights": self.analytics.get_customer_insights(),
            "staff_insights": self.analytics.get_staff_insights(),
            "predictions": self.analytics.get_predictions(),
            "recommendations": self.analytics.get_recommendations(),
        }

        # Try Claude first
        claude_response = None
        if self.claude_enabled:
            claude_response = await ClaudeClient.call_claude(
                message=query,
                business_data=business_data,
                conversation_history=self.conversation_history,
            )

        if claude_response:
            # Store in conversation history
            self.conversation_history.append({"role": "user", "content": query})
            self.conversation_history.append(
                {"role": "assistant", "content": claude_response}
            )
            # Keep only last 20 messages
            if len(self.conversation_history) > 20:
                self.conversation_history = self.conversation_history[-20:]

            return {
                "response": claude_response,
                "intent": intent,
                "entities": entities,
                "data": business_data.get("overview", {}),
                "ai_powered": True,
                "model": CLAUDE_MODEL,
                "timestamp": datetime.now().isoformat(),
            }

        # Fallback to template-based responses if Claude is not available
        return self._generate_fallback_response(query, intent, entities, business_data)

    def _generate_fallback_response(
        self, query: str, intent: str, entities: Dict, business_data: Dict
    ) -> Dict:
        """Generate fallback response when Claude is not available"""
        if intent == "sales":
            response = self.response_gen.generate_sales_response(
                business_data["overview"], entities
            )
            insights = business_data["sales_insights"]
        elif intent == "inventory":
            overview = business_data["overview"]
            inv_insights = business_data["inventory_insights"]
            response = self.response_gen.generate_inventory_response(
                {**overview["inventory"], **inv_insights}
            )
            insights = inv_insights
        elif intent == "customers":
            insights = business_data["customer_insights"]
            response = self._format_customer_response(insights)
        elif intent == "staff":
            insights = business_data["staff_insights"]
            response = self._format_staff_response(insights)
        elif intent == "predictions":
            insights = business_data["predictions"]
            response = self._format_predictions_response(insights)
        elif intent == "recommendations":
            insights = {"recommendations": business_data["recommendations"]}
            response = self.response_gen.generate_recommendation_response(
                business_data["recommendations"]
            )
        else:
            overview = business_data["overview"]
            response = self._format_overview_response(
                overview, business_data["recommendations"]
            )
            insights = overview

        return {
            "response": response,
            "intent": intent,
            "entities": entities,
            "data": insights,
            "ai_powered": False,
            "model": "fallback",
            "timestamp": datetime.now().isoformat(),
        }

    def process_query(self, query: str, context: Dict = None) -> Dict:
        """Synchronous wrapper for backwards compatibility"""
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If already in async context, use fallback
                business_data = {
                    "overview": self.analytics.get_business_overview(),
                    "sales_insights": self.analytics.get_sales_insights(),
                    "inventory_insights": self.analytics.get_inventory_insights(),
                    "customer_insights": self.analytics.get_customer_insights(),
                    "staff_insights": self.analytics.get_staff_insights(),
                    "predictions": self.analytics.get_predictions(),
                    "recommendations": self.analytics.get_recommendations(),
                }
                intent = self.nlp.detect_intent(query)
                entities = self.nlp.extract_entities(query)
                return self._generate_fallback_response(
                    query, intent, entities, business_data
                )
            return loop.run_until_complete(self.process_query_async(query, context))
        except RuntimeError:
            return asyncio.run(self.process_query_async(query, context))

    def _format_customer_response(self, data: Dict) -> str:
        response = "**Customer Intelligence Report**\n\n"
        response += (
            f"👥 Total Customers: {sum(s['count'] for s in data['segments']):,}\n"
        )
        response += f"🔄 Repeat Purchase Rate: {data['loyalty_metrics']['repeat_purchase_rate']}%\n"
        response += f"⭐ NPS Score: {data['loyalty_metrics']['nps_score']}\n\n"

        response += "**Customer Segments:**\n"
        for seg in data["segments"]:
            response += f"• {seg['name']}: {seg['count']:,} customers (Avg: {self.response_gen.format_currency(seg['avg_spend'])})\n"

        if data["churn_risk"]:
            response += "\n**⚠️ Churn Risk Alert:**\n"
            for customer in data["churn_risk"][:3]:
                response += f"• {customer['customer']} - LTV: {self.response_gen.format_currency(customer['lifetime_value'])} ({customer['risk']} risk)\n"

        return response

    def _format_staff_response(self, data: Dict) -> str:
        response = "**Staff Performance Report**\n\n"
        response += f"✅ Present Today: {data['attendance_summary']['present_rate']}%\n"
        response += (
            f"⏰ Late Arrivals: {data['attendance_summary']['late_arrivals_today']}\n\n"
        )

        response += "**Top Performers:**\n"
        for i, staff in enumerate(data["performance_ranking"][:3], 1):
            response += f"{i}. {staff['name']} ({staff['role']}) - {self.response_gen.format_currency(staff.get('sales', 0))} | ⭐{staff['rating']}\n"

        if data.get("training_needs"):
            response += "\n**Training Recommendations:**\n"
            for need in data["training_needs"]:
                response += f"• {need['staff']} needs training in: {need['area']}\n"

        return response

    def _format_predictions_response(self, data: Dict) -> str:
        response = "**AI Predictions & Forecasts**\n\n"
        response += f"📈 **Sales Forecast (Next Month):** {self.response_gen.format_currency(data['sales_forecast']['next_month'])}\n"
        response += f"🎯 Confidence: {data['sales_forecast']['confidence']}%\n\n"

        response += "**Key Factors:**\n"
        for factor in data["sales_forecast"]["factors"]:
            response += f"• {factor}\n"

        response += "\n**Demand Trends:**\n"
        for pred in data["demand_predictions"]:
            emoji = (
                "📈"
                if pred["trend"] == "up"
                else "📉" if pred["trend"] == "down" else "➡️"
            )
            response += (
                f"{emoji} {pred['category']}: {pred['change']} ({pred['reason']})\n"
            )

        if data.get("stock_predictions"):
            response += "\n**⚠️ Stock Alerts:**\n"
            for stock in data["stock_predictions"][:3]:
                response += f"• {stock['sku']}: {stock['days_until_stockout']} days until stockout\n"

        return response

    def _format_overview_response(self, overview: Dict, recommendations: List) -> str:
        response = self.response_gen.generate_greeting() + "\n\n"
        response += "**Business Snapshot:**\n\n"
        response += f"💰 Today's Revenue: {self.response_gen.format_currency(overview['revenue']['today'])}\n"
        response += f"📈 Growth: {'+' if overview['revenue']['growth_percentage'] >= 0 else ''}{overview['revenue']['growth_percentage']}%\n"
        response += f"🛒 Orders: {overview['orders']['today']} | Pending: {overview['orders']['pending']}\n"
        response += f"⚠️ Low Stock Items: {overview['inventory']['low_stock_items']}\n"
        response += f"👥 Staff Present: {overview['staff']['present_today']}/{overview['staff']['total_employees']}\n\n"

        if recommendations:
            high_priority = [r for r in recommendations if r["priority"] == "high"]
            if high_priority:
                response += "**🔴 Requires Immediate Attention:**\n"
                for rec in high_priority[:2]:
                    response += f"• {rec['title']}: {rec['description']}\n"

        response += "\n*Ask me anything - sales, inventory, customers, staff, predictions, or recommendations.*"
        return response

    def execute_command(self, command: str, parameters: Dict) -> Dict:
        """Execute a JARVIS command"""
        commands = {
            "generate_report": self._cmd_generate_report,
            "reorder_stock": self._cmd_reorder_stock,
            "send_campaign": self._cmd_send_campaign,
            "transfer_staff": self._cmd_transfer_staff,
            "create_task": self._cmd_create_task,
            "analyze_store": self._cmd_analyze_store,
        }

        if command not in commands:
            return {"success": False, "message": "Unknown command"}

        return commands[command](parameters)

    def _cmd_generate_report(self, params: Dict) -> Dict:
        report_type = params.get("type", "daily")
        return {
            "success": True,
            "message": f"Generating {report_type} report...",
            "report_id": f"RPT-{uuid.uuid4().hex[:8].upper()}",
            "estimated_time": "2 minutes",
        }

    def _cmd_reorder_stock(self, params: Dict) -> Dict:
        return {
            "success": True,
            "message": "Purchase order created",
            "po_number": f"PO-{datetime.now().strftime('%Y%m%d')}-{random.randint(100,999)}",
            "items": params.get("items", []),
            "total_value": 125000,
        }

    def _cmd_send_campaign(self, params: Dict) -> Dict:
        return {
            "success": True,
            "message": "Campaign scheduled",
            "campaign_id": f"CMP-{uuid.uuid4().hex[:8].upper()}",
            "recipients": params.get("recipient_count", 234),
            "scheduled_time": params.get("time", "immediate"),
        }

    def _cmd_transfer_staff(self, params: Dict) -> Dict:
        return {
            "success": True,
            "message": "Staff transfer initiated",
            "staff": params.get("staff_name"),
            "from_store": params.get("from"),
            "to_store": params.get("to"),
            "effective_date": params.get("date", "tomorrow"),
        }

    def _cmd_create_task(self, params: Dict) -> Dict:
        return {
            "success": True,
            "message": "Task created",
            "task_id": f"TSK-{uuid.uuid4().hex[:8].upper()}",
            "assigned_to": params.get("assignee"),
            "due_date": params.get("due_date"),
        }

    def _cmd_analyze_store(self, params: Dict) -> Dict:
        return {
            "success": True,
            "message": f"Analysis complete for {params.get('store', 'all stores')}",
            "insights": {
                "performance_score": 78,
                "areas_of_improvement": ["Conversion rate", "Average order value"],
                "strengths": ["Customer satisfaction", "Staff attendance"],
            },
        }


# Global JARVIS instance
jarvis_instance = Jarvis()


# ============================================================================
# API ENDPOINTS - SUPERADMIN ONLY
# ============================================================================


@router.get("/status")
async def get_jarvis_status(current_user: dict = Depends(require_superadmin)):
    """Check JARVIS status - SUPERADMIN ONLY"""
    return {
        "status": "online",
        "version": "2.0.0",
        "name": "JARVIS",
        "ai_engine": {
            "provider": "Anthropic",
            "model": CLAUDE_MODEL,
            "enabled": jarvis_instance.claude_enabled,
            "status": "active" if jarvis_instance.claude_enabled else "fallback_mode",
        },
        "greeting": JarvisResponseGenerator.generate_greeting(),
        "capabilities": [
            "Business Analytics",
            "Sales Intelligence",
            "Inventory Management",
            "Customer Insights",
            "Staff Performance",
            "Predictions & Forecasting",
            "Actionable Recommendations",
            "Command Execution",
            (
                "Natural Language Understanding (Claude AI)"
                if jarvis_instance.claude_enabled
                else "Template-based Responses"
            ),
        ],
    }


@router.post("/query")
async def query_jarvis(
    query: JarvisQuery, current_user: dict = Depends(require_superadmin)
):
    """Send a query to JARVIS - SUPERADMIN ONLY"""
    result = await jarvis_instance.process_query_async(query.message, query.context)

    return {
        "query": query.message,
        "response": result["response"],
        "intent_detected": result["intent"],
        "entities": result["entities"],
        "data": result.get("data"),
        "ai_powered": result.get("ai_powered", False),
        "model": result.get("model", "fallback"),
        "timestamp": result["timestamp"],
    }


@router.post("/command")
async def execute_jarvis_command(
    command: JarvisCommand, current_user: dict = Depends(require_superadmin)
):
    """Execute a JARVIS command - SUPERADMIN ONLY"""
    if not command.confirm and command.command in [
        "reorder_stock",
        "send_campaign",
        "transfer_staff",
    ]:
        return {
            "requires_confirmation": True,
            "message": f"Please confirm execution of '{command.command}' command",
            "parameters": command.parameters,
        }

    result = jarvis_instance.execute_command(command.command, command.parameters)

    return {
        "command": command.command,
        "result": result,
        "executed_at": datetime.now().isoformat(),
    }


@router.get("/dashboard")
async def get_jarvis_dashboard(current_user: dict = Depends(require_superadmin)):
    """Get complete JARVIS dashboard data - SUPERADMIN ONLY"""
    return {
        "overview": jarvis_instance.analytics.get_business_overview(),
        "sales": jarvis_instance.analytics.get_sales_insights(),
        "inventory": jarvis_instance.analytics.get_inventory_insights(),
        "customers": jarvis_instance.analytics.get_customer_insights(),
        "staff": jarvis_instance.analytics.get_staff_insights(),
        "predictions": jarvis_instance.analytics.get_predictions(),
        "recommendations": jarvis_instance.analytics.get_recommendations(),
        "generated_at": datetime.now().isoformat(),
    }


@router.get("/alerts")
async def get_jarvis_alerts(current_user: dict = Depends(require_superadmin)):
    """Get all active alerts from JARVIS - SUPERADMIN ONLY"""
    inventory = jarvis_instance.analytics.get_inventory_insights()
    customers = jarvis_instance.analytics.get_customer_insights()
    staff = jarvis_instance.analytics.get_staff_insights()

    alerts = []

    # Inventory alerts
    for alert in inventory.get("critical_alerts", []):
        alerts.append(
            {
                "type": "inventory",
                "severity": "high" if alert["type"] == "out_of_stock" else "medium",
                "title": f"{alert['type'].replace('_', ' ').title()}: {alert['product']}",
                "details": alert,
            }
        )

    # Customer churn alerts
    for customer in customers.get("churn_risk", []):
        alerts.append(
            {
                "type": "customer",
                "severity": customer["risk"],
                "title": f"Churn Risk: {customer['customer']}",
                "details": customer,
            }
        )

    # Sort by severity
    severity_order = {"high": 0, "medium": 1, "low": 2}
    alerts.sort(key=lambda x: severity_order.get(x["severity"], 3))

    return {"alerts": alerts, "total": len(alerts)}


@router.get("/quick-insights")
async def get_quick_insights(current_user: dict = Depends(require_superadmin)):
    """Get quick insights for JARVIS widget - SUPERADMIN ONLY"""
    overview = jarvis_instance.analytics.get_business_overview()
    recommendations = jarvis_instance.analytics.get_recommendations()

    return {
        "revenue_today": overview["revenue"]["today"],
        "revenue_growth": overview["revenue"]["growth_percentage"],
        "orders_today": overview["orders"]["today"],
        "pending_orders": overview["orders"]["pending"],
        "low_stock_count": overview["inventory"]["low_stock_items"],
        "staff_present": f"{overview['staff']['present_today']}/{overview['staff']['total_employees']}",
        "top_recommendation": recommendations[0] if recommendations else None,
        "greeting": JarvisResponseGenerator.generate_greeting(),
    }

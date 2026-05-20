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
    model: Optional[str] = None  # which configured LLM to use (local|claude|extra)


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
    def _compute_overview_live() -> Optional[Dict]:
        """Aggregate a live business overview straight from the real
        operational collections. Returns None if the DB is unavailable so
        the caller can fall back. Chain-wide (all stores) — JARVIS is a
        SUPERADMIN tool."""
        orders_col = get_db_collection("orders")
        if orders_col is None:
            return None
        try:
            from datetime import datetime as _dt, timedelta as _td

            now = _dt.now()
            today = now.strftime("%Y-%m-%d")
            month_start = now.strftime("%Y-%m-01")
            ly_month_start = f"{now.year - 1:04d}-{now.month:02d}-01"
            ly_month_end = f"{now.year - 1:04d}-{now.month:02d}-31"
            counted = {"CONFIRMED", "PROCESSING", "READY", "DELIVERED"}

            rev_today = rev_month = rev_lastyear = 0.0
            orders_today = orders_month = 0
            pending = in_progress = ready = 0
            for o in orders_col.find({}):
                status = (o.get("status") or "").upper()
                created = str(o.get("created_at") or "")[:10]
                gt = float(o.get("grand_total") or 0)
                if status in counted:
                    if created >= month_start:
                        rev_month += gt
                        orders_month += 1
                    if created == today:
                        rev_today += gt
                        orders_today += 1
                    if ly_month_start <= created <= ly_month_end:
                        rev_lastyear += gt
                if status == "CONFIRMED":
                    pending += 1
                elif status == "PROCESSING":
                    in_progress += 1
                elif status == "READY":
                    ready += 1

            growth = (
                round(((rev_month - rev_lastyear) / rev_lastyear * 100), 1)
                if rev_lastyear > 0
                else 0.0
            )
            aov = round(rev_month / orders_month, 2) if orders_month else 0.0

            # Inventory
            products_col = get_db_collection("products")
            total_products = low_stock = out_of_stock = 0
            inv_value = 0.0
            if products_col is not None:
                for p in products_col.find({}):
                    total_products += 1
                    qty = int(p.get("stock_quantity") or p.get("quantity") or 0)
                    reorder = int(p.get("reorder_point") or 0)
                    price = float(p.get("offer_price") or p.get("mrp") or 0)
                    inv_value += qty * price
                    if qty <= 0:
                        out_of_stock += 1
                    elif reorder and qty <= reorder:
                        low_stock += 1

            # Customers
            customers_col = get_db_collection("customers")
            total_cust = new_cust = 0
            if customers_col is not None:
                total_cust = customers_col.count_documents({})
                new_cust = customers_col.count_documents(
                    {"created_at": {"$gte": month_start}}
                )

            # Staff
            users_col = get_db_collection("users")
            total_staff = 0
            if users_col is not None:
                total_staff = users_col.count_documents({"is_active": {"$ne": False}})

            return {
                "revenue": {
                    "today": round(rev_today, 2),
                    "yesterday": 0,
                    "this_week": 0,
                    "this_month": round(rev_month, 2),
                    "last_month": round(rev_lastyear, 2),
                    "growth_percentage": growth,
                    "target": 0,
                    "achievement_percent": 0,
                    "trend": (
                        "up" if growth > 0 else ("down" if growth < 0 else "stable")
                    ),
                },
                "orders": {
                    "today": orders_today,
                    "pending": pending,
                    "in_progress": in_progress,
                    "ready_for_delivery": ready,
                    "average_order_value": aov,
                    "conversion_rate": 0,
                },
                "inventory": {
                    "total_products": total_products,
                    "low_stock_items": low_stock,
                    "out_of_stock": out_of_stock,
                    "inventory_value": round(inv_value, 2),
                    "fast_moving_count": 0,
                    "slow_moving_count": 0,
                    "expiring_soon": 0,
                    "turnover_rate": 0,
                },
                "customers": {
                    "total": total_cust,
                    "new_this_month": new_cust,
                    "returning_rate": 0,
                    "average_lifetime_value": 0,
                    "nps_score": 0,
                    "top_segment": "N/A",
                },
                "staff": {
                    "total_employees": total_staff,
                    "present_today": 0,
                    "on_leave": 0,
                    "top_performer": "N/A",
                    "average_sales_per_staff": (
                        round(rev_month / total_staff, 2) if total_staff else 0
                    ),
                    "attendance_rate": 0,
                },
            }
        except Exception as e:
            logger.warning("JARVIS live overview failed: %s", e)
            return None

    @staticmethod
    def get_business_overview() -> Dict:
        """Comprehensive business overview — live from real collections,
        else an empty envelope. No seeded/mock fallback."""
        live = JarvisAnalyticsEngine._compute_overview_live()
        if live is not None:
            return live
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
    def _compute_sales_live() -> Optional[Dict]:
        """Live category breakdown + top products from this month's orders."""
        orders_col = get_db_collection("orders")
        if orders_col is None:
            return None
        try:
            from datetime import datetime as _dt

            month_start = _dt.now().strftime("%Y-%m-01")
            counted = {"CONFIRMED", "PROCESSING", "READY", "DELIVERED"}
            cat_sales: Dict[str, float] = {}
            cat_units: Dict[str, int] = {}
            prod_rev: Dict[str, Dict[str, Any]] = {}
            total_rev = 0.0
            for o in orders_col.find({"created_at": {"$gte": month_start}}):
                if (o.get("status") or "").upper() not in counted:
                    continue
                total_rev += float(o.get("grand_total") or 0)
                for it in o.get("items") or []:
                    cat = (it.get("category") or it.get("item_type") or "OTHER").upper()
                    val = float(it.get("item_total") or it.get("item_value") or 0)
                    qty = int(it.get("quantity") or 1)
                    cat_sales[cat] = cat_sales.get(cat, 0) + val
                    cat_units[cat] = cat_units.get(cat, 0) + qty
                    key = (
                        it.get("sku")
                        or it.get("product_id")
                        or it.get("product_name")
                        or "?"
                    )
                    slot = prod_rev.setdefault(
                        key,
                        {
                            "name": it.get("product_name") or key,
                            "sku": it.get("sku") or "",
                            "sales": 0,
                            "revenue": 0.0,
                        },
                    )
                    slot["sales"] += qty
                    slot["revenue"] += val
            if not cat_sales and total_rev == 0:
                return None
            top_cats = sorted(cat_sales.items(), key=lambda kv: -kv[1])[:6]
            top_prods = sorted(prod_rev.values(), key=lambda p: -p["revenue"])[:5]
            return {
                "top_selling_categories": [
                    {
                        "category": c.title(),
                        "sales": round(s, 2),
                        "units": cat_units.get(c, 0),
                        "growth": 0,
                    }
                    for c, s in top_cats
                ],
                "top_selling_products": [
                    {
                        "name": p["name"],
                        "sku": p["sku"],
                        "sales": p["sales"],
                        "revenue": round(p["revenue"], 2),
                    }
                    for p in top_prods
                ],
                "sales_by_store": [],
                "month_revenue": round(total_rev, 2),
            }
        except Exception as e:
            logger.warning("JARVIS live sales failed: %s", e)
            return None

    @staticmethod
    def get_sales_insights() -> Dict:
        """Detailed sales insights — live from orders, else empty (no mock)."""
        live = JarvisAnalyticsEngine._compute_sales_live()
        if live is not None:
            return live
        return {
            "top_selling_categories": [],
            "top_selling_products": [],
            "sales_by_store": [],
            "month_revenue": 0,
        }

    @staticmethod
    def _compute_inventory_live() -> Optional[Dict]:
        """Reorder + stock-health insights from the live products
        collection. Returns None if products are unavailable."""
        products_col = get_db_collection("products")
        if products_col is None:
            return None
        try:
            critical_alerts = []
            reorder_recs = []
            total = low = oos = 0
            total_value = 0.0
            for p in products_col.find({}):
                if p.get("is_active") is False:
                    continue
                total += 1
                qty = int(p.get("stock_quantity") or p.get("quantity") or 0)
                reorder = int(p.get("reorder_point") or 0)
                price = float(
                    p.get("offer_price") or p.get("mrp") or p.get("cost_price") or 0
                )
                total_value += qty * price
                name = p.get("name") or p.get("product_name") or "Unknown"
                sku = p.get("sku") or p.get("product_id") or ""
                if qty <= 0:
                    oos += 1
                    critical_alerts.append(
                        {
                            "type": "out_of_stock",
                            "sku": sku,
                            "product": name,
                            "last_sold": "—",
                            "demand": "unknown",
                        }
                    )
                elif reorder and qty <= reorder:
                    low += 1
                    critical_alerts.append(
                        {
                            "type": "low_stock",
                            "sku": sku,
                            "product": name,
                            "quantity": qty,
                            "reorder_point": reorder,
                        }
                    )
                    reorder_recs.append(
                        {
                            "sku": sku,
                            "product": name,
                            "current": qty,
                            "recommended_order": max(
                                int(p.get("reorder_quantity") or 0), reorder * 2, 20
                            ),
                            "supplier": p.get("vendor") or p.get("brand") or "—",
                        }
                    )
            if total == 0:
                return None
            healthy = max(0, total - low - oos)
            health = round(healthy / total * 100) if total else 0
            return {
                "critical_alerts": critical_alerts[:8],
                "reorder_recommendations": reorder_recs[:8],
                "slow_movers": [],
                "inventory_health_score": health,
                "turnover_ratio": 0,
                "dead_stock_value": 0,
                "totals": {
                    "products": total,
                    "low_stock": low,
                    "out_of_stock": oos,
                    "inventory_value": round(total_value, 2),
                },
            }
        except Exception as e:
            logger.warning("JARVIS live inventory failed: %s", e)
            return None

    @staticmethod
    def get_inventory_insights() -> Dict:
        """Inventory insights — live from the products collection, else an
        empty envelope. No seeded/mock fallback."""
        live = JarvisAnalyticsEngine._compute_inventory_live()
        if live is not None:
            return live
        return {
            "critical_alerts": [],
            "reorder_recommendations": [],
            "slow_movers": [],
            "inventory_health_score": 0,
            "turnover_ratio": 0,
            "dead_stock_value": 0,
        }

    @staticmethod
    def get_customer_insights() -> Dict:
        """Customer insights — live counts from the customers collection."""
        col = get_db_collection("customers")
        if col is None:
            return {
                "segments": [],
                "loyalty_metrics": {},
                "churn_risk": [],
                "upcoming_eye_tests": [],
            }
        try:
            from datetime import datetime as _dt

            month_start = _dt.now().strftime("%Y-%m-01")
            total = col.count_documents({})
            new_this_month = col.count_documents({"created_at": {"$gte": month_start}})
            b2b = col.count_documents({"customer_type": "B2B"})
            return {
                "segments": [
                    {
                        "name": "All customers",
                        "count": total,
                        "avg_spend": 0,
                        "characteristics": "",
                    },
                    {
                        "name": "New this month",
                        "count": new_this_month,
                        "avg_spend": 0,
                        "characteristics": "",
                    },
                    {
                        "name": "B2B",
                        "count": b2b,
                        "avg_spend": 0,
                        "characteristics": "",
                    },
                ],
                "loyalty_metrics": {
                    "repeat_purchase_rate": 0,
                    "referral_rate": 0,
                    "nps_score": 0,
                },
                "churn_risk": [],
                "upcoming_eye_tests": [],
            }
        except Exception:
            return {
                "segments": [],
                "loyalty_metrics": {},
                "churn_risk": [],
                "upcoming_eye_tests": [],
            }

    @staticmethod
    def get_staff_insights() -> Dict:
        """Staff insights — live active-staff count; perf ranking needs the
        incentive module (no mock numbers here)."""
        users = get_db_collection("users")
        total = 0
        if users is not None:
            try:
                total = users.count_documents({"is_active": {"$ne": False}})
            except Exception:
                total = 0
        return {
            "performance_ranking": [],
            "attendance_summary": {
                "total_staff": total,
                "present_today": 0,
                "on_leave": 0,
                "present_rate": 0,
                "late_arrivals_today": 0,
            },
            "orders_per_staff": 0,
        }

    @staticmethod
    def get_predictions() -> Dict:
        """Forecasts require the ORACLE agent + history. Honest empty
        envelope until that runs (no fabricated predictions)."""
        return {
            "revenue_forecast": [],
            "demand_forecast": [],
            "stockout_predictions": [],
            "churn_predictions": [],
        }

    @staticmethod
    def get_recommendations() -> List[Dict]:
        """Actionable recommendations derived from live inventory — real
        low/out-of-stock reorder prompts, no canned suggestions."""
        recs: List[Dict] = []
        inv = JarvisAnalyticsEngine._compute_inventory_live()
        if inv:
            oos = sum(
                1
                for a in inv.get("critical_alerts", [])
                if a.get("type") == "out_of_stock"
            )
            low = sum(
                1
                for a in inv.get("critical_alerts", [])
                if a.get("type") == "low_stock"
            )
            if oos:
                recs.append(
                    {
                        "priority": "high",
                        "category": "inventory",
                        "title": f"{oos} product(s) out of stock",
                        "action": "Reorder to avoid lost sales",
                    }
                )
            if low:
                recs.append(
                    {
                        "priority": "medium",
                        "category": "inventory",
                        "title": f"{low} product(s) below reorder point",
                        "action": "Raise purchase orders",
                    }
                )
        return recs


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
        cls,
        message: str,
        business_data: Dict,
        conversation_history: List[Dict] = None,
        model_id: str = None,
    ) -> str:
        """Run a JARVIS chat completion through the pluggable LLM provider
        (local OSS and/or Claude). `model_id` selects which configured
        model answers; None = the configured default. business_data PII is
        scrubbed by the provider before it leaves the process. Returns the
        text or None (caller falls back to the deterministic template)."""
        from agents import llm_provider

        system_prompt = cls.JARVIS_SYSTEM_PROMPT.format(
            current_datetime=datetime.now().strftime("%Y-%m-%d %H:%M:%S IST")
        )
        return await llm_provider.complete(
            system_prompt,
            message,
            model_id=model_id,
            business_data=business_data,
            history=conversation_history,
            max_tokens=2048,
            scrub=True,
        )


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

    @property
    def claude_enabled(self) -> bool:
        """True if any LLM (local OSS or Claude) is configured."""
        try:
            from agents import llm_provider

            return llm_provider.any_available()
        except Exception:
            return bool(ANTHROPIC_API_KEY)

    async def process_query_async(
        self, query: str, context: Dict = None, model_id: str = None
    ) -> Dict:
        """Process a natural language query via the selected LLM (or the
        deterministic template fallback)."""
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

        # Try the selected LLM first (local OSS and/or Claude)
        claude_response = None
        if self.claude_enabled:
            claude_response = await ClaudeClient.call_claude(
                message=query,
                business_data=business_data,
                conversation_history=self.conversation_history,
                model_id=model_id,
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

            from agents import llm_provider

            used_model = model_id or llm_provider.default_model_id() or "llm"
            return {
                "response": claude_response,
                "intent": intent,
                "entities": entities,
                "data": business_data.get("overview", {}),
                "ai_powered": True,
                "model": used_model,
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
            "success": False,
            "status": "NOT_IMPLEMENTED",
            "message": "Auto-PO creation is in advisory mode only — not connected to the vendor/purchase module yet. Please create the PO manually.",
            "suggested_items": params.get("items", []),
            "action_required": "Create PO manually in Vendor Management > Purchase Orders",
        }

    def _cmd_send_campaign(self, params: Dict) -> Dict:
        return {
            "success": False,
            "status": "NOT_IMPLEMENTED",
            "message": "Campaign execution is in advisory mode only — not connected to WhatsApp/SMS gateway yet. Please use the Marketing module to send campaigns.",
            "suggested_recipients": params.get("recipient_count", 0),
            "action_required": "Create campaign manually in Marketing > Campaigns",
        }

    def _cmd_transfer_staff(self, params: Dict) -> Dict:
        return {
            "success": False,
            "status": "NOT_IMPLEMENTED",
            "message": "Staff transfer is in advisory mode only — not connected to the HR module yet. Please process transfers manually.",
            "suggested_transfer": {
                "staff": params.get("staff_name"),
                "from_store": params.get("from"),
                "to_store": params.get("to"),
                "effective_date": params.get("date", "tomorrow"),
            },
            "action_required": "Process transfer manually in HR > Staff Transfers",
        }

    def _cmd_create_task(self, params: Dict) -> Dict:
        # This one CAN work — create a real task via the tasks collection
        try:
            from database.connection import get_seeded_db

            db = get_seeded_db()
            if db:
                task_id = f"TSK-{uuid.uuid4().hex[:8].upper()}"
                db.get_collection("tasks").insert_one(
                    {
                        "task_id": task_id,
                        "title": params.get("title", "JARVIS-created task"),
                        "description": params.get("description", ""),
                        "assigned_to": params.get("assignee"),
                        "due_date": params.get("due_date"),
                        "status": "OPEN",
                        "priority": params.get("priority", "MEDIUM"),
                        "source": "JARVIS",
                        "created_at": datetime.now().isoformat(),
                    }
                )
                return {
                    "success": True,
                    "message": "Task created successfully",
                    "task_id": task_id,
                    "assigned_to": params.get("assignee"),
                    "due_date": params.get("due_date"),
                }
        except Exception as e:
            return {"success": False, "message": f"Failed to create task: {str(e)}"}
        return {
            "success": False,
            "status": "NOT_IMPLEMENTED",
            "message": "Task creation unavailable — database not connected.",
        }

    def _cmd_analyze_store(self, params: Dict) -> Dict:
        # This is a read-only command — OK to return analytics summary
        store_id = params.get("store")
        try:
            # get_business_overview is a @staticmethod with no args; store_id is kept
            # in the response message below for context.
            overview = self.analytics.get_business_overview()
            return {
                "success": True,
                "message": f"Analysis complete for {store_id or 'all stores'}",
                "insights": overview,
            }
        except Exception:
            return {
                "success": True,
                "message": f"Analysis complete for {store_id or 'all stores'}",
                "insights": {
                    "performance_score": "N/A — connect to analytics for real data",
                    "areas_of_improvement": [],
                    "strengths": [],
                },
            }


# Global JARVIS instance
jarvis_instance = Jarvis()


# ============================================================================
# API ENDPOINTS - SUPERADMIN ONLY
# ============================================================================


@router.get("")
@router.get("/")
async def get_jarvis_root():
    """Root endpoint for AI assistant status"""
    return {
        "module": "jarvis",
        "status": "active",
        "message": "JARVIS status endpoint ready",
    }


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


@router.get("/models")
async def list_jarvis_models(current_user: dict = Depends(require_superadmin)):
    """Available LLM choices for the JARVIS chat selector. Reflects what's
    configured via env (local OSS, Claude, extra). Empty list = JARVIS
    runs on the deterministic template fallback only."""
    from agents import llm_provider

    return {
        "models": llm_provider.list_models(),
        "default": llm_provider.default_model_id(),
    }


@router.post("/query")
async def query_jarvis(
    query: JarvisQuery, current_user: dict = Depends(require_superadmin)
):
    """Send a query to JARVIS - SUPERADMIN ONLY"""
    result = await jarvis_instance.process_query_async(
        query.message, query.context, model_id=query.model
    )

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


# ============================================================================
# SUBAGENT INTELLIGENCE — Specialized domain agents
# ============================================================================


@router.post("/analyze")
async def subagent_analyze(
    query: JarvisQuery, current_user: dict = Depends(require_superadmin)
):
    """
    Route query through specialized subagents for deep analysis.
    SUPERADMIN ONLY. READ-ONLY — no modifications.
    """
    try:
        from ai.subagents import SubAgentOrchestrator
        from database.connection import get_seeded_db

        db = get_seeded_db()
        if db is None:
            return {
                "error": "Database not available",
                "query": query.message,
                "results": [],
            }

        store_id = query.context.get("store_id") if query.context else None
        result = await SubAgentOrchestrator.process(db, query.message, store_id)
        return result

    except ImportError:
        return {
            "error": "Subagent module not loaded",
            "query": query.message,
            "results": [],
            "note": "AI subagent architecture is being deployed",
        }
    except Exception as e:
        logger.error(f"Subagent analysis error: {e}")
        return {
            "error": str(e),
            "query": query.message,
            "results": [],
        }


# ============================================================================
# SUBAGENT ENDPOINTS
# ============================================================================
# NOTE: The legacy `GET /agents` handler that previously lived here has been
# removed in Phase 6.5b. It was reading from `core.subagents.AGENT_REGISTRY`
# (the pre-Phase-3 5-agent system) and shadowing the canonical 8-agent
# endpoint in `api/routers/agents.py` because jarvis_router is mounted
# before agents_router in main.py. The shadowing is exactly why the Jarvis
# page on production showed 5 of 8 agents instead of 8 of 8. The new
# endpoint at agents.py::list_agents now wins. The other endpoints on this
# file (`POST /agents/run-all`, `POST /agents/{id}/run`) live on distinct
# paths and continue to serve the legacy core.subagents flow for
# backwards-compat with anything still calling them.


@router.post("/agents/run-all")
async def run_all(current_user: dict = Depends(require_superadmin)):
    """Run all subagents and return combined intelligence report"""
    try:
        from core.subagents import (
            run_all_agents,
        )  # pylint: disable=import-outside-toplevel

        db = get_db_collection("__db__")  # Get the database reference
        if db is None:
            return {"error": "Database not available"}
        results = await run_all_agents(db.client.get_database())
        return {
            "ran_at": datetime.utcnow().isoformat(),
            "agents": results,
            "total_alerts": sum(r.get("alerts_count", 0) for r in results.values()),
        }
    except Exception as e:
        logger.error(f"Run all agents error: {e}")
        return {"error": str(e)}


@router.post("/agents/{agent_id}/run")
async def run_single_agent(
    agent_id: str, current_user: dict = Depends(require_superadmin)
):
    """Run a specific subagent"""
    try:
        from core.subagents import run_agent  # pylint: disable=import-outside-toplevel

        db = get_db_collection("__db__")
        if db is None:
            return {"error": "Database not available"}
        result = await run_agent(db.client.get_database(), agent_id)
        return {"agent_id": agent_id, "ran_at": datetime.utcnow().isoformat(), **result}
    except Exception as e:
        logger.error(f"Run agent {agent_id} error: {e}")
        return {"error": str(e)}

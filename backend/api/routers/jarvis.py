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
        """Staff insights — full roster (SUPERADMIN-owned data), today's
        attendance summary, and leave/payroll signal. JARVIS feeds this
        into the LLM with `scrub_level="customer"` so names flow through
        and the assistant can answer questions like "who's on leave today"
        or "how is Ravi performing" by name.
        """
        users_col = get_db_collection("users")
        attendance_col = get_db_collection("attendance")
        leaves_col = get_db_collection("leaves")

        roster: List[Dict] = []
        total = 0
        if users_col is not None:
            try:
                cursor = users_col.find(
                    {"is_active": {"$ne": False}},
                    {
                        "_id": 1, "username": 1, "name": 1, "full_name": 1,
                        "first_name": 1, "last_name": 1,
                        "roles": 1, "role": 1, "active_role": 1,
                        "active_store_id": 1, "store_id": 1, "stores": 1,
                        "phone": 1, "email": 1,
                        "joined_at": 1, "created_at": 1, "is_active": 1,
                    },
                ).limit(200)
                for u in cursor:
                    total += 1
                    name = (
                        u.get("full_name")
                        or u.get("name")
                        or (
                            f"{u.get('first_name','')} {u.get('last_name','')}".strip()
                            if (u.get("first_name") or u.get("last_name")) else None
                        )
                        or u.get("username")
                        or "Unnamed"
                    )
                    role_val = u.get("active_role") or u.get("role")
                    roles = u.get("roles") if isinstance(u.get("roles"), list) else (
                        [role_val] if role_val else []
                    )
                    store = u.get("active_store_id") or u.get("store_id") or (
                        (u.get("stores") or [None])[0] if isinstance(u.get("stores"), list) else None
                    )
                    roster.append({
                        "name": name,
                        "roles": roles,
                        "store_id": store,
                        "phone": u.get("phone") or "",
                        "joined_at": str(u.get("joined_at") or u.get("created_at") or "")[:10],
                    })
            except Exception as e:
                logger.warning("[JARVIS] staff roster fetch failed: %s", e)

        # Today's attendance roll-up — counts only, names live in roster.
        present_today = on_leave_today = 0
        if attendance_col is not None:
            try:
                today = datetime.now().strftime("%Y-%m-%d")
                present_today = attendance_col.count_documents(
                    {"date": today, "status": {"$in": ["PRESENT", "present", "PARTIAL"]}}
                )
                on_leave_today = attendance_col.count_documents(
                    {"date": today, "status": {"$in": ["LEAVE", "on_leave", "ABSENT"]}}
                )
            except Exception:
                pass
        if leaves_col is not None and on_leave_today == 0:
            try:
                today = datetime.now().strftime("%Y-%m-%d")
                on_leave_today = leaves_col.count_documents({
                    "status": {"$in": ["APPROVED", "approved"]},
                    "from_date": {"$lte": today},
                    "to_date": {"$gte": today},
                })
            except Exception:
                pass

        return {
            "roster": roster,
            "performance_ranking": [],
            "attendance_summary": {
                "total_staff": total,
                "present_today": present_today,
                "on_leave": on_leave_today,
                "present_rate": (
                    round(present_today / total * 100, 1) if total else 0
                ),
                "late_arrivals_today": 0,
            },
            "orders_per_staff": 0,
        }

    # ------------------------------------------------------------------
    # Extended-scope context — JARVIS reads the rest of the database
    # ------------------------------------------------------------------

    @staticmethod
    def get_extended_context() -> Dict:
        """Pulls compact roll-ups from every operational collection so
        JARVIS can answer questions outside the original sales/inventory/
        customer triad: stores, tasks, vendors, purchases, workshop,
        prescriptions, marketing/walkouts, eye tests, and the live agent
        roster. SUPERADMIN-only data — staff/vendor PII is preserved by
        the customer-scrub mode so the assistant can reason about them
        by name. Each section fail-soft to `[]` / `{}` if the collection
        is empty or missing.
        """
        ctx: Dict[str, Any] = {}

        # Stores
        try:
            col = get_db_collection("stores")
            if col is not None:
                ctx["stores"] = [
                    {
                        "store_id": str(s.get("_id") or s.get("store_id") or ""),
                        "name": s.get("name") or s.get("store_name") or "",
                        "city": s.get("city") or "",
                        "is_active": s.get("is_active", True),
                    }
                    for s in col.find({}).limit(50)
                ]
        except Exception as e:
            logger.warning("[JARVIS] stores ctx failed: %s", e)

        # Vendors
        try:
            col = get_db_collection("vendors")
            if col is not None:
                ctx["vendors"] = [
                    {
                        "name": v.get("name") or "",
                        "gstin": v.get("gstin") or "",
                        "city": v.get("city") or v.get("address_city") or "",
                        "category": v.get("category") or "",
                        "is_active": v.get("is_active", True),
                    }
                    for v in col.find({"is_active": {"$ne": False}}).limit(50)
                ]
        except Exception as e:
            logger.warning("[JARVIS] vendors ctx failed: %s", e)

        # Purchase orders (open + recent)
        try:
            col = get_db_collection("purchase_orders")
            if col is not None:
                open_pos = list(col.find(
                    {"status": {"$in": ["DRAFT", "SENT", "PARTIAL", "OPEN"]}},
                    {"vendor_name": 1, "status": 1, "total": 1, "created_at": 1, "po_number": 1},
                ).limit(20))
                ctx["purchases"] = {
                    "open_pos_count": col.count_documents(
                        {"status": {"$in": ["DRAFT", "SENT", "PARTIAL", "OPEN"]}}
                    ),
                    "open_pos_sample": [
                        {
                            "po_number": p.get("po_number") or str(p.get("_id"))[-6:],
                            "vendor": p.get("vendor_name") or "",
                            "status": p.get("status") or "",
                            "total": float(p.get("total") or 0),
                            "created_at": str(p.get("created_at") or "")[:10],
                        }
                        for p in open_pos
                    ],
                }
        except Exception as e:
            logger.warning("[JARVIS] purchases ctx failed: %s", e)

        # GRNs (recent goods-received)
        try:
            col = get_db_collection("grns")
            if col is not None:
                ctx["grns_last_30d"] = col.count_documents({
                    "created_at": {"$gte": (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")},
                })
        except Exception:
            pass

        # Tasks (open + overdue + by type)
        try:
            col = get_db_collection("tasks")
            if col is not None:
                today = datetime.now().strftime("%Y-%m-%d")
                open_count = col.count_documents({"status": {"$in": ["OPEN", "open", "IN_PROGRESS"]}})
                overdue = col.count_documents({
                    "status": {"$in": ["OPEN", "open", "IN_PROGRESS"]},
                    "due_date": {"$lt": today},
                })
                ctx["tasks"] = {
                    "open_count": open_count,
                    "overdue_count": overdue,
                    "open_sample": [
                        {
                            "title": t.get("title") or t.get("name") or "",
                            "assignee": t.get("assignee_name") or t.get("assigned_to") or "",
                            "due_date": str(t.get("due_date") or "")[:10],
                            "priority": t.get("priority") or "",
                            "type": t.get("task_type") or "",
                        }
                        for t in col.find(
                            {"status": {"$in": ["OPEN", "open", "IN_PROGRESS"]}},
                            {"title": 1, "name": 1, "assignee_name": 1, "assigned_to": 1,
                             "due_date": 1, "priority": 1, "task_type": 1},
                        ).sort("due_date", 1).limit(15)
                    ],
                }
        except Exception as e:
            logger.warning("[JARVIS] tasks ctx failed: %s", e)

        # Workshop jobs
        try:
            col = get_db_collection("workshop_jobs")
            if col is not None:
                today = datetime.now().strftime("%Y-%m-%d")
                ctx["workshop"] = {
                    "pending": col.count_documents({"status": "PENDING"}),
                    "in_progress": col.count_documents({"status": "IN_PROGRESS"}),
                    "ready_for_pickup": col.count_documents({"status": "READY_FOR_PICKUP"}),
                    "qc_failed": col.count_documents({"status": "QC_FAILED"}),
                    "completed_today": col.count_documents({
                        "status": {"$in": ["COMPLETED", "DELIVERED"]},
                        "completed_at": {"$gte": today},
                    }),
                    "overdue": col.count_documents({
                        "status": {"$nin": ["COMPLETED", "DELIVERED", "CANCELLED"]},
                        "promised_date": {"$lt": today},
                    }),
                }
        except Exception as e:
            logger.warning("[JARVIS] workshop ctx failed: %s", e)

        # Prescriptions
        try:
            col = get_db_collection("prescriptions")
            if col is not None:
                month_start = datetime.now().strftime("%Y-%m-01")
                ctx["prescriptions"] = {
                    "total": col.count_documents({}),
                    "this_month": col.count_documents({"created_at": {"$gte": month_start}}),
                }
        except Exception:
            pass

        # Eye tests
        try:
            col = get_db_collection("eye_tests")
            if col is not None:
                today = datetime.now().strftime("%Y-%m-%d")
                ctx["eye_tests"] = {
                    "today": col.count_documents({"test_date": today}),
                    "pending": col.count_documents({"status": {"$in": ["PENDING", "SCHEDULED"]}}),
                }
        except Exception:
            pass

        # Walkouts (MEGAPHONE/footfall)
        try:
            col = get_db_collection("walkouts")
            if col is not None:
                today = datetime.now().strftime("%Y-%m-%d")
                ctx["walkouts"] = {
                    "today": col.count_documents({"date": today}),
                    "this_week": col.count_documents({
                        "date": {"$gte": (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")},
                    }),
                }
        except Exception:
            pass

        # Marketing / notification logs
        try:
            col = get_db_collection("notification_logs")
            if col is not None:
                today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                ctx["marketing"] = {
                    "sent_today": col.count_documents({"sent_at": {"$gte": today_start.isoformat()}}),
                    "sent_this_week": col.count_documents({
                        "sent_at": {"$gte": (datetime.now() - timedelta(days=7)).isoformat()},
                    }),
                }
        except Exception:
            pass

        # Agent roll-up
        try:
            col = get_db_collection("agent_config")
            if col is not None:
                ctx["agents"] = [
                    {
                        "agent_id": a.get("agent_id"),
                        "enabled": a.get("enabled", False),
                        "last_run": str(a.get("last_run") or "")[:19],
                        "last_status": a.get("last_status") or "",
                        "run_count": a.get("run_count", 0),
                        "error_count": a.get("error_count", 0),
                    }
                    for a in col.find({}, {
                        "agent_id": 1, "enabled": 1, "last_run": 1,
                        "last_status": 1, "run_count": 1, "error_count": 1,
                    }).limit(20)
                ]
        except Exception:
            pass

        # Top SKUs by sales (last 30d) — actionable inventory intel
        try:
            orders_col = get_db_collection("orders")
            if orders_col is not None:
                cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
                pipeline = [
                    {"$match": {
                        "created_at": {"$gte": cutoff},
                        "status": {"$in": ["CONFIRMED", "PROCESSING", "READY", "DELIVERED"]},
                    }},
                    {"$unwind": "$items"},
                    {"$group": {
                        "_id": "$items.product_name",
                        "qty": {"$sum": "$items.quantity"},
                        "revenue": {"$sum": "$items.line_total"},
                    }},
                    {"$sort": {"qty": -1}},
                    {"$limit": 20},
                ]
                ctx["top_skus_30d"] = [
                    {
                        "product_name": str(r.get("_id") or ""),
                        "qty_sold": int(r.get("qty") or 0),
                        "revenue": float(r.get("revenue") or 0),
                    }
                    for r in orders_col.aggregate(pipeline)
                ]
        except Exception as e:
            logger.warning("[JARVIS] top SKUs ctx failed: %s", e)

        # PRODUCT CATALOG ANALYTICS — answers "most expensive product",
        # "biggest stock value at risk", "how many SKUs per category", etc.
        # These read from the products collection, NOT the orders, so they
        # work even when there's no sales history yet (e.g. fresh
        # TechCherry import where all 10,805 products exist but no orders).
        try:
            prod_col = get_db_collection("products")
            if prod_col is not None:
                # Top 20 most expensive — sorted by mrp/offer_price desc.
                # Includes store_id so JARVIS can answer per-store queries.
                top_by_price = list(prod_col.aggregate([
                    {"$match": {"is_active": {"$ne": False}}},
                    {"$addFields": {
                        "_price": {"$ifNull": ["$offer_price", "$mrp"]},
                    }},
                    {"$match": {"_price": {"$gt": 0}}},
                    {"$sort": {"_price": -1}},
                    {"$limit": 20},
                    {"$project": {
                        "_id": 0, "name": 1, "brand": 1, "category": 1,
                        "barcode": 1, "store_id": 1,
                        "mrp": 1, "offer_price": 1, "cost_price": 1,
                        "stock_quantity": 1,
                        "price": "$_price",
                    }},
                ]))
                ctx["top_products_by_price"] = top_by_price

                # Top 20 by stock value (price * quantity_on_hand) —
                # surfaces "which SKUs hold the most working capital".
                top_by_value = list(prod_col.aggregate([
                    {"$match": {"is_active": {"$ne": False}}},
                    {"$addFields": {
                        "_price": {"$ifNull": ["$offer_price", "$mrp"]},
                        "_qty": {"$ifNull": ["$stock_quantity", 0]},
                    }},
                    {"$addFields": {
                        "_stock_value": {"$multiply": ["$_price", "$_qty"]},
                    }},
                    {"$match": {"_stock_value": {"$gt": 0}}},
                    {"$sort": {"_stock_value": -1}},
                    {"$limit": 20},
                    {"$project": {
                        "_id": 0, "name": 1, "brand": 1, "category": 1,
                        "store_id": 1,
                        "price": "$_price",
                        "stock_quantity": "$_qty",
                        "stock_value": "$_stock_value",
                    }},
                ]))
                ctx["top_stock_value"] = top_by_value

                # Catalog summary by category + brand, plus a per-store
                # roll-up so JARVIS knows what each store's inventory
                # looks like at a glance.
                by_category = list(prod_col.aggregate([
                    {"$match": {"is_active": {"$ne": False}}},
                    {"$group": {
                        "_id": "$category",
                        "sku_count": {"$sum": 1},
                        "total_units": {"$sum": {"$ifNull": ["$stock_quantity", 0]}},
                        "avg_price": {"$avg": {"$ifNull": ["$offer_price", "$mrp"]}},
                        "max_price": {"$max": {"$ifNull": ["$offer_price", "$mrp"]}},
                    }},
                    {"$sort": {"sku_count": -1}},
                    {"$limit": 30},
                ]))
                ctx["catalog_by_category"] = [
                    {
                        "category": str(r.get("_id") or "UNCATEGORISED"),
                        "sku_count": int(r.get("sku_count") or 0),
                        "total_units_on_hand": int(r.get("total_units") or 0),
                        "avg_price": round(float(r.get("avg_price") or 0), 2),
                        "max_price": round(float(r.get("max_price") or 0), 2),
                    }
                    for r in by_category
                ]

                by_brand = list(prod_col.aggregate([
                    {"$match": {
                        "is_active": {"$ne": False},
                        "brand": {"$ne": "", "$ne": None},
                    }},
                    {"$group": {
                        "_id": "$brand",
                        "sku_count": {"$sum": 1},
                        "total_units": {"$sum": {"$ifNull": ["$stock_quantity", 0]}},
                        "avg_price": {"$avg": {"$ifNull": ["$offer_price", "$mrp"]}},
                    }},
                    {"$sort": {"sku_count": -1}},
                    {"$limit": 30},
                ]))
                ctx["catalog_by_brand"] = [
                    {
                        "brand": str(r.get("_id") or "UNBRANDED"),
                        "sku_count": int(r.get("sku_count") or 0),
                        "total_units_on_hand": int(r.get("total_units") or 0),
                        "avg_price": round(float(r.get("avg_price") or 0), 2),
                    }
                    for r in by_brand
                ]

                # Per-store catalog roll-up — answers "how many SKUs in
                # Pune?" without JARVIS having to count.
                by_store = list(prod_col.aggregate([
                    {"$match": {"is_active": {"$ne": False}}},
                    {"$group": {
                        "_id": "$store_id",
                        "sku_count": {"$sum": 1},
                        "total_stock_value": {"$sum": {
                            "$multiply": [
                                {"$ifNull": ["$offer_price", "$mrp", 0]},
                                {"$ifNull": ["$stock_quantity", 0]},
                            ],
                        }},
                        "max_price_sku_name": {"$first": "$name"},
                    }},
                ]))
                ctx["catalog_by_store"] = [
                    {
                        "store_id": str(r.get("_id") or "UNASSIGNED"),
                        "sku_count": int(r.get("sku_count") or 0),
                        "total_stock_value": round(float(r.get("total_stock_value") or 0), 2),
                    }
                    for r in by_store
                ]

                # Low-stock items, sorted by value at risk (price * gap).
                # More actionable than just "X items low" since it tells
                # the operator WHICH stockouts hurt the most.
                low_stock = list(prod_col.aggregate([
                    {"$match": {
                        "is_active": {"$ne": False},
                        "$expr": {"$lte": [
                            {"$ifNull": ["$stock_quantity", 0]},
                            {"$ifNull": ["$reorder_point", 0]},
                        ]},
                        "reorder_point": {"$gt": 0},
                    }},
                    {"$addFields": {
                        "_price": {"$ifNull": ["$offer_price", "$mrp"]},
                        "_gap": {"$subtract": [
                            {"$ifNull": ["$reorder_point", 0]},
                            {"$ifNull": ["$stock_quantity", 0]},
                        ]},
                    }},
                    {"$addFields": {
                        "_at_risk": {"$multiply": ["$_price", "$_gap"]},
                    }},
                    {"$sort": {"_at_risk": -1}},
                    {"$limit": 15},
                    {"$project": {
                        "_id": 0, "name": 1, "brand": 1, "category": 1,
                        "store_id": 1,
                        "stock_quantity": 1, "reorder_point": 1,
                        "price": "$_price",
                        "value_at_risk": "$_at_risk",
                    }},
                ]))
                ctx["low_stock_value_at_risk"] = low_stock
        except Exception as e:
            logger.warning("[JARVIS] catalog analytics ctx failed: %s", e)

        # Period locks + settings — answers "is March locked?"
        try:
            col = get_db_collection("period_locks")
            if col is not None:
                ctx["period_locks"] = [
                    {"period": str(p.get("period") or p.get("_id")), "locked": p.get("locked", False)}
                    for p in col.find({}).sort("period", -1).limit(6)
                ]
        except Exception:
            pass

        # ==============================================================
        # WIDE LENS — every operational collection JARVIS can read.
        # Compact roll-ups only (counts + small samples) so we stay under
        # the context budget. Each block fail-soft so one missing
        # collection doesn't take out the rest.
        # ==============================================================

        # FINANCE: expenses, budgets, advances
        try:
            col = get_db_collection("expenses")
            if col is not None:
                month_start = datetime.now().strftime("%Y-%m-01")
                total_month = 0.0
                category_breakdown: Dict[str, float] = {}
                for e in col.find({"date": {"$gte": month_start}}, {
                    "amount": 1, "category": 1, "vendor": 1,
                }).limit(500):
                    amt = float(e.get("amount") or 0)
                    total_month += amt
                    cat = e.get("category") or "OTHER"
                    category_breakdown[cat] = category_breakdown.get(cat, 0) + amt
                ctx["expenses_mtd"] = {
                    "total_this_month": round(total_month, 2),
                    "by_category": [
                        {"category": k, "amount": round(v, 2)}
                        for k, v in sorted(category_breakdown.items(), key=lambda kv: -kv[1])
                    ][:10],
                }
        except Exception:
            pass

        try:
            col = get_db_collection("budgets")
            if col is not None:
                ctx["budgets"] = [
                    {
                        "category": b.get("category") or b.get("name") or "",
                        "period": b.get("period") or "",
                        "allocated": float(b.get("allocated") or 0),
                        "spent": float(b.get("spent") or 0),
                    }
                    for b in col.find({}).limit(30)
                ]
        except Exception:
            pass

        # PAYROLL: salary records + advances + payslips signal
        try:
            col = get_db_collection("salary_records")
            if col is not None:
                month_start = datetime.now().strftime("%Y-%m-01")
                ctx["payroll_mtd"] = {
                    "records_this_month": col.count_documents({"month": {"$gte": month_start[:7]}}),
                    "total_paid_this_month": round(sum(
                        float(r.get("net_pay") or 0)
                        for r in col.find({"month": {"$gte": month_start[:7]}}).limit(500)
                    ), 2),
                }
        except Exception:
            pass
        try:
            col = get_db_collection("advances") or get_db_collection("salary_advances")
            if col is not None:
                ctx["salary_advances_open"] = col.count_documents({
                    "status": {"$in": ["OPEN", "open", "ACTIVE", "OUTSTANDING"]},
                })
        except Exception:
            pass

        # INCENTIVES (Pune model — Modules i/ii/iii)
        try:
            col = get_db_collection("payout_snapshots")
            if col is not None:
                ctx["incentive_payouts_recent"] = [
                    {
                        "period": s.get("period") or "",
                        "employee_id": s.get("employee_id") or "",
                        "employee_name": s.get("employee_name") or "",
                        "module_i": float(s.get("module_i") or 0),
                        "module_ii": float(s.get("module_ii") or 0),
                        "module_iii": float(s.get("module_iii") or 0),
                        "total": float(s.get("total_payout") or s.get("total") or 0),
                    }
                    for s in col.find({}).sort("period", -1).limit(20)
                ]
        except Exception:
            pass
        try:
            col = get_db_collection("incentive_settings")
            if col is not None:
                s = col.find_one({}) or {}
                ctx["incentive_settings_active"] = {
                    "module_i_weight": s.get("module_i_weight"),
                    "module_ii_weight": s.get("module_ii_weight"),
                    "module_iii_weight": s.get("module_iii_weight"),
                    "walkout_penalty_per": s.get("walkout_penalty_per"),
                    "fy": s.get("fy"),
                }
        except Exception:
            pass

        # STOCK detail — counts, transfers, returns
        try:
            col = get_db_collection("stock_counts")
            if col is not None:
                today = datetime.now().strftime("%Y-%m-%d")
                ctx["stock_counts"] = {
                    "today": col.count_documents({"count_date": today}),
                    "open_discrepancies": col.count_documents({"has_discrepancy": True}),
                }
        except Exception:
            pass
        try:
            col = get_db_collection("stock_transfers")
            if col is not None:
                ctx["stock_transfers"] = {
                    "in_transit": col.count_documents({"status": {"$in": ["IN_TRANSIT", "DISPATCHED"]}}),
                    "received_this_week": col.count_documents({
                        "received_at": {"$gte": (datetime.now() - timedelta(days=7)).isoformat()},
                    }),
                }
        except Exception:
            pass
        try:
            col = get_db_collection("vendor_returns")
            if col is not None:
                ctx["vendor_returns"] = {
                    "open": col.count_documents({"status": {"$in": ["OPEN", "PENDING"]}}),
                    "this_month": col.count_documents({
                        "created_at": {"$gte": datetime.now().strftime("%Y-%m-01")},
                    }),
                }
        except Exception:
            pass

        # LOYALTY
        try:
            col = get_db_collection("loyalty_accounts")
            if col is not None:
                ctx["loyalty"] = {
                    "active_accounts": col.count_documents({"status": {"$ne": "INACTIVE"}}),
                    "total_points_outstanding": sum(
                        int(a.get("points_balance") or 0)
                        for a in col.find({}, {"points_balance": 1}).limit(2000)
                    ),
                }
        except Exception:
            pass

        # TARGETS — sales targets
        try:
            col = get_db_collection("targets")
            if col is not None:
                month_key = datetime.now().strftime("%Y-%m")
                ctx["targets"] = [
                    {
                        "store_id": t.get("store_id") or "",
                        "period": t.get("period") or "",
                        "target_amount": float(t.get("target_amount") or 0),
                        "achievement": float(t.get("achievement") or 0),
                        "target_type": t.get("target_type") or "",
                    }
                    for t in col.find({"period": month_key}).limit(30)
                ]
        except Exception:
            pass

        # AGENT ACTIVITY — recent events + recent anomalies + audit log
        try:
            col = get_db_collection("agent_events")
            if col is not None:
                ctx["agent_events_recent"] = [
                    {
                        "event": e.get("event") or e.get("event_type") or "",
                        "source": e.get("source") or e.get("agent_id") or "",
                        "at": str(e.get("created_at") or e.get("at") or "")[:19],
                    }
                    for e in col.find({}).sort("created_at", -1).limit(20)
                ]
        except Exception:
            pass
        try:
            col = get_db_collection("anomalies")
            if col is not None:
                ctx["anomalies_open"] = [
                    {
                        "kind": a.get("kind") or a.get("type") or "",
                        "severity": a.get("severity") or "",
                        "summary": (a.get("summary") or a.get("message") or "")[:200],
                        "detected_at": str(a.get("detected_at") or a.get("created_at") or "")[:19],
                    }
                    for a in col.find(
                        {"status": {"$nin": ["RESOLVED", "DISMISSED"]}}
                    ).sort("detected_at", -1).limit(15)
                ]
        except Exception:
            pass
        try:
            col = get_db_collection("alert_history")
            if col is not None:
                ctx["alerts_last_7d"] = col.count_documents({
                    "created_at": {"$gte": (datetime.now() - timedelta(days=7)).isoformat()},
                })
        except Exception:
            pass
        try:
            col = get_db_collection("audit_logs")
            if col is not None:
                ctx["audit_log_recent"] = [
                    {
                        "action": a.get("action") or "",
                        "user": a.get("username") or a.get("user_id") or "",
                        "entity": a.get("entity_type") or a.get("resource") or "",
                        "at": str(a.get("created_at") or "")[:19],
                    }
                    for a in col.find({}).sort("created_at", -1).limit(20)
                ]
        except Exception:
            pass

        # PIXEL audit history — surface UI quality summary
        try:
            col = get_db_collection("ui_audits")
            if col is not None:
                latest = col.find_one(
                    {"agent_id": "pixel", "kind": "scheduled_audit"},
                    sort=[("ran_at", -1)],
                )
                if latest:
                    ctx["ui_audit_latest"] = {
                        "ran_at": latest.get("ran_at"),
                        "summary": latest.get("summary") or {},
                        "regressions_count": len(latest.get("regressions") or []),
                    }
                ctx["ui_audits_total"] = col.count_documents({"agent_id": "pixel"})
        except Exception:
            pass

        # HEALTH CHECKS — SENTINEL output
        try:
            col = get_db_collection("health_checks")
            if col is not None:
                ctx["health_checks_recent"] = [
                    {
                        "service": h.get("service") or h.get("target") or "",
                        "status": h.get("status") or "",
                        "checked_at": str(h.get("checked_at") or h.get("created_at") or "")[:19],
                    }
                    for h in col.find({}).sort("checked_at", -1).limit(10)
                ]
        except Exception:
            pass

        # INTEGRATIONS / sync runs / webhook inbox — NEXUS output
        try:
            col = get_db_collection("integrations")
            if col is not None:
                ctx["integrations"] = [
                    {
                        "name": i.get("name") or i.get("provider") or "",
                        "status": i.get("status") or "",
                        "last_sync_at": str(i.get("last_sync_at") or "")[:19],
                        "enabled": i.get("enabled", False),
                    }
                    for i in col.find({}).limit(20)
                ]
        except Exception:
            pass
        try:
            col = get_db_collection("sync_runs")
            if col is not None:
                ctx["sync_runs_recent"] = [
                    {
                        "provider": s.get("provider") or "",
                        "status": s.get("status") or "",
                        "records": s.get("records_synced") or 0,
                        "at": str(s.get("started_at") or s.get("created_at") or "")[:19],
                    }
                    for s in col.find({}).sort("started_at", -1).limit(10)
                ]
        except Exception:
            pass
        try:
            col = get_db_collection("webhook_inbox")
            if col is not None:
                ctx["webhook_inbox_recent"] = col.count_documents({
                    "received_at": {"$gte": (datetime.now() - timedelta(days=1)).isoformat()},
                })
        except Exception:
            pass

        # TALLY exports
        try:
            col = get_db_collection("tally_exports")
            if col is not None:
                ctx["tally_exports_recent"] = col.count_documents({
                    "exported_at": {"$gte": (datetime.now() - timedelta(days=7)).isoformat()},
                })
        except Exception:
            pass

        # SETTINGS — surface non-secret config (single doc usually)
        try:
            col = get_db_collection("settings")
            if col is not None:
                s = col.find_one({}) or {}
                # Strip any obvious secret fields before exposing
                safe_keys = {
                    k: v for k, v in s.items()
                    if not any(token in str(k).lower() for token in (
                        "secret", "key", "password", "token", "private",
                    )) and k != "_id"
                }
                if safe_keys:
                    ctx["settings"] = safe_keys
        except Exception:
            pass

        # SOP templates
        try:
            col = get_db_collection("sop_templates")
            if col is not None:
                ctx["sop_templates"] = [
                    {"id": str(t.get("_id"))[-6:], "title": t.get("title") or "", "active": t.get("active", True)}
                    for t in col.find({}).limit(20)
                ]
        except Exception:
            pass

        # EYE CAMPS
        try:
            col = get_db_collection("eye_camps")
            if col is not None:
                today = datetime.now().strftime("%Y-%m-%d")
                ctx["eye_camps"] = {
                    "upcoming": col.count_documents({"camp_date": {"$gte": today}}),
                    "this_month": col.count_documents({
                        "camp_date": {"$gte": datetime.now().strftime("%Y-%m-01")},
                    }),
                }
        except Exception:
            pass

        # HANDOFFS (shift / inventory handoffs)
        try:
            col = get_db_collection("handoffs")
            if col is not None:
                ctx["handoffs_open"] = col.count_documents({
                    "status": {"$in": ["OPEN", "PENDING", "DRAFT"]},
                })
        except Exception:
            pass

        return ctx

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
        """Actionable recommendations derived from live state across
        inventory, staffing, marketing, and finance. Each entry has:
          - priority: high | medium | low
          - category: inventory | staffing | marketing | finance | ops
          - title:    short headline
          - description: 1-2 sentence detail (load-bearing for the UI)
          - action:   imperative the operator can take
          - impact:   estimated ₹ or % impact when computable, else ""
          - link:     optional in-app path the "Take action" button can
                      deep-link to (e.g. /inventory?filter=low-stock)

        Fail-soft — any sub-source raising returns [] for that section,
        the others still run.
        """
        recs: List[Dict] = []

        # --- INVENTORY: out-of-stock + low-stock alerts -------------------
        try:
            inv = JarvisAnalyticsEngine._compute_inventory_live() or {}
            alerts = inv.get("critical_alerts") or []
            oos_count = sum(1 for a in alerts if a.get("type") == "out_of_stock")
            low_count = sum(1 for a in alerts if a.get("type") == "low_stock")
            oos_names = [a.get("product_name") for a in alerts if a.get("type") == "out_of_stock" and a.get("product_name")][:3]
            if oos_count:
                detail_names = (", ".join(oos_names) + ("…" if oos_count > 3 else "")) if oos_names else ""
                recs.append({
                    "priority": "high",
                    "category": "inventory",
                    "title": f"{oos_count} SKU(s) out of stock",
                    "description": (
                        f"{detail_names} are at zero stock. Every walk-in asking for these is a lost sale."
                        if detail_names else
                        f"{oos_count} products show zero stock. Lost-sale risk grows by the hour."
                    ),
                    "action": "Reorder critical SKUs",
                    "impact": "Prevents lost sales",
                    "link": "/inventory?filter=out-of-stock",
                })
            if low_count:
                recs.append({
                    "priority": "medium",
                    "category": "inventory",
                    "title": f"{low_count} SKU(s) below reorder point",
                    "description": (
                        f"{low_count} products are at or under their reorder threshold. "
                        "Acting now avoids a stock-out in the next 7-14 days."
                    ),
                    "action": "Raise purchase orders",
                    "impact": "Avoids future stock-outs",
                    "link": "/inventory?filter=low-stock",
                })
        except Exception:
            pass

        # --- STAFFING: open POs / understaffed signal ---------------------
        try:
            users_col = get_db_collection("users")
            attendance_col = get_db_collection("attendance")
            if users_col is not None and attendance_col is not None:
                today = datetime.now().strftime("%Y-%m-%d")
                total_active = users_col.count_documents({"is_active": {"$ne": False}})
                present = attendance_col.count_documents({"date": today, "status": {"$in": ["PRESENT", "present", "PARTIAL"]}})
                if total_active > 0 and present / total_active < 0.5:
                    recs.append({
                        "priority": "high",
                        "category": "staffing",
                        "title": "Low attendance today",
                        "description": (
                            f"Only {present} of {total_active} active staff marked present. "
                            "Customer-facing roles may be uncovered."
                        ),
                        "action": "Check store rosters & call backups",
                        "impact": "Protects service quality",
                        "link": "/hr/attendance",
                    })
        except Exception:
            pass

        # --- MARKETING: re-engagement opportunity -------------------------
        try:
            cust_col = get_db_collection("customers")
            orders_col = get_db_collection("orders")
            if cust_col is not None and orders_col is not None:
                six_months_ago = (datetime.now() - timedelta(days=180)).isoformat()
                # Customers who haven't ordered in 6 months (cap query small)
                recent_buyer_ids = set()
                for o in orders_col.find(
                    {"created_at": {"$gte": six_months_ago}},
                    {"customer_id": 1},
                ).limit(5000):
                    cid = o.get("customer_id")
                    if cid:
                        recent_buyer_ids.add(str(cid))
                total_customers = cust_col.count_documents({})
                lapsed = max(0, total_customers - len(recent_buyer_ids))
                if total_customers >= 20 and lapsed >= max(5, total_customers // 5):
                    recs.append({
                        "priority": "medium",
                        "category": "marketing",
                        "title": f"{lapsed} lapsed customers (6+ months)",
                        "description": (
                            f"{lapsed} of {total_customers} customers haven't purchased in 6+ months. "
                            "A targeted WhatsApp campaign typically wins back 5-10%."
                        ),
                        "action": "Launch reactivation campaign via MEGAPHONE",
                        "impact": "Recovers latent revenue",
                        "link": "/customers/campaigns",
                    })
        except Exception:
            pass

        # --- FINANCE: open vendor returns / outstanding handoffs ----------
        try:
            vr_col = get_db_collection("vendor_returns")
            if vr_col is not None:
                open_returns = vr_col.count_documents({"status": {"$in": ["OPEN", "PENDING"]}})
                if open_returns >= 3:
                    recs.append({
                        "priority": "low",
                        "category": "finance",
                        "title": f"{open_returns} open vendor returns",
                        "description": (
                            f"{open_returns} vendor returns are open. Each one ties up cash + shelf space."
                        ),
                        "action": "Close vendor returns or escalate",
                        "impact": "Frees working capital",
                        "link": "/inventory/vendor-returns",
                    })
        except Exception:
            pass

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
            priority = rec.get("priority", "low")
            priority_emoji = (
                "🔴"
                if priority == "high"
                else "🟡" if priority == "medium" else "🟢"
            )
            title = rec.get("title", "")
            description = rec.get("description") or rec.get("action") or ""
            action = rec.get("action", "")
            impact = rec.get("impact", "")
            response += f"{i}. {priority_emoji} **{title}**\n"
            if description:
                response += f"   {description}\n"
            if action:
                response += f"   💡 *Action:* {action}\n"
            if impact:
                response += f"   📈 *Impact:* {impact}\n"
            response += "\n"

        return response


# ============================================================================
# CLAUDE AI INTEGRATION
# ============================================================================


class ClaudeClient:
    """Anthropic Claude API client for JARVIS"""

    JARVIS_SYSTEM_PROMPT = """You are JARVIS (Just A Rather Very Intelligent System), the personal AI executive assistant to the Superadmin of Better Vision + WizOpt — a premium optical retail chain operating multiple stores in India. You report directly to the owner. Think of yourself as Tony Stark's JARVIS: an inner-circle AI with full access to the business's operational nervous system.

## Your Personality:
- Sophisticated, professional, lightly witty British English
- Address the user as "Sir" or "Ma'am"
- Direct and concise — the owner is busy
- When you deliver bad news, lead with the solution
- Never apologise for limits — if data is missing, say so plainly and tell the owner where to look

## Your Access (this is important):
You have **full read access to the entire IMS 2.0 database** as the Superadmin's agent. The BUSINESS DATA section below is a live snapshot extracted moments ago across the operational surface. The snapshot is grouped into these sections — when you reference a number, prefer naming the section so the owner knows what view it came from:

**Sales + customers**
- `overview` — revenue (today / month / YoY), orders, inventory, customers, staff KPI
- `sales_insights` — trends, per-channel split
- `customer_insights` — segments, loyalty, churn risk
- `top_skus_30d` — top 20 SKUs by units sold last 30 days

**Staff + operations**
- `staff_insights.roster` — every active employee by **name, role, store, phone, join date**. You may reference them by name.
- `staff_insights.attendance_summary` — today's present / on-leave counts
- `stores` — every retail location
- `tasks.open_sample` — open + overdue tasks with assignee names
- `workshop` — job pipeline (pending / in_progress / overdue / ready)
- `prescriptions`, `eye_tests`, `eye_camps` — clinical pipeline
- `walkouts` — footfall today + this week
- `sop_templates`, `handoffs_open` — SOP coverage + open handoffs

**Inventory + procurement**
- `inventory_insights` — critical alerts, reorder list, slow movers
- `top_products_by_price` — **20 most-expensive SKUs** with brand, category, MRP, stock, store_id. Use this to answer "what's our most expensive product" / "most expensive in Pune" — filter by store_id when the user names a store.
- `top_stock_value` — 20 SKUs holding the most working capital (price × on-hand). Useful for "where's our cash tied up".
- `catalog_by_category` — SKU count + total units + avg/max price per category (top 30). Answers "how many frame SKUs do we have" / "which category has the most expensive items".
- `catalog_by_brand` — SKU count + total units + avg price per brand (top 30). Answers "how many Ray-Ban SKUs" / "which brand have we stocked the most of".
- `catalog_by_store` — per-store SKU count + total stock value. Answers "how many products in Pune" / "which store has the highest inventory value".
- `low_stock_value_at_risk` — SKUs at/below reorder point, ranked by revenue at risk (price × stock gap). Answers "what are our most urgent reorders".
- `vendors` — supplier roster (name, GSTIN, city, category)
- `purchases.open_pos_sample` — open POs by vendor
- `grns_last_30d` — goods receipts
- `stock_counts`, `stock_transfers`, `vendor_returns` — stock movement signals

**For per-store questions** (e.g. "most expensive product in Pune"): use `top_products_by_price` and filter the list by `store_id == "BV-PUN-01"` (or whichever the user named). Each entry carries its `store_id` so this is a one-line filter, no extra query needed.

**Finance + payroll + incentives**
- `expenses_mtd` — month-to-date expense total + category breakdown
- `budgets` — allocated vs spent
- `payroll_mtd` — month-to-date payroll spend + record count
- `salary_advances_open` — outstanding advances
- `incentive_payouts_recent` — Pune model (Module i/ii/iii) payout snapshots
- `incentive_settings_active` — current weightages + walkout penalty
- `targets` — sales targets for current month
- `period_locks` — accounting period status

**Loyalty + marketing**
- `loyalty.active_accounts`, `loyalty.total_points_outstanding`
- `marketing.sent_today/sent_this_week` — outbound WhatsApp/SMS volume

**System / agents**
- `agents` — live status of all 8 Jarvis agents (last_run, run_count, errors)
- `agent_events_recent` — last 20 cross-agent events
- `anomalies_open` — open anomalies from ORACLE
- `alerts_last_7d` — alert volume signal
- `audit_log_recent` — last 20 audit log entries (action, user, entity)
- `ui_audit_latest` + `ui_audits_total` — PIXEL's most recent UI/UX audit summary + total audits on record
- `health_checks_recent` — SENTINEL output
- `integrations`, `sync_runs_recent`, `webhook_inbox_recent`, `tally_exports_recent` — NEXUS surface
- `settings` — non-secret system config

You also have an on-demand reader at `GET /api/v1/jarvis/data/{collection}` that the operator can hit for any collection in the allow-list when they want the full table (e.g. "show me all expenses for May"). The list of available collections is at `/jarvis/data/collections`.

**Customer/patient PII (names, phones, emails, addresses) is automatically redacted before you see it — that is by design, not a limit on your authority.** You may refer to customers in aggregate ("23 new customers this month", "8 churn-risk accounts") without naming individuals.

If a specific data point isn't in the snapshot, do NOT say "I'm restricted" or "ask the Superadmin for access". Instead, give your best read of what IS there, and tell the owner where to look for the rest (mention the relevant /reports or /finance / /agents page).

## Response Guidelines:
- Markdown for clarity (headers, bullet points, bold for emphasis)
- Format currency in Indian Rupees (₹), Lakhs (L), Crores (Cr)
- Actionable insights — not just numbers
- Owner-of-business voice: think strategy, not tutorial
- Avoid emojis unless they genuinely help scannability

## Business Context:
Premium optical retail — frames, sunglasses, lenses (progressive + Rx), contact lenses, watches, hearing aids, smart eyewear. Indian financial year (April–March). 11-role staff hierarchy.

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
        (Claude). `model_id` selects which configured Claude tier answers;
        None = the configured default.

        Scrub policy: `scrub_level="customer"` — customer/patient PII is
        stripped before leaving the process, but owner-data (own staff
        names, vendor names, store info, SKU names) flows through so
        JARVIS can reason about it by name. Context budget bumped to
        ~32k chars to accommodate the wider lens.
        """
        from agents import llm_provider

        # Use .replace() rather than .format() — the prompt contains lots
        # of literal `{collection}`, `{...}` and other curly braces as
        # documentation of API paths and JSON keys, which .format() would
        # interpret as missing placeholders → KeyError. .replace() only
        # touches the explicit `{current_datetime}` token.
        # Tier-aware budgets — Claude (standard) and Opus (premium) both
        # have a 200k-token window so they get the full lens.
        # See `llm_provider.model_budgets()` for the per-tier defaults.
        budgets = llm_provider.model_budgets(model_id)

        system_prompt = cls.JARVIS_SYSTEM_PROMPT.replace(
            "{current_datetime}",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S IST"),
        )
        return await llm_provider.complete(
            system_prompt,
            message,
            model_id=model_id,
            business_data=business_data,
            history=conversation_history,
            scrub_level="customer",
            **budgets,
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

        # Gather comprehensive business data — full owner-data lens.
        # Staff/vendor/store names flow through (scrub_level="customer"
        # downstream) so JARVIS can reason about them by name; only
        # customer/patient PII is stripped before leaving the process.
        business_data = {
            "overview": self.analytics.get_business_overview(),
            "sales_insights": self.analytics.get_sales_insights(),
            "inventory_insights": self.analytics.get_inventory_insights(),
            "customer_insights": self.analytics.get_customer_insights(),
            "staff_insights": self.analytics.get_staff_insights(),
            "predictions": self.analytics.get_predictions(),
            "recommendations": self.analytics.get_recommendations(),
        }
        try:
            business_data.update(self.analytics.get_extended_context())
        except Exception as e:
            logger.warning("[JARVIS] extended ctx failed (continuing with base): %s", e)

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
            high_priority = [r for r in recommendations if r.get("priority") == "high"]
            if high_priority:
                response += "**🔴 Requires Immediate Attention:**\n"
                for rec in high_priority[:2]:
                    # `description` is optional — fall back to `action` text
                    # (the recommendations from get_recommendations() only
                    # carry title + action, not description).
                    detail = rec.get("description") or rec.get("action") or ""
                    response += f"• {rec.get('title', '?')}: {detail}\n"

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
    configured via env (Claude, Claude Opus, extra). Empty list = JARVIS
    runs on the deterministic template fallback only."""
    from agents import llm_provider

    return {
        "models": llm_provider.list_models(),
        "default": llm_provider.default_model_id(),
    }


@router.get("/integrations/status")
async def get_integration_status(current_user: dict = Depends(require_superadmin)):
    """Read-only integration status for the operator. SUPERADMIN ONLY.

    Reports, per external integration, whether its credentials are present
    (KEY presence only - never values), the integrations-collection state,
    and the current DISPATCH_MODE - so the owner can see what is live vs
    dormant as credentials are added on Railway. Never returns a secret."""
    from ..services.integration_status import build_integration_status

    db = None
    try:
        from ..dependencies import get_db

        conn = get_db()
        if conn is not None and getattr(conn, "is_connected", False):
            db = conn.db
    except Exception:
        db = None
    return build_integration_status(db)


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


@router.get("/recommendations")
async def get_jarvis_recommendations(
    limit: int = 10,
    priority: Optional[str] = None,
    current_user: dict = Depends(require_superadmin),
):
    """Full list of actionable recommendations across inventory,
    staffing, marketing, and finance. SUPERADMIN ONLY.

    Each rec has: priority, category, title, description, action,
    impact, optional link (deep-link for the UI Take-action button).

    Filter via `priority=high` to only get critical items.
    """
    recs = jarvis_instance.analytics.get_recommendations() or []
    if priority:
        recs = [r for r in recs if r.get("priority") == priority.lower()]
    limit = max(1, min(int(limit), 50))
    return {
        "recommendations": recs[:limit],
        "total": len(recs),
        "as_of": datetime.now().isoformat(),
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


# ============================================================================
# JARVIS "wide-lens" data endpoints
# ============================================================================

# Collections that ARE allowed via the ad-hoc /jarvis/data/{collection}
# endpoint. This is an explicit allow-list rather than a deny-list — the
# alternative is leaking sensitive collections (vendor_portal_tokens,
# session secrets, etc.) the moment anyone adds one without thinking.
_JARVIS_QUERYABLE_COLLECTIONS = frozenset({
    # Operational
    "orders", "products", "stock", "stock_units", "stock_counts",
    "stock_transfers", "purchase_orders", "grns", "vendor_returns",
    "prescriptions", "eye_tests", "eye_camps", "workshop_jobs",
    "walkouts", "walk_in_counters",
    # People (own-data, no customer PII fields shown)
    "users", "stores", "vendors",
    "attendance", "leaves",
    # Finance
    "expenses", "budgets", "advances", "salary_advances",
    "salary_records", "salary_config", "payslips", "payroll",
    "incentives", "incentive_inputs", "incentive_settings",
    "points_log", "payout_snapshots",
    "targets", "period_locks",
    # Tasks + SOP
    "tasks", "sop_templates", "handoffs",
    # Loyalty (aggregate; customer name still PII-scrubbed)
    "loyalty_accounts", "loyalty_transactions", "loyalty_settings",
    # Agent infra
    "agent_config", "agent_events", "agent_audit_log",
    "anomalies", "alert_history", "health_checks", "ui_audits",
    "audit_logs",
    # Integrations
    "integrations", "sync_runs", "webhook_inbox", "tally_exports",
    "notification_logs", "notifications",
    # Settings
    "settings",
})

# Collections that MUST be customer-PII-scrubbed before going to the LLM
# even though they're queryable. customer/patient names + phones are
# scrubbed; aggregate counts + IDs remain.
_CUSTOMER_PII_COLLECTIONS = frozenset({
    "customers", "loyalty_accounts", "loyalty_transactions",
    "prescriptions", "eye_tests",
})


def _coerce_mongo_value(s: str) -> Any:
    """Best-effort JSON parse; falls back to string if it isn't JSON.
    Lets ?filter={"status":"OPEN"} work without forcing the caller to
    URL-encode the JSON when they pass it via the structured params."""
    import json as _json
    if not s:
        return s
    try:
        return _json.loads(s)
    except (ValueError, TypeError):
        return s


@router.get("/data/collections", summary="List collections JARVIS can read")
async def list_queryable_collections(
    current_user: dict = Depends(require_superadmin),
):
    """Return the allow-list of collections JARVIS can query via
    `/jarvis/data/{collection}`. Used by the UI to populate a picker
    and by JARVIS itself when answering "what data do you have?".
    """
    return {
        "collections": sorted(_JARVIS_QUERYABLE_COLLECTIONS),
        "customer_pii_collections": sorted(_CUSTOMER_PII_COLLECTIONS),
        "as_of": datetime.now().isoformat(),
    }


@router.get(
    "/data/{collection}",
    summary="Ad-hoc read of any collection JARVIS is permitted to see",
    description=(
        "Read-only paginated dump of a single collection for JARVIS / "
        "the operator. SUPERADMIN-only. The `collection` must be on the "
        "allow-list (see `/jarvis/data/collections`). Customer-PII "
        "collections have name/phone/email fields stripped before return. "
        "Write operations are NOT exposed — this endpoint never mutates."
    ),
)
async def jarvis_read_collection(
    collection: str,
    limit: int = 50,
    skip: int = 0,
    sort_by: Optional[str] = None,
    sort_desc: bool = True,
    filter_field: Optional[str] = None,
    filter_value: Optional[str] = None,
    current_user: dict = Depends(require_superadmin),
):
    """Generic read endpoint — JARVIS or the UI can use this to fetch
    any allow-listed collection on demand."""
    if collection not in _JARVIS_QUERYABLE_COLLECTIONS:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Collection '{collection}' is not on the JARVIS allow-list. "
                f"Hit /jarvis/data/collections to see what's available."
            ),
        )
    limit = max(1, min(int(limit), 500))
    skip = max(0, int(skip))

    col = get_db_collection(collection)
    if col is None:
        return {
            "collection": collection,
            "rows": [],
            "total": 0,
            "limit": limit,
            "skip": skip,
            "as_of": datetime.now().isoformat(),
        }

    flt: Dict[str, Any] = {}
    if filter_field and filter_value is not None:
        flt[filter_field] = _coerce_mongo_value(filter_value)

    try:
        total = col.count_documents(flt)
    except Exception:
        total = 0

    try:
        cursor = col.find(flt, {"_id": 0})
        if sort_by:
            cursor = cursor.sort(sort_by, -1 if sort_desc else 1)
        rows = list(cursor.skip(skip).limit(limit))
    except Exception as e:
        logger.warning("[JARVIS-DATA] read %s failed: %s", collection, e)
        rows = []

    # Apply customer-PII scrub for sensitive collections
    if collection in _CUSTOMER_PII_COLLECTIONS:
        from agents.llm_provider import scrub_pii
        rows = [scrub_pii(r, level="customer") for r in rows]

    return {
        "collection": collection,
        "rows": rows,
        "total": total,
        "limit": limit,
        "skip": skip,
        "filter": flt,
        "as_of": datetime.now().isoformat(),
    }


# ============================================================================
# PIXEL audit history
# ============================================================================

@router.get(
    "/agents/pixel/audits",
    summary="Recent PIXEL UI/UX audit runs",
    description=(
        "Returns the last N PIXEL audit runs from `ui_audits` plus "
        "the latest summary (overall scores, regression count). Used by "
        "the JARVIS page's PIXEL card to render audit history. "
        "SUPERADMIN-only. If PIXEL has never run, returns an empty list "
        "and a `pagespeed_ready` flag indicating whether the API key is "
        "provisioned."
    ),
)
async def pixel_audit_history(
    limit: int = 7,
    current_user: dict = Depends(require_superadmin),
):
    """Return PIXEL audit history + readiness signal."""
    import os as _os
    limit = max(1, min(int(limit), 50))
    col = get_db_collection("ui_audits")
    pagespeed_ready = bool(_os.getenv("PAGESPEED_API_KEY"))
    frontend_url = _os.getenv("FRONTEND_BASE_URL", "https://ims-2-0-railway.vercel.app")

    if col is None:
        return {
            "audits": [],
            "latest": None,
            "pagespeed_ready": pagespeed_ready,
            "frontend_url": frontend_url,
            "audits_total": 0,
            "as_of": datetime.now().isoformat(),
        }

    try:
        audits = list(
            col.find(
                {"agent_id": "pixel", "kind": "scheduled_audit"},
                {"_id": 0},
            ).sort("ran_at", -1).limit(limit)
        )
    except Exception as e:
        logger.warning("[JARVIS] pixel audits read failed: %s", e)
        audits = []

    latest = audits[0] if audits else None

    # Compute trend deltas for the latest vs the previous audit
    deltas = {}
    if len(audits) >= 2:
        prev = audits[1]
        for key in ("overall_min_perf", "overall_min_a11y", "total_a11y_violations"):
            cur_v = (latest or {}).get("summary", {}).get(key)
            prev_v = (prev or {}).get("summary", {}).get(key)
            if cur_v is not None and prev_v is not None:
                deltas[key] = round(cur_v - prev_v, 3)

    try:
        audits_total = col.count_documents({"agent_id": "pixel"})
    except Exception:
        audits_total = len(audits)

    return {
        "audits": audits,
        "latest": latest,
        "deltas_vs_previous": deltas,
        "pagespeed_ready": pagespeed_ready,
        "frontend_url": frontend_url,
        "audits_total": audits_total,
        "as_of": datetime.now().isoformat(),
    }


# ============================================================================
# SENTINEL health history
# ============================================================================

@router.get(
    "/agents/sentinel/health",
    summary="SENTINEL latest health checks + active alerts",
    description=(
        "Returns SENTINEL's latest scored health check, the last 60 minutes "
        "of score history for sparkline rendering, and the most recent "
        "alert_history rows. SUPERADMIN-only."
    ),
)
async def sentinel_health(
    history_limit: int = 60,
    alerts_limit: int = 10,
    current_user: dict = Depends(require_superadmin),
):
    """Return SENTINEL operational data for the UI card."""
    history_limit = max(1, min(int(history_limit), 240))
    alerts_limit = max(1, min(int(alerts_limit), 50))

    health_col = get_db_collection("health_checks")
    alerts_col = get_db_collection("alert_history")

    latest = None
    history: List[Dict[str, Any]] = []
    if health_col is not None:
        try:
            history = list(
                health_col.find({"agent_id": "sentinel"}, {"_id": 0})
                .sort("timestamp", -1)
                .limit(history_limit)
            )
            if history:
                latest = history[0]
        except Exception as e:
            logger.warning("[JARVIS] sentinel health read failed: %s", e)

    alerts: List[Dict[str, Any]] = []
    if alerts_col is not None:
        try:
            alerts = list(
                alerts_col.find({}, {"_id": 0})
                .sort("timestamp", -1)
                .limit(alerts_limit)
            )
        except Exception:
            pass

    return {
        "latest": latest,
        "history": list(reversed(history)),  # oldest → newest for the sparkline
        "alerts": alerts,
        "history_count": len(history),
        "as_of": datetime.now().isoformat(),
    }

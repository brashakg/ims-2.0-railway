"""
IMS 2.0 - Dashboard Widgets Router
==================================
Thin read-only endpoints that back the role-specific Hub widgets
(frontend RoleSpecificWidgets.tsx). These previously 404'd and the
widgets fell back to em-dashes. Computed live from real collections
where cheap; empty/zero envelopes otherwise (the widgets fail-soft).

Mounted FIRST at /api/v1 so these exact paths resolve before any
domain router's /{id} catch-all could shadow them.
"""

from fastapi import APIRouter, Depends, Query
from typing import Optional, Dict, Any, List
from datetime import datetime

from ..utils.ist import ist_day_start_utc, ist_today, now_ist
from .auth import get_current_user, require_roles

# /admin/* widgets surface cross-store escalations + system status and were
# AUTHENTICATED-only (any user) -- they bypass the admin router's gate because
# they live in THIS router, mounted at bare /api/v1. Restrict to Admin
# (SUPERADMIN auto-passes). The Hub fetches widgets fail-soft, so other roles
# simply get an empty card.
_WIDGET_ADMIN_ROLES = ("ADMIN",)

# RBAC-1: the finance/* and hr/* Hub widgets surface store revenue/order counts
# and staff headcount/attendance. This router (finance_ticker_router) is mounted
# at bare /api/v1 and at /api/v1/finance WITHOUT the _FINANCE_ROLES gate, so the
# handlers below were protected ONLY by the request-time RBAC middleware + the
# frontend hiding the cards -- frontend-hiding is not protection. Gate each
# handler with its own require_roles (defense-in-depth) so floor roles
# (SALES_STAFF, CASHIER, WORKSHOP_STAFF, OPTOMETRIST) get a hard 403 even if the
# middleware is ever disabled/reordered. Mirrors the policy registry rows for
# these routes and the owner-digest route above. SUPERADMIN auto-passes.
_WIDGET_FINANCE_HR_ROLES = ("ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER")

router = APIRouter()


def _coll(name: str):
    try:
        from database.connection import get_db

        db = get_db()
        if db and db.is_connected:
            return db.get_collection(name)
    except Exception:
        pass
    return None


def _store(user: dict, override: Optional[str]) -> Optional[str]:
    roles = set(user.get("roles") or [])
    if {"SUPERADMIN", "ADMIN", "AREA_MANAGER"} & roles:
        return override or user.get("active_store_id")
    return user.get("active_store_id") or override


def _month_start() -> datetime:
    """First instant of the current IST month, in the naive-UTC frame
    `created_at` is stored in. The old "%Y-%m-01" STRING never matched a
    BSON Date (Mongo type bracketing) -- the widgets always read 0."""
    return ist_day_start_utc(ist_today().replace(day=1))


def _today_start() -> datetime:
    """IST midnight today as a naive-UTC instant (for `created_at` bounds)."""
    return ist_day_start_utc()


def _today() -> str:
    """IST calendar-day string, for date-keyed STRING fields (attendance.date,
    eye_test_queue.created_date) -- NOT for `created_at` instants."""
    return ist_today().isoformat()


_COUNTED = {"CONFIRMED", "PROCESSING", "READY", "DELIVERED"}


# ── Tasks ──────────────────────────────────────────────────────────────────


@router.get("/tasks/completion-stats")
async def tasks_completion_stats(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    coll = _coll("tasks")
    sid = _store(current_user, store_id)
    if coll is None:
        return {"open": 0, "in_progress": 0, "completed": 0, "completion_rate": 0}
    base = {"store_id": sid} if sid else {}
    open_c = coll.count_documents({**base, "status": "open"})
    inprog = coll.count_documents({**base, "status": "in_progress"})
    done = coll.count_documents({**base, "status": "completed"})
    total = open_c + inprog + done
    return {
        "open": open_c,
        "in_progress": inprog,
        "completed": done,
        "completion_rate": round(done / total * 100, 1) if total else 0,
    }


@router.get("/tasks/escalations")
async def tasks_escalations(
    status: Optional[str] = Query(None),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    coll = _coll("tasks")
    sid = _store(current_user, store_id)
    if coll is None:
        return {"escalations": [], "count": 0}
    q: Dict[str, Any] = {"status": "escalated"}
    if sid:
        q["store_id"] = sid
    rows = [{k: v for k, v in r.items() if k != "_id"} for r in coll.find(q).limit(20)]
    return {"escalations": rows, "count": len(rows)}


# ── Clinical ────────────────────────────────────────────────────────────────


@router.get("/clinical/patient-queue")
async def clinical_patient_queue(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    coll = _coll("eye_test_queue")
    sid = _store(current_user, store_id)
    if coll is None:
        return {"waiting": 0, "in_progress": 0, "queue": []}
    q = (
        {"store_id": sid, "created_date": _today()}
        if sid
        else {"created_date": _today()}
    )
    waiting = coll.count_documents({**q, "status": "WAITING"})
    inprog = coll.count_documents({**q, "status": "IN_PROGRESS"})
    return {"waiting": waiting, "in_progress": inprog, "queue": []}


@router.get("/clinical/eye-tests")
async def clinical_eye_tests(
    status: Optional[str] = Query(None),
    date: Optional[str] = Query(None),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    coll = _coll("eye_tests")
    sid = _store(current_user, store_id)
    if coll is None:
        return {"tests": [], "count": 0}
    q: Dict[str, Any] = {}
    if sid:
        q["store_id"] = sid
    if status:
        q["status"] = status.upper()
    if date == "today":
        q["test_date"] = _today()
    cnt = coll.count_documents(q)
    return {"tests": [], "count": cnt}


@router.get("/clinical/prescription-redo-rate")
async def clinical_redo_rate(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    # No redo tracking field yet — honest zero envelope.
    return {"redo_rate": 0, "total_prescriptions": 0, "redos": 0}


# ── Finance ─────────────────────────────────────────────────────────────────


@router.get("/finance/summary-month")
async def finance_summary_month(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_WIDGET_FINANCE_HR_ROLES)),
):
    orders = _coll("orders")
    sid = _store(current_user, store_id)
    revenue = 0.0
    count = 0
    if orders is not None:
        q: Dict[str, Any] = {"created_at": {"$gte": _month_start()}}
        if sid:
            q["store_id"] = sid
        for o in orders.find(q):
            if (o.get("status") or "").upper() in _COUNTED:
                revenue += float(o.get("grand_total") or 0)
                count += 1
    return {"revenue_month": round(revenue, 2), "orders_month": count}


@router.get("/finance/gst-status")
async def finance_gst_status(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_WIDGET_FINANCE_HR_ROLES)),
):
    return {
        "filed": False,
        "period": now_ist().strftime("%Y-%m"),
        "pending_returns": [],
    }


@router.get("/finance/pending-reconciliations")
async def finance_pending_reconciliations(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_WIDGET_FINANCE_HR_ROLES)),
):
    return {"pending": 0, "items": []}


# ── HR ──────────────────────────────────────────────────────────────────────


@router.get("/hr/summary-today")
async def hr_summary_today(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_WIDGET_FINANCE_HR_ROLES)),
):
    users = _coll("users")
    sid = _store(current_user, store_id)
    total = 0
    if users is not None:
        q: Dict[str, Any] = {"is_active": {"$ne": False}}
        if sid:
            q["$or"] = [
                {"store_ids": sid},
                {"home_store_id": sid},
                {"active_store_id": sid},
            ]
        total = users.count_documents(q)
    att = _coll("attendance")
    present = 0
    if att is not None:
        aq: Dict[str, Any] = {"date": _today(), "status": "present"}
        if sid:
            aq["store_id"] = sid
        present = att.count_documents(aq)
    return {
        "total_staff": total,
        "present_today": present,
        "on_leave": max(0, total - present),
    }


@router.get("/hr/attendance-compliance")
async def hr_attendance_compliance(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_WIDGET_FINANCE_HR_ROLES)),
):
    return {"compliance_rate": 0, "late_arrivals": 0, "absent": 0}


# ── Inventory ────────────────────────────────────────────────────────────────


@router.get("/inventory/stock-count-status")
async def inventory_stock_count_status(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    products = _coll("products")
    low = oos = total = 0
    if products is not None:
        for p in products.find({}):
            if p.get("is_active") is False:
                continue
            total += 1
            qty = int(p.get("stock_quantity") or p.get("quantity") or 0)
            rp = int(p.get("reorder_point") or 0)
            if qty <= 0:
                oos += 1
            elif rp and qty <= rp:
                low += 1
    return {"total_products": total, "low_stock": low, "out_of_stock": oos}


# ── Owner digest (SUPERADMIN / ADMIN — the day-close snapshot, in the Hub) ────


@router.get("/admin/owner-digest")
async def owner_digest(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_WIDGET_ADMIN_ROLES)),
):
    """Day-close owner digest for SUPERADMIN / ADMIN, surfaced IN the Hub (not
    over WhatsApp): today's sales, collections, expenses, cash-net, orders, new
    customers, pending tasks, and low / out-of-stock -- plus a month-to-date
    snapshot and (for the EXPANDED view) per-store sales, the payment-mode split,
    the low-stock + pending-task lists, and staff presence.

    store_id None = ALL stores (the owner default). Orders/expenses/customers are
    filtered on the canonical `created_at` datetime; attendance uses the date-only
    string `date` (matching hr/summary-today). One pass over the month's orders
    derives both MTD and today (today is the tail of the month). Fail-soft: each
    metric degrades to 0 / [] rather than erroring the card.
    """
    # IST business day; bounds are naive-UTC instants of the IST day/month
    # start (the frame `created_at` is stored in), not the UTC box date.
    t = ist_today()
    today_start = ist_day_start_utc(t)
    month_start = ist_day_start_utc(t.replace(day=1))
    excluded = {"CANCELLED", "DRAFT", "REFUNDED"}

    sales_today = collections_today = sales_mtd = 0.0
    orders_today = orders_mtd = 0
    by_store: Dict[str, Dict[str, float]] = {}
    pay_modes: Dict[str, float] = {}

    orders = _coll("orders")
    if orders is not None:
        q: Dict[str, Any] = {"created_at": {"$gte": month_start}}
        if store_id:
            q["store_id"] = store_id
        for o in orders.find(q):
            if (o.get("status") or "").upper() in excluded:
                continue
            gt = float(o.get("grand_total") or 0)
            paid = float(o.get("amount_paid") or 0)
            sales_mtd += gt
            orders_mtd += 1
            cdt = o.get("created_at")
            if isinstance(cdt, datetime) and cdt >= today_start:
                sales_today += gt
                collections_today += paid
                orders_today += 1
                sid = o.get("store_id") or "?"
                bucket = by_store.setdefault(sid, {"sales": 0.0, "orders": 0.0})
                bucket["sales"] += gt
                bucket["orders"] += 1
                pays = o.get("payments")
                if isinstance(pays, list) and pays:
                    for p in pays:
                        pp = p or {}
                        mode = str(
                            pp.get("method") or pp.get("payment_method") or "OTHER"
                        ).upper()
                        pay_modes[mode] = pay_modes.get(mode, 0.0) + float(
                            pp.get("amount") or 0
                        )
                elif o.get("payment_method"):
                    mode = str(o.get("payment_method")).upper()
                    pay_modes[mode] = pay_modes.get(mode, 0.0) + paid

    expenses_today = expenses_mtd = 0.0
    exp = _coll("expenses")
    if exp is not None:
        eq: Dict[str, Any] = {"created_at": {"$gte": month_start}}
        if store_id:
            eq["store_id"] = store_id
        for e in exp.find(eq):
            amt = float(e.get("amount") or e.get("amount_inr") or e.get("total") or 0)
            expenses_mtd += amt
            cdt = e.get("created_at")
            if isinstance(cdt, datetime) and cdt >= today_start:
                expenses_today += amt

    new_customers = 0
    custs = _coll("customers")
    if custs is not None:
        cq: Dict[str, Any] = {"created_at": {"$gte": today_start}}
        if store_id:
            cq["$or"] = [{"store_id": store_id}, {"store_ids": store_id}]
        try:
            new_customers = custs.count_documents(cq)
        except Exception:
            new_customers = 0

    pending_tasks = 0
    task_list: List[Dict[str, Any]] = []
    tasks = _coll("tasks")
    if tasks is not None:
        tq: Dict[str, Any] = {"status": {"$in": ["open", "in_progress"]}}
        if store_id:
            tq["store_id"] = store_id
        try:
            pending_tasks = tasks.count_documents(tq)
            for r in tasks.find(tq).limit(10):
                task_list.append(
                    {
                        "title": r.get("title") or "Task",
                        "priority": r.get("priority") or "P3",
                        "status": r.get("status"),
                        "due_at": r.get("due_at"),
                        "store_id": r.get("store_id"),
                    }
                )
        except Exception:
            pass

    low = oos = 0
    low_items: List[Dict[str, Any]] = []
    products = _coll("products")
    if products is not None:
        pq: Dict[str, Any] = {}
        if store_id:
            pq["store_id"] = store_id
        for p in products.find(pq):
            if p.get("is_active") is False:
                continue
            qty = int(p.get("stock_quantity") or p.get("quantity") or 0)
            rp = int(p.get("reorder_point") or 0)
            is_low = qty <= 0 or (rp and qty <= rp)
            if qty <= 0:
                oos += 1
            elif rp and qty <= rp:
                low += 1
            if is_low and len(low_items) < 10:
                low_items.append(
                    {
                        "name": p.get("name") or p.get("title") or p.get("sku"),
                        "sku": p.get("sku"),
                        "qty": qty,
                        "reorder_point": rp,
                        "store_id": p.get("store_id"),
                    }
                )

    total_staff = present_today = 0
    users = _coll("users")
    if users is not None:
        uq: Dict[str, Any] = {"is_active": {"$ne": False}}
        if store_id:
            uq["$or"] = [
                {"store_ids": store_id},
                {"home_store_id": store_id},
                {"active_store_id": store_id},
            ]
        try:
            total_staff = users.count_documents(uq)
        except Exception:
            total_staff = 0
    att = _coll("attendance")
    if att is not None:
        aq: Dict[str, Any] = {"date": _today(), "status": "present"}
        if store_id:
            aq["store_id"] = store_id
        try:
            present_today = att.count_documents(aq)
        except Exception:
            present_today = 0

    # backlog #4: resolve per-store ids -> names for the by_store digest rows.
    _store_names: Dict[str, str] = {}
    try:
        from database.connection import get_db
        from ..services.name_resolver import store_name_map

        _store_names = store_name_map(get_db(), list(by_store.keys()))
    except Exception:
        _store_names = {}

    return {
        "date": t.isoformat(),
        "store_id": store_id,
        "today": {
            "sales": round(sales_today, 2),
            "collections": round(collections_today, 2),
            "expenses": round(expenses_today, 2),
            "cash_net": round(collections_today - expenses_today, 2),
            "orders": orders_today,
            "new_customers": new_customers,
            "pending_tasks": pending_tasks,
            "low_stock": low,
            "out_of_stock": oos,
        },
        "mtd": {
            "sales": round(sales_mtd, 2),
            "expenses": round(expenses_mtd, 2),
            "orders": orders_mtd,
        },
        "expanded": {
            "by_store": [
                {
                    "store_id": k,
                    "store_name": _store_names.get(str(k), k),
                    "sales": round(v["sales"], 2),
                    "orders": int(v["orders"]),
                }
                for k, v in sorted(by_store.items(), key=lambda kv: -kv[1]["sales"])
            ],
            "payment_modes": {
                k: round(v, 2)
                for k, v in sorted(pay_modes.items(), key=lambda kv: -kv[1])
            },
            "low_stock_items": low_items,
            "pending_task_list": task_list,
            "staff": {"present_today": present_today, "total_staff": total_staff},
        },
    }


# ── Catalog ──────────────────────────────────────────────────────────────────


@router.get("/catalog/sku-counts")
async def catalog_sku_counts(current_user: dict = Depends(get_current_user)):
    products = _coll("products")
    if products is None:
        return {"total": 0, "active": 0, "inactive": 0}
    total = products.count_documents({})
    active = products.count_documents({"is_active": {"$ne": False}})
    return {"total": total, "active": active, "inactive": max(0, total - active)}


@router.get("/catalog/recent-activity")
async def catalog_recent_activity(
    limit: int = Query(5, ge=1, le=50), current_user: dict = Depends(get_current_user)
):
    products = _coll("products")
    if products is None:
        return {"activity": []}
    rows = list(products.find({}).sort("updated_at", -1).limit(limit))
    return {
        "activity": [
            {
                "name": r.get("name") or r.get("product_name"),
                "sku": r.get("sku"),
                "updated_at": r.get("updated_at"),
            }
            for r in rows
        ]
    }


@router.get("/catalog/price-change-requests")
async def catalog_price_change_requests(
    status: Optional[str] = Query(None), current_user: dict = Depends(get_current_user)
):
    coll = _coll("price_change_requests")
    if coll is None:
        return {"requests": [], "count": 0}
    q = {"status": status} if status else {}
    rows = [{k: v for k, v in r.items() if k != "_id"} for r in coll.find(q).limit(20)]
    return {"requests": rows, "count": len(rows)}


# ── Analytics ────────────────────────────────────────────────────────────────


@router.get("/analytics/store-target-today")
async def analytics_store_target_today(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    orders = _coll("orders")
    sid = _store(current_user, store_id)
    achieved = 0.0
    if orders is not None:
        q: Dict[str, Any] = {"created_at": {"$gte": _today_start()}}
        if sid:
            q["store_id"] = sid
        for o in orders.find(q):
            if (o.get("status") or "").upper() in _COUNTED:
                achieved += float(o.get("grand_total") or 0)
    return {"target": 0, "achieved_today": round(achieved, 2), "achievement_percent": 0}


# ── Admin ────────────────────────────────────────────────────────────────────


@router.get("/admin/escalations")
async def admin_escalations(
    level: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_WIDGET_ADMIN_ROLES)),
):
    coll = _coll("tasks")
    if coll is None:
        return {"escalations": [], "count": 0}
    rows = [
        {k: v for k, v in r.items() if k != "_id"}
        for r in coll.find({"status": "escalated"}).limit(20)
    ]
    return {"escalations": rows, "count": len(rows)}


@router.get("/admin/system-health")
async def admin_system_health(
    current_user: dict = Depends(require_roles(*_WIDGET_ADMIN_ROLES)),
):
    from database.connection import get_db

    status = {"api": "healthy", "checked_at": datetime.now().isoformat()}
    try:
        db = get_db()
        status["database"] = "connected" if (db and db.is_connected) else "disconnected"
    except Exception:
        status["database"] = "error"
    return status

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
from typing import Optional, Dict, Any
from datetime import datetime

from .auth import get_current_user

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


def _month_start() -> str:
    return datetime.now().strftime("%Y-%m-01")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


_COUNTED = {"CONFIRMED", "PROCESSING", "READY", "DELIVERED"}


# ── Tasks ──────────────────────────────────────────────────────────────────

@router.get("/tasks/completion-stats")
async def tasks_completion_stats(
    store_id: Optional[str] = Query(None), current_user: dict = Depends(get_current_user)
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
        "open": open_c, "in_progress": inprog, "completed": done,
        "completion_rate": round(done / total * 100, 1) if total else 0,
    }


@router.get("/tasks/escalations")
async def tasks_escalations(
    status: Optional[str] = Query(None),
    store_id: Optional[str] = Query(None), current_user: dict = Depends(get_current_user)
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
    store_id: Optional[str] = Query(None), current_user: dict = Depends(get_current_user)
):
    coll = _coll("eye_test_queue")
    sid = _store(current_user, store_id)
    if coll is None:
        return {"waiting": 0, "in_progress": 0, "queue": []}
    q = {"store_id": sid, "created_date": _today()} if sid else {"created_date": _today()}
    waiting = coll.count_documents({**q, "status": "WAITING"})
    inprog = coll.count_documents({**q, "status": "IN_PROGRESS"})
    return {"waiting": waiting, "in_progress": inprog, "queue": []}


@router.get("/clinical/eye-tests")
async def clinical_eye_tests(
    status: Optional[str] = Query(None), date: Optional[str] = Query(None),
    store_id: Optional[str] = Query(None), current_user: dict = Depends(get_current_user)
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
    store_id: Optional[str] = Query(None), current_user: dict = Depends(get_current_user)
):
    # No redo tracking field yet — honest zero envelope.
    return {"redo_rate": 0, "total_prescriptions": 0, "redos": 0}


# ── Finance ─────────────────────────────────────────────────────────────────

@router.get("/finance/summary-month")
async def finance_summary_month(
    store_id: Optional[str] = Query(None), current_user: dict = Depends(get_current_user)
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
    store_id: Optional[str] = Query(None), current_user: dict = Depends(get_current_user)
):
    return {"filed": False, "period": datetime.now().strftime("%Y-%m"), "pending_returns": []}


@router.get("/finance/pending-reconciliations")
async def finance_pending_reconciliations(
    store_id: Optional[str] = Query(None), current_user: dict = Depends(get_current_user)
):
    return {"pending": 0, "items": []}


# ── HR ──────────────────────────────────────────────────────────────────────

@router.get("/hr/summary-today")
async def hr_summary_today(
    store_id: Optional[str] = Query(None), current_user: dict = Depends(get_current_user)
):
    users = _coll("users")
    sid = _store(current_user, store_id)
    total = 0
    if users is not None:
        q: Dict[str, Any] = {"is_active": {"$ne": False}}
        if sid:
            q["$or"] = [{"store_ids": sid}, {"home_store_id": sid}, {"active_store_id": sid}]
        total = users.count_documents(q)
    att = _coll("attendance")
    present = 0
    if att is not None:
        aq: Dict[str, Any] = {"date": _today(), "status": "present"}
        if sid:
            aq["store_id"] = sid
        present = att.count_documents(aq)
    return {"total_staff": total, "present_today": present, "on_leave": max(0, total - present)}


@router.get("/hr/attendance-compliance")
async def hr_attendance_compliance(
    store_id: Optional[str] = Query(None), current_user: dict = Depends(get_current_user)
):
    return {"compliance_rate": 0, "late_arrivals": 0, "absent": 0}


# ── Inventory ────────────────────────────────────────────────────────────────

@router.get("/inventory/stock-count-status")
async def inventory_stock_count_status(
    store_id: Optional[str] = Query(None), current_user: dict = Depends(get_current_user)
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
    return {"activity": [
        {"name": r.get("name") or r.get("product_name"), "sku": r.get("sku"),
         "updated_at": r.get("updated_at")} for r in rows
    ]}


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
    store_id: Optional[str] = Query(None), current_user: dict = Depends(get_current_user)
):
    orders = _coll("orders")
    sid = _store(current_user, store_id)
    achieved = 0.0
    if orders is not None:
        q: Dict[str, Any] = {"created_at": {"$gte": _today()}}
        if sid:
            q["store_id"] = sid
        for o in orders.find(q):
            if (o.get("status") or "").upper() in _COUNTED:
                achieved += float(o.get("grand_total") or 0)
    return {"target": 0, "achieved_today": round(achieved, 2), "achievement_percent": 0}


# ── Admin ────────────────────────────────────────────────────────────────────

@router.get("/admin/escalations")
async def admin_escalations(
    level: Optional[str] = Query(None), current_user: dict = Depends(get_current_user)
):
    coll = _coll("tasks")
    if coll is None:
        return {"escalations": [], "count": 0}
    rows = [{k: v for k, v in r.items() if k != "_id"}
            for r in coll.find({"status": "escalated"}).limit(20)]
    return {"escalations": rows, "count": len(rows)}


@router.get("/admin/system-health")
async def admin_system_health(current_user: dict = Depends(get_current_user)):
    from database.connection import get_db
    status = {"api": "healthy", "checked_at": datetime.now().isoformat()}
    try:
        db = get_db()
        status["database"] = "connected" if (db and db.is_connected) else "disconnected"
    except Exception:
        status["database"] = "error"
    return status

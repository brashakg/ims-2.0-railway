"""
IMS 2.0 - HR Employee Self-Service Router
=========================================
Own-data reads for ANY authenticated employee (floor staff included).

Why this is a SEPARATE router (not part of hr.py / payroll.py):
    The ``/api/v1/hr`` and ``/api/v1/payroll`` routers are mounted in main.py
    behind a router-level finance-role gate
    (``dependencies=[Depends(require_roles(*_FINANCE_ROLES))]`` where
    _FINANCE_ROLES = ADMIN/AREA_MANAGER/STORE_MANAGER/ACCOUNTANT). That gate
    means a Sales Staff / Cashier / Optometrist / Workshop role gets a 403 on
    EVERY route under those prefixes -- including the existing "self-read"
    payslip / commission endpoints. So floor staff had no way to see their own
    attendance, payslip, commission or leaves.

    This router is mounted at the SAME ``/api/v1/hr`` prefix but WITHOUT the
    finance gate (mirroring how ``roster_router`` is mounted separately in
    main.py). Every endpoint is pinned to the REQUESTING user (current_user) --
    there is NO ``employee_id`` parameter, so a staff member can only ever read
    their own data. No privilege-escalation surface.

All endpoints are fail-soft: no DB / no records => a valid empty-but-shaped
payload, never a 500.
"""

from fastapi import APIRouter, Depends, Query
from typing import Optional
from datetime import datetime
from calendar import monthrange
import logging

from .auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_db():
    """Shared DB-handle helper (CLAUDE.md convention). None => DB not connected."""
    try:
        from database.connection import get_db

        return get_db().db
    except Exception:
        return None


def _caller_id(current_user: dict) -> Optional[str]:
    return current_user.get("user_id") or current_user.get("id")


def _month_year(month: Optional[int], year: Optional[int]) -> tuple:
    """Resolve (month, year), defaulting to the current calendar month."""
    now = datetime.now()
    m = month if (month and 1 <= month <= 12) else now.month
    y = year if (year and 2020 <= year <= 2100) else now.year
    return m, y


# ============================================================================
# OWN ATTENDANCE
# ============================================================================


@router.get("/me/attendance")
async def my_attendance(
    month: Optional[int] = Query(None, ge=1, le=12),
    year: Optional[int] = Query(None, ge=2020, le=2100),
    current_user: dict = Depends(get_current_user),
):
    """The requesting employee's OWN attendance for one calendar month.

    Returns per-day status codes plus rolled-up counts (present / absent /
    half_day / leave / late). Reads the ``attendance`` collection filtered to
    the caller's employee_id -- never another employee's. Fail-soft.
    """
    mon, yr = _month_year(month, year)
    caller = _caller_id(current_user)
    empty = {
        "month": mon,
        "year": yr,
        "days": {},
        "summary": {
            "present": 0,
            "absent": 0,
            "half_day": 0,
            "leave": 0,
            "holiday": 0,
            "week_off": 0,
            "lwp": 0,
            "late": 0,
        },
    }
    db = _get_db()
    if db is None or not caller:
        return empty

    try:
        n_days = monthrange(yr, mon)[1]
        start = f"{yr:04d}-{mon:02d}-01"
        end = f"{yr:04d}-{mon:02d}-{n_days:02d}"
        records = list(
            db.get_collection("attendance").find(
                {"employee_id": caller, "date": {"$gte": start, "$lte": end}}
            ).limit(400)
        )

        # Code map mirrors the HR grid (P/A/HD/L/LWP/WO).
        code_for = {
            "PRESENT": "P",
            "ABSENT": "A",
            "HALF_DAY": "HD",
            "LEAVE": "L",
            "LWP": "LWP",
            "HOLIDAY": "WO",
            "WEEK_OFF": "WO",
        }
        days = {}
        summary = dict(empty["summary"])
        for r in records:
            d = str(r.get("date", ""))[:10]
            status = (r.get("status") or "").upper()
            if d:
                days[d] = code_for.get(status, "-")
            if status == "PRESENT":
                summary["present"] += 1
            elif status == "ABSENT":
                summary["absent"] += 1
            elif status == "HALF_DAY":
                summary["half_day"] += 1
            elif status == "LEAVE":
                summary["leave"] += 1
            elif status == "LWP":
                summary["lwp"] += 1
            elif status in ("HOLIDAY", "WEEK_OFF"):
                summary["week_off"] += 1
            if r.get("is_late"):
                summary["late"] += 1

        return {"month": mon, "year": yr, "days": days, "summary": summary}
    except Exception as e:
        logger.error("Self attendance read failed: %s", e)
        return empty


# ============================================================================
# OWN LEAVES + BALANCE
# ============================================================================


@router.get("/me/leaves")
async def my_leaves(
    year: Optional[int] = Query(None, ge=2020, le=2100),
    current_user: dict = Depends(get_current_user),
):
    """The requesting employee's OWN leave requests for a calendar year, plus a
    rolled-up balance summary (approved / pending day counts by leave type).

    Fills the gap left by ``/hr/leaves/balance/{employee_id}`` (a stub that
    returns an empty balance). Self-only: filters the ``leaves`` collection to
    the caller's employee_id. Fail-soft.
    """
    _, yr = _month_year(None, year)
    caller = _caller_id(current_user)
    empty = {
        "year": yr,
        "leaves": [],
        "summary": {"approved_days": 0, "pending_days": 0, "by_type": {}},
    }
    db = _get_db()
    if db is None or not caller:
        return empty

    try:
        docs = list(
            db.get_collection("leaves").find({"employee_id": caller}).limit(500)
        )

        def _day_count(frm, to):
            try:
                f = datetime.fromisoformat(str(frm)[:10])
                t = datetime.fromisoformat(str(to or frm)[:10])
                d = (t - f).days + 1
                return d if d > 0 else 1
            except Exception:
                return 1

        leaves_out = []
        approved_days = 0
        pending_days = 0
        by_type = {}
        for d in docs:
            frm = d.get("from_date")
            to = d.get("to_date") or frm
            if not frm:
                continue
            # Year filter on the leave start date.
            try:
                if datetime.fromisoformat(str(frm)[:10]).year != yr:
                    continue
            except Exception:
                continue
            status = (d.get("status") or "").upper()
            days = _day_count(frm, to)
            ltype = (d.get("leave_type") or "OTHER").upper()
            if status == "APPROVED":
                approved_days += days
                by_type[ltype] = by_type.get(ltype, 0) + days
            elif status == "PENDING":
                pending_days += days
            leaves_out.append(
                {
                    "leave_id": d.get("leave_id", ""),
                    "leave_type": ltype,
                    "from_date": str(frm)[:10],
                    "to_date": str(to)[:10],
                    "days": days,
                    "status": status,
                    "reason": d.get("reason", ""),
                    "applied_at": str(d.get("applied_at", ""))[:10],
                }
            )

        leaves_out.sort(key=lambda x: x["from_date"], reverse=True)
        return {
            "year": yr,
            "leaves": leaves_out,
            "summary": {
                "approved_days": approved_days,
                "pending_days": pending_days,
                "by_type": by_type,
            },
        }
    except Exception as e:
        logger.error("Self leaves read failed: %s", e)
        return empty


# ============================================================================
# OWN LATEST PAYSLIP
# ============================================================================


@router.get("/me/payslip")
async def my_latest_payslip(current_user: dict = Depends(get_current_user)):
    """The requesting employee's OWN most-recent payslip.

    Reuses the payroll module's payslip builder so the shape exactly matches
    GET /payroll/payslip/{id}. Searches the ``payslips`` cache first, then the
    run-engine ``payroll`` collection. Self-only. Fail-soft.
    """
    caller = _caller_id(current_user)
    db = _get_db()
    if db is None or not caller:
        return {"payslip": None}

    try:
        # Reuse payroll helpers (same collections + shape as /payroll/payslip).
        from .payroll import (
            _build_payslip_from_run_row,
            _get_employee_details,
            _strip_id,
        )

        payslips_coll = db.get_collection("payslips")
        cached = list(
            payslips_coll.find({"employee_id": caller})
            .sort([("year", -1), ("month", -1)])
            .limit(1)
        )
        if cached:
            return {"payslip": _strip_id(cached[0])}

        run_row = (
            list(
                db.get_collection("payroll")
                .find({"employee_id": caller})
                .sort([("year", -1), ("month", -1)])
                .limit(1)
            )
            or [None]
        )[0]
        if not run_row:
            return {"payslip": None}

        employee = _get_employee_details(db, caller)
        payslip = _build_payslip_from_run_row(run_row, employee, db)
        try:
            payslips_coll.insert_one({**payslip})
        except Exception:
            pass
        return {"payslip": _strip_id(payslip)}
    except Exception as e:
        logger.error("Self payslip read failed: %s", e)
        return {"payslip": None}


# ============================================================================
# OWN COMMISSION (this month)
# ============================================================================


@router.get("/me/commission")
async def my_commission(
    month: Optional[int] = Query(None, ge=1, le=12),
    year: Optional[int] = Query(None, ge=2020, le=2100),
    current_user: dict = Depends(get_current_user),
):
    """The requesting employee's OWN commission for one month.

    Computed exactly like /payroll/commission/summary but pinned to the caller:
    revenue of orders they sold * their salary_config commission rate. Self-only.
    Fail-soft.
    """
    mon, yr = _month_year(month, year)
    caller = _caller_id(current_user)
    empty = {
        "month": mon,
        "year": yr,
        "sales_count": 0,
        "revenue": 0.0,
        "commission_rate_percent": 0.0,
        "commission_amount": 0.0,
    }
    db = _get_db()
    if db is None or not caller:
        return empty

    try:
        days_in_month = monthrange(yr, mon)[1]
        from_dt = datetime(yr, mon, 1)
        to_dt = datetime(yr, mon, days_in_month, 23, 59, 59)
        store_id = current_user.get("active_store_id")

        order_query = {
            "status": {"$in": ["COMPLETED", "DELIVERED", "PAID"]},
            "created_at": {"$gte": from_dt, "$lte": to_dt},
            "$or": [{"sales_staff_id": caller}, {"created_by": caller}],
        }
        if store_id:
            order_query["store_id"] = store_id

        orders = list(db.get_collection("orders").find(order_query).limit(20000))
        sales_count = len(orders)
        revenue = sum(
            float(o.get("total_amount") or o.get("grand_total") or 0) for o in orders
        )

        rate = 0.0
        cfg = db.get_collection("salary_config").find_one(
            {"employee_id": caller}, {"commission_rate_percent": 1}
        )
        if cfg:
            rate = float(cfg.get("commission_rate_percent") or 0)

        return {
            "month": mon,
            "year": yr,
            "sales_count": sales_count,
            "revenue": round(revenue, 2),
            "commission_rate_percent": rate,
            "commission_amount": round(revenue * rate / 100, 2),
        }
    except Exception as e:
        logger.error("Self commission read failed: %s", e)
        return empty

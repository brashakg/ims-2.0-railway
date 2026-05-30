"""
IMS 2.0 - Budgets Router (dual-mode: planned vs actual)
=======================================================
Per-store, per-period (YYYY-MM), per-head budgeting.

A "head" is either:
  * REVENUE          -> the income target for the month (compared against
                        order revenue), or
  * an expense category (e.g. "rent", "marketing", "salaries") -> compared
                        against APPROVED expenses in that category.

The planned amounts are user-entered and persisted in the `budgets`
collection (one document per (store_id, period, head); upserted). The
ACTUALS are NOT stored - they are derived on demand by REUSING the proven
aggregation logic already in reports.py / expenses.py so the variance view
always matches what those modules report:

  * revenue actual  = reports._orders_in_window + reports._order_revenue
                      (non-CANCELLED/non-DRAFT orders for the store in the
                       month; grand_total -> final_amount -> total_amount
                       fallback).
  * expense actuals = sum of APPROVED expenses grouped by `category` for the
                      store in the month (expense_date ISO-string prefix
                      ^YYYY-MM, mirroring expenses.py's expense_date + status
                      conventions).

Fail-soft contract: no DB -> empty list / zeroed variance, never a 500.
"""

import re
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .auth import require_roles
from ..dependencies import (
    get_db,
    get_order_repository,
    get_expense_repository,
    validate_store_access,
)

# Reuse the proven order-revenue aggregation from reports.py verbatim so the
# budget variance can never drift from what the Reports module shows.
from . import reports as _reports

router = APIRouter()

# Manager+ roles allowed to view/edit budgets. Mirrors reports.py's
# _REPORT_FINANCE_ROLES (SUPERADMIN auto-passes inside require_roles).
_BUDGET_ROLES = ("ADMIN", "AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT")

_BUDGETS_COLLECTION = "budgets"

# The reserved head that means "income target" rather than an expense head.
REVENUE_HEAD = "REVENUE"

# YYYY-MM (e.g. 2026-05). Strict: 4-digit year, 01-12 month.
_PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

# Expense statuses that count as a real, committed outflow for ACTUALS.
# The task asks for APPROVED expenses specifically.
_ACTUAL_EXPENSE_STATUSES = ("APPROVED",)


# ============================================================================
# Models
# ============================================================================


class BudgetUpsert(BaseModel):
    """Upsert the planned amount for one (store, period, head)."""

    store_id: Optional[str] = None
    period: str = Field(..., description="Budget period as YYYY-MM")
    head: str = Field(..., min_length=1, description="REVENUE or an expense category")
    planned_amount: float = Field(..., ge=0)


# ============================================================================
# Helpers
# ============================================================================


def _budgets_collection():
    """Return the `budgets` collection, or None when the DB is unavailable.

    Tolerates both the get_collection() wrapper and attribute access, matching
    the fail-soft pattern used elsewhere (expenses.py::_caps_collection).
    """
    db = get_db()
    if db is None or not getattr(db, "is_connected", False):
        return None
    getter = getattr(db, "get_collection", None)
    if callable(getter):
        try:
            return getter(_BUDGETS_COLLECTION)
        except Exception:
            return None
    try:
        return db[_BUDGETS_COLLECTION]
    except Exception:
        return None


def _validate_period(period: str) -> str:
    """Validate YYYY-MM or raise 400. Returns the period unchanged."""
    if not period or not _PERIOD_RE.match(period):
        raise HTTPException(
            status_code=400,
            detail="period must be in YYYY-MM format (e.g. 2026-05)",
        )
    return period


def _month_window(period: str):
    """(start_dt, end_dt) datetimes spanning the whole month of `period`.

    Mirrors the inclusive [month-start, month-end] window reports.py builds for
    its month aggregations so the revenue actual lines up exactly.
    """
    year = int(period[:4])
    month = int(period[5:7])
    start_dt = datetime(year, month, 1)
    if month == 12:
        end_dt = datetime(year + 1, 1, 1) - timedelta(seconds=1)
    else:
        end_dt = datetime(year, month + 1, 1) - timedelta(seconds=1)
    return start_dt, end_dt


def _revenue_actual(store_id: Optional[str], period: str) -> float:
    """Order revenue for the store in the month.

    REUSES reports._orders_in_window (non-CANCELLED/non-DRAFT date-window
    filter) + reports._order_revenue (grand_total -> final_amount ->
    total_amount fallback). Fail-soft -> 0.0.
    """
    order_repo = get_order_repository()
    if order_repo is None:
        return 0.0
    start_dt, end_dt = _month_window(period)
    try:
        orders = _reports._orders_in_window(
            order_repo, store_id=store_id, start_dt=start_dt, end_dt=end_dt
        )
        return float(sum(_reports._order_revenue(o) for o in orders))
    except Exception:
        return 0.0


def _expense_actuals_by_category(
    store_id: Optional[str], period: str
) -> Dict[str, float]:
    """{category: total} of APPROVED expenses for the store in the month.

    Mirrors expenses.py conventions: `expense_date` is an ISO string filtered
    by the ^YYYY-MM prefix, `status` is APPROVED, grouped by `category`,
    summing `amount`. Fail-soft -> {}.
    """
    repo = get_expense_repository()
    if repo is None:
        return {}
    flt: Dict[str, Any] = {
        # expense_date is stored as an ISO string -> month prefix match.
        "expense_date": {"$regex": f"^{re.escape(period)}"},
        "status": {"$in": list(_ACTUAL_EXPENSE_STATUSES)},
    }
    if store_id:
        flt["store_id"] = store_id
    out: Dict[str, float] = {}
    try:
        rows = repo.find_many(flt, limit=100000) or []
    except Exception:
        return {}
    for row in rows:
        cat = row.get("category") or "Uncategorized"
        amt = row.get("amount")
        try:
            amt = float(amt)
        except (TypeError, ValueError):
            amt = 0.0
        out[cat] = out.get(cat, 0.0) + amt
    return out


def _variance(planned: float, actual: float) -> float:
    return round(actual - planned, 2)


def _variance_pct(planned: float, actual: float) -> Optional[float]:
    """Percent variance relative to plan. None when there is no plan to
    measure against (planned == 0), so the client can show '-' instead of a
    misleading divide-by-zero."""
    if planned <= 0:
        return None
    return round((actual - planned) / planned * 100, 2)


def _clean_budget_doc(doc: dict) -> dict:
    """Project a stored budget doc to the API shape."""
    return {
        "budget_id": doc.get("budget_id"),
        "store_id": doc.get("store_id"),
        "period": doc.get("period"),
        "head": doc.get("head"),
        "planned_amount": float(doc.get("planned_amount") or 0.0),
    }


# ============================================================================
# Endpoints
# ============================================================================


@router.post("")
@router.post("/")
async def upsert_budget(
    payload: BudgetUpsert,
    current_user: dict = Depends(require_roles(*_BUDGET_ROLES)),
):
    """Upsert the planned amount for one (store, period, head).

    One document per (store_id, period, head) - re-posting the same triple
    overwrites the planned amount. Store-scoped via validate_store_access.
    """
    store_id = validate_store_access(payload.store_id, current_user)
    if not store_id:
        raise HTTPException(status_code=400, detail="store_id is required")
    period = _validate_period(payload.period)
    head = payload.head.strip()
    if not head:
        raise HTTPException(status_code=400, detail="head is required")

    coll = _budgets_collection()
    if coll is None:
        # Fail-soft: echo back what would have been stored.
        return {
            "budget": {
                "budget_id": None,
                "store_id": store_id,
                "period": period,
                "head": head,
                "planned_amount": float(payload.planned_amount),
            },
            "persisted": False,
        }

    key = {"store_id": store_id, "period": period, "head": head}
    now = datetime.now().isoformat()
    update = {
        "$set": {
            **key,
            "planned_amount": float(payload.planned_amount),
            "updated_at": now,
            "updated_by": current_user.get("user_id") or current_user.get("username"),
        },
        "$setOnInsert": {
            "budget_id": uuid.uuid4().hex,
            "created_at": now,
        },
    }
    try:
        coll.update_one(key, update, upsert=True)
        doc = coll.find_one(key)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Failed to save budget: {exc}")

    return {"budget": _clean_budget_doc(doc or {}), "persisted": True}


@router.get("")
@router.get("/")
async def list_budgets(
    store_id: Optional[str] = Query(None),
    period: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_BUDGET_ROLES)),
):
    """List the planned budget lines for a store + period (store-scoped)."""
    store_id = validate_store_access(store_id, current_user)
    if period:
        period = _validate_period(period)

    coll = _budgets_collection()
    if coll is None:
        return {"budgets": [], "total": 0}

    flt: Dict[str, Any] = {}
    if store_id:
        flt["store_id"] = store_id
    if period:
        flt["period"] = period
    try:
        rows = list(coll.find(flt))
    except Exception:
        return {"budgets": [], "total": 0}

    budgets = [_clean_budget_doc(r) for r in rows]
    budgets.sort(key=lambda b: ((b.get("head") != REVENUE_HEAD), b.get("head") or ""))
    return {"budgets": budgets, "total": len(budgets)}


@router.delete("/{budget_id}")
async def delete_budget(
    budget_id: str,
    current_user: dict = Depends(require_roles(*_BUDGET_ROLES)),
):
    """Remove a planned budget line (store-scoped to the caller's access)."""
    coll = _budgets_collection()
    if coll is None:
        return {"deleted": False, "budget_id": budget_id}

    try:
        doc = coll.find_one({"budget_id": budget_id})
    except Exception:
        doc = None
    if not doc:
        raise HTTPException(status_code=404, detail="Budget line not found")

    # Enforce store scope on the row being deleted.
    validate_store_access(doc.get("store_id"), current_user)

    try:
        coll.delete_one({"budget_id": budget_id})
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Failed to delete budget: {exc}")
    return {"deleted": True, "budget_id": budget_id}


@router.get("/variance")
async def budget_variance(
    store_id: Optional[str] = Query(None),
    period: str = Query(..., description="Budget period as YYYY-MM"),
    current_user: dict = Depends(require_roles(*_BUDGET_ROLES)),
):
    """Planned-vs-actual variance per head for a store + period.

    Returns one row per head with planned / actual / variance (actual-planned)
    / variance_pct, plus a totals block. Heads that have ACTUALS but no plan
    are included with planned=0 so nothing is hidden.
    """
    store_id = validate_store_access(store_id, current_user)
    period = _validate_period(period)

    # ---- planned lines for this (store, period) -------------------------
    planned_by_head: Dict[str, float] = {}
    coll = _budgets_collection()
    if coll is not None:
        flt: Dict[str, Any] = {"period": period}
        if store_id:
            flt["store_id"] = store_id
        try:
            for r in coll.find(flt):
                head = r.get("head")
                if not head:
                    continue
                planned_by_head[head] = planned_by_head.get(head, 0.0) + float(
                    r.get("planned_amount") or 0.0
                )
        except Exception:
            planned_by_head = {}

    # ---- actuals (derived, never stored) --------------------------------
    actual_by_head: Dict[str, float] = {}
    actual_by_head[REVENUE_HEAD] = round(_revenue_actual(store_id, period), 2)
    for cat, amt in _expense_actuals_by_category(store_id, period).items():
        actual_by_head[cat] = round(amt, 2)

    # ---- merge: every head that has a plan OR an actual -----------------
    all_heads = set(planned_by_head) | set(actual_by_head)

    lines: List[Dict[str, Any]] = []
    for head in all_heads:
        planned = round(float(planned_by_head.get(head, 0.0)), 2)
        actual = round(float(actual_by_head.get(head, 0.0)), 2)
        lines.append(
            {
                "head": head,
                "is_revenue": head == REVENUE_HEAD,
                "planned": planned,
                "actual": actual,
                "variance": _variance(planned, actual),
                "variance_pct": _variance_pct(planned, actual),
            }
        )

    # Stable order: REVENUE first, then expense heads alphabetically.
    lines.sort(key=lambda r: (not r["is_revenue"], r["head"]))

    # ---- totals block ----------------------------------------------------
    revenue_planned = round(planned_by_head.get(REVENUE_HEAD, 0.0), 2)
    revenue_actual = round(actual_by_head.get(REVENUE_HEAD, 0.0), 2)
    expense_planned = round(
        sum(v for k, v in planned_by_head.items() if k != REVENUE_HEAD), 2
    )
    expense_actual = round(
        sum(v for k, v in actual_by_head.items() if k != REVENUE_HEAD), 2
    )

    totals = {
        "revenue_planned": revenue_planned,
        "revenue_actual": revenue_actual,
        "revenue_variance": _variance(revenue_planned, revenue_actual),
        "revenue_variance_pct": _variance_pct(revenue_planned, revenue_actual),
        "expense_planned": expense_planned,
        "expense_actual": expense_actual,
        "expense_variance": _variance(expense_planned, expense_actual),
        "expense_variance_pct": _variance_pct(expense_planned, expense_actual),
        # Net = revenue - expenses, for both plan and actual.
        "net_planned": round(revenue_planned - expense_planned, 2),
        "net_actual": round(revenue_actual - expense_actual, 2),
    }

    return {
        "store_id": store_id,
        "period": period,
        "lines": lines,
        "totals": totals,
    }

"""P&L by store / by category and the period status read.

Moved verbatim out of the 6,604-line api/routers/finance.py (Wave 5
package split): no path, method, dependency, status code, response_model
or default was changed.
"""

from typing import Optional
from fastapi import Depends, HTTPException
from ..auth import get_current_user
from ...services.salary_visibility import SALARY_RESTRICTED_MESSAGE, is_salary_admin
from ._shared import (
    _REAL_ORDER_STATUS_FILTER,
    _REVENUE_EXPR,
    _apply_created_at_range,
    _cost_by_product,
    _get_db,
    _payroll_by_store,
    _store_maps,
    _store_name_map,
    compute_cogs_with_flag,
    is_period_locked,
    pnl_by_category,
    router,
)

# === P&L breakdowns + period status ===


@router.get("/pnl/by-store")
async def get_pnl_by_store(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    entity_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """P&L (revenue - COGS - approved expenses - payroll) per store.

    ADMIN / SUPERADMIN only (OWNER DECISION 2026-08-13). Every row carries that
    store's monthly wage bill in `payroll`, and the business runs 4 stores of
    1-5 people: a per-store payroll total IS an individual's pay packet at that
    size, and a two-person store gives up the second person to one subtraction
    against the reader's own payslip. `net_profit` re-exposes the same figure
    (revenue - cogs - expenses - payroll) even if `payroll` were dropped, so
    there is no version of this table that is safe to widen -- the owner was
    offered a payroll-free variant and chose to close the whole screen instead.

    THE COST, STATED PLAINLY: store managers and area managers lose sight of
    their own store's performance ON THIS SCREEN. Their store-level trading
    figures (revenue, cost, profit -- no payroll term anywhere) remain open, and
    keep a working UI, on GET /api/v1/reports/finance/expense-vs-revenue, which
    is store-scoped via validate_store_access and is what ReportsPage's Forecast
    tab renders. GET /api/v1/reports/profit/by-store is likewise clean and
    likewise left open (verified: store-scoped via user_store_scope, and
    profit == revenue - cost exactly), but no frontend screen calls it.
    """
    if not is_salary_admin(current_user):
        raise HTTPException(status_code=403, detail=SALARY_RESTRICTED_MESSAGE)
    db = _get_db()
    s2e, _ = _store_maps(db)
    store_ids = (
        [sid for sid, eid in s2e.items() if eid == entity_id] if entity_id else None
    )

    # Exclude DRAFT/CANCELLED and compare the date range as datetimes
    # (created_at is a BSON datetime). This `match` is reused for both the
    # revenue aggregation and the COGS find() below.
    match: dict = {"status": _REAL_ORDER_STATUS_FILTER}
    if store_ids is not None:
        match["store_id"] = {"$in": store_ids}
    _apply_created_at_range(match, from_date, to_date)

    rev = list(
        db.get_collection("orders").aggregate(
            [
                {"$match": match},
                {"$group": {"_id": "$store_id", "revenue": {"$sum": _REVENUE_EXPR}}},
            ]
        )
    )
    rev_by_store = {r["_id"]: r["revenue"] for r in rev}

    cost_map = _cost_by_product(db)
    cogs_by_store: dict = {}
    cogs_estimated_by_store: dict = {}  # store_id -> bool (any line estimated)
    for o in db.get_collection("orders").find(
        match, {"_id": 0, "store_id": 1, "items": 1}
    ):
        sid = o.get("store_id")
        _c, _est, _tot = compute_cogs_with_flag([o], cost_map, fallback_rate=0.6)
        cogs_by_store[sid] = cogs_by_store.get(sid, 0) + _c
        if _est > 0:
            cogs_estimated_by_store[sid] = True

    # Expenses are dated on `expense_date` (ISO 'YYYY-MM-DD' string), not
    # `date`; the old field name silently dropped every expense for any
    # date-ranged P&L. from_date / to_date are 'YYYY-MM-DD' strings -> the
    # string comparison is consistent.
    exp_match: dict = {"status": {"$in": ["APPROVED", "PAID", "approved", "paid"]}}
    if store_ids is not None:
        exp_match["store_id"] = {"$in": store_ids}
    if from_date:
        exp_match.setdefault("expense_date", {})["$gte"] = from_date
    if to_date:
        exp_match.setdefault("expense_date", {})["$lte"] = to_date
    exp = list(
        db.get_collection("expenses").aggregate(
            [
                {"$match": exp_match},
                {"$group": {"_id": "$store_id", "amt": {"$sum": "$amount"}}},
            ]
        )
    )
    exp_by_store = {e["_id"]: e["amt"] for e in exp}

    pay_by_store = _payroll_by_store(db, from_date, to_date)

    # backlog #4: show the store NAME, not the raw store id, on the P&L table.
    store_names = _store_name_map(db)

    rows = []
    for sid in (
        set(rev_by_store) | set(cogs_by_store) | set(exp_by_store) | set(pay_by_store)
    ):
        r = round(rev_by_store.get(sid, 0), 2)
        c = round(cogs_by_store.get(sid, 0), 2)
        e = round(exp_by_store.get(sid, 0), 2)
        p = round(pay_by_store.get(sid, 0), 2)
        net = round(r - c - e - p, 2)
        rows.append(
            {
                "store_id": sid,
                "store_name": store_names.get(sid, sid),
                "entity_id": s2e.get(sid),
                "revenue": r,
                "cogs": c,
                # True when any order line used the 60%-fallback (no cost_price).
                # Gross margin for this store should be treated as approximate.
                "cogs_is_estimated": bool(cogs_estimated_by_store.get(sid)),
                "expenses": e,
                "payroll": p,
                "net_profit": net,
                "net_margin": round(net / r * 100, 1) if r > 0 else 0,
            }
        )
    return {
        "stores": sorted(rows, key=lambda x: -x["revenue"]),
        "total_net": round(sum(x["net_profit"] for x in rows), 2),
    }


@router.get("/pnl/by-category")
async def get_pnl_by_category(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    store_id: Optional[str] = None,
    entity_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """Revenue + COGS + gross profit per product category."""
    db = _get_db()
    # Exclude DRAFT/CANCELLED; compare the date range as datetimes
    # (created_at is a BSON datetime, so a string bound matched nothing).
    match: dict = {"status": _REAL_ORDER_STATUS_FILTER}
    store_ids = None
    if store_id:
        store_ids = [store_id]
    elif entity_id:
        s2e, _ = _store_maps(db)
        store_ids = [sid for sid, eid in s2e.items() if eid == entity_id]
    if store_ids is not None:
        match["store_id"] = {"$in": store_ids}
    _apply_created_at_range(match, from_date, to_date)

    orders = list(db.get_collection("orders").find(match, {"_id": 0, "items": 1}))
    cats = pnl_by_category(orders, _cost_by_product(db))
    return {
        "categories": cats,
        "total_revenue": round(sum(c["revenue"] for c in cats), 2),
        "total_gross_profit": round(sum(c["gross_profit"] for c in cats), 2),
    }


@router.get("/period-status")
async def get_period_status(
    month: int,
    year: int,
    current_user: dict = Depends(get_current_user),
):
    """Whether an accounting period is locked (for the UI to disable edits)."""
    db = _get_db()
    return {"month": month, "year": year, "locked": is_period_locked(db, month, year)}

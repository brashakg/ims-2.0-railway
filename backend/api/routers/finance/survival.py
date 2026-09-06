"""N8 owner survival cash-flow (`/survival-cashflow`), read-only analytics.

Moved verbatim out of the 6,604-line api/routers/finance.py (Wave 5
package split): no path, method, dependency, status code, response_model
or default was changed.
"""

import calendar
from datetime import datetime, date
from ...utils.ist import now_ist_naive, ist_day_start_utc
from typing import Optional
from fastapi import Depends, Query
from ..auth import get_current_user
from ...services import ap_engine
from ...services import survival_cashflow
from ...services import policy_engine
from ._shared import (
    PAID_STATUSES,
    _REAL_ORDER_STATUS_FILTER,
    _REVENUE_EXPR,
    _get_db,
    _require_finance_admin,
    router,
)
from .cash_flow import _agg_sum, _ap_rows

# === N8 owner "survival" cash-flow (essential vs deferrable + min-pay) ======
# Read-only analytics: NOTHING in this block writes to any collection. The
# pure math lives in services/survival_cashflow.py; these helpers only fetch
# rows + resolve the two E2 policy lists, so each piece is fail-soft and
# independently testable.


def _survival_policy_lists() -> tuple:
    """(essential_heads, critical_vendors) from E2 policy, fail-soft.

    Both keys resolve at GLOBAL scope (the survival view is an org-wide owner
    figure). Junk values (non-list) fall back to the code defaults; an owner
    who explicitly saves an EMPTY essential list is honored (everything
    becomes deferrable -- that is a meaningful policy choice, not junk).
    """
    try:
        essential = policy_engine.get_policy(
            "finance.survival_essential_heads",
            default=survival_cashflow.ESSENTIAL_DEFAULT_HEADS,
        )
    except Exception:
        essential = None
    try:
        critical = policy_engine.get_policy(
            "finance.survival_critical_vendors", default=[]
        )
    except Exception:
        critical = None
    if not isinstance(essential, list):
        essential = list(survival_cashflow.ESSENTIAL_DEFAULT_HEADS)
    if not isinstance(critical, list):
        critical = []
    return essential, critical


def _survival_month_expense_rows(db, now: datetime, store_id: Optional[str] = None):
    """Current-month committed expenses grouped by head (rupees).

    Same field conventions as the rest of this router: `expense_date` is a
    date-only 'YYYY-MM-DD' string and committed states are APPROVED / PAID.
    """
    start = date(now.year, now.month, 1).isoformat()
    end = (
        date(now.year + 1, 1, 1)
        if now.month == 12
        else date(now.year, now.month + 1, 1)
    ).isoformat()
    match = {
        "expense_date": {"$gte": start, "$lt": end},
        "status": {"$in": ["APPROVED", "PAID", "approved", "paid"]},
    }
    if store_id:
        match["store_id"] = store_id
    try:
        rows = list(
            db.get_collection("expenses").aggregate(
                [
                    {"$match": match},
                    {"$group": {"_id": "$category", "total": {"$sum": "$amount"}}},
                ]
            )
        )
    except Exception:
        rows = []
    return [
        {"head": (r.get("_id") or "uncategorized"), "amount": r.get("total") or 0}
        for r in rows
    ]


def _survival_ap_items(db):
    """Open AP bills as aging items (rupee `outstanding`, resolved `due_date`)
    + the raw bill's vendor_critical flag carried through.

    Vendor bills carry no store_id (they are entity-level liabilities), so the
    AP side of the survival view is always org-wide.
    """
    bills, payments, dn = _ap_rows(db)
    ap = ap_engine.build_aging(bills, payments, dn)
    crit_by_bill = {}
    for b in bills:
        if isinstance(b, dict) and b.get("bill_id") is not None:
            crit_by_bill[b["bill_id"]] = bool(b.get("vendor_critical"))
    items = []
    for it in ap.get("items", []):
        row = dict(it)
        row["vendor_critical"] = crit_by_bill.get(it.get("bill_id"), False)
        items.append(row)
    return items


def _survival_projected_income_paise(
    db, now: datetime, store_id: Optional[str] = None
) -> int:
    """This month's PAID revenue-to-date pro-rated to a full month, in paise.

    Uses the exact same revenue definition as /owner-dashboard (paid orders,
    DRAFT/CANCELLED excluded, datetime bound on created_at) so the two owner
    views can never disagree about what 'income' means.
    """
    start = ist_day_start_utc(now.replace(day=1).date())
    match = {
        "created_at": {"$gte": start},
        "payment_status": {"$in": PAID_STATUSES},
        "status": _REAL_ORDER_STATUS_FILTER,
    }
    if store_id:
        match["store_id"] = store_id
    revenue_to_date = _agg_sum(db, "orders", match, _REVENUE_EXPR)
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    projected = revenue_to_date / max(now.day, 1) * days_in_month
    return int(round(projected * 100))


def _build_survival_payload(db, now: datetime, store_id: Optional[str] = None) -> dict:
    """Assemble inputs and run the pure builder. db=None -> all-zero view."""
    essential, critical = _survival_policy_lists()
    if db is None:
        expenses, ap_items, income = [], [], 0
    else:
        expenses = _survival_month_expense_rows(db, now, store_id=store_id)
        ap_items = _survival_ap_items(db)
        income = _survival_projected_income_paise(db, now, store_id=store_id)
    # P3-2: month-to-date fraction = elapsed days / days in month. The income
    # helper projects full-month from revenue-to-date by dividing by now.day,
    # so scaling that projection back by exactly this fraction recovers the
    # month-to-date booked revenue -- a true like-for-like vs MTD expenses.
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    mtd_fraction = min(now.day, days_in_month) / days_in_month
    return survival_cashflow.build_survival_view(
        expenses,
        ap_items,
        income,
        now=now,
        essential_heads=essential,
        critical_vendors=critical,
        # P3-1: AP is always org-wide; income/expenses are store-scoped only
        # when a store filter is supplied.
        store_scoped=bool(store_id),
        month_to_date_fraction=mtd_fraction,
    )


@router.get("/survival-cashflow")
async def get_survival_cashflow(
    store_id: Optional[str] = Query(
        None,
        description="Filter expenses + income to one store. AP bills are "
        "entity-level liabilities and stay org-wide.",
    ),
    current_user: dict = Depends(get_current_user),
):
    """Owner "survival" view: ESSENTIAL fixed costs vs MUST-PAY vendor bills
    vs DEFERRABLE spend, with the min-pay scenario (fixed + must-pay) compared
    against projected month income (this month's paid revenue pro-rated).

    Read-only analytics; integer paise. ADMIN / ACCOUNTANT only -- mirrors
    /owner-dashboard's gate exactly.

    *** OWNER RULING 2026-08-14: THIS ROUTE STAYS OPEN TO THE ACCOUNTANT, AND
        THAT IS A DELIBERATE EXCEPTION TO HIS OWN 2026-08-09 SALARY RULING.
        DO NOT "TIDY IT UP". ***

    services/survival_cashflow.ESSENTIAL_DEFAULT_HEADS literally lists salary,
    salaries, payroll, pf and esi, and `survival.essential_detail` returns those
    heads BY NAME with their amounts. The route is store-narrowable via
    ?store_id=, and the business runs stores of 1-5 people, so for an ACCOUNTANT
    -- who is NOT a salary admin -- this is a named, per-store wage bill.

    The owner was shown that exact consequence and left it open anyway. His
    reasoning, recorded because it is sound: knowing what pay is due IS the
    point of a survival-cash view. An accountant who cannot see the largest
    committed outgoing of the month cannot answer "can we make payroll this
    month", which is the only question this screen exists to answer.

    THE COST, stated plainly so nobody has to rediscover it: after PR #985 the
    ACCOUNTANT cannot see the wage bill on /finance/pnl or /finance/cash-flow,
    but CAN read it by name here. Closing /finance/cash-flow for the ACCOUNTANT
    is therefore belt-and-braces rather than a seal; the roles those strips
    genuinely protect against are STORE_MANAGER and AREA_MANAGER, who cannot
    reach this route at all. That inconsistency is the owner's to resolve, not a
    fresh audit finding -- test_salary_aggregate_leak.py PINS this behaviour so
    the next well-meaning security pass cannot silently delete the accountant's
    cash-survival tool.
    """
    _require_finance_admin(current_user)
    db = _get_db()
    now = now_ist_naive()
    return {
        "as_of": now.date().isoformat(),
        "month": f"{now.year:04d}-{now.month:02d}",
        "store_id": store_id,
        "survival": _build_survival_payload(db, now, store_id=store_id),
    }

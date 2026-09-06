"""Budget vs actual (`/budget`) and the simple reconciliation read.

Moved verbatim out of the 6,604-line api/routers/finance.py (Wave 5
package split): no path, method, dependency, status code, response_model
or default was changed.
"""

from datetime import datetime
from ...utils.ist import now_ist, now_ist_naive
from typing import Optional
from fastapi import Depends, Query
from ..auth import get_current_user
from ...services.salary_visibility import is_salary_admin
from ._shared import _get_db, _require_finance_admin, router
from .pnl import _is_payroll_shaped_expense
from .survival import _build_survival_payload

# === Budget ===


@router.get("/budget")
async def get_budget(
    mode: str = Query("full", pattern="^(full|survival)$"),
    month: Optional[int] = None,
    year: Optional[int] = None,
    current_user: dict = Depends(get_current_user),
):
    db = _get_db()
    now = now_ist()
    m = month or now.month
    y = year or now.year

    budget = db.get_collection("budgets").find_one(
        {"month": m, "year": y, "mode": mode}, {"_id": 0}
    )
    if not budget:
        # No budget configured for this period -- return an honest empty
        # skeleton (budget=0 for all categories) rather than fabricated
        # allocation numbers. The UI should prompt the user to set a budget.
        budget = {
            "month": m,
            "year": y,
            "mode": mode,
            "no_budget_set": True,
            "categories": {
                "rent": {"budget": 0, "actual": 0},
                "salaries": {"budget": 0, "actual": 0},
                "utilities": {"budget": 0, "actual": 0},
                "marketing": {"budget": 0, "actual": 0},
                "inventory": {"budget": 0, "actual": 0},
                "miscellaneous": {"budget": 0, "actual": 0},
            },
        }

    # Fill actuals from expenses. Expenses are dated on `expense_date`
    # (date-only 'YYYY-MM-DD' string), NOT `date`; the old field name + datetime
    # isoformat boundary matched nothing. Use date-only string bounds to match
    # the stored values.
    start = datetime(y, m, 1)
    end = datetime(y, m + 1 if m < 12 else 1, 1) if m < 12 else datetime(y + 1, 1, 1)
    actuals = list(
        db.get_collection("expenses").aggregate(
            [
                {
                    "$match": {
                        "expense_date": {
                            "$gte": start.date().isoformat(),
                            "$lt": end.date().isoformat(),
                        },
                        "status": {"$in": ["APPROVED", "PAID", "approved", "paid"]},
                    }
                },
                {"$group": {"_id": "$category", "total": {"$sum": "$amount"}}},
            ]
        )
    )
    for a in actuals:
        cat = a["_id"].lower() if a["_id"] else "miscellaneous"
        if cat in budget.get("categories", {}):
            budget["categories"][cat]["actual"] = a["total"]

    if mode == "survival":
        # N8: this branch used to be DEAD -- the budgets writer never stores a
        # `mode` field, so the lookup above always missed and mode=survival
        # returned only the empty no_budget_set skeleton. Wire it to the REAL
        # survival view (kept on the existing envelope for back-compat; the
        # dedicated GET /finance/survival-cashflow is the first-class API).
        # The survival figures are org-wide owner material (AP totals +
        # projected income), so this mode narrows to the owner-dashboard gate
        # -- the plain budget skeleton stays visible to the wider finance set.
        _require_finance_admin(current_user)
        budget.pop("no_budget_set", None)
        # Survival is inherently an as-of-NOW "can I cover THIS month" question
        # (it weighs live overdue AP + month-to-date revenue), so it cannot be
        # rendered for an arbitrary historical month/year. P3-3: rather than
        # silently embed a NOW view inside an envelope stamped with a requested
        # past month, stamp the survival block with the real as-of date and
        # flag when the requested period is not the current month, so the
        # envelope can never mislead.
        survival_now = now_ist_naive()
        survival = _build_survival_payload(db, survival_now)
        budget["survival"] = survival
        budget["survival_as_of"] = survival["as_of"]
        budget["survival_month"] = f"{survival_now.year:04d}-{survival_now.month:02d}"
        requested_is_current = m == survival_now.month and y == survival_now.year
        budget["survival_reflects_requested_period"] = requested_is_current
        if not requested_is_current:
            budget["survival_note"] = (
                f"The budget skeleton is for {y:04d}-{m:02d}, but the survival "
                f"block is an as-of-{survival['as_of']} view of the CURRENT "
                "month -- it always reflects today's overdue AP and "
                "month-to-date revenue, not the requested historical period."
            )

    # THE TWIN OF THE /pnl EXPENSE STRIP ABOVE, and the reason it is here rather
    # than in a later ticket: the Budgets tab of FinanceDashboard renders this
    # response beside the P&L tab that /pnl feeds. Closing "Salary" on one tab
    # while the tab next to it shows the same rupees under `categories.salaries`
    # would be the rule applied in one place and not its twin -- the failure this
    # codebase keeps repeating.
    #
    # BOTH numbers go, not just the actual: `budget` is the PLANNED wage bill for
    # the month, which in a 1-5 person store is an individual's pay to within a
    # rounding, and `actual` is the same expense figure /pnl now withholds.
    #
    # THE FLAG IS GATED ON MONEY, NOT ON THE POP. Dropping the head always
    # happens; CLAIMING something was withheld only happens when the head
    # actually carried a figure. The no-budget skeleton above (:2374) hard-codes
    # `"salaries": {"budget": 0, "actual": 0}`, and no-budget-set is the live
    # state today -- so flagging on the pop alone lit the amber "this is not the
    # full operating cost" notice permanently for every store and area manager,
    # while nothing of value had been withheld. A warning that is always on is
    # exactly as useless as one that never appears: it gets tuned out, and then
    # it is not believed on the day a real wage bill IS withheld.
    if not is_salary_admin(current_user):
        _cats = budget.get("categories") or {}
        _dropped = [c for c in list(_cats) if _is_payroll_shaped_expense(c)]
        _withheld_money = any(
            float((_cats.get(_c) or {}).get("budget") or 0) != 0
            or float((_cats.get(_c) or {}).get("actual") or 0) != 0
            for _c in _dropped
        )
        for _c in _dropped:
            _cats.pop(_c, None)
        if _withheld_money:
            budget["categories_partially_restricted"] = True
    return budget


# === Reconciliation ===


@router.get("/reconciliation")
async def get_reconciliation(current_user: dict = Depends(get_current_user)):
    db = _get_db()
    # Inter-store transfers needing reconciliation
    pending = list(
        db.get_collection("stock_transfers")
        .find(
            {"status": {"$in": ["shipped", "in_transit"]}},
            {
                "_id": 0,
                "transfer_id": 1,
                "from_store": 1,
                "to_store": 1,
                "items": 1,
                "created_at": 1,
            },
        )
        .limit(50)
    )

    return {
        "pending_transfers": len(pending),
        "transfers": pending,
    }

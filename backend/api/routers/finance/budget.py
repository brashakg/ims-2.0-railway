"""Budget vs actual (`/budget`) and the simple reconciliation read.

Moved verbatim out of the 6,604-line api/routers/finance.py (Wave 5
package split): no path, method, dependency, status code, response_model
or default was changed.
"""

from ...utils.ist import now_ist, now_ist_naive
from typing import Optional
from fastapi import Depends, Query
from ..auth import get_current_user
from .. import budgets as _budgets
from ...dependencies import store_in_view
from ...services.salary_visibility import is_salary_admin
from ._shared import _get_db, _require_finance_admin, router
from .pnl import _is_payroll_shaped_expense
from .survival import _build_survival_payload, _survival_scope

# === Budget ===


@router.get("/budget")
async def get_budget(
    mode: str = Query("full", pattern="^(full|survival)$"),
    month: Optional[int] = None,
    year: Optional[int] = None,
    store_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    now = now_ist()
    m = month or now.month
    y = year or now.year
    period = f"{y:04d}-{m:02d}"
    # The shop rule every store-level finance read shares: a shop login reads
    # its own shop; an admin reads the shop he picked, or every shop.
    shop = store_in_view(store_id, current_user)

    # ONE BUDGET, ONE READER (audit L3-15). The plan is whatever the Budgeting
    # page saved -- one row per (shop, YYYY-MM, head) -- read through the same
    # function Budgeting's own variance view uses, so the two screens cannot
    # disagree. This used to look up a {month, year, mode} document that no
    # code has ever written, so the Budgets tab said "no budget set" beside a
    # saved plan; and it summed every shop's expenses for any reader.
    planned, actual = _budgets.plan_and_actuals(shop, period)
    heads = (set(planned) | set(actual)) - {_budgets.REVENUE_HEAD}
    budget = {
        "month": m,
        "year": y,
        "mode": mode,
        "store_id": shop,
        "period": period,
        "categories": {
            h: {
                "budget": round(float(planned.get(h, 0.0)), 2),
                "actual": round(float(actual.get(h, 0.0)), 2),
            }
            for h in sorted(heads)
        },
        # The income target is not an allocation to spend, so it sits beside
        # the expense heads rather than among them.
        "revenue": {
            "budget": round(float(planned.get(_budgets.REVENUE_HEAD, 0.0)), 2),
            "actual": round(float(actual.get(_budgets.REVENUE_HEAD, 0.0)), 2),
        },
    }
    if not planned:
        # Nothing planned for this shop-month: say so, and still name the usual
        # heads at zero (never fabricated allocations) so the tab is not blank.
        budget["no_budget_set"] = True
        for h in ("rent", "salaries", "utilities", "marketing", "inventory", "miscellaneous"):
            budget["categories"].setdefault(h, {"budget": 0, "actual": 0})

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
        # The ONE shop rule (F63), as GET /finance/survival-cashflow: a shop's
        # accountant reads their own shop's AP, income and expenses here too.
        survival = _build_survival_payload(
            _get_db(), survival_now, **_survival_scope(None, current_user)
        )
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
    # The one transfer store reach (transfers._in_callers_stores): a shop's
    # manager never sees another region's transfers; and the units' cost on
    # each line is read through the one transfer mask (transfers._for: the
    # accounts roles only, owner ruling 2026-10-12).
    from ..transfers import _for, _in_callers_stores

    db = _get_db()
    # Inter-store transfers needing reconciliation
    pending = _in_callers_stores(
        list(
            db.get_collection("stock_transfers").find(
                {"status": {"$in": ["shipped", "in_transit"]}},
                {
                    "_id": 0,
                    "transfer_id": 1,
                    "from_store": 1,
                    "to_store": 1,
                    "from_location_id": 1,
                    "to_location_id": 1,
                    "items": 1,
                    "created_at": 1,
                },
            )
        ),
        current_user,
    )[:50]

    return {
        "pending_transfers": len(pending),
        "transfers": [_for(current_user, t) for t in pending],
    }

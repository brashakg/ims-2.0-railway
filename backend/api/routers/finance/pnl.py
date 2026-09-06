"""Revenue and Profit & Loss (`/revenue`, `/pnl`) plus the two masking field sets.

Moved verbatim out of the 6,604-line api/routers/finance.py (Wave 5
package split): no path, method, dependency, status code, response_model
or default was changed.
"""

from datetime import timedelta
from ...utils.ist import now_ist, ist_today, ist_day_start_utc, fy_start_year_ist
from typing import Optional
from fastapi import Depends, Query
from ..auth import get_current_user
from ...services.cost_mask import can_see_cost
from ...services.salary_visibility import (
    is_payroll_shaped_expense,
    is_salary_admin,
    normalise_expense_category,
)
from ...services import je_service
from ._shared import (
    _DISCOUNT_EXPR,
    _REAL_ORDER_STATUS_FILTER,
    _REVENUE_EXPR,
    _TAX_EXPR,
    _apply_created_at_range,
    _cost_by_product,
    _get_db,
    _je_cal_day,
    _payroll_cost,
    _scope_store,
    compute_cogs_with_flag,
    router,
)


@router.get("/revenue")
async def get_revenue(
    period: str = Query("month", pattern="^(day|week|month|year)$"),
    store_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    store_id = _scope_store(store_id, current_user)
    db = _get_db()
    if db is None:
        return {
            "total_revenue": 0,
            "total_orders": 0,
            "total_tax": 0,
            "total_discount": 0,
            "prev_revenue": 0,
            "change_pct": None,
        }
    today = ist_today()

    if period == "day":
        start = ist_day_start_utc(today)
    elif period == "week":
        start = ist_day_start_utc(today - timedelta(days=today.weekday()))
    elif period == "month":
        start = ist_day_start_utc(today.replace(day=1))
    else:
        fy_year = fy_start_year_ist(now_ist())
        start = ist_day_start_utc(today.replace(year=fy_year, month=4, day=1))

    # created_at is a BSON datetime -> compare against a datetime, not an ISO
    # string (a string bound never matches). Exclude DRAFT/CANCELLED so revenue
    # reflects real booked sales only.
    match = {"created_at": {"$gte": start}, "status": _REAL_ORDER_STATUS_FILTER}
    if store_id:
        match["store_id"] = store_id

    pipeline = [
        {"$match": match},
        {
            "$group": {
                "_id": None,
                "total_revenue": {"$sum": _REVENUE_EXPR},
                "total_orders": {"$sum": 1},
                "total_tax": {"$sum": _TAX_EXPR},
                "total_discount": {"$sum": _DISCOUNT_EXPR},
            }
        },
    ]
    result = list(db.get_collection("orders").aggregate(pipeline))
    current = (
        result[0]
        if result
        else {
            "total_revenue": 0,
            "total_orders": 0,
            "total_tax": 0,
            "total_discount": 0,
        }
    )

    # Previous period for MoM/YoY
    if period == "month":
        # BUG-104: derive the previous month in the CALENDAR frame, then
        # convert. `start` is a SHIFTED naive-UTC instant (18:30 on the last
        # day of the prior month), so calendar arithmetic on it -- the old
        # `(start - 1 day).replace(day=1)` -- landed on the 1st AT 18:30 UTC,
        # i.e. IST midnight of the 2nd: the whole first IST day of the
        # previous month fell out of the MoM denominator, every month.
        prev_first = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
        prev_start = ist_day_start_utc(prev_first)
        prev_match = {
            "created_at": {"$gte": prev_start, "$lt": start},
            "status": _REAL_ORDER_STATUS_FILTER,
        }
        if store_id:
            prev_match["store_id"] = store_id
        prev_result = list(
            db.get_collection("orders").aggregate(
                [
                    {"$match": prev_match},
                    {"$group": {"_id": None, "total_revenue": {"$sum": _REVENUE_EXPR}}},
                ]
            )
        )
        prev_revenue = prev_result[0]["total_revenue"] if prev_result else 0
        mom_growth = (
            ((current["total_revenue"] - prev_revenue) / prev_revenue * 100)
            if prev_revenue > 0
            else 0
        )
    else:
        mom_growth = 0

    return {
        "total_revenue": current["total_revenue"],
        "total_orders": current["total_orders"],
        "total_tax": current["total_tax"],
        "total_discount": current["total_discount"],
        "avg_order_value": (
            current["total_revenue"] / current["total_orders"]
            if current["total_orders"] > 0
            else 0
        ),
        "mom_growth": round(mom_growth, 1),
        "period": period,
    }


# === Profit & Loss ===


# The /pnl response is masked by TWO independent gates, because it mixes two
# different secrets and they do not belong to the same people.
#
# 1. COST_ONLY_PNL_FIELDS -- supplier landing prices and the margins derived from
#    them. Commercially sensitive; gate is services/cost_mask.can_see_cost, which
#    deliberately includes ACCOUNTANT (the books need COGS). UNCHANGED.
COST_ONLY_PNL_FIELDS = (
    "cogs",
    "cogs_is_estimated",
    "cogs_estimated_lines",
    "cogs_total_lines",
    "gross_profit",
    "gross_margin",
)

# 2. PAYROLL_DERIVED_PNL_FIELDS -- the wage bill and every figure it can be
#    subtracted OUT of. Gate is services/salary_visibility.is_salary_admin
#    (ADMIN / SUPERADMIN), per the owner ruling of 2026-08-09.
#
#    net_profit and net_margin are here even though neither is "a salary": with
#    net_profit = gross_profit - total_expenses - payroll_cost + je_revenue_adj,
#    a reader holding the other four recovers payroll_cost with one subtraction.
#    That is how the ACCOUNTANT would still have had the wage bill after
#    payroll_cost alone was removed -- they can see gross_profit and
#    total_expenses. Hiding a number while leaving its addends beside it is not
#    hiding it. In a 1-5 person store the wage bill IS an individual's pay.
#
#    NOT stripped, deliberately: total_expenses, the `expenses` category dict and
#    je_expense_adjustment. All three are payroll-EXCLUSIVE (payroll is a
#    separate line; the payroll run writes the `payroll` collection, never
#    `expenses`), and with the six fields above removed there is no longer any
#    payroll-INCLUSIVE figure left in the body for them to be subtracted from.
#    Dropping them would blank the store manager's operating-cost panel and buy
#    nothing. If a payroll-inclusive figure is ever ADDED to this response, it
#    belongs in this tuple on the same day.
PAYROLL_DERIVED_PNL_FIELDS = (
    "payroll_cost",
    "net_profit",
    "net_margin",
)

# 3. The one hole in "the `expenses` dict is payroll-EXCLUSIVE" above: `category`
#    is FREE TEXT typed by whoever books the expense, so somebody can book the
#    wage bill as an ordinary expense and hand it to a store manager by name.
#    The deny-set that catches the heads a person actually reaches for, what it
#    covers and what it cannot, now lives in services/salary_visibility.py
#    beside the role tuple -- it was found forked-by-omission onto
#    routers/budgets.py within a round of being written here. Imported above as
#    ``is_payroll_shaped_expense``; the two private aliases below keep this
#    module's existing call sites (and their tests) reading naturally.
_normalise_expense_category = normalise_expense_category
_is_payroll_shaped_expense = is_payroll_shaped_expense


@router.get("/pnl")
async def get_pnl(
    store_id: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    store_id = _scope_store(store_id, current_user)
    db = _get_db()
    if db is None:
        return {
            "revenue": 0,
            "tax": 0,
            "expenses": 0,
            "gross_profit": 0,
            "net_profit": 0,
            "gross_margin_pct": 0,
            "net_margin_pct": 0,
        }
    # Exclude DRAFT/CANCELLED (never-booked / reversed) and compare the
    # date range as datetimes (created_at is a BSON datetime, so a 'YYYY-MM-DD'
    # string bound matched nothing -> the whole date-ranged P&L read zero).
    match = {"status": _REAL_ORDER_STATUS_FILTER}
    if store_id:
        match["store_id"] = store_id
    _apply_created_at_range(match, from_date, to_date)

    # Revenue
    rev_pipeline = [
        {"$match": match},
        {
            "$group": {
                "_id": None,
                "revenue": {"$sum": _REVENUE_EXPR},
                "tax": {"$sum": _TAX_EXPR},
            }
        },
    ]
    rev = list(db.get_collection("orders").aggregate(rev_pipeline))
    revenue = rev[0]["revenue"] if rev else 0
    tax = rev[0]["tax"] if rev else 0

    # Expenses. Expense docs store the date on `expense_date` (a 'YYYY-MM-DD'
    # ISO string), NOT `date` -- filtering on `date` dropped EVERY expense
    # whenever a date range was supplied. from_date / to_date arrive as
    # 'YYYY-MM-DD' query strings, so the string comparison is consistent.
    exp_match = {}
    if store_id:
        exp_match["store_id"] = store_id
    if from_date:
        exp_match.setdefault("expense_date", {})["$gte"] = from_date
    if to_date:
        exp_match.setdefault("expense_date", {})["$lte"] = to_date
    exp_match["status"] = {"$in": ["APPROVED", "PAID", "approved", "paid"]}
    exp_pipeline = [
        {"$match": exp_match},
        {"$group": {"_id": "$category", "amount": {"$sum": "$amount"}}},
    ]
    expenses = list(db.get_collection("expenses").aggregate(exp_pipeline))
    total_expenses = sum(e["amount"] for e in expenses)

    # Real COGS from product cost_price (fallback 60% of line total if a
    # product's cost is unknown). Surface a flag when the fallback is used so
    # the UI can warn the owner that some margins are estimated (SYSTEM_INTENT:
    # never show fabricated numbers without flagging them as estimates).
    cost_map = _cost_by_product(db)
    period_orders = list(
        db.get_collection("orders").find(match, {"_id": 0, "items": 1})
    )
    cogs, cogs_est_lines, cogs_total_lines = compute_cogs_with_flag(
        period_orders, cost_map, fallback_rate=0.6
    )
    cogs_is_estimated = cogs_est_lines > 0
    gross_profit = revenue - cogs

    # Payroll cost-to-company for the period's months.
    payroll_cost = _payroll_cost(db, store_id, from_date, to_date)

    # F17/#25: POSTED manual journal entries adjust the P&L (depreciation, bank
    # charges, prior-period corrections). EXPENSE-type debits raise cost;
    # REVENUE-type credits raise revenue. DRAFT/SUBMITTED/APPROVED JEs do NOT
    # touch the ledger -- only POSTED ones (je_service filters on status).
    # JE entry_date is stored as a CALENDAR-day midnight (see _je_parse_entry_date),
    # so range it on calendar-day bounds -- NOT the ist_day_start_utc-shifted
    # created_at bounds used for orders above -- so a JE on the first/last day of
    # the window isn't mis-bucketed (the two frames must agree).
    je_from_dt = _je_cal_day(from_date)
    je_to_dt = _je_cal_day(to_date, end=True)
    je_adj = je_service.pnl_adjustments(
        db, store_id=store_id, from_dt=je_from_dt, to_dt=je_to_dt
    )
    je_rev = je_adj.get("je_revenue_adjustment", 0.0)
    je_exp = je_adj.get("je_expense_adjustment", 0.0)
    # JE EXPENSE debits are genuine period expenses (depreciation, bank charges)
    # and sit below gross profit with the other expenses. JE REVENUE credits are
    # NON-OPERATING income (misc income, prior-period corrections) -- they must
    # NOT inflate trading revenue / gross profit / gross margin (adversarial P2):
    # they enter as their own line below gross profit and only lift net_profit.
    total_expenses = round(total_expenses + je_exp, 2)
    net_profit = gross_profit - total_expenses - payroll_cost + je_rev

    pnl = {
        "revenue": revenue,
        "cogs": round(cogs, 2),
        # When cogs_is_estimated=True, some cost lines used a 60%-of-revenue
        # fallback because the product cost_price is not set. Gross margin shown
        # should be treated as approximate until all products have a cost_price.
        "cogs_is_estimated": cogs_is_estimated,
        "cogs_estimated_lines": cogs_est_lines,
        "cogs_total_lines": cogs_total_lines,
        "gross_profit": round(gross_profit, 2),
        "gross_margin": round(gross_profit / revenue * 100, 1) if revenue > 0 else 0,
        "expenses": {e["_id"]: e["amount"] for e in expenses},
        "total_expenses": total_expenses,
        # Manual-JE adjustments surfaced as their own lines (below gross profit).
        "je_revenue_adjustment": round(je_rev, 2),
        "je_expense_adjustment": round(je_exp, 2),
        "payroll_cost": payroll_cost,
        "net_profit": round(net_profit, 2),
        "net_margin": round(net_profit / revenue * 100, 1) if revenue > 0 else 0,
        "tax_collected": tax,
    }
    # F35 / GAP_ANALYSIS G1: /pnl is store-scoped only (NO role gate), so the cost,
    # profit and margin economics must be stripped for any role not in
    # COST_VISIBLE_ROLES (excludes AREA_MANAGER per DECISIONS sec 9). Revenue + tax
    # (top line) stay visible. Without this, gross_margin/cogs reach every role.
    if not can_see_cost(current_user):
        for _f in COST_ONLY_PNL_FIELDS:
            pnl.pop(_f, None)
    # SEPARATE GATE, SEPARATE RULE (owner ruling 2026-08-09). The payroll figures
    # used to ride on can_see_cost, which admits ACCOUNTANT -- the same accountant
    # /payroll/registers/summary already 403s. Cost is a commercial secret; pay is
    # somebody's pay packet, and the owner declined the accountant carve-out. So
    # the wage bill answers to is_salary_admin, never to the cost gate.
    if not is_salary_admin(current_user):
        for _f in PAYROLL_DERIVED_PNL_FIELDS:
            pnl.pop(_f, None)
        # Same gate, same reason: an expense head somebody TYPED as "Salary" is
        # pay, whatever collection it landed in. Remove the head AND its
        # contribution to total_expenses.
        #
        # WHY NOT BUCKET IT INTO "Other", which is the obvious move: renaming a
        # head does not hide the amount. An "Other" bucket built from the
        # payroll-shaped heads alone IS the wage bill under a new label, and even
        # if it were merged into a real "Other" head, the reader subtracts the
        # named heads from total_expenses and has it back. That is precisely the
        # aggregate-of-one arithmetic PR #984 exists to close, so this drops the
        # figure out of the body entirely instead.
        #
        # sum(expenses.values()) + je_expense_adjustment == total_expenses still
        # holds afterwards, so the manager's operating-cost panel stays
        # internally consistent -- it is simply a smaller, pay-free panel.
        _restricted = {
            head: amount
            for head, amount in (pnl.get("expenses") or {}).items()
            if _is_payroll_shaped_expense(head)
        }
        if _restricted:
            pnl["expenses"] = {
                head: amount
                for head, amount in pnl["expenses"].items()
                if head not in _restricted
            }
            pnl["total_expenses"] = round(
                float(pnl.get("total_expenses") or 0.0)
                - sum(float(v or 0.0) for v in _restricted.values()),
                2,
            )
            # Tell the reader their panel is incomplete rather than letting a
            # short total read as the truth. A flag, never a figure.
            pnl["expenses_partially_restricted"] = True
    return pnl

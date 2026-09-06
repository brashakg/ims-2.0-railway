"""Cash flow (`/cash-flow`), the owner dashboard and the 13-week forecast.

Moved verbatim out of the 6,604-line api/routers/finance.py (Wave 5
package split): no path, method, dependency, status code, response_model
or default was changed.
"""

from datetime import datetime, timedelta
from ...utils.ist import now_ist_naive, ist_today, ist_day_start_utc
from typing import Optional
from fastapi import Depends, Query
from ..auth import get_current_user
from ...dependencies import validate_store_access
from ...services import ap_engine, cashflow
from ...services.salary_visibility import is_payroll_shaped_expense, is_salary_admin
from ._shared import (
    PAID_STATUSES,
    UNPAID_STATUSES,
    _REAL_ORDER_STATUS_FILTER,
    _REVENUE_EXPR,
    _get_db,
    _order_total,
    _require_finance_admin,
    router,
)
from .receivables import _ar_days_overdue, _ar_due_date, _customer_credit_terms

# === Cash Flow ===


@router.get("/cash-flow")
async def get_cash_flow(
    period: str = Query("month"),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    db = _get_db()
    today = ist_today()
    start = ist_day_start_utc(today.replace(day=1))

    active_store = validate_store_access(store_id, current_user) or current_user.get(
        "active_store_id"
    )

    # Inflows (from orders) -- scoped to the active store. created_at is a BSON
    # datetime; an .isoformat() string bound never matched, so inflow always
    # read zero. Also exclude DRAFT/CANCELLED -- a cancelled order is not cash
    # in even if it was once marked PAID.
    inflow_match = {
        "created_at": {"$gte": start},
        "payment_status": {"$in": PAID_STATUSES},
        "status": _REAL_ORDER_STATUS_FILTER,
    }
    if active_store:
        inflow_match["store_id"] = active_store
    inflow = list(
        db.get_collection("orders").aggregate(
            [
                {"$match": inflow_match},
                {"$group": {"_id": None, "total": {"$sum": _REVENUE_EXPR}}},
            ]
        )
    )
    total_inflow = inflow[0]["total"] if inflow else 0

    # Outflows (expenses + purchase orders) — scoped to the active store.
    # NOTE: POs store the store as `delivery_store_id`, expenses as `store_id`.
    # Expenses are dated on `expense_date` (date-only 'YYYY-MM-DD' string), NOT
    # `date`; the old field name dropped every expense. The boundary uses
    # start.date().isoformat() (date-only) so it compares cleanly with the
    # stored date-only strings and INCLUDES 1st-of-month expenses (a datetime
    # 'YYYY-MM-01T00:00:00' boundary would sort AFTER the bare 'YYYY-MM-01').
    exp_match = {
        "expense_date": {"$gte": start.date().isoformat()},
        "status": {"$in": ["APPROVED", "PAID", "approved", "paid"]},
    }
    if active_store:
        exp_match["store_id"] = active_store
    # Grouped BY HEAD, not into a single grand total, purely so the payroll
    # strip below can subtract the pay heads out. Nothing downstream sees the
    # heads -- this response has never carried them and still does not.
    exp_out = list(
        db.get_collection("expenses").aggregate(
            [
                {"$match": exp_match},
                {"$group": {"_id": "$category", "total": {"$sum": "$amount"}}},
            ]
        )
    )
    po_match = {
        "date": {"$gte": start.isoformat()},
        "payment_status": {"$in": PAID_STATUSES},
    }
    if active_store:
        po_match["delivery_store_id"] = active_store
    po_out = list(
        db.get_collection("purchase_orders").aggregate(
            [
                {"$match": po_match},
                {
                    "$group": {
                        "_id": None,
                        "total": {
                            "$sum": {
                                "$ifNull": ["$total_amount", {"$ifNull": ["$total", 0]}]
                            }
                        },
                    }
                },
            ]
        )
    )
    # Real cash paid to vendors this period (vendor_payments). AP is org-level,
    # so only fold it in for the org/owner view (no specific store selected) to
    # avoid double-attributing HQ payments to one store.
    vendor_payment_outflow = 0.0
    if not active_store:
        try:
            vp = list(
                db.get_collection("vendor_payments").aggregate(
                    [
                        {
                            "$match": {
                                "payment_date": {"$gte": start.date().isoformat()}
                            }
                        },
                        {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
                    ]
                )
            )
            vendor_payment_outflow = round(vp[0]["total"], 2) if vp else 0.0
        except Exception:
            vendor_payment_outflow = 0.0

    expense_outflow = round(sum(float(r.get("total") or 0.0) for r in exp_out), 2)
    purchase_outflow = po_out[0]["total"] if po_out else 0

    # THE CROSS-ROUTE SUBTRACTION, and the reason this strip exists at all.
    #
    # /pnl is payroll-EXCLUSIVE below salary-admin. This route totals THE SAME
    # expenses collection over THE SAME store (validate_store_access above) for
    # THE SAME window (1st of the current month) -- a window /pnl will happily
    # be asked for. So a store manager who holds both responses does:
    #
    #     cash-flow.expense_outflow - pnl.total_expenses = the wage bill
    #
    # measured live at 88360.65 - 24450.00 = 63910.65 before this change. The
    # earlier sibling sweep cleared this route because its body carries no head
    # names. The head name was never the leak: two figures over the same scope
    # that differ only by payroll ARE the payroll. See the second corollary in
    # services/salary_visibility.py.
    #
    # ALL THREE FIGURES MOVE TOGETHER. `outflows` and `net_cash_flow` are built
    # FROM expense_outflow, so reducing expense_outflow alone would hand the pay
    # straight back as
    #     outflows - expense_outflow - purchase_outflow - vendor_payment_outflow
    # Reducing the variable at source, BEFORE total_outflow is summed, is what
    # keeps the whole body internally consistent: the four numbers still add up,
    # they simply add up to a smaller, pay-free month. `inflows` is order
    # revenue and shares no term with any of them.
    expenses_partially_restricted = False
    if not is_salary_admin(current_user):
        _restricted = round(
            sum(
                float(r.get("total") or 0.0)
                for r in exp_out
                if is_payroll_shaped_expense(r.get("_id"))
            ),
            2,
        )
        if _restricted:
            expense_outflow = round(expense_outflow - _restricted, 2)
            expenses_partially_restricted = True

    total_outflow = round(
        expense_outflow + purchase_outflow + vendor_payment_outflow, 2
    )

    body = {
        "period": period,
        "inflows": total_inflow,
        "outflows": total_outflow,
        "net_cash_flow": round(total_inflow - total_outflow, 2),
        "expense_outflow": expense_outflow,
        "purchase_outflow": purchase_outflow,
        "vendor_payment_outflow": vendor_payment_outflow,
    }
    if expenses_partially_restricted:
        # Same flag /pnl sets, and for the same reason: a short total must not
        # read as the truth. A flag, never a figure.
        body["expenses_partially_restricted"] = True
    return body


# === Owner cash-flow dashboard + forecast (ADMIN / ACCOUNTANT) ===


def _ap_rows(db):
    """(outstanding bills, all payments, all debit notes) for AP math."""
    try:
        bills = list(
            db.get_collection("vendor_bills").find(
                {"status": {"$ne": "PAID"}}, {"_id": 0}
            )
        )
        payments = list(db.get_collection("vendor_payments").find({}, {"_id": 0}))
        dn = list(db.get_collection("vendor_debit_notes").find({}, {"_id": 0}))
    except Exception:
        bills, payments, dn = [], [], []
    return bills, payments, dn


def _ar_aging(db, now: datetime) -> dict:
    """Customer receivables aged by DUE date.

    due_date = order.created_at + customer.credit_terms_days (fallback 30).
    Buckets: 'current' (not yet due), then 0_30 / 31_60 / 61_90 / 90_plus
    measured from days PAST due. 'overdue' totals anything past due.

    Pre-fix this aged by (now - created_at), which mislabeled current-status
    receivables (NET-60 customer, sold 25 days ago) as already in the 0-30
    overdue bucket. The mirror /outstanding is also fixed to match.
    """
    buckets = {
        "current": 0.0,
        "0_30": 0.0,
        "31_60": 0.0,
        "61_90": 0.0,
        "90_plus": 0.0,
    }
    total = 0.0
    try:
        orders = list(
            db.get_collection("orders").find(
                {
                    "payment_status": {"$in": UNPAID_STATUSES},
                    # A cancelled order is not a receivable.
                    "status": _REAL_ORDER_STATUS_FILTER,
                },
                {
                    "_id": 0,
                    "customer_id": 1,
                    "grand_total": 1,
                    "total": 1,
                    "amount_paid": 1,
                    "created_at": 1,
                    "payment_terms_days": 1,
                },
            )
        )
    except Exception:
        orders = []
    terms_by_customer = _customer_credit_terms(db)
    for o in orders:
        bal = _order_total(o) - float(o.get("amount_paid", 0) or 0)
        if bal <= 0:
            continue
        due = _ar_due_date(o, terms_by_customer)
        days_overdue = _ar_days_overdue(now, due)
        if days_overdue <= 0:
            buckets["current"] += bal
        elif days_overdue <= 30:
            buckets["0_30"] += bal
        elif days_overdue <= 60:
            buckets["31_60"] += bal
        elif days_overdue <= 90:
            buckets["61_90"] += bal
        else:
            buckets["90_plus"] += bal
        total += bal
    buckets = {k: round(v, 2) for k, v in buckets.items()}
    # 'Overdue' is everything past the due date (any bucket except current).
    overdue = round(
        buckets["0_30"] + buckets["31_60"] + buckets["61_90"] + buckets["90_plus"], 2
    )
    return {"total": round(total, 2), "buckets": buckets, "overdue": overdue}


def _agg_sum(db, coll: str, match: dict, expr) -> float:
    try:
        r = list(
            db.get_collection(coll).aggregate(
                [{"$match": match}, {"$group": {"_id": None, "total": {"$sum": expr}}}]
            )
        )
        return round(r[0]["total"], 2) if r else 0.0
    except Exception:
        return 0.0


@router.get("/owner-dashboard")
async def owner_dashboard(current_user: dict = Depends(get_current_user)):
    """CEO/owner financial snapshot: receivables (AR) vs payables (AP), net
    working-capital position, this-month cash movement, and alerts. Org-wide,
    ADMIN / ACCOUNTANT only."""
    _require_finance_admin(current_user)
    db = _get_db()
    now = now_ist_naive()
    start = ist_day_start_utc(now.replace(day=1).date())

    ar = _ar_aging(db, now)

    bills, payments, dn = _ap_rows(db)
    ap = ap_engine.build_aging(bills, payments, dn)
    ap_overdue = round(ap["total_outstanding"] - ap["buckets"]["current"], 2)
    due_7d = 0.0
    due_30d = 0.0
    for it in ap["items"]:
        due = ap_engine.parse_date(it.get("due_date"))
        if due is None:
            continue
        delta = (due.date() - now.date()).days
        if delta <= 7:
            due_7d += it["outstanding"]
        if delta <= 30:
            due_30d += it["outstanding"]
    due_7d = round(due_7d, 2)
    due_30d = round(due_30d, 2)

    revenue = _agg_sum(
        db,
        "orders",
        {
            # Datetime bound (created_at is BSON datetime) + exclude
            # DRAFT/CANCELLED so the owner's month revenue is real.
            "created_at": {"$gte": start},
            "payment_status": {"$in": PAID_STATUSES},
            "status": _REAL_ORDER_STATUS_FILTER,
        },
        _REVENUE_EXPR,
    )
    # PAYROLL-INCLUSIVE, DELIBERATELY -- INSIDE THE 2026-08-14 EXCEPTION.
    #
    # This sum has no category filter, so it carries any pay booked as an
    # ordinary expense, and it is surfaced raw as `this_month.expenses` and
    # folded into `this_month.net_cash_flow`. It is NOT stripped, and the reason
    # is the ROLE SET, not the shape of the figure:
    #
    #   the gate is _require_finance_admin = SUPERADMIN / ADMIN / ACCOUNTANT,
    #   and the rbac_policy row is the same three. Take the salary admins out
    #   and exactly ONE role remains: ACCOUNTANT -- the role the owner ruled on
    #   2026-08-14 may read the pay heads BY NAME AND BY AMOUNT on
    #   /finance/survival-cashflow. STORE_MANAGER and AREA_MANAGER, the roles
    #   the /pnl and /cash-flow strips genuinely protect, cannot reach this
    #   route at all, at either layer.
    #
    # So stripping here would withhold from the accountant, blended, a figure
    # the owner has just decided they may read unblended one screen over. That
    # is theatre, and it would cost the owner an accurate month-to-date cash
    # position on his own dashboard.
    #
    # IF THIS GATE EVER WIDENS BELOW ACCOUNTANT this comment is void and the
    # strip must be added the same day -- see get_cash_flow for the shape.
    expenses = _agg_sum(
        db,
        "expenses",
        {
            # Expenses are dated on `expense_date` (date-only 'YYYY-MM-DD'
            # string), NOT `date` -- the old field name matched nothing, so
            # this-month expenses read 0 and net_cash_flow was overstated by
            # the entire expense total. Use a date-only bound to match.
            "expense_date": {"$gte": start.date().isoformat()},
            "status": {"$in": ["APPROVED", "PAID", "approved", "paid"]},
        },
        "$amount",
    )
    vpaid = _agg_sum(
        db,
        "vendor_payments",
        {"payment_date": {"$gte": start.date().isoformat()}},
        "$amount",
    )

    # Structured alerts: emit `amount` + `label_template` (with a '{}' slot for
    # the FE-rendered INR symbol). `message` keeps an ASCII-only fallback so
    # existing consumers don't break (no Rupee sign in Python source -- breaks
    # Windows cp1252 on print/logger). The FE renders the rupee glyph via
    # inr() over the `amount` value.
    alerts = []
    if ap_overdue > 0:
        alerts.append(
            {
                "level": "warning",
                "amount": ap_overdue,
                "label_template": "{} of vendor payables overdue",
                "message": f"INR {ap_overdue:.0f} of vendor payables overdue",
            }
        )
    if due_7d > 0:
        alerts.append(
            {
                "level": "info",
                "amount": due_7d,
                "label_template": "{} of vendor bills due within 7 days",
                "message": f"INR {due_7d:.0f} of vendor bills due within 7 days",
            }
        )
    if ar["overdue"] > 0:
        alerts.append(
            {
                "level": "warning",
                "amount": ar["overdue"],
                "label_template": "{} of receivables past due date",
                "message": f"INR {ar['overdue']:.0f} of receivables past due date",
            }
        )

    return {
        "as_of": now.date().isoformat(),
        "receivables": ar,
        "payables": {
            "total": ap["total_outstanding"],
            "buckets": ap["buckets"],
            "overdue": ap_overdue,
            "due_7d": due_7d,
            "due_30d": due_30d,
            "unallocated_credits": ap["unallocated_credits"],
        },
        "net_position": round(ar["total"] - ap["total_outstanding"], 2),
        "this_month": {
            "revenue": revenue,
            "expenses": expenses,
            "vendor_payments": vpaid,
            "net_cash_flow": round(revenue - expenses - vpaid, 2),
        },
        "alerts": alerts,
    }


@router.get("/cash-flow-forecast")
async def cash_flow_forecast(
    days: int = Query(90, ge=7, le=365),
    opening_cash: float = Query(0.0),
    collection_lag_days: int = Query(15, ge=0, le=120),
    recurring_monthly_outflow: float = Query(0.0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    """Weekly cash-flow projection. Inflows = unpaid orders projected to a
    collection date (created + collection_lag_days); outflows = outstanding
    vendor bills on their due date + a recurring monthly estimate (avg of the
    last 3 months' expenses plus an owner-supplied recurring_monthly_outflow,
    e.g. payroll). Surfaces the lowest projected balance as a cash-crunch
    warning. ADMIN / ACCOUNTANT only."""
    _require_finance_admin(current_user)
    db = _get_db()
    now = now_ist_naive()

    # Inflows from AR.
    inflow_events = []
    try:
        orders = list(
            db.get_collection("orders").find(
                {
                    "payment_status": {"$in": UNPAID_STATUSES},
                    # Don't project a cancelled order as an expected collection.
                    "status": _REAL_ORDER_STATUS_FILTER,
                },
                {
                    "_id": 0,
                    "grand_total": 1,
                    "total": 1,
                    "amount_paid": 1,
                    "created_at": 1,
                },
            )
        )
    except Exception:
        orders = []
    for o in orders:
        bal = _order_total(o) - float(o.get("amount_paid", 0) or 0)
        if bal <= 0:
            continue
        created = ap_engine.parse_date(o.get("created_at")) or now
        coll_date = created + timedelta(days=collection_lag_days)
        inflow_events.append({"date": coll_date.date().isoformat(), "amount": bal})

    # Outflows from AP (real due dates).
    bills, payments, dn = _ap_rows(db)
    ap = ap_engine.build_aging(bills, payments, dn)
    outflow_events = [
        {"date": it.get("due_date"), "amount": it["outstanding"]} for it in ap["items"]
    ]

    # PAYROLL-INCLUSIVE, DELIBERATELY, AND HERE IS WHY (reviewed 2026-08-14).
    #
    # `monthly_expense_est` is an all-heads expense total and it is surfaced in
    # `assumptions.monthly_expense_estimate`, so it carries any pay booked as an
    # expense. It is NOT stripped, and the reason is the ROLE SET, not the
    # blending:
    #
    #   this route's gate is _require_finance_admin = SUPERADMIN / ADMIN /
    #   ACCOUNTANT, and the rbac_policy row is the same three. Take the salary
    #   admins out and exactly ONE role remains: ACCOUNTANT -- the role the owner
    #   ruled on 2026-08-14 may read the pay heads BY NAME AND BY AMOUNT on
    #   /finance/survival-cashflow, which sits behind this identical gate.
    #
    # So stripping here would withhold from the accountant, in blended form, a
    # figure the owner has just decided they may read unblended one endpoint
    # over. It buys no secrecy from anybody and costs the accountant an accurate
    # runway. STORE_MANAGER and AREA_MANAGER -- the roles the /pnl and
    # /cash-flow strips genuinely protect against -- cannot reach this route at
    # all, at either layer.
    #
    # WHAT AN ATTACKER WOULD HAVE TO KNOW TO UNBLEND IT, stated properly rather
    # than hiding behind "it is blended": the figure is sum(all approved/paid
    # expenses, ALL stores, trailing 90 days) / 3. To pull one month's wage bill
    # out of it they would need the 90-day non-pay expense total across every
    # store (this response does not carry it, and /pnl is per-store and
    # caller-scoped) AND the other two months' pay. An accountant closing the
    # books has both from the ledger anyway, which is the point above.
    #
    # IF THIS GATE EVER WIDENS BELOW ACCOUNTANT, this comment is void and the
    # strip must be added the same day -- see get_cash_flow for the shape.
    #
    # Recurring monthly outflow estimate. Expenses are dated on `expense_date`
    # (date-only 'YYYY-MM-DD' string), NOT `date` -- the old field name matched
    # nothing, so the recurring estimate was always 0 and the forecast
    # understated outflows. Use a date-only bound to match the stored values.
    monthly_expense_est = 0.0
    try:
        three_mo_ago = (now - timedelta(days=90)).date().isoformat()
        r = list(
            db.get_collection("expenses").aggregate(
                [
                    {
                        "$match": {
                            "expense_date": {"$gte": three_mo_ago},
                            "status": {"$in": ["APPROVED", "PAID", "approved", "paid"]},
                        }
                    },
                    {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
                ]
            )
        )
        monthly_expense_est = round(r[0]["total"] / 3.0, 2) if r else 0.0
    except Exception:
        monthly_expense_est = 0.0
    recurring_monthly = round(monthly_expense_est + recurring_monthly_outflow, 2)
    if recurring_monthly > 0:
        m, y = now.month, now.year
        for _ in range((days // 28) + 1):
            if m == 12:
                m, y = 1, y + 1
            else:
                m += 1
            event_date = datetime(y, m, 1)
            if 0 <= (event_date.date() - now.date()).days <= days:
                outflow_events.append(
                    {
                        "date": event_date.date().isoformat(),
                        "amount": recurring_monthly,
                        "label": "Recurring (expenses/payroll est.)",
                    }
                )

    forecast = cashflow.build_forecast(
        opening_cash, inflow_events, outflow_events, now.date().isoformat(), days
    )
    forecast["assumptions"] = {
        "collection_lag_days": collection_lag_days,
        "monthly_expense_estimate": monthly_expense_est,
        "recurring_monthly_outflow_input": recurring_monthly_outflow,
        "recurring_monthly_total": recurring_monthly,
    }
    return forecast

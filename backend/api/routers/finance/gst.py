"""GST summary (`/gst/summary`) and ITC bill eligibility.

Moved verbatim out of the 6,604-line api/routers/finance.py (Wave 5
package split): no path, method, dependency, status code, response_model
or default was changed.
"""

from datetime import datetime, date
from ...utils.ist import now_ist, ist_day_start_utc
from typing import Optional
from fastapi import Depends
from ..auth import get_current_user
from ...services import ap_engine
from ._shared import (
    _REAL_ORDER_STATUS_FILTER,
    _customer_state_map,
    _get_db,
    _split_output_tax,
    _store_state_map,
    router,
)

# === GST Management ===


# Bill statuses that mean "not yet received / not bookable for ITC". A bill in
# one of these is excluded from the ITC total; any other status (or none) counts.
_ITC_PENDING_STATUSES = {"DRAFT", "PENDING", "CANCELLED", "REJECTED", "VOID"}


def _itc_eligible_bill(bill: dict) -> bool:
    """Whether a vendor bill's GST counts toward input credit (owner decision:
    received AND not 17(5)-blocked). DEFAULT-INCLUDE: a bill with no eligibility
    flags is counted, so historical data never silently drops. Excluded only
    when EXPLICITLY blocked or not-yet-received."""
    if not isinstance(bill, dict):
        return False
    # 17(5) disallowed (food / motor vehicle / personal use ...) -> never ITC.
    if bool(bill.get("itc_blocked")):
        return False
    # An explicit itc_eligible=False also blocks (operator marked it).
    if bill.get("itc_eligible") is False:
        return False
    # Not-yet-received: explicit received=False, or a pending-ish status.
    if bill.get("received") is False:
        return False
    status = str(bill.get("status") or "").strip().upper()
    if status in _ITC_PENDING_STATUSES:
        return False
    return True


@router.get("/gst/summary")
async def get_gst_summary(
    month: Optional[int] = None,
    year: Optional[int] = None,
    current_user: dict = Depends(get_current_user),
):
    db = _get_db()
    if db is None:
        return {
            "tax_collected": 0,
            "tax_paid": 0,
            "net_gst_liability": 0,
            "gst_by_rate": {},
        }
    now = now_ist()
    m = month or now.month
    y = year or now.year

    # The GST tax period is an IST calendar month; orders.created_at is a
    # naive-UTC instant -- shift the month boundaries through ist_day_start_utc
    # (same pattern as /cash-flow). With plain datetime(y, m, 1) bounds an
    # invoice at 01-Jun 02:00 IST (= 31-May 20:30 UTC) summed into MAY.
    start = ist_day_start_utc(date(y, m, 1))
    end = ist_day_start_utc(date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1))
    # bill_date is a CALENDAR date (not an instant), so the ITC filter below
    # keeps calendar-day month bounds -- do NOT shift it through IST (see the
    # /itc-register note on bill_date framing).
    bill_start = datetime(y, m, 1)
    bill_end = datetime(y + 1, 1, 1) if m == 12 else datetime(y, m + 1, 1)

    # GST collected (from sales). Orders store `created_at` as a BSON
    # datetime, so the match MUST use datetime objects -- an .isoformat()
    # STRING comparison silently matched nothing and zeroed the GST summary.
    # Exclude DRAFT/CANCELLED (a cancelled sale collected no GST). total_sales
    # uses _REVENUE_EXPR (grand_total) -- `$total` is a legacy field modern
    # orders don't carry, so summing it returned ~0.
    sales_match = {
        "created_at": {"$gte": start, "$lt": end},
        "status": _REAL_ORDER_STATUS_FILTER,
    }
    # Fetch the matched sales rows once with the fields the CGST/SGST/IGST
    # classifier needs (the split happens below). The prior aggregation summed
    # total_tax then split it 50/50, which mis-stated every inter-state sale and
    # never reported IGST.
    _sales_orders = list(
        db.get_collection("orders").find(
            sales_match,
            {
                "_id": 0,
                "store_id": 1,
                "customer_id": 1,
                "tax_amount": 1,
                "tax_total": 1,
                # OS-008: the order-carried inter-state flag (online orders).
                "interstate": 1,
            },
        )
    )

    # GST paid (Input Tax Credit). ITC is claimable on PURCHASES recorded as
    # vendor BILLS (GRN-backed), NOT purchase_orders. Read vendor_bills.bill_date
    # (same source as /itc-register) so the summary reconciles. Date filtered in
    # Python via the tolerant parser (handles ISO string OR BSON datetime).
    #
    # ELIGIBILITY (owner decision, mix of 'received' + 'not blocked'): a bill's
    # tax counts toward ITC only when it is ELIGIBLE -- _itc_eligible_bill()
    # excludes a bill explicitly flagged itc_blocked (17(5) disallowed: food /
    # motor vehicle / etc.) OR whose status says it is not yet received
    # (DRAFT/PENDING/CANCELLED). A bill with NO such flags DEFAULTS to included
    # so historical data never silently drops. gst_amount is the legacy alias.
    gst_paid = 0.0
    gst_paid_excluded = 0.0  # surfaced so the report can show what was held back
    try:
        for _b in db.get_collection("vendor_bills").find(
            {},
            {
                "_id": 0,
                "bill_date": 1,
                "tax_amount": 1,
                "gst_amount": 1,
                "status": 1,
                "itc_blocked": 1,
                "received": 1,
                "itc_eligible": 1,
            },
        ):
            _bd = ap_engine.parse_date(_b.get("bill_date"))
            if _bd is None or not (bill_start <= _bd < bill_end):
                continue
            _tax = float(_b.get("tax_amount") or _b.get("gst_amount") or 0)
            if _itc_eligible_bill(_b):
                gst_paid += _tax
            else:
                gst_paid_excluded += _tax
    except Exception:
        gst_paid = 0.0
        gst_paid_excluded = 0.0
    gst_paid = round(gst_paid, 2)
    gst_paid_excluded = round(gst_paid_excluded, 2)

    # Classify output tax into CGST/SGST (intra-state) vs IGST (inter-state) by
    # the same store-state-vs-customer-state rule as gst_reconciliation()/GSTR-1
    # (unknown state either side -> intra-state). gst_collected is the sum of the
    # three, so the summary cards reconcile to the period total.
    cgst, sgst, igst = _split_output_tax(
        _sales_orders, _store_state_map(db), _customer_state_map(db)
    )
    gst_collected = round(cgst + sgst + igst, 2)
    net_payable = round(gst_collected - gst_paid, 2)

    # Filing status. GSTR-1 is due the 11th and GSTR-3B the 20th of the month
    # AFTER the tax period. For December (m==12) that is January of the NEXT
    # year -- the old `datetime(y, 1, 11)` kept the SAME year, so Dec returns
    # showed a due date in the past (e.g. Dec-2025 due 2025-01-11).
    due_year = y + 1 if m == 12 else y
    due_month = 1 if m == 12 else m + 1
    gstr1_due = datetime(due_year, due_month, 11)
    gstr3b_due = datetime(due_year, due_month, 20)

    return {
        "month": m,
        "year": y,
        "gst_collected": gst_collected,
        "cgst": cgst,
        "sgst": sgst,
        "igst": igst,
        "gst_input_credit": gst_paid,
        # ITC held back this period (not-yet-received or 17(5)-blocked bills) so
        # the CA can see what was excluded rather than wonder why ITC dropped.
        "gst_input_credit_excluded": gst_paid_excluded,
        "net_gst_payable": net_payable,
        "gstr1_due_date": gstr1_due.isoformat(),
        "gstr3b_due_date": gstr3b_due.isoformat(),
        "gstr1_filed": False,
        "gstr3b_filed": False,
    }

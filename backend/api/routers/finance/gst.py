"""GST summary (`/gst/summary`) and ITC bill eligibility.

Moved verbatim out of the 6,604-line api/routers/finance.py (Wave 5
package split): no path, method, dependency, status code, response_model
or default was changed.
"""

from datetime import datetime, date
from ...utils.ist import now_ist, ist_day_start_utc
from typing import Optional
from fastapi import Depends, HTTPException
from ..auth import get_current_user
from ...dependencies import resolve_store_scope
from ...services import ap_engine
from ...services.payables_mask import can_see_payables, strip_input_tax
from ...services.gst_registration import RegistrationAmbiguous, SummaryRegistrations
from ._shared import (
    _REAL_ORDER_STATUS_FILTER,
    _customer_state_map,
    _get_db,
    _require_finance_admin,
    _split_output_tax,
    _store_state_map,
    router,
)

# === GST Management ===

# Owner ruling 2026-10-07 (R1): the GST summary carries input tax summed from
# supplier bills, so it is the accounts roles' alone (_require_finance_admin);
# a manager is refused. Its input-tax keys also pass services/payables_mask.
# strip_input_tax (R5, keys and rule there) for a reader outside that rule.


# Bill statuses that mean "not yet received / not bookable for ITC". A bill in
# one of these is excluded from the ITC total; any other status (or none) counts.
_ITC_PENDING_STATUSES = {"DRAFT", "PENDING", "CANCELLED", "REJECTED", "VOID"}


def _summary_unavailable():
    return HTTPException(
        status_code=503,
        detail="GST summary could not read its source or registration data. Retry when the data is available.",
    )


def _bill_booked(bill: dict) -> bool:
    """Whether a vendor bill is a booked purchase: received and not a draft /
    pending / cancelled / rejected / void one. Whether it then claims credit
    is _itc_eligible_bill's question."""
    if not isinstance(bill, dict):
        return False
    # Not-yet-received: explicit received=False, or a pending-ish status.
    if bill.get("received") is False:
        return False
    status = str(bill.get("status") or "").strip().upper()
    return status not in _ITC_PENDING_STATUSES


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
    # An explicit itc_eligible=False also blocks (operator marked it; a bill
    # for goods bought without a PO is stored so, D14).
    if bill.get("itc_eligible") is False:
        return False
    return _bill_booked(bill)


@router.get("/gst/summary")
async def get_gst_summary(
    month: Optional[int] = None,
    year: Optional[int] = None,
    current_user: dict = Depends(get_current_user),
):
    # R1 (owner ruling 2026-10-07): ADMIN / SUPERADMIN / ACCOUNTANT only.
    _require_finance_admin(current_user)
    # The one shop rule (F63), as the ITC register and GSTR-2B books beside
    # it: ADMIN / SUPERADMIN read every shop; any other login its own shop's
    # sales tax (and, for an accounts role, input credit) -- never every
    # shop's (panel 2026-10-08: one accountant read three input-credit totals
    # for a month). A non-admin login with NO shop gets the reserved
    # NO_SHOP_SCOPE (owner ruling 2026-10-12, R4), which matches no order and
    # no bill: an empty summary, never every shop's GST (its Finance screen
    # says why: NoShopNotice). GSTR-3B stays per GSTIN: it is the filing, and
    # a GSTIN can cover more than one shop. A shop login reading its OWN
    # shop's sales tax (not the company's, as before) waits on the owner: no
    # 2026-10-12 ruling covers it (PLAN_STATUS section 5, OPEN 2026-10-12).
    scope = resolve_store_scope(None, current_user)
    at_shop = {"store_id": scope} if scope else {}
    try:
        db = _get_db()
    except Exception as exc:
        raise _summary_unavailable() from exc
    if db is None:
        raise _summary_unavailable()
    registrations = None
    buckets = {}

    def bucket_for(row, *, purchase=False):
        nonlocal registrations
        try:
            if registrations is None:
                registrations = SummaryRegistrations(db)
            key = registrations.bill_key(row) if purchase else registrations.shop_key(row.get("store_id"))
        except RegistrationAmbiguous as exc:
            raise HTTPException(
                status_code=409,
                detail=("GST summary cannot be calculated because a registration is missing or ambiguous. "
                        "Accounts must correct the shop/entity GST registration or the supplier bill's "
                        "receiving registration, then retry."),
            ) from exc
        except Exception as exc:
            raise _summary_unavailable() from exc
        return buckets.setdefault(key, {"orders": [], "itc": 0.0, "itc_c": 0.0, "itc_s": 0.0, "rcm": 0.0})
    # R5 (owner ruling 2026-10-12): a caller outside the accounts rule never
    # reads the supplier bills, and strip_input_tax drops the input-tax keys
    # from what it returns (the R1 gate above admits none today).
    show_input = can_see_payables(current_user)
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
        **at_shop,
        "created_at": {"$gte": start, "$lt": end},
        "status": _REAL_ORDER_STATUS_FILTER,
    }
    # Fetch the matched sales rows once with the fields the CGST/SGST/IGST
    # classifier needs (the split happens below). The prior aggregation summed
    # total_tax then split it 50/50, which mis-stated every inter-state sale and
    # never reported IGST. A routed online order held on its seller (GSTIN)
    # check is not output tax of any return: GSTR-1/3B and Tally drop it
    # (tally._filed_orders, the ONE rule), and so does this summary.
    from .tally import _filed_orders

    try:
        _sales_orders = list(_filed_orders(
            db,
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
                    "fulfillment_route": 1,
                    "fulfillment_hold": 1,
                    "stock_hold_reason": 1,
                },
            ),
        ))
    except Exception as exc:
        raise _summary_unavailable() from exc
    for order in _sales_orders:
        tax = order.get("tax_amount")
        if float((order.get("tax_total") if tax is None else tax) or 0):
            bucket_for(order)["orders"].append(order)

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
    # Reverse charge: the shop's own GST on its RCM purchases (GSTR-3B 3.1(d)),
    # owed to the government in cash whatever its credit. Counting the credit
    # without the liability read net payable short by exactly this.
    rcm_tax = 0.0
    # The credit by head, for the set-off (net_cash_due): a bill's CGST and SGST
    # are its own; the rest of its tax counts as IGST credit, which pays any
    # head -- so a legacy bill with no heads sets off as its total always did.
    # 3.1(d)'s own "dead bill", and GSTR-3B's own cash rule.
    from ..reports.gst_itc import _DEAD_BILL, net_cash_due

    try:
        for _b in db.get_collection("vendor_bills").find(
            at_shop,
            {
                "_id": 0,
                "bill_date": 1,
                "tax_amount": 1,
                "gst_amount": 1,
                "status": 1,
                "itc_blocked": 1,
                "received": 1,
                "itc_eligible": 1,
                "reverse_charge": 1,
                "cgst_total": 1,
                "sgst_total": 1,
                "store_id": 1,
                "to_store_id": 1,
                "recipient_entity_id": 1,
                "recipient_gstin": 1,
                "grn_id": 1,
                "linked_dc_ids": 1,
            },
        ) if show_input else ():
            _bd = ap_engine.parse_date(_b.get("bill_date"))
            if _bd is None or not (bill_start <= _bd < bill_end):
                continue
            _tax = float(_b.get("tax_amount") or _b.get("gst_amount") or 0)
            rcm = _b.get("reverse_charge") is True and _b.get("status") not in _DEAD_BILL
            eligible = _itc_eligible_bill(_b)
            b = bucket_for(_b, purchase=True) if _tax and (rcm or eligible) else None
            if rcm:
                rcm_tax += _tax
                if b is not None:
                    b["rcm"] += _tax
            if eligible:
                gst_paid += _tax
                if b is not None:
                    b["itc"] += _tax
                    b["itc_c"] += float(_b.get("cgst_total") or 0)
                    b["itc_s"] += float(_b.get("sgst_total") or 0)
            else:
                gst_paid_excluded += _tax
    except HTTPException:
        raise
    except Exception as exc:
        raise _summary_unavailable() from exc
    gst_paid = round(gst_paid, 2)
    gst_paid_excluded = round(gst_paid_excluded, 2)
    rcm_tax = round(rcm_tax, 2)

    # Classify output tax into CGST/SGST (intra-state) vs IGST (inter-state) by
    # the same store-state-vs-customer-state rule as gst_reconciliation()/GSTR-1
    # (unknown state either side -> intra-state). gst_collected is the sum of the
    # three, so the summary cards reconcile to the period total.
    try:
        states = _store_state_map(db, strict=True)
        customers = _customer_state_map(db, strict=True)
    except Exception as exc:
        raise _summary_unavailable() from exc
    cgst, sgst, igst = _split_output_tax(_sales_orders, states, customers)
    gst_collected = round(cgst + sgst + igst, 2)
    # GSTR-3B's cash rule (net_cash_due): credit sets off the output tax as the
    # law orders it, never the reverse-charge tax, which is paid in cash on top
    # (its head does not change the total). The credit it did not use carries
    # to next month (shown, so the panel adds up).
    # One set-off per verified registration. Sum cash AFTER calculation;
    # spare credit on a different GSTIN never pays this registration's tax.
    net_payable = 0.0
    for b in buckets.values():
        out_c, out_s, out_i = _split_output_tax(b["orders"], states, customers)
        net_payable += sum(net_cash_due(
            (out_i, out_c, out_s),
            (b["itc"] - b["itc_c"] - b["itc_s"], b["itc_c"], b["itc_s"]),
            (b["rcm"], 0.0, 0.0),
        ))
    net_payable = round(net_payable, 2)
    credit_used = gst_collected + rcm_tax - net_payable
    # max() only keeps a float -0.0 off the screen; the rule is net_cash_due's.
    credit_carried = round(max(0.0, gst_paid - credit_used), 2)

    # Filing status. GSTR-1 is due the 11th and GSTR-3B the 20th of the month
    # AFTER the tax period. For December (m==12) that is January of the NEXT
    # year -- the old `datetime(y, 1, 11)` kept the SAME year, so Dec returns
    # showed a due date in the past (e.g. Dec-2025 due 2025-01-11).
    due_year = y + 1 if m == 12 else y
    due_month = 1 if m == 12 else m + 1
    gstr1_due = datetime(due_year, due_month, 11)
    gstr3b_due = datetime(due_year, due_month, 20)

    return strip_input_tax({
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
        "reverse_charge_tax": rcm_tax,
        "gst_input_credit_carried_forward": credit_carried,
        "net_gst_payable": net_payable,
        "gstr1_due_date": gstr1_due.isoformat(),
        "gstr3b_due_date": gstr3b_due.isoformat(),
        "gstr1_filed": False,
        "gstr3b_filed": False,
    }, current_user)

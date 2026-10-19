"""GST summary (`/gst/summary`) and ITC bill eligibility.

Moved verbatim out of the 6,604-line api/routers/finance.py (Wave 5
package split): no path, method, dependency, status code, response_model
or default was changed.
"""

from datetime import datetime
from typing import Optional
from fastapi import Depends, HTTPException
from ..auth import get_current_user
from ...dependencies import is_no_shop_scope, resolve_store_scope
from ...services.payables_mask import strip_input_tax
from ._shared import (
    _get_db,
    _require_finance_admin,
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
    """The Finance GST summary cards: the table's rows below them added up
    (finance.gst_crosscheck.gst_by_registration, M3) -- each GST
    registration's GSTR-3B return, so the cards, the table and GSTR-3B are
    one rule. Output tax net of credit notes and with transfers' deemed
    supply; credit set off per GSTIN; reverse charge paid in cash on top.

    When the caller's GSTIN also files for shops outside his view, the
    input-side figures (credit, held back, carried forward, reverse charge,
    net payable) are None: they are the whole GSTIN's (M4)."""
    # R1 (owner ruling 2026-10-07): ADMIN / SUPERADMIN / ACCOUNTANT only.
    _require_finance_admin(current_user)
    from .gst_crosscheck import gst_by_registration, gst_period

    m, y = gst_period(month, year)
    # The one shop rule (F63), as the ITC register and GSTR-2B books beside
    # it: ADMIN / SUPERADMIN read every shop; any other login its own shop.
    # A non-admin login with NO shop gets the reserved NO_SHOP_SCOPE (owner
    # ruling 2026-10-12, R4): an empty summary, never every shop's GST (its
    # Finance screen says why: NoShopNotice).
    scope = resolve_store_scope(None, current_user)
    try:
        db = _get_db()
    except Exception as exc:
        raise _summary_unavailable() from exc
    if db is None:
        raise _summary_unavailable()
    rows = [] if is_no_shop_scope(scope) else gst_by_registration(db, m, y, scope)[0]

    def total(key):
        if any(r[key] is None for r in rows):
            return None
        return round(sum(r[key] for r in rows), 2)

    # Filing status. GSTR-1 is due the 11th and GSTR-3B the 20th of the month
    # AFTER the tax period. For December (m==12) that is January of the NEXT
    # year -- the old `datetime(y, 1, 11)` kept the SAME year, so Dec returns
    # showed a due date in the past (e.g. Dec-2025 due 2025-01-11).
    due_year = y + 1 if m == 12 else y
    due_month = 1 if m == 12 else m + 1
    return strip_input_tax({
        "month": m,
        "year": y,
        "gst_collected": total("gst_collected"),
        "cgst": total("cgst"),
        "sgst": total("sgst"),
        "igst": total("igst"),
        "gst_input_credit": total("input_credit"),
        # GST on this period's bills claiming no credit (switched off, 17(5),
        # an unregistered supplier's tax is no GST and not here) -- shown so
        # the CA can see what was held back rather than wonder why ITC dropped.
        "gst_input_credit_excluded": total("input_credit_excluded"),
        "reverse_charge_tax": total("reverse_charge"),
        "gst_input_credit_carried_forward": total("credit_carried_forward"),
        "net_gst_payable": total("net_payable"),
        "gstr1_due_date": datetime(due_year, due_month, 11).isoformat(),
        "gstr3b_due_date": datetime(due_year, due_month, 20).isoformat(),
        "gstr1_filed": False,
        "gstr3b_filed": False,
    }, current_user)

"""IMS 2.0 - Supplier money (payables) masking.

Owner ruling 2026-10-01: "supplier balances should not be shown to anyone
apart from admin superadmin and accountant". Supplier money is what we owe a
supplier, have paid it, or are owed by it: bills and their paid state, a
supplier's monthly billing, the RTV GST debit note's amounts, a vendor
return's credit, an RMA's expected / received credit and its variance, and the
amount on a Return-to-Vendor ('rtv') approval. Purchase-order totals and
product cost prices are NOT in it -- they follow the cost rule in cost_mask.

ONE rule decides who sees it: cost_mask.can_see_cost(user, "payables") (the
accounts roles, AP_ROLES, plus SUPERADMIN). This module keeps no role list of
its own. Its helpers are PURE: each returns a copy without the supplier money
and never mutates what it was given; an accounts caller gets the input back
unchanged.

Routes that also carry the cost rule (cost_mask.mask_debit_note /
mask_vendor_return) apply these helpers on top, so either rule alone is
enough to hide the money from a role it excludes.

No DB access. No emoji (Windows cp1252).
"""

import functools
import inspect
import re
from typing import Any, Callable, Optional

from fastapi import HTTPException

from .cost_mask import AP_ROLES, can_see_cost  # noqa: F401  (AP_ROLES re-exported)


def can_see_payables(user: Optional[dict]) -> bool:
    """True for the accounts roles alone. Fail-closed: no user -> False."""
    return can_see_cost(user or {}, "payables")


def require_payables(user: Optional[dict], what: str) -> None:
    """403 unless the caller is an accounts role -- for a document that IS
    supplier money end to end (a print or an export cannot drop its amounts
    and still be the document)."""
    if not can_see_payables(user):
        raise HTTPException(
            status_code=403, detail=f"{what} is ADMIN / ACCOUNTANT only"
        )


def _without(node: Any, drop: Callable[[str], bool],
             rewrite: Optional[Callable[[str, Any], Any]] = None) -> Any:
    """A copy of ``node`` with every dict key for which ``drop(key)`` is true
    removed at any depth. ``rewrite(key, value)`` may replace a kept scalar."""
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if isinstance(key, str) and drop(key):
                continue
            if rewrite is not None and not isinstance(value, (dict, list)):
                value = rewrite(key, value)
            out[key] = _without(value, drop, rewrite)
        return out
    if isinstance(node, list):
        return [_without(item, drop, rewrite) for item in node]
    return node


# --- RTV GST debit note (routers/rtv_debit_notes) ---------------------------
# Every amount the note carries: per-line rate / taxable / tax / total, the
# totals block and its rupee twin. The GST RATE (a percentage) and the
# quantities stay -- they are not money owed.
_DEBIT_NOTE_MONEY = frozenset({
    "rate_paise", "taxable_paise", "cgst_paise", "sgst_paise", "igst_paise",
    "tax_paise", "line_total_paise", "grand_total_paise",
    "totals", "totals_rupees",
})


def strip_debit_note_money(doc: Any, user: Optional[dict]) -> Any:
    """An RTV debit note (or a response holding notes) without its amounts,
    unless the caller is an accounts role."""
    if can_see_payables(user):
        return doc
    return _without(doc, _DEBIT_NOTE_MONEY.__contains__)


# --- Vendor return (routers/vendor_returns) ---------------------------------
# The credit the supplier owes us for the returned goods: total_value is the
# credit-note amount once credit_issued (vendor_returns PATCH copies it), and
# the credit-note number is that credit's reference. items[].unit_price is the
# per-piece cost and follows the cost rule (cost_mask), not this one.
_VENDOR_RETURN_CREDIT = frozenset({
    "total_value", "credit_note_amount", "credit_note_number",
})


def strip_vendor_return_credit(doc: Any, user: Optional[dict]) -> Any:
    """A vendor return (or a response holding returns) without the supplier
    credit, unless the caller is an accounts role."""
    if can_see_payables(user):
        return doc
    return _without(doc, _VENDOR_RETURN_CREDIT.__contains__)


# --- Vendor RMA (routers/vendor_rma) ----------------------------------------
# Expected credit, every credit received (the total and each credit note's
# amount), the variance between them and any written-off residual, in paise
# and rupees; line_expected_paise is a line's share of the expected credit.
# The per-unit cost (unit_cost_paise) follows the cost rule.
_RMA_CREDIT = frozenset({
    "expected_credit_paise", "expected_credit_rupees",
    "received_credit_paise", "received_credit_rupees",
    "variance_paise", "variance_rupees",
    "written_off_paise", "line_expected_paise", "received_paise",
})
# The engine writes the amount into its own status-history notes
# (services/vendor_rma record_credit_note / close_rma); keep the event, drop
# the figure. A note a person typed is theirs and passes as written.
_RMA_NOTE_TEMPLATES = (
    (re.compile(r"^(credit note .+) for -?\d+ paise$"), r"\1 recorded"),
    (re.compile(r"^closed; wrote off -?\d+ paise$"), "closed; variance written off"),
)


def _rma_note(key: str, value: Any) -> Any:
    if key != "notes" or not isinstance(value, str):
        return value
    for pattern, replacement in _RMA_NOTE_TEMPLATES:
        if pattern.match(value):
            return pattern.sub(replacement, value)
    return value


def strip_rma_credit(doc: Any, user: Optional[dict]) -> Any:
    """A vendor RMA, an RMA write result or an RMA error detail without the
    supplier credit, unless the caller is an accounts role."""
    if can_see_payables(user):
        return doc
    return _without(doc, _RMA_CREDIT.__contains__, _rma_note)


# --- PO timeline (routers/vendors/po_detail) --------------------------------
# A purchase invoice booked against the PO: its total and its paid state
# (OUTSTANDING / PARTIAL / PAID) are supplier money. Anyone else reads that
# the bill was booked -- the drawer's chip maps BOOKED to "Bill settled", the
# word it shows for every bill.
BILL_BOOKED = "BOOKED"
BILL_BOOKED_DETAIL = "Purchase invoice booked"


def strip_po_timeline_bills(body: Any, user: Optional[dict]) -> Any:
    """A PO timeline without each bill's total and paid state, unless the
    caller is an accounts role."""
    if can_see_payables(user) or not isinstance(body, dict):
        return body
    out = dict(body)
    out["invoices"] = [
        {**{k: v for k, v in inv.items() if k != "total"}, "status": BILL_BOOKED}
        if isinstance(inv, dict) else inv
        for inv in body.get("invoices") or []
    ]
    out["events"] = [
        {**ev, "detail": BILL_BOOKED_DETAIL}
        if isinstance(ev, dict) and ev.get("kind") == "bill_settled" else ev
        for ev in body.get("events") or []
    ]
    return out


# --- Approvals (routers/approvals, services/approvals) ----------------------
# An 'rtv' approval's amount is the supplier credit recorded on an RMA
# (routers/vendor_rma record_credit_note). Other action types are not supplier
# money and keep theirs.
SUPPLIER_MONEY_ACTIONS = frozenset({"rtv"})
_CONTEXT_MONEY = re.compile(r"amount|paise|rupees|total|variance|balance", re.I)


def strip_approval_money(row: Any, user: Optional[dict]) -> Any:
    """An approval request without the supplier amount when it is an 'rtv'
    request, unless the caller is an accounts role. The amount is dropped and
    so is any money-named key the maker put in its context."""
    if (
        not isinstance(row, dict)
        or row.get("action_type") not in SUPPLIER_MONEY_ACTIONS
        or can_see_payables(user)
    ):
        return row
    out = {k: v for k, v in row.items() if k != "amount"}
    if isinstance(row.get("context"), dict):
        out["context"] = _without(row["context"], lambda k: bool(_CONTEXT_MONEY.search(k)))
    return out


# --- Route decorator ---------------------------------------------------------


def masks_supplier_money(strip: Callable[[Any, Optional[dict]], Any]):
    """Run a JSON route's result -- and the dict detail of any HTTPException it
    raises -- through ``strip(value, current_user)``.

    Goes BETWEEN ``@router.<verb>(...)`` and ``async def`` so FastAPI registers
    the wrapper; functools.wraps keeps the handler's signature and globals
    visible to FastAPI through ``__wrapped__``, so Depends / Query are
    unchanged. The handler finds the caller in its ``current_user`` argument
    (no caller -> stripped). For a route whose return value is built on lines
    another rule owns, this strips on top of it. Not for a route returning a
    Response (HTML / XML): those use require_payables."""

    def decorate(handler):
        if not inspect.iscoroutinefunction(handler):
            raise TypeError(f"{handler.__qualname__}: masks_supplier_money needs an async route")
        signature = inspect.signature(handler)

        @functools.wraps(handler)
        async def wrapper(*args, **kwargs):
            user = signature.bind_partial(*args, **kwargs).arguments.get("current_user")
            try:
                result = await handler(*args, **kwargs)
            except HTTPException as exc:
                if isinstance(exc.detail, (dict, list)):
                    exc.detail = strip(exc.detail, user)
                raise
            return strip(result, user)

        return wrapper

    return decorate

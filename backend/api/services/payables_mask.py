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

What this module does NOT do: hide the price paid per piece or the supplier
from the workshop on a vendor return / debit note (owner ruling 2026-09-29).
That is the cost rule's, and its projections (cost_mask.mask_vendor_return /
mask_debit_note) arrive with #1161 -- not on this branch. Where both apply,
these helpers run on top, so either rule alone hides the money from a role it
excludes. The PO timeline's bill masking is NOT here either: it is the
inline can_see_cost(user, "payables") check in routers/vendors/po_detail
(#1161's text, one implementation).

No DB access. No emoji (Windows cp1252).
"""

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
# price paid per piece and follows the cost rule (cost_mask), not this one: the
# purchase roles see it (owner ruling D7 2026-09-29, F47), so a manager CAN
# work a return's credit out as quantity x price -- the totals are hidden, the
# arithmetic is not. Whether managers keep the line price is the owner's open
# question; the workshop and the counter lose it with #1161 (F60).
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
# The per-unit cost (unit_cost_paise) follows the cost rule, as a vendor
# return's unit_price above: line_expected = qty x unit_cost_paise, so the
# credit stays derivable for whoever the cost rule shows the price to.
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

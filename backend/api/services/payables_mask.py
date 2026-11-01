"""IMS 2.0 - Supplier money (payables) masking.

Owner ruling 2026-10-01: "supplier balances should not be shown to anyone
apart from admin superadmin and accountant". Supplier money is what we owe a
supplier, have paid it, or are owed by it: bills and their paid state, a
supplier's monthly billing, the RTV GST debit note's amounts, a vendor
return's credit, an RMA's expected / received credit and its variance, the
amount on a Return-to-Vendor ('rtv') approval, and (owner ruling 2026-10-12)
the unit price, rate and line value on a vendor return or RMA, and the GST
input tax on the GST summary and GSTR-3B (R5, strip_input_tax). Purchase-order
totals and product cost prices elsewhere are NOT in it -- they follow the cost
rule in cost_mask.

ONE rule decides who sees it: cost_mask.can_see_cost(user, "payables") (the
accounts roles, AP_ROLES, plus SUPERADMIN). This module keeps no role list of
its own. Its helpers are PURE: each returns a copy without the supplier money
and never mutates what it was given; an accounts caller gets the input back
unchanged.

What this module does NOT do: project a vendor return (cost_mask.
mask_vendor_return is THE projection: items, quantities and reasons only for
anyone outside the accounts roles -- owner ruling 2026-10-12, R3) or hide the
supplier's identity on a debit note from a non-purchase role (cost_mask.
mask_debit_note, owner ruling 2026-09-29; debit_note_for_reader below
composes it with this module's money strip, the one projection the debit-note
reads use). The PO timeline's bill masking is NOT here either: it is the
inline can_see_cost(user, "payables") check in routers/vendors/po_detail
(#1161's text, one implementation).

No DB access. No emoji (Windows cp1252).
"""

import re
from typing import Any, Callable, Optional

from fastapi import HTTPException

from .cost_mask import (  # noqa: F401  (AP_ROLES re-exported)
    AP_ROLES,
    can_see_cost,
    mask_debit_note,
    return_line_for_reader,
)


def can_see_payables(user: Optional[dict]) -> bool:
    """True for the accounts roles alone. Fail-closed: no user -> False."""
    return can_see_cost(user or {}, "payables")


def require_payables(user: Optional[dict], what: str) -> None:
    """403 unless the caller is an accounts role -- for a document that IS
    supplier money end to end (a print or an export cannot drop its amounts
    and still be the document), and for a write that RECORDS supplier money
    (owner ruling 2026-10-12: an RMA credit note, an RMA write-off, a vendor
    return's credit_issued, the RTV debit note's issue)."""
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


def debit_note_for_reader(doc: Any, user: Optional[dict]) -> Any:
    """THE projection of one debit note for its reader (rtv_debit_notes list
    and detail): the cost rule's supplier-identity allow-list for a
    non-purchase role (cost_mask.mask_debit_note: the workshop reads items and
    quantities) and, for every non-accounts role, no amounts (the 2026-10-01
    supplier-money ruling). Two disjoint rules, one call."""
    return strip_debit_note_money(mask_debit_note(doc, user), user)


# --- Return lines -------------------------------------------------------------
# Owner ruling 2026-10-12 (R3): on a vendor return or an RMA anyone outside the
# accounts roles reads the items, quantities and reasons only -- no unit price,
# no line value, no rate. ONE allow-list cuts every return line, a vendor
# return's (cost_mask.mask_vendor_return) and an RMA's (strip_rma_credit
# below): cost_mask.return_line_for_reader. (The per-unit cost a manager sees
# on stock and POs follows the cost rule, cost_mask; on a return it is the
# basis of the supplier's credit, so it is supplier money here.)


# --- Vendor RMA (routers/vendor_rma) ----------------------------------------
# Expected credit, every credit received (the total and each credit note's
# amount), the variance between them and any written-off residual, in paise
# and rupees, a line's share of the expected credit wherever it sits
# (line_expected_paise), and who set the expected credit and when (priced_by /
# priced_at: the accounts roles' pricing, owner rulings 2026-10-12 R2 + R3 --
# a vendor return's allow-list hides the same two). A line's PRICE
# (unit_cost_paise) is cut by the one return-line allow-list above, not
# listed here.
_RMA_CREDIT = frozenset({
    "expected_credit_paise", "expected_credit_rupees",
    "received_credit_paise", "received_credit_rupees",
    "variance_paise", "variance_rupees",
    "written_off_paise", "line_expected_paise", "received_paise",
    "priced_by", "priced_at",
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


def _rma_for_reader(node: Any) -> Any:
    """``node`` without the RMA credit at any depth, its notes' figures
    rewritten, and every ``lines`` list cut by the one return-line allow-list."""
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if isinstance(key, str) and key in _RMA_CREDIT:
                continue
            if key == "lines" and isinstance(value, list):
                out[key] = [
                    return_line_for_reader(ln) if isinstance(ln, dict) else ln
                    for ln in value
                ]
                continue
            if not isinstance(value, (dict, list)):
                value = _rma_note(key, value)
            out[key] = _rma_for_reader(value)
        return out
    if isinstance(node, list):
        return [_rma_for_reader(item) for item in node]
    return node


def strip_rma_credit(doc: Any, user: Optional[dict]) -> Any:
    """A vendor RMA, an RMA write result or an RMA error detail without the
    supplier credit and with its lines cut to item, quantity and reason,
    unless the caller is an accounts role."""
    if can_see_payables(user):
        return doc
    return _rma_for_reader(doc)


# --- GST input tax (routers/finance/gst, routers/reports/gstr3b) ------------
# Owner ruling 2026-10-12 (R5): GST INPUT tax -- the credit taken on supplier
# bills -- is supplier money. The finance readers keep the sales-side
# (output) figures; every input-tax figure, and every net that hands it back
# by subtraction from the output figure beside it, is ABSENT for a
# non-accounts caller (never 0). ONE key set for both payloads (their key
# names do not collide) and ONE function: a new input-tax figure on either
# payload is added here, once.
INPUT_TAX_KEYS = frozenset({
    # GET /finance/gst/summary
    "gst_input_credit",
    "gst_input_credit_excluded",
    "gst_input_credit_carried_forward",
    "reverse_charge_tax",
    "net_gst_payable",
    "tax_paid",
    "net_gst_liability",
    # GET /reports/gstr3b: Table 4 (ITC available, its company-wide / GSTIN
    # split, ITC utilised), Table 3.1(d) (inward supplies on reverse charge --
    # supplier bills) and the tax paid in cash (output - ITC + RCM).
    "itcAvailable",
    "itcReversed",
    "itcAvailableRegular",
    "itcAvailableGstin",
    "itcReverseCharge",
    "itcIneligible",
    "itcBlocked",
    "itcWriteOffReversed",
    "itcWriteOffReclaimed",
    "unallocatedWriteOffs",
    "inwardExemptNilNonGst",
    "itcUtilized",
    "inwardSuppliesReverseChargeValue",
    "inwardSuppliesReverseCharge",
    "inwardSuppliesReverseChargeGstin",
    "taxPaidCash",
})


def strip_input_tax(body: Any, user: Optional[dict]) -> Any:
    """A GST summary / GSTR-3B without its input-tax figures, unless the
    caller is an accounts role (R5). Top-level keys: both payloads carry
    their input tax there."""
    if can_see_payables(user) or not isinstance(body, dict):
        return body
    return {k: v for k, v in body.items() if k not in INPUT_TAX_KEYS}


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

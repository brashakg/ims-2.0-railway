"""N8 -- Owner "survival" cash-flow service (pure, read-only analytics).

Answers the single owner question: "if this month goes badly, what MUST I
pay, and do I have it?" by splitting the month's outflows into:

  * ESSENTIAL fixed costs   -- expense heads matching the essential list
                               (rent, salaries, electricity, statutory, ...)
  * MUST_PAY vendor bills   -- already overdue, or due within 7 days from a
                               critical vendor (or undatable -- conservative)
  * DEFERRABLE everything else

and comparing the minimum-pay total (fixed + must-pay) against projected
income.

Classification rules
--------------------
* ``classify_expense_head(head, essential_list)`` -> ESSENTIAL when any
  essential keyword matches the case/space-normalized head. Keywords of
  <= 3 chars (pf, esi, gst) must match a whole word -- a bare substring
  match would mis-file "Design retainer" under "esi". Longer keywords
  match as substrings ("rent" matches "Store Rent").
* ``classify_ap_bill(bill, *, now, critical_vendors)`` -> MUST_PAY when the
  bill is overdue (due strictly before today), undatable (no parseable due
  date -- conservative, mirrors ap_engine's 90_plus bucketing), or due
  within ``DUE_SOON_DAYS`` from a critical vendor. Vendor-critical =
  ``bill["vendor_critical"]`` truthy, or the bill's vendor_id / vendor_name
  appears (normalized) in ``critical_vendors``.

Money convention
----------------
Integer paise everywhere. Rupee floats from Mongo are converted ONCE at
entry (``_paise`` -- round at the boundary); a row may instead carry an
already-integer ``amount_paise`` / ``outstanding_paise`` which is taken
verbatim.

The essential-heads seed list and critical-vendor list are owner-editable
via E2 policy keys ``finance.survival_essential_heads`` and
``finance.survival_critical_vendors`` (read by the finance router, never
here). Everything in this module is pure -- no DB, no clock, no policy
reads; the router supplies rows + `now`. No emoji (Windows cp1252).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

# --- Classification verdicts -------------------------------------------------
ESSENTIAL = "ESSENTIAL"
DEFERRABLE = "DEFERRABLE"
MUST_PAY = "MUST_PAY"

# A non-overdue bill from a CRITICAL vendor becomes MUST_PAY when due within
# this many days (the owner cannot risk a supply cut from a critical vendor).
DUE_SOON_DAYS = 7

# Seed list -- owner-editable via E2 policy key
# `finance.survival_essential_heads` (policy_registry.py carries the same
# default by importing THIS constant, so they cannot drift).
ESSENTIAL_DEFAULT_HEADS: List[str] = [
    "rent",
    "salary",
    "salaries",
    "payroll",
    "electricity",
    "statutory",
    "gst",
    "pf",
    "esi",
    "internet",
    "insurance",
]


# --- Small pure helpers ------------------------------------------------------


def _norm(value: Any) -> str:
    """Lowercase, trim, collapse internal whitespace. '' for None/junk."""
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _words(text: str) -> set:
    """Alphanumeric word tokens of an already-normalized string."""
    return {w for w in re.split(r"[^a-z0-9]+", text) if w}


def _paise(rupees: Any) -> int:
    """Rupees (float/str/None) -> integer paise, rounded at the boundary."""
    try:
        return int(round(float(rupees or 0) * 100))
    except (TypeError, ValueError):
        return 0


def _amount_paise(row: Dict[str, Any], paise_key: str, rupee_key: str) -> int:
    """Take an already-integer paise field verbatim, else convert rupees."""
    v = row.get(paise_key)
    if isinstance(v, int) and not isinstance(v, bool):
        return v
    return _paise(row.get(rupee_key))


def _parse_date(value: Any) -> Optional[datetime]:
    """Tolerant ISO parse ('YYYY-MM-DD' or full ISO). None on junk."""
    if isinstance(value, datetime):
        return value
    if not value or not isinstance(value, str):
        return None
    txt = value.strip()
    if not txt:
        return None
    try:
        return datetime.fromisoformat(txt.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(txt[:10])
    except ValueError:
        return None


# --- Classification ----------------------------------------------------------


def classify_expense_head(head: str, essential_list: Sequence[str]) -> str:
    """ESSENTIAL when any essential keyword matches the normalized head.

    Case/space-normalized contains-match; keywords of <= 3 characters
    (pf / esi / gst) must match a whole word to avoid substring false
    positives. Unknown / empty heads default to DEFERRABLE.
    """
    h = _norm(head)
    if not h:
        return DEFERRABLE
    head_words = _words(h)
    for kw in essential_list or []:
        k = _norm(kw)
        if not k:
            continue
        if len(k) <= 3:
            if k in head_words:
                return ESSENTIAL
        elif k in h:
            return ESSENTIAL
    return DEFERRABLE


def _vendor_is_critical(
    bill: Dict[str, Any], critical_vendors: Sequence[str]
) -> bool:
    """Bill-level flag wins; else vendor_id / vendor_name in the policy list."""
    if bill.get("vendor_critical"):
        return True
    crit = {_norm(v) for v in (critical_vendors or []) if _norm(v)}
    if not crit:
        return False
    return _norm(bill.get("vendor_id")) in crit or _norm(bill.get("vendor_name")) in crit


def classify_ap_bill(
    bill: Dict[str, Any],
    *,
    now: datetime,
    critical_vendors: Sequence[str] = (),
) -> str:
    """MUST_PAY when overdue / undatable, or due soon from a critical vendor.

    * due strictly before today           -> MUST_PAY (anyone)
    * no parseable due date               -> MUST_PAY (conservative; mirrors
                                             ap_engine bucketing undatable
                                             bills as 90_plus)
    * due within DUE_SOON_DAYS (incl. today) AND vendor critical -> MUST_PAY
    * everything else                     -> DEFERRABLE
    """
    due = _parse_date(bill.get("due_date"))
    if due is None:
        return MUST_PAY
    days_to_due = (due.date() - now.date()).days
    if days_to_due < 0:
        return MUST_PAY
    if days_to_due <= DUE_SOON_DAYS and _vendor_is_critical(bill, critical_vendors):
        return MUST_PAY
    return DEFERRABLE


# --- The survival view -------------------------------------------------------


def build_survival_view(
    expenses: List[Dict[str, Any]],
    ap_bills: List[Dict[str, Any]],
    projected_income_paise: int,
    *,
    now: datetime,
    essential_heads: Optional[Sequence[str]] = None,
    critical_vendors: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Pure survival-view dict from pre-fetched rows. Integer paise.

    ``expenses``: rows with ``head`` (or ``category``) + ``amount`` (rupees)
    or ``amount_paise`` (integer). ``ap_bills``: rows with ``outstanding``
    (rupees) or ``outstanding_paise``, plus ``due_date`` / vendor identity /
    optional ``vendor_critical``.

    Invariant: fixed_costs + deferrable_expenses + must_pay_ap +
    deferrable_ap == total_outflows (every input paisa lands in exactly one
    bucket). survival_gap_paise = max(0, min_pay - income) -- a POSITIVE gap
    means the owner cannot cover even the bare minimum; the mirror-side
    surplus_paise = max(0, income - min_pay).
    """
    heads = (
        list(essential_heads)
        if essential_heads is not None
        else list(ESSENTIAL_DEFAULT_HEADS)
    )
    crit = list(critical_vendors or [])

    fixed_costs_paise = 0
    deferrable_expenses_paise = 0
    essential_detail: List[Dict[str, Any]] = []
    deferrable_detail: List[Dict[str, Any]] = []

    for row in expenses or []:
        if not isinstance(row, dict):
            continue
        head = row.get("head") or row.get("category") or "uncategorized"
        amt = _amount_paise(row, "amount_paise", "amount")
        cls = classify_expense_head(head, heads)
        item = {
            "kind": "expense",
            "head": head,
            "amount_paise": amt,
            "classification": cls,
        }
        if cls == ESSENTIAL:
            fixed_costs_paise += amt
            essential_detail.append(item)
        else:
            deferrable_expenses_paise += amt
            deferrable_detail.append(item)

    must_pay_ap_paise = 0
    deferrable_ap_paise = 0
    for bill in ap_bills or []:
        if not isinstance(bill, dict):
            continue
        amt = _amount_paise(bill, "outstanding_paise", "outstanding")
        cls = classify_ap_bill(bill, now=now, critical_vendors=crit)
        item = {
            "kind": "ap_bill",
            "bill_id": bill.get("bill_id"),
            "bill_number": bill.get("bill_number"),
            "vendor_id": bill.get("vendor_id"),
            "vendor_name": bill.get("vendor_name"),
            "due_date": bill.get("due_date"),
            "amount_paise": amt,
            "classification": cls,
        }
        if cls == MUST_PAY:
            must_pay_ap_paise += amt
            essential_detail.append(item)
        else:
            deferrable_ap_paise += amt
            deferrable_detail.append(item)

    income = int(projected_income_paise or 0)
    min_pay_total_paise = fixed_costs_paise + must_pay_ap_paise
    return {
        "as_of": now.date().isoformat(),
        "fixed_costs_paise": fixed_costs_paise,
        "deferrable_expenses_paise": deferrable_expenses_paise,
        "must_pay_ap_paise": must_pay_ap_paise,
        "deferrable_ap_paise": deferrable_ap_paise,
        "total_outflows_paise": (
            fixed_costs_paise
            + deferrable_expenses_paise
            + must_pay_ap_paise
            + deferrable_ap_paise
        ),
        "projected_income_paise": income,
        "min_pay_total_paise": min_pay_total_paise,
        # >= 0; positive means even the bare minimum is not covered.
        "survival_gap_paise": max(0, min_pay_total_paise - income),
        "surplus_paise": max(0, income - min_pay_total_paise),
        "essential_detail": essential_detail,
        "deferrable_detail": deferrable_detail,
        # Transparency: which keyword list produced this classification.
        "essential_heads_used": heads,
    }

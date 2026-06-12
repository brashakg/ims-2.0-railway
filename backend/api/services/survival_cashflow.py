"""N8 -- Owner "survival" cash-flow service (pure, read-only analytics).

Splits the owner's monthly outflows into:
  - ESSENTIAL fixed costs (rent, salaries, electricity, statutory, ...)
  - MUST_PAY accounts-payable bills (overdue, or due within 7 days from a
    critical vendor)
  - DEFERRABLE everything else

and compares the minimum-pay total (fixed + must-pay) against projected
income to answer the single owner question: "if this month goes badly,
what MUST I pay, and do I have it?"

All money is integer paise. All functions are pure -- no DB access here.
The essential-heads seed list and critical-vendor list are owner-editable
via E2 policy keys ``finance.survival_essential_heads`` and
``finance.survival_critical_vendors``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# Owner-editable via E2 policy key `finance.survival_essential_heads`.
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


def classify_expense_head(head: str, essential_list: List[str]) -> str:
    """Return "ESSENTIAL" | "DEFERRABLE" (stub)."""
    raise NotImplementedError


def classify_ap_bill(bill: Dict[str, Any], *, now: Any) -> str:
    """Return "MUST_PAY" | "DEFERRABLE" (stub)."""
    raise NotImplementedError


def build_survival_view(
    expenses: List[Dict[str, Any]],
    ap_bills: List[Dict[str, Any]],
    projected_income_paise: int,
    *,
    now: Any,
    essential_heads: Optional[List[str]] = None,
    critical_vendors: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Build the pure survival-view dict (stub)."""
    raise NotImplementedError

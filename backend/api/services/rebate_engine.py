"""Vendor volume-rebate engine -- pure, deterministic, integer paise.

Feature #18. Tiered earned-rebate computation over a vendor's accepted
purchase-invoice spend within a period. All money is integer paise. No I/O
here -- callers pass plain dicts; the router wires DB + AP + Tally.

Owner decision (binding): the earned rebate REDUCES vendor AP (a credit note
against the vendor -- you owe them less). Manual-post first (auto_post=false).
"""

from __future__ import annotations

from typing import Any, Optional


def compute_period_spend(invoices, vendor_id, period_start, period_end) -> int:
    """Sum eligible accepted purchase-invoice spend (paise) for a vendor in
    [period_start, period_end). Pure. Returns integer paise."""
    return 0


def resolve_tier(spend_paise, tiers) -> Optional[dict]:
    """Return the highest tier whose min_spend_paise <= spend_paise, or None."""
    return None


def compute_rebate_paise(spend_paise, tier) -> int:
    """Paise-exact rebate for the resolved tier. Returns 0 if no tier."""
    return 0

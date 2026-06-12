"""Blind stock take (F15) -- transparent, soft-lockable physical count.

Staff physically count stock WITHOUT first seeing the system on-hand (blind =
no anchoring). On manager LOCK the system reveals per-SKU expected-vs-counted
VARIANCE, a summary, and SOFT-LOCKS the session (transparent: who/when, manager
re-openable with a mandatory reason, audited). A confirmed variance can enqueue
a stock-ADJUSTMENT PROPOSAL (reversible, manager-approved) -- it does NOT
silently mutate on-hand.

Mirrors the merged #23 (eod_tally) blind-entry + redact-before-reveal + atomic
soft-lock find_one_and_update pattern, and builds on the existing
inventory.py ``stock_counts`` flow (does NOT fork it).

Money/valuation is integer paise; cost is read from the product doc.

This module is PURE where possible: the variance math takes plain dicts so it
is trivially unit-testable and has no DB / framework imports.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional


# --- count-session status state machine -------------------------------------
STATUS_OPEN = "open"        # accepting blind counted quantities
STATUS_LOCKED = "locked"    # manager revealed variance + soft-locked
STATUS_REOPENED = "reopened"  # manager re-opened (audited, reason required)

# Per-SKU variance verdicts
VERDICT_MATCHED = "matched"
VERDICT_OVER = "over"      # counted > expected (surplus found)
VERDICT_SHORT = "short"    # counted < expected (shrinkage)


def variance(counted: Optional[int], expected: Optional[int]) -> int:
    """Per-SKU variance = counted - expected (units). None treated as 0."""
    return int(counted or 0) - int(expected or 0)


def verdict(counted: Optional[int], expected: Optional[int], tolerance: int = 0) -> str:
    """Classify a per-SKU variance as matched / over / short within tolerance."""
    delta = variance(counted, expected)
    if abs(delta) <= max(0, int(tolerance)):
        return VERDICT_MATCHED
    return VERDICT_OVER if delta > 0 else VERDICT_SHORT

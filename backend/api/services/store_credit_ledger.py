"""
IMS 2.0 - Store-credit / credit-note ledger
===========================================
Today store_credit is a bare number on the customer doc with no history. This
adds an auditable per-customer ledger: every issue / redeem / adjustment is a
signed entry with a running balance_after, so we can answer "where did this
customer's credit come from and where did it go?".

Pure helpers here (no DB) so the math is unit-tested; the router persists.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from api.utils.ist import now_ist_naive

ISSUED = "ISSUED"
REDEEMED = "REDEEMED"
ADJUSTED = "ADJUSTED"
ENTRY_TYPES = {ISSUED, REDEEMED, ADJUSTED}

_EPS = 1e-9


def compute_balance(entries: List[Dict[str, Any]]) -> float:
    """Running balance = sum of signed deltas."""
    return round(sum(float(e.get("delta", 0) or 0) for e in entries), 2)


def make_entry(
    customer_id: str,
    entry_type: str,
    amount: float,
    current_balance: float,
    reason: str = "",
    ref: Optional[str] = None,
    store_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a validated ledger entry. Raises ValueError on invalid input.

    - ISSUED: amount > 0 (adds credit).
    - REDEEMED: amount > 0 and <= current_balance (spends credit).
    - ADJUSTED: signed amount; cannot drive the balance negative.
    """
    if not math.isfinite(amount):
        raise ValueError("amount must be a finite number")
    if not math.isfinite(current_balance):
        raise ValueError("current_balance must be a finite number")

    et = (entry_type or "").upper()
    if et not in ENTRY_TYPES:
        raise ValueError(f"entry_type must be one of {sorted(ENTRY_TYPES)}")

    amt = round(float(amount), 2)
    if et == ISSUED:
        if amt <= 0:
            raise ValueError("amount must be greater than 0")
        delta = amt
    elif et == REDEEMED:
        if amt <= 0:
            raise ValueError("amount must be greater than 0")
        if amt > current_balance + _EPS:
            raise ValueError("insufficient store credit")
        delta = -amt
    else:  # ADJUSTED — amount carries its own sign
        delta = amt
        if current_balance + delta < -_EPS:
            raise ValueError("adjustment would make the balance negative")

    new_balance = round(current_balance + delta, 2)
    return {
        "entry_id": str(uuid.uuid4()),
        "customer_id": customer_id,
        "type": et,
        "amount": abs(amt) if et != ADJUSTED else amt,
        "delta": delta,
        "balance_after": new_balance,
        "reason": reason or "",
        "ref": ref,
        "store_id": store_id,
        "created_by": user_id,
        "created_at": now_ist_naive().isoformat(),
    }

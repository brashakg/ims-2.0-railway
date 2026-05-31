"""
IMS 2.0 — canonical Indian-mobile normalization (single source of truth)
=========================================================================
ONE place to turn human-entered phone input into the canonical stored form: a
bare 10-digit Indian mobile starting 6-9 (``^[6-9]\\d{9}$``).

Before this, every router rolled its own: customers.py / marketing.WalkinRequest
used a RAW ``Field(pattern=...)`` that REJECTED common real input (``+91...``,
``0...``, spaces, dashes), while users.py / portal.py / agents.providers /
techcherry_import each had their own ``_normalize_phone``. The mismatch meant the
SAME human number was accepted in one place and 422'd in another — and the same
customer could be stored under two surface forms across collections, silently
breaking dedup / search / marketing matching.

Keep new phone-accepting code pointed here so the stored form never drifts again.
"""

from __future__ import annotations

import re
from typing import Optional

# Canonical stored form: bare 10-digit Indian mobile, leading digit 6-9.
INDIA_MOBILE_RE = re.compile(r"^[6-9]\d{9}$")


def normalize_indian_mobile(phone: Optional[str]) -> Optional[str]:
    """Normalize human-entered input to the canonical bare 10-digit form.

    Accepts and collapses the common surface variants people actually type:
        9876543210, 098..., +91 98..., 91-98..., "98765 43210", "(+91) 98..."
    by stripping every non-digit, then a leading 91 country code (only when it
    leaves exactly 10 digits) or a single leading 0 trunk prefix.

    Returns:
        * None when the input is None/blank (phone is optional in many models).
        * the bare 10-digit string when it normalizes to a valid Indian mobile.

    Raises:
        ValueError on a non-empty value that is NOT a valid Indian mobile, so a
        Pydantic validator surfaces a clean 422. (Mirrors users.py's prior
        behaviour exactly so nothing that validated before now rejects.)
    """
    if phone is None:
        return None
    digits = re.sub(r"\D", "", str(phone))
    if digits == "":
        return None
    # Peel a leading 0 trunk prefix, then a leading 91 country code, in that
    # order -- so 0..., 91..., +91..., AND 091... (0 trunk + 91 country, 13
    # digits, e.g. "091-9988776655") all collapse to the bare 10 digits. Each
    # strip only fires when it leaves >= 10 digits, so a genuine mobile that
    # happens to start with 0/91 isn't mangled.
    if len(digits) == 13 and digits.startswith("091"):
        digits = digits[3:]
    elif len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if not INDIA_MOBILE_RE.match(digits):
        raise ValueError(
            "Phone must be a 10-digit Indian mobile number starting with 6-9 "
            "(a leading +91 / 0 is accepted and stripped)"
        )
    return digits


def is_valid_indian_mobile(phone: Optional[str]) -> bool:
    """True when `phone` normalizes to a valid Indian mobile. Never raises."""
    try:
        return normalize_indian_mobile(phone) is not None
    except ValueError:
        return False

"""Feature #14 -- Non-adaptation / remake tracking (pure helpers).

A customer who cannot ADAPT to new spectacles (esp. progressives / multifocals)
gets a REMAKE within a policy window. This module holds the pure, side-effect-free
logic used by the non_adapt router: the policy window check and the paise-exact
remake charge decision. The router owns Mongo I/O, RBAC, audit, and the
order/workshop reuse.

----------------------------------------------------------------------------
NON-ADAPTATION RECORD SHAPE (Mongo collection `non_adapt_records`, single doc)
----------------------------------------------------------------------------
{
  "_id": ObjectId,
  "record_id": "NA-<short>",            # human-friendly id
  "store_id": "<store>",                # store-scoped (validate_store_access)
  "original_order_id": "<order id>",    # the order being remade
  "original_item_id": "<line id>",      # the specific lens line, optional
  "lens_brand": "Zeiss",               # denormalised for the quality report
  "product_id": "<catalog product>",    # optional, for the quality report
  "reason": "PROGRESSIVE_INTOLERANCE", # see NonAdaptReason
  "reason_note": "free text",
  "optometrist_id": "<user>",           # who captured / owns the re-check
  "rx_recheck_required": true,           # a non-adapt often => Rx re-check
  "prescription_id": "<rx>",            # new Rx if re-checked, optional

  # --- remake link (set when a remake is initiated) ---
  "remake_order_id": "<new order id>",  # reuses the existing order create path
  "remake_workshop_job_id": "<job id>", # reuses the existing workshop job path
  "remake_status": "RECORDED|REMAKE_INITIATED|COMPLETED|CANCELLED",

  # --- policy charge decision (paise; see remake_charge_paise) ---
  "original_cost_paise": 250000,
  "within_window": true,
  "window_days": 45,
  "charge_policy": "FREE|PERCENT|FULL",
  "charge_percent": 0,                   # when PERCENT
  "remake_charge_paise": 0,              # the DECISION, not a payment capture
  "charge_waived": true,                 # free/discounted vs full
  "authorized_by": "<user>",            # who authorized a waiver (manager+)

  "created_by": "<user>",
  "created_at": "ISO8601",
  "updated_at": "ISO8601",
}

This module does NOT capture money. `remake_charge_paise` is only the DECISION;
the actual remake order, if created, goes through the existing order/workshop
create path so POS pricing/payment stays the single source of truth.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional, Union


# Canonical non-adaptation reasons (quality signal -- aggregated in the report).
NON_ADAPT_REASONS = (
    "PROGRESSIVE_INTOLERANCE",
    "WRONG_POWER_FELT",
    "COSMETIC",
    "OTHER",
)

# Charge policy modes resolved from E2 get_policy.
CHARGE_FREE = "FREE"
CHARGE_PERCENT = "PERCENT"
CHARGE_FULL = "FULL"

DEFAULT_WINDOW_DAYS = 45


def _as_date(value: Union[str, date, datetime]) -> date:
    """Coerce an ISO string / datetime / date into a date (pure)."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    # ISO string; tolerate a trailing 'Z' and time component.
    text = str(value).strip().replace("Z", "")
    if "T" in text:
        text = text.split("T", 1)[0]
    return date.fromisoformat(text)


def is_within_window(
    sale_date: Union[str, date, datetime],
    today: Union[str, date, datetime],
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> bool:
    """True if `today` is within `window_days` (inclusive) of `sale_date`.

    The window is the policy grace period for a free/discounted remake. A remake
    requested on the boundary day still counts as within the window. Future
    sale dates (today < sale_date) are treated as within the window.
    """
    sale = _as_date(sale_date)
    now = _as_date(today)
    delta = (now - sale).days
    if delta < 0:
        return True
    return delta <= int(window_days)


def remake_charge_paise(
    original_cost_paise: int,
    within_window: bool,
    charge_policy: str,
    charge_percent: Union[int, float] = 0,
) -> int:
    """Resolve the paise-exact remake charge DECISION.

    - Within the window:
        FREE    -> 0
        PERCENT -> round(original * percent / 100), half-up, clamped [0, original]
        FULL    -> original (a policy may choose to charge in-window)
    - Outside the window: always the full original cost (chargeable).

    Pure + deterministic. Never returns a negative value. This is the charge
    DECISION only -- it is not a payment capture.
    """
    original = int(original_cost_paise)
    if original < 0:
        raise ValueError("original_cost_paise must be >= 0")

    if not within_window:
        return original

    policy = (charge_policy or "").upper()
    if policy == CHARGE_FREE:
        return 0
    if policy == CHARGE_FULL:
        return original
    if policy == CHARGE_PERCENT:
        pct = float(charge_percent or 0)
        if pct <= 0:
            return 0
        if pct >= 100:
            return original
        # Half-up rounding on integer paise.
        charge = int((original * pct + 50) // 100)
        if charge < 0:
            return 0
        if charge > original:
            return original
        return charge
    # Unknown policy in-window: fail safe to FREE (the audited, gated path).
    return 0


__all__ = [
    "NON_ADAPT_REASONS",
    "CHARGE_FREE",
    "CHARGE_PERCENT",
    "CHARGE_FULL",
    "DEFAULT_WINDOW_DAYS",
    "is_within_window",
    "remake_charge_paise",
]

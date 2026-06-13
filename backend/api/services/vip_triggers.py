"""
IMS 2.0 - F43 VIP personal-triggers engine (pure date core)
============================================================
Centralized engine that computes WHEN a staff alert should fire for a VIP
customer's personal event (anniversary, birthday + N days, a recurring N-day
cadence, or a one-shot custom date). The whole date core is PURE and
clock-injected (``today`` is passed in) so it is fully unit-testable and
deterministic -- no hidden ``datetime.now()`` inside the math.

STAFF_ALERT slice ONLY (comms-DARK): this module + its endpoints + the
MEGAPHONE ``_scan_personal_triggers`` scan create an in-app STAFF notification
and a follow_up work-list row. The customer-MESSAGE channel for #43 stays
DEFERRED under the WhatsApp ban -- nothing leaves the building on a fresh
deploy. If a customer-facing send is ever wired, it rides notification_service
as a PENDING row gated by DISPATCH_MODE.

Trigger doc shape (``personal_triggers`` collection):
    {
      "trigger_id": "VTR-<hex>",
      "customer_id": str,
      "store_id": str | None,
      "type": one of TRIGGER_TYPES,
      "label": str,
      "base_date": "YYYY-MM-DD",     # the anchor event date
      "lead_time_days": int,          # fire this many days BEFORE the event
      "recur_every_days": int | None, # RECURRING only
      "plus_n_days": int | None,      # BIRTHDAY_PLUS_N only
      "active": bool,
      "created_by": str,
      "created_at": iso,
      "last_fired_for": str | None,   # cycle key already fired (no double-fire)
    }
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Trigger taxonomy
# ---------------------------------------------------------------------------
ANNIVERSARY = "ANNIVERSARY"
BIRTHDAY_PLUS_N = "BIRTHDAY_PLUS_N"
RECURRING = "RECURRING"
CUSTOM_DATE = "CUSTOM_DATE"

TRIGGER_TYPES = (ANNIVERSARY, BIRTHDAY_PLUS_N, RECURRING, CUSTOM_DATE)

DEFAULT_LEAD_TIME_DAYS = 7


# ---------------------------------------------------------------------------
# Pure date helpers (no clock; ``today`` is always injected)
# ---------------------------------------------------------------------------
def parse_date(value: Any) -> Optional[date]:
    """Parse a YYYY-MM-DD string (or pass through a date) -> date | None."""
    raise NotImplementedError  # filled in next commit


def next_fire_date(trigger: Dict[str, Any], today: date) -> Optional[date]:
    """Compute the next date the staff alert should fire (pure).

    The fire date is ``lead_time_days`` BEFORE the event occurrence.
    """
    raise NotImplementedError  # filled in next commit


def is_due(trigger: Dict[str, Any], today: date) -> bool:
    """True if the alert's fire window has been reached and this cycle has not
    already been fired (the ``last_fired_for`` cycle-key guard)."""
    raise NotImplementedError  # filled in next commit

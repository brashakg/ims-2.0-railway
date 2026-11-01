"""THE stock-expiry rule (owner ruling 2026-11-01).

A dated batch (contact lenses) is EXPIRED on its expiry date itself: it is not
sold from that IST calendar day on. Days are counted IST calendar days from
today to the expiry date (see inventory.compute_days_until_expiry), so the
expiry day is day 0 and the near-expiry window is days 1..N.

Every reader goes through here: the Contact Lens screens (partition_by_expiry,
the near-expiry filter, the FEFO pick) ask ``is_expired``; the till and the
stock claim (StockRepository's expiry floor) sell only expiries on or after
``first_sellable_expiry``, and the expiring report (find_expiring) starts its
window there. All come from ``EXPIRED_THROUGH_DAY``, so the screen and the till
can never disagree about the same lens. The one frontend copy, the goods-receipt
warning (frontend/src/pages/purchase/receiveLines.ts expiryWarning), mirrors it.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from .ist import ist_today

# A batch this many days (or fewer) from its expiry date is expired. 0 == the
# expiry date itself is expired.
EXPIRED_THROUGH_DAY = 0


def is_expired(days_until_expiry: int) -> bool:
    """True when a batch `days_until_expiry` IST calendar days out is expired."""
    return days_until_expiry <= EXPIRED_THROUGH_DAY


def first_sellable_expiry(today: Optional[date] = None) -> date:
    """The earliest expiry date still sellable today (IST): the first day
    ``is_expired`` is False. The till's / claim's floor."""
    return (today or ist_today()) + timedelta(days=EXPIRED_THROUGH_DAY + 1)

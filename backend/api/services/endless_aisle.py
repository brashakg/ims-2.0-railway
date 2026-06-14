"""Endless Aisle - inter-branch fulfillment of an out-of-stock SKU.

Feature #38. When a selling store is out of stock of a SKU but another
branch holds the unit, a STORE_MANAGER+ can open a fulfillment request.
A two-step source-ACCEPT (the holding branch confirms the unit is real and
sellable) prevents selling ghost stock. The company bears shipping (booked
to the SELLING store's P&L); the customer pays nothing extra and sees no
shipping line on the bill. Eligible source stores are an editable setting
(default ALL). Payment is always collected at the SELLING store.

This module is pure (no DB): the lifecycle state machine, the source-store
selection algorithm, and validation helpers. The router owns all I/O.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# --- Status lifecycle -------------------------------------------------------

STATUS_PENDING = "PENDING"
STATUS_ACCEPTED = "ACCEPTED"
STATUS_TRANSFER_CREATED = "TRANSFER_CREATED"
STATUS_SHIPPED = "SHIPPED"
STATUS_DELIVERED = "DELIVERED"
STATUS_REJECTED = "REJECTED"
STATUS_CANCELLED = "CANCELLED"

ALL_STATUSES = (
    STATUS_PENDING,
    STATUS_ACCEPTED,
    STATUS_TRANSFER_CREATED,
    STATUS_SHIPPED,
    STATUS_DELIVERED,
    STATUS_REJECTED,
    STATUS_CANCELLED,
)

# Legal transitions. PENDING may be accepted, rejected, or cancelled.
# An ACCEPTED request becomes a transfer or may still be cancelled.
ALLOWED_TRANSITIONS: Dict[str, set] = {
    STATUS_PENDING: {STATUS_ACCEPTED, STATUS_REJECTED, STATUS_CANCELLED},
    STATUS_ACCEPTED: {STATUS_TRANSFER_CREATED, STATUS_CANCELLED},
    STATUS_TRANSFER_CREATED: {STATUS_SHIPPED, STATUS_CANCELLED},
    STATUS_SHIPPED: {STATUS_DELIVERED},
    STATUS_DELIVERED: set(),
    STATUS_REJECTED: set(),
    STATUS_CANCELLED: set(),
}


def can_transition(frm: str, to: str) -> bool:
    """True iff moving a request from status `frm` to `to` is legal."""
    return to in ALLOWED_TRANSITIONS.get(frm, set())

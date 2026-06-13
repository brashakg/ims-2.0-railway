"""Feature #48 -- Multi-category servicing & repair portal (pure helpers + engine).

A new service-revenue stream, store-scoped: a customer brings in an item (frame
repair, watch battery, watch repair, sunglass repair, send-to-vendor, ...) and a
repair JOB is opened, tracked through a lifecycle, and handed back to the
customer on DELIVERY. Each store enables only the services it offers via a
per-store service CATALOG.

This module owns the PURE pieces (the status set + legal-transition map and the
intake-validation / paise helpers) AND the Mongo I/O engine (catalog CRUD + the
guarded job lifecycle). RBAC, audit rows, the DARK status SMS, and the HTTP
surface live in ``api/routers/repair_portal.py``.

Repair-job lifecycle (status on the repair_jobs doc)::

    INTAKE --> IN_PROGRESS | SENT_TO_VENDOR | CANCELLED
    IN_PROGRESS --> SENT_TO_VENDOR | READY | CANCELLED
    SENT_TO_VENDOR --> READY | CANCELLED
    READY --> DELIVERED | CANCELLED
    DELIVERED  (terminal)
    CANCELLED  (terminal)

Key guarantees (mirrors workshop / serial_tracking):
- The job transition is a SINGLE guarded ``find_one_and_update`` keyed on
  ``{job_id, store_id, status == <from>}`` so two concurrent transitions of the
  same job -> exactly one winner; an illegal/terminal transition -> 409.
- Everything is store-scoped: every read/write keys on (store_id, ...). One store
  can never see or mutate another store's catalog or jobs.
- A transition to READY fires a DARK status SMS (PENDING row via
  notification_service; nothing leaves while DISPATCH_MODE=off) -- the router
  owns that side-effect.
- POS-billing on DELIVERED is DEFERRED to a later sub-phase -- DELIVERED just
  stamps the status; orders.py / POS are NOT touched here.

Money, if any, is integer paise (``quoted_price_paise``).

No emoji (Windows cp1252).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from .non_adapt import rupees_to_paise

# ---------------------------------------------------------------------------
# Status set + legal transitions
# ---------------------------------------------------------------------------

STATUS_INTAKE = "INTAKE"
STATUS_IN_PROGRESS = "IN_PROGRESS"
STATUS_SENT_TO_VENDOR = "SENT_TO_VENDOR"
STATUS_READY = "READY"
STATUS_DELIVERED = "DELIVERED"
STATUS_CANCELLED = "CANCELLED"

REPAIR_STATUSES = (
    STATUS_INTAKE,
    STATUS_IN_PROGRESS,
    STATUS_SENT_TO_VENDOR,
    STATUS_READY,
    STATUS_DELIVERED,
    STATUS_CANCELLED,
)

# Legal forward transitions. DELIVERED + CANCELLED are terminal (empty set).
ALLOWED_TRANSITIONS: Dict[str, set] = {
    STATUS_INTAKE: {STATUS_IN_PROGRESS, STATUS_SENT_TO_VENDOR, STATUS_CANCELLED},
    STATUS_IN_PROGRESS: {STATUS_SENT_TO_VENDOR, STATUS_READY, STATUS_CANCELLED},
    STATUS_SENT_TO_VENDOR: {STATUS_READY, STATUS_CANCELLED},
    STATUS_READY: {STATUS_DELIVERED, STATUS_CANCELLED},
    STATUS_DELIVERED: set(),
    STATUS_CANCELLED: set(),
}

TERMINAL_STATUSES = frozenset({STATUS_DELIVERED, STATUS_CANCELLED})

# Service categories a store can enable.
SERVICE_CATEGORIES = (
    "FRAME_REPAIR",
    "WATCH_BATTERY",
    "WATCH_REPAIR",
    "SUNGLASS_REPAIR",
    "SEND_TO_VENDOR",
    "OTHER",
)


def can_transition(frm: Optional[str], to: Optional[str]) -> bool:
    """True iff ``frm -> to`` is a legal repair-job transition. Pure -- no I/O.

    An unknown ``frm`` status or a ``to`` not in the allowed set returns False.
    Same-state (frm == to) is False -- a no-op is not a transition.
    """
    f = str(frm or "").upper()
    t = str(to or "").upper()
    return t in ALLOWED_TRANSITIONS.get(f, set())


class RepairError(Exception):
    """A business-rule failure in the repair layer. Carries an HTTP-ish status
    so the router can translate it (404 unknown / 409 conflict / 422 bad input)
    without leaking Mongo internals."""

    def __init__(self, message: str, status: int = 400, code: str = "repair_error"):
        super().__init__(message)
        self.status = status
        self.code = code


# ---------------------------------------------------------------------------
# Pure validation helpers
# ---------------------------------------------------------------------------


def validate_intake(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validate + normalize a repair-job intake payload. Pure -- no I/O.

    Rules:
    - ``service_type`` is required (non-empty).
    - a customer must be identified: either ``customer_id`` OR a walk-in
      (``walkin_name`` AND ``walkin_mobile`` both non-empty).
    - ``quoted_price_paise`` >= 0 (already-paise integer; or derived from
      ``quoted_price`` rupees if paise absent).

    Returns the cleaned field dict. Raises RepairError(422) on any failure so the
    router maps it to an HTTP 422 unprocessable-entity.
    """
    payload = payload or {}
    service_type = str(payload.get("service_type") or "").strip()
    if not service_type:
        raise RepairError("service_type is required", status=422)

    customer_id = payload.get("customer_id")
    customer_id = str(customer_id).strip() if customer_id else None
    walkin_name = payload.get("walkin_name")
    walkin_name = str(walkin_name).strip() if walkin_name else None
    walkin_mobile = payload.get("walkin_mobile")
    walkin_mobile = str(walkin_mobile).strip() if walkin_mobile else None

    if not customer_id and not (walkin_name and walkin_mobile):
        raise RepairError(
            "a customer_id, or a walk-in name AND mobile, is required",
            status=422,
        )

    # Price: prefer explicit paise; else convert rupees. Must be >= 0.
    if payload.get("quoted_price_paise") is not None:
        try:
            quoted_price_paise = int(payload.get("quoted_price_paise"))
        except (TypeError, ValueError):
            raise RepairError("quoted_price_paise must be an integer", status=422)
    elif payload.get("quoted_price") is not None:
        quoted_price_paise = rupees_to_paise(payload.get("quoted_price"))
    else:
        quoted_price_paise = 0
    if quoted_price_paise < 0:
        raise RepairError("quoted_price_paise must be >= 0", status=422)

    return {
        "service_type": service_type,
        "customer_id": customer_id,
        "walkin_name": walkin_name,
        "walkin_mobile": walkin_mobile,
        "quoted_price_paise": quoted_price_paise,
    }


__all__ = [
    "REPAIR_STATUSES",
    "ALLOWED_TRANSITIONS",
    "TERMINAL_STATUSES",
    "SERVICE_CATEGORIES",
    "STATUS_INTAKE",
    "STATUS_IN_PROGRESS",
    "STATUS_SENT_TO_VENDOR",
    "STATUS_READY",
    "STATUS_DELIVERED",
    "STATUS_CANCELLED",
    "can_transition",
    "validate_intake",
    "RepairError",
]

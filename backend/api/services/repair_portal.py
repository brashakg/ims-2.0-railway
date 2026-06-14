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
from datetime import datetime, timezone
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


# ---------------------------------------------------------------------------
# DB engine -- per-store service catalog + guarded repair-job lifecycle.
# Mirrors the workshop / serial_tracking single-doc atomic pattern. Money is
# integer paise. Standalone Mongo: every state change is ONE find_one_and_update.
# ---------------------------------------------------------------------------

CATALOG_COLLECTION = "repair_service_catalog"
JOBS_COLLECTION = "repair_jobs"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_db(db) -> None:
    if db is None:
        raise RepairError("repair store unavailable", status=503, code="no_db")


def ensure_indexes(db) -> None:
    """Idempotent indexes. Fail-soft."""
    if db is None:
        return
    try:
        db.get_collection(JOBS_COLLECTION).create_index(
            [("store_id", 1), ("status", 1)]
        )
        db.get_collection(CATALOG_COLLECTION).create_index(
            [("service_id", 1)], unique=True
        )
    except Exception:  # noqa: BLE001
        return


# --- service catalog (per-store enablement) --------------------------------


def upsert_service(
    db, payload: Dict[str, Any], *, actor: Dict[str, Any]
) -> Dict[str, Any]:
    """Create (no service_id) or replace (service_id present) a catalog service.
    category must be a known SERVICE_CATEGORIES member; price is integer paise."""
    _require_db(db)
    payload = payload or {}
    name = str(payload.get("name") or "").strip()
    if not name:
        raise RepairError("service name is required", status=422)
    category = str(payload.get("category") or "").strip().upper()
    if category not in SERVICE_CATEGORIES:
        raise RepairError(
            "category must be one of " + ", ".join(SERVICE_CATEGORIES), status=422
        )
    try:
        default_price_paise = int(payload.get("default_price_paise") or 0)
    except (TypeError, ValueError):
        raise RepairError("default_price_paise must be an integer", status=422)
    if default_price_paise < 0:
        raise RepairError("default_price_paise must be >= 0", status=422)
    enabled = [str(s) for s in (payload.get("enabled_store_ids") or []) if s]
    active = bool(payload.get("active", True))
    coll = db.get_collection(CATALOG_COLLECTION)
    now = _now_iso()
    sid = payload.get("service_id")
    if sid:
        from pymongo import ReturnDocument

        updated = coll.find_one_and_update(
            {"service_id": sid},
            {
                "$set": {
                    "name": name,
                    "category": category,
                    "default_price_paise": default_price_paise,
                    "enabled_store_ids": enabled,
                    "active": active,
                    "updated_by": actor.get("user_id"),
                    "updated_at": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            raise RepairError("service not found", status=404, code="not_found")
        return updated
    sid = "RSV-" + uuid.uuid4().hex[:10].upper()
    doc = {
        "_id": sid,
        "service_id": sid,
        "name": name,
        "category": category,
        "default_price_paise": default_price_paise,
        "enabled_store_ids": enabled,
        "active": active,
        "created_by": actor.get("user_id"),
        "created_at": now,
        "updated_at": now,
    }
    coll.insert_one(dict(doc))
    return doc


def list_services(db, store_id: str) -> List[Dict[str, Any]]:
    """Active services enabled for ``store_id`` (membership in enabled_store_ids).
    Fail-soft -> []."""
    if db is None:
        return []
    try:
        return list(
            db.get_collection(CATALOG_COLLECTION).find(
                {"active": True, "enabled_store_ids": store_id}
            )
        )
    except Exception:  # noqa: BLE001
        return []


def get_service(db, service_id: str) -> Optional[Dict[str, Any]]:
    if db is None:
        return None
    return db.get_collection(CATALOG_COLLECTION).find_one({"service_id": service_id})


# --- repair-job lifecycle --------------------------------------------------


def open_job(
    db, store_id: str, payload: Dict[str, Any], *, actor: Dict[str, Any]
) -> Dict[str, Any]:
    """Open a repair job at INTAKE. Validates the intake payload (422 on bad
    input). The status_history seeds with the INTAKE entry."""
    _require_db(db)
    if not store_id:
        raise RepairError("store_id is required", status=422)
    clean = validate_intake(payload)
    jid = "RJ-" + uuid.uuid4().hex[:10].upper()
    now = _now_iso()
    doc = {
        "_id": jid,
        "job_id": jid,
        "store_id": store_id,
        "service_id": (payload or {}).get("service_id"),
        "service_type": clean["service_type"],
        "customer_id": clean["customer_id"],
        "walkin_name": clean["walkin_name"],
        "walkin_mobile": clean["walkin_mobile"],
        "quoted_price_paise": clean["quoted_price_paise"],
        "status": STATUS_INTAKE,
        "status_history": [
            {"from": None, "to": STATUS_INTAKE, "by": actor.get("user_id"), "at": now}
        ],
        "created_by": actor.get("user_id"),
        "created_at": now,
        "updated_at": now,
    }
    db.get_collection(JOBS_COLLECTION).insert_one(dict(doc))
    return doc


def transition_job(
    db,
    job_id: str,
    store_id: str,
    to: str,
    *,
    actor: Dict[str, Any],
    note: Optional[str] = None,
    vendor_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Advance a job's status. THE GUARD: a single find_one_and_update keyed on
    the CURRENT (from) status + store_id -- two concurrent transitions of the
    same job produce exactly one winner (the loser's filter no longer matches
    -> 409). An illegal/terminal transition is rejected (422) before the write.
    Returns {job, from, to} so the router can decide the READY status SMS."""
    _require_db(db)
    coll = db.get_collection(JOBS_COLLECTION)
    job = coll.find_one({"job_id": job_id, "store_id": store_id})
    if job is None:
        raise RepairError("repair job not found", status=404, code="not_found")
    frm = job.get("status")
    to_u = str(to or "").upper()
    if not can_transition(frm, to_u):
        raise RepairError(
            "illegal transition " + str(frm) + " -> " + to_u,
            status=422,
            code="illegal_transition",
        )
    now = _now_iso()
    set_fields: Dict[str, Any] = {"status": to_u, "updated_at": now}
    if vendor_id and to_u == STATUS_SENT_TO_VENDOR:
        set_fields["vendor_id"] = vendor_id
    from pymongo import ReturnDocument

    updated = coll.find_one_and_update(
        {"job_id": job_id, "store_id": store_id, "status": frm},  # GUARD on from-state
        {
            "$set": set_fields,
            "$push": {
                "status_history": {
                    "from": frm,
                    "to": to_u,
                    "by": actor.get("user_id"),
                    "at": now,
                    "note": note,
                }
            },
        },
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise RepairError(
            "job changed concurrently (state no longer matches)",
            status=409,
            code="conflict",
        )
    return {"job": updated, "from": frm, "to": to_u}


def get_job(
    db, job_id: str, store_id: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    if db is None:
        return None
    query: Dict[str, Any] = {"job_id": job_id}
    if store_id:
        query["store_id"] = store_id
    return db.get_collection(JOBS_COLLECTION).find_one(query)


def list_jobs(db, store_id: str, status: Optional[str] = None) -> List[Dict[str, Any]]:
    if db is None:
        return []
    query: Dict[str, Any] = {"store_id": store_id}
    if status:
        query["status"] = str(status).upper()
    try:
        return list(db.get_collection(JOBS_COLLECTION).find(query))
    except Exception:  # noqa: BLE001
        return []


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
    "CATALOG_COLLECTION",
    "JOBS_COLLECTION",
    "ensure_indexes",
    "upsert_service",
    "list_services",
    "get_service",
    "open_job",
    "transition_job",
    "get_job",
    "list_jobs",
]

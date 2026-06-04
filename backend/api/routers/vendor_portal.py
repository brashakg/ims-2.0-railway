"""
IMS 2.0 - Vendor Portal Router (PUBLIC, token-auth)
====================================================
Read+post surface for external lens labs (Zeiss, Essilor, etc.) tracking the
workshop jobs IMS hands them off to.

This router is mounted *outside* the JWT-protected wrapper. Auth is a UUID
bearer token — `token_id` from `vendor_portal_tokens` — passed as a path
parameter (the URL admin shares with the lab IS the credential, same shape
Stripe / Linear use for public-link surfaces). Each request:

  1. Resolves token → vendor_id, or 401 if revoked / expired / missing
  2. Bumps a per-token rate-limit counter (60/min — labs don't poll faster)
  3. Filters workshop_jobs to ONLY rows with matching `vendor_id`
  4. Redacts customer PII to initials before returning

Status posts from the portal are stamped `source='vendor_portal'`; the
matching admin-side handler in workshop.py stamps `source='ims_user'`. The
frontend treats them differently (vendor blue, ims green) so the audit trail
is read at a glance.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional
import logging
import time

from fastapi import APIRouter, HTTPException, Path
from pydantic import AliasChoices, BaseModel, Field

from ..dependencies import (
    get_vendor_portal_token_repository,
    get_workshop_repository,
    get_audit_repository,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ============================================================================
# RATE LIMIT — per-token sliding window
# ============================================================================
# 60 requests/min/token. Lens labs don't legitimately poll faster than ~once
# every two seconds; this caps an automation that's gone rogue without
# punishing a human refreshing a few times. Window keyed on token_id, NOT
# IP — each lab gets their own bucket so one busy lab can't squeeze another.
_PORTAL_RATE_LIMIT_PER_MIN = 60
_PORTAL_RATE_WINDOW = 60.0
_portal_request_log: dict = defaultdict(list)


def _check_portal_rate(token_id: str) -> None:
    """Raise 429 when token_id has spent its bucket. Sliding window."""
    now = time.time()
    cutoff = now - _PORTAL_RATE_WINDOW
    bucket = [t for t in _portal_request_log[token_id] if t > cutoff]
    if len(bucket) >= _PORTAL_RATE_LIMIT_PER_MIN:
        raise HTTPException(
            status_code=429,
            detail=f"Vendor portal rate limit exceeded ({_PORTAL_RATE_LIMIT_PER_MIN}/min)",
        )
    bucket.append(now)
    _portal_request_log[token_id] = bucket


# ============================================================================
# AUTH HELPER
# ============================================================================


def _resolve_token(token_id: str) -> Dict[str, Any]:
    """Validate the bearer token and return its row. 401 if invalid."""
    if not token_id:
        raise HTTPException(status_code=401, detail="Vendor portal token required")
    _check_portal_rate(token_id)
    repo = get_vendor_portal_token_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Vendor portal storage unavailable")
    doc = repo.find_active(token_id)
    if doc is None:
        raise HTTPException(
            status_code=401, detail="Invalid or expired vendor portal token"
        )
    # Bump last_used_at; ignore failure
    try:
        repo.touch(token_id)
    except Exception:
        pass
    return doc


# ============================================================================
# PII REDACTION
# ============================================================================


def _initials(name: Optional[str]) -> str:
    """Return up to two initials separated by a dot. Vendors don't need
    full customer names — they need a stable handle that lets a
    technician confirm they're working on the right job by reading it
    back to the IMS staff over the phone."""
    if not name or not isinstance(name, str):
        return "?"
    parts = [p for p in name.strip().split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:1].upper() + "."
    return f"{parts[0][:1].upper()}.{parts[-1][:1].upper()}."


def _redact_job_for_vendor(job: Dict, vendor_id: str) -> Dict:
    """Strip PII; project only fields the lens lab needs.

    Specifically NEVER returned: customer_phone, address, email, full
    customer_name. Lens lab needs: job number, prescription summary, frame
    type, due date, current vendor status.
    """
    fitting = job.get("fitting_details") or {}
    return {
        "job_id": job.get("job_id"),
        "job_number": job.get("job_number"),
        "order_number": job.get("order_number"),
        "customer_initials": _initials(job.get("customer_name")),
        "frame_brand": (job.get("frame_details") or {}).get("brand"),
        "frame_model": (job.get("frame_details") or {}).get("model"),
        "lens_type": (job.get("lens_details") or {}).get("type"),
        "lens_coating": (job.get("lens_details") or {}).get("coating")
        or fitting.get("coating"),
        "lens_diameter": fitting.get("dia"),
        "fitting_height": fitting.get("fh"),
        "base_curve": fitting.get("base_curve"),
        "tint": fitting.get("tint"),
        "expected_date": job.get("expected_date"),
        "expected_lens_receive_date": fitting.get("expected_lens_receive_date"),
        "vendor_id": job.get("vendor_id"),
        "vendor_order_id": job.get("vendor_order_id"),
        "vendor_status": job.get("vendor_status"),
        "vendor_dispatch_date": job.get("vendor_dispatch_date"),
        "vendor_received_date": job.get("vendor_received_date"),
        "vendor_tracking_url": job.get("vendor_tracking_url"),
        "vendor_status_history": [
            {
                "status": h.get("status"),
                "note": h.get("note"),
                "source": h.get("source"),
                "logged_at": h.get("logged_at"),
            }
            for h in (job.get("vendor_status_history") or [])
        ],
        "ims_status": job.get("status"),
    }


# ============================================================================
# SCHEMAS
# ============================================================================


# Lens-lab status taxonomy. Keep it short — labs don't want a 12-step
# state machine, they want enough granularity to communicate "I got it",
# "I'm cutting it", "it's coming back to you", "something's broken".
PORTAL_STATUSES = {
    "RECEIVED",  # Lab acknowledges the job
    "IN_PRODUCTION",  # Cutting / fitting in progress
    "DISPATCHED",  # On its way back to the store
    "DELIVERED",  # IMS confirms receipt
    "ON_HOLD",  # Blocked — waiting for clarification
    "CANCELLED",  # Won't be fulfilled
}


class VendorPortalStatusUpdate(BaseModel):
    status: str = Field(..., description="One of " + ", ".join(sorted(PORTAL_STATUSES)))
    note: Optional[str] = Field(None, max_length=500)
    # Accept BOTH the canonical key and the key the public lab-portal FE actually
    # submits ("vendor_tracking_url"). Without this the unknown key was dropped by
    # pydantic, so the lab's tracking URL never reached the job card.
    tracking_url: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("tracking_url", "vendor_tracking_url"),
        max_length=500,
    )
    vendor_order_id: Optional[str] = Field(None, max_length=100)


# ============================================================================
# ENDPOINTS
# ============================================================================


@router.get("/{token_id}/jobs")
async def list_vendor_jobs(
    token_id: str = Path(..., min_length=8, max_length=64),
):
    """List the open workshop jobs assigned to this vendor.

    "Open" = anything not yet DELIVERED on the IMS side. The vendor sees
    each job redacted (initials, no phone). Sort: oldest expected_date
    first (most urgent at the top).
    """
    token = _resolve_token(token_id)
    vendor_id = token.get("vendor_id")
    repo = get_workshop_repository()
    if repo is None:
        return {"vendor_id": vendor_id, "jobs": [], "total": 0}

    # Mongo filter: this vendor + still-active job. We filter on `vendor_id`
    # which the admin sets via PATCH /workshop/jobs/{id}/vendor.
    rows = (
        repo.find_many(
            {"vendor_id": vendor_id, "status": {"$nin": ["DELIVERED", "CANCELLED"]}},
            sort=[("expected_date", 1)],
            limit=200,
        )
        or []
    )

    return {
        "vendor_id": vendor_id,
        "vendor_name": token.get("vendor_name"),
        "jobs": [_redact_job_for_vendor(j, vendor_id) for j in rows],
        "total": len(rows),
        "as_of": datetime.now().isoformat(),
    }


@router.get("/{token_id}/jobs/{job_id}")
async def get_vendor_job(
    token_id: str = Path(..., min_length=8, max_length=64),
    job_id: str = Path(...),
):
    """Single-job lookup. 404 if it isn't this vendor's. We refuse to confirm
    the existence of jobs assigned to other vendors — same response either
    way blocks enumeration."""
    token = _resolve_token(token_id)
    vendor_id = token.get("vendor_id")
    repo = get_workshop_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Workshop storage unavailable")
    job = repo.find_by_id(job_id)
    if job is None or job.get("vendor_id") != vendor_id:
        raise HTTPException(status_code=404, detail="Job not found")
    return _redact_job_for_vendor(job, vendor_id)


@router.post("/{token_id}/jobs/{job_id}/status")
async def post_vendor_status(
    payload: VendorPortalStatusUpdate,
    token_id: str = Path(..., min_length=8, max_length=64),
    job_id: str = Path(...),
):
    """Lab posts a status update.

    Side-effects:
      - appends to `vendor_status_history` with source='vendor_portal'
      - sets `vendor_status` to the new value
      - DISPATCHED auto-stamps `vendor_dispatch_date`
      - DELIVERED auto-stamps `vendor_received_date`
      - audit-logs the call
    """
    token = _resolve_token(token_id)
    vendor_id = token.get("vendor_id")

    if payload.status not in PORTAL_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown status. Allowed: {', '.join(sorted(PORTAL_STATUSES))}",
        )

    repo = get_workshop_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Workshop storage unavailable")

    job = repo.find_by_id(job_id)
    if job is None or job.get("vendor_id") != vendor_id:
        raise HTTPException(status_code=404, detail="Job not found")

    now = datetime.now()
    history_entry = {
        "status": payload.status,
        "note": payload.note,
        "source": "vendor_portal",
        "logged_by": vendor_id,  # vendor_id is the closest thing to identity here
        "logged_at": now.isoformat(),
    }
    history = list(job.get("vendor_status_history") or [])
    history.append(history_entry)

    update: Dict[str, Any] = {
        "vendor_status": payload.status,
        "vendor_status_history": history,
        "vendor_status_updated_at": now,
    }
    if payload.tracking_url:
        update["vendor_tracking_url"] = payload.tracking_url
    if payload.vendor_order_id:
        update["vendor_order_id"] = payload.vendor_order_id
    if payload.status == "DISPATCHED" and not job.get("vendor_dispatch_date"):
        update["vendor_dispatch_date"] = now.isoformat()
    if payload.status == "DELIVERED" and not job.get("vendor_received_date"):
        update["vendor_received_date"] = now.isoformat()

    repo.update(job_id, update)

    # Audit log — every vendor-portal write goes in the trail
    try:
        audit = get_audit_repository()
        if audit is not None:
            audit.create(
                {
                    "action": "workshop.vendor_status",
                    "entity_type": "workshop_job",
                    "entity_id": job_id,
                    "store_id": job.get("store_id"),
                    "user_id": f"vendor-portal:{vendor_id}",
                    "detail": {
                        "vendor_id": vendor_id,
                        "vendor_name": token.get("vendor_name"),
                        "token_id": token_id,
                        "status": payload.status,
                        "source": "vendor_portal",
                        "note": payload.note,
                    },
                }
            )
    except Exception as e:
        logger.warning(f"vendor portal audit-log failed: {e}")

    return {
        "job_id": job_id,
        "vendor_status": payload.status,
        "logged_at": history_entry["logged_at"],
        "message": f"Status recorded as {payload.status}",
    }

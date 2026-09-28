"""Shared workshop plumbing: the router, the logger, the role tuples, the
lens-status / vendor-status / job-transition / QC-input taxonomies, the
rework cap and the F9 lens DC hardlock.

The import block below is the ORIGINAL workshop.py block, kept whole on
purpose: most names are no longer referenced in THIS file (each sub-module
imports what it needs directly), but they are part of the module surface
the single file exposed and __init__.py re-exports them, so
``from api.routers.workshop import get_workshop_repository`` and the tests
that monkeypatch them keep working unchanged.

Moved verbatim out of the 3,528-line api/routers/workshop.py (Wave 6
package split): no path, method, dependency, status code, response_model,
default or validation was changed.
"""

from fastapi import APIRouter, HTTPException, Depends, Query, Body, Response
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import date, datetime, timedelta
import uuid
import logging

logger = logging.getLogger(__package__)

from ..auth import get_current_user, require_roles
from ...services import spoilage_analytics
from ...dependencies import (
    get_db,
    get_workshop_repository,
    get_order_repository,
    get_audit_repository,
    get_vendor_repository,
    validate_store_access,
    can_access_store_scoped,
)

# Roles allowed to drive the lens lifecycle + ready-notify. SUPERADMIN passes
# automatically via require_roles, so it is intentionally not listed.
WORKSHOP_ROLES = (
    "WORKSHOP_STAFF",
    "STORE_MANAGER",
    "AREA_MANAGER",
    "ADMIN",
)

# BUG-092: the sales-confirmation gate (fitting_details.confirmed_by_sales) and
# fitting-detail edits are a SALES act -- WORKSHOP_STAFF must not self-confirm the
# very gate that authorises their own work. POS captures these at sale time, so
# the allowed set is the sales-facing + manager roles (NOT workshop staff).
# SUPERADMIN passes automatically via require_roles.
_FITTING_ROLES = (
    "SALES_STAFF",
    "SALES_CASHIER",
    "CASHIER",
    "STORE_MANAGER",
    "AREA_MANAGER",
    "ADMIN",
)

# Forward-only lens-order lifecycle for a workshop job. The lens is ordered
# from the lab, received into the store, then mounted into the frame. Each
# transition stamps a timestamp field (see LENS_STATUS_TIMESTAMP_FIELD).
LENS_STATUS_ORDER = ["NOT_ORDERED", "ORDERED", "RECEIVED", "MOUNTED"]
LENS_STATUS_TIMESTAMP_FIELD = {
    "ORDERED": "lens_ordered_at",
    "RECEIVED": "lens_received_at",
    "MOUNTED": "lens_mounted_at",
}


def _next_lens_status_ok(current, target) -> bool:
    """Pure transition guard for the lens lifecycle. No DB access.

    Returns True only when `target` is the IMMEDIATE next step after
    `current` along NOT_ORDERED -> ORDERED -> RECEIVED -> MOUNTED. Skips
    (e.g. NOT_ORDERED -> RECEIVED), backwards moves, no-ops, and any value
    not in LENS_STATUS_ORDER all return False.

    A missing / empty / unknown current status is treated as NOT_ORDERED so a
    legacy job with no lens_status set can still be advanced to ORDERED.
    """
    cur = current if current in LENS_STATUS_ORDER else "NOT_ORDERED"
    if target not in LENS_STATUS_ORDER:
        return False
    try:
        return LENS_STATUS_ORDER.index(target) == LENS_STATUS_ORDER.index(cur) + 1
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# F9 -- LENS DC HARDLOCK
# ---------------------------------------------------------------------------
# An external-lab lens (lens_status=ORDERED) physically arrives at the store
# with a Delivery Challan (DC). The DC is the mandatory accountability checkpoint
# between goods arrival and workshop work: a job for an ORDERED lens may NOT be
# opened until at least one accepted DC covering that lens SKU exists at that
# store -- otherwise lenses can be worked (and later lost / phantom-paid) with no
# procurement record. The lock is operationally gated by two purchase_settings
# keys so it can be rolled out without a redeploy and never retroactively blocks
# pre-existing jobs.

# Roles allowed to OVERRIDE the hardlock (create an ORDERED-lens job with no DC).
_DC_HARDLOCK_OVERRIDE_ROLES = ("ADMIN", "SUPERADMIN")


def _resolve_dc_workshop_settings(db) -> dict:
    """Read the F9 hardlock flags from the single purchase_settings doc.

    Returns {require_dc_for_workshop: bool, dc_hardlock_from_date: str|None}.
    Default require_dc_for_workshop = TRUE (the control is on by default in prod;
    the operator sets it false for a grace period). Fail-soft: any DB error / no
    doc -> the safe default (lock ON, no cutover so it applies to new jobs)."""
    require = True
    cutover = None
    try:
        if db is not None:
            doc = db.get_collection("purchase_settings").find_one(
                {"_id": "default"}, {"_id": 0}
            )
            if isinstance(doc, dict):
                if doc.get("require_dc_for_workshop") is not None:
                    require = bool(doc.get("require_dc_for_workshop"))
                cutover = doc.get("dc_hardlock_from_date")
    except Exception:
        pass
    return {"require_dc_for_workshop": require, "dc_hardlock_from_date": cutover}


def _check_dc_hardlock(db, lens_status, lens_product_id, store_id, created_at, current_user, override_reason):
    """F9 DC HARDLOCK guard. Returns dict(override_applied: bool, reason: str|None).

    `lens_status` is the TOP-LEVEL job field ("NOT_ORDERED"/"ORDERED"/"RECEIVED"/
    "MOUNTED"), passed explicitly by the caller -- it is NOT a key inside the
    lens_details Rx spec (reading it from there was a no-op: every job sailed
    through). `lens_product_id` is the lens SKU to match a DC against.

    Hard-blocks (raises 422 with code DC_HARDLOCK) when:
      * the lens is external-lab (lens_status == ORDERED), AND
      * require_dc_for_workshop is true, AND
      * the job's created_at is on/after dc_hardlock_from_date (so existing
        pending jobs are never retroactively blocked), AND
      * no accepted DELIVERY_CHALLAN GRN covers the lens product_id at store_id.

    An ADMIN+ may bypass with override_reason (audited by the caller). In-house
    lenses (lens_status != ORDERED) are exempt and pass straight through.

    SHARED: both barcode-scan paths (labels.scan_advance + lab_routing.
    advance_lab_station) reach this exact function through the single scan gate
    ``evaluate_scan_transition_gate`` for their scan-driven -> IN_PROGRESS check,
    passing no override_reason and no privileged roles, and translating the 422
    raise into a status-hold (gate_block="DC_REQUIRED") -- a physical scan is
    never failed. Keep flag/cutover/exemption/DC-lookup semantics HERE, once.
    """
    # Only external-lab (ORDERED) lenses are gated; in-house stock is exempt.
    if lens_status != "ORDERED":
        return {"override_applied": False, "reason": None}

    settings = _resolve_dc_workshop_settings(db)
    if not settings["require_dc_for_workshop"]:
        return {"override_applied": False, "reason": None}

    # Cutover: a job created before dc_hardlock_from_date is never blocked.
    cutover = settings.get("dc_hardlock_from_date")
    if cutover and created_at:
        try:
            # ISO string comparison is correct for YYYY-MM-DD[THH:MM:SS] prefixes.
            if str(created_at) < str(cutover):
                return {"override_applied": False, "reason": None}
        except Exception:
            pass

    product_id = lens_product_id
    has_dc = False
    if db is not None and product_id:
        try:
            dc = db.get_collection("grns").find_one(
                {
                    "grn_subtype": "DELIVERY_CHALLAN",
                    "status": "ACCEPTED",
                    "store_id": store_id,
                    "items.product_id": product_id,
                },
                {"_id": 0, "grn_id": 1},
            )
            has_dc = dc is not None
        except Exception:
            has_dc = False

    if has_dc:
        return {"override_applied": False, "reason": None}

    # No DC -> blocked, unless an authorised role overrides with a reason.
    roles = current_user.get("roles") or []
    can_override = any(r in roles for r in _DC_HARDLOCK_OVERRIDE_ROLES)
    reason = (override_reason or "").strip()
    if reason:
        if not can_override:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "DC_HARDLOCK_OVERRIDE_FORBIDDEN",
                    "message": (
                        "Only an Admin can override the DC hardlock. Ask the "
                        "Store Manager to log the Delivery Challan first."
                    ),
                },
            )
        return {"override_applied": True, "reason": reason}

    raise HTTPException(
        status_code=422,
        detail={
            "code": "DC_HARDLOCK",
            "message": (
                "No Delivery Challan logged for this lens. Ask the Store "
                "Manager to record the DC before opening the workshop job."
            ),
            "product_id": product_id,
            "store_id": store_id,
        },
    )


# Vendor-side status taxonomy used by the admin endpoints below. Mirrors
# vendor_portal.PORTAL_STATUSES — kept duplicated to avoid a cross-router
# import cycle (vendor_portal imports from workshop's dependencies; if
# workshop imported back we'd have a circle at module-load time).
ADMIN_VENDOR_STATUSES = {
    "RECEIVED",
    "IN_PRODUCTION",
    "DISPATCHED",
    "DELIVERED",
    "ON_HOLD",
    "CANCELLED",
}

# Valid workshop job state transitions
VALID_JOB_TRANSITIONS = {
    "PENDING": {"IN_PROGRESS", "CANCELLED"},
    "IN_PROGRESS": {"COMPLETED", "CANCELLED"},
    "COMPLETED": {"READY", "QC_FAILED"},  # QC pass → READY, QC fail → QC_FAILED
    "QC_FAILED": {"IN_PROGRESS", "CANCELLED"},  # rework sends back to IN_PROGRESS
    "READY": {"DELIVERED"},
    "DELIVERED": set(),
    "CANCELLED": set(),
}

# BUG-116d: cap QC_FAILED -> IN_PROGRESS reworks. A QC-failed lens job sent back
# for rework could otherwise churn the QC-fail -> rework loop forever. Owner
# decision: allow 2 reworks; beyond that only a manager may override and send it
# back again. MAX_REWORK is the number of reworks ALREADY done at which a fresh
# rework is blocked for non-managers.
MAX_REWORK = 2
_REWORK_OVERRIDE_ROLES = {"SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"}

# ---------------------------------------------------------------------------
# WHICH JOB STATES QC MAY BE RUN ON
# ---------------------------------------------------------------------------
# QC has to be runnable on every state the QC handover gate can strand a job in,
# or the gate becomes a dead end at a live counter.
#
#   IN_PROGRESS  is the state the SCAN flow actually parks a held job in. No
#                station in lab_routing.DEFAULT_STATIONS ever sets COMPLETED
#                (INTAKE -> IN_PROGRESS, EDGING/COATING/QC_LAB -> None,
#                DISPATCH -> READY, PICKUP -> DELIVERED), so a job whose
#                DISPATCH -> READY leg is HELD for missing QC keeps the status
#                it had at INTAKE. Refusing QC there made the one state the
#                gate produces the one state QC would not accept.
#   COMPLETED    the bench-finished state (the manual Workshop-page route).
#   QC_FAILED    a re-check after rework.
#   READY        a job already on the pickup shelf with no QC record; QC in
#                place is what clears it for handover.
#
# PENDING stays EXCLUDED on purpose: a QC pass routes the job to READY, which
# would let an unstarted job skip the sales-confirm gate and the F9 DC hardlock
# that guard the -> IN_PROGRESS leg. DELIVERED / CANCELLED stay excluded because
# the job is gone -- retro-QC'ing a handed-over job would rewrite history.
# (All three exclusions are pinned by tests in test_workshop_qc_checklist.py.)
# PROCESSING is the legacy frontend alias of IN_PROGRESS (update_job_status maps
# it via STATUS_ALIASES). A stored PROCESSING job must be QC-able for the same
# reason IN_PROGRESS must be: otherwise the handover gate would block it with a
# remedy the API refuses.
_QC_INPUT_STATUSES = ("IN_PROGRESS", "PROCESSING", "COMPLETED", "QC_FAILED", "READY")
_QC_INPUT_STATUS_MESSAGE = (
    "QC can only be recorded while the job is in progress, completed, QC-failed "
    "or ready for pickup (current: {status})."
)


router = APIRouter()

"""
IMS 2.0 - Workshop Router
==========================
Workshop job management endpoints
"""

from fastapi import APIRouter, HTTPException, Depends, Query, Body
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import date, datetime
import uuid
import logging

logger = logging.getLogger(__name__)

from .auth import get_current_user, require_roles
from ..dependencies import (
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

    SHARED: the F2 lab-routing scan path (services/lab_routing._dc_gate_block)
    REUSES this exact function for its scan-driven -> IN_PROGRESS gate, passing
    no override_reason and no privileged roles, and translating the 422 raise
    into a status-hold (gate_block="DC_REQUIRED") -- a physical scan is never
    failed. Keep flag/cutover/exemption/DC-lookup semantics HERE, defined once.
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

router = APIRouter()


# ============================================================================
# SCHEMAS
# ============================================================================


class FittingDetails(BaseModel):
    """Phase 6.8 — physical measurements the sales staff hands over to
    the workshop technician for lens cutting / fitting. All fields are
    optional individually, but `confirmed_by_sales` must be True for
    the workshop to accept the job (sales explicitly confirming that
    power + product details are correct).

    CLI-6 adds the four progressive-lens fitting parameters that matter
    for high-index / progressive / occupational lenses and directly
    impact remake rates when mis-measured:
      - segment_height: seg height (mm) — distance from bottom of lens to
        the optical centre / progression start. Critical for progressives.
      - pantoscopic_tilt: degrees the frame tilts toward the face (typical
        8-12 deg). Mis-tilt shifts the effective Rx by ~0.25D.
      - vertex_distance: mm from the back surface to the cornea (standard
        12 mm). Every mm deviation shifts effective power for strong Rx.
      - wrap_angle: frame wrap / face-form angle (degrees). High wrap shifts
        the effective cylinder axis on peripheral gaze.
    """

    dia: Optional[str] = None  # Lens diameter (e.g. "65", "70")
    fh: Optional[str] = None  # Fitting height (for progressive/bifocal)
    b_size: Optional[str] = None  # Lens vertical measurement
    dbl: Optional[str] = None  # Distance between lenses (bridge width)
    tint: Optional[str] = None  # Tint colour / percentage
    base_curve: Optional[str] = None  # Base curve (e.g. "6", "8")
    coating: Optional[str] = (
        None  # Coating name (redundant with lens_details.coating but captured here for sales confirmation)
    )
    other: Optional[str] = None  # Free-text notes
    order_date: Optional[str] = None  # ISO date (auto-filled on save)
    order_time: Optional[str] = None  # HH:MM (auto-filled on save)
    ordered_by: Optional[str] = None  # User id of sales staff
    ordered_by_name: Optional[str] = None
    expected_lens_receive_date: Optional[date] = None
    # Phase 6.8 — vendor (lens supplier) PO reference. Sales enters the ID
    # issued when the lens was ordered from Zeiss / Essilor / etc; workshop
    # + finance use it to reconcile incoming lens stock.
    vendor_order_id: Optional[str] = None
    confirmed_by_sales: bool = False  # Must be True to submit
    confirmed_at: Optional[str] = None  # ISO timestamp

    # CLI-6 — progressive lens fitting parameters (all optional; only
    # relevant for progressive / high-index / occupational lenses but stored
    # even for single-vision so the data is available for lab scorecards)
    segment_height: Optional[str] = None   # mm, e.g. "19", "22"
    pantoscopic_tilt: Optional[str] = None  # degrees, e.g. "10"
    vertex_distance: Optional[str] = None   # mm, e.g. "12", "13.5"
    wrap_angle: Optional[str] = None        # degrees (face-form), e.g. "5"


class WorkshopJobCreate(BaseModel):
    order_id: str
    frame_details: dict
    lens_details: dict
    prescription_id: str
    fitting_instructions: Optional[str] = None
    special_notes: Optional[str] = None
    expected_date: date
    # Phase 6.8 — optional at create time; sales fills via a modal
    # right after order confirmation (PATCH /jobs/{id}/fitting-details)
    fitting_details: Optional[FittingDetails] = None
    # F9 -- DC HARDLOCK override. When an external-lab lens (lens_status=ORDERED)
    # has no logged Delivery Challan, the create is HARD-BLOCKED (422) -- an
    # ADMIN+ may bypass by supplying a reason (audited). Ignored for in-house
    # lenses / when the lock is disabled.
    override_reason: Optional[str] = None


class WorkshopJobUpdate(BaseModel):
    fitting_instructions: Optional[str] = None
    special_notes: Optional[str] = None
    expected_date: Optional[date] = None


class FittingDetailsUpdate(BaseModel):
    """Payload for PATCH /workshop/jobs/{id}/fitting-details."""

    fitting_details: FittingDetails


class WorkshopVendorPatch(BaseModel):
    """Payload for PATCH /workshop/jobs/{id}/vendor — admin assigns / edits
    the lens lab handling this job. All fields are individually optional;
    we only update what's supplied (so a partial form save doesn't blow
    away tracking_url, etc.)."""

    vendor_id: Optional[str] = None
    vendor_order_id: Optional[str] = None
    vendor_tracking_url: Optional[str] = None
    vendor_dispatch_date: Optional[str] = None  # ISO date
    vendor_received_date: Optional[str] = None


class WorkshopVendorStatusBody(BaseModel):
    """Payload for POST /workshop/jobs/{id}/vendor-status — admin (IMS user)
    logging a vendor-status update on behalf of the lab (e.g. lab phoned
    them). Source on the resulting history row is `ims_user`."""

    status: str
    note: Optional[str] = None


class LensStatusBody(BaseModel):
    """Payload for POST /workshop/jobs/{id}/lens-status — advance the lens
    lifecycle by exactly one forward step (validated by _next_lens_status_ok)."""

    status: str


class QcCheckItem(BaseModel):
    """A single structured checklist item for the QC checklist endpoint."""

    key: str = Field(
        ..., description="Checklist item key, e.g. 'power', 'fitting', 'cosmetic'"
    )
    label: str = Field(..., description="Human-readable label")
    passed: bool = Field(..., description="True if this item passed")
    note: Optional[str] = Field(None, description="Optional note for this item")


class QcChecklistBody(BaseModel):
    """Payload for POST /workshop/jobs/{id}/qc-checklist.

    Carries a structured per-item checklist. A job cannot advance to
    READY_FOR_PICKUP unless either every item passed or an explicit waiver
    is provided with a reason.
    """

    checklist: List[QcCheckItem] = Field(..., min_length=1, description="One or more QC check items (cannot be empty)")
    overall_notes: Optional[str] = Field(
        None, description="Free-text summary / rework instructions"
    )
    # Optional waiver path: a manager can override a failed item with a reason.
    waived: bool = Field(
        False,
        description=(
            "If True the QC result is treated as passed despite individual failures."
            " Requires waive_reason."
        ),
    )
    waive_reason: Optional[str] = Field(
        None, description="Mandatory when waived=True. Explain why QC is being waived."
    )


class StatusBody(BaseModel):
    """Payload for PATCH /workshop/jobs/{id}/status.
    Accepts status + optional notes as a JSON body (the frontend sends PATCH
    with a JSON body, not query params, so this model is needed for
    compatibility)."""

    status: str
    notes: Optional[str] = None
    # F9 DC HARDLOCK override (ADMIN+ + reason) when advancing an external-lab
    # (lens_status=ORDERED) job to IN_PROGRESS with no logged Delivery Challan.
    override_reason: Optional[str] = None


# ---------------------------------------------------------------------------
# F2 -- internal lab routing (disposable job cards). See
# api/services/lab_routing.py for the routing brain.
# ---------------------------------------------------------------------------


class LabScanBody(BaseModel):
    """Payload for POST /workshop/scan -- a barcode scan at a lab bench.

    scanned_code: the value read off the disposable job card (the job_number or
                  job_id). Resolves WHICH job to advance.
    station_code: the bench being scanned at (INTAKE/EDGING/COATING/QC_LAB/
                  DISPATCH/PICKUP). The forward-only gate rejects an out-of-order
                  scan.
    store_id:     optional store hint (HQ roles scanning across stores).

    NOTE: any client-supplied dwell field is IGNORED -- dwell is always
    server-computed from the stored station_timestamps.
    """

    scanned_code: str
    station_code: str
    store_id: Optional[str] = None


class LabStationUpsert(BaseModel):
    """Payload for POST /workshop/stations -- configure one lab station for a
    store. Keyed on store_id + code. All fields besides code are optional so a
    partial save (e.g. just toggling is_active) does not clobber the rest."""

    code: str
    store_id: Optional[str] = None
    label: Optional[str] = None
    sequence_order: Optional[int] = None
    is_active: Optional[bool] = None
    target_dwell_minutes: Optional[int] = None
    advances_job_status: Optional[str] = None
    auto_notify_customer: Optional[bool] = None


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def generate_job_number(repo=None) -> str:
    """Generate unique workshop job number with collision retry."""
    for _ in range(5):
        candidate = (
            f"WS-{datetime.now().strftime('%y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
        )
        if repo is not None:
            try:
                existing = repo.collection.find_one({"job_number": candidate})
                if existing:
                    continue  # collision — retry
            except Exception:
                pass
        return candidate
    # Fallback: use full UUID to guarantee uniqueness
    return f"WS-{datetime.now().strftime('%y%m%d')}-{uuid.uuid4().hex[:12].upper()}"


def _assert_job_store_access(job: dict, current_user: dict) -> None:
    """Object-level IDOR guard: existence-hide a workshop job whose store the
    caller can't reach. A workshop job carries customer + medical Rx data and
    drives real lens-lifecycle / QC state, so a store-scoped caller must never
    read or mutate another store's job. SUPERADMIN/ADMIN bypass; an unattributed
    legacy doc (no store_id) is admin-only -- both handled by
    dependencies.can_access_store_scoped. 404 (not 403) mirrors GET /{id} so the
    job's existence isn't confirmed to a cross-store caller."""
    if not can_access_store_scoped(job.get("store_id"), current_user):
        raise HTTPException(status_code=404, detail="Workshop job not found")


def job_to_frontend(job: dict) -> dict:
    """Convert workshop job from snake_case to camelCase for frontend"""
    if job is None:
        return job

    key_map = {
        "job_id": "id",
        "job_number": "jobNumber",
        "order_id": "orderId",
        "order_number": "orderNumber",
        "store_id": "storeId",
        "customer_id": "customerId",
        "customer_name": "customerName",
        "customer_phone": "customerPhone",
        "frame_details": "frameDetails",
        "frame_name": "frameName",
        "frame_barcode": "frameBarcode",
        "lens_details": "lensDetails",
        "lens_type": "lensType",
        "prescription_id": "prescriptionId",
        "fitting_instructions": "fittingInstructions",
        "special_notes": "notes",
        "technician_id": "assignedTo",
        "assigned_to": "assignedTo",
        "expected_date": "expectedDate",
        "promised_date": "promisedDate",
        "created_at": "createdAt",
        "completed_at": "completedAt",
        "updated_at": "updatedAt",
        "updated_by": "updatedBy",
        "created_by": "createdBy",
    }

    result = {}
    for key, value in job.items():
        # Drop MongoDB's BSON ObjectId — same reasoning as
        # orders.order_to_frontend: Pydantic/FastAPI's default JSON
        # encoder can't serialise ObjectId, and workshop_jobs carry
        # their own job_id/job_number so `_id` isn't needed in responses.
        if key == "_id":
            continue
        if key in key_map:
            result[key_map[key]] = value
        else:
            # Keep other fields as-is
            result[key] = value

    return result


# ============================================================================
# ENDPOINTS
# ============================================================================


# NOTE: Specific routes MUST come before /jobs/{job_id}


@router.get("")
@router.get("/")
async def get_workshop_root():
    """Root endpoint for workshop job list"""
    return {
        "module": "workshop",
        "status": "active",
        "message": "workshop jobs endpoint ready",
    }


@router.get("/pending")
async def get_pending_jobs(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get pending workshop jobs"""
    repo = get_workshop_repository()
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")

    if repo is not None:
        jobs = repo.find_pending(active_store)
        jobs_formatted = [job_to_frontend(j) for j in jobs]
        return {"jobs": jobs_formatted, "total": len(jobs_formatted)}

    return {"jobs": [], "total": 0}


@router.get("/overdue")
async def get_overdue_jobs(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get overdue workshop jobs"""
    repo = get_workshop_repository()
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")

    if repo is not None:
        jobs = repo.find_overdue(active_store)
        jobs_formatted = [job_to_frontend(j) for j in jobs]
        return {"jobs": jobs_formatted, "total": len(jobs_formatted)}

    return {"jobs": [], "total": 0}


@router.get("/ready")
async def get_ready_jobs(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get jobs ready for delivery"""
    repo = get_workshop_repository()
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")

    if repo is not None:
        jobs = repo.find_ready(active_store)
        jobs_formatted = [job_to_frontend(j) for j in jobs]
        return {"jobs": jobs_formatted, "total": len(jobs_formatted)}

    return {"jobs": [], "total": 0}


@router.get("/technician-workload")
async def get_technician_workload(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get technician workload summary"""
    repo = get_workshop_repository()
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")

    if repo and active_store:
        workload = repo.get_technician_workload(active_store)
        return {"workload": workload}

    return {"workload": []}


# ---------------------------------------------------------------------------
# Phase 6.4 — single-shot KPIs for the workshop dashboard header.
# The frontend currently computes Active / Urgent / Ready / Overdue on the
# client from the full job list. That works at small scale but means every
# workshop page load pulls every job in the store. This endpoint lets the
# client drop 4 HTTP calls and ~a few hundred KB of JSON in favour of one
# small summary call — and as a bonus exposes `avg_turnaround_days` and
# `completed_today` which the client couldn't cheaply compute before.
# ---------------------------------------------------------------------------


@router.get("/dashboard-kpis")
async def get_dashboard_kpis(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """
    Aggregated workshop KPIs for the dashboard header.

    Returns:
        pending            — PENDING + IN_PROGRESS (i.e. "Active Jobs")
        in_progress        — IN_PROGRESS only
        qc_failed          — jobs sent back for rework
        ready_for_pickup   — READY status
        overdue            — pending/in_progress past expected_date
        completed_today    — COMPLETED or READY with completed_at == today
        delivered_today    — DELIVERED with status_updated_at == today
        avg_turnaround_days — mean (completed_at - created_at) across the
                              last 100 closed jobs. `None` if fewer than 5
                              samples exist (avoids noisy averages).

    Fail-soft: repo absent → returns zeros with null turnaround, never raises.
    """
    repo = get_workshop_repository()
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")

    empty = {
        "pending": 0,
        "in_progress": 0,
        "qc_failed": 0,
        "ready_for_pickup": 0,
        "overdue": 0,
        "completed_today": 0,
        "delivered_today": 0,
        "avg_turnaround_days": None,
        "per_station_counts": {},
        "avg_dwell_by_station": {},
        "store_id": active_store,
        "as_of": datetime.now().isoformat(),
    }

    if repo is None or not active_store:
        return empty

    try:
        # One pass over the store's jobs so we don't hit Mongo five times
        # for what is effectively a group-by-status.
        all_jobs = repo.find_by_store(active_store)
    except Exception:
        return empty

    now = datetime.now()
    today_str = now.date().isoformat()

    pending = 0
    in_progress = 0
    qc_failed = 0
    ready = 0
    overdue = 0
    completed_today = 0
    delivered_today = 0
    turnaround_samples = []

    for job in all_jobs:
        status = job.get("status", "")

        if status == "PENDING":
            pending += 1
        elif status == "IN_PROGRESS":
            in_progress += 1
            pending += 1  # "Active" is PENDING + IN_PROGRESS in the UI
        elif status == "QC_FAILED":
            qc_failed += 1
        elif status == "READY":
            ready += 1

        # Overdue = open-ish job whose expected_date is BEFORE today.
        # Bug fixed: the previous code parsed the stored date-only string
        # ("2026-05-30") into a full datetime (midnight UTC), then compared
        # it against datetime.now() which includes the current time. Jobs due
        # TODAY would appear as overdue if any time had elapsed that day
        # because "2026-05-30T00:00:00" < "2026-05-30T14:00:00".
        # We now compare date-only strings so a job is only overdue when its
        # expected_date is STRICTLY BEFORE today.
        if status in ("PENDING", "IN_PROGRESS"):
            expected = job.get("expected_date")
            if expected:
                try:
                    if isinstance(expected, str):
                        # Take just the date portion (first 10 chars)
                        exp_date_str = expected[:10]
                    elif isinstance(expected, datetime):
                        exp_date_str = expected.date().isoformat()
                    elif isinstance(expected, date):
                        exp_date_str = expected.isoformat()
                    else:
                        exp_date_str = None
                    if exp_date_str is not None and exp_date_str < today_str:
                        overdue += 1
                except (ValueError, TypeError):
                    pass

        # Today counts — both by completed_at and by status_updated_at for
        # DELIVERED, so the "what shipped today" number is always live.
        completed_at = job.get("completed_at")
        if completed_at:
            ca_str = (
                completed_at
                if isinstance(completed_at, str)
                else completed_at.isoformat()
            )
            if ca_str.startswith(today_str):
                completed_today += 1

        if status == "DELIVERED":
            sua = job.get("status_updated_at")
            if sua:
                sua_str = sua if isinstance(sua, str) else sua.isoformat()
                if sua_str.startswith(today_str):
                    delivered_today += 1

        # Turnaround sample — only include finished jobs with both ts.
        if status in ("COMPLETED", "READY", "DELIVERED"):
            ca = job.get("completed_at")
            cr = job.get("created_at")
            if ca and cr:
                try:
                    ca_dt = (
                        ca
                        if isinstance(ca, datetime)
                        else datetime.fromisoformat(str(ca).replace("Z", "+00:00"))
                    )
                    cr_dt = (
                        cr
                        if isinstance(cr, datetime)
                        else datetime.fromisoformat(str(cr).replace("Z", "+00:00"))
                    )
                    days = (ca_dt - cr_dt).total_seconds() / 86400.0
                    if days >= 0:
                        turnaround_samples.append(days)
                except (ValueError, TypeError):
                    pass

    # Cap sample size — latest 100 closed jobs are plenty and we've already
    # walked them; take the tail for a rolling view rather than all-time.
    if len(turnaround_samples) >= 5:
        recent = turnaround_samples[-100:]
        avg_turnaround = round(sum(recent) / len(recent), 2)
    else:
        avg_turnaround = None

    # F2 -- per-station live counts + avg dwell. ADDITIVE: existing keys above
    # are unchanged; these two are appended. Reuses the same all_jobs walk.
    per_station_counts: dict = {}
    avg_dwell_by_station: dict = {}
    try:
        from ..services import lab_routing

        stations = lab_routing.list_stations(get_db(), active_store)
        station_kpis = lab_routing.station_kpis(all_jobs, stations)
        per_station_counts = station_kpis.get("per_station_counts", {})
        avg_dwell_by_station = station_kpis.get("avg_dwell_by_station", {})
    except Exception as e:  # noqa: BLE001
        logger.warning("[WORKSHOP] station KPIs failed: %s", e)

    return {
        "pending": pending,  # PENDING + IN_PROGRESS
        "in_progress": in_progress,
        "qc_failed": qc_failed,
        "ready_for_pickup": ready,
        "overdue": overdue,
        "completed_today": completed_today,
        "delivered_today": delivered_today,
        "avg_turnaround_days": avg_turnaround,
        "per_station_counts": per_station_counts,
        "avg_dwell_by_station": avg_dwell_by_station,
        "store_id": active_store,
        "as_of": now.isoformat(),
    }


@router.get("/jobs/by-vendor/{vendor_id}")
async def list_jobs_by_vendor(
    vendor_id: str,
    include_delivered: bool = Query(False),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    current_user: dict = Depends(get_current_user),
):
    """Admin view of the same queue an external vendor sees through
    their portal — useful for checking what the lab is supposed to be
    seeing without copy-pasting their token URL.

    Registered BEFORE `/jobs/{job_id}` so the literal path segment
    `by-vendor` doesn't get matched as a job_id (FastAPI matches by
    registration order — first match wins).
    """
    repo = get_workshop_repository()
    if repo is None:
        return {"vendor_id": vendor_id, "jobs": [], "total": 0}

    filter_dict: dict = {"vendor_id": vendor_id}
    if not include_delivered:
        filter_dict["status"] = {"$nin": ["DELIVERED", "CANCELLED"]}

    jobs = (
        repo.find_many(filter_dict, skip=skip, limit=limit, sort=[("expected_date", 1)])
        or []
    )

    return {
        "vendor_id": vendor_id,
        "jobs": [job_to_frontend(j) for j in jobs],
        "total": len(jobs),
    }


@router.get("/jobs")
async def list_jobs(
    status: Optional[str] = Query(None),
    technician_id: Optional[str] = Query(None),
    store_id: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """List workshop jobs with filters"""
    repo = get_workshop_repository()
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")

    if repo is not None:
        filter_dict = {}
        if active_store:
            filter_dict["store_id"] = active_store
        if status:
            filter_dict["status"] = status
        if technician_id:
            filter_dict["technician_id"] = technician_id

        jobs = repo.find_many(
            filter_dict, skip=skip, limit=limit, sort=[("created_at", -1)]
        )
        jobs_formatted = [job_to_frontend(j) for j in jobs]
        return {"jobs": jobs_formatted, "total": len(jobs_formatted)}

    return {"jobs": [], "total": 0}


@router.post("/jobs", status_code=201)
async def create_job(
    job: WorkshopJobCreate, current_user: dict = Depends(get_current_user)
):
    """Create a new workshop job"""
    repo = get_workshop_repository()
    order_repo = get_order_repository()

    if repo is not None:
        # Verify order exists
        if order_repo is not None:
            order = order_repo.find_by_id(job.order_id)
            if order is None:
                raise HTTPException(status_code=404, detail="Order not found")

        store_id = current_user.get("active_store_id")
        created_at = datetime.now().isoformat()

        # F9 -- LENS DC HARDLOCK. An external-lab lens (lens_status=ORDERED) may
        # not be worked until an accepted Delivery Challan covering its SKU
        # exists at this store. Raises 422 (code DC_HARDLOCK) when blocked; an
        # ADMIN+ can bypass with override_reason (audited below). In-house lenses
        # and a disabled flag pass straight through.
        # At create the job's lens_status is usually unset (the lens is ORDERED
        # later via the lens lifecycle) -> exempt here; the REAL gate is the
        # -> IN_PROGRESS transition below. We still pass any lens_status carried on
        # the create payload so an already-ORDERED create is gated too.
        hardlock = _check_dc_hardlock(
            get_db(),
            (job.lens_details or {}).get("lens_status"),
            (job.lens_details or {}).get("product_id"),
            store_id,
            created_at,
            current_user,
            job.override_reason,
        )

        job_data = {
            "job_number": generate_job_number(repo),
            "order_id": job.order_id,
            "store_id": store_id,
            "frame_details": job.frame_details,
            "lens_details": job.lens_details,
            "prescription_id": job.prescription_id,
            "fitting_instructions": job.fitting_instructions,
            "special_notes": job.special_notes,
            "expected_date": job.expected_date.isoformat(),
            "fitting_details": (
                job.fitting_details.model_dump(mode="json")
                if job.fitting_details
                else None
            ),
            "status": "PENDING",
            "created_at": created_at,
            "created_by": current_user.get("user_id"),
        }

        created = repo.create(job_data)
        if created:
            # F9 -- if the DC hardlock was OVERRIDDEN, write an immutable audit
            # row (the override is a control bypass and MUST be recorded).
            # Fail-soft: an audit failure never fails the job create.
            if hardlock.get("override_applied"):
                try:
                    audit = get_audit_repository()
                    if audit is not None:
                        audit.create(
                            {
                                "action": "dc_hardlock_override",
                                "entity_type": "workshop_job",
                                "entity_id": created["job_id"],
                                "user_id": current_user.get("user_id"),
                                "detail": {
                                    "job_number": created["job_number"],
                                    "order_id": job.order_id,
                                    "store_id": store_id,
                                    "product_id": (job.lens_details or {}).get(
                                        "product_id"
                                    ),
                                    "reason": hardlock.get("reason"),
                                },
                            }
                        )
                except Exception:
                    pass
            # Stamp the reverse pointer on the order so an order can find its
            # workshop job directly (the link was previously one-way: job ->
            # order only). Best-effort: a stamp failure never fails job create.
            if order_repo is not None:
                try:
                    order_repo.update(
                        job.order_id,
                        {
                            "workshop_job_id": created["job_id"],
                            "workshop_job_number": created["job_number"],
                        },
                    )
                except Exception:
                    pass
            return {
                "job_id": created["job_id"],
                "job_number": created["job_number"],
                "dc_hardlock_override": bool(hardlock.get("override_applied")),
                "message": "Workshop job created",
            }

        raise HTTPException(status_code=500, detail="Failed to create workshop job")

    return {
        "id": str(uuid.uuid4()),
        "jobNumber": generate_job_number(),
        "message": "Workshop job created",
    }


@router.patch("/jobs/{job_id}/fitting-details")
async def update_fitting_details(
    job_id: str,
    payload: FittingDetailsUpdate,
    current_user: dict = Depends(get_current_user),
):
    """
    Phase 6.8 — attach / update the lens-fitting measurements the sales
    staff fill after creating a prescription order. The sales staff
    confirms the power + product details are correct via the
    `confirmed_by_sales` checkbox before the workshop can accept the job.
    """
    repo = get_workshop_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Workshop repository unavailable")

    job = repo.find_by_id(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Workshop job not found")
    _assert_job_store_access(job, current_user)

    # Stamp metadata we want server-controlled rather than trusting the client.
    fd = payload.fitting_details.model_dump(mode="json")
    now = datetime.now()
    fd["order_date"] = fd.get("order_date") or now.date().isoformat()
    fd["order_time"] = fd.get("order_time") or now.strftime("%H:%M")
    fd["ordered_by"] = fd.get("ordered_by") or current_user.get("user_id")
    fd["ordered_by_name"] = fd.get("ordered_by_name") or current_user.get("username")
    if fd.get("confirmed_by_sales"):
        fd["confirmed_at"] = fd.get("confirmed_at") or now.isoformat()

    ok = repo.update(job_id, {"fitting_details": fd})
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to save fitting details")

    return {"job_id": job_id, "fitting_details": fd, "message": "Fitting details saved"}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, current_user: dict = Depends(get_current_user)):
    """Get workshop job by ID"""
    repo = get_workshop_repository()

    if repo is not None:
        job = repo.find_by_id(job_id)
        if job is not None:
            # NEW-IDOR-by-id: a workshop job carries customer + medical Rx data;
            # existence-hide one whose store the caller can't access (cross-store
            # PII leak). Admins / area-managers pass.
            if not can_access_store_scoped(job.get("store_id"), current_user):
                raise HTTPException(status_code=404, detail="Workshop job not found")
            return job_to_frontend(job)
        raise HTTPException(status_code=404, detail="Workshop job not found")

    return {"id": job_id}


@router.put("/jobs/{job_id}")
async def update_job(
    job_id: str, job: WorkshopJobUpdate, current_user: dict = Depends(get_current_user)
):
    """Update workshop job details"""
    repo = get_workshop_repository()

    if repo is not None:
        existing = repo.find_by_id(job_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Workshop job not found")
        # NEW-IDOR-by-id: don't let a store-scoped caller mutate another store's job.
        if not can_access_store_scoped(existing.get("store_id"), current_user):
            raise HTTPException(status_code=404, detail="Workshop job not found")

        # Bug fix: READY and QC_FAILED were missing from the immutable-status
        # guard. A job in READY or CANCELLED state must not have its details
        # changed out from under QC/delivery. COMPLETED is intentionally
        # included so QC rework can't silently alter specs mid-check.
        if existing.get("status") in ["COMPLETED", "READY", "DELIVERED", "CANCELLED"]:
            raise HTTPException(
                status_code=400,
                detail="Cannot update completed, ready, delivered, or cancelled jobs",
            )

        update_data = job.model_dump(exclude_unset=True)
        if "expected_date" in update_data and update_data["expected_date"]:
            update_data["expected_date"] = update_data["expected_date"].isoformat()
        update_data["updated_by"] = current_user.get("user_id")

        if repo.update(job_id, update_data):
            return {"job_id": job_id, "message": "Workshop job updated"}

        raise HTTPException(status_code=500, detail="Failed to update workshop job")

    return {"job_id": job_id, "message": "Workshop job updated"}


@router.patch("/jobs/{job_id}/status")
async def update_job_status(
    job_id: str,
    body: Optional[StatusBody] = Body(None),
    status_q: Optional[str] = Query(None, alias="status"),
    notes_q: Optional[str] = Query(None, alias="notes"),
    current_user: dict = Depends(get_current_user),
):
    """Update job status (generic endpoint) with state machine validation.

    Accepts the transition target and optional notes either as a JSON body
    (preferred by the frontend via Axios PATCH) or as query parameters
    (backward-compatible with existing callers). The body takes precedence.

    Bug fixed: previous signature used ``Query(...)`` for ``status``, but the
    frontend sends ``api.patch(url, { status, notes })`` which delivers the data
    as a JSON body — not a query string. That mismatch caused every generic
    status transition from the UI to return 422 Unprocessable Entity.
    """
    # Resolve status + notes from body (preferred) or query params (fallback)
    status = (body.status if body else None) or status_q
    notes = (body.notes if body else None) or notes_q

    if not status:
        raise HTTPException(
            status_code=422,
            detail="status is required (provide as JSON body field or ?status= query param)",
        )

    repo = get_workshop_repository()

    if repo is not None:
        job = repo.find_by_id(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Workshop job not found")
        _assert_job_store_access(job, current_user)

        current_status = job.get("status", "PENDING")

        # Map legacy frontend status names to canonical backend values.
        # The frontend historically used "PROCESSING" for what the backend
        # calls "IN_PROGRESS" — normalise here so old clients still work.
        STATUS_ALIASES = {"PROCESSING": "IN_PROGRESS"}
        status = STATUS_ALIASES.get(status, status)

        allowed = VALID_JOB_TRANSITIONS.get(current_status, set())
        if status not in allowed:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Cannot transition from {current_status} to {status}. "
                    f"Allowed: {', '.join(sorted(allowed)) if allowed else 'none (terminal state)'}."
                ),
            )

        # BUG-116c: the workshop may only ACCEPT a job (-> IN_PROGRESS) once sales
        # has confirmed the fitting details (power + product correct). Mirror the
        # /start gate on the generic status PATCH so it cannot be bypassed.
        if status == "IN_PROGRESS" and not (
            (job.get("fitting_details") or {}).get("confirmed_by_sales")
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Cannot start this job: sales must confirm the fitting details "
                    "(confirmed_by_sales) first."
                ),
            )

        # F9 DC HARDLOCK: an external-lab lens (top-level lens_status=ORDERED) may
        # not ADVANCE TO IN_PROGRESS until an accepted Delivery Challan covering its
        # SKU exists at this store -- this is the REAL gate (create-time lens_status
        # is unset). Raises 422 (DC_HARDLOCK) when blocked; an ADMIN+ override_reason
        # bypasses (audited). In-house lenses + a disabled flag pass through.
        if status == "IN_PROGRESS":
            dc_lock = _check_dc_hardlock(
                get_db(),
                job.get("lens_status"),
                (job.get("lens_details") or {}).get("product_id"),
                job.get("store_id"),
                job.get("created_at"),
                current_user,
                (body.override_reason if body else None),
            )
            if dc_lock.get("override_applied"):
                try:
                    audit = get_audit_repository()
                    if audit is not None:
                        audit.create(
                            {
                                "action": "dc_hardlock_override",
                                "entity_type": "workshop_job",
                                "entity_id": job_id,
                                "user_id": current_user.get("user_id"),
                                "detail": {
                                    "transition": "IN_PROGRESS",
                                    "store_id": job.get("store_id"),
                                    "product_id": (job.get("lens_details") or {}).get("product_id"),
                                    "reason": dc_lock.get("reason"),
                                },
                            }
                        )
                except Exception:  # noqa: BLE001
                    pass

        # BUG-116a (patient-safety): a lens job must NOT reach READY-for-pickup
        # without a QC record. The dedicated QC endpoints (/jobs/{id}/qc) set
        # qc_passed before flipping the job to READY; this GENERIC transition
        # previously bypassed that (the gate here was a no-op `pass`), so a job
        # could be PATCHed COMPLETED -> READY with zero QC and reach the patient.
        # Require an explicit QC pass or waiver on ANY -> READY transition.
        if status == "READY" and not (
            job.get("qc_passed") is True or job.get("qc_waived") is True
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Cannot mark this job READY: lens QC must pass (record it via "
                    "the QC endpoint) or be explicitly waived (qc_waived) first."
                ),
            )
        # DELIVERED from READY is always fine -- the job passed/waived QC to reach
        # READY (enforced above), so no further gate is needed here.

        # BUG-116d: a QC_FAILED -> IN_PROGRESS move is a rework. Cap it: once the
        # job has already been reworked MAX_REWORK times, only a manager may send
        # it back again (override), otherwise the rework loop is blocked.
        is_rework = current_status == "QC_FAILED" and status == "IN_PROGRESS"
        rework_count = int(job.get("rework_count") or 0)
        if is_rework and rework_count >= MAX_REWORK:
            roles = set(current_user.get("roles") or [])
            if not (roles & _REWORK_OVERRIDE_ROLES):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"This job has already been reworked {rework_count} time(s) "
                        f"(max {MAX_REWORK}). A Store Manager must override to rework it again."
                    ),
                )

        if repo.update_status(job_id, status, current_user.get("user_id"), notes):
            if is_rework:
                # Count this rework so the cap is enforced on the next attempt.
                try:
                    repo.update(job_id, {"rework_count": rework_count + 1})
                except Exception:  # noqa: BLE001 -- counting is best-effort
                    pass
            return {
                "job_id": job_id,
                "status": status,
                "message": f"Job status updated to {status}",
            }

        raise HTTPException(status_code=500, detail="Failed to update job status")

    return {
        "job_id": job_id,
        "status": status or "",
        "message": f"Job status updated to {status}",
    }


@router.post("/jobs/{job_id}/assign")
async def assign_job(
    job_id: str,
    technician_id: str = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Assign job to a technician"""
    repo = get_workshop_repository()

    if repo is not None:
        job = repo.find_by_id(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Workshop job not found")
        _assert_job_store_access(job, current_user)

        if job.get("status") not in ["PENDING", "IN_PROGRESS"]:
            raise HTTPException(
                status_code=400, detail="Job cannot be assigned in current state"
            )

        # Validate technician exists and has WORKSHOP_STAFF role
        from ..dependencies import get_user_repository

        user_repo = get_user_repository()
        if user_repo:
            tech_user = user_repo.find_by_id(technician_id)
            if tech_user is None:
                raise HTTPException(
                    status_code=404, detail=f"Technician {technician_id} not found"
                )
            tech_roles = tech_user.get("roles", [])
            if not any(
                r in tech_roles
                for r in ["WORKSHOP_STAFF", "STORE_MANAGER", "ADMIN", "SUPERADMIN"]
            ):
                raise HTTPException(
                    status_code=400,
                    detail=f"User {tech_user.get('full_name', technician_id)} is not a workshop technician",
                )

        if repo.assign_technician(job_id, technician_id):
            return {
                "job_id": job_id,
                "technician_id": technician_id,
                "message": "Job assigned",
            }

        raise HTTPException(status_code=500, detail="Failed to assign job")

    return {"message": "Job assigned"}


@router.post("/jobs/{job_id}/start")
async def start_job(job_id: str, current_user: dict = Depends(get_current_user)):
    """Start working on a job. Requires sales confirmation (fitting_details.confirmed_by_sales=True)."""
    repo = get_workshop_repository()

    if repo is not None:
        job = repo.find_by_id(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Workshop job not found")
        _assert_job_store_access(job, current_user)

        if job.get("status") != "PENDING":
            raise HTTPException(status_code=400, detail="Job must be PENDING to start")

        # BUG-116c: gate job acceptance on sales confirmation
        fitting_details = job.get("fitting_details") or {}
        if not fitting_details.get("confirmed_by_sales"):
            raise HTTPException(
                status_code=400,
                detail="Cannot start job: sales must confirm fitting details (confirmed_by_sales=True) first",
            )

        if repo.update_status(job_id, "IN_PROGRESS", current_user.get("user_id")):
            return {"job_id": job_id, "status": "IN_PROGRESS", "message": "Job started"}

        raise HTTPException(status_code=500, detail="Failed to start job")

    return {"message": "Job started"}


@router.post("/jobs/{job_id}/complete")
async def complete_job(job_id: str, current_user: dict = Depends(get_current_user)):
    """Mark job as completed (pending QC). Requires sales confirmation (fitting_details.confirmed_by_sales=True)."""
    repo = get_workshop_repository()

    if repo is not None:
        job = repo.find_by_id(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Workshop job not found")
        _assert_job_store_access(job, current_user)

        if job.get("status") != "IN_PROGRESS":
            raise HTTPException(
                status_code=400, detail="Job must be IN_PROGRESS to complete"
            )

        # BUG-116c: defensive check (should have been gated at /start, but verify here too)
        fitting_details = job.get("fitting_details") or {}
        if not fitting_details.get("confirmed_by_sales"):
            raise HTTPException(
                status_code=400,
                detail="Cannot complete job: sales must confirm fitting details (confirmed_by_sales=True) first",
            )

        if repo.update_status(job_id, "COMPLETED", current_user.get("user_id")):
            return {
                "job_id": job_id,
                "status": "COMPLETED",
                "message": "Job completed, pending QC",
            }

        raise HTTPException(status_code=500, detail="Failed to complete job")

    return {"message": "Job completed, pending QC"}


@router.post("/jobs/{job_id}/qc")
async def qc_job(
    job_id: str,
    passed: bool = Query(...),
    notes: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*WORKSHOP_ROLES)),
):
    """Simple pass/fail QC result on a completed job.

    Gate changed to WORKSHOP_ROLES (WORKSHOP_STAFF / STORE_MANAGER / AREA_MANAGER / ADMIN
    + implicit SUPERADMIN). Sales staff cannot run QC.

    A job with passed=True advances to READY; passed=False advances to QC_FAILED.
    The job must be in COMPLETED or QC_FAILED state; any other state returns 400.
    """
    repo = get_workshop_repository()

    if repo is not None:
        job = repo.find_by_id(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Workshop job not found")
        _assert_job_store_access(job, current_user)

        if job.get("status") not in ["COMPLETED", "QC_FAILED"]:
            raise HTTPException(
                status_code=400, detail="Job must be COMPLETED or QC_FAILED for QC"
            )

        if repo.add_qc_result(
            job_id,
            passed,
            notes or "",
            current_user.get("user_id"),
        ):
            status = "READY" if passed else "QC_FAILED"
            return {
                "job_id": job_id,
                "status": status,
                "qc_passed": passed,
                "message": "QC recorded",
            }

        raise HTTPException(status_code=500, detail="Failed to record QC")

    return {"message": "QC recorded", "status": "READY" if passed else "QC_FAILED"}


@router.post("/jobs/{job_id}/qc-checklist")
async def qc_checklist(
    job_id: str,
    payload: QcChecklistBody,
    current_user: dict = Depends(require_roles(*WORKSHOP_ROLES)),
):
    """Submit a structured per-item QC checklist for a workshop job.

    This is the authoritative QC endpoint for the checklist feature. It
    stores each check item (key, label, pass/fail, note) along with the
    reviewer identity and a timestamp, then advances the job status:

    - All items passed (or waived with reason): job -> READY_FOR_PICKUP
    - Any item failed without waiver: job -> QC_FAILED

    A job MUST NOT reach READY status via any other path unless QC passed or
    was explicitly waived here. This endpoint enforces that invariant.

    Gate: WORKSHOP_STAFF / STORE_MANAGER / AREA_MANAGER / ADMIN / SUPERADMIN.
    Sales staff and cashiers cannot run QC.
    """
    repo = get_workshop_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Workshop repository unavailable")

    job = repo.find_by_id(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Workshop job not found")
    _assert_job_store_access(job, current_user)

    if job.get("status") not in ["COMPLETED", "QC_FAILED"]:
        raise HTTPException(
            status_code=400,
            detail=(
                "QC checklist can only be submitted when the job is in COMPLETED "
                "or QC_FAILED state (current: {})".format(job.get("status"))
            ),
        )

    # Validate waiver: waived=True requires a waive_reason
    if payload.waived and not (payload.waive_reason or "").strip():
        raise HTTPException(
            status_code=422,
            detail="waive_reason is required when waived=True",
        )

    # Determine overall pass/fail: all items must pass unless explicitly waived
    all_passed = all(item.passed for item in payload.checklist)
    effective_pass = all_passed or payload.waived

    # Stamp each checklist item with reviewer identity + timestamp
    now = datetime.now()
    stamped_items = [
        {
            "key": item.key,
            "label": item.label,
            "passed": item.passed,
            "note": item.note or "",
            "checked_by": current_user.get("user_id"),
            "checked_at": now.isoformat(),
        }
        for item in payload.checklist
    ]

    notes_parts = []
    if payload.overall_notes:
        notes_parts.append(payload.overall_notes)
    if payload.waived:
        notes_parts.append(
            "QC WAIVED by {}: {}".format(
                current_user.get("username") or current_user.get("user_id"),
                payload.waive_reason,
            )
        )
    combined_notes = " | ".join(notes_parts) if notes_parts else ""

    # Audit (fail-soft)
    try:
        audit = get_audit_repository()
        if audit is not None:
            audit.create(
                {
                    "action": "workshop.qc_checklist",
                    "entity_type": "workshop_job",
                    "entity_id": job_id,
                    "store_id": job.get("store_id"),
                    "user_id": current_user.get("user_id"),
                    "detail": {
                        "effective_pass": effective_pass,
                        "waived": payload.waived,
                        "item_count": len(stamped_items),
                        "failed_items": [
                            i["key"] for i in stamped_items if not i["passed"]
                        ],
                    },
                }
            )
    except Exception as audit_exc:  # noqa: BLE001
        logger.warning("[WORKSHOP] qc_checklist audit failed: %s", audit_exc)

    if repo.add_qc_result(
        job_id,
        effective_pass,
        combined_notes,
        current_user.get("user_id"),
        checklist_items=stamped_items,
        waived=payload.waived,
        waive_reason=payload.waive_reason,
    ):
        target_status = "READY" if effective_pass else "QC_FAILED"
        return {
            "job_id": job_id,
            "status": target_status,
            "qc_passed": effective_pass,
            "all_items_passed": all_passed,
            "waived": payload.waived,
            "checklist": stamped_items,
            "message": (
                "QC checklist submitted — job is now ready for pickup"
                if effective_pass
                else "QC checklist submitted — job flagged for rework"
            ),
        }

    raise HTTPException(status_code=500, detail="Failed to record QC checklist")


@router.post("/jobs/{job_id}/rework")
async def rework_job(
    job_id: str,
    notes: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Send QC-failed job back for rework (QC_FAILED → IN_PROGRESS)."""
    repo = get_workshop_repository()

    if repo is not None:
        job = repo.find_by_id(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Workshop job not found")
        _assert_job_store_access(job, current_user)

        if job.get("status") != "QC_FAILED":
            raise HTTPException(
                status_code=400, detail="Only QC_FAILED jobs can be sent for rework"
            )

        rework_count = job.get("rework_count", 0) + 1
        repo.update(job_id, {"rework_count": rework_count})

        if repo.update_status(
            job_id,
            "IN_PROGRESS",
            current_user.get("user_id"),
            notes or f"Rework #{rework_count}",
        ):
            return {
                "job_id": job_id,
                "status": "IN_PROGRESS",
                "rework_count": rework_count,
                "message": f"Job sent for rework (attempt #{rework_count})",
            }

        raise HTTPException(status_code=500, detail="Failed to send job for rework")

    return {"message": "Job sent for rework"}


# ============================================================================
# LENS-ORDER LIFECYCLE + READY-NOTIFY
# ============================================================================
# A workshop job's physical lens moves NOT_ORDERED -> ORDERED -> RECEIVED ->
# MOUNTED. This is independent of the job's overall workflow status (PENDING /
# IN_PROGRESS / READY / ...) and tracks where the actual lens is. When the job
# is finished we ping the customer that it's ready for pickup.


@router.post("/jobs/{job_id}/lens-status")
async def update_lens_status(
    job_id: str,
    payload: LensStatusBody,
    current_user: dict = Depends(require_roles(*WORKSHOP_ROLES)),
):
    """Advance a job's lens lifecycle by ONE forward step.

    Forward-only along NOT_ORDERED -> ORDERED -> RECEIVED -> MOUNTED. Skips,
    backwards moves, and no-ops are rejected with 400. The matching timestamp
    field (lens_ordered_at / lens_received_at / lens_mounted_at) is stamped.
    Fail-soft: repo absent -> 503, never an unhandled 500.
    """
    target = (payload.status or "").strip().upper()
    if target not in LENS_STATUS_ORDER:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown lens status {target!r}. Allowed: {', '.join(LENS_STATUS_ORDER)}.",
        )

    repo = get_workshop_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Workshop repository unavailable")

    job = repo.find_by_id(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Workshop job not found")
    _assert_job_store_access(job, current_user)

    current = job.get("lens_status") or "NOT_ORDERED"
    if not _next_lens_status_ok(current, target):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot move lens status from {current} to {target}. "
                f"Lens lifecycle is forward-only: {' -> '.join(LENS_STATUS_ORDER)}."
            ),
        )

    now = datetime.now()
    update = {
        "lens_status": target,
        "lens_status_updated_by": current_user.get("user_id"),
    }
    ts_field = LENS_STATUS_TIMESTAMP_FIELD.get(target)
    if ts_field:
        update[ts_field] = now.isoformat()

    if not repo.update(job_id, update):
        raise HTTPException(status_code=500, detail="Failed to update lens status")

    # Audit (fail-soft)
    try:
        audit = get_audit_repository()
        if audit is not None:
            audit.create(
                {
                    "action": "workshop.lens_status",
                    "entity_type": "workshop_job",
                    "entity_id": job_id,
                    "store_id": job.get("store_id"),
                    "user_id": current_user.get("user_id"),
                    "detail": {"from": current, "to": target},
                }
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("[WORKSHOP] lens_status audit failed: %s", e)

    # Branch B' sub-PR 4 -- on lens MOUNTED, hard-commit the reserved
    # lens-catalog cell (the unit physically left the tray and is in
    # the customer's frame). Fail-soft: a missing reservation, mongo
    # blip, or 409 here is logged but never blocks the lens-status
    # transition (the workshop has already cut the lens).
    if target == "MOUNTED":
        try:
            order_id = job.get("order_id")
            if order_id and get_order_repository is not None:
                order_repo = get_order_repository()
                if order_repo is not None:
                    order = order_repo.find_by_id(order_id)
                    if order:
                        from ..services.lens_stock_hook import (
                            commit_for_workshop_dispatch,
                        )

                        items_for_commit = order.get("items") or []
                        for idx, oi in enumerate(items_for_commit):
                            try:
                                await commit_for_workshop_dispatch(
                                    order_item=oi,
                                    order_id=order_id,
                                    line_index=idx,
                                    store_id=(
                                        order.get("store_id")
                                        or job.get("store_id")
                                        or ""
                                    ),
                                    user=current_user,
                                )
                            except Exception as cm_exc:  # noqa: BLE001
                                logger.warning(
                                    "[LENS_HOOK] commit on MOUNTED failed "
                                    "(order %s line %s): %s",
                                    order_id,
                                    idx,
                                    cm_exc,
                                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[LENS_HOOK] MOUNTED commit outer error job=%s: %s",
                job_id,
                exc,
            )

    return {
        "job_id": job_id,
        "lens_status": target,
        **({ts_field: update[ts_field]} if ts_field else {}),
        "message": f"Lens status updated to {target}",
    }


def _ready_whatsapp_text(job: dict) -> str:
    """Plain-text 'ready for pickup' WhatsApp body. Pure (no IO)."""
    name = job.get("customer_name") or "Customer"
    job_no = job.get("job_number") or job.get("job_id") or ""
    tail = f" (Job {job_no})" if job_no else ""
    return (
        f"Hi {name}, your eyewear order{tail} is ready for pickup at our store. "
        f"Please visit us at your convenience. - Better Vision"
    )


async def _perform_ready_notify(job: dict, actor_id: Optional[str]) -> dict:
    """Send the 'ready for pickup' WhatsApp + stamp ready_notified_at + write an
    in-app notification row. Reused by BOTH the manual notify-ready endpoint and
    the F2 auto-notify-on-DISPATCH path.

    Fail-soft everywhere: a provider/DB hiccup never raises; the WhatsApp result
    (SENT / SIMULATED / FAILED / no_phone) is reported back in the dict.
    """
    job_id = job.get("job_id")
    phone = job.get("customer_phone") or job.get("customerPhone")
    now = datetime.now()

    # 1. WhatsApp (provider is DISPATCH_MODE-gated + fail-soft internally).
    wa_status = "no_phone"
    if phone:
        try:
            from agents.providers import send_whatsapp  # lazy import

            res = await send_whatsapp(phone, _ready_whatsapp_text(job))
            wa_status = getattr(res, "status", "SENT")
        except Exception as e:  # noqa: BLE001
            logger.warning("[WORKSHOP] notify-ready whatsapp failed: %s", e)
            wa_status = "FAILED"

    # 2. Stamp the job (fail-soft).
    try:
        repo = get_workshop_repository()
        if repo is not None:
            repo.update(
                job_id,
                {
                    "ready_notified_at": now.isoformat(),
                    "ready_notified_by": actor_id,
                },
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("[WORKSHOP] notify-ready stamp failed: %s", e)

    # 3. In-app notification row (fail-soft; only if the collection exists).
    notif_written = False
    try:
        db = get_db()
        if db is not None and getattr(db, "is_connected", True):
            coll = db.get_collection("notifications")
            if coll is not None:
                coll.insert_one(
                    {
                        "notification_id": f"NTF-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}",
                        "notification_type": "workshop_ready",
                        "user_id": actor_id,
                        "title": "Pickup notification sent",
                        "message": (
                            f"Customer notified that job "
                            f"{job.get('job_number') or job_id} is ready for pickup."
                        ),
                        "entity_type": "workshop_job",
                        "entity_id": job_id,
                        "action_url": "/workshop",
                        "channels": ["WHATSAPP", "IN_APP"],
                        "priority": "NORMAL",
                        "status": "SENT",
                        "created_at": now,
                    }
                )
                notif_written = True
    except Exception as e:  # noqa: BLE001
        logger.warning("[WORKSHOP] notify-ready notification insert failed: %s", e)

    return {
        "ready_notified_at": now.isoformat(),
        "whatsapp_status": wa_status,
        "notification_logged": notif_written,
    }


@router.post("/jobs/{job_id}/notify-ready")
async def notify_ready(
    job_id: str,
    current_user: dict = Depends(require_roles(*WORKSHOP_ROLES)),
):
    """Notify the customer that their job is ready for pickup.

    Sends a WhatsApp via the existing MSG91 provider (DISPATCH_MODE-gated +
    fail-soft), stamps `ready_notified_at` on the job, and inserts a row into
    the `notifications` collection when available. Never raises on a provider
    or DB hiccup: the WhatsApp result is reported back in the response so the
    UI can surface SENT / SIMULATED / FAILED.
    """
    repo = get_workshop_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Workshop repository unavailable")

    job = repo.find_by_id(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Workshop job not found")
    _assert_job_store_access(job, current_user)

    result = await _perform_ready_notify(job, current_user.get("user_id"))
    return {
        "job_id": job_id,
        "ready_notified_at": result["ready_notified_at"],
        "whatsapp_status": result["whatsapp_status"],
        "notification_logged": result["notification_logged"],
        "message": "Pickup notification processed",
    }


# ============================================================================
# F2 -- INTERNAL LAB ROUTING (disposable barcoded job cards)
# ============================================================================
# A workshop order travels through in-house benches (INTAKE -> EDGING ->
# COATING -> QC_LAB -> DISPATCH -> PICKUP). A disposable Code128 job card rides
# with the job; each bench scans it; the forward-only gate advances
# current_station + records per-station dwell. Reuses the EXISTING
# WorkshopJobRepository + notify_ready path; calls NO money/E3 engine (no
# stocked-unit state changes here). See api/services/lab_routing.py.

# Roles allowed to scan at a lab bench. Mirrors labels.SCAN_ROLES (CASHIER is
# included for the front-desk PICKUP scan). SUPERADMIN passes via require_roles.
_LAB_SCAN_ROLES = (
    "ADMIN",
    "AREA_MANAGER",
    "STORE_MANAGER",
    "WORKSHOP_STAFF",
    "CASHIER",
)

# Roles allowed to configure (upsert) a store's station registry. Manager ladder
# only -- bench staff scan, managers configure.
_STATION_CONFIG_ROLES = (
    "ADMIN",
    "AREA_MANAGER",
    "STORE_MANAGER",
)


@router.get("/stations")
async def list_lab_stations(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """List the lab stations configured for a store, in sequence order.

    Store-scoped: resolves to the caller's active store when store_id is omitted.
    Seeds the 6 defaults on first use. Fail-soft: no DB -> empty list."""
    from ..services import lab_routing

    active_store = validate_store_access(store_id, current_user) or current_user.get(
        "active_store_id"
    )
    db = get_db()
    if db is None or not active_store:
        return {"stations": [], "store_id": active_store}
    stations = lab_routing.list_stations(db, active_store)
    return {"stations": stations, "store_id": active_store, "total": len(stations)}


@router.post("/stations")
async def upsert_lab_station(
    body: LabStationUpsert,
    current_user: dict = Depends(require_roles(*_STATION_CONFIG_ROLES)),
):
    """Create or update a single lab station config for a store (key store+code).

    STORE_MANAGER+ only. Validates `code` against the canonical vocabulary."""
    from ..services import lab_routing

    active_store = validate_store_access(body.store_id, current_user) or current_user.get(
        "active_store_id"
    )
    if not active_store:
        raise HTTPException(status_code=400, detail="No store in scope")
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    ok, station, reason = lab_routing.upsert_station(
        db,
        store_id=active_store,
        code=body.code,
        actor_id=current_user.get("user_id"),
        label=body.label,
        sequence_order=body.sequence_order,
        is_active=body.is_active,
        target_dwell_minutes=body.target_dwell_minutes,
        advances_job_status=body.advances_job_status,
        auto_notify_customer=body.auto_notify_customer,
    )
    if not ok:
        if reason == "UNKNOWN_STATION":
            raise HTTPException(
                status_code=400,
                detail={"code": "unknown_station", "message": f"Unknown station {body.code}."},
            )
        raise HTTPException(status_code=503, detail="Failed to save station config")
    return {"ok": True, "station": station}


@router.get("/stations/{code}/queue")
async def get_station_queue(
    code: str,
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_LAB_SCAN_ROLES)),
):
    """Jobs currently AT a given station for a store, oldest-first.

    Each row carries time-at-station + an SLA colour chip. Store-scoped."""
    from ..services import lab_routing

    active_store = validate_store_access(store_id, current_user) or current_user.get(
        "active_store_id"
    )
    db = get_db()
    if db is None or not active_store:
        return {"station": code.upper(), "store_id": active_store, "jobs": [], "total": 0}
    jobs = lab_routing.station_queue(db, active_store, code)
    return {
        "station": code.upper(),
        "store_id": active_store,
        "jobs": jobs,
        "total": len(jobs),
    }


@router.post("/scan")
async def lab_scan(
    body: LabScanBody,
    current_user: dict = Depends(require_roles(*_LAB_SCAN_ROLES)),
):
    """Scan a disposable job card at a lab bench -- the F2 core.

    Resolves the job by scanned_code (job_number or job_id, store-scoped),
    validates `station_code` is the NEXT active station in this job's sequence,
    advances current_station, records server-computed dwell for the station the
    job is leaving, appends to scan_history, and -- when the station config says
    so -- transitions job status (DISPATCH -> READY, PICKUP -> DELIVERED) and
    fires the customer 'ready for pickup' notify INLINE (fail-soft, never blocks
    the scan response).

    LOUD-failure contract: returns HTTP 200 with {ok:false, reason} on any guard
    failure WITHOUT mutating state (the scan box renders a rich in-page error).
    reasons: REPO_UNAVAILABLE / NOT_FOUND / NO_STATIONS / TERMINAL_STAGE /
             UNKNOWN_STATION / WRONG_STATION / ALREADY_HERE / CONCURRENT_CONFLICT
    """
    from ..services import lab_routing

    repo = get_workshop_repository()
    db = get_db()
    if repo is None or db is None:
        return {
            "ok": False,
            "reason": "REPO_UNAVAILABLE",
            "message": "Workshop repository unavailable; cannot route.",
        }

    code = (body.scanned_code or "").strip()
    if not code:
        return {"ok": False, "reason": "NOT_FOUND", "message": "Empty scan code."}

    # Resolve the job: try job_number first (what the card encodes), then job_id.
    job = repo.find_by_number(code)
    if job is None:
        job = repo.find_by_id(code)
    if job is None:
        return {
            "ok": False,
            "reason": "NOT_FOUND",
            "message": f"No workshop job matches the scanned code {code}.",
        }

    # Store-scope guard: existence-hide a cross-store job (medical PII / IDOR).
    if not can_access_store_scoped(job.get("store_id"), current_user):
        return {
            "ok": False,
            "reason": "NOT_FOUND",
            "message": f"No workshop job matches the scanned code {code}.",
        }

    result = lab_routing.advance_lab_station(
        db, job, body.station_code, current_user.get("user_id")
    )
    if not result.get("ok"):
        return result

    # Audit (fail-soft) -- one row per successful lab scan.
    try:
        audit = get_audit_repository()
        if audit is not None:
            audit.create(
                {
                    "action": "workshop.lab_scan",
                    "entity_type": "workshop_job",
                    "entity_id": result.get("job_id"),
                    "store_id": result.get("store_id"),
                    "user_id": current_user.get("user_id"),
                    "detail": {
                        "from_station": result.get("previous_station"),
                        "to_station": result.get("current_station"),
                        "status": result.get("stage"),
                    },
                }
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("[WORKSHOP] lab-scan audit failed: %s", e)

    # Auto-notify on DISPATCH -> READY (fail-soft; MUST NOT roll back the scan).
    if result.get("auto_notify"):
        try:
            fresh = repo.find_by_id(result.get("job_id")) or job
            notify = await _perform_ready_notify(fresh, current_user.get("user_id"))
            result["notify"] = notify
        except Exception as e:  # noqa: BLE001
            logger.warning("[WORKSHOP] auto-notify on dispatch failed: %s", e)
            result["notify"] = {"whatsapp_status": "FAILED"}

    return result


@router.post("/jobs/{job_id}/print-job-card")
async def print_job_card(
    job_id: str,
    current_user: dict = Depends(require_roles(*_LAB_SCAN_ROLES)),
):
    """Stamp job_card_printed_at / _by on a job and return its traveler label
    payload (the data the disposable Code128 job card prints). Idempotent --
    re-printing is allowed and re-stamps the timestamp."""
    repo = get_workshop_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Workshop repository unavailable")
    job = repo.find_by_id(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Workshop job not found")
    if not can_access_store_scoped(job.get("store_id"), current_user):
        raise HTTPException(status_code=404, detail="Workshop job not found")

    now = datetime.now()
    try:
        repo.update(
            job_id,
            {
                "job_card_printed_at": now.isoformat(),
                "job_card_printed_by": current_user.get("user_id"),
            },
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[WORKSHOP] print-job-card stamp failed: %s", e)

    return {
        "ok": True,
        "job_id": job_id,
        "job_number": job.get("job_number"),
        "barcode_value": job.get("job_number") or job_id,
        "job_card_printed_at": now.isoformat(),
        "message": "Job card stamped; print the traveler label.",
    }


# ============================================================================
# VENDOR / LENS-LAB ADMIN ENDPOINTS
# ============================================================================
# These three endpoints are the IMS-side complement to the public token-auth
# vendor portal (backend/api/routers/vendor_portal.py). Admin users assign a
# lab to a job, log status updates the lab phoned in, and pull a per-vendor
# queue view.


@router.patch("/jobs/{job_id}/vendor")
async def patch_job_vendor(
    job_id: str,
    payload: WorkshopVendorPatch,
    current_user: dict = Depends(get_current_user),
):
    """Admin assigns / updates the lens lab handling a workshop job.

    Setting `vendor_id` for the first time is the trigger that makes a
    job visible on the corresponding vendor portal token's `/jobs` feed.
    """
    repo = get_workshop_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Workshop repository unavailable")

    job = repo.find_by_id(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Workshop job not found")
    _assert_job_store_access(job, current_user)

    # Only admin / store-manager / workshop-staff can assign vendors. Sales
    # staff can see jobs but shouldn't be touching vendor IDs.
    user_roles = current_user.get("roles", [])
    if not any(
        r in user_roles
        for r in [
            "SUPERADMIN",
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "WORKSHOP_STAFF",
        ]
    ):
        raise HTTPException(
            status_code=403, detail="Not authorized to manage vendor assignment"
        )

    update = payload.model_dump(exclude_unset=True, exclude_none=True)
    if not update:
        return {"job_id": job_id, "message": "No changes"}

    # Validate vendor exists if a new vendor_id is supplied
    if "vendor_id" in update:
        vendor_repo = get_vendor_repository()
        if vendor_repo is not None:
            vendor = vendor_repo.find_by_id(update["vendor_id"])
            if vendor is None:
                raise HTTPException(status_code=404, detail="Vendor not found")
            # Cache the vendor's display name on the job for fast list rendering
            update["vendor_name"] = vendor.get("trade_name") or vendor.get("legal_name")

    update["vendor_updated_by"] = current_user.get("user_id")
    update["vendor_updated_at"] = datetime.now()

    if not repo.update(job_id, update):
        raise HTTPException(status_code=500, detail="Failed to update vendor fields")

    # Audit
    try:
        audit = get_audit_repository()
        if audit is not None:
            audit.create(
                {
                    "action": "workshop.vendor_assign",
                    "entity_type": "workshop_job",
                    "entity_id": job_id,
                    "store_id": job.get("store_id"),
                    "user_id": current_user.get("user_id"),
                    "detail": update,
                }
            )
    except Exception as e:
        logger.warning(f"workshop vendor_assign audit failed: {e}")

    return {"job_id": job_id, "message": "Vendor fields updated", **update}


@router.post("/jobs/{job_id}/vendor-status")
async def post_admin_vendor_status(
    job_id: str,
    payload: WorkshopVendorStatusBody,
    current_user: dict = Depends(get_current_user),
):
    """IMS user logs a vendor status update (e.g. "lab called, says
    DISPATCHED today"). Logged with source='ims_user' so the audit trail
    can distinguish phoned-in updates from the lab's own portal posts.
    """
    if payload.status not in ADMIN_VENDOR_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown vendor status. Allowed: {', '.join(sorted(ADMIN_VENDOR_STATUSES))}",
        )

    repo = get_workshop_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Workshop repository unavailable")

    job = repo.find_by_id(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Workshop job not found")
    _assert_job_store_access(job, current_user)
    if not job.get("vendor_id"):
        raise HTTPException(
            status_code=400,
            detail="Job has no vendor assigned. PATCH /vendor first.",
        )

    now = datetime.now()
    history_entry = {
        "status": payload.status,
        "note": payload.note,
        "source": "ims_user",
        "logged_by": current_user.get("user_id"),
        "logged_at": now.isoformat(),
    }
    history = list(job.get("vendor_status_history") or [])
    history.append(history_entry)

    update = {
        "vendor_status": payload.status,
        "vendor_status_history": history,
        "vendor_status_updated_at": now,
    }
    if payload.status == "DISPATCHED" and not job.get("vendor_dispatch_date"):
        update["vendor_dispatch_date"] = now.isoformat()
    if payload.status == "DELIVERED" and not job.get("vendor_received_date"):
        update["vendor_received_date"] = now.isoformat()

    repo.update(job_id, update)

    # Audit
    try:
        audit = get_audit_repository()
        if audit is not None:
            audit.create(
                {
                    "action": "workshop.vendor_status",
                    "entity_type": "workshop_job",
                    "entity_id": job_id,
                    "store_id": job.get("store_id"),
                    "user_id": current_user.get("user_id"),
                    "detail": {
                        "vendor_id": job.get("vendor_id"),
                        "status": payload.status,
                        "source": "ims_user",
                        "note": payload.note,
                    },
                }
            )
    except Exception as e:
        logger.warning(f"workshop vendor_status (ims) audit failed: {e}")

    return {
        "job_id": job_id,
        "vendor_status": payload.status,
        "logged_at": history_entry["logged_at"],
        "source": "ims_user",
    }


# NOTE: `/jobs/by-vendor/{vendor_id}` is registered up near the other
# specific `/jobs/...` routes so it doesn't get shadowed by the catch-all
# `/jobs/{job_id}` (FastAPI matches by registration order).

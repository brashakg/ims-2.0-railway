"""
IMS 2.0 - Workshop Labels + Scan-to-Advance Router
===================================================
Thermal sticker / label system for the optical workshop.

Three responsibilities, all ADDITIVE to the existing workshop module (they
reuse the same `workshop_jobs.status` field via the WorkshopJobRepository --
no parallel state machine):

1. Scan-to-advance: a barcode scan on a printed job label advances that job
   to the NEXT legal workshop stage, enforcing forward-only stage-gating
   (no skipping). Wrong job / wrong scan / wrong station return a LOUD
   structured error WITHOUT mutating state.

2. Label payloads: the data a thermal label needs (job traveler, stage
   sticker, ready/pickup) plus a frame-tag / CL-box payload from a
   product / stock id. Fail-soft -- never 500.

3. QZ Tray signing: silent raw (ZPL) printing through QZ Tray needs each
   request payload signed with a private key. We sign server-side (the key
   never leaves the backend) and expose the public cert. If the QZ env vars
   are unset we return 204 / empty so the frontend falls back to HTML
   printing (works before any cert is configured).

HARD constraints honoured here: no non-ASCII in source (Windows cp1252),
"Rs" not the rupee glyph, ASCII tags like [LABELS], fail-soft everywhere,
no secret values ever returned (only signatures + the public cert).
"""

from __future__ import annotations

import base64
import logging
import os
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel

logger = logging.getLogger(__name__)

from .auth import get_current_user, require_roles
from ..dependencies import (
    get_workshop_repository,
    get_prescription_repository,
    get_product_repository,
    get_stock_repository,
    get_store_repository,
    get_audit_repository,
    can_access_store_scoped,
)

router = APIRouter()

# Roles permitted to print a quarantine (DO-NOT-SHELVE) label. Mirrors
# inventory._STOCK_MANAGER_ROLES (the manager ladder that owns the quarantine
# lifecycle). SUPERADMIN auto-passes via require_roles.
_QUARANTINE_LABEL_ROLES = (
    "ADMIN",
    "AREA_MANAGER",
    "STORE_MANAGER",
)

# Luxury brands carrying an extra "BRAND AUTHORIZATION REQUIRED" line on the
# quarantine label (F21 owner decision option b -- the more rigorous choice for
# luxury optical). Same brand set as pricing_caps.LUXURY_BRAND_CAPS; matched on
# the upper-cased, stripped brand name.
_LUXURY_BRANDS = {
    "CARTIER",
    "CHOPARD",
    "BVLGARI",
    "GUCCI",
    "PRADA",
    "VERSACE",
    "BURBERRY",
}


# ============================================================================
# STAGE PIPELINE  (pure, unit-tested -- no DB access)
# ============================================================================
# The scan-to-advance happy path is the linear forward spine of the workshop
# job lifecycle. It deliberately mirrors workshop.VALID_JOB_TRANSITIONS:
#
#   PENDING -> IN_PROGRESS -> COMPLETED -> READY -> DELIVERED
#
# COMPLETED is the "work done, pending QC" stage in the real flow (QC pass
# routes COMPLETED -> READY). QC_FAILED is a BRANCH off COMPLETED, not a
# forward step, so a scan can never "advance" a job into QC_FAILED -- a
# rework/QC decision is an explicit human action via the existing
# /workshop/jobs/{id}/qc + /rework endpoints. DELIVERED is terminal.

STAGE_PIPELINE: List[str] = [
    "PENDING",
    "IN_PROGRESS",
    "COMPLETED",
    "READY",
    "DELIVERED",
]

# Human-friendly stage names for labels / UI (ASCII only).
STAGE_LABELS = {
    "PENDING": "Received",
    "IN_PROGRESS": "In Progress",
    "COMPLETED": "Work Done (QC)",
    "READY": "Ready for Pickup",
    "DELIVERED": "Delivered",
    "QC_FAILED": "QC Failed - Rework",
    "CANCELLED": "Cancelled",
}


def next_stage(current: Optional[str]) -> Optional[str]:
    """Return the immediate next workshop stage after `current`, or None.

    Pure -- no DB, no IO. This is the single source of truth for
    scan-to-advance gating.

    Rules:
      - A missing / empty / unknown current stage is treated as PENDING (a
        legacy job with no status can still be advanced to IN_PROGRESS).
      - The last stage (DELIVERED) has no next -> None.
      - QC_FAILED is NOT on the forward spine; it has no scan-advance next
        (rework is an explicit human QC action) -> None.
      - CANCELLED is terminal -> None.
    """
    cur = (current or "").strip().upper() or "PENDING"
    if cur in ("QC_FAILED", "CANCELLED"):
        return None
    if cur not in STAGE_PIPELINE:
        cur = "PENDING"
    idx = STAGE_PIPELINE.index(cur)
    if idx >= len(STAGE_PIPELINE) - 1:
        return None
    return STAGE_PIPELINE[idx + 1]


# Which stage each station is allowed to ADVANCE INTO. A scan at the wrong
# station (e.g. scanning at the FITTING station a job that is only ready to
# move to IN_PROGRESS) is rejected loudly. station is optional -- when
# omitted, any forward advance is allowed.
STATION_TARGET_STAGE = {
    "INTAKE": "IN_PROGRESS",  # received at counter -> start work
    "FITTING": "COMPLETED",  # lab finished cutting/mounting -> QC queue
    "QC": "READY",  # QC passed -> ready shelf
    "PICKUP": "DELIVERED",  # handed to customer
}


# ============================================================================
# SCHEMAS
# ============================================================================


class ScanAdvanceBody(BaseModel):
    """POST /workshop/jobs/{job_id}/scan-advance body.

    scanned_code: the raw value read off the label (job_id, job_number, or a
                  barcode that embeds either). Used to confirm the scan
                  targets THIS job -- mismatch = WRONG_JOB.
    station:      optional station hint (INTAKE/FITTING/QC/PICKUP). When set,
                  the computed next stage must match the station's expected
                  target stage, else WRONG_STATION.
    """

    scanned_code: Optional[str] = None
    station: Optional[str] = None


# Roles allowed to drive scan-to-advance. Mirrors workshop.py's fulfilment
# roles; SUPERADMIN always passes via require_roles. CASHIER is included so a
# front-desk cashier can scan a job to DELIVERED at pickup.
SCAN_ROLES = (
    "ADMIN",
    "AREA_MANAGER",
    "STORE_MANAGER",
    "WORKSHOP_STAFF",
    "CASHIER",
)


# ============================================================================
# SCAN-TO-ADVANCE
# ============================================================================


def _code_matches_job(scanned_code: str, job: dict) -> bool:
    """True if the scanned barcode plausibly identifies THIS job.

    Accepts an exact (case-insensitive) match on job_id or job_number, or a
    barcode that contains either as a substring (some label encoders prefix
    a store code). Pure helper.
    """
    code = (scanned_code or "").strip().upper()
    if not code:
        return False
    job_id = str(job.get("job_id") or "").strip().upper()
    job_no = str(job.get("job_number") or "").strip().upper()
    if code in (job_id, job_no):
        return True
    # Substring either way (label may carry a prefix/suffix).
    for ident in (job_id, job_no):
        if ident and (ident in code or code in ident):
            return True
    return False


@router.post("/workshop/jobs/{job_id}/scan-advance")
async def scan_advance(
    job_id: str,
    body: ScanAdvanceBody,
    current_user: dict = Depends(require_roles(*SCAN_ROLES)),
):
    """Advance a workshop job to its next legal stage via a barcode scan.

    LOUD-failure contract: on any guard failure this returns HTTP 200 with
    {ok: false, reason: ...} and DOES NOT change job state. The frontend
    keys off `ok` + `reason` to raise a wrong-scan / wrong-stage alert. (We
    return 200 rather than 4xx so the scan box can render a rich in-page
    error without axios swallowing the body.)

    reasons: NOT_FOUND, WRONG_JOB, TERMINAL_STAGE, WRONG_STATION, WRITE_FAILED
    success: {ok: true, job_id, previous, stage, station, stamped_at}
    """
    repo = get_workshop_repository()
    if repo is None:
        # Fail-soft: no DB -> report unavailable, never a 500.
        return {
            "ok": False,
            "reason": "REPO_UNAVAILABLE",
            "message": "Workshop repository unavailable; cannot advance.",
        }

    job = repo.find_by_id(job_id)
    if job is None:
        return {
            "ok": False,
            "reason": "NOT_FOUND",
            "message": f"No workshop job for id {job_id}.",
        }

    # NEW-IDOR-by-id: a store-scoped caller may only scan-advance a job at a store
    # it can access -- existence-hide a cross-store job (same NOT_FOUND contract).
    if not can_access_store_scoped(job.get("store_id"), current_user):
        return {
            "ok": False,
            "reason": "NOT_FOUND",
            "message": f"No workshop job for id {job_id}.",
        }

    # Wrong-job guard: the scanned label must identify this job. If the
    # caller supplied no scanned_code we trust the path job_id (e.g. a button
    # press rather than a physical scan).
    if body.scanned_code and not _code_matches_job(body.scanned_code, job):
        return {
            "ok": False,
            "reason": "WRONG_JOB",
            "message": (
                "Scanned label does not match this job. "
                f"Expected {job.get('job_number') or job_id}, got {body.scanned_code}."
            ),
            "expected": job.get("job_number") or job_id,
            "got": body.scanned_code,
        }

    current = (job.get("status") or "PENDING").strip().upper()
    target = next_stage(current)
    if target is None:
        return {
            "ok": False,
            "reason": "TERMINAL_STAGE",
            "message": (
                f"Job is at {STAGE_LABELS.get(current, current)} and cannot be "
                "advanced by scanning."
            ),
            "expected": None,
            "got": current,
        }

    # Wrong-station guard: if a station is named, the move it implies must be
    # the move this job is actually ready for.
    station = (body.station or "").strip().upper() or None
    if station:
        expected_target = STATION_TARGET_STAGE.get(station)
        if expected_target is None:
            return {
                "ok": False,
                "reason": "UNKNOWN_STATION",
                "message": f"Unknown station {station}.",
                "got": station,
            }
        if expected_target != target:
            return {
                "ok": False,
                "reason": "WRONG_STATION",
                "message": (
                    f"This job is ready to move to {STAGE_LABELS.get(target, target)}, "
                    f"not the {station} station's step "
                    f"({STAGE_LABELS.get(expected_target, expected_target)})."
                ),
                "expected": expected_target,
                "got": target,
                "station": station,
            }

    now = datetime.now()
    operator = current_user.get("user_id")

    # Advance the SAME status field the rest of the workshop uses. Stamp a
    # per-stage timestamp + append to an immutable scan history trail.
    history_entry = {
        "stage": target,
        "previous": current,
        "via": "scan",
        "station": station,
        "scanned_code": body.scanned_code,
        "operator": operator,
        "at": now.isoformat(),
    }
    history = list(job.get("scan_history") or [])
    history.append(history_entry)

    update = {
        "scan_history": history,
        f"stage_at_{target.lower()}": now.isoformat(),
    }
    ok = repo.update_status(job_id, target, operator, "scan-advance")
    if not ok:
        return {
            "ok": False,
            "reason": "WRITE_FAILED",
            "message": "Failed to persist the stage change.",
        }
    # Best-effort secondary write for history + per-stage timestamp (the
    # status itself already landed via update_status above).
    try:
        repo.update(job_id, update)
    except Exception as e:  # noqa: BLE001
        logger.warning("[LABELS] scan-advance history write failed: %s", e)

    # Audit (fail-soft).
    try:
        audit = get_audit_repository()
        if audit is not None:
            audit.create(
                {
                    "action": "workshop.scan_advance",
                    "entity_type": "workshop_job",
                    "entity_id": job_id,
                    "store_id": job.get("store_id"),
                    "user_id": operator,
                    "detail": {"from": current, "to": target, "station": station},
                }
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("[LABELS] scan-advance audit failed: %s", e)

    # E3w-NOT-WIRED: scan-advance moves a WORKSHOP JOB through its stage spine
    # (workshop_jobs.status) -- it does NOT change a stock_units state and there
    # is no matching ItemEventType in the merged item_events enum (no
    # SCAN_ADVANCE). Wiring it would require a parallel path, so it is left
    # unwired per the E3w contract and reported back to the orchestrator.

    return {
        "ok": True,
        "job_id": job_id,
        "job_number": job.get("job_number"),
        "previous": current,
        "stage": target,
        "stage_label": STAGE_LABELS.get(target, target),
        "station": station,
        "stamped_at": now.isoformat(),
        "message": f"Advanced to {STAGE_LABELS.get(target, target)}.",
    }


# ============================================================================
# LABEL PAYLOADS
# ============================================================================


def _rx_summary(rx: Optional[dict]) -> dict:
    """Build a compact, ASCII-safe Rx summary for a label from a prescription
    doc. Returns empty-ish dict if rx is missing. Pure."""
    if not rx or not isinstance(rx, dict):
        return {"right": "", "left": "", "available": False}

    def _eye(eye: Optional[dict]) -> str:
        if not eye or not isinstance(eye, dict):
            return ""
        parts = []
        if eye.get("sph"):
            parts.append(f"SPH {eye['sph']}")
        if eye.get("cyl"):
            parts.append(f"CYL {eye['cyl']}")
        if eye.get("axis") is not None and eye.get("axis") != "":
            parts.append(f"AX {eye['axis']}")
        if eye.get("add"):
            parts.append(f"ADD {eye['add']}")
        return " ".join(parts)

    return {
        "right": _eye(rx.get("right_eye")),
        "left": _eye(rx.get("left_eye")),
        "available": True,
    }


def _frame_summary(frame_details: Optional[dict]) -> str:
    """One-line ASCII frame description from a job's frame_details. Pure."""
    if not frame_details or not isinstance(frame_details, dict):
        return ""
    brand = frame_details.get("brand") or frame_details.get("brand_name") or ""
    model = frame_details.get("model") or frame_details.get("name") or ""
    color = frame_details.get("color") or frame_details.get("colour") or ""
    bits = [b for b in (brand, model) if b]
    out = " ".join(bits)
    if color:
        out = f"{out} ({color})" if out else color
    return out.strip()


def _lens_summary(lens_details: Optional[dict]) -> str:
    """One-line ASCII lens description from a job's lens_details. Pure."""
    if not lens_details or not isinstance(lens_details, dict):
        return ""
    parts = []
    for key in ("type", "lens_type", "material", "index", "coating", "tint"):
        val = lens_details.get(key)
        if val:
            parts.append(str(val))
    # de-dup while preserving order
    seen = set()
    uniq = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return " / ".join(uniq)


@router.get("/workshop/jobs/{job_id}/label")
async def get_job_label(
    job_id: str,
    type: str = Query("traveler", pattern="^(traveler|stage|ready)$"),
    current_user: dict = Depends(get_current_user),
):
    """Return the data a workshop label needs for a given job.

    type:
      traveler -- full work-order / job-card label (largest)
      stage    -- compact current-stage sticker
      ready    -- ready-for-pickup label (includes follow-up section flag)

    Fail-soft: a missing repo / prescription never raises; the payload just
    carries whatever is available with `barcode_value` always set to the
    job id so a label can still be printed + scanned.
    """
    repo = get_workshop_repository()
    if repo is None:
        # Minimal payload so the frontend can still render a barcode label.
        return {
            "ok": False,
            "type": type,
            "job_id": job_id,
            "barcode_value": job_id,
            "message": "Workshop repository unavailable; minimal label.",
        }

    job = repo.find_by_id(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Workshop job not found")
    # NEW-IDOR-LABEL: the label carries customer phone + medical Rx (SPH/CYL).
    # Existence-hide a job whose store the caller cannot access (cross-store
    # medical-PII leak). Admins / area-managers pass.
    if not can_access_store_scoped(job.get("store_id"), current_user):
        raise HTTPException(status_code=404, detail="Workshop job not found")

    # Rx summary (best-effort).
    rx_summary = {"right": "", "left": "", "available": False}
    pid = job.get("prescription_id")
    if pid:
        try:
            rx_repo = get_prescription_repository()
            if rx_repo is not None:
                rx = rx_repo.find_by_id(pid)
                rx_summary = _rx_summary(rx)
        except Exception as e:  # noqa: BLE001
            logger.warning("[LABELS] rx lookup failed: %s", e)

    # Store identity (best-effort): name + code + brand + address + GSTIN +
    # phone so the work-order traveler can show the FULL issuing-store block and
    # never falls back to a hardcoded brand name. The org module persists all of
    # these on the store doc (GSTIN derived from the entity by state).
    store_name = ""
    store_code = ""
    store_brand = ""
    store_address = ""
    store_gstin = ""
    store_phone = ""
    try:
        store_repo = get_store_repository()
        if store_repo is not None and job.get("store_id"):
            store = store_repo.find_by_id(job.get("store_id"))
            if store:
                store_name = store.get("name") or store.get("store_name") or ""
                store_code = store.get("store_code") or store.get("code") or ""
                store_brand = store.get("brand") or ""
                store_gstin = store.get("gstin") or ""
                store_phone = store.get("phone") or store.get("whatsapp") or ""
                store_address = ", ".join(
                    str(p)
                    for p in [
                        store.get("address"),
                        store.get("city"),
                        store.get("state"),
                        store.get("pincode"),
                    ]
                    if p
                )
    except Exception as e:  # noqa: BLE001
        logger.warning("[LABELS] store lookup failed: %s", e)

    status = (job.get("status") or "PENDING").strip().upper()

    payload = {
        "ok": True,
        "type": type,
        "job_id": job.get("job_id") or job_id,
        "job_number": job.get("job_number") or job_id,
        "barcode_value": job.get("job_number") or job.get("job_id") or job_id,
        "order_id": job.get("order_id"),
        "order_number": job.get("order_number"),
        "customer_name": job.get("customer_name") or "",
        "customer_phone": job.get("customer_phone") or "",
        "rx": rx_summary,
        "frame": _frame_summary(job.get("frame_details")),
        "lens": _lens_summary(job.get("lens_details")),
        "fitting_instructions": job.get("fitting_instructions") or "",
        "special_notes": job.get("special_notes") or "",
        "promised_date": job.get("expected_date") or job.get("promised_date") or "",
        "store_id": job.get("store_id") or "",
        "store_name": store_name,
        "store_code": store_code,
        "store_brand": store_brand,
        "store_address": store_address,
        "store_gstin": store_gstin,
        "store_phone": store_phone,
        "stage": status,
        "stage_label": STAGE_LABELS.get(status, status),
        "lens_status": job.get("lens_status") or "NOT_ORDERED",
        # Drives the handwriting / follow-up sections on the frontend label.
        "next_stage": next_stage(status),
        "include_followup": type == "ready",
        "generated_at": datetime.now().isoformat(),
    }
    return payload


@router.get("/workshop/product-label")
async def get_product_label(
    product_id: Optional[str] = Query(None),
    stock_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Frame-tag / contact-lens-box label payload from a product or stock id.

    Either product_id or stock_id may be given (stock takes precedence -- a
    stock row carries the barcode + batch/expiry for a specific unit). Fail-
    soft: returns ok=false with a minimal payload rather than raising.
    """
    if not product_id and not stock_id:
        return {
            "ok": False,
            "reason": "MISSING_ID",
            "message": "Provide product_id or stock_id.",
        }

    barcode_value = stock_id or product_id
    out: dict = {
        "ok": True,
        "barcode_value": barcode_value,
        "product_id": product_id,
        "stock_id": stock_id,
        "is_contact_lens": False,
        "generated_at": datetime.now().isoformat(),
    }

    # Stock unit (gives barcode + batch + expiry for a specific item).
    stock_doc = None
    if stock_id:
        try:
            stock_repo = get_stock_repository()
            if stock_repo is not None:
                stock_doc = stock_repo.find_by_id(stock_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("[LABELS] stock lookup failed: %s", e)
    if stock_doc:
        out["barcode_value"] = stock_doc.get("barcode") or barcode_value
        out["batch_code"] = stock_doc.get("batch_code") or stock_doc.get("lot")
        out["expiry"] = stock_doc.get("expiry") or stock_doc.get("expiry_date")
        out["store_id"] = stock_doc.get("store_id")
        if not product_id:
            product_id = stock_doc.get("product_id")
            out["product_id"] = product_id

    # Issuing-store identity (best-effort): so a frame tag / CL box is traceable
    # to the store/brand it was printed at in a multi-store chain.
    store_id_resolved = out.get("store_id")
    if store_id_resolved:
        try:
            store_repo = get_store_repository()
            if store_repo is not None:
                store = store_repo.find_by_id(store_id_resolved)
                if store:
                    out["store_name"] = (
                        store.get("name") or store.get("store_name") or ""
                    )
                    out["store_code"] = (
                        store.get("store_code") or store.get("code") or ""
                    )
                    out["store_brand"] = store.get("brand") or ""
        except Exception as e:  # noqa: BLE001
            logger.warning("[LABELS] product-label store lookup failed: %s", e)

    # Product master (name / brand / MRP / category / CL fields).
    if product_id:
        try:
            prod_repo = get_product_repository()
            if prod_repo is not None:
                prod = prod_repo.find_by_id(product_id)
                if prod:
                    out["name"] = prod.get("name") or prod.get("product_name") or ""
                    out["brand"] = prod.get("brand") or ""
                    out["sku"] = prod.get("sku") or ""
                    out["category"] = prod.get("category") or ""
                    # MRP / price -- ASCII "Rs", never the rupee glyph.
                    mrp = prod.get("mrp") or prod.get("price")
                    if mrp is not None:
                        out["mrp"] = mrp
                        out["price_label"] = f"Rs {mrp}"
                    cl_cats = ("CONTACT_LENS", "COLORED_CONTACT_LENS", "CL")
                    if (prod.get("category") or "").upper() in cl_cats:
                        out["is_contact_lens"] = True
                        out["cl"] = {
                            "modality": prod.get("modality"),
                            "base_curve": prod.get("base_curve"),
                            "diameter": prod.get("diameter"),
                            "power": prod.get("cl_power"),
                            "cyl": prod.get("cl_cyl"),
                            "axis": prod.get("cl_axis"),
                            "add": prod.get("cl_add"),
                            "color": prod.get("color"),
                            "pack_size": prod.get("pack_size"),
                        }
        except Exception as e:  # noqa: BLE001
            logger.warning("[LABELS] product lookup failed: %s", e)

    return out


@router.post("/labels/quarantine/{stock_id}")
async def get_quarantine_label(
    stock_id: str,
    current_user: dict = Depends(require_roles(*_QUARANTINE_LABEL_ROLES)),
):
    """Build the bright-red "QUARANTINED -- DO NOT SHELVE" label for a defective
    unit, flip quarantine_label_printed=true, and write an audit row.

    Guards: the unit must exist, be QUARANTINED (400 not_quarantined otherwise),
    and be store-accessible to the caller. The payload carries the unit's
    existing barcode, the quarantine reason/date, the store name, and -- for the
    named luxury brands -- a "BRAND AUTHORIZATION REQUIRED" line. ASCII only
    (Windows cp1252): the header uses a double-dash, never a unicode em-dash.
    """
    stock_repo = get_stock_repository()
    if stock_repo is None:
        raise HTTPException(status_code=503, detail="Stock repository unavailable")

    stock_doc = stock_repo.find_by_id(stock_id)
    if stock_doc is None:
        raise HTTPException(status_code=404, detail="Stock unit not found")

    store_id = stock_doc.get("store_id")
    if not can_access_store_scoped(store_id, current_user):
        raise HTTPException(status_code=404, detail="Stock unit not found")

    status = (stock_doc.get("status") or "").strip().upper()
    if status != "QUARANTINED":
        raise HTTPException(
            status_code=400,
            detail={"code": "not_quarantined", "message": "Unit is not quarantined."},
        )

    # Product master (name / brand / category) -- best-effort.
    product = {}
    pid = stock_doc.get("product_id")
    if pid:
        try:
            prod_repo = get_product_repository()
            if prod_repo is not None:
                product = prod_repo.find_by_id(pid) or {}
        except Exception as e:  # noqa: BLE001
            logger.warning("[LABELS] quarantine product lookup failed: %s", e)

    # Store identity -- best-effort (name + code + brand so a stray quarantined
    # unit is traceable to its origin store in a multi-store chain).
    store_name = ""
    store_code = ""
    store_brand = ""
    try:
        store_repo = get_store_repository()
        if store_repo is not None and store_id:
            store = store_repo.find_by_id(store_id)
            if store:
                store_name = store.get("name") or store.get("store_name") or ""
                store_code = store.get("store_code") or store.get("code") or ""
                store_brand = store.get("brand") or ""
    except Exception as e:  # noqa: BLE001
        logger.warning("[LABELS] quarantine store lookup failed: %s", e)

    rtv_vendor_id = stock_doc.get("rtv_vendor_id")
    q_at = stock_doc.get("quarantine_at")
    payload = {
        "ok": True,
        "label_type": "QUARANTINED",
        "header": "QUARANTINED -- DO NOT SHELVE",
        "sub_header": "RTV Pending" if rtv_vendor_id else "No RTV linked",
        "background_color": "#DC2626",
        "stock_id": stock_id,
        "barcode_value": stock_doc.get("barcode") or stock_id,
        "product_id": pid,
        "name": product.get("name") or product.get("product_name") or "",
        "brand": product.get("brand") or "",
        "category": product.get("category") or "",
        "quarantine_reason": stock_doc.get("quarantine_reason") or "",
        "quarantine_at": q_at.isoformat() if hasattr(q_at, "isoformat") else q_at,
        "quarantine_by_name": stock_doc.get("quarantine_by_name") or "",
        "store_id": store_id,
        "store_name": store_name,
        "store_code": store_code,
        "store_brand": store_brand,
        "rtv_vendor_id": rtv_vendor_id,
        "generated_at": datetime.now().isoformat(),
    }

    brand = (product.get("brand") or "").strip().upper()
    if brand in _LUXURY_BRANDS:
        payload["luxury_brand_line"] = "BRAND AUTHORIZATION REQUIRED"

    # Flip the printed flag (single-document write) BEFORE returning so the
    # queue's unlabeled count drops the moment the label is produced.
    try:
        stock_repo.update(stock_id, {"quarantine_label_printed": True})
    except Exception as e:  # noqa: BLE001
        logger.warning("[LABELS] quarantine label flag write failed: %s", e)

    # Audit (fail-soft) -- one STOCK_UNIT row per label print.
    try:
        audit = get_audit_repository()
        if audit is not None:
            audit.create(
                {
                    "action": "QUARANTINE_LABEL_PRINTED",
                    "entity_type": "STOCK_UNIT",
                    "entity_id": stock_id,
                    "store_id": store_id,
                    "user_id": current_user.get("user_id"),
                    "before_state": {"quarantine_label_printed": False},
                    "after_state": {"quarantine_label_printed": True},
                    "detail": {"reason": payload["quarantine_reason"]},
                }
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("[LABELS] quarantine label audit failed: %s", e)

    return payload


# ============================================================================
# QZ TRAY SIGNING  (silent raw printing)
# ============================================================================
# QZ Tray verifies that each print request came from a trusted source by
# checking an RSA-SHA256 signature over the request payload against a known
# certificate. We hold the PRIVATE key in an env var and sign server-side;
# the key value is NEVER returned. The public cert is served so the QZ JS
# client can present it. If either env var is unset we return 204 / empty so
# the frontend falls back to HTML printing -- the workshop is never blocked.


class QzSignBody(BaseModel):
    """POST /print/qz/sign body. QZ sends the exact string to sign as
    `request` (it may also be called `toSign` in some QZ versions)."""

    request: Optional[str] = None
    toSign: Optional[str] = None


def _qz_private_key() -> Optional[str]:
    """Read the QZ private key PEM from env. Supports a literal PEM or a
    base64-encoded PEM (QZ_PRIVATE_KEY_B64) for easier env storage. Returns
    None when unset -> caller falls back to HTML printing."""
    pem = os.getenv("QZ_PRIVATE_KEY")
    if pem:
        # Allow "\n"-escaped single-line env values.
        return pem.replace("\\n", "\n")
    b64 = os.getenv("QZ_PRIVATE_KEY_B64")
    if b64:
        try:
            return base64.b64decode(b64).decode("utf-8")
        except Exception:  # noqa: BLE001
            return None
    return None


def _qz_cert() -> Optional[str]:
    """Read the QZ public certificate PEM from env (literal or base64)."""
    pem = os.getenv("QZ_CERT")
    if pem:
        return pem.replace("\\n", "\n")
    b64 = os.getenv("QZ_CERT_B64")
    if b64:
        try:
            return base64.b64decode(b64).decode("utf-8")
        except Exception:  # noqa: BLE001
            return None
    return None


@router.get("/print/qz/cert")
async def qz_cert(current_user: dict = Depends(get_current_user)):
    """Return the QZ Tray public certificate (PEM) for the JS client.

    Returns 204 (empty) when no cert is configured -> the frontend treats
    that as "QZ not set up" and uses HTML printing. Only the PUBLIC cert is
    ever returned here.
    """
    cert = _qz_cert()
    if not cert:
        return Response(status_code=204)
    # text/plain so the QZ client receives the raw PEM string.
    return Response(content=cert, media_type="text/plain")


@router.post("/print/qz/sign")
async def qz_sign(
    body: QzSignBody,
    current_user: dict = Depends(get_current_user),
):
    """Sign a QZ Tray request payload with the server-held RSA private key.

    Returns the base64 signature as text/plain (what the QZ JS client
    expects). Returns 204 (empty) when no private key is configured OR when
    the cryptography backend is unavailable -> the frontend falls back to
    HTML printing. The private key value is NEVER returned. Fail-soft: a
    signing error returns 204, never a 500.
    """
    to_sign = body.request if body.request is not None else body.toSign
    if to_sign is None:
        # QZ may probe with an empty body; nothing to sign.
        return Response(status_code=204)

    pem = _qz_private_key()
    if not pem:
        return Response(status_code=204)

    try:
        # Imported lazily so the module loads even if `cryptography` isn't
        # installed in a given environment (fail-soft to HTML printing).
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        private_key = serialization.load_pem_private_key(
            pem.encode("utf-8"), password=None
        )
        signature = private_key.sign(
            to_sign.encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        b64sig = base64.b64encode(signature).decode("ascii")
        return Response(content=b64sig, media_type="text/plain")
    except Exception as e:  # noqa: BLE001
        # Never surface key material or a stack trace; just fall back.
        logger.warning("[LABELS] QZ sign failed (falling back to HTML): %s", e)
        return Response(status_code=204)

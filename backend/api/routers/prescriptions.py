"""
IMS 2.0 - Prescriptions Router
===============================
Prescription management endpoints
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field, field_validator
from typing import Optional
from datetime import date, datetime, timedelta
import uuid

from .auth import get_current_user
from ..dependencies import get_prescription_repository, get_customer_repository

router = APIRouter()


# ============================================================================
# Prescription Value Validation Ranges — per IMS 2.0 business rules
# (see CLAUDE.md and docs/reference/IMS2_Complete_App_Summary.docx §6.3).
# ============================================================================
# SPH (Sphere):  -20.00 to +20.00 diopters, 0.25 steps
# CYL (Cylinder): -6.00 to +6.00 diopters, 0.25 steps
# AXIS:           1 to 180 degrees (whole number) — enforced via Field(ge/le)
# ADD (Addition): +0.75 to +3.50 diopters, 0.25 steps
# PD (Pupillary Distance): 20 to 80 mm (wider than clinical ADD/CYL range
#                                       because PD is a measurement, not Rx)
#
# These are the ONLY source of truth. Endpoint-level inline checks call the
# same validator — don't duplicate ranges elsewhere. If you need to relax a
# limit, update this dict AND the spec docs; don't add a second check.

_RX_LIMITS = {
    "sph": (-20.0, 20.0),
    "cyl": (-6.0, 6.0),
    "add": (0.75, 3.50),
    "pd": (20.0, 80.0),
}


def _validate_rx_value(value: Optional[str], field_name: str) -> Optional[str]:
    """Validate that an Rx string value falls within acceptable clinical range."""
    if value is None or value.strip() == "" or value.strip() == "0":
        return value
    try:
        num = float(value)
    except (ValueError, TypeError):
        raise ValueError(f"{field_name} must be a valid number, got '{value}'")
    lo, hi = _RX_LIMITS.get(field_name, (-999, 999))
    if num < lo or num > hi:
        raise ValueError(
            f"{field_name} value {num} is outside the valid range ({lo} to {hi}). "
            f"Please double-check the prescription."
        )
    return value


# ============================================================================
# SCHEMAS
# ============================================================================


class EyeData(BaseModel):
    sph: Optional[str] = None
    cyl: Optional[str] = None
    axis: Optional[int] = Field(None, ge=1, le=180)
    add: Optional[str] = None
    pd: Optional[str] = None
    prism: Optional[str] = None
    base: Optional[str] = None
    acuity: Optional[str] = None

    @field_validator("sph")
    @classmethod
    def validate_sph(cls, v: Optional[str]) -> Optional[str]:
        return _validate_rx_value(v, "sph")

    @field_validator("cyl")
    @classmethod
    def validate_cyl(cls, v: Optional[str]) -> Optional[str]:
        return _validate_rx_value(v, "cyl")

    @field_validator("add")
    @classmethod
    def validate_add(cls, v: Optional[str]) -> Optional[str]:
        return _validate_rx_value(v, "add")

    @field_validator("pd")
    @classmethod
    def validate_pd(cls, v: Optional[str]) -> Optional[str]:
        return _validate_rx_value(v, "pd")


class PrescriptionCreate(BaseModel):
    patient_id: str
    customer_id: str
    source: str = "TESTED_AT_STORE"  # TESTED_AT_STORE, FROM_DOCTOR
    optometrist_id: Optional[str] = None
    validity_months: int = Field(default=12, ge=6, le=24)
    right_eye: EyeData
    left_eye: EyeData
    lens_recommendation: Optional[str] = None
    coating_recommendation: Optional[str] = None
    remarks: Optional[str] = None


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def generate_rx_number() -> str:
    """Generate unique prescription number"""
    return f"RX-{datetime.now().strftime('%y%m%d')}-{str(uuid.uuid4())[:6].upper()}"


# ============================================================================
# ENDPOINTS
# ============================================================================


# NOTE: Specific routes MUST come before /{prescription_id}
@router.get("/patient/{patient_id}")
async def get_patient_prescriptions(
    patient_id: str, current_user: dict = Depends(get_current_user)
):
    """Get all prescriptions for a patient"""
    repo = get_prescription_repository()

    if repo is not None:
        prescriptions = repo.find_by_patient(patient_id)
        return {"prescriptions": prescriptions, "total": len(prescriptions)}

    return {"prescriptions": [], "total": 0}


@router.get("/patient/{patient_id}/latest")
async def get_latest_prescription(
    patient_id: str, current_user: dict = Depends(get_current_user)
):
    """Get latest prescription for a patient"""
    repo = get_prescription_repository()

    if repo is not None:
        prescriptions = repo.find_by_patient(patient_id, limit=1)
        if prescriptions:
            return {"prescription": prescriptions[0]}
        return {"prescription": None}

    return {"prescription": None}


@router.get("/patient/{patient_id}/valid")
async def get_valid_prescriptions(
    patient_id: str, current_user: dict = Depends(get_current_user)
):
    """Get valid (non-expired) prescriptions for a patient"""
    repo = get_prescription_repository()

    if repo is not None:
        prescriptions = repo.find_valid(patient_id)
        return {"prescriptions": prescriptions, "total": len(prescriptions)}

    return {"prescriptions": [], "total": 0}


@router.get("/expiring")
async def get_expiring_prescriptions(
    days: int = Query(30, ge=7, le=90), current_user: dict = Depends(get_current_user)
):
    """Get prescriptions expiring within specified days"""
    repo = get_prescription_repository()

    if repo is not None:
        prescriptions = repo.find_expiring_soon(days)
        return {"prescriptions": prescriptions, "total": len(prescriptions)}

    return {"prescriptions": [], "total": 0}


@router.get("/optometrist/{optometrist_id}/stats")
async def get_optometrist_stats(
    optometrist_id: str,
    from_date: date = Query(...),
    to_date: date = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Get prescription statistics for an optometrist"""
    repo = get_prescription_repository()

    if repo is not None:
        stats = repo.get_optometrist_stats(optometrist_id, from_date, to_date)
        return stats

    return {"total": 0, "tested_at_store": 0}


@router.get("")
@router.get("/")
async def list_prescriptions(
    patient_id: Optional[str] = Query(None),
    customer_id: Optional[str] = Query(None),
    optometrist_id: Optional[str] = Query(None),
    store_id: Optional[str] = Query(None),
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """List prescriptions with filters"""
    repo = get_prescription_repository()
    active_store = store_id or current_user.get("active_store_id")

    if repo is not None:
        if patient_id:
            prescriptions = repo.find_by_patient(patient_id)
        elif customer_id:
            prescriptions = repo.find_by_customer(customer_id)
        elif optometrist_id:
            prescriptions = repo.find_by_optometrist(optometrist_id, from_date, to_date)
        elif active_store:
            prescriptions = repo.find_by_store(active_store, from_date, to_date)
        else:
            prescriptions = repo.find_many({}, skip=skip, limit=limit)

        return {"prescriptions": prescriptions, "total": len(prescriptions)}

    return {"prescriptions": [], "total": 0}


@router.post("", status_code=201)
@router.post("/", status_code=201, include_in_schema=False)
async def create_prescription(
    rx: PrescriptionCreate, current_user: dict = Depends(get_current_user)
):
    """Create a new prescription"""
    repo = get_prescription_repository()
    customer_repo = get_customer_repository()

    # Role-based access: only qualified roles may create prescriptions
    user_roles = current_user.get("roles", [])
    CLINICAL_ROLES = {"SUPERADMIN", "ADMIN", "STORE_MANAGER", "OPTOMETRIST"}
    is_clinical = bool(set(user_roles) & CLINICAL_ROLES)
    if not is_clinical:
        raise HTTPException(
            status_code=403,
            detail="Only optometrists and managers can create prescriptions. "
            "Your role does not have clinical access.",
        )

    is_admin = any(r in user_roles for r in ["SUPERADMIN", "ADMIN", "STORE_MANAGER"])
    if rx.source == "TESTED_AT_STORE" and not rx.optometrist_id and not is_admin:
        raise HTTPException(
            status_code=400, detail="Optometrist required for store tests"
        )
    # If FROM_DOCTOR, optometrist_id is optional
    if rx.source == "FROM_DOCTOR" and not rx.optometrist_id:
        rx.optometrist_id = current_user.get("user_id", "external-doctor")

    # Validate prescription power ranges
    def _validate_power(eye_label: str, eye: EyeData):
        if eye.sph:
            try:
                sph_val = float(eye.sph)
                if sph_val < -20.0 or sph_val > 20.0:
                    raise HTTPException(
                        status_code=422,
                        detail=f"{eye_label} SPH must be between -20.00 and +20.00",
                    )
            except ValueError:
                raise HTTPException(
                    status_code=422, detail=f"{eye_label} SPH must be a valid number"
                )
        if eye.cyl:
            try:
                cyl_val = float(eye.cyl)
                if cyl_val < -10.0 or cyl_val > 10.0:
                    raise HTTPException(
                        status_code=422,
                        detail=f"{eye_label} CYL must be between -10.00 and +10.00",
                    )
            except ValueError:
                raise HTTPException(
                    status_code=422, detail=f"{eye_label} CYL must be a valid number"
                )
        if eye.axis is not None:
            if not isinstance(eye.axis, int) or eye.axis < 1 or eye.axis > 180:
                raise HTTPException(
                    status_code=422,
                    detail=f"{eye_label} AXIS must be whole number between 1 and 180",
                )

    _validate_power("Right eye", rx.right_eye)
    _validate_power("Left eye", rx.left_eye)

    if repo is not None:
        # Verify customer exists
        if customer_repo is not None:
            customer = customer_repo.find_by_id(rx.customer_id)
            is_walkin = not customer and (
                rx.customer_id.startswith("walkin-") or rx.customer_id == "walk-in"
            )
            if not customer and not is_walkin:
                raise HTTPException(status_code=404, detail="Customer not found")

        prescription_date = datetime.now()
        expiry_date = prescription_date + timedelta(days=rx.validity_months * 30)

        rx_data = {
            "prescription_number": generate_rx_number(),
            "patient_id": rx.patient_id,
            "customer_id": rx.customer_id,
            "store_id": current_user.get("active_store_id"),
            "source": rx.source,
            "optometrist_id": rx.optometrist_id,
            "prescription_date": prescription_date.isoformat(),
            "expiry_date": expiry_date.isoformat(),
            "validity_months": rx.validity_months,
            "right_eye": rx.right_eye.model_dump(),
            "left_eye": rx.left_eye.model_dump(),
            "lens_recommendation": rx.lens_recommendation,
            "coating_recommendation": rx.coating_recommendation,
            "remarks": rx.remarks,
            "created_by": current_user.get("user_id"),
        }

        created = repo.create(rx_data)
        if created:
            return {
                "prescription_id": created["prescription_id"],
                "prescription_number": created["prescription_number"],
                "message": "Prescription created",
            }

        raise HTTPException(status_code=500, detail="Failed to create prescription")

    return {
        "prescription_id": str(uuid.uuid4()),
        "prescription_number": generate_rx_number(),
        "message": "Prescription created",
    }


@router.get("/{prescription_id}")
async def get_prescription(
    prescription_id: str, current_user: dict = Depends(get_current_user)
):
    """Get prescription by ID"""
    repo = get_prescription_repository()

    if repo is not None:
        prescription = repo.find_by_id(prescription_id)
        if prescription is not None:
            return prescription
        raise HTTPException(status_code=404, detail="Prescription not found")

    return {"prescription_id": prescription_id}


@router.get("/{prescription_id}/validate")
async def validate_prescription(
    prescription_id: str, current_user: dict = Depends(get_current_user)
):
    """Validate a prescription's Rx values against the clinical ranges
    (SPH -20..+20, CYL -6..+6, AXIS 1-180, ADD +0.75..+3.50) and report
    expiry. Frontend prescriptionApi.validatePrescription was 404'ing.
    Returns {valid, expired, issues:[...]}."""
    repo = get_prescription_repository()
    if repo is None:
        return {
            "prescription_id": prescription_id,
            "valid": True,
            "expired": False,
            "issues": [],
        }
    rx = repo.find_by_id(prescription_id)
    if rx is None:
        raise HTTPException(status_code=404, detail="Prescription not found")

    issues = []

    def _check(eye_label, eye):
        if not isinstance(eye, dict):
            return
        sph = eye.get("sphere", eye.get("sph"))
        cyl = eye.get("cylinder", eye.get("cyl"))
        axis = eye.get("axis")
        add = eye.get("add", eye.get("addition"))
        try:
            if sph is not None and sph != "" and not (-20.0 <= float(sph) <= 20.0):
                issues.append(f"{eye_label} SPH {sph} out of range (-20..+20)")
            if cyl is not None and cyl != "" and not (-6.0 <= float(cyl) <= 6.0):
                issues.append(f"{eye_label} CYL {cyl} out of range (-6..+6)")
            if axis is not None and axis != "" and not (1 <= int(float(axis)) <= 180):
                issues.append(f"{eye_label} AXIS {axis} out of range (1-180)")
            if add not in (None, "", 0) and not (0.75 <= float(add) <= 3.50):
                issues.append(f"{eye_label} ADD {add} out of range (+0.75..+3.50)")
        except (ValueError, TypeError):
            issues.append(f"{eye_label} has a non-numeric Rx value")

    _check("Right", rx.get("right_eye") or rx.get("rightEye"))
    _check("Left", rx.get("left_eye") or rx.get("leftEye"))

    # Expiry — 12 months from test/created date unless validity set
    expired = False
    try:
        from datetime import datetime as _dt

        test_date = rx.get("test_date") or rx.get("testDate") or rx.get("created_at")
        months = int(rx.get("validity_months") or rx.get("validityMonths") or 12)
        if test_date:
            td = _dt.fromisoformat(str(test_date).replace("Z", "").split(".")[0])
            expiry = td.replace(
                year=td.year + (td.month - 1 + months) // 12,
                month=(td.month - 1 + months) % 12 + 1,
            )
            expired = _dt.utcnow() > expiry
    except Exception:
        pass

    return {
        "prescription_id": prescription_id,
        "valid": len(issues) == 0,
        "expired": expired,
        "issues": issues,
    }


@router.get("/{prescription_id}/print")
async def print_prescription(
    prescription_id: str, current_user: dict = Depends(get_current_user)
):
    """Generate printable prescription HTML"""
    repo = get_prescription_repository()

    if repo is not None:
        prescription = repo.find_by_id(prescription_id)
        if prescription is None:
            raise HTTPException(status_code=404, detail="Prescription not found")

        # Generate basic HTML for printing
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Prescription {prescription.get('prescription_number')}</title>
            <style>
                body {{ font-family: Arial, sans-serif; padding: 20px; }}
                .header {{ text-align: center; margin-bottom: 20px; }}
                .rx-number {{ font-size: 14px; color: #666; }}
                table {{ width: 100%; border-collapse: collapse; margin: 20px 0; }}
                th, td {{ border: 1px solid #ddd; padding: 8px; text-align: center; }}
                th {{ background: #f5f5f5; }}
                .footer {{ margin-top: 40px; }}
            </style>
        </head>
        <body>
            <div class="header">
                <h2>Eye Prescription</h2>
                <p class="rx-number">{prescription.get('prescription_number')}</p>
                <p>Date: {prescription.get('prescription_date', '')[:10]}</p>
            </div>
            <table>
                <tr>
                    <th></th><th>SPH</th><th>CYL</th><th>AXIS</th><th>ADD</th><th>PD</th>
                </tr>
                <tr>
                    <td><strong>RE</strong></td>
                    <td>{prescription.get('right_eye', {}).get('sph', '-')}</td>
                    <td>{prescription.get('right_eye', {}).get('cyl', '-')}</td>
                    <td>{prescription.get('right_eye', {}).get('axis', '-')}</td>
                    <td>{prescription.get('right_eye', {}).get('add', '-')}</td>
                    <td>{prescription.get('right_eye', {}).get('pd', '-')}</td>
                </tr>
                <tr>
                    <td><strong>LE</strong></td>
                    <td>{prescription.get('left_eye', {}).get('sph', '-')}</td>
                    <td>{prescription.get('left_eye', {}).get('cyl', '-')}</td>
                    <td>{prescription.get('left_eye', {}).get('axis', '-')}</td>
                    <td>{prescription.get('left_eye', {}).get('add', '-')}</td>
                    <td>{prescription.get('left_eye', {}).get('pd', '-')}</td>
                </tr>
            </table>
            <p><strong>Lens Recommendation:</strong> {prescription.get('lens_recommendation', 'N/A')}</p>
            <p><strong>Coating:</strong> {prescription.get('coating_recommendation', 'N/A')}</p>
            <p><strong>Remarks:</strong> {prescription.get('remarks', '-')}</p>
            <div class="footer">
                <p>Valid until: {prescription.get('expiry_date', '')[:10]}</p>
            </div>
        </body>
        </html>
        """

        return {
            "html": html,
            "prescription_number": prescription.get("prescription_number"),
        }

    return {"html": "<html><body>Prescription not found</body></html>"}


# ============================================================================
# 4-VERSION PRESCRIPTION MODEL (May 2026)
# ============================================================================
# Per the YouTube competitor research (Optical CRM ships a 4-version Rx
# model), each visit captures FOUR distinct Rx states: before_testing /
# after_testing / manual / final. `final` is mirrored into top-level
# right_eye/left_eye/pd on finalize so existing POS code keeps working.
# Pure logic lives in `api/services/prescription_versions.py`.


class VersionEyeData(BaseModel):
    sphere: Optional[float] = None
    cylinder: Optional[float] = None
    axis: Optional[int] = None
    addition: Optional[float] = None
    va: Optional[str] = None


class PrescriptionVersionPayload(BaseModel):
    right_eye: Optional[VersionEyeData] = None
    left_eye: Optional[VersionEyeData] = None
    pd: Optional[float] = None
    source: Optional[str] = Field(
        None,
        description="auto_ref | subjective_refraction | manual_override | optometrist_signoff | etc",
    )
    override_reason: Optional[str] = None
    signed_off_by: Optional[str] = None  # Required for the `final` version


@router.patch("/{prescription_id}/version/{version_name}")
async def patch_prescription_version(
    prescription_id: str,
    version_name: str,
    payload: PrescriptionVersionPayload,
    current_user: dict = Depends(get_current_user),
):
    """Write or overwrite one of the 4 Rx versions. Only writable
    while status='in_progress'. Use POST /finalize to lock the record."""
    from ..services.prescription_versions import (
        VALID_VERSION_NAMES,
        merge_version,
        backfill_versions_from_top_level,
    )

    if version_name not in VALID_VERSION_NAMES:
        raise HTTPException(
            status_code=400,
            detail=f"version_name must be one of {sorted(VALID_VERSION_NAMES)}",
        )

    repo = get_prescription_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database not available")
    doc = repo.find_by_id(prescription_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Prescription not found")

    # Backfill legacy single-Rx docs so they're patchable as if they
    # had a versions block from the start.
    doc = backfill_versions_from_top_level(doc)

    body = payload.model_dump(exclude_unset=True)
    try:
        new_doc = merge_version(
            doc, version_name, body, captured_by=current_user.get("user_id")
        )
    except ValueError as e:
        if "finalized" in str(e):
            raise HTTPException(status_code=409, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))

    repo.update(
        prescription_id,
        {
            "versions": new_doc["versions"],
            "status": new_doc["status"],
        },
    )
    return {"prescription_id": prescription_id, "versions": new_doc["versions"]}


@router.post("/{prescription_id}/finalize")
async def finalize_prescription(
    prescription_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Lock the prescription. Mirrors versions.final into top-level
    right_eye/left_eye/pd for backwards-compat. Only optometrist /
    superadmin / admin can finalize."""
    from ..services.prescription_versions import (
        can_finalize,
        mirror_final_to_top_level,
    )

    roles = current_user.get("roles") or []
    if not any(r in {"OPTOMETRIST", "SUPERADMIN", "ADMIN"} for r in roles):
        raise HTTPException(
            status_code=403,
            detail="Only OPTOMETRIST / SUPERADMIN / ADMIN can finalize a prescription",
        )

    repo = get_prescription_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database not available")
    doc = repo.find_by_id(prescription_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Prescription not found")
    if doc.get("status") == "finalized":
        raise HTTPException(status_code=409, detail="Already finalized")
    if not can_finalize(doc):
        raise HTTPException(
            status_code=400,
            detail="Cannot finalize: `final` version is missing right_eye / left_eye",
        )

    new_doc = mirror_final_to_top_level(doc)
    repo.update(
        prescription_id,
        {
            "right_eye": new_doc.get("right_eye"),
            "left_eye": new_doc.get("left_eye"),
            "pd": new_doc.get("pd"),
            "status": "finalized",
            "finalized_at": new_doc.get("finalized_at"),
        },
    )
    return {"prescription_id": prescription_id, "status": "finalized"}


@router.get("/{prescription_id}/versions")
async def get_prescription_versions(
    prescription_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Return all 4 versions for a prescription. Auto-backfills for
    legacy single-Rx docs."""
    from ..services.prescription_versions import backfill_versions_from_top_level

    repo = get_prescription_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database not available")
    doc = repo.find_by_id(prescription_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Prescription not found")
    doc = backfill_versions_from_top_level(doc)
    return {
        "prescription_id": prescription_id,
        "status": doc.get("status", "in_progress"),
        "versions": doc.get("versions"),
        "finalized_at": doc.get("finalized_at"),
    }


@router.get("/customer/{customer_id}/progression")
async def get_prescription_progression(
    customer_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Adjacent-visit deltas for a customer's FINAL Rx history. Useful
    for the clinical dashboard's progression chart ('is this myopia
    accelerating?')."""
    from ..services.prescription_versions import progression_diffs

    repo = get_prescription_repository()
    if repo is None:
        return {"customer_id": customer_id, "deltas": []}
    history = repo.find_many({"customer_id": customer_id}) or []
    deltas = progression_diffs(history)
    return {"customer_id": customer_id, "deltas": deltas, "visits": len(history)}

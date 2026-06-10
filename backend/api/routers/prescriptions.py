"""
IMS 2.0 - Prescriptions Router
===============================
Prescription management endpoints
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional
from typing_extensions import Literal
from datetime import date, datetime, timedelta
import uuid

from .auth import get_current_user
from ..dependencies import (
    get_prescription_repository,
    get_customer_repository,
    get_audit_repository,
    can_access_store_scoped,
    filter_docs_by_store,
)

router = APIRouter()


# Roles permitted to READ clinical prescriptions. Rx carry medical data
# (SPH/CYL/AXIS, IPD, lens Rx) + patient PII, so the read surface is limited to
# clinical, POS-fulfilment, workshop, and management roles. This is ADDITIVE
# defense on top of the per-object store-scope guard (BUG-088): it blocks
# CASHIER (payment-only), ACCOUNTANT, CATALOG_MANAGER and INVENTORY_HQ -- roles
# with no clinical need -- from reading any prescription, even in their own
# store. SUPERADMIN/ADMIN pass via membership. POS order-building runs as
# SALES_STAFF/SALES_CASHIER (both allowed); workshop lens-grinding as
# WORKSHOP_STAFF; Rx capture as OPTOMETRIST.
_RX_READ_ROLES = {
    "SUPERADMIN",
    "ADMIN",
    "AREA_MANAGER",
    "STORE_MANAGER",
    "OPTOMETRIST",
    "SALES_CASHIER",
    "SALES_STAFF",
    "WORKSHOP_STAFF",
}


def require_rx_read(current_user: dict = Depends(get_current_user)) -> dict:
    """Gate clinical-Rx READS to roles with a clinical/fulfilment need.

    Returns the caller (so it is a drop-in replacement for the get_current_user
    dependency on read endpoints). Store-scope is still enforced separately.
    """
    roles = set(current_user.get("roles") or [])
    if roles & _RX_READ_ROLES:
        return current_user
    raise HTTPException(
        status_code=403,
        detail="This role is not permitted to read clinical prescriptions",
    )


# Roles permitted to WRITE/edit a prescription's clinical content (PUT edit,
# 4-version PATCH). Same set create_prescription uses. Narrower than
# _RX_READ_ROLES on purpose: POS/workshop roles may READ an Rx to fulfil it
# but must never ALTER medical data.
_RX_WRITE_ROLES = {"SUPERADMIN", "ADMIN", "STORE_MANAGER", "OPTOMETRIST"}


def _require_rx_write_roles(current_user: dict) -> None:
    """Inline role gate for Rx WRITE paths. Raises the canonical body-specific
    clinical 403 (asserted by tests + mirrored as ``self_enforced`` in
    rbac_policy) when the caller holds no clinical-write role."""
    roles = set(current_user.get("roles") or [])
    if roles & _RX_WRITE_ROLES:
        return
    raise HTTPException(
        status_code=403,
        detail="Only optometrists and managers can edit prescriptions. "
        "Your role does not have clinical access.",
    )


def _store_scope_or_404(doc: dict, current_user: dict) -> None:
    """Per-object store-scope guard for Rx WRITE paths (BUG-088 class): the
    caller must be scoped to the prescription's store. 404 -- not 403 -- so an
    out-of-scope caller can't even confirm the Rx exists (same hiding the
    sibling READ guards use). Unattributed (no store_id) docs are writable
    only by cross-store admins, exactly like the reads."""
    if not can_access_store_scoped((doc or {}).get("store_id"), current_user):
        raise HTTPException(status_code=404, detail="Prescription not found")


def _audit_rx(
    action: str,
    prescription_id: Optional[str],
    current_user: dict,
    *,
    customer_id: Optional[str] = None,
    rx_kind: Optional[str] = None,
    before_state: Optional[dict] = None,
    after_state: Optional[dict] = None,
    detail: Optional[dict] = None,
) -> None:
    """Best-effort domain audit for a prescription action -> append-only
    audit_logs (source="domain"). Clinical / Rx saves were invisible in the
    Activity Log; this records who saved which Rx for which customer. FAIL-SOFT:
    any audit failure is swallowed so it can never undo or 500 the Rx write.
    ``timestamp`` is stamped explicitly (the Activity Log sorts + range-filters
    on it; BaseRepository only sets created_at/updated_at)."""
    try:
        audit_repo = get_audit_repository()
        if audit_repo is None:
            return
        merged_detail = {"customer_id": customer_id, "rx_kind": rx_kind}
        if detail:
            merged_detail.update(detail)
        row = {
            "action": action,
            "entity_type": "PRESCRIPTION",
            "entity_id": prescription_id,
            "store_id": current_user.get("active_store_id"),
            "user_id": current_user.get("user_id"),
            "user_name": current_user.get("full_name") or current_user.get("username"),
            "timestamp": datetime.utcnow(),
            "severity": "INFO",
            "source": "domain",
            "detail": merged_detail,
        }
        if before_state is not None:
            row["before_state"] = before_state
        if after_state is not None:
            row["after_state"] = after_state
        audit_repo.create(row)
    except Exception:  # noqa: BLE001 - audit must never break the Rx write
        pass


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

# ============================================================================
# Contact-lens (CL) prescription support (additive, May 2026)
# ============================================================================
# A contact-lens Rx is a DISTINCT thing from a spectacle Rx: it is fit by
# base-curve (BC) + diameter (DIA) rather than PD, and the powers are
# vertex-adjusted to sit on the cornea. We reuse the SAME prescriptions
# collection + validity/expiry/optometrist/store machinery, discriminated by
# a top-level `rx_kind` field. `rx_kind` defaults to "SPECTACLE" so every
# existing prescription + endpoint behaves identically.
#
# CL field names mirror the just-shipped CL inventory product master
# (products.py: cl_power/cl_cyl/cl_axis/cl_add/base_curve/diameter/modality)
# so the Rx and the product it binds to never disagree on naming.

# Allowed CL replacement modalities -- kept in sync with products.CL_MODALITIES.
CL_MODALITIES = ("DAILY", "FORTNIGHTLY", "MONTHLY", "QUARTERLY", "YEARLY", "COLOR")

# CL power ranges are tighter than spectacle (toric cyl on a soft CL rarely
# exceeds -2.75; powers beyond these are almost always a data-entry slip).
_CL_LIMITS = {
    "cl_power": (-30.0, 30.0),
    "cl_cyl": (-10.0, 10.0),
    "cl_add": (0.0, 4.0),
    "base_curve": (7.0, 10.0),
    "diameter": (12.0, 16.0),
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
    # SYSTEM_INTENT section 4: dioptric powers move in 0.25 steps. Reject
    # off-step values (e.g. +1.30, +0.10) that no lens is ground to. AXIS is
    # integer-checked elsewhere; linear measures (PD/base_curve/diameter) are
    # exempt from the 0.25 grid.
    if field_name in ("sph", "cyl", "add", "cl_power", "cl_cyl", "cl_add"):
        if round(num * 100) % 25 != 0:
            raise ValueError(
                f"{field_name} value {num} must be in 0.25-diopter steps "
                f"(e.g. -1.25, 0.00, +2.50)."
            )
    return value


def _validate_rx_number(value, field_name: str):
    """Numeric (float) variant of _validate_rx_value for the 4-version Rx model,
    which stores sphere/cylinder/addition as floats. Applies the SAME ranges +
    0.25-diopter grid, so the version-PATCH path can no longer be used to slip an
    out-of-range power past the validation the POST/PUT paths enforce -- an
    unvalidated version mirrors straight to top-level right_eye on finalize."""
    if value is None:
        return value
    try:
        num = float(value)
    except (ValueError, TypeError):
        raise ValueError(f"{field_name} must be a valid number, got '{value}'")
    if num == 0:  # plano / no-add -- mirror the string validator's "0" pass-through
        return value
    lo, hi = _RX_LIMITS.get(field_name, (-999, 999))
    if num < lo or num > hi:
        raise ValueError(
            f"{field_name} value {num} is outside the valid range ({lo} to {hi}). "
            f"Please double-check the prescription."
        )
    if field_name in ("sph", "cyl", "add", "cl_power", "cl_cyl", "cl_add"):
        if round(num * 100) % 25 != 0:
            raise ValueError(
                f"{field_name} value {num} must be in 0.25-diopter steps "
                f"(e.g. -1.25, 0.00, +2.50)."
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

    @field_validator("prism")
    @classmethod
    def validate_prism(cls, v: Optional[str]) -> Optional[str]:
        # BUG-117c: prism is a magnitude in prism dioptres (0-10). Blank/None ok.
        if v is None or str(v).strip() == "":
            return v
        try:
            mag = float(str(v).strip())
        except (ValueError, TypeError):
            raise ValueError("prism must be a number in prism dioptres (0-10)")
        if not (0.0 <= mag <= 10.0):
            raise ValueError("prism must be between 0 and 10 prism dioptres")
        return v

    @field_validator("base")
    @classmethod
    def validate_base(cls, v: Optional[str]) -> Optional[str]:
        # BUG-117c: prism base direction must be one of UP / DOWN / IN / OUT.
        if v is None or str(v).strip() == "":
            return v
        if str(v).strip().upper() not in ("UP", "DOWN", "IN", "OUT"):
            raise ValueError("base must be one of UP, DOWN, IN, OUT")
        return v

    @model_validator(mode="after")
    def _cyl_requires_axis(self) -> "EyeData":
        # BUG-117d: a non-zero cylinder is clinically un-grindable without an axis.
        # When cyl is present and not plano (0), axis (1-180) is mandatory.
        cyl = self.cyl
        if cyl is not None and str(cyl).strip() != "":
            try:
                cyl_num = float(str(cyl).strip())
            except (ValueError, TypeError):
                cyl_num = None
            if cyl_num is not None and abs(cyl_num) > 1e-9 and self.axis is None:
                raise ValueError("axis (1-180) is required when cylinder is non-zero")
        return self


class CLEyeData(BaseModel):
    """Per-eye contact-lens parameters. Fit by base-curve + diameter (not PD);
    cl_cyl/cl_axis present only for toric lenses, cl_add only for multifocal.
    Powers are stored as floats (the CL inventory master uses floats too)."""

    cl_power: Optional[float] = None
    cl_cyl: Optional[float] = None  # toric
    cl_axis: Optional[int] = Field(None, ge=0, le=180)  # toric (CL axis is 0-180)
    cl_add: Optional[float] = None  # multifocal
    base_curve: Optional[float] = None  # BC, mm
    diameter: Optional[float] = None  # DIA, mm
    acuity: Optional[str] = None  # visual acuity, e.g. "6/6"


class PrescriptionCreate(BaseModel):
    patient_id: str
    customer_id: str
    # rx_kind discriminates a spectacle Rx from a contact-lens Rx. Defaults to
    # SPECTACLE so every pre-existing prescription stays a spectacle Rx and all
    # existing create calls (which omit rx_kind) behave exactly as before.
    rx_kind: Literal["SPECTACLE", "CONTACT_LENS"] = "SPECTACLE"
    source: str = "TESTED_AT_STORE"  # TESTED_AT_STORE, FROM_DOCTOR
    optometrist_id: Optional[str] = None
    validity_months: Optional[int] = Field(default=None, ge=6, le=24)
    # Optional back-date: when supplied the prescription is stamped with this
    # date instead of utcnow(), and expiry_date is derived from it. Must not be
    # in the future. Omitting it (or passing null) keeps the original behaviour
    # (utcnow()). Accepts a full ISO-8601 datetime or a plain YYYY-MM-DD date.
    prescription_date: Optional[datetime] = None

    @model_validator(mode="after")
    def _set_validity_months_default(self) -> "PrescriptionCreate":
        """Apply kind-aware validity_months defaults: CONTACT_LENS -> 12 months,
        SPECTACLE -> 24 months. Only when validity_months is omitted/None; an
        explicit value (even if it matches the default) always wins."""
        if self.validity_months is None:
            self.validity_months = 12 if self.rx_kind == "CONTACT_LENS" else 24
        return self
    # Spectacle eyes default to empty EyeData so a CONTACT_LENS payload need not
    # send them; a SPECTACLE payload still validates powers as before.
    right_eye: EyeData = Field(default_factory=EyeData)
    left_eye: EyeData = Field(default_factory=EyeData)
    # ---- Contact-lens (CL) block. All optional + only used when CONTACT_LENS. ----
    cl_right: Optional[CLEyeData] = None
    cl_left: Optional[CLEyeData] = None
    cl_brand: Optional[str] = None
    cl_series: Optional[str] = None
    modality: Optional[str] = None
    color: Optional[str] = None  # for cosmetic / coloured lenses
    cl_product_id: Optional[str] = None  # bind to a CONTACT_LENS product
    lens_recommendation: Optional[str] = None
    coating_recommendation: Optional[str] = None
    # IPD (single binocular inter-pupillary distance) + next-checkup date.
    # Additive PARITY fields so a spectacle Rx created at POS carries the SAME
    # data the clinical Final-Rx captures. Optional -> existing create calls
    # (which omit them) behave exactly as before.
    ipd: Optional[str] = None
    next_checkup: Optional[str] = None
    remarks: Optional[str] = None

    @model_validator(mode="after")
    def _validate_contact_lens_block(self) -> "PrescriptionCreate":
        """BUG-117e: when rx_kind==CONTACT_LENS, require at least one of cl_right/
        cl_left, each present eye carrying cl_power + base_curve + diameter, and a
        valid modality. Spectacle Rx (default) are unaffected."""
        if self.rx_kind != "CONTACT_LENS":
            return self
        
        # At least one eye must be present.
        if self.cl_right is None and self.cl_left is None:
            raise ValueError(
                "When rx_kind=CONTACT_LENS, at least one of cl_right or cl_left "
                "must be present."
            )
        
        # Validate each present eye has required fields.
        def _check_eye_required_fields(eye: Optional[CLEyeData], label: str):
            if eye is None:
                return
            if eye.cl_power is None:
                raise ValueError(
                    f"{label}: cl_power is required for a contact-lens Rx."
                )
            if eye.base_curve is None:
                raise ValueError(
                    f"{label}: base_curve is required for a contact-lens Rx."
                )
            if eye.diameter is None:
                raise ValueError(
                    f"{label}: diameter is required for a contact-lens Rx."
                )
        
        _check_eye_required_fields(self.cl_right, "Right eye")
        _check_eye_required_fields(self.cl_left, "Left eye")
        
        # modality is strongly recommended (though optional at the model level,
        # it should be present for a complete CL Rx). The endpoint-level check
        # still allows omission for backwards-compat, but at least validate it
        # if supplied.
        if self.modality is not None and self.modality not in CL_MODALITIES:
            raise ValueError(
                f"Invalid modality. Allowed: {', '.join(CL_MODALITIES)}."
            )
        
        return self


class EyeDataEdit(BaseModel):
    """Same shape as EyeData but WITHOUT the field-level validators.

    On an edit we want a clean 400 (a deliberate business-rule rejection) for an
    out-of-range power, not Pydantic's 422 body-parse error. So the eye is
    accepted as-is here, then the handler runs the SAME `_validate_rx_value`
    ranges explicitly and raises HTTPException(400). axis stays Field-bounded
    (1-180) because that's a structural constraint, identical to EyeData.
    """

    sph: Optional[str] = None
    cyl: Optional[str] = None
    axis: Optional[int] = Field(None, ge=1, le=180)
    add: Optional[str] = None
    pd: Optional[str] = None
    prism: Optional[str] = None
    base: Optional[str] = None
    acuity: Optional[str] = None


class PrescriptionUpdate(BaseModel):
    """Editable fields of an existing prescription (clinic Edit flow).

    Every field is optional: the handler patches ONLY the keys the caller sends
    (exclude_unset), so a partial edit never blanks out fields it didn't touch.
    The eye blocks are range-checked by the SAME `_validate_rx_value` ranges
    (SPH -20..+20, CYL -6..+6, AXIS 1-180, ADD +0.75..+3.50, 0.25 steps) that
    guard create -- an out-of-range Rx can't be saved here (rejected 400).

    Identity / provenance fields (patient_id, customer_id, store_id, source,
    rx_kind, prescription_number, created_by) are intentionally NOT editable:
    editing a prescription must never silently re-assign it to another patient
    or rewrite who/where it came from. Create a new Rx for that instead.
    """

    right_eye: Optional[EyeDataEdit] = None
    left_eye: Optional[EyeDataEdit] = None
    cl_right: Optional[CLEyeData] = None
    cl_left: Optional[CLEyeData] = None
    cl_brand: Optional[str] = None
    cl_series: Optional[str] = None
    modality: Optional[str] = None
    color: Optional[str] = None
    lens_recommendation: Optional[str] = None
    coating_recommendation: Optional[str] = None
    ipd: Optional[str] = None
    next_checkup: Optional[str] = None
    remarks: Optional[str] = None
    optometrist_id: Optional[str] = None
    # Re-dating an edit: when validity_months changes we recompute expiry_date.
    validity_months: Optional[int] = Field(default=None, ge=6, le=24)


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def generate_rx_number() -> str:
    """Generate unique prescription number"""
    return f"RX-{datetime.now().strftime('%y%m%d')}-{str(uuid.uuid4())[:6].upper()}"


def _validate_cl_eye(eye_label: str, eye: Optional[CLEyeData]):
    """Validate one eye of a contact-lens Rx. Raises HTTPException(422) on a
    bad value. CL axis is 0-180 (toric); modality is checked separately at the
    top level. All fields optional -- only present values are range-checked."""
    if eye is None:
        return
    if eye.cl_axis is not None and (eye.cl_axis < 0 or eye.cl_axis > 180):
        raise HTTPException(
            status_code=422,
            detail=f"{eye_label} CL AXIS must be a whole number between 0 and 180",
        )
    for field_name, lo, hi in (
        ("cl_power", *_CL_LIMITS["cl_power"]),
        ("cl_cyl", *_CL_LIMITS["cl_cyl"]),
        ("cl_add", *_CL_LIMITS["cl_add"]),
        ("base_curve", *_CL_LIMITS["base_curve"]),
        ("diameter", *_CL_LIMITS["diameter"]),
    ):
        val = getattr(eye, field_name)
        if val is not None and (val < lo or val > hi):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{eye_label} {field_name} value {val} is outside the valid "
                    f"range ({lo} to {hi}). Please double-check the prescription."
                ),
            )


# ============================================================================
# ENDPOINTS
# ============================================================================


# NOTE: Specific routes MUST come before /{prescription_id}
@router.get("/patient/{patient_id}")
async def get_patient_prescriptions(
    patient_id: str, current_user: dict = Depends(require_rx_read)
):
    """Get all prescriptions for a patient"""
    repo = get_prescription_repository()

    if repo is not None:
        # BUG-088: only return Rx for stores the caller may access (admins see all).
        prescriptions = filter_docs_by_store(
            repo.find_by_patient(patient_id), current_user
        )
        return {"prescriptions": prescriptions, "total": len(prescriptions)}

    return {"prescriptions": [], "total": 0}


@router.get("/patient/{patient_id}/latest")
async def get_latest_prescription(
    patient_id: str, current_user: dict = Depends(require_rx_read)
):
    """Get latest prescription for a patient"""
    repo = get_prescription_repository()

    if repo is not None:
        # BUG-088: scope to the caller's stores, then take the latest in-scope Rx
        # (don't trust a limit=1 fetch that could return an out-of-scope newest).
        prescriptions = filter_docs_by_store(
            repo.find_by_patient(patient_id), current_user
        )
        if prescriptions:
            return {"prescription": prescriptions[0]}
        return {"prescription": None}

    return {"prescription": None}


@router.get("/patient/{patient_id}/valid")
async def get_valid_prescriptions(
    patient_id: str, current_user: dict = Depends(require_rx_read)
):
    """Get valid (non-expired) prescriptions for a patient"""
    repo = get_prescription_repository()

    if repo is not None:
        # BUG-088: only return Rx for stores the caller may access (admins see all).
        prescriptions = filter_docs_by_store(repo.find_valid(patient_id), current_user)
        return {"prescriptions": prescriptions, "total": len(prescriptions)}

    return {"prescriptions": [], "total": 0}


@router.get("/expiring")
async def get_expiring_prescriptions(
    days: int = Query(30, ge=7, le=90), current_user: dict = Depends(require_rx_read)
):
    """Get prescriptions expiring within specified days"""
    repo = get_prescription_repository()

    if repo is not None:
        # BUG-088: a store-level caller only sees their own stores' expiring Rx.
        prescriptions = filter_docs_by_store(
            repo.find_expiring_soon(days), current_user
        )
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
    rx_kind: Optional[Literal["SPECTACLE", "CONTACT_LENS"]] = Query(None),
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    current_user: dict = Depends(require_rx_read),
):
    """List prescriptions with filters -- the real Rx library across dates.

    This is the store-scoped, role-gated (AUTHENTICATED) list the Prescriptions
    page reads to show the whole Rx library, not just today's eye-tests. When no
    patient/customer/optometrist filter is given it scopes to the caller's active
    store (or an explicit ``store_id``) and honours an inclusive ``from_date`` /
    ``to_date`` window on prescription_date. ``rx_kind=CONTACT_LENS`` (or
    SPECTACLE) narrows by kind; a doc with no stored rx_kind is treated as
    SPECTACLE (back-compat). ``skip`` / ``limit`` paginate. ``total`` reflects
    the page (the repo applies the window before slicing)."""
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

        if rx_kind is not None:
            prescriptions = [
                p for p in prescriptions if (p.get("rx_kind") or "SPECTACLE") == rx_kind
            ]

        # BUG-088: drop any Rx outside the caller's store scope BEFORE paginating
        # (admins/area-managers keep their full scope). A non-admin passing
        # ?store_id=<other store> gets an empty page rather than a cross-store leak.
        prescriptions = filter_docs_by_store(prescriptions, current_user)

        # Pagination: the repo helpers (find_by_store / find_by_customer /
        # find_by_patient) don't take skip/limit, so apply the page window here
        # so a large Rx library doesn't ship every row to the browser at once.
        # The bare find_many({}) branch already paged at the DB; re-slicing it
        # by the same skip/limit is a no-op (idempotent), so this stays correct
        # for every branch.
        total = len(prescriptions)
        paged = prescriptions[skip : skip + limit]
        return {"prescriptions": paged, "total": total}

    return {"prescriptions": [], "total": 0}


# ---- Family Rx view helpers --------------------------------------------


def _parse_dt(value):
    """Best-effort parse of an ISO date/datetime (or passthrough) -> datetime, else None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "").split(".")[0])
    except Exception:  # noqa: BLE001
        return None


def _add_months(dt: datetime, months: int) -> datetime:
    """Add whole months to a datetime, clamping the day to the target month."""
    m = dt.month - 1 + months
    year = dt.year + m // 12
    month = m % 12 + 1
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    dim = [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return dt.replace(year=year, month=month, day=min(dt.day, dim))


def _rx_validity(rx: dict):
    """(expiry_datetime | None, is_valid | None) for a prescription using
    prescription_date / test_date + validity_months (defaults 12), tolerant of
    snake/camel fields. prescription_date is checked first so that back-dated
    prescriptions created via POST /prescriptions (which stores prescription_date)
    compute the correct expiry rather than falling back to created_at."""
    td = _parse_dt(
        rx.get("prescription_date")
        or rx.get("test_date")
        or rx.get("testDate")
        or rx.get("created_at")
        or rx.get("createdAt")
    )
    months = rx.get("validity_months") or rx.get("validityMonths") or 12
    try:
        months = int(months)
    except (TypeError, ValueError):
        months = 12
    if td is None:
        return None, None
    expiry = _add_months(td, months)
    return expiry, datetime.now() < expiry


@router.get("/family/{customer_id}")
async def family_prescriptions(
    customer_id: str, current_user: dict = Depends(require_rx_read)
):
    """Family Rx view: a customer account's prescriptions grouped by family
    member (patient), each annotated with expiry + validity. Lets POS / clinical
    see the whole household's prescriptions in one place. Patients with no Rx are
    still listed; any prescription whose patient_id isn't on the account surfaces
    under an 'Unlinked patient' group (legacy/imported data)."""
    repo = get_prescription_repository()
    customer_repo = get_customer_repository()
    if repo is None:
        return {
            "customer_id": customer_id,
            "members": [],
            "member_count": 0,
            "total_prescriptions": 0,
        }

    customer = (
        customer_repo.find_by_id(customer_id) if customer_repo is not None else None
    )
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    # BUG-062 tail: the Rx below are store-filtered, but the customer ROSTER
    # (names / relations / DOB) was returned unscoped -- a store-scoped caller
    # could read another store's family PII by id. Existence-hide a cross-store
    # customer (404). Admins/area-managers pass; null-store legacy docs are left
    # readable to avoid over-blocking unattributed accounts.
    _cust_stores = [
        customer.get(k)
        for k in ("preferred_store_id", "home_store_id", "primary_store_id", "store_id")
        if customer.get(k)
    ]
    if _cust_stores and not any(
        can_access_store_scoped(s, current_user) for s in _cust_stores
    ):
        raise HTTPException(status_code=404, detail="Customer not found")
    patients = customer.get("patients", []) or []

    # BUG-088: scope the household Rx view to stores the caller may access so a
    # single-store user can't pull another store's family clinical PII by id.
    all_rx = filter_docs_by_store(repo.find_by_customer(customer_id) or [], current_user)
    by_patient: dict = {}
    for rx in all_rx:
        by_patient.setdefault(rx.get("patient_id"), []).append(rx)

    def _enrich(rx_list):
        rows, valid_count, latest = [], 0, None
        ordered = sorted(
            rx_list,
            key=lambda r: str(
                r.get("prescription_date")
                or r.get("test_date")
                or r.get("testDate")
                or r.get("created_at")
                or ""
            ),
            reverse=True,
        )
        for rx in ordered:
            expiry, is_valid = _rx_validity(rx)
            row = dict(rx)
            row["expiry_date"] = expiry.isoformat() if expiry else None
            row["is_valid"] = bool(is_valid) if is_valid is not None else None
            rows.append(row)
            if is_valid:
                valid_count += 1
            if latest is None:
                latest = row
        return rows, valid_count, latest

    members, seen = [], set()
    for p in patients:
        pid = p.get("patient_id")
        seen.add(pid)
        rows, valid_count, latest = _enrich(by_patient.get(pid, []))
        members.append(
            {
                "patient_id": pid,
                "name": p.get("name"),
                "relation": p.get("relation"),
                "dob": p.get("dob"),
                "prescription_count": len(rows),
                "valid_count": valid_count,
                "latest": latest,
                "prescriptions": rows,
            }
        )
    for pid, rx_list in by_patient.items():
        if pid in seen:
            continue
        rows, valid_count, latest = _enrich(rx_list)
        members.append(
            {
                "patient_id": pid,
                "name": (latest or {}).get("patient_name") or "Unlinked patient",
                "relation": None,
                "dob": None,
                "prescription_count": len(rows),
                "valid_count": valid_count,
                "latest": latest,
                "prescriptions": rows,
            }
        )

    return {
        "customer_id": customer_id,
        "customer_name": customer.get("name"),
        "members": members,
        "member_count": len(members),
        "total_prescriptions": len(all_rx),
    }


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
                # SYSTEM_INTENT section 4 + the canonical RANGES table both bound
                # spectacle CYL to -6.00..+6.00. This path previously allowed
                # +/-10.00, so a high-cyl Rx could be saved here yet fail the
                # /validate check. Reconciled to +/-6.00.
                if cyl_val < -6.0 or cyl_val > 6.0:
                    raise HTTPException(
                        status_code=422,
                        detail=f"{eye_label} CYL must be between -6.00 and +6.00",
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

    if rx.rx_kind == "CONTACT_LENS":
        # Contact-lens Rx: validate CL fields (modality + per-eye BC/DIA/power/
        # toric axis) instead of spectacle powers. Spectacle eyes are ignored.
        if rx.modality and rx.modality not in CL_MODALITIES:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid modality. Allowed: {', '.join(CL_MODALITIES)}",
            )
        _validate_cl_eye("Right eye", rx.cl_right)
        _validate_cl_eye("Left eye", rx.cl_left)
    else:
        # Spectacle Rx (default): unchanged power validation.
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

        # Resolve the effective prescription date.
        # If the caller supplies prescription_date, honour it (back-dated Rx).
        # Guard: a prescription issued in the future makes no clinical sense.
        # Today (midnight) is allowed so a prescription written earlier today
        # is never rejected due to timezone rounding.
        if rx.prescription_date is not None:
            # Strip any timezone info so comparison stays naive throughout.
            supplied = rx.prescription_date.replace(tzinfo=None)
            # A date in the future is rejected.  "Today" is allowed: a
            # prescription written earlier this morning must not be blocked by
            # a sub-second clock skew, so we compare against end-of-today.
            end_of_today = datetime.now().replace(
                hour=23, minute=59, second=59, microsecond=999999
            )
            if supplied > end_of_today:
                raise HTTPException(
                    status_code=400,
                    detail="prescription_date cannot be in the future",
                )
            prescription_date = supplied
        else:
            prescription_date = datetime.now()

        expiry_date = _add_months(prescription_date, rx.validity_months)

        rx_data = {
            "prescription_number": generate_rx_number(),
            "patient_id": rx.patient_id,
            "customer_id": rx.customer_id,
            "rx_kind": rx.rx_kind,
            "store_id": current_user.get("active_store_id"),
            "source": rx.source,
            "optometrist_id": rx.optometrist_id,
            "prescription_date": prescription_date.isoformat(),
            # test_date mirrors prescription_date so legacy readers (clinical
            # report queries, _rx_validity, family-view sort) that look for
            # test_date before prescription_date still get the correct date.
            "test_date": prescription_date.isoformat(),
            "expiry_date": expiry_date.isoformat(),
            "validity_months": rx.validity_months,
            # Spectacle eyes are always persisted (empty for a CL Rx) so any
            # reader expecting right_eye/left_eye keys never KeyErrors.
            "right_eye": rx.right_eye.model_dump(),
            "left_eye": rx.left_eye.model_dump(),
            "lens_recommendation": rx.lens_recommendation,
            "coating_recommendation": rx.coating_recommendation,
            "ipd": rx.ipd,
            "next_checkup": rx.next_checkup,
            "remarks": rx.remarks,
            "created_by": current_user.get("user_id"),
        }

        # Persist the CL block only for a contact-lens Rx so spectacle docs
        # stay byte-for-byte identical to before.
        if rx.rx_kind == "CONTACT_LENS":
            rx_data.update(
                {
                    "cl_right": rx.cl_right.model_dump() if rx.cl_right else None,
                    "cl_left": rx.cl_left.model_dump() if rx.cl_left else None,
                    "cl_brand": rx.cl_brand,
                    "cl_series": rx.cl_series,
                    "modality": rx.modality,
                    "color": rx.color,
                    "cl_product_id": rx.cl_product_id,
                }
            )

        created = repo.create(rx_data)
        if created:
            _audit_rx(
                "PRESCRIPTION_CREATED",
                created.get("prescription_id"),
                current_user,
                customer_id=rx.customer_id,
                rx_kind=rx.rx_kind,
                after_state={
                    "prescription_number": created.get("prescription_number"),
                    "patient_id": rx.patient_id,
                    "source": rx.source,
                    "optometrist_id": rx.optometrist_id,
                },
            )
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


@router.put("/{prescription_id}")
async def update_prescription(
    prescription_id: str,
    rx: PrescriptionUpdate,
    current_user: dict = Depends(get_current_user),
):
    """Edit an existing prescription's mutable Rx fields (clinic Edit flow).

    Gated identically to create_prescription (OPTOMETRIST / STORE_MANAGER /
    ADMIN / SUPERADMIN). Re-runs the canonical Rx-range validation
    (`_validate_rx_value`: SPH -20..+20, CYL -6..+6, ADD +0.75..+3.50 in 0.25
    steps; AXIS 1-180) so an edit can never persist an invalid Rx. Only the keys
    the caller sends are written (PATCH-style merge), so a partial edit never
    blanks fields it didn't touch. Identity/provenance fields are immutable.
    """
    # --- Role gate (same set create_prescription uses) ---
    _require_rx_write_roles(current_user)

    repo = get_prescription_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database not available")

    existing = repo.find_by_id(prescription_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Prescription not found")
    # Store-scope the WRITE like the reads (BUG-088): an optometrist in one
    # store must not be able to edit another store's Rx (404-hide).
    _store_scope_or_404(existing, current_user)

    # Only the explicitly-supplied keys are touched (PATCH-style merge).
    body = rx.model_dump(exclude_unset=True)
    if not body:
        raise HTTPException(status_code=400, detail="No fields to update")

    # Re-validate spectacle powers against the canonical clinical ranges. We
    # call the SAME `_validate_rx_value` the create path / EyeData validators
    # use, but surface a 400 (deliberate business rejection) instead of 422.
    def _validate_eye(eye_label: str, eye: dict):
        if not isinstance(eye, dict):
            return
        try:
            _validate_rx_value(eye.get("sph"), "sph")
            _validate_rx_value(eye.get("cyl"), "cyl")
            _validate_rx_value(eye.get("add"), "add")
            _validate_rx_value(eye.get("pd"), "pd")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"{eye_label}: {exc}")
        axis = eye.get("axis")
        if axis is not None and (not isinstance(axis, int) or axis < 1 or axis > 180):
            raise HTTPException(
                status_code=400,
                detail=f"{eye_label} AXIS must be a whole number between 1 and 180",
            )

    if "right_eye" in body:
        _validate_eye("Right eye", body["right_eye"])
    if "left_eye" in body:
        _validate_eye("Left eye", body["left_eye"])

    # Contact-lens block (only when the edit touches CL fields).
    if body.get("modality") and body["modality"] not in CL_MODALITIES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid modality. Allowed: {', '.join(CL_MODALITIES)}",
        )
    if "cl_right" in body:
        _validate_cl_eye("Right eye", rx.cl_right)
    if "cl_left" in body:
        _validate_cl_eye("Left eye", rx.cl_left)

    # If validity changed, recompute expiry off the original test/created date
    # so the edit stays internally consistent (expiry = test_date + N months).
    update_doc = dict(body)
    if rx.validity_months is not None:
        base_dt = (
            _parse_dt(
                existing.get("prescription_date")
                or existing.get("test_date")
                or existing.get("created_at")
            )
            or datetime.now()
        )
        update_doc["expiry_date"] = _add_months(base_dt, rx.validity_months).isoformat()

    update_doc["updated_by"] = current_user.get("user_id")
    update_doc["updated_at"] = datetime.now().isoformat()

    repo.update(prescription_id, update_doc)
    refreshed = repo.find_by_id(prescription_id) or {**existing, **update_doc}
    _audit_rx(
        "PRESCRIPTION_UPDATED",
        prescription_id,
        current_user,
        customer_id=existing.get("customer_id"),
        rx_kind=existing.get("rx_kind"),
        detail={"fields": sorted(body.keys())},
    )
    return {
        "prescription_id": prescription_id,
        "message": "Prescription updated",
        "prescription": refreshed,
    }


@router.get("/{prescription_id}")
async def get_prescription(
    prescription_id: str, current_user: dict = Depends(require_rx_read)
):
    """Get prescription by ID"""
    repo = get_prescription_repository()

    if repo is not None:
        prescription = repo.find_by_id(prescription_id)
        if prescription is not None:
            # BUG-088: a prescription is clinical PII. Only a caller scoped to the
            # Rx's store (admins are cross-store) may read it. Return 404 -- not
            # 403 -- so we don't even confirm the Rx exists to an out-of-scope user.
            if not can_access_store_scoped(
                prescription.get("store_id"), current_user
            ):
                raise HTTPException(status_code=404, detail="Prescription not found")
            return prescription
        raise HTTPException(status_code=404, detail="Prescription not found")

    return {"prescription_id": prescription_id}


@router.get("/{prescription_id}/validate")
async def validate_prescription(
    prescription_id: str, current_user: dict = Depends(require_rx_read)
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
    # BUG-088: store-scope the clinical Rx read (404 hides existence cross-store).
    if rx is None or not can_access_store_scoped(rx.get("store_id"), current_user):
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
            if axis is not None and axis != "":
                axis_f = float(axis)
                # AXIS is a WHOLE degree 1..180. The old check used
                # int(float(axis)), which truncated 90.5 -> 90 and let a
                # non-integer axis pass silently. Flag both out-of-range AND
                # fractional values, matching what create / PUT enforce.
                if not (1 <= axis_f <= 180):
                    issues.append(f"{eye_label} AXIS {axis} out of range (1-180)")
                elif axis_f != int(axis_f):
                    issues.append(
                        f"{eye_label} AXIS {axis} must be a whole number (1-180)"
                    )
            if (
                add not in (None, "", 0)
                and float(add) != 0
                and not (0.75 <= float(add) <= 3.50)
            ):
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


_PRINT_STYLE = """
                body { font-family: Arial, sans-serif; padding: 20px; }
                .header { text-align: center; margin-bottom: 20px; }
                .rx-number { font-size: 14px; color: #666; }
                table { width: 100%; border-collapse: collapse; margin: 20px 0; }
                th, td { border: 1px solid #ddd; padding: 8px; text-align: center; }
                th { background: #f5f5f5; }
                .footer { margin-top: 40px; }
"""


def _cell(value) -> str:
    """Render a stored Rx cell value, falling back to '-' for None/empty."""
    if value is None or value == "":
        return "-"
    return str(value)


def _build_spectacle_print_html(prescription: dict) -> str:
    """Existing spectacle Rx card (unchanged output)."""
    right = prescription.get("right_eye", {}) or {}
    left = prescription.get("left_eye", {}) or {}
    return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Prescription {prescription.get('prescription_number')}</title>
            <style>{_PRINT_STYLE}</style>
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
                    <td>{_cell(right.get('sph'))}</td>
                    <td>{_cell(right.get('cyl'))}</td>
                    <td>{_cell(right.get('axis'))}</td>
                    <td>{_cell(right.get('add'))}</td>
                    <td>{_cell(right.get('pd'))}</td>
                </tr>
                <tr>
                    <td><strong>LE</strong></td>
                    <td>{_cell(left.get('sph'))}</td>
                    <td>{_cell(left.get('cyl'))}</td>
                    <td>{_cell(left.get('axis'))}</td>
                    <td>{_cell(left.get('add'))}</td>
                    <td>{_cell(left.get('pd'))}</td>
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


def _build_cl_print_html(prescription: dict) -> str:
    """Contact-lens Rx card: brand/series/modality header + per-eye
    power/CYL/AXIS/ADD/BC/DIA (no PD -- a CL is fit by base-curve+diameter)."""
    right = prescription.get("cl_right") or {}
    left = prescription.get("cl_left") or {}
    brand = prescription.get("cl_brand") or "-"
    series = prescription.get("cl_series") or "-"
    modality = prescription.get("modality") or "-"
    color = prescription.get("color")
    color_row = f"<p><strong>Color:</strong> {color}</p>" if color else ""
    return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Contact Lens Prescription {prescription.get('prescription_number')}</title>
            <style>{_PRINT_STYLE}</style>
        </head>
        <body>
            <div class="header">
                <h2>Contact Lens Prescription</h2>
                <p class="rx-number">{prescription.get('prescription_number')}</p>
                <p>Date: {prescription.get('prescription_date', '')[:10]}</p>
            </div>
            <p><strong>Brand:</strong> {brand} &nbsp; <strong>Series:</strong> {series}
               &nbsp; <strong>Modality:</strong> {modality}</p>
            {color_row}
            <table>
                <tr>
                    <th></th><th>POWER</th><th>CYL</th><th>AXIS</th><th>ADD</th>
                    <th>BC</th><th>DIA</th>
                </tr>
                <tr>
                    <td><strong>RE</strong></td>
                    <td>{_cell(right.get('cl_power'))}</td>
                    <td>{_cell(right.get('cl_cyl'))}</td>
                    <td>{_cell(right.get('cl_axis'))}</td>
                    <td>{_cell(right.get('cl_add'))}</td>
                    <td>{_cell(right.get('base_curve'))}</td>
                    <td>{_cell(right.get('diameter'))}</td>
                </tr>
                <tr>
                    <td><strong>LE</strong></td>
                    <td>{_cell(left.get('cl_power'))}</td>
                    <td>{_cell(left.get('cl_cyl'))}</td>
                    <td>{_cell(left.get('cl_axis'))}</td>
                    <td>{_cell(left.get('cl_add'))}</td>
                    <td>{_cell(left.get('base_curve'))}</td>
                    <td>{_cell(left.get('diameter'))}</td>
                </tr>
            </table>
            <p><strong>Remarks:</strong> {prescription.get('remarks', '-')}</p>
            <div class="footer">
                <p>Valid until: {prescription.get('expiry_date', '')[:10]}</p>
            </div>
        </body>
        </html>
        """


@router.get("/{prescription_id}/print")
async def print_prescription(
    prescription_id: str, current_user: dict = Depends(require_rx_read)
):
    """Generate printable prescription HTML. Renders a contact-lens card when
    the Rx is rx_kind=CONTACT_LENS, else the existing spectacle card."""
    repo = get_prescription_repository()

    if repo is not None:
        prescription = repo.find_by_id(prescription_id)
        # BUG-088: store-scope the clinical Rx read (404 hides existence cross-store).
        if prescription is None or not can_access_store_scoped(
            prescription.get("store_id"), current_user
        ):
            raise HTTPException(status_code=404, detail="Prescription not found")

        if (prescription.get("rx_kind") or "SPECTACLE") == "CONTACT_LENS":
            html = _build_cl_print_html(prescription)
        else:
            html = _build_spectacle_print_html(prescription)

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
    axis: Optional[int] = Field(None, ge=1, le=180)
    addition: Optional[float] = None
    va: Optional[str] = None

    # Close the version-PATCH validation bypass (audit P2): range + 0.25-grid
    # check sphere/cylinder/addition exactly like the POST/PUT paths, so a value
    # like SPH +99 can't be saved into a version and mirrored to top-level on
    # finalize. axis is bounded structurally above (ge=1, le=180).
    @field_validator("sphere")
    @classmethod
    def _v_sphere(cls, v):
        return _validate_rx_number(v, "sph")

    @field_validator("cylinder")
    @classmethod
    def _v_cylinder(cls, v):
        return _validate_rx_number(v, "cyl")

    @field_validator("addition")
    @classmethod
    def _v_addition(cls, v):
        return _validate_rx_number(v, "add")


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
    while status='in_progress'. Use POST /finalize to lock the record.

    Gated identically to PUT /{prescription_id} (audit P1): this path writes
    clinical Rx data (and the `final` slot is mirrored to top-level on
    finalize), so it must never be open to non-clinical roles or cross-store
    callers. It previously had NO role gate at all -- any authenticated
    cashier could overwrite Rx versions chain-wide.
    """
    from ..services.prescription_versions import (
        VALID_VERSION_NAMES,
        merge_version,
        backfill_versions_from_top_level,
    )

    # --- Role gate (same set update_prescription / create_prescription use) ---
    _require_rx_write_roles(current_user)

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
    # Store-scope the WRITE like the reads (BUG-088): 404-hide cross-store.
    _store_scope_or_404(doc, current_user)

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
    # Store-scope the WRITE like the reads (BUG-088): 404-hide cross-store
    # BEFORE the 409/400 checks so status is never leaked out-of-scope.
    _store_scope_or_404(doc, current_user)
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
    current_user: dict = Depends(require_rx_read),
):
    """Return all 4 versions for a prescription. Auto-backfills for
    legacy single-Rx docs."""
    from ..services.prescription_versions import backfill_versions_from_top_level

    repo = get_prescription_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database not available")
    doc = repo.find_by_id(prescription_id)
    # BUG-088: store-scope the clinical Rx read (404 hides existence cross-store).
    if doc is None or not can_access_store_scoped(doc.get("store_id"), current_user):
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
    current_user: dict = Depends(require_rx_read),
):
    """Adjacent-visit deltas for a customer's FINAL Rx history. Useful
    for the clinical dashboard's progression chart ('is this myopia
    accelerating?')."""
    from ..services.prescription_versions import progression_diffs

    repo = get_prescription_repository()
    if repo is None:
        return {"customer_id": customer_id, "deltas": []}
    # BUG-088: scope the progression history to the caller's stores.
    history = filter_docs_by_store(
        repo.find_many({"customer_id": customer_id}) or [], current_user
    )
    deltas = progression_diffs(history)
    return {"customer_id": customer_id, "deltas": deltas, "visits": len(history)}

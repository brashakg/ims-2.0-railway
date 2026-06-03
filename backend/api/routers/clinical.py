"""
IMS 2.0 - Clinical Router
==========================
Eye test queue and clinical management endpoints with database persistence
"""

from fastapi import APIRouter, HTTPException, Depends, Query, Path
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import List, Optional
from datetime import datetime, date
from html import escape as _html_escape
import uuid
from .auth import get_current_user, require_roles
from ..dependencies import (
    get_db,
    get_eye_test_queue_repository,
    get_eye_test_repository,
    get_prescription_repository,
    get_customer_repository,
    get_store_repository,
    get_audit_repository,
)
from ..services import clinical_abuse as _abuse

router = APIRouter()


def _audit_clinical(
    action: str,
    entity_id: Optional[str],
    current_user: dict,
    *,
    store_id: Optional[str] = None,
    detail: Optional[dict] = None,
) -> None:
    """Best-effort domain audit for a clinical/eye-test action -> append-only
    audit_logs (source="domain"). Eye-test completions (which auto-mint a
    prescription) were invisible in the Activity Log; this records who recorded
    which exam. FAIL-SOFT: any audit failure is swallowed so it can never undo
    or 500 the clinical write. ``timestamp`` is stamped explicitly (the Activity
    Log sorts + range-filters on it)."""
    try:
        audit_repo = get_audit_repository()
        if audit_repo is None:
            return
        audit_repo.create(
            {
                "action": action,
                "entity_type": "CLINICAL",
                "entity_id": entity_id,
                "store_id": store_id or current_user.get("active_store_id"),
                "user_id": current_user.get("user_id"),
                "user_name": current_user.get("full_name") or current_user.get("username"),
                "timestamp": datetime.utcnow(),
                "severity": "INFO",
                "source": "domain",
                "detail": detail or {},
            }
        )
    except Exception:  # noqa: BLE001 - audit must never break the clinical write
        pass

# Roles permitted to mutate the optometry queue + eye-test records. Mirrors the
# frontend Clinical route guard. SUPERADMIN auto-passes via require_roles.
_CLINICAL_ROLES = ("ADMIN", "STORE_MANAGER", "OPTOMETRIST")

# Roles permitted to record a redo on a prescription. Wider than the queue
# mutators on purpose: an Area Manager auditing a botched dispense should be
# able to flag a redo. SUPERADMIN auto-passes via require_roles.
_REDO_ROLES = ("OPTOMETRIST", "STORE_MANAGER", "AREA_MANAGER", "ADMIN")

# Roles permitted to see the clinical abuse-detection (fraud-control) view.
# Management only -- the optometrists being measured must NOT see their own
# scorecard. SUPERADMIN auto-passes via require_roles.
_ABUSE_VIEW_ROLES = ("STORE_MANAGER", "AREA_MANAGER", "ADMIN")

# Canonical queue lifecycle states. Mirrors EyeTestQueueRepository.update_status'
# allow-list so the router can reject an invalid status with a clean 400 BEFORE
# the repo silently no-ops (which used to surface as a misleading 200 "updated").
_VALID_QUEUE_STATUSES = ("WAITING", "IN_PROGRESS", "COMPLETED", "CANCELLED", "NO_SHOW")


# ============================================================================
# SCHEMAS
# ============================================================================


class QueueItemCreate(BaseModel):
    store_id: str = Field(..., alias="storeId")
    patient_name: str = Field(..., alias="patientName")
    customer_phone: str = Field(..., alias="customerPhone")
    age: Optional[int] = None
    reason: Optional[str] = None
    customer_id: Optional[str] = Field(None, alias="customerId")
    patient_id: Optional[str] = Field(None, alias="patientId")

    model_config = ConfigDict(populate_by_name=True)


class ClinicalFindings(BaseModel):
    """C6-B: the rest of an optometric exam record beyond refraction.

    Every field is OPTIONAL -- a quick refraction-only test sends none of this
    and behaves exactly as before; a full exam can now persist the clinical
    context that was previously dropped (VA, IOP, history, diagnosis, extra
    findings). Stored on the test record under `clinical_findings`; never blocks
    a completion. Strings are kept free-text on purpose (no premature enum) so
    the optometrist isn't fought by validation mid-exam.
    """

    # Visual acuity (e.g. "6/6", "6/9 N6"), unaided + aided, per eye + binocular.
    va_right_unaided: Optional[str] = Field(None, alias="vaRightUnaided")
    va_left_unaided: Optional[str] = Field(None, alias="vaLeftUnaided")
    va_right_aided: Optional[str] = Field(None, alias="vaRightAided")
    va_left_aided: Optional[str] = Field(None, alias="vaLeftAided")
    va_binocular: Optional[str] = Field(None, alias="vaBinocular")
    # Intra-ocular pressure (tonometry), mmHg, per eye. Bounded to a sane clinical
    # window so a fat-finger ("220") is rejected but a real reading (10-30) passes.
    iop_right: Optional[float] = Field(None, ge=0, le=80, alias="iopRight")
    iop_left: Optional[float] = Field(None, ge=0, le=80, alias="iopLeft")
    # History / presenting problem + structured-ish findings.
    chief_complaint: Optional[str] = Field(None, alias="chiefComplaint")
    history: Optional[str] = None
    diagnosis: Optional[str] = None
    colour_vision: Optional[str] = Field(None, alias="colourVision")  # e.g. "Normal", "Ishihara 14/14"
    cover_test: Optional[str] = Field(None, alias="coverTest")
    dominant_eye: Optional[str] = Field(None, alias="dominantEye")  # "RIGHT"/"LEFT"
    additional_notes: Optional[str] = Field(None, alias="additionalNotes")

    @field_validator("dominant_eye", mode="after")
    @classmethod
    def _v_dominant(cls, v):
        if v is None or v == "":
            return None
        up = str(v).strip().upper()
        if up not in ("RIGHT", "LEFT", "R", "L"):
            raise ValueError("dominant_eye must be RIGHT or LEFT")
        return "RIGHT" if up in ("RIGHT", "R") else "LEFT"

    model_config = ConfigDict(populate_by_name=True)


class EyeTestData(BaseModel):
    right_eye: dict = Field(..., alias="rightEye")
    left_eye: dict = Field(..., alias="leftEye")
    pd: Optional[float] = Field(None, ge=0, le=120)
    # IPD + next-checkup so the clinical Final-Rx mirror writes the SAME parity
    # fields a POS-created prescription does. Kept as str -> no float-coercion
    # 422 on an empty value.
    ipd: Optional[str] = None
    next_checkup: Optional[str] = Field(None, alias="nextCheckup")
    notes: Optional[str] = None
    lens_recommendation: Optional[str] = Field(None, alias="lensRecommendation")
    coating_recommendation: Optional[str] = Field(None, alias="coatingRecommendation")
    # C6-B: optional full-exam findings (VA / IOP / history / diagnosis / ...).
    # Absent -> the test stays a refraction-only record exactly as before.
    clinical_findings: Optional[ClinicalFindings] = Field(
        None, alias="clinicalFindings"
    )

    model_config = ConfigDict(populate_by_name=True)


class StatusUpdate(BaseModel):
    status: str


class RedoCreate(BaseModel):
    reason: str = Field(..., min_length=1)


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def format_rx_value(v) -> str:
    """Render an Rx power for display/print.

    Rules (Rx business rules, IMS 2.0):
      - None / missing / empty -> "" (blank cell)
      - exactly 0 (any numeric form) -> "Plano"
      - otherwise -> explicit sign + 2 decimals, e.g. "-1.25", "+0.50"

    PURE function (no I/O) so it can be unit-tested in isolation. Accepts ints,
    floats, or numeric strings (the prescriptions collection stores eye powers
    as strings, e.g. {"sph": "-1.25"}). Non-numeric junk -> "" rather than a
    crash, keeping the print endpoint fail-soft.
    """
    if v is None:
        return ""
    if isinstance(v, str):
        s = v.strip()
        if s == "":
            return ""
        try:
            num = float(s)
        except ValueError:
            return ""
    else:
        try:
            num = float(v)
        except (TypeError, ValueError):
            return ""
    # Normalise -0.0 and tiny float noise to a clean zero -> "Plano".
    if abs(num) < 0.005:
        return "Plano"
    sign = "+" if num > 0 else "-"
    return f"{sign}{abs(num):.2f}"


def format_axis_value(v) -> str:
    """Render an AXIS (1-180 whole degrees). Blank for None/missing, else int."""
    if v is None:
        return ""
    if isinstance(v, str):
        s = v.strip()
        if s == "":
            return ""
        try:
            return str(int(round(float(s))))
        except ValueError:
            return ""
    try:
        return str(int(round(float(v))))
    except (TypeError, ValueError):
        return ""


def _validate_eye_test_rx(eye_label: str, eye: dict) -> None:
    """Validate the Rx powers captured on an eye-test eye dict against the
    canonical clinical ranges (SPH -20..+20, CYL -6..+6, AXIS 1-180 whole,
    ADD +0.75..+3.50, all dioptric powers on the 0.25-diopter grid).

    Reuses the SINGLE source-of-truth validator in prescriptions.py so the
    eye-test capture path -- which auto-creates a prescription on completion --
    can never persist an Rx the prescriptions endpoint would reject. Raises
    HTTPException(422) on a violation. None / empty / "0" values are tolerated
    (a blank cell is valid) exactly as the prescription validator does.

    `eye` carries the frontend's loose shape: sphere/sph, cylinder/cyl, axis,
    add. We normalise the alias pairs before checking.
    """
    from .prescriptions import _validate_rx_value

    if not isinstance(eye, dict):
        return

    def _as_str(v):
        # The shared validator takes Optional[str]; numbers stringify cleanly,
        # None passes straight through (treated as "no value").
        if v is None:
            return None
        return str(v)

    pairs = (
        ("sph", eye.get("sphere", eye.get("sph"))),
        ("cyl", eye.get("cylinder", eye.get("cyl"))),
        ("add", eye.get("add", eye.get("addition"))),
    )
    for field_name, raw in pairs:
        try:
            _validate_rx_value(_as_str(raw), field_name)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"{eye_label} {exc}")

    # AXIS is a whole number 1..180. Tolerate None / "" (no value).
    axis = eye.get("axis")
    if axis is not None and str(axis).strip() != "":
        try:
            axis_int = int(round(float(axis)))
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=422,
                detail=f"{eye_label} AXIS must be a whole number between 1 and 180",
            )
        if axis_int < 1 or axis_int > 180:
            raise HTTPException(
                status_code=422,
                detail=f"{eye_label} AXIS must be a whole number between 1 and 180",
            )


def _to_camel_case(snake_str: str) -> str:
    """Convert snake_case to camelCase"""
    components = snake_str.split("_")
    return components[0] + "".join(x.title() for x in components[1:])


def _convert_to_camel(data: dict) -> dict:
    """Convert all keys in dict from snake_case to camelCase"""
    if data is None:
        return data
    result = {}
    for key, value in data.items():
        if key.startswith("_"):
            continue
        camel_key = _to_camel_case(key)
        if isinstance(value, dict):
            result[camel_key] = _convert_to_camel(value)
        elif isinstance(value, list):
            result[camel_key] = [
                _convert_to_camel(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            result[camel_key] = value
    return result


def _get_empty_queue() -> List[dict]:
    """Return empty queue when database not available"""
    return []


def _get_empty_tests() -> List[dict]:
    """Return empty tests when database not available"""
    return []


# ============================================================================
# QUEUE ENDPOINTS
# ============================================================================


@router.get("")
@router.get("/")
async def get_clinical_root():
    """Root endpoint for clinical/eye test queue"""
    return {
        "module": "clinical",
        "status": "active",
        "message": "clinical queue endpoint ready",
    }


@router.get("/queue")
async def get_queue(
    store_id: str = Query(..., alias="store_id"),
    current_user: dict = Depends(get_current_user),
):
    """Get eye test queue for a store"""
    queue_repo = get_eye_test_queue_repository()

    if queue_repo is not None:
        queue_items = queue_repo.get_store_queue(store_id)
        # Convert to camelCase and add 'id' alias
        result = []
        for item in queue_items:
            converted = _convert_to_camel(item)
            converted["id"] = item.get("queue_id")
            result.append(converted)
        return {"queue": result}

    # Return empty queue when no DB available
    return {"queue": _get_empty_queue()}


@router.post("/queue")
async def add_to_queue(
    item: QueueItemCreate,
    current_user: dict = Depends(require_roles(*_CLINICAL_ROLES)),
):
    """Add a patient to the eye test queue"""
    queue_repo = get_eye_test_queue_repository()

    if queue_repo is not None:
        created = queue_repo.add_to_queue(
            store_id=item.store_id,
            patient_name=item.patient_name,
            customer_phone=item.customer_phone,
            age=item.age,
            reason=item.reason,
            customer_id=item.customer_id,
            patient_id=item.patient_id,
        )
        if created:
            result = _convert_to_camel(created)
            result["id"] = created.get("queue_id")
            return result
        raise HTTPException(status_code=500, detail="Failed to add to queue")

    # Fallback for demo
    new_item = {
        "id": str(uuid.uuid4()),
        "queueId": str(uuid.uuid4()),
        "tokenNumber": "T001",
        "patientName": item.patient_name,
        "customerPhone": item.customer_phone,
        "age": item.age,
        "reason": item.reason,
        "customerId": item.customer_id,
        "patientId": item.patient_id,
        "status": "WAITING",
        "createdAt": datetime.now().isoformat(),
        "waitTime": 0,
    }
    return new_item


@router.patch("/queue/{queue_id}/status")
async def update_queue_status(
    queue_id: str,
    body: StatusUpdate,
    current_user: dict = Depends(require_roles(*_CLINICAL_ROLES)),
):
    """Update queue item status.

    Validates the requested status against the canonical lifecycle states up
    front. The repository silently no-ops on an unknown status, so the previous
    handler returned a misleading 200 "Status updated" for garbage like
    ``{"status": "BANANA"}`` -- a caller could believe a state change happened
    that never did. We now reject an unknown status with 400.
    """
    status = (body.status or "").strip().upper()
    if status not in _VALID_QUEUE_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid queue status '{body.status}'. "
                f"Allowed: {', '.join(_VALID_QUEUE_STATUSES)}"
            ),
        )

    queue_repo = get_eye_test_queue_repository()

    if queue_repo is not None:
        # The item may legitimately be absent (sample/demo data); the repo
        # no-ops in that case. We don't 404 -- the frontend treats this as a
        # best-effort state sync -- but we DO echo the normalised status.
        queue_repo.update_status(queue_id, status)

    return {"message": "Status updated", "status": status}


@router.delete("/queue/{queue_id}")
async def remove_from_queue(
    queue_id: str, current_user: dict = Depends(require_roles(*_CLINICAL_ROLES))
):
    """Remove a patient from the queue"""
    queue_repo = get_eye_test_queue_repository()

    if queue_repo is not None:
        queue_repo.remove_from_queue(queue_id)

    return {"message": "Removed from queue"}


@router.post("/queue/{queue_id}/start-test")
async def start_test(
    queue_id: str, current_user: dict = Depends(require_roles(*_CLINICAL_ROLES))
):
    """Start an eye test for a queue item"""
    queue_repo = get_eye_test_queue_repository()
    test_repo = get_eye_test_repository()

    if queue_repo is not None and test_repo is not None:
        # Get queue item
        queue_item = queue_repo.find_by_id(queue_id)

        if queue_item:
            # Update queue status
            queue_repo.update_status(queue_id, "IN_PROGRESS")

            # Create test record
            test = test_repo.create_test(
                queue_id=queue_id,
                patient_name=queue_item.get("patient_name", ""),
                customer_phone=queue_item.get("customer_phone", ""),
                store_id=queue_item.get("store_id", ""),
                optometrist_id=current_user.get("user_id", ""),
                optometrist_name=current_user.get("full_name", "Unknown"),
                customer_id=queue_item.get("customer_id"),
                patient_id=queue_item.get("patient_id"),
            )

            if test:
                # Stamp the test_id back onto the queue doc so a page
                # reload mid-test still lets "Continue" resolve the
                # right test record. Without this stamp the frontend
                # fell back to queue_id-as-test_id on Continue, the
                # completion call no-op'd (find_by_id on a queue_id
                # returns nothing), and the queue stayed IN_PROGRESS
                # forever.
                queue_repo.update(queue_id, {"test_id": test.get("test_id")})
                return {"testId": test.get("test_id"), "message": "Test started"}

        # Queue item may be from sample data, create test anyway
        test_id = str(uuid.uuid4())
        return {"testId": test_id, "message": "Test started"}

    # Fallback for demo
    test_id = str(uuid.uuid4())
    return {"testId": test_id, "message": "Test started"}


@router.get("/queue/stats")
async def get_queue_stats(
    store_id: str = Query(..., alias="store_id"),
    current_user: dict = Depends(get_current_user),
):
    """Get queue statistics for today"""
    queue_repo = get_eye_test_queue_repository()

    if queue_repo is not None:
        return queue_repo.get_today_stats(store_id)

    # Return zeros when no DB available
    return {"total": 0, "waiting": 0, "in_progress": 0, "completed": 0, "no_show": 0}


# ============================================================================
# TEST ENDPOINTS
# ============================================================================


@router.get("/tests")
async def get_tests(
    store_id: str = Query(..., alias="store_id"),
    date: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get eye tests for a store"""
    test_repo = get_eye_test_repository()

    if test_repo is not None:
        if date == "today":
            tests = test_repo.get_today_completed_tests(store_id)
        else:
            tests = test_repo.get_store_tests(store_id)

        result = []
        for test in tests:
            converted = _convert_to_camel(test)
            converted["id"] = test.get("test_id")
            result.append(converted)
        return {"tests": result}

    # Return empty tests when no DB available
    return {"tests": _get_empty_tests()}


@router.get("/tests/{test_id}")
async def get_test(test_id: str, current_user: dict = Depends(get_current_user)):
    """Get a specific eye test"""
    test_repo = get_eye_test_repository()

    if test_repo is not None:
        test = test_repo.find_by_id(test_id)
        if test:
            result = _convert_to_camel(test)
            result["id"] = test.get("test_id")
            return result
        raise HTTPException(status_code=404, detail="Test not found")

    raise HTTPException(status_code=404, detail="Test not found")


@router.post("/tests/{test_id}/complete")
async def complete_test(
    test_id: str,
    data: EyeTestData,
    current_user: dict = Depends(require_roles(*_CLINICAL_ROLES)),
):
    """Complete an eye test with prescription data"""
    # Validate the captured Rx powers against the canonical clinical ranges
    # BEFORE anything is persisted. This path auto-creates a prescription on
    # success, so an out-of-range SPH/CYL/AXIS/ADD here would otherwise be
    # saved into an Rx the prescriptions endpoint would reject -- closing that
    # write-path gap. Raises 422 on a violation.
    _validate_eye_test_rx("Right eye", data.right_eye)
    _validate_eye_test_rx("Left eye", data.left_eye)

    test_repo = get_eye_test_repository()
    queue_repo = get_eye_test_queue_repository()

    if test_repo is not None:
        # Look the test up FIRST so completion is idempotent + ordered:
        #   * unknown test_id  -> 404 (don't silently mint an orphan Rx)
        #   * already COMPLETED -> return the EXISTING prescription, do NOT
        #     write a second one. The previous code blind-updated and re-created
        #     a prescription on every call, so a double-click / retry / page
        #     reload produced duplicate Rx rows for one exam.
        existing_test = test_repo.find_by_id(test_id)
        if existing_test is None:
            raise HTTPException(status_code=404, detail="Test not found")

        rx_repo = get_prescription_repository()

        if existing_test.get("status") == "COMPLETED":
            existing_rx = (
                rx_repo.find_by_eye_test(test_id) if rx_repo is not None else None
            )
            return {
                "message": "Test already completed",
                "testId": test_id,
                "prescriptionId": (
                    existing_rx.get("prescription_id") if existing_rx else None
                ),
                "alreadyCompleted": True,
            }

        # Update test record. C6-B: persist the optional full-exam findings
        # (VA / IOP / history / diagnosis / ...) so a complete optometric exam
        # is recorded, not just the refraction. by_alias=False keeps the stored
        # keys snake_case (consistent with the rest of the test doc).
        success = test_repo.complete_test(
            test_id=test_id,
            right_eye=data.right_eye,
            left_eye=data.left_eye,
            pd=data.pd,
            notes=data.notes,
            lens_recommendation=data.lens_recommendation,
            coating_recommendation=data.coating_recommendation,
            clinical_findings=(
                data.clinical_findings.model_dump(exclude_none=True)
                if data.clinical_findings
                else None
            ),
        )

        if success:
            # Get the test to find queue_id and patient info
            test = test_repo.find_by_id(test_id)
            if test and queue_repo:
                queue_id = test.get("queue_id")
                if queue_id:
                    queue_repo.update_status(queue_id, "COMPLETED")

            # ── Auto-create prescription so POS can find it ──
            prescription_id = None
            # Idempotency belt-and-braces: even if the test row's status didn't
            # flip COMPLETED for some reason (or a concurrent request raced us),
            # never create a duplicate Rx for an exam that already has one.
            already_rx = (
                rx_repo.find_by_eye_test(test_id)
                if (rx_repo is not None and test)
                else None
            )
            if already_rx:
                return {
                    "message": "Test completed",
                    "testId": test_id,
                    "prescriptionId": already_rx.get("prescription_id"),
                }
            if rx_repo is not None and test:
                from datetime import timedelta

                now = datetime.utcnow()
                rx_number = (
                    f"RX-{now.strftime('%y%m%d')}-{str(uuid.uuid4())[:6].upper()}"
                )
                customer_id = test.get("customer_id", "")
                store_id = test.get("store_id", "")

                rx_data = {
                    "prescription_id": str(uuid.uuid4()),
                    "prescription_number": rx_number,
                    # Attribute to the specific family member when the queue/test
                    # carried one; fall back to the account holder for legacy
                    # tests. Threading patient_id through queue->test is the
                    # remaining half of the Family-Rx grouping fix.
                    "patient_id": test.get("patient_id") or customer_id,
                    "customer_id": customer_id,
                    "store_id": store_id,
                    "source": "TESTED_AT_STORE",
                    "optometrist_id": current_user.get("user_id", ""),
                    "optometrist_name": current_user.get(
                        "full_name", current_user.get("username", "")
                    ),
                    "eye_test_id": test_id,
                    # An ABSENT power/axis means "not tested for this eye" -> leave
                    # it blank, never fabricate a 0.00 power or a 180 axis into a
                    # billable Rx (audit P1). A genuine plano "0" is preserved.
                    "right_eye": {
                        "sph": str(
                            data.right_eye.get("sphere") or data.right_eye.get("sph") or ""
                        ),
                        "cyl": str(
                            data.right_eye.get("cylinder") or data.right_eye.get("cyl") or ""
                        ),
                        "axis": data.right_eye.get("axis"),
                        "add": str(data.right_eye.get("add") or ""),
                        "pd": str(data.right_eye.get("pd", "")),
                        "prism": (data.right_eye.get("prism") or None),
                        "base": (data.right_eye.get("base") or None),
                        "acuity": (
                            data.right_eye.get("acuity")
                            or data.right_eye.get("va")
                            or None
                        ),
                    },
                    "left_eye": {
                        "sph": str(
                            data.left_eye.get("sphere") or data.left_eye.get("sph") or ""
                        ),
                        "cyl": str(
                            data.left_eye.get("cylinder") or data.left_eye.get("cyl") or ""
                        ),
                        "axis": data.left_eye.get("axis"),
                        "add": str(data.left_eye.get("add") or ""),
                        "pd": str(data.left_eye.get("pd", "")),
                        "prism": (data.left_eye.get("prism") or None),
                        "base": (data.left_eye.get("base") or None),
                        "acuity": (
                            data.left_eye.get("acuity")
                            or data.left_eye.get("va")
                            or None
                        ),
                    },
                    "lens_recommendation": data.lens_recommendation,
                    "coating_recommendation": data.coating_recommendation,
                    "ipd": data.ipd,
                    "next_checkup": data.next_checkup,
                    "remarks": data.notes,
                    "validity_months": 12,
                    "test_date": now.isoformat(),
                    "expiry_date": (now + timedelta(days=365)).isoformat(),
                    "status": "ACTIVE",
                    "created_at": now.isoformat(),
                    "created_by": current_user.get("user_id", ""),
                }
                try:
                    created = rx_repo.create(rx_data)
                    if created:
                        prescription_id = rx_data["prescription_id"]
                except Exception as e:
                    # Log but don't fail the test completion
                    import logging

                    logging.getLogger(__name__).warning(
                        f"Auto-prescription creation failed: {e}"
                    )

            _audit_clinical(
                "EYE_TEST_RECORDED",
                test_id,
                current_user,
                store_id=(test or {}).get("store_id"),
                detail={
                    "customer_id": (test or {}).get("customer_id"),
                    "patient_id": (test or {}).get("patient_id"),
                    "prescription_id": prescription_id,
                },
            )

            return {
                "message": "Test completed",
                "testId": test_id,
                "prescriptionId": prescription_id,
            }

    # Fallback for demo
    return {"message": "Test completed", "testId": test_id}


@router.get("/tests/patient/{customer_phone}")
async def get_patient_tests(
    customer_phone: str, current_user: dict = Depends(get_current_user)
):
    """Get all tests for a patient by phone number"""
    test_repo = get_eye_test_repository()

    if test_repo is not None:
        tests = test_repo.get_patient_tests(customer_phone)
        result = []
        for test in tests:
            converted = _convert_to_camel(test)
            converted["id"] = test.get("test_id")
            result.append(converted)
        return {"tests": result, "total": len(result)}

    return {"tests": [], "total": 0}


@router.get("/tests/customer/{customer_id}")
async def get_customer_tests(
    customer_id: str, current_user: dict = Depends(get_current_user)
):
    """Get all tests for a customer by ID"""
    test_repo = get_eye_test_repository()

    if test_repo is not None:
        tests = test_repo.get_customer_tests(customer_id)
        result = []
        for test in tests:
            converted = _convert_to_camel(test)
            converted["id"] = test.get("test_id")
            result.append(converted)
        return {"tests": result, "total": len(result)}

    return {"tests": [], "total": 0}


@router.get("/optometrist/{optometrist_id}/stats")
async def get_optometrist_stats(
    optometrist_id: str,
    from_date: date = Query(...),
    to_date: date = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Get statistics for an optometrist"""
    test_repo = get_eye_test_repository()

    if test_repo is not None:
        return test_repo.get_optometrist_stats(optometrist_id, from_date, to_date)

    return {"total_tests": 0, "completed_tests": 0, "completion_rate": 0}


# ============================================================================
# PRESCRIPTION PRINT (A5 card) + REDO TRACKING
# ============================================================================


def _eye_block(rx: dict, key: str) -> dict:
    """Normalise a per-eye block. Prescriptions store eyes under several shapes:
    right_eye/left_eye with sph/cyl/axis/add (the auto-created Rx in
    complete_test) or sphere/cylinder. Return a dict of raw values; rendering
    is left to format_rx_value / format_axis_value."""
    eye = rx.get(key) or {}
    if not isinstance(eye, dict):
        eye = {}
    return {
        "sph": eye.get("sph", eye.get("sphere")),
        "cyl": eye.get("cyl", eye.get("cylinder")),
        "axis": eye.get("axis"),
        "add": eye.get("add"),
        "pd": eye.get("pd"),
    }


def _rx_date(rx: dict) -> str:
    """Best-effort human date for the Rx (prescription_date / test_date / created_at)."""
    raw = rx.get("prescription_date") or rx.get("test_date") or rx.get("created_at")
    if raw is None:
        return ""
    if isinstance(raw, datetime):
        return raw.strftime("%d %b %Y")
    if isinstance(raw, str):
        # ISO strings -> dd Mon YYYY; fall back to the raw string on parse fail.
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).strftime(
                "%d %b %Y"
            )
        except ValueError:
            return raw[:10]
    return str(raw)


def _build_rx_card_html(rx: dict, store: Optional[dict]) -> str:
    """Build a self-contained, printable A5 Rx card (no external assets).

    PURE-ish: depends only on its two dict args, so it renders deterministically
    for a given prescription + store. All dynamic text is HTML-escaped.
    """
    store = store or {}

    def esc(v) -> str:
        return _html_escape("" if v is None else str(v))

    clinic_name = (
        store.get("store_name")
        or store.get("storeName")
        or store.get("name")
        or "Eye Clinic"
    )
    addr_parts = [
        store.get("address"),
        store.get("city"),
        store.get("state"),
        store.get("pincode"),
    ]
    clinic_addr = ", ".join(str(p) for p in addr_parts if p)
    clinic_phone = store.get("phone") or store.get("contact_number") or ""
    clinic_gstin = store.get("gstin") or ""

    patient_name = (
        rx.get("patient_name")
        or rx.get("patientName")
        or rx.get("customer_name")
        or "Patient"
    )
    age = rx.get("patient_age", rx.get("age"))
    phone = rx.get("customer_phone") or rx.get("patient_phone") or rx.get("phone")
    optometrist = rx.get("optometrist_name") or rx.get("optometristName") or ""
    rx_number = rx.get("prescription_number") or rx.get("prescription_id") or ""
    rx_date = _rx_date(rx)

    od = _eye_block(rx, "right_eye")
    os_ = _eye_block(rx, "left_eye")

    # PD: prefer a top-level pd, else fall back to per-eye pd values.
    pd = rx.get("pd")
    if pd in (None, "", 0):
        pd = od.get("pd") or os_.get("pd")
    pd_str = "" if pd in (None, "") else str(pd)

    def row(label: str, eye: dict) -> str:
        return (
            "<tr>"
            f"<td class='eye'>{esc(label)}</td>"
            f"<td>{esc(format_rx_value(eye['sph']))}</td>"
            f"<td>{esc(format_rx_value(eye['cyl']))}</td>"
            f"<td>{esc(format_axis_value(eye['axis']))}</td>"
            f"<td>{esc(format_rx_value(eye['add']))}</td>"
            "</tr>"
        )

    # Optional meta lines built only when present.
    age_html = f"<span><b>Age:</b> {esc(age)}</span>" if age not in (None, "") else ""
    phone_html = (
        f"<span><b>Phone:</b> {esc(phone)}</span>" if phone not in (None, "") else ""
    )
    pd_html = f"<div class='pd'><b>PD:</b> {esc(pd_str)} mm</div>" if pd_str else ""
    gstin_html = (
        f"<div class='clinic-gstin'>GSTIN: {esc(clinic_gstin)}</div>"
        if clinic_gstin
        else ""
    )
    addr_html = (
        f"<div class='clinic-addr'>{esc(clinic_addr)}</div>" if clinic_addr else ""
    )
    phone_line_html = (
        f"<div class='clinic-phone'>Ph: {esc(clinic_phone)}</div>"
        if clinic_phone
        else ""
    )
    rxno_html = (
        f"<span class='rx-no'>Rx No: {esc(rx_number)}</span>" if rx_number else ""
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Prescription {esc(rx_number)}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{
    font-family: Arial, Helvetica, sans-serif;
    color: #111827;
    margin: 0;
    background: #f3f4f6;
  }}
  .card {{
    width: 148mm;
    min-height: 210mm;
    margin: 8mm auto;
    padding: 12mm;
    background: #ffffff;
    border: 1px solid #e5e7eb;
  }}
  .clinic {{ text-align: center; border-bottom: 2px solid #111827; padding-bottom: 6px; }}
  .clinic-name {{ font-size: 18px; font-weight: bold; }}
  .clinic-addr, .clinic-phone, .clinic-gstin {{ font-size: 11px; color: #4b5563; }}
  .doc-title {{
    text-align: center; font-size: 13px; font-weight: bold;
    letter-spacing: 2px; margin: 10px 0 6px;
  }}
  .meta {{ display: flex; flex-wrap: wrap; gap: 4px 18px; font-size: 12px; margin-bottom: 4px; }}
  .meta-row {{ display: flex; justify-content: space-between; font-size: 12px; margin-bottom: 8px; }}
  table.rx {{ width: 100%; border-collapse: collapse; margin: 10px 0; }}
  table.rx th, table.rx td {{
    border: 1px solid #9ca3af; padding: 6px 4px; text-align: center; font-size: 13px;
  }}
  table.rx th {{ background: #f9fafb; font-size: 11px; }}
  table.rx td.eye {{ text-align: left; font-weight: bold; }}
  .pd {{ font-size: 12px; margin: 6px 0; }}
  .footer {{ margin-top: 18mm; display: flex; justify-content: space-between; align-items: flex-end; }}
  .sign {{ text-align: center; font-size: 11px; }}
  .sign .line {{ border-top: 1px solid #111827; width: 48mm; margin-bottom: 2px; }}
  .rx-no {{ color: #6b7280; }}
  @media print {{
    @page {{ size: A5; margin: 0; }}
    body {{ background: #ffffff; }}
    .card {{ margin: 0; border: none; }}
  }}
</style>
</head>
<body onload="window.print()">
  <div class="card">
    <div class="clinic">
      <div class="clinic-name">{esc(clinic_name)}</div>
      {addr_html}
      {phone_line_html}
      {gstin_html}
    </div>
    <div class="doc-title">PRESCRIPTION</div>
    <div class="meta-row">
      <span><b>Patient:</b> {esc(patient_name)}</span>
      {rxno_html}
    </div>
    <div class="meta">
      {age_html}
      {phone_html}
      <span><b>Date:</b> {esc(rx_date)}</span>
    </div>
    <table class="rx">
      <thead>
        <tr><th>Eye</th><th>SPH</th><th>CYL</th><th>AXIS</th><th>ADD</th></tr>
      </thead>
      <tbody>
        {row("Right (OD)", od)}
        {row("Left (OS)", os_)}
      </tbody>
    </table>
    {pd_html}
    <div class="footer">
      <div></div>
      <div class="sign">
        <div class="line"></div>
        <div>{esc(optometrist) or "Optometrist"}</div>
      </div>
    </div>
  </div>
</body>
</html>"""


@router.get("/prescriptions/{prescription_id}/print")
async def print_prescription(
    prescription_id: str = Path(...),
    current_user: dict = Depends(get_current_user),
):
    """Return a self-contained, printable A5 Rx card (read-only).

    Any authenticated clinical/POS user can print. Fail-soft: if the DB is
    unavailable we still return a minimal valid card rather than 500-ing, so a
    degraded backend never blocks a counter staffer from handing a patient a
    printout.
    """
    rx_repo = get_prescription_repository()
    if rx_repo is None:
        # DB down: render an empty-but-valid card so the print window opens.
        return HTMLResponse(
            _build_rx_card_html({"prescription_id": prescription_id}, None)
        )

    rx = rx_repo.find_by_id(prescription_id)
    if not rx:
        raise HTTPException(status_code=404, detail="Prescription not found")

    store = None
    store_repo = get_store_repository()
    store_id = rx.get("store_id")
    if store_repo is not None and store_id:
        try:
            store = store_repo.find_by_id(store_id)
        except Exception:
            store = None  # Header is optional; never block the printout.

    return HTMLResponse(_build_rx_card_html(rx, store))


@router.post("/prescriptions/{prescription_id}/redo")
async def create_prescription_redo(
    prescription_id: str = Path(...),
    body: RedoCreate = ...,
    current_user: dict = Depends(require_roles(*_REDO_ROLES)),
):
    """Record a redo against a prescription (lens remake / re-dispense).

    Appends to the prescription's `redos` array AND stamps the latest-redo
    fields (redo_of / redo_reason / redo_by / redo_at) so both an audit list
    and a quick "was this redone?" check are cheap. Gated to optometry/manager
    roles.
    """
    rx_repo = get_prescription_repository()
    if rx_repo is None:
        raise HTTPException(status_code=503, detail="Database not available")

    rx = rx_repo.find_by_id(prescription_id)
    if not rx:
        raise HTTPException(status_code=404, detail="Prescription not found")

    now = datetime.utcnow()
    redo_entry = {
        "redo_id": str(uuid.uuid4()),
        "reason": body.reason,
        "redo_by": current_user.get("user_id", ""),
        "redo_by_name": current_user.get("full_name", current_user.get("username", "")),
        "redo_at": now.isoformat(),
    }

    existing = rx.get("redos") or []
    if not isinstance(existing, list):
        existing = []
    updated = existing + [redo_entry]

    ok = rx_repo.update(
        prescription_id,
        {
            "redos": updated,
            "redo_count": len(updated),
            # Latest-redo shortcut fields (linked back to the original Rx).
            "redo_of": prescription_id,
            "redo_reason": body.reason,
            "redo_by": redo_entry["redo_by"],
            "redo_at": redo_entry["redo_at"],
        },
    )
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to record redo")

    return {
        "message": "Redo recorded",
        "prescriptionId": prescription_id,
        "redo": redo_entry,
        "redoCount": len(updated),
    }


@router.get("/prescriptions/{prescription_id}/redos")
async def list_prescription_redos(
    prescription_id: str = Path(...),
    current_user: dict = Depends(get_current_user),
):
    """Return the redo history for a prescription (most recent last)."""
    rx_repo = get_prescription_repository()
    if rx_repo is None:
        return {"redos": [], "total": 0}

    rx = rx_repo.find_by_id(prescription_id)
    if not rx:
        raise HTTPException(status_code=404, detail="Prescription not found")

    redos = rx.get("redos") or []
    if not isinstance(redos, list):
        redos = []
    return {"redos": redos, "total": len(redos)}


# ============================================================================
# ABUSE / FRAUD-SIGNAL DETECTION
# ============================================================================


def _rx_has_redo(rx: dict) -> bool:
    """True if a prescription needed a redo. A redo is stamped onto the Rx
    (redo_count / redos array / redo_of) by the redo endpoint, so any of those
    being set marks this test as redone."""
    if rx.get("redo_count"):
        try:
            if int(rx.get("redo_count")) > 0:
                return True
        except (TypeError, ValueError):
            pass
    redos = rx.get("redos")
    if isinstance(redos, list) and len(redos) > 0:
        return True
    if rx.get("redo_of"):
        return True
    return False


def _opto_label(rx: dict) -> str:
    """Human label for the optometrist on an Rx (name preferred, else id)."""
    return (
        rx.get("optometrist_name")
        or rx.get("optometristName")
        or rx.get("optometrist_id")
        or "Unknown"
    )


def _patient_label(rx: dict) -> str:
    """Human label for the patient on an Rx."""
    return (
        rx.get("patient_name")
        or rx.get("patientName")
        or rx.get("customer_name")
        or rx.get("customer_phone")
        or rx.get("customer_id")
        or "Unknown patient"
    )


def _build_abuse_alerts(prescriptions: List[dict], now: datetime) -> List[dict]:
    """Pure-ish assembly of AbuseAlert dicts from a window of prescriptions.

    Takes the already-fetched, already-window-filtered prescription list and
    the reference 'now', groups by optometrist / patient, and runs each
    detector in ``services.clinical_abuse``. Returns a list of dicts matching
    the frontend ``AbuseAlert`` interface (camelCase keys). No IO -> unit-test
    friendly. Each detector is wrapped so a malformed row can't sink the rest.
    """
    alerts: List[dict] = []
    now_iso = now.isoformat()

    # --- group by optometrist ---
    by_opto: dict = {}
    for rx in prescriptions:
        if not isinstance(rx, dict):
            continue
        opto_id = rx.get("optometrist_id") or rx.get("optometristId") or _opto_label(rx)
        bucket = by_opto.setdefault(
            opto_id,
            {"name": _opto_label(rx), "rxs": [], "redos": 0, "oor": 0, "dates": []},
        )
        bucket["rxs"].append(rx)
        if _rx_has_redo(rx):
            bucket["redos"] += 1
        try:
            if _abuse.is_rx_out_of_range(rx):
                bucket["oor"] += 1
        except Exception:  # pragma: no cover - defensive
            pass
        dt = _abuse.rx_date(rx)
        if dt is not None:
            bucket["dates"].append(dt)

    # 1. Excessive redos per optometrist
    for opto_id, b in by_opto.items():
        total = len(b["rxs"])
        try:
            sev = _abuse.redo_severity(b["redos"], total)
        except Exception:  # pragma: no cover - defensive
            sev = None
        if sev:
            rate = _abuse.redo_rate_percent(b["redos"], total)
            alerts.append(
                {
                    "id": f"redo-{opto_id}",
                    "type": "high-redo-rate",
                    "severity": sev,
                    "optometristName": b["name"],
                    "optometristId": str(opto_id),
                    "details": (
                        f"{b['redos']} redo(s) across {total} tests "
                        f"({rate:.1f}% redo rate) -- above the "
                        f"{int(_abuse.REDO_RATE_WARN * 100)}% review threshold."
                    ),
                    "timestamp": now_iso,
                    "redoRate": rate,
                }
            )

    # 2. Out-of-range / suspicious Rx values per optometrist
    for opto_id, b in by_opto.items():
        total = len(b["rxs"])
        try:
            sev = _abuse.out_of_range_severity(b["oor"], total)
        except Exception:  # pragma: no cover - defensive
            sev = None
        if sev:
            pct = round(b["oor"] / total * 100.0, 1) if total else 0.0
            alerts.append(
                {
                    "id": f"oor-{opto_id}",
                    "type": "high-redo-rate",
                    "severity": sev,
                    "optometristName": b["name"],
                    "optometristId": str(opto_id),
                    "details": (
                        f"{b['oor']} of {total} tests ({pct:.1f}%) carry Rx "
                        "values at or beyond the validation limits "
                        "(SPH +/-20, CYL +/-6, AXIS 1-180, ADD +0.75..+3.50)."
                    ),
                    "timestamp": now_iso,
                }
            )

    # 4. Rapid / implausibly-fast entries per optometrist
    for opto_id, b in by_opto.items():
        try:
            burst = _abuse.find_rapid_burst(b["dates"])
        except Exception:  # pragma: no cover - defensive
            burst = None
        if burst:
            sev = _abuse.rapid_severity(burst["avg_gap_minutes"])
            alerts.append(
                {
                    "id": f"rapid-{opto_id}",
                    "type": "suspicious-speed",
                    "severity": sev,
                    "optometristName": b["name"],
                    "optometristId": str(opto_id),
                    "details": (
                        f"{burst['count']} tests entered within "
                        f"{burst['span_minutes']:.0f} min "
                        f"(~{burst['avg_gap_minutes']:.1f} min apart) -- "
                        "implausibly fast for genuine refractions."
                    ),
                    "timestamp": now_iso,
                }
            )

    # 3. Repeat tests for the same patient in a short window
    by_patient: dict = {}
    for rx in prescriptions:
        if not isinstance(rx, dict):
            continue
        pid = (
            rx.get("customer_id")
            or rx.get("patient_id")
            or rx.get("customer_phone")
            or _patient_label(rx)
        )
        pb = by_patient.setdefault(pid, {"name": _patient_label(rx), "dates": []})
        dt = _abuse.rx_date(rx)
        if dt is not None:
            pb["dates"].append(dt)

    for pid, pb in by_patient.items():
        try:
            in_window = _abuse.max_tests_in_window(
                pb["dates"], _abuse.REPEAT_WINDOW_DAYS
            )
            sev = _abuse.repeat_severity(in_window)
        except Exception:  # pragma: no cover - defensive
            sev = None
            in_window = 0
        if sev:
            alerts.append(
                {
                    "id": f"repeat-{pid}",
                    "type": "exact-copy",
                    "severity": sev,
                    # The patient is the subject here; surface them in the
                    # optometrist slot so the (single-name) card stays useful.
                    "optometristName": pb["name"],
                    "optometristId": str(pid),
                    "details": (
                        f"{in_window} eye tests for the same patient within "
                        f"{_abuse.REPEAT_WINDOW_DAYS} days -- possible repeat-"
                        "visit gaming of footfall / incentives."
                    ),
                    "timestamp": now_iso,
                }
            )

    # Critical first, then warnings; stable within each severity.
    alerts.sort(key=lambda a: 0 if a.get("severity") == "critical" else 1)
    return alerts


@router.get("/abuse-detection")
async def get_abuse_detection(
    store_id: Optional[str] = Query(None, alias="store_id"),
    days: int = Query(30, ge=1, le=365),
    current_user: dict = Depends(require_roles(*_ABUSE_VIEW_ROLES)),
):
    """Clinical fraud-control: compute abuse alerts over a recent window.

    Returns ``{"alerts": [AbuseAlert, ...], "generated_at": iso}`` where each
    alert matches the frontend ``AbuseAlert`` interface. Fail-soft throughout:
    no DB or no data -> an empty alert list (never a 500), so the management
    view degrades to "No issues detected" rather than erroring.

    Gated to STORE_MANAGER / AREA_MANAGER / ADMIN (SUPERADMIN auto-passes).
    """
    from datetime import timedelta

    now = datetime.utcnow()
    generated_at = now.isoformat()

    # Fail-soft: degraded backend returns an empty (valid) envelope.
    db = get_db()
    rx_repo = get_prescription_repository()
    if db is None or rx_repo is None:
        return {"alerts": [], "generated_at": generated_at}

    # Default the store scope to the caller's active store when none is given.
    if not store_id:
        store_id = current_user.get("active_store_id")

    cutoff = now - timedelta(days=days)

    try:
        flt: dict = {}
        if store_id:
            flt["store_id"] = store_id
        # Pull a generous slice (well past a single store's 30-day volume) and
        # window-filter in Python -- prescriptions store dates inconsistently
        # (prescription_date as datetime, test_date/created_at as ISO string),
        # so a single Mongo range query would silently miss the string-dated
        # auto-created Rx rows. Python-side parsing via rx_date() catches all.
        rows = rx_repo.find_many(flt, sort=[("created_at", -1)], limit=5000)
    except Exception as e:  # pragma: no cover - defensive
        import logging

        logging.getLogger(__name__).warning("[CLINICAL] abuse fetch failed: %s", e)
        return {"alerts": [], "generated_at": generated_at}

    in_window: List[dict] = []
    for rx in rows or []:
        dt = _abuse.rx_date(rx)
        # Undated rows are kept (can't prove they're stale); dated rows must
        # fall inside the window.
        if dt is None or dt >= cutoff:
            in_window.append(rx)

    try:
        alerts = _build_abuse_alerts(in_window, now)
    except Exception as e:  # pragma: no cover - defensive
        import logging

        logging.getLogger(__name__).warning("[CLINICAL] abuse build failed: %s", e)
        alerts = []

    return {"alerts": alerts, "generated_at": generated_at}

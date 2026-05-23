"""
IMS 2.0 - Clinical Router
==========================
Eye test queue and clinical management endpoints with database persistence
"""

from fastapi import APIRouter, HTTPException, Depends, Query, Path
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime, date
from html import escape as _html_escape
import uuid
from .auth import get_current_user, require_roles
from ..dependencies import (
    get_eye_test_queue_repository,
    get_eye_test_repository,
    get_prescription_repository,
    get_customer_repository,
    get_store_repository,
)

router = APIRouter()

# Roles permitted to mutate the optometry queue + eye-test records. Mirrors the
# frontend Clinical route guard. SUPERADMIN auto-passes via require_roles.
_CLINICAL_ROLES = ("ADMIN", "STORE_MANAGER", "OPTOMETRIST")

# Roles permitted to record a redo on a prescription. Wider than the queue
# mutators on purpose: an Area Manager auditing a botched dispense should be
# able to flag a redo. SUPERADMIN auto-passes via require_roles.
_REDO_ROLES = ("OPTOMETRIST", "STORE_MANAGER", "AREA_MANAGER", "ADMIN")


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

    class Config:
        populate_by_name = True


class EyeTestData(BaseModel):
    right_eye: dict = Field(..., alias="rightEye")
    left_eye: dict = Field(..., alias="leftEye")
    pd: Optional[float] = None
    notes: Optional[str] = None
    lens_recommendation: Optional[str] = Field(None, alias="lensRecommendation")
    coating_recommendation: Optional[str] = Field(None, alias="coatingRecommendation")

    class Config:
        populate_by_name = True


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
    """Update queue item status"""
    queue_repo = get_eye_test_queue_repository()

    if queue_repo is not None:
        success = queue_repo.update_status(queue_id, body.status)
        if success:
            return {"message": "Status updated", "status": body.status}
        # Item may not exist, but still return success for compatibility
        return {"message": "Status updated", "status": body.status}

    return {"message": "Status updated", "status": body.status}


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
    test_repo = get_eye_test_repository()
    queue_repo = get_eye_test_queue_repository()

    if test_repo is not None:
        # Update test record
        success = test_repo.complete_test(
            test_id=test_id,
            right_eye=data.right_eye,
            left_eye=data.left_eye,
            pd=data.pd,
            notes=data.notes,
            lens_recommendation=data.lens_recommendation,
            coating_recommendation=data.coating_recommendation,
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
            rx_repo = get_prescription_repository()
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
                    "patient_id": customer_id,  # In BV, patient_id maps to customer
                    "customer_id": customer_id,
                    "store_id": store_id,
                    "source": "TESTED_AT_STORE",
                    "optometrist_id": current_user.get("user_id", ""),
                    "optometrist_name": current_user.get(
                        "full_name", current_user.get("username", "")
                    ),
                    "eye_test_id": test_id,
                    "right_eye": {
                        "sph": str(
                            data.right_eye.get("sphere", data.right_eye.get("sph", "0"))
                        ),
                        "cyl": str(
                            data.right_eye.get(
                                "cylinder", data.right_eye.get("cyl", "0")
                            )
                        ),
                        "axis": data.right_eye.get("axis", 180),
                        "add": str(data.right_eye.get("add", "0")),
                        "pd": str(data.right_eye.get("pd", "")),
                    },
                    "left_eye": {
                        "sph": str(
                            data.left_eye.get("sphere", data.left_eye.get("sph", "0"))
                        ),
                        "cyl": str(
                            data.left_eye.get("cylinder", data.left_eye.get("cyl", "0"))
                        ),
                        "axis": data.left_eye.get("axis", 180),
                        "add": str(data.left_eye.get("add", "0")),
                        "pd": str(data.left_eye.get("pd", "")),
                    },
                    "lens_recommendation": data.lens_recommendation,
                    "coating_recommendation": data.coating_recommendation,
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
    raw = (
        rx.get("prescription_date")
        or rx.get("test_date")
        or rx.get("created_at")
    )
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
    age_html = (
        f"<span><b>Age:</b> {esc(age)}</span>" if age not in (None, "") else ""
    )
    phone_html = (
        f"<span><b>Phone:</b> {esc(phone)}</span>" if phone not in (None, "") else ""
    )
    pd_html = (
        f"<div class='pd'><b>PD:</b> {esc(pd_str)} mm</div>" if pd_str else ""
    )
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
        "redo_by_name": current_user.get(
            "full_name", current_user.get("username", "")
        ),
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

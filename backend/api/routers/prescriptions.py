"""
IMS 2.0 - Prescriptions Router
===============================
Prescription management endpoints
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import Optional
from datetime import date, datetime, timedelta
import uuid

from .auth import get_current_user
from ..dependencies import get_prescription_repository, get_customer_repository

router = APIRouter()


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

    if repo:
        prescriptions = repo.find_by_patient(patient_id)
        return {"prescriptions": prescriptions, "total": len(prescriptions)}

    return {"prescriptions": [], "total": 0}


@router.get("/patient/{patient_id}/latest")
async def get_latest_prescription(
    patient_id: str, current_user: dict = Depends(get_current_user)
):
    """Get latest prescription for a patient"""
    repo = get_prescription_repository()

    if repo:
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

    if repo:
        prescriptions = repo.find_valid(patient_id)
        return {"prescriptions": prescriptions, "total": len(prescriptions)}

    return {"prescriptions": [], "total": 0}


@router.get("/expiring")
async def get_expiring_prescriptions(
    days: int = Query(30, ge=7, le=90), current_user: dict = Depends(get_current_user)
):
    """Get prescriptions expiring within specified days"""
    repo = get_prescription_repository()

    if repo:
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

    if repo:
        stats = repo.get_optometrist_stats(optometrist_id, from_date, to_date)
        return stats

    return {"total": 0, "tested_at_store": 0}


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

    if repo:
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


@router.post("/", status_code=201)
async def create_prescription(
    rx: PrescriptionCreate, current_user: dict = Depends(get_current_user)
):
    """Create a new prescription"""
    repo = get_prescription_repository()
    customer_repo = get_customer_repository()

    # Validate optometrist requirement
    if rx.source == "TESTED_AT_STORE" and not rx.optometrist_id:
        raise HTTPException(
            status_code=400, detail="Optometrist required for store tests"
        )

    # Validate axis is whole number
    if rx.right_eye.axis and not isinstance(rx.right_eye.axis, int):
        raise HTTPException(
            status_code=400, detail="Right eye axis must be whole number"
        )
    if rx.left_eye.axis and not isinstance(rx.left_eye.axis, int):
        raise HTTPException(
            status_code=400, detail="Left eye axis must be whole number"
        )

    if repo:
        # Verify customer exists
        if customer_repo:
            customer = customer_repo.find_by_id(rx.customer_id)
            if not customer:
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

    if repo:
        prescription = repo.find_by_id(prescription_id)
        if prescription:
            return prescription
        raise HTTPException(status_code=404, detail="Prescription not found")

    return {"prescription_id": prescription_id}


@router.get("/{prescription_id}/print")
async def print_prescription(
    prescription_id: str, current_user: dict = Depends(get_current_user)
):
    """Generate printable prescription HTML"""
    repo = get_prescription_repository()

    if repo:
        prescription = repo.find_by_id(prescription_id)
        if not prescription:
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

"""
IMS 2.0 - Prescriptions Router
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import Optional
from datetime import date
from .auth import get_current_user

router = APIRouter()

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

@router.get("/")
async def list_prescriptions(
    patient_id: Optional[str] = Query(None),
    customer_id: Optional[str] = Query(None),
    optometrist_id: Optional[str] = Query(None),
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    current_user: dict = Depends(get_current_user)
):
    return {"prescriptions": []}

@router.post("/", status_code=201)
async def create_prescription(rx: PrescriptionCreate, current_user: dict = Depends(get_current_user)):
    if rx.source == "TESTED_AT_STORE" and not rx.optometrist_id:
        raise HTTPException(status_code=400, detail="Optometrist required for store tests")
    # Validate axis is whole number
    if rx.right_eye.axis and not isinstance(rx.right_eye.axis, int):
        raise HTTPException(status_code=400, detail="Axis must be whole number")
    return {"prescription_id": "new-rx-id", "prescription_number": "RX-001"}

@router.get("/{prescription_id}")
async def get_prescription(prescription_id: str, current_user: dict = Depends(get_current_user)):
    return {"prescription_id": prescription_id}

@router.get("/patient/{patient_id}")
async def get_patient_prescriptions(patient_id: str, current_user: dict = Depends(get_current_user)):
    return {"prescriptions": []}

@router.get("/patient/{patient_id}/latest")
async def get_latest_prescription(patient_id: str, current_user: dict = Depends(get_current_user)):
    return {"prescription": None}

@router.get("/patient/{patient_id}/valid")
async def get_valid_prescriptions(patient_id: str, current_user: dict = Depends(get_current_user)):
    return {"prescriptions": []}

@router.get("/{prescription_id}/print")
async def print_prescription(prescription_id: str, current_user: dict = Depends(get_current_user)):
    return {"html": "<html>...</html>"}

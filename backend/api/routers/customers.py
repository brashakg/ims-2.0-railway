"""
IMS 2.0 - Customers Router
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import date
from .auth import get_current_user

router = APIRouter()

class PatientCreate(BaseModel):
    name: str
    mobile: Optional[str] = None
    dob: Optional[date] = None
    anniversary: Optional[date] = None

class CustomerCreate(BaseModel):
    customer_type: str = "B2C"  # B2C, B2B
    name: str = Field(..., min_length=2)
    mobile: str = Field(..., pattern=r"^\d{10}$")
    email: Optional[str] = None
    gstin: Optional[str] = None
    billing_address: Optional[dict] = None
    patients: List[PatientCreate] = []

class CustomerUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    billing_address: Optional[dict] = None

@router.get("/")
async def list_customers(
    search: Optional[str] = Query(None),
    customer_type: Optional[str] = Query(None),
    skip: int = Query(0),
    limit: int = Query(50),
    current_user: dict = Depends(get_current_user)
):
    return {"customers": [], "total": 0}

@router.post("/", status_code=201)
async def create_customer(customer: CustomerCreate, current_user: dict = Depends(get_current_user)):
    return {"customer_id": "new-customer-id", "name": customer.name}

@router.get("/search")
async def search_customers(q: str = Query(..., min_length=3), current_user: dict = Depends(get_current_user)):
    return {"customers": []}

@router.get("/mobile/{mobile}")
async def get_customer_by_mobile(mobile: str, current_user: dict = Depends(get_current_user)):
    return {"mobile": mobile}

@router.get("/{customer_id}")
async def get_customer(customer_id: str, current_user: dict = Depends(get_current_user)):
    return {"customer_id": customer_id}

@router.put("/{customer_id}")
async def update_customer(customer_id: str, customer: CustomerUpdate, current_user: dict = Depends(get_current_user)):
    return {"message": "Customer updated"}

@router.post("/{customer_id}/patients")
async def add_patient(customer_id: str, patient: PatientCreate, current_user: dict = Depends(get_current_user)):
    return {"patient_id": "new-patient-id"}

@router.get("/{customer_id}/orders")
async def get_customer_orders(customer_id: str, current_user: dict = Depends(get_current_user)):
    return {"orders": []}

@router.get("/{customer_id}/prescriptions")
async def get_customer_prescriptions(customer_id: str, current_user: dict = Depends(get_current_user)):
    return {"prescriptions": []}

@router.post("/{customer_id}/loyalty/add")
async def add_loyalty_points(customer_id: str, points: int = Query(..., ge=1), current_user: dict = Depends(get_current_user)):
    return {"message": f"Added {points} loyalty points"}

@router.post("/{customer_id}/store-credit/add")
async def add_store_credit(customer_id: str, amount: float = Query(..., gt=0), current_user: dict = Depends(get_current_user)):
    return {"message": f"Added ₹{amount} store credit"}

"""
IMS 2.0 - Vendors Router
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import List, Optional
from .auth import get_current_user

router = APIRouter()

class VendorCreate(BaseModel):
    legal_name: str
    trade_name: str
    vendor_type: str = "INDIAN"
    gstin_status: str
    gstin: Optional[str] = None
    address: str
    city: str
    state: str
    mobile: str
    email: Optional[str] = None
    credit_days: int = 30

class POItemCreate(BaseModel):
    product_id: str
    quantity: int
    unit_price: float

class POCreate(BaseModel):
    vendor_id: str
    delivery_store_id: str
    items: List[POItemCreate]
    expected_date: Optional[str] = None

class GRNItemCreate(BaseModel):
    po_item_id: str
    received_qty: int
    accepted_qty: int
    rejected_qty: int = 0
    rejection_reason: Optional[str] = None

class GRNCreate(BaseModel):
    po_id: str
    vendor_invoice_no: str
    vendor_invoice_date: str
    items: List[GRNItemCreate]

@router.get("/")
async def list_vendors(search: Optional[str] = Query(None), current_user: dict = Depends(get_current_user)):
    return {"vendors": []}

@router.post("/", status_code=201)
async def create_vendor(vendor: VendorCreate, current_user: dict = Depends(get_current_user)):
    return {"vendor_id": "new-vendor-id"}

@router.get("/{vendor_id}")
async def get_vendor(vendor_id: str, current_user: dict = Depends(get_current_user)):
    return {"vendor_id": vendor_id}

@router.get("/purchase-orders")
async def list_pos(vendor_id: Optional[str] = Query(None), status: Optional[str] = Query(None), current_user: dict = Depends(get_current_user)):
    return {"purchase_orders": []}

@router.post("/purchase-orders", status_code=201)
async def create_po(po: POCreate, current_user: dict = Depends(get_current_user)):
    return {"po_id": "new-po-id", "po_number": "PO-001"}

@router.get("/purchase-orders/{po_id}")
async def get_po(po_id: str, current_user: dict = Depends(get_current_user)):
    return {"po_id": po_id}

@router.post("/purchase-orders/{po_id}/send")
async def send_po(po_id: str, current_user: dict = Depends(get_current_user)):
    return {"message": "PO sent to vendor"}

@router.get("/grn")
async def list_grns(store_id: Optional[str] = Query(None), status: Optional[str] = Query(None), current_user: dict = Depends(get_current_user)):
    return {"grns": []}

@router.post("/grn", status_code=201)
async def create_grn(grn: GRNCreate, current_user: dict = Depends(get_current_user)):
    return {"grn_id": "new-grn-id", "grn_number": "GRN-001"}

@router.post("/grn/{grn_id}/accept")
async def accept_grn(grn_id: str, current_user: dict = Depends(get_current_user)):
    return {"message": "GRN accepted, stock added"}

@router.post("/grn/{grn_id}/escalate")
async def escalate_grn(grn_id: str, note: str = Query(...), current_user: dict = Depends(get_current_user)):
    return {"message": "GRN escalated to HQ"}

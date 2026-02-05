"""
IMS 2.0 - Vendors Router
=========================
Real database queries for vendor and purchase order management
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import uuid
from .auth import get_current_user
from ..dependencies import (
    get_vendor_repository,
    get_purchase_order_repository,
    get_grn_repository,
    get_stock_repository,
)

router = APIRouter()


# ============================================================================
# SCHEMAS
# ============================================================================

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


class VendorUpdate(BaseModel):
    legal_name: Optional[str] = None
    trade_name: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    mobile: Optional[str] = None
    email: Optional[str] = None
    credit_days: Optional[int] = None
    is_active: Optional[bool] = None


class POItemCreate(BaseModel):
    product_id: str
    product_name: str
    sku: str
    quantity: int
    unit_price: float


class POCreate(BaseModel):
    vendor_id: str
    delivery_store_id: str
    items: List[POItemCreate]
    expected_date: Optional[str] = None
    notes: Optional[str] = None


class GRNItemCreate(BaseModel):
    po_item_id: str
    product_id: str
    received_qty: int
    accepted_qty: int
    rejected_qty: int = 0
    rejection_reason: Optional[str] = None


class GRNCreate(BaseModel):
    po_id: str
    vendor_invoice_no: str
    vendor_invoice_date: str
    items: List[GRNItemCreate]
    notes: Optional[str] = None


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def generate_po_number(store_id: str) -> str:
    """Generate unique PO number"""
    prefix = store_id[:3].upper() if store_id else "HQ"
    timestamp = datetime.now().strftime('%y%m%d%H%M')
    return f"PO-{prefix}-{timestamp}"


def generate_grn_number(store_id: str) -> str:
    """Generate unique GRN number"""
    prefix = store_id[:3].upper() if store_id else "HQ"
    timestamp = datetime.now().strftime('%y%m%d%H%M')
    return f"GRN-{prefix}-{timestamp}"


# ============================================================================
# VENDOR ENDPOINTS
# ============================================================================

@router.get("/")
async def list_vendors(
    search: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(get_current_user)
):
    """List all vendors with optional search"""
    vendor_repo = get_vendor_repository()

    if not vendor_repo:
        return {"vendors": [], "total": 0}

    filter_dict = {}
    if is_active is not None:
        filter_dict["is_active"] = is_active

    if search:
        # Search in name, trade name, or mobile
        vendors = vendor_repo.search(search)
    else:
        vendors = vendor_repo.find_many(filter_dict, skip=skip, limit=limit)

    return {"vendors": vendors or [], "total": len(vendors) if vendors else 0}


@router.post("/", status_code=201)
async def create_vendor(
    vendor: VendorCreate,
    current_user: dict = Depends(get_current_user)
):
    """Create a new vendor"""
    vendor_repo = get_vendor_repository()
    vendor_id = str(uuid.uuid4())

    if vendor_repo:
        # Check for duplicate GSTIN
        if vendor.gstin:
            existing = vendor_repo.find_one({"gstin": vendor.gstin})
            if existing:
                raise HTTPException(status_code=400, detail="Vendor with this GSTIN already exists")

        vendor_repo.create({
            "vendor_id": vendor_id,
            "legal_name": vendor.legal_name,
            "trade_name": vendor.trade_name,
            "vendor_type": vendor.vendor_type,
            "gstin_status": vendor.gstin_status,
            "gstin": vendor.gstin,
            "address": vendor.address,
            "city": vendor.city,
            "state": vendor.state,
            "mobile": vendor.mobile,
            "email": vendor.email,
            "credit_days": vendor.credit_days,
            "is_active": True,
            "created_by": current_user.get("user_id"),
            "created_at": datetime.now().isoformat()
        })

    return {"vendor_id": vendor_id, "message": "Vendor created successfully"}


@router.get("/{vendor_id}")
async def get_vendor(
    vendor_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Get vendor details"""
    vendor_repo = get_vendor_repository()

    if not vendor_repo:
        return {"vendor_id": vendor_id}

    vendor = vendor_repo.find_by_id(vendor_id)
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")

    return vendor


@router.put("/{vendor_id}")
async def update_vendor(
    vendor_id: str,
    updates: VendorUpdate,
    current_user: dict = Depends(get_current_user)
):
    """Update vendor details"""
    vendor_repo = get_vendor_repository()

    if vendor_repo:
        existing = vendor_repo.find_by_id(vendor_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Vendor not found")

        update_data = updates.model_dump(exclude_unset=True)
        update_data["updated_by"] = current_user.get("user_id")
        update_data["updated_at"] = datetime.now().isoformat()

        vendor_repo.update(vendor_id, update_data)

    return {"vendor_id": vendor_id, "message": "Vendor updated successfully"}


# ============================================================================
# PURCHASE ORDER ENDPOINTS
# ============================================================================

@router.get("/purchase-orders")
async def list_pos(
    vendor_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    store_id: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(get_current_user)
):
    """List purchase orders with filters"""
    po_repo = get_purchase_order_repository()
    active_store = store_id or current_user.get("active_store_id")

    if not po_repo:
        return {"purchase_orders": [], "total": 0}

    filter_dict = {}
    if vendor_id:
        filter_dict["vendor_id"] = vendor_id
    if status:
        filter_dict["status"] = status
    if active_store:
        filter_dict["delivery_store_id"] = active_store

    pos = po_repo.find_many(filter_dict, skip=skip, limit=limit)

    return {"purchase_orders": pos or [], "total": len(pos) if pos else 0}


@router.post("/purchase-orders", status_code=201)
async def create_po(
    po: POCreate,
    current_user: dict = Depends(get_current_user)
):
    """Create a new purchase order"""
    po_repo = get_purchase_order_repository()
    vendor_repo = get_vendor_repository()

    po_id = str(uuid.uuid4())
    po_number = generate_po_number(po.delivery_store_id)

    # Validate vendor exists
    if vendor_repo:
        vendor = vendor_repo.find_by_id(po.vendor_id)
        if not vendor:
            raise HTTPException(status_code=404, detail="Vendor not found")

    # Calculate totals
    subtotal = sum(item.quantity * item.unit_price for item in po.items)
    tax = subtotal * 0.18  # Assuming 18% GST
    total = subtotal + tax

    if po_repo:
        po_repo.create({
            "po_id": po_id,
            "po_number": po_number,
            "vendor_id": po.vendor_id,
            "vendor_name": vendor.get("trade_name") if vendor_repo and vendor else None,
            "delivery_store_id": po.delivery_store_id,
            "items": [item.model_dump() for item in po.items],
            "subtotal": subtotal,
            "tax_amount": tax,
            "total_amount": total,
            "expected_date": po.expected_date,
            "notes": po.notes,
            "status": "DRAFT",
            "created_by": current_user.get("user_id"),
            "created_at": datetime.now().isoformat()
        })

    return {
        "po_id": po_id,
        "po_number": po_number,
        "total_amount": total,
        "message": "Purchase order created"
    }


@router.get("/purchase-orders/{po_id}")
async def get_po(
    po_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Get purchase order details"""
    po_repo = get_purchase_order_repository()

    if not po_repo:
        return {"po_id": po_id}

    po = po_repo.find_by_id(po_id)
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")

    return po


@router.post("/purchase-orders/{po_id}/send")
async def send_po(
    po_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Send PO to vendor (mark as sent)"""
    po_repo = get_purchase_order_repository()

    if po_repo:
        po = po_repo.find_by_id(po_id)
        if not po:
            raise HTTPException(status_code=404, detail="Purchase order not found")

        if po.get("status") != "DRAFT":
            raise HTTPException(status_code=400, detail="Only draft POs can be sent")

        po_repo.update(po_id, {
            "status": "SENT",
            "sent_at": datetime.now().isoformat(),
            "sent_by": current_user.get("user_id")
        })

    return {"message": "PO sent to vendor", "po_id": po_id}


@router.post("/purchase-orders/{po_id}/cancel")
async def cancel_po(
    po_id: str,
    reason: str = Query(...),
    current_user: dict = Depends(get_current_user)
):
    """Cancel a purchase order"""
    po_repo = get_purchase_order_repository()

    if po_repo:
        po = po_repo.find_by_id(po_id)
        if not po:
            raise HTTPException(status_code=404, detail="Purchase order not found")

        if po.get("status") in ["RECEIVED", "CANCELLED"]:
            raise HTTPException(status_code=400, detail="Cannot cancel this PO")

        po_repo.update(po_id, {
            "status": "CANCELLED",
            "cancelled_at": datetime.now().isoformat(),
            "cancelled_by": current_user.get("user_id"),
            "cancellation_reason": reason
        })

    return {"message": "PO cancelled", "po_id": po_id}


# ============================================================================
# GRN (GOODS RECEIVED NOTE) ENDPOINTS
# ============================================================================

@router.get("/grn")
async def list_grns(
    store_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    po_id: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(get_current_user)
):
    """List GRNs with filters"""
    grn_repo = get_grn_repository()
    active_store = store_id or current_user.get("active_store_id")

    if not grn_repo:
        return {"grns": [], "total": 0}

    filter_dict = {}
    if active_store:
        filter_dict["store_id"] = active_store
    if status:
        filter_dict["status"] = status
    if po_id:
        filter_dict["po_id"] = po_id

    grns = grn_repo.find_many(filter_dict, skip=skip, limit=limit)

    return {"grns": grns or [], "total": len(grns) if grns else 0}


@router.post("/grn", status_code=201)
async def create_grn(
    grn: GRNCreate,
    current_user: dict = Depends(get_current_user)
):
    """Create a new GRN"""
    grn_repo = get_grn_repository()
    po_repo = get_purchase_order_repository()

    grn_id = str(uuid.uuid4())
    grn_number = generate_grn_number(current_user.get("active_store_id"))

    # Validate PO exists
    po = None
    if po_repo:
        po = po_repo.find_by_id(grn.po_id)
        if not po:
            raise HTTPException(status_code=404, detail="Purchase order not found")

        if po.get("status") not in ["SENT", "PARTIAL"]:
            raise HTTPException(status_code=400, detail="PO is not in receivable status")

    # Calculate totals
    total_received = sum(item.received_qty for item in grn.items)
    total_accepted = sum(item.accepted_qty for item in grn.items)
    total_rejected = sum(item.rejected_qty for item in grn.items)

    if grn_repo:
        grn_repo.create({
            "grn_id": grn_id,
            "grn_number": grn_number,
            "po_id": grn.po_id,
            "po_number": po.get("po_number") if po else None,
            "vendor_id": po.get("vendor_id") if po else None,
            "vendor_name": po.get("vendor_name") if po else None,
            "store_id": current_user.get("active_store_id"),
            "vendor_invoice_no": grn.vendor_invoice_no,
            "vendor_invoice_date": grn.vendor_invoice_date,
            "items": [item.model_dump() for item in grn.items],
            "total_received": total_received,
            "total_accepted": total_accepted,
            "total_rejected": total_rejected,
            "notes": grn.notes,
            "status": "PENDING",
            "created_by": current_user.get("user_id"),
            "created_at": datetime.now().isoformat()
        })

    return {
        "grn_id": grn_id,
        "grn_number": grn_number,
        "total_received": total_received,
        "message": "GRN created"
    }


@router.get("/grn/{grn_id}")
async def get_grn(
    grn_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Get GRN details"""
    grn_repo = get_grn_repository()

    if not grn_repo:
        return {"grn_id": grn_id}

    grn = grn_repo.find_by_id(grn_id)
    if not grn:
        raise HTTPException(status_code=404, detail="GRN not found")

    return grn


@router.post("/grn/{grn_id}/accept")
async def accept_grn(
    grn_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Accept GRN and add stock"""
    grn_repo = get_grn_repository()
    stock_repo = get_stock_repository()
    po_repo = get_purchase_order_repository()

    if not grn_repo:
        return {"message": "GRN accepted, stock added"}

    grn = grn_repo.find_by_id(grn_id)
    if not grn:
        raise HTTPException(status_code=404, detail="GRN not found")

    if grn.get("status") != "PENDING":
        raise HTTPException(status_code=400, detail="GRN is not pending")

    # Add stock for accepted items
    if stock_repo:
        for item in grn.get("items", []):
            if item.get("accepted_qty", 0) > 0:
                stock_repo.add_stock(
                    store_id=grn.get("store_id"),
                    product_id=item.get("product_id"),
                    quantity=item.get("accepted_qty"),
                    source_type="GRN",
                    source_id=grn_id
                )

    # Update GRN status
    grn_repo.update(grn_id, {
        "status": "ACCEPTED",
        "accepted_at": datetime.now().isoformat(),
        "accepted_by": current_user.get("user_id")
    })

    # Update PO status
    if po_repo and grn.get("po_id"):
        po_repo.update(grn.get("po_id"), {"status": "RECEIVED"})

    return {
        "message": "GRN accepted, stock added",
        "grn_id": grn_id,
        "items_added": len([i for i in grn.get("items", []) if i.get("accepted_qty", 0) > 0])
    }


@router.post("/grn/{grn_id}/escalate")
async def escalate_grn(
    grn_id: str,
    note: str = Query(...),
    current_user: dict = Depends(get_current_user)
):
    """Escalate GRN to HQ for review"""
    grn_repo = get_grn_repository()

    if grn_repo:
        grn = grn_repo.find_by_id(grn_id)
        if not grn:
            raise HTTPException(status_code=404, detail="GRN not found")

        grn_repo.update(grn_id, {
            "status": "ESCALATED",
            "escalated_at": datetime.now().isoformat(),
            "escalated_by": current_user.get("user_id"),
            "escalation_note": note
        })

    return {"message": "GRN escalated to HQ", "grn_id": grn_id}

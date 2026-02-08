# ============================================================================
# IMS 2.0 - Supply Chain & Procurement Management
# ============================================================================
# Purchase Orders, Vendors, GRN, Stock Replenishment, Stock Audit

from fastapi import APIRouter, HTTPException, Query
from datetime import datetime, timedelta
from typing import Optional, List
from pydantic import BaseModel, Field
import random
import string

router = APIRouter(prefix="/supply-chain", tags=["supply-chain"])


# ============================================================================
# Models
# ============================================================================

class POLineItem(BaseModel):
    product_id: str
    product_name: str
    quantity: int
    unit_price: float
    total_price: float = Field(default=0)


class PurchaseOrderCreate(BaseModel):
    vendor_id: str
    items: List[POLineItem]
    delivery_date: str
    notes: Optional[str] = None


class PurchaseOrderResponse(BaseModel):
    id: str
    po_number: str
    vendor_id: str
    vendor_name: str
    items: List[POLineItem]
    status: str
    total_amount: float
    gst_amount: float
    net_amount: float
    created_at: str
    expected_delivery: str
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None


class VendorRating(BaseModel):
    vendor_id: str
    rating: float = Field(ge=1, le=5)
    comment: Optional[str] = None


class VendorPerformance(BaseModel):
    vendor_id: str
    vendor_name: str
    delivery_reliability: float
    quality_rating: float
    price_competitiveness: float
    payment_terms: str
    on_time_delivery_percentage: float
    defect_rate: float
    average_lead_days: int
    total_pos: int
    total_orders_received: int


class GRNCreate(BaseModel):
    po_id: str
    received_items: List[dict]
    quality_notes: Optional[str] = None
    discrepancies: Optional[str] = None


class GRNResponse(BaseModel):
    id: str
    grn_number: str
    po_id: str
    po_number: str
    received_at: str
    items_received: int
    quality_status: str
    created_by: str


class ReplenishmentSuggestion(BaseModel):
    product_id: str
    product_name: str
    current_stock: int
    reorder_level: int
    eoq: int
    abc_category: str
    xyz_category: str
    preferred_vendor_id: str
    preferred_vendor_name: str
    estimated_cost: float
    last_purchase_price: float
    stock_status: str


class StockAuditCreate(BaseModel):
    category: str
    zone: Optional[str] = None
    location: str
    notes: Optional[str] = None


class StockAuditResponse(BaseModel):
    id: str
    audit_number: str
    category: str
    zone: Optional[str]
    status: str
    scheduled_date: str
    created_at: str
    created_by: str
    items_counted: int


# ============================================================================
# Mock Data
# ============================================================================

MOCK_POS = [
    {
        "id": "po-001",
        "po_number": "PO-2024-001",
        "vendor_id": "v-001",
        "vendor_name": "Optical Frames Ltd",
        "items": [
            {
                "product_id": "prod-001",
                "product_name": "Frame Model A",
                "quantity": 100,
                "unit_price": 500,
                "total_price": 50000,
            }
        ],
        "status": "draft",
        "total_amount": 50000,
        "gst_amount": 9000,
        "net_amount": 59000,
        "created_at": "2024-02-01T10:00:00Z",
        "expected_delivery": "2024-02-15",
        "approved_by": None,
        "approved_at": None,
    },
    {
        "id": "po-002",
        "po_number": "PO-2024-002",
        "vendor_id": "v-002",
        "vendor_name": "Lens Manufacturers Inc",
        "items": [
            {
                "product_id": "prod-002",
                "product_name": "Premium Lens Coating",
                "quantity": 500,
                "unit_price": 300,
                "total_price": 150000,
            }
        ],
        "status": "approved",
        "total_amount": 150000,
        "gst_amount": 27000,
        "net_amount": 177000,
        "created_at": "2024-02-02T09:30:00Z",
        "expected_delivery": "2024-02-20",
        "approved_by": "Manager",
        "approved_at": "2024-02-03T14:00:00Z",
    },
]

MOCK_VENDORS = [
    {
        "id": "v-001",
        "name": "Optical Frames Ltd",
        "contact": "contact@frames.com",
        "phone": "+91-1234567890",
        "address": "123 Frame Street, Delhi",
        "delivery_reliability": 4.5,
        "quality_rating": 4.3,
        "price_competitiveness": 4.0,
        "payment_terms": "Net 30",
        "on_time_delivery_percentage": 92,
        "defect_rate": 2.5,
        "average_lead_days": 7,
        "total_pos": 24,
        "total_orders_received": 23,
    },
    {
        "id": "v-002",
        "name": "Lens Manufacturers Inc",
        "contact": "sales@lensmakers.com",
        "phone": "+91-9876543210",
        "address": "456 Lens Road, Bangalore",
        "delivery_reliability": 4.8,
        "quality_rating": 4.7,
        "price_competitiveness": 4.2,
        "payment_terms": "Net 45",
        "on_time_delivery_percentage": 96,
        "defect_rate": 1.2,
        "average_lead_days": 10,
        "total_pos": 18,
        "total_orders_received": 18,
    },
]

MOCK_GRNS = [
    {
        "id": "grn-001",
        "grn_number": "GRN-2024-001",
        "po_id": "po-001",
        "po_number": "PO-2024-001",
        "received_at": "2024-02-15T14:30:00Z",
        "items_received": 100,
        "quality_status": "passed",
        "created_by": "Store Manager",
    }
]

MOCK_STOCK_AUDIT = [
    {
        "id": "audit-001",
        "audit_number": "AUDIT-2024-001",
        "category": "Frames",
        "zone": "Zone A",
        "status": "scheduled",
        "scheduled_date": "2024-02-20",
        "created_at": "2024-02-08T10:00:00Z",
        "created_by": "Admin",
        "items_counted": 0,
    }
]


# ============================================================================
# Purchase Orders Endpoints
# ============================================================================


@router.get("/purchase-orders")
def list_purchase_orders(
    status: Optional[str] = Query(None),
    vendor_id: Optional[str] = Query(None),
    skip: int = Query(0),
    limit: int = Query(10),
):
    """List all purchase orders with filters"""
    filtered_pos = MOCK_POS
    if status:
        filtered_pos = [po for po in filtered_pos if po["status"] == status]
    if vendor_id:
        filtered_pos = [po for po in filtered_pos if po["vendor_id"] == vendor_id]

    return {
        "total": len(filtered_pos),
        "skip": skip,
        "limit": limit,
        "items": filtered_pos[skip : skip + limit],
    }


@router.post("/purchase-orders")
def create_purchase_order(po_data: PurchaseOrderCreate):
    """Create new purchase order"""
    po_id = f"po-{len(MOCK_POS) + 1:03d}"
    po_number = f"PO-{datetime.now().year}-{len(MOCK_POS) + 1:03d}"

    total_amount = sum(item.unit_price * item.quantity for item in po_data.items)
    gst_amount = total_amount * 0.18
    net_amount = total_amount + gst_amount

    vendor = next(
        (v for v in MOCK_VENDORS if v["id"] == po_data.vendor_id),
        {"name": "Unknown Vendor"},
    )

    new_po = {
        "id": po_id,
        "po_number": po_number,
        "vendor_id": po_data.vendor_id,
        "vendor_name": vendor["name"],
        "items": [item.dict() for item in po_data.items],
        "status": "draft",
        "total_amount": total_amount,
        "gst_amount": gst_amount,
        "net_amount": net_amount,
        "created_at": datetime.now().isoformat() + "Z",
        "expected_delivery": po_data.delivery_date,
        "approved_by": None,
        "approved_at": None,
    }

    MOCK_POS.append(new_po)
    return new_po


@router.get("/purchase-orders/{po_id}")
def get_purchase_order(po_id: str):
    """Get purchase order details"""
    po = next((p for p in MOCK_POS if p["id"] == po_id), None)
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    return po


@router.put("/purchase-orders/{po_id}/status")
def update_purchase_order_status(po_id: str, new_status: dict):
    """Update purchase order status"""
    po = next((p for p in MOCK_POS if p["id"] == po_id), None)
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")

    valid_statuses = ["draft", "approved", "sent", "partial_receipt", "received", "closed"]
    if new_status.get("status") not in valid_statuses:
        raise HTTPException(status_code=400, detail="Invalid status")

    po["status"] = new_status["status"]
    return po


@router.post("/purchase-orders/{po_id}/approve")
def approve_purchase_order(po_id: str, approval_data: dict):
    """Approve purchase order"""
    po = next((p for p in MOCK_POS if p["id"] == po_id), None)
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")

    po["status"] = "approved"
    po["approved_by"] = approval_data.get("approved_by", "Manager")
    po["approved_at"] = datetime.now().isoformat() + "Z"
    return po


# ============================================================================
# Vendor Endpoints
# ============================================================================


@router.get("/vendors")
def list_vendors(skip: int = Query(0), limit: int = Query(10)):
    """List all vendors with scorecards"""
    return {
        "total": len(MOCK_VENDORS),
        "skip": skip,
        "limit": limit,
        "items": MOCK_VENDORS[skip : skip + limit],
    }


@router.get("/vendors/{vendor_id}/performance")
def get_vendor_performance(vendor_id: str):
    """Get vendor performance metrics"""
    vendor = next(
        (v for v in MOCK_VENDORS if v["id"] == vendor_id), None
    )
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")

    return {
        "vendor_id": vendor_id,
        "vendor_name": vendor["name"],
        "delivery_reliability": vendor["delivery_reliability"],
        "quality_rating": vendor["quality_rating"],
        "price_competitiveness": vendor["price_competitiveness"],
        "payment_terms": vendor["payment_terms"],
        "on_time_delivery_percentage": vendor["on_time_delivery_percentage"],
        "defect_rate": vendor["defect_rate"],
        "average_lead_days": vendor["average_lead_days"],
        "total_pos": vendor["total_pos"],
        "total_orders_received": vendor["total_orders_received"],
    }


@router.post("/vendors/{vendor_id}/rate")
def rate_vendor(vendor_id: str, rating_data: VendorRating):
    """Rate vendor"""
    vendor = next(
        (v for v in MOCK_VENDORS if v["id"] == vendor_id), None
    )
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")

    return {
        "status": "success",
        "message": f"Vendor {vendor['name']} rated with {rating_data.rating} stars",
        "vendor_id": vendor_id,
        "rating": rating_data.rating,
    }


# ============================================================================
# Goods Receipt Note (GRN) Endpoints
# ============================================================================


@router.post("/grn")
def create_grn(grn_data: GRNCreate):
    """Create goods receipt note against purchase order"""
    po = next((p for p in MOCK_POS if p["id"] == grn_data.po_id), None)
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")

    grn_id = f"grn-{len(MOCK_GRNS) + 1:03d}"
    grn_number = f"GRN-{datetime.now().year}-{len(MOCK_GRNS) + 1:03d}"

    new_grn = {
        "id": grn_id,
        "grn_number": grn_number,
        "po_id": grn_data.po_id,
        "po_number": po["po_number"],
        "received_at": datetime.now().isoformat() + "Z",
        "items_received": sum(item.get("quantity", 0) for item in grn_data.received_items),
        "quality_status": "passed",
        "created_by": "Store Manager",
        "quality_notes": grn_data.quality_notes,
        "discrepancies": grn_data.discrepancies,
    }

    MOCK_GRNS.append(new_grn)
    po["status"] = "received"
    return new_grn


@router.get("/grn/{grn_id}")
def get_grn(grn_id: str):
    """Get goods receipt note details"""
    grn = next((g for g in MOCK_GRNS if g["id"] == grn_id), None)
    if not grn:
        raise HTTPException(status_code=404, detail="GRN not found")
    return grn


# ============================================================================
# Stock Replenishment Endpoints
# ============================================================================


@router.get("/replenishment/suggestions")
def get_replenishment_suggestions():
    """Get auto-generated reorder suggestions based on stock levels"""
    suggestions = [
        {
            "product_id": "prod-001",
            "product_name": "Frame Model A",
            "current_stock": 25,
            "reorder_level": 50,
            "eoq": 150,
            "abc_category": "A",
            "xyz_category": "Y",
            "preferred_vendor_id": "v-001",
            "preferred_vendor_name": "Optical Frames Ltd",
            "estimated_cost": 75000,
            "last_purchase_price": 500,
            "stock_status": "low",
        },
        {
            "product_id": "prod-002",
            "product_name": "Premium Lens Coating",
            "current_stock": 120,
            "reorder_level": 100,
            "eoq": 500,
            "abc_category": "A",
            "xyz_category": "Z",
            "preferred_vendor_id": "v-002",
            "preferred_vendor_name": "Lens Manufacturers Inc",
            "estimated_cost": 150000,
            "last_purchase_price": 300,
            "stock_status": "normal",
        },
    ]
    return {"total": len(suggestions), "items": suggestions}


@router.get("/replenishment/abc-analysis")
def get_abc_analysis():
    """Get ABC/XYZ classification for inventory management"""
    return {
        "analysis": {
            "A": {
                "name": "High Priority",
                "percentage": 20,
                "count": 45,
                "description": "High-value items requiring regular monitoring",
                "items": [
                    {
                        "product_id": "prod-001",
                        "product_name": "Frame Model A",
                        "abc_category": "A",
                        "xyz_category": "Y",
                        "annual_value": 500000,
                    }
                ],
            },
            "B": {
                "name": "Medium Priority",
                "percentage": 30,
                "count": 68,
                "description": "Moderate-value items with periodic review",
                "items": [],
            },
            "C": {
                "name": "Low Priority",
                "percentage": 50,
                "count": 187,
                "description": "Low-value items with minimal control",
                "items": [],
            },
        },
        "xyz_classification": {
            "X": {
                "name": "Predictable",
                "percentage": 40,
                "description": "Stable demand pattern",
            },
            "Y": {
                "name": "Moderate",
                "percentage": 35,
                "description": "Variable demand pattern",
            },
            "Z": {
                "name": "Unpredictable",
                "percentage": 25,
                "description": "Highly unpredictable demand",
            },
        },
    }


@router.post("/replenishment/generate-pos")
def generate_replenishment_pos(generation_data: dict):
    """Bulk PO generation based on reorder suggestions"""
    vendor_id = generation_data.get("vendor_id")
    items = generation_data.get("items", [])

    if not vendor_id or not items:
        raise HTTPException(
            status_code=400,
            detail="vendor_id and items are required"
        )

    total_amount = sum(item.get("quantity", 0) * item.get("unit_price", 0) for item in items)
    gst_amount = total_amount * 0.18
    net_amount = total_amount + gst_amount

    vendor = next(
        (v for v in MOCK_VENDORS if v["id"] == vendor_id),
        {"name": "Unknown Vendor"},
    )

    po_ids = []
    for i in range(len(items)):
        po_id = f"po-bulk-{len(MOCK_POS) + i + 1:03d}"
        po_number = f"PO-BULK-{datetime.now().year}-{len(MOCK_POS) + i + 1:03d}"

        new_po = {
            "id": po_id,
            "po_number": po_number,
            "vendor_id": vendor_id,
            "vendor_name": vendor["name"],
            "items": [items[i]],
            "status": "draft",
            "total_amount": items[i].get("quantity", 0) * items[i].get("unit_price", 0),
            "gst_amount": (items[i].get("quantity", 0) * items[i].get("unit_price", 0)) * 0.18,
            "net_amount": (items[i].get("quantity", 0) * items[i].get("unit_price", 0)) * 1.18,
            "created_at": datetime.now().isoformat() + "Z",
            "expected_delivery": (datetime.now() + timedelta(days=7)).isoformat().split("T")[0],
            "approved_by": None,
            "approved_at": None,
        }
        MOCK_POS.append(new_po)
        po_ids.append(po_id)

    return {
        "status": "success",
        "message": f"{len(po_ids)} purchase orders generated",
        "po_ids": po_ids,
        "total_amount": total_amount,
        "gst_amount": gst_amount,
        "net_amount": net_amount,
    }


# ============================================================================
# Stock Audit Endpoints
# ============================================================================


@router.post("/audits")
def create_stock_audit(audit_data: StockAuditCreate):
    """Create stock audit"""
    audit_id = f"audit-{len(MOCK_STOCK_AUDIT) + 1:03d}"
    audit_number = f"AUDIT-{datetime.now().year}-{len(MOCK_STOCK_AUDIT) + 1:03d}"

    new_audit = {
        "id": audit_id,
        "audit_number": audit_number,
        "category": audit_data.category,
        "zone": audit_data.zone,
        "status": "scheduled",
        "scheduled_date": (datetime.now() + timedelta(days=7)).isoformat().split("T")[0],
        "created_at": datetime.now().isoformat() + "Z",
        "created_by": "Admin",
        "items_counted": 0,
        "location": audit_data.location,
        "notes": audit_data.notes,
    }

    MOCK_STOCK_AUDIT.append(new_audit)
    return new_audit


@router.get("/audits")
def list_stock_audits(status: Optional[str] = Query(None)):
    """List stock audits"""
    audits = MOCK_STOCK_AUDIT
    if status:
        audits = [a for a in audits if a["status"] == status]
    return {"total": len(audits), "items": audits}


@router.get("/audits/{audit_id}")
def get_stock_audit(audit_id: str):
    """Get stock audit details"""
    audit = next((a for a in MOCK_STOCK_AUDIT if a["id"] == audit_id), None)
    if not audit:
        raise HTTPException(status_code=404, detail="Audit not found")
    return audit


@router.put("/audits/{audit_id}/status")
def update_audit_status(audit_id: str, status_data: dict):
    """Update audit status"""
    audit = next((a for a in MOCK_STOCK_AUDIT if a["id"] == audit_id), None)
    if not audit:
        raise HTTPException(status_code=404, detail="Audit not found")

    audit["status"] = status_data.get("status", audit["status"])
    return audit


@router.post("/audit/start")
def start_stock_count(count_data: dict):
    """Start stock count for audit"""
    audit_id = count_data.get("audit_id")
    audit = next((a for a in MOCK_STOCK_AUDIT if a["id"] == audit_id), None)
    if not audit:
        raise HTTPException(status_code=404, detail="Audit not found")

    audit["status"] = "in_progress"
    audit["started_at"] = datetime.now().isoformat() + "Z"
    return {
        "status": "success",
        "message": "Stock count started",
        "audit_id": audit_id,
    }


@router.put("/audit/{audit_id}/count")
def submit_count(audit_id: str, count_data: dict):
    """Submit physical count for audit"""
    audit = next((a for a in MOCK_STOCK_AUDIT if a["id"] == audit_id), None)
    if not audit:
        raise HTTPException(status_code=404, detail="Audit not found")

    items_counted = count_data.get("items_counted", 0)
    variance_percentage = count_data.get("variance_percentage", 0)

    audit["items_counted"] = items_counted
    audit["variance_percentage"] = variance_percentage
    audit["status"] = "completed"
    audit["completed_at"] = datetime.now().isoformat() + "Z"

    return {
        "status": "success",
        "message": "Count submitted and audit completed",
        "audit_id": audit_id,
        "items_counted": items_counted,
        "variance_percentage": variance_percentage,
    }


@router.get("/audit/{audit_id}/variance")
def get_variance_report(audit_id: str):
    """Get variance report for audit"""
    audit = next((a for a in MOCK_STOCK_AUDIT if a["id"] == audit_id), None)
    if not audit:
        raise HTTPException(status_code=404, detail="Audit not found")

    return {
        "audit_id": audit_id,
        "audit_number": audit["audit_number"],
        "category": audit["category"],
        "total_items_system": 245,
        "total_items_physical": 238,
        "variance": 7,
        "variance_percentage": 2.86,
        "variance_value": 8500,
        "shrinkage_percentage": 0.8,
        "discrepancies": [
            {
                "product_id": "prod-001",
                "product_name": "Frame Model A",
                "system_quantity": 100,
                "physical_quantity": 97,
                "variance": -3,
            },
            {
                "product_id": "prod-003",
                "product_name": "Lens Case",
                "system_quantity": 145,
                "physical_quantity": 141,
                "variance": -4,
            },
        ],
    }

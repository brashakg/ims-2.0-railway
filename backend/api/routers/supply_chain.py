# ============================================================================
# IMS 2.0 - Supply Chain & Procurement Management
# ============================================================================
# Purchase Orders, Vendors, GRN, Stock Replenishment, Stock Audit.
#
# NOTE (May 2026 cleanup): this router previously returned hardcoded mock
# data (MOCK_POS / MOCK_VENDORS / MOCK_GRNS / MOCK_STOCK_AUDIT) that lived in
# module-level lists and reset on every restart. Nothing in the frontend
# consumes /supply-chain — the app uses the DB-backed /vendors and /inventory
# routers — so the mock data was pure scaffolding that could mislead anyone
# poking at the API. Every endpoint now reads/writes the real MongoDB
# collections via the shared repositories, fail-soft to empty when the DB is
# unavailable. The canonical PO / vendor / GRN implementation lives in
# vendors.py; this router is a thin compatibility surface over the same data.

from fastapi import APIRouter, HTTPException, Query, Depends
from datetime import datetime, timedelta
from typing import Optional, List
from pydantic import BaseModel, Field
import uuid

from .auth import get_current_user
from ..dependencies import (
    get_purchase_order_repository,
    get_vendor_repository,
    get_grn_repository,
)

# Mount prefix is applied in api/main.py (prefix="/api/v1/supply-chain").
router = APIRouter(tags=["supply-chain"])


def _get_db():
    """Raw MongoDB handle for collections without a dedicated repository
    (stock audits). Returns None when the DB is unavailable so callers can
    fail-soft."""
    try:
        from ..dependencies import get_db

        conn = get_db()
        if conn is not None and conn.is_connected:
            return conn.db
    except Exception:
        pass
    return None


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
    delivery_store_id: Optional[str] = None
    notes: Optional[str] = None


class VendorRating(BaseModel):
    vendor_id: str
    rating: float = Field(ge=1, le=5)
    comment: Optional[str] = None


class GRNCreate(BaseModel):
    po_id: str
    received_items: List[dict]
    quality_notes: Optional[str] = None
    discrepancies: Optional[str] = None


class StockAuditCreate(BaseModel):
    category: str
    zone: Optional[str] = None
    location: str
    notes: Optional[str] = None


# ============================================================================
# Helpers
# ============================================================================


def _gst(amount: float) -> float:
    return round(amount * 0.18, 2)


# ============================================================================
# Root
# ============================================================================


@router.get("")
@router.get("/")
async def get_supply_chain_root():
    """Root endpoint for supply chain summary"""
    return {
        "module": "supply_chain",
        "status": "active",
        "message": "supply chain overview endpoint ready",
    }


# ============================================================================
# Purchase Orders (real `purchase_orders` collection)
# ============================================================================


@router.get("/purchase-orders")
async def list_purchase_orders(
    status: Optional[str] = Query(None),
    vendor_id: Optional[str] = Query(None),
    store_id: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """List purchase orders from the real DB (was MOCK_POS)."""
    po_repo = get_purchase_order_repository()
    if po_repo is None:
        return {"total": 0, "skip": skip, "limit": limit, "items": []}

    filter_dict: dict = {}
    if status:
        filter_dict["status"] = status
    if vendor_id:
        filter_dict["vendor_id"] = vendor_id
    if store_id:
        filter_dict["delivery_store_id"] = store_id

    items = po_repo.find_many(filter_dict, skip=skip, limit=limit) or []
    return {
        "total": po_repo.count(filter_dict),
        "skip": skip,
        "limit": limit,
        "items": items,
    }


@router.post("/purchase-orders")
async def create_purchase_order(
    po_data: PurchaseOrderCreate,
    current_user: dict = Depends(get_current_user),
):
    """Create a real purchase order."""
    po_repo = get_purchase_order_repository()
    vendor_repo = get_vendor_repository()
    if po_repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    vendor_name = None
    if vendor_repo is not None:
        vendor = vendor_repo.find_by_id(po_data.vendor_id)
        vendor_name = (
            vendor.get("trade_name") or vendor.get("legal_name")
            if vendor
            else None
        )

    subtotal = sum(item.unit_price * item.quantity for item in po_data.items)
    gst_amount = _gst(subtotal)
    po_id = str(uuid.uuid4())
    po_number = f"PO-{datetime.now().year}-{po_id[:6].upper()}"

    doc = {
        "po_id": po_id,
        "po_number": po_number,
        "vendor_id": po_data.vendor_id,
        "vendor_name": vendor_name,
        "delivery_store_id": po_data.delivery_store_id
        or current_user.get("active_store_id"),
        "items": [item.model_dump() for item in po_data.items],
        "subtotal": subtotal,
        "tax_amount": gst_amount,
        "total_amount": round(subtotal + gst_amount, 2),
        "expected_date": po_data.delivery_date,
        "notes": po_data.notes,
        "status": "DRAFT",
        "created_by": current_user.get("user_id"),
    }
    created = po_repo.create(doc)
    return created or doc


@router.get("/purchase-orders/{po_id}")
async def get_purchase_order(
    po_id: str, current_user: dict = Depends(get_current_user)
):
    """Get a purchase order by id."""
    po_repo = get_purchase_order_repository()
    if po_repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    po = po_repo.find_by_id(po_id)
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    return po


@router.put("/purchase-orders/{po_id}/status")
async def update_purchase_order_status(
    po_id: str, new_status: dict, current_user: dict = Depends(get_current_user)
):
    """Update purchase order status."""
    po_repo = get_purchase_order_repository()
    if po_repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    po = po_repo.find_by_id(po_id)
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")

    valid_statuses = [
        "DRAFT",
        "APPROVED",
        "SENT",
        "PARTIAL_RECEIPT",
        "RECEIVED",
        "CLOSED",
        "CANCELLED",
    ]
    status_value = str(new_status.get("status", "")).upper()
    if status_value not in valid_statuses:
        raise HTTPException(status_code=400, detail="Invalid status")

    po_repo.update(po_id, {"status": status_value})
    return po_repo.find_by_id(po_id)


@router.post("/purchase-orders/{po_id}/approve")
async def approve_purchase_order(
    po_id: str, approval_data: dict, current_user: dict = Depends(get_current_user)
):
    """Approve a purchase order."""
    po_repo = get_purchase_order_repository()
    if po_repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    po = po_repo.find_by_id(po_id)
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")

    po_repo.update(
        po_id,
        {
            "status": "APPROVED",
            "approved_by": approval_data.get(
                "approved_by", current_user.get("user_id")
            ),
            "approved_at": datetime.now().isoformat(),
        },
    )
    return po_repo.find_by_id(po_id)


# ============================================================================
# Vendors (real `vendors` collection)
# ============================================================================


@router.get("/vendors")
async def list_vendors(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """List vendors from the real DB (was MOCK_VENDORS)."""
    vendor_repo = get_vendor_repository()
    if vendor_repo is None:
        return {"total": 0, "skip": skip, "limit": limit, "items": []}
    items = vendor_repo.find_many({}, skip=skip, limit=limit) or []
    return {
        "total": vendor_repo.count({}),
        "skip": skip,
        "limit": limit,
        "items": items,
    }


@router.get("/vendors/{vendor_id}/performance")
async def get_vendor_performance(
    vendor_id: str, current_user: dict = Depends(get_current_user)
):
    """Vendor performance metrics, derived from the stored vendor doc.
    Missing metrics are reported as null rather than fabricated."""
    vendor_repo = get_vendor_repository()
    if vendor_repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    vendor = vendor_repo.find_by_id(vendor_id)
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")

    return {
        "vendor_id": vendor_id,
        "vendor_name": vendor.get("trade_name") or vendor.get("legal_name"),
        "delivery_reliability": vendor.get("delivery_reliability"),
        "quality_rating": vendor.get("quality_rating"),
        "price_competitiveness": vendor.get("price_competitiveness"),
        "payment_terms": vendor.get("payment_terms"),
        "on_time_delivery_percentage": vendor.get("on_time_delivery_percentage"),
        "defect_rate": vendor.get("defect_rate"),
        "average_lead_days": vendor.get("average_lead_days") or vendor.get("credit_days"),
        "total_pos": vendor.get("total_pos", 0),
        "total_orders_received": vendor.get("total_orders_received", 0),
    }


@router.post("/vendors/{vendor_id}/rate")
async def rate_vendor(
    vendor_id: str,
    rating_data: VendorRating,
    current_user: dict = Depends(get_current_user),
):
    """Persist a vendor rating onto the vendor doc."""
    vendor_repo = get_vendor_repository()
    if vendor_repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    vendor = vendor_repo.find_by_id(vendor_id)
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")

    vendor_repo.update(
        vendor_id,
        {
            "quality_rating": rating_data.rating,
            "last_rating_comment": rating_data.comment,
            "last_rated_by": current_user.get("user_id"),
            "last_rated_at": datetime.now().isoformat(),
        },
    )
    return {
        "status": "success",
        "vendor_id": vendor_id,
        "rating": rating_data.rating,
    }


# ============================================================================
# Goods Receipt Note (GRN) — real `grns` collection
# ============================================================================


@router.post("/grn")
async def create_grn(
    grn_data: GRNCreate, current_user: dict = Depends(get_current_user)
):
    """Create a goods receipt note against a purchase order."""
    po_repo = get_purchase_order_repository()
    grn_repo = get_grn_repository()
    if po_repo is None or grn_repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    po = po_repo.find_by_id(grn_data.po_id)
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")

    grn_id = str(uuid.uuid4())
    grn_number = f"GRN-{datetime.now().year}-{grn_id[:6].upper()}"
    doc = {
        "grn_id": grn_id,
        "grn_number": grn_number,
        "po_id": grn_data.po_id,
        "po_number": po.get("po_number"),
        "received_at": datetime.now().isoformat(),
        "items_received": sum(
            int(item.get("quantity", 0) or 0) for item in grn_data.received_items
        ),
        "items": grn_data.received_items,
        "quality_status": "PASSED",
        "quality_notes": grn_data.quality_notes,
        "discrepancies": grn_data.discrepancies,
        "created_by": current_user.get("user_id"),
    }
    created = grn_repo.create(doc)
    po_repo.update(grn_data.po_id, {"status": "RECEIVED"})
    return created or doc


@router.get("/grn/{grn_id}")
async def get_grn(grn_id: str, current_user: dict = Depends(get_current_user)):
    """Get a goods receipt note by id."""
    grn_repo = get_grn_repository()
    if grn_repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    grn = grn_repo.find_by_id(grn_id)
    if not grn:
        raise HTTPException(status_code=404, detail="GRN not found")
    return grn


# ============================================================================
# Stock Replenishment (computed from real products; no fabricated numbers)
# ============================================================================


@router.get("/replenishment/suggestions")
async def get_replenishment_suggestions(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Reorder suggestions computed from real product stock levels: any
    active product whose stock_quantity is at/below its reorder_point.
    Fail-soft empty when the DB is unavailable (was a hardcoded list)."""
    db = _get_db()
    if db is None:
        return {"total": 0, "items": []}

    active_store = store_id or current_user.get("active_store_id")
    try:
        products_coll = db.get_collection("products")
        query: dict = {"is_active": {"$ne": False}, "reorder_point": {"$gt": 0}}
        if active_store:
            query["store_id"] = active_store

        suggestions = []
        for p in products_coll.find(query, {"_id": 0}).limit(200):
            stock = int(p.get("stock_quantity", 0) or 0)
            reorder = int(p.get("reorder_point", 0) or 0)
            if stock <= reorder:
                suggestions.append(
                    {
                        "product_id": p.get("sku") or p.get("barcode") or "",
                        "product_name": p.get("name", ""),
                        "current_stock": stock,
                        "reorder_level": reorder,
                        "last_purchase_price": p.get("cost_price", 0) or 0,
                        "estimated_cost": round(
                            (reorder * 2 - stock) * (p.get("cost_price", 0) or 0), 2
                        ),
                        "stock_status": "out" if stock <= 0 else "low",
                    }
                )
        return {"total": len(suggestions), "items": suggestions}
    except Exception:
        return {"total": 0, "items": []}


@router.get("/replenishment/abc-analysis")
async def get_abc_analysis(current_user: dict = Depends(get_current_user)):
    """ABC/XYZ classification. Real annual-value modelling is not yet
    implemented, so this returns an empty (not fabricated) structure instead
    of the previous hardcoded percentages."""
    return {
        "analysis": {
            "A": {"name": "High Priority", "count": 0, "items": []},
            "B": {"name": "Medium Priority", "count": 0, "items": []},
            "C": {"name": "Low Priority", "count": 0, "items": []},
        },
        "xyz_classification": {
            "X": {"name": "Predictable"},
            "Y": {"name": "Moderate"},
            "Z": {"name": "Unpredictable"},
        },
        "note": "ABC/XYZ modelling not yet implemented; awaiting sales-value pipeline.",
    }


@router.post("/replenishment/generate-pos")
async def generate_replenishment_pos(
    generation_data: dict, current_user: dict = Depends(get_current_user)
):
    """Bulk-create real purchase orders from reorder suggestions."""
    po_repo = get_purchase_order_repository()
    vendor_repo = get_vendor_repository()
    if po_repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    vendor_id = generation_data.get("vendor_id")
    items = generation_data.get("items", [])
    if not vendor_id or not items:
        raise HTTPException(status_code=400, detail="vendor_id and items are required")

    vendor_name = None
    if vendor_repo is not None:
        vendor = vendor_repo.find_by_id(vendor_id)
        vendor_name = (
            vendor.get("trade_name") or vendor.get("legal_name") if vendor else None
        )

    po_ids = []
    for item in items:
        subtotal = item.get("quantity", 0) * item.get("unit_price", 0)
        po_id = str(uuid.uuid4())
        po_repo.create(
            {
                "po_id": po_id,
                "po_number": f"PO-{datetime.now().year}-{po_id[:6].upper()}",
                "vendor_id": vendor_id,
                "vendor_name": vendor_name,
                "delivery_store_id": current_user.get("active_store_id"),
                "items": [item],
                "subtotal": subtotal,
                "tax_amount": _gst(subtotal),
                "total_amount": round(subtotal + _gst(subtotal), 2),
                "expected_date": (datetime.now() + timedelta(days=7))
                .isoformat()
                .split("T")[0],
                "status": "DRAFT",
                "created_by": current_user.get("user_id"),
            }
        )
        po_ids.append(po_id)

    return {
        "status": "success",
        "message": f"{len(po_ids)} purchase orders generated",
        "po_ids": po_ids,
    }


# ============================================================================
# Stock Audit (real `stock_audits` collection)
# ============================================================================


@router.post("/audits")
async def create_stock_audit(
    audit_data: StockAuditCreate, current_user: dict = Depends(get_current_user)
):
    """Create a stock audit."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    audit_id = str(uuid.uuid4())
    now = datetime.now()
    doc = {
        "id": audit_id,
        "audit_number": f"AUDIT-{now.year}-{audit_id[:6].upper()}",
        "category": audit_data.category,
        "zone": audit_data.zone,
        "location": audit_data.location,
        "notes": audit_data.notes,
        "status": "scheduled",
        "scheduled_date": (now + timedelta(days=7)).isoformat().split("T")[0],
        "created_at": now.isoformat(),
        "created_by": current_user.get("user_id"),
        "store_id": current_user.get("active_store_id"),
        "items_counted": 0,
    }
    try:
        db.get_collection("stock_audits").insert_one(dict(doc))
    except Exception:
        pass
    return doc


@router.get("/audits")
async def list_stock_audits(
    status: Optional[str] = Query(None),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """List stock audits."""
    db = _get_db()
    if db is None:
        return {"total": 0, "items": []}
    active_store = store_id or current_user.get("active_store_id")
    try:
        query: dict = {}
        if status:
            query["status"] = status
        if active_store:
            query["store_id"] = active_store
        audits = list(db.get_collection("stock_audits").find(query, {"_id": 0}).limit(100))
        return {"total": len(audits), "items": audits}
    except Exception:
        return {"total": 0, "items": []}


@router.get("/audits/{audit_id}")
async def get_stock_audit(
    audit_id: str, current_user: dict = Depends(get_current_user)
):
    """Get a stock audit by id."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    audit = db.get_collection("stock_audits").find_one({"id": audit_id}, {"_id": 0})
    if not audit:
        raise HTTPException(status_code=404, detail="Audit not found")
    return audit


@router.put("/audits/{audit_id}/status")
async def update_audit_status(
    audit_id: str, status_data: dict, current_user: dict = Depends(get_current_user)
):
    """Update a stock audit's status."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    coll = db.get_collection("stock_audits")
    if coll.find_one({"id": audit_id}, {"_id": 1}) is None:
        raise HTTPException(status_code=404, detail="Audit not found")
    coll.update_one({"id": audit_id}, {"$set": {"status": status_data.get("status")}})
    return coll.find_one({"id": audit_id}, {"_id": 0})


@router.post("/audit/start")
async def start_stock_count(
    count_data: dict, current_user: dict = Depends(get_current_user)
):
    """Start the physical count phase of a stock audit."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    audit_id = count_data.get("audit_id")
    coll = db.get_collection("stock_audits")
    if coll.find_one({"id": audit_id}, {"_id": 1}) is None:
        raise HTTPException(status_code=404, detail="Audit not found")
    coll.update_one(
        {"id": audit_id},
        {"$set": {"status": "in_progress", "started_at": datetime.now().isoformat()}},
    )
    return {"status": "success", "audit_id": audit_id}


@router.put("/audit/{audit_id}/count")
async def submit_count(
    audit_id: str, count_data: dict, current_user: dict = Depends(get_current_user)
):
    """Submit the physical count and close the audit."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    coll = db.get_collection("stock_audits")
    if coll.find_one({"id": audit_id}, {"_id": 1}) is None:
        raise HTTPException(status_code=404, detail="Audit not found")
    items_counted = count_data.get("items_counted", 0)
    variance_percentage = count_data.get("variance_percentage", 0)
    coll.update_one(
        {"id": audit_id},
        {
            "$set": {
                "items_counted": items_counted,
                "variance_percentage": variance_percentage,
                "status": "completed",
                "completed_at": datetime.now().isoformat(),
            }
        },
    )
    return {
        "status": "success",
        "audit_id": audit_id,
        "items_counted": items_counted,
        "variance_percentage": variance_percentage,
    }


@router.get("/audit/{audit_id}/variance")
async def get_variance_report(
    audit_id: str, current_user: dict = Depends(get_current_user)
):
    """Variance report for an audit, read from the stored audit doc.
    Returns the persisted variance fields rather than fabricated figures."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    audit = db.get_collection("stock_audits").find_one({"id": audit_id}, {"_id": 0})
    if not audit:
        raise HTTPException(status_code=404, detail="Audit not found")
    return {
        "audit_id": audit_id,
        "audit_number": audit.get("audit_number"),
        "category": audit.get("category"),
        "items_counted": audit.get("items_counted", 0),
        "variance_percentage": audit.get("variance_percentage", 0),
        "discrepancies": audit.get("discrepancies", []),
    }

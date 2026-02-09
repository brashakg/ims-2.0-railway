"""
IMS 2.0 - Inventory Router
===========================
Stock and inventory management endpoints
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import date
import uuid
from .auth import get_current_user
from ..dependencies import get_stock_repository, get_product_repository

router = APIRouter()


# ============================================================================
# SCHEMAS
# ============================================================================


class StockAddRequest(BaseModel):
    product_id: str
    quantity: int = Field(..., ge=1)
    location_code: Optional[str] = None
    batch_code: Optional[str] = None
    expiry_date: Optional[date] = None


class StockTransferRequest(BaseModel):
    from_store_id: str
    to_store_id: str
    items: List[dict]  # stock_id, quantity


class StockCountItem(BaseModel):
    product_id: str
    counted_quantity: int


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def generate_barcode(store_id: str, product_id: str) -> str:
    """Generate unique barcode for stock item"""
    short_uuid = str(uuid.uuid4())[:8].upper()
    return f"{store_id[:3]}-{short_uuid}"


# ============================================================================
# ENDPOINTS
# ============================================================================


@router.get("/stock")
async def get_stock(
    store_id: Optional[str] = Query(None),
    product_id: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    low_stock: bool = Query(False),
    current_user: dict = Depends(get_current_user),
):
    """Get stock with filtering"""
    repo = get_stock_repository()
    active_store = store_id or current_user.get("active_store_id")

    if repo:
        if low_stock:
            stock = repo.find_low_stock(active_store)
        elif product_id:
            stock = repo.find_by_product_store(product_id, active_store)
        else:
            filter_dict = {"store_id": active_store} if active_store else {}
            if category:
                filter_dict["category"] = category
            stock = repo.find_many(filter_dict, limit=100)

        return {"items": stock, "total": len(stock)}

    return {"items": [], "total": 0}


# NOTE: Specific routes MUST come before /{parameter} routes
@router.get("/low-stock")
async def get_low_stock_alerts(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get low stock alerts"""
    repo = get_stock_repository()
    active_store = store_id or current_user.get("active_store_id")

    if repo:
        items = repo.find_low_stock(active_store)
        return {"items": items}

    return {"items": []}


@router.get("/barcode/{barcode}")
async def get_stock_by_barcode_short(
    barcode: str, current_user: dict = Depends(get_current_user)
):
    """Get stock item by barcode (short path)"""
    repo = get_stock_repository()

    if repo:
        stock = repo.find_by_barcode(barcode)
        if stock:
            return stock
        raise HTTPException(status_code=404, detail="Stock item not found")

    return {"barcode": barcode}


@router.get("/expiring")
async def get_expiring_stock(
    days: int = Query(30, ge=1, le=365), current_user: dict = Depends(get_current_user)
):
    """Get stock items expiring within specified days"""
    repo = get_stock_repository()
    active_store = current_user.get("active_store_id")

    if repo:
        items = repo.find_expiring(active_store, days)
        return {"items": items}

    return {"items": []}


@router.get("/transfers")
async def list_transfers(
    store_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """List stock transfers"""
    # Transfer logic to be implemented with transfer repository
    return {"transfers": []}


@router.get("/stock-count")
async def list_stock_counts(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """List stock counts"""
    return {"counts": []}


@router.get("/stock/barcode/{barcode}")
async def get_stock_by_barcode(
    barcode: str, current_user: dict = Depends(get_current_user)
):
    """Get stock item by barcode"""
    repo = get_stock_repository()

    if repo:
        stock = repo.find_by_barcode(barcode)
        if stock:
            return stock
        raise HTTPException(status_code=404, detail="Stock item not found")

    return {"barcode": barcode}


@router.post("/stock/add")
async def add_stock(
    request: StockAddRequest, current_user: dict = Depends(get_current_user)
):
    """Add stock to inventory"""
    stock_repo = get_stock_repository()
    product_repo = get_product_repository()
    active_store = current_user.get("active_store_id")

    if stock_repo is not None and product_repo is not None:
        # Verify product exists
        product = product_repo.find_by_id(request.product_id)
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")

        # Create stock entries for each unit
        stock_items = []
        for _ in range(request.quantity):
            barcode = generate_barcode(active_store, request.product_id)
            stock_data = {
                "product_id": request.product_id,
                "store_id": active_store,
                "barcode": barcode,
                "location_code": request.location_code or "DEFAULT",
                "batch_code": request.batch_code,
                "expiry_date": (
                    request.expiry_date.isoformat() if request.expiry_date else None
                ),
                "status": "AVAILABLE",
                "is_reserved": False,
                "barcode_printed": False,
                "created_by": current_user.get("user_id"),
            }
            created = stock_repo.create(stock_data)
            if created:
                stock_items.append(created)

        return {
            "stock_ids": [s["stock_unit_id"] for s in stock_items],
            "barcodes": [s["barcode"] for s in stock_items],
            "quantity": len(stock_items),
        }

    return {"stock_id": str(uuid.uuid4()), "barcode": generate_barcode("STR", "PRD")}


@router.post("/transfers")
async def create_transfer(
    request: StockTransferRequest, current_user: dict = Depends(get_current_user)
):
    """Create a stock transfer request"""
    # Transfer creation to be implemented
    return {
        "transfer_id": str(uuid.uuid4()),
        "transfer_number": f"TRF-{uuid.uuid4().hex[:6].upper()}",
    }


@router.post("/transfers/{transfer_id}/send")
async def send_transfer(
    transfer_id: str, current_user: dict = Depends(get_current_user)
):
    """Mark transfer as sent"""
    return {"message": "Transfer sent", "transfer_id": transfer_id}


@router.post("/transfers/{transfer_id}/receive")
async def receive_transfer(
    transfer_id: str, items: List[dict], current_user: dict = Depends(get_current_user)
):
    """Receive a stock transfer"""
    return {"message": "Transfer received", "transfer_id": transfer_id}


@router.post("/stock-count/start")
async def start_stock_count(
    category: str, current_user: dict = Depends(get_current_user)
):
    """Start a stock count session"""
    return {"count_id": str(uuid.uuid4()), "category": category}


@router.post("/stock-count/{count_id}/items")
async def record_count_item(
    count_id: str, item: StockCountItem, current_user: dict = Depends(get_current_user)
):
    """Record a counted item"""
    return {"message": "Item counted", "count_id": count_id}


@router.post("/stock-count/{count_id}/complete")
async def complete_stock_count(
    count_id: str, current_user: dict = Depends(get_current_user)
):
    """Complete stock count and calculate variances"""
    return {"message": "Stock count completed", "variances": []}

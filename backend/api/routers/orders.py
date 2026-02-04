"""
IMS 2.0 - Orders Router
========================
Sales order management endpoints
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime, date
from enum import Enum

from .auth import get_current_user

router = APIRouter()


# ============================================================================
# ENUMS & SCHEMAS
# ============================================================================

class OrderStatus(str, Enum):
    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"
    PROCESSING = "PROCESSING"
    READY = "READY"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"

class PaymentMethod(str, Enum):
    CASH = "CASH"
    UPI = "UPI"
    CARD = "CARD"
    BANK_TRANSFER = "BANK_TRANSFER"
    EMI = "EMI"
    CREDIT = "CREDIT"
    GIFT_VOUCHER = "GIFT_VOUCHER"

class OrderItemCreate(BaseModel):
    item_type: str  # FRAME, LENS, CONTACT_LENS, ACCESSORY, SERVICE
    product_id: str
    quantity: int = Field(default=1, ge=1)
    unit_price: float = Field(..., ge=0)
    discount_percent: float = Field(default=0, ge=0, le=100)
    prescription_id: Optional[str] = None
    lens_options: Optional[dict] = None  # coating, tint, etc.

class PaymentCreate(BaseModel):
    method: PaymentMethod
    amount: float = Field(..., gt=0)
    reference: Optional[str] = None

class OrderCreate(BaseModel):
    customer_id: str
    patient_id: Optional[str] = None
    items: List[OrderItemCreate]
    notes: Optional[str] = None
    expected_delivery_days: int = Field(default=7, ge=1)

class OrderUpdate(BaseModel):
    notes: Optional[str] = None
    expected_delivery: Optional[date] = None


# ============================================================================
# ENDPOINTS
# ============================================================================

@router.get("/")
async def list_orders(
    store_id: Optional[str] = Query(None),
    status: Optional[OrderStatus] = Query(None),
    customer_id: Optional[str] = Query(None),
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(get_current_user)
):
    """
    List orders with filters
    """
    # Use active store from token if not provided
    if not store_id:
        store_id = current_user.get("active_store_id")

    # TODO: Implement with database
    return {"orders": [], "total": 0}


# NOTE: Specific routes MUST come before /{order_id} to avoid being matched as order_id
@router.get("/pending/delivery")
async def get_pending_deliveries(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user)
):
    """
    Get orders pending delivery
    """
    return {"orders": []}


@router.get("/unpaid/list")
async def get_unpaid_orders(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user)
):
    """
    Get unpaid/partially paid orders
    """
    return {"orders": []}


@router.get("/overdue/list")
async def get_overdue_orders(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user)
):
    """
    Get overdue orders (past expected delivery)
    """
    return {"orders": []}


@router.post("/", status_code=201)
async def create_order(
    order: OrderCreate,
    current_user: dict = Depends(get_current_user)
):
    """
    Create new sales order
    """
    store_id = current_user.get("active_store_id")
    salesperson_id = current_user.get("user_id")
    
    # Validate items
    if not order.items:
        raise HTTPException(status_code=400, detail="Order must have at least one item")
    
    # TODO: Validate discount against user's discount_cap
    # TODO: Validate prescription for lens items
    # TODO: Check stock availability
    # TODO: Apply MRP/Offer price logic
    
    return {
        "order_id": "new-order-id",
        "order_number": "BV-BKR-2024-0001",
        "status": "DRAFT",
        "message": "Order created successfully"
    }


@router.get("/{order_id}")
async def get_order(
    order_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Get order details
    """
    # TODO: Implement with database
    return {"order_id": order_id}


@router.put("/{order_id}")
async def update_order(
    order_id: str,
    order: OrderUpdate,
    current_user: dict = Depends(get_current_user)
):
    """
    Update order (only DRAFT orders)
    """
    return {"order_id": order_id, "message": "Order updated"}


@router.post("/{order_id}/items")
async def add_order_item(
    order_id: str,
    item: OrderItemCreate,
    current_user: dict = Depends(get_current_user)
):
    """
    Add item to order (only DRAFT orders)
    """
    return {"message": "Item added to order"}


@router.delete("/{order_id}/items/{item_id}")
async def remove_order_item(
    order_id: str,
    item_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Remove item from order (only DRAFT orders)
    """
    return {"message": "Item removed from order"}


@router.post("/{order_id}/confirm")
async def confirm_order(
    order_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Confirm order (DRAFT -> CONFIRMED)
    """
    # TODO: Reserve stock
    # TODO: Create workshop job if lenses involved
    return {"order_id": order_id, "status": "CONFIRMED"}


@router.post("/{order_id}/payments")
async def add_payment(
    order_id: str,
    payment: PaymentCreate,
    current_user: dict = Depends(get_current_user)
):
    """
    Add payment to order
    """
    # TODO: Validate cashier role for CASH
    # TODO: Update payment status
    return {
        "payment_id": "new-payment-id",
        "message": "Payment recorded"
    }


@router.post("/{order_id}/ready")
async def mark_ready(
    order_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Mark order as ready for delivery
    """
    return {"order_id": order_id, "status": "READY"}


@router.post("/{order_id}/deliver")
async def deliver_order(
    order_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Deliver order to customer
    """
    # TODO: Verify full payment for non-credit customers
    return {"order_id": order_id, "status": "DELIVERED"}


@router.post("/{order_id}/cancel")
async def cancel_order(
    order_id: str,
    reason: str = Query(..., min_length=10),
    current_user: dict = Depends(get_current_user)
):
    """
    Cancel order
    """
    # TODO: Release reserved stock
    # TODO: Process refund if paid
    return {"order_id": order_id, "status": "CANCELLED"}


@router.get("/{order_id}/invoice")
async def get_invoice(
    order_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Get/generate invoice for order
    """
    return {"invoice_number": "BV/INV/2024/0001", "order_id": order_id}

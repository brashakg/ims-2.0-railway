"""
IMS 2.0 - Orders Router
========================
Sales order management endpoints
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime, date, timedelta
from enum import Enum
import uuid

from .auth import get_current_user
from ..dependencies import (
    get_order_repository,
    get_customer_repository,
    get_stock_repository,
)

router = APIRouter()


# ============================================================================
# HELPER: Convert snake_case to camelCase for frontend compatibility
# ============================================================================


def to_camel_case(snake_str: str) -> str:
    """Convert snake_case to camelCase"""
    components = snake_str.split("_")
    return components[0] + "".join(x.title() for x in components[1:])


def order_to_frontend(order: dict) -> dict:
    """Convert order dict from snake_case to camelCase for frontend"""
    if not order:
        return order

    # Map of snake_case keys to camelCase
    key_map = {
        "order_id": "id",
        "order_number": "orderNumber",
        "store_id": "storeId",
        "customer_id": "customerId",
        "customer_name": "customerName",
        "customer_phone": "customerPhone",
        "patient_id": "patientId",
        "patient_name": "patientName",
        "salesperson_id": "salespersonId",
        "grand_total": "grandTotal",
        "tax_amount": "taxAmount",
        "tax_rate": "taxRate",
        "amount_paid": "amountPaid",
        "balance_due": "balanceDue",
        "payment_status": "paymentStatus",
        "total_discount": "totalDiscount",
        "expected_delivery": "expectedDelivery",
        "created_at": "createdAt",
        "updated_at": "updatedAt",
        "delivered_at": "deliveredAt",
        "cancelled_at": "cancelledAt",
        "cancelled_by": "cancelledBy",
        "cancellation_reason": "cancellationReason",
        "invoice_number": "invoiceNumber",
        "invoice_date": "invoiceDate",
        "created_by": "createdBy",
    }

    # Status field needs special handling - backend uses 'status', frontend uses 'orderStatus'
    result = {}
    for key, value in order.items():
        if key == "status":
            result["orderStatus"] = value
        elif key == "items" and isinstance(value, list):
            # Convert item fields
            result["items"] = [item_to_frontend(item) for item in value]
        elif key == "payments" and isinstance(value, list):
            # Convert payment fields
            result["payments"] = [payment_to_frontend(p) for p in value]
        elif key in key_map:
            result[key_map[key]] = value
        else:
            # Keep other fields as-is (already camelCase or no mapping needed)
            result[key] = value

    return result


def item_to_frontend(item: dict) -> dict:
    """Convert order item from snake_case to camelCase"""
    if not item:
        return item

    key_map = {
        "item_id": "id",
        "item_type": "itemType",
        "product_id": "productId",
        "product_name": "productName",
        "unit_price": "unitPrice",
        "discount_percent": "discountPercent",
        "discount_amount": "discountAmount",
        "item_total": "finalPrice",
        "final_price": "finalPrice",
        "prescription_id": "prescriptionId",
        "lens_options": "lensOptions",
    }

    result = {}
    for key, value in item.items():
        if key in key_map:
            result[key_map[key]] = value
        else:
            result[key] = value
    return result


def payment_to_frontend(payment: dict) -> dict:
    """Convert payment from snake_case to camelCase"""
    if not payment:
        return payment

    key_map = {
        "payment_id": "id",
        "received_by": "receivedBy",
        "received_at": "paidAt",
    }

    result = {}
    for key, value in payment.items():
        if key in key_map:
            result[key_map[key]] = value
        else:
            result[key] = value
    return result


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
    current_user: dict = Depends(get_current_user),
):
    """List orders with filters"""
    repo = get_order_repository()
    active_store = store_id or current_user.get("active_store_id")

    if repo is not None:
        if customer_id:
            orders = repo.find_by_customer(customer_id, limit=limit)
        elif active_store:
            orders = repo.find_by_store(
                active_store,
                from_date=from_date,
                to_date=to_date,
                status=status.value if status else None,
            )
        else:
            filter_dict = {}
            if status:
                filter_dict["status"] = status.value
            orders = repo.find_many(filter_dict, skip=skip, limit=limit)

        # Convert to frontend format (camelCase)
        orders_formatted = [order_to_frontend(o) for o in orders]
        return {"orders": orders_formatted, "total": len(orders_formatted)}

    return {"orders": [], "total": 0}


# NOTE: Specific routes MUST come before /{order_id} to avoid being matched as order_id
@router.get("/pending/delivery")
async def get_pending_deliveries(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get orders pending delivery"""
    repo = get_order_repository()
    active_store = store_id or current_user.get("active_store_id")

    if repo is not None:
        orders = repo.find_ready_for_delivery(active_store)
        orders_formatted = [order_to_frontend(o) for o in orders]
        return {"orders": orders_formatted}

    return {"orders": []}


@router.get("/unpaid/list")
async def get_unpaid_orders(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get unpaid/partially paid orders"""
    repo = get_order_repository()
    active_store = store_id or current_user.get("active_store_id")

    if repo is not None:
        orders = repo.find_unpaid(active_store)
        orders_formatted = [order_to_frontend(o) for o in orders]
        return {"orders": orders_formatted}

    return {"orders": []}


@router.get("/overdue/list")
async def get_overdue_orders(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get overdue orders (past expected delivery)"""
    repo = get_order_repository()
    active_store = store_id or current_user.get("active_store_id")

    if repo is not None:
        orders = repo.find_overdue(active_store)
        orders_formatted = [order_to_frontend(o) for o in orders]
        return {"orders": orders_formatted}

    return {"orders": []}


@router.get("/search")
async def search_orders(
    q: str = Query(..., min_length=2),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Search orders by number, customer name, or phone"""
    repo = get_order_repository()
    active_store = store_id or current_user.get("active_store_id")

    if repo is not None:
        orders = repo.search_orders(q, active_store)
        orders_formatted = [order_to_frontend(o) for o in orders]
        return {"orders": orders_formatted}

    return {"orders": []}


@router.get("/sales/summary")
async def get_sales_summary(
    store_id: Optional[str] = Query(None),
    from_date: date = Query(...),
    to_date: date = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Get sales summary for a date range"""
    repo = get_order_repository()
    active_store = store_id or current_user.get("active_store_id")

    if repo and active_store:
        summary = repo.get_sales_summary(active_store, from_date, to_date)
        return summary

    return {
        "totalOrders": 0,
        "totalRevenue": 0,
        "totalPaid": 0,
        "avgOrderValue": 0,
        "totalItems": 0,
    }


@router.get("/status/counts")
async def get_status_counts(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get order counts by status"""
    repo = get_order_repository()
    active_store = store_id or current_user.get("active_store_id")

    if repo is not None:
        counts = repo.get_status_counts(active_store)
        return {"statusCounts": counts}

    return {"statusCounts": {}}


def generate_order_number(store_id: str) -> str:
    """Generate unique order number"""
    prefix = store_id[:3].upper() if store_id else "IMS"
    year = datetime.now().year
    short_uuid = str(uuid.uuid4())[:6].upper()
    return f"BV-{prefix}-{year}-{short_uuid}"


@router.post("/", status_code=201)
async def create_order(
    order: OrderCreate, current_user: dict = Depends(get_current_user)
):
    """Create new sales order"""
    order_repo = get_order_repository()
    customer_repo = get_customer_repository()
    store_id = current_user.get("active_store_id")
    salesperson_id = current_user.get("user_id")

    # Validate items
    if not order.items:
        raise HTTPException(status_code=400, detail="Order must have at least one item")

    if order_repo and customer_repo:
        # Verify customer exists
        customer = customer_repo.find_by_id(order.customer_id)
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")

        # Calculate totals
        items_data = []
        subtotal = 0.0

        for item in order.items:
            item_total = item.unit_price * item.quantity
            discount_amount = item_total * (item.discount_percent / 100)
            item_subtotal = item_total - discount_amount

            items_data.append(
                {
                    "item_id": str(uuid.uuid4()),
                    "item_type": item.item_type,
                    "product_id": item.product_id,
                    "quantity": item.quantity,
                    "unit_price": item.unit_price,
                    "discount_percent": item.discount_percent,
                    "discount_amount": discount_amount,
                    "item_total": item_subtotal,
                    "prescription_id": item.prescription_id,
                    "lens_options": item.lens_options,
                }
            )
            subtotal += item_subtotal

        # Calculate tax (18% GST default)
        tax_rate = 18.0
        tax_amount = subtotal * (tax_rate / 100)
        grand_total = subtotal + tax_amount
        expected_delivery = datetime.now() + timedelta(
            days=order.expected_delivery_days
        )

        order_data = {
            "order_number": generate_order_number(store_id),
            "store_id": store_id,
            "customer_id": order.customer_id,
            "customer_name": customer.get("name"),
            "customer_phone": customer.get("mobile"),
            "patient_id": order.patient_id,
            "salesperson_id": salesperson_id,
            "items": items_data,
            "subtotal": subtotal,
            "tax_rate": tax_rate,
            "tax_amount": tax_amount,
            "grand_total": grand_total,
            "amount_paid": 0.0,
            "balance_due": grand_total,
            "payment_status": "UNPAID",
            "status": "DRAFT",
            "expected_delivery": expected_delivery.isoformat(),
            "notes": order.notes,
            "payments": [],
        }

        created = order_repo.create(order_data)
        if created:
            return {
                "order_id": created["order_id"],
                "order_number": created["order_number"],
                "status": "DRAFT",
                "grand_total": grand_total,
                "message": "Order created successfully",
            }

        raise HTTPException(status_code=500, detail="Failed to create order")

    return {
        "order_id": str(uuid.uuid4()),
        "order_number": generate_order_number(store_id or "STR"),
        "status": "DRAFT",
        "message": "Order created successfully",
    }


@router.get("/{order_id}")
async def get_order(order_id: str, current_user: dict = Depends(get_current_user)):
    """Get order details"""
    repo = get_order_repository()

    if repo is not None:
        order = repo.find_by_id(order_id)
        if order is not None:
            return order_to_frontend(order)
        raise HTTPException(status_code=404, detail="Order not found")

    return {"id": order_id}


@router.put("/{order_id}")
async def update_order(
    order_id: str, order: OrderUpdate, current_user: dict = Depends(get_current_user)
):
    """Update order (only DRAFT orders)"""
    repo = get_order_repository()

    if repo is not None:
        existing = repo.find_by_id(order_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Order not found")

        if existing.get("status") != "DRAFT":
            raise HTTPException(
                status_code=400, detail="Only DRAFT orders can be updated"
            )

        update_data = order.model_dump(exclude_unset=True)
        if "expected_delivery" in update_data and update_data["expected_delivery"]:
            update_data["expected_delivery"] = update_data[
                "expected_delivery"
            ].isoformat()

        update_data["updated_by"] = current_user.get("user_id")

        if repo.update(order_id, update_data):
            return {"order_id": order_id, "message": "Order updated"}

        raise HTTPException(status_code=500, detail="Failed to update order")

    return {"order_id": order_id, "message": "Order updated"}


@router.post("/{order_id}/items")
async def add_order_item(
    order_id: str, item: OrderItemCreate, current_user: dict = Depends(get_current_user)
):
    """Add item to order (only DRAFT orders)"""
    repo = get_order_repository()

    if repo is not None:
        order = repo.find_by_id(order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        if order.get("status") != "DRAFT":
            raise HTTPException(
                status_code=400, detail="Can only add items to DRAFT orders"
            )

        # Calculate item totals
        item_total = item.unit_price * item.quantity
        discount_amount = item_total * (item.discount_percent / 100)
        item_subtotal = item_total - discount_amount

        item_data = {
            "item_id": str(uuid.uuid4()),
            "item_type": item.item_type,
            "product_id": item.product_id,
            "quantity": item.quantity,
            "unit_price": item.unit_price,
            "discount_percent": item.discount_percent,
            "discount_amount": discount_amount,
            "item_total": item_subtotal,
            "prescription_id": item.prescription_id,
            "lens_options": item.lens_options,
        }

        # Add item and recalculate totals
        items = order.get("items", []) + [item_data]
        subtotal = sum(i.get("item_total", 0) for i in items)
        tax_rate = order.get("tax_rate", 18.0)
        tax_amount = subtotal * (tax_rate / 100)
        grand_total = subtotal + tax_amount

        repo.update(
            order_id,
            {
                "items": items,
                "subtotal": subtotal,
                "tax_amount": tax_amount,
                "grand_total": grand_total,
                "balance_due": grand_total - order.get("amount_paid", 0),
            },
        )

        return {"message": "Item added to order", "item_id": item_data["item_id"]}

    return {"message": "Item added to order"}


@router.delete("/{order_id}/items/{item_id}")
async def remove_order_item(
    order_id: str, item_id: str, current_user: dict = Depends(get_current_user)
):
    """Remove item from order (only DRAFT orders)"""
    repo = get_order_repository()

    if repo is not None:
        order = repo.find_by_id(order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        if order.get("status") != "DRAFT":
            raise HTTPException(
                status_code=400, detail="Can only remove items from DRAFT orders"
            )

        items = [i for i in order.get("items", []) if i.get("item_id") != item_id]
        if len(items) == len(order.get("items", [])):
            raise HTTPException(status_code=404, detail="Item not found in order")

        # Recalculate totals
        subtotal = sum(i.get("item_total", 0) for i in items)
        tax_rate = order.get("tax_rate", 18.0)
        tax_amount = subtotal * (tax_rate / 100)
        grand_total = subtotal + tax_amount

        repo.update(
            order_id,
            {
                "items": items,
                "subtotal": subtotal,
                "tax_amount": tax_amount,
                "grand_total": grand_total,
                "balance_due": grand_total - order.get("amount_paid", 0),
            },
        )

        return {"message": "Item removed from order"}

    return {"message": "Item removed from order"}


@router.post("/{order_id}/confirm")
async def confirm_order(order_id: str, current_user: dict = Depends(get_current_user)):
    """Confirm order (DRAFT -> CONFIRMED)"""
    repo = get_order_repository()

    if repo is not None:
        order = repo.find_by_id(order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        if order.get("status") != "DRAFT":
            raise HTTPException(
                status_code=400, detail="Only DRAFT orders can be confirmed"
            )

        if not order.get("items"):
            raise HTTPException(
                status_code=400, detail="Cannot confirm order with no items"
            )

        if repo.update_status(order_id, "CONFIRMED", current_user.get("user_id")):
            return {
                "order_id": order_id,
                "status": "CONFIRMED",
                "message": "Order confirmed",
            }

        raise HTTPException(status_code=500, detail="Failed to confirm order")

    return {"order_id": order_id, "status": "CONFIRMED"}


@router.post("/{order_id}/payments")
async def add_payment(
    order_id: str,
    payment: PaymentCreate,
    current_user: dict = Depends(get_current_user),
):
    """Add payment to order"""
    repo = get_order_repository()

    if repo is not None:
        order = repo.find_by_id(order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        if order.get("status") == "CANCELLED":
            raise HTTPException(
                status_code=400, detail="Cannot add payment to cancelled order"
            )

        balance_due = order.get("balance_due", order.get("grand_total", 0))
        if payment.amount > balance_due:
            raise HTTPException(
                status_code=400,
                detail=f"Payment amount exceeds balance due (₹{balance_due})",
            )

        payment_data = {
            "payment_id": str(uuid.uuid4()),
            "method": payment.method.value,
            "amount": payment.amount,
            "reference": payment.reference,
            "received_by": current_user.get("user_id"),
            "received_at": datetime.now().isoformat(),
        }

        if repo.add_payment(order_id, payment_data):
            return {
                "payment_id": payment_data["payment_id"],
                "message": "Payment recorded",
                "amount": payment.amount,
            }

        raise HTTPException(status_code=500, detail="Failed to add payment")

    return {"payment_id": str(uuid.uuid4()), "message": "Payment recorded"}


@router.post("/{order_id}/ready")
async def mark_ready(order_id: str, current_user: dict = Depends(get_current_user)):
    """Mark order as ready for delivery"""
    repo = get_order_repository()

    if repo is not None:
        order = repo.find_by_id(order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        if order.get("status") not in ["CONFIRMED", "PROCESSING"]:
            raise HTTPException(
                status_code=400,
                detail="Order must be CONFIRMED or PROCESSING to mark as ready",
            )

        if repo.update_status(order_id, "READY", current_user.get("user_id")):
            return {
                "order_id": order_id,
                "status": "READY",
                "message": "Order marked as ready",
            }

        raise HTTPException(status_code=500, detail="Failed to update order status")

    return {"order_id": order_id, "status": "READY"}


@router.post("/{order_id}/deliver")
async def deliver_order(order_id: str, current_user: dict = Depends(get_current_user)):
    """Deliver order to customer"""
    repo = get_order_repository()

    if repo is not None:
        order = repo.find_by_id(order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        if order.get("status") != "READY":
            raise HTTPException(
                status_code=400, detail="Order must be READY for delivery"
            )

        # Check payment status (allow partial for B2B customers)
        payment_status = order.get("payment_status", "UNPAID")
        if payment_status == "UNPAID":
            raise HTTPException(
                status_code=400,
                detail="Order must have at least partial payment before delivery",
            )

        if repo.update_status(order_id, "DELIVERED", current_user.get("user_id")):
            return {
                "order_id": order_id,
                "status": "DELIVERED",
                "message": "Order delivered",
            }

        raise HTTPException(status_code=500, detail="Failed to deliver order")

    return {"order_id": order_id, "status": "DELIVERED"}


@router.post("/{order_id}/cancel")
async def cancel_order(
    order_id: str,
    reason: str = Query(..., min_length=10),
    current_user: dict = Depends(get_current_user),
):
    """Cancel order"""
    repo = get_order_repository()

    if repo is not None:
        order = repo.find_by_id(order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        if order.get("status") == "DELIVERED":
            raise HTTPException(
                status_code=400, detail="Cannot cancel delivered orders"
            )

        if order.get("status") == "CANCELLED":
            raise HTTPException(status_code=400, detail="Order is already cancelled")

        # Update status and add cancellation reason
        repo.update(
            order_id,
            {
                "status": "CANCELLED",
                "cancellation_reason": reason,
                "cancelled_by": current_user.get("user_id"),
                "cancelled_at": datetime.now().isoformat(),
            },
        )

        return {
            "order_id": order_id,
            "status": "CANCELLED",
            "message": "Order cancelled",
        }

    return {"order_id": order_id, "status": "CANCELLED"}


@router.get("/{order_id}/invoice")
async def get_invoice(order_id: str, current_user: dict = Depends(get_current_user)):
    """Get/generate invoice for order"""
    repo = get_order_repository()

    if repo is not None:
        order = repo.find_by_id(order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        if order.get("status") == "DRAFT":
            raise HTTPException(
                status_code=400, detail="Cannot generate invoice for DRAFT orders"
            )

        # Return existing invoice or generate new one
        invoice_number = order.get("invoice_number")
        if not invoice_number:
            year = datetime.now().year
            short_id = order.get("order_id", "")[:8].upper()
            invoice_number = f"BV/INV/{year}/{short_id}"
            repo.set_invoice(order_id, invoice_number)

        # Convert items to camelCase
        items_formatted = [item_to_frontend(item) for item in order.get("items", [])]
        return {
            "invoiceNumber": invoice_number,
            "orderId": order_id,
            "orderNumber": order.get("order_number"),
            "customerName": order.get("customer_name"),
            "grandTotal": order.get("grand_total"),
            "amountPaid": order.get("amount_paid"),
            "balanceDue": order.get("balance_due"),
            "items": items_formatted,
            "invoiceDate": order.get("invoice_date") or datetime.now().isoformat(),
        }

    return {"invoiceNumber": "BV/INV/2024/0001", "orderId": order_id}

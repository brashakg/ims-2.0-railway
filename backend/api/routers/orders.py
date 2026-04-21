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

import logging

logger = logging.getLogger(__name__)

from .auth import get_current_user
from ..dependencies import (
    get_order_repository,
    get_customer_repository,
    get_stock_repository,
)

# Discount cap by product discount_category (mirrors billing.py caps)
CATEGORY_DISCOUNT_CAPS = {
    "LUXURY": 2.0,
    "PREMIUM": 5.0,
    "MASS": 10.0,
    "NON_DISCOUNTABLE": 0.0,
}

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
    if order is None:
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
        elif key == "status_history" and isinstance(value, list):
            # Convert status_history fields (timestamp -> timestamp, changed_by -> changedBy)
            result["statusHistory"] = []
            for entry in value:
                history_entry = {
                    "status": entry.get("status"),
                    "timestamp": entry.get("timestamp"),
                    "changedBy": entry.get("changed_by")
                }
                result["statusHistory"].append(history_entry)
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


# Valid state transitions — only these moves are allowed
VALID_TRANSITIONS = {
    "DRAFT":      {"CONFIRMED", "CANCELLED"},
    "CONFIRMED":  {"PROCESSING", "READY", "CANCELLED"},   # READY for quick-sale (no workshop)
    "PROCESSING": {"READY", "CANCELLED"},
    "READY":      {"DELIVERED", "CANCELLED"},
    "DELIVERED":  set(),       # Terminal
    "CANCELLED":  set(),       # Terminal
}


def validate_status_transition(current: str, target: str) -> bool:
    """Check if an order status transition is valid."""
    allowed = VALID_TRANSITIONS.get(current, set())
    return target in allowed


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
    product_name: Optional[str] = None
    sku: Optional[str] = None
    brand: Optional[str] = None
    subbrand: Optional[str] = None
    category: Optional[str] = None
    quantity: int = Field(default=1, ge=1)
    unit_price: float = Field(..., ge=0)
    discount_percent: float = Field(default=0, ge=0, le=100)
    prescription_id: Optional[str] = None
    lens_options: Optional[dict] = None  # coating, tint, etc.
    lens_details: Optional[dict] = None  # type, material, coatings


class PaymentCreate(BaseModel):
    method: PaymentMethod
    amount: float = Field(..., gt=0)
    reference: Optional[str] = None
    # EMI-specific fields (only required when method=EMI)
    emi_months: Optional[int] = Field(None, ge=3, le=24)
    emi_provider: Optional[str] = None  # e.g., "BAJAJ", "HDFC", "ICICI"


class OrderCreate(BaseModel):
    customer_id: str
    patient_id: Optional[str] = None
    items: List[OrderItemCreate]
    notes: Optional[str] = None
    expected_delivery_days: int = Field(default=7, ge=1)
    # Phase 6.7 — delivery scheduling + order-level discount
    delivery_date: Optional[date] = None
    delivery_time_slot: Optional[str] = None   # e.g. "10:00-12:00"
    delivery_priority: Optional[str] = Field(default="NORMAL")  # NORMAL | EXPRESS | URGENT
    cart_discount_percent: float = Field(default=0.0, ge=0.0, le=100.0)
    cart_discount_amount: float = Field(default=0.0, ge=0.0)
    cart_discount_reason: Optional[str] = None
    cart_discount_approved_by: Optional[str] = None


class OrderUpdate(BaseModel):
    notes: Optional[str] = None
    expected_delivery: Optional[date] = None


# ============================================================================
# ENDPOINTS
# ============================================================================


@router.get("")
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
        from ..utils.pagination import paginate
        orders_formatted = [order_to_frontend(o) for o in orders]
        page = (skip // limit) + 1 if limit > 0 else 1
        result = paginate(orders_formatted, page=page, page_size=limit)
        result["orders"] = result["data"]  # backward compat
        return result

    return {"orders": [], "total": 0, "data": [], "pagination": {"total": 0, "page": 1, "page_size": limit, "total_pages": 0}}


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
    """Generate unique order number: ORD-BOK01-2026-A1B2C3"""
    # Extract store short code from store_id like "BV-BOK-01" → "BOK01"
    parts = store_id.split("-") if store_id else []
    if len(parts) >= 3:
        prefix = parts[1] + parts[2]  # BOK + 01 = BOK01
    elif len(parts) >= 2:
        prefix = parts[1][:5]
    else:
        prefix = (store_id or "IMS")[:5].upper()
    year = datetime.now().year
    short_uuid = str(uuid.uuid4())[:6].upper()
    return f"ORD-{prefix}-{year}-{short_uuid}"


@router.post("", status_code=201)
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

    MAX_CART_ITEMS = 15
    if len(order.items) > MAX_CART_ITEMS:
        raise HTTPException(
            status_code=400,
            detail=f"Cart exceeds maximum of {MAX_CART_ITEMS} items. Split into multiple orders."
        )

    # Validate product_ids exist
    stock_repo = get_stock_repository()
    if stock_repo is not None:
        for item in order.items:
            if item.product_id and not item.product_id.startswith("custom-"):
                product = stock_repo.find_by_id(item.product_id)
                if product is None:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Product not found: {item.product_id} ({item.product_name or 'unknown'})"
                    )

    # Validate: offer_price cannot exceed MRP
    for item in order.items:
        offer_price = getattr(item, "offer_price", 0) or 0
        mrp = getattr(item, "mrp", 0) or 0
        if offer_price > 0 and mrp > 0 and offer_price > mrp:
            raise HTTPException(
                status_code=400,
                detail=f"Offer price (₹{offer_price}) cannot exceed MRP (₹{mrp}) for {getattr(item, 'product_name', 'item')}"
            )

    if order_repo is not None and customer_repo is not None:
        # Verify customer exists (allow walk-in with generated IDs)
        customer = customer_repo.find_by_id(order.customer_id)
        is_walkin = not customer and (
            order.customer_id.startswith("walkin-") or order.customer_id == "walk-in"
        )
        if not customer and not is_walkin:
            raise HTTPException(status_code=404, detail="Customer not found")
        
        customer_name = customer.get("name") if customer else "Walk-in Customer"
        customer_phone = customer.get("phone") or customer.get("mobile") if customer else ""

        # Calculate totals
        items_data = []
        subtotal = 0.0

        # Retrieve user discount cap for enforcement
        user_discount_cap = current_user.get("discount_cap", 10.0)
        user_roles = current_user.get("roles", [])
        is_admin = any(r in user_roles for r in ["SUPERADMIN", "ADMIN", "STORE_MANAGER"])

        for item in order.items:
            item_total = item.unit_price * item.quantity

            # Enforce discount cap (admins bypass)
            effective_cap = user_discount_cap
            if not is_admin and item.discount_percent > 0:
                # Look up category cap for this product
                try:
                    stock_repo = get_stock_repository()
                    if stock_repo is not None:
                        product = stock_repo.find_by_id(item.product_id)
                        if product:
                            cat = product.get("discount_category", "MASS")
                            category_cap = CATEGORY_DISCOUNT_CAPS.get(cat, 10.0)
                            effective_cap = min(user_discount_cap, category_cap)
                except Exception:
                    pass  # fall back to user cap only

                if item.discount_percent > effective_cap:
                    raise HTTPException(
                        status_code=403,
                        detail=f"Discount {item.discount_percent}% on {item.product_name or item.product_id} "
                               f"exceeds your limit of {effective_cap}%. Contact a manager for approval."
                    )

            discount_amount = item_total * (item.discount_percent / 100)
            item_subtotal = item_total - discount_amount

            # ============================================================
            # INCENTIVE AUTO-TAGGING
            # Detects qualifying items for kicker tracking at POS time
            # Tags: brand group, subbrand, kicker type, item value, discount
            # Replaces the manual PRODUCT_INCENTIVE Excel entirely
            # ============================================================
            INCENTIVE_BRANDS = {
                'ZEISS': 'ZEISS', 'SAFILO': 'SAFILO', 'CARRERA': 'SAFILO', 
                'POLAROID': 'SAFILO', 'MARC JACOB': 'SAFILO', 'HUGO': 'SAFILO',
                'SEVENTH STREET': 'SAFILO', 'BOSS': 'SAFILO', 'TOMMY HILFIGER': 'SAFILO',
                'PIERRE CARDIN': 'SAFILO', 'UNDER ARMOUR': 'SAFILO',
            }
            brand_upper = (item.brand or '').upper()
            subbrand_upper = (item.subbrand or '').upper()
            product_name_upper = (item.product_name or '').upper()
            
            # Check brand, subbrand, AND product name for matches
            # (lens details like "1.5 ZEISS PROGRESSIVE LIGHT..." appear in product name)
            incentive_brand = None
            matched_key = None
            for key, group in INCENTIVE_BRANDS.items():
                if key in brand_upper or key in subbrand_upper or key in product_name_upper:
                    incentive_brand = group
                    matched_key = key
                    break
            
            # Detect kicker type from lens_details, subbrand, or product name
            incentive_kicker = None
            incentive_lens_type = None
            incentive_addon = None
            
            if incentive_brand == 'ZEISS':
                # Check lens_details dict first (structured data from LensDetailsModal)
                lens_type_str = ''
                if item.lens_details:
                    lens_type_str = (item.lens_details.get('type', '') or '').upper()
                    lens_material = (item.lens_details.get('material', '') or '').upper()
                    lens_coatings = ' '.join(item.lens_details.get('coatings', []) or []).upper()
                    lens_type_str = f"{lens_type_str} {lens_material} {lens_coatings}"
                
                # Also check product name (e.g., "1.5 ZEISS PROGRESSIVE LIGHT 2 3D DVP UV")
                full_check = f"{lens_type_str} {product_name_upper} {subbrand_upper}"
                
                if 'SMARTLIFE' in full_check or 'SMART LIFE' in full_check:
                    incentive_kicker = 'ZEISS_SMARTLIFE'
                    incentive_lens_type = 'PAL' if 'PROGRESS' in full_check else 'SV'
                    incentive_addon = 'SMART LIFE'
                elif 'PHOTOFUSION' in full_check or 'PFX' in full_check:
                    incentive_kicker = 'ZEISS_PHOTOFUSION'
                    incentive_lens_type = 'PAL' if 'PROGRESS' in full_check else 'SV'
                    incentive_addon = 'PFX'
                elif 'PROGRESSIVE' in full_check or 'PAL' in full_check:
                    incentive_kicker = 'ZEISS_PROGRESSIVE'
                    incentive_lens_type = 'PAL'
                elif 'FSV' in full_check or 'SINGLE' in full_check:
                    incentive_kicker = 'ZEISS_SV'
                    incentive_lens_type = 'SV'
                else:
                    incentive_kicker = 'ZEISS_OTHER'
                    
            elif incentive_brand == 'SAFILO':
                if item.item_type in ('FRAME',):
                    incentive_kicker = 'SAFILO_FRAME'
                elif item.item_type in ('SUNGLASS',) or (item.category and 'SG' in item.category.upper()):
                    incentive_kicker = 'SAFILO_SG'
                else:
                    incentive_kicker = 'SAFILO_OTHER'

            # Build incentive tag (None if not qualifying)
            incentive_tag = None
            if incentive_brand:
                incentive_tag = {
                    "brand_group": incentive_brand,
                    "brand": item.brand,
                    "subbrand": item.subbrand or matched_key,
                    "kicker": incentive_kicker,
                    "lens_type": incentive_lens_type,
                    "addon": incentive_addon,
                    "item_value": item_subtotal,
                    "item_mrp": item.unit_price * item.quantity,
                    "discount_percent": item.discount_percent,
                    "discount_amount": discount_amount,
                    "salesperson_id": salesperson_id,
                    "tagged_at": datetime.now().isoformat(),
                }

            items_data.append(
                {
                    "item_id": str(uuid.uuid4()),
                    "item_type": item.item_type,
                    "product_id": item.product_id,
                    "product_name": item.product_name,
                    "sku": item.sku,
                    "brand": item.brand,
                    "subbrand": item.subbrand,
                    "category": item.category,
                    "quantity": item.quantity,
                    "unit_price": item.unit_price,
                    "discount_percent": item.discount_percent,
                    "discount_amount": discount_amount,
                    "item_total": item_subtotal,
                    "prescription_id": item.prescription_id,
                    "lens_options": item.lens_options,
                    "lens_details": item.lens_details,
                    "incentive_tag": incentive_tag,
                }
            )
            subtotal += item_subtotal

        # Calculate tax (18% GST default)
        tax_rate = 18.0
        # Phase 6.7 — apply order-level discount on top of per-item discounts.
        # Discount comes off taxable subtotal BEFORE GST so invoice math is
        # consistent (tax charged on what the customer actually pays).
        cart_discount_percent = max(0.0, min(100.0, order.cart_discount_percent or 0.0))
        cart_discount_amount = round(subtotal * (cart_discount_percent / 100.0), 2)
        taxable_after_cart_discount = round(subtotal - cart_discount_amount, 2)
        tax_amount = round(taxable_after_cart_discount * (tax_rate / 100), 2)
        grand_total = round(taxable_after_cart_discount + tax_amount, 2)

        # Resolve delivery date — explicit date > expected_delivery_days
        if order.delivery_date:
            expected_delivery = datetime.combine(order.delivery_date, datetime.min.time())
        else:
            expected_delivery = datetime.now() + timedelta(days=order.expected_delivery_days)

        order_data = {
            "order_number": generate_order_number(store_id),
            "store_id": store_id,
            "customer_id": order.customer_id,
            "customer_name": customer_name,
            "customer_phone": customer_phone,
            "patient_id": order.patient_id,
            "salesperson_id": salesperson_id,
            "items": items_data,
            "subtotal": subtotal,
            "cart_discount_percent": cart_discount_percent,
            "cart_discount_amount": cart_discount_amount,
            "cart_discount_reason": order.cart_discount_reason,
            "cart_discount_approved_by": order.cart_discount_approved_by,
            "tax_rate": tax_rate,
            "tax_amount": tax_amount,
            "grand_total": grand_total,
            "amount_paid": 0.0,
            "balance_due": grand_total,
            "payment_status": "UNPAID",
            "status": "DRAFT",
            "expected_delivery": expected_delivery.isoformat(),
            "delivery_time_slot": order.delivery_time_slot,
            "delivery_priority": (order.delivery_priority or "NORMAL").upper(),
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
        if existing is None:
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
    # Validate: offer_price cannot exceed MRP
    offer_price = getattr(item, "offer_price", 0) or 0
    mrp = getattr(item, "mrp", 0) or 0
    if offer_price > 0 and mrp > 0 and offer_price > mrp:
        raise HTTPException(
            status_code=400,
            detail=f"Offer price (₹{offer_price}) cannot exceed MRP (₹{mrp})"
        )

    repo = get_order_repository()

    if repo is not None:
        order = repo.find_by_id(order_id)
        if order is None:
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
        if order is None:
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
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")

        if not validate_status_transition(order.get("status", ""), "CONFIRMED"):
            raise HTTPException(
                status_code=400, detail=f"Cannot confirm order — current status is {order.get('status')}"
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
        if order is None:
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

        # EMI validation and interest calculation
        emi_details = None
        if payment.method == PaymentMethod.EMI:
            if not payment.emi_months:
                raise HTTPException(status_code=400, detail="EMI tenure (emi_months) is required for EMI payments")
            # Fetch configurable EMI rate from store settings (default 12% annual)
            emi_annual_rate = 12.0  # fallback
            try:
                from ..dependencies import get_seeded_db
                db = get_seeded_db()
                if db:
                    store_settings = db.get_collection("settings").find_one({
                        "store_id": current_user.get("active_store_id"),
                        "key": "emi_config"
                    })
                    if store_settings and store_settings.get("value", {}).get("annual_rate"):
                        emi_annual_rate = float(store_settings["value"]["annual_rate"])
            except Exception:
                pass  # use default

            monthly_rate = emi_annual_rate / 12 / 100
            months = payment.emi_months
            if monthly_rate > 0:
                emi_amount = payment.amount * monthly_rate * (1 + monthly_rate) ** months / ((1 + monthly_rate) ** months - 1)
            else:
                emi_amount = payment.amount / months

            emi_details = {
                "tenure_months": months,
                "annual_rate": emi_annual_rate,
                "monthly_emi": round(emi_amount, 2),
                "total_payable": round(emi_amount * months, 2),
                "interest_amount": round(emi_amount * months - payment.amount, 2),
                "provider": payment.emi_provider or "STORE",
            }

        payment_data = {
            "payment_id": str(uuid.uuid4()),
            "method": payment.method.value,
            "amount": payment.amount,
            "reference": payment.reference,
            "received_by": current_user.get("user_id"),
            "received_at": datetime.now().isoformat(),
        }
        if emi_details:
            payment_data["emi_details"] = emi_details

        if repo.add_payment(order_id, payment_data):
            # Auto-confirm DRAFT orders when first payment is received
            # This fixes the "stuck in DRAFT+PARTIAL" lifecycle issue
            refreshed = repo.find_by_id(order_id)
            auto_confirmed = False
            if refreshed and refreshed.get("status") == "DRAFT":
                repo.update_status(order_id, "CONFIRMED", current_user.get("user_id"))
                auto_confirmed = True

            return {
                "payment_id": payment_data["payment_id"],
                "message": "Payment recorded" + (" — order auto-confirmed" if auto_confirmed else ""),
                "amount": payment.amount,
                "order_status": "CONFIRMED" if auto_confirmed else refreshed.get("status") if refreshed else "DRAFT",
                "payment_status": refreshed.get("payment_status") if refreshed else "PARTIAL",
            }

        raise HTTPException(status_code=500, detail="Failed to add payment")

    return {"payment_id": str(uuid.uuid4()), "message": "Payment recorded"}


@router.post("/{order_id}/ready")
async def mark_ready(order_id: str, current_user: dict = Depends(get_current_user)):
    """Mark order as ready for delivery"""
    repo = get_order_repository()

    if repo is not None:
        order = repo.find_by_id(order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")

        if not validate_status_transition(order.get("status", ""), "READY"):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot mark as ready — current status is {order.get('status')}. Valid transitions: {', '.join(VALID_TRANSITIONS.get(order.get('status', ''), set()))}",
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
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")

        if not validate_status_transition(order.get("status", ""), "DELIVERED"):
            raise HTTPException(
                status_code=400, detail=f"Cannot deliver — current status is {order.get('status')}. Must be READY."
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
        if order is None:
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
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")

        if order.get("status") == "DRAFT":
            raise HTTPException(
                status_code=400, detail="Cannot generate invoice for DRAFT orders"
            )

        # GST compliance: store must have GSTIN configured before generating invoice
        store_id = order.get("store_id") or current_user.get("active_store_id")
        if store_id:
            try:
                from ..dependencies import get_store_repository
                store_repo = get_store_repository()
                if store_repo:
                    store = store_repo.find_by_id(store_id)
                    if store and not store.get("gstin"):
                        raise HTTPException(
                            status_code=400,
                            detail="Cannot generate invoice: store GSTIN is not configured. "
                                   "Update store settings with a valid GSTIN first."
                        )
            except HTTPException:
                raise
            except Exception:
                pass  # don't block invoice if store lookup fails

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

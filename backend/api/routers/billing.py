"""
Enterprise Billing Router
Comprehensive POS billing system with GST calculations, discounts, and payment processing
"""

from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session

from api.database import get_db
from api.models import Order, OrderLineItem, Customer, Store
from api.auth import get_current_user
from api.schemas import UserSchema

router = APIRouter(prefix="/billing", tags=["Billing"])

# ============================================================================
# Types and Data Models
# ============================================================================


class CartItemData:
    """Shopping cart item"""

    def __init__(
        self,
        product_id: str,
        name: str,
        unit_price: float,
        quantity: int,
        discount_percent: Optional[float] = None,
    ):
        self.product_id = product_id
        self.name = name
        self.unit_price = unit_price
        self.quantity = quantity
        self.discount_percent = discount_percent or 0


class BillCalculation:
    """Bill calculation result"""

    def __init__(self):
        self.subtotal = 0.0
        self.item_discount = 0.0
        self.order_discount = 0.0
        self.order_discount_amount = 0.0
        self.taxable_amount = 0.0
        self.cgst_amount = 0.0
        self.sgst_amount = 0.0
        self.igst_amount = 0.0
        self.total_gst = 0.0
        self.roundoff_amount = 0.0
        self.total_amount = 0.0


# ============================================================================
# Mock Data
# ============================================================================


HELD_BILLS = [
    {
        "bill_id": "BIL-1707394800000",
        "bill_number": "BIL-001",
        "created_at": "2025-02-08 10:30:00",
        "customer_name": "Raj Kumar",
        "items_count": 3,
        "total_amount": 24500.0,
    },
    {
        "bill_id": "BIL-1707394900000",
        "bill_number": "BIL-002",
        "created_at": "2025-02-08 11:15:00",
        "customer_name": "Priya Sharma",
        "items_count": 2,
        "total_amount": 18750.0,
    },
]


# ============================================================================
# Helper Functions
# ============================================================================


def calculate_bill(
    items: List[CartItemData],
    order_discount_percent: float = 0,
    use_igst: bool = False,
) -> BillCalculation:
    """Calculate bill with all taxes and discounts"""
    bill = BillCalculation()

    # Calculate subtotal with item-level discounts
    for item in items:
        bill.subtotal += item.unit_price * item.quantity
        if item.discount_percent > 0:
            bill.item_discount += (
                item.unit_price * item.quantity * (item.discount_percent / 100)
            )

    bill.subtotal_after_discount = bill.subtotal - bill.item_discount

    # Apply order-level discount
    bill.order_discount_amount = bill.subtotal_after_discount * (
        order_discount_percent / 100
    )
    bill.order_discount = order_discount_percent
    bill.taxable_amount = bill.subtotal_after_discount - bill.order_discount_amount

    # Calculate GST (18% standard rate for optical goods in India)
    gst_rate = 0.18

    if use_igst:
        bill.igst_amount = bill.taxable_amount * gst_rate
    else:
        # Split GST equally (9% CGST + 9% SGST) for intra-state
        bill.cgst_amount = bill.taxable_amount * (gst_rate / 2)
        bill.sgst_amount = bill.taxable_amount * (gst_rate / 2)

    bill.total_gst = bill.cgst_amount + bill.sgst_amount + bill.igst_amount

    # Calculate total before round-off
    total_before_roundoff = bill.taxable_amount + bill.total_gst

    # Round-off to nearest rupee
    bill.roundoff_amount = round(total_before_roundoff) - total_before_roundoff
    bill.total_amount = round(total_before_roundoff)

    return bill


# ============================================================================
# Endpoints
# ============================================================================


@router.post("/create-invoice")
async def create_invoice(
    db: Session = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user),
    invoice_data: Dict[str, Any] = None,
):
    """
    Create a new invoice/bill with items and discounts
    Supports GST calculations (CGST+SGST or IGST), multiple payment methods
    """
    try:
        # Mock implementation - in production, would save to database
        if not invoice_data:
            raise HTTPException(status_code=400, detail="Invoice data required")

        items = invoice_data.get("items", [])
        customer_id = invoice_data.get("customer_id")
        order_discount = invoice_data.get("order_discount", 0)
        use_igst = invoice_data.get("use_igst", False)
        payment_method = invoice_data.get("payment_method", "cash")

        # Convert items to CartItemData
        cart_items = [
            CartItemData(
                product_id=item["product_id"],
                name=item["name"],
                unit_price=item["unit_price"],
                quantity=item["quantity"],
                discount_percent=item.get("discount_percent", 0),
            )
            for item in items
        ]

        # Calculate bill
        bill = calculate_bill(cart_items, order_discount, use_igst)

        # Generate bill number
        bill_number = f"BIL-{int(datetime.now().timestamp() * 1000)}"

        return {
            "bill_number": bill_number,
            "timestamp": datetime.now().isoformat(),
            "subtotal": bill.subtotal,
            "item_discount": bill.item_discount,
            "order_discount": order_discount,
            "order_discount_amount": bill.order_discount_amount,
            "taxable_amount": bill.taxable_amount,
            "cgst_amount": bill.cgst_amount,
            "sgst_amount": bill.sgst_amount,
            "igst_amount": bill.igst_amount,
            "total_gst": bill.total_gst,
            "roundoff_amount": bill.roundoff_amount,
            "total_amount": bill.total_amount,
            "items_count": len(items),
            "customer_id": customer_id,
            "payment_method": payment_method,
            "status": "completed",
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating invoice: {str(e)}")


@router.post("/apply-discount")
async def apply_discount(
    db: Session = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user),
    discount_data: Dict[str, Any] = None,
):
    """
    Apply discounts: coupon codes, item-level %, order-level %, loyalty points
    """
    try:
        if not discount_data:
            raise HTTPException(status_code=400, detail="Discount data required")

        discount_type = discount_data.get("type")  # "coupon", "percent", "loyalty"
        discount_value = discount_data.get("value", 0)
        coupon_code = discount_data.get("coupon_code")

        # Mock coupon database
        valid_coupons = {
            "SAVE10": {"type": "percent", "value": 10},
            "SUMMER": {"type": "percent", "value": 15},
            "LOYAL": {"type": "percent", "value": 20},
            "NEWYEAR": {"type": "percent", "value": 25},
        }

        if discount_type == "coupon" and coupon_code:
            if coupon_code in valid_coupons:
                coupon_info = valid_coupons[coupon_code]
                return {
                    "valid": True,
                    "coupon_code": coupon_code,
                    "discount_type": coupon_info["type"],
                    "discount_percent": coupon_info["value"],
                    "message": f"Coupon {coupon_code} applied successfully",
                }
            else:
                return {
                    "valid": False,
                    "coupon_code": coupon_code,
                    "message": "Invalid or expired coupon code",
                }

        # Item-level discount
        if discount_type == "item":
            return {
                "valid": True,
                "discount_type": "item",
                "discount_percent": discount_value,
                "message": f"Item discount of {discount_value}% applied",
            }

        # Order-level discount
        if discount_type == "order":
            return {
                "valid": True,
                "discount_type": "order",
                "discount_percent": discount_value,
                "message": f"Order discount of {discount_value}% applied",
            }

        # Loyalty points redemption
        if discount_type == "loyalty":
            # 1 loyalty point = ₹1
            loyalty_discount = discount_value
            return {
                "valid": True,
                "discount_type": "loyalty",
                "discount_amount": loyalty_discount,
                "message": f"Loyalty discount of ₹{loyalty_discount} applied",
            }

        return {"valid": False, "message": "Invalid discount type"}

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error applying discount: {str(e)}"
        )


@router.get("/gst-summary")
async def get_gst_summary(
    db: Session = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user),
    period: str = "month",
):
    """
    Get GST summary for accounting/compliance: total CGST, SGST, IGST collected
    """
    try:
        store_id = current_user.active_store_id

        # Mock GST summary data
        return {
            "period": period,
            "timestamp": datetime.now().isoformat(),
            "store_id": store_id,
            "summary": {
                "total_cgst": 125000.50,
                "total_sgst": 125000.50,
                "total_igst": 0.0,
                "total_gst": 250001.00,
                "total_taxable": 1388900.00,
                "total_revenue": 1638901.00,
            },
            "by_category": {
                "Frames": {
                    "cgst": 45000.00,
                    "sgst": 45000.00,
                    "igst": 0.0,
                    "taxable": 500000.00,
                },
                "Lenses": {
                    "cgst": 50000.00,
                    "sgst": 50000.00,
                    "igst": 0.0,
                    "taxable": 555556.00,
                },
                "Contact Lenses": {
                    "cgst": 15000.00,
                    "sgst": 15000.00,
                    "igst": 0.0,
                    "taxable": 166667.00,
                },
                "Sunglasses": {
                    "cgst": 10000.50,
                    "sgst": 10000.50,
                    "igst": 0.0,
                    "taxable": 111111.00,
                },
                "Accessories": {
                    "cgst": 5000.00,
                    "sgst": 5000.00,
                    "igst": 0.0,
                    "taxable": 55555.00,
                },
            },
            "inter_state_sales": {
                "igst_collected": 0.0,
                "count": 0,
            },
            "filing_due": "10th of next month",
            "last_filing": "2025-01-10",
        }

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error fetching GST summary: {str(e)}"
        )


@router.get("/held-bills")
async def get_held_bills(
    db: Session = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user),
):
    """
    Get list of bills held for later completion
    Allows customers to recall incomplete transactions
    """
    try:
        store_id = current_user.active_store_id

        # Filter held bills for this store (mock data for now)
        return {
            "store_id": store_id,
            "held_bills": HELD_BILLS,
            "total_held": len(HELD_BILLS),
            "total_held_amount": sum(b["total_amount"] for b in HELD_BILLS),
        }

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error fetching held bills: {str(e)}"
        )


@router.post("/hold-bill")
async def hold_bill(
    db: Session = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user),
    bill_data: Dict[str, Any] = None,
):
    """
    Hold current bill for later completion
    Saves cart state temporarily
    """
    try:
        if not bill_data:
            raise HTTPException(status_code=400, detail="Bill data required")

        bill_id = f"BIL-{int(datetime.now().timestamp() * 1000)}"

        # Mock implementation - in production, would save to database
        return {
            "success": True,
            "bill_id": bill_id,
            "message": "Bill held successfully",
            "recall_instructions": "Use bill ID to recall this bill later",
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error holding bill: {str(e)}")


@router.post("/recall-held-bill")
async def recall_held_bill(
    db: Session = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user),
    bill_id: str = None,
):
    """
    Recall a previously held bill to continue shopping
    """
    try:
        if not bill_id:
            raise HTTPException(status_code=400, detail="Bill ID required")

        # Find the held bill
        held_bill = next((b for b in HELD_BILLS if b["bill_id"] == bill_id), None)

        if not held_bill:
            raise HTTPException(status_code=404, detail="Held bill not found")

        return {
            "success": True,
            "bill": held_bill,
            "message": "Bill recalled successfully",
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error recalling bill: {str(e)}")


@router.post("/process-payment")
async def process_payment(
    db: Session = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user),
    payment_data: Dict[str, Any] = None,
):
    """
    Process payment for completed bill
    Supports: Cash, Card, UPI, Wallet, Split payments
    """
    try:
        if not payment_data:
            raise HTTPException(status_code=400, detail="Payment data required")

        payment_method = payment_data.get("method")
        amount = payment_data.get("amount", 0)
        bill_id = payment_data.get("bill_id")

        if amount <= 0:
            raise HTTPException(status_code=400, detail="Invalid payment amount")

        # Mock payment processing
        return {
            "success": True,
            "payment_id": f"PAY-{int(datetime.now().timestamp() * 1000)}",
            "bill_id": bill_id,
            "amount": amount,
            "method": payment_method,
            "timestamp": datetime.now().isoformat(),
            "status": "completed",
            "message": f"Payment of ₹{amount} processed successfully via {payment_method}",
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error processing payment: {str(e)}"
        )

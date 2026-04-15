"""
Enterprise Billing Router
Comprehensive POS billing system with GST calculations, discounts, and payment processing
"""

from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime
from typing import Optional, List, Dict, Any
from .auth import get_current_user
from ..dependencies import (
    get_order_repository,
    get_customer_repository,
    get_db,
)
import uuid

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
        category: Optional[str] = None,
    ):
        self.product_id = product_id
        self.name = name
        self.unit_price = unit_price
        self.quantity = quantity
        self.discount_percent = discount_percent or 0
        self.category = category or ""


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
# Helper Functions
# ============================================================================


def _get_db():
    """Get raw MongoDB database for collections without a dedicated repository"""
    try:
        conn = get_db()
        if conn is not None and conn.is_connected:
            return conn.db
    except Exception:
        pass
    return None


def get_gst_rate_by_category(category: str) -> float:
    """
    Get GST rate based on product category.
    GST 2.0 rates (effective Sep 22, 2025):
      5% — Frames, Lenses, Spectacles, Contact Lenses
     18% — Sunglasses, Watches, Accessories, Services
    """
    category_upper = (category or "").upper().strip()
    five_percent_categories = {
        "FRAMES", "FRAME", "EYEGLASS_FRAME",
        "LENSES", "LENS", "RX_LENSES", "EYEGLASS_LENS", "OPTICAL_LENS",
        "CONTACT_LENSES", "CONTACT_LENS", "COLOUR_CONTACTS",
        "SPECTACLES", "SPECTACLE", "COMPLETE_SPECTACLE",
    }
    if category_upper in five_percent_categories:
        return 0.05
    # Sunglasses, Watches, Accessories, Services → 18%
    return 0.18


def calculate_bill(
    items: List[CartItemData],
    order_discount_percent: float = 0,
    use_igst: bool = False,
) -> BillCalculation:
    """Calculate bill with per-item GST rates based on product category"""
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

    # Calculate GST per item using category-based rates
    for item in items:
        gst_rate = get_gst_rate_by_category(item.category)
        item_total = item.unit_price * item.quantity
        item_discount = item_total * (item.discount_percent / 100) if item.discount_percent > 0 else 0
        item_after_discount = item_total - item_discount
        # Apply order-level discount proportionally
        if bill.subtotal_after_discount > 0:
            item_taxable = item_after_discount * (1 - order_discount_percent / 100)
        else:
            item_taxable = 0

        if use_igst:
            bill.igst_amount += item_taxable * gst_rate
        else:
            # Split GST equally (CGST + SGST) for intra-state
            bill.cgst_amount += item_taxable * (gst_rate / 2)
            bill.sgst_amount += item_taxable * (gst_rate / 2)

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



@router.get("/")
async def get_billing_root():
    """Root endpoint for billing/invoice list"""
    return {"module": "billing", "status": "active", "message": "billing records endpoint ready"}


@router.post("/create-invoice")
async def create_invoice(
    current_user: dict = Depends(get_current_user),
    invoice_data: Dict[str, Any] = None,
):
    """
    Create a new invoice/bill with items and discounts
    Supports GST calculations (CGST+SGST or IGST), multiple payment methods
    """
    try:
        if not invoice_data:
            raise HTTPException(status_code=400, detail="Invoice data required")

        items = invoice_data.get("items", [])
        customer_id = invoice_data.get("customer_id")
        order_discount = invoice_data.get("order_discount", 0)
        use_igst = invoice_data.get("use_igst", False)
        payment_method = invoice_data.get("payment_method", "cash")
        store_id = current_user.get("active_store_id")

        # GST compliance: block invoice if store GSTIN is missing
        if store_id:
            db = _get_db()
            if db is not None:
                try:
                    store_doc = db["stores"].find_one({"store_id": store_id})
                    if store_doc and not store_doc.get("gstin"):
                        raise HTTPException(
                            status_code=400,
                            detail="Cannot generate invoice: store GSTIN is not configured. "
                                   "Update store settings with a valid GSTIN first."
                        )
                except HTTPException:
                    raise
                except Exception:
                    pass

        # Convert items to CartItemData
        cart_items = [
            CartItemData(
                product_id=item["product_id"],
                name=item["name"],
                unit_price=item["unit_price"],
                quantity=item["quantity"],
                discount_percent=item.get("discount_percent", 0),
                category=item.get("category", ""),
            )
            for item in items
        ]

        # Validate: offer_price cannot exceed MRP (FIX 3)
        for item in items:
            if item.get("offer_price", 0) > item.get("mrp", 0) and item.get("mrp", 0) > 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"Offer price (₹{item['offer_price']}) cannot exceed MRP (₹{item['mrp']}) for {item.get('name', 'item')}"
                )

        # Calculate bill
        bill = calculate_bill(cart_items, order_discount, use_igst)

        # Generate bill number
        bill_number = f"BIL-{int(datetime.now().timestamp() * 1000)}"
        bill_id = str(uuid.uuid4())

        # Prepare order document
        order_doc = {
            "order_id": bill_id,
            "bill_number": bill_number,
            "store_id": store_id,
            "customer_id": customer_id,
            "items": items,
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
            "payment_method": payment_method,
            "payment_status": "pending",
            "status": "completed",
            "created_by": current_user.get("user_id"),
            "created_at": datetime.utcnow(),
        }

        # Save to orders collection
        db = _get_db()
        if db is not None:
            try:
                db["orders"].insert_one(order_doc)
            except Exception as e:
                # Log but continue - return data even if DB save fails
                pass

        return {
            "bill_id": bill_id,
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

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Error creating invoice. Please try again.")


@router.post("/apply-discount")
async def apply_discount(
    current_user: dict = Depends(get_current_user),
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
        product_id = discount_data.get("product_id")

        # Enforce user discount cap (FIX 2)
        if discount_type in ("item", "order") and discount_value > 0:
            user_discount_cap = current_user.get("discount_cap", 10.0)
            if discount_value > user_discount_cap:
                raise HTTPException(
                    status_code=403,
                    detail=f"Discount {discount_value}% exceeds your cap of {user_discount_cap}%"
                )

            # Enforce category-based discount cap if product_id provided
            if product_id:
                db = _get_db()
                if db is not None:
                    try:
                        product = db["products"].find_one({"product_id": product_id})
                        if product:
                            category_caps = {"LUXURY": 2.0, "PREMIUM": 5.0, "MASS": 10.0, "NON_DISCOUNTABLE": 0.0}
                            category_cap = category_caps.get(product.get("discount_category", "MASS"), 10.0)
                            effective_cap = min(user_discount_cap, category_cap)
                            if discount_value > effective_cap:
                                raise HTTPException(
                                    status_code=403,
                                    detail=f"Discount {discount_value}% exceeds limit of {effective_cap}% for this product category"
                                )
                    except HTTPException:
                        raise
                    except Exception:
                        pass

        if discount_type == "coupon" and coupon_code:
            # Query discount_rules collection for valid coupons
            db = _get_db()
            coupon_info = None

            if db is not None:
                try:
                    collection = db["discount_rules"]
                    coupon_doc = collection.find_one({"code": coupon_code, "active": True})
                    if coupon_doc:
                        coupon_info = {
                            "type": coupon_doc.get("discount_type", "percent"),
                            "value": coupon_doc.get("discount_value", 0)
                        }
                except Exception:
                    pass

            if coupon_info:
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
            status_code=500, detail="Error applying discount. Please try again."
        )


@router.get("/gst-summary")
async def get_gst_summary(
    current_user: dict = Depends(get_current_user),
    period: str = "month",
):
    """
    Get GST summary for accounting/compliance: total CGST, SGST, IGST collected
    """
    try:
        store_id = current_user.get("active_store_id")
        db = _get_db()

        # Initialize response structure
        summary_response = {
            "period": period,
            "timestamp": datetime.now().isoformat(),
            "store_id": store_id,
            "summary": {
                "total_cgst": 0.0,
                "total_sgst": 0.0,
                "total_igst": 0.0,
                "total_gst": 0.0,
                "total_taxable": 0.0,
                "total_revenue": 0.0,
            },
            "by_category": {},
            "inter_state_sales": {
                "igst_collected": 0.0,
                "count": 0,
            },
            "filing_due": "10th of next month",
            "last_filing": None,
        }

        # Query MongoDB if available
        if db is not None:
            try:
                collection = db["orders"]

                # Build aggregation pipeline
                pipeline = [
                    {"$match": {"store_id": store_id, "status": "completed"}},
                    {
                        "$group": {
                            "_id": None,
                            "total_cgst": {"$sum": "$cgst_amount"},
                            "total_sgst": {"$sum": "$sgst_amount"},
                            "total_igst": {"$sum": "$igst_amount"},
                            "total_taxable": {"$sum": "$taxable_amount"},
                        }
                    }
                ]

                result = list(collection.aggregate(pipeline))
                if result and result[0]:
                    agg = result[0]
                    summary_response["summary"]["total_cgst"] = agg.get("total_cgst", 0.0)
                    summary_response["summary"]["total_sgst"] = agg.get("total_sgst", 0.0)
                    summary_response["summary"]["total_igst"] = agg.get("total_igst", 0.0)
                    summary_response["summary"]["total_taxable"] = agg.get("total_taxable", 0.0)

                    total_gst = agg.get("total_cgst", 0.0) + agg.get("total_sgst", 0.0) + agg.get("total_igst", 0.0)
                    summary_response["summary"]["total_gst"] = total_gst
                    summary_response["summary"]["total_revenue"] = agg.get("total_taxable", 0.0) + total_gst

                # Aggregate by category
                category_pipeline = [
                    {"$match": {"store_id": store_id, "status": "completed"}},
                    {"$unwind": "$items"},
                    {
                        "$group": {
                            "_id": "$items.category",
                            "cgst": {"$sum": {"$multiply": ["$cgst_amount", {"$divide": ["$items.unit_price", "$taxable_amount"]}]}},
                            "sgst": {"$sum": {"$multiply": ["$sgst_amount", {"$divide": ["$items.unit_price", "$taxable_amount"]}]}},
                            "igst": {"$sum": {"$multiply": ["$igst_amount", {"$divide": ["$items.unit_price", "$taxable_amount"]}]}},
                            "taxable": {"$sum": {"$multiply": ["$taxable_amount", {"$divide": ["$items.unit_price", "$taxable_amount"]}]}},
                        }
                    }
                ]

                category_results = list(collection.aggregate(category_pipeline))
                for cat_result in category_results:
                    category = cat_result.get("_id") or "Unknown"
                    summary_response["by_category"][category] = {
                        "cgst": cat_result.get("cgst", 0.0),
                        "sgst": cat_result.get("sgst", 0.0),
                        "igst": cat_result.get("igst", 0.0),
                        "taxable": cat_result.get("taxable", 0.0),
                    }

                # Count inter-state sales (IGST > 0)
                igst_count = collection.count_documents({"store_id": store_id, "igst_amount": {"$gt": 0}})
                summary_response["inter_state_sales"]["count"] = igst_count
                summary_response["inter_state_sales"]["igst_collected"] = summary_response["summary"]["total_igst"]

            except Exception as e:
                # Fall back to empty summary if query fails
                pass

        return summary_response

    except Exception as e:
        raise HTTPException(
            status_code=500, detail="Error fetching GST summary. Please try again."
        )


@router.get("/held-bills")
async def get_held_bills(
    current_user: dict = Depends(get_current_user),
):
    """
    Get list of bills held for later completion
    Allows customers to recall incomplete transactions
    """
    try:
        store_id = current_user.get("active_store_id")
        db = _get_db()

        held_bills = []
        total_held_amount = 0.0

        if db is not None:
            try:
                collection = db["held_bills"]
                bills = collection.find({"store_id": store_id, "status": "held"})

                for bill in bills:
                    held_bills.append({
                        "bill_id": bill.get("bill_id"),
                        "bill_number": bill.get("bill_number"),
                        "created_at": bill.get("created_at"),
                        "customer_id": bill.get("customer_id"),
                        "items_count": len(bill.get("items", [])),
                        "total_amount": bill.get("total_amount", 0.0),
                    })
                    total_held_amount += bill.get("total_amount", 0.0)
            except Exception:
                pass

        return {
            "store_id": store_id,
            "held_bills": held_bills,
            "total_held": len(held_bills),
            "total_held_amount": total_held_amount,
        }

    except Exception as e:
        raise HTTPException(
            status_code=500, detail="Error fetching held bills. Please try again."
        )


@router.post("/hold-bill")
async def hold_bill(
    current_user: dict = Depends(get_current_user),
    bill_data: Dict[str, Any] = None,
):
    """
    Hold current bill for later completion
    Saves cart state temporarily
    """
    try:
        if not bill_data:
            raise HTTPException(status_code=400, detail="Bill data required")

        store_id = current_user.get("active_store_id")
        bill_id = str(uuid.uuid4())

        # Extract bill data
        items = bill_data.get("items", [])
        customer_id = bill_data.get("customer_id")
        total_amount = bill_data.get("total_amount", 0.0)

        # Prepare held bill document
        held_bill_doc = {
            "bill_id": bill_id,
            "bill_number": f"HELD-{int(datetime.now().timestamp() * 1000)}",
            "store_id": store_id,
            "customer_id": customer_id,
            "items": items,
            "total_amount": total_amount,
            "status": "held",
            "created_by": current_user.get("user_id"),
            "created_at": datetime.utcnow(),
        }

        # Save to held_bills collection
        db = _get_db()
        if db is not None:
            try:
                db["held_bills"].insert_one(held_bill_doc)
            except Exception as e:
                # Log but continue - return success even if DB save fails
                pass

        return {
            "success": True,
            "bill_id": bill_id,
            "message": "Bill held successfully",
            "recall_instructions": "Use bill ID to recall this bill later",
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail="Error holding bill. Please try again.")


@router.post("/recall-held-bill")
async def recall_held_bill(
    current_user: dict = Depends(get_current_user),
    bill_id: str = None,
):
    """
    Recall a previously held bill to continue shopping
    """
    try:
        if not bill_id:
            raise HTTPException(status_code=400, detail="Bill ID required")

        db = _get_db()
        held_bill = None

        if db is not None:
            try:
                collection = db["held_bills"]
                held_bill = collection.find_one({"bill_id": bill_id})

                # Update status to recalled
                if held_bill:
                    collection.update_one(
                        {"bill_id": bill_id},
                        {"$set": {"status": "recalled", "recalled_at": datetime.utcnow()}}
                    )
            except Exception:
                pass

        if not held_bill:
            raise HTTPException(status_code=404, detail="Held bill not found")

        return {
            "success": True,
            "bill": {
                "bill_id": held_bill.get("bill_id"),
                "bill_number": held_bill.get("bill_number"),
                "customer_id": held_bill.get("customer_id"),
                "items": held_bill.get("items", []),
                "total_amount": held_bill.get("total_amount"),
            },
            "message": "Bill recalled successfully",
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Error recalling bill. Please try again.")


@router.post("/process-payment")
async def process_payment(
    current_user: dict = Depends(get_current_user),
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
        store_id = current_user.get("active_store_id")

        if amount <= 0:
            raise HTTPException(status_code=400, detail="Invalid payment amount")

        payment_id = str(uuid.uuid4())
        now = datetime.utcnow()

        # Prepare payment document
        payment_doc = {
            "payment_id": payment_id,
            "bill_id": bill_id,
            "amount": amount,
            "method": payment_method,
            "store_id": store_id,
            "processed_by": current_user.get("user_id"),
            "timestamp": now,
            "status": "completed",
        }

        # Save to payments collection
        db = _get_db()
        if db is not None:
            try:
                db["payments"].insert_one(payment_doc)

                # Update order's payment_status
                db["orders"].update_one(
                    {"order_id": bill_id},
                    {
                        "$set": {
                            "payment_status": "paid",
                            "payment_method": payment_method,
                            "payment_date": now,
                        }
                    }
                )
            except Exception:
                # Log but continue - return success even if DB save fails
                pass

        return {
            "success": True,
            "payment_id": payment_id,
            "bill_id": bill_id,
            "amount": amount,
            "method": payment_method,
            "timestamp": now.isoformat(),
            "status": "completed",
            "message": f"Payment of ₹{amount} processed successfully via {payment_method}",
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail="Error processing payment. Please try again."
        )

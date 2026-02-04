"""
IMS 2.0 - POS Sale Flow Engine
==============================
Implements YOUR exact sale workflow:
Customer walks in → chooses frame → eye test → chooses lens → pays advance → comes later → collects

Key features:
1. Prescription MUST be attached before lens selection
2. MRP vs Offer Price enforcement
3. Role-based discount validation
4. Partial payment (advance + final)
5. Order status tracking
"""

from dataclasses import dataclass, field
from datetime import datetime, date
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import List, Optional, Dict
import uuid

# Import our pricing engine
from pricing_engine import PricingEngine, Product, DiscountClass, Role, DiscountRequest, PricingDecision


class OrderStatus(Enum):
    DRAFT = "DRAFT"                    # Being created
    CONFIRMED = "CONFIRMED"            # Customer confirmed, advance paid
    IN_PROGRESS = "IN_PROGRESS"        # Lens being made/order processing
    READY = "READY"                    # Ready for pickup
    DELIVERED = "DELIVERED"            # Customer collected
    CANCELLED = "CANCELLED"            # Order cancelled


class PaymentStatus(Enum):
    PENDING = "PENDING"                # No payment made
    PARTIAL = "PARTIAL"                # Advance paid, balance due
    PAID = "PAID"                      # Fully paid


class PaymentMethod(Enum):
    CASH = "CASH"
    UPI = "UPI"
    CARD = "CARD"
    BANK_TRANSFER = "BANK_TRANSFER"
    EMI = "EMI"
    CREDIT = "CREDIT"                  # For known customers
    GIFT_VOUCHER = "GIFT_VOUCHER"


class ItemType(Enum):
    FRAME = "FRAME"
    SUNGLASS = "SUNGLASS"
    READING_GLASSES = "READING_GLASSES"
    OPTICAL_LENS = "OPTICAL_LENS"
    CONTACT_LENS = "CONTACT_LENS"
    COLORED_CONTACT_LENS = "COLORED_CONTACT_LENS"
    ACCESSORY = "ACCESSORY"
    SERVICE = "SERVICE"
    WATCH = "WATCH"
    SMARTWATCH = "SMARTWATCH"
    SMARTGLASSES = "SMARTGLASSES"
    WALL_CLOCK = "WALL_CLOCK"


@dataclass
class Customer:
    id: str
    name: str
    phone: str
    email: Optional[str] = None
    customer_type: str = "B2C"  # B2C or B2B
    gst_number: Optional[str] = None


@dataclass
class Patient:
    id: str
    customer_id: str
    name: str
    phone: Optional[str] = None
    date_of_birth: Optional[date] = None


@dataclass
class Prescription:
    id: str
    patient_id: str
    prescription_date: date
    
    # Right Eye
    r_sph: Optional[Decimal] = None
    r_cyl: Optional[Decimal] = None
    r_axis: Optional[int] = None
    r_add: Optional[Decimal] = None
    
    # Left Eye
    l_sph: Optional[Decimal] = None
    l_cyl: Optional[Decimal] = None
    l_axis: Optional[int] = None
    l_add: Optional[Decimal] = None
    
    # Metadata
    optometrist_id: Optional[str] = None
    optometrist_name: Optional[str] = None
    source: str = "STORE"  # STORE or EXTERNAL
    valid_until: Optional[date] = None
    remarks: Optional[str] = None


@dataclass
class OrderItem:
    id: str
    item_type: ItemType
    
    # Product reference
    product_id: Optional[str] = None
    product_name: str = ""
    product_sku: Optional[str] = None
    
    # For lens items - prescription is MANDATORY
    prescription_id: Optional[str] = None
    prescription: Optional[Prescription] = None
    
    # Lens details (for optical lens)
    lens_type: Optional[str] = None       # Single Vision, Bifocal, Progressive
    lens_material: Optional[str] = None   # CR39, Polycarbonate, 1.67, 1.74
    lens_coating: Optional[str] = None    # Anti-reflective, Blue cut, Photochromic
    lens_tint: Optional[str] = None
    
    # Quantity
    quantity: int = 1
    
    # Pricing
    mrp: Decimal = Decimal("0")
    offer_price: Decimal = Decimal("0")
    
    # Discount
    discount_percent: Decimal = Decimal("0")
    discount_amount: Decimal = Decimal("0")
    discount_approved_by: Optional[str] = None
    discount_reason: Optional[str] = None
    
    # Final pricing
    unit_price: Decimal = Decimal("0")
    gst_rate: Decimal = Decimal("18.00")
    gst_amount: Decimal = Decimal("0")
    line_total: Decimal = Decimal("0")
    
    # Status
    item_status: str = "PENDING"  # PENDING, ORDERED, RECEIVED, FITTED, DELIVERED
    
    def calculate_totals(self):
        """Calculate line totals based on pricing"""
        self.discount_amount = (self.offer_price * self.discount_percent / 100).quantize(Decimal("0.01"))
        self.unit_price = self.offer_price - self.discount_amount
        subtotal = self.unit_price * self.quantity
        self.gst_amount = (subtotal * self.gst_rate / 100).quantize(Decimal("0.01"))
        self.line_total = subtotal + self.gst_amount


@dataclass
class Payment:
    id: str
    amount: Decimal
    method: PaymentMethod
    payment_date: datetime = field(default_factory=datetime.now)
    transaction_reference: Optional[str] = None
    payment_type: str = "ADVANCE"  # ADVANCE, FINAL, REFUND
    received_by: Optional[str] = None
    notes: Optional[str] = None


@dataclass
class SaleOrder:
    id: str
    order_number: str
    store_id: str
    
    # Customer & Patient
    customer: Customer
    patient: Patient
    
    # Staff
    sales_person_id: str
    sales_person_name: str
    
    # Order details
    order_date: datetime = field(default_factory=datetime.now)
    reference_number: Optional[str] = None
    
    # Items
    items: List[OrderItem] = field(default_factory=list)
    
    # Totals
    subtotal: Decimal = Decimal("0")
    total_discount: Decimal = Decimal("0")
    total_gst: Decimal = Decimal("0")
    round_off: Decimal = Decimal("0")
    grand_total: Decimal = Decimal("0")
    
    # Payments
    payments: List[Payment] = field(default_factory=list)
    amount_paid: Decimal = Decimal("0")
    balance_due: Decimal = Decimal("0")
    payment_status: PaymentStatus = PaymentStatus.PENDING
    
    # Status
    status: OrderStatus = OrderStatus.DRAFT
    
    # Delivery
    expected_delivery_date: Optional[date] = None
    actual_delivery_date: Optional[date] = None
    
    # Notes
    internal_notes: Optional[str] = None
    customer_notes: Optional[str] = None
    
    def add_item(self, item: OrderItem):
        """Add item to order and recalculate totals"""
        item.calculate_totals()
        self.items.append(item)
        self._recalculate_totals()
    
    def remove_item(self, item_id: str) -> bool:
        """Remove item from order"""
        for i, item in enumerate(self.items):
            if item.id == item_id:
                del self.items[i]
                self._recalculate_totals()
                return True
        return False
    
    def add_payment(self, payment: Payment):
        """Add payment and update payment status"""
        self.payments.append(payment)
        self.amount_paid += payment.amount
        self.balance_due = self.grand_total - self.amount_paid
        
        if self.balance_due <= 0:
            self.payment_status = PaymentStatus.PAID
        elif self.amount_paid > 0:
            self.payment_status = PaymentStatus.PARTIAL
    
    def _recalculate_totals(self):
        """Recalculate all order totals"""
        self.subtotal = sum(item.unit_price * item.quantity for item in self.items)
        self.total_discount = sum(item.discount_amount * item.quantity for item in self.items)
        self.total_gst = sum(item.gst_amount for item in self.items)
        
        total_before_round = self.subtotal + self.total_gst
        # Round to nearest rupee
        rounded = total_before_round.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        self.round_off = rounded - total_before_round
        self.grand_total = rounded
        
        self.balance_due = self.grand_total - self.amount_paid


class POSSaleFlowEngine:
    """
    Manages the complete POS sale flow with all validations.
    
    WORKFLOW:
    1. Select/Create Customer
    2. Select/Create Patient
    3. Add Products:
       - For Frame Only: Just add frame
       - For Frame + Lens: 
         a. Select/Create Prescription (MANDATORY for lens)
         b. Configure lens options
         c. Select frame
       - For Contact Lens: Similar flow with prescription
    4. Apply Discounts (with role validation)
    5. Collect Advance Payment
    6. Confirm Order
    7. Later: Collect Final Payment & Deliver
    """
    
    def __init__(self, pricing_engine: PricingEngine):
        self.pricing_engine = pricing_engine
        self.active_orders: Dict[str, SaleOrder] = {}
    
    def generate_order_number(self, store_code: str) -> str:
        """Generate unique order number"""
        date_part = datetime.now().strftime("%Y%m%d")
        random_part = uuid.uuid4().hex[:6].upper()
        return f"{store_code}-{date_part}-{random_part}"
    
    def start_new_sale(
        self,
        store_id: str,
        store_code: str,
        customer: Customer,
        patient: Patient,
        sales_person_id: str,
        sales_person_name: str
    ) -> SaleOrder:
        """Start a new sale order"""
        order = SaleOrder(
            id=str(uuid.uuid4()),
            order_number=self.generate_order_number(store_code),
            store_id=store_id,
            customer=customer,
            patient=patient,
            sales_person_id=sales_person_id,
            sales_person_name=sales_person_name
        )
        self.active_orders[order.id] = order
        return order
    
    def add_frame_only(
        self,
        order_id: str,
        product: Product,
        quantity: int = 1
    ) -> tuple:
        """Add a frame without lens (e.g., sunglass purchase)"""
        order = self.active_orders.get(order_id)
        if not order:
            return False, "Order not found", None
        
        # Validate product pricing
        is_valid, msg = self.pricing_engine.validate_product_pricing(product)
        if not is_valid:
            return False, msg, None
        
        item = OrderItem(
            id=str(uuid.uuid4()),
            item_type=ItemType.FRAME,
            product_id=product.id,
            product_name=product.name,
            product_sku=product.sku,
            quantity=quantity,
            mrp=product.mrp,
            offer_price=product.offer_price
        )
        
        order.add_item(item)
        return True, "Frame added", item
    
    def add_frame_with_lens(
        self,
        order_id: str,
        frame_product: Product,
        prescription: Prescription,
        lens_type: str,
        lens_material: str,
        lens_coating: str,
        lens_mrp: Decimal,
        lens_offer_price: Decimal,
        lens_tint: Optional[str] = None
    ) -> tuple:
        """
        Add frame with prescription lens.
        PRESCRIPTION IS MANDATORY for lens orders.
        """
        order = self.active_orders.get(order_id)
        if not order:
            return False, "Order not found", None
        
        # CRITICAL: Prescription is mandatory for lens
        if not prescription:
            return False, "BLOCKED: Prescription is MANDATORY for lens orders", None
        
        if not prescription.id:
            return False, "BLOCKED: Invalid prescription", None
        
        # Validate prescription belongs to the patient
        if prescription.patient_id != order.patient.id:
            return False, "BLOCKED: Prescription does not belong to this patient", None
        
        # Validate prescription is not expired
        if prescription.valid_until and prescription.valid_until < date.today():
            return False, f"BLOCKED: Prescription expired on {prescription.valid_until}", None
        
        # Validate frame pricing
        is_valid, msg = self.pricing_engine.validate_product_pricing(frame_product)
        if not is_valid:
            return False, msg, None
        
        # Validate lens pricing (offer price cannot exceed MRP)
        if lens_offer_price > lens_mrp:
            return False, f"BLOCKED: Lens offer price (₹{lens_offer_price}) cannot exceed MRP (₹{lens_mrp})", None
        
        # Add frame item
        frame_item = OrderItem(
            id=str(uuid.uuid4()),
            item_type=ItemType.FRAME,
            product_id=frame_product.id,
            product_name=frame_product.name,
            product_sku=frame_product.sku,
            quantity=1,
            mrp=frame_product.mrp,
            offer_price=frame_product.offer_price,
            prescription_id=prescription.id,
            prescription=prescription
        )
        order.add_item(frame_item)
        
        # Add lens item
        lens_item = OrderItem(
            id=str(uuid.uuid4()),
            item_type=ItemType.OPTICAL_LENS,
            product_name=f"{lens_type} - {lens_material} - {lens_coating}",
            prescription_id=prescription.id,
            prescription=prescription,
            lens_type=lens_type,
            lens_material=lens_material,
            lens_coating=lens_coating,
            lens_tint=lens_tint,
            quantity=1,  # Pair of lenses
            mrp=lens_mrp,
            offer_price=lens_offer_price
        )
        order.add_item(lens_item)
        
        return True, "Frame and lens added with prescription", (frame_item, lens_item)
    
    def add_contact_lens(
        self,
        order_id: str,
        product: Product,
        prescription: Prescription,
        quantity: int = 1
    ) -> tuple:
        """Add contact lens (prescription mandatory)"""
        order = self.active_orders.get(order_id)
        if not order:
            return False, "Order not found", None
        
        # CRITICAL: Prescription is mandatory
        if not prescription:
            return False, "BLOCKED: Prescription is MANDATORY for contact lens", None
        
        # Validate product
        is_valid, msg = self.pricing_engine.validate_product_pricing(product)
        if not is_valid:
            return False, msg, None
        
        item = OrderItem(
            id=str(uuid.uuid4()),
            item_type=ItemType.CONTACT_LENS,
            product_id=product.id,
            product_name=product.name,
            product_sku=product.sku,
            prescription_id=prescription.id,
            prescription=prescription,
            quantity=quantity,
            mrp=product.mrp,
            offer_price=product.offer_price
        )
        
        order.add_item(item)
        return True, "Contact lens added", item
    
    def apply_discount(
        self,
        order_id: str,
        item_id: str,
        discount_percent: Decimal,
        user_role: Role,
        reason: Optional[str] = None
    ) -> tuple:
        """
        Apply discount to an item with role-based validation.
        Uses the pricing engine for all discount rules.
        """
        order = self.active_orders.get(order_id)
        if not order:
            return False, "Order not found", None
        
        # Find item
        item = None
        for i in order.items:
            if i.id == item_id:
                item = i
                break
        
        if not item:
            return False, "Item not found", None
        
        # Determine discount class based on item type
        discount_class_map = {
            ItemType.FRAME: DiscountClass.PREMIUM,
            ItemType.SUNGLASS: DiscountClass.LUXURY,
            ItemType.READING_GLASSES: DiscountClass.MASS,
            ItemType.OPTICAL_LENS: DiscountClass.PREMIUM,
            ItemType.CONTACT_LENS: DiscountClass.MASS,
            ItemType.COLORED_CONTACT_LENS: DiscountClass.MASS,
            ItemType.ACCESSORY: DiscountClass.MASS,
            ItemType.SERVICE: DiscountClass.SERVICE,
            ItemType.WATCH: DiscountClass.LUXURY,
            ItemType.SMARTWATCH: DiscountClass.PREMIUM,
            ItemType.SMARTGLASSES: DiscountClass.LUXURY,
            ItemType.WALL_CLOCK: DiscountClass.MASS,
        }
        
        # Create product for pricing engine
        product = Product(
            id=item.product_id or item.id,
            sku=item.product_sku or "CUSTOM",
            name=item.product_name,
            mrp=item.mrp,
            offer_price=item.offer_price,
            category_code=item.item_type.value,
            discount_class=discount_class_map.get(item.item_type, DiscountClass.PREMIUM)
        )
        
        # Create discount request
        request = DiscountRequest(
            product=product,
            requested_discount_percent=discount_percent,
            user_role=user_role,
            store_id=order.store_id,
            reason=reason
        )
        
        # Get pricing decision
        result = self.pricing_engine.calculate_pricing(request)
        
        if result.decision == PricingDecision.BLOCKED:
            return False, result.reason, None
        
        if result.decision == PricingDecision.REQUIRES_APPROVAL:
            return False, result.reason, {
                "approval_required": True,
                "approval_id": result.approval_id,
                "max_allowed": result.allowed_discount_percent,
                "requires_approval_from": result.requires_approval_from_role
            }
        
        # Apply the discount
        item.discount_percent = result.allowed_discount_percent
        item.discount_reason = reason
        item.calculate_totals()
        order._recalculate_totals()
        
        return True, result.reason, {
            "discount_applied": result.allowed_discount_percent,
            "final_price": result.final_unit_price,
            "discount_amount": result.discount_amount
        }
    
    def collect_payment(
        self,
        order_id: str,
        amount: Decimal,
        method: PaymentMethod,
        received_by: str,
        transaction_reference: Optional[str] = None,
        notes: Optional[str] = None
    ) -> tuple:
        """Collect payment (advance or final)"""
        order = self.active_orders.get(order_id)
        if not order:
            return False, "Order not found", None
        
        if amount <= 0:
            return False, "Invalid payment amount", None
        
        if amount > order.balance_due:
            return False, f"Amount (₹{amount}) exceeds balance due (₹{order.balance_due})", None
        
        # Determine payment type
        payment_type = "FINAL" if order.amount_paid > 0 else "ADVANCE"
        if amount >= order.balance_due:
            payment_type = "FINAL"
        
        payment = Payment(
            id=str(uuid.uuid4()),
            amount=amount,
            method=method,
            payment_type=payment_type,
            received_by=received_by,
            transaction_reference=transaction_reference,
            notes=notes
        )
        
        order.add_payment(payment)
        
        return True, f"Payment of ₹{amount} received ({payment_type})", {
            "payment_id": payment.id,
            "amount_paid_total": order.amount_paid,
            "balance_due": order.balance_due,
            "payment_status": order.payment_status.value
        }
    
    def confirm_order(self, order_id: str, expected_delivery_days: int = 7) -> tuple:
        """Confirm order after advance payment"""
        order = self.active_orders.get(order_id)
        if not order:
            return False, "Order not found"
        
        if order.status != OrderStatus.DRAFT:
            return False, f"Order already {order.status.value}"
        
        if not order.items:
            return False, "Cannot confirm empty order"
        
        if order.payment_status == PaymentStatus.PENDING:
            return False, "At least advance payment required to confirm order"
        
        order.status = OrderStatus.CONFIRMED
        order.expected_delivery_date = date.today() + __import__('datetime').timedelta(days=expected_delivery_days)
        
        return True, f"Order confirmed. Expected delivery: {order.expected_delivery_date}"
    
    def mark_ready_for_delivery(self, order_id: str) -> tuple:
        """Mark order as ready for pickup"""
        order = self.active_orders.get(order_id)
        if not order:
            return False, "Order not found"
        
        if order.status not in [OrderStatus.CONFIRMED, OrderStatus.IN_PROGRESS]:
            return False, f"Order must be confirmed first (current: {order.status.value})"
        
        order.status = OrderStatus.READY
        return True, "Order is ready for delivery. Notify customer."
    
    def deliver_order(self, order_id: str) -> tuple:
        """Complete order delivery"""
        order = self.active_orders.get(order_id)
        if not order:
            return False, "Order not found"
        
        if order.status != OrderStatus.READY:
            return False, f"Order must be ready first (current: {order.status.value})"
        
        if order.payment_status != PaymentStatus.PAID:
            return False, f"Full payment required. Balance due: ₹{order.balance_due}"
        
        order.status = OrderStatus.DELIVERED
        order.actual_delivery_date = date.today()
        
        return True, "Order delivered successfully"
    
    def get_order_summary(self, order_id: str) -> Optional[dict]:
        """Get complete order summary"""
        order = self.active_orders.get(order_id)
        if not order:
            return None
        
        return {
            "order_number": order.order_number,
            "status": order.status.value,
            "customer": order.customer.name,
            "patient": order.patient.name,
            "items": [
                {
                    "type": item.item_type.value,
                    "name": item.product_name,
                    "quantity": item.quantity,
                    "mrp": float(item.mrp),
                    "discount": float(item.discount_percent),
                    "unit_price": float(item.unit_price),
                    "gst": float(item.gst_amount),
                    "total": float(item.line_total),
                    "has_prescription": item.prescription_id is not None
                }
                for item in order.items
            ],
            "subtotal": float(order.subtotal),
            "total_discount": float(order.total_discount),
            "total_gst": float(order.total_gst),
            "round_off": float(order.round_off),
            "grand_total": float(order.grand_total),
            "amount_paid": float(order.amount_paid),
            "balance_due": float(order.balance_due),
            "payment_status": order.payment_status.value,
            "expected_delivery": str(order.expected_delivery_date) if order.expected_delivery_date else None
        }


# =============================================================================
# DEMO
# =============================================================================

def demo_pos_flow():
    """Demonstrate complete POS flow with your exact workflow"""
    
    print("=" * 70)
    print("IMS 2.0 POS SALE FLOW - DEMO")
    print("Your workflow: Customer → Frame → Eye Test → Lens → Advance → Collect")
    print("=" * 70)
    
    # Initialize
    pricing_engine = PricingEngine()
    pos = POSSaleFlowEngine(pricing_engine)
    
    # Define role
    sales_staff = Role(
        code="SALES_STAFF",
        name="Sales Staff",
        max_discount_percent=Decimal("10"),
        hierarchy_level=6,
        can_approve_discounts=False
    )
    
    # Create customer
    customer = Customer(
        id="cust-001",
        name="Rajesh Kumar",
        phone="9876543210",
        email="rajesh@email.com"
    )
    
    patient = Patient(
        id="pat-001",
        customer_id="cust-001",
        name="Rajesh Kumar",
        phone="9876543210"
    )
    
    # Create prescription (from eye test)
    prescription = Prescription(
        id="rx-001",
        patient_id="pat-001",
        prescription_date=date.today(),
        r_sph=Decimal("-2.50"),
        r_cyl=Decimal("-0.75"),
        r_axis=180,
        r_add=None,
        l_sph=Decimal("-2.25"),
        l_cyl=Decimal("-0.50"),
        l_axis=175,
        l_add=None,
        optometrist_id="opt-001",
        optometrist_name="Dr. Sharma",
        source="STORE"
    )
    
    # Frame product
    frame = Product(
        id="prod-001",
        sku="RB-5154-2000",
        name="Ray-Ban RB5154 Clubmaster",
        mrp=Decimal("7490.00"),
        offer_price=Decimal("7490.00"),
        category_code="FRAME",
        discount_class=DiscountClass.PREMIUM,
        brand_code="RAYBAN"
    )
    
    # -------------------------------------------------------------------------
    # STEP 1: Start new sale
    # -------------------------------------------------------------------------
    print("\n📋 STEP 1: Start New Sale")
    print("-" * 50)
    
    order = pos.start_new_sale(
        store_id="store-bv-001",
        store_code="BV-BKR",
        customer=customer,
        patient=patient,
        sales_person_id="emp-003",
        sales_person_name="Rahul Kumar"
    )
    print(f"Order Number: {order.order_number}")
    print(f"Customer: {customer.name}")
    print(f"Patient: {patient.name}")
    
    # -------------------------------------------------------------------------
    # STEP 2: Try to add lens WITHOUT prescription (should fail)
    # -------------------------------------------------------------------------
    print("\n📋 STEP 2: Try adding lens WITHOUT prescription")
    print("-" * 50)
    
    success, msg, _ = pos.add_frame_with_lens(
        order_id=order.id,
        frame_product=frame,
        prescription=None,  # No prescription!
        lens_type="Progressive",
        lens_material="1.67 High Index",
        lens_coating="Blue Cut Anti-Reflective",
        lens_mrp=Decimal("8500.00"),
        lens_offer_price=Decimal("8500.00")
    )
    print(f"Result: {'✅' if success else '❌'} {msg}")
    
    # -------------------------------------------------------------------------
    # STEP 3: Add frame + lens WITH prescription (correct flow)
    # -------------------------------------------------------------------------
    print("\n📋 STEP 3: Add Frame + Lens WITH Prescription")
    print("-" * 50)
    
    success, msg, items = pos.add_frame_with_lens(
        order_id=order.id,
        frame_product=frame,
        prescription=prescription,
        lens_type="Single Vision",
        lens_material="1.67 High Index",
        lens_coating="Blue Cut Anti-Reflective",
        lens_mrp=Decimal("4500.00"),
        lens_offer_price=Decimal("4500.00")
    )
    print(f"Result: {'✅' if success else '❌'} {msg}")
    
    if success:
        frame_item, lens_item = items
        print(f"\nFrame: {frame_item.product_name}")
        print(f"  MRP: ₹{frame_item.mrp} | Price: ₹{frame_item.offer_price}")
        print(f"\nLens: {lens_item.product_name}")
        print(f"  MRP: ₹{lens_item.mrp} | Price: ₹{lens_item.offer_price}")
        print(f"  Prescription: R: {prescription.r_sph}/{prescription.r_cyl}x{prescription.r_axis}")
        print(f"               L: {prescription.l_sph}/{prescription.l_cyl}x{prescription.l_axis}")
    
    # -------------------------------------------------------------------------
    # STEP 4: Apply discount
    # -------------------------------------------------------------------------
    print("\n📋 STEP 4: Apply 8% Discount on Frame")
    print("-" * 50)
    
    # Get frame item ID
    frame_item_id = order.items[0].id
    
    success, msg, details = pos.apply_discount(
        order_id=order.id,
        item_id=frame_item_id,
        discount_percent=Decimal("8"),
        user_role=sales_staff,
        reason="Regular customer"
    )
    print(f"Result: {'✅' if success else '❌'} {msg}")
    if details and not details.get('approval_required'):
        print(f"  Discount Applied: {details['discount_applied']}%")
        print(f"  Final Price: ₹{details['final_price']}")
    
    # -------------------------------------------------------------------------
    # STEP 5: Try to apply 15% discount (exceeds role limit)
    # -------------------------------------------------------------------------
    print("\n📋 STEP 5: Try 15% Discount on Lens (exceeds 10% limit)")
    print("-" * 50)
    
    lens_item_id = order.items[1].id
    
    success, msg, details = pos.apply_discount(
        order_id=order.id,
        item_id=lens_item_id,
        discount_percent=Decimal("15"),
        user_role=sales_staff,
        reason="Special request"
    )
    print(f"Result: {'✅' if success else '❌'} {msg}")
    if details and details.get('approval_required'):
        print(f"  Needs approval from: {details['requires_approval_from']}")
        print(f"  Max allowed for your role: {details['max_allowed']}%")
    
    # -------------------------------------------------------------------------
    # STEP 6: View order summary
    # -------------------------------------------------------------------------
    print("\n📋 STEP 6: Order Summary")
    print("-" * 50)
    
    summary = pos.get_order_summary(order.id)
    print(f"Order: {summary['order_number']}")
    print(f"Status: {summary['status']}")
    print(f"\nItems:")
    for item in summary['items']:
        print(f"  {item['type']}: {item['name']}")
        print(f"    MRP: ₹{item['mrp']} | Discount: {item['discount']}% | Final: ₹{item['unit_price']}")
        print(f"    GST: ₹{item['gst']} | Total: ₹{item['total']}")
    print(f"\nSubtotal: ₹{summary['subtotal']}")
    print(f"Total GST: ₹{summary['total_gst']}")
    print(f"Round Off: ₹{summary['round_off']}")
    print(f"Grand Total: ₹{summary['grand_total']}")
    
    # -------------------------------------------------------------------------
    # STEP 7: Collect advance payment
    # -------------------------------------------------------------------------
    print("\n📋 STEP 7: Collect Advance Payment (₹5000)")
    print("-" * 50)
    
    success, msg, details = pos.collect_payment(
        order_id=order.id,
        amount=Decimal("5000"),
        method=PaymentMethod.UPI,
        received_by="emp-003",
        transaction_reference="UPI123456789"
    )
    print(f"Result: {'✅' if success else '❌'} {msg}")
    if details:
        print(f"  Total Paid: ₹{details['amount_paid_total']}")
        print(f"  Balance Due: ₹{details['balance_due']}")
        print(f"  Payment Status: {details['payment_status']}")
    
    # -------------------------------------------------------------------------
    # STEP 8: Confirm order
    # -------------------------------------------------------------------------
    print("\n📋 STEP 8: Confirm Order")
    print("-" * 50)
    
    success, msg = pos.confirm_order(order.id, expected_delivery_days=5)
    print(f"Result: {'✅' if success else '❌'} {msg}")
    
    # -------------------------------------------------------------------------
    # STEP 9: Later - collect final payment
    # -------------------------------------------------------------------------
    print("\n📋 STEP 9: Customer Returns - Collect Final Payment")
    print("-" * 50)
    
    # Mark ready
    pos.mark_ready_for_delivery(order.id)
    
    # Collect remaining amount
    remaining = order.balance_due
    success, msg, details = pos.collect_payment(
        order_id=order.id,
        amount=remaining,
        method=PaymentMethod.CARD,
        received_by="emp-003",
        transaction_reference="CARD987654321"
    )
    print(f"Result: {'✅' if success else '❌'} {msg}")
    if details:
        print(f"  Payment Status: {details['payment_status']}")
    
    # -------------------------------------------------------------------------
    # STEP 10: Deliver order
    # -------------------------------------------------------------------------
    print("\n📋 STEP 10: Deliver Order")
    print("-" * 50)
    
    success, msg = pos.deliver_order(order.id)
    print(f"Result: {'✅' if success else '❌'} {msg}")
    
    # Final summary
    print("\n📋 FINAL ORDER SUMMARY")
    print("-" * 50)
    summary = pos.get_order_summary(order.id)
    print(f"Order: {summary['order_number']}")
    print(f"Status: {summary['status']}")
    print(f"Grand Total: ₹{summary['grand_total']}")
    print(f"Amount Paid: ₹{summary['amount_paid']}")
    print(f"Payment Status: {summary['payment_status']}")
    
    print("\n" + "=" * 70)
    print("END OF DEMO")
    print("=" * 70)


if __name__ == "__main__":
    demo_pos_flow()

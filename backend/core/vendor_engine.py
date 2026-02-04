"""
IMS 2.0 - Vendor Management Engine
===================================
Complete vendor lifecycle management

Features:
1. Vendor Master (GST, PAN, Contacts)
2. Purchase Orders (PO)
3. GRN (Goods Received Note)
4. Vendor Payments & Ledger
5. Credit Notes & Returns
6. Vendor Follow-up Reminders
7. MOQ Tracking
"""
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from decimal import Decimal
from enum import Enum
from typing import List, Optional, Dict, Tuple
import uuid

class VendorType(Enum):
    INDIAN = "INDIAN"
    INTERNATIONAL = "INTERNATIONAL"

class GSTINStatus(Enum):
    REGISTERED = "REGISTERED"
    UNREGISTERED = "UNREGISTERED"
    COMPOSITE = "COMPOSITE"

class POStatus(Enum):
    DRAFT = "DRAFT"
    SENT = "SENT"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_RECEIVED = "PARTIALLY_RECEIVED"
    FULLY_RECEIVED = "FULLY_RECEIVED"
    CANCELLED = "CANCELLED"

class GRNStatus(Enum):
    DRAFT = "DRAFT"
    PENDING_QC = "PENDING_QC"
    QC_PASSED = "QC_PASSED"
    QC_FAILED = "QC_FAILED"
    ACCEPTED = "ACCEPTED"
    DISPUTED = "DISPUTED"

class PaymentStatus(Enum):
    PENDING = "PENDING"
    PARTIAL = "PARTIAL"
    PAID = "PAID"
    OVERDUE = "OVERDUE"

class ReturnStatus(Enum):
    INITIATED = "INITIATED"
    SHIPPED = "SHIPPED"
    RECEIVED_BY_VENDOR = "RECEIVED_BY_VENDOR"
    CREDIT_NOTE_RECEIVED = "CREDIT_NOTE_RECEIVED"
    REPLACEMENT_RECEIVED = "REPLACEMENT_RECEIVED"
    CLOSED = "CLOSED"

@dataclass
class VendorContact:
    name: str
    designation: str
    mobile: str
    email: str
    is_primary: bool = False

@dataclass
class Vendor:
    id: str
    vendor_code: str
    
    # Basic Info
    legal_name: str
    trade_name: str
    vendor_type: VendorType
    
    # GST
    gstin_status: GSTINStatus
    gstin: str = ""
    pan: str = ""
    
    # Address
    address: str = ""
    city: str = ""
    state: str = ""
    pincode: str = ""
    country: str = "India"
    
    # Contact
    email: str = ""
    mobile: str = ""
    contacts: List[VendorContact] = field(default_factory=list)
    
    # Billing
    is_reverse_charge: bool = False
    is_service_provider: bool = False
    
    # Credit Terms
    credit_days: int = 30
    credit_from: str = "INVOICE_DATE"  # or DELIVERY_DATE
    
    # PO Settings
    po_approval_required: bool = False
    multi_delivery_location: bool = False
    default_delivery_location: str = ""
    
    # MOQ
    moq_products: Dict[str, int] = field(default_factory=dict)  # product_id -> min qty
    
    # Ledger
    opening_balance: Decimal = Decimal("0")
    current_balance: Decimal = Decimal("0")  # DR is positive
    
    # Status
    is_active: bool = True
    created_at: datetime = field(default_factory=datetime.now)

@dataclass
class POItem:
    id: str
    product_id: str
    product_name: str
    sku: str
    ordered_qty: int
    received_qty: int = 0
    unit_price: Decimal = Decimal("0")
    tax_rate: Decimal = Decimal("18")
    
    @property
    def total(self) -> Decimal:
        return self.unit_price * self.ordered_qty
    
    @property
    def pending_qty(self) -> int:
        return self.ordered_qty - self.received_qty

@dataclass
class PurchaseOrder:
    id: str
    po_number: str
    vendor_id: str
    vendor_name: str
    
    # Delivery
    delivery_store_id: str
    delivery_address: str
    expected_date: date
    
    # Items
    items: List[POItem] = field(default_factory=list)
    
    # Totals
    subtotal: Decimal = Decimal("0")
    tax_amount: Decimal = Decimal("0")
    total_amount: Decimal = Decimal("0")
    
    # Status
    status: POStatus = POStatus.DRAFT
    created_by: str = ""
    approved_by: str = ""
    sent_at: Optional[datetime] = None
    
    # Notes
    terms: str = ""
    notes: str = ""
    
    created_at: datetime = field(default_factory=datetime.now)

@dataclass
class GRNItem:
    id: str
    po_item_id: str
    product_id: str
    product_name: str
    expected_qty: int
    received_qty: int
    accepted_qty: int = 0
    rejected_qty: int = 0
    rejection_reason: str = ""
    barcode_printed: bool = False

@dataclass
class GRN:
    id: str
    grn_number: str
    
    # Reference
    po_id: Optional[str] = None
    po_number: Optional[str] = None
    vendor_id: str = ""
    vendor_name: str = ""
    
    # Document
    vendor_invoice_no: str = ""
    vendor_invoice_date: Optional[date] = None
    document_type: str = "INVOICE"  # or DC (Delivery Challan)
    
    # Store
    store_id: str = ""
    received_by: str = ""
    
    # Items
    items: List[GRNItem] = field(default_factory=list)
    
    # Totals
    total_expected: int = 0
    total_received: int = 0
    total_accepted: int = 0
    
    # Status
    status: GRNStatus = GRNStatus.DRAFT
    accepted_by: str = ""
    accepted_at: Optional[datetime] = None
    
    # Mismatch
    has_mismatch: bool = False
    mismatch_escalated: bool = False
    escalation_note: str = ""
    
    created_at: datetime = field(default_factory=datetime.now)

@dataclass
class VendorReturn:
    id: str
    return_number: str
    vendor_id: str
    vendor_name: str
    
    # Reference
    grn_id: Optional[str] = None
    reason: str = ""
    
    # Items
    items: List[Dict] = field(default_factory=list)  # product_id, qty, reason
    
    # Shipping
    courier_name: str = ""
    tracking_number: str = ""
    shipped_at: Optional[datetime] = None
    
    # Resolution
    status: ReturnStatus = ReturnStatus.INITIATED
    credit_note_number: str = ""
    credit_note_amount: Decimal = Decimal("0")
    replacement_grn_id: str = ""
    
    # Images
    issue_images: List[str] = field(default_factory=list)
    
    created_at: datetime = field(default_factory=datetime.now)

@dataclass
class VendorPayment:
    id: str
    payment_number: str
    vendor_id: str
    vendor_name: str
    
    amount: Decimal
    payment_date: date
    payment_mode: str  # NEFT, RTGS, CHEQUE, etc.
    reference: str
    
    # Against
    invoice_numbers: List[str] = field(default_factory=list)
    
    created_at: datetime = field(default_factory=datetime.now)

@dataclass
class VendorReminder:
    id: str
    vendor_id: str
    reminder_type: str  # PAYMENT_DUE, PO_FOLLOWUP, RETURN_FOLLOWUP
    due_date: date
    message: str
    is_completed: bool = False
    completed_at: Optional[datetime] = None


class VendorEngine:
    """
    Vendor Management Engine
    """
    
    def __init__(self):
        self.vendors: Dict[str, Vendor] = {}
        self.purchase_orders: Dict[str, PurchaseOrder] = {}
        self.grns: Dict[str, GRN] = {}
        self.returns: Dict[str, VendorReturn] = {}
        self.payments: Dict[str, VendorPayment] = {}
        self.reminders: Dict[str, VendorReminder] = {}
        
        self._vendor_counter = 0
        self._po_counter = 0
        self._grn_counter = 0
        self._return_counter = 0
        self._payment_counter = 0
    
    def _gen_vendor_code(self) -> str:
        self._vendor_counter += 1
        return f"VND-{self._vendor_counter:04d}"
    
    def _gen_po_number(self) -> str:
        self._po_counter += 1
        return f"PO-{date.today().strftime('%Y%m')}-{self._po_counter:05d}"
    
    def _gen_grn_number(self) -> str:
        self._grn_counter += 1
        return f"GRN-{date.today().strftime('%Y%m')}-{self._grn_counter:05d}"
    
    def _gen_return_number(self) -> str:
        self._return_counter += 1
        return f"RTN-{date.today().strftime('%Y%m')}-{self._return_counter:05d}"
    
    def _gen_payment_number(self) -> str:
        self._payment_counter += 1
        return f"PAY-{date.today().strftime('%Y%m')}-{self._payment_counter:05d}"
    
    # =========================================================================
    # VENDOR MASTER
    # =========================================================================
    
    def create_vendor(
        self,
        legal_name: str,
        trade_name: str,
        vendor_type: VendorType,
        gstin_status: GSTINStatus,
        gstin: str = "",
        pan: str = "",
        address: str = "",
        city: str = "",
        state: str = "",
        mobile: str = "",
        email: str = ""
    ) -> Tuple[bool, str, Optional[Vendor]]:
        """Create new vendor"""
        
        # Validate GSTIN if registered
        if gstin_status == GSTINStatus.REGISTERED and len(gstin) != 15:
            return False, "Invalid GSTIN format", None
        
        # Extract PAN from GSTIN
        if gstin and not pan:
            pan = gstin[2:12]
        
        vendor = Vendor(
            id=str(uuid.uuid4()),
            vendor_code=self._gen_vendor_code(),
            legal_name=legal_name,
            trade_name=trade_name,
            vendor_type=vendor_type,
            gstin_status=gstin_status,
            gstin=gstin,
            pan=pan,
            address=address,
            city=city,
            state=state,
            mobile=mobile,
            email=email
        )
        
        self.vendors[vendor.id] = vendor
        return True, f"Vendor {vendor.vendor_code} created", vendor
    
    def add_vendor_contact(self, vendor_id: str, name: str, designation: str, 
                           mobile: str, email: str, is_primary: bool = False) -> Tuple[bool, str]:
        """Add contact to vendor"""
        vendor = self.vendors.get(vendor_id)
        if not vendor:
            return False, "Vendor not found"
        
        contact = VendorContact(name, designation, mobile, email, is_primary)
        
        if is_primary:
            for c in vendor.contacts:
                c.is_primary = False
        
        vendor.contacts.append(contact)
        return True, f"Contact {name} added"
    
    def set_vendor_moq(self, vendor_id: str, product_id: str, min_qty: int) -> Tuple[bool, str]:
        """Set minimum order quantity for product"""
        vendor = self.vendors.get(vendor_id)
        if not vendor:
            return False, "Vendor not found"
        
        vendor.moq_products[product_id] = min_qty
        return True, f"MOQ set to {min_qty}"
    
    # =========================================================================
    # PURCHASE ORDERS
    # =========================================================================
    
    def create_purchase_order(
        self,
        vendor_id: str,
        delivery_store_id: str,
        delivery_address: str,
        expected_date: date,
        created_by: str
    ) -> Tuple[bool, str, Optional[PurchaseOrder]]:
        """Create new purchase order"""
        
        vendor = self.vendors.get(vendor_id)
        if not vendor:
            return False, "Vendor not found", None
        
        po = PurchaseOrder(
            id=str(uuid.uuid4()),
            po_number=self._gen_po_number(),
            vendor_id=vendor_id,
            vendor_name=vendor.trade_name,
            delivery_store_id=delivery_store_id,
            delivery_address=delivery_address,
            expected_date=expected_date,
            created_by=created_by
        )
        
        self.purchase_orders[po.id] = po
        return True, f"PO {po.po_number} created", po
    
    def add_po_item(
        self,
        po_id: str,
        product_id: str,
        product_name: str,
        sku: str,
        qty: int,
        unit_price: Decimal,
        tax_rate: Decimal = Decimal("18")
    ) -> Tuple[bool, str]:
        """Add item to PO"""
        
        po = self.purchase_orders.get(po_id)
        if not po:
            return False, "PO not found"
        
        if po.status != POStatus.DRAFT:
            return False, "Cannot modify sent PO"
        
        # Check MOQ
        vendor = self.vendors.get(po.vendor_id)
        if vendor and product_id in vendor.moq_products:
            moq = vendor.moq_products[product_id]
            if qty < moq:
                return False, f"Quantity below MOQ of {moq}"
        
        item = POItem(
            id=str(uuid.uuid4()),
            product_id=product_id,
            product_name=product_name,
            sku=sku,
            ordered_qty=qty,
            unit_price=unit_price,
            tax_rate=tax_rate
        )
        
        po.items.append(item)
        self._recalculate_po(po)
        
        return True, f"Added {qty} x {product_name}"
    
    def _recalculate_po(self, po: PurchaseOrder):
        """Recalculate PO totals"""
        po.subtotal = sum(item.total for item in po.items)
        po.tax_amount = sum(item.total * item.tax_rate / 100 for item in po.items)
        po.total_amount = po.subtotal + po.tax_amount
    
    def send_po(self, po_id: str) -> Tuple[bool, str]:
        """Send PO to vendor"""
        po = self.purchase_orders.get(po_id)
        if not po:
            return False, "PO not found"
        
        if not po.items:
            return False, "PO has no items"
        
        po.status = POStatus.SENT
        po.sent_at = datetime.now()
        
        # Create follow-up reminder
        reminder = VendorReminder(
            id=str(uuid.uuid4()),
            vendor_id=po.vendor_id,
            reminder_type="PO_FOLLOWUP",
            due_date=po.expected_date - timedelta(days=2),
            message=f"Follow up on PO {po.po_number}"
        )
        self.reminders[reminder.id] = reminder
        
        return True, f"PO {po.po_number} sent to vendor"
    
    # =========================================================================
    # GRN (Goods Received Note)
    # =========================================================================
    
    def create_grn_from_po(
        self,
        po_id: str,
        store_id: str,
        received_by: str,
        vendor_invoice_no: str = "",
        vendor_invoice_date: date = None
    ) -> Tuple[bool, str, Optional[GRN]]:
        """Create GRN against PO"""
        
        po = self.purchase_orders.get(po_id)
        if not po:
            return False, "PO not found", None
        
        grn = GRN(
            id=str(uuid.uuid4()),
            grn_number=self._gen_grn_number(),
            po_id=po_id,
            po_number=po.po_number,
            vendor_id=po.vendor_id,
            vendor_name=po.vendor_name,
            vendor_invoice_no=vendor_invoice_no,
            vendor_invoice_date=vendor_invoice_date,
            store_id=store_id,
            received_by=received_by
        )
        
        # Create GRN items from PO items
        for po_item in po.items:
            if po_item.pending_qty > 0:
                grn_item = GRNItem(
                    id=str(uuid.uuid4()),
                    po_item_id=po_item.id,
                    product_id=po_item.product_id,
                    product_name=po_item.product_name,
                    expected_qty=po_item.pending_qty,
                    received_qty=0
                )
                grn.items.append(grn_item)
                grn.total_expected += po_item.pending_qty
        
        self.grns[grn.id] = grn
        return True, f"GRN {grn.grn_number} created", grn
    
    def record_grn_receipt(
        self,
        grn_id: str,
        item_id: str,
        received_qty: int,
        accepted_qty: int,
        rejected_qty: int = 0,
        rejection_reason: str = ""
    ) -> Tuple[bool, str]:
        """Record received quantities in GRN"""
        
        grn = self.grns.get(grn_id)
        if not grn:
            return False, "GRN not found"
        
        item = next((i for i in grn.items if i.id == item_id), None)
        if not item:
            return False, "GRN item not found"
        
        item.received_qty = received_qty
        item.accepted_qty = accepted_qty
        item.rejected_qty = rejected_qty
        item.rejection_reason = rejection_reason
        
        # Check for mismatch
        if received_qty != item.expected_qty or rejected_qty > 0:
            grn.has_mismatch = True
        
        # Update totals
        grn.total_received = sum(i.received_qty for i in grn.items)
        grn.total_accepted = sum(i.accepted_qty for i in grn.items)
        
        return True, f"Recorded: {received_qty} received, {accepted_qty} accepted"
    
    def accept_grn(self, grn_id: str, accepted_by: str) -> Tuple[bool, str]:
        """Accept GRN and update stock"""
        grn = self.grns.get(grn_id)
        if not grn:
            return False, "GRN not found"
        
        if grn.has_mismatch and not grn.mismatch_escalated:
            return False, "Mismatch detected - must escalate before accepting"
        
        grn.status = GRNStatus.ACCEPTED
        grn.accepted_by = accepted_by
        grn.accepted_at = datetime.now()
        
        # Update PO status
        if grn.po_id:
            po = self.purchase_orders.get(grn.po_id)
            if po:
                for grn_item in grn.items:
                    po_item = next((i for i in po.items if i.id == grn_item.po_item_id), None)
                    if po_item:
                        po_item.received_qty += grn_item.accepted_qty
                
                # Check if fully received
                all_received = all(item.pending_qty == 0 for item in po.items)
                po.status = POStatus.FULLY_RECEIVED if all_received else POStatus.PARTIALLY_RECEIVED
        
        return True, f"GRN {grn.grn_number} accepted"
    
    def escalate_grn_mismatch(self, grn_id: str, note: str, escalated_by: str) -> Tuple[bool, str]:
        """Escalate GRN mismatch to HQ"""
        grn = self.grns.get(grn_id)
        if not grn:
            return False, "GRN not found"
        
        grn.mismatch_escalated = True
        grn.escalation_note = note
        grn.status = GRNStatus.DISPUTED
        
        return True, f"Mismatch escalated for GRN {grn.grn_number}"
    
    # =========================================================================
    # VENDOR RETURNS
    # =========================================================================
    
    def create_vendor_return(
        self,
        vendor_id: str,
        grn_id: str,
        items: List[Dict],
        reason: str,
        issue_images: List[str] = None
    ) -> Tuple[bool, str, Optional[VendorReturn]]:
        """Create vendor return"""
        
        vendor = self.vendors.get(vendor_id)
        if not vendor:
            return False, "Vendor not found", None
        
        ret = VendorReturn(
            id=str(uuid.uuid4()),
            return_number=self._gen_return_number(),
            vendor_id=vendor_id,
            vendor_name=vendor.trade_name,
            grn_id=grn_id,
            reason=reason,
            items=items,
            issue_images=issue_images or []
        )
        
        self.returns[ret.id] = ret
        
        # Create reminder
        reminder = VendorReminder(
            id=str(uuid.uuid4()),
            vendor_id=vendor_id,
            reminder_type="RETURN_FOLLOWUP",
            due_date=date.today() + timedelta(days=7),
            message=f"Follow up on return {ret.return_number}"
        )
        self.reminders[reminder.id] = reminder
        
        return True, f"Return {ret.return_number} created", ret
    
    def ship_return(self, return_id: str, courier_name: str, tracking_number: str) -> Tuple[bool, str]:
        """Mark return as shipped"""
        ret = self.returns.get(return_id)
        if not ret:
            return False, "Return not found"
        
        ret.status = ReturnStatus.SHIPPED
        ret.courier_name = courier_name
        ret.tracking_number = tracking_number
        ret.shipped_at = datetime.now()
        
        return True, f"Return shipped via {courier_name}"
    
    def receive_credit_note(self, return_id: str, credit_note_number: str, amount: Decimal) -> Tuple[bool, str]:
        """Record credit note received"""
        ret = self.returns.get(return_id)
        if not ret:
            return False, "Return not found"
        
        ret.status = ReturnStatus.CREDIT_NOTE_RECEIVED
        ret.credit_note_number = credit_note_number
        ret.credit_note_amount = amount
        
        # Update vendor balance
        vendor = self.vendors.get(ret.vendor_id)
        if vendor:
            vendor.current_balance -= amount
        
        return True, f"Credit note ₹{amount} recorded"
    
    # =========================================================================
    # PAYMENTS
    # =========================================================================
    
    def record_payment(
        self,
        vendor_id: str,
        amount: Decimal,
        payment_mode: str,
        reference: str,
        invoice_numbers: List[str] = None
    ) -> Tuple[bool, str, Optional[VendorPayment]]:
        """Record payment to vendor"""
        
        vendor = self.vendors.get(vendor_id)
        if not vendor:
            return False, "Vendor not found", None
        
        payment = VendorPayment(
            id=str(uuid.uuid4()),
            payment_number=self._gen_payment_number(),
            vendor_id=vendor_id,
            vendor_name=vendor.trade_name,
            amount=amount,
            payment_date=date.today(),
            payment_mode=payment_mode,
            reference=reference,
            invoice_numbers=invoice_numbers or []
        )
        
        # Update vendor balance
        vendor.current_balance -= amount
        
        self.payments[payment.id] = payment
        return True, f"Payment {payment.payment_number} recorded", payment
    
    # =========================================================================
    # QUERIES
    # =========================================================================
    
    def get_pending_reminders(self) -> List[VendorReminder]:
        """Get pending reminders"""
        return [
            r for r in self.reminders.values()
            if not r.is_completed and r.due_date <= date.today()
        ]
    
    def get_vendor_ledger(self, vendor_id: str) -> Dict:
        """Get vendor ledger summary"""
        vendor = self.vendors.get(vendor_id)
        if not vendor:
            return {}
        
        payments = [p for p in self.payments.values() if p.vendor_id == vendor_id]
        grns = [g for g in self.grns.values() if g.vendor_id == vendor_id and g.status == GRNStatus.ACCEPTED]
        
        return {
            "vendor_code": vendor.vendor_code,
            "vendor_name": vendor.trade_name,
            "opening_balance": float(vendor.opening_balance),
            "current_balance": float(vendor.current_balance),
            "total_purchases": len(grns),
            "total_payments": len(payments),
            "payment_amount": float(sum(p.amount for p in payments))
        }


def demo_vendor():
    print("=" * 60)
    print("IMS 2.0 VENDOR ENGINE DEMO")
    print("=" * 60)
    
    engine = VendorEngine()
    
    # Create vendor
    print("\n🏭 Create Vendor")
    success, msg, vendor = engine.create_vendor(
        "Essilor India Pvt Ltd", "Essilor",
        VendorType.INDIAN, GSTINStatus.REGISTERED,
        gstin="27AABCE1234F1ZP",
        city="Mumbai", state="Maharashtra",
        mobile="9876543210", email="orders@essilor.in"
    )
    print(f"  {msg}")
    
    # Add contact
    engine.add_vendor_contact(vendor.id, "Rahul Sharma", "Sales Manager", "9123456789", "rahul@essilor.in", True)
    
    # Set MOQ
    engine.set_vendor_moq(vendor.id, "lens-001", 10)
    print(f"  MOQ set for lens-001: 10 units")
    
    # Create PO
    print("\n📝 Create Purchase Order")
    success, msg, po = engine.create_purchase_order(
        vendor.id, "store-001", "Better Vision, Bokaro",
        date.today() + timedelta(days=7), "admin-001"
    )
    print(f"  {msg}")
    
    # Add items
    engine.add_po_item(po.id, "lens-001", "Crizal Prevencia", "ESS-CP-001", 20, Decimal("1500"))
    engine.add_po_item(po.id, "lens-002", "Varilux Comfort", "ESS-VC-001", 15, Decimal("3500"))
    print(f"  Added 2 items. Total: ₹{po.total_amount:,.2f}")
    
    # Send PO
    success, msg = engine.send_po(po.id)
    print(f"  {msg}")
    
    # Create GRN
    print("\n📦 Create GRN")
    success, msg, grn = engine.create_grn_from_po(
        po.id, "store-001", "staff-001",
        "ESS/INV/2024/1234", date.today()
    )
    print(f"  {msg}")
    
    # Record receipt
    for item in grn.items:
        engine.record_grn_receipt(grn.id, item.id, item.expected_qty, item.expected_qty)
    print(f"  Received: {grn.total_received} items")
    
    # Accept GRN
    success, msg = engine.accept_grn(grn.id, "mgr-001")
    print(f"  {msg}")
    
    # Record payment
    print("\n💰 Record Payment")
    success, msg, payment = engine.record_payment(
        vendor.id, Decimal("50000"), "NEFT", "NEFT-98765", ["ESS/INV/2024/1234"]
    )
    print(f"  {msg}")
    
    # Ledger
    print("\n📊 Vendor Ledger")
    ledger = engine.get_vendor_ledger(vendor.id)
    for k, v in ledger.items():
        print(f"  {k}: {v}")
    
    # Reminders
    print("\n⏰ Pending Reminders")
    reminders = engine.get_pending_reminders()
    print(f"  {len(reminders)} reminders pending")
    
    print("\n" + "=" * 60)


if __name__ == "__main__":
    demo_vendor()

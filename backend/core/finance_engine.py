"""
IMS 2.0 - Finance & Accounting Engine
======================================
Features:
1. Invoice generation (Tax Invoice, Delivery Challan, Credit Note)
2. GST calculations (CGST, SGST, IGST)
3. HSN code management
4. Payment tracking and reconciliation
5. Outstanding management
6. Till/Cash management
7. Tally export format
8. Financial reports
9. Period locking
"""

from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import List, Optional, Dict, Tuple
import uuid


class InvoiceType(Enum):
    TAX_INVOICE = "TAX_INVOICE"
    DELIVERY_CHALLAN = "DELIVERY_CHALLAN"
    CREDIT_NOTE = "CREDIT_NOTE"
    DEBIT_NOTE = "DEBIT_NOTE"
    PROFORMA = "PROFORMA"


class InvoiceStatus(Enum):
    DRAFT = "DRAFT"
    GENERATED = "GENERATED"
    PAID = "PAID"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    CANCELLED = "CANCELLED"


class PaymentMode(Enum):
    CASH = "CASH"
    UPI = "UPI"
    CARD = "CARD"
    BANK_TRANSFER = "BANK_TRANSFER"
    CHEQUE = "CHEQUE"
    EMI = "EMI"
    CREDIT = "CREDIT"
    GIFT_VOUCHER = "GIFT_VOUCHER"


class GSTType(Enum):
    INTRA_STATE = "INTRA_STATE"  # CGST + SGST
    INTER_STATE = "INTER_STATE"  # IGST


class LedgerType(Enum):
    SALES = "SALES"
    PURCHASE = "PURCHASE"
    RECEIPT = "RECEIPT"
    PAYMENT = "PAYMENT"
    JOURNAL = "JOURNAL"
    CONTRA = "CONTRA"


class TillStatus(Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    RECONCILED = "RECONCILED"


@dataclass
class HSNCode:
    code: str
    description: str
    gst_rate: Decimal
    category: str


@dataclass
class InvoiceItem:
    id: str
    description: str
    hsn_code: str
    quantity: int
    unit_price: Decimal
    discount_amount: Decimal = Decimal("0")
    taxable_value: Decimal = Decimal("0")
    gst_rate: Decimal = Decimal("18")
    cgst_amount: Decimal = Decimal("0")
    sgst_amount: Decimal = Decimal("0")
    igst_amount: Decimal = Decimal("0")
    total_amount: Decimal = Decimal("0")
    
    def calculate(self, gst_type: GSTType):
        self.taxable_value = (self.unit_price * self.quantity - self.discount_amount).quantize(Decimal("0.01"))
        
        if gst_type == GSTType.INTRA_STATE:
            half_rate = (self.gst_rate / 2).quantize(Decimal("0.01"))
            self.cgst_amount = (self.taxable_value * half_rate / 100).quantize(Decimal("0.01"))
            self.sgst_amount = self.cgst_amount
            self.igst_amount = Decimal("0")
        else:
            self.cgst_amount = Decimal("0")
            self.sgst_amount = Decimal("0")
            self.igst_amount = (self.taxable_value * self.gst_rate / 100).quantize(Decimal("0.01"))
        
        self.total_amount = self.taxable_value + self.cgst_amount + self.sgst_amount + self.igst_amount


@dataclass
class Invoice:
    id: str
    invoice_number: str
    invoice_type: InvoiceType
    invoice_date: date
    store_id: str
    store_name: str
    store_gstin: str
    store_address: str
    store_state_code: str
    customer_id: str
    customer_name: str
    customer_phone: str
    customer_email: Optional[str] = None
    customer_gstin: Optional[str] = None
    customer_address: Optional[str] = None
    customer_state_code: Optional[str] = None
    items: List[InvoiceItem] = field(default_factory=list)
    gst_type: GSTType = GSTType.INTRA_STATE
    subtotal: Decimal = Decimal("0")
    total_discount: Decimal = Decimal("0")
    taxable_value: Decimal = Decimal("0")
    cgst_total: Decimal = Decimal("0")
    sgst_total: Decimal = Decimal("0")
    igst_total: Decimal = Decimal("0")
    round_off: Decimal = Decimal("0")
    grand_total: Decimal = Decimal("0")
    amount_paid: Decimal = Decimal("0")
    balance_due: Decimal = Decimal("0")
    status: InvoiceStatus = InvoiceStatus.DRAFT
    order_id: Optional[str] = None
    order_number: Optional[str] = None
    original_invoice_id: Optional[str] = None
    original_invoice_number: Optional[str] = None
    reason: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    created_by: str = ""
    
    def calculate_totals(self):
        self.subtotal = Decimal("0")
        self.total_discount = Decimal("0")
        self.taxable_value = Decimal("0")
        self.cgst_total = Decimal("0")
        self.sgst_total = Decimal("0")
        self.igst_total = Decimal("0")
        
        for item in self.items:
            item.calculate(self.gst_type)
            self.subtotal += item.unit_price * item.quantity
            self.total_discount += item.discount_amount
            self.taxable_value += item.taxable_value
            self.cgst_total += item.cgst_amount
            self.sgst_total += item.sgst_amount
            self.igst_total += item.igst_amount
        
        total_before_round = self.taxable_value + self.cgst_total + self.sgst_total + self.igst_total
        self.grand_total = total_before_round.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        self.round_off = self.grand_total - total_before_round
        self.balance_due = self.grand_total - self.amount_paid


@dataclass
class PaymentEntry:
    id: str
    invoice_id: str
    payment_date: datetime
    amount: Decimal
    payment_mode: PaymentMode
    transaction_reference: Optional[str] = None
    bank_name: Optional[str] = None
    cheque_number: Optional[str] = None
    cheque_date: Optional[date] = None
    received_by: str = ""
    notes: Optional[str] = None


@dataclass
class LedgerEntry:
    id: str
    entry_date: date
    ledger_type: LedgerType
    account_code: str
    account_name: str
    debit_amount: Decimal = Decimal("0")
    credit_amount: Decimal = Decimal("0")
    reference_type: str = ""
    reference_id: str = ""
    reference_number: str = ""
    party_type: Optional[str] = None
    party_id: Optional[str] = None
    party_name: Optional[str] = None
    narration: str = ""
    store_id: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    created_by: str = ""


@dataclass
class TillSession:
    id: str
    store_id: str
    session_date: date
    opened_by: str
    opened_by_name: str
    closed_by: Optional[str] = None
    closed_by_name: Optional[str] = None
    opening_balance: Decimal = Decimal("0")
    cash_sales: Decimal = Decimal("0")
    card_sales: Decimal = Decimal("0")
    upi_sales: Decimal = Decimal("0")
    bank_transfer_sales: Decimal = Decimal("0")
    cheque_sales: Decimal = Decimal("0")
    gift_voucher_sales: Decimal = Decimal("0")
    cash_received: Decimal = Decimal("0")
    cash_paid_out: Decimal = Decimal("0")
    expected_cash: Decimal = Decimal("0")
    actual_cash: Decimal = Decimal("0")
    variance: Decimal = Decimal("0")
    variance_notes: Optional[str] = None
    status: TillStatus = TillStatus.OPEN
    opened_at: datetime = field(default_factory=datetime.now)
    closed_at: Optional[datetime] = None


@dataclass
class Outstanding:
    id: str
    customer_id: str
    customer_name: str
    customer_phone: str
    invoice_id: str
    invoice_number: str
    invoice_date: date
    invoice_amount: Decimal
    amount_paid: Decimal = Decimal("0")
    balance_due: Decimal = Decimal("0")
    due_date: Optional[date] = None
    days_overdue: int = 0
    aging_bucket: str = "CURRENT"
    last_followup_date: Optional[date] = None
    followup_notes: Optional[str] = None


@dataclass
class FinancialPeriod:
    id: str
    period_name: str
    start_date: date
    end_date: date
    is_locked: bool = False
    locked_at: Optional[datetime] = None
    locked_by: Optional[str] = None
    gst_filed: bool = False
    gst_filed_at: Optional[datetime] = None


class FinanceEngine:
    def __init__(self):
        self.invoices: Dict[str, Invoice] = {}
        self.payments: Dict[str, PaymentEntry] = {}
        self.ledger_entries: List[LedgerEntry] = []
        self.till_sessions: Dict[str, TillSession] = {}
        self.outstandings: Dict[str, Outstanding] = {}
        self.periods: Dict[str, FinancialPeriod] = {}
        self.hsn_codes: Dict[str, HSNCode] = {}
        self._invoice_counters: Dict[str, int] = {}
        self._initialize_hsn_codes()
    
    def _initialize_hsn_codes(self):
        codes = [
            HSNCode("9004", "Spectacles, goggles", Decimal("18"), "FRAME"),
            HSNCode("900410", "Sunglasses", Decimal("18"), "SUNGLASS"),
            HSNCode("900490", "Spectacle frames", Decimal("18"), "FRAME"),
            HSNCode("900491", "Reading glasses", Decimal("18"), "READING_GLASSES"),
            HSNCode("9001", "Optical lenses", Decimal("18"), "OPTICAL_LENS"),
            HSNCode("900130", "Contact lenses", Decimal("12"), "CONTACT_LENS"),
            HSNCode("900131", "Colored contact lenses", Decimal("12"), "COLORED_CONTACT_LENS"),
            HSNCode("9102", "Wrist watches", Decimal("18"), "WATCH"),
            HSNCode("8517", "Smartwatches", Decimal("18"), "SMARTWATCH"),
            HSNCode("900492", "Smart glasses", Decimal("18"), "SMARTGLASSES"),
            HSNCode("9023", "Hearing aids", Decimal("5"), "HEARING_AID"),
            HSNCode("9105", "Wall clocks", Decimal("18"), "WALL_CLOCK"),
            HSNCode("9003", "Accessories", Decimal("18"), "ACCESSORY"),
            HSNCode("9999", "Services", Decimal("18"), "SERVICE"),
        ]
        for code in codes:
            self.hsn_codes[code.code] = code
    
    def generate_invoice_number(self, store_code: str, invoice_type: InvoiceType) -> str:
        prefix_map = {
            InvoiceType.TAX_INVOICE: "INV",
            InvoiceType.DELIVERY_CHALLAN: "DC",
            InvoiceType.CREDIT_NOTE: "CN",
            InvoiceType.DEBIT_NOTE: "DN",
            InvoiceType.PROFORMA: "PI",
        }
        prefix = prefix_map.get(invoice_type, "INV")
        fy = self._get_financial_year()
        key = f"{store_code}:{prefix}:{fy}"
        
        if key not in self._invoice_counters:
            self._invoice_counters[key] = 0
        self._invoice_counters[key] += 1
        
        return f"{store_code}/{prefix}/{fy}/{self._invoice_counters[key]:05d}"
    
    def _get_financial_year(self) -> str:
        today = date.today()
        if today.month >= 4:
            return f"{today.year}-{(today.year + 1) % 100:02d}"
        else:
            return f"{today.year - 1}-{today.year % 100:02d}"
    
    def determine_gst_type(self, store_state_code: str, customer_state_code: Optional[str]) -> GSTType:
        if not customer_state_code:
            return GSTType.INTRA_STATE
        if store_state_code == customer_state_code:
            return GSTType.INTRA_STATE
        return GSTType.INTER_STATE
    
    def create_invoice(
        self,
        invoice_type: InvoiceType,
        store_id: str,
        store_code: str,
        store_name: str,
        store_gstin: str,
        store_address: str,
        store_state_code: str,
        customer_id: str,
        customer_name: str,
        customer_phone: str,
        created_by: str,
        customer_gstin: Optional[str] = None,
        customer_address: Optional[str] = None,
        customer_state_code: Optional[str] = None,
        order_id: Optional[str] = None,
        order_number: Optional[str] = None
    ) -> Invoice:
        gst_type = self.determine_gst_type(store_state_code, customer_state_code)
        
        invoice = Invoice(
            id=str(uuid.uuid4()),
            invoice_number=self.generate_invoice_number(store_code, invoice_type),
            invoice_type=invoice_type,
            invoice_date=date.today(),
            store_id=store_id,
            store_name=store_name,
            store_gstin=store_gstin,
            store_address=store_address,
            store_state_code=store_state_code,
            customer_id=customer_id,
            customer_name=customer_name,
            customer_phone=customer_phone,
            customer_gstin=customer_gstin,
            customer_address=customer_address,
            customer_state_code=customer_state_code,
            gst_type=gst_type,
            order_id=order_id,
            order_number=order_number,
            created_by=created_by
        )
        
        self.invoices[invoice.id] = invoice
        return invoice
    
    def add_invoice_item(
        self,
        invoice_id: str,
        description: str,
        hsn_code: str,
        quantity: int,
        unit_price: Decimal,
        discount_amount: Decimal = Decimal("0"),
        gst_rate: Optional[Decimal] = None
    ) -> Tuple[bool, str]:
        invoice = self.invoices.get(invoice_id)
        if not invoice:
            return False, "Invoice not found"
        
        if invoice.status != InvoiceStatus.DRAFT:
            return False, "Cannot modify generated invoice"
        
        if gst_rate is None:
            hsn = self.hsn_codes.get(hsn_code)
            gst_rate = hsn.gst_rate if hsn else Decimal("18")
        
        item = InvoiceItem(
            id=str(uuid.uuid4()),
            description=description,
            hsn_code=hsn_code,
            quantity=quantity,
            unit_price=unit_price,
            discount_amount=discount_amount,
            gst_rate=gst_rate
        )
        
        invoice.items.append(item)
        invoice.calculate_totals()
        
        return True, f"Item added. Total: ₹{invoice.grand_total}"
    
    def generate_invoice(self, invoice_id: str) -> Tuple[bool, str]:
        invoice = self.invoices.get(invoice_id)
        if not invoice:
            return False, "Invoice not found"
        
        if not invoice.items:
            return False, "Cannot generate empty invoice"
        
        invoice.calculate_totals()
        invoice.status = InvoiceStatus.GENERATED
        
        self._create_invoice_ledger_entries(invoice)
        
        if invoice.balance_due > 0:
            self._create_outstanding(invoice)
        
        return True, f"Invoice {invoice.invoice_number} generated"
    
    def _create_invoice_ledger_entries(self, invoice: Invoice):
        # Debit Customer
        self.ledger_entries.append(LedgerEntry(
            id=str(uuid.uuid4()),
            entry_date=invoice.invoice_date,
            ledger_type=LedgerType.SALES,
            account_code="SUNDRY_DEBTORS",
            account_name="Sundry Debtors",
            debit_amount=invoice.grand_total,
            reference_type="INVOICE",
            reference_id=invoice.id,
            reference_number=invoice.invoice_number,
            party_type="CUSTOMER",
            party_id=invoice.customer_id,
            party_name=invoice.customer_name,
            narration=f"Sales Invoice {invoice.invoice_number}",
            store_id=invoice.store_id,
            created_by=invoice.created_by
        ))
        
        # Credit Sales
        self.ledger_entries.append(LedgerEntry(
            id=str(uuid.uuid4()),
            entry_date=invoice.invoice_date,
            ledger_type=LedgerType.SALES,
            account_code="SALES",
            account_name="Sales Account",
            credit_amount=invoice.taxable_value,
            reference_type="INVOICE",
            reference_id=invoice.id,
            reference_number=invoice.invoice_number,
            narration=f"Sales Invoice {invoice.invoice_number}",
            store_id=invoice.store_id,
            created_by=invoice.created_by
        ))
        
        # Credit GST
        total_gst = invoice.cgst_total + invoice.sgst_total + invoice.igst_total
        if total_gst > 0:
            self.ledger_entries.append(LedgerEntry(
                id=str(uuid.uuid4()),
                entry_date=invoice.invoice_date,
                ledger_type=LedgerType.SALES,
                account_code="GST_OUTPUT",
                account_name="GST Output",
                credit_amount=total_gst,
                reference_type="INVOICE",
                reference_id=invoice.id,
                reference_number=invoice.invoice_number,
                narration=f"GST on {invoice.invoice_number}",
                store_id=invoice.store_id,
                created_by=invoice.created_by
            ))
    
    def _create_outstanding(self, invoice: Invoice):
        outstanding = Outstanding(
            id=str(uuid.uuid4()),
            customer_id=invoice.customer_id,
            customer_name=invoice.customer_name,
            customer_phone=invoice.customer_phone,
            invoice_id=invoice.id,
            invoice_number=invoice.invoice_number,
            invoice_date=invoice.invoice_date,
            invoice_amount=invoice.grand_total,
            amount_paid=invoice.amount_paid,
            balance_due=invoice.balance_due,
            due_date=invoice.invoice_date + timedelta(days=30)
        )
        self.outstandings[outstanding.id] = outstanding
    
    def record_payment(
        self,
        invoice_id: str,
        amount: Decimal,
        payment_mode: PaymentMode,
        received_by: str,
        transaction_reference: Optional[str] = None,
        bank_name: Optional[str] = None,
        cheque_number: Optional[str] = None,
        cheque_date: Optional[date] = None,
        notes: Optional[str] = None
    ) -> Tuple[bool, str]:
        invoice = self.invoices.get(invoice_id)
        if not invoice:
            return False, "Invoice not found"
        
        if amount <= 0:
            return False, "Invalid payment amount"
        
        if amount > invoice.balance_due:
            return False, f"Amount exceeds balance (₹{invoice.balance_due})"
        
        payment = PaymentEntry(
            id=str(uuid.uuid4()),
            invoice_id=invoice_id,
            payment_date=datetime.now(),
            amount=amount,
            payment_mode=payment_mode,
            transaction_reference=transaction_reference,
            bank_name=bank_name,
            cheque_number=cheque_number,
            cheque_date=cheque_date,
            received_by=received_by,
            notes=notes
        )
        
        self.payments[payment.id] = payment
        
        invoice.amount_paid += amount
        invoice.balance_due = invoice.grand_total - invoice.amount_paid
        
        if invoice.balance_due <= 0:
            invoice.status = InvoiceStatus.PAID
        else:
            invoice.status = InvoiceStatus.PARTIALLY_PAID
        
        for ost in self.outstandings.values():
            if ost.invoice_id == invoice_id:
                ost.amount_paid = invoice.amount_paid
                ost.balance_due = invoice.balance_due
                break
        
        if payment_mode == PaymentMode.CASH:
            self._update_till_cash(invoice.store_id, amount)
        
        return True, f"Payment ₹{amount} recorded. Balance: ₹{invoice.balance_due}"
    
    def _update_till_cash(self, store_id: str, amount: Decimal):
        today = date.today()
        session_key = f"{store_id}:{today}"
        if session_key in self.till_sessions:
            self.till_sessions[session_key].cash_sales += amount
    
    def open_till(
        self,
        store_id: str,
        user_id: str,
        user_name: str,
        opening_balance: Decimal
    ) -> Tuple[bool, str, Optional[TillSession]]:
        today = date.today()
        session_key = f"{store_id}:{today}"
        
        if session_key in self.till_sessions:
            existing = self.till_sessions[session_key]
            if existing.status == TillStatus.OPEN:
                return False, "Till already open", existing
        
        session = TillSession(
            id=str(uuid.uuid4()),
            store_id=store_id,
            session_date=today,
            opened_by=user_id,
            opened_by_name=user_name,
            opening_balance=opening_balance
        )
        
        self.till_sessions[session_key] = session
        return True, f"Till opened with ₹{opening_balance}", session
    
    def close_till(
        self,
        store_id: str,
        user_id: str,
        user_name: str,
        actual_cash: Decimal,
        variance_notes: Optional[str] = None
    ) -> Tuple[bool, str, Optional[TillSession]]:
        today = date.today()
        session_key = f"{store_id}:{today}"
        
        if session_key not in self.till_sessions:
            return False, "No till session found", None
        
        session = self.till_sessions[session_key]
        
        if session.status != TillStatus.OPEN:
            return False, "Till already closed", session
        
        session.expected_cash = (
            session.opening_balance +
            session.cash_sales +
            session.cash_received -
            session.cash_paid_out
        )
        
        session.actual_cash = actual_cash
        session.variance = actual_cash - session.expected_cash
        session.variance_notes = variance_notes
        session.closed_by = user_id
        session.closed_by_name = user_name
        session.closed_at = datetime.now()
        session.status = TillStatus.CLOSED
        
        msg = f"Till closed. Expected: ₹{session.expected_cash}, Actual: ₹{actual_cash}"
        if session.variance != 0:
            msg += f", Variance: ₹{session.variance}"
        
        return True, msg, session
    
    def create_credit_note(
        self,
        original_invoice_id: str,
        reason: str,
        items: List[dict],
        created_by: str
    ) -> Tuple[bool, str, Optional[Invoice]]:
        original = self.invoices.get(original_invoice_id)
        if not original:
            return False, "Original invoice not found", None
        
        cn = Invoice(
            id=str(uuid.uuid4()),
            invoice_number=self.generate_invoice_number(
                original.store_id[:6],
                InvoiceType.CREDIT_NOTE
            ),
            invoice_type=InvoiceType.CREDIT_NOTE,
            invoice_date=date.today(),
            store_id=original.store_id,
            store_name=original.store_name,
            store_gstin=original.store_gstin,
            store_address=original.store_address,
            store_state_code=original.store_state_code,
            customer_id=original.customer_id,
            customer_name=original.customer_name,
            customer_phone=original.customer_phone,
            customer_gstin=original.customer_gstin,
            gst_type=original.gst_type,
            original_invoice_id=original.id,
            original_invoice_number=original.invoice_number,
            reason=reason,
            created_by=created_by
        )
        
        for item in items:
            cn.items.append(InvoiceItem(
                id=str(uuid.uuid4()),
                description=item["description"],
                hsn_code=item.get("hsn_code", "9999"),
                quantity=item["quantity"],
                unit_price=item["amount"],
                gst_rate=item.get("gst_rate", Decimal("18"))
            ))
        
        cn.calculate_totals()
        cn.status = InvoiceStatus.GENERATED
        self.invoices[cn.id] = cn
        
        return True, f"Credit Note {cn.invoice_number} created", cn
    
    def get_outstanding_report(self, store_id: Optional[str] = None) -> dict:
        self._update_outstanding_aging()
        
        report = {
            "total_outstanding": Decimal("0"),
            "by_aging": {
                "CURRENT": Decimal("0"),
                "1-30": Decimal("0"),
                "31-60": Decimal("0"),
                "61-90": Decimal("0"),
                "90+": Decimal("0")
            },
            "customers": []
        }
        
        for ost in self.outstandings.values():
            if ost.balance_due <= 0:
                continue
            
            report["total_outstanding"] += ost.balance_due
            report["by_aging"][ost.aging_bucket] += ost.balance_due
            report["customers"].append({
                "customer_name": ost.customer_name,
                "invoice_number": ost.invoice_number,
                "balance_due": float(ost.balance_due),
                "days_overdue": ost.days_overdue,
                "aging_bucket": ost.aging_bucket
            })
        
        return report
    
    def _update_outstanding_aging(self):
        today = date.today()
        for ost in self.outstandings.values():
            if ost.balance_due <= 0:
                continue
            if ost.due_date:
                days_overdue = (today - ost.due_date).days
                ost.days_overdue = max(0, days_overdue)
                
                if days_overdue <= 0:
                    ost.aging_bucket = "CURRENT"
                elif days_overdue <= 30:
                    ost.aging_bucket = "1-30"
                elif days_overdue <= 60:
                    ost.aging_bucket = "31-60"
                elif days_overdue <= 90:
                    ost.aging_bucket = "61-90"
                else:
                    ost.aging_bucket = "90+"
    
    def get_gst_summary(self, store_id: str, month: int, year: int) -> dict:
        start_date = date(year, month, 1)
        if month == 12:
            end_date = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            end_date = date(year, month + 1, 1) - timedelta(days=1)
        
        summary = {
            "period": f"{start_date.strftime('%B %Y')}",
            "store_id": store_id,
            "total_invoices": 0,
            "total_taxable_value": Decimal("0"),
            "cgst_collected": Decimal("0"),
            "sgst_collected": Decimal("0"),
            "igst_collected": Decimal("0"),
            "total_gst": Decimal("0"),
            "by_hsn": {},
            "b2b_invoices": [],
            "b2c_invoices": []
        }
        
        for invoice in self.invoices.values():
            if invoice.store_id != store_id:
                continue
            if invoice.invoice_date < start_date or invoice.invoice_date > end_date:
                continue
            if invoice.invoice_type != InvoiceType.TAX_INVOICE:
                continue
            if invoice.status == InvoiceStatus.CANCELLED:
                continue
            
            summary["total_invoices"] += 1
            summary["total_taxable_value"] += invoice.taxable_value
            summary["cgst_collected"] += invoice.cgst_total
            summary["sgst_collected"] += invoice.sgst_total
            summary["igst_collected"] += invoice.igst_total
            
            if invoice.customer_gstin:
                summary["b2b_invoices"].append({
                    "invoice_number": invoice.invoice_number,
                    "customer_gstin": invoice.customer_gstin,
                    "taxable_value": float(invoice.taxable_value)
                })
            else:
                summary["b2c_invoices"].append({
                    "invoice_number": invoice.invoice_number,
                    "taxable_value": float(invoice.taxable_value)
                })
        
        summary["total_gst"] = summary["cgst_collected"] + summary["sgst_collected"] + summary["igst_collected"]
        return summary
    
    def export_to_tally_format(self, store_id: str, from_date: date, to_date: date) -> str:
        entries = [e for e in self.ledger_entries 
                   if e.store_id == store_id and from_date <= e.entry_date <= to_date]
        
        xml = ['<?xml version="1.0" encoding="UTF-8"?>']
        xml.append('<ENVELOPE>')
        xml.append('  <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>')
        xml.append('  <BODY><IMPORTDATA>')
        
        for entry in entries:
            xml.append(f'    <VOUCHER>')
            xml.append(f'      <DATE>{entry.entry_date.strftime("%Y%m%d")}</DATE>')
            xml.append(f'      <VOUCHERNUMBER>{entry.reference_number}</VOUCHERNUMBER>')
            xml.append(f'      <LEDGERNAME>{entry.account_name}</LEDGERNAME>')
            if entry.debit_amount > 0:
                xml.append(f'      <AMOUNT>-{entry.debit_amount}</AMOUNT>')
            else:
                xml.append(f'      <AMOUNT>{entry.credit_amount}</AMOUNT>')
            xml.append(f'    </VOUCHER>')
        
        xml.append('  </IMPORTDATA></BODY>')
        xml.append('</ENVELOPE>')
        return '\n'.join(xml)


def demo_finance():
    print("=" * 70)
    print("IMS 2.0 FINANCE & ACCOUNTING - DEMO")
    print("=" * 70)
    
    engine = FinanceEngine()
    
    print("\n📄 SCENARIO 1: Create Tax Invoice (Intra-State)")
    print("-" * 50)
    
    invoice = engine.create_invoice(
        invoice_type=InvoiceType.TAX_INVOICE,
        store_id="store-bv-001",
        store_code="BV-BKR",
        store_name="Better Vision - Bokaro",
        store_gstin="20AABCU9603R1ZM",
        store_address="Main Road, Bokaro Steel City",
        store_state_code="20",
        customer_id="cust-001",
        customer_name="Rajesh Kumar",
        customer_phone="9876543210",
        customer_state_code="20",
        created_by="user-sales"
    )
    print(f"Invoice Created: {invoice.invoice_number}")
    print(f"GST Type: {invoice.gst_type.value}")
    
    engine.add_invoice_item(
        invoice_id=invoice.id,
        description="Ray-Ban RB5154 Clubmaster Frame",
        hsn_code="900490",
        quantity=1,
        unit_price=Decimal("6890.80")
    )
    
    engine.add_invoice_item(
        invoice_id=invoice.id,
        description="1.67 High Index Blue Cut Lens",
        hsn_code="9001",
        quantity=1,
        unit_price=Decimal("4500")
    )
    
    success, msg = engine.generate_invoice(invoice.id)
    print(f"Generate: {msg}")
    print(f"Taxable: ₹{invoice.taxable_value}, CGST: ₹{invoice.cgst_total}, SGST: ₹{invoice.sgst_total}")
    print(f"Grand Total: ₹{invoice.grand_total}")
    
    print("\n💰 SCENARIO 2: Payment Collection")
    print("-" * 50)
    
    engine.open_till("store-bv-001", "user-cashier", "Priya Singh", Decimal("5000"))
    
    success, msg = engine.record_payment(
        invoice_id=invoice.id,
        amount=Decimal("8000"),
        payment_mode=PaymentMode.UPI,
        received_by="user-sales",
        transaction_reference="UPI123456789"
    )
    print(f"Payment (UPI): {msg}")
    
    success, msg = engine.record_payment(
        invoice_id=invoice.id,
        amount=invoice.balance_due,
        payment_mode=PaymentMode.CASH,
        received_by="user-sales"
    )
    print(f"Payment (Cash): {msg}")
    print(f"Invoice Status: {invoice.status.value}")
    
    print("\n📋 SCENARIO 3: GST Summary")
    print("-" * 50)
    
    gst = engine.get_gst_summary("store-bv-001", 1, 2026)
    print(f"Period: {gst['period']}")
    print(f"Invoices: {gst['total_invoices']}")
    print(f"CGST: ₹{gst['cgst_collected']}, SGST: ₹{gst['sgst_collected']}")
    
    print("\n" + "=" * 70)
    print("FINANCE DEMO COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    demo_finance()

"""
IMS 2.0 - Main Application
===========================
Integrates all backend modules into unified retail operating system.
"""
from dataclasses import dataclass
from datetime import datetime, date
from decimal import Decimal
from typing import Optional, List, Dict, Tuple
import uuid

# Import all engines
from inventory_engine import InventoryEngine, StockUnit
from hr_engine import Role

class ProductCategory:
    FRAME = "FRAME"
    SUNGLASS = "SUNGLASS"
    READING_GLASSES = "READING_GLASSES"
    OPTICAL_LENS = "OPTICAL_LENS"
    CONTACT_LENS = "CONTACT_LENS"
    COLORED_CONTACT_LENS = "COLORED_CONTACT_LENS"
    WATCH = "WATCH"
    SMARTWATCH = "SMARTWATCH"
    SMARTGLASSES = "SMARTGLASSES"
    WALL_CLOCK = "WALL_CLOCK"
    ACCESSORY = "ACCESSORY"
    SERVICE = "SERVICE"
from pricing_engine import PricingEngine
from clinical_engine import ClinicalEngine, EyePower
from hr_engine import HREngine
from finance_engine import FinanceEngine, InvoiceType, PaymentMode
from tasks_engine import TaskEngine, TaskCategory, TaskPriority, SOPType
from marketplace_engine import MarketplaceEngine

@dataclass
class Store:
    id: str
    code: str
    name: str
    gstin: str
    address: str
    state_code: str
    latitude: float
    longitude: float
    is_active: bool = True
    brand: str = "Better Vision"

@dataclass
class AuditLog:
    id: str
    timestamp: datetime
    user_id: str
    user_name: str
    store_id: str
    action: str
    module: str
    entity_type: str
    entity_id: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None


class IMSApplication:
    """IMS 2.0 - Retail Operating System"""
    
    VERSION = "2.0.0"
    
    def __init__(self):
        self.inventory = InventoryEngine()
        self.pricing = PricingEngine()
        self.clinical = ClinicalEngine()
        self.hr = HREngine()
        self.finance = FinanceEngine()
        self.tasks = TaskEngine()
        self.marketplace = MarketplaceEngine()
        self.stores: Dict[str, Store] = {}
        self.audit_logs: List[AuditLog] = []
    
    def log_audit(self, user_id: str, user_name: str, store_id: str,
                  action: str, module: str, entity_type: str, entity_id: str,
                  old_value: str = None, new_value: str = None):
        log = AuditLog(str(uuid.uuid4()), datetime.now(), user_id, user_name,
                       store_id, action, module, entity_type, entity_id, old_value, new_value)
        self.audit_logs.append(log)
        return log
    
    def register_store(self, code: str, name: str, gstin: str, address: str,
                       state_code: str, lat: float, lon: float, brand: str = "Better Vision") -> Store:
        store = Store(str(uuid.uuid4()), code, name, gstin, address, state_code, lat, lon, True, brand)
        self.stores[store.id] = store
        self.hr.register_location(store.id, lat, lon, 100)
        return store
    
    def process_optical_sale(self, store_id: str, user_id: str, user_name: str,
                             customer_id: str, customer_name: str, customer_phone: str,
                             prescription_id: str, frame_barcode: str,
                             frame_price: Decimal, lens_price: Decimal,
                             discount_percent: Decimal, advance_amount: Decimal,
                             payment_mode: PaymentMode) -> Tuple[bool, str, Optional[dict]]:
        """Complete optical sale workflow"""
        
        store = self.stores.get(store_id)
        if not store:
            return False, "Store not found", None
        
        # Validate prescription
        rx = self.clinical.prescriptions.get(prescription_id)
        if not rx:
            return False, "Prescription not found", None
        if not rx.is_valid():
            return False, f"Prescription expired", None
        
        # Check frame stock
        frame_unit = None
        for unit in self.inventory.stock_units.values():
            if hasattr(unit, 'barcode') and unit.barcode == frame_barcode and unit.store_id == store_id:
                frame_unit = unit
                break
        if not frame_unit:
            return False, "Frame not found", None
        
        # Validate discount
        discount_cap = self.hr.get_discount_cap(user_id)
        if discount_percent > discount_cap:
            return False, f"Discount exceeds cap ({discount_cap}%)", None
        
        # Calculate
        subtotal = frame_price + lens_price
        discount_amount = (subtotal * discount_percent / 100).quantize(Decimal("0.01"))
        taxable = subtotal - discount_amount
        gst = (taxable * Decimal("0.18")).quantize(Decimal("0.01"))
        total = taxable + gst
        
        # Create invoice
        invoice = self.finance.create_invoice(
            InvoiceType.TAX_INVOICE, store_id, store.code, store.name,
            store.gstin, store.address, store.state_code,
            customer_id, customer_name, customer_phone, user_id
        )
        
        frame_name = getattr(frame_unit, 'name', 'Frame')
        self.finance.add_invoice_item(invoice.id, f"Frame: {frame_name}",
                                       "900490", 1, frame_price)
        self.finance.add_invoice_item(invoice.id, f"Lens (Rx: {rx.prescription_number})",
                                       "9001", 1, lens_price)
        
        self.finance.generate_invoice(invoice.id)
        
        if advance_amount > 0:
            self.finance.record_payment(invoice.id, advance_amount, payment_mode, user_id)
        
        frame_unit.status = "SOLD"
        self.hr.record_sale(user_id, total)
        
        self.log_audit(user_id, user_name, store_id, "CREATE", "POS", "INVOICE",
                       invoice.id, None, f"Invoice {invoice.invoice_number} - ₹{total}")
        
        return True, f"Sale completed. Invoice: {invoice.invoice_number}", {
            "invoice_number": invoice.invoice_number,
            "total": float(total),
            "balance": float(invoice.balance_due)
        }
    
    def start_day(self, store_id: str, user_id: str, user_name: str,
                  opening_cash: Decimal, lat: float, lon: float) -> Tuple[bool, str, dict]:
        """Start of day workflow"""
        results = {}
        
        success, msg = self.hr.check_in(user_id, store_id, lat, lon)
        results["attendance"] = msg
        if not success:
            return False, f"Attendance failed: {msg}", results
        
        success, msg, _ = self.finance.open_till(store_id, user_id, user_name, opening_cash)
        results["till"] = msg
        
        task = self.tasks.create_daily_sop_tasks(store_id, SOPType.DAILY_OPENING, user_id, user_name)
        results["sop_task"] = task.task_number if task else None
        
        pending = self.tasks.get_user_tasks(user_id)
        results["pending_tasks"] = len(pending)
        
        return True, "Day started", results
    
    def end_day(self, store_id: str, user_id: str, user_name: str,
                actual_cash: Decimal, lat: float, lon: float) -> Tuple[bool, str, dict]:
        """End of day workflow"""
        results = {}
        
        success, msg, till = self.finance.close_till(store_id, user_id, user_name, actual_cash)
        results["till"] = msg
        
        if till and till.variance != 0:
            self.tasks.create_system_task(
                f"Cash Variance: ₹{till.variance}",
                f"Expected: ₹{till.expected_cash}, Actual: ₹{actual_cash}",
                TaskCategory.PAYMENT, TaskPriority.P1_URGENT,
                store_id, due_in_minutes=30
            )
        
        success, msg = self.hr.check_out(user_id)
        results["attendance"] = msg
        
        return True, "Day ended", results
    
    def get_dashboard(self, store_id: str) -> dict:
        task_stats = self.tasks.get_task_statistics(store_id)
        return {
            "store_id": store_id,
            "date": str(date.today()),
            "tasks": task_stats,
            "invoices": len([i for i in self.finance.invoices.values() if i.store_id == store_id])
        }


def demo_ims():
    print("=" * 60)
    print("IMS 2.0 - COMPLETE SYSTEM DEMO")
    print("=" * 60)
    
    app = IMSApplication()
    
    # Register store
    print("\n🏪 Register Store")
    store = app.register_store("BV-BKR", "Better Vision - Bokaro", "20AABCU9603R1ZM",
                                "Main Road, Bokaro", "20", 23.6693, 86.1511)
    print(f"  {store.name} ({store.code})")
    
    # Create employee
    print("\n👤 Create Employee")
    from hr_engine import Role
    emp = app.hr.create_employee("Neha", "Sharma", "neha@bv.in", "9876543210",
                                  [Role.STORE_MANAGER, Role.OPTOMETRIST],
                                  store.id, store.code, Decimal("600000"))
    emp.monthly_sales_target = Decimal("500000")
    emp.incentive_percentage = Decimal("2")
    print(f"  {emp.full_name} - {[r.value for r in emp.roles]}")
    
    # Start day
    print("\n🌅 Start Day")
    success, msg, results = app.start_day(store.id, emp.id, emp.full_name, Decimal("5000"), 23.6693, 86.1511)
    print(f"  {msg}")
    print(f"  Attendance: {results['attendance']}")
    print(f"  Tasks: {results['pending_tasks']} pending")
    
    # Eye test
    print("\n👁️ Eye Test")
    test = app.clinical.start_eye_test("pat-001", "Rajesh Kumar", "cust-001",
                                        store.id, store.code, emp.id, emp.full_name)
    success, msg, rx = app.clinical.complete_eye_test(
        test.id,
        EyePower(sph=Decimal("-2.50"), cyl=Decimal("-0.75"), axis=90, add=Decimal("1.50")),
        EyePower(sph=Decimal("-2.25"), cyl=Decimal("-0.50"), axis=85, add=Decimal("1.50")),
        Decimal("64"), None, "Progressive recommended"
    )
    print(f"  {msg}")
    print(f"  Rx: R: {rx.right_eye.sph}/{rx.right_eye.cyl}x{rx.right_eye.axis}")
    
    # Add frame to inventory
    print("\n📦 Add Frame")
    # Using simplified frame data since StockUnit structure differs
    frame_data = {"id": str(uuid.uuid4()), "barcode": "RB5154-001", "product_id": "prod-001",
                  "name": "Ray-Ban RB5154", "store_id": store.id, "price": Decimal("6890"), "status": "AVAILABLE"}
    
    # Create a simple frame record in inventory
    class SimpleFrame:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)
    
    frame = SimpleFrame(**frame_data)
    app.inventory.stock_units[frame.id] = frame
    print(f"  {frame.name} - ₹{frame.price}")
    
    # Process sale
    print("\n💰 Process Sale")
    success, msg, result = app.process_optical_sale(
        store.id, emp.id, emp.full_name, "cust-001", "Rajesh Kumar", "9876543210",
        rx.id, "RB5154-001", Decimal("6890"), Decimal("4500"),
        Decimal("10"), Decimal("8000"), PaymentMode.UPI
    )
    print(f"  {msg}")
    if result:
        print(f"  Invoice: {result['invoice_number']}")
        print(f"  Total: ₹{result['total']}, Balance: ₹{result['balance']}")
    
    # End day
    print("\n🌙 End Day")
    success, msg, results = app.end_day(store.id, emp.id, emp.full_name, Decimal("5000"), 23.6693, 86.1511)
    print(f"  {msg}")
    
    # Dashboard
    print("\n📊 Dashboard")
    dash = app.get_dashboard(store.id)
    print(f"  Tasks - Open: {dash['tasks']['open']}, Overdue: {dash['tasks']['overdue']}")
    
    # Audit
    print("\n📜 Audit Trail")
    for log in app.audit_logs:
        print(f"  [{log.timestamp.strftime('%H:%M')}] {log.action} {log.entity_type}")
    
    print("\n" + "=" * 60)
    print("DEMO COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    demo_ims()

"""
IMS 2.0 - Customer & CRM Engine
================================
"""
from dataclasses import dataclass, field
from datetime import datetime, date
from decimal import Decimal
from enum import Enum
from typing import List, Optional, Dict, Tuple
import uuid

class CustomerType(Enum):
    B2C = "B2C"
    B2B = "B2B"

class CustomerGroup(Enum):
    REGULAR = "REGULAR"
    PREMIUM = "PREMIUM"
    VIP = "VIP"
    CORPORATE = "CORPORATE"

class LoyaltyTier(Enum):
    BRONZE = "BRONZE"
    SILVER = "SILVER"
    GOLD = "GOLD"
    PLATINUM = "PLATINUM"

@dataclass
class Address:
    line1: str
    line2: str = ""
    city: str = ""
    state: str = ""
    pincode: str = ""
    country: str = "India"

@dataclass
class Patient:
    id: str
    customer_id: str
    name: str
    mobile: str = ""
    email: str = ""
    dob: Optional[date] = None
    anniversary: Optional[date] = None
    created_at: datetime = field(default_factory=datetime.now)

@dataclass
class Customer:
    id: str
    customer_code: str
    customer_type: CustomerType
    name: str
    mobile: str
    email: str = ""
    
    # B2B
    gstin: str = ""
    pan: str = ""
    legal_name: str = ""
    
    # Address
    billing_address: Optional[Address] = None
    shipping_address: Optional[Address] = None
    
    # Classification
    customer_group: CustomerGroup = CustomerGroup.REGULAR
    
    # Loyalty
    loyalty_points: int = 0
    loyalty_tier: LoyaltyTier = LoyaltyTier.BRONZE
    total_purchases: Decimal = Decimal("0")
    
    # Credit
    credit_enabled: bool = False
    credit_limit: Decimal = Decimal("0")
    outstanding: Decimal = Decimal("0")
    
    # Patients
    patients: List[Patient] = field(default_factory=list)
    
    created_at: datetime = field(default_factory=datetime.now)
    is_active: bool = True

@dataclass
class OrderTracking:
    id: str
    order_id: str
    order_number: str
    customer_id: str
    customer_phone: str
    
    # Status
    current_status: str = "ORDERED"
    status_history: List[Dict] = field(default_factory=list)
    
    # Delivery
    expected_date: Optional[date] = None
    tracking_token: str = ""
    tracking_url: str = ""
    
    created_at: datetime = field(default_factory=datetime.now)

class CustomerEngine:
    def __init__(self):
        self.customers: Dict[str, Customer] = {}
        self.patients: Dict[str, Patient] = {}
        self.tracking: Dict[str, OrderTracking] = {}
        self._cust_counter = 0
    
    def _gen_code(self, prefix: str) -> str:
        self._cust_counter += 1
        return f"{prefix}-{date.today().strftime('%Y%m')}-{self._cust_counter:05d}"
    
    def create_customer(self, cust_type: CustomerType, name: str, mobile: str, 
                        email: str = "", gstin: str = "") -> Tuple[bool, str, Optional[Customer]]:
        # Check duplicate
        for c in self.customers.values():
            if c.mobile == mobile:
                return False, "Mobile already exists", None
        
        customer = Customer(
            id=str(uuid.uuid4()),
            customer_code=self._gen_code("CUST"),
            customer_type=cust_type,
            name=name,
            mobile=mobile,
            email=email,
            gstin=gstin
        )
        
        if cust_type == CustomerType.B2B and gstin:
            customer.pan = gstin[2:12] if len(gstin) >= 12 else ""
        
        self.customers[customer.id] = customer
        return True, f"Customer {customer.customer_code} created", customer
    
    def add_patient(self, customer_id: str, name: str, mobile: str = "", 
                    dob: date = None) -> Tuple[bool, str, Optional[Patient]]:
        customer = self.customers.get(customer_id)
        if not customer:
            return False, "Customer not found", None
        
        patient = Patient(
            id=str(uuid.uuid4()),
            customer_id=customer_id,
            name=name,
            mobile=mobile or customer.mobile,
            dob=dob
        )
        
        self.patients[patient.id] = patient
        customer.patients.append(patient)
        return True, f"Patient {name} added", patient
    
    def record_purchase(self, customer_id: str, amount: Decimal) -> Tuple[bool, str]:
        customer = self.customers.get(customer_id)
        if not customer:
            return False, "Customer not found"
        
        customer.total_purchases += amount
        
        # Add loyalty points (1 point per ₹100)
        points = int(amount / 100)
        customer.loyalty_points += points
        
        # Update tier
        if customer.total_purchases >= 100000:
            customer.loyalty_tier = LoyaltyTier.PLATINUM
        elif customer.total_purchases >= 50000:
            customer.loyalty_tier = LoyaltyTier.GOLD
        elif customer.total_purchases >= 25000:
            customer.loyalty_tier = LoyaltyTier.SILVER
        
        return True, f"Added {points} loyalty points. Tier: {customer.loyalty_tier.value}"
    
    def redeem_points(self, customer_id: str, points: int) -> Tuple[bool, str, Decimal]:
        customer = self.customers.get(customer_id)
        if not customer:
            return False, "Customer not found", Decimal("0")
        
        if points > customer.loyalty_points:
            return False, "Insufficient points", Decimal("0")
        
        # 1 point = ₹1 discount
        discount = Decimal(str(points))
        customer.loyalty_points -= points
        
        return True, f"Redeemed {points} points", discount
    
    def create_order_tracking(self, order_id: str, order_number: str, 
                               customer_id: str, expected_date: date) -> OrderTracking:
        customer = self.customers.get(customer_id)
        
        token = uuid.uuid4().hex[:8].upper()
        tracking = OrderTracking(
            id=str(uuid.uuid4()),
            order_id=order_id,
            order_number=order_number,
            customer_id=customer_id,
            customer_phone=customer.mobile if customer else "",
            expected_date=expected_date,
            tracking_token=token,
            tracking_url=f"https://bettervision.in/track/{token}"
        )
        
        tracking.status_history.append({
            "status": "ORDERED",
            "timestamp": datetime.now().isoformat(),
            "note": "Order placed"
        })
        
        self.tracking[tracking.id] = tracking
        return tracking
    
    def update_order_status(self, tracking_id: str, status: str, note: str = "") -> Tuple[bool, str]:
        track = self.tracking.get(tracking_id)
        if not track:
            return False, "Tracking not found"
        
        track.current_status = status
        track.status_history.append({
            "status": status,
            "timestamp": datetime.now().isoformat(),
            "note": note
        })
        
        return True, f"Status updated to {status}"
    
    def get_tracking_by_token(self, token: str) -> Optional[OrderTracking]:
        for track in self.tracking.values():
            if track.tracking_token == token:
                return track
        return None
    
    def search_customers(self, query: str) -> List[Customer]:
        query = query.lower()
        results = []
        for c in self.customers.values():
            if query in c.name.lower() or query in c.mobile:
                results.append(c)
        return results[:20]
    
    def get_customer_summary(self, customer_id: str) -> Dict:
        customer = self.customers.get(customer_id)
        if not customer:
            return {}
        
        return {
            "customer_code": customer.customer_code,
            "name": customer.name,
            "mobile": customer.mobile,
            "type": customer.customer_type.value,
            "group": customer.customer_group.value,
            "total_purchases": float(customer.total_purchases),
            "loyalty_points": customer.loyalty_points,
            "loyalty_tier": customer.loyalty_tier.value,
            "patients": len(customer.patients),
            "credit_enabled": customer.credit_enabled,
            "outstanding": float(customer.outstanding)
        }


def demo_customer():
    print("=" * 60)
    print("IMS 2.0 CUSTOMER ENGINE DEMO")
    print("=" * 60)
    
    engine = CustomerEngine()
    
    # Create B2C customer
    print("\n👤 Create B2C Customer")
    success, msg, cust = engine.create_customer(
        CustomerType.B2C, "Rajesh Kumar", "9876543210", "rajesh@email.com"
    )
    print(f"  {msg}")
    print(f"  Code: {cust.customer_code}")
    
    # Add patient
    print("\n👨‍👩‍👧 Add Patient")
    success, msg, patient = engine.add_patient(cust.id, "Rajesh Kumar", dob=date(1985, 5, 15))
    print(f"  {msg}")
    
    success, msg, patient2 = engine.add_patient(cust.id, "Priya Kumar", dob=date(1988, 8, 20))
    print(f"  {msg}")
    
    # Record purchases
    print("\n💰 Record Purchases")
    success, msg = engine.record_purchase(cust.id, Decimal("15000"))
    print(f"  ₹15,000: {msg}")
    
    success, msg = engine.record_purchase(cust.id, Decimal("12000"))
    print(f"  ₹12,000: {msg}")
    
    # Redeem points
    print("\n🎁 Redeem Loyalty Points")
    success, msg, discount = engine.redeem_points(cust.id, 100)
    print(f"  {msg} - Discount: ₹{discount}")
    
    # Create B2B customer
    print("\n🏢 Create B2B Customer")
    success, msg, b2b = engine.create_customer(
        CustomerType.B2B, "ABC Corp", "9123456789", 
        gstin="20AABCU9603R1ZM"
    )
    print(f"  {msg}")
    print(f"  PAN: {b2b.pan}")
    
    # Order tracking
    print("\n📦 Order Tracking")
    tracking = engine.create_order_tracking(
        "order-001", "BV/ORD/001", cust.id, date.today()
    )
    print(f"  Token: {tracking.tracking_token}")
    print(f"  URL: {tracking.tracking_url}")
    
    engine.update_order_status(tracking.id, "LENS_ORDERED", "Lens ordered from lab")
    engine.update_order_status(tracking.id, "FITTING", "Frame fitting in progress")
    
    # Lookup by token
    found = engine.get_tracking_by_token(tracking.tracking_token)
    print(f"  Current Status: {found.current_status}")
    print(f"  History: {len(found.status_history)} updates")
    
    # Summary
    print("\n📊 Customer Summary")
    summary = engine.get_customer_summary(cust.id)
    for k, v in summary.items():
        print(f"  {k}: {v}")
    
    print("\n" + "=" * 60)


if __name__ == "__main__":
    demo_customer()

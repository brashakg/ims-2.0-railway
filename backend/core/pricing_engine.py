"""
IMS 2.0 - Pricing Engine
========================
This module enforces ALL pricing rules from your business requirements:
1. MRP vs Offer Price validation
2. Role-based discount caps
3. Category-based discount limits
4. Brand-level discount restrictions (luxury brands)
5. Approval workflow for exceeding limits

CRITICAL BUSINESS RULES:
- If Offer Price < MRP → No further discounts allowed (HQ override only)
- If Offer Price > MRP → BLOCK (never allow)
- If Offer Price = MRP → Role-based discounts applicable
- Luxury brands have their own caps (e.g., Cartier max 2%)
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Optional, Tuple
from datetime import datetime
import uuid


class DiscountClass(Enum):
    """Category-based discount classification"""
    MASS = "MASS"           # Contact lenses, accessories, wall clocks
    PREMIUM = "PREMIUM"     # Frames, optical lenses, smartwatches, hearing aids
    LUXURY = "LUXURY"       # Watches, smart glasses (Ray-Ban Meta), luxury brand frames
    SERVICE = "SERVICE"     # Services (fitting, repairs)
    NON_DISCOUNTABLE = "NON_DISCOUNTABLE"


class PricingDecision(Enum):
    """Outcome of pricing validation"""
    ALLOWED = "ALLOWED"
    BLOCKED = "BLOCKED"
    REQUIRES_APPROVAL = "REQUIRES_APPROVAL"


@dataclass
class Role:
    """User role with discount authority"""
    code: str
    name: str
    max_discount_percent: Decimal
    hierarchy_level: int
    can_approve_discounts: bool


@dataclass
class Product:
    """Product with pricing information"""
    id: str
    sku: str
    name: str
    mrp: Decimal
    offer_price: Decimal
    category_code: str
    discount_class: DiscountClass
    brand_code: Optional[str] = None
    is_luxury_brand: bool = False
    brand_max_discount: Optional[Decimal] = None


@dataclass
class DiscountRequest:
    """Request to apply discount"""
    product: Product
    requested_discount_percent: Decimal
    user_role: Role
    store_id: str
    reason: Optional[str] = None


@dataclass
class PricingResult:
    """Result of pricing calculation"""
    decision: PricingDecision
    
    # Original values
    mrp: Decimal
    offer_price: Decimal
    requested_discount_percent: Decimal
    
    # Calculated values
    allowed_discount_percent: Decimal
    final_unit_price: Decimal
    discount_amount: Decimal
    
    # Metadata
    reason: str
    requires_approval_from_role: Optional[str] = None
    approval_id: Optional[str] = None
    
    # GST calculation
    gst_rate: Decimal = Decimal("18.00")
    gst_amount: Decimal = Decimal("0")
    final_price_with_gst: Decimal = Decimal("0")


class PricingEngine:
    """
    Core pricing engine for IMS 2.0
    
    Enforces your exact business rules for pricing, discounts, and approvals.
    """
    
    # Default role discount caps (from your requirements)
    ROLE_DISCOUNT_CAPS = {
        "SUPERADMIN": Decimal("100.00"),
        "ADMIN": Decimal("100.00"),
        "AREA_MANAGER": Decimal("25.00"),
        "STORE_MANAGER": Decimal("20.00"),
        "ACCOUNTANT": Decimal("0.00"),
        "CATALOG_MANAGER": Decimal("0.00"),
        "OPTOMETRIST": Decimal("0.00"),
        "SALES_CASHIER": Decimal("10.00"),
        "SALES_STAFF": Decimal("10.00"),
        "FITTING_OPTICAL": Decimal("0.00"),
        "FITTING_WATCH": Decimal("0.00"),
    }
    
    # Discount class limits (additional category-level restrictions)
    CATEGORY_DISCOUNT_LIMITS = {
        DiscountClass.MASS: Decimal("15.00"),           # Max 15% on mass products
        DiscountClass.PREMIUM: Decimal("20.00"),        # Max 20% on premium
        DiscountClass.LUXURY: Decimal("5.00"),          # Max 5% on luxury (unless brand says otherwise)
        DiscountClass.SERVICE: Decimal("10.00"),        # Max 10% on services
        DiscountClass.NON_DISCOUNTABLE: Decimal("0.00"),
    }
    
    def __init__(self, hq_override_enabled: bool = False):
        """
        Initialize pricing engine.
        
        Args:
            hq_override_enabled: If True, allows HQ to override discount restrictions
        """
        self.hq_override_enabled = hq_override_enabled
    
    def validate_product_pricing(self, product: Product) -> Tuple[bool, str]:
        """
        Validate that product pricing is valid before it can be sold.
        
        CRITICAL RULE: Offer Price can NEVER be greater than MRP
        
        Returns:
            (is_valid, message)
        """
        if product.offer_price > product.mrp:
            return False, f"BLOCKED: Offer price (₹{product.offer_price}) cannot exceed MRP (₹{product.mrp})"
        
        return True, "Pricing valid"
    
    def get_effective_discount_cap(
        self,
        role: Role,
        product: Product,
        is_hq_user: bool = False
    ) -> Decimal:
        """
        Calculate the maximum discount allowed considering:
        1. Role-based cap
        2. Category-based cap
        3. Brand-level cap (for luxury brands)
        4. MRP vs Offer Price rule
        
        The LOWEST of all applicable caps is used.
        """
        caps = []
        
        # 1. Role-based cap
        role_cap = self.ROLE_DISCOUNT_CAPS.get(role.code, Decimal("0"))
        caps.append(role_cap)
        
        # 2. Category-based cap
        category_cap = self.CATEGORY_DISCOUNT_LIMITS.get(
            product.discount_class, 
            Decimal("0")
        )
        caps.append(category_cap)
        
        # 3. Brand-level cap (luxury brands have their own limits)
        if product.is_luxury_brand and product.brand_max_discount is not None:
            caps.append(product.brand_max_discount)
        
        # 4. MRP vs Offer Price rule
        if product.offer_price < product.mrp:
            # Offer price already discounted - NO further discount allowed
            # Unless HQ override is enabled
            if not (is_hq_user and self.hq_override_enabled):
                caps.append(Decimal("0"))
        
        # Return the minimum (most restrictive) cap
        return min(caps)
    
    def calculate_pricing(
        self,
        request: DiscountRequest,
        is_hq_user: bool = False
    ) -> PricingResult:
        """
        Main pricing calculation method.
        
        This enforces ALL your business rules and returns a complete pricing result.
        """
        product = request.product
        role = request.user_role
        requested_discount = request.requested_discount_percent
        
        # Step 1: Validate product pricing is valid
        is_valid, validation_msg = self.validate_product_pricing(product)
        if not is_valid:
            return PricingResult(
                decision=PricingDecision.BLOCKED,
                mrp=product.mrp,
                offer_price=product.offer_price,
                requested_discount_percent=requested_discount,
                allowed_discount_percent=Decimal("0"),
                final_unit_price=product.mrp,
                discount_amount=Decimal("0"),
                reason=validation_msg
            )
        
        # Step 2: Get effective discount cap for this user + product combination
        max_allowed = self.get_effective_discount_cap(role, product, is_hq_user)
        
        # Step 3: Determine pricing decision
        if requested_discount <= Decimal("0"):
            # No discount requested - always allowed
            decision = PricingDecision.ALLOWED
            final_discount = Decimal("0")
            reason = "No discount applied"
        
        elif requested_discount <= max_allowed:
            # Within limits - allowed
            decision = PricingDecision.ALLOWED
            final_discount = requested_discount
            reason = f"Discount of {final_discount}% approved (within {role.name} limit of {max_allowed}%)"
        
        elif max_allowed == Decimal("0"):
            # No discount allowed at all
            if product.offer_price < product.mrp:
                reason = f"BLOCKED: Product already has offer price (₹{product.offer_price} vs MRP ₹{product.mrp}). No further discount allowed."
            else:
                reason = f"BLOCKED: {role.name} role cannot give discounts on this product category"
            
            decision = PricingDecision.BLOCKED
            final_discount = Decimal("0")
        
        else:
            # Exceeds limit - requires approval from higher authority
            decision = PricingDecision.REQUIRES_APPROVAL
            final_discount = max_allowed  # Apply max allowed for now
            
            # Find which role can approve this
            approver_role = self._find_approver_role(requested_discount, product)
            
            reason = (
                f"Requested discount ({requested_discount}%) exceeds {role.name} limit ({max_allowed}%). "
                f"Requires approval from {approver_role or 'Admin/Superadmin'}."
            )
            
            return PricingResult(
                decision=decision,
                mrp=product.mrp,
                offer_price=product.offer_price,
                requested_discount_percent=requested_discount,
                allowed_discount_percent=max_allowed,
                final_unit_price=self._calculate_discounted_price(product.offer_price, max_allowed),
                discount_amount=self._calculate_discount_amount(product.offer_price, max_allowed),
                reason=reason,
                requires_approval_from_role=approver_role,
                approval_id=str(uuid.uuid4())  # Generate approval request ID
            )
        
        # Step 4: Calculate final prices
        final_price = self._calculate_discounted_price(product.offer_price, final_discount)
        discount_amount = self._calculate_discount_amount(product.offer_price, final_discount)
        
        # Step 5: Calculate GST
        gst_rate = Decimal("18.00")  # Default GST rate
        gst_amount = (final_price * gst_rate / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        final_with_gst = final_price + gst_amount
        
        return PricingResult(
            decision=decision,
            mrp=product.mrp,
            offer_price=product.offer_price,
            requested_discount_percent=requested_discount,
            allowed_discount_percent=final_discount,
            final_unit_price=final_price,
            discount_amount=discount_amount,
            reason=reason,
            gst_rate=gst_rate,
            gst_amount=gst_amount,
            final_price_with_gst=final_with_gst
        )
    
    def _calculate_discounted_price(self, base_price: Decimal, discount_percent: Decimal) -> Decimal:
        """Calculate price after discount"""
        if discount_percent <= 0:
            return base_price
        
        discount_multiplier = (100 - discount_percent) / 100
        return (base_price * discount_multiplier).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    
    def _calculate_discount_amount(self, base_price: Decimal, discount_percent: Decimal) -> Decimal:
        """Calculate discount amount in rupees"""
        if discount_percent <= 0:
            return Decimal("0")
        
        return (base_price * discount_percent / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    
    def _find_approver_role(self, requested_discount: Decimal, product: Product) -> Optional[str]:
        """Find which role can approve the requested discount"""
        
        # Check category limit first
        category_limit = self.CATEGORY_DISCOUNT_LIMITS.get(product.discount_class, Decimal("0"))
        
        if requested_discount > category_limit:
            # Only Superadmin/Admin can override category limits
            return "Admin or Superadmin"
        
        # Find the lowest role that can approve this discount
        approval_hierarchy = [
            ("STORE_MANAGER", Decimal("20.00")),
            ("AREA_MANAGER", Decimal("25.00")),
            ("ADMIN", Decimal("100.00")),
            ("SUPERADMIN", Decimal("100.00")),
        ]
        
        for role_code, max_discount in approval_hierarchy:
            if requested_discount <= max_discount:
                return role_code.replace("_", " ").title()
        
        return "Superadmin"
    
    def process_approval(
        self,
        approval_id: str,
        approver_role: Role,
        original_request: DiscountRequest,
        approved_discount: Optional[Decimal] = None,
        is_approved: bool = True,
        rejection_reason: Optional[str] = None
    ) -> PricingResult:
        """
        Process a discount approval request.
        
        The approver can:
        - Approve the full requested discount
        - Approve a partial discount (approved_discount parameter)
        - Reject the request
        """
        product = original_request.product
        
        if not is_approved:
            return PricingResult(
                decision=PricingDecision.BLOCKED,
                mrp=product.mrp,
                offer_price=product.offer_price,
                requested_discount_percent=original_request.requested_discount_percent,
                allowed_discount_percent=Decimal("0"),
                final_unit_price=product.offer_price,
                discount_amount=Decimal("0"),
                reason=f"Discount rejected: {rejection_reason or 'No reason provided'}"
            )
        
        # Use approved discount or fall back to requested
        final_discount = approved_discount or original_request.requested_discount_percent
        
        # Verify approver has authority
        approver_cap = self.get_effective_discount_cap(approver_role, product, is_hq_user=True)
        
        if final_discount > approver_cap:
            return PricingResult(
                decision=PricingDecision.BLOCKED,
                mrp=product.mrp,
                offer_price=product.offer_price,
                requested_discount_percent=original_request.requested_discount_percent,
                allowed_discount_percent=approver_cap,
                final_unit_price=self._calculate_discounted_price(product.offer_price, approver_cap),
                discount_amount=self._calculate_discount_amount(product.offer_price, approver_cap),
                reason=f"Approver {approver_role.name} can only approve up to {approver_cap}%"
            )
        
        # Approval successful
        final_price = self._calculate_discounted_price(product.offer_price, final_discount)
        discount_amount = self._calculate_discount_amount(product.offer_price, final_discount)
        
        gst_rate = Decimal("18.00")
        gst_amount = (final_price * gst_rate / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        
        return PricingResult(
            decision=PricingDecision.ALLOWED,
            mrp=product.mrp,
            offer_price=product.offer_price,
            requested_discount_percent=original_request.requested_discount_percent,
            allowed_discount_percent=final_discount,
            final_unit_price=final_price,
            discount_amount=discount_amount,
            reason=f"Approved by {approver_role.name}: {final_discount}% discount",
            gst_rate=gst_rate,
            gst_amount=gst_amount,
            final_price_with_gst=final_price + gst_amount
        )


# =============================================================================
# USAGE EXAMPLES
# =============================================================================

def demo_pricing_scenarios():
    """Demonstrate pricing engine with your real business scenarios"""
    
    engine = PricingEngine()
    
    print("=" * 70)
    print("IMS 2.0 PRICING ENGINE - DEMO SCENARIOS")
    print("=" * 70)
    
    # Define roles
    sales_staff = Role(
        code="SALES_STAFF",
        name="Sales Staff",
        max_discount_percent=Decimal("10"),
        hierarchy_level=6,
        can_approve_discounts=False
    )
    
    store_manager = Role(
        code="STORE_MANAGER",
        name="Store Manager",
        max_discount_percent=Decimal("20"),
        hierarchy_level=4,
        can_approve_discounts=True
    )
    
    superadmin = Role(
        code="SUPERADMIN",
        name="Superadmin (CEO)",
        max_discount_percent=Decimal("100"),
        hierarchy_level=1,
        can_approve_discounts=True
    )
    
    # -------------------------------------------------------------------------
    # SCENARIO 1: Normal frame sale with allowed discount
    # -------------------------------------------------------------------------
    print("\n📦 SCENARIO 1: Normal Frame Sale")
    print("-" * 50)
    
    rayban_frame = Product(
        id="prod-001",
        sku="RB-3025-001",
        name="Ray-Ban Aviator RB3025",
        mrp=Decimal("8990.00"),
        offer_price=Decimal("8990.00"),  # Same as MRP, discounts allowed
        category_code="FRAME",
        discount_class=DiscountClass.PREMIUM,
        brand_code="RAYBAN",
        is_luxury_brand=False
    )
    
    request = DiscountRequest(
        product=rayban_frame,
        requested_discount_percent=Decimal("8"),  # 8% discount
        user_role=sales_staff,
        store_id="store-001"
    )
    
    result = engine.calculate_pricing(request)
    print(f"Product: {rayban_frame.name}")
    print(f"MRP: ₹{rayban_frame.mrp}")
    print(f"Requested Discount: {request.requested_discount_percent}%")
    print(f"Decision: {result.decision.value}")
    print(f"Final Price: ₹{result.final_unit_price}")
    print(f"Discount Amount: ₹{result.discount_amount}")
    print(f"Reason: {result.reason}")
    
    # -------------------------------------------------------------------------
    # SCENARIO 2: Offer price already set (NO further discount allowed)
    # -------------------------------------------------------------------------
    print("\n📦 SCENARIO 2: Product Already Has Offer Price")
    print("-" * 50)
    
    discounted_frame = Product(
        id="prod-002",
        sku="OAK-9102-001",
        name="Oakley Holbrook OO9102",
        mrp=Decimal("12500.00"),
        offer_price=Decimal("9999.00"),  # Already discounted by HQ
        category_code="FRAME",
        discount_class=DiscountClass.PREMIUM,
        brand_code="OAKLEY",
        is_luxury_brand=False
    )
    
    request = DiscountRequest(
        product=discounted_frame,
        requested_discount_percent=Decimal("5"),  # Trying to give additional 5%
        user_role=sales_staff,
        store_id="store-001"
    )
    
    result = engine.calculate_pricing(request)
    print(f"Product: {discounted_frame.name}")
    print(f"MRP: ₹{discounted_frame.mrp}")
    print(f"Offer Price: ₹{discounted_frame.offer_price}")
    print(f"Requested Additional Discount: {request.requested_discount_percent}%")
    print(f"Decision: {result.decision.value}")
    print(f"Reason: {result.reason}")
    
    # -------------------------------------------------------------------------
    # SCENARIO 3: Luxury brand with strict limits
    # -------------------------------------------------------------------------
    print("\n📦 SCENARIO 3: Luxury Brand (Cartier)")
    print("-" * 50)
    
    cartier_watch = Product(
        id="prod-003",
        sku="CAR-TANK-001",
        name="Cartier Tank Française",
        mrp=Decimal("350000.00"),
        offer_price=Decimal("350000.00"),
        category_code="WATCH",
        discount_class=DiscountClass.LUXURY,
        brand_code="CARTIER",
        is_luxury_brand=True,
        brand_max_discount=Decimal("2.00")  # Cartier allows max 2%
    )
    
    # Store manager tries to give 5% discount
    request = DiscountRequest(
        product=cartier_watch,
        requested_discount_percent=Decimal("5"),
        user_role=store_manager,
        store_id="store-001"
    )
    
    result = engine.calculate_pricing(request)
    print(f"Product: {cartier_watch.name}")
    print(f"MRP: ₹{cartier_watch.mrp}")
    print(f"Store Manager requests: {request.requested_discount_percent}% discount")
    print(f"Decision: {result.decision.value}")
    print(f"Allowed Discount: {result.allowed_discount_percent}%")
    print(f"Final Price: ₹{result.final_unit_price}")
    print(f"Reason: {result.reason}")
    
    # -------------------------------------------------------------------------
    # SCENARIO 4: Discount exceeds role limit, needs approval
    # -------------------------------------------------------------------------
    print("\n📦 SCENARIO 4: Discount Exceeds Role Limit")
    print("-" * 50)
    
    frame = Product(
        id="prod-004",
        sku="GUC-001",
        name="Gucci Square Frame",
        mrp=Decimal("25000.00"),
        offer_price=Decimal("25000.00"),
        category_code="FRAME",
        discount_class=DiscountClass.PREMIUM,
        brand_code="GUCCI",
        is_luxury_brand=True,
        brand_max_discount=Decimal("5.00")
    )
    
    # Sales staff tries to give 15% discount (their limit is 10%)
    request = DiscountRequest(
        product=frame,
        requested_discount_percent=Decimal("15"),
        user_role=sales_staff,
        store_id="store-001",
        reason="Customer is a regular, celebrating birthday"
    )
    
    result = engine.calculate_pricing(request)
    print(f"Product: {frame.name}")
    print(f"MRP: ₹{frame.mrp}")
    print(f"Sales Staff requests: {request.requested_discount_percent}% discount")
    print(f"Decision: {result.decision.value}")
    print(f"Needs approval from: {result.requires_approval_from_role}")
    print(f"Reason: {result.reason}")
    
    # Store manager approves
    if result.decision == PricingDecision.REQUIRES_APPROVAL:
        print("\n  → Store Manager reviews and approves at 5% (brand limit)...")
        approved_result = engine.process_approval(
            approval_id=result.approval_id,
            approver_role=store_manager,
            original_request=request,
            approved_discount=Decimal("5"),  # Approves only 5% due to brand limit
            is_approved=True
        )
        print(f"  → Final Decision: {approved_result.decision.value}")
        print(f"  → Approved Discount: {approved_result.allowed_discount_percent}%")
        print(f"  → Final Price: ₹{approved_result.final_unit_price}")
        print(f"  → Reason: {approved_result.reason}")
    
    # -------------------------------------------------------------------------
    # SCENARIO 5: BLOCKED - Offer price > MRP (should never happen)
    # -------------------------------------------------------------------------
    print("\n📦 SCENARIO 5: Invalid Pricing (Offer > MRP)")
    print("-" * 50)
    
    invalid_product = Product(
        id="prod-005",
        sku="INV-001",
        name="Invalid Product",
        mrp=Decimal("5000.00"),
        offer_price=Decimal("6000.00"),  # INVALID: Offer > MRP
        category_code="FRAME",
        discount_class=DiscountClass.PREMIUM
    )
    
    request = DiscountRequest(
        product=invalid_product,
        requested_discount_percent=Decimal("0"),
        user_role=sales_staff,
        store_id="store-001"
    )
    
    result = engine.calculate_pricing(request)
    print(f"Product: {invalid_product.name}")
    print(f"MRP: ₹{invalid_product.mrp}")
    print(f"Offer Price: ₹{invalid_product.offer_price}")
    print(f"Decision: {result.decision.value}")
    print(f"Reason: {result.reason}")
    
    print("\n" + "=" * 70)
    print("END OF DEMO")
    print("=" * 70)


if __name__ == "__main__":
    demo_pricing_scenarios()

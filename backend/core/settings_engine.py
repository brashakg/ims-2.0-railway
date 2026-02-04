"""
IMS 2.0 - Settings & Configuration Engine
==========================================
Comprehensive system configuration for Superadmin

Sections:
1. Store Configuration
2. Role & Permission Management
3. Product Category Configuration
4. Discount Rules
5. Tax Configuration (GST)
6. Workflow Settings
7. Notification Settings
8. UI/Theme Settings
9. Feature Toggles
10. Backup & Maintenance
"""
from dataclasses import dataclass, field
from datetime import datetime, date, time
from decimal import Decimal
from enum import Enum
from typing import List, Optional, Dict, Any, Tuple, Set
import uuid
import json

class SettingCategory(Enum):
    STORE = "STORE"
    ROLE = "ROLE"
    PRODUCT = "PRODUCT"
    DISCOUNT = "DISCOUNT"
    TAX = "TAX"
    WORKFLOW = "WORKFLOW"
    NOTIFICATION = "NOTIFICATION"
    UI = "UI"
    FEATURE = "FEATURE"
    SYSTEM = "SYSTEM"

class PermissionType(Enum):
    # POS
    POS_CREATE_SALE = "POS_CREATE_SALE"
    POS_APPLY_DISCOUNT = "POS_APPLY_DISCOUNT"
    POS_VOID_SALE = "POS_VOID_SALE"
    POS_REFUND = "POS_REFUND"
    
    # Inventory
    INV_VIEW_STOCK = "INV_VIEW_STOCK"
    INV_ACCEPT_STOCK = "INV_ACCEPT_STOCK"
    INV_TRANSFER_STOCK = "INV_TRANSFER_STOCK"
    INV_ADJUST_STOCK = "INV_ADJUST_STOCK"
    INV_COUNT_STOCK = "INV_COUNT_STOCK"
    
    # Clinical
    CLINICAL_EYE_TEST = "CLINICAL_EYE_TEST"
    CLINICAL_PRESCRIPTION = "CLINICAL_PRESCRIPTION"
    CLINICAL_OVERRIDE_RX = "CLINICAL_OVERRIDE_RX"
    
    # Finance
    FIN_VIEW_REPORTS = "FIN_VIEW_REPORTS"
    FIN_MANAGE_TILL = "FIN_MANAGE_TILL"
    FIN_APPROVE_EXPENSE = "FIN_APPROVE_EXPENSE"
    FIN_CREDIT_NOTE = "FIN_CREDIT_NOTE"
    
    # HR
    HR_VIEW_ATTENDANCE = "HR_VIEW_ATTENDANCE"
    HR_APPROVE_LEAVE = "HR_APPROVE_LEAVE"
    HR_MANAGE_SALARY = "HR_MANAGE_SALARY"
    
    # Admin
    ADMIN_MANAGE_USERS = "ADMIN_MANAGE_USERS"
    ADMIN_MANAGE_STORES = "ADMIN_MANAGE_STORES"
    ADMIN_VIEW_AUDIT = "ADMIN_VIEW_AUDIT"
    ADMIN_SETTINGS = "ADMIN_SETTINGS"
    
    # Superadmin
    SUPER_AI_ACCESS = "SUPER_AI_ACCESS"
    SUPER_OVERRIDE = "SUPER_OVERRIDE"
    SUPER_SYSTEM_CONFIG = "SUPER_SYSTEM_CONFIG"

@dataclass
class StoreConfig:
    id: str
    store_id: str
    store_code: str
    store_name: str
    brand: str  # "Better Vision" or "WizOpt"
    
    # GST Details
    gstin: str
    legal_name: str
    trade_name: str
    state_code: str
    
    # Location
    address: str
    city: str
    state: str
    pincode: str
    latitude: float
    longitude: float
    geo_fence_radius: float = 100.0  # meters for attendance
    
    # Operations
    opening_time: time = time(10, 0)
    closing_time: time = time(20, 0)
    week_off_days: List[int] = field(default_factory=lambda: [0])  # Sunday
    
    # Inventory
    allowed_categories: List[str] = field(default_factory=list)
    auto_reorder_enabled: bool = False
    
    # Feature Flags (store-specific)
    features: Dict[str, bool] = field(default_factory=dict)
    
    is_active: bool = True
    created_at: datetime = field(default_factory=datetime.now)

@dataclass
class RoleConfig:
    id: str
    role_code: str
    role_name: str
    description: str
    
    # Hierarchy
    level: int  # 1 = highest (Superadmin), 10 = lowest (Staff)
    reports_to: List[str] = field(default_factory=list)
    
    # Permissions
    permissions: Set[PermissionType] = field(default_factory=set)
    
    # Discount Authority
    max_discount_percent: Decimal = Decimal("0")
    can_approve_discount: bool = False
    discount_approval_limit: Decimal = Decimal("0")
    
    # Store Access
    all_stores_access: bool = False
    assigned_store_ids: List[str] = field(default_factory=list)
    
    # Dashboard
    dashboard_type: str = "STAFF"  # STAFF, MANAGER, ADMIN, SUPERADMIN
    
    is_active: bool = True

@dataclass
class CategoryConfig:
    id: str
    category_code: str
    category_name: str
    parent_category: Optional[str] = None
    
    # Attributes
    mandatory_attributes: List[str] = field(default_factory=list)
    optional_attributes: List[str] = field(default_factory=list)
    
    # SKU Pattern
    sku_prefix: str = ""
    sku_pattern: str = ""  # e.g., "{BRAND}-{MODEL}-{COLOR}"
    
    # Pricing
    default_tax_rate: Decimal = Decimal("18")  # GST %
    hsn_code: str = ""
    
    # Discount Rules
    max_discount_percent: Decimal = Decimal("100")
    discount_approval_required_above: Decimal = Decimal("20")
    luxury_flag: bool = False  # If true, stricter discount rules
    
    # Stock
    track_inventory: bool = True
    track_expiry: bool = False  # For contact lenses
    track_batch: bool = False
    
    # Store Assignment
    enabled_stores: List[str] = field(default_factory=list)
    
    is_active: bool = True

@dataclass
class DiscountRule:
    id: str
    rule_name: str
    
    # Conditions
    category_codes: List[str] = field(default_factory=list)  # Empty = all
    store_ids: List[str] = field(default_factory=list)  # Empty = all
    customer_groups: List[str] = field(default_factory=list)
    min_order_value: Decimal = Decimal("0")
    max_order_value: Optional[Decimal] = None
    
    # Limits by Role
    role_limits: Dict[str, Decimal] = field(default_factory=dict)  # role_code -> max %
    
    # Approval
    approval_required_above: Decimal = Decimal("0")
    approver_roles: List[str] = field(default_factory=list)
    
    # Time-based
    valid_from: Optional[date] = None
    valid_until: Optional[date] = None
    valid_days: List[int] = field(default_factory=lambda: [0,1,2,3,4,5,6])
    
    priority: int = 1  # Lower = higher priority
    is_active: bool = True

@dataclass
class TaxConfig:
    id: str
    tax_name: str
    tax_type: str  # CGST, SGST, IGST, CESS
    rate: Decimal
    hsn_codes: List[str] = field(default_factory=list)
    is_active: bool = True

@dataclass
class WorkflowConfig:
    id: str
    workflow_name: str
    workflow_type: str  # APPROVAL, ESCALATION, NOTIFICATION
    
    # Trigger
    trigger_event: str
    trigger_conditions: Dict[str, Any] = field(default_factory=dict)
    
    # Steps
    steps: List[Dict[str, Any]] = field(default_factory=list)
    
    # Timing
    sla_minutes: int = 0
    escalation_enabled: bool = False
    escalation_after_minutes: int = 0
    escalate_to_role: Optional[str] = None
    
    is_active: bool = True

@dataclass
class NotificationConfig:
    id: str
    event_type: str
    
    # Channels
    sms_enabled: bool = False
    sms_template: Optional[str] = None
    
    email_enabled: bool = False
    email_template: Optional[str] = None
    
    whatsapp_enabled: bool = False
    whatsapp_template: Optional[str] = None
    
    push_enabled: bool = False
    push_template: Optional[str] = None
    
    # Recipients
    recipient_roles: List[str] = field(default_factory=list)
    
    is_active: bool = True

@dataclass
class FeatureToggle:
    id: str
    feature_code: str
    feature_name: str
    description: str
    
    # Scope
    global_enabled: bool = False
    enabled_stores: List[str] = field(default_factory=list)
    enabled_roles: List[str] = field(default_factory=list)
    
    # Rollout
    rollout_percentage: int = 0  # 0-100
    
    created_at: datetime = field(default_factory=datetime.now)


class SettingsEngine:
    """
    Comprehensive Settings Management
    
    All settings configurable by Superadmin through UI
    """
    
    def __init__(self):
        self.stores: Dict[str, StoreConfig] = {}
        self.roles: Dict[str, RoleConfig] = {}
        self.categories: Dict[str, CategoryConfig] = {}
        self.discount_rules: Dict[str, DiscountRule] = {}
        self.tax_configs: Dict[str, TaxConfig] = {}
        self.workflows: Dict[str, WorkflowConfig] = {}
        self.notifications: Dict[str, NotificationConfig] = {}
        self.features: Dict[str, FeatureToggle] = {}
        self.system_settings: Dict[str, Any] = {}
        
        self._initialize_default_roles()
        self._initialize_default_categories()
        self._initialize_default_taxes()
        self._initialize_system_settings()
    
    def _initialize_default_roles(self):
        """Initialize default role configurations"""
        
        roles_data = [
            ("SUPERADMIN", "Superadmin", "CEO - Full system access", 1, Decimal("100"), {
                PermissionType.SUPER_AI_ACCESS, PermissionType.SUPER_OVERRIDE, 
                PermissionType.SUPER_SYSTEM_CONFIG, PermissionType.ADMIN_SETTINGS
            }),
            ("ADMIN", "Admin", "Directors - HQ control", 2, Decimal("100"), {
                PermissionType.ADMIN_MANAGE_USERS, PermissionType.ADMIN_MANAGE_STORES,
                PermissionType.ADMIN_VIEW_AUDIT, PermissionType.ADMIN_SETTINGS
            }),
            ("AREA_MANAGER", "Area Manager", "Regional oversight", 3, Decimal("25"), {
                PermissionType.INV_VIEW_STOCK, PermissionType.INV_TRANSFER_STOCK,
                PermissionType.FIN_VIEW_REPORTS, PermissionType.HR_APPROVE_LEAVE
            }),
            ("STORE_MANAGER", "Store Manager", "Store-level control", 4, Decimal("20"), {
                PermissionType.POS_CREATE_SALE, PermissionType.POS_APPLY_DISCOUNT,
                PermissionType.INV_ACCEPT_STOCK, PermissionType.INV_COUNT_STOCK,
                PermissionType.FIN_MANAGE_TILL, PermissionType.HR_VIEW_ATTENDANCE
            }),
            ("ACCOUNTANT", "Accountant", "Finance & compliance", 4, Decimal("0"), {
                PermissionType.FIN_VIEW_REPORTS, PermissionType.FIN_APPROVE_EXPENSE,
                PermissionType.FIN_CREDIT_NOTE
            }),
            ("CATALOG_MANAGER", "Catalog Manager", "Product management", 5, Decimal("0"), {
                PermissionType.INV_VIEW_STOCK, PermissionType.INV_ADJUST_STOCK
            }),
            ("OPTOMETRIST", "Optometrist", "Clinical services", 6, Decimal("5"), {
                PermissionType.CLINICAL_EYE_TEST, PermissionType.CLINICAL_PRESCRIPTION,
                PermissionType.POS_CREATE_SALE
            }),
            ("SALES_CASHIER", "Sales Cashier", "POS + Cash handling", 7, Decimal("10"), {
                PermissionType.POS_CREATE_SALE, PermissionType.POS_APPLY_DISCOUNT,
                PermissionType.FIN_MANAGE_TILL
            }),
            ("SALES_STAFF", "Sales Staff", "POS only", 8, Decimal("10"), {
                PermissionType.POS_CREATE_SALE, PermissionType.POS_APPLY_DISCOUNT
            }),
            ("WORKSHOP_STAFF", "Workshop Staff", "Fitting & repairs", 9, Decimal("0"), {
                PermissionType.INV_VIEW_STOCK
            }),
        ]
        
        for code, name, desc, level, discount, perms in roles_data:
            role = RoleConfig(
                id=str(uuid.uuid4()),
                role_code=code,
                role_name=name,
                description=desc,
                level=level,
                permissions=perms,
                max_discount_percent=discount,
                all_stores_access=(level <= 3),
                dashboard_type="SUPERADMIN" if level == 1 else "ADMIN" if level <= 3 else "MANAGER" if level <= 5 else "STAFF"
            )
            self.roles[code] = role
    
    def _initialize_default_categories(self):
        """Initialize default product categories"""
        
        categories_data = [
            ("FRAME", "Frame", "900490", ["brand", "model_no", "color_code", "size", "material", "type"], False),
            ("SUNGLASS", "Sunglass", "900410", ["brand", "model_no", "color_code", "size", "lens_color"], False),
            ("READING_GLASSES", "Reading Glasses", "900490", ["brand", "model_no", "power", "color", "size"], False),
            ("OPTICAL_LENS", "Optical Lens", "9001", ["brand", "type", "material", "coating", "power_range"], False),
            ("CONTACT_LENS", "Contact Lens", "90013100", ["brand", "product_name", "power", "bc", "dia", "pack_size"], True),
            ("COLORED_CONTACT_LENS", "Colored Contact Lens", "90013100", ["brand", "product_name", "color", "power", "bc", "dia", "pack_size"], True),
            ("WATCH", "Watch", "9102", ["brand", "model_no", "dial_color", "strap_type", "movement"], False),
            ("SMARTWATCH", "Smartwatch", "8517", ["brand", "model_no", "color", "connectivity"], False),
            ("SMARTGLASSES", "Smart Glasses", "900490", ["brand", "model_no", "color", "connectivity", "features"], False),
            ("WALL_CLOCK", "Wall Clock", "9105", ["brand", "model_no", "size", "type", "material"], False),
            ("ACCESSORY", "Accessory", "9003", ["type", "brand", "description"], False),
            ("SERVICE", "Service", "9987", ["service_type", "description"], False),
        ]
        
        for code, name, hsn, attrs, track_exp in categories_data:
            cat = CategoryConfig(
                id=str(uuid.uuid4()),
                category_code=code,
                category_name=name,
                hsn_code=hsn,
                mandatory_attributes=attrs,
                track_expiry=track_exp,
                track_batch=track_exp,
                luxury_flag=(code in ["FRAME", "SUNGLASS", "WATCH", "SMARTGLASSES"])
            )
            self.categories[code] = cat
    
    def _initialize_default_taxes(self):
        """Initialize GST configuration"""
        taxes = [
            ("CGST_9", "CGST", "CGST", Decimal("9")),
            ("SGST_9", "SGST", "SGST", Decimal("9")),
            ("IGST_18", "IGST", "IGST", Decimal("18")),
            ("CGST_6", "CGST 6%", "CGST", Decimal("6")),
            ("SGST_6", "SGST 6%", "SGST", Decimal("6")),
            ("IGST_12", "IGST 12%", "IGST", Decimal("12")),
        ]
        
        for code, name, tax_type, rate in taxes:
            tax = TaxConfig(
                id=str(uuid.uuid4()),
                tax_name=name,
                tax_type=tax_type,
                rate=rate
            )
            self.tax_configs[code] = tax
    
    def _initialize_system_settings(self):
        """Initialize system-wide settings"""
        self.system_settings = {
            # General
            "company_name": "Better Vision Opticals Private Limited",
            "default_currency": "INR",
            "timezone": "Asia/Kolkata",
            "date_format": "DD/MM/YYYY",
            "financial_year_start": "04-01",  # April 1
            
            # Pricing
            "mrp_offer_rule": "BLOCK_IF_OFFER_GREATER",  # MRP < Offer = BLOCK
            "default_gst_rate": 18,
            
            # Prescription
            "default_rx_validity_months": 12,
            "external_rx_validity_months": 6,
            "rx_required_for_lens": True,
            
            # Attendance
            "attendance_geo_required": True,
            "default_geo_fence_meters": 100,
            "late_grace_minutes": 15,
            
            # Till
            "daily_till_mandatory": True,
            "variance_alert_threshold": 100,
            
            # Stock
            "low_stock_alert_days": 30,
            "expiry_alert_days": 90,
            
            # AI
            "ai_enabled": True,
            "ai_superadmin_only": True,
            "ai_auto_execute": False,  # Always False
            
            # Backup
            "auto_backup_enabled": True,
            "backup_retention_days": 90,
        }
    
    # =========================================================================
    # STORE MANAGEMENT
    # =========================================================================
    
    def create_store(self, store_data: Dict) -> Tuple[bool, str, Optional[StoreConfig]]:
        """Create a new store configuration"""
        required = ["store_code", "store_name", "brand", "gstin", "state_code", "address", "city", "state", "pincode"]
        missing = [f for f in required if f not in store_data]
        if missing:
            return False, f"Missing required fields: {missing}", None
        
        store = StoreConfig(
            id=str(uuid.uuid4()),
            store_id=str(uuid.uuid4()),
            store_code=store_data["store_code"],
            store_name=store_data["store_name"],
            brand=store_data["brand"],
            gstin=store_data["gstin"],
            legal_name=store_data.get("legal_name", store_data["store_name"]),
            trade_name=store_data.get("trade_name", store_data["store_name"]),
            state_code=store_data["state_code"],
            address=store_data["address"],
            city=store_data["city"],
            state=store_data["state"],
            pincode=store_data["pincode"],
            latitude=store_data.get("latitude", 0.0),
            longitude=store_data.get("longitude", 0.0)
        )
        
        self.stores[store.store_code] = store
        return True, f"Store {store.store_code} created", store
    
    def update_store_features(self, store_code: str, features: Dict[str, bool]) -> Tuple[bool, str]:
        """Update store-specific feature flags"""
        store = self.stores.get(store_code)
        if not store:
            return False, "Store not found"
        
        store.features.update(features)
        return True, f"Features updated for {store_code}"
    
    # =========================================================================
    # ROLE MANAGEMENT
    # =========================================================================
    
    def create_custom_role(
        self,
        role_code: str,
        role_name: str,
        description: str,
        permissions: List[PermissionType],
        max_discount: Decimal
    ) -> Tuple[bool, str, Optional[RoleConfig]]:
        """Create a custom role"""
        if role_code in self.roles:
            return False, "Role code already exists", None
        
        role = RoleConfig(
            id=str(uuid.uuid4()),
            role_code=role_code,
            role_name=role_name,
            description=description,
            level=6,  # Custom roles at mid-level
            permissions=set(permissions),
            max_discount_percent=max_discount
        )
        
        self.roles[role_code] = role
        return True, f"Role {role_code} created", role
    
    def update_role_permissions(self, role_code: str, add: List[PermissionType] = None, remove: List[PermissionType] = None) -> Tuple[bool, str]:
        """Update role permissions"""
        role = self.roles.get(role_code)
        if not role:
            return False, "Role not found"
        
        if add:
            role.permissions.update(add)
        if remove:
            role.permissions -= set(remove)
        
        return True, f"Permissions updated for {role_code}"
    
    def get_role_permissions(self, role_code: str) -> Set[PermissionType]:
        """Get all permissions for a role"""
        role = self.roles.get(role_code)
        return role.permissions if role else set()
    
    # =========================================================================
    # DISCOUNT RULES
    # =========================================================================
    
    def create_discount_rule(self, rule_data: Dict) -> Tuple[bool, str, Optional[DiscountRule]]:
        """Create discount rule"""
        rule = DiscountRule(
            id=str(uuid.uuid4()),
            rule_name=rule_data.get("rule_name", "Discount Rule"),
            category_codes=rule_data.get("category_codes", []),
            store_ids=rule_data.get("store_ids", []),
            role_limits=rule_data.get("role_limits", {}),
            approval_required_above=Decimal(str(rule_data.get("approval_required_above", 0)))
        )
        
        self.discount_rules[rule.id] = rule
        return True, "Discount rule created", rule
    
    def get_effective_discount_limit(self, role_code: str, category_code: str, store_id: str) -> Decimal:
        """Get effective discount limit based on all rules"""
        role = self.roles.get(role_code)
        if not role:
            return Decimal("0")
        
        base_limit = role.max_discount_percent
        
        # Check category-specific rules
        category = self.categories.get(category_code)
        if category and category.luxury_flag:
            # Luxury items have stricter limits
            base_limit = min(base_limit, Decimal("5"))
        
        # Check discount rules
        for rule in self.discount_rules.values():
            if not rule.is_active:
                continue
            
            # Check if rule applies
            if rule.category_codes and category_code not in rule.category_codes:
                continue
            if rule.store_ids and store_id not in rule.store_ids:
                continue
            
            # Apply role-specific limit from rule
            if role_code in rule.role_limits:
                base_limit = min(base_limit, rule.role_limits[role_code])
        
        return base_limit
    
    # =========================================================================
    # FEATURE TOGGLES
    # =========================================================================
    
    def create_feature_toggle(
        self,
        feature_code: str,
        feature_name: str,
        description: str
    ) -> Tuple[bool, str, Optional[FeatureToggle]]:
        """Create feature toggle"""
        feature = FeatureToggle(
            id=str(uuid.uuid4()),
            feature_code=feature_code,
            feature_name=feature_name,
            description=description
        )
        
        self.features[feature_code] = feature
        return True, f"Feature {feature_code} created", feature
    
    def is_feature_enabled(self, feature_code: str, store_id: str = None, role_code: str = None) -> bool:
        """Check if feature is enabled for given context"""
        feature = self.features.get(feature_code)
        if not feature:
            return True  # Unknown features enabled by default
        
        if feature.global_enabled:
            return True
        
        if store_id and store_id in feature.enabled_stores:
            return True
        
        if role_code and role_code in feature.enabled_roles:
            return True
        
        return False
    
    def toggle_feature(self, feature_code: str, enabled: bool, scope: str = "global", scope_ids: List[str] = None) -> Tuple[bool, str]:
        """Toggle feature on/off"""
        feature = self.features.get(feature_code)
        if not feature:
            return False, "Feature not found"
        
        if scope == "global":
            feature.global_enabled = enabled
        elif scope == "stores":
            if enabled:
                feature.enabled_stores.extend(scope_ids or [])
            else:
                feature.enabled_stores = [s for s in feature.enabled_stores if s not in (scope_ids or [])]
        elif scope == "roles":
            if enabled:
                feature.enabled_roles.extend(scope_ids or [])
            else:
                feature.enabled_roles = [r for r in feature.enabled_roles if r not in (scope_ids or [])]
        
        return True, f"Feature {feature_code} updated"
    
    # =========================================================================
    # SYSTEM SETTINGS
    # =========================================================================
    
    def get_setting(self, key: str, default: Any = None) -> Any:
        """Get system setting"""
        return self.system_settings.get(key, default)
    
    def update_setting(self, key: str, value: Any, updated_by: str) -> Tuple[bool, str]:
        """Update system setting"""
        old_value = self.system_settings.get(key)
        self.system_settings[key] = value
        
        # Audit log would be created here
        return True, f"Setting {key} updated from {old_value} to {value}"
    
    def get_all_settings(self) -> Dict:
        """Get all system settings"""
        return self.system_settings.copy()
    
    # =========================================================================
    # EXPORT / SUMMARY
    # =========================================================================
    
    def get_settings_summary(self) -> Dict:
        """Get summary of all settings"""
        return {
            "stores": {
                "total": len(self.stores),
                "active": len([s for s in self.stores.values() if s.is_active]),
                "brands": list(set(s.brand for s in self.stores.values()))
            },
            "roles": {
                "total": len(self.roles),
                "active": len([r for r in self.roles.values() if r.is_active])
            },
            "categories": {
                "total": len(self.categories),
                "active": len([c for c in self.categories.values() if c.is_active])
            },
            "discount_rules": {
                "total": len(self.discount_rules),
                "active": len([d for d in self.discount_rules.values() if d.is_active])
            },
            "features": {
                "total": len(self.features),
                "globally_enabled": len([f for f in self.features.values() if f.global_enabled])
            }
        }


def demo_settings():
    print("=" * 60)
    print("IMS 2.0 SETTINGS ENGINE DEMO")
    print("=" * 60)
    
    engine = SettingsEngine()
    
    # Roles
    print("\n👥 Default Roles")
    for code, role in list(engine.roles.items())[:5]:
        print(f"  {role.role_name}: Level {role.level}, Discount: {role.max_discount_percent}%")
    
    # Categories
    print("\n📦 Product Categories")
    for code, cat in engine.categories.items():
        print(f"  {cat.category_name} [{cat.hsn_code}] - Luxury: {cat.luxury_flag}")
    
    # Create Store
    print("\n🏪 Create Store")
    success, msg, store = engine.create_store({
        "store_code": "BV-BKR",
        "store_name": "Better Vision - Bokaro",
        "brand": "Better Vision",
        "gstin": "20AABCU9603R1ZM",
        "state_code": "20",
        "address": "Main Road",
        "city": "Bokaro Steel City",
        "state": "Jharkhand",
        "pincode": "827001",
        "latitude": 23.6693,
        "longitude": 86.1511
    })
    print(f"  {msg}")
    
    # Feature Toggle
    print("\n🎛️ Feature Toggles")
    success, msg, feature = engine.create_feature_toggle(
        "AI_PURCHASE_ADVISOR",
        "AI Purchase Advisor",
        "AI recommendations for trade fair purchases"
    )
    print(f"  Created: {msg}")
    
    engine.toggle_feature("AI_PURCHASE_ADVISOR", True, "roles", ["SUPERADMIN"])
    print(f"  Enabled for: SUPERADMIN")
    
    print(f"  Is enabled for SUPERADMIN: {engine.is_feature_enabled('AI_PURCHASE_ADVISOR', role_code='SUPERADMIN')}")
    print(f"  Is enabled for SALES_STAFF: {engine.is_feature_enabled('AI_PURCHASE_ADVISOR', role_code='SALES_STAFF')}")
    
    # Discount Limits
    print("\n💰 Discount Limits")
    print(f"  Sales Staff on FRAME: {engine.get_effective_discount_limit('SALES_STAFF', 'FRAME', 'store-001')}%")
    print(f"  Store Manager on FRAME: {engine.get_effective_discount_limit('STORE_MANAGER', 'FRAME', 'store-001')}%")
    print(f"  Admin on FRAME: {engine.get_effective_discount_limit('ADMIN', 'FRAME', 'store-001')}%")
    
    # System Settings
    print("\n⚙️ System Settings")
    print(f"  Company: {engine.get_setting('company_name')}")
    print(f"  Default GST: {engine.get_setting('default_gst_rate')}%")
    print(f"  Rx Validity: {engine.get_setting('default_rx_validity_months')} months")
    print(f"  AI Superadmin Only: {engine.get_setting('ai_superadmin_only')}")
    
    # Summary
    print("\n📊 Settings Summary")
    summary = engine.get_settings_summary()
    print(f"  Stores: {summary['stores']['total']}")
    print(f"  Roles: {summary['roles']['total']}")
    print(f"  Categories: {summary['categories']['total']}")
    
    print("\n" + "=" * 60)


if __name__ == "__main__":
    demo_settings()

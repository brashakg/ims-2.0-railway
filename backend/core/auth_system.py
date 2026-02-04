"""
IMS 2.0 - Authentication & Role System
======================================
Features:
1. Multi-role per user (e.g., Neha = Store Manager + Optometrist + Sales)
2. Geo-location based login restriction
3. Store-specific role assignment
4. Role hierarchy for approvals
5. Session management with device tracking
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import List, Optional, Dict, Set
from math import radians, sin, cos, sqrt, atan2
import hashlib
import secrets
import uuid


class LoginResult(Enum):
    SUCCESS = "SUCCESS"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    ACCOUNT_LOCKED = "ACCOUNT_LOCKED"
    ACCOUNT_INACTIVE = "ACCOUNT_INACTIVE"
    OUTSIDE_GEO_RADIUS = "OUTSIDE_GEO_RADIUS"
    NO_STORE_ACCESS = "NO_STORE_ACCESS"
    SESSION_EXPIRED = "SESSION_EXPIRED"


@dataclass
class GeoLocation:
    latitude: float
    longitude: float
    
    def distance_to(self, other: 'GeoLocation') -> float:
        """Calculate distance between two points in meters using Haversine formula"""
        R = 6371000  # Earth's radius in meters
        
        lat1, lon1 = radians(self.latitude), radians(self.longitude)
        lat2, lon2 = radians(other.latitude), radians(other.longitude)
        
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        
        a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
        c = 2 * atan2(sqrt(a), sqrt(1-a))
        
        return R * c


@dataclass
class Store:
    id: str
    code: str
    name: str
    company_name: str  # 'Better Vision' or 'WizOpt'
    
    # Geo-location for login restriction
    location: Optional[GeoLocation] = None
    geo_radius_meters: int = 500  # Default 500m radius
    
    # Settings
    is_active: bool = True


@dataclass
class Role:
    id: str
    code: str
    name: str
    
    # Discount authority
    max_discount_percent: Decimal
    
    # Hierarchy (1 = highest like Superadmin, 10 = lowest)
    hierarchy_level: int
    
    # Permissions
    can_access_cash_drawer: bool = False
    can_change_prices: bool = False
    can_approve_discounts: bool = False
    can_transfer_stock: bool = False
    can_access_ai: bool = False
    can_view_all_stores: bool = False
    can_setup_store: bool = False
    can_manage_users: bool = False
    
    def can_approve_for(self, other_role: 'Role') -> bool:
        """Check if this role can approve requests from another role"""
        return self.hierarchy_level < other_role.hierarchy_level


@dataclass
class UserRole:
    """Represents a user's assignment to a role, optionally for a specific store"""
    role: Role
    store_id: Optional[str] = None  # None means role applies to all stores user has access to
    custom_discount_percent: Optional[Decimal] = None  # Override role's default if set
    assigned_at: datetime = field(default_factory=datetime.now)
    
    @property
    def effective_discount_percent(self) -> Decimal:
        return self.custom_discount_percent or self.role.max_discount_percent


@dataclass
class User:
    id: str
    employee_code: str
    first_name: str
    last_name: str
    username: str
    password_hash: str
    phone: str
    email: Optional[str] = None
    
    # Primary store
    primary_store_id: Optional[str] = None
    
    # Multi-role support - list of roles assigned to this user
    roles: List[UserRole] = field(default_factory=list)
    
    # Store access - which stores can this user access
    store_access: Set[str] = field(default_factory=set)
    
    # Status
    is_active: bool = True
    is_locked: bool = False
    lock_reason: Optional[str] = None
    
    # Employment
    date_of_joining: Optional[datetime] = None
    date_of_leaving: Optional[datetime] = None
    
    # Tracking
    last_login_at: Optional[datetime] = None
    last_login_store_id: Optional[str] = None
    last_login_location: Optional[GeoLocation] = None
    
    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()
    
    def has_role(self, role_code: str, store_id: Optional[str] = None) -> bool:
        """Check if user has a specific role, optionally for a specific store"""
        for user_role in self.roles:
            if user_role.role.code == role_code:
                if store_id:
                    if user_role.store_id is None or user_role.store_id == store_id:
                        return True
                else:
                    return True
        return False
    
    def get_roles_for_store(self, store_id: str) -> List[UserRole]:
        """Get all roles applicable for a specific store"""
        applicable_roles = []
        for user_role in self.roles:
            if user_role.store_id is None or user_role.store_id == store_id:
                applicable_roles.append(user_role)
        return applicable_roles
    
    def get_max_discount_for_store(self, store_id: str) -> Decimal:
        """Get the maximum discount this user can give at a specific store"""
        roles = self.get_roles_for_store(store_id)
        if not roles:
            return Decimal("0")
        return max(r.effective_discount_percent for r in roles)
    
    def get_highest_role(self) -> Optional[Role]:
        """Get the highest hierarchy role this user has"""
        if not self.roles:
            return None
        return min(self.roles, key=lambda r: r.role.hierarchy_level).role
    
    def can_access_store(self, store_id: str) -> bool:
        """Check if user can access a specific store"""
        if any(r.role.can_view_all_stores for r in self.roles):
            return True
        return store_id in self.store_access
    
    def has_permission(self, permission: str, store_id: Optional[str] = None) -> bool:
        """Check if user has a specific permission"""
        roles = self.roles if store_id is None else self.get_roles_for_store(store_id)
        
        permission_map = {
            'cash_drawer': 'can_access_cash_drawer',
            'change_prices': 'can_change_prices',
            'approve_discounts': 'can_approve_discounts',
            'transfer_stock': 'can_transfer_stock',
            'access_ai': 'can_access_ai',
            'view_all_stores': 'can_view_all_stores',
            'setup_store': 'can_setup_store',
            'manage_users': 'can_manage_users',
        }
        
        attr = permission_map.get(permission)
        if not attr:
            return False
            
        return any(getattr(r.role, attr, False) for r in roles)


@dataclass
class Session:
    id: str
    user_id: str
    store_id: str
    active_role_codes: List[str]
    token: str
    created_at: datetime = field(default_factory=datetime.now)
    expires_at: datetime = field(default_factory=lambda: datetime.now() + timedelta(hours=12))
    last_activity_at: datetime = field(default_factory=datetime.now)
    login_location: Optional[GeoLocation] = None
    device_type: Optional[str] = None
    device_info: Optional[str] = None
    ip_address: Optional[str] = None
    
    @property
    def is_expired(self) -> bool:
        return datetime.now() > self.expires_at
    
    def refresh(self, extend_hours: int = 12):
        self.expires_at = datetime.now() + timedelta(hours=extend_hours)
        self.last_activity_at = datetime.now()


@dataclass
class LoginResponse:
    result: LoginResult
    message: str
    session: Optional[Session] = None
    user: Optional[User] = None
    available_roles: List[Role] = field(default_factory=list)


class AuthenticationService:
    """Handles authentication, authorization, and session management."""
    
    def __init__(self):
        self.users: Dict[str, User] = {}
        self.stores: Dict[str, Store] = {}
        self.roles: Dict[str, Role] = {}
        self.sessions: Dict[str, Session] = {}
        self._initialize_default_roles()
    
    def _initialize_default_roles(self):
        """Initialize the default roles from your requirements"""
        default_roles = [
            Role(id="role-superadmin", code="SUPERADMIN", name="Superadmin (CEO)",
                 max_discount_percent=Decimal("100.00"), hierarchy_level=1,
                 can_access_cash_drawer=True, can_change_prices=True, can_approve_discounts=True,
                 can_transfer_stock=True, can_access_ai=True, can_view_all_stores=True,
                 can_setup_store=True, can_manage_users=True),
            Role(id="role-admin", code="ADMIN", name="Admin (Director)",
                 max_discount_percent=Decimal("100.00"), hierarchy_level=2,
                 can_access_cash_drawer=True, can_change_prices=True, can_approve_discounts=True,
                 can_transfer_stock=True, can_view_all_stores=True, can_setup_store=True, can_manage_users=True),
            Role(id="role-area-manager", code="AREA_MANAGER", name="Area Manager",
                 max_discount_percent=Decimal("25.00"), hierarchy_level=3,
                 can_access_cash_drawer=True, can_approve_discounts=True, can_transfer_stock=True, can_view_all_stores=True),
            Role(id="role-store-manager", code="STORE_MANAGER", name="Store Manager",
                 max_discount_percent=Decimal("20.00"), hierarchy_level=4,
                 can_access_cash_drawer=True, can_approve_discounts=True),
            Role(id="role-accountant", code="ACCOUNTANT", name="Accountant",
                 max_discount_percent=Decimal("0.00"), hierarchy_level=4,
                 can_change_prices=True, can_view_all_stores=True),
            Role(id="role-catalog-manager", code="CATALOG_MANAGER", name="Product Catalog Manager",
                 max_discount_percent=Decimal("0.00"), hierarchy_level=4,
                 can_change_prices=True, can_view_all_stores=True),
            Role(id="role-optometrist", code="OPTOMETRIST", name="Optometrist",
                 max_discount_percent=Decimal("0.00"), hierarchy_level=5),
            Role(id="role-sales-cashier", code="SALES_CASHIER", name="Sales Staff (Cashier)",
                 max_discount_percent=Decimal("10.00"), hierarchy_level=6, can_access_cash_drawer=True),
            Role(id="role-sales-staff", code="SALES_STAFF", name="Sales Staff",
                 max_discount_percent=Decimal("10.00"), hierarchy_level=6),
            Role(id="role-fitting-optical", code="FITTING_OPTICAL", name="Fitting Staff (Optical)",
                 max_discount_percent=Decimal("0.00"), hierarchy_level=7),
            Role(id="role-fitting-watch", code="FITTING_WATCH", name="Fitting Staff (Watch)",
                 max_discount_percent=Decimal("0.00"), hierarchy_level=7),
        ]
        for role in default_roles:
            self.roles[role.code] = role
    
    def hash_password(self, password: str) -> str:
        return hashlib.sha256(password.encode()).hexdigest()
    
    def verify_password(self, password: str, password_hash: str) -> bool:
        return self.hash_password(password) == password_hash
    
    def generate_token(self) -> str:
        return secrets.token_urlsafe(32)
    
    def register_store(self, store: Store):
        self.stores[store.id] = store
    
    def register_user(self, user: User):
        self.users[user.id] = user
    
    def assign_role_to_user(self, user_id: str, role_code: str, store_id: Optional[str] = None,
                           custom_discount: Optional[Decimal] = None) -> bool:
        user = self.users.get(user_id)
        role = self.roles.get(role_code)
        if not user or not role:
            return False
        for existing in user.roles:
            if existing.role.code == role_code and existing.store_id == store_id:
                return False
        user.roles.append(UserRole(role=role, store_id=store_id, custom_discount_percent=custom_discount))
        return True
    
    def grant_store_access(self, user_id: str, store_id: str) -> bool:
        user = self.users.get(user_id)
        if not user:
            return False
        user.store_access.add(store_id)
        return True
    
    def check_geo_location(self, store: Store, user_location: Optional[GeoLocation]) -> tuple:
        if not store.location:
            return True, "Store has no geo-restriction"
        if not user_location:
            return False, "Location required for login at this store"
        distance = store.location.distance_to(user_location)
        if distance <= store.geo_radius_meters:
            return True, f"Within {distance:.0f}m of store (allowed: {store.geo_radius_meters}m)"
        return False, f"Too far from store: {distance:.0f}m (allowed: {store.geo_radius_meters}m)"
    
    def login(self, username: str, password: str, store_id: str, location: Optional[GeoLocation] = None,
              device_type: Optional[str] = None, device_info: Optional[str] = None,
              ip_address: Optional[str] = None) -> LoginResponse:
        user = None
        for u in self.users.values():
            if u.username == username:
                user = u
                break
        
        if not user:
            return LoginResponse(result=LoginResult.INVALID_CREDENTIALS, message="Invalid username or password")
        if not self.verify_password(password, user.password_hash):
            return LoginResponse(result=LoginResult.INVALID_CREDENTIALS, message="Invalid username or password")
        if not user.is_active:
            return LoginResponse(result=LoginResult.ACCOUNT_INACTIVE, message="Account is inactive. Please contact admin.")
        if user.is_locked:
            return LoginResponse(result=LoginResult.ACCOUNT_LOCKED, message=f"Account is locked: {user.lock_reason or 'Contact admin'}")
        if not user.can_access_store(store_id):
            return LoginResponse(result=LoginResult.NO_STORE_ACCESS, message="You don't have access to this store")
        
        store = self.stores.get(store_id)
        if not store or not store.is_active:
            return LoginResponse(result=LoginResult.NO_STORE_ACCESS, message="Store not found or inactive")
        
        geo_ok, geo_message = self.check_geo_location(store, location)
        if not geo_ok:
            return LoginResponse(result=LoginResult.OUTSIDE_GEO_RADIUS, message=geo_message)
        
        applicable_roles = user.get_roles_for_store(store_id)
        if not applicable_roles:
            return LoginResponse(result=LoginResult.NO_STORE_ACCESS, message="No roles assigned for this store")
        
        session = Session(
            id=str(uuid.uuid4()), user_id=user.id, store_id=store_id,
            active_role_codes=[r.role.code for r in applicable_roles],
            token=self.generate_token(), login_location=location,
            device_type=device_type, device_info=device_info, ip_address=ip_address
        )
        self.sessions[session.token] = session
        
        user.last_login_at = datetime.now()
        user.last_login_store_id = store_id
        user.last_login_location = location
        
        return LoginResponse(
            result=LoginResult.SUCCESS,
            message=f"Welcome, {user.full_name}! Logged into {store.name}",
            session=session, user=user,
            available_roles=[r.role for r in applicable_roles]
        )
    
    def validate_session(self, token: str) -> Optional[Session]:
        session = self.sessions.get(token)
        if not session or session.is_expired:
            if session:
                del self.sessions[token]
            return None
        session.last_activity_at = datetime.now()
        return session
    
    def logout(self, token: str) -> bool:
        if token in self.sessions:
            del self.sessions[token]
            return True
        return False
    
    def can_user_approve(self, approver_user_id: str, requester_user_id: str, store_id: str) -> bool:
        approver = self.users.get(approver_user_id)
        requester = self.users.get(requester_user_id)
        if not approver or not requester:
            return False
        approver_roles = approver.get_roles_for_store(store_id)
        requester_roles = requester.get_roles_for_store(store_id)
        if not approver_roles or not requester_roles:
            return False
        approver_best = min(approver_roles, key=lambda r: r.role.hierarchy_level)
        requester_best = min(requester_roles, key=lambda r: r.role.hierarchy_level)
        return approver_best.role.can_approve_for(requester_best.role)


def demo_auth_system():
    """Demonstrate authentication system with real scenarios"""
    
    auth = AuthenticationService()
    
    print("=" * 70)
    print("IMS 2.0 AUTHENTICATION SYSTEM - DEMO")
    print("=" * 70)
    
    # Create stores
    store_bv_bokaro = Store(
        id="store-bv-001", code="BV-BKR", name="Better Vision - Bokaro",
        company_name="Better Vision",
        location=GeoLocation(latitude=23.6693, longitude=86.1511),
        geo_radius_meters=500
    )
    store_wizopt = Store(
        id="store-wo-001", code="WO-DEL", name="WizOpt - Delhi",
        company_name="WizOpt",
        location=GeoLocation(latitude=28.6139, longitude=77.2090),
        geo_radius_meters=500
    )
    auth.register_store(store_bv_bokaro)
    auth.register_store(store_wizopt)
    
    # Create Superadmin (CEO)
    superadmin = User(
        id="user-001", employee_code="EMP001", first_name="Brashak", last_name="G",
        username="brashak", password_hash=auth.hash_password("ceo123"),
        phone="9876543210", email="ceo@bettervision.in"
    )
    auth.register_user(superadmin)
    auth.assign_role_to_user(superadmin.id, "SUPERADMIN")
    
    # Create Neha - Multi-role user
    neha = User(
        id="user-002", employee_code="EMP002", first_name="Neha", last_name="Sharma",
        username="neha", password_hash=auth.hash_password("neha123"),
        phone="9876543211", primary_store_id="store-bv-001"
    )
    auth.register_user(neha)
    auth.assign_role_to_user(neha.id, "STORE_MANAGER", store_id="store-bv-001")
    auth.assign_role_to_user(neha.id, "OPTOMETRIST", store_id="store-bv-001")
    auth.assign_role_to_user(neha.id, "SALES_STAFF", store_id="store-bv-001")
    auth.grant_store_access(neha.id, "store-bv-001")
    
    # Create regular sales staff
    rahul = User(
        id="user-003", employee_code="EMP003", first_name="Rahul", last_name="Kumar",
        username="rahul", password_hash=auth.hash_password("rahul123"),
        phone="9876543212", primary_store_id="store-bv-001"
    )
    auth.register_user(rahul)
    auth.assign_role_to_user(rahul.id, "SALES_STAFF", store_id="store-bv-001")
    auth.grant_store_access(rahul.id, "store-bv-001")
    
    # SCENARIO 1: Superadmin Login
    print("\n🔐 SCENARIO 1: Superadmin Login (No geo restriction)")
    print("-" * 50)
    response = auth.login(username="brashak", password="ceo123", store_id="store-bv-001", device_type="desktop")
    print(f"Result: {response.result.value}")
    print(f"Message: {response.message}")
    if response.session:
        print(f"Active Roles: {response.session.active_role_codes}")
        print(f"Can Access AI: {response.user.has_permission('access_ai')}")
    
    # SCENARIO 2: Multi-role user login
    print("\n🔐 SCENARIO 2: Neha Login (Multi-role: Manager + Optom + Sales)")
    print("-" * 50)
    neha_location = GeoLocation(latitude=23.6695, longitude=86.1513)
    response = auth.login(username="neha", password="neha123", store_id="store-bv-001",
                         location=neha_location, device_type="ipad")
    print(f"Result: {response.result.value}")
    print(f"Message: {response.message}")
    if response.session:
        print(f"Active Roles: {response.session.active_role_codes}")
        print(f"Max Discount: {response.user.get_max_discount_for_store('store-bv-001')}%")
    
    # SCENARIO 3: Login blocked - outside geo radius
    print("\n🔐 SCENARIO 3: Rahul Login - Outside Store Radius (5km away)")
    print("-" * 50)
    rahul_far = GeoLocation(latitude=23.7100, longitude=86.2000)
    response = auth.login(username="rahul", password="rahul123", store_id="store-bv-001",
                         location=rahul_far, device_type="mobile")
    print(f"Result: {response.result.value}")
    print(f"Message: {response.message}")
    
    # SCENARIO 4: Login blocked - no store access
    print("\n🔐 SCENARIO 4: Rahul tries WizOpt (No Access)")
    print("-" * 50)
    response = auth.login(username="rahul", password="rahul123", store_id="store-wo-001")
    print(f"Result: {response.result.value}")
    print(f"Message: {response.message}")
    
    # SCENARIO 5: Successful login at store
    print("\n🔐 SCENARIO 5: Rahul Login - At Store Location")
    print("-" * 50)
    rahul_at_store = GeoLocation(latitude=23.6693, longitude=86.1512)
    response = auth.login(username="rahul", password="rahul123", store_id="store-bv-001",
                         location=rahul_at_store, device_type="ipad")
    print(f"Result: {response.result.value}")
    print(f"Message: {response.message}")
    
    # SCENARIO 6: Approval hierarchy
    print("\n🔐 SCENARIO 6: Approval Hierarchy")
    print("-" * 50)
    print(f"Can Neha approve Rahul? {auth.can_user_approve(neha.id, rahul.id, 'store-bv-001')}")
    print(f"Can Rahul approve Neha? {auth.can_user_approve(rahul.id, neha.id, 'store-bv-001')}")
    print(f"Can Superadmin approve anyone? {auth.can_user_approve(superadmin.id, neha.id, 'store-bv-001')}")
    
    print("\n" + "=" * 70)


if __name__ == "__main__":
    demo_auth_system()

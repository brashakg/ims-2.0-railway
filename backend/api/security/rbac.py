# ============================================================================
# IMS 2.0 - Advanced Role-Based Access Control (RBAC) System
# ============================================================================

from enum import Enum
from typing import List, Dict, Optional, Set
from dataclasses import dataclass
from functools import lru_cache

# ============================================================================
# Enums & Constants
# ============================================================================

class Permission(str, Enum):
    """Fine-grained permissions"""

    # Product Management
    PRODUCT_CREATE = "product:create"
    PRODUCT_READ = "product:read"
    PRODUCT_UPDATE = "product:update"
    PRODUCT_DELETE = "product:delete"
    PRODUCT_BULK_IMPORT = "product:bulk_import"

    # Customer Management
    CUSTOMER_CREATE = "customer:create"
    CUSTOMER_READ = "customer:read"
    CUSTOMER_UPDATE = "customer:update"
    CUSTOMER_DELETE = "customer:delete"
    CUSTOMER_EXPORT = "customer:export"

    # Order Management
    ORDER_CREATE = "order:create"
    ORDER_READ = "order:read"
    ORDER_UPDATE = "order:update"
    ORDER_CANCEL = "order:cancel"
    ORDER_PROCESS_PAYMENT = "order:process_payment"

    # Inventory Management
    INVENTORY_READ = "inventory:read"
    INVENTORY_UPDATE = "inventory:update"
    INVENTORY_TRANSFER = "inventory:transfer"
    INVENTORY_ADJUST = "inventory:adjust"

    # Financial
    FINANCIAL_READ = "financial:read"
    FINANCIAL_EXPORT = "financial:export"
    FINANCIAL_AUDIT = "financial:audit"

    # User Management
    USER_CREATE = "user:create"
    USER_READ = "user:read"
    USER_UPDATE = "user:update"
    USER_DELETE = "user:delete"
    USER_MANAGE_ROLES = "user:manage_roles"
    USER_MANAGE_PERMISSIONS = "user:manage_permissions"

    # System Administration
    SYSTEM_ADMIN = "system:admin"
    SYSTEM_BACKUP = "system:backup"
    SYSTEM_SETTINGS = "system:settings"
    SYSTEM_AUDIT_LOG = "system:audit_log"

class Role(str, Enum):
    """System roles"""
    ADMIN = "ADMIN"
    STORE_MANAGER = "STORE_MANAGER"
    INVENTORY_MANAGER = "INVENTORY_MANAGER"
    SALES_STAFF = "SALES_STAFF"
    CUSTOMER_SUPPORT = "CUSTOMER_SUPPORT"
    ACCOUNTANT = "ACCOUNTANT"
    READ_ONLY = "READ_ONLY"

# ============================================================================
# Role-Permission Mapping
# ============================================================================

ROLE_PERMISSIONS: Dict[Role, Set[Permission]] = {
    Role.ADMIN: {
        # All permissions
        Permission.PRODUCT_CREATE, Permission.PRODUCT_READ,
        Permission.PRODUCT_UPDATE, Permission.PRODUCT_DELETE,
        Permission.PRODUCT_BULK_IMPORT,

        Permission.CUSTOMER_CREATE, Permission.CUSTOMER_READ,
        Permission.CUSTOMER_UPDATE, Permission.CUSTOMER_DELETE,
        Permission.CUSTOMER_EXPORT,

        Permission.ORDER_CREATE, Permission.ORDER_READ,
        Permission.ORDER_UPDATE, Permission.ORDER_CANCEL,
        Permission.ORDER_PROCESS_PAYMENT,

        Permission.INVENTORY_READ, Permission.INVENTORY_UPDATE,
        Permission.INVENTORY_TRANSFER, Permission.INVENTORY_ADJUST,

        Permission.FINANCIAL_READ, Permission.FINANCIAL_EXPORT,
        Permission.FINANCIAL_AUDIT,

        Permission.USER_CREATE, Permission.USER_READ,
        Permission.USER_UPDATE, Permission.USER_DELETE,
        Permission.USER_MANAGE_ROLES, Permission.USER_MANAGE_PERMISSIONS,

        Permission.SYSTEM_ADMIN, Permission.SYSTEM_BACKUP,
        Permission.SYSTEM_SETTINGS, Permission.SYSTEM_AUDIT_LOG,
    },

    Role.STORE_MANAGER: {
        Permission.PRODUCT_READ, Permission.PRODUCT_UPDATE,
        Permission.CUSTOMER_READ, Permission.CUSTOMER_UPDATE,
        Permission.ORDER_READ, Permission.ORDER_UPDATE,
        Permission.INVENTORY_READ, Permission.INVENTORY_UPDATE,
        Permission.INVENTORY_TRANSFER,
        Permission.FINANCIAL_READ, Permission.FINANCIAL_EXPORT,
        Permission.USER_READ,
    },

    Role.INVENTORY_MANAGER: {
        Permission.PRODUCT_READ, Permission.PRODUCT_UPDATE,
        Permission.INVENTORY_READ, Permission.INVENTORY_UPDATE,
        Permission.INVENTORY_TRANSFER, Permission.INVENTORY_ADJUST,
        Permission.ORDER_READ,
    },

    Role.SALES_STAFF: {
        Permission.PRODUCT_READ,
        Permission.CUSTOMER_CREATE, Permission.CUSTOMER_READ,
        Permission.CUSTOMER_UPDATE,
        Permission.ORDER_CREATE, Permission.ORDER_READ,
        Permission.ORDER_UPDATE, Permission.ORDER_PROCESS_PAYMENT,
        Permission.INVENTORY_READ,
    },

    Role.CUSTOMER_SUPPORT: {
        Permission.CUSTOMER_READ, Permission.CUSTOMER_UPDATE,
        Permission.ORDER_READ,
        Permission.FINANCIAL_READ,
    },

    Role.ACCOUNTANT: {
        Permission.PRODUCT_READ,
        Permission.CUSTOMER_READ,
        Permission.ORDER_READ,
        Permission.FINANCIAL_READ, Permission.FINANCIAL_EXPORT,
        Permission.FINANCIAL_AUDIT,
    },

    Role.READ_ONLY: {
        Permission.PRODUCT_READ,
        Permission.CUSTOMER_READ,
        Permission.ORDER_READ,
        Permission.INVENTORY_READ,
        Permission.FINANCIAL_READ,
    }
}

# ============================================================================
# RBAC Service
# ============================================================================

class RBACService:
    """Advanced RBAC service with permission caching"""

    @staticmethod
    @lru_cache(maxsize=1024)
    def has_permission(user_roles: tuple, required_permission: str) -> bool:
        """Check if user with given roles has required permission"""

        required_perm = Permission(required_permission)

        for role_str in user_roles:
            role = Role(role_str)
            if required_perm in ROLE_PERMISSIONS[role]:
                return True

        return False

    @staticmethod
    @lru_cache(maxsize=1024)
    def get_user_permissions(user_roles: tuple) -> Set[str]:
        """Get all permissions for user's roles"""

        permissions = set()
        for role_str in user_roles:
            role = Role(role_str)
            permissions.update(ROLE_PERMISSIONS[role])

        return permissions

    @staticmethod
    def has_any_permission(user_roles: tuple, permissions: List[str]) -> bool:
        """Check if user has any of the given permissions"""

        user_permissions = RBACService.get_user_permissions(user_roles)
        return any(perm in user_permissions for perm in permissions)

    @staticmethod
    def has_all_permissions(user_roles: tuple, permissions: List[str]) -> bool:
        """Check if user has all of the given permissions"""

        user_permissions = RBACService.get_user_permissions(user_roles)
        return all(perm in user_permissions for perm in permissions)

# ============================================================================
# Resource-Level Access Control
# ============================================================================

class ResourceAccessControl:
    """Handle resource-level access control decisions"""

    @staticmethod
    async def can_access_customer(user_id: str, customer_id: str, action: str) -> bool:
        """Check if user can access specific customer"""

        user = await get_user(user_id)

        # Admins can access anything
        if Role.ADMIN in user.roles:
            return True

        # Store managers can access customers in their store
        if Role.STORE_MANAGER in user.roles:
            customer = await get_customer(customer_id)
            return customer.store_id == user.store_id

        # Other checks...
        return False

    @staticmethod
    async def can_access_order(user_id: str, order_id: str, action: str) -> bool:
        """Check if user can access specific order"""

        user = await get_user(user_id)
        order = await get_order(order_id)

        # Admins can access anything
        if Role.ADMIN in user.roles:
            return True

        # Store managers can see orders from their store
        if Role.STORE_MANAGER in user.roles:
            return order.store_id == user.store_id

        # Sales staff can only see orders they created
        if Role.SALES_STAFF in user.roles and action in ["read", "update"]:
            return order.created_by == user_id

        return False

# ============================================================================
# FastAPI Dependency for Permission Checking
# ============================================================================

from fastapi import Depends, HTTPException, status

async def require_permission(
    required_permission: str,
    current_user = Depends(get_current_user)
):
    """Dependency to enforce permission check"""

    if not RBACService.has_permission(
        tuple(current_user.roles),
        required_permission
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Permission denied: {required_permission} required"
        )

    return current_user

async def require_any_permission(
    permissions: List[str],
    current_user = Depends(get_current_user)
):
    """Dependency to enforce any of multiple permissions"""

    if not RBACService.has_any_permission(
        tuple(current_user.roles),
        permissions
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions"
        )

    return current_user

# ============================================================================
# Usage Examples
# ============================================================================

"""
# In route handlers:

@router.post("/customers")
async def create_customer(
    request: CustomerCreate,
    current_user = Depends(require_permission(Permission.CUSTOMER_CREATE))
):
    # Only users with CUSTOMER_CREATE permission can execute
    ...

@router.delete("/users/{user_id}")
async def delete_user(
    user_id: str,
    current_user = Depends(require_permission(Permission.USER_DELETE))
):
    # Only admins have this permission
    ...

@router.get("/orders/{order_id}")
async def get_order(
    order_id: str,
    current_user = Depends(get_current_user)
):
    # Check resource-level access
    if not await ResourceAccessControl.can_access_order(
        current_user.id,
        order_id,
        "read"
    ):
        raise HTTPException(status_code=403)
    ...
"""

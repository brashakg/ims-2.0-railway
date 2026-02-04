"""
IMS 2.0 - Users Router
=======================
User management endpoints
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field, EmailStr
from typing import List, Optional
from datetime import datetime

from .auth import get_current_user

router = APIRouter()


# ============================================================================
# SCHEMAS
# ============================================================================

class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=8)
    full_name: str = Field(..., min_length=2)
    phone: Optional[str] = None
    roles: List[str] = Field(default=["SALES_STAFF"])
    store_ids: List[str] = Field(default=[])
    primary_store_id: Optional[str] = None
    discount_cap: float = Field(default=10.0, ge=0, le=100)

class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    roles: Optional[List[str]] = None
    store_ids: Optional[List[str]] = None
    primary_store_id: Optional[str] = None
    discount_cap: Optional[float] = Field(default=None, ge=0, le=100)
    is_active: Optional[bool] = None

class UserResponse(BaseModel):
    user_id: str
    username: str
    email: str
    full_name: str
    phone: Optional[str]
    roles: List[str]
    store_ids: List[str]
    primary_store_id: Optional[str]
    discount_cap: float
    is_active: bool
    created_at: datetime
    last_login: Optional[datetime]


# ============================================================================
# ROLE CHECKS
# ============================================================================

def require_admin(current_user: dict = Depends(get_current_user)):
    """Require ADMIN or SUPERADMIN role"""
    if not any(r in ["ADMIN", "SUPERADMIN"] for r in current_user.get("roles", [])):
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user

def require_manager(current_user: dict = Depends(get_current_user)):
    """Require STORE_MANAGER or higher"""
    allowed = ["STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"]
    if not any(r in allowed for r in current_user.get("roles", [])):
        raise HTTPException(status_code=403, detail="Manager access required")
    return current_user


# ============================================================================
# ENDPOINTS
# ============================================================================

@router.get("/", response_model=List[dict])
async def list_users(
    store_id: Optional[str] = Query(None),
    role: Optional[str] = Query(None),
    active_only: bool = Query(True),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(require_manager)
):
    """
    List users with filters
    """
    # TODO: Implement with database
    return []


@router.post("/", response_model=dict, status_code=201)
async def create_user(
    user: UserCreate,
    current_user: dict = Depends(require_admin)
):
    """
    Create new user (Admin only)
    """
    # TODO: Implement with database
    return {
        "user_id": "new-user-id",
        "username": user.username,
        "message": "User created successfully"
    }


@router.get("/{user_id}", response_model=dict)
async def get_user(
    user_id: str,
    current_user: dict = Depends(require_manager)
):
    """
    Get user by ID
    """
    # TODO: Implement with database
    return {"user_id": user_id}


@router.put("/{user_id}", response_model=dict)
async def update_user(
    user_id: str,
    user: UserUpdate,
    current_user: dict = Depends(require_admin)
):
    """
    Update user (Admin only)
    """
    # TODO: Implement with database
    return {"user_id": user_id, "message": "User updated successfully"}


@router.delete("/{user_id}")
async def delete_user(
    user_id: str,
    current_user: dict = Depends(require_admin)
):
    """
    Deactivate user (soft delete)
    """
    # TODO: Implement with database
    return {"message": "User deactivated"}


@router.post("/{user_id}/roles/{role}")
async def add_role(
    user_id: str,
    role: str,
    current_user: dict = Depends(require_admin)
):
    """
    Add role to user
    """
    return {"message": f"Role {role} added to user"}


@router.delete("/{user_id}/roles/{role}")
async def remove_role(
    user_id: str,
    role: str,
    current_user: dict = Depends(require_admin)
):
    """
    Remove role from user
    """
    return {"message": f"Role {role} removed from user"}


@router.post("/{user_id}/stores/{store_id}")
async def add_store_access(
    user_id: str,
    store_id: str,
    current_user: dict = Depends(require_admin)
):
    """
    Add store access to user
    """
    return {"message": f"Store {store_id} access granted"}


@router.delete("/{user_id}/stores/{store_id}")
async def remove_store_access(
    user_id: str,
    store_id: str,
    current_user: dict = Depends(require_admin)
):
    """
    Remove store access from user
    """
    return {"message": f"Store {store_id} access revoked"}


@router.get("/store/{store_id}", response_model=List[dict])
async def get_store_users(
    store_id: str,
    role: Optional[str] = Query(None),
    current_user: dict = Depends(require_manager)
):
    """
    Get users for a specific store
    """
    return []


@router.get("/role/{role}", response_model=List[dict])
async def get_users_by_role(
    role: str,
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_manager)
):
    """
    Get users by role
    """
    return []

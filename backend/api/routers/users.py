"""
IMS 2.0 - Users Router
=======================
User management endpoints
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field, EmailStr
from typing import List, Optional
from datetime import datetime
import uuid
import hashlib

from .auth import get_current_user
from ..dependencies import get_user_repository

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
# HELPER FUNCTIONS
# ============================================================================

def hash_password(password: str) -> str:
    """Hash password using SHA-256"""
    return hashlib.sha256(password.encode()).hexdigest()


def sanitize_user(user: dict) -> dict:
    """Remove sensitive fields from user response"""
    if user:
        user.pop("password_hash", None)
        user.pop("password", None)
    return user


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

# NOTE: Specific routes MUST come before /{user_id} to avoid being matched as user_id
@router.get("/store/{store_id}", response_model=List[dict])
async def get_store_users(
    store_id: str,
    role: Optional[str] = Query(None),
    current_user: dict = Depends(require_manager)
):
    """Get users for a specific store"""
    repo = get_user_repository()

    if repo:
        if role:
            users = repo.find_by_role(role, store_id)
        else:
            users = repo.find_by_store(store_id)
        return [sanitize_user(u) for u in users]

    return []


@router.get("/role/{role}", response_model=List[dict])
async def get_users_by_role(
    role: str,
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_manager)
):
    """Get users by role"""
    repo = get_user_repository()

    if repo:
        users = repo.find_by_role(role, store_id)
        return [sanitize_user(u) for u in users]

    return []


@router.get("/search")
async def search_users(
    q: str = Query(..., min_length=2),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_manager)
):
    """Search users by name, username, or email"""
    repo = get_user_repository()

    if repo:
        users = repo.search_users(q, store_id)
        return {"users": [sanitize_user(u) for u in users]}

    return {"users": []}


@router.get("/summary")
async def get_user_summary(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_manager)
):
    """Get user count summary by role"""
    repo = get_user_repository()

    if repo:
        summary = repo.get_user_summary(store_id)
        return {"summary": summary}

    return {"summary": {}}


@router.get("/", response_model=List[dict])
async def list_users(
    store_id: Optional[str] = Query(None),
    role: Optional[str] = Query(None),
    active_only: bool = Query(True),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(require_manager)
):
    """List users with filters"""
    repo = get_user_repository()

    if repo:
        filter_dict = {}
        if store_id:
            filter_dict["store_ids"] = store_id
        if role:
            filter_dict["roles"] = role
        if active_only:
            filter_dict["is_active"] = True

        users = repo.find_many(filter_dict, skip=skip, limit=limit)
        return [sanitize_user(u) for u in users]

    return []


@router.post("/", response_model=dict, status_code=201)
async def create_user(
    user: UserCreate,
    current_user: dict = Depends(require_admin)
):
    """Create new user (Admin only)"""
    repo = get_user_repository()

    if repo:
        # Check if username exists
        if repo.find_by_username(user.username):
            raise HTTPException(status_code=400, detail="Username already exists")

        # Check if email exists
        if repo.find_by_email(user.email):
            raise HTTPException(status_code=400, detail="Email already exists")

        user_data = {
            "username": user.username,
            "email": user.email,
            "password_hash": hash_password(user.password),
            "full_name": user.full_name,
            "phone": user.phone,
            "roles": user.roles,
            "store_ids": user.store_ids,
            "primary_store_id": user.primary_store_id or (user.store_ids[0] if user.store_ids else None),
            "discount_cap": user.discount_cap,
            "is_active": True,
            "created_by": current_user.get("user_id")
        }

        created = repo.create(user_data)
        if created:
            return {
                "user_id": created["user_id"],
                "username": created["username"],
                "message": "User created successfully"
            }

        raise HTTPException(status_code=500, detail="Failed to create user")

    return {
        "user_id": str(uuid.uuid4()),
        "username": user.username,
        "message": "User created successfully"
    }


@router.get("/{user_id}", response_model=dict)
async def get_user(
    user_id: str,
    current_user: dict = Depends(require_manager)
):
    """Get user by ID"""
    repo = get_user_repository()

    if repo:
        user = repo.find_by_id(user_id)
        if user:
            return sanitize_user(user)
        raise HTTPException(status_code=404, detail="User not found")

    return {"user_id": user_id}


@router.put("/{user_id}", response_model=dict)
async def update_user(
    user_id: str,
    user: UserUpdate,
    current_user: dict = Depends(require_admin)
):
    """Update user (Admin only)"""
    repo = get_user_repository()

    if repo:
        existing = repo.find_by_id(user_id)
        if not existing:
            raise HTTPException(status_code=404, detail="User not found")

        update_data = user.model_dump(exclude_unset=True)
        update_data["updated_by"] = current_user.get("user_id")

        if repo.update(user_id, update_data):
            return {"user_id": user_id, "message": "User updated successfully"}

        raise HTTPException(status_code=500, detail="Failed to update user")

    return {"user_id": user_id, "message": "User updated successfully"}


@router.delete("/{user_id}")
async def delete_user(
    user_id: str,
    current_user: dict = Depends(require_admin)
):
    """Deactivate user (soft delete)"""
    repo = get_user_repository()

    if repo:
        existing = repo.find_by_id(user_id)
        if not existing:
            raise HTTPException(status_code=404, detail="User not found")

        # Prevent deactivating yourself
        if user_id == current_user.get("user_id"):
            raise HTTPException(status_code=400, detail="Cannot deactivate yourself")

        if repo.update(user_id, {"is_active": False, "deactivated_by": current_user.get("user_id")}):
            return {"message": "User deactivated"}

        raise HTTPException(status_code=500, detail="Failed to deactivate user")

    return {"message": "User deactivated"}


@router.post("/{user_id}/roles/{role}")
async def add_role(
    user_id: str,
    role: str,
    current_user: dict = Depends(require_admin)
):
    """Add role to user"""
    repo = get_user_repository()

    if repo:
        existing = repo.find_by_id(user_id)
        if not existing:
            raise HTTPException(status_code=404, detail="User not found")

        if repo.add_role(user_id, role):
            return {"message": f"Role {role} added to user"}

        raise HTTPException(status_code=500, detail="Failed to add role")

    return {"message": f"Role {role} added to user"}


@router.delete("/{user_id}/roles/{role}")
async def remove_role(
    user_id: str,
    role: str,
    current_user: dict = Depends(require_admin)
):
    """Remove role from user"""
    repo = get_user_repository()

    if repo:
        existing = repo.find_by_id(user_id)
        if not existing:
            raise HTTPException(status_code=404, detail="User not found")

        if repo.remove_role(user_id, role):
            return {"message": f"Role {role} removed from user"}

        raise HTTPException(status_code=500, detail="Failed to remove role")

    return {"message": f"Role {role} removed from user"}


@router.post("/{user_id}/stores/{store_id}")
async def add_store_access(
    user_id: str,
    store_id: str,
    current_user: dict = Depends(require_admin)
):
    """Add store access to user"""
    repo = get_user_repository()

    if repo:
        existing = repo.find_by_id(user_id)
        if not existing:
            raise HTTPException(status_code=404, detail="User not found")

        if repo.add_store(user_id, store_id):
            return {"message": f"Store {store_id} access granted"}

        raise HTTPException(status_code=500, detail="Failed to add store access")

    return {"message": f"Store {store_id} access granted"}


@router.delete("/{user_id}/stores/{store_id}")
async def remove_store_access(
    user_id: str,
    store_id: str,
    current_user: dict = Depends(require_admin)
):
    """Remove store access from user"""
    repo = get_user_repository()

    if repo:
        existing = repo.find_by_id(user_id)
        if not existing:
            raise HTTPException(status_code=404, detail="User not found")

        if repo.remove_store(user_id, store_id):
            return {"message": f"Store {store_id} access revoked"}

        raise HTTPException(status_code=500, detail="Failed to remove store access")

    return {"message": f"Store {store_id} access revoked"}

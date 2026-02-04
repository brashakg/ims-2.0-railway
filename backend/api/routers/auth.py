"""
IMS 2.0 - Authentication Router
================================
Login, logout, token management
"""
from fastapi import APIRouter, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime, timedelta
import jwt
import hashlib
import os

router = APIRouter()
security = HTTPBearer()

# JWT Configuration
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "ims-2.0-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 480  # 8 hours


# ============================================================================
# SCHEMAS
# ============================================================================

class LoginRequest(BaseModel):
    username: str = Field(..., min_length=3)
    password: str = Field(..., min_length=6)
    store_id: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: dict

class TokenData(BaseModel):
    user_id: str
    username: str
    roles: List[str]
    store_ids: List[str]
    active_store_id: Optional[str]
    exp: datetime

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8)

class RefreshTokenRequest(BaseModel):
    token: str


# ============================================================================
# HELPERS
# ============================================================================

def hash_password(password: str) -> str:
    """Hash password using SHA-256"""
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password against hash"""
    return hash_password(plain_password) == hashed_password

def create_access_token(data: dict, expires_delta: timedelta = None) -> str:
    """Create JWT access token"""
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def decode_token(token: str) -> dict:
    """Decode and validate JWT token"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """Get current user from JWT token"""
    token = credentials.credentials
    payload = decode_token(token)
    return payload


# ============================================================================
# ENDPOINTS
# ============================================================================

@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    """
    Authenticate user and return JWT token
    """
    # TODO: Replace with actual database lookup
    # Mock user for demonstration
    mock_users = {
        "admin": {
            "user_id": "user-001",
            "username": "admin",
            "password_hash": hash_password("admin123"),
            "full_name": "Admin User",
            "roles": ["ADMIN", "SUPERADMIN"],
            "store_ids": ["store-hq"],
            "is_active": True
        },
        "manager": {
            "user_id": "user-002",
            "username": "manager",
            "password_hash": hash_password("manager123"),
            "full_name": "Store Manager",
            "roles": ["STORE_MANAGER"],
            "store_ids": ["store-001"],
            "is_active": True
        },
        "staff": {
            "user_id": "user-003",
            "username": "staff",
            "password_hash": hash_password("staff123"),
            "full_name": "Sales Staff",
            "roles": ["SALES_STAFF"],
            "store_ids": ["store-001"],
            "is_active": True
        }
    }
    
    user = mock_users.get(request.username)
    
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    
    if not verify_password(request.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    
    if not user["is_active"]:
        raise HTTPException(status_code=403, detail="User account is disabled")
    
    # Validate store access if store_id provided
    active_store = request.store_id
    if active_store and active_store not in user["store_ids"]:
        # Allow ADMIN/SUPERADMIN to access any store
        if not any(r in ["ADMIN", "SUPERADMIN"] for r in user["roles"]):
            raise HTTPException(status_code=403, detail="No access to this store")
    
    # Create token
    token_data = {
        "user_id": user["user_id"],
        "username": user["username"],
        "roles": user["roles"],
        "store_ids": user["store_ids"],
        "active_store_id": active_store or user["store_ids"][0] if user["store_ids"] else None
    }
    
    access_token = create_access_token(token_data)
    
    return LoginResponse(
        access_token=access_token,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user={
            "user_id": user["user_id"],
            "username": user["username"],
            "full_name": user["full_name"],
            "roles": user["roles"],
            "store_ids": user["store_ids"],
            "active_store_id": token_data["active_store_id"]
        }
    )


@router.post("/logout")
async def logout(current_user: dict = Depends(get_current_user)):
    """
    Logout user (invalidate token)
    """
    # In production, add token to blacklist
    return {"message": "Successfully logged out"}


@router.get("/me")
async def get_me(current_user: dict = Depends(get_current_user)):
    """
    Get current user info from token
    """
    return current_user


@router.post("/refresh")
async def refresh_token(request: RefreshTokenRequest):
    """
    Refresh access token
    """
    payload = decode_token(request.token)
    
    # Create new token
    token_data = {
        "user_id": payload["user_id"],
        "username": payload["username"],
        "roles": payload["roles"],
        "store_ids": payload["store_ids"],
        "active_store_id": payload.get("active_store_id")
    }
    
    new_token = create_access_token(token_data)
    
    return {
        "access_token": new_token,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60
    }


@router.post("/change-password")
async def change_password(
    request: ChangePasswordRequest, 
    current_user: dict = Depends(get_current_user)
):
    """
    Change user password
    """
    # TODO: Verify current password and update in database
    return {"message": "Password changed successfully"}


@router.post("/switch-store/{store_id}")
async def switch_store(store_id: str, current_user: dict = Depends(get_current_user)):
    """
    Switch active store context
    """
    if store_id not in current_user["store_ids"]:
        if not any(r in ["ADMIN", "SUPERADMIN"] for r in current_user["roles"]):
            raise HTTPException(status_code=403, detail="No access to this store")
    
    # Create new token with updated store
    token_data = {
        "user_id": current_user["user_id"],
        "username": current_user["username"],
        "roles": current_user["roles"],
        "store_ids": current_user["store_ids"],
        "active_store_id": store_id
    }
    
    new_token = create_access_token(token_data)
    
    return {
        "access_token": new_token,
        "active_store_id": store_id
    }

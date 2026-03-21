"""
IMS 2.0 - Authentication Router
================================
Login, logout, token management
"""

from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime, timedelta
import jwt
import hashlib
import os
import logging

logger = logging.getLogger(__name__)

router = APIRouter()
# HTTPBearer with auto_error=False allows us to handle missing credentials gracefully
security = HTTPBearer(auto_error=False)

# JWT Configuration
_default_secret = os.getenv("JWT_SECRET_KEY", "")
if not _default_secret:
    logger.warning("JWT_SECRET_KEY not set — generating ephemeral secret. Set JWT_SECRET_KEY env var for production!")
    import secrets as _sec
    _default_secret = _sec.token_hex(32)
SECRET_KEY = _default_secret
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
    """Hash password using bcrypt directly"""
    import bcrypt as _bc
    return _bc.hashpw(password.encode(), _bc.gensalt(rounds=12)).decode()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password against bcrypt hash (with SHA-256 fallback for legacy)"""
    # Try bcrypt first (seed data and new users)
    if hashed_password.startswith("$2b$") or hashed_password.startswith("$2a$"):
        import bcrypt as _bc
        try:
            return _bc.checkpw(plain_password.encode(), hashed_password.encode())
        except Exception:
            return False
    # Fallback to SHA-256 for any legacy hashes
    return hashlib.sha256(plain_password.encode()).hexdigest() == hashed_password


def create_access_token(data: dict, expires_delta: timedelta = None) -> str:
    """Create JWT access token"""
    to_encode = data.copy()
    expire = datetime.utcnow() + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
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


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> dict:
    """Get current user from JWT token"""
    try:
        if not credentials:
            raise HTTPException(
                status_code=401,
                detail="Not authenticated",
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = credentials.credentials
        if not token:
            raise HTTPException(
                status_code=401,
                detail="No token provided",
                headers={"WWW-Authenticate": "Bearer"},
            )

        payload = decode_token(token)
        return payload
    except HTTPException:
        # Re-raise HTTPException as-is
        raise
    except Exception as e:
        logger.error(f"Authentication error: {str(e)}")
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ============================================================================
# ENDPOINTS
# ============================================================================


@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    """
    Authenticate user and return JWT token.
    Accepts either username or email in the username field.
    """
    # Look up user from MongoDB (real database)
    from ..dependencies import get_user_repository
    user_repo = get_user_repository()
    
    login_input = request.username.lower().strip()
    user = None
    
    if user_repo:
        # Try database lookup first
        try:
            # Search by username
            db_user = user_repo.collection.find_one({"username": login_input})
            if not db_user:
                # Search by email
                db_user = user_repo.collection.find_one({"email": login_input})
            if db_user:
                user = db_user
        except Exception as e:
            logger.warning("DB user lookup error: %s", e)
    
    # Emergency access via environment variable only (no hardcoded credentials)
    if user is None and os.getenv("EMERGENCY_ADMIN_HASH"):
        import bcrypt as _bc
        emergency_hash = os.getenv("EMERGENCY_ADMIN_HASH")
        if login_input == "admin":
            try:
                if _bc.checkpw(request.password.encode(), emergency_hash.encode()):
                    user = {
                        "user_id": "user-emergency-admin",
                        "username": "admin",
                        "email": "admin@bettervision.in",
                        "password_hash": emergency_hash,
                        "full_name": "Emergency Admin",
                        "roles": ["SUPERADMIN"],
                        "store_ids": ["BV-BOK-01", "BV-BOK-02", "BV-DHN-01", "BV-DHN-02", "WO-DHN-01", "BV-PUN-01"],
                        "is_active": True,
                    }
            except Exception:
                pass

    if user is None:
        raise HTTPException(status_code=401, detail="Invalid username or password")

    if not verify_password(request.password, user.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    if not user.get("is_active", False):
        raise HTTPException(status_code=403, detail="User account is disabled")

    # Validate store access if store_id provided
    user_store_ids = user.get("store_ids", [])
    active_store = request.store_id
    if active_store and active_store not in user_store_ids:
        if not any(r in ["ADMIN", "SUPERADMIN"] for r in user.get("roles", [])):
            raise HTTPException(status_code=403, detail="No access to this store")

    # Create token
    token_data = {
        "user_id": user.get("user_id", user.get("_id", "")),
        "username": user.get("username", ""),
        "roles": user.get("roles", []),
        "store_ids": user_store_ids,
        "active_store_id": (
            active_store or (user_store_ids[0] if user_store_ids else None)
        ),
    }

    access_token = create_access_token(token_data)

    return LoginResponse(
        access_token=access_token,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user={
            "user_id": token_data["user_id"],
            "username": user.get("username", ""),
            "full_name": user.get("full_name", ""),
            "roles": user.get("roles", []),
            "store_ids": user_store_ids,
            "active_store_id": token_data["active_store_id"],
        },
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
        "active_store_id": payload.get("active_store_id"),
    }

    new_token = create_access_token(token_data)

    return {
        "access_token": new_token,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    }


@router.post("/change-password")
async def change_password(
    request: ChangePasswordRequest, current_user: dict = Depends(get_current_user)
):
    """
    Change user password
    """
    from ..dependencies import get_user_repository
    user_repo = get_user_repository()

    if user_repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    # Get user from DB
    user = user_repo.collection.find_one({"user_id": current_user.get("user_id")})
    if not user:
        # Also try username lookup
        user = user_repo.collection.find_one({"username": current_user.get("username")})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Verify current password
    if not verify_password(request.current_password, user.get("password_hash", "")):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    # Hash new password and update
    new_hash = hash_password(request.new_password)
    user_repo.collection.update_one(
        {"_id": user["_id"]},
        {"$set": {"password_hash": new_hash, "updated_at": datetime.utcnow().isoformat()}}
    )

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
        "active_store_id": store_id,
    }

    new_token = create_access_token(token_data)

    return {"access_token": new_token, "active_store_id": store_id}

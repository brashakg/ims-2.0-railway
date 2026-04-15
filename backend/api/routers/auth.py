"""
IMS 2.0 - Authentication Router
================================
Login, logout, token management
"""

from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Tuple
from datetime import datetime, timedelta
from collections import defaultdict
import time
import jwt
import hashlib
import os
import math
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


def _haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance in meters between two lat/lng points using Haversine formula."""
    R = 6_371_000  # Earth radius in meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
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
# RATE LIMITER — Brute-force protection for login
# ============================================================================
# Per-IP: 5 failed attempts in 15 minutes → 15 minute lockout
# Per-username: 10 failed attempts in 30 minutes → 30 minute lockout

class LoginRateLimiter:
    """In-memory sliding-window rate limiter for login attempts."""

    def __init__(self):
        # { key: [(timestamp, success)] }
        self._attempts: Dict[str, list] = defaultdict(list)
        self._lockouts: Dict[str, float] = {}  # key → lockout_until timestamp

    def _cleanup(self, key: str, window_seconds: int):
        cutoff = time.time() - window_seconds
        self._attempts[key] = [
            (ts, ok) for ts, ok in self._attempts[key] if ts > cutoff
        ]

    def check(self, ip: str, username: str) -> Optional[str]:
        """Returns error message if rate-limited, None if OK."""
        now = time.time()

        # Check IP lockout
        ip_key = f"ip:{ip}"
        if ip_key in self._lockouts and now < self._lockouts[ip_key]:
            remaining = int(self._lockouts[ip_key] - now)
            return f"Too many login attempts. Try again in {remaining // 60 + 1} minutes."

        # Check username lockout
        user_key = f"user:{username.lower().strip()}"
        if user_key in self._lockouts and now < self._lockouts[user_key]:
            remaining = int(self._lockouts[user_key] - now)
            return f"Account temporarily locked. Try again in {remaining // 60 + 1} minutes."

        # Check IP failures (5 in 15 min)
        self._cleanup(ip_key, 900)
        ip_failures = sum(1 for _, ok in self._attempts[ip_key] if not ok)
        if ip_failures >= 5:
            self._lockouts[ip_key] = now + 900  # 15 min lockout
            return "Too many login attempts from this location. Try again in 15 minutes."

        # Check username failures (10 in 30 min)
        self._cleanup(user_key, 1800)
        user_failures = sum(1 for _, ok in self._attempts[user_key] if not ok)
        if user_failures >= 10:
            self._lockouts[user_key] = now + 1800  # 30 min lockout
            return "Account temporarily locked due to too many failed attempts. Try again in 30 minutes."

        return None

    def record(self, ip: str, username: str, success: bool):
        now = time.time()
        ip_key = f"ip:{ip}"
        user_key = f"user:{username.lower().strip()}"
        self._attempts[ip_key].append((now, success))
        self._attempts[user_key].append((now, success))
        # On success, clear lockouts
        if success:
            self._lockouts.pop(ip_key, None)
            self._lockouts.pop(user_key, None)


_login_limiter = LoginRateLimiter()


# ============================================================================
# TOKEN BLACKLIST — Revoke tokens on logout
# ============================================================================
# In-memory blacklist with auto-cleanup. Tokens expire after ACCESS_TOKEN_EXPIRE_MINUTES
# anyway, so we only need to hold revoked tokens until they'd naturally expire.
# For multi-instance production, replace with Redis SET + TTL.

class TokenBlacklist:
    """In-memory token blacklist with periodic cleanup of expired entries."""

    def __init__(self):
        self._revoked: Dict[str, float] = {}  # token_hash → expiry timestamp
        self._last_cleanup = time.time()

    def revoke(self, token: str, expires_at: float = None):
        """Add a token to the blacklist."""
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        ttl = expires_at or (time.time() + ACCESS_TOKEN_EXPIRE_MINUTES * 60)
        self._revoked[token_hash] = ttl
        self._maybe_cleanup()

    def is_revoked(self, token: str) -> bool:
        """Check if a token has been revoked."""
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        expiry = self._revoked.get(token_hash)
        if expiry is None:
            return False
        if time.time() > expiry:
            # Token has naturally expired, remove from blacklist
            self._revoked.pop(token_hash, None)
            return False
        return True

    def _maybe_cleanup(self):
        """Periodically purge expired entries (every 10 minutes)."""
        now = time.time()
        if now - self._last_cleanup < 600:
            return
        self._last_cleanup = now
        expired_keys = [k for k, exp in self._revoked.items() if now > exp]
        for k in expired_keys:
            self._revoked.pop(k, None)


_token_blacklist = TokenBlacklist()


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
        if not token or _token_blacklist.is_revoked(token):
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
async def login(request: LoginRequest, req: Request = None):
    """
    Authenticate user and return JWT token.
    Accepts either username or email in the username field.
    Rate-limited: 5 failed attempts per IP (15 min), 10 per username (30 min).
    """
    # Rate limiting check
    client_ip = "unknown"
    if req:
        client_ip = req.headers.get("x-forwarded-for", "").split(",")[0].strip() or req.client.host if req.client else "unknown"
    rate_err = _login_limiter.check(client_ip, request.username)
    if rate_err:
        raise HTTPException(status_code=429, detail=rate_err)

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
        _login_limiter.record(client_ip, request.username, success=False)
        raise HTTPException(status_code=401, detail="Invalid username or password")

    if not verify_password(request.password, user.get("password_hash", "")):
        _login_limiter.record(client_ip, request.username, success=False)
        raise HTTPException(status_code=401, detail="Invalid username or password")

    if not user.get("is_active", False):
        raise HTTPException(status_code=403, detail="User account is disabled")

    # Geo-fence validation: if user is geo_restricted, verify login coordinates
    if user.get("geo_restricted") and user.get("allowed_coordinates"):
        if request.latitude is None or request.longitude is None:
            raise HTTPException(
                status_code=403,
                detail="Location access required. Please enable location services and try again."
            )
        allowed = user["allowed_coordinates"]
        within_fence = False
        for coord in allowed:
            c_lat = coord.get("lat")
            c_lng = coord.get("lng")
            radius = coord.get("radius_meters", 500)  # default 500m
            if c_lat is not None and c_lng is not None:
                dist = _haversine_distance(request.latitude, request.longitude, c_lat, c_lng)
                if dist <= radius:
                    within_fence = True
                    break
        if not within_fence:
            logger.warning(
                "Geo-fence rejection: user=%s lat=%.6f lng=%.6f",
                user.get("username"), request.latitude, request.longitude
            )
            raise HTTPException(
                status_code=403,
                detail="Login not permitted from this location. Please log in from an authorized store."
            )

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

    # Record successful login (clears lockouts)
    _login_limiter.record(client_ip, request.username, success=True)

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
async def logout(
    current_user: dict = Depends(get_current_user),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
):
    """
    Logout user — revokes the current JWT so it can't be reused.
    """
    if credentials and credentials.credentials:
        # Calculate remaining TTL from the token's exp claim
        try:
            exp = current_user.get("exp")
            expires_at = float(exp) if exp else None
        except (TypeError, ValueError):
            expires_at = None
        _token_blacklist.revoke(credentials.credentials, expires_at)
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

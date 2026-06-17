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
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# HTTPBearer with auto_error=False allows us to handle missing credentials gracefully
security = HTTPBearer(auto_error=False)

# Constant dummy bcrypt hash for timing side-channel defense
# Used when user not found to maintain constant-time password verification
# Prevents username enumeration via response-time analysis
_DUMMY_BCRYPT_HASH = "$2b$12$uqhsMGHlGOXPvtqN9Bgcv.vBJ2D6x3aajGtBt/eNIC2c3Yk/pTYU6"

# JWT Configuration
# The previous fallback generated a per-process random secret if JWT_SECRET_KEY
# was unset. With `uvicorn --workers 4` (see backend/Dockerfile) each worker
# held a different secret, so a token signed by one worker failed to decode on
# another → /auth/me returned 401 "Invalid token" right after a successful
# login. Fail fast on missing config instead of silently corrupting tokens.
SECRET_KEY = os.getenv("JWT_SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError(
        "JWT_SECRET_KEY environment variable is required. "
        "Generate one with: openssl rand -hex 32"
    )
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 480  # 8 hours

# BUG-027: the password every seed account ships with (also public in the repo).
# Used ONLY to force a change-on-login for any account still using it -- never to
# authenticate. This is a backend-only constant (it is NOT sent to the client;
# the frontend bundle must not contain it -- see BUG-132).
_SEED_DEFAULT_PASSWORD = "admin123"


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
            return (
                f"Too many login attempts. Try again in {remaining // 60 + 1} minutes."
            )

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
            return (
                "Too many login attempts from this location. Try again in 15 minutes."
            )

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

    # BUG-087: shared-cache key prefix. The cache is Redis-backed in prod
    # (cross-worker) and in-memory in dev (single-worker fallback).
    _PREFIX = "revoked_token:"

    def revoke(self, token: str, expires_at: float = None):
        """Add a token to the blacklist (shared cache + local fast-path).

        BUG-087: prod runs 4 uvicorn workers, so a purely per-process dict meant
        logout only revoked the token in the ONE worker that handled it. Writing
        to the shared cache (Redis SET + TTL) makes revocation visible to every
        worker. Fail-soft: cache write errors never block a logout.
        """
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        now = time.time()
        ttl_at = expires_at or (now + ACCESS_TOKEN_EXPIRE_MINUTES * 60)
        self._revoked[token_hash] = ttl_at
        remaining = max(1, int(ttl_at - now))
        try:
            from api.services.cache import cache

            cache.set(self._PREFIX + token_hash, "1", ttl=remaining)
        except Exception as e:  # noqa: BLE001
            logger.warning("Token revoke: shared-cache write failed: %s", e)
        self._maybe_cleanup()

    def is_revoked(self, token: str) -> bool:
        """Check if a token has been revoked (cross-worker via shared cache)."""
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        # Cross-worker source of truth first (Redis in prod).
        try:
            from api.services.cache import cache

            if cache.get(self._PREFIX + token_hash):
                return True
        except Exception as e:  # noqa: BLE001
            logger.warning("Token revoke: shared-cache read failed: %s", e)
        # Local fast-path / single-worker dev fallback.
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


def require_roles(*allowed_roles: str):
    """Reusable RBAC dependency factory. Returns a dependency that 403s unless
    the user holds one of `allowed_roles`. SUPERADMIN always passes.

    Usage (per-endpoint):   current_user: dict = Depends(require_roles("ADMIN"))
    Usage (whole router):   include_router(r, dependencies=[Depends(require_roles(...))])

    Most sensitive backend routers historically had NO server-side role check
    (only frontend route guards), so any authenticated user could call them
    directly. This is the shared enforcement primitive.
    """
    allowed = set(allowed_roles)

    async def _dep(current_user: dict = Depends(get_current_user)) -> dict:
        roles = set(current_user.get("roles", []) or [])
        if "SUPERADMIN" in roles or (roles & allowed):
            return current_user
        raise HTTPException(
            status_code=403,
            detail="Your role does not have access to this resource",
        )

    return _dep


# ============================================================================
# AUDIT -- chained auth-event trail
# ============================================================================
# SYSTEM_INTENT 'Audit Everything': authentication is a security boundary, so
# login success/failure, logout, and password changes are recorded in the
# tamper-evident audit_logs chain (database/repositories/audit_chain.py) via
# AuditRepository.create(). The client IP is threaded as the "where" dimension.
# Every helper is FAIL-SOFT: a missing DB or any error never blocks the auth
# action -- a login must not 500 because the audit write hiccuped.


def _client_ip(req: "Request") -> str:
    """Best-effort client IP for the audit "where" dimension.

    Prefers the first hop of X-Forwarded-For (the real client behind Railway's
    proxy), then the direct socket peer. Fail-soft -> 'unknown'.
    """
    if req is None:
        return "unknown"
    try:
        xff = req.headers.get("x-forwarded-for", "") or ""
        first = xff.split(",")[0].strip()
        if first:
            return first
        if req.client and req.client.host:
            return req.client.host
    except Exception:  # noqa: BLE001
        pass
    return "unknown"


def _audit_auth_event(
    *,
    action: str,
    user_id: Optional[str],
    username: Optional[str],
    ip_address: str,
    severity: str = "INFO",
    detail: Optional[str] = None,
) -> None:
    """Append a hash-chained row to audit_logs for an auth event.

    Routed through AuditRepository.create() (NOT a raw insert) so the row gets
    seq / prev_hash / entry_hash and is verifiable at GET /api/v1/audit/verify.
    Fail-soft: swallows every error so authentication never breaks on an audit
    hiccup (the gap then shows honestly at verify time).
    """
    try:
        from ..dependencies import get_audit_repository

        repo = get_audit_repository()
        if repo is None:
            return
        repo.create(
            {
                "action": action,
                "entity_type": "auth",
                "entity_id": user_id or username,
                "user_id": user_id or username,
                "username": username,
                "source": "AUTH",
                "ip_address": ip_address,
                "severity": severity,
                "detail": detail,
                "timestamp": datetime.utcnow(),
            }
        )
    except Exception as e:  # noqa: BLE001 - auth must never break on audit
        logger.debug("[AUTH] audit write skipped (%s): %s", action, e)


# ============================================================================
# ENDPOINTS
# ============================================================================


def _store_fence_coords(user: dict) -> list:
    """Geo-fence coordinates derived from the store(s) a user is assigned to, so
    seeding a store's location auto-fences its staff (roles 4-7) without having
    to copy coordinates onto every user. Fail-soft -> []."""
    try:
        from database.connection import get_db

        db = get_db().db
    except Exception:
        return []
    if db is None:
        return []
    ids = set()
    for key in ("store_ids", "home_store_id", "active_store_id"):
        val = user.get(key)
        if isinstance(val, list):
            ids.update(v for v in val if v)
        elif val:
            ids.add(val)
    out = []
    for sid in ids:
        try:
            s = db.get_collection("stores").find_one(
                {"store_id": sid},
                {"_id": 0, "latitude": 1, "longitude": 1, "geofence_radius_m": 1},
            )
        except Exception:
            s = None
        if s and s.get("latitude") is not None and s.get("longitude") is not None:
            out.append(
                {
                    "lat": s["latitude"],
                    "lng": s["longitude"],
                    "radius_meters": s.get("geofence_radius_m") or 500,
                }
            )
    return out


def _default_active_store(user: dict) -> Optional[str]:
    """Pick a sensible active store for an all-stores role (SUPERADMIN/ADMIN/
    AREA_MANAGER) whose account has NO explicit store assignment, so the topbar
    never shows a 'No store' pill and POS isn't dead-ended on first login.

    Prefers an active HQ store, else any active store, else any store. Returns
    None (the prior behaviour) when the user is not an all-stores role, there is
    no DB, or no stores exist. Fail-soft -- never blocks token issue."""
    roles = user.get("roles", []) or []
    if not any(r in ("SUPERADMIN", "ADMIN", "AREA_MANAGER") for r in roles):
        return None
    try:
        from database.connection import get_db

        db = get_db().db
    except Exception:
        return None
    if db is None:
        return None
    try:
        coll = db.get_collection("stores")
        s = (
            coll.find_one({"is_active": True, "store_type": "HQ"}, {"_id": 0, "store_id": 1})
            or coll.find_one({"is_active": True}, {"_id": 0, "store_id": 1})
            or coll.find_one({}, {"_id": 0, "store_id": 1})
        )
        return (s or {}).get("store_id")
    except Exception:
        return None


@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest, req: Request = None):
    """
    Authenticate user and return JWT token.
    Accepts either username or email in the username field.
    Rate-limited: 5 failed attempts per IP (15 min), 10 per username (30 min).
    """
    # Rate limiting check
    client_ip = _client_ip(req)
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
                        "store_ids": [
                            "BV-BOK-01",
                            "BV-BOK-02",
                            "BV-DHN-01",
                            "BV-DHN-02",
                            "WO-DHN-01",
                            "BV-PUN-01",
                        ],
                        "is_active": True,
                    }
            except Exception:
                pass

    if user is None:
        # Always run bcrypt verify against dummy hash to prevent timing side-channel
        # username enumeration. Unknown users will have constant-time response.
        verify_password(request.password, _DUMMY_BCRYPT_HASH)
        _login_limiter.record(client_ip, request.username, success=False)
        _audit_auth_event(
            action="login_failure",
            user_id=None,
            username=request.username,
            ip_address=client_ip,
            severity="WARNING",
            detail="unknown username",
        )
        raise HTTPException(status_code=401, detail="Invalid username or password")

    if not verify_password(request.password, user.get("password_hash", "")):
        _login_limiter.record(client_ip, request.username, success=False)
        _audit_auth_event(
            action="login_failure",
            user_id=user.get("user_id"),
            username=user.get("username", request.username),
            ip_address=client_ip,
            severity="WARNING",
            detail="bad password",
        )
        raise HTTPException(status_code=401, detail="Invalid username or password")

    if not user.get("is_active", False):
        _audit_auth_event(
            action="login_failure",
            user_id=user.get("user_id"),
            username=user.get("username", request.username),
            ip_address=client_ip,
            severity="WARNING",
            detail="account disabled",
        )
        raise HTTPException(status_code=403, detail="User account is disabled")

    # Geo-fence validation: a geo_restricted user must log in within range of an
    # allowed coordinate. Allowed coordinates are the union of the user's
    # explicit allowed_coordinates AND the geo-coordinates of the store(s) they
    # are assigned to -- so seeding a store's location auto-fences its staff.
    if user.get("geo_restricted"):
        fence_coords = list(user.get("allowed_coordinates") or [])
        fence_coords.extend(_store_fence_coords(user))
    else:
        fence_coords = []
    if fence_coords:
        if request.latitude is None or request.longitude is None:
            raise HTTPException(
                status_code=403,
                detail="Location access required. Please enable location services and try again.",
            )
        allowed = fence_coords
        within_fence = False
        for coord in allowed:
            c_lat = coord.get("lat")
            c_lng = coord.get("lng")
            radius = coord.get("radius_meters", 500)  # default 500m
            if c_lat is not None and c_lng is not None:
                dist = _haversine_distance(
                    request.latitude, request.longitude, c_lat, c_lng
                )
                if dist <= radius:
                    within_fence = True
                    break
        if not within_fence:
            logger.warning(
                "Geo-fence rejection: user=%s lat=%.6f lng=%.6f",
                user.get("username"),
                request.latitude,
                request.longitude,
            )
            raise HTTPException(
                status_code=403,
                detail="Login not permitted from this location. Please log in from an authorized store.",
            )

    # Validate store access if store_id provided
    user_store_ids = user.get("store_ids", [])
    active_store = request.store_id
    if active_store and active_store not in user_store_ids:
        if not any(r in ["ADMIN", "SUPERADMIN"] for r in user.get("roles", [])):
            raise HTTPException(status_code=403, detail="No access to this store")

    # Whether this user must change their password before using the app. Set by
    # an admin on user-create or password-reset; cleared by /change-password.
    # Threaded through the token AND the login response so the frontend can gate
    # the app and it survives a browser refresh (via /auth/me + /refresh).
    must_change_password = bool(user.get("must_change_password", False))

    # BUG-027 defense-in-depth: any account that authenticated with the SHIPPED
    # DEFAULT password (seed accounts: admin/admin123 etc.) is forced to change
    # it, regardless of the stored flag. This catches pre-existing prod seed docs
    # created before must_change_password was seeded. Persist so the gate (the
    # frontend ForcePasswordChange screen) survives refresh; never blocks the
    # token issue itself. Cleared automatically once the user picks a new password.
    # (Skipped under ENVIRONMENT=test so the deterministic CI/e2e suite, which
    # logs in with the default, isn't bounced to the change-password screen.
    # Prod does not set ENVIRONMENT=test, so the control is live in production.)
    if (
        request.password == _SEED_DEFAULT_PASSWORD
        and os.getenv("ENVIRONMENT", "").lower() != "test"
    ):
        must_change_password = True
        if user_repo and not user.get("must_change_password"):
            try:
                user_repo.collection.update_one(
                    {"username": user.get("username")},
                    {"$set": {"must_change_password": True}},
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("Could not persist must_change_password: %s", e)

    # Per-user module access -- a DENY-ONLY override carried in the token so
    # every store-scoped worker resolves the same restrictions from the JWT
    # alone. Always a dict (absent on the user doc -> {}) for a stable,
    # backward-compatible shape. The role remains the CEILING: this is only ever
    # read by the frontend to HIDE/route-block modules, never to grant one.
    module_access = user.get("module_access") or {}

    # Create token
    token_data = {
        "user_id": user.get("user_id", user.get("_id", "")),
        "username": user.get("username", ""),
        "roles": user.get("roles", []),
        "store_ids": user_store_ids,
        "active_store_id": (
            active_store
            or (user_store_ids[0] if user_store_ids else None)
            or _default_active_store(user)
        ),
        "must_change_password": must_change_password,
        "module_access": module_access,
    }

    access_token = create_access_token(token_data)

    # Record successful login (clears lockouts)
    _login_limiter.record(client_ip, request.username, success=True)
    _audit_auth_event(
        action="login_success",
        user_id=token_data["user_id"],
        username=user.get("username", request.username),
        ip_address=client_ip,
        severity="INFO",
    )

    # Compute role-aware effective discount cap so the frontend doesn't have to
    # know the role→cap matrix. SUPERADMIN/ADMIN → 100% (unlimited), managers
    # get role baseline + any per-user override. See services/role_caps.py.
    from api.services.role_caps import effective_discount_cap

    eff_cap = effective_discount_cap(
        user.get("roles", []),
        user.get("discount_cap"),
    )

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
            "discount_cap": eff_cap,
            "must_change_password": must_change_password,
            "module_access": module_access,
            # Per-user CAPABILITY override (council ruling sec.2). Carried in the
            # RESPONSE BODY only -- deliberately NOT in the JWT (a revoke must
            # take effect next request, not next 8h login). The FE merges it over
            # the role baseline in hasPermission. Always a dict for a stable
            # shape; absent on the doc -> {} -> DARK (role baseline unchanged).
            "permissions": user.get("permissions") or {},
        },
    )


@router.post("/logout")
async def logout(
    req: Request = None,
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
    _audit_auth_event(
        action="logout",
        user_id=current_user.get("user_id"),
        username=current_user.get("username"),
        ip_address=_client_ip(req),
        severity="INFO",
    )
    return {"message": "Successfully logged out"}


@router.get("/me")
async def get_me(current_user: dict = Depends(get_current_user)):
    """
    Get current user info from token.

    Ensures `must_change_password` is present so the frontend gate survives a
    browser refresh. Tokens minted before this flag existed won't carry it, so
    fall back to the live DB record (fail-soft -> False) for those.

    `module_access` (deny-only per-user module override) gets the same
    treatment: tokens minted before it existed won't carry it, so fall back to
    the live DB record (fail-soft -> {}) so the field is always present and the
    frontend's hasModuleAccess never crashes on undefined.
    """
    me = dict(current_user)
    needs_pwd = "must_change_password" not in me
    needs_modules = "module_access" not in me
    # `permissions` (the capability override) is DELIBERATELY never in the JWT
    # (ruling sec.2: a revoke must take effect next request, not next login), so
    # /me ALWAYS hydrates it live from the DB doc -- it can never come from the
    # token. Fail-soft -> {} (DARK).
    needs_perms = True
    if needs_pwd or needs_modules or needs_perms:
        rec = None
        try:
            from ..dependencies import get_user_repository

            user_repo = get_user_repository()
            if user_repo is not None:
                rec = user_repo.find_by_id(me.get("user_id")) or (
                    user_repo.collection.find_one({"username": me.get("username")})
                )
        except Exception:
            rec = None
        if needs_pwd:
            me["must_change_password"] = bool(
                (rec or {}).get("must_change_password", False)
            )
        if needs_modules:
            me["module_access"] = (rec or {}).get("module_access") or {}
        me["permissions"] = (rec or {}).get("permissions") or {}
    return me


@router.get("/ecommerce-sso")
async def ecommerce_sso(current_user: dict = Depends(get_current_user)):
    """Single-sign-on handoff into the online-store (e-commerce/BVI) admin.

    Mints a short-lived RS256 exchange token (NOT the IMS session token) for an
    authorised user and returns the URL to open. Deny-by-default by role; the
    online store maps the token's email to an EXISTING online-store user.
    """
    from ..services import ecommerce_sso as sso

    role = sso.mapped_bvi_role(current_user.get("roles"))
    if role is None:
        raise HTTPException(
            status_code=403, detail="Your role does not have online-store admin access"
        )
    if not sso.sso_configured():
        raise HTTPException(
            status_code=503, detail="Online-store SSO is not configured yet"
        )

    # The online store maps users by email, so we need the IMS account's email.
    email = current_user.get("email")
    if not email:
        try:
            from ..dependencies import get_user_repository

            urepo = get_user_repository()
            rec = urepo.find_by_id(current_user.get("user_id")) if urepo else None
            email = (rec or {}).get("email")
        except Exception:
            email = None
    if not email:
        raise HTTPException(
            status_code=400,
            detail="Your account has no email; add one to use online-store SSO",
        )

    token = sso.mint_sso_token(
        {
            "user_id": current_user.get("user_id"),
            "email": email,
            "username": current_user.get("username"),
            "roles": current_user.get("roles"),
        }
    )
    if not token:
        raise HTTPException(status_code=503, detail="Online-store SSO is unavailable")

    # Claim the jti to prevent the SAME token (same jti) from being re-minted
    # within its lifetime. BVI cannot easily check IMS's Mongo (different DB,
    # different runtime), so this single-use guard is enforced at mint-time on
    # the IMS side. Fail-soft when db is None -- BVI still enforces aud/iss/
    # scope/exp on the wire.
    try:
        _claims = jwt.decode(token, options={"verify_signature": False})
        jti = _claims.get("jti")
        exp = int(_claims.get("exp") or 0)
    except Exception:
        jti, exp = None, 0
    try:
        from ..dependencies import get_db as _get_db_dep

        _wrapper = _get_db_dep()
        _db = getattr(_wrapper, "db", None) if _wrapper is not None else None
    except Exception:
        _db = None
    if jti and exp and not sso.claim_jti(_db, jti, exp):
        # Fresh uuid4 collision is astronomically unlikely; if it happens, fail
        # closed rather than reuse a jti.
        raise HTTPException(
            status_code=500, detail="SSO token jti collision; please retry"
        )

    base = (os.getenv("ECOMMERCE_URL") or "https://uniparallel.com").rstrip("/")
    return {"url": f"{base}/sso?token={token}", "expires_in": 90}


@router.post("/refresh")
async def refresh_token(request: RefreshTokenRequest):
    """
    Refresh access token
    """
    payload = decode_token(request.token)

    # Re-validate against the LIVE user record so a disabled or role-downgraded
    # account cannot keep elevated access for another 8h by refreshing. The access
    # token is stateless, so /refresh is the natural session re-check point.
    # Fail-soft: if the DB is unavailable, fall back to the token's own claims
    # (the prior behaviour) rather than blocking a refresh.
    from ..dependencies import get_user_repository

    db_user = None
    try:
        user_repo = get_user_repository()
        if user_repo:
            db_user = user_repo.collection.find_one(
                {"user_id": payload.get("user_id")}
            ) or user_repo.collection.find_one({"username": payload.get("username")})
    except Exception as e:  # noqa: BLE001
        logger.warning("refresh: live user re-check failed: %s", e)
        db_user = None

    if db_user is not None:
        if not db_user.get("is_active", True):
            raise HTTPException(status_code=403, detail="Account is disabled")
        roles = db_user.get("roles", payload.get("roles", []))
        store_ids = db_user.get("store_ids", payload.get("store_ids", []))
        module_access = db_user.get("module_access") or {}
        must_change = bool(db_user.get("must_change_password", False))
    else:
        roles = payload.get("roles", [])
        store_ids = payload.get("store_ids", [])
        module_access = payload.get("module_access") or {}
        must_change = bool(payload.get("must_change_password", False))

    # Keep the active store only if it is still one of the user's stores (a
    # reassigned store-level user falls back to their first remaining store).
    active_store = payload.get("active_store_id")
    if (
        store_ids
        and active_store
        and active_store not in store_ids
        and not any(r in ("ADMIN", "SUPERADMIN") for r in roles)
    ):
        active_store = store_ids[0]

    # Create new token (preserve the force-password-change flag + the deny-only
    # module override across refresh so the restriction survives a re-issue).
    token_data = {
        "user_id": payload["user_id"],
        "username": payload["username"],
        "roles": roles,
        "store_ids": store_ids,
        "active_store_id": active_store,
        "must_change_password": must_change,
        "module_access": module_access,
    }

    new_token = create_access_token(token_data)

    return {
        "access_token": new_token,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    }


@router.post("/change-password")
async def change_password(
    request: ChangePasswordRequest,
    req: Request = None,
    current_user: dict = Depends(get_current_user),
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
        _audit_auth_event(
            action="password_change_failed",
            user_id=user.get("user_id"),
            username=user.get("username"),
            ip_address=_client_ip(req),
            severity="WARNING",
            detail="current password incorrect",
        )
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    # Hash new password and update. Clear must_change_password so a forced
    # first-login change unblocks the user (set by admin create / reset).
    new_hash = hash_password(request.new_password)
    user_repo.collection.update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "password_hash": new_hash,
                "must_change_password": False,
                "updated_at": datetime.utcnow().isoformat(),
            }
        },
    )

    _audit_auth_event(
        action="password_changed",
        user_id=user.get("user_id"),
        username=user.get("username"),
        ip_address=_client_ip(req),
        severity="INFO",
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

    # Create new token with updated store (preserve force-password-change flag
    # + the deny-only module override so switching stores can't drop it)
    token_data = {
        "user_id": current_user["user_id"],
        "username": current_user["username"],
        "roles": current_user["roles"],
        "store_ids": current_user["store_ids"],
        "active_store_id": store_id,
        "must_change_password": bool(current_user.get("must_change_password", False)),
        "module_access": current_user.get("module_access") or {},
    }

    new_token = create_access_token(token_data)

    return {"access_token": new_token, "active_store_id": store_id}

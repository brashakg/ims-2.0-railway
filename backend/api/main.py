"""
IMS 2.0 - FastAPI Main Application
===================================
Main entry point for the API server
"""

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from collections import defaultdict
import time
import logging
import os
import sys

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Observability note: error/health monitoring is in-house via the SENTINEL
# agent (health_checks + alert_history collections, agent.error events). There
# is no external APM (sentry.io) dependency. Slack alerting lives in
# backend/observability.py and is used by SENTINEL/ORACLE when configured.

# Add parent directory to path for database imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Database connection
try:
    from database.connection import init_db, close_db, get_db, DatabaseConfig

    DATABASE_AVAILABLE = True
except ImportError:
    DATABASE_AVAILABLE = False
    logger.warning("Database module not available - running in stub mode")

# Import routers
from .routers import (
    auth_router,
    dashboard_widgets_router,
    users_router,
    stores_router,
    products_router,
    inventory_router,
    customers_router,
    crm_router,
    orders_router,
    prescriptions_router,
    vendors_router,
    vendor_returns_router,
    returns_router,
    tasks_router,
    expenses_router,
    finance_router,
    hr_router,
    workshop_router,
    reports_router,
    settings_router,
    clinical_router,
    admin_router,
    admin_catalog_router,
    admin_extras_router,
    handoffs_router,
    transfers_router,
    catalog_router,
    catalog_autopilot_router,
    jarvis_router,
    analytics_router,
    follow_ups_router,
    payroll_router,
    marketing_router,
    analytics_v2_router,
    agents_router,
    proposals_router,
    walkouts_router,
    points_router,
    payout_router,
    webhooks_router,
    loyalty_router,
    vendor_portal_router,
    portal_router,
    techcherry_import_router,
    vouchers_router,
    entities_router,
    notifications_router,
    shipping_router,
    labels_router,
    display_fixtures_router,
    display_placements_router,
    print_overrides_router,
    lens_catalog_router,
    lens_stock_router,
    lens_enums_router,
    product_templates_router,
    audit_router,
    budgets_router,
    online_store_router,
    online_store_collections_router,
    online_store_menus_router,
    online_store_images_router,
    online_store_push_router,
)
from .routers.auth import require_roles

# Roles allowed to see company financials / payroll (mirrors the frontend
# Finance + Payroll route guards — managers + accountant; SUPERADMIN auto).
_FINANCE_ROLES = ("ADMIN", "AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT")


# Lifespan context manager for startup/shutdown
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("[START] Starting IMS 2.0 API Server...")

    # ── Environment validation ──────────────────────────────────────────
    _jwt_key = os.getenv("JWT_SECRET_KEY", "")
    if not _jwt_key or _jwt_key in (
        "CHANGE_THIS_TO_A_RANDOM_SECRET_KEY_IN_PRODUCTION",
        "dev-secret-key-change-in-production",
    ):
        logger.warning(
            "[SECURITY] JWT_SECRET_KEY is missing or using a default placeholder! "
            "Set a strong random secret via environment variable for production."
        )
    _mongo_url = (
        os.getenv("MONGODB_URL") or os.getenv("MONGO_URL") or os.getenv("MONGO_HOST")
    )
    if not _mongo_url:
        logger.warning(
            "[CONFIG] No MongoDB connection configured (MONGODB_URL / MONGO_URL / MONGO_HOST). Running in stub mode."
        )
    _missing_recommended = [
        v for v in ("CORS_ORIGINS", "RATE_LIMIT_PER_MINUTE") if not os.getenv(v)
    ]
    if _missing_recommended:
        logger.info(
            f"[CONFIG] Optional env vars not set (using defaults): {', '.join(_missing_recommended)}"
        )

    # Initialize database connection
    if DATABASE_AVAILABLE:
        mongo_url = os.getenv("MONGODB_URL") or os.getenv("MONGO_URL")
        if mongo_url:
            config = DatabaseConfig.from_uri(mongo_url, database="ims_2_0")
        else:
            config = DatabaseConfig.from_env()

        if init_db(config):
            logger.info("[OK] Database connection established")
            # Create performance indexes (idempotent — safe to call every startup)
            try:
                get_db().ensure_indexes()
            except Exception as e:
                logger.warning(f"[WARN] Index creation skipped: {e}")
            # Seed the editable HSN -> GST rate master (idempotent; never
            # overwrites owner edits). Powers POS GST resolution overrides
            # via services/gst_rates.py.
            try:
                from .services.gst_rates import seed_hsn_gst_master

                _n = seed_hsn_gst_master()
                if _n:
                    logger.info(f"[OK] Seeded {_n} HSN->GST master rows")
            except Exception as e:
                logger.warning(f"[WARN] HSN->GST seed skipped: {e}")
        else:
            logger.warning("[WARN] Database not connected - running in mock mode")
    else:
        logger.info("[INFO] Running without database (stub mode)")

    # ── Agent System startup ────────────────────────────────────────────
    # Split into independent try/except blocks so one step's failure
    # doesn't silently kill the rest (audit Run #2 found the whole block
    # bailing before registry init ran → 0/8 agents on prod).
    _scheduler = None
    _event_bus = None
    AGENT_REGISTRY = None
    db = None

    try:
        if DATABASE_AVAILABLE:
            from database.connection import get_seeded_db

            db = get_seeded_db()
    except Exception as e:
        logger.error(f"[AGENTS] get_seeded_db failed: {e}", exc_info=True)

    # Step 1: seed configs — never blocks registry/scheduler
    try:
        from agents.config import AgentConfigManager

        AgentConfigManager(db=db).seed_configs()
        logger.info("[AGENTS] Agent configs seeded")
    except Exception as e:
        logger.error(f"[AGENTS] Config seed failed (non-fatal): {e}", exc_info=True)

    # Step 2: register agents — the big one, wrapped independently
    try:
        from agents.registry import initialize_registry, AGENT_REGISTRY as _reg

        initialize_registry(db=db)
        AGENT_REGISTRY = _reg
        logger.info(
            f"[AGENTS] Registry initialized — {len(AGENT_REGISTRY)} agents registered"
        )
    except Exception as e:
        logger.error(
            f"[AGENTS] Registry init CATASTROPHIC failure: {e}. No agents will run this worker.",
            exc_info=True,
        )
        AGENT_REGISTRY = {}

    # Step 3: event bus — depends on registry for subscriptions, but fail-soft
    try:
        from agents.event_bus import get_event_bus

        _event_bus = get_event_bus(db=db)
        await _event_bus.start()
        logger.info(
            f"[AGENTS] Event bus started ({'DISTRIBUTED' if _event_bus.is_distributed else 'IN-PROCESS'})"
        )
    except Exception as e:
        logger.error(f"[AGENTS] Event bus start failed (non-fatal): {e}", exc_info=True)
        _event_bus = None

    # Step 4: scheduler — only meaningful if registry has agents.
    # Skipped under pytest / ENVIRONMENT=test: the 60s SENTINEL tick (and other
    # agents) would write health_checks / alert_history / agent_audit_log
    # mid-session, polluting CI's shared mongo and adding non-determinism to
    # every agent test (e.g. test_sentinel's "no data -> latest is None").
    # Mirrors the rate-limit test-gate further below.
    if os.getenv("PYTEST_CURRENT_TEST") or os.getenv("ENVIRONMENT") == "test":
        logger.info("[AGENTS] Scheduler not started (test env)")
        _scheduler = None
    else:
        try:
            from agents.scheduler import AgentScheduler

            _scheduler = AgentScheduler(db=db)
            await _scheduler.start(AGENT_REGISTRY or {})
            logger.info(
                f"[AGENTS] Scheduler started with {len(AGENT_REGISTRY or {})} agents"
            )
            import agents as _agents_pkg

            _agents_pkg._scheduler_instance = _scheduler
        except Exception as e:
            logger.error(
                f"[AGENTS] Scheduler start failed (non-fatal): {e}", exc_info=True
            )
            _scheduler = None

    yield

    # Shutdown
    logger.info("🛑 Shutting down IMS 2.0 API Server...")

    # Shutdown Agent Scheduler
    if _scheduler:
        try:
            await _scheduler.shutdown()
            logger.info("[AGENTS] Scheduler shutdown")
        except Exception as e:
            logger.warning(f"[AGENTS] Scheduler shutdown error: {e}")

    # Shutdown Event Bus (cancel listener task, close Redis conn)
    if _event_bus:
        try:
            await _event_bus.stop()
            logger.info("[AGENTS] Event bus stopped")
        except Exception as e:
            logger.warning(f"[AGENTS] Event bus shutdown error: {e}")

    if DATABASE_AVAILABLE:
        close_db()
        logger.info("🔌 Database connection closed")


# Create FastAPI application
app = FastAPI(
    title="IMS 2.0 - Retail Operating System",
    description="Complete Optical & Lifestyle Retail Operating System API",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
    redirect_slashes=False,  # Prevent 307 redirects that break CORS
)

# CORS Middleware - Configure allowed origins
# When allow_credentials=True, we cannot use wildcard "*"
# Must explicitly list all allowed origins
DEFAULT_CORS_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
    "https://ims-2-0-railway.vercel.app",
    "https://ims-20-railway.vercel.app",
    "https://ims-2-0-railway-production.up.railway.app",
    "https://ims-20-railway-production.up.railway.app",
    # Unified custom-domain plan (Option A — subdomains under uniparallel.com):
    #   app.uniparallel.com = IMS frontend, api.uniparallel.com = this backend,
    #   uniparallel.com (apex) = BVI admin. Listed explicitly here so they show
    #   in the startup CORS log; any other *.uniparallel.com subdomain is also
    #   accepted by the pattern in _is_allowed_origin().
    "https://uniparallel.com",
    "https://app.uniparallel.com",
    "https://api.uniparallel.com",
]


def _sanitize_cors_origin(origin: str) -> str:
    """Sanitize a CORS origin - remove paths, ensure it's a valid origin"""
    origin = origin.strip()
    # Skip wildcards and empty strings
    if not origin or origin == "*":
        return ""
    # Remove any path component - CORS origins should only be scheme://host[:port]
    if "://" in origin:
        parts = origin.split("://", 1)
        scheme = parts[0]
        rest = parts[1]
        # Remove path (everything after first /)
        host_port = rest.split("/")[0]
        return f"{scheme}://{host_port}"
    return ""


def _is_allowed_origin(origin: str) -> bool:
    """Check if an origin is allowed based on exact match or pattern"""
    if not origin:
        return False

    # Check exact match first
    if origin in DEFAULT_CORS_ORIGINS:
        return True

    # Allow all Vercel preview deployments (*.vercel.app)
    if origin.startswith("https://") and ".vercel.app" in origin:
        return True

    # Allow Railway preview deployments (*.up.railway.app)
    if origin.startswith("https://") and ".up.railway.app" in origin:
        return True

    # Allow the unified uniparallel.com app + ANY subdomain (Option A):
    # app.uniparallel.com (IMS frontend) calls api.uniparallel.com (backend),
    # and the apex hosts the BVI admin. ".uniparallel.com" (with the leading
    # dot) means only true subdomains match -- "eviluniparallel.com" does NOT.
    if origin.startswith("https://") and (
        origin == "https://uniparallel.com" or origin.endswith(".uniparallel.com")
    ):
        return True

    return False


# Add any custom origins from environment
env_origins = os.getenv("CORS_ORIGINS", "")
if env_origins:
    custom_origins = [_sanitize_cors_origin(o) for o in env_origins.split(",")]
    # Filter out empty strings and duplicates
    custom_origins = [o for o in custom_origins if o and o not in DEFAULT_CORS_ORIGINS]
    CORS_ORIGINS = DEFAULT_CORS_ORIGINS + custom_origins
else:
    CORS_ORIGINS = DEFAULT_CORS_ORIGINS

logger.info(f"CORS Origins configured: {CORS_ORIGINS}")
logger.info("✓ All *.vercel.app and *.up.railway.app domains allowed")

# ============================================================================
# CORS — origin-whitelisted, headers reflected from request.
#
# Why not starlette's CORSMiddleware with a static allow_headers list?
# ----------------------------------------------------------------------------
# We used to list headers explicitly ("Authorization, Content-Type, Accept,
# Origin, X-Requested-With, Cache-Control"). That list silently drifts out
# of date every time the frontend adds a client-side header (e.g. a retry
# counter, request-ID, version tag). The browser's CORS preflight asks for
# the new header in `Access-Control-Request-Headers`, the server's list
# doesn't include it → preflight 400 → axios sees `!error.response` → the
# user sees "Network error connecting to API" with no actionable clue.
#
# This has happened more than once. Permanent fix: keep the strict origin
# whitelist (that's the real security boundary) but REFLECT whatever
# headers the browser requests in preflight. CORS is a browser-level
# mechanism — we still enforce auth, rate limits, and validation on the
# server side. Reflecting headers from an allowed origin does not weaken
# security; it just stops friendly preflights from failing.
# ============================================================================

_CORS_ALLOW_METHODS = "GET, POST, PUT, DELETE, PATCH, OPTIONS"
_CORS_EXPOSE_HEADERS = "X-Process-Time, Content-Disposition"
# Sensible fallback when a request doesn't include Access-Control-Request-Headers
# (some tools send a preflight without listing anything).
_CORS_DEFAULT_ALLOW_HEADERS = (
    "Authorization, Content-Type, Accept, Origin, X-Requested-With, "
    "Cache-Control, X-Retry-Count, X-Request-ID, X-Client-Version, "
    "X-Idempotency-Key"
)


# ============================================================================
# GLOBAL RATE LIMITER — Per-IP sliding window
# ============================================================================
# 120 requests per minute per IP for all endpoints (generous for POS use)
_GLOBAL_RATE_LIMIT = int(os.getenv("RATE_LIMIT_PER_MINUTE", "120"))
_GLOBAL_RATE_WINDOW = 60  # seconds
_request_log: dict = defaultdict(list)


@app.middleware("http")
async def global_rate_limiter(request: Request, call_next):
    """Per-IP rate limiting for all API endpoints."""
    # Skip health checks and static files
    if request.url.path in ("/health", "/docs", "/openapi.json", "/redoc"):
        return await call_next(request)

    # Skip rate limiting under pytest — TestClient hammers a single IP
    # (127.0.0.1) and CI's matrix POSTs cumulatively cross the 120/min
    # threshold within a few seconds. Production traffic is per-real-IP.
    if os.getenv("PYTEST_CURRENT_TEST") or os.getenv("ENVIRONMENT") == "test":
        return await call_next(request)

    client_ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (
        request.client.host if request.client else "unknown"
    )
    now = time.time()
    cutoff = now - _GLOBAL_RATE_WINDOW

    # Clean old entries and count
    _request_log[client_ip] = [t for t in _request_log[client_ip] if t > cutoff]
    if len(_request_log[client_ip]) >= _GLOBAL_RATE_LIMIT:
        return JSONResponse(
            status_code=429,
            content={
                "detail": f"Rate limit exceeded. Max {_GLOBAL_RATE_LIMIT} requests per minute."
            },
        )
    _request_log[client_ip].append(now)
    return await call_next(request)


# ============================================================================
# SECURITY HEADERS
# ============================================================================


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Add security headers to every response."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(self)"
    )
    response.headers["Strict-Transport-Security"] = (
        "max-age=31536000; includeSubDomains"
    )
    # CSP: allow self + Vercel preview domains for the frontend
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; "
        "connect-src 'self' https://*.vercel.app https://*.up.railway.app "
        "https://*.uniparallel.com"
    )
    return response


# Full CORS middleware — handles preflight + adds CORS headers to all responses.
# Reflects `Access-Control-Request-Headers` so any custom frontend header works.
@app.middleware("http")
async def dynamic_cors_handler(request: Request, call_next):
    origin = request.headers.get("origin")
    origin_allowed = bool(origin and _is_allowed_origin(origin))

    # Preflight: short-circuit without invoking downstream routing.
    # Origins we don't allow get the default 405 so browsers refuse.
    if request.method == "OPTIONS":
        if not origin_allowed:
            logger.debug(f"CORS preflight rejected for origin: {origin!r}")
            return await call_next(request)

        # Reflect what the browser asked for — permanent fix for the "add a
        # custom header, CORS breaks" class of bug.
        requested_headers = request.headers.get(
            "access-control-request-headers", _CORS_DEFAULT_ALLOW_HEADERS
        )
        requested_method = request.headers.get(
            "access-control-request-method", _CORS_ALLOW_METHODS
        )

        logger.debug(
            f"CORS preflight OK origin={origin} headers={requested_headers!r} method={requested_method}"
        )
        return JSONResponse(
            status_code=200,
            content=None,
            headers={
                "Access-Control-Allow-Origin": origin,
                "Access-Control-Allow-Credentials": "true",
                "Access-Control-Allow-Headers": requested_headers,
                "Access-Control-Allow-Methods": _CORS_ALLOW_METHODS,
                "Access-Control-Max-Age": "600",  # cache preflight for 10 min
                "Vary": "Origin, Access-Control-Request-Headers, Access-Control-Request-Method",
            },
        )

    # Normal request: pass through, then add CORS headers to the response.
    response = await call_next(request)
    if origin_allowed:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Expose-Headers"] = _CORS_EXPOSE_HEADERS
        # Prevent cache poisoning across origins.
        existing_vary = response.headers.get("Vary", "")
        response.headers["Vary"] = (
            (existing_vary + ", Origin").strip(", ") if existing_vary else "Origin"
        )
    return response


# Request timing middleware
@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(process_time)
    return response


# ── INVESTOR write-block ──────────────────────────────────────────────────
# The INVESTOR role (12th canonical role added May 2026) is read-only across
# the entire app — silent investors / franchise partners' accountants need
# numbers but must never edit anything. Rather than annotating every router
# with a role gate, we 403 every non-safe HTTP method at the middleware
# layer. Read methods (GET, HEAD, OPTIONS) flow through; writes (POST, PUT,
# PATCH, DELETE) get blocked.
#
# Carve-outs:
#   - /api/v1/auth/login          (otherwise the investor can't log in)
#   - /api/v1/auth/logout         (let them sign out)
#   - /api/v1/auth/refresh        (refresh the JWT)
#   - /api/v1/auth/change-password (let them change their own password)
#
# The check is gated on "user holds INVESTOR but no other elevated role" —
# someone tagged INVESTOR + SUPERADMIN keeps full write access.
_INVESTOR_WRITE_CARVE_OUTS = {
    "/api/v1/auth/login",
    "/api/v1/auth/logout",
    "/api/v1/auth/refresh",
    "/api/v1/auth/change-password",
}
_INVESTOR_WRITE_BLOCK_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _is_investor_only(roles) -> bool:
    """True iff the user holds INVESTOR and no other role with write power.
    Any other role (SUPERADMIN, ADMIN, AREA_MANAGER, STORE_MANAGER, etc.) on
    the same user opens the gate — INVESTOR is purely additive, never a
    privilege downgrade."""
    if not roles:
        return False
    role_set = set(roles) if not isinstance(roles, set) else roles
    if "INVESTOR" not in role_set:
        return False
    # If the user has only INVESTOR (or INVESTOR + non-write roles, but
    # there aren't any non-INVESTOR read-only roles in the canonical set)
    return role_set == {"INVESTOR"}


@app.middleware("http")
async def block_investor_writes(request: Request, call_next):
    if request.method not in _INVESTOR_WRITE_BLOCK_METHODS:
        return await call_next(request)
    if request.url.path in _INVESTOR_WRITE_CARVE_OUTS:
        return await call_next(request)

    # Decode JWT just enough to inspect roles. Don't fail on missing —
    # downstream auth dependency will return 401 if the token is bad.
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return await call_next(request)
    token = auth_header.split(" ", 1)[1].strip()
    try:
        from .routers.auth import decode_token

        payload = decode_token(token)
        roles = payload.get("roles", [])
    except Exception:
        return await call_next(request)

    if _is_investor_only(roles):
        # Add CORS headers so the browser doesn't mask the 403 as a network error
        origin = request.headers.get("origin")
        response = JSONResponse(
            status_code=403,
            content={
                "detail": "INVESTOR role is read-only. Write operations are not permitted.",
                "code": "investor_read_only",
            },
        )
        if origin and _is_allowed_origin(origin):
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
        return response

    return await call_next(request)


# ── RBAC enforcement (defense-in-depth) ───────────────────────────────────
# A SECOND, request-time enforcement layer built on the central policy registry
# (api/services/rbac_policy.py), sitting ON TOP of the existing per-route gates
# (Depends(require_roles(...)), router-level deps, inline handler checks) which
# all remain in place. Registered AFTER CORS / investor-block so those still run;
# fail-open + fail-soft so it can never change an endpoint's effective access:
#   * only /api/v1/* is considered; openapi/docs + non-/api/v1 skipped
#   * un-catalogued route -> ALLOW + warn (the coverage-lock test guarantees
#     completeness, so a miss is a new/dynamic route the route's own gate covers)
#   * PUBLIC -> allow; no/invalid/expired token -> PASS THROUGH so the route's
#     own get_current_user returns the canonical 401 (error shape unchanged)
#   * valid token failing check_access -> 403 (same answer the route gate gives)
# See api/middleware/rbac_enforcement.py for the full contract + reasoning.
from .middleware.rbac_enforcement import rbac_enforcement_middleware

app.middleware("http")(rbac_enforcement_middleware)


# ── Activity-audit ("Audit Everything") ───────────────────────────────────
# A request-time middleware that writes ONE append-only, hash-chained
# audit_logs row after every SUCCESSFUL authenticated MUTATING request under
# /api/v1/* (POST/PUT/PATCH/DELETE), so EVERY mutation -- now and future,
# whether or not its handler also emits a rich domain audit row -- reaches the
# SUPERADMIN Activity Log (GET /settings/audit-logs, the same trail JARVIS
# reads). The owner reported clinic/Rx, customer-create and mobile-edit actions
# were missing; this guarantees baseline coverage structurally. FAIL-SOFT: a
# logging failure never blocks, errors, or alters the real request. See
# api/middleware/audit_activity.py for the full contract.
from .middleware.audit_activity import audit_activity_middleware

app.middleware("http")(audit_activity_middleware)


# HTTPException handler - handles authentication and validation errors
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Handle HTTP exceptions (like 401 Unauthorized, 403 Forbidden, etc.)"""
    logger.warning(f"HTTP Exception {exc.status_code}: {exc.detail}")

    # Create response with proper content
    response = JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    # Add CORS headers to the error response so browser doesn't treat it as CORS error
    origin = request.headers.get("origin")
    if origin and _is_allowed_origin(origin):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"

    # Re-apply WWW-Authenticate header if present in original exception
    if exc.headers:
        for header_name, header_value in exc.headers.items():
            response.headers[header_name] = header_value

    return response


# Global exception handler for unexpected errors
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(
        f"Unhandled exception on {request.method} {request.url.path}: {exc}",
        exc_info=True,
    )
    response = JSONResponse(
        status_code=500, content={"detail": "Internal server error"}
    )

    # Add CORS headers to error responses
    origin = request.headers.get("origin")
    if origin and _is_allowed_origin(origin):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"

    return response


# Health check endpoint
# Audit Run #3 noted uptime monitors pointing at /api/v1/health silently
# 404ing. Expose the same handler at both paths so external probes work
# regardless of which one they've been configured to hit.
@app.get("/health", tags=["Health"])
@app.get("/api/v1/health", tags=["Health"])
async def health_check():
    import os

    db_status = (
        "connected" if DATABASE_AVAILABLE and get_db().is_connected else "disconnected"
    )
    # Expose the active GST pricing mode + build SHA so the frontend can read
    # the mode at RUNTIME (Vite bakes build-time env, so a flag flip wouldn't
    # reach the FE otherwise) and so a FE/BE version skew is detectable.
    try:
        from .services.gst_rates import gst_pricing_mode

        pricing_mode = gst_pricing_mode()
    except Exception:  # noqa: BLE001
        pricing_mode = "inclusive"
    build_sha = (
        os.environ.get("RAILWAY_GIT_COMMIT_SHA")
        or os.environ.get("RAILWAY_DEPLOYMENT_ID")
        or "dev"
    )[:12]
    return {
        "status": "healthy",
        "service": "IMS 2.0 API",
        "version": "2.0.0",
        "database": db_status,
        "pricing_mode": pricing_mode,
        "build_sha": build_sha,
    }


# Root endpoint
@app.get("/", tags=["Root"])
async def root():
    return {
        "message": "IMS 2.0 - Retail Operating System API",
        "docs": "/docs",
        "health": "/health",
    }


# Database seed endpoint (for initial setup)
@app.post("/api/v1/admin/seed-database", tags=["Admin"])
async def seed_database(secret: str = "", force: str = ""):
    """
    Seed the database with Better Vision Opticals store data.
    Requires secret key. Only inserts into empty collections.
    Use force=users to drop and re-seed users (fixes password hashes).
    """
    # SECURITY: the seed secret MUST come from the environment. There is NO
    # hardcoded fallback — this is a public repo, and a known secret on an
    # endpoint that can `force`-drop the users collection (re-seeding accounts
    # with known passwords) is a full unauthenticated account-takeover path.
    # If SEED_SECRET is unset the endpoint is disabled. Constant-time compare.
    import hmac as _hmac

    expected = os.getenv("SEED_SECRET")
    if not expected:
        raise HTTPException(
            status_code=403,
            detail="DB seeding is disabled (SEED_SECRET not configured on the server).",
        )
    if not secret or not _hmac.compare_digest(secret, expected):
        raise HTTPException(status_code=403, detail="Invalid seed secret")

    if not DATABASE_AVAILABLE:
        return {"status": "error", "message": "Database not connected"}

    db = get_db()
    if not db or not db.is_connected:
        return {"status": "error", "message": "Database not connected"}

    try:
        import sys as _sys, os as _os

        _sys.path.insert(
            0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
        )
        from database.seed_data import get_all_seed_data

        seed_data = get_all_seed_data()
        results = {}

        # Force reseed specific collections
        force_collections = [f.strip() for f in force.split(",") if f.strip()]

        for coll_name, documents in seed_data.items():
            collection = db.get_collection(coll_name)

            if coll_name in force_collections:
                collection.delete_many({})
                results[coll_name] = f"FORCE-DROPPED"

            existing = collection.count_documents({})
            if existing > 0:
                results[coll_name] = f"SKIPPED ({existing} existing)"
                continue
            if documents:
                collection.insert_many(documents)
                results[coll_name] = f"SEEDED ({len(documents)} docs)"
            else:
                results[coll_name] = "EMPTY"

        return {"status": "success", "message": "Database seeded", "results": results}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# Include routers
# Dashboard widgets FIRST — its exact paths must resolve before any
# domain router's /{id} catch-all could shadow them.
app.include_router(
    dashboard_widgets_router, prefix="/api/v1", tags=["Dashboard Widgets"]
)
app.include_router(auth_router, prefix="/api/v1/auth", tags=["Authentication"])
app.include_router(users_router, prefix="/api/v1/users", tags=["Users"])
app.include_router(stores_router, prefix="/api/v1/stores", tags=["Stores"])
app.include_router(products_router, prefix="/api/v1/products", tags=["Products"])
app.include_router(
    product_templates_router,
    prefix="/api/v1/product-templates",
    tags=["Product Templates"],
)
app.include_router(inventory_router, prefix="/api/v1/inventory", tags=["Inventory"])
app.include_router(customers_router, prefix="/api/v1/customers", tags=["Customers"])
app.include_router(crm_router, prefix="/api/v1/crm", tags=["CRM"])
app.include_router(orders_router, prefix="/api/v1/orders", tags=["Orders"])
app.include_router(
    prescriptions_router, prefix="/api/v1/prescriptions", tags=["Prescriptions"]
)
app.include_router(vendors_router, prefix="/api/v1/vendors", tags=["Vendors"])
app.include_router(
    vendor_returns_router, prefix="/api/v1/vendor-returns", tags=["Vendor Returns"]
)
app.include_router(returns_router, prefix="/api/v1/returns", tags=["Returns"])
app.include_router(shipping_router, prefix="/api/v1/shipping", tags=["Shipping"])
# Labels router carries its own /workshop/... and /print/... path prefixes so
# it mounts at the bare /api/v1 root (scan-advance + label payloads live under
# /api/v1/workshop/*, QZ signing under /api/v1/print/qz/*).
app.include_router(labels_router, prefix="/api/v1", tags=["Labels"])
app.include_router(tasks_router, prefix="/api/v1/tasks", tags=["Tasks"])
app.include_router(
    notifications_router, prefix="/api/v1/notifications", tags=["Notifications"]
)
app.include_router(expenses_router, prefix="/api/v1/expenses", tags=["Expenses"])
app.include_router(
    finance_router,
    prefix="/api/v1/finance",
    tags=["Finance"],
    dependencies=[Depends(require_roles(*_FINANCE_ROLES))],
)
app.include_router(
    hr_router,
    prefix="/api/v1/hr",
    tags=["HR"],
    dependencies=[Depends(require_roles(*_FINANCE_ROLES))],
)
app.include_router(workshop_router, prefix="/api/v1/workshop", tags=["Workshop"])
app.include_router(reports_router, prefix="/api/v1/reports", tags=["Reports"])
app.include_router(budgets_router, prefix="/api/v1/budgets", tags=["Budgets"])
app.include_router(settings_router, prefix="/api/v1/settings", tags=["Settings"])
app.include_router(clinical_router, prefix="/api/v1/clinical", tags=["Clinical"])
app.include_router(admin_router, prefix="/api/v1/admin", tags=["Admin"])
app.include_router(
    admin_catalog_router, prefix="/api/v1/admin", tags=["Admin · Catalog"]
)
app.include_router(
    admin_extras_router, prefix="/api/v1/admin", tags=["Admin · Discounts & System"]
)
app.include_router(handoffs_router, prefix="/api/v1/handoffs", tags=["Handoffs"])
app.include_router(entities_router, prefix="/api/v1/entities", tags=["Entities"])
# v2-2a: Display fixture / placement system (one row per physical fixture per
# store + one row per SKU x fixture combo with qty). Surfaces in the Display
# Layout tab (v2-2b) + the GRN Receive modal (v2-2c) + the Stock count sheet.
app.include_router(
    display_fixtures_router,
    prefix="/api/v1/display-fixtures",
    tags=["Display Fixtures"],
)
app.include_router(
    display_placements_router,
    prefix="/api/v1/display-placements",
    tags=["Display Placements"],
)
# Branch B' sub-PR 1: lens catalog backend foundation. Three new routers
# build the typed lens-line + per-power stock model that replaces the old
# inventory.py::get_lens_power_grid endpoint (which B'2 swaps over).
# Atomic reserve/commit/release endpoints under /api/v1/lens-stock are
# wired to POS Step 6 + Workshop dispatch in B'4. Enums under
# /api/v1/lens-enums are SUPERADMIN/ADMIN-only.
app.include_router(
    lens_catalog_router,
    prefix="/api/v1/lens-catalog",
    tags=["Lens Catalog"],
)
app.include_router(
    lens_stock_router,
    prefix="/api/v1/lens-stock",
    tags=["Lens Stock"],
)
app.include_router(
    lens_enums_router,
    prefix="/api/v1/lens-enums",
    tags=["Lens Enums"],
)
app.include_router(
    transfers_router, prefix="/api/v1/transfers", tags=["Stock Transfers"]
)
app.include_router(catalog_router, prefix="/api/v1/catalog", tags=["Catalog"])
app.include_router(
    catalog_autopilot_router,
    prefix="/api/v1/catalog-autopilot",
    tags=["Catalog Autopilot"],
)
app.include_router(jarvis_router, prefix="/api/v1/jarvis", tags=["JARVIS"])
app.include_router(analytics_router, prefix="/api/v1/analytics", tags=["Analytics"])
app.include_router(follow_ups_router, prefix="/api/v1/follow-ups", tags=["Follow-ups"])
app.include_router(
    payroll_router,
    prefix="/api/v1/payroll",
    tags=["Payroll"],
    dependencies=[Depends(require_roles(*_FINANCE_ROLES))],
)
app.include_router(marketing_router, prefix="/api/v1/marketing", tags=["Marketing"])
app.include_router(
    analytics_v2_router, prefix="/api/v1/analytics-v2", tags=["Analytics V2"]
)
app.include_router(agents_router, prefix="/api/v1/jarvis", tags=["Agents"])
# AI change-proposal workflow (SYSTEM_INTENT section 8) - SUPERADMIN-only.
# Mounted at the same /api/v1/jarvis prefix; its paths (/proposals*) don't
# collide with agents_router's (/agents*).
app.include_router(proposals_router, prefix="/api/v1/jarvis", tags=["AI Proposals"])
app.include_router(walkouts_router, prefix="/api/v1/walkouts", tags=["Walkouts"])
app.include_router(
    points_router, prefix="/api/v1/incentive/points", tags=["Daily Points"]
)
app.include_router(payout_router, prefix="/api/v1/payout", tags=["Payout"])
app.include_router(webhooks_router, prefix="/api/v1/webhooks", tags=["Webhooks"])
app.include_router(loyalty_router, prefix="/api/v1/loyalty", tags=["Loyalty"])
app.include_router(vouchers_router, prefix="/api/v1/vouchers", tags=["Vouchers"])
# Vendor portal — PUBLIC, token-auth via path param. Mounted OUTSIDE the
# JWT-protected family of routers because external lens labs hit this
# without an IMS user account.
app.include_router(
    vendor_portal_router, prefix="/api/v1/vendor-portal", tags=["Vendor Portal"]
)
# Customer self-service portal — PUBLIC. Order tracking is a tokenized link
# (no login); Rx viewing is OTP-gated (medical data). Mounted OUTSIDE the
# JWT-protected family because real customers hit this without an IMS account.
app.include_router(portal_router, prefix="/api/v1/portal", tags=["Customer Portal"])
# TechCherry one-time migration — SUPERADMIN-only batch upsert endpoint.
# Mounted under /admin/techcherry so it sits in the operator namespace
# without inheriting the broader admin router (which is ADMIN+SUPERADMIN;
# this needs strictly SUPERADMIN). Routes: POST /import, GET /status.
app.include_router(
    techcherry_import_router,
    prefix="/api/v1/admin/techcherry",
    tags=["TechCherry Migration"],
)
# Per-entity print template content overrides (v2-3). SUPERADMIN/ADMIN can
# edit; any authenticated user can read so the renderer resolves overrides.
app.include_router(
    print_overrides_router,
    prefix="/api/v1/print-overrides",
    tags=["Print Overrides"],
)
# Audit trail integrity (SYSTEM_INTENT 10). READ-ONLY, SUPERADMIN-only:
# GET /api/v1/audit/verify walks the tamper-evident hash-chain. The audit
# collection is append-only -- this router exposes no mutation route.
app.include_router(
    audit_router,
    prefix="/api/v1/audit",
    tags=["Audit"],
)
# Online Store module (BVI Phase 1 foundation). The Shopify PIM folded into IMS
# as ONE app -- see docs/reference/BVI_MERGE_PLAN.md. Phase 1 mounts the module
# skeleton + a stub GET /online-store/summary. Each route is role-gated INSIDE
# the router (require_roles -> SUPERADMIN/ADMIN/CATALOG_MANAGER/DESIGN_MANAGER),
# so a plain mount here; no router-level dependency needed.
app.include_router(
    online_store_router,
    prefix="/api/v1/online-store",
    tags=["Online Store"],
)
# Collections sub-module (BVI Phase 2, FLAGSHIP #1). ecom_collections CRUD +
# manual/smart membership + smart-rule resolver -- PUSH-DARK (Mongo only, no
# Shopify writes; that is Phase 5). Mounted at /api/v1/online-store/collections.
# Each route is role-gated INSIDE the router (require_roles -> SUPERADMIN /
# ADMIN / CATALOG_MANAGER / DESIGN_MANAGER) + catalogued in rbac_policy.POLICY.
app.include_router(
    online_store_collections_router,
    prefix="/api/v1/online-store/collections",
    tags=["Online Store - Collections"],
)
# Menus / Mega-menu sub-module (BVI Phase 3, FLAGSHIP #2). ecom_menus CRUD + an
# embedded recursive item-tree editor (add/move/remove/reorder nodes) -- PUSH-DARK
# (Mongo only, no Shopify writes; the menuUpdate push is Phase 5). Mounted at
# /api/v1/online-store/menus. Each route is role-gated INSIDE the router
# (require_roles -> SUPERADMIN / ADMIN / CATALOG_MANAGER / DESIGN_MANAGER) +
# catalogued in rbac_policy.POLICY.
app.include_router(
    online_store_menus_router,
    prefix="/api/v1/online-store/menus",
    tags=["Online Store - Menus"],
)
# Image Design Queue sub-module (BVI Phase 4, FLAGSHIP #3). product_images CRUD +
# the RAW->EDITED->APPROVED design lifecycle (assign / status / attach-edited) --
# PUSH-DARK (Mongo only, no Shopify image push; that is Phase 5). Mounted at
# /api/v1/online-store/images. Each route is role-gated INSIDE the router
# (require_roles -> SUPERADMIN / ADMIN / CATALOG_MANAGER / DESIGN_MANAGER) +
# catalogued in rbac_policy.POLICY. APPROVE writes a chained audit_logs row.
app.include_router(
    online_store_images_router,
    prefix="/api/v1/online-store/images",
    tags=["Online Store - Images"],
)
# Shopify PUSH sub-module (BVI Phase 5). The IMS -> Shopify GraphQL push for
# product / collection / menu / image + a status surface -- BUILT DARK: every
# push is SIMULATED (dry-run, no network) unless IMS_SHOPIFY_WRITES on AND
# DISPATCH_MODE=live AND creds present (per #262 BVI is the single writer until
# the Phase-6 cutover). Mounted at /api/v1/online-store/push. Role-gated INSIDE
# the router to SUPERADMIN/ADMIN ONLY (integration-critical, narrower than the
# rest of the module) + catalogued in rbac_policy.POLICY. Every push writes a
# chained audit_logs row. See docs/reference/BVI_MERGE_PLAN.md Phase 5.
app.include_router(
    online_store_push_router,
    prefix="/api/v1/online-store/push",
    tags=["Online Store - Push"],
)


if __name__ == "__main__":
    import uvicorn

    # Bind 0.0.0.0 is intentional for container/Railway deployment.
    uvicorn.run(app, host="0.0.0.0", port=8000)  # nosec B104

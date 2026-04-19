"""
IMS 2.0 - FastAPI Main Application
===================================
Main entry point for the API server
"""

from fastapi import FastAPI, Request, HTTPException
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

# ── Sentry APM — error tracking & performance monitoring ────────────────
_sentry_dsn = os.getenv("SENTRY_DSN")
if _sentry_dsn:
    try:
        import sentry_sdk
        sentry_sdk.init(
            dsn=_sentry_dsn,
            environment=os.getenv("NODE_ENV", "development"),
            traces_sample_rate=float(os.getenv("SENTRY_TRACES_RATE", "0.2")),
            profiles_sample_rate=float(os.getenv("SENTRY_PROFILES_RATE", "0.1")),
            send_default_pii=False,  # don't send user IPs/emails to Sentry
        )
        logger.info("[APM] Sentry initialized")
    except Exception as e:
        logger.warning(f"[APM] Sentry init failed: {e}")

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
    tasks_router,
    expenses_router,
    finance_router,
    hr_router,
    workshop_router,
    reports_router,
    settings_router,
    clinical_router,
    admin_router,
    transfers_router,
    catalog_router,
    jarvis_router,
    analytics_router,
    billing_router,
    supply_chain_router,
    follow_ups_router,
    payroll_router,
    incentives_router,
    marketing_router,
    analytics_v2_router,
    agents_router,
)


# Lifespan context manager for startup/shutdown
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("[START] Starting IMS 2.0 API Server...")

    # ── Environment validation ──────────────────────────────────────────
    _jwt_key = os.getenv("JWT_SECRET_KEY", "")
    if not _jwt_key or _jwt_key in ("CHANGE_THIS_TO_A_RANDOM_SECRET_KEY_IN_PRODUCTION", "dev-secret-key-change-in-production"):
        logger.warning(
            "[SECURITY] JWT_SECRET_KEY is missing or using a default placeholder! "
            "Set a strong random secret via environment variable for production."
        )
    _mongo_url = os.getenv("MONGODB_URL") or os.getenv("MONGO_URL") or os.getenv("MONGO_HOST")
    if not _mongo_url:
        logger.warning("[CONFIG] No MongoDB connection configured (MONGODB_URL / MONGO_URL / MONGO_HOST). Running in stub mode.")
    _missing_recommended = [
        v for v in ("CORS_ORIGINS", "RATE_LIMIT_PER_MINUTE")
        if not os.getenv(v)
    ]
    if _missing_recommended:
        logger.info(f"[CONFIG] Optional env vars not set (using defaults): {', '.join(_missing_recommended)}")

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
        else:
            logger.warning("[WARN] Database not connected - running in mock mode")
    else:
        logger.info("[INFO] Running without database (stub mode)")

    # Initialize Agent System (JARVIS Agents)
    _scheduler = None
    try:
        from agents.config import AgentConfigManager
        from agents.registry import initialize_registry, AGENT_REGISTRY
        from agents.scheduler import AgentScheduler

        if DATABASE_AVAILABLE:
            from database.connection import get_seeded_db
            db = get_seeded_db()
        else:
            db = None

        # Seed default agent configs into MongoDB
        config_mgr = AgentConfigManager(db=db)
        config_mgr.seed_configs()
        logger.info("[AGENTS] Agent configs seeded")

        # Initialize agent registry (creates CORTEX + SENTINEL instances)
        initialize_registry(db=db)
        logger.info(f"[AGENTS] Registry initialized — {len(AGENT_REGISTRY)} agents")

        # Start the background scheduler
        _scheduler = AgentScheduler(db=db)
        await _scheduler.start(AGENT_REGISTRY)
        logger.info("[AGENTS] Scheduler started")

        # Store scheduler globally so the toggle endpoint can pause/resume
        import agents as _agents_pkg
        _agents_pkg._scheduler_instance = _scheduler

    except Exception as e:
        logger.warning(f"[AGENTS] Agent system init failed (non-fatal): {e}")

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

    client_ip = (
        request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or (request.client.host if request.client else "unknown")
    )
    now = time.time()
    cutoff = now - _GLOBAL_RATE_WINDOW

    # Clean old entries and count
    _request_log[client_ip] = [t for t in _request_log[client_ip] if t > cutoff]
    if len(_request_log[client_ip]) >= _GLOBAL_RATE_LIMIT:
        return JSONResponse(
            status_code=429,
            content={"detail": f"Rate limit exceeded. Max {_GLOBAL_RATE_LIMIT} requests per minute."},
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
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(self)"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    # CSP: allow self + Vercel preview domains for the frontend
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; "
        "connect-src 'self' https://*.vercel.app https://*.up.railway.app"
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
        response.headers["Vary"] = (existing_vary + ", Origin").strip(", ") if existing_vary else "Origin"
    return response


# Request timing middleware
@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(process_time)
    return response


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
    logger.error(f"Unhandled exception on {request.method} {request.url.path}: {exc}", exc_info=True)
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
@app.get("/health", tags=["Health"])
async def health_check():
    db_status = (
        "connected" if DATABASE_AVAILABLE and get_db().is_connected else "disconnected"
    )
    return {
        "status": "healthy",
        "service": "IMS 2.0 API",
        "version": "2.0.0",
        "database": db_status,
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
    if secret != "bv-seed-2026":
        raise HTTPException(status_code=403, detail="Invalid seed secret")

    if not DATABASE_AVAILABLE:
        return {"status": "error", "message": "Database not connected"}

    db = get_db()
    if not db or not db.is_connected:
        return {"status": "error", "message": "Database not connected"}

    try:
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
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
app.include_router(auth_router, prefix="/api/v1/auth", tags=["Authentication"])
app.include_router(users_router, prefix="/api/v1/users", tags=["Users"])
app.include_router(stores_router, prefix="/api/v1/stores", tags=["Stores"])
app.include_router(products_router, prefix="/api/v1/products", tags=["Products"])
app.include_router(inventory_router, prefix="/api/v1/inventory", tags=["Inventory"])
app.include_router(customers_router, prefix="/api/v1/customers", tags=["Customers"])
app.include_router(crm_router, prefix="/api/v1/crm", tags=["CRM"])
app.include_router(orders_router, prefix="/api/v1/orders", tags=["Orders"])
app.include_router(
    prescriptions_router, prefix="/api/v1/prescriptions", tags=["Prescriptions"]
)
app.include_router(vendors_router, prefix="/api/v1/vendors", tags=["Vendors"])
app.include_router(vendor_returns_router, prefix="/api/v1/vendor-returns", tags=["Vendor Returns"])
app.include_router(tasks_router, prefix="/api/v1/tasks", tags=["Tasks"])
app.include_router(expenses_router, prefix="/api/v1/expenses", tags=["Expenses"])
app.include_router(finance_router, prefix="/api/v1/finance", tags=["Finance"])
app.include_router(hr_router, prefix="/api/v1/hr", tags=["HR"])
app.include_router(workshop_router, prefix="/api/v1/workshop", tags=["Workshop"])
app.include_router(reports_router, prefix="/api/v1/reports", tags=["Reports"])
app.include_router(settings_router, prefix="/api/v1/settings", tags=["Settings"])
app.include_router(clinical_router, prefix="/api/v1/clinical", tags=["Clinical"])
app.include_router(admin_router, prefix="/api/v1/admin", tags=["Admin"])
app.include_router(
    transfers_router, prefix="/api/v1/transfers", tags=["Stock Transfers"]
)
app.include_router(catalog_router, prefix="/api/v1/catalog", tags=["Catalog"])
app.include_router(jarvis_router, prefix="/api/v1/jarvis", tags=["JARVIS"])
app.include_router(analytics_router, prefix="/api/v1/analytics", tags=["Analytics"])
app.include_router(billing_router, prefix="/api/v1/billing", tags=["Billing"])
app.include_router(
    supply_chain_router, prefix="/api/v1/supply-chain", tags=["Supply Chain"]
)
app.include_router(incentives_router, prefix="/api/v1/incentives", tags=["Incentives"])
app.include_router(follow_ups_router, prefix="/api/v1/follow-ups", tags=["Follow-ups"])
app.include_router(payroll_router, prefix="/api/v1/payroll", tags=["Payroll"])
app.include_router(marketing_router, prefix="/api/v1/marketing", tags=["Marketing"])
app.include_router(analytics_v2_router, prefix="/api/v1/analytics-v2", tags=["Analytics V2"])
app.include_router(agents_router, prefix="/api/v1/jarvis", tags=["Agents"])


if __name__ == "__main__":
    import uvicorn

    # Bind 0.0.0.0 is intentional for container/Railway deployment.
    uvicorn.run(app, host="0.0.0.0", port=8000)  # nosec B104

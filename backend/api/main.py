"""
IMS 2.0 - FastAPI Main Application
===================================
Main entry point for the API server
"""

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import time
import logging
import os
import sys

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
    tasks_router,
    expenses_router,
    hr_router,
    workshop_router,
    reports_router,
    settings_router,
    clinical_router,
    admin_router,
    shopify_router,
    transfers_router,
    catalog_router,
    jarvis_router,
    analytics_router,
    billing_router,
)


# Lifespan context manager for startup/shutdown
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("🚀 Starting IMS 2.0 API Server...")

    # Initialize database connection
    if DATABASE_AVAILABLE:
        mongo_url = os.getenv("MONGODB_URL") or os.getenv("MONGO_URL")
        if mongo_url:
            config = DatabaseConfig.from_uri(mongo_url, database="ims_2_0")
        else:
            config = DatabaseConfig.from_env()

        if init_db(config):
            logger.info("✅ Database connection established")
        else:
            logger.warning("⚠️ Database not connected - running in mock mode")
    else:
        logger.info("📦 Running without database (stub mode)")

    yield

    # Shutdown
    logger.info("🛑 Shutting down IMS 2.0 API Server...")
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

# Add CORS middleware FIRST (before other middlewares)
# In FastAPI, middlewares are applied in reverse order of definition
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["*"],  # Allow all headers including Authorization
    expose_headers=["*"],  # Expose all response headers
)


# Custom CORS handler for dynamic origin validation (additional safety layer)
@app.middleware("http")
async def dynamic_cors_handler(request: Request, call_next):
    origin = request.headers.get("origin")

    # Log CORS requests for debugging
    if request.method == "OPTIONS":
        logger.debug(f"CORS preflight request from origin: {origin}")
        if origin:
            logger.debug(f"Origin allowed: {_is_allowed_origin(origin)}")

    # Process the request
    response = await call_next(request)

    # Ensure CORS headers are present for all responses
    origin = request.headers.get("origin")
    if origin and _is_allowed_origin(origin):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"

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
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


# Global exception handler for unexpected errors
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500, content={"detail": "Internal server error", "error": str(exc)}
    )


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
app.include_router(tasks_router, prefix="/api/v1/tasks", tags=["Tasks"])
app.include_router(expenses_router, prefix="/api/v1/expenses", tags=["Expenses"])
app.include_router(hr_router, prefix="/api/v1/hr", tags=["HR"])
app.include_router(workshop_router, prefix="/api/v1/workshop", tags=["Workshop"])
app.include_router(reports_router, prefix="/api/v1/reports", tags=["Reports"])
app.include_router(settings_router, prefix="/api/v1/settings", tags=["Settings"])
app.include_router(clinical_router, prefix="/api/v1/clinical", tags=["Clinical"])
app.include_router(admin_router, prefix="/api/v1/admin", tags=["Admin"])
app.include_router(shopify_router, prefix="/api/v1/shopify", tags=["Shopify"])
app.include_router(
    transfers_router, prefix="/api/v1/transfers", tags=["Stock Transfers"]
)
app.include_router(catalog_router, prefix="/api/v1/catalog", tags=["Catalog"])
app.include_router(jarvis_router, prefix="/api/v1/jarvis", tags=["JARVIS"])
app.include_router(analytics_router, prefix="/api/v1/analytics", tags=["Analytics"])
app.include_router(billing_router, prefix="/api/v1/billing", tags=["Billing"])


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)

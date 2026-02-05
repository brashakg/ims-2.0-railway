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
    transfers_router
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
    lifespan=lifespan
)

# CORS Middleware - Get allowed origins from environment
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Request timing middleware
@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(process_time)
    return response


# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "error": str(exc)}
    )


# Health check endpoint
@app.get("/health", tags=["Health"])
async def health_check():
    db_status = "connected" if DATABASE_AVAILABLE and get_db().is_connected else "disconnected"
    return {
        "status": "healthy",
        "service": "IMS 2.0 API",
        "version": "2.0.0",
        "database": db_status
    }


# Root endpoint
@app.get("/", tags=["Root"])
async def root():
    return {
        "message": "IMS 2.0 - Retail Operating System API",
        "docs": "/docs",
        "health": "/health"
    }


# Include routers
app.include_router(auth_router, prefix="/api/v1/auth", tags=["Authentication"])
app.include_router(users_router, prefix="/api/v1/users", tags=["Users"])
app.include_router(stores_router, prefix="/api/v1/stores", tags=["Stores"])
app.include_router(products_router, prefix="/api/v1/products", tags=["Products"])
app.include_router(inventory_router, prefix="/api/v1/inventory", tags=["Inventory"])
app.include_router(customers_router, prefix="/api/v1/customers", tags=["Customers"])
app.include_router(orders_router, prefix="/api/v1/orders", tags=["Orders"])
app.include_router(prescriptions_router, prefix="/api/v1/prescriptions", tags=["Prescriptions"])
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
app.include_router(transfers_router, prefix="/api/v1/transfers", tags=["Stock Transfers"])


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

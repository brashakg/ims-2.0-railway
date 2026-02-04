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

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
    settings_router
)

# Lifespan context manager for startup/shutdown
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("🚀 Starting IMS 2.0 API Server...")
    # Initialize database connection here if needed
    yield
    # Shutdown
    logger.info("🛑 Shutting down IMS 2.0 API Server...")


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
    return {
        "status": "healthy",
        "service": "IMS 2.0 API",
        "version": "2.0.0"
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

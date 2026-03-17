"""
IMS 2.0 - API Dependencies
===========================
Dependency injection for repositories and services
"""

import os
import sys

# Add parent directory to path for database imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from database.connection import get_seeded_db
    from database.repositories.customer_repository import CustomerRepository
    from database.repositories.product_repository import (
        ProductRepository,
        StockRepository,
    )
    from database.repositories.order_repository import OrderRepository
    from database.repositories.user_repository import UserRepository
    from database.repositories.store_repository import StoreRepository
    from database.repositories.prescription_repository import PrescriptionRepository
    from database.repositories.task_repository import TaskRepository
    from database.repositories.workshop_repository import WorkshopJobRepository
    from database.repositories.expense_repository import (
        ExpenseRepository,
        AdvanceRepository,
    )
    from database.repositories.vendor_repository import (
        VendorRepository,
        PurchaseOrderRepository,
        GRNRepository,
    )
    from database.repositories.hr_repository import (
        AttendanceRepository,
        LeaveRepository,
        PayrollRepository,
    )
    from database.repositories.audit_repository import AuditRepository
    from database.repositories.clinical_repository import (
        EyeTestQueueRepository,
        EyeTestRepository,
    )

    DATABASE_AVAILABLE = True
except ImportError as e:
    print(f"⚠️ Database import error: {e}")
    DATABASE_AVAILABLE = False


def get_db():
    """Get database connection (with seeded fallback)"""
    if DATABASE_AVAILABLE:
        return get_seeded_db()
    return None


def validate_store_access(store_id: str, current_user: dict) -> str:
    """
    Validate that the current user has access to the requested store.
    Returns the validated store_id (or user's active_store_id if none provided).
    Raises HTTPException(403) if user doesn't have access.
    """
    from fastapi import HTTPException

    user_roles = current_user.get("roles", [])
    is_admin = any(r in user_roles for r in ["SUPERADMIN", "ADMIN"])

    # If no store_id provided, use user's active store
    if not store_id:
        return current_user.get("active_store_id")

    # Admins can access any store
    if is_admin:
        return store_id

    # Area managers can access stores in their region
    if "AREA_MANAGER" in user_roles:
        user_store_ids = current_user.get("store_ids", [])
        if store_id in user_store_ids:
            return store_id
        raise HTTPException(
            status_code=403,
            detail=f"No access to store {store_id}"
        )

    # Other roles: must be in their store_ids list
    user_store_ids = current_user.get("store_ids", [])
    if store_id not in user_store_ids:
        raise HTTPException(
            status_code=403,
            detail=f"No access to store {store_id}"
        )
    return store_id


def get_customer_repository():
    """Get CustomerRepository instance"""
    db = get_db()
    if db is not None and db.is_connected:
        return CustomerRepository(db.customers)
    return None


def get_product_repository():
    """Get ProductRepository instance"""
    db = get_db()
    if db is not None and db.is_connected:
        return ProductRepository(db.products)
    return None


def get_stock_repository():
    """Get StockRepository instance"""
    db = get_db()
    if db is not None and db.is_connected:
        return StockRepository(db.stock_units)
    return None


def get_order_repository():
    """Get OrderRepository instance"""
    db = get_db()
    if db is not None and db.is_connected:
        return OrderRepository(db.orders)
    return None


def get_user_repository():
    """Get UserRepository instance"""
    db = get_db()
    if db is not None and db.is_connected:
        return UserRepository(db.users)
    return None


def get_store_repository():
    """Get StoreRepository instance"""
    db = get_db()
    if db is not None and db.is_connected:
        return StoreRepository(db.stores)
    return None


def get_prescription_repository():
    """Get PrescriptionRepository instance"""
    db = get_db()
    if db is not None and db.is_connected:
        return PrescriptionRepository(db.prescriptions)
    return None


def get_task_repository():
    """Get TaskRepository instance"""
    db = get_db()
    if db is not None and db.is_connected:
        return TaskRepository(db.tasks)
    return None


def get_workshop_repository():
    """Get WorkshopJobRepository instance"""
    db = get_db()
    if db is not None and db.is_connected:
        return WorkshopJobRepository(db.get_collection("workshop_jobs"))
    return None


def get_expense_repository():
    """Get ExpenseRepository instance"""
    db = get_db()
    if db is not None and db.is_connected:
        return ExpenseRepository(db.expenses)
    return None


def get_advance_repository():
    """Get AdvanceRepository instance"""
    db = get_db()
    if db is not None and db.is_connected:
        return AdvanceRepository(db.advances)
    return None


def get_vendor_repository():
    """Get VendorRepository instance"""
    db = get_db()
    if db is not None and db.is_connected:
        return VendorRepository(db.vendors)
    return None


def get_purchase_order_repository():
    """Get PurchaseOrderRepository instance"""
    db = get_db()
    if db is not None and db.is_connected:
        return PurchaseOrderRepository(db.purchase_orders)
    return None


def get_grn_repository():
    """Get GRNRepository instance"""
    db = get_db()
    if db is not None and db.is_connected:
        return GRNRepository(db.grns)
    return None


def get_attendance_repository():
    """Get AttendanceRepository instance"""
    db = get_db()
    if db is not None and db.is_connected:
        return AttendanceRepository(db.get_collection("attendance"))
    return None


def get_leave_repository():
    """Get LeaveRepository instance"""
    db = get_db()
    if db is not None and db.is_connected:
        return LeaveRepository(db.get_collection("leaves"))
    return None


def get_payroll_repository():
    """Get PayrollRepository instance"""
    db = get_db()
    if db is not None and db.is_connected:
        return PayrollRepository(db.get_collection("payroll"))
    return None


def get_audit_repository():
    """Get AuditRepository instance"""
    db = get_db()
    if db is not None and db.is_connected:
        return AuditRepository(db.audit_logs)
    return None


def get_eye_test_queue_repository():
    """Get EyeTestQueueRepository instance"""
    db = get_db()
    if db is not None and db.is_connected:
        return EyeTestQueueRepository(db.eye_test_queue)
    return None


def get_eye_test_repository():
    """Get EyeTestRepository instance"""
    db = get_db()
    if db is not None and db.is_connected:
        return EyeTestRepository(db.eye_tests)
    return None

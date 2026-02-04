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
    from database.connection import get_db
    from database.repositories.customer_repository import CustomerRepository
    from database.repositories.product_repository import ProductRepository, StockRepository
    from database.repositories.order_repository import OrderRepository
    from database.repositories.user_repository import UserRepository
    from database.repositories.store_repository import StoreRepository
    from database.repositories.prescription_repository import PrescriptionRepository
    from database.repositories.task_repository import TaskRepository
    from database.repositories.workshop_repository import WorkshopJobRepository
    from database.repositories.expense_repository import ExpenseRepository, AdvanceRepository
    from database.repositories.vendor_repository import VendorRepository, PurchaseOrderRepository, GRNRepository
    from database.repositories.hr_repository import AttendanceRepository, LeaveRepository, PayrollRepository
    from database.repositories.audit_repository import AuditRepository
    DATABASE_AVAILABLE = True
except ImportError:
    DATABASE_AVAILABLE = False


def get_customer_repository():
    """Get CustomerRepository instance"""
    if DATABASE_AVAILABLE:
        db = get_db()
        if db.is_connected:
            return CustomerRepository(db.customers)
    return None


def get_product_repository():
    """Get ProductRepository instance"""
    if DATABASE_AVAILABLE:
        db = get_db()
        if db.is_connected:
            return ProductRepository(db.products)
    return None


def get_stock_repository():
    """Get StockRepository instance"""
    if DATABASE_AVAILABLE:
        db = get_db()
        if db.is_connected:
            return StockRepository(db.stock_units)
    return None


def get_order_repository():
    """Get OrderRepository instance"""
    if DATABASE_AVAILABLE:
        db = get_db()
        if db.is_connected:
            return OrderRepository(db.orders)
    return None


def get_user_repository():
    """Get UserRepository instance"""
    if DATABASE_AVAILABLE:
        db = get_db()
        if db.is_connected:
            return UserRepository(db.users)
    return None


def get_store_repository():
    """Get StoreRepository instance"""
    if DATABASE_AVAILABLE:
        db = get_db()
        if db.is_connected:
            return StoreRepository(db.stores)
    return None


def get_prescription_repository():
    """Get PrescriptionRepository instance"""
    if DATABASE_AVAILABLE:
        db = get_db()
        if db.is_connected:
            return PrescriptionRepository(db.prescriptions)
    return None


def get_task_repository():
    """Get TaskRepository instance"""
    if DATABASE_AVAILABLE:
        db = get_db()
        if db.is_connected:
            return TaskRepository(db.tasks)
    return None


def get_workshop_repository():
    """Get WorkshopJobRepository instance"""
    if DATABASE_AVAILABLE:
        db = get_db()
        if db.is_connected:
            return WorkshopJobRepository(db.get_collection("workshop_jobs"))
    return None


def get_expense_repository():
    """Get ExpenseRepository instance"""
    if DATABASE_AVAILABLE:
        db = get_db()
        if db.is_connected:
            return ExpenseRepository(db.expenses)
    return None


def get_advance_repository():
    """Get AdvanceRepository instance"""
    if DATABASE_AVAILABLE:
        db = get_db()
        if db.is_connected:
            return AdvanceRepository(db.advances)
    return None


def get_vendor_repository():
    """Get VendorRepository instance"""
    if DATABASE_AVAILABLE:
        db = get_db()
        if db.is_connected:
            return VendorRepository(db.vendors)
    return None


def get_purchase_order_repository():
    """Get PurchaseOrderRepository instance"""
    if DATABASE_AVAILABLE:
        db = get_db()
        if db.is_connected:
            return PurchaseOrderRepository(db.purchase_orders)
    return None


def get_grn_repository():
    """Get GRNRepository instance"""
    if DATABASE_AVAILABLE:
        db = get_db()
        if db.is_connected:
            return GRNRepository(db.grns)
    return None


def get_attendance_repository():
    """Get AttendanceRepository instance"""
    if DATABASE_AVAILABLE:
        db = get_db()
        if db.is_connected:
            return AttendanceRepository(db.get_collection("attendance"))
    return None


def get_leave_repository():
    """Get LeaveRepository instance"""
    if DATABASE_AVAILABLE:
        db = get_db()
        if db.is_connected:
            return LeaveRepository(db.get_collection("leaves"))
    return None


def get_payroll_repository():
    """Get PayrollRepository instance"""
    if DATABASE_AVAILABLE:
        db = get_db()
        if db.is_connected:
            return PayrollRepository(db.get_collection("payroll"))
    return None


def get_audit_repository():
    """Get AuditRepository instance"""
    if DATABASE_AVAILABLE:
        db = get_db()
        if db.is_connected:
            return AuditRepository(db.audit_logs)
    return None

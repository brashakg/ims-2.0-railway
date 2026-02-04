"""
IMS 2.0 - Repository Layer
===========================
Data access layer with CRUD operations
"""
from .base_repository import BaseRepository
from .user_repository import UserRepository
from .store_repository import StoreRepository
from .product_repository import ProductRepository, StockRepository
from .customer_repository import CustomerRepository
from .order_repository import OrderRepository
from .vendor_repository import VendorRepository, PurchaseOrderRepository, GRNRepository
from .task_repository import TaskRepository
from .prescription_repository import PrescriptionRepository
from .expense_repository import ExpenseRepository, AdvanceRepository
from .audit_repository import AuditRepository, NotificationRepository
from .hr_repository import AttendanceRepository, LeaveRepository, PayrollRepository
from .workshop_repository import WorkshopJobRepository

__all__ = [
    # Base
    'BaseRepository',
    
    # Core
    'UserRepository',
    'StoreRepository',
    'ProductRepository',
    'StockRepository',
    'CustomerRepository',
    'OrderRepository',
    
    # Clinical
    'PrescriptionRepository',
    
    # Vendor
    'VendorRepository',
    'PurchaseOrderRepository',
    'GRNRepository',
    
    # Tasks
    'TaskRepository',
    
    # Expense
    'ExpenseRepository',
    'AdvanceRepository',
    
    # Audit & Notifications
    'AuditRepository',
    'NotificationRepository',
    
    # HR
    'AttendanceRepository',
    'LeaveRepository',
    'PayrollRepository',
    
    # Workshop
    'WorkshopJobRepository'
]

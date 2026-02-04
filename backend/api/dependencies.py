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

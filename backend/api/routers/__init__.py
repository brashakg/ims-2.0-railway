"""
IMS 2.0 - API Routers
======================
"""
from .auth import router as auth_router
from .users import router as users_router
from .stores import router as stores_router
from .products import router as products_router
from .inventory import router as inventory_router
from .customers import router as customers_router
from .orders import router as orders_router
from .prescriptions import router as prescriptions_router
from .vendors import router as vendors_router
from .tasks import router as tasks_router
from .expenses import router as expenses_router
from .hr import router as hr_router
from .workshop import router as workshop_router
from .reports import router as reports_router
from .settings import router as settings_router

__all__ = [
    'auth_router',
    'users_router',
    'stores_router',
    'products_router',
    'inventory_router',
    'customers_router',
    'orders_router',
    'prescriptions_router',
    'vendors_router',
    'tasks_router',
    'expenses_router',
    'hr_router',
    'workshop_router',
    'reports_router',
    'settings_router'
]

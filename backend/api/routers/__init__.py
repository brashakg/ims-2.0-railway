"""
IMS 2.0 - API Routers
======================
"""

from .auth import router as auth_router
from .dashboard_widgets import router as dashboard_widgets_router
from .users import router as users_router
from .stores import router as stores_router
from .products import router as products_router
from .inventory import router as inventory_router
from .customers import router as customers_router
from .orders import router as orders_router
from .prescriptions import router as prescriptions_router
from .vendors import router as vendors_router
from .vendor_returns import router as vendor_returns_router
from .tasks import router as tasks_router
from .expenses import router as expenses_router
from .finance import router as finance_router
from .hr import router as hr_router
from .workshop import router as workshop_router
from .reports import router as reports_router
from .settings import router as settings_router
from .clinical import router as clinical_router
from .admin import router as admin_router
from .admin_catalog import router as admin_catalog_router
from .admin_extras import router as admin_extras_router
from .handoffs import router as handoffs_router
from .transfers import router as transfers_router
from .catalog import router as catalog_router
from .jarvis import router as jarvis_router
from .analytics import router as analytics_router
from .billing import router as billing_router
from .crm import router as crm_router
from .supply_chain import router as supply_chain_router
from .follow_ups import router as follow_ups_router
from .payroll import router as payroll_router
from .marketing import router as marketing_router
from .analytics_v2 import router as analytics_v2_router
from .agents import router as agents_router
from .walkouts import router as walkouts_router
from .points import router as points_router
from .payout import router as payout_router
from .webhooks import router as webhooks_router
from .loyalty import router as loyalty_router
from .vendor_portal import router as vendor_portal_router
from .techcherry_import import router as techcherry_import_router
from .vouchers import router as vouchers_router
from .entities import router as entities_router
from .notifications import router as notifications_router

__all__ = [
    "auth_router",
    "dashboard_widgets_router",
    "users_router",
    "stores_router",
    "products_router",
    "inventory_router",
    "customers_router",
    "crm_router",
    "orders_router",
    "prescriptions_router",
    "vendors_router",
    "vendor_returns_router",
    "tasks_router",
    "expenses_router",
    "finance_router",
    "hr_router",
    "workshop_router",
    "reports_router",
    "settings_router",
    "clinical_router",
    "admin_router",
    "admin_catalog_router",
    "admin_extras_router",
    "handoffs_router",
    "transfers_router",
    "catalog_router",
    "jarvis_router",
    "analytics_router",
    "billing_router",
    "supply_chain_router",
    "follow_ups_router",
    "payroll_router",
    "marketing_router",
    "analytics_v2_router",
    "agents_router",
    "walkouts_router",
    "points_router",
    "payout_router",
    "webhooks_router",
    "loyalty_router",
    "vendor_portal_router",
    "techcherry_import_router",
    "vouchers_router",
    "entities_router",
    "notifications_router",
]

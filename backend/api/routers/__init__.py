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
from .returns import router as returns_router
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
from .catalog_autopilot import router as catalog_autopilot_router
from .jarvis import router as jarvis_router
from .analytics import router as analytics_router
from .crm import router as crm_router
from .follow_ups import router as follow_ups_router
from .payroll import router as payroll_router
from .marketing import router as marketing_router
from .analytics_v2 import router as analytics_v2_router
from .agents import router as agents_router
from .proposals import router as proposals_router
from .walkouts import router as walkouts_router
from .points import router as points_router
from .payout import router as payout_router
from .webhooks import router as webhooks_router
from .loyalty import router as loyalty_router
from .vendor_portal import router as vendor_portal_router
from .portal import router as portal_router
from .techcherry_import import router as techcherry_import_router
from .vouchers import router as vouchers_router
from .entities import router as entities_router
from .notifications import router as notifications_router
from .shipping import router as shipping_router
from .labels import router as labels_router
from .display_fixtures import router as display_fixtures_router
from .display_placements import router as display_placements_router
from .print_overrides import router as print_overrides_router
from .lens_catalog import router as lens_catalog_router
from .lens_stock import router as lens_stock_router
from .lens_enums import router as lens_enums_router
from .product_templates import router as product_templates_router
from .audit import router as audit_router
from .budgets import router as budgets_router
from .online_store import router as online_store_router
from .online_store_collections import router as online_store_collections_router

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
    "returns_router",
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
    "catalog_autopilot_router",
    "jarvis_router",
    "analytics_router",
    "follow_ups_router",
    "payroll_router",
    "marketing_router",
    "analytics_v2_router",
    "agents_router",
    "proposals_router",
    "walkouts_router",
    "points_router",
    "payout_router",
    "webhooks_router",
    "loyalty_router",
    "vendor_portal_router",
    "portal_router",
    "techcherry_import_router",
    "vouchers_router",
    "entities_router",
    "notifications_router",
    "shipping_router",
    "labels_router",
    "display_fixtures_router",
    "display_placements_router",
    "print_overrides_router",
    "lens_catalog_router",
    "lens_stock_router",
    "lens_enums_router",
    "product_templates_router",
    "audit_router",
    "budgets_router",
    "online_store_router",
]

"""
IMS 2.0 - API Routers
======================
"""

from .auth import router as auth_router
from .dashboard_widgets import router as dashboard_widgets_router
from .users import router as users_router
from .stores import router as stores_router
from .products import router as products_router
from .product_master import router as product_master_router
from .inventory import router as inventory_router
from .customers import router as customers_router
from .orders import router as orders_router
from .prescriptions import router as prescriptions_router
from .vendors import router as vendors_router
from .purchase_invoices import router as purchase_invoices_router
from .purchase_recon import router as purchase_recon_router
from .vendor_returns import router as vendor_returns_router
from .vendor_rma import router as vendor_rma_router
from .rtv_debit_notes import router as rtv_debit_notes_router
from .returns import router as returns_router
from .tasks import router as tasks_router
from .expenses import router as expenses_router
from .finance import router as finance_router
from .finance import ticker_router as finance_ticker_router
from .reconciliation import router as reconciliation_router
from .till import router as till_router
from .bank_reconciliation import router as bank_reconciliation_router
from .non_adapt import router as non_adapt_router
from .serial_tracking import router as serial_tracking_router
from .family_wallet import router as family_wallet_router
from .blind_stock_take import router as blind_stock_take_router
from .inventory_balancing import router as inventory_balancing_router
from .repair_portal import router as repair_portal_router
from .roster import router as roster_router
from .cl_po import router as cl_po_router
from .endless_aisle import router as endless_aisle_router
from .vendor_rebates import router as vendor_rebates_router
from .hr import router as hr_router
from .hr_self_service import router as hr_self_service_router
from .workshop import router as workshop_router
from .reports import router as reports_router
from .settings import router as settings_router
from .clinical import router as clinical_router
from .clinical_device_import import router as clinical_device_import_router
from .admin import router as admin_router
from .admin_catalog import router as admin_catalog_router
from .admin_extras import router as admin_extras_router
from .handoffs import router as handoffs_router
from .transfers import router as transfers_router
from .item_events import router as item_events_router
from .catalog import router as catalog_router
from .catalog_autopilot import router as catalog_autopilot_router
from .catalog_import import router as catalog_import_router
from .buy_desk import router as buy_desk_router
from .jarvis import router as jarvis_router
from .analytics import router as analytics_router
from .crm import router as crm_router
from .follow_ups import router as follow_ups_router
from .payroll import router as payroll_router
from .marketing import router as marketing_router
from .campaigns import router as campaigns_router
from .reminders import router as reminders_router
from .analytics_v2 import router as analytics_v2_router
from .agents import router as agents_router
from .proposals import router as proposals_router
from .walkouts import router as walkouts_router
from .points import router as points_router
from .payout import router as payout_router
from .kicker import router as kicker_router
from .webhooks import router as webhooks_router
from .loyalty import router as loyalty_router
from .vendor_portal import router as vendor_portal_router
from .portal import router as portal_router
from .techcherry_import import router as techcherry_import_router
from .vouchers import router as vouchers_router
from .promotions import router as promotions_router
from .entities import router as entities_router
from .notifications import router as notifications_router
from .shipping import router as shipping_router
from .labels import router as labels_router
from .display_fixtures import router as display_fixtures_router
from .display_placements import router as display_placements_router
from .print_overrides import router as print_overrides_router
from .print_documents import router as print_documents_router
from .estimates import router as estimates_router
from .lens_catalog import router as lens_catalog_router
from .lens_stock import router as lens_stock_router
from .lens_enums import router as lens_enums_router
from .catalog_field_options import router as catalog_field_options_router
from .product_templates import router as product_templates_router
from .audit import router as audit_router
from .budgets import router as budgets_router
from .online_store import router as online_store_router
from .online_store_collections import router as online_store_collections_router
from .collections_browse import router as collections_browse_router
from .collections_insights import router as collections_insights_router
from .online_store_menus import router as online_store_menus_router
from .online_store_images import router as online_store_images_router
from .online_store_push import router as online_store_push_router
from .online_store_orders import router as online_store_orders_router
from .catalogue_pdf import router as catalogue_pdf_router
from .ondc import router as ondc_router
from .approvals import router as approvals_router

__all__ = [
    "auth_router",
    "dashboard_widgets_router",
    "users_router",
    "stores_router",
    "products_router",
    "product_master_router",
    "inventory_router",
    "customers_router",
    "crm_router",
    "orders_router",
    "prescriptions_router",
    "vendors_router",
    "purchase_invoices_router",
    "purchase_recon_router",
    "vendor_returns_router",
    "vendor_rma_router",
    "rtv_debit_notes_router",
    "returns_router",
    "tasks_router",
    "expenses_router",
    "finance_router",
    "finance_ticker_router",
    "reconciliation_router",
    "till_router",
    "bank_reconciliation_router",
    "non_adapt_router",
    "serial_tracking_router",
    "family_wallet_router",
    "blind_stock_take_router",
    "inventory_balancing_router",
    "repair_portal_router",
    "roster_router",
    "cl_po_router",
    "endless_aisle_router",
    "vendor_rebates_router",
    "hr_router",
    "hr_self_service_router",
    "workshop_router",
    "reports_router",
    "settings_router",
    "clinical_router",
    "clinical_device_import_router",
    "admin_router",
    "admin_catalog_router",
    "admin_extras_router",
    "handoffs_router",
    "transfers_router",
    "item_events_router",
    "catalog_router",
    "catalog_autopilot_router",
    "catalog_import_router",
    "buy_desk_router",
    "jarvis_router",
    "analytics_router",
    "follow_ups_router",
    "payroll_router",
    "marketing_router",
    "campaigns_router",
    "reminders_router",
    "analytics_v2_router",
    "agents_router",
    "proposals_router",
    "walkouts_router",
    "points_router",
    "payout_router",
    "kicker_router",
    "webhooks_router",
    "loyalty_router",
    "vendor_portal_router",
    "portal_router",
    "techcherry_import_router",
    "vouchers_router",
    "promotions_router",
    "entities_router",
    "notifications_router",
    "shipping_router",
    "labels_router",
    "display_fixtures_router",
    "display_placements_router",
    "print_overrides_router",
    "print_documents_router",
    "estimates_router",
    "lens_catalog_router",
    "lens_stock_router",
    "lens_enums_router",
    "catalog_field_options_router",
    "product_templates_router",
    "audit_router",
    "budgets_router",
    "online_store_router",
    "online_store_collections_router",
    "collections_browse_router",
    "collections_insights_router",
    "online_store_menus_router",
    "online_store_images_router",
    "online_store_push_router",
    "online_store_orders_router",
    "catalogue_pdf_router",
    "ondc_router",
    "approvals_router",
]

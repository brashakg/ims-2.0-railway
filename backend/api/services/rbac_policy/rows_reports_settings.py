"""
POLICY rows: /reports, /returns, /settings.

Moved VERBATIM from the flat ``api/services/rbac_policy.py`` lines 5697-6213.
These rows are load-bearing DATA. Their ORDER is preserved (``policy_for``
prefers the most specific match, and a row under a module widens that
module's capability grant-union). Do not reorder, reword or re-group them;
a new route needs a new row here or CI fails the coverage lock.
"""

from __future__ import annotations

from typing import Dict, List

ROWS: List[Dict[str, object]] = [
    # --- /api/v1/reports ---
    {"method": "GET", "path": "/api/v1/reports", "allowed": "PUBLIC"},
    {"method": "GET", "path": "/api/v1/reports/", "allowed": "PUBLIC"},
    {"method": "GET", "path": "/api/v1/reports/blueprint", "allowed": "AUTHENTICATED"},
    {
        "method": "GET",
        "path": "/api/v1/reports/clinical/eye-tests",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/reports/customers/acquisition",
        "allowed": "AUTHENTICATED",
    },
    {"method": "GET", "path": "/api/v1/reports/dashboard", "allowed": "AUTHENTICATED"},
    {
        "method": "GET",
        "path": "/api/v1/reports/day-end-close",
        "allowed": [
            "ACCOUNTANT",
            "ADMIN",
            "AREA_MANAGER",
            "CASHIER",
            "SALES_STAFF",
            "STORE_MANAGER",
        ],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/reports/day-end-close",
        "allowed": [
            "ACCOUNTANT",
            "ADMIN",
            "AREA_MANAGER",
            "CASHIER",
            "SALES_STAFF",
            "STORE_MANAGER",
        ],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/reports/discount/analysis",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/reports/finance/expense-vs-revenue",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/reports/finance/gst",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/reports/finance/outstanding",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/reports/gstr1",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/reports/gstr1/gstn-json",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/reports/gstr3b",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/reports/gstr3b/gstn-json",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/reports/hr/attendance",
        "allowed": "AUTHENTICATED",
    },
    # F11 Offer Tally / promotions report (finance-sensitive margin data).
    {
        "method": "GET",
        "path": "/api/v1/reports/promotions",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {"method": "GET", "path": "/api/v1/reports/inventory", "allowed": "AUTHENTICATED"},
    {
        "method": "GET",
        "path": "/api/v1/reports/inventory/brand-sellthrough",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/reports/inventory/non-moving-stock",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/reports/inventory/summary",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/reports/inventory/tax-code-audit",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/reports/inventory/valuation",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/reports/profit/by-category",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/reports/profit/by-store",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/reports/purchase/recommendations",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/reports/sales/by-category",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/reports/sales/by-salesperson",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/reports/sales/comparison",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/reports/sales/daily",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/reports/sales/growth",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/reports/sales/lens-deep-dive",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/reports/sales/price-bands",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/reports/sales/seasonality",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/reports/sales/summary",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/reports/staff/ranking",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/reports/stock/count",
        "allowed": "AUTHENTICATED",
    },
    {"method": "GET", "path": "/api/v1/reports/targets", "allowed": "AUTHENTICATED"},
    {
        "method": "GET",
        "path": "/api/v1/reports/tasks/summary",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/reports/walkouts/footfall-audit",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/reports/workshop/pending-jobs",
        "allowed": "AUTHENTICATED",
    },
    # Workshop productivity report (per-technician scorecard: completion,
    # QC-fail-rate, on-time, utilization over a date range). A management lens
    # -> store/area managers + admins (SUPERADMIN auto-passes via require_roles).
    {
        "method": "GET",
        "path": "/api/v1/reports/workshop/productivity",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    # --- /api/v1/returns ---
    # SALES_CASHIER merged into SALES_STAFF (backlog #12): the create/restock
    # gate (_RETURN_ROLES) granted SALES_CASHIER but not SALES_STAFF, so the
    # access moves to the survivor SALES_STAFF. Mirrors returns.py._RETURN_ROLES.
    {"method": "GET", "path": "/api/v1/returns", "allowed": "AUTHENTICATED"},
    {
        "method": "POST",
        "path": "/api/v1/returns",
        "allowed": ["ADMIN", "CASHIER", "SALES_STAFF", "STORE_MANAGER"],
    },
    {"method": "GET", "path": "/api/v1/returns/", "allowed": "AUTHENTICATED"},
    {
        "method": "POST",
        "path": "/api/v1/returns/",
        "allowed": ["ADMIN", "CASHIER", "SALES_STAFF", "STORE_MANAGER"],
    },
    # Read-only authoritative money preview for a return (no side effects). Same
    # role set as creating the return -- it echoes order money + tender figures.
    {
        "method": "POST",
        "path": "/api/v1/returns/quote",
        "allowed": ["ADMIN", "CASHIER", "SALES_STAFF", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/returns/{return_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/returns/{return_id}/restock",
        "allowed": ["ADMIN", "CASHIER", "SALES_STAFF", "STORE_MANAGER"],
    },
    # --- /api/v1/settings ---
    {"method": "GET", "path": "/api/v1/settings", "allowed": "PUBLIC"},
    {"method": "GET", "path": "/api/v1/settings/", "allowed": "PUBLIC"},
    # E2 policy matrix: GET reads are restricted to settings-viewing roles (a cashier
    # should not enumerate another store's discount caps / refund thresholds);
    # PUT/DELETE = union of write roles (the fine-grained per-key write_roles gate is
    # enforced in set_policy/clear_override -- the table row is defense-in-depth).
    {
        "method": "GET",
        "path": "/api/v1/settings/policies/registry",
        "allowed": [
            "SUPERADMIN",
            "ADMIN",
            "AREA_MANAGER",
            "ACCOUNTANT",
            "STORE_MANAGER",
        ],
    },
    {
        "method": "GET",
        "path": "/api/v1/settings/policies",
        "allowed": [
            "SUPERADMIN",
            "ADMIN",
            "AREA_MANAGER",
            "ACCOUNTANT",
            "STORE_MANAGER",
        ],
    },
    {
        "method": "GET",
        "path": "/api/v1/settings/policies/{key}",
        "allowed": [
            "SUPERADMIN",
            "ADMIN",
            "AREA_MANAGER",
            "ACCOUNTANT",
            "STORE_MANAGER",
        ],
    },
    {
        "method": "PUT",
        "path": "/api/v1/settings/policies/{key}",
        "allowed": ["SUPERADMIN", "ADMIN", "STORE_MANAGER", "ACCOUNTANT"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/settings/policies/{key}",
        "allowed": ["SUPERADMIN", "ADMIN", "STORE_MANAGER", "ACCOUNTANT"],
    },
    {
        "method": "GET",
        "path": "/api/v1/settings/admin-controls",
        "allowed": ["SUPERADMIN"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/settings/admin-controls",
        "allowed": ["SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/settings/approval-workflows",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/settings/approval-workflows",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/settings/audit-logs",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/settings/audit-logs/summary",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {"method": "GET", "path": "/api/v1/settings/business", "allowed": "AUTHENTICATED"},
    {
        "method": "PUT",
        "path": "/api/v1/settings/business",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/settings/business/logo",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/settings/business/logo/{file_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/settings/discount-rules",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/settings/discount-rules",
        "allowed": ["ADMIN", "AREA_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/settings/discount-rules",
        "allowed": ["ADMIN", "AREA_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/settings/feature-toggles/{store_id}",
        "allowed": ["STORE_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "PATCH",
        "path": "/api/v1/settings/feature-toggles/{store_id}",
        "allowed": ["STORE_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/settings/feature-toggles/{store_id}",
        "allowed": ["STORE_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/settings/integrations",
        "allowed": "AUTHENTICATED",
    },
    {
        # Integration catalog = the field definitions the IntegrationsHub renders
        # (no secrets). ADMIN/SUPERADMIN only, matching the GET/PUT integration
        # config gating. Literal path -- must beat the {integration_type} template
        # below (policy_for prefers the exact-literal match + the most specific
        # row, and the route gate is require_roles("ADMIN")).
        "method": "GET",
        "path": "/api/v1/settings/integrations/catalog",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        # Live Claude model list for the Anthropic integration's model picker.
        # Read-only listing of available models (no secrets returned). Literal
        # path -- must beat the {integration_type} template below. ADMIN/
        # SUPERADMIN only, matching the integration config GET/PUT gating.
        "method": "GET",
        "path": "/api/v1/settings/integrations/anthropic/models",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/settings/integrations/{integration_type}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "PUT",
        "path": "/api/v1/settings/integrations/{integration_type}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/settings/integrations/{integration_type}/test",
        "allowed": "AUTHENTICATED",
    },
    {"method": "GET", "path": "/api/v1/settings/invoice", "allowed": "AUTHENTICATED"},
    {
        "method": "PUT",
        "path": "/api/v1/settings/invoice",
        "allowed": ["ACCOUNTANT", "ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/settings/marketplace-channels",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "PUT",
        "path": "/api/v1/settings/marketplace-channels",
        "allowed": ["ADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/settings/marketplace-channels/{channel}/sync",
        "allowed": ["ADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/settings/notifications/logs",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/settings/notifications/providers",
        "allowed": ["ADMIN"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/settings/notifications/providers",
        "allowed": ["ADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/settings/notifications/templates",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/settings/notifications/templates",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/settings/notifications/templates/{template_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/settings/notifications/templates/{template_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "PUT",
        "path": "/api/v1/settings/notifications/templates/{template_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/settings/notifications/test",
        "allowed": "AUTHENTICATED",
    },
    {"method": "GET", "path": "/api/v1/settings/printers", "allowed": "AUTHENTICATED"},
    {"method": "PUT", "path": "/api/v1/settings/printers", "allowed": "AUTHENTICATED"},
    {
        "method": "GET",
        "path": "/api/v1/settings/printers/available",
        "allowed": "AUTHENTICATED",
    },
    {"method": "GET", "path": "/api/v1/settings/profile", "allowed": "AUTHENTICATED"},
    {"method": "PUT", "path": "/api/v1/settings/profile", "allowed": "AUTHENTICATED"},
    {
        "method": "POST",
        "path": "/api/v1/settings/profile/change-password",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/settings/profile/preferences",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "PUT",
        "path": "/api/v1/settings/profile/preferences",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/settings/system",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {"method": "PUT", "path": "/api/v1/settings/system", "allowed": ["SUPERADMIN"]},
    {"method": "GET", "path": "/api/v1/settings/tax", "allowed": "AUTHENTICATED"},
    {
        "method": "PUT",
        "path": "/api/v1/settings/tax",
        "allowed": ["ACCOUNTANT", "ADMIN", "SUPERADMIN"],
    },
    {"method": "GET", "path": "/api/v1/settings/tds-rates", "allowed": "AUTHENTICATED"},
    {"method": "PUT", "path": "/api/v1/settings/tds-rates", "allowed": ["SUPERADMIN"]},
]

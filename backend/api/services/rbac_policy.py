"""
IMS 2.0 - Central RBAC Policy Registry (request-time enforced)
==============================================================

A single declarative table of every API endpoint and the role set that may
reach it, derived from the CURRENT enforcement in the routers (hardening dim 4).

WHY THIS EXISTS
---------------
Access control today is spread across four mechanisms:

  1. Per-endpoint dependencies  - ``Depends(require_roles(...))``,
     ``require_admin`` / ``require_manager`` / ``require_superadmin``.
  2. Router-level dependencies  - ``APIRouter(dependencies=[Depends(_require_admin_role)])``
     on the admin / admin_catalog / admin_extras routers, and
     ``include_router(..., dependencies=[Depends(require_roles(*_FINANCE_ROLES))])``
     on finance / hr / payroll in ``api/main.py``.
  3. Inline checks in handler bodies - e.g. POS_WRITE_ROLES on ``POST /orders``,
     ``_require_superadmin(current_user)`` on ``GET /audit/verify``.
  4. Implicit (no role check) - ``Depends(get_current_user)`` only.

That scattering makes it hard to answer "who can call X?" and easy to ship a
new endpoint with the wrong gate. This module collapses all four into one
enforcer. ``check_access`` is now consumed at request time by the middleware in
``api/middleware/rbac_enforcement.py`` as a SECOND, defense-in-depth layer that
sits ON TOP of the per-route gates (which remain in place). The registry mirrors
the route gates EXACTLY, so the enforcer is behavior-preserving: no endpoint's
effective access changes. The middleware fails OPEN on un-catalogued routes and
PASSES THROUGH on missing/invalid tokens (so the route's own ``get_current_user``
returns the canonical 401); only an authenticated caller who genuinely lacks the
role is 403'd one layer earlier. The coverage-lock test
(``tests/test_rbac_policy.py``) guarantees the table stays complete.

HOW THE TABLE WAS BUILT
-----------------------
Each route in ``api.main.app.routes`` was introspected for its dependency tree
(catching mechanisms 1 and 2 above) and, where only ``get_current_user`` was a
dependency, its handler source was read to capture inline role gates
(mechanism 3). The finance/hr/payroll router-level gate does NOT flatten into a
route's ``dependant`` in this FastAPI version, so it is applied by prefix.

POLICY ENTRY SHAPE
------------------
    {"method": "POST", "path": "/api/v1/orders",
     "allowed": [<roles>] | "AUTHENTICATED" | "PUBLIC",
     "store_scoped": bool}      # store_scoped omitted when False

  - A role list  : caller must hold at least one of these roles. SUPERADMIN is
                   listed explicitly wherever it passes (it always does via
                   ``require_roles``), so the table is self-contained.
  - "AUTHENTICATED": any logged-in user (valid JWT); no role differentiation.
  - "PUBLIC"     : reachable with no IMS auth at all. Each PUBLIC route is
                   protected by its OWN mechanism (HMAC webhook signature,
                   tokenized/OTP customer-portal link, vendor-portal path token,
                   ``SEED_SECRET`` hmac, login credentials) or is a static
                   module-info stub with no data access.

ROLE MODEL (12 canonical roles)
-------------------------------
SUPERADMIN, ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT, CATALOG_MANAGER,
OPTOMETRIST, SALES_CASHIER, SALES_STAFF, CASHIER, WORKSHOP_STAFF, plus the
read-only INVESTOR role.

CAVEATS (the request-time enforcer relies on these holding true)
----------------------------------------------------------------
  * INVESTOR write-block is a MIDDLEWARE in ``api/main.py``
    (``block_investor_writes``): an INVESTOR-only user is 403'd on every
    non-safe method app-wide, regardless of this table. INVESTOR is therefore
    NOT added to any ``allowed`` list; treat it as read-only everywhere.
  * Some "AUTHENTICATED" rows still 403 on a *data-conditional* basis the table
    cannot express: store-scope (``validate_store_access``), resource ownership
    (handoff recipient/uploader), or a discount-cap breach. Those are flagged
    ``store_scoped`` where applicable; ownership/cap conditions are documented in
    ``docs/reference/RBAC_MATRIX.md`` REVIEW section, not encoded here.
  * ``store_scoped`` means the handler additionally restricts the row to the
    caller's store(s) (HQ roles bypass). It is orthogonal to ``allowed``.

This file is GENERATED-then-curated; if routes change, re-derive it and update
``docs/reference/RBAC_MATRIX.md``. The companion test
``backend/tests/test_rbac_policy.py`` fails if any live ``/api/v1`` route is
missing here (the regression lock).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Union

# All 11 operational roles (INVESTOR excluded - read-only via middleware, never
# an allow-list member). SUPERADMIN is a member of every gate implicitly.
ALL_ROLES: List[str] = [
    "SUPERADMIN",
    "ADMIN",
    "AREA_MANAGER",
    "STORE_MANAGER",
    "ACCOUNTANT",
    "CATALOG_MANAGER",
    "OPTOMETRIST",
    "SALES_CASHIER",
    "SALES_STAFF",
    "CASHIER",
    "WORKSHOP_STAFF",
    # DESIGN_MANAGER (lowest-privilege ecom design-queue role, BVI Phase 1).
    # Added to the matrix for the "Online Store" module; does not change any
    # existing route gate. See routers/online_store.py + BVI_MERGE_PLAN.md.
    "DESIGN_MANAGER",
]

# Sentinel allow-values.
PUBLIC = "PUBLIC"
AUTHENTICATED = "AUTHENTICATED"

Allowed = Union[List[str], str]

# ---------------------------------------------------------------------------
# POLICY - one row per (method, path). Mirrors CURRENT enforcement exactly.
# Generated from api.main.app.routes; see module docstring for methodology.
# ---------------------------------------------------------------------------
POLICY: List[Dict[str, object]] = [
    # --- /api/v1/approvals (E4 PIN-gated maker-checker) ---
    # Any authenticated maker can open a request, view their own, or consume an
    # approval they hold; approve/reject is gated to business approvers (the PIN
    # is the second factor inside the handler). Inbox adds ACCOUNTANT (read-only).
    {"method": "POST", "path": "/api/v1/approvals/requests", "allowed": AUTHENTICATED},
    {
        "method": "GET",
        "path": "/api/v1/approvals/requests/inbox",
        "allowed": [
            "SUPERADMIN",
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "ACCOUNTANT",
        ],
    },
    {
        "method": "GET",
        "path": "/api/v1/approvals/requests/mine",
        "allowed": AUTHENTICATED,
    },
    {
        "method": "GET",
        "path": "/api/v1/approvals/requests/{request_id}",
        "allowed": AUTHENTICATED,
    },
    {
        "method": "POST",
        "path": "/api/v1/approvals/requests/{request_id}/approve",
        "allowed": ["SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/approvals/requests/{request_id}/reject",
        "allowed": ["SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/approvals/requests/{request_id}/consume",
        "allowed": AUTHENTICATED,
    },
    # --- /api/v1/admin ---
    {"method": "GET", "path": "/api/v1/admin", "allowed": ["ADMIN", "SUPERADMIN"]},
    {"method": "GET", "path": "/api/v1/admin/", "allowed": ["ADMIN", "SUPERADMIN"]},
    {
        "method": "GET",
        "path": "/api/v1/admin/brands",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/brands",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/admin/brands/{brand_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/brands/{brand_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/admin/brands/{brand_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/brands/{brand_id}/subbrands",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/brands/{brand_id}/subbrands",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/admin/brands/{brand_id}/subbrands/{subbrand_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/categories",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/categories",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/admin/categories/{category_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/categories/{category_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/admin/categories/{category_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/discounts/enforced-caps",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/discounts/promo-codes",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/discounts/promo-codes",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/admin/discounts/promo-codes/{code_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/discounts/role-caps",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/discounts/role-caps",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/discounts/rules",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/discounts/tier-discounts",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/discounts/tier-discounts",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {"method": "GET", "path": "/api/v1/admin/escalations", "allowed": ["ADMIN"]},
    {"method": "GET", "path": "/api/v1/admin/hsn", "allowed": ["ADMIN", "SUPERADMIN"]},
    {"method": "POST", "path": "/api/v1/admin/hsn", "allowed": ["ADMIN", "SUPERADMIN"]},
    {
        "method": "DELETE",
        "path": "/api/v1/admin/hsn/{hsn_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/admin/hsn/{hsn_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/integrations",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/integrations/razorpay",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/integrations/razorpay",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/integrations/razorpay/test",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/integrations/shiprocket",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/integrations/shiprocket",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/integrations/shiprocket/create-shipment",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/integrations/shiprocket/rates",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/integrations/shiprocket/test",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/integrations/shiprocket/track/{awb}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/integrations/shopify",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/integrations/shopify",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/integrations/shopify/sync-inventory",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/integrations/shopify/sync-orders",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/integrations/shopify/test",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/integrations/sms",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/integrations/sms",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/integrations/tally",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/integrations/tally",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/integrations/tally/exports",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/integrations/tally/regenerate",
        "allowed": ["SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/integrations/tally/test",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/integrations/tally/voucher.xml",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/integrations/whatsapp",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/integrations/whatsapp",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/integrations/whatsapp/test",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/lens/addons",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/lens/addons",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/admin/lens/addons/{addon_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/admin/lens/addons/{addon_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/lens/brands",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/lens/brands",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/admin/lens/brands/{brand_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/admin/lens/brands/{brand_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/lens/coatings",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/lens/coatings",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/admin/lens/coatings/{coating_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/admin/lens/coatings/{coating_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/lens/indices",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/lens/indices",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/admin/lens/indices/{index_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/admin/lens/indices/{index_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/lens/pricing",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/lens/pricing",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/lens/pricing-ranges",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/lens/pricing-ranges",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/lens/pricing-ranges/bulk",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/lens/pricing-ranges/quote",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/admin/lens/pricing-ranges/{range_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/admin/lens/pricing-ranges/{range_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    # Online-store bridge health tile. Lives under the ADMIN-gated admin router
    # but the handler narrows to SUPERADMIN inline (online-store admin is a
    # SUPERADMIN concern, matching the Jarvis / ecommerce-SSO posture).
    {
        "method": "GET",
        "path": "/api/v1/admin/online-store/sync-health",
        "allowed": ["SUPERADMIN"],
    },
    # BVI safety nets (Steps 3, 4, 6): drift detector, oversell repush, parity oracle.
    # All narrowed to SUPERADMIN inline (same posture as sync-health above).
    {
        "method": "GET",
        "path": "/api/v1/admin/online-store/drift",
        "allowed": ["SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/online-store/repush-oversell",
        "allowed": ["SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/online-store/parity",
        "allowed": ["SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/online-store/rehost-images",
        "allowed": ["SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/products",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/products/bulk-import",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/products/bulk-import/{job_id}/file",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/products/generate-sku",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/products/{product_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {"method": "POST", "path": "/api/v1/admin/seed-database", "allowed": "PUBLIC"},
    {"method": "GET", "path": "/api/v1/admin/system-health", "allowed": ["ADMIN"]},
    {"method": "GET", "path": "/api/v1/admin/owner-digest", "allowed": ["ADMIN"]},
    {
        "method": "GET",
        "path": "/api/v1/admin/system/audit-logs",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/system/backups",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/system/backups",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/system/backups/{backup_id}/restore",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/system/export/{export_type}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/system/settings",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/admin/system/settings",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/system/status",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/techcherry/import",
        "allowed": ["SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/techcherry/status",
        "allowed": ["SUPERADMIN"],
    },
    # --- /api/v1/analytics ---
    {"method": "GET", "path": "/api/v1/analytics", "allowed": "PUBLIC"},
    {"method": "GET", "path": "/api/v1/analytics/", "allowed": "PUBLIC"},
    {
        "method": "GET",
        "path": "/api/v1/analytics/customer-insights",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/analytics/dashboard-summary",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/analytics/enterprise-kpis",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/analytics/inventory-intelligence",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/analytics/revenue-trends",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/analytics/store-performance",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/analytics/store-target-today",
        "allowed": "AUTHENTICATED",
    },
    # --- /api/v1/analytics-v2 ---
    {
        "method": "GET",
        "path": "/api/v1/analytics-v2/anomaly-detection",
        "allowed": ["SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/analytics-v2/churn-prediction",
        "allowed": ["SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/analytics-v2/cl-subscription/reminder/{customer_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/analytics-v2/cl-subscriptions",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/analytics-v2/dead-stock",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/analytics-v2/demand-forecast",
        "allowed": ["SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/analytics-v2/discount-analysis",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/analytics-v2/eye-camps",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/analytics-v2/eye-camps",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "PATCH",
        "path": "/api/v1/analytics-v2/eye-camps/{camp_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/analytics-v2/family-deals",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/analytics-v2/loyalty/earn",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/analytics-v2/loyalty/redeem",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/analytics-v2/loyalty/tiers",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/analytics-v2/staff-leaderboard",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/analytics-v2/vendor-margins",
        "allowed": ["SUPERADMIN"],
    },
    # --- /api/v1/audit ---
    {"method": "GET", "path": "/api/v1/audit/verify", "allowed": ["SUPERADMIN"]},
    # --- /api/v1/auth ---
    {
        "method": "POST",
        "path": "/api/v1/auth/change-password",
        "allowed": "AUTHENTICATED",
    },
    {"method": "GET", "path": "/api/v1/auth/ecommerce-sso", "allowed": "AUTHENTICATED"},
    {"method": "POST", "path": "/api/v1/auth/login", "allowed": "PUBLIC"},
    {"method": "POST", "path": "/api/v1/auth/logout", "allowed": "AUTHENTICATED"},
    {"method": "GET", "path": "/api/v1/auth/me", "allowed": "AUTHENTICATED"},
    {"method": "POST", "path": "/api/v1/auth/refresh", "allowed": "PUBLIC"},
    {
        "method": "POST",
        "path": "/api/v1/auth/switch-store/{store_id}",
        "allowed": "AUTHENTICATED",
    },
    # --- /api/v1/catalog ---
    {"method": "GET", "path": "/api/v1/catalog", "allowed": "PUBLIC"},
    {"method": "GET", "path": "/api/v1/catalog/", "allowed": "PUBLIC"},
    {"method": "GET", "path": "/api/v1/catalog/brands", "allowed": "AUTHENTICATED"},
    {"method": "GET", "path": "/api/v1/catalog/categories", "allowed": "AUTHENTICATED"},
    {
        "method": "GET",
        "path": "/api/v1/catalog/categories/{category}/fields",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/catalog/online-status",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/catalog/online-status",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/catalog/online-stock-reconcile",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/catalog/online-summary",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/catalog/price-change-requests",
        "allowed": "AUTHENTICATED",
    },
    {"method": "GET", "path": "/api/v1/catalog/products", "allowed": "AUTHENTICATED"},
    {
        "method": "POST",
        "path": "/api/v1/catalog/products",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "SUPERADMIN"],
    },
    # Hub Phase 3: vendor price-list import (preview = no-write dry run; commit
    # lands DRAFT products + teaches the SKU-alias flywheel). CATALOG-role gated.
    {
        "method": "POST",
        "path": "/api/v1/catalog-import/preview",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/catalog-import/commit",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "SUPERADMIN"],
    },
    # Hub Buy Desk: read-only rows for the one-screen catalog->purchase landing.
    # Catalog owners + PO raisers may view (they decide what to buy).
    {
        "method": "GET",
        "path": "/api/v1/buy-desk/rows",
        "allowed": [
            "ADMIN",
            "CATALOG_MANAGER",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "ACCOUNTANT",
            "SUPERADMIN",
        ],
    },
    {
        "method": "POST",
        "path": "/api/v1/catalog/products/bulk-sync-shopify",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/catalog/products/export",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/catalog/products/import",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/catalog/products/{product_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/catalog/products/{product_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "PUT",
        "path": "/api/v1/catalog/products/{product_id}",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/catalog/products/{product_id}/inventory",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/catalog/products/{product_id}/inventory/adjust",
        "allowed": ["ADMIN", "STORE_MANAGER", "SUPERADMIN", "WORKSHOP_STAFF"],
    },
    {
        "method": "POST",
        "path": "/api/v1/catalog/products/{product_id}/sync-shopify",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/catalog/recent-activity",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/catalog/reconcile-store-barcodes",
        "allowed": ["SUPERADMIN"],
    },
    {"method": "GET", "path": "/api/v1/catalog/sku-counts", "allowed": "AUTHENTICATED"},
    # --- /api/v1/catalog-autopilot ---
    {
        "method": "POST",
        "path": "/api/v1/catalog-autopilot/candidates/{candidate_id}/decision",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/catalog-autopilot/jobs",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/catalog-autopilot/jobs",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/catalog-autopilot/jobs/{job_id}",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/catalog-autopilot/sources",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "SUPERADMIN"],
    },
    # --- /api/v1/clinical ---
    {"method": "GET", "path": "/api/v1/clinical", "allowed": "PUBLIC"},
    {"method": "GET", "path": "/api/v1/clinical/", "allowed": "PUBLIC"},
    {
        "method": "GET",
        "path": "/api/v1/clinical/abuse-detection",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/clinical/conversion-dashboard",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "OPTOMETRIST",
            "STORE_MANAGER",
            "SUPERADMIN",
        ],
        "store_scoped": True,
    },
    {"method": "GET", "path": "/api/v1/clinical/eye-tests", "allowed": "AUTHENTICATED"},
    {
        "method": "GET",
        "path": "/api/v1/clinical/optometrist/{optometrist_id}/stats",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "OPTOMETRIST",
            "STORE_MANAGER",
            "SUPERADMIN",
        ],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/clinical/patient-queue",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/clinical/prescription-redo-rate",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/clinical/prescriptions/{prescription_id}/print",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/clinical/prescriptions/{prescription_id}/redo",
        "allowed": ["ADMIN", "AREA_MANAGER", "OPTOMETRIST", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/clinical/prescriptions/{prescription_id}/redos",
        "allowed": "AUTHENTICATED",
    },
    {"method": "GET", "path": "/api/v1/clinical/queue", "allowed": "AUTHENTICATED"},
    {
        "method": "POST",
        "path": "/api/v1/clinical/queue",
        "allowed": ["ADMIN", "OPTOMETRIST", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/clinical/queue/stats",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "DELETE",
        "path": "/api/v1/clinical/queue/{queue_id}",
        "allowed": ["ADMIN", "OPTOMETRIST", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/clinical/queue/{queue_id}/start-test",
        "allowed": ["ADMIN", "OPTOMETRIST", "STORE_MANAGER"],
    },
    {
        "method": "PATCH",
        "path": "/api/v1/clinical/queue/{queue_id}/status",
        "allowed": ["ADMIN", "OPTOMETRIST", "STORE_MANAGER"],
    },
    {"method": "GET", "path": "/api/v1/clinical/tests", "allowed": "AUTHENTICATED"},
    # Eye-test READS carry clinical PII (Rx + exam findings) -> same role set
    # as prescription reads (prescriptions._RX_READ_ROLES / require_rx_read);
    # store-scope enforced per object in the handler (404-hide cross-store).
    {
        "method": "GET",
        "path": "/api/v1/clinical/tests/customer/{customer_id}",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "OPTOMETRIST",
            "SALES_CASHIER",
            "SALES_STAFF",
            "STORE_MANAGER",
            "SUPERADMIN",
            "WORKSHOP_STAFF",
        ],
    },
    {
        "method": "GET",
        "path": "/api/v1/clinical/tests/patient/{customer_phone}",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "OPTOMETRIST",
            "SALES_CASHIER",
            "SALES_STAFF",
            "STORE_MANAGER",
            "SUPERADMIN",
            "WORKSHOP_STAFF",
        ],
    },
    {
        "method": "GET",
        "path": "/api/v1/clinical/tests/{test_id}",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "OPTOMETRIST",
            "SALES_CASHIER",
            "SALES_STAFF",
            "STORE_MANAGER",
            "SUPERADMIN",
            "WORKSHOP_STAFF",
        ],
    },
    {
        "method": "POST",
        "path": "/api/v1/clinical/tests/{test_id}/complete",
        "allowed": ["ADMIN", "OPTOMETRIST", "STORE_MANAGER"],
    },
    # F50 -- send a completed Rx to the sales floor (in-app handover). Same gate
    # as test completion (require_roles(*_CLINICAL_ROLES); SUPERADMIN implicit).
    # Per-store feature flag + store IDOR guard enforced in the handler.
    {
        "method": "POST",
        "path": "/api/v1/clinical/tests/{test_id}/send-to-floor",
        "allowed": ["ADMIN", "OPTOMETRIST", "STORE_MANAGER"],
        "store_scoped": True,
    },
    # CLI-7 — frame+lens+Rx manufacturability pre-check
    {
        "method": "POST",
        "path": "/api/v1/clinical/manufacturability-check",
        "allowed": "AUTHENTICATED",
    },
    # CLI-9 — named lens-power combos (save-and-reuse Rx templates)
    {
        "method": "GET",
        "path": "/api/v1/clinical/lens-power-combos",
        "allowed": ["ADMIN", "AREA_MANAGER", "OPTOMETRIST", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/clinical/lens-power-combos",
        "allowed": ["ADMIN", "AREA_MANAGER", "OPTOMETRIST", "STORE_MANAGER"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/clinical/lens-power-combos/{combo_id}",
        "allowed": ["ADMIN", "AREA_MANAGER", "OPTOMETRIST", "STORE_MANAGER"],
    },
    # CLI-12: ophthalmic device CSV import (autorefractor / lensmeter -> Rx).
    # Same role gate as clinical write operations (clinical_device_import.py
    # _DEVICE_IMPORT_ROLES). SUPERADMIN passes via require_roles always.
    {
        "method": "POST",
        "path": "/api/v1/clinical/device-import",
        "allowed": ["ADMIN", "OPTOMETRIST", "STORE_MANAGER", "SUPERADMIN"],
    },
    # CLI-11: SOAP exam note endpoints.  GET carries the exam narrative + Dx
    # codes -> same role set as prescription reads (require_rx_read) +
    # per-object store scope; POST replaces the note -> same roles as test
    # completion.
    {
        "method": "GET",
        "path": "/api/v1/clinical/tests/{test_id}/soap-note",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "OPTOMETRIST",
            "SALES_CASHIER",
            "SALES_STAFF",
            "STORE_MANAGER",
            "SUPERADMIN",
            "WORKSHOP_STAFF",
        ],
    },
    {
        "method": "POST",
        "path": "/api/v1/clinical/tests/{test_id}/soap-note",
        "allowed": ["ADMIN", "OPTOMETRIST", "STORE_MANAGER"],
    },
    # --- /api/v1/crm ---
    {"method": "GET", "path": "/api/v1/crm", "allowed": "PUBLIC"},
    {"method": "GET", "path": "/api/v1/crm/", "allowed": "PUBLIC"},
    # F40 VIP-churn watchlist (#40): SUPERADMIN/ADMIN; ADMIN store-scoped server-side.
    {
        "method": "GET",
        "path": "/api/v1/crm/vip-churn",
        "allowed": ["SUPERADMIN", "ADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/crm/vip-churn/{customer_id}/intervene",
        "allowed": ["SUPERADMIN", "ADMIN"],
    },
    # F43 VIP personal-triggers (#43): STAFF_ALERT slice, comms-DARK. Writes are
    # CRM management roles; reads add CATALOG_MANAGER/OPTOMETRIST (they see the
    # 360 view). Store-guarded server-side (non-SUPERADMIN scoped to owned store).
    {
        "method": "POST",
        "path": "/api/v1/crm/customers/{customer_id}/vip",
        "allowed": ["STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/crm/customers/{customer_id}/vip",
        "allowed": [
            "STORE_MANAGER",
            "AREA_MANAGER",
            "ADMIN",
            "SUPERADMIN",
            "CATALOG_MANAGER",
            "OPTOMETRIST",
        ],
    },
    {
        "method": "POST",
        "path": "/api/v1/crm/personal-triggers",
        "allowed": ["STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/crm/personal-triggers",
        "allowed": [
            "STORE_MANAGER",
            "AREA_MANAGER",
            "ADMIN",
            "SUPERADMIN",
            "CATALOG_MANAGER",
            "OPTOMETRIST",
        ],
    },
    {
        "method": "PUT",
        "path": "/api/v1/crm/personal-triggers/{trigger_id}",
        "allowed": ["STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/crm/personal-triggers/{trigger_id}",
        "allowed": ["STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"],
    },
    # F39 NBA daily call list (#39): store-facing call work-list (store-scoped via
    # validate_store_access). NOT ACCOUNTANT/OPTOMETRIST/CATALOG/WORKSHOP/CASHIER.
    {
        "method": "GET",
        "path": "/api/v1/crm/nba/{store_id}",
        "allowed": [
            "SUPERADMIN",
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "SALES_STAFF",
            "SALES_CASHIER",
        ],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/crm/nba/{store_id}/dismiss",
        "allowed": [
            "SUPERADMIN",
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "SALES_STAFF",
            "SALES_CASHIER",
        ],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/crm/nba/{store_id}/complete",
        "allowed": [
            "SUPERADMIN",
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "SALES_STAFF",
            "SALES_CASHIER",
        ],
        "store_scoped": True,
    },
    # F41 lapsed-patient reactivation (#41): in-app reactivation work-list +
    # outcome log (store-scoped via validate_store_access). DARK -- the work-list
    # never sends a message and never mints a voucher. Store-facing roles ONLY,
    # matching the FE route gate (App.tsx customers/reactivation). ACCOUNTANT was
    # dropped from analytics to close the FE/BE role drift (audit F41-P3).
    {
        "method": "GET",
        "path": "/api/v1/crm/reactivation/{store_id}",
        "allowed": [
            "SUPERADMIN",
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "SALES_STAFF",
            "SALES_CASHIER",
        ],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/crm/reactivation/{store_id}/log",
        "allowed": [
            "SUPERADMIN",
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "SALES_STAFF",
            "SALES_CASHIER",
        ],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/crm/reactivation/{store_id}/analytics",
        "allowed": [
            "SUPERADMIN",
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "SALES_STAFF",
            "SALES_CASHIER",
        ],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/crm/customers/360/{customer_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/crm/customers/churn-risk/list",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/crm/customers/segment/rfm",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/crm/customers/{customer_id}/cl-refill-status",
        "allowed": "AUTHENTICATED",
    },
    # CRM-2 phase 2: in-app CL refill-due worklist + deduped reminder-task
    # creator. Read worklist = any store staff; create reminders = manager+.
    # NO outbound message (customer send stays WhatsApp-gated / dark).
    {
        "method": "GET",
        "path": "/api/v1/crm/cl-refill/{store_id}/due",
        "allowed": [
            "SUPERADMIN",
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "SALES_STAFF",
            "SALES_CASHIER",
            "OPTOMETRIST",
        ],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/crm/cl-refill/{store_id}/create-reminders",
        "allowed": ["SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/crm/customers/{customer_id}/interactions",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/crm/customers/{customer_id}/interactions",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/crm/customers/{customer_id}/lifecycle",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/crm/customers/{customer_id}/loyalty-points",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/crm/customers/{customer_id}/prescriptions",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/crm/customers/{customer_id}/return-risk",
        "allowed": "AUTHENTICATED",
    },
    # --- /api/v1/customers ---
    {"method": "GET", "path": "/api/v1/customers", "allowed": "AUTHENTICATED"},
    {"method": "POST", "path": "/api/v1/customers", "allowed": "AUTHENTICATED"},
    {
        "method": "GET",
        "path": "/api/v1/customers/mobile/{mobile}",
        "allowed": "AUTHENTICATED",
    },
    {"method": "GET", "path": "/api/v1/customers/search", "allowed": "AUTHENTICATED"},
    {
        "method": "GET",
        "path": "/api/v1/customers/search/phone",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/customers/{customer_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "PUT",
        "path": "/api/v1/customers/{customer_id}",
        "allowed": "AUTHENTICATED",
    },
    # F39 customer tags: staff SUGGEST, STORE_MANAGER+ approves (DECISIONS s3).
    {
        "method": "PATCH",
        "path": "/api/v1/customers/{customer_id}/tags",
        "allowed": ["SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/customers/{customer_id}/tags/suggest",
        "allowed": [
            "SUPERADMIN",
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "SALES_STAFF",
            "SALES_CASHIER",
        ],
    },
    {
        "method": "GET",
        "path": "/api/v1/customers/{customer_id}/tags/suggestions",
        "allowed": ["SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/customers/{customer_id}/tags/suggestions/{suggestion_id}/approve",
        "allowed": ["SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/customers/{customer_id}/tags/suggestions/{suggestion_id}/reject",
        "allowed": ["SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/customers/{customer_id}/loyalty/add",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/customers/{customer_id}/orders",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/customers/{customer_id}/patients",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/customers/{customer_id}/prescriptions",
        "allowed": "AUTHENTICATED",
    },
    # POS-4: credit-limit / khata summary (same gate as orders -- any POS user)
    {
        "method": "GET",
        "path": "/api/v1/customers/{customer_id}/credit-summary",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/customers/{customer_id}/store-credit/add",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/customers/{customer_id}/store-credit/issue",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/customers/{customer_id}/store-credit/ledger",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/customers/{customer_id}/store-credit/redeem",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    # DPDP Act 2023 — consent ledger endpoints
    {
        "method": "GET",
        "path": "/api/v1/customers/consent/pending-purge",
        "allowed": ["ADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/customers/{customer_id}/consent/ledger",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/customers/{customer_id}/consent",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/customers/{customer_id}/consent/withdraw",
        "allowed": "AUTHENTICATED",
    },
    # --- /api/v1/display-fixtures ---
    {
        "method": "GET",
        "path": "/api/v1/display-fixtures",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/display-fixtures",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/display-fixtures/",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/display-fixtures/",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/display-fixtures/meta/options",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "DELETE",
        "path": "/api/v1/display-fixtures/{fixture_id}",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/display-fixtures/{fixture_id}",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "PATCH",
        "path": "/api/v1/display-fixtures/{fixture_id}",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    # --- /api/v1/display-placements ---
    {
        "method": "GET",
        "path": "/api/v1/display-placements",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/display-placements",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/display-placements/",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/display-placements/",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/display-placements/move",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "DELETE",
        "path": "/api/v1/display-placements/{placement_id}",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/display-placements/{placement_id}",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "PATCH",
        "path": "/api/v1/display-placements/{placement_id}",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    # --- /api/v1/entities ---
    {"method": "GET", "path": "/api/v1/entities", "allowed": "AUTHENTICATED"},
    {"method": "POST", "path": "/api/v1/entities", "allowed": ["ADMIN"]},
    {"method": "GET", "path": "/api/v1/entities/", "allowed": "AUTHENTICATED"},
    {"method": "POST", "path": "/api/v1/entities/", "allowed": ["ADMIN"]},
    {
        "method": "GET",
        "path": "/api/v1/entities/meta/options",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/entities/{entity_id}",
        "allowed": "AUTHENTICATED",
    },
    {"method": "PUT", "path": "/api/v1/entities/{entity_id}", "allowed": ["ADMIN"]},
    {
        "method": "GET",
        "path": "/api/v1/entities/{entity_id}/stores",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "DELETE",
        "path": "/api/v1/entities/{entity_id}/stores/{store_id}",
        "allowed": ["ADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/entities/{entity_id}/stores/{store_id}",
        "allowed": ["ADMIN"],
    },
    # --- /api/v1/estimates ---
    # Non-binding estimate/quotation. Reads are store-scoped (any authenticated
    # caller, filtered to their stores); creation is POS-capable + ADMIN tier.
    {
        "method": "GET",
        "path": "/api/v1/estimates",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/estimates/",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/estimates",
        "allowed": [
            "SUPERADMIN",
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "SALES_CASHIER",
            "SALES_STAFF",
        ],
    },
    {
        "method": "POST",
        "path": "/api/v1/estimates/",
        "allowed": [
            "SUPERADMIN",
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "SALES_CASHIER",
            "SALES_STAFF",
        ],
    },
    {
        "method": "GET",
        "path": "/api/v1/estimates/{estimate_id}",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/estimates/{estimate_id}/render",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    # --- /api/v1/expenses ---
    {"method": "GET", "path": "/api/v1/expenses", "allowed": "AUTHENTICATED"},
    {"method": "POST", "path": "/api/v1/expenses", "allowed": "AUTHENTICATED"},
    {"method": "GET", "path": "/api/v1/expenses/", "allowed": "AUTHENTICATED"},
    {"method": "POST", "path": "/api/v1/expenses/", "allowed": "AUTHENTICATED"},
    {"method": "GET", "path": "/api/v1/expenses/advances", "allowed": "AUTHENTICATED"},
    {"method": "POST", "path": "/api/v1/expenses/advances", "allowed": "AUTHENTICATED"},
    {
        "method": "POST",
        "path": "/api/v1/expenses/advances/{advance_id}/approve",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/expenses/advances/{advance_id}/disburse",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/expenses/advances/{advance_id}/settle",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/expenses/aging",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    {"method": "GET", "path": "/api/v1/expenses/caps", "allowed": "AUTHENTICATED"},
    {"method": "PUT", "path": "/api/v1/expenses/caps", "allowed": ["ADMIN"]},
    # F17 petty-cash float (manage = open/topup; view = balance+ledger).
    {
        "method": "POST",
        "path": "/api/v1/expenses/petty-cash/open",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/expenses/petty-cash/topup",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/expenses/petty-cash/balance",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    # F17 petty-cash EOD settlement (position view + settle + history).
    {
        "method": "GET",
        "path": "/api/v1/expenses/petty-cash/settlement/position",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/expenses/petty-cash/settlement",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/expenses/petty-cash/settlement",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/expenses/duplicate-bills",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/expenses/pending-approval",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/expenses/to-enter",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/expenses/{expense_id}/approve",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/expenses/{expense_id}/bill",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/expenses/{expense_id}/mark-entered",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/expenses/{expense_id}/reject",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/expenses/{expense_id}/send-to-accountant",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/expenses/{expense_id}/submit",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/expenses/{expense_id}/upload-bill",
        "allowed": "AUTHENTICATED",
    },
    # --- /api/v1/finance ---
    {
        "method": "GET",
        "path": "/api/v1/finance/budget",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/finance/cash-flow",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/finance/cash-flow-forecast",
        "allowed": ["ACCOUNTANT", "ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/finance/cash-register/close",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/finance/cash-register/open",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/finance/cash-register/sessions",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        # #7: manager-facing cash reconciliation (close-by-denomination + blind-EOD).
        # In-function gate _CASH_RECON_ROLES; store-scoped roles see only their store.
        "method": "GET",
        "path": "/api/v1/finance/cash-reconciliation-summary",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/finance/cash-reconciliation-signoff",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/finance/gst-status",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/finance/gst/reconciliation",
        # Org-wide, entity-grouped GST recon = finance-admin only (owner decision
        # 2026-06-16; handler enforces _require_finance_admin).
        "allowed": ["ACCOUNTANT", "ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/finance/gst/summary",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/finance/gstr2b-reconcile",
        "allowed": ["ACCOUNTANT", "ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/finance/itc-export",
        "allowed": ["ACCOUNTANT", "ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/finance/itc-register",
        "allowed": ["ACCOUNTANT", "ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/finance/outstanding",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/finance/owner-dashboard",
        "allowed": ["ACCOUNTANT", "ADMIN", "SUPERADMIN"],
    },
    {
        # N8 owner survival cash-flow: org-wide owner figures (AP + projected
        # income), mirrors the owner-dashboard gate exactly. Note: the legacy
        # GET /finance/budget?mode=survival hook narrows to this same set
        # inline in the handler (the plain budget skeleton stays on the wider
        # finance-role row above).
        "method": "GET",
        "path": "/api/v1/finance/survival-cashflow",
        "allowed": ["ACCOUNTANT", "ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/finance/pending-reconciliations",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/finance/period-lock",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/finance/period-locks",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/finance/period-status",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/finance/pnl",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/finance/pnl/by-category",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/finance/pnl/by-store",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/finance/reconciliation",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    # --- E5 tender / cash reconciliation (mounted on /finance behind the finance
    # role gate; map-write + lock narrow further inline in the handler) ---
    {
        "method": "GET",
        "path": "/api/v1/finance/tender-ledger-map",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        # Global/entity writes are SUPERADMIN/ADMIN; a store-scope write also
        # allows ACCOUNTANT/AREA_MANAGER/STORE_MANAGER (own store) -- the handler
        # enforces the per-scope split. The route is reachable by the finance set.
        "method": "PUT",
        "path": "/api/v1/finance/tender-ledger-map",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/finance/reconciliation/by-mode",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/finance/reconciliation/snapshot",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        # Lock narrows to SUPERADMIN/ADMIN/ACCOUNTANT inline (atomic + immutable).
        "method": "POST",
        "path": "/api/v1/finance/reconciliation/{snapshot_id}/lock",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    # --- F23 Blind EOD cash tally & Z-Read (mounted on its OWN /api/v1/till
    # prefix WITHOUT the finance role gate; each route gates inline + store-scopes.
    # Expected/variance are blind-redacted for cashier-only callers at the data
    # layer). ---
    {
        # Open + blind-submit: cashier roles + store management. Cashiers get a
        # redacted response (no expected figure) -- blind enforcement.
        "method": "POST",
        "path": "/api/v1/till/sessions",
        "allowed": [
            "SALES_CASHIER",
            "CASHIER",
            "SALES_STAFF",
            "STORE_MANAGER",
            "AREA_MANAGER",
            "ADMIN",
            "SUPERADMIN",
        ],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/till/sessions/{session_id}/blind-submit",
        "allowed": [
            "SALES_CASHIER",
            "CASHIER",
            "SALES_STAFF",
            "STORE_MANAGER",
            "AREA_MANAGER",
            "ADMIN",
            "SUPERADMIN",
        ],
        "store_scoped": True,
    },
    {
        # Reveal variance + soft-lock the Z-Read: managers + above only.
        "method": "POST",
        "path": "/api/v1/till/sessions/{session_id}/lock",
        "allowed": ["STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"],
        "store_scoped": True,
    },
    {
        # Release the transparent soft-lock (mandatory reason + E2 reopen-role set
        # re-checked in the service): managers + above.
        "method": "POST",
        "path": "/api/v1/till/sessions/{session_id}/reopen",
        "allowed": ["STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"],
        "store_scoped": True,
    },
    {
        # Session list reveals expected/variance -> manager/finance read roles.
        "method": "GET",
        "path": "/api/v1/till/sessions",
        "allowed": [
            "STORE_MANAGER",
            "AREA_MANAGER",
            "ADMIN",
            "ACCOUNTANT",
            "SUPERADMIN",
        ],
        "store_scoped": True,
    },
    {
        # One session: reachable by the OPERATE set (cashiers get a redacted
        # view; manager sees the full figures). The handler fail-closes on
        # _TILL_OPERATE_ROLES which EXCLUDES ACCOUNTANT -- this row matches that
        # (ACCOUNTANT reads the Z-Read via /zread + the session list, not here).
        "method": "GET",
        "path": "/api/v1/till/sessions/{session_id}",
        "allowed": [
            "SALES_CASHIER",
            "CASHIER",
            "SALES_STAFF",
            "STORE_MANAGER",
            "AREA_MANAGER",
            "ADMIN",
            "SUPERADMIN",
        ],
        "store_scoped": True,
    },
    {
        # Full Z-Read (reveals expected) -> manager/finance read roles only.
        "method": "GET",
        "path": "/api/v1/till/sessions/{session_id}/zread",
        "allowed": [
            "STORE_MANAGER",
            "AREA_MANAGER",
            "ADMIN",
            "ACCOUNTANT",
            "SUPERADMIN",
        ],
        "store_scoped": True,
    },
    # --- Feature #16 Bank / Cash / POS reconciliation (own /api/v1/bank-recon
    # prefix; finance + store management only -- a cashier can NEVER run or sign
    # off a reconciliation; every route store-scopes via validate_store_access). ---
    {
        "method": "POST",
        "path": "/api/v1/bank-recon/reconciliations",
        "allowed": [
            "ACCOUNTANT",
            "STORE_MANAGER",
            "AREA_MANAGER",
            "ADMIN",
            "SUPERADMIN",
        ],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/bank-recon/reconciliations",
        "allowed": [
            "ACCOUNTANT",
            "STORE_MANAGER",
            "AREA_MANAGER",
            "ADMIN",
            "SUPERADMIN",
        ],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/bank-recon/reconciliations/{run_id}",
        "allowed": [
            "ACCOUNTANT",
            "STORE_MANAGER",
            "AREA_MANAGER",
            "ADMIN",
            "SUPERADMIN",
        ],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/bank-recon/reconciliations/{run_id}/lock",
        "allowed": [
            "ACCOUNTANT",
            "STORE_MANAGER",
            "AREA_MANAGER",
            "ADMIN",
            "SUPERADMIN",
        ],
        "store_scoped": True,
    },
    {
        # Sign-off is a management attestation: managers + finance, NOT a cashier.
        "method": "POST",
        "path": "/api/v1/bank-recon/reconciliations/{run_id}/sign-off",
        "allowed": [
            "ACCOUNTANT",
            "STORE_MANAGER",
            "AREA_MANAGER",
            "ADMIN",
            "SUPERADMIN",
        ],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/bank-recon/bank-lines",
        "allowed": [
            "ACCOUNTANT",
            "STORE_MANAGER",
            "AREA_MANAGER",
            "ADMIN",
            "SUPERADMIN",
        ],
        "store_scoped": True,
    },
    # --- Feature #14 Non-adaptation / remake tracking (own /api/v1/non-adapt
    # prefix; clinical + store management record + remake -- a cashier/sales role
    # can never record or initiate a (possibly waived) remake; every route
    # store-scopes). ---
    {
        "method": "POST",
        "path": "/api/v1/non-adapt/record",
        "allowed": [
            "OPTOMETRIST",
            "STORE_MANAGER",
            "AREA_MANAGER",
            "ADMIN",
            "SUPERADMIN",
        ],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/non-adapt/{record_id}/remake",
        "allowed": [
            "OPTOMETRIST",
            "STORE_MANAGER",
            "AREA_MANAGER",
            "ADMIN",
            "SUPERADMIN",
        ],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/non-adapt/order/{order_id}",
        "allowed": [
            "OPTOMETRIST",
            "STORE_MANAGER",
            "AREA_MANAGER",
            "ADMIN",
            "SUPERADMIN",
        ],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/non-adapt/{record_id}",
        "allowed": [
            "OPTOMETRIST",
            "STORE_MANAGER",
            "AREA_MANAGER",
            "ADMIN",
            "SUPERADMIN",
        ],
        "store_scoped": True,
    },
    {
        # Quality report (counts by reason/optometrist/brand) -> management + finance.
        "method": "GET",
        "path": "/api/v1/non-adapt",
        "allowed": [
            "STORE_MANAGER",
            "AREA_MANAGER",
            "ADMIN",
            "ACCOUNTANT",
            "SUPERADMIN",
        ],
        "store_scoped": True,
    },
    # --- Feature #6 per-unit serial tracking (own /api/v1/serials prefix; a
    # cashier can NEVER mint/relabel/recall a serial -- only read a warranty;
    # every route store-scopes). ---
    {
        "method": "POST",
        "path": "/api/v1/serials/capture",
        "allowed": [
            "CATALOG_MANAGER",
            "STORE_MANAGER",
            "AREA_MANAGER",
            "ADMIN",
            "SUPERADMIN",
        ],
        "store_scoped": True,
    },
    {
        # At-sale IN_STOCK->SOLD: a system/manager action (driven by order finalize).
        "method": "POST",
        "path": "/api/v1/serials/{serial}/mark-sold",
        "allowed": ["STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/serials/{serial}/recall",
        "allowed": ["STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/serials/{serial}/return",
        "allowed": [
            "CATALOG_MANAGER",
            "STORE_MANAGER",
            "AREA_MANAGER",
            "ADMIN",
            "SUPERADMIN",
        ],
        "store_scoped": True,
    },
    {
        # Warranty lookup -> any store staff (read-only; a cashier CAN check a warranty).
        "method": "GET",
        "path": "/api/v1/serials/warranty/{serial}",
        "allowed": [
            "SALES_CASHIER",
            "CASHIER",
            "SALES_STAFF",
            "OPTOMETRIST",
            "CATALOG_MANAGER",
            "STORE_MANAGER",
            "AREA_MANAGER",
            "ACCOUNTANT",
            "ADMIN",
            "SUPERADMIN",
        ],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/serials/{serial}",
        "allowed": [
            "SALES_CASHIER",
            "CASHIER",
            "SALES_STAFF",
            "OPTOMETRIST",
            "CATALOG_MANAGER",
            "STORE_MANAGER",
            "AREA_MANAGER",
            "ACCOUNTANT",
            "ADMIN",
            "SUPERADMIN",
        ],
        "store_scoped": True,
    },
    # --- Feature #49 family/household loyalty wallet (own /api/v1/family-wallet
    # prefix). Manager+ creates households / edits members (enrolment changes who
    # can spend a shared balance); the POS money family redeems (OTP-gated to the
    # PRIMARY member's mobile); any store staff reads. store_scoped: False on ALL
    # rows BY OWNER DECISION -- household lookup + pool redemption are chain-wide
    # (mirrors chain-wide customer-lookup + voucher-redeem); the household only
    # records its creating store for provenance. ---
    {
        "method": "POST",
        "path": "/api/v1/family-wallet/households",
        "allowed": ["STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"],
        "store_scoped": False,
    },
    {
        "method": "POST",
        "path": "/api/v1/family-wallet/households/{household_id}/members",
        "allowed": ["STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"],
        "store_scoped": False,
    },
    {
        "method": "DELETE",
        "path": "/api/v1/family-wallet/households/{household_id}/members/{customer_id}",
        "allowed": ["STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"],
        "store_scoped": False,
    },
    {
        # Chain-wide household lookup by member customer (owner decision).
        "method": "GET",
        "path": "/api/v1/family-wallet/households/by-customer/{customer_id}",
        "allowed": [
            "SALES_CASHIER",
            "CASHIER",
            "SALES_STAFF",
            "OPTOMETRIST",
            "CATALOG_MANAGER",
            "STORE_MANAGER",
            "AREA_MANAGER",
            "ACCOUNTANT",
            "ADMIN",
            "SUPERADMIN",
        ],
        "store_scoped": False,
    },
    {
        "method": "GET",
        "path": "/api/v1/family-wallet/households/{household_id}",
        "allowed": [
            "SALES_CASHIER",
            "CASHIER",
            "SALES_STAFF",
            "OPTOMETRIST",
            "CATALOG_MANAGER",
            "STORE_MANAGER",
            "AREA_MANAGER",
            "ACCOUNTANT",
            "ADMIN",
            "SUPERADMIN",
        ],
        "store_scoped": False,
    },
    {
        # Manual/store-driven pool earn (manager+; idempotent per order ref).
        # The POS auto-earn hook stays OWNER-GATED -- this is the day-1 funder.
        "method": "POST",
        "path": "/api/v1/family-wallet/households/{household_id}/earn",
        "allowed": ["STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"],
        "store_scoped": False,
    },
    {
        # OTP issue to the PRIMARY member's mobile (reminder_rail slice; the
        # cashier-initiated counter flow -- standalone, NOT the POS order path).
        "method": "POST",
        "path": "/api/v1/family-wallet/households/{household_id}/redeem/request-otp",
        "allowed": [
            "SALES_CASHIER",
            "SALES_STAFF",
            "CASHIER",
            "STORE_MANAGER",
            "AREA_MANAGER",
            "ADMIN",
            "SUPERADMIN",
        ],
        "store_scoped": False,
    },
    {
        # OTP-verified pool debit -> mints a store-credit voucher. Chain-wide
        # redeem BY OWNER DECISION (mirrors voucher redeem).
        "method": "POST",
        "path": "/api/v1/family-wallet/households/{household_id}/redeem",
        "allowed": [
            "SALES_CASHIER",
            "SALES_STAFF",
            "CASHIER",
            "STORE_MANAGER",
            "AREA_MANAGER",
            "ADMIN",
            "SUPERADMIN",
        ],
        "store_scoped": False,
    },
    # --- Feature #15 blind stock take (own /api/v1/blind-count prefix). Floor
    # staff / inventory OPEN + SUBMIT counts BLIND (never see the system on-hand
    # pre-lock -- enforced at the data layer); only a manager REVEALS variance +
    # soft-locks + reopens + proposes an adjustment. Every route store-scopes;
    # propose only ENQUEUES a reversible proposal, never mutates on-hand. ---
    {
        "method": "POST",
        "path": "/api/v1/blind-count/open",
        "allowed": [
            "SALES_STAFF",
            "SALES_CASHIER",
            "CATALOG_MANAGER",
            "STORE_MANAGER",
            "AREA_MANAGER",
            "ADMIN",
            "SUPERADMIN",
        ],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/blind-count/{session_id}/submit",
        "allowed": [
            "SALES_STAFF",
            "SALES_CASHIER",
            "CATALOG_MANAGER",
            "STORE_MANAGER",
            "AREA_MANAGER",
            "ADMIN",
            "SUPERADMIN",
        ],
        "store_scoped": True,
    },
    {
        # Reveal variance + soft-lock: manager+ only (a counter can NEVER reveal).
        "method": "POST",
        "path": "/api/v1/blind-count/{session_id}/lock",
        "allowed": ["STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"],
        "store_scoped": True,
    },
    {
        # Reopen a locked count (mandatory reason, audited): manager+ only.
        "method": "POST",
        "path": "/api/v1/blind-count/{session_id}/reopen",
        "allowed": ["STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"],
        "store_scoped": True,
    },
    {
        # Enqueue a reversible stock-adjustment PROPOSAL: manager+ only.
        "method": "POST",
        "path": "/api/v1/blind-count/{session_id}/propose-adjustment",
        "allowed": ["STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"],
        "store_scoped": True,
    },
    {
        # Read one session -- counter sees the BLIND-redacted view pre-lock.
        "method": "GET",
        "path": "/api/v1/blind-count/{session_id}",
        "allowed": [
            "SALES_STAFF",
            "SALES_CASHIER",
            "CATALOG_MANAGER",
            "STORE_MANAGER",
            "AREA_MANAGER",
            "ADMIN",
            "SUPERADMIN",
        ],
        "store_scoped": True,
    },
    # --- Feature #48 multi-category servicing & repair portal (own /api/v1/repairs
    # prefix). Catalog edits = CATALOG_MANAGER+; intake + lifecycle transitions =
    # store staff family; reads = any store staff. Every route store-scopes. ---
    {
        "method": "GET",
        "path": "/api/v1/repairs/catalog",
        "allowed": [
            "SALES_STAFF",
            "SALES_CASHIER",
            "CASHIER",
            "OPTOMETRIST",
            "CATALOG_MANAGER",
            "STORE_MANAGER",
            "AREA_MANAGER",
            "ACCOUNTANT",
            "ADMIN",
            "SUPERADMIN",
        ],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/repairs/catalog",
        "allowed": ["CATALOG_MANAGER", "ADMIN", "SUPERADMIN"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/repairs/catalog/{service_id}",
        "allowed": ["CATALOG_MANAGER", "ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/repairs/jobs",
        "allowed": [
            "SALES_STAFF",
            "SALES_CASHIER",
            "STORE_MANAGER",
            "AREA_MANAGER",
            "ADMIN",
            "SUPERADMIN",
        ],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/repairs/jobs/{job_id}/transition",
        "allowed": [
            "SALES_STAFF",
            "SALES_CASHIER",
            "STORE_MANAGER",
            "AREA_MANAGER",
            "ADMIN",
            "SUPERADMIN",
        ],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/repairs/jobs",
        "allowed": [
            "SALES_STAFF",
            "SALES_CASHIER",
            "CASHIER",
            "OPTOMETRIST",
            "CATALOG_MANAGER",
            "STORE_MANAGER",
            "AREA_MANAGER",
            "ACCOUNTANT",
            "ADMIN",
            "SUPERADMIN",
        ],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/repairs/jobs/{job_id}",
        "allowed": [
            "SALES_STAFF",
            "SALES_CASHIER",
            "CASHIER",
            "OPTOMETRIST",
            "CATALOG_MANAGER",
            "STORE_MANAGER",
            "AREA_MANAGER",
            "ACCOUNTANT",
            "ADMIN",
            "SUPERADMIN",
        ],
        "store_scoped": True,
    },
    # --- Feature #29 skills-based rostering (shares /api/v1/hr; mounted without
    # the HR finance gate). All stores clinical -> every shift needs optometrist
    # coverage; NO licence-expiry machinery. Roster/skills edits = management;
    # reads = management + staff family. Store-scoped where a store_id is given. ---
    {
        "method": "GET",
        "path": "/api/v1/hr/staff-skills",
        "allowed": [
            "SALES_STAFF",
            "SALES_CASHIER",
            "OPTOMETRIST",
            "STORE_MANAGER",
            "AREA_MANAGER",
            "ADMIN",
            "SUPERADMIN",
        ],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/hr/staff-skills/{employee_id}",
        "allowed": [
            "SALES_STAFF",
            "SALES_CASHIER",
            "OPTOMETRIST",
            "STORE_MANAGER",
            "AREA_MANAGER",
            "ADMIN",
            "SUPERADMIN",
        ],
    },
    {
        "method": "PUT",
        "path": "/api/v1/hr/staff-skills/{employee_id}",
        "allowed": ["STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/hr/roster",
        "allowed": ["STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/hr/roster",
        "allowed": [
            "SALES_STAFF",
            "SALES_CASHIER",
            "OPTOMETRIST",
            "STORE_MANAGER",
            "AREA_MANAGER",
            "ADMIN",
            "SUPERADMIN",
        ],
        "store_scoped": True,
    },
    {
        "method": "PUT",
        "path": "/api/v1/hr/roster/{roster_id}",
        "allowed": ["STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/hr/roster/{roster_id}/coverage",
        "allowed": [
            "SALES_STAFF",
            "SALES_CASHIER",
            "OPTOMETRIST",
            "STORE_MANAGER",
            "AREA_MANAGER",
            "ADMIN",
            "SUPERADMIN",
        ],
        "store_scoped": True,
    },
    # --- Feature #38 endless aisle (own /api/v1/endless-aisle prefix). All
    # STORE_MANAGER+; behind endless_aisle.enabled (off -> 403). Source-accept
    # 2-step; company-borne shipping; store-scoped per route. ---
    {
        "method": "GET",
        "path": "/api/v1/endless-aisle/availability",
        "allowed": ["STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/endless-aisle/requests",
        "allowed": ["STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/endless-aisle/requests/{request_id}/accept",
        "allowed": ["STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/endless-aisle/requests/{request_id}/reject",
        "allowed": ["STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/endless-aisle/requests/{request_id}/create-transfer",
        "allowed": ["STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/endless-aisle/requests/{request_id}/ship",
        "allowed": ["STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/endless-aisle/requests/{request_id}/deliver",
        "allowed": ["STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/endless-aisle/requests",
        "allowed": ["STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/endless-aisle/requests/{request_id}",
        "allowed": ["STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"],
        "store_scoped": True,
    },
    # --- Feature #18 vendor volume-rebate tracker (own /api/v1/vendor-rebates).
    # Finance roles only (mirrors vendor bills/AP). Manual-post; reduces vendor AP. ---
    {
        "method": "POST",
        "path": "/api/v1/vendor-rebates/agreements",
        "allowed": ["ACCOUNTANT", "ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/vendor-rebates/agreements",
        "allowed": ["ACCOUNTANT", "ADMIN", "SUPERADMIN"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/vendor-rebates/agreements/{agreement_id}",
        "allowed": ["ACCOUNTANT", "ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/vendor-rebates/agreements/{agreement_id}/preview",
        "allowed": ["ACCOUNTANT", "ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/vendor-rebates/post",
        "allowed": ["ACCOUNTANT", "ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/vendor-rebates/ledger",
        "allowed": ["ACCOUNTANT", "ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/vendor-rebates/ledger/{rebate_id}",
        "allowed": ["ACCOUNTANT", "ADMIN", "SUPERADMIN"],
    },
    # Feature #1 cross-store inventory balancing (read-only proposals). Management
    # only; the route itself store-scopes the OUTPUT for a single-store manager.
    {
        "method": "GET",
        "path": "/api/v1/inventory-balancing/proposals",
        "allowed": ["STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"],
        "store_scoped": True,
    },
    # --- Feature N7 CL/lens PO generator (own /api/v1/cl-po prefix). Drafts
    # vendor-grouped DRAFT purchase orders whose lines carry the power cell
    # (sph/cyl/add) from Base-Bank replenishment / lens-stock gap-planner data.
    # dry_run=True default; never SENT; manager-ladder only, store-scoped. ---
    {
        "method": "POST",
        "path": "/api/v1/cl-po/generate",
        "allowed": ["STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/finance/revenue",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/finance/summary-month",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    # F34 target ticker. The GET is mounted on a SEPARATE router WITHOUT the
    # finance role gate so EVERY authenticated role can reach it (the response is
    # privacy-stratified server-side -- floor roles get pct only, no rupees). The
    # settings POST is SUPERADMIN/ADMIN only.
    {
        "method": "GET",
        "path": "/api/v1/finance/target-ticker",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/finance/target-ticker/settings",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/finance/tally/sales-jv",
        # Org-wide sales-voucher export = finance-admin only (owner decision
        # 2026-06-16; handler enforces _require_finance_admin).
        "allowed": ["ACCOUNTANT", "ADMIN", "SUPERADMIN"],
    },
    # --- B2B invoices -> Tally (accountant export console + worklist). Every
    # endpoint enforces _require_finance_admin inline -> finance-admin only
    # (ACCOUNTANT/ADMIN/SUPERADMIN); e-invoice + e-way are issued in Tally. ---
    {
        "method": "GET",
        "path": "/api/v1/finance/b2b-invoices",
        "allowed": ["ACCOUNTANT", "ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/finance/b2b-invoices/{order_id}/tally-xml",
        "allowed": ["ACCOUNTANT", "ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/finance/b2b-invoices/export",
        "allowed": ["ACCOUNTANT", "ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/finance/b2b-invoices/mark-exported",
        "allowed": ["ACCOUNTANT", "ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/finance/b2b-invoices/{order_id}/mark-done",
        "allowed": ["ACCOUNTANT", "ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/finance/b2b-invoices/{order_id}/attention-note",
        "allowed": ["ACCOUNTANT", "ADMIN", "SUPERADMIN"],
    },
    # E5 wiring: tender-routed Receipt voucher, sibling of the sales-JV export
    # (same finance role set -- finance-admin only). DARK by default -- the
    # handler additionally 403s until policy tally.tender_receipt_voucher is on.
    {
        "method": "GET",
        "path": "/api/v1/finance/tally/tender-receipt-jv",
        "allowed": ["ACCOUNTANT", "ADMIN", "SUPERADMIN"],
    },
    # --- F17/#25 maker-checker journal entries (mounted on /finance behind the
    # finance role gate; each handler narrows further inline -- create/submit to
    # the JE-maker set, approve/post/reject/reverse to ADMIN/SUPERADMIN, COA POST
    # to SUPERADMIN). The maker-checker PIN + single-use is the shared E4 engine. ---
    {
        "method": "POST",
        "path": "/api/v1/finance/journal-entries",
        "allowed": ["ACCOUNTANT", "ADMIN", "SUPERADMIN"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/finance/journal-entries",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/finance/journal-entries/{je_id}",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/finance/journal-entries/{je_id}/submit",
        "allowed": ["ACCOUNTANT", "ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/finance/journal-entries/{je_id}/approve",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/finance/journal-entries/{je_id}/reject",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/finance/journal-entries/{je_id}/post",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/finance/journal-entries/{je_id}/reverse",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/finance/chart-of-accounts",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/finance/chart-of-accounts",
        "allowed": ["SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/finance/tally/journal-jv",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/finance/vendor-payments",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    # FIN-1: GST e-invoice (IRN generation). Narrower than the router-level finance
    # gate (no AREA_MANAGER / STORE_MANAGER; matching the inline role check in
    # the handler). DARK by default -- returns SIMULATED until owner enables.
    {
        "method": "POST",
        "path": "/api/v1/finance/einvoice/{order_id}",
        "allowed": ["ACCOUNTANT", "ADMIN", "SUPERADMIN"],
    },
    # FIND-5: Bank statement import + auto-reconciliation
    {
        "method": "POST",
        "path": "/api/v1/finance/bank-statement/import",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/finance/bank-statement",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/finance/bank-statement/{statement_id}",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    # --- /api/v1/follow-ups ---
    {"method": "POST", "path": "/api/v1/follow-ups", "allowed": "AUTHENTICATED"},
    {"method": "GET", "path": "/api/v1/follow-ups/", "allowed": "AUTHENTICATED"},
    {"method": "POST", "path": "/api/v1/follow-ups/", "allowed": "AUTHENTICATED"},
    {
        "method": "POST",
        "path": "/api/v1/follow-ups/auto-generate",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/follow-ups/due-today",
        "allowed": "AUTHENTICATED",
    },
    {"method": "GET", "path": "/api/v1/follow-ups/summary", "allowed": "AUTHENTICATED"},
    {
        "method": "PATCH",
        "path": "/api/v1/follow-ups/{follow_up_id}/complete",
        "allowed": "AUTHENTICATED",
    },
    # --- /api/v1/handoffs ---
    {"method": "POST", "path": "/api/v1/handoffs", "allowed": "AUTHENTICATED"},
    {"method": "POST", "path": "/api/v1/handoffs/", "allowed": "AUTHENTICATED"},
    # F50 -- clinical->retail handover (CLINICAL_RX). The inbox is gated to the
    # sales floor + their managers (require_roles(*_CLINICAL_INBOX_ROLES)); the
    # acknowledge / mark-served actions to the floor + store manager
    # (require_roles(*_CLINICAL_ACTION_ROLES)). SUPERADMIN implicit. Recipient
    # ownership + store scope enforced in the handler.
    {
        "method": "GET",
        "path": "/api/v1/handoffs/clinical-inbox",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "SALES_CASHIER",
            "SALES_STAFF",
            "STORE_MANAGER",
            "SUPERADMIN",
        ],
        "store_scoped": True,
    },
    {
        "method": "PATCH",
        "path": "/api/v1/handoffs/{handoff_id}/acknowledge",
        "allowed": ["SALES_CASHIER", "SALES_STAFF", "STORE_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "PATCH",
        "path": "/api/v1/handoffs/{handoff_id}/mark-served",
        "allowed": ["SALES_CASHIER", "SALES_STAFF", "STORE_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/handoffs/eligible-recipients/list",
        "allowed": "AUTHENTICATED",
    },
    {"method": "GET", "path": "/api/v1/handoffs/inbox", "allowed": "AUTHENTICATED"},
    {"method": "GET", "path": "/api/v1/handoffs/sent", "allowed": "AUTHENTICATED"},
    {
        "method": "DELETE",
        "path": "/api/v1/handoffs/{handoff_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/handoffs/{handoff_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/handoffs/{handoff_id}/dismiss",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/handoffs/{handoff_id}/file",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/handoffs/{handoff_id}/reshare",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/handoffs/{handoff_id}/respond",
        "allowed": "AUTHENTICATED",
    },
    # --- /api/v1/health ---
    {"method": "GET", "path": "/api/v1/health", "allowed": "PUBLIC"},
    # --- /api/v1/hr employee self-service (hr_self_service_router) ---
    # Mounted at /api/v1/hr but OUTSIDE the _FINANCE_ROLES gate so any logged-in
    # staff member can read their OWN attendance / leaves / payslip / commission.
    # Each route is pinned to the requesting user (no employee_id param) -> self.
    {
        "method": "GET",
        "path": "/api/v1/hr/me/attendance",
        "allowed": AUTHENTICATED,
        "self_enforced": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/hr/me/leaves",
        "allowed": AUTHENTICATED,
        "self_enforced": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/hr/me/payslip",
        "allowed": AUTHENTICATED,
        "self_enforced": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/hr/me/commission",
        "allowed": AUTHENTICATED,
        "self_enforced": True,
    },
    # --- /api/v1/hr ---
    {
        "method": "GET",
        "path": "/api/v1/hr",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/hr/",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/hr/attendance",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/hr/attendance-compliance",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/hr/attendance/check-in",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/hr/attendance/check-out",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/hr/attendance/grid",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/hr/attendance/late-marks",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/hr/attendance/mark",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/hr/attendance/summary",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    # Manager attendance CORRECTION is a stronger action than read/mark: gated to
    # SUPERADMIN/ADMIN/STORE_MANAGER (require_roles('ADMIN','STORE_MANAGER') +
    # SUPERADMIN auto) on top of the router-level finance gate, and audit-logged.
    {
        "method": "PUT",
        "path": "/api/v1/hr/attendance/{attendance_id}",
        "allowed": ["ADMIN", "STORE_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/hr/attendance/{attendance_id}/check-out",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/hr/employee/{employee_id}/salary-slip",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    # Employee onboarding documents (govt-ID + HR paperwork). SENSITIVE PII --
    # owner directive: SUPERADMIN + ADMIN ONLY (SUPERADMIN auto-passes the
    # middleware). The route handlers gate with require_roles("ADMIN"); each also
    # runs a per-employee store-scope check on top.
    {
        "method": "POST",
        "path": "/api/v1/hr/employees/{employee_id}/documents",
        "allowed": ["ADMIN"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/hr/employees/{employee_id}/documents",
        "allowed": ["ADMIN"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/hr/employees/{employee_id}/documents/{doc_id}",
        "allowed": ["ADMIN"],
        "store_scoped": True,
    },
    {
        "method": "DELETE",
        "path": "/api/v1/hr/employees/{employee_id}/documents/{doc_id}",
        "allowed": ["ADMIN"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/hr/leaves",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/hr/leaves",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/hr/leaves/balance/{employee_id}",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/hr/leaves/{leave_id}/approve",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/hr/leaves/{leave_id}/reject",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        # F26 remote fast-path leave approval (consumes an E4 approval token).
        # Reachable via the hr router's finance-role gate; the per-route gate
        # (_SWAP_APPROVER_ROLES) further 403s ACCOUNTANT, mirroring the sibling
        # /approve + /reject rows.
        "method": "POST",
        "path": "/api/v1/hr/leaves/{leave_id}/approve-remote",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/hr/payroll",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/hr/payroll/generate",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/hr/payroll/{payroll_id}/approve",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/hr/reports/lwp",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/hr/shifts",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/hr/shifts",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/hr/shifts/assign",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/hr/summary-today",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/hr/weekoff-swaps",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/hr/weekoff-swaps",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/hr/weekoff-swaps/{swap_id}/approve",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/hr/weekoff-swaps/{swap_id}/reject",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    # --- /api/v1/incentive ---
    {
        "method": "POST",
        "path": "/api/v1/incentive/kicker/product-sale",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/incentive/kicker/{ym}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/incentive/points/daily",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/incentive/points/daily",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/incentive/points/daily/",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/incentive/points/daily/",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/incentive/points/daily/bulk",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "DELETE",
        "path": "/api/v1/incentive/points/daily/{log_id}",
        "allowed": ["STORE_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/incentive/points/inputs/last-year-sale",
        "allowed": [
            "ACCOUNTANT",
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "SUPERADMIN",
        ],
    },
    {
        "method": "GET",
        "path": "/api/v1/incentive/points/leaderboard",
        "allowed": "AUTHENTICATED",
    },
    # F33 — leaderboard display layer. POST settings is the only write.
    # The org/area scope widening on GET /leaderboard + /mtd is a
    # data-conditional 403 inside the handler (not expressible here).
    {
        "method": "POST",
        "path": "/api/v1/incentive/points/leaderboard/settings",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/incentive/points/leaderboard/titles",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/incentive/points/mtd",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/incentive/points/settings/eligibility",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "PATCH",
        "path": "/api/v1/incentive/points/settings/eligibility",
        "allowed": ["SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/incentive/points/settings/effective",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "PATCH",
        "path": "/api/v1/incentive/points/settings/payout",
        "allowed": ["SUPERADMIN"],
    },
    {
        "method": "PATCH",
        "path": "/api/v1/incentive/points/settings/scope",
        "allowed": ["SUPERADMIN"],
    },
    {
        "method": "PATCH",
        "path": "/api/v1/incentive/points/settings/visufit-gate",
        "allowed": ["SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/incentive/points/staff/{staff_id}/history",
        "allowed": "AUTHENTICATED",
    },
    # --- /api/v1/inventory ---
    {"method": "GET", "path": "/api/v1/inventory", "allowed": "PUBLIC"},
    {"method": "GET", "path": "/api/v1/inventory/", "allowed": "PUBLIC"},
    {
        "method": "GET",
        "path": "/api/v1/inventory/accountability",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "CATALOG_MANAGER",
            "STORE_MANAGER",
            "WORKSHOP_STAFF",
        ],
    },
    {
        "method": "POST",
        "path": "/api/v1/inventory/accountability",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/inventory/accountability/shrinkage",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/inventory/aging",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/inventory/alerts",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/inventory/barcode/{barcode}",
        "allowed": "AUTHENTICATED",
    },
    # INV-12: barcode lifecycle trace (purchase->sale->transfer->return)
    {
        "method": "GET",
        "path": "/api/v1/inventory/barcode/{barcode}/trace",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/inventory/contact-lenses",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/inventory/contact-lenses/expiry-status",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/inventory/contact-lenses/power-grid",
        "allowed": "AUTHENTICATED",
    },
    {"method": "GET", "path": "/api/v1/inventory/expiring", "allowed": "AUTHENTICATED"},
    {
        "method": "GET",
        "path": "/api/v1/inventory/lenses/power-grid",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/inventory/low-stock",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/inventory/non-moving",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/inventory/opening-stock/commit",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "CATALOG_MANAGER",
            "STORE_MANAGER",
            "WORKSHOP_STAFF",
        ],
    },
    {
        "method": "POST",
        "path": "/api/v1/inventory/opening-stock/preview",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "CATALOG_MANAGER",
            "STORE_MANAGER",
            "WORKSHOP_STAFF",
        ],
    },
    {
        "method": "GET",
        "path": "/api/v1/inventory/overstock-analysis",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/inventory/sell-through-analysis",
        "allowed": "AUTHENTICATED",
    },
    # ------------------------------------------------------------------
    # E3 item-event ledger (/api/v1/items). Reads are store-scoped to any
    # authenticated role; quarantine + serial-bind + sell are inventory/
    # manager-ladder writes; Base-Bank target writes are the store-manager
    # ladder. The SELL event is additionally feature-flagged OFF (FF_E3_POS_SELL).
    # ------------------------------------------------------------------
    {
        "method": "GET",
        "path": "/api/v1/items/{stock_id}/events",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/items/events",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/items/{stock_id}/quarantine",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/items/{stock_id}/quarantine/release",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/items/{stock_id}/serial-bind",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "CATALOG_MANAGER",
            "WORKSHOP_STAFF",
        ],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/items/{stock_id}/sell",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "CATALOG_MANAGER",
            "WORKSHOP_STAFF",
            "SALES_CASHIER",
            "SALES_STAFF",
        ],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/items/base-bank",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/items/base-bank",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/items/replenishment",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/inventory/serials",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/inventory/serials",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "CATALOG_MANAGER",
            "STORE_MANAGER",
            "WORKSHOP_STAFF",
        ],
        "store_scoped": True,
    },
    {
        "method": "PATCH",
        "path": "/api/v1/inventory/serials/{serial_id}",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "CATALOG_MANAGER",
            "STORE_MANAGER",
            "WORKSHOP_STAFF",
        ],
    },
    {
        "method": "GET",
        "path": "/api/v1/inventory/stock",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    # F21 -- defective quarantine lifecycle (manager-ladder only; queue read also
    # for ACCOUNTANT). store_scoped: a store role only sees / acts on its store.
    {
        "method": "GET",
        "path": "/api/v1/inventory/stock/quarantined",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT"],
        "store_scoped": True,
    },
    {
        "method": "PATCH",
        "path": "/api/v1/inventory/stock/{stock_id}/quarantine",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "PATCH",
        "path": "/api/v1/inventory/stock/{stock_id}/lift-quarantine",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/inventory/stock-count",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/inventory/stock-count-scan",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "CATALOG_MANAGER",
            "STORE_MANAGER",
            "WORKSHOP_STAFF",
        ],
    },
    {
        "method": "GET",
        "path": "/api/v1/inventory/stock-count-status",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/inventory/stock-count/start",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "CATALOG_MANAGER",
            "STORE_MANAGER",
            "WORKSHOP_STAFF",
        ],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/inventory/stock-count/{count_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/inventory/stock-count/{count_id}/complete",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "CATALOG_MANAGER",
            "STORE_MANAGER",
            "WORKSHOP_STAFF",
        ],
    },
    {
        "method": "POST",
        "path": "/api/v1/inventory/stock-count/{count_id}/items",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "CATALOG_MANAGER",
            "STORE_MANAGER",
            "WORKSHOP_STAFF",
        ],
    },
    {
        "method": "POST",
        "path": "/api/v1/inventory/stock-count/{count_id}/reconcile",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "CATALOG_MANAGER",
            "STORE_MANAGER",
            "WORKSHOP_STAFF",
        ],
    },
    {
        "method": "POST",
        "path": "/api/v1/inventory/stock/add",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "CATALOG_MANAGER",
            "STORE_MANAGER",
            "WORKSHOP_STAFF",
        ],
    },
    {
        "method": "GET",
        "path": "/api/v1/inventory/stock/barcode/{barcode}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/inventory/transfer-recommendations",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "CATALOG_MANAGER",
            "STORE_MANAGER",
            "WORKSHOP_STAFF",
        ],
    },
    # POS-7: BOPIS / ship-from-store cross-store stock lookup
    {
        "method": "GET",
        "path": "/api/v1/inventory/cross-store-stock",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "CATALOG_MANAGER",
            "SALES_CASHIER",
            "SALES_STAFF",
            "STORE_MANAGER",
            "SUPERADMIN",
        ],
    },
    {
        "method": "GET",
        "path": "/api/v1/inventory/transfers",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/inventory/transfers",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "CATALOG_MANAGER",
            "STORE_MANAGER",
            "WORKSHOP_STAFF",
        ],
    },
    # BUG-018: /api/v1/inventory/transfers/{transfer_id}/receive and .../send
    # were dead-stub endpoints (returned fake success, moved no stock) and have
    # been REMOVED. The real, stock-moving workflow is at
    # POST /api/v1/transfers/{transfer_id}/ship and .../receive (catalogued
    # below under "/api/v1/transfers"). No policy rows are needed for routes that
    # no longer exist (test_no_stale_policy_entries enforces this).
    # --- /api/v1/jarvis ---
    {"method": "GET", "path": "/api/v1/jarvis", "allowed": ["SUPERADMIN"]},
    {"method": "GET", "path": "/api/v1/jarvis/", "allowed": ["SUPERADMIN"]},
    {"method": "GET", "path": "/api/v1/jarvis/agents", "allowed": ["SUPERADMIN"]},
    {
        "method": "GET",
        "path": "/api/v1/jarvis/agents/activity",
        "allowed": ["SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/jarvis/agents/diagnostic",
        "allowed": ["SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/jarvis/agents/health-history",
        "allowed": ["SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/jarvis/agents/pixel/audits",
        "allowed": ["SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/jarvis/agents/reseed",
        "allowed": ["SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/jarvis/agents/run-all",
        "allowed": ["SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/jarvis/agents/sentinel/health",
        "allowed": ["SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/jarvis/agents/timeline",
        "allowed": ["SUPERADMIN"],
    },
    {
        "method": "PATCH",
        "path": "/api/v1/jarvis/agents/{agent_id}/config",
        "allowed": ["SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/jarvis/agents/{agent_id}/logs",
        "allowed": ["SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/jarvis/agents/{agent_id}/run",
        "allowed": ["SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/jarvis/agents/{agent_id}/run-now",
        "allowed": ["SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/jarvis/agents/{agent_id}/status",
        "allowed": ["SUPERADMIN"],
    },
    {
        "method": "PATCH",
        "path": "/api/v1/jarvis/agents/{agent_id}/toggle",
        "allowed": ["SUPERADMIN"],
    },
    {"method": "GET", "path": "/api/v1/jarvis/alerts", "allowed": ["SUPERADMIN"]},
    {"method": "POST", "path": "/api/v1/jarvis/analyze", "allowed": ["SUPERADMIN"]},
    {"method": "POST", "path": "/api/v1/jarvis/command", "allowed": ["SUPERADMIN"]},
    {"method": "GET", "path": "/api/v1/jarvis/dashboard", "allowed": ["SUPERADMIN"]},
    {
        "method": "GET",
        "path": "/api/v1/jarvis/data/collections",
        "allowed": ["SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/jarvis/data/{collection}",
        "allowed": ["SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/jarvis/integrations/status",
        "allowed": ["SUPERADMIN"],
    },
    {"method": "GET", "path": "/api/v1/jarvis/models", "allowed": ["SUPERADMIN"]},
    # #7 predictive purchasing: the proposal review queue is SUPERADMIN + ADMIN
    # (DECISIONS). self_enforced is still auto-applied by the /jarvis/ prefix
    # below, so a DENIED role (AREA_MANAGER and down) keeps the route's 404
    # existence-hiding response - the middleware defers, the route's
    # require_superadmin_or_admin guard returns 404.
    {
        "method": "GET",
        "path": "/api/v1/jarvis/proposals",
        "allowed": ["SUPERADMIN", "ADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/jarvis/proposals/{proposal_id}",
        "allowed": ["SUPERADMIN", "ADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/jarvis/proposals/{proposal_id}/approve",
        "allowed": ["SUPERADMIN", "ADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/jarvis/proposals/{proposal_id}/reject",
        "allowed": ["SUPERADMIN", "ADMIN"],
    },
    {"method": "POST", "path": "/api/v1/jarvis/query", "allowed": ["SUPERADMIN"]},
    {
        "method": "GET",
        "path": "/api/v1/jarvis/quick-insights",
        "allowed": ["SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/jarvis/recommendations",
        "allowed": ["SUPERADMIN"],
    },
    {"method": "GET", "path": "/api/v1/jarvis/status", "allowed": ["SUPERADMIN"]},
    # --- /api/v1/lens-catalog ---
    {"method": "GET", "path": "/api/v1/lens-catalog", "allowed": "AUTHENTICATED"},
    {
        "method": "POST",
        "path": "/api/v1/lens-catalog",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
    {"method": "GET", "path": "/api/v1/lens-catalog/", "allowed": "AUTHENTICATED"},
    {
        "method": "POST",
        "path": "/api/v1/lens-catalog/",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/lens-catalog/meta/options",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "DELETE",
        "path": "/api/v1/lens-catalog/{lens_line_id}",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/lens-catalog/{lens_line_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "PATCH",
        "path": "/api/v1/lens-catalog/{lens_line_id}",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
    # --- /api/v1/lens-enums ---
    {"method": "GET", "path": "/api/v1/lens-enums", "allowed": "AUTHENTICATED"},
    {"method": "GET", "path": "/api/v1/lens-enums/", "allowed": "AUTHENTICATED"},
    {
        "method": "GET",
        "path": "/api/v1/lens-enums/{enum_type}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "PATCH",
        "path": "/api/v1/lens-enums/{enum_type}",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/lens-enums/{enum_type}/items",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/lens-enums/{enum_type}/items/{item}",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/lens-enums/{enum_type}/rename",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
    # --- /api/v1/catalog-field-options (Settings -> Catalog Dictionary) ---
    {
        "method": "GET",
        "path": "/api/v1/products/brand-options",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/catalog-field-options",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/catalog-field-options/",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "PATCH",
        "path": "/api/v1/catalog-field-options/{field_name}",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
    # --- /api/v1/lens-stock ---
    {
        "method": "POST",
        "path": "/api/v1/lens-stock",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/lens-stock/",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/lens-stock/audit/{line_stock_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/lens-stock/cell/{line_stock_id}",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/lens-stock/gap-planner",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/lens-stock/{lens_line_id}",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/lens-stock/{lens_line_id}/bulk-import",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/lens-stock/{lens_line_id}/commit",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/lens-stock/{lens_line_id}/release",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/lens-stock/{lens_line_id}/reserve",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "PATCH",
        "path": "/api/v1/lens-stock/{line_stock_id}",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    # --- /api/v1/loyalty ---
    {
        "method": "GET",
        "path": "/api/v1/loyalty/account/{customer_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/loyalty/account/{customer_id}/ledger",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/loyalty/adjust",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    # IDOR/value-trust hardening: earn + redeem MOVE MONEY (points), so they
    # are gated to the POS payment family (loyalty._POS_ROLES) -- the same
    # role set as POST /vouchers/{code}/redeem. earn additionally derives its
    # rupee basis from the order server-side (route-level, not expressible
    # here). SUPERADMIN passes via check_access.
    {
        "method": "POST",
        "path": "/api/v1/loyalty/earn",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "CASHIER",
            "SALES_CASHIER",
            "SALES_STAFF",
            "STORE_MANAGER",
        ],
    },
    {
        "method": "POST",
        "path": "/api/v1/loyalty/expire",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/loyalty/program-stats",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/loyalty/redeem",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "CASHIER",
            "SALES_CASHIER",
            "SALES_STAFF",
            "STORE_MANAGER",
        ],
    },
    {"method": "GET", "path": "/api/v1/loyalty/settings", "allowed": "AUTHENTICATED"},
    {"method": "PUT", "path": "/api/v1/loyalty/settings", "allowed": ["SUPERADMIN"]},
    # CRM-13: Loyalty reward catalog. READ is open to all authenticated staff
    # (so cashiers can describe rewards at POS); writes are gated to managers.
    {"method": "GET", "path": "/api/v1/loyalty/rewards", "allowed": "AUTHENTICATED"},
    {
        "method": "POST",
        "path": "/api/v1/loyalty/rewards",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/loyalty/rewards/{reward_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "PUT",
        "path": "/api/v1/loyalty/rewards/{reward_id}",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/loyalty/rewards/{reward_id}",
        "allowed": ["ADMIN"],
    },
    # --- /api/v1/marketing ---
    {
        "method": "GET",
        "path": "/api/v1/marketing/consent-text",
        "allowed": "AUTHENTICATED",
    },
    {"method": "PUT", "path": "/api/v1/marketing/consent-text", "allowed": ["ADMIN"]},
    # Campaign layer (routers/campaigns.py): ADMIN/AREA_MANAGER/STORE_MANAGER
    # (SUPERADMIN implicit). Campaign-specific routes additionally restrict a
    # store-scoped campaign (one carrying a store_id) to that store via
    # _enforce_store_scope -> validate_store_access; hence store_scoped=True.
    {
        "method": "GET",
        "path": "/api/v1/marketing/campaigns",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/marketing/campaigns",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/marketing/campaigns/{campaign_id}",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "PUT",
        "path": "/api/v1/marketing/campaigns/{campaign_id}",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "DELETE",
        "path": "/api/v1/marketing/campaigns/{campaign_id}",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/marketing/campaigns/{campaign_id}/duplicate",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/marketing/campaigns/{campaign_id}/schedule",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/marketing/campaigns/{campaign_id}/pause",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/marketing/campaigns/{campaign_id}/resume",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/marketing/campaigns/{campaign_id}/send",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/marketing/campaigns/{campaign_id}/analytics",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/marketing/segments",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/marketing/segments/{key}/preview",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/marketing/notifications/logs",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/marketing/notifications/send",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/marketing/notifications/send-bulk",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/marketing/nps-dashboard",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/marketing/nps-response",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/marketing/nps-survey/{order_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/marketing/referral-invite/{customer_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/marketing/referrals",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/marketing/referrals/{referral_id}/redeem",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/marketing/review-request/{order_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/marketing/rx-expiry-alerts",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/marketing/rx-reminder/{customer_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/marketing/rx-snooze/{customer_id}",
        "allowed": "AUTHENTICATED",
    },
    {"method": "POST", "path": "/api/v1/marketing/walkin", "allowed": "AUTHENTICATED"},
    {"method": "GET", "path": "/api/v1/marketing/walkins", "allowed": "AUTHENTICATED"},
    # F45 D1 -- RETIRED to HTTP 410 Gone (zombie duplicate of /api/v1/walkouts).
    # The routes remain registered (returning 410) so coverage-lock + no-stale
    # stay green; the canonical 30-field walkout path is /api/v1/walkouts. A
    # logged-in caller simply receives 410 -- AUTHENTICATED is correct here.
    {
        "method": "GET",
        "path": "/api/v1/marketing/walkout-recoveries",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/marketing/walkout/{customer_id}",
        "allowed": "AUTHENTICATED",
    },
    # CRM-8: Promo offer-template library (BOGO / COMBO / THRESHOLD).
    # Same role gate as campaigns (ADMIN/AREA_MANAGER/STORE_MANAGER, SUPERADMIN
    # implicit).  Store-scoped templates additionally validated inside the handler
    # via _enforce_store_scope -> validate_store_access.
    {
        "method": "GET",
        "path": "/api/v1/marketing/promo-templates",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/marketing/promo-templates",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/marketing/promo-templates/{template_id}",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "PUT",
        "path": "/api/v1/marketing/promo-templates/{template_id}",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "DELETE",
        "path": "/api/v1/marketing/promo-templates/{template_id}",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    # --- /api/v1/promotions (F11/F12 advanced promotions + bundling engine) ---
    # WRITE (create/update/deactivate): ADMIN/SUPERADMIN + CATALOG_MANAGER (pricing
    # visibility) + AREA/STORE managers (store-scoped inside the handler).
    # READ + the pure /evaluate preview: the write roles plus ACCOUNTANT (margin)
    # and the POS staff who see what applied. uses_count is never client-settable.
    {
        "method": "GET",
        "path": "/api/v1/promotions",
        "allowed": [
            "ADMIN", "AREA_MANAGER", "STORE_MANAGER", "CATALOG_MANAGER",
            "ACCOUNTANT", "SALES_CASHIER", "SALES_STAFF", "CASHIER",
        ],
    },
    {
        "method": "GET",
        "path": "/api/v1/promotions/",
        "allowed": [
            "ADMIN", "AREA_MANAGER", "STORE_MANAGER", "CATALOG_MANAGER",
            "ACCOUNTANT", "SALES_CASHIER", "SALES_STAFF", "CASHIER",
        ],
    },
    {
        "method": "POST",
        "path": "/api/v1/promotions",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER", "CATALOG_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/promotions/",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER", "CATALOG_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/promotions/evaluate",
        "allowed": [
            "ADMIN", "AREA_MANAGER", "STORE_MANAGER", "CATALOG_MANAGER",
            "ACCOUNTANT", "SALES_CASHIER", "SALES_STAFF", "CASHIER",
        ],
    },
    {
        "method": "GET",
        "path": "/api/v1/promotions/{promo_id}",
        "allowed": [
            "ADMIN", "AREA_MANAGER", "STORE_MANAGER", "CATALOG_MANAGER",
            "ACCOUNTANT", "SALES_CASHIER", "SALES_STAFF", "CASHIER",
        ],
    },
    {
        "method": "PUT",
        "path": "/api/v1/promotions/{promo_id}",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER", "CATALOG_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "DELETE",
        "path": "/api/v1/promotions/{promo_id}",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER", "CATALOG_MANAGER"],
        "store_scoped": True,
    },
    # CRM-15: WhatsApp opt-in / opt-out STOP ledger.
    # Any authenticated staff can record a consent event (staff relay verbal
    # opt-out from customers).  The full audit ledger is ADMIN-only (compliance).
    {
        "method": "POST",
        "path": "/api/v1/marketing/whatsapp-consent",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/marketing/whatsapp-consent/{customer_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/marketing/whatsapp-consent-ledger",
        "allowed": ["ADMIN"],
    },
    # CRM-16: Ad Performance dashboard (Google + Meta). Finance-sensitive:
    # restricted to ADMIN and SUPERADMIN (SUPERADMIN implicit via require_roles).
    {
        "method": "GET",
        "path": "/api/v1/marketing/ad-performance",
        "allowed": ["SUPERADMIN", "ADMIN"],
    },
    # --- /api/v1/notifications ---
    {"method": "GET", "path": "/api/v1/notifications", "allowed": "AUTHENTICATED"},
    {"method": "GET", "path": "/api/v1/notifications/", "allowed": "AUTHENTICATED"},
    {
        "method": "POST",
        "path": "/api/v1/notifications/mark-all-read",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/notifications/unread-count",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "PATCH",
        "path": "/api/v1/notifications/{notification_id}/read",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/notifications/{notification_id}/snooze",
        "allowed": "AUTHENTICATED",
    },
    # --- /api/v1/online-store ---  (BVI Phase 1: Online Store module skeleton)
    {
        "method": "GET",
        "path": "/api/v1/online-store/summary",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    # --- /api/v1/online-store/stock-tally ---  (BVI Phase 5: read-only reconcile)
    # Per online-listed SKU: online-listed vs on-hand vs reserved vs sellable +
    # an oversell-risk flag + a conservative buffer suggestion. STRICTLY read-only
    # (never mutates/reserves stock); the write-path allocation is a deferred
    # follow-up. Same ecom role set. See routers/online_store.py.
    {
        "method": "GET",
        "path": "/api/v1/online-store/stock-tally",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    # Store health readiness dashboard (BVI Phase 5 "Store health" card):
    # orphan SKUs, attribute coverage, barcode match + a composite readiness
    # score. Read-only + fail-soft; same ecom role set as the module summary.
    # See routers/online_store.py + services/store_health.py.
    {
        "method": "GET",
        "path": "/api/v1/online-store/store-health",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    # --- /api/v1/collections ---  (unification step-13: materialised collection
    # BROWSE). Read-only, fast-path over the collection_products materialised
    # view. AUTHENTICATED -- same posture as GET /products + GET /catalog/products
    # (an internal-app catalogue browse, not the role-gated admin editor under
    # /online-store/collections). See routers/collections_browse.py.
    {"method": "GET", "path": "/api/v1/collections", "allowed": "AUTHENTICATED"},
    {
        "method": "GET",
        "path": "/api/v1/collections/{handle}/products",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/collections/{handle}/refresh",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    # --- /api/v1/online-store/collections ---  (BVI Phase 2: Collections, FLAGSHIP #1)
    # PUSH-DARK ecom_collections CRUD + manual/smart membership + smart-rule
    # resolver. All gated to the ecom role set (router-level require_roles); see
    # routers/online_store_collections.py + BVI_MERGE_PLAN.md Phase 2.
    {
        "method": "GET",
        "path": "/api/v1/online-store/collections",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/online-store/collections",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/online-store/collections/{collection_id}",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/online-store/collections/{collection_id}",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/online-store/collections/{collection_id}",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/online-store/collections/{collection_id}/products",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/online-store/collections/{collection_id}/products",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/online-store/collections/{collection_id}/products/{sku}",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/online-store/collections/{collection_id}/products/reorder",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/online-store/collections/{collection_id}/resolved-products",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    # --- /api/v1/online-store/menus ---  (BVI Phase 3: Menus / Mega-menu, FLAGSHIP #2)
    # PUSH-DARK ecom_menus CRUD + an embedded recursive item-tree editor
    # (add/move/remove/reorder/patch nodes). All gated to the ecom role set
    # (router-level require_roles); see routers/online_store_menus.py +
    # BVI_MERGE_PLAN.md Phase 3. The literal .../items/reorder + .../items/{item_id}/move
    # routes are more specific than .../items/{item_id} (policy_for ranks fewest-params
    # first), so they resolve correctly.
    {
        "method": "GET",
        "path": "/api/v1/online-store/menus",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/online-store/menus",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/online-store/menus/{menu_id}",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/online-store/menus/{menu_id}",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/online-store/menus/{menu_id}",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/online-store/menus/{menu_id}/items",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/online-store/menus/{menu_id}/items/reorder",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/online-store/menus/{menu_id}/items/{item_id}/move",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/online-store/menus/{menu_id}/items/{item_id}",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/online-store/menus/{menu_id}/items/{item_id}",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    # --- /api/v1/online-store/images ---  (BVI Phase 4: Image Design Queue, FLAGSHIP #3)
    # PUSH-DARK product_images CRUD + the RAW->EDITED->APPROVED design lifecycle
    # (assign / status / attach-edited). All gated to the ecom role set
    # (router-level require_roles); see routers/online_store_images.py +
    # BVI_MERGE_PLAN.md Phase 4. The literal action sub-paths .../{image_id}/assign,
    # .../{image_id}/status, .../{image_id}/edited are more specific than the bare
    # .../{image_id} route (policy_for ranks fewest-params then longest first), so
    # they resolve to their own entries. APPROVE writes a chained audit_logs row.
    {
        "method": "GET",
        "path": "/api/v1/online-store/images",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/online-store/images",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        # Phase 4a: durable multipart image UPLOAD -> object_storage (S3 seam,
        # fail-soft to local in dev). Literal /upload out-ranks the {image_id}
        # param route in the policy matcher. Audit-logged action IMAGE_UPLOAD.
        "method": "POST",
        "path": "/api/v1/online-store/images/upload",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/online-store/images/{image_id}",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/online-store/images/{image_id}",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/online-store/images/{image_id}",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/online-store/images/{image_id}/assign",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/online-store/images/{image_id}/status",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/online-store/images/{image_id}/edited",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/online-store/images/{image_id}/auto-edit",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    # --- /api/v1/online-store/push ---  (BVI Phase 5: IMS -> Shopify GraphQL PUSH)
    # The IMS->Shopify push for product/collection/menu/image + a status surface.
    # BUILT DARK: every push is SIMULATED (dry-run, no network) unless
    # IMS_SHOPIFY_WRITES on AND DISPATCH_MODE=live AND creds present (per #262 BVI
    # is the single Shopify writer until the Phase-6 cutover). UNLIKE the rest of
    # the Online Store module this surface is NARROWED to SUPERADMIN/ADMIN ONLY
    # (integration-critical -- pushing to the live storefront). Each push writes a
    # chained audit_logs row. See routers/online_store_push.py + BVI_MERGE_PLAN.md
    # Phase 5. The literal /status route is more specific than the /{entity}/{id}
    # POST routes and resolves on its own; the four POST routes carry one param each.
    {
        "method": "GET",
        "path": "/api/v1/online-store/push/status",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/online-store/push/product/{product_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/online-store/push/collection/{collection_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/online-store/push/menu/{menu_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/online-store/push/image/{image_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/online-store/push/all-pending",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    # --- /api/v1/online-store/orders ---  (BVI Phase 3b: online sales into IMS books)
    # The read + recovery surface over the canonical IMS orders that
    # online_order_mapper creates from Shopify orders (channel='ONLINE', GST invoice
    # minted, counted once by Finance/P&L). GET list is also for the ACCOUNTANT (it
    # reads the books); POST remap MUTATES/re-creates an order so it is narrowed to
    # SUPERADMIN/ADMIN. The router mounts the list at both ''/'/' so both concrete
    # paths are catalogued. A remap writes a chained audit_logs row. See
    # routers/online_store_orders.py + BVI_MERGE_PLAN.md Phase 3.
    {
        "method": "GET",
        "path": "/api/v1/online-store/orders",
        "allowed": ["ADMIN", "ACCOUNTANT", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/online-store/orders/",
        "allowed": ["ADMIN", "ACCOUNTANT", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/online-store/orders/remap/{shopify_order_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    # --- /api/v1/ondc ---  (BVI-20: ONDC Seller Node scaffolding -- DARK default)
    # Callback endpoints are PUBLIC (Beckn protocol; SNP signature-gated when
    # config.ukp is set). Admin routes require SUPERADMIN / ADMIN.
    # See backend/api/services/ondc_seller.py + backend/api/routers/ondc.py.
    {"method": "POST", "path": "/api/v1/ondc/on_search", "allowed": "PUBLIC"},
    {"method": "POST", "path": "/api/v1/ondc/on_select", "allowed": "PUBLIC"},
    {"method": "POST", "path": "/api/v1/ondc/on_init", "allowed": "PUBLIC"},
    {"method": "POST", "path": "/api/v1/ondc/on_confirm", "allowed": "PUBLIC"},
    {"method": "POST", "path": "/api/v1/ondc/on_status", "allowed": "PUBLIC"},
    {"method": "POST", "path": "/api/v1/ondc/on_cancel", "allowed": "PUBLIC"},
    {
        "method": "GET",
        "path": "/api/v1/ondc/status",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/ondc/publish",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    # --- /api/v1/orders ---
    {
        "method": "GET",
        "path": "/api/v1/orders",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/orders",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "SALES_CASHIER",
            "SALES_STAFF",
            "STORE_MANAGER",
            "SUPERADMIN",
        ],
    },
    {
        "method": "GET",
        "path": "/api/v1/orders/overdue/list",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/orders/pending/delivery",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/orders/sales/summary",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/orders/search",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/orders/status/counts",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/orders/unpaid/list",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/orders/{order_id}",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {"method": "PUT", "path": "/api/v1/orders/{order_id}", "allowed": "AUTHENTICATED"},
    # Cancelling a sale is POS-tier (mirrors POST /orders' POS_WRITE_ROLES
    # in-function gate in orders.py::cancel_order -- keep the two in sync).
    {
        "method": "POST",
        "path": "/api/v1/orders/{order_id}/cancel",
        "allowed": [
            "SUPERADMIN",
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "SALES_CASHIER",
            "SALES_STAFF",
        ],
    },
    {
        "method": "POST",
        "path": "/api/v1/orders/{order_id}/confirm",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/orders/{order_id}/deliver",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/orders/{order_id}/invoice",
        "allowed": "AUTHENTICATED",
    },
    # #16: SUPERADMIN-only post-creation order/invoice edit. Catalogued
    # AUTHENTICATED here; the real gate is the in-function _require_superadmin
    # in orders.py (same pattern as cancel_order) -- keep the two in sync.
    {
        "method": "PUT",
        "path": "/api/v1/orders/{order_id}/superadmin-edit",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "PUT",
        "path": "/api/v1/orders/{order_id}/superadmin-invoice-change",
        "allowed": "AUTHENTICATED",
    },
    # POS-7: BOPIS ship-from-store transfer creation
    {
        "method": "POST",
        "path": "/api/v1/orders/{order_id}/bopis-transfer",
        "allowed": "AUTHENTICATED",
    },
    # POS-6: UPI QR code for an order (any authenticated POS user may request it)
    {
        "method": "GET",
        "path": "/api/v1/orders/{order_id}/upi-qr",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/orders/{order_id}/items",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "DELETE",
        "path": "/api/v1/orders/{order_id}/items/{item_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/orders/{order_id}/payments",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/orders/{order_id}/ready",
        "allowed": "AUTHENTICATED",
    },
    # --- /api/v1/payout ---
    {
        "method": "GET",
        "path": "/api/v1/payout/export/{snapshot_id}.csv",
        "allowed": "AUTHENTICATED",
    },
    {"method": "POST", "path": "/api/v1/payout/lock", "allowed": ["SUPERADMIN"]},
    {
        "method": "GET",
        "path": "/api/v1/payout/payroll-feed",
        "allowed": ["ACCOUNTANT", "ADMIN", "SUPERADMIN"],
    },
    {"method": "GET", "path": "/api/v1/payout/preview", "allowed": "AUTHENTICATED"},
    {
        "method": "GET",
        "path": "/api/v1/payout/snapshot/{snapshot_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "PATCH",
        "path": "/api/v1/payout/snapshot/{snapshot_id}/mark-paid",
        "allowed": ["SUPERADMIN"],
    },
    {"method": "GET", "path": "/api/v1/payout/snapshots", "allowed": "AUTHENTICATED"},
    # --- /api/v1/payroll ---
    {
        "method": "GET",
        "path": "/api/v1/payroll",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/payroll/",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/payroll/advances",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/payroll/advances/{advance_id}/settle",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/payroll/advances/{employee_id}",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/payroll/approve",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/payroll/config",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {"method": "POST", "path": "/api/v1/payroll/config", "allowed": ["ADMIN"]},
    {"method": "POST", "path": "/api/v1/payroll/config/bulk", "allowed": ["ADMIN"]},
    {
        "method": "GET",
        "path": "/api/v1/payroll/config/{employee_id}",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/payroll/config/{employee_id}",
        "allowed": ["ADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/payroll/incentive-summary/{employee_id}/{month}/{year}",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {"method": "POST", "path": "/api/v1/payroll/lock", "allowed": ["ADMIN"]},
    {
        "method": "GET",
        "path": "/api/v1/payroll/payslip/{employee_id}",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/payroll/payslip/{employee_id}/{month}/{year}",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/payroll/payslip/{employee_id}/{month}/{year}/print",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/payroll/pt-slabs",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {"method": "POST", "path": "/api/v1/payroll/pt-slabs/seed", "allowed": ["ADMIN"]},
    {
        "method": "GET",
        "path": "/api/v1/payroll/pt-slabs/{state_code}",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/payroll/pt-slabs/{state_code}",
        "allowed": ["ADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/payroll/registers/pf-ecr",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/payroll/registers/summary",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/payroll/run",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/payroll/run/rows",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/payroll/salary-sheet",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/payroll/commission/summary",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/payroll/commission/leaderboard",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/payroll/salary/calculate",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/payroll/salary/{employee_id}",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/payroll/tally/salary-jv",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    # --- /api/v1/portal ---
    {"method": "GET", "path": "/api/v1/portal/rx", "allowed": "PUBLIC"},
    {"method": "POST", "path": "/api/v1/portal/rx/request-otp", "allowed": "PUBLIC"},
    {"method": "POST", "path": "/api/v1/portal/rx/verify-otp", "allowed": "PUBLIC"},
    {"method": "GET", "path": "/api/v1/portal/track/{token}", "allowed": "PUBLIC"},
    # --- /api/v1/prescriptions ---
    {"method": "GET", "path": "/api/v1/prescriptions", "allowed": "AUTHENTICATED"},
    {
        "method": "POST",
        "path": "/api/v1/prescriptions",
        "allowed": ["ADMIN", "OPTOMETRIST", "STORE_MANAGER", "SUPERADMIN"],
        "self_enforced": True,
    },
    {"method": "GET", "path": "/api/v1/prescriptions/", "allowed": "AUTHENTICATED"},
    {
        "method": "POST",
        "path": "/api/v1/prescriptions/",
        "allowed": ["ADMIN", "OPTOMETRIST", "STORE_MANAGER", "SUPERADMIN"],
        "self_enforced": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/prescriptions/customer/{customer_id}/progression",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/prescriptions/expiring",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/prescriptions/optometrist/{optometrist_id}/stats",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/prescriptions/patient/{patient_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/prescriptions/patient/{patient_id}/latest",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/prescriptions/patient/{patient_id}/valid",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/prescriptions/family/{customer_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/prescriptions/{prescription_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "PUT",
        "path": "/api/v1/prescriptions/{prescription_id}",
        "allowed": ["ADMIN", "OPTOMETRIST", "STORE_MANAGER", "SUPERADMIN"],
        "self_enforced": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/prescriptions/{prescription_id}/finalize",
        "allowed": ["ADMIN", "OPTOMETRIST", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/prescriptions/{prescription_id}/print",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/prescriptions/{prescription_id}/validate",
        "allowed": "AUTHENTICATED",
    },
    # Version PATCH writes clinical Rx data -> same gate as PUT /{id}
    # (update_prescription); self_enforced because the route raises the
    # body-specific clinical 403 the enforcer must not override.
    {
        "method": "PATCH",
        "path": "/api/v1/prescriptions/{prescription_id}/version/{version_name}",
        "allowed": ["ADMIN", "OPTOMETRIST", "STORE_MANAGER", "SUPERADMIN"],
        "self_enforced": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/prescriptions/{prescription_id}/versions",
        "allowed": "AUTHENTICATED",
    },
    # --- /api/v1/labels (F21 quarantine sticker) ---
    {
        "method": "POST",
        "path": "/api/v1/labels/quarantine/{stock_id}",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    # --- /api/v1/print ---
    {"method": "GET", "path": "/api/v1/print/qz/cert", "allowed": "AUTHENTICATED"},
    {"method": "POST", "path": "/api/v1/print/qz/sign", "allowed": "AUTHENTICATED"},
    # Delivery-challan HTML render: POS-capable roles + ACCOUNTANT (read-only,
    # store-scoped via validate_store_access / transfer access guard).
    {
        "method": "GET",
        "path": "/api/v1/print/delivery-challan/order/{order_id}",
        "allowed": [
            "SUPERADMIN",
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "SALES_CASHIER",
            "SALES_STAFF",
            "ACCOUNTANT",
        ],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/print/delivery-challan/transfer/{transfer_id}",
        "allowed": [
            "SUPERADMIN",
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "SALES_CASHIER",
            "SALES_STAFF",
            "ACCOUNTANT",
        ],
        "store_scoped": True,
    },
    # --- /api/v1/print-overrides ---
    {"method": "GET", "path": "/api/v1/print-overrides", "allowed": "AUTHENTICATED"},
    {
        "method": "GET",
        "path": "/api/v1/print-overrides/_meta/templates",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "DELETE",
        "path": "/api/v1/print-overrides/{entity_id}/{template_key}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/print-overrides/{entity_id}/{template_key}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "PUT",
        "path": "/api/v1/print-overrides/{entity_id}/{template_key}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    # --- /api/v1/product-templates ---
    {
        "method": "GET",
        "path": "/api/v1/product-templates",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/product-templates",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/product-templates/",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/product-templates/",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/product-templates/{template_id}",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
    # --- /api/v1/products ---
    {"method": "GET", "path": "/api/v1/products", "allowed": "AUTHENTICATED"},
    {
        "method": "POST",
        "path": "/api/v1/products",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/products/brands/list",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/products/bulk-create",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
    {
        # Hub Phase 4 clone-and-vary -> N DRAFT variants.
        "method": "POST",
        "path": "/api/v1/products/clone-vary",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/products/bulk-offer",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/products/bulk-price",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/products/categories/list",
        "allowed": "AUTHENTICATED",
    },
    # Step-12: known product tags (filter + autocomplete). AUTHENTICATED, same as
    # the sibling brands/categories list endpoints.
    {
        "method": "GET",
        "path": "/api/v1/products/tags/list",
        "allowed": "AUTHENTICATED",
    },
    # --- PM (N5) unified product-master sub-paths (router product_master.py) ---
    {
        "method": "GET",
        "path": "/api/v1/products/master/categories",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/products/master/categories/{category}/fields",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/products/sku-preview",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/products/master",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/products/master/{product_id}",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
    {"method": "GET", "path": "/api/v1/products/gst-rates", "allowed": "AUTHENTICATED"},
    # Product-image upload/serve (GridFS-backed). Upload is a catalog-mutation
    # (write) gated to the catalog roles; the serve endpoint is PUBLIC because
    # the returned URL is embedded in <img> tags that carry no auth header and
    # product photos are non-sensitive catalog media.
    {
        "method": "POST",
        "path": "/api/v1/products/image",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
    # Autopilot v2 image RE-HOST: server-side copies an external (brand-site)
    # image into our GridFS so products never hotlink. Same catalog write gate
    # as the multipart upload; the fetch itself is SSRF-hardened in
    # services/image_rehost.py (private/loopback/metadata ranges blocked).
    {
        "method": "POST",
        "path": "/api/v1/products/image/from-url",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
    # Background-removal edit: re-runs the DETERMINISTIC cut-out pipeline
    # (Photoroom) on a previously-uploaded product image and persists the
    # cleaned result as a NEW image. Same catalog-write gate as the upload.
    {
        "method": "POST",
        "path": "/api/v1/products/image/{file_id}/edit",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/products/image/{file_id}",
        "allowed": "PUBLIC",
    },
    {"method": "GET", "path": "/api/v1/products/sku/{sku}", "allowed": "AUTHENTICATED"},
    {
        "method": "GET",
        "path": "/api/v1/products/{product_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "PUT",
        "path": "/api/v1/products/{product_id}",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
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
    # --- /api/v1/shipping ---
    {"method": "GET", "path": "/api/v1/shipping/shipments", "allowed": "AUTHENTICATED"},
    {
        "method": "POST",
        "path": "/api/v1/shipping/shipments",
        # SALES_CASHIER merged into SALES_STAFF (backlog #12); mirrors
        # shipping.py._FULFILMENT_ROLES.
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "CASHIER",
            "SALES_STAFF",
            "STORE_MANAGER",
        ],
    },
    {
        "method": "GET",
        "path": "/api/v1/shipping/shipments/{shipment_id}/track",
        "allowed": "AUTHENTICATED",
    },
    # --- /api/v1/stores ---
    {"method": "GET", "path": "/api/v1/stores", "allowed": "AUTHENTICATED"},
    {"method": "POST", "path": "/api/v1/stores", "allowed": ["ADMIN", "SUPERADMIN"]},
    {
        "method": "GET",
        "path": "/api/v1/stores/go-live-checklist",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {"method": "GET", "path": "/api/v1/stores/summary", "allowed": "AUTHENTICATED"},
    {
        "method": "DELETE",
        "path": "/api/v1/stores/{store_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/stores/{store_id}",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "PUT",
        "path": "/api/v1/stores/{store_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/stores/{store_id}/categories/{category}",
        "allowed": ["ADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/stores/{store_id}/categories/{category}",
        "allowed": ["ADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/stores/{store_id}/stats",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/stores/{store_id}/users",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    # --- /api/v1/tasks ---
    {"method": "GET", "path": "/api/v1/tasks", "allowed": "AUTHENTICATED"},
    {"method": "POST", "path": "/api/v1/tasks", "allowed": "AUTHENTICATED"},
    {"method": "GET", "path": "/api/v1/tasks/", "allowed": "AUTHENTICATED"},
    {"method": "POST", "path": "/api/v1/tasks/", "allowed": "AUTHENTICATED"},
    {
        "method": "POST",
        "path": "/api/v1/tasks/auto-escalate-overdue",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/tasks/auto-generate",
        "allowed": "AUTHENTICATED",
    },
    {"method": "GET", "path": "/api/v1/tasks/checklists", "allowed": "AUTHENTICATED"},
    {
        "method": "POST",
        "path": "/api/v1/tasks/checklists/{checklist_type}/complete-item",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/tasks/completion-stats",
        "allowed": "AUTHENTICATED",
    },
    {"method": "GET", "path": "/api/v1/tasks/escalations", "allowed": "AUTHENTICATED"},
    {
        "method": "GET",
        "path": "/api/v1/tasks/integrity/fake-closures",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/tasks/integrity/silent",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {"method": "GET", "path": "/api/v1/tasks/my-tasks", "allowed": "AUTHENTICATED"},
    {"method": "GET", "path": "/api/v1/tasks/overdue", "allowed": "AUTHENTICATED"},
    {
        "method": "POST",
        "path": "/api/v1/tasks/scan/payment-variance",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {"method": "GET", "path": "/api/v1/tasks/sla-config", "allowed": "AUTHENTICATED"},
    {
        "method": "PUT",
        "path": "/api/v1/tasks/sla-config",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/tasks/sop-checklist",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/tasks/sop-checklist/item",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/tasks/sop-templates",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/tasks/sop-templates",
        "allowed": ["ADMIN", "STORE_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/tasks/sop-templates/",
        "allowed": ["ADMIN", "STORE_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/tasks/sop-templates/seed-defaults",
        "allowed": ["ADMIN", "STORE_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/tasks/sop-templates/{template_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/tasks/sop-templates/{template_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "PATCH",
        "path": "/api/v1/tasks/sop-templates/{template_id}",
        "allowed": ["ADMIN", "STORE_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/tasks/sop-templates/{template_id}/assign",
        "allowed": ["ADMIN", "STORE_MANAGER", "SUPERADMIN"],
    },
    {"method": "GET", "path": "/api/v1/tasks/summary", "allowed": "AUTHENTICATED"},
    # #5: upload a task attachment (image/PDF <=25MB); any authenticated user.
    {
        "method": "POST",
        "path": "/api/v1/tasks/upload-file",
        "allowed": "AUTHENTICATED",
    },
    {"method": "GET", "path": "/api/v1/tasks/{task_id}", "allowed": "AUTHENTICATED"},
    {"method": "PATCH", "path": "/api/v1/tasks/{task_id}", "allowed": "AUTHENTICATED"},
    {"method": "PUT", "path": "/api/v1/tasks/{task_id}", "allowed": "AUTHENTICATED"},
    {
        "method": "POST",
        "path": "/api/v1/tasks/{task_id}/acknowledge",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "PATCH",
        "path": "/api/v1/tasks/{task_id}/complete",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/tasks/{task_id}/escalate",
        "allowed": "AUTHENTICATED",
    },
    # #5: download a task's attachment; in-function store-scope (anyone who can see the task).
    {
        "method": "GET",
        "path": "/api/v1/tasks/{task_id}/file",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/tasks/{task_id}/reassign",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/tasks/{task_id}/start",
        "allowed": "AUTHENTICATED",
    },
    # --- /api/v1/transfers ---
    {"method": "GET", "path": "/api/v1/transfers", "allowed": "AUTHENTICATED"},
    {
        "method": "POST",
        "path": "/api/v1/transfers",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER", "SUPERADMIN"],
    },
    {"method": "GET", "path": "/api/v1/transfers/", "allowed": "AUTHENTICATED"},
    {
        "method": "POST",
        "path": "/api/v1/transfers/",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/transfers/analytics/location/{location_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/transfers/analytics/summary",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/transfers/bulk-approve",
        "allowed": ["ADMIN", "AREA_MANAGER", "SUPERADMIN"],
    },
    {"method": "GET", "path": "/api/v1/transfers/pending", "allowed": "AUTHENTICATED"},
    {
        "method": "GET",
        "path": "/api/v1/transfers/{transfer_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "PUT",
        "path": "/api/v1/transfers/{transfer_id}",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/transfers/{transfer_id}/approve",
        "allowed": ["ADMIN", "AREA_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/transfers/{transfer_id}/cancel",
        "allowed": ["ADMIN", "AREA_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/transfers/{transfer_id}/complete",
        "allowed": ["ADMIN", "STORE_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/transfers/{transfer_id}/complete-picking",
        "allowed": ["ADMIN", "STORE_MANAGER", "SUPERADMIN", "WORKSHOP_STAFF"],
    },
    {
        "method": "POST",
        "path": "/api/v1/transfers/{transfer_id}/create-shiprocket-shipment",
        "allowed": ["ADMIN", "STORE_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/transfers/{transfer_id}/receive",
        "allowed": ["ADMIN", "STORE_MANAGER", "SUPERADMIN", "WORKSHOP_STAFF"],
    },
    {
        "method": "POST",
        "path": "/api/v1/transfers/{transfer_id}/ship",
        "allowed": ["ADMIN", "STORE_MANAGER", "SUPERADMIN", "WORKSHOP_STAFF"],
    },
    {
        "method": "POST",
        "path": "/api/v1/transfers/{transfer_id}/start-picking",
        "allowed": ["ADMIN", "STORE_MANAGER", "SUPERADMIN", "WORKSHOP_STAFF"],
    },
    {
        "method": "GET",
        "path": "/api/v1/transfers/{transfer_id}/tracking",
        "allowed": "AUTHENTICATED",
    },
    # --- /api/v1/users ---
    {"method": "POST", "path": "/api/v1/users", "allowed": ["ADMIN", "SUPERADMIN"]},
    # Per-user capability permissions editor + audit/revert (require_admin).
    {
        "method": "GET",
        "path": "/api/v1/users/permissions/options",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/users/{user_id}/permissions",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/users/{user_id}/permissions/revert",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/users/",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER", "SUPERADMIN"],
    },
    {"method": "POST", "path": "/api/v1/users/", "allowed": ["ADMIN", "SUPERADMIN"]},
    {
        "method": "GET",
        "path": "/api/v1/users/role/{role}",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/users/search",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/users/store/{store_id}",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/users/summary",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/users/{user_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/users/{user_id}",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/users/{user_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/users/{user_id}/assign-store",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/users/{user_id}/reset-password",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/users/{user_id}/roles/{role}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/users/{user_id}/roles/{role}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/users/{user_id}/stores/{store_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/users/{user_id}/stores/{store_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    # E4 approval-PIN management. PUT + GET-status are self-OR-admin (the handler
    # enforces self/admin inline), so AUTHENTICATED; DELETE (force-clear) is
    # ADMIN/SUPERADMIN only.
    {
        "method": "PUT",
        "path": "/api/v1/users/{user_id}/approval-pin",
        "allowed": AUTHENTICATED,
    },
    {
        "method": "DELETE",
        "path": "/api/v1/users/{user_id}/approval-pin",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/users/{user_id}/approval-pin/status",
        "allowed": AUTHENTICATED,
    },
    # --- /api/v1/vendor-portal ---
    {
        "method": "GET",
        "path": "/api/v1/vendor-portal/{token_id}/jobs",
        "allowed": "PUBLIC",
    },
    {
        "method": "GET",
        "path": "/api/v1/vendor-portal/{token_id}/jobs/{job_id}",
        "allowed": "PUBLIC",
    },
    {
        "method": "POST",
        "path": "/api/v1/vendor-portal/{token_id}/jobs/{job_id}/status",
        "allowed": "PUBLIC",
    },
    # --- /api/v1/vendor-returns ---
    {"method": "GET", "path": "/api/v1/vendor-returns", "allowed": "AUTHENTICATED"},
    {"method": "POST", "path": "/api/v1/vendor-returns", "allowed": "AUTHENTICATED"},
    {"method": "GET", "path": "/api/v1/vendor-returns/", "allowed": "AUTHENTICATED"},
    {"method": "POST", "path": "/api/v1/vendor-returns/", "allowed": "AUTHENTICATED"},
    {
        "method": "GET",
        "path": "/api/v1/vendor-returns/{return_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "PATCH",
        "path": "/api/v1/vendor-returns/{return_id}/status",
        "allowed": "AUTHENTICATED",
    },
    # --- /api/v1/vendor-rma (N4 Vendor RMA + credit-note reconciliation) ---
    # An RMA + its vendor credit note are financial instruments against a
    # vendor; create + every lifecycle transition is gated to the same vendor/AP
    # role set vendor_returns hardened to (SUPERADMIN implicit via require_roles).
    # GET list/detail are AUTHENTICATED but store-scoped per object in the
    # handler (validate_store_access / resolve_store_scope), so a cashier can
    # read but never authorize an RMA or record a credit.
    {
        "method": "GET",
        "path": "/api/v1/vendor-rma",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/vendor-rma/",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/vendor-rma",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/vendor-rma/",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/vendor-rma/{rma_id}",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/vendor-rma/{rma_id}/authorize",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/vendor-rma/{rma_id}/dispatch",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/vendor-rma/{rma_id}/credit-note",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/vendor-rma/{rma_id}/reject",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/vendor-rma/{rma_id}/close",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    # --- /api/v1/rtv-debit-notes (F20 GST debit note ON TOP of an RTV) ---
    # The GST-compliant debit-note DOCUMENT issued to a vendor when goods are
    # returned. Issuing + Tally export are gated to the same vendor/AP role set
    # vendor_returns / vendor_rma use (a cashier can NEVER issue a debit note).
    # GET list/detail/print are AUTHENTICATED but store-scoped per object in the
    # handler (validate_store_access / resolve_store_scope).
    {
        "method": "GET",
        "path": "/api/v1/rtv-debit-notes",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/rtv-debit-notes/",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/rtv-debit-notes/issue",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/rtv-debit-notes/{debit_note_id}",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/rtv-debit-notes/{debit_note_id}/print",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/rtv-debit-notes/{debit_note_id}/tally",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    # --- /api/v1/vendors ---
    {"method": "GET", "path": "/api/v1/vendors", "allowed": "AUTHENTICATED"},
    {
        "method": "POST",
        "path": "/api/v1/vendors",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {"method": "GET", "path": "/api/v1/vendors/", "allowed": "AUTHENTICATED"},
    {
        "method": "POST",
        "path": "/api/v1/vendors/",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/vendors/ap-aging",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    {"method": "GET", "path": "/api/v1/vendors/grn", "allowed": "AUTHENTICATED"},
    {
        "method": "POST",
        "path": "/api/v1/vendors/grn",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/vendors/grn/{grn_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/vendors/grn/{grn_id}/accept",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/vendors/grn/{grn_id}/escalate",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    # P1/S2: vendor-first goods-receipt cockpit (open POs + worklists for the
    # receiving screen). Same gate as receiving -- the receiving roles.
    {
        "method": "GET",
        "path": "/api/v1/vendors/goods-receipt/cockpit",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    # P1/S3: the ops user uploads the mandatory goods-receipt document (vendor
    # invoice/challan) here BEFORE creating the GRN. Same gate as creating the
    # GRN itself -- the receiving roles.
    {
        "method": "POST",
        "path": "/api/v1/vendors/grn/upload-doc",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    # P1/S3: stream the attached goods-receipt document (accountant recon links
    # here). Store-scoped object access inside the handler; the role gate is the
    # receiving + accounting roles.
    {
        "method": "GET",
        "path": "/api/v1/vendors/grn/{grn_id}/document",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    # Purchase Invoices (first-class AP+ITC; books the payable + ITC ledger).
    # Create/from-grn/book is an accounting action -> ACCOUNTANT/ADMIN; reads
    # are AUTHENTICATED. (SUPERADMIN auto-passes via require_roles.)
    {
        "method": "GET",
        "path": "/api/v1/vendors/purchase-invoices",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/vendors/purchase-invoices",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/vendors/purchase-invoices/",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/vendors/purchase-invoices/",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/vendors/purchase-invoices/from-grn/{grn_id}",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    # F9: consolidate N Delivery Challans into a draft bulk invoice (accounting
    # action -> ACCOUNTANT/ADMIN, same gate as from-grn).
    {
        "method": "GET",
        "path": "/api/v1/vendors/purchase-invoices/from-dcs",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    # F9: stored DC bulk-tally detail (accounting read -> ACCOUNTANT/ADMIN).
    {
        "method": "GET",
        "path": "/api/v1/vendors/purchase-invoices/{invoice_id}/dc-match",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    # Phase 2: 3-way-match config + per-invoice match detail + exception override.
    # Config read is AUTHENTICATED; config write + exception override are
    # accounting actions -> ACCOUNTANT/ADMIN. Match detail read is AUTHENTICATED.
    {
        "method": "GET",
        "path": "/api/v1/vendors/purchase-invoices/config",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "PUT",
        "path": "/api/v1/vendors/purchase-invoices/config",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/vendors/purchase-invoices/{invoice_id}/match",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/vendors/purchase-invoices/{invoice_id}/approve-exception",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    # F19: landed-cost capture / preview / one-way allocation. All three are
    # accounting actions on the bill's cost basis -> ACCOUNTANT/ADMIN (same
    # gate as from-grn / dc-match; SUPERADMIN auto-passes via require_roles).
    {
        "method": "POST",
        "path": "/api/v1/vendors/purchase-invoices/{invoice_id}/landed-costs",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/vendors/purchase-invoices/{invoice_id}/landed-costs/preview",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/vendors/purchase-invoices/{invoice_id}/allocate-landed-costs",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/vendors/purchase-invoices/{invoice_id}",
        "allowed": "AUTHENTICATED",
    },
    # S6: Accountant reconciliation ticks (inline recon sub-doc on vendor_bills).
    # Both write and read are accounting actions -> ACCOUNTANT/ADMIN.
    {
        "method": "POST",
        "path": "/api/v1/vendors/purchase-invoices/{invoice_id}/recon",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/vendors/purchase-invoices/{invoice_id}/recon",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    # S6: Accountant console worklists (stock-yet-to-receive, vendor returns,
    # pending scheme + return CNs). ACCOUNTANT/ADMIN read-only.
    {
        "method": "GET",
        "path": "/api/v1/vendors/recon/worklists",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    # P4: tick a scheme/rebate credit note as physically received (clears it from
    # the pending-scheme-CN worklist). ACCOUNTANT/ADMIN.
    {
        "method": "POST",
        "path": "/api/v1/vendors/recon/credit-notes/{credit_note_number}/mark-received",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/vendors/purchase-orders",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/vendors/purchase-orders",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/vendors/purchase-orders/from-forecast",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/vendors/purchase-orders/{po_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/vendors/purchase-orders/{po_id}/cancel",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/vendors/purchase-orders/{po_id}/send",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    # F8 PO-vs-GRN variance: dismiss a variance/backorder line with a mandatory
    # justification (single-doc PO $push + one audit row). An accounting-style
    # decision -> ACCOUNTANT/ADMIN only (SUPERADMIN auto-passes).
    {
        "method": "POST",
        "path": "/api/v1/vendors/purchase-orders/{po_id}/dismiss-variance",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/vendors/sku-alias-lookup",
        "allowed": "AUTHENTICATED",
    },
    # F8 PO-vs-GRN variance report (read-only). Open/partial PO lines whose
    # received qty trails the order, with open qty + aging enum. Visible to the
    # AP pair plus the managers who chase late deliveries.
    {
        "method": "GET",
        "path": "/api/v1/vendors/variance-report",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/vendors/{vendor_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "PUT",
        "path": "/api/v1/vendors/{vendor_id}",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/vendors/{vendor_id}/bills",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/vendors/{vendor_id}/bills",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/vendors/{vendor_id}/debit-notes",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/vendors/{vendor_id}/debit-notes",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/vendors/{vendor_id}/ledger",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/vendors/{vendor_id}/payments",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/vendors/{vendor_id}/payments",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/vendors/{vendor_id}/portal-token",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/vendors/{vendor_id}/portal-token/{token_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/vendors/{vendor_id}/portal-tokens",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    # INV-13: vendor performance scoring + purchase-history analytics
    {
        "method": "GET",
        "path": "/api/v1/vendors/{vendor_id}/performance",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/vendors/{vendor_id}/purchase-history",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/vendors/{vendor_id}/sku-aliases",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/vendors/{vendor_id}/sku-aliases",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/vendors/{vendor_id}/sku-aliases/{alias_id}",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    # FIN-11: TDS threshold status + quarterly 26Q/27EQ export
    {
        "method": "GET",
        "path": "/api/v1/vendors/tds/threshold-status",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/vendors/tds/26q-export",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    # --- /api/v1/vouchers ---
    # IDOR hardening: issue validates an explicit store_id against the
    # caller's reach (validate_store_access) and cancel is scoped to the
    # voucher's issuing store (can_access_store_scoped; ADMIN/SUPERADMIN
    # cross-store). REDEEM stays chain-wide BY DESIGN -- a gift card is
    # redeemable at any store -- so the redeem row is deliberately NOT
    # store_scoped.
    {
        "method": "GET",
        "path": "/api/v1/vouchers",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/vouchers",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/vouchers/",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/vouchers/",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {"method": "GET", "path": "/api/v1/vouchers/{code}", "allowed": "AUTHENTICATED"},
    {
        "method": "POST",
        "path": "/api/v1/vouchers/{code}/cancel",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/vouchers/{code}/redeem",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "CASHIER",
            "SALES_CASHIER",
            "SALES_STAFF",
            "STORE_MANAGER",
        ],
    },
    # --- /api/v1/walkouts ---
    {"method": "GET", "path": "/api/v1/walkouts", "allowed": "AUTHENTICATED"},
    {"method": "POST", "path": "/api/v1/walkouts", "allowed": "AUTHENTICATED"},
    {"method": "GET", "path": "/api/v1/walkouts/", "allowed": "AUTHENTICATED"},
    {"method": "POST", "path": "/api/v1/walkouts/", "allowed": "AUTHENTICATED"},
    # F45 D5 -- POS soft-block compliance counter (read-only; never blocks a sale).
    {
        "method": "GET",
        "path": "/api/v1/walkouts/pos-compliance-check",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/walkouts/conversion-feed",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/walkouts/dashboard/fu-status",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/walkouts/dashboard/per-staff",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/walkouts/dashboard/result-breakdown",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/walkouts/dashboard/top-reasons",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/walkouts/followups/due-today",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/walkouts/followups/escalate-overdue",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/walkouts/walkins/manual-topup",
        "allowed": [
            "ACCOUNTANT",
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "SUPERADMIN",
        ],
    },
    {
        "method": "GET",
        "path": "/api/v1/walkouts/walkins/mtd",
        "allowed": "AUTHENTICATED",
    },
    # N3 -- manager sets/updates a per-staff walk-in count (drives the SC
    # conversion denominator). Managers + admin only so sales staff cannot
    # self-inflate their own conversion %. In-handler role gate mirrors this.
    {
        "method": "PATCH",
        "path": "/api/v1/walkouts/walkins/per-staff",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "SUPERADMIN",
        ],
    },
    {
        "method": "POST",
        "path": "/api/v1/walkouts/walkins/pos-increment",
        "allowed": [
            "ACCOUNTANT",
            "ADMIN",
            "AREA_MANAGER",
            "CASHIER",
            "OPTOMETRIST",
            "SALES_CASHIER",
            "SALES_STAFF",
            "STORE_MANAGER",
            "SUPERADMIN",
        ],
    },
    # N3 -- footfall capture status (PENDING / PARTIAL / COMPLETE). Same
    # store-scoping + access class as walkins/today (the SC scorecard input).
    {
        "method": "GET",
        "path": "/api/v1/walkouts/walkins/status",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/walkouts/walkins/today",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "DELETE",
        "path": "/api/v1/walkouts/{walkout_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/walkouts/{walkout_id}",
        "allowed": "AUTHENTICATED",
    },
    # Edit is OWNERSHIP-gated in-handler (_check_edit_permission): SUPERADMIN/
    # ADMIN any row, STORE/AREA manager their store, and SALES_STAFF/SALES_CASHIER/
    # CASHIER their OWN rows. That is a data-conditional gate the role table can't
    # express (like store_scoped), so the role-class is AUTHENTICATED -- a static
    # role list here would be STRICTER than the real route (it 403'd sales staff
    # editing their own walkout, which the handler actually allows).
    {
        "method": "PATCH",
        "path": "/api/v1/walkouts/{walkout_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/walkouts/{walkout_id}/followups",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "PATCH",
        "path": "/api/v1/walkouts/{walkout_id}/followups/{round_num}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/walkouts/{walkout_id}/followups/{round_num}/approve",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "PATCH",
        "path": "/api/v1/walkouts/{walkout_id}/result",
        "allowed": "AUTHENTICATED",
    },
    # --- /api/v1/webhooks ---
    {"method": "GET", "path": "/api/v1/webhooks/health", "allowed": "PUBLIC"},
    {"method": "POST", "path": "/api/v1/webhooks/razorpay", "allowed": "PUBLIC"},
    {"method": "POST", "path": "/api/v1/webhooks/shiprocket", "allowed": "PUBLIC"},
    {"method": "POST", "path": "/api/v1/webhooks/shopify", "allowed": "PUBLIC"},
    {"method": "POST", "path": "/api/v1/webhooks/msg91/delivery", "allowed": "PUBLIC"},
    # CRM-14: WhatsApp inbound (Meta Business API).
    # GET = Meta verify-token challenge (PUBLIC -- Meta hits this with no IMS auth).
    # POST = Meta message delivery (HMAC-signed by Meta; no IMS bearer token).
    # GET conversations = inbox view; role-checked INSIDE the handler (self_enforced).
    {"method": "GET", "path": "/api/v1/webhooks/whatsapp", "allowed": "PUBLIC"},
    {"method": "POST", "path": "/api/v1/webhooks/whatsapp", "allowed": "PUBLIC"},
    {
        "method": "GET",
        "path": "/api/v1/webhooks/whatsapp/conversations",
        "allowed": ["SUPERADMIN", "ADMIN", "STORE_MANAGER"],
        "self_enforced": True,
    },
    # --- /api/v1/workshop ---
    {"method": "GET", "path": "/api/v1/workshop", "allowed": "PUBLIC"},
    {"method": "GET", "path": "/api/v1/workshop/", "allowed": "PUBLIC"},
    {
        "method": "GET",
        "path": "/api/v1/workshop/dashboard-kpis",
        "allowed": "AUTHENTICATED",
    },
    {"method": "GET", "path": "/api/v1/workshop/jobs", "allowed": "AUTHENTICATED"},
    {"method": "POST", "path": "/api/v1/workshop/jobs", "allowed": "AUTHENTICATED"},
    {
        "method": "GET",
        "path": "/api/v1/workshop/jobs/by-vendor/{vendor_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/workshop/jobs/{job_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "PUT",
        "path": "/api/v1/workshop/jobs/{job_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/workshop/jobs/{job_id}/assign",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/workshop/jobs/{job_id}/complete",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "SUPERADMIN",
            "WORKSHOP_STAFF",
        ],
    },
    {
        # BUG-092: sales-confirmation gate is a SALES act, not WORKSHOP_STAFF's.
        "method": "PATCH",
        "path": "/api/v1/workshop/jobs/{job_id}/fitting-details",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "CASHIER",
            "SALES_CASHIER",
            "SALES_STAFF",
            "STORE_MANAGER",
            "SUPERADMIN",
        ],
    },
    {
        "method": "GET",
        "path": "/api/v1/workshop/jobs/{job_id}/label",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/workshop/jobs/{job_id}/lens-status",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER", "WORKSHOP_STAFF"],
    },
    {
        "method": "POST",
        "path": "/api/v1/workshop/jobs/{job_id}/notify-ready",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER", "WORKSHOP_STAFF"],
    },
    # F2 -- disposable job-card print stamp (workshop fulfilment ladder).
    {
        "method": "POST",
        "path": "/api/v1/workshop/jobs/{job_id}/print-job-card",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "CASHIER",
            "STORE_MANAGER",
            "WORKSHOP_STAFF",
        ],
    },
    # /qc gate tightened to WORKSHOP_ROLES (not AUTHENTICATED) — sales staff
    # cannot run or override QC. Mirrors require_roles(*WORKSHOP_ROLES) on the handler.
    {
        "method": "POST",
        "path": "/api/v1/workshop/jobs/{job_id}/qc",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "SUPERADMIN",
            "WORKSHOP_STAFF",
        ],
    },
    # New Phase 6.9 structured QC checklist endpoint.
    {
        "method": "POST",
        "path": "/api/v1/workshop/jobs/{job_id}/qc-checklist",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "SUPERADMIN",
            "WORKSHOP_STAFF",
        ],
    },
    {
        "method": "POST",
        "path": "/api/v1/workshop/jobs/{job_id}/rework",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "SUPERADMIN",
            "WORKSHOP_STAFF",
        ],
    },
    {
        "method": "POST",
        "path": "/api/v1/workshop/jobs/{job_id}/scan-advance",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "CASHIER",
            "STORE_MANAGER",
            "WORKSHOP_STAFF",
        ],
    },
    {
        "method": "POST",
        "path": "/api/v1/workshop/jobs/{job_id}/start",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "SUPERADMIN",
            "WORKSHOP_STAFF",
        ],
    },
    {
        "method": "PATCH",
        "path": "/api/v1/workshop/jobs/{job_id}/status",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "SUPERADMIN",
            "WORKSHOP_STAFF",
        ],
    },
    {
        "method": "PATCH",
        "path": "/api/v1/workshop/jobs/{job_id}/vendor",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "SUPERADMIN",
            "WORKSHOP_STAFF",
        ],
    },
    {
        "method": "POST",
        "path": "/api/v1/workshop/jobs/{job_id}/vendor-status",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "SUPERADMIN",
            "WORKSHOP_STAFF",
        ],
    },
    {"method": "GET", "path": "/api/v1/workshop/overdue", "allowed": "AUTHENTICATED"},
    {"method": "GET", "path": "/api/v1/workshop/pending", "allowed": "AUTHENTICATED"},
    {
        "method": "GET",
        "path": "/api/v1/workshop/product-label",
        "allowed": "AUTHENTICATED",
    },
    {"method": "GET", "path": "/api/v1/workshop/ready", "allowed": "AUTHENTICATED"},
    # F13 -- remake justification taxonomy: read anywhere (the bench needs it
    # for the rework dialog), replace is owner/admin-only.
    {
        "method": "GET",
        "path": "/api/v1/workshop/remake-reason-codes",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "PUT",
        "path": "/api/v1/workshop/remake-reason-codes",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    # F2 -- internal lab routing (disposable barcoded job cards).
    {
        "method": "POST",
        "path": "/api/v1/workshop/scan",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "CASHIER",
            "STORE_MANAGER",
            "WORKSHOP_STAFF",
        ],
    },
    # F13 -- spoilage analytics rollup exposes cost data: manager+ only.
    # Mirrors the inline role check in workshop.get_spoilage_analytics.
    {
        "method": "GET",
        "path": "/api/v1/workshop/spoilage-analytics",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/workshop/stations",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/workshop/stations",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/workshop/stations/{code}/queue",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "CASHIER",
            "STORE_MANAGER",
            "WORKSHOP_STAFF",
        ],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/workshop/technician-workload",
        "allowed": "AUTHENTICATED",
    },
    # Budgeting (dual-mode planned vs actual) -- manager+/accountant + store-scoped.
    {
        "method": "POST",
        "path": "/api/v1/budgets",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/budgets/",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/budgets",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/budgets/",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/budgets/variance",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "DELETE",
        "path": "/api/v1/budgets/{budget_id}",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    # ------------------------------------------------------------------
    # E6 reminder rail (routers/reminders.py): ADMIN/AREA_MANAGER/STORE_MANAGER
    # (SUPERADMIN implicit). STORE-scope rules are additionally locked to that
    # store via _enforce_store_scope; GLOBAL/ENTITY mutations require ADMIN+.
    # ------------------------------------------------------------------
    {
        "method": "GET",
        "path": "/api/v1/reminders/rules",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/reminders/rules",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/reminders/rules/{rule_id}",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "PUT",
        "path": "/api/v1/reminders/rules/{rule_id}",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "DELETE",
        "path": "/api/v1/reminders/rules/{rule_id}",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/reminders/rules/{rule_id}/toggle",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/reminders/rules/{rule_id}/preview",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/reminders/rules/{rule_id}/run-now",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/reminders/rules/{rule_id}/history",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
]


# ---------------------------------------------------------------------------
# self_enforced - rows whose route DELIBERATELY denies with a non-generic
# response the enforcer must NOT override.
# ---------------------------------------------------------------------------
# Most role-gated routes reject a wrong role with a plain 403, which the
# request-time enforcer can mirror byte-for-behaviour. A few routes reject
# differently and that difference is INTENTIONAL + relied upon:
#
#   * /api/v1/jarvis/** and /api/v1/admin/techcherry/** reject non-SUPERADMIN
#     with a 404 ("Not found") to HIDE the endpoint's existence (a deliberate
#     security feature; their tests assert 404, not 403). A generic 403 here
#     would both break those tests AND leak that the path is a real route.
#   * POST /api/v1/prescriptions[/] rejects non-clinical roles with a 403 whose
#     BODY ("...does not have clinical access") is asserted by callers/tests.
#
# For these, the enforcer does the role check but, on DENY, DEFERS to the route
# (lets the request through) so the route's own gate returns its canonical
# response. ``allowed`` is unchanged (the role-class is still correct + the
# coverage-lock / jarvis-superadmin tests still pass); only the *rejection
# delivery* is left to the route. ``self_enforced`` is auto-applied by prefix
# below for the 404-hiding families; prescription rows carry it inline.
for _entry in POLICY:
    _p = str(_entry["path"])
    if (
        _p == "/api/v1/jarvis"
        or _p.startswith("/api/v1/jarvis/")
        or _p.startswith("/api/v1/admin/techcherry/")
    ):
        _entry.setdefault("self_enforced", True)


# ---------------------------------------------------------------------------
# Lookup + matching
# ---------------------------------------------------------------------------
# Build an index once at import. Paths can contain {param} segments; we match a
# concrete request path against templated policy paths segment-by-segment and
# prefer the MOST SPECIFIC (fewest params, longest) match.

_INDEX: Dict[str, List[Dict[str, object]]] = {}
for _entry in POLICY:
    _INDEX.setdefault(str(_entry["method"]).upper(), []).append(_entry)


def _segments(path: str) -> List[str]:
    return [s for s in path.split("/") if s != ""]


def _template_matches(template: str, concrete: str) -> bool:
    """True if a concrete request path matches a (possibly templated) policy
    path. A ``{param}`` segment matches exactly one non-empty concrete segment."""
    t_segs = _segments(template)
    c_segs = _segments(concrete)
    if len(t_segs) != len(c_segs):
        return False
    for t, c in zip(t_segs, c_segs):
        if t.startswith("{") and t.endswith("}"):
            continue  # param - matches any single segment
        if t != c:
            return False
    return True


def _specificity(template: str) -> tuple:
    """Higher = more specific. Rank by (fewest params, most segments)."""
    segs = _segments(template)
    params = sum(1 for s in segs if s.startswith("{") and s.endswith("}"))
    return (-params, len(segs))


def policy_for(method: str, path: str) -> Optional[Dict[str, object]]:
    """Return the POLICY entry for a concrete (method, path), or None if the
    route is not catalogued. On multiple template matches, the most specific
    (fewest path params, then longest) wins -- so a literal
    ``/orders/summary`` beats ``/orders/{order_id}``."""
    candidates = _INDEX.get(method.upper(), [])
    # Exact (literal) match first - cheapest and unambiguous.
    for entry in candidates:
        if entry["path"] == path:
            return entry
    # Then templated matches, most specific wins.
    matches = [e for e in candidates if _template_matches(str(e["path"]), path)]
    if not matches:
        return None
    matches.sort(key=lambda e: _specificity(str(e["path"])), reverse=True)
    return matches[0]


def is_store_scoped(method: str, path: str) -> bool:
    """Whether the matched endpoint additionally restricts results to the
    caller's store(s) via validate_store_access."""
    entry = policy_for(method, path)
    return bool(entry and entry.get("store_scoped"))


def is_self_enforced(method: str, path: str) -> bool:
    """Whether the matched endpoint rejects with a non-generic response the
    request-time enforcer must NOT override (404 existence-hiding, or a
    body-specific 403). On a role denial the enforcer DEFERS to the route for
    these so its canonical response is preserved. See the ``self_enforced``
    section above for the rationale + which families carry it."""
    entry = policy_for(method, path)
    return bool(entry and entry.get("self_enforced"))


def check_access(method: str, path: str, user_roles) -> bool:
    """Decision function: may a caller holding ``user_roles`` reach (method, path)?

    Rules (mirror the routers):
      * Unknown route            -> False (deny by default; nothing un-catalogued
                                   should silently pass).
      * allowed == PUBLIC        -> True (no auth needed).
      * allowed == AUTHENTICATED -> True iff the caller has ANY role (i.e. is a
                                   logged-in user). An empty role set is treated
                                   as unauthenticated -> False.
      * allowed is a role list   -> True iff SUPERADMIN in roles OR the caller's
                                   roles intersect the allow-list.

    Does NOT evaluate store-scope / ownership / discount-cap conditions -- those
    are data-level checks the handler still performs. This answers the
    role-class question only.
    """
    entry = policy_for(method, path)
    if entry is None:
        return False
    allowed = entry["allowed"]
    roles = set(user_roles or [])
    if allowed == PUBLIC:
        return True
    if allowed == AUTHENTICATED:
        return len(roles) > 0
    # role list
    if "SUPERADMIN" in roles:
        return True
    return bool(roles & set(allowed))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Coverage helper
# ---------------------------------------------------------------------------


def uncatalogued_routes(app) -> List[Dict[str, str]]:
    """Return live ``/api/v1`` routes (method, path) that have NO POLICY entry.

    Excludes docs / openapi / static and non-/api/v1 utility routes (``/``,
    ``/health``, ``/docs`` …). HEAD is ignored (auto-paired with GET). This is
    the regression lock used by the coverage test: any new endpoint added
    without a POLICY row shows up here.
    """
    missing: List[Dict[str, str]] = []
    seen = set()
    for route in getattr(app, "routes", []):
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods:
            continue
        if not path.startswith("/api/v1"):
            continue
        for method in methods:
            if method in ("HEAD", "OPTIONS"):
                continue
            key = (method, path)
            if key in seen:
                continue
            seen.add(key)
            if policy_for(method, path) is None:
                missing.append({"method": method, "path": path})
    missing.sort(key=lambda r: (r["path"], r["method"]))
    return missing

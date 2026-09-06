"""
POLICY rows: /analytics, /analytics-v2, /audit, /auth, /auth/devices, /catalog.

Moved VERBATIM from the flat ``api/services/rbac_policy.py`` lines 622-918.
These rows are load-bearing DATA. Their ORDER is preserved (``policy_for``
prefers the most specific match, and a row under a module widens that
module's capability grant-union). Do not reorder, reword or re-group them;
a new route needs a new row here or CI fails the coverage lock.
"""

from __future__ import annotations

from typing import Dict, List

ROWS: List[Dict[str, object]] = [
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
    # --- /api/v1/auth/devices (approved-device login gate; routers/devices.py,
    # DARK behind DEVICE_GATE_MODE=off) ---
    # The three POST pre-auth routes are PUBLIC in the middleware sense but the
    # two enrolment ones demand a purpose-scoped ENROLMENT TICKET that only a
    # correct-password login mints (and which get_current_user refuses as a
    # session token); assertion-options serves only a TTL'd random challenge.
    # Approval/revocation/listing are SUPERADMIN-ONLY by owner ruling
    # 2026-09-02 -- an ADMIN is exempt from the gate but must NOT approve
    # devices. Do not widen these rows (see store_login_device_gate memory).
    {
        "method": "POST",
        "path": "/api/v1/auth/devices/enroll/options",
        "allowed": "PUBLIC",
    },
    {"method": "POST", "path": "/api/v1/auth/devices/enroll", "allowed": "PUBLIC"},
    # Pre-arming path: any signed-in user may mint a ticket to register the
    # device they are CURRENTLY on (so devices get approved before enforce
    # mode is armed). Approval still gates actual sign-in rights.
    {
        "method": "POST",
        "path": "/api/v1/auth/devices/enroll-ticket",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/auth/devices/assertion-options",
        "allowed": "PUBLIC",
    },
    {"method": "GET", "path": "/api/v1/auth/devices", "allowed": ["SUPERADMIN"]},
    {
        "method": "POST",
        "path": "/api/v1/auth/devices/{device_id}/approve",
        "allowed": ["SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/auth/devices/{device_id}/revoke",
        "allowed": ["SUPERADMIN"],
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
    # Catalog Manager review queue: promote an imported catalog doc to a
    # POS-sellable `products` spine row (the only writer of needs_review/
    # pos_ready). Mirrors the catalog-mutation role set.
    {
        "method": "POST",
        "path": "/api/v1/catalog/products/{product_id}/promote",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "SUPERADMIN"],
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
]

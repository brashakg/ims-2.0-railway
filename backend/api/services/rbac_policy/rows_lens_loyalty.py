"""
POLICY rows: /lens-catalog, /lens-enums, /catalog-field-options, /lens-stock, /loyalty.

Moved VERBATIM from the flat ``api/services/rbac_policy.py`` lines 3908-4171.
These rows are load-bearing DATA. Their ORDER is preserved (``policy_for``
prefers the most specific match, and a row under a module widens that
module's capability grant-union). Do not reorder, reword or re-group them;
a new route needs a new row here or CI fails the coverage lock.
"""

from __future__ import annotations

from typing import Dict, List

ROWS: List[Dict[str, object]] = [
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
    # Live "similar products" strip in the Add-Product form (dup-detect
    # Phase 2). Read-only hint any authenticated catalog operator can use;
    # mirrors GET /api/v1/products.
    {
        "method": "GET",
        "path": "/api/v1/products/similar",
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
    # OTP on loyalty redemption (owner ruling 2026-08-30: redemption ONLY,
    # never customer creation). Same POS money family as /loyalty/redeem -
    # these two only send/check the customer's verification code; the debit
    # stays behind /loyalty/redeem itself.
    {
        "method": "POST",
        "path": "/api/v1/loyalty/redeem/otp/send",
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
        "path": "/api/v1/loyalty/redeem/otp/verify",
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
]

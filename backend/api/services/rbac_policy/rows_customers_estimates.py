"""
POLICY rows: /customers, /display-fixtures, /display-placements, /entities, /estimates.

Moved VERBATIM from the flat ``api/services/rbac_policy.py`` lines 1365-1687.
These rows are load-bearing DATA. Their ORDER is preserved (``policy_for``
prefers the most specific match, and a row under a module widens that
module's capability grant-union). Do not reorder, reword or re-group them;
a new route needs a new row here or CI fails the coverage lock.
"""

from __future__ import annotations

from typing import Dict, List

ROWS: List[Dict[str, object]] = [
    # --- /api/v1/customers ---
    {"method": "GET", "path": "/api/v1/customers", "allowed": "AUTHENTICATED"},
    {"method": "POST", "path": "/api/v1/customers", "allowed": "AUTHENTICATED"},
    # Batch email validation over the customer list (MSG91, billed per
    # address). ADMIN-only because each run SPENDS money once armed; the
    # endpoint itself additionally refuses honestly while DISPATCH_MODE is
    # dark. Roles are a strict subset of the module's existing write union
    # (POST /customers is AUTHENTICATED), so this row broadens no capability
    # grant (rbac capability-union gotcha).
    {
        "method": "POST",
        "path": "/api/v1/customers/validate-emails",
        "allowed": ["SUPERADMIN", "ADMIN"],
    },
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
    # Promote a family member to their own account (family-member guard
    # counterpart). Whoever can create a customer can promote one; the handler
    # is store-scoped via _scoped_customer_or_404(write=True). AUTHENTICATED is
    # the module's existing create gate, so this row broadens no grant-union.
    {
        "method": "POST",
        "path": "/api/v1/customers/{customer_id}/patients/{patient_id}/promote",
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
]

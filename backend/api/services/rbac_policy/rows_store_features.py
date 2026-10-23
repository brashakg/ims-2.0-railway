"""
POLICY rows: /non-adapt, /serials, /family-wallet, /blind-count, /repairs.

Moved VERBATIM from the flat ``api/services/rbac_policy.py`` lines 2166-2564.
These rows are load-bearing DATA. Their ORDER is preserved (``policy_for``
prefers the most specific match, and a row under a module widens that
module's capability grant-union). Do not reorder, reword or re-group them;
a new route needs a new row here or CI fails the coverage lock.
"""

from __future__ import annotations

from typing import Dict, List

ROWS: List[Dict[str, object]] = [
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
        "allowed": ["ADMIN", "SUPERADMIN"],
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
        # A recall takes the unit off the books: a write-off, admin only.
        "allowed": ["ADMIN", "SUPERADMIN"],
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
    # --- Feature #15 blind stock take (/api/v1/blind-count): RETIRED in S1
    # (review M5) -- the router is no longer mounted, so its rows are gone.
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
]

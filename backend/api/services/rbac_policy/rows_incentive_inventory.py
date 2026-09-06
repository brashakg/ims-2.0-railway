"""
POLICY rows: /incentive, /inventory.

Moved VERBATIM from the flat ``api/services/rbac_policy.py`` lines 3252-3507.
These rows are load-bearing DATA. Their ORDER is preserved (``policy_for``
prefers the most specific match, and a row under a module widens that
module's capability grant-union). Do not reorder, reword or re-group them;
a new route needs a new row here or CI fails the coverage lock.
"""

from __future__ import annotations

from typing import Dict, List

ROWS: List[Dict[str, object]] = [
    # --- /api/v1/incentive ---
    {
        "method": "POST",
        "path": "/api/v1/incentive/kicker/product-sale",
        "allowed": "AUTHENTICATED",
    },
    # SELF-ONLY BELOW ADMIN (owner ruling 2026-08-13), which is a data condition
    # this table cannot express -- see the CAVEATS note in the module docstring.
    # The ROUTE stays open to every authenticated user ON PURPOSE: the owner kept
    # each person's view of their OWN incentive, so closing the route to
    # ADMIN/SUPERADMIN would delete the sales-staff self-view he asked to keep.
    # What is gated is the FIGURE, inside routers/kicker.py: anyone who is not
    # is_salary_admin has `staff_id` forced to their own, so items AND every
    # total are their own numbers. Do not "tidy" this row to a role list.
    {
        "method": "GET",
        "path": "/api/v1/incentive/kicker/{ym}",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
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
    # POS "My day" tile: the caller's OWN figures only (the handler keys on
    # user_id and takes no staff parameter), so the route is open to every POS
    # role. Read-only; maps to incentive:read, whose union is already
    # AUTHENTICATED-broad (kicker/{ym}, mtd, leaderboard) -- widens nothing.
    {
        "method": "GET",
        "path": "/api/v1/incentive/points/my-day",
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
    # Movements ledger (Movements tab): merged GRN/order/transfer event feed.
    # Mirrors the /inventory/stock row -- any authenticated role may read its
    # own store's ledger; store_scoped stops cross-store reads via ?store_id=.
    {
        "method": "GET",
        "path": "/api/v1/inventory/movements",
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
    {
        # Brand-wise KPI rollup for the Inventory Insights tab (2026-07-05).
        # Same posture as the sell-through/overstock reads above: any
        # authenticated role; data is store-scoped inside the endpoint.
        "method": "GET",
        "path": "/api/v1/inventory/brand-insights",
        "allowed": "AUTHENTICATED",
    },
]

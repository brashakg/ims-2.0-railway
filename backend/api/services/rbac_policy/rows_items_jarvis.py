"""
POLICY rows: /items event ledger, /jarvis.

Moved VERBATIM from the flat ``api/services/rbac_policy.py`` lines 3508-3907.
These rows are load-bearing DATA. Their ORDER is preserved (``policy_for``
prefers the most specific match, and a row under a module widens that
module's capability grant-union). Do not reorder, reword or re-group them;
a new route needs a new row here or CI fails the coverage lock.
"""

from __future__ import annotations

from typing import Dict, List

ROWS: List[Dict[str, object]] = [
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
        # OWNER RULING 2026-08-25 (#8): writing off missing stock is ADMIN /
        # SUPERADMIN ONLY, at every value. Counting stays open to the manager
        # ladder; destroying it off the books does not.
        "allowed": ["ADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/inventory/stock-count/{count_id}/reconcile/finish",
        # Closes a write-off that destroyed the stock but lost its audit
        # write. It destroys nothing itself, but it is a door onto a stock
        # write-off, so it carries the same ADMIN-only gate.
        "allowed": ["ADMIN"],
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
]

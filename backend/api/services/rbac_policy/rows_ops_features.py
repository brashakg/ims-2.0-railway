"""
POLICY rows: HR rostering, /endless-aisle, /vendor-rebates, /cl-po, B2B Tally, journal entries.

Moved VERBATIM from the flat ``api/services/rbac_policy.py`` lines 2565-2916.
These rows are load-bearing DATA. Their ORDER is preserved (``policy_for``
prefers the most specific match, and a row under a module widens that
module's capability grant-union). Do not reorder, reword or re-group them;
a new route needs a new row here or CI fails the coverage lock.
"""

from __future__ import annotations

from typing import Dict, List

ROWS: List[Dict[str, object]] = [
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
]

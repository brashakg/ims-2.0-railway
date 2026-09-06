"""
POLICY rows: /expenses, /finance, E5 tender recon, /till, /bank-recon.

Moved VERBATIM from the flat ``api/services/rbac_policy.py`` lines 1688-2165.
These rows are load-bearing DATA. Their ORDER is preserved (``policy_for``
prefers the most specific match, and a row under a module widens that
module's capability grant-union). Do not reorder, reword or re-group them;
a new route needs a new row here or CI fails the coverage lock.
"""

from __future__ import annotations

from typing import Dict, List

ROWS: List[Dict[str, object]] = [
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
        # Accountant GST cross-check: GSTR-1/3B vs books side-by-side + sign-off.
        # Org-wide/entity-grouped filing view -> finance-admin only (handler
        # enforces _require_finance_admin).
        "method": "GET",
        "path": "/api/v1/finance/gst/cross-check",
        "allowed": ["ACCOUNTANT", "ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/finance/gst/cross-check-signoff",
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
    # OWNER DECISION 2026-08-13: the store-by-store profit table is ADMIN /
    # SUPERADMIN only. Every row carries that store's monthly wage bill, and a
    # 1-5 person store's payroll total IS an individual's pay. Narrowed at BOTH
    # layers -- this row and finance.get_pnl_by_store's own is_salary_admin gate.
    {
        "method": "GET",
        "path": "/api/v1/finance/pnl/by-store",
        "allowed": ["ADMIN", "SUPERADMIN"],
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
        # Session list: manager/finance see the full figures. Cashier roles may
        # list too -- without it they cannot FIND the shared drawer they must
        # count (owner ruling 2026-09-03: cashiers count and submit; the
        # manager reviews the variance AFTER submission). Their rows are
        # blind-redacted at the data layer (redact_for_cashier -- no expected
        # figure on the wire pre-lock).
        "method": "GET",
        "path": "/api/v1/till/sessions",
        "allowed": [
            "SALES_CASHIER",
            "CASHIER",
            "SALES_STAFF",
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
]

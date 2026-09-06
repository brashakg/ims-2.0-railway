"""
POLICY rows: /orders, /payout, /payroll.

Moved VERBATIM from the flat ``api/services/rbac_policy.py`` lines 5013-5344.
These rows are load-bearing DATA. Their ORDER is preserved (``policy_for``
prefers the most specific match, and a row under a module widens that
module's capability grant-union). Do not reorder, reword or re-group them;
a new route needs a new row here or CI fails the coverage lock.
"""

from __future__ import annotations

from typing import Dict, List

ROWS: List[Dict[str, object]] = [
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
        "method": "POST",
        "path": "/api/v1/orders/{order_id}/deliver-with-payment",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/orders/{order_id}/invoice",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/orders/{order_id}/invoice.pdf",
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
    # OWNER DECISION 2026-08-13: every payout read is ADMIN / SUPERADMIN only.
    # These bodies list NAMED colleagues with their per-person incentive rupees,
    # which is a payslip line (payout.py:65-95 has the full reasoning). The four
    # read rows were "AUTHENTICATED" here while payout._check_view_permission
    # narrowed them to the manager tier inline; both layers now say the same
    # thing, so the table no longer understates the real gate.
    {
        "method": "GET",
        "path": "/api/v1/payout/export/{snapshot_id}.csv",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {"method": "POST", "path": "/api/v1/payout/lock", "allowed": ["SUPERADMIN"]},
    {
        "method": "GET",
        "path": "/api/v1/payout/payroll-feed",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/payout/preview",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/payout/snapshot/{snapshot_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "PATCH",
        "path": "/api/v1/payout/snapshot/{snapshot_id}/mark-paid",
        "allowed": ["SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/payout/snapshots",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
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
        "allowed": ["ADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/payroll/advances/{advance_id}/settle",
        "allowed": ["ADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/payroll/advances/{employee_id}",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/payroll/approve",
        "allowed": ["ADMIN"],
    },
    # OWNER RULING 2026-08-09 ("nobody except admin/superadmin should see anyone
    # elses salary"), applied 2026-08-10. The AGGREGATE salary routes below --
    # a whole store's pay, the run register, the statutory exports -- are
    # ADMIN-only in BOTH layers: the handler enforces it and these rows say so,
    # rather than leaving the middleware to admit a role the handler then
    # refuses. SUPERADMIN auto-passes in check_access.
    #
    # The PER-EMPLOYEE salary routes (payslip/{id}, config/{id}, advances/{id},
    # incentive-summary/{id}) deliberately KEEP the manager tier here: a
    # STORE_MANAGER must get past the middleware to read their OWN payslip, and
    # payroll._assert_self_or_salary_admin then allows self and refuses everyone
    # else. Narrowing those rows would break self-service one layer early.
    {
        "method": "GET",
        "path": "/api/v1/payroll/config",
        "allowed": ["ADMIN"],
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
        "allowed": ["ADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/payroll/registers/summary",
        "allowed": ["ADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/payroll/run",
        "allowed": ["ADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/payroll/run/rows",
        "allowed": ["ADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/payroll/salary-sheet",
        "allowed": ["ADMIN"],
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
        "allowed": ["ADMIN"],
    },
    # GET /api/v1/payroll/salary/{employee_id} was REMOVED 2026-08-10 with the
    # route itself (owner decision -- it served raw bank_account_no / pan /
    # ctc_annual for any employee id). The row must go with the route: the
    # coverage lock in tests/test_rbac_policy.py checks parity in BOTH
    # directions, so a stale row for a deleted route fails CI.
    {
        "method": "GET",
        "path": "/api/v1/payroll/tally/salary-jv",
        "allowed": ["ADMIN"],
    },
]

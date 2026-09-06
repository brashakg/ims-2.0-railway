"""
POLICY rows: /follow-ups, /handoffs, /health, /hr self-service, /hr.

Moved VERBATIM from the flat ``api/services/rbac_policy.py`` lines 2917-3251.
These rows are load-bearing DATA. Their ORDER is preserved (``policy_for``
prefers the most specific match, and a row under a module widens that
module's capability grant-union). Do not reorder, reword or re-group them;
a new route needs a new row here or CI fails the coverage lock.
"""

from __future__ import annotations

from typing import Dict, List

from ._core import AUTHENTICATED

ROWS: List[Dict[str, object]] = [
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
    # Apply-for-leave door for EVERY role (floor staff included). Delegates to
    # hr.apply_leave, which pins the employee to the requesting user (the body
    # has no employee_id field), so this is self-write-only despite AUTHENTICATED.
    {
        "method": "POST",
        "path": "/api/v1/hr/me/leaves",
        "allowed": AUTHENTICATED,
        "self_enforced": True,
    },
    # Cancel own still-PENDING leave. Handler 404s unless the leave belongs to
    # the caller (colleague's id == missing id), and 400s once decided.
    {
        "method": "POST",
        "path": "/api/v1/hr/me/leaves/{leave_id}/cancel",
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
        "allowed": ["ADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/hr/payroll/generate",
        "allowed": ["ADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/hr/payroll/{payroll_id}/approve",
        "allowed": ["ADMIN"],
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
]

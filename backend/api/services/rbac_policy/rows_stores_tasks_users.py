"""
POLICY rows: /shipping, /stores, /tasks, /transfers, /users.

Moved VERBATIM from the flat ``api/services/rbac_policy.py`` lines 6214-6607.
These rows are load-bearing DATA. Their ORDER is preserved (``policy_for``
prefers the most specific match, and a row under a module widens that
module's capability grant-union). Do not reorder, reword or re-group them;
a new route needs a new row here or CI fails the coverage lock.
"""

from __future__ import annotations

from typing import Dict, List

from ._core import AUTHENTICATED

ROWS: List[Dict[str, object]] = [
    # --- /api/v1/shipping ---
    {"method": "GET", "path": "/api/v1/shipping/shipments", "allowed": "AUTHENTICATED"},
    {
        "method": "POST",
        "path": "/api/v1/shipping/shipments",
        # SALES_CASHIER merged into SALES_STAFF (backlog #12); mirrors
        # shipping.py._FULFILMENT_ROLES.
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "CASHIER",
            "SALES_STAFF",
            "STORE_MANAGER",
        ],
    },
    {
        "method": "GET",
        "path": "/api/v1/shipping/shipments/{shipment_id}/track",
        "allowed": "AUTHENTICATED",
    },
    # --- /api/v1/stores ---
    {"method": "GET", "path": "/api/v1/stores", "allowed": "AUTHENTICATED"},
    {"method": "POST", "path": "/api/v1/stores", "allowed": ["ADMIN", "SUPERADMIN"]},
    {
        "method": "GET",
        "path": "/api/v1/stores/go-live-checklist",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {"method": "GET", "path": "/api/v1/stores/summary", "allowed": "AUTHENTICATED"},
    {
        "method": "DELETE",
        "path": "/api/v1/stores/{store_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/stores/{store_id}",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "PUT",
        "path": "/api/v1/stores/{store_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/stores/{store_id}/categories/{category}",
        "allowed": ["ADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/stores/{store_id}/categories/{category}",
        "allowed": ["ADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/stores/{store_id}/stats",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/stores/{store_id}/users",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    # --- /api/v1/tasks ---
    {"method": "GET", "path": "/api/v1/tasks", "allowed": "AUTHENTICATED"},
    {"method": "POST", "path": "/api/v1/tasks", "allowed": "AUTHENTICATED"},
    {"method": "GET", "path": "/api/v1/tasks/", "allowed": "AUTHENTICATED"},
    {"method": "POST", "path": "/api/v1/tasks/", "allowed": "AUTHENTICATED"},
    {
        "method": "POST",
        "path": "/api/v1/tasks/auto-escalate-overdue",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/tasks/auto-generate",
        "allowed": "AUTHENTICATED",
    },
    {"method": "GET", "path": "/api/v1/tasks/checklists", "allowed": "AUTHENTICATED"},
    {
        "method": "POST",
        "path": "/api/v1/tasks/checklists/{checklist_type}/complete-item",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/tasks/completion-stats",
        "allowed": "AUTHENTICATED",
    },
    {"method": "GET", "path": "/api/v1/tasks/escalations", "allowed": "AUTHENTICATED"},
    {
        "method": "GET",
        "path": "/api/v1/tasks/integrity/fake-closures",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/tasks/integrity/silent",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {"method": "GET", "path": "/api/v1/tasks/my-tasks", "allowed": "AUTHENTICATED"},
    {"method": "GET", "path": "/api/v1/tasks/overdue", "allowed": "AUTHENTICATED"},
    {
        "method": "POST",
        "path": "/api/v1/tasks/scan/payment-variance",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {"method": "GET", "path": "/api/v1/tasks/sla-config", "allowed": "AUTHENTICATED"},
    {
        "method": "PUT",
        "path": "/api/v1/tasks/sla-config",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/tasks/sop-checklist",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/tasks/sop-checklist/item",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/tasks/sop-templates",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/tasks/sop-templates",
        "allowed": ["ADMIN", "STORE_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/tasks/sop-templates/",
        "allowed": ["ADMIN", "STORE_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/tasks/sop-templates/{template_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/tasks/sop-templates/{template_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "PATCH",
        "path": "/api/v1/tasks/sop-templates/{template_id}",
        "allowed": ["ADMIN", "STORE_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/tasks/sop-templates/{template_id}/assign",
        "allowed": ["ADMIN", "STORE_MANAGER", "SUPERADMIN"],
    },
    {"method": "GET", "path": "/api/v1/tasks/summary", "allowed": "AUTHENTICATED"},
    # #5: upload a task attachment (image/PDF <=25MB); any authenticated user.
    {
        "method": "POST",
        "path": "/api/v1/tasks/upload-file",
        "allowed": "AUTHENTICATED",
    },
    {"method": "GET", "path": "/api/v1/tasks/{task_id}", "allowed": "AUTHENTICATED"},
    {"method": "PATCH", "path": "/api/v1/tasks/{task_id}", "allowed": "AUTHENTICATED"},
    {"method": "PUT", "path": "/api/v1/tasks/{task_id}", "allowed": "AUTHENTICATED"},
    {
        "method": "POST",
        "path": "/api/v1/tasks/{task_id}/acknowledge",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "PATCH",
        "path": "/api/v1/tasks/{task_id}/complete",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/tasks/{task_id}/escalate",
        "allowed": "AUTHENTICATED",
    },
    # #5: download a task's attachment; in-function store-scope (anyone who can see the task).
    {
        "method": "GET",
        "path": "/api/v1/tasks/{task_id}/file",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/tasks/{task_id}/reassign",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/tasks/{task_id}/start",
        "allowed": "AUTHENTICATED",
    },
    # --- /api/v1/transfers ---
    {"method": "GET", "path": "/api/v1/transfers", "allowed": "AUTHENTICATED"},
    {
        "method": "POST",
        "path": "/api/v1/transfers",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER", "SUPERADMIN"],
    },
    {"method": "GET", "path": "/api/v1/transfers/", "allowed": "AUTHENTICATED"},
    {
        "method": "POST",
        "path": "/api/v1/transfers/",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/transfers/analytics/location/{location_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/transfers/analytics/summary",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/transfers/bulk-approve",
        "allowed": ["ADMIN", "AREA_MANAGER", "SUPERADMIN"],
    },
    {"method": "GET", "path": "/api/v1/transfers/pending", "allowed": "AUTHENTICATED"},
    {
        "method": "GET",
        "path": "/api/v1/transfers/{transfer_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "PUT",
        "path": "/api/v1/transfers/{transfer_id}",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/transfers/{transfer_id}/approve",
        "allowed": ["ADMIN", "AREA_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/transfers/{transfer_id}/cancel",
        "allowed": ["ADMIN", "AREA_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/transfers/{transfer_id}/complete",
        "allowed": ["ADMIN", "STORE_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/transfers/{transfer_id}/complete-picking",
        "allowed": ["ADMIN", "STORE_MANAGER", "SUPERADMIN", "WORKSHOP_STAFF"],
    },
    {
        "method": "POST",
        "path": "/api/v1/transfers/{transfer_id}/create-shiprocket-shipment",
        "allowed": ["ADMIN", "STORE_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/transfers/{transfer_id}/receive",
        "allowed": ["ADMIN", "STORE_MANAGER", "SUPERADMIN", "WORKSHOP_STAFF"],
    },
    {
        "method": "POST",
        "path": "/api/v1/transfers/{transfer_id}/ship",
        "allowed": ["ADMIN", "STORE_MANAGER", "SUPERADMIN", "WORKSHOP_STAFF"],
    },
    {
        "method": "POST",
        "path": "/api/v1/transfers/{transfer_id}/start-picking",
        "allowed": ["ADMIN", "STORE_MANAGER", "SUPERADMIN", "WORKSHOP_STAFF"],
    },
    {
        "method": "GET",
        "path": "/api/v1/transfers/{transfer_id}/tracking",
        "allowed": "AUTHENTICATED",
    },
    # --- /api/v1/users ---
    {"method": "POST", "path": "/api/v1/users", "allowed": ["ADMIN", "SUPERADMIN"]},
    # Per-user capability permissions editor + audit/revert (require_admin).
    {
        "method": "GET",
        "path": "/api/v1/users/permissions/options",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/users/{user_id}/permissions",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/users/{user_id}/permissions/revert",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/users/",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER", "SUPERADMIN"],
    },
    {"method": "POST", "path": "/api/v1/users/", "allowed": ["ADMIN", "SUPERADMIN"]},
    {
        "method": "GET",
        "path": "/api/v1/users/role/{role}",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/users/search",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/users/store/{store_id}",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/users/summary",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/users/{user_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/users/{user_id}",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/users/{user_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/users/{user_id}/assign-store",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/users/{user_id}/reset-password",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/users/{user_id}/roles/{role}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/users/{user_id}/roles/{role}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/users/{user_id}/stores/{store_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/users/{user_id}/stores/{store_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    # E4 approval-PIN management. PUT + GET-status are self-OR-admin (the handler
    # enforces self/admin inline), so AUTHENTICATED; DELETE (force-clear) is
    # ADMIN/SUPERADMIN only.
    {
        "method": "PUT",
        "path": "/api/v1/users/{user_id}/approval-pin",
        "allowed": AUTHENTICATED,
    },
    {
        "method": "DELETE",
        "path": "/api/v1/users/{user_id}/approval-pin",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/users/{user_id}/approval-pin/status",
        "allowed": AUTHENTICATED,
    },
]

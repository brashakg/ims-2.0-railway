"""
POLICY rows: /clinical, /crm.

Moved VERBATIM from the flat ``api/services/rbac_policy.py`` lines 919-1364.
These rows are load-bearing DATA. Their ORDER is preserved (``policy_for``
prefers the most specific match, and a row under a module widens that
module's capability grant-union). Do not reorder, reword or re-group them;
a new route needs a new row here or CI fails the coverage lock.
"""

from __future__ import annotations

from typing import Dict, List

ROWS: List[Dict[str, object]] = [
    # --- /api/v1/clinical ---
    {"method": "GET", "path": "/api/v1/clinical", "allowed": "PUBLIC"},
    {"method": "GET", "path": "/api/v1/clinical/", "allowed": "PUBLIC"},
    {
        "method": "GET",
        "path": "/api/v1/clinical/abuse-detection",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/clinical/conversion-dashboard",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "OPTOMETRIST",
            "STORE_MANAGER",
            "SUPERADMIN",
        ],
        "store_scoped": True,
    },
    {"method": "GET", "path": "/api/v1/clinical/eye-tests", "allowed": "AUTHENTICATED"},
    {
        "method": "GET",
        "path": "/api/v1/clinical/optometrist/{optometrist_id}/stats",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "OPTOMETRIST",
            "STORE_MANAGER",
            "SUPERADMIN",
        ],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/clinical/patient-queue",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/clinical/prescription-redo-rate",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/clinical/prescriptions/{prescription_id}/print",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/clinical/prescriptions/{prescription_id}/redo",
        "allowed": ["ADMIN", "AREA_MANAGER", "OPTOMETRIST", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/clinical/prescriptions/{prescription_id}/redos",
        "allowed": "AUTHENTICATED",
    },
    {"method": "GET", "path": "/api/v1/clinical/queue", "allowed": "AUTHENTICATED"},
    # Owner ruling 2026-09-06: sales roles may add a customer to today's queue
    # from the POS customer panel (clinical._QUEUE_ADD_ROLES). Every other
    # clinical write row below is unchanged. Stays on the clinical:write
    # capability: that union already carries AUTHENTICATED (manufacturability-
    # check), so this row broadens no grant union -- see test_misc_gating.
    {
        "method": "POST",
        "path": "/api/v1/clinical/queue",
        "allowed": ["ADMIN", "OPTOMETRIST", "SALES_CASHIER", "SALES_STAFF", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/clinical/queue/stats",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "DELETE",
        "path": "/api/v1/clinical/queue/{queue_id}",
        "allowed": ["ADMIN", "OPTOMETRIST", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/clinical/queue/{queue_id}/start-test",
        "allowed": ["ADMIN", "OPTOMETRIST", "STORE_MANAGER"],
    },
    {
        "method": "PATCH",
        "path": "/api/v1/clinical/queue/{queue_id}/status",
        "allowed": ["ADMIN", "OPTOMETRIST", "STORE_MANAGER"],
    },
    {"method": "GET", "path": "/api/v1/clinical/tests", "allowed": "AUTHENTICATED"},
    # Eye-test READS carry clinical PII (Rx + exam findings) -> same role set
    # as prescription reads (prescriptions._RX_READ_ROLES / require_rx_read);
    # store-scope enforced per object in the handler (404-hide cross-store).
    {
        "method": "GET",
        "path": "/api/v1/clinical/tests/customer/{customer_id}",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "OPTOMETRIST",
            "SALES_CASHIER",
            "SALES_STAFF",
            "STORE_MANAGER",
            "SUPERADMIN",
            "WORKSHOP_STAFF",
        ],
    },
    {
        "method": "GET",
        "path": "/api/v1/clinical/tests/patient/{customer_phone}",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "OPTOMETRIST",
            "SALES_CASHIER",
            "SALES_STAFF",
            "STORE_MANAGER",
            "SUPERADMIN",
            "WORKSHOP_STAFF",
        ],
    },
    {
        "method": "GET",
        "path": "/api/v1/clinical/tests/{test_id}",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "OPTOMETRIST",
            "SALES_CASHIER",
            "SALES_STAFF",
            "STORE_MANAGER",
            "SUPERADMIN",
            "WORKSHOP_STAFF",
        ],
    },
    {
        "method": "POST",
        "path": "/api/v1/clinical/tests/{test_id}/complete",
        "allowed": ["ADMIN", "OPTOMETRIST", "STORE_MANAGER"],
    },
    # Amend an already-completed exam (the clinic Edit screen, which reopens the
    # full seven-tab form). Amending a recorded medical power is the same act as
    # recording it, so it takes the SAME gate as completion, plus the per-object
    # store guard the handler enforces.
    {
        "method": "PUT",
        "path": "/api/v1/clinical/tests/{test_id}/exam",
        "allowed": ["ADMIN", "OPTOMETRIST", "STORE_MANAGER"],
        "store_scoped": True,
    },
    # F50 -- send a completed Rx to the sales floor (in-app handover). Same gate
    # as test completion (require_roles(*_CLINICAL_ROLES); SUPERADMIN implicit).
    # Per-store feature flag + store IDOR guard enforced in the handler.
    {
        "method": "POST",
        "path": "/api/v1/clinical/tests/{test_id}/send-to-floor",
        "allowed": ["ADMIN", "OPTOMETRIST", "STORE_MANAGER"],
        "store_scoped": True,
    },
    # CLI-7 — frame+lens+Rx manufacturability pre-check
    {
        "method": "POST",
        "path": "/api/v1/clinical/manufacturability-check",
        "allowed": "AUTHENTICATED",
    },
    # CLI-9 — named lens-power combos (save-and-reuse Rx templates).
    # The two WRITE rows mirror clinical.py _CLINICAL_ROLES exactly (ADMIN /
    # STORE_MANAGER / OPTOMETRIST + SUPERADMIN via check_access). AREA_MANAGER is
    # deliberately NOT a write role here -- it is a supervisory READ role across
    # clinical (cf. _ABUSE_VIEW_ROLES / _CONVERSION_VIEW_ROLES), so it keeps the
    # GET row. Narrowing these two rows broadens nothing: the clinical:write
    # capability union already carries AREA_MANAGER via the redo route
    # (_REDO_ROLES), so no dedicated capability key is warranted here.
    {
        "method": "GET",
        "path": "/api/v1/clinical/lens-power-combos",
        "allowed": ["ADMIN", "AREA_MANAGER", "OPTOMETRIST", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/clinical/lens-power-combos",
        "allowed": ["ADMIN", "OPTOMETRIST", "STORE_MANAGER"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/clinical/lens-power-combos/{combo_id}",
        "allowed": ["ADMIN", "OPTOMETRIST", "STORE_MANAGER"],
    },
    # CLI-12: ophthalmic device CSV import (autorefractor / lensmeter -> Rx).
    # Same role gate as clinical write operations (clinical_device_import.py
    # _DEVICE_IMPORT_ROLES). SUPERADMIN passes via require_roles always.
    {
        "method": "POST",
        "path": "/api/v1/clinical/device-import",
        "allowed": ["ADMIN", "OPTOMETRIST", "STORE_MANAGER", "SUPERADMIN"],
    },
    # CLI-11: SOAP exam note endpoints.  GET carries the exam narrative + Dx
    # codes -> same role set as prescription reads (require_rx_read) +
    # per-object store scope; POST replaces the note -> same roles as test
    # completion.
    {
        "method": "GET",
        "path": "/api/v1/clinical/tests/{test_id}/soap-note",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "OPTOMETRIST",
            "SALES_CASHIER",
            "SALES_STAFF",
            "STORE_MANAGER",
            "SUPERADMIN",
            "WORKSHOP_STAFF",
        ],
    },
    {
        "method": "POST",
        "path": "/api/v1/clinical/tests/{test_id}/soap-note",
        "allowed": ["ADMIN", "OPTOMETRIST", "STORE_MANAGER"],
    },
    # --- /api/v1/crm ---
    {"method": "GET", "path": "/api/v1/crm", "allowed": "PUBLIC"},
    {"method": "GET", "path": "/api/v1/crm/", "allowed": "PUBLIC"},
    # F40 VIP-churn watchlist (#40): SUPERADMIN/ADMIN; ADMIN store-scoped server-side.
    {
        "method": "GET",
        "path": "/api/v1/crm/vip-churn",
        "allowed": ["SUPERADMIN", "ADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/crm/vip-churn/{customer_id}/intervene",
        "allowed": ["SUPERADMIN", "ADMIN"],
    },
    # F43 VIP personal-triggers (#43): STAFF_ALERT slice, comms-DARK. Writes are
    # CRM management roles; reads add CATALOG_MANAGER/OPTOMETRIST (they see the
    # 360 view). Store-guarded server-side (non-SUPERADMIN scoped to owned store).
    {
        "method": "POST",
        "path": "/api/v1/crm/customers/{customer_id}/vip",
        "allowed": ["STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/crm/customers/{customer_id}/vip",
        "allowed": [
            "STORE_MANAGER",
            "AREA_MANAGER",
            "ADMIN",
            "SUPERADMIN",
            "CATALOG_MANAGER",
            "OPTOMETRIST",
        ],
    },
    {
        "method": "POST",
        "path": "/api/v1/crm/personal-triggers",
        "allowed": ["STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/crm/personal-triggers",
        "allowed": [
            "STORE_MANAGER",
            "AREA_MANAGER",
            "ADMIN",
            "SUPERADMIN",
            "CATALOG_MANAGER",
            "OPTOMETRIST",
        ],
    },
    {
        "method": "PUT",
        "path": "/api/v1/crm/personal-triggers/{trigger_id}",
        "allowed": ["STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/crm/personal-triggers/{trigger_id}",
        "allowed": ["STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"],
    },
    # F39 NBA daily call list (#39): store-facing call work-list (store-scoped via
    # validate_store_access). NOT ACCOUNTANT/OPTOMETRIST/CATALOG/WORKSHOP/CASHIER.
    {
        "method": "GET",
        "path": "/api/v1/crm/nba/{store_id}",
        "allowed": [
            "SUPERADMIN",
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "SALES_STAFF",
            "SALES_CASHIER",
        ],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/crm/nba/{store_id}/dismiss",
        "allowed": [
            "SUPERADMIN",
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "SALES_STAFF",
            "SALES_CASHIER",
        ],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/crm/nba/{store_id}/complete",
        "allowed": [
            "SUPERADMIN",
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "SALES_STAFF",
            "SALES_CASHIER",
        ],
        "store_scoped": True,
    },
    # F41 lapsed-patient reactivation (#41): in-app reactivation work-list +
    # outcome log (store-scoped via validate_store_access). DARK -- the work-list
    # never sends a message and never mints a voucher. Store-facing roles ONLY,
    # matching the FE route gate (App.tsx customers/reactivation). ACCOUNTANT was
    # dropped from analytics to close the FE/BE role drift (audit F41-P3).
    {
        "method": "GET",
        "path": "/api/v1/crm/reactivation/{store_id}",
        "allowed": [
            "SUPERADMIN",
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "SALES_STAFF",
            "SALES_CASHIER",
        ],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/crm/reactivation/{store_id}/log",
        "allowed": [
            "SUPERADMIN",
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "SALES_STAFF",
            "SALES_CASHIER",
        ],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/crm/reactivation/{store_id}/analytics",
        "allowed": [
            "SUPERADMIN",
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "SALES_STAFF",
            "SALES_CASHIER",
        ],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/crm/customers/360/{customer_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/crm/customers/churn-risk/list",
        # Matches the /customers/segmentation screen gate. Was AUTHENTICATED
        # while the route returned whole customer documents.
        "allowed": ["SUPERADMIN", "ADMIN", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/crm/customers/segment/rfm",
        # Matches the /customers/segmentation screen gate. Was AUTHENTICATED
        # while the route published company-wide average customer value.
        "allowed": ["SUPERADMIN", "ADMIN", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/crm/customers/{customer_id}/cl-refill-status",
        "allowed": "AUTHENTICATED",
    },
    # CRM-2 phase 2: in-app CL refill-due worklist + deduped reminder-task
    # creator. Read worklist = any store staff; create reminders = manager+.
    # NO outbound message (customer send stays WhatsApp-gated / dark).
    {
        "method": "GET",
        "path": "/api/v1/crm/cl-refill/{store_id}/due",
        "allowed": [
            "SUPERADMIN",
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "SALES_STAFF",
            "SALES_CASHIER",
            "OPTOMETRIST",
        ],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/crm/cl-refill/{store_id}/create-reminders",
        "allowed": ["SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/crm/customers/{customer_id}/interactions",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/crm/customers/{customer_id}/interactions",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/crm/customers/{customer_id}/lifecycle",
        "allowed": "AUTHENTICATED",
    },
    # Loyalty points redeem against real money. NARROWED from AUTHENTICATED to
    # match its twin POST /customers/{customer_id}/loyalty/add and the route's
    # own require_roles(*_CREDIT_ROLES) gate -- a stale AUTHENTICATED row here
    # would let the middleware wave through a caller the route then 403s.
    {
        "method": "POST",
        "path": "/api/v1/crm/customers/{customer_id}/loyalty-points",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/crm/customers/{customer_id}/prescriptions",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/crm/customers/{customer_id}/return-risk",
        "allowed": "AUTHENTICATED",
    },
]

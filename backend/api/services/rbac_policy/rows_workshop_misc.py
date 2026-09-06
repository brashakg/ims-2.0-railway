"""
POLICY rows: /vouchers, /walkouts, /webhooks, /workshop, /budgets, /reminders.

Moved VERBATIM from the flat ``api/services/rbac_policy.py`` lines 7109-7667.
These rows are load-bearing DATA. Their ORDER is preserved (``policy_for``
prefers the most specific match, and a row under a module widens that
module's capability grant-union). Do not reorder, reword or re-group them;
a new route needs a new row here or CI fails the coverage lock.
"""

from __future__ import annotations

from typing import Dict, List

ROWS: List[Dict[str, object]] = [
    # --- /api/v1/vouchers ---
    # IDOR hardening: issue validates an explicit store_id against the
    # caller's reach (validate_store_access) and cancel is scoped to the
    # voucher's issuing store (can_access_store_scoped; ADMIN/SUPERADMIN
    # cross-store). REDEEM stays chain-wide BY DESIGN -- a gift card is
    # redeemable at any store -- so the redeem row is deliberately NOT
    # store_scoped.
    {
        "method": "GET",
        "path": "/api/v1/vouchers",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/vouchers",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/vouchers/",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/vouchers/",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {"method": "GET", "path": "/api/v1/vouchers/{code}", "allowed": "AUTHENTICATED"},
    {
        "method": "POST",
        "path": "/api/v1/vouchers/{code}/cancel",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/vouchers/{code}/redeem",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "CASHIER",
            "SALES_CASHIER",
            "SALES_STAFF",
            "STORE_MANAGER",
        ],
    },
    # --- /api/v1/walkouts ---
    {"method": "GET", "path": "/api/v1/walkouts", "allowed": "AUTHENTICATED"},
    {"method": "POST", "path": "/api/v1/walkouts", "allowed": "AUTHENTICATED"},
    {"method": "GET", "path": "/api/v1/walkouts/", "allowed": "AUTHENTICATED"},
    {"method": "POST", "path": "/api/v1/walkouts/", "allowed": "AUTHENTICATED"},
    # F45 D5 -- POS soft-block compliance counter (read-only; never blocks a sale).
    {
        "method": "GET",
        "path": "/api/v1/walkouts/pos-compliance-check",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/walkouts/conversion-feed",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/walkouts/dashboard/fu-status",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/walkouts/dashboard/per-staff",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/walkouts/dashboard/result-breakdown",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/walkouts/dashboard/top-reasons",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/walkouts/followups/due-today",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/walkouts/followups/escalate-overdue",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/walkouts/walkins/manual-topup",
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
        "path": "/api/v1/walkouts/walkins/mtd",
        "allowed": "AUTHENTICATED",
    },
    # N3 -- manager sets/updates a per-staff walk-in count (drives the SC
    # conversion denominator). Managers + admin only so sales staff cannot
    # self-inflate their own conversion %. In-handler role gate mirrors this.
    {
        "method": "PATCH",
        "path": "/api/v1/walkouts/walkins/per-staff",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "SUPERADMIN",
        ],
    },
    {
        "method": "POST",
        "path": "/api/v1/walkouts/walkins/pos-increment",
        "allowed": [
            "ACCOUNTANT",
            "ADMIN",
            "AREA_MANAGER",
            "CASHIER",
            "OPTOMETRIST",
            "SALES_CASHIER",
            "SALES_STAFF",
            "STORE_MANAGER",
            "SUPERADMIN",
        ],
    },
    # N3 -- footfall capture status (PENDING / PARTIAL / COMPLETE). Same
    # store-scoping + access class as walkins/today (the SC scorecard input).
    {
        "method": "GET",
        "path": "/api/v1/walkouts/walkins/status",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/walkouts/walkins/today",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "DELETE",
        "path": "/api/v1/walkouts/{walkout_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/walkouts/{walkout_id}",
        "allowed": "AUTHENTICATED",
    },
    # Edit is OWNERSHIP-gated in-handler (_check_edit_permission): SUPERADMIN/
    # ADMIN any row, STORE/AREA manager their store, and SALES_STAFF/SALES_CASHIER/
    # CASHIER their OWN rows. That is a data-conditional gate the role table can't
    # express (like store_scoped), so the role-class is AUTHENTICATED -- a static
    # role list here would be STRICTER than the real route (it 403'd sales staff
    # editing their own walkout, which the handler actually allows).
    {
        "method": "PATCH",
        "path": "/api/v1/walkouts/{walkout_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/walkouts/{walkout_id}/followups",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "PATCH",
        "path": "/api/v1/walkouts/{walkout_id}/followups/{round_num}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/walkouts/{walkout_id}/followups/{round_num}/approve",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "PATCH",
        "path": "/api/v1/walkouts/{walkout_id}/result",
        "allowed": "AUTHENTICATED",
    },
    # --- /api/v1/webhooks ---
    {"method": "GET", "path": "/api/v1/webhooks/health", "allowed": "PUBLIC"},
    {"method": "POST", "path": "/api/v1/webhooks/razorpay", "allowed": "PUBLIC"},
    {"method": "POST", "path": "/api/v1/webhooks/shiprocket", "allowed": "PUBLIC"},
    {"method": "POST", "path": "/api/v1/webhooks/shopify", "allowed": "PUBLIC"},
    {"method": "POST", "path": "/api/v1/webhooks/msg91/delivery", "allowed": "PUBLIC"},
    # MSG91 "Webhook (New)" per-channel receivers (delivery reports, click
    # events, WhatsApp inbound). PUBLIC like the receivers above -- the HMAC
    # signature / shared token IS the auth (verified inside the handler
    # against the msg91 integration's webhook_secret / MSG91_WEBHOOK_TOKEN);
    # MSG91 has no IMS bearer token. {channel} is a closed 6-value allowlist
    # inside the handler (sms/whatsapp/email/voice/rcs/shorturl).
    {
        "method": "POST",
        "path": "/api/v1/integrations/msg91/webhooks/{channel}",
        "allowed": "PUBLIC",
    },
    # CRM-14: WhatsApp inbound (Meta Business API).
    # GET = Meta verify-token challenge (PUBLIC -- Meta hits this with no IMS auth).
    # POST = Meta message delivery (HMAC-signed by Meta; no IMS bearer token).
    # GET conversations = inbox view; role-checked INSIDE the handler (self_enforced).
    {"method": "GET", "path": "/api/v1/webhooks/whatsapp", "allowed": "PUBLIC"},
    {"method": "POST", "path": "/api/v1/webhooks/whatsapp", "allowed": "PUBLIC"},
    {
        "method": "GET",
        "path": "/api/v1/webhooks/whatsapp/conversations",
        "allowed": ["SUPERADMIN", "ADMIN", "STORE_MANAGER"],
        "self_enforced": True,
    },
    # --- /api/v1/workshop ---
    {"method": "GET", "path": "/api/v1/workshop", "allowed": "PUBLIC"},
    {"method": "GET", "path": "/api/v1/workshop/", "allowed": "PUBLIC"},
    {
        "method": "GET",
        "path": "/api/v1/workshop/dashboard-kpis",
        "allowed": "AUTHENTICATED",
    },
    {"method": "GET", "path": "/api/v1/workshop/jobs", "allowed": "AUTHENTICATED"},
    {"method": "POST", "path": "/api/v1/workshop/jobs", "allowed": "AUTHENTICATED"},
    {
        "method": "GET",
        "path": "/api/v1/workshop/jobs/by-vendor/{vendor_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/workshop/jobs/{job_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "PUT",
        "path": "/api/v1/workshop/jobs/{job_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/workshop/jobs/{job_id}/assign",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/workshop/jobs/{job_id}/complete",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "SUPERADMIN",
            "WORKSHOP_STAFF",
        ],
    },
    {
        # BUG-092: sales-confirmation gate is a SALES act, not WORKSHOP_STAFF's.
        "method": "PATCH",
        "path": "/api/v1/workshop/jobs/{job_id}/fitting-details",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "CASHIER",
            "SALES_CASHIER",
            "SALES_STAFF",
            "STORE_MANAGER",
            "SUPERADMIN",
        ],
    },
    {
        "method": "GET",
        "path": "/api/v1/workshop/jobs/{job_id}/label",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/workshop/jobs/{job_id}/lens-status",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER", "WORKSHOP_STAFF"],
    },
    {
        "method": "POST",
        "path": "/api/v1/workshop/jobs/{job_id}/notify-ready",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER", "WORKSHOP_STAFF"],
    },
    # F2 -- disposable job-card print stamp (workshop fulfilment ladder).
    {
        "method": "POST",
        "path": "/api/v1/workshop/jobs/{job_id}/print-job-card",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "CASHIER",
            "STORE_MANAGER",
            "WORKSHOP_STAFF",
        ],
    },
    # /qc gate tightened to WORKSHOP_ROLES (not AUTHENTICATED) — sales staff
    # cannot run or override QC. Mirrors require_roles(*WORKSHOP_ROLES) on the handler.
    {
        "method": "POST",
        "path": "/api/v1/workshop/jobs/{job_id}/qc",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "SUPERADMIN",
            "WORKSHOP_STAFF",
        ],
    },
    # New Phase 6.9 structured QC checklist endpoint.
    {
        "method": "POST",
        "path": "/api/v1/workshop/jobs/{job_id}/qc-checklist",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "SUPERADMIN",
            "WORKSHOP_STAFF",
        ],
    },
    {
        "method": "POST",
        "path": "/api/v1/workshop/jobs/{job_id}/rework",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "SUPERADMIN",
            "WORKSHOP_STAFF",
        ],
    },
    {
        "method": "POST",
        "path": "/api/v1/workshop/jobs/{job_id}/scan-advance",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "CASHIER",
            "STORE_MANAGER",
            "WORKSHOP_STAFF",
        ],
    },
    {
        "method": "POST",
        "path": "/api/v1/workshop/jobs/{job_id}/start",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "SUPERADMIN",
            "WORKSHOP_STAFF",
        ],
    },
    {
        "method": "PATCH",
        "path": "/api/v1/workshop/jobs/{job_id}/status",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "SUPERADMIN",
            "WORKSHOP_STAFF",
        ],
    },
    {
        "method": "PATCH",
        "path": "/api/v1/workshop/jobs/{job_id}/vendor",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "SUPERADMIN",
            "WORKSHOP_STAFF",
        ],
    },
    {
        "method": "POST",
        "path": "/api/v1/workshop/jobs/{job_id}/vendor-status",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "SUPERADMIN",
            "WORKSHOP_STAFF",
        ],
    },
    {"method": "GET", "path": "/api/v1/workshop/overdue", "allowed": "AUTHENTICATED"},
    {"method": "GET", "path": "/api/v1/workshop/pending", "allowed": "AUTHENTICATED"},
    {
        "method": "GET",
        "path": "/api/v1/workshop/product-label",
        "allowed": "AUTHENTICATED",
    },
    {"method": "GET", "path": "/api/v1/workshop/ready", "allowed": "AUTHENTICATED"},
    # F13 -- remake justification taxonomy: read anywhere (the bench needs it
    # for the rework dialog), replace is owner/admin-only.
    {
        "method": "GET",
        "path": "/api/v1/workshop/remake-reason-codes",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "PUT",
        "path": "/api/v1/workshop/remake-reason-codes",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    # F2 -- internal lab routing (disposable barcoded job cards).
    {
        "method": "POST",
        "path": "/api/v1/workshop/scan",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "CASHIER",
            "STORE_MANAGER",
            "WORKSHOP_STAFF",
        ],
    },
    # F13 -- spoilage analytics rollup exposes cost data: manager+ only.
    # Mirrors the inline role check in workshop.get_spoilage_analytics.
    {
        "method": "GET",
        "path": "/api/v1/workshop/spoilage-analytics",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/workshop/stations",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/workshop/stations",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/workshop/stations/{code}/queue",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "CASHIER",
            "STORE_MANAGER",
            "WORKSHOP_STAFF",
        ],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/workshop/technician-workload",
        "allowed": "AUTHENTICATED",
    },
    # Budgeting (dual-mode planned vs actual) -- manager+/accountant + store-scoped.
    {
        "method": "POST",
        "path": "/api/v1/budgets",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/budgets/",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/budgets",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/budgets/",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/budgets/variance",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "DELETE",
        "path": "/api/v1/budgets/{budget_id}",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    # ------------------------------------------------------------------
    # E6 reminder rail (routers/reminders.py): ADMIN/AREA_MANAGER/STORE_MANAGER
    # (SUPERADMIN implicit). STORE-scope rules are additionally locked to that
    # store via _enforce_store_scope; GLOBAL/ENTITY mutations require ADMIN+.
    # ------------------------------------------------------------------
    {
        "method": "GET",
        "path": "/api/v1/reminders/rules",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/reminders/rules",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/reminders/rules/{rule_id}",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "PUT",
        "path": "/api/v1/reminders/rules/{rule_id}",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "DELETE",
        "path": "/api/v1/reminders/rules/{rule_id}",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/reminders/rules/{rule_id}/toggle",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/reminders/rules/{rule_id}/preview",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/reminders/rules/{rule_id}/run-now",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/reminders/rules/{rule_id}/history",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
]

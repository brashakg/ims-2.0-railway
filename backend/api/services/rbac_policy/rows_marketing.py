"""
POLICY rows: /marketing, /promotions, /notifications.

Moved VERBATIM from the flat ``api/services/rbac_policy.py`` lines 4172-4520.
These rows are load-bearing DATA. Their ORDER is preserved (``policy_for``
prefers the most specific match, and a row under a module widens that
module's capability grant-union). Do not reorder, reword or re-group them;
a new route needs a new row here or CI fails the coverage lock.
"""

from __future__ import annotations

from typing import Dict, List

ROWS: List[Dict[str, object]] = [
    # --- /api/v1/marketing ---
    {
        "method": "GET",
        "path": "/api/v1/marketing/consent-text",
        "allowed": "AUTHENTICATED",
    },
    {"method": "PUT", "path": "/api/v1/marketing/consent-text", "allowed": ["ADMIN"]},
    # Campaign layer (routers/campaigns.py): ADMIN/AREA_MANAGER/STORE_MANAGER
    # (SUPERADMIN implicit). Campaign-specific routes additionally restrict a
    # store-scoped campaign (one carrying a store_id) to that store via
    # _enforce_store_scope -> validate_store_access; hence store_scoped=True.
    {
        "method": "GET",
        "path": "/api/v1/marketing/campaigns",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/marketing/campaigns",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/marketing/campaigns/{campaign_id}",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "PUT",
        "path": "/api/v1/marketing/campaigns/{campaign_id}",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "DELETE",
        "path": "/api/v1/marketing/campaigns/{campaign_id}",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/marketing/campaigns/{campaign_id}/duplicate",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/marketing/campaigns/{campaign_id}/schedule",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/marketing/campaigns/{campaign_id}/pause",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/marketing/campaigns/{campaign_id}/resume",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/marketing/campaigns/{campaign_id}/send",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/marketing/campaigns/{campaign_id}/analytics",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/marketing/segments",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/marketing/segments/{key}/preview",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/marketing/notifications/logs",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/marketing/notifications/send",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/marketing/notifications/send-bulk",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/marketing/nps-dashboard",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/marketing/nps-response",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/marketing/nps-survey/{order_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/marketing/referral-invite/{customer_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/marketing/referrals",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/marketing/referrals/{referral_id}/redeem",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/marketing/review-request/{order_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/marketing/rx-expiry-alerts",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/marketing/rx-reminder/{customer_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/marketing/rx-snooze/{customer_id}",
        "allowed": "AUTHENTICATED",
    },
    {"method": "POST", "path": "/api/v1/marketing/walkin", "allowed": "AUTHENTICATED"},
    {"method": "GET", "path": "/api/v1/marketing/walkins", "allowed": "AUTHENTICATED"},
    # F45 D1 -- RETIRED to HTTP 410 Gone (zombie duplicate of /api/v1/walkouts).
    # The routes remain registered (returning 410) so coverage-lock + no-stale
    # stay green; the canonical 30-field walkout path is /api/v1/walkouts. A
    # logged-in caller simply receives 410 -- AUTHENTICATED is correct here.
    {
        "method": "GET",
        "path": "/api/v1/marketing/walkout-recoveries",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/marketing/walkout/{customer_id}",
        "allowed": "AUTHENTICATED",
    },
    # CRM-8: Promo offer-template library (BOGO / COMBO / THRESHOLD).
    # Same role gate as campaigns (ADMIN/AREA_MANAGER/STORE_MANAGER, SUPERADMIN
    # implicit).  Store-scoped templates additionally validated inside the handler
    # via _enforce_store_scope -> validate_store_access.
    {
        "method": "GET",
        "path": "/api/v1/marketing/promo-templates",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/marketing/promo-templates",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/marketing/promo-templates/{template_id}",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "PUT",
        "path": "/api/v1/marketing/promo-templates/{template_id}",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "DELETE",
        "path": "/api/v1/marketing/promo-templates/{template_id}",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    # --- /api/v1/promotions (F11/F12 advanced promotions + bundling engine) ---
    # WRITE (create/update/deactivate): ADMIN/SUPERADMIN + CATALOG_MANAGER (pricing
    # visibility) + AREA/STORE managers (store-scoped inside the handler).
    # READ + the pure /evaluate preview: the write roles plus ACCOUNTANT (margin)
    # and the POS staff who see what applied. uses_count is never client-settable.
    {
        "method": "GET",
        "path": "/api/v1/promotions",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "CATALOG_MANAGER",
            "ACCOUNTANT",
            "SALES_CASHIER",
            "SALES_STAFF",
            "CASHIER",
        ],
    },
    {
        "method": "GET",
        "path": "/api/v1/promotions/",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "CATALOG_MANAGER",
            "ACCOUNTANT",
            "SALES_CASHIER",
            "SALES_STAFF",
            "CASHIER",
        ],
    },
    {
        "method": "POST",
        "path": "/api/v1/promotions",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER", "CATALOG_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/promotions/",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER", "CATALOG_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/promotions/evaluate",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "CATALOG_MANAGER",
            "ACCOUNTANT",
            "SALES_CASHIER",
            "SALES_STAFF",
            "CASHIER",
        ],
    },
    {
        "method": "GET",
        "path": "/api/v1/promotions/{promo_id}",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "CATALOG_MANAGER",
            "ACCOUNTANT",
            "SALES_CASHIER",
            "SALES_STAFF",
            "CASHIER",
        ],
    },
    {
        "method": "PUT",
        "path": "/api/v1/promotions/{promo_id}",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER", "CATALOG_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "DELETE",
        "path": "/api/v1/promotions/{promo_id}",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER", "CATALOG_MANAGER"],
        "store_scoped": True,
    },
    # CRM-15: WhatsApp opt-in / opt-out STOP ledger.
    # Any authenticated staff can record a consent event (staff relay verbal
    # opt-out from customers).  The full audit ledger is ADMIN-only (compliance).
    {
        "method": "POST",
        "path": "/api/v1/marketing/whatsapp-consent",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/marketing/whatsapp-consent/{customer_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/marketing/whatsapp-consent-ledger",
        "allowed": ["ADMIN"],
    },
    # CRM-16: Ad Performance dashboard (Google + Meta). Finance-sensitive:
    # restricted to ADMIN and SUPERADMIN (SUPERADMIN implicit via require_roles).
    {
        "method": "GET",
        "path": "/api/v1/marketing/ad-performance",
        "allowed": ["SUPERADMIN", "ADMIN"],
    },
    # Marketing Funnel Phase 0: consent-gated ad-audience export (DARK). Discloses
    # hashed customer PII to a Google/Meta match audience -> SUPERADMIN/ADMIN only.
    {
        "method": "GET",
        "path": "/api/v1/marketing/ad-audience/summary",
        "allowed": ["SUPERADMIN", "ADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/marketing/ad-audience/export",
        "allowed": ["SUPERADMIN", "ADMIN"],
    },
    # --- /api/v1/notifications ---
    {"method": "GET", "path": "/api/v1/notifications", "allowed": "AUTHENTICATED"},
    {"method": "GET", "path": "/api/v1/notifications/", "allowed": "AUTHENTICATED"},
    {
        "method": "POST",
        "path": "/api/v1/notifications/mark-all-read",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/notifications/unread-count",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "PATCH",
        "path": "/api/v1/notifications/{notification_id}/read",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/notifications/{notification_id}/snooze",
        "allowed": "AUTHENTICATED",
    },
]

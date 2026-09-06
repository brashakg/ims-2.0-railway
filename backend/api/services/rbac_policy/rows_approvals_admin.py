"""
POLICY rows: /approvals, /admin.

Moved VERBATIM from the flat ``api/services/rbac_policy.py`` lines 119-621.
These rows are load-bearing DATA. Their ORDER is preserved (``policy_for``
prefers the most specific match, and a row under a module widens that
module's capability grant-union). Do not reorder, reword or re-group them;
a new route needs a new row here or CI fails the coverage lock.
"""

from __future__ import annotations

from typing import Dict, List

from ._core import AUTHENTICATED

ROWS: List[Dict[str, object]] = [
    # --- /api/v1/approvals (E4 PIN-gated maker-checker) ---
    # Any authenticated maker can open a request, view their own, or consume an
    # approval they hold; approve/reject is gated to business approvers (the PIN
    # is the second factor inside the handler). Inbox adds ACCOUNTANT (read-only).
    {"method": "POST", "path": "/api/v1/approvals/requests", "allowed": AUTHENTICATED},
    {
        "method": "GET",
        "path": "/api/v1/approvals/requests/inbox",
        "allowed": [
            "SUPERADMIN",
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "ACCOUNTANT",
        ],
    },
    {
        "method": "GET",
        "path": "/api/v1/approvals/requests/mine",
        "allowed": AUTHENTICATED,
    },
    {
        "method": "GET",
        "path": "/api/v1/approvals/requests/{request_id}",
        "allowed": AUTHENTICATED,
    },
    {
        "method": "POST",
        "path": "/api/v1/approvals/requests/{request_id}/approve",
        "allowed": ["SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/approvals/requests/{request_id}/reject",
        "allowed": ["SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/approvals/requests/{request_id}/consume",
        "allowed": AUTHENTICATED,
    },
    # --- /api/v1/admin ---
    {"method": "GET", "path": "/api/v1/admin", "allowed": ["ADMIN", "SUPERADMIN"]},
    {"method": "GET", "path": "/api/v1/admin/", "allowed": ["ADMIN", "SUPERADMIN"]},
    {
        "method": "GET",
        "path": "/api/v1/admin/brands",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/brands",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/admin/brands/{brand_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/brands/{brand_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/admin/brands/{brand_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/brands/{brand_id}/subbrands",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/brands/{brand_id}/subbrands",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/admin/brands/{brand_id}/subbrands/{subbrand_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/categories",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/categories",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/admin/categories/{category_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/categories/{category_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/admin/categories/{category_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/discounts/enforced-caps",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/discounts/promo-codes",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/discounts/promo-codes",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/admin/discounts/promo-codes/{code_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/discounts/role-caps",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/discounts/role-caps",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/discounts/rules",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/discounts/tier-discounts",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/discounts/tier-discounts",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {"method": "GET", "path": "/api/v1/admin/escalations", "allowed": ["ADMIN"]},
    {"method": "GET", "path": "/api/v1/admin/hsn", "allowed": ["ADMIN", "SUPERADMIN"]},
    {"method": "POST", "path": "/api/v1/admin/hsn", "allowed": ["ADMIN", "SUPERADMIN"]},
    {
        "method": "DELETE",
        "path": "/api/v1/admin/hsn/{hsn_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/admin/hsn/{hsn_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/integrations",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/integrations/razorpay",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/integrations/razorpay",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/integrations/razorpay/test",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/integrations/shiprocket",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/integrations/shiprocket",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/integrations/shiprocket/create-shipment",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/integrations/shiprocket/rates",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/integrations/shiprocket/test",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/integrations/shiprocket/track/{awb}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/integrations/shopify",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/integrations/shopify",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/integrations/shopify/sync-inventory",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/integrations/shopify/sync-orders",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/integrations/shopify/test",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/integrations/tally",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/integrations/tally",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/integrations/tally/exports",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/integrations/tally/regenerate",
        "allowed": ["SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/integrations/tally/test",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/integrations/tally/voucher.xml",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/lens/addons",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/lens/addons",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/admin/lens/addons/{addon_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/admin/lens/addons/{addon_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/lens/brands",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/lens/brands",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/admin/lens/brands/{brand_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/admin/lens/brands/{brand_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/lens/coatings",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/lens/coatings",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/admin/lens/coatings/{coating_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/admin/lens/coatings/{coating_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/lens/indices",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/lens/indices",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/admin/lens/indices/{index_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/admin/lens/indices/{index_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/lens/pricing",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/lens/pricing",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/lens/pricing-ranges",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/lens/pricing-ranges",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/lens/pricing-ranges/bulk",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/lens/pricing-ranges/quote",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/admin/lens/pricing-ranges/{range_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/admin/lens/pricing-ranges/{range_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    # Online-store bridge health tile. Lives under the ADMIN-gated admin router
    # but the handler narrows to SUPERADMIN inline (online-store admin is a
    # SUPERADMIN concern, matching the Jarvis / ecommerce-SSO posture).
    {
        "method": "GET",
        "path": "/api/v1/admin/online-store/sync-health",
        "allowed": ["SUPERADMIN"],
    },
    # BVI safety nets (Steps 3, 4, 6): drift detector, oversell repush, parity oracle.
    # All narrowed to SUPERADMIN inline (same posture as sync-health above).
    {
        "method": "GET",
        "path": "/api/v1/admin/online-store/drift",
        "allowed": ["SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/online-store/repush-oversell",
        "allowed": ["SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/online-store/parity",
        "allowed": ["SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/online-store/rehost-images",
        "allowed": ["SUPERADMIN"],
    },
    # Phase-6 cutover: register Shopify webhookSubscriptions pointing at IMS's
    # signed receiver (/api/v1/webhooks/shopify). Dry-run by default; mutation
    # only behind apply=True + the triple push gate. SUPERADMIN inline.
    {
        "method": "POST",
        "path": "/api/v1/admin/online-store/register-webhooks",
        "allowed": ["SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/products",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/products/bulk-import",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/products/bulk-import/{job_id}/file",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/products/{product_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {"method": "POST", "path": "/api/v1/admin/seed-database", "allowed": "PUBLIC"},
    {"method": "GET", "path": "/api/v1/admin/system-health", "allowed": ["ADMIN"]},
    {"method": "GET", "path": "/api/v1/admin/owner-digest", "allowed": ["ADMIN"]},
    {
        "method": "GET",
        "path": "/api/v1/admin/system/audit-logs",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/system/backups",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/system/backups",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/system/backups/{backup_id}/restore",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/system/export/{export_type}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/system/settings",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/admin/system/settings",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/system/status",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/admin/techcherry/import",
        "allowed": ["SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/admin/techcherry/status",
        "allowed": ["SUPERADMIN"],
    },
]

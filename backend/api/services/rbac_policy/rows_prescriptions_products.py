"""
POLICY rows: /portal, /prescriptions, /labels, /print, /print-overrides, /product-templates, /products, product-master.

Moved VERBATIM from the flat ``api/services/rbac_policy.py`` lines 5345-5696.
These rows are load-bearing DATA. Their ORDER is preserved (``policy_for``
prefers the most specific match, and a row under a module widens that
module's capability grant-union). Do not reorder, reword or re-group them;
a new route needs a new row here or CI fails the coverage lock.
"""

from __future__ import annotations

from typing import Dict, List

ROWS: List[Dict[str, object]] = [
    # --- /api/v1/portal ---
    {"method": "GET", "path": "/api/v1/portal/rx", "allowed": "PUBLIC"},
    {"method": "POST", "path": "/api/v1/portal/rx/request-otp", "allowed": "PUBLIC"},
    {"method": "POST", "path": "/api/v1/portal/rx/verify-otp", "allowed": "PUBLIC"},
    {"method": "GET", "path": "/api/v1/portal/track/{token}", "allowed": "PUBLIC"},
    # --- /api/v1/prescriptions ---
    {"method": "GET", "path": "/api/v1/prescriptions", "allowed": "AUTHENTICATED"},
    {
        "method": "POST",
        "path": "/api/v1/prescriptions",
        "allowed": ["ADMIN", "OPTOMETRIST", "STORE_MANAGER", "SUPERADMIN"],
        "self_enforced": True,
    },
    {"method": "GET", "path": "/api/v1/prescriptions/", "allowed": "AUTHENTICATED"},
    {
        "method": "POST",
        "path": "/api/v1/prescriptions/",
        "allowed": ["ADMIN", "OPTOMETRIST", "STORE_MANAGER", "SUPERADMIN"],
        "self_enforced": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/prescriptions/customer/{customer_id}/progression",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/prescriptions/expiring",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/prescriptions/optometrist/{optometrist_id}/stats",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/prescriptions/patient/{patient_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/prescriptions/patient/{patient_id}/latest",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/prescriptions/patient/{patient_id}/valid",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/prescriptions/family/{customer_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/prescriptions/{prescription_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/prescriptions/{prescription_id}/photo",
        "allowed": [
            "ADMIN", "CASHIER", "OPTOMETRIST", "SALES_CASHIER",
            "SALES_STAFF", "STORE_MANAGER", "SUPERADMIN",
        ],
        "self_enforced": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/prescriptions/{prescription_id}/photo",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "PUT",
        "path": "/api/v1/prescriptions/{prescription_id}",
        "allowed": ["ADMIN", "OPTOMETRIST", "STORE_MANAGER", "SUPERADMIN"],
        "self_enforced": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/prescriptions/{prescription_id}/finalize",
        "allowed": ["ADMIN", "OPTOMETRIST", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/prescriptions/{prescription_id}/print",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/prescriptions/{prescription_id}/validate",
        "allowed": "AUTHENTICATED",
    },
    # Version PATCH writes clinical Rx data -> same gate as PUT /{id}
    # (update_prescription); self_enforced because the route raises the
    # body-specific clinical 403 the enforcer must not override.
    {
        "method": "PATCH",
        "path": "/api/v1/prescriptions/{prescription_id}/version/{version_name}",
        "allowed": ["ADMIN", "OPTOMETRIST", "STORE_MANAGER", "SUPERADMIN"],
        "self_enforced": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/prescriptions/{prescription_id}/versions",
        "allowed": "AUTHENTICATED",
    },
    # --- /api/v1/labels (F21 quarantine sticker) ---
    {
        "method": "POST",
        "path": "/api/v1/labels/quarantine/{stock_id}",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    # --- /api/v1/print ---
    {"method": "GET", "path": "/api/v1/print/qz/cert", "allowed": "AUTHENTICATED"},
    {"method": "POST", "path": "/api/v1/print/qz/sign", "allowed": "AUTHENTICATED"},
    # Delivery-challan HTML render: POS-capable roles + ACCOUNTANT (read-only,
    # store-scoped via validate_store_access / transfer access guard).
    {
        "method": "GET",
        "path": "/api/v1/print/delivery-challan/order/{order_id}",
        "allowed": [
            "SUPERADMIN",
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "SALES_CASHIER",
            "SALES_STAFF",
            "ACCOUNTANT",
        ],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/print/delivery-challan/transfer/{transfer_id}",
        "allowed": [
            "SUPERADMIN",
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "SALES_CASHIER",
            "SALES_STAFF",
            "ACCOUNTANT",
        ],
        "store_scoped": True,
    },
    # --- /api/v1/print-overrides ---
    {"method": "GET", "path": "/api/v1/print-overrides", "allowed": "AUTHENTICATED"},
    {
        "method": "GET",
        "path": "/api/v1/print-overrides/_meta/templates",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "DELETE",
        "path": "/api/v1/print-overrides/{entity_id}/{template_key}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/print-overrides/{entity_id}/{template_key}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "PUT",
        "path": "/api/v1/print-overrides/{entity_id}/{template_key}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    # --- /api/v1/product-templates ---
    {
        "method": "GET",
        "path": "/api/v1/product-templates",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/product-templates",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/product-templates/",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/product-templates/",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/product-templates/{template_id}",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
    # --- /api/v1/products ---
    {"method": "GET", "path": "/api/v1/products", "allowed": "AUTHENTICATED"},
    {
        "method": "POST",
        "path": "/api/v1/products",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/products/brands/list",
        "allowed": "AUTHENTICATED",
    },
    # AI description draft for the Add-Product form (catalog write roles;
    # SUPERADMIN auto-passes). Always 200 with a status field -- see products.py.
    {
        "method": "POST",
        "path": "/api/v1/products/generate-description",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/products/bulk-create",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
    {
        # Hub Phase 4 clone-and-vary -> N DRAFT variants.
        "method": "POST",
        "path": "/api/v1/products/clone-vary",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/products/bulk-offer",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/products/bulk-price",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/products/categories/list",
        "allowed": "AUTHENTICATED",
    },
    # Step-12: known product tags (filter + autocomplete). AUTHENTICATED, same as
    # the sibling brands/categories list endpoints.
    {
        "method": "GET",
        "path": "/api/v1/products/tags/list",
        "allowed": "AUTHENTICATED",
    },
    # Cataloguer attribution roster: distinct product creators + per-user
    # created counts (drives the Inventory "Catalogued by" filter and the
    # owner's cataloguing-performance view). Manager ladder only -- regular
    # staff don't need the roster; SUPERADMIN auto-passes.
    {
        "method": "GET",
        "path": "/api/v1/products/cataloguers",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER", "CATALOG_MANAGER"],
    },
    # Cataloguing performance scorecard (attribution phase 2): per-user volume,
    # approvals, corrections received, QC error rate. Same manager ladder.
    {
        "method": "GET",
        "path": "/api/v1/products/cataloguing-scorecard",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER", "CATALOG_MANAGER"],
    },
    # QC sampling workflow (attribution phase 2): draw a random per-cataloguer
    # sample batch, list items, and record OK/ERROR verdicts (no self-QC;
    # verdicts immutable except an ADMIN/SUPERADMIN overwrite). Manager ladder.
    {
        "method": "POST",
        "path": "/api/v1/products/qc-samples/generate",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER", "CATALOG_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/products/qc-samples",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER", "CATALOG_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/products/qc-samples/{item_id}/verdict",
        "allowed": ["ADMIN", "AREA_MANAGER", "STORE_MANAGER", "CATALOG_MANAGER"],
    },
    # --- PM (N5) unified product-master sub-paths (router product_master.py) ---
    {
        "method": "GET",
        "path": "/api/v1/products/master/categories",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/products/master/categories/{category}/fields",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/products/sku-preview",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/products/master",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/products/master/{product_id}",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
    {"method": "GET", "path": "/api/v1/products/gst-rates", "allowed": "AUTHENTICATED"},
    # Product-image upload/serve (GridFS-backed). Upload is a catalog-mutation
    # (write) gated to the catalog roles; the serve endpoint is PUBLIC because
    # the returned URL is embedded in <img> tags that carry no auth header and
    # product photos are non-sensitive catalog media.
    {
        "method": "POST",
        "path": "/api/v1/products/image",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
    # Image RE-HOST: server-side copies an external (brand-site)
    # image into our GridFS so products never hotlink. Same catalog write gate
    # as the multipart upload; the fetch itself is SSRF-hardened in
    # services/image_rehost.py (private/loopback/metadata ranges blocked).
    {
        "method": "POST",
        "path": "/api/v1/products/image/from-url",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
    # Background-removal edit: re-runs the DETERMINISTIC cut-out pipeline
    # (Photoroom) on a previously-uploaded product image and persists the
    # cleaned result as a NEW image. Same catalog-write gate as the upload.
    {
        "method": "POST",
        "path": "/api/v1/products/image/{file_id}/edit",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/products/image/{file_id}",
        "allowed": "PUBLIC",
    },
    {"method": "GET", "path": "/api/v1/products/sku/{sku}", "allowed": "AUTHENTICATED"},
    {
        "method": "GET",
        "path": "/api/v1/products/{product_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "PUT",
        "path": "/api/v1/products/{product_id}",
        "allowed": ["ADMIN", "CATALOG_MANAGER"],
    },
]

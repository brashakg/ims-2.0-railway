"""
POLICY rows: /online-store family, /collections, /catalogue, /ondc.

Moved VERBATIM from the flat ``api/services/rbac_policy.py`` lines 4521-5012.
These rows are load-bearing DATA. Their ORDER is preserved (``policy_for``
prefers the most specific match, and a row under a module widens that
module's capability grant-union). Do not reorder, reword or re-group them;
a new route needs a new row here or CI fails the coverage lock.
"""

from __future__ import annotations

from typing import Dict, List

from ._core import AUTHENTICATED

ROWS: List[Dict[str, object]] = [
    # --- /api/v1/online-store ---  (BVI Phase 1: Online Store module skeleton)
    {
        "method": "GET",
        "path": "/api/v1/online-store/summary",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    # --- /api/v1/online-store/stock-tally ---  (BVI Phase 5: read-only reconcile)
    # Per online-listed SKU: online-listed vs on-hand vs reserved vs sellable +
    # an oversell-risk flag + a conservative buffer suggestion. STRICTLY read-only
    # (never mutates/reserves stock); the write-path allocation is a deferred
    # follow-up. Same ecom role set. See routers/online_store.py.
    {
        "method": "GET",
        "path": "/api/v1/online-store/stock-tally",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    # Store health readiness dashboard (BVI Phase 5 "Store health" card):
    # orphan SKUs, attribute coverage, barcode match + a composite readiness
    # score. Read-only + fail-soft; same ecom role set as the module summary.
    # See routers/online_store.py + services/store_health.py.
    {
        "method": "GET",
        "path": "/api/v1/online-store/store-health",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    # --- /api/v1/online-store/discount-rules ---  (rebuild of BVI DiscountRule).
    # Owner-editable CRUD for the automatic ONLINE storefront discount rules the
    # online discount engine reads. PUSH-DARK + ONLINE-only (never in-store POS).
    # Role-gated to SUPERADMIN/ADMIN/CATALOG_MANAGER (pricing, not a design-queue
    # concern -> DESIGN_MANAGER excluded). The literal /recompute row precedes the
    # /{rule_id} rows (policy_for prefers the exact-literal / fewest-params match).
    # See routers/online_store_discount_rules.py + services/online_discount_engine.py.
    {
        "method": "GET",
        "path": "/api/v1/online-store/discount-rules",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/online-store/discount-rules",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/online-store/discount-rules/recompute",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/online-store/discount-rules/{rule_id}",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/online-store/discount-rules/{rule_id}",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/online-store/discount-rules/{rule_id}",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "SUPERADMIN"],
    },
    # --- /api/v1/collections ---  (unification step-13: materialised collection
    # BROWSE). Read-only, fast-path over the collection_products materialised
    # view. AUTHENTICATED -- same posture as GET /products + GET /catalog/products
    # (an internal-app catalogue browse, not the role-gated admin editor under
    # /online-store/collections). See routers/collections_browse.py.
    {"method": "GET", "path": "/api/v1/collections", "allowed": "AUTHENTICATED"},
    {
        "method": "GET",
        "path": "/api/v1/collections/{handle}/products",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/collections/{handle}/refresh",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    # --- /api/v1/collections insights ---  (Collections Phase 1, Track 2:
    # read-only analytics over the materialised collection_products view +
    # stock_units + orders, plus an unsaved-rules preview). Router-level
    # require_roles with this exact set; non-HQ callers (STORE_MANAGER here)
    # are additionally FORCED to their own active_store_id inside the handlers
    # (store_scoped). See routers/collections_insights.py.
    {
        "method": "GET",
        "path": "/api/v1/collections/insights/summary",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "CATALOG_MANAGER",
            "SUPERADMIN",
        ],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/collections/preview",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "CATALOG_MANAGER",
            "SUPERADMIN",
        ],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/collections/{collection_id}/insights",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "CATALOG_MANAGER",
            "SUPERADMIN",
        ],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/collections/{collection_id}/insights/stores",
        "allowed": [
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "CATALOG_MANAGER",
            "SUPERADMIN",
        ],
        "store_scoped": True,
    },
    # --- /api/v1/online-store/collections ---  (BVI Phase 2: Collections, FLAGSHIP #1)
    # PUSH-DARK ecom_collections CRUD + manual/smart membership + smart-rule
    # resolver. All gated to the ecom role set (router-level require_roles); see
    # routers/online_store_collections.py + BVI_MERGE_PLAN.md Phase 2.
    {
        "method": "GET",
        "path": "/api/v1/online-store/collections",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/online-store/collections",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/online-store/collections/{collection_id}",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/online-store/collections/{collection_id}",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/online-store/collections/{collection_id}",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/online-store/collections/{collection_id}/products",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/online-store/collections/{collection_id}/products",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/online-store/collections/{collection_id}/products/{sku}",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/online-store/collections/{collection_id}/products/reorder",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/online-store/collections/{collection_id}/resolved-products",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    # SUPERADMIN "block a collection from online sale" (BVI-retirement). Some
    # brands contractually forbid online sale; the owner (SUPERADMIN) flags a
    # collection so every product in it is excluded from Shopify (never pushed;
    # delisted if already synced). NARROWED to SUPERADMIN ONLY (the owner asked
    # for the SUPERADMIN right) -- the broader ecom set that runs the rest of the
    # collections router is NOT admitted here. The literal .../block + .../unblock
    # suffixes each carry one param + a literal segment, so policy_for resolves
    # them ahead of the bare .../{collection_id} (different segment count anyway).
    # See routers/online_store_collections.py + services/online_block.py.
    {
        "method": "POST",
        "path": "/api/v1/online-store/collections/{collection_id}/block",
        "allowed": ["SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/online-store/collections/{collection_id}/unblock",
        "allowed": ["SUPERADMIN"],
    },
    # --- /api/v1/online-store/menus ---  (BVI Phase 3: Menus / Mega-menu, FLAGSHIP #2)
    # PUSH-DARK ecom_menus CRUD + an embedded recursive item-tree editor
    # (add/move/remove/reorder/patch nodes). All gated to the ecom role set
    # (router-level require_roles); see routers/online_store_menus.py +
    # BVI_MERGE_PLAN.md Phase 3. The literal .../items/reorder + .../items/{item_id}/move
    # routes are more specific than .../items/{item_id} (policy_for ranks fewest-params
    # first), so they resolve correctly.
    {
        "method": "GET",
        "path": "/api/v1/online-store/menus",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/online-store/menus",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/online-store/menus/{menu_id}",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/online-store/menus/{menu_id}",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/online-store/menus/{menu_id}",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/online-store/menus/{menu_id}/items",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/online-store/menus/{menu_id}/items/reorder",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/online-store/menus/{menu_id}/items/{item_id}/move",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/online-store/menus/{menu_id}/items/{item_id}",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/online-store/menus/{menu_id}/items/{item_id}",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    # --- /api/v1/online-store/images ---  (BVI Phase 4: Image Design Queue, FLAGSHIP #3)
    # PUSH-DARK product_images CRUD + the RAW->EDITED->APPROVED design lifecycle
    # (assign / status / attach-edited). All gated to the ecom role set
    # (router-level require_roles); see routers/online_store_images.py +
    # BVI_MERGE_PLAN.md Phase 4. The literal action sub-paths .../{image_id}/assign,
    # .../{image_id}/status, .../{image_id}/edited are more specific than the bare
    # .../{image_id} route (policy_for ranks fewest-params then longest first), so
    # they resolve to their own entries. APPROVE writes a chained audit_logs row.
    {
        "method": "GET",
        "path": "/api/v1/online-store/images",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/online-store/images",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        # Phase 4a: durable multipart image UPLOAD -> object_storage (S3 seam,
        # fail-soft to local in dev). Literal /upload out-ranks the {image_id}
        # param route in the policy matcher. Audit-logged action IMAGE_UPLOAD.
        "method": "POST",
        "path": "/api/v1/online-store/images/upload",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/online-store/images/{image_id}",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/online-store/images/{image_id}",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/online-store/images/{image_id}",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/online-store/images/{image_id}/assign",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/online-store/images/{image_id}/status",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/online-store/images/{image_id}/edited",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/online-store/images/{image_id}/auto-edit",
        "allowed": ["ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"],
    },
    # --- /api/v1/online-store/push ---  (BVI Phase 5: IMS -> Shopify GraphQL PUSH)
    # The IMS->Shopify push for product/collection/menu/image + a status surface.
    # BUILT DARK: every push is SIMULATED (dry-run, no network) unless
    # IMS_SHOPIFY_WRITES on AND DISPATCH_MODE=live AND creds present (per #262 BVI
    # is the single Shopify writer until the Phase-6 cutover). UNLIKE the rest of
    # the Online Store module this surface is NARROWED to SUPERADMIN/ADMIN ONLY
    # (integration-critical -- pushing to the live storefront). Each push writes a
    # chained audit_logs row. See routers/online_store_push.py + BVI_MERGE_PLAN.md
    # Phase 5. The literal /status route is more specific than the /{entity}/{id}
    # POST routes and resolves on its own; the four POST routes carry one param each.
    {
        "method": "GET",
        "path": "/api/v1/online-store/push/status",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    # Read-only push HISTORY (OS-047): surfaces the chained ONLINE_STORE_PUSH
    # audit ledger on the sync page. Same {ADMIN, SUPERADMIN} set as every other
    # row in this push family, so the module's grant-union is UNCHANGED (no
    # capability broadening -- see the rbac capability-union gotcha).
    {
        "method": "GET",
        "path": "/api/v1/online-store/push/history",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/online-store/push/product/{product_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    # TAKE-DOWN (owner ruling 2026-08-25): pull ONE product off the live
    # storefront. Same {ADMIN, SUPERADMIN} set as every other row in this push
    # family, so the module grant-union is UNCHANGED (no capability broadening
    # -- see the rbac capability-union gotcha). The literal /take-down suffix
    # is more specific than the /product/{product_id} POST above and resolves
    # on its own.
    {
        "method": "POST",
        "path": "/api/v1/online-store/push/product/{product_id}/take-down",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/online-store/push/collection/{collection_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/online-store/push/menu/{menu_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/online-store/push/image/{image_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/online-store/push/all-pending",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    # --- /api/v1/catalogue ---  (Share collection as PDF + temp collections)
    # Catalogue sharing is a broad staff activity (anyone helping a customer),
    # so these are AUTHENTICATED -- the same posture as the internal catalogue
    # browse (GET /products, GET /collections). Build a branded PDF from a
    # collection OR a hand-picked product_ids list, and save a hand-picked
    # selection as a TEMPORARY, auto-expiring internal sharing set (never pushed
    # to Shopify). The literal /temp-collections out-ranks the {collection_id}
    # param route in the matcher. See routers/catalogue_pdf.py.
    {"method": "POST", "path": "/api/v1/catalogue/pdf", "allowed": AUTHENTICATED},
    {
        "method": "POST",
        "path": "/api/v1/catalogue/temp-collections",
        "allowed": AUTHENTICATED,
    },
    {
        "method": "GET",
        "path": "/api/v1/catalogue/temp-collections",
        "allowed": AUTHENTICATED,
    },
    {
        "method": "DELETE",
        "path": "/api/v1/catalogue/temp-collections/{collection_id}",
        "allowed": AUTHENTICATED,
    },
    # --- /api/v1/online-store/orders ---  (BVI Phase 3b: online sales into IMS books)
    # The read + recovery surface over the canonical IMS orders that
    # online_order_mapper creates from Shopify orders (channel='ONLINE', GST invoice
    # minted, counted once by Finance/P&L). GET list is also for the ACCOUNTANT (it
    # reads the books); POST remap MUTATES/re-creates an order so it is narrowed to
    # SUPERADMIN/ADMIN. The router mounts the list at both ''/'/' so both concrete
    # paths are catalogued. A remap writes a chained audit_logs row. See
    # routers/online_store_orders.py + BVI_MERGE_PLAN.md Phase 3.
    {
        "method": "GET",
        "path": "/api/v1/online-store/orders",
        "allowed": ["ADMIN", "ACCOUNTANT", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/online-store/orders/",
        "allowed": ["ADMIN", "ACCOUNTANT", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/online-store/orders/remap/{shopify_order_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    # Release the clinical Rx FLAG-AND-HOLD on one online order after the
    # prescription is captured (OS-012: the hold was write-only). SAME allowed
    # set as remap above -- deliberately NOT a wider set, so this row does not
    # broaden the module's write grant-union (rbac capability-union gotcha).
    # Capability layer (PR #947 follow-up 1): this clinical route is carved out
    # to a dedicated capability key `online-store:rx-clear` (services/
    # capabilities.capability_for) so it never rides the shared online-store:write
    # key; the allowed set is unchanged, so no grant-union is broadened. A module
    # ('ecommerce') deny still covers it via MODULE_EXTRA_DENY_CAPABILITIES.
    {
        "method": "POST",
        "path": "/api/v1/online-store/orders/{order_id}/clear-rx-hold",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    # --- /api/v1/online-store/refund-reviews ---  (Shopify refund -> GST consumer)
    # The ACCOUNTANT-facing consumer for shopify_refund_review -- the queue the
    # refunds/create handler writes to by DEFAULT (SHOPIFY_REFUND_AUTO off). The
    # books are the accountant's, so ACCOUNTANT is first-class (GET list + POST
    # confirm/reject); ADMIN is HQ; SUPERADMIN auto. Confirm posts the credit note
    # + restock from the stored row (reuses _issue_store_credit + _restock_good_items).
    # Non-HQ callers are FORCED to their own store scope. See
    # routers/online_store_refund_reviews.py.
    {
        "method": "GET",
        "path": "/api/v1/online-store/refund-reviews",
        "allowed": ["ADMIN", "ACCOUNTANT", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/online-store/refund-reviews/",
        "allowed": ["ADMIN", "ACCOUNTANT", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/online-store/refund-reviews/{review_id}/confirm",
        "allowed": ["ADMIN", "ACCOUNTANT", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/online-store/refund-reviews/{review_id}/reject",
        "allowed": ["ADMIN", "ACCOUNTANT", "SUPERADMIN"],
    },
    # --- /api/v1/ondc ---  (BVI-20: ONDC Seller Node scaffolding -- DARK default)
    # Callback endpoints are PUBLIC (Beckn protocol; SNP signature-gated when
    # config.ukp is set). Admin routes require SUPERADMIN / ADMIN.
    # See backend/api/services/ondc_seller.py + backend/api/routers/ondc.py.
    {"method": "POST", "path": "/api/v1/ondc/on_search", "allowed": "PUBLIC"},
    {"method": "POST", "path": "/api/v1/ondc/on_select", "allowed": "PUBLIC"},
    {"method": "POST", "path": "/api/v1/ondc/on_init", "allowed": "PUBLIC"},
    {"method": "POST", "path": "/api/v1/ondc/on_confirm", "allowed": "PUBLIC"},
    {"method": "POST", "path": "/api/v1/ondc/on_status", "allowed": "PUBLIC"},
    {"method": "POST", "path": "/api/v1/ondc/on_cancel", "allowed": "PUBLIC"},
    {
        "method": "GET",
        "path": "/api/v1/ondc/status",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/ondc/publish",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
]

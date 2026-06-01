# DECISION PLAN: Fully Rebuild BVI into IMS as ONE App

**Council Chair synthesis of three lenses (architecture Â· migration Â· product).** All three lenses independently converged on the same core ruling, with high confidence and code-verified facts. Where they agreed, I lock it. Where they diverged, I rule below.

---

## CHAIR'S RULINGS ON THE THREE DIVERGENCES

**Ruling 1 â€” Endorse the owner's full-rebuild directive, but sequence it strangler-fig (unanimous across lenses).** The destination (IMS = single source of truth, BVI Postgres + Next.js retired, IMS = sole Shopify writer) is correct. A big-bang Postgresâ†’Mongo port on a live revenue storefront, owned by a non-developer, is uninsurable. Every phase ships behind a flag and is independently revertible. The end state fully satisfies the directive; the *path* is incremental.

**Ruling 2 â€” On the product lens's contrarian flag ("re-confirm killing Postgres").** I partially uphold it as a *sequencing* safeguard, not a reason to stop. Phases 1â€“5 (build the differentiators in Mongo) deliver ~90% of the value and are where the work concentrates. Phases 6â€“7 (the actual writer-cutover + Postgres deletion) are a **separable go/no-go gate** the owner approves *after* seeing the rebuilt module work. This does not dilute the directive â€” it means we do not let "delete the database" block shipping, and the owner pulls the final trigger with eyes open. **The plan proceeds toward full retirement.**

**Ruling 3 â€” Variant model: BRIDGE, do not unify the quantity model (unanimous, this is the technical crux).** BVI `VariantLocation.quantity` is an aggregate count; IMS `stock_units` is one serialized row per physical unit. Unify the **product/variant identity** (PIM) into Mongo; keep **physical on-hand** as serialized `stock_units` (IMS is stronger here); the **online quantity pushed to Shopify is a DERIVED conservative slice** via the existing `stock_allocation.recommend_allocation`. The architecture lens's `parent_sku` sub-SKU approach and the product lens's `catalog_variants` collection are the same idea expressed two ways â€” I rule for an **explicit `catalog_variants` collection** (cleaner lineage, supports the color/size storefront PDP grouping, isolates Shopify variant IDs) over flattening variants into more `catalog_products` rows.

---

## A) TARGET ARCHITECTURE

### A.1 BVI Postgres model â†’ IMS Mongo mapping

| BVI Postgres model | IMS target | Verdict | Key notes |
|---|---|---|---|
| `Product` (parent) | `catalog_products` (+ `products`) + new `ecom` sub-doc | **REUSE + EXTEND** | Add embedded `ecom` object (Shopify/SEO/theme/rxable/design-status). Additive, fail-soft. |
| `ProductVariant` (color+size, qty/loc) | **NEW `catalog_variants`** (bridged to `stock_units`) | **BUILD (the crux)** | Variant = identity + Shopify-mapping layer. On-hand = roll-up of `stock_units`. Online qty = derived allocation. |
| `ProductImage` (RAW/EDITED) | **NEW `product_images`** | **BUILD** | role (RAW/EDITED), originalUrl, position, shopifyMediaId, imageDesignStatus. **Re-host `/uploads/` assets (R3).** |
| `VariantImage` | merged into `product_images` (`variant_sku` discriminator) | **BUILD** | Same collection. |
| `Collection` (custom/smart, rules, SEO, banner, `autoSource`, `categoryAnchor`) | **NEW `ecom_collections`** | **BUILD (flagship #1)** | Richest new surface. Auto-collection lineage by brand/category/attribute. |
| `CollectionProduct` | embedded array (manual) + computed (smart) | **BUILD** | Manual = stored SKU array; SMART = rules JSON evaluated at push. |
| `Menu` / `MenuItem` (mega-menu) | **NEW `ecom_menus`** (items embedded as tree) | **BUILD (flagship #2)** | Mongo subtree fits nested MenuItem natively. |
| `AttributeType` / `AttributeOption` | **NEW `ecom_attributes`** | **BUILD** | Drives attribute editor + auto-collection lineage. Small, low-risk. |
| `DiscountRule` | `pricing_caps` + `services/role_caps.py` | **REUSE (reconcile)** | **Do NOT port a second discount engine.** Map BVI rules onto IMS canonical caps. |
| `Order` / `OrderLineItem` | `orders` (via `webhook_inbox` landing) | **REUSE + BUILD mapper** | IMS `ORDER_SCHEMA` canonical. **Gap: webhook inbox is generic â€” needs an online-orderâ†’IMS-order mapper (Phase 3).** |
| `Customer` | `customers` | **REUSE** | Join on phone/email; store `shopify_customer_id`. |
| `StockTransfer` / `StockTransferItem` | `stock_transfers` (`transfers.py`) | **REUSE** | IMS already has multi-store transfers. Drop BVI's. |
| `Location` | `stores` | **REUSE** | Add `shopify_location_id` to store doc. |
| `User` (+ DESIGN_MANAGER/CATALOG_MANAGER, enabledFeatures) | `users` + `rbac_policy.py` | **REUSE + EXTEND** | Add design-queue roles to 11-role matrix. SSO map already in `ecommerce_sso.py`. |
| `SyncLog` | reuse `sync_runs` / `agent_events` | **REUSE** | NEXUS already writes these; extend per-entity push outcomes. |
| `WebhookEvent` | `webhook_inbox` (`webhooks.py`) | **REUSE** | Already built. |
| `WebhookSubscription` | **NEW `ecom_webhook_subs`** (tiny) | **BUILD** | Needed once IMS registers its own webhooks. |
| `ActivityLog` | chained `audit_logs` | **REUSE** | IMS chained audit is superior; route ecom actions there. |

**Net new: 6 Mongo collections** â€” `catalog_variants`, `ecom_collections`, `ecom_menus`, `ecom_attributes`, `product_images`, `ecom_webhook_subs` â€” **plus** an `ecom` sub-doc on `catalog_products`. Everything orders/customers/inventory/transfers/audit/SSO/webhooks/discounts **reuses** existing IMS infrastructure.

### A.2 Unified product/variant model

**Product master** (`catalog_products`, extended):
```
catalog_products.ecom = {
  shopify_product_id, status (DRAFT|PUBLISHED|ARCHIVED),
  handle, theme_suffix, rxable,
  seo: { title, description, page_url, tags, html },
  category_specific: {â€¦},          // BVI Product.categorySpecific JSON
  image_design_status (PENDING_DESIGN|READY|null),
  last_pushed_at, locally_modified  // dirty flag â†’ push queue
}
```

**Variant tier** (`catalog_variants`, NEW â€” resolves the crux):
```
catalog_variants = {
  sku (unique), parent_product_id, parent_sku,
  option_color, option_size,
  shopify_variant_id, shopify_inventory_item_id, shopify_location_id,
  gtin/barcode,                    // BVI barcode â†’ Shopify inventoryItem.barcode
  // NO stored quantity. on_hand = COUNT(stock_units WHERE sku, store, AVAILABLE)
  // online_qty = stock_allocation.recommend_allocation(on_hand, buffer)
}
```

**The bridge invariants (locked):**
- `stock_units` remains the master physical on-hand (serialized, one row/unit). No `VariantLocation`-style qty mirror in Mongo.
- Online quantity to Shopify = `stock_allocation.recommend_allocation(on_hand âˆ’ safety_buffer)` â€” **already coded** in `stock_allocation.py` + `online_catalog.py`. Online listed-qty must NEVER exceed real on-hand, not even momentarily.
- **`storeBarcode` â†’ `stock_units.barcode` is the immutable physical join key** (already the reconcile key in `online_catalog.reconcile_store_barcodes`). BVI two-barcode model: `barcode` (GTIN) â†’ `catalog_variants.gtin`/Shopify `inventoryItem.barcode`; `storeBarcode` (never pushed) â†’ `stock_units.barcode`.

**Idempotent join keys (locked, never key on Mongo `_id`):** `sku` (primary) Â· `storeBarcode`â†’`stock_units.barcode` (physical) Â· `shopify_product_id`/`shopify_variant_id`/`shopify_inventory_item_id` (Shopify side) Â· phone/email (customers) Â· `shopify_order_id` (orders).

**Category enum mismatch (must resolve):** IMS uses `FRAME/SUNGLASS/OPTICAL_LENS/CONTACT_LENSâ€¦`; BVI uses `SPECTACLES/SUNGLASSES/SOLUTIONS/READING_GLASSES/WATCHESâ€¦`. Build **`services/ecom_category_map.py`** (IMS â†” BVI â†” Shopify productType). Feeds auto-collections.

### A.3 Shopify-sync service design (extend NEXUS, do NOT fork the writer)

The safety primitives **already exist and are code-verified**: `nexus_providers.ims_shopify_writes_enabled()` is the single-writer kill-switch (default **OFF**; `shopify_push_product` currently returns a `RETIRED` no-op), `sync_runs` is the run log, `webhooks.py` is the signed inbox, `stock_allocation.py` is the oversell math. We wire existing parts; we do not reinvent them.

- **The current IMS pusher is REST; BVI uses GraphQL Admin API.** Build **`backend/services/shopify_admin.py`** (GraphQL client, ported from BVI) + **`backend/agents/shopify_sync.py`**, and have NEXUS call it. Gated by `IMS_SHOPIFY_WRITES` + `DISPATCH_MODE=live`.
- **PUSH (IMSâ†’Shopify):** `productSet`/`productCreate` + `productVariantsBulkUpdate`; `collectionCreate`/`collectionUpdate` (+ `collectionAddProducts`, smart-rules); `menuUpdate` (mega-menu); `inventorySetQuantities` (allocation only). Driven by `â€¦ecom.locally_modified` / `ecom_collections.locally_modified` dirty flags â†’ a push queue (reuse `sync_runs`).
- **PULL (Shopifyâ†’IMS):** orders + customers + inventory levels via webhook (`webhooks.py /shopify` â†’ `webhook_inbox` â†’ NEXUS drain â†’ `orders`/`customers` upsert, `channel:"ONLINE"`, `source:"shopify"`), with an hourly reconcile sweep as backstop. Collections/menus are IMS-owned post-cutover â†’ pulled only once to seed.
- **Single-writer guarantee:** add a startup assertion + extend the `sync-health` (#421) surface to scream if two writers are detected (Shopify `updatedAt` drift vs last IMS push). Cutover = an **atomic baton pass** (Â§C).

---

## B) PHASED ROADMAP (each phase ships a WORKING increment; Shopify cutover is LAST)

### Phase 0 â€” Pre-flight, shadow harness & parity oracle *(days; no user-visible change)*
- **Deliverables:** (1) Stand up a **read-only Postgresâ†’Mongo shadow sync** (BVI stays master) so IMS Mongo continuously mirrors Product/Variant/Collection/Menu â€” every later phase is verified against live data before IMS writes anything. Widen the existing `online_catalog._connect()` psycopg bridge. (2) **Parity oracle script**: snapshot row-counts + checksums from BVI Postgres âŸ· Shopify (GraphQL) âŸ· IMS Mongo per subsystem. (3) **Audit `ProductImage.url`/`originalUrl` for local `/uploads/` paths** (R3 â€” these files are on BVI's disk, not in Postgres). (4) Add `tsc --noEmit` to ecommerce-CI (`next.config` has `ignoreBuildErrors:true` hiding errors).
- **Reuses:** `online_catalog.py` psycopg connection, `sync_runs`.
- **Acceptance:** shadow sync runs on a schedule with zero errors; parity oracle emits a per-subsystem diff report; image-URL audit lists every non-CDN asset. BVI fully untouched.

### Phase 1 â€” FOUNDATION: Online Store module skeleton + variant tier + Shopify-sync scaffold *(the foundation)*
- **Deliverables:**
  - **Nav + shell:** "Online Store" nav group in the IMS React shell (SSO already live via `ecommerce_sso.py`); add **DESIGN_MANAGER / CATALOG_MANAGER** to `rbac_policy.py` (11â†’ role matrix) + middleware.
  - **Core PIM model:** add `ecom` sub-doc + extend `catalog_products`; **create `catalog_variants` collection** (resolves the crux) with `parent_sku`/Shopify-ID/two-barcode fields; build **`services/ecom_category_map.py`**.
  - **Backfill join keys (read-only):** run `reconcile_store_barcodes(apply=False)` dry-run â†’ then `apply=True`; one-time read-only pull of BVI products/variants â†’ IMS master via the psycopg bridge, mapping Shopify columns. **No writes to Shopify.**
  - **Shopify-sync scaffold (dark):** stub `backend/services/shopify_admin.py` (GraphQL client) + `backend/agents/shopify_sync.py` wired into NEXUS, behind `IMS_SHOPIFY_WRITES` (stays **0**). Startup single-writer assertion.
- **Reuses:** `catalog_products`, `products`, `brands`/`categories`, `admin_catalog.py`, `rbac_policy.py`, `ecommerce_sso.py`, `stock_allocation.py`, `online_catalog.py`, NEXUS, `sync_runs`.
- **Acceptance:** Online Store nav renders for the new roles; every Shopify variant resolves to exactly one IMS product/variant via `storeBarcode` (`online_summary.variants_with_store_barcode` = 100% or every orphan explained â€” **exit gate**); `catalog_variants` populated; smoke import passes; `IMS_SHOPIFY_WRITES` confirmed 0. **Working increment:** the catalog is visible inside IMS.

### Phase 2 â€” FLAGSHIP #1: Collections module *(read/edit, push dark)*
- **Deliverables:** `ecom_collections` + `collection_products`; router `collections.py`; port custom/smart collections, smart-rules JSON, SEO/metafields, banners, **auto-collection lineage (`autoSource`/`categoryAnchor`)**. React Collections editor screen in the IMS shell. One-time seed-import of the Postgres-exclusive collection data.
- **Reuses:** `ecom_category_map.py`, `ecom_attributes` (built here or P1), `pricing_caps` (for any discount linkage), the dark `shopify_sync.py`.
- **Acceptance:** create/edit a smart + a manual collection in IMS; smart-rule preview resolves correct SKUs; parity oracle shows IMS collections == Shopify collections. **Push stays OFF â€” BVI still the writer. Working increment:** owner manages collections inside IMS.

### Phase 3 â€” FLAGSHIP #2: Menus + Mega-Menu editor + online-order ingestion mapper
- **Deliverables:** (1) `ecom_menus` (items embedded as a tree); `menus.py`; **mega-menu editor** (thumbnails, badges, pin-to-top) React screen. (2) **Online-order mapper (the confirmed gap):** extend `webhooks.py` drain so a Shopify `orders/create` maps into an IMS `orders` doc + `customers` upsert + decrements `stock_units` (closes the oversell loop), `channel:"ONLINE"`. Dual-run against BVI's existing `Order`/`Customer` mirror for parity.
- **Reuses:** `webhook_inbox`, NEXUS drain, `orders`/`customers` schemas, `stock_units`.
- **Acceptance:** edit the mega-menu in IMS (matches storefront structure); a test Shopify order lands as an IMS order with correct customer + stock decrement, parity-matched to BVI's mirror. Push still dark. **Working increment:** nav editor live + online sales flow into IMS books.

### Phase 4 â€” FLAGSHIP #3: Image Design Workflow + asset re-host *(highest daily-ops value)*
- **Deliverables:** `product_images` (RAWâ†’EDITED, role, position, shopifyMediaId, `imageDesignStatus`); design-queue endpoints + React design-queue screen; wire DESIGN_MANAGER/CATALOG_MANAGER permissions. **Critical: re-host every local `/uploads/` asset (audited in P0) to durable object storage (Railway volume/bucket or S3/Cloudinary) and rewrite URLs HERE â€” before BVI is ever touched (R3).**
- **Reuses:** `rbac_policy.py`, `file_store`/object storage, `audit_logs`.
- **Acceptance:** an image moves RAWâ†’EDITED through the queue with role-gating; **zero `ProductImage.url` still points at BVI local disk** (re-host audit = 0 remaining). **Working increment:** the design team works entirely inside IMS.

### Phase 5 â€” Parity screens + GraphQL push service (dark dry-run)
- **Deliverables:** (1) Thin screens: orphans report, stock-tally, store-health, attributes editor, stock-import â€” mostly wrappers over existing inventory/reconcile + `online_summary` + the variant tier. (2) **Complete `shopify_admin.py` GraphQL push** (product/collection/menu/inventory); dirty-flag push queue; **dry-run against a Shopify dev/draft**; byte-parity diff vs what BVI would push (SKU-by-SKU `productSet` payload compare). `IMS_SHOPIFY_WRITES` **stays 0**.
- **Reuses:** `online_catalog`, `stock_allocation`, `transfers.py`, `sync_runs`, NEXUS.
- **Acceptance:** every parity screen renders correct live data; the push dry-run produces Shopify payloads byte-parity-matching current Shopify product JSON for a sample set; **zero live writes**. **Working increment:** full feature parity with BVI achieved, push verified safe but not yet armed.

### Phase 6 â€” THE BATON CUTOVER *(highest blast radius; canary-gated; owner-triggered go/no-go per Ruling 2)*
- **Deliverables (in strict order):** (1) **Write-freeze BVI PIM** (read-only). (2) Final Postgres delta-sync into Mongo; per-table row-count parity gate (Menu/MenuItem, Collection metafields, ProductImage RAW/EDITED, VariantLocation, StockTransfer, `ProductVariant.storeBarcode`). (3) **100-SKU canary:** IMS pushes 100 SKUs while BVI still writes the rest; verify in Shopify admin â€” no drift, no double-write, idempotency keyed on Shopify GIDs. (4) **The atomic flip:** `IMS_SHOPIFY_WRITES=1` **AND** BVI writer OFF **in one change** â€” never both on, never both off. Point Shopify webhooks at IMS. (5) Watch `webhook_inbox` + `sync-health` + Shopify for 48h.
- **Reuses:** the kill-switch, `sync-health` (#421), the dark push service from P5.
- **Acceptance:** canary 100 SKUs correct in Shopify with no drift; post-flip, exactly one writer detected by `sync-health`; 48h clean. **Working increment:** IMS is the live Shopify writer.

### Phase 7 â€” DECOMMISSION *(owner-gated; after 2-week clean bake)*
- **Deliverables:** repoint `uniparallel.com` â†’ IMS "Online Store" (or retire it; admin now lives in IMS), DNS TTL 300s; **owner deletes the `satisfied-adventure` Railway project + its Postgres**; archive a final `pg_dump`; remove the `ecommerce/` subtree + shadow-sync. Keep BVI Postgres read-only as rollback for the full 2 weeks first.
- **Acceptance:** storefront + admin fully on IMS for 2 weeks; final dump archived; Postgres + Next.js app deleted. **Directive fully satisfied.**

**Value-per-risk ordering rationale:** Phases 1â€“5 are pure PIM work in Mongo with **zero storefront blast radius** (push dark throughout) and deliver the three IMS-lacking flagships (Collections, Mega-Menu, Design Workflow) â€” ~90% of the value. The single highest-risk action (writer cutover) is isolated to Phase 6, canary-gated, atomic, and reversible. Postgres deletion (Phase 7) is the last, owner-approved step.

---

## C) DATA MIGRATION + CUTOVER

**The single most de-risking fact:** the storefront (`bettervision.in`) is served by **Shopify**, not Postgres. BVI Postgres is only the admin PIM. **A shopper cannot tell the PIM moved** â€” provided Shopify writes never stop and never double. The DB move is therefore invisible to customers.

**What lives ONLY in Postgres (the real migration payload, not recoverable from Shopify):** Menus/MenuItem, Collection metafields/banners/`autoSource`/`categoryAnchor` lineage, ProductImage RAW/EDITED design-queue state + `imageDesignStatus`, `storeBarcode`. Everything else (products, variants, published collections, orders, customers) can be **re-pulled from Shopify as a parity oracle**.

**Migration mechanics:**
1. **PG18 client** â€” server is **18.3**; a PG17 dump fails. Owner owns/runs this. Echo the target host before any `--clean`/`--if-exists` (a wrong-host `--clean` wipes it). Run via `railway run` so creds never surface; archive the dump.
2. **Strangler-fig dual-run:** Phase 0 shadow sync keeps Mongo mirroring Postgres continuously; each subsystem migrates ONCE, dual-run-verified by the parity oracle before IMS writes.
3. **Parity checks (exit gates):** per-subsystem row-count + checksum (Postgres âŸ· Shopify âŸ· Mongo); Phase 1 gate = 100% variant-match via `storeBarcode` (`online_summary.variants_with_store_barcode`); Phase 6 gate = per-table row-count parity on the Postgres-exclusive payload + canary 100-SKU Shopify verification.
4. **Single-writer handover (the non-negotiable invariant):** **exactly one Shopify writer at all times** â€” never zero (catalog goes stale), never two (last-write-wins corrupts the live store). The flip is one atomic change: `IMS_SHOPIFY_WRITES=1` â‡” BVI writer OFF. Kill-switch defaults OFF, so an accidental IMS deploy can't start writing. `sync-health` screams on dual-writer detection.
5. **Repoint `uniparallel.com`** (Phase 7): DNS TTL 300s; either redirect into IMS or retire (admin now inside IMS).
6. **Rollback (per phase):** every cutover is an **env-flag flip, not a redeploy**, with a one-line reverse. P6 rollback = flip `IMS_SHOPIFY_WRITES=0` + BVI writer back ON; BVI Postgres stays HOT (read-only) as the rollback target for 2 weeks; only Phase 7 deletes it.

---

## D) RISKS + DE-RISKING + OWNER ACTIONS

### Risks (ranked by blast radius)

- **R1 â€” Double-write to the live Shopify store (CRITICAL).** Two writers = last-write-wins corruption of `bettervision.in`. *De-risk:* single atomic flip (`IMS_SHOPIFY_WRITES` ON â‡” BVI OFF), never both; 100-SKU canary first; idempotency keyed on Shopify GIDs; kill-switch defaults OFF so an accidental deploy can't write; `sync-health` (#421) detects dual writers. **Never run the P5 live push before P6.**
- **R2 â€” Variant model mismatch (HIGH; the technical crux).** BVI qty-per-location vs IMS serialized `stock_units`. *De-risk:* BRIDGE not unify â€” `catalog_variants` is identity/Shopify-mapping only; `stock_units` stays on-hand truth; reconcile via `storeBarcode`/`stock_allocation`. Get it wrong â†’ inventory desyncs across 6 stores.
- **R3 â€” Local `/uploads/` images 404 after cutover (HIGH; customer-visible).** `ProductImage.url`/`originalUrl` on BVI's disk are **not in Postgres** â†’ broken product images on the live store. *De-risk:* audit in Phase 0; **re-host to durable storage + rewrite URLs in Phase 4**, before BVI is touched.
- **R4 â€” Write-freeze gap on the data move (HIGH; data-loss).** Edits in BVI between final dump and cutover are silently lost. *De-risk:* hard read-only freeze on BVI's PIM before the final delta-sync; per-table row-count parity on the Postgres-exclusive payload.
- **R5 â€” `storeBarcode` coverage gap (MEDIUM).** The whole bridge dies for any variant lacking a `storeBarcode`. *De-risk:* `online_summary.variants_with_store_barcode` is the **Phase-1 exit gate**; any uncovered variant = manual reconciliation before that SKU cuts over (match falls back to `barcode`/`sku`, but `storeBarcode` is the reliable physical key).
- **R6 â€” Oversell during the dual-channel window (MEDIUM; revenue).** Online listed-qty briefly disagreeing with physical on-hand strands an order. *De-risk:* `stock_allocation` conservative buffer (online < on-hand) stays in force throughout; one-way IMSâ†’Shopify for listed SKUs only; never let online qty exceed real on-hand even momentarily.
- **R7 â€” Discount/category double source of truth (MEDIUM).** *De-risk:* map BVI `DiscountRule` onto canonical `pricing_caps`; **delete BVI's rule engine â€” do not run two.** Single `ecom_category_map.py` for the enum mismatch.
- **R8 â€” PG18 client + wrong-host restore footgun (MEDIUM).** PG17 dump of an 18.3 server fails; a `--clean` at the wrong host wipes it. *De-risk:* confirm PG18 client; echo target host before any `--clean`; `railway run`; archive the dump.
- **R9 â€” Owner is non-developer + long effort (CROSS-CUTTING).** *De-risk:* every phase ships a **visible, usable screen** (Collections â†’ Mega-Menu â†’ Design-Queue) so progress is tangible; every cutover is an env-flag flip with a one-line revert; owner's only hands-on tasks are dashboard clicks with exact step-by-step.

### Owner actions (the ONLY hands-on tasks â€” all dashboard clicks / one-line paste)

1. **Phase 0:** confirm/run the **PG18 client install** for the dump (server is PG 18.3) â€” Claude provides the exact `railway run pg_dump â€¦` one-liner; owner pastes it.
2. **Phase 4:** approve a **durable image store** (Railway volume/bucket, or paste S3/Cloudinary creds into Railway as variable references) so `/uploads/` assets can be re-hosted.
3. **Phase 6 (go/no-go gate per Ruling 2):** after seeing the rebuilt module work, approve the cutover; then perform the **atomic baton flip** = set Railway env `IMS_SHOPIFY_WRITES=1` on IMS **and** disable BVI's writer (scale BVI to read-only) â€” Claude hands the exact two Railway toggles to do in one sitting.
4. **Phase 7:** repoint **`uniparallel.com` DNS** (GoDaddy, TTL 300s) per exact step-by-step; then **delete the `satisfied-adventure` Railway project + Postgres** (owner-only destructive action) once the 2-week bake is clean.

**Credentials/secrets:** never surfaced â€” all Postgres/Shopify ops run via `railway run` (injects creds into the subprocess); Shopify Admin token + any S3/Cloudinary creds live as Railway variable references; print env-var KEYS only, never values.

---

**THE ONE NON-NEGOTIABLE INVARIANT across every phase:** Shopify always has **exactly one writer**, and the conservative stock allocation (**online â‰¤ physical on-hand**) is never violated â€” not even for a minute. Everything else is reversible behind a flag; these two are not.

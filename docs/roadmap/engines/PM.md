I now have a complete grounding. There are two parallel product surfaces today: the canonical `products` collection (POS/billing reads, flat fields, `category` enum, GST from table, unique SKU index) and a second `catalog_products`/`catalog_variants` PIM lineage (BVI/Shopify, `attributes` dict, Shopify GIDs). The Excel spec asks for ONE category-conditional master with a strict SKU rule and the Shopify superset. Here is the buildable contract.

# Engine PM: Unified Product Master (foundation)

The system today has **two divergent product surfaces** that must be unified behind one engine:
1. `products` (canonical, POS/billing) — flat fields, `category` enum, unique `sku`, GST from `gst_rates` table. Written by `backend/api/routers/products.py` via `ProductRepository`.
2. `catalog_products` + `catalog_variants` (PIM/Shopify lineage) — `attributes` dict, Shopify GIDs, two-barcode model. Written by `backend/api/routers/catalog.py`.

The category→prefix→required-field spec (`No of fields required product category wise.xlsx`) and the 70-column Shopify PIM (`Shopify sheet...xlsx`) are the authoritative schema. **The engine is a thin service layer (`product_master`) that owns category schema, SKU minting, and validation; `products` stays the billing/stock spine; `catalog_products` becomes the editable PIM superset hung off it.** No collection is dropped.

## Reuse (existing files/functions to build on — real paths)

- `backend/api/routers/catalog.py` — `ProductCategory` enum (12 codes incl. SMTSG/SMTFR/SMTWT), `CATEGORY_FIELDS` (per-category required/optional/field-specs), `CATEGORY_NAMES`, `generate_sku()`, `_next_sku_counter()` (atomic `counters` collection `$inc`, multi-worker safe), `generate_product_title()`. **Extract these into the new engine module; keep router thin.**
- `backend/api/services/gst_rates.py` — `GST_CATEGORY_TABLE`, `gst_rate_for_category()`, `hsn_for_category()`, `resolve_gst_rate()` (DB-override layer). Engine calls these; never re-derives GST.
- `backend/api/services/pricing_caps.py` — `evaluate_offer_price()` (MRP≥offer guard), `CATEGORY_DISCOUNT_CAPS`. Engine reuses for the offer>MRP block + `discount_category` validation.
- `backend/api/services/barcode.py` — `validate_ean13()`, `allocate_sequence()`. Engine reuses for GTIN/barcode validation.
- `backend/database/repositories/product_repository.py` — `ProductRepository.find_by_sku/find_by_category/create`. Engine writes the billing spine through this.
- `backend/database/schemas.py` — `PRODUCT_SCHEMA` (line 92), `CATALOG_VARIANT_SCHEMA` (line 1453), `ECOM_SUBDOC_SCHEMA` (line 1495), `INDEXES["products"]` (line 651). Extend these; do not rewrite.
- `backend/api/routers/products.py` — `_build_product_data()`, `_validate_category_or_422()`, `_assert_mrp_ge_offer()`, `ProductCreate`/`ProductUpdate`. Refactor `create_product`/`update_product` to delegate to the engine.
- `backend/api/dependencies.py` — `get_product_repository()`. Add a sibling `get_product_master()`.
- Frontend: `frontend/src/pages/catalog/AddProductPage.tsx` (6-step wizard, `validateCurrentStep`), `ProductAddShell.tsx`, `frontend/src/constants/gst.ts`, `frontend/src/services/api/products.ts`. The category/field/SKU-preview endpoints already exist (`GET /catalog/categories/{category}/fields`); reuse them.

## Public API (functions and/or endpoints with signatures)

New service module `backend/api/services/product_master.py`:

```python
# --- Category schema (single source of truth, served to FE) ---
def category_spec(category: str) -> CategorySpec
    # -> {code, name, sku_prefix, required: [field], optional: [field], superset: [field]}
def all_category_specs() -> list[CategorySpec]
def required_fields(category: str) -> list[str]

# --- SKU rule: PREFIX + BRAND + MODEL + COLORCODE + SIZE ---
def build_sku(category: str, attributes: dict, db=None) -> str
    # deterministic concat per Excel rule; sanitises to [A-Z0-9/], uppercases,
    # appends atomic counter suffix ONLY on collision (see Open conflicts)
def parse_sku(sku: str) -> ParsedSku | None      # reverse for search/dedupe

# --- Validation (raises ProductMasterError -> mapped to HTTP) ---
def validate_attributes(category: str, attributes: dict) -> None
    # every required field present+non-empty; rejects unknown category
def normalise_payload(payload: ProductMasterCreate) -> dict
    # GST/HSN derived (gst_rates), offer<=MRP (pricing_caps), discount_category,
    # category-conditional coercion (e.g. CL expiry_date required), title built

# --- Persistence (writes BOTH spine + PIM atomically-ish, fail-soft) ---
def create_product(payload: ProductMasterCreate, actor: str) -> ProductMasterDoc
def update_product(product_id: str, patch: ProductMasterUpdate, actor: str) -> ProductMasterDoc
def get_product(product_id_or_sku: str) -> ProductMasterDoc | None
def list_products(filters: ProductFilter) -> Page[ProductMasterDoc]
```

New/refactored endpoints (router `backend/api/routers/product_master.py`, prefix `/api/v1/products` — **subsumes the legacy split**, with `/catalog/*` and `/products/*` kept as thin proxies for back-compat):

```
GET  /api/v1/products/categories                      -> all_category_specs()        [AUTHENTICATED]
GET  /api/v1/products/categories/{category}/fields    -> category_spec(category)      [AUTHENTICATED]
POST /api/v1/products/sku-preview                      {category, attributes} -> {sku} [CATALOG_ROLES]
POST /api/v1/products                                  ProductMasterCreate -> {product_id, sku} [CATALOG_ROLES]
PUT  /api/v1/products/{product_id}                     ProductMasterUpdate -> doc      [CATALOG_ROLES]
POST /api/v1/products/bulk-create                      [ProductMasterCreate] -> {created, errors[]} [ADMIN/CATALOG_MANAGER]
GET  /api/v1/products / /{id} / /sku/{sku}             (unchanged, served from engine)
```

`ProductMasterCreate` (Pydantic) merges today's `ProductCreate` (products.py) + `ProductCreateInput` (catalog.py): `{category, brand, model, color, size, attributes: dict, pricing: {mrp, offer_price, cost_price, discount_category}, hsn_code?, gst_rate?, weight?, images?, ecom?: EcomSubdoc, cl_*, sph/cyl/axis/add}`. `sku` is **optional on create** (engine mints it); accepted if supplied (legacy import).

## Data model (Mongo collection name + fields + indexes; mark new vs existing)

**`products`** (EXISTING — the billing/stock spine; engine writes here via `ProductRepository`):
- Existing fields per `PRODUCT_SCHEMA` (product_id, sku, category, brand, model, variant, color, size, material, gender, mrp/offer_price/cost_price decimal, hsn_code, gst_rate, attributes, all `cl_*`, discount_category, is_active, created_*).
- **NEW additive fields** (all optional, validator already allows `attributes`): `pim_product_id` (str, FK → `catalog_products.id`), `sku_prefix` (str), `country_of_origin` (str), `warranty_months` (int), `weight_grams` (double).
- Existing indexes (line 651): `sku` unique, `category`, `brand`, `is_active`, `(brand,category)`. **NEW indexes:** `{pim_product_id:1}` sparse; `{sku_prefix:1}`; `{(category:1, brand:1, model:1, color:1, size:1)}` (dedupe + stock-grid grouping).

**`catalog_products`** (EXISTING — promoted to editable PIM superset; currently schemaless):
- Existing: `id`, `sku`, `category`, `attributes`, `pricing`, `inventory`, `shopify`, `seo`, `images`, `is_active`.
- **NEW additive (Shopify-superset, all optional)** sourced from `Shopify sheet...xlsx` cols T–BH: `subbrand`, `label` (e.g. "Limited Edition"), `full_model_no`, `shape` (enum candidate, 22 values), `frame_color`, `temple_color`, `frame_material`, `temple_material`, `frame_type`, `product_usp_1/2`, `lens_usp`, `frame_size`, `bridge`, `temple_length`, `gender_label`, `country_of_origin`, `warranty`, `configurable`; sunglass-only: `lens_colour`, `tint`, `lens_material`, `polarization`, `uv_protection`; solutions-only: `recommended_for`, `instructions`, `ingredients`, `price_per_ml`. Embed via existing `ECOM_SUBDOC_SCHEMA.category_specific`. **NEW index:** `{id:1}` unique (currently only app-enforced).

**`catalog_variants`** (EXISTING, line 1453 — colour/size SKU children → stock join). Unchanged; engine ensures one variant row per minted SKU with `parent_product_id`/`parent_sku`.

**`counters`** (EXISTING) — `_id="sku:{prefix}"` atomic `$inc`. Reused unchanged.

**NEW collection `product_category_specs`** (optional, owner-overridable): per-category required/optional/superset field config so SUPERADMIN can add a category or mark a field required without a deploy. Falls back to the in-code `CATEGORY_FIELDS` (fail-soft, mirrors the `hsn_gst_master` override pattern). Indexes: `{code:1}` unique.

## How dependents call it (list the feature numbers/names that consume it and the exact call)

- **#6 Serial tracking** — at GRN, calls `product_master.get_product(sku)` to read the per-category `serialized` flag (HA always serial; opt-in per SKU per LOCKED serial decision); stock_units rows key off the minted SKU.
- **#10 Ageing/non-moving** — joins `reports/inventory/non-moving-stock` on `products.sku`; reads `category`+`sku_prefix` for category-bucketed ageing.
- **#36 Shopify sync** — NEXUS agent (`backend/agents/nexus_providers.py`) reads `catalog_products.ecom` superset (`shape`, `tags`, `seo`, `polarization`, `uv_protection`) for the storefront PDP; pushes per `catalog_variants` GTIN; `online_catalog.online_status_for_skus()` joins on SKU.
- **Stock grids (Power Grid / Rapid Grid)** — `POST /products/bulk-create` per row; group by `(category,brand,model,color,size)` index.
- **POS** (`backend/api/routers/orders.py`) — reads `products` spine for `gst_rate`/`hsn_code`/`offer_price`/`discount_category` at billing; **untouched** (no POS payment redesign per LOCKED).
- **Pricing engine** — `pricing_caps.evaluate_offer_price` already shared; engine is the single writer enforcing offer≤MRP and the future cost+10% floor (LOCKED price floor) at create/update.
- **Catalog Autopilot** (`catalog_autopilot.py`) and **AddProductPage** wizard — call `categories/{category}/fields` + `sku-preview` + `POST /products`.

## Integration points (agents, MSG91, Tally, RBAC, audit)

- **Agents:** NEXUS (Shopify push from PIM superset); SENTINEL can subscribe to a new `product.created` / `product.price_changed` event via `backend/agents/registry.py::dispatch_event` (fail-soft, in-process when no Redis).
- **MSG91:** none directly (no customer comms on product CRUD).
- **Tally:** none at master level; HSN/GST written here flow into the existing sales-JV (`payroll_exports`/finance) via order lines — ledger names unchanged (LOCKED E5).
- **RBAC:** all write endpoints gated through `require_roles(*_CATALOG_ROLES)` and registered in `backend/api/services/rbac_policy.py` so the `rbac_enforcement` middleware double-checks. Category-spec read = AUTHENTICATED.
- **Audit:** every create/update/delete writes an immutable `audit_log` entry (`{actor, action, product_id, before, after, ts}`) — Audit-Everything / Fail-Loudly. Price changes log old→new MRP/offer (mirrors `ProductRepository.update_price` `price_updated_by`).

## RBAC (who can do what)

| Action | Roles |
|---|---|
| Read categories/fields/SKU-preview | All authenticated |
| List/get products | All authenticated (store-scoped read) |
| Create / update product | SUPERADMIN, ADMIN, CATALOG_MANAGER |
| Bulk-create | SUPERADMIN, ADMIN, CATALOG_MANAGER |
| Edit category-spec (`product_category_specs`) | SUPERADMIN only |
| Soft-delete | SUPERADMIN, ADMIN |
| Edit PIM/Shopify superset (`ecom`) | SUPERADMIN, ADMIN, CATALOG_MANAGER |

Mirrors the existing role gates in `products.py` (`_CATALOG_ROLES`) and `catalog.py`. No store-staff/optometrist write access.

## Migration impact (schema/back-compat)

- **Additive only** — every new field is optional; `PRODUCT_SCHEMA` already permits free-form `attributes`, so no existing `products` doc fails validation. Add new fields + indexes to `schemas.py` `INDEXES`/`CATALOG_VARIANT_SCHEMA`; `database/migrations.py::_create_index` is idempotent.
- **Two-surface reconciliation (one-time backfill script `scripts/_backfill_product_master.py`, dry-run default):** (a) for each `products` row, create/link a `catalog_products` PIM doc + `catalog_variants` row (set `pim_product_id`/`parent_product_id`); (b) re-derive `sku_prefix` from `category`; (c) leave existing SKUs **as-is** (do not re-mint — POS/stock/Shopify reference them).
- **SKU rule:** new rule (`PREFIX+BRAND+MODEL+COLORCODE+SIZE`) applies to **new** products only; legacy `SG-BR-MODELCOL-1001` and Shopify-style `FRBURBERRYB31421109/7155` SKUs stay valid (SKUs can contain `/`, per Excel — validator must allow `/`).
- **Back-compat:** `/catalog/products` and `/products` endpoints keep working (proxy to engine); the in-memory `CATALOG_PRODUCTS` fallback stays for offline/test.
- **GST:** no rate changes — engine routes through the existing `gst_rates` table/override.

## Build effort (dev-days) + risk

- Engine module + category-spec extraction + SKU build/parse + validation: **2.5d**
- Endpoint refactor (`/products`, `/catalog` proxies, `sku-preview`) + RBAC registry rows: **1.5d**
- Schema/index additions + migration backfill script (dry-run + apply): **1.5d**
- PIM superset fields + `catalog_products` editable surface wiring: **1d**
- Frontend: point AddProductPage at engine endpoints, surface superset fields conditionally: **2d**
- Tests (intent-level) + audit wiring: **1.5d**
- **Total ≈ 10 dev-days.**

**Risk:** *Medium.* Highest risk = the two-surface backfill (must not re-mint live SKUs referenced by POS/stock/Shopify — guarded by leaving SKUs untouched + dry-run). Secondary = SKU collisions on the deterministic rule (mitigated by atomic counter suffix). Low risk on the additive schema changes. **POS billing path is read-only against `products` and is not modified.**

## Acceptance tests (intent-level, assert behavior not plumbing)

1. **Category-conditional required fields:** creating a Contact Lens without `expiry_date` is rejected; creating a Hearing Aid without `serial_no` is rejected; a Frame needs brand+model_no+colour_code; assert the 422 names the missing field. (Driven by spec table, not hardcoded per test.)
2. **SKU rule:** a Burberry sunglass with model `B 3142`, colour `1109/71` mints a SKU that starts with `SG`, contains the brand+model+colourcode, and preserves the `/`; minting the same product twice yields **distinct, unique** SKUs (counter suffix), and the `sku` unique index holds.
3. **One add-flow → both surfaces:** `POST /products` for a frame creates a `products` spine row AND a linked `catalog_products` PIM doc AND a `catalog_variants` row sharing the SKU; `GET /products/sku/{sku}` and the online-status join both resolve it.
4. **GST is derived, never guessed:** a frame persists at 5% (HSN 9003xx), a non-corrective sunglass at 18%, a contact lens at 5% — exactly what POS bills (assert master rate == `resolve_gst_rate` for the category).
5. **Pricing invariant:** offer_price > MRP is blocked on create AND on partial update (raising offer above existing MRP, or lowering MRP below existing offer). LUXURY `discount_category` cannot be silently downgraded to MASS.
6. **Superset round-trip for Shopify:** a product saved with `shape`, `polarization`, `uv_protection`, `tags`, SEO slug exposes those exact values to the NEXUS/online-catalog reader unchanged (HTML stripped on import).
7. **Back-compat:** an existing legacy `products` row (no PIM link, Shopify-style SKU) still lists, gets, and sells without modification; the backfill in dry-run reports the link it *would* create without writing.
8. **Audit:** every create/update/delete emits an immutable `audit_log` row with actor + before/after; a price change records old→new.

## Open conflicts / notes for the chair

1. **SKU determinism vs uniqueness.** Excel says SKU = pure concat (`FRBURBERRYB31421109/7155` — no counter). Current `generate_sku` appends a numeric counter. Two genuinely-different products can share brand/model/colour/size, so a pure concat collides. **Recommendation:** mint pure-concat first; append `-{counter}` **only on collision** (keeps the Excel form for the common case, guarantees uniqueness). Chair to confirm.
2. **Two collections or one?** This design keeps `products` (billing spine) + `catalog_products` (PIM) as **two linked collections** rather than collapsing into one mega-doc, because POS/stock/orders all key off `products.sku` and `catalog_variants` already exists with that lineage. Collapsing would be a large, POS-touching rewrite. Chair: confirm two-surface-with-link over single-collection.
3. **Category enum mismatch.** `products` enum uses long names (`FRAME`, `OPTICAL_LENS`, `WATCH`); `catalog.py` uses short codes (`FR`, `LS`, `WT`); `gst_rates` maps both. The engine must canonicalise to one set. **Recommendation:** store long name in `products.category` + short code in `sku_prefix`; engine translates. Confirm canonical set.
4. **Lens (`LS`) has no model/colour** — its identity is brand+index+coating+add-ons. SKU rule `PREFIX+BRAND+MODEL+COLOR+SIZE` doesn't fit lenses cleanly; for `LS` the engine should substitute `INDEX+COATING` for the MODEL/COLOR slots. Confirm.
5. **HEARING_AID GST = 0% (NIL)** in the table but devices vs parts differ; flagged in `gst_rates` already. No engine action, but note for go-live.
6. **`shape`/`frame_material`/`USP` enums are dirty in source** (typos: `AVAITOR`/`AVIATOR`, `STLISH`/`STYLISH`). Per LOCKED "every legacy colour-flag becomes an explicit status enum" — recommend the engine normalises to a clean enum with an alias map on import, but keep them free-text on the PIM superset to avoid blocking imports. Confirm enum-vs-freetext for these merch attributes.

Key files: `backend/api/services/product_master.py` (NEW), `backend/api/routers/product_master.py` (NEW), `backend/api/routers/catalog.py:200` (`CATEGORY_FIELDS` to extract), `backend/api/routers/products.py:287` (`ProductCreate` to merge), `backend/api/services/gst_rates.py:44`, `backend/api/services/pricing_caps.py`, `backend/database/schemas.py:92` (`PRODUCT_SCHEMA`) + `:1453` (`CATALOG_VARIANT_SCHEMA`) + `:651` (`INDEXES`), `backend/database/repositories/product_repository.py`, `frontend/src/pages/catalog/AddProductPage.tsx`.
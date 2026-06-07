Now I have everything I need. Let me write the full packet:

# Packet PM: Unified product master

## Current behavior (file:line)

**Two divergent product surfaces exist in parallel — neither is complete:**

**Surface 1 — `products` collection (billing/stock spine)**
- `backend/api/routers/products.py` owns `POST /api/v1/products` (single + bulk-create), `GET /api/v1/products`, `GET /api/v1/products/{id}`, `PUT /api/v1/products/{id}`, and bulk-pricing endpoints.
- `ProductCreate` (products.py:287) requires a caller-supplied `sku` — there is **no server-side SKU minting** on this path. The caller must pre-compute the SKU and pass it; duplicate SKU → HTTP 400.
- `_build_product_data` (products.py:243) handles `category`, `brand`, `model`, `color`, `size`, `mrp`, `offer_price`, `hsn_code`, `gst_rate`, additive CL/lens power fields — but **no `pim_product_id` link, no `sku_prefix`, no PIM write**.
- `$jsonSchema` validator on `products` (schemas.py:99-101) lists 12 categories: `FRAME`, `SUNGLASS`, `READING_GLASSES`, `OPTICAL_LENS`, `CONTACT_LENS`, `COLORED_CONTACT_LENS`, `WATCH`, `SMARTWATCH`, `SMARTGLASSES`, `WALL_CLOCK`, `ACCESSORIES`, `SERVICES`. **`HEARING_AID` is absent from the enum** despite being present in `gst_rates.py:64` and `catalog.py:174` (`ProductCategory.HEARING_AID = "HA"`). A hearing-aid product saved via `POST /products` with `category="HEARING_AID"` will fail Mongo validator or be stored inconsistently.
- `discount_category` enum in schemas.py:134 is `["MASS", "PREMIUM", "LUXURY", "NON_DISCOUNTABLE"]` — **`SERVICE` is missing** from the `$jsonSchema` despite being a valid cap tier in `pricing_caps.CATEGORY_DISCOUNT_CAPS` (pricing_caps.py:63) and being accepted/tested in `test_bvi_seam_fixes.py:73-74`.
- Indexes (schemas.py:651-657): `sku` unique, `category`, `brand`, `is_active`, `(brand,category)`. No `pim_product_id`, no `sku_prefix`, no `(category,brand,model,color,size)` dedupe index.

**Surface 2 — `catalog_products` + `catalog_variants` (PIM/Shopify lineage)**
- `backend/api/routers/catalog.py` owns `/api/v1/catalog/*`.
- `ProductCategory` enum (catalog.py:163-175): 12 short codes — `SG`, `CL`, `FR`, `ACC`, `LS`, `RG`, `WT`, `CK`, `HA`, `SMTSG`, `SMTFR`, `SMTWT`. **This is a completely different set of string values** from the `products` collection enum (`FRAME`, `SUNGLASS`, etc.). The two surfaces share no canonical category identifier — a product created via `/catalog/products` as category `FR` and one via `/products` as category `FRAME` are not cross-joined.
- `generate_sku` (catalog.py:1135-1155): mints `PREFIX-BRAND[:2]-MODEL[:4]COLOUR[:3]-COUNTER` (e.g. `FR-BU-B314110-1001`). This is **not** the Excel-spec rule (`PREFIX+BRAND+MODEL+COLORCODE+SIZE` producing `FRBURBERRYB31421109/7155`). The counter is always appended (even for the first product of a brand/model/colour), and the output is always truncated.
- `CATEGORY_FIELDS` (catalog.py:200-882): per-category required/optional field definitions exist for all 12 catalog categories. These are served by `GET /catalog/categories/{category}/fields`. However, these are **not enforced at create time** — `POST /catalog/products` passes `attributes: dict` through without validating that required fields are non-empty.
- `catalog_products` is **schemaless** in Mongo (no `$jsonSchema` validator) and currently has no unique index on `id`.
- `generate_sku` is called by the `/catalog` path only — the canonical `/products` path does **not** call it; callers must supply a SKU.
- No write path creates **both** a `products` spine row AND a `catalog_products` PIM doc in a single call. The two surfaces have no `pim_product_id` / `catalog_product_id` link in any real document.
- `ECOM_SUBDOC_SCHEMA` (schemas.py:1495) and `CATALOG_VARIANT_SCHEMA` (schemas.py:1453) are defined but `catalog_products` carries no Mongo-enforced schema.

**Frontend**
- `AddProductPage.tsx` (6-step wizard) calls `/catalog/categories/{category}/fields` for dynamic fields, then submits to `/catalog/products` (the PIM path). It does **not** call `POST /api/v1/products`. The canonical billing spine is not written by the wizard.
- GST defaults are auto-populated from frontend constants (`getGSTRateByCategory`, AddProductPage.tsx:87) — redundant with the server-side `gst_rates.py` table; drift risk.

---

## Intended behavior (full intent)

The Excel spec (`No of fields required product category wise.xlsx`, lines 801-837) defines a single unified product master with 12 categories, each with a strict SKU rule (`PREFIX+BRAND+MODEL+COLORCODE+SIZE` verbatim concatenation) and category-conditional required fields. Creating a product must:

1. **Validate category-conditional required fields** at the server at create/update time — not just on the frontend wizard. A Contact Lens without `expiry_date`, a Hearing Aid without `serial_no`, a Frame without `brand+model_no+colour_code` must be rejected with a 422 naming the missing field.
2. **Mint an SKU** server-side by deterministic concat per the Excel rule, allowing `/` and `-` in the output (e.g. `FRBURBERRYB31421109/7155`). Legacy SKUs already in the DB (Shopify-style or older format) stay as-is — the validator must be format-permissive (allow `/`, `-`).
3. **Triple-write atomically-ish — spine first, then PIM, then variant** (compensation on PIM/variant failure; spine write is the durable anchor): a single `POST /products` creates a `products` billing-spine row via `ProductRepository`, then creates/links a `catalog_products` PIM doc, then creates a `catalog_variants` row sharing the SKU. If PIM or variant write fails, it is retried or logged — but the spine succeeds and is not rolled back (no multi-document transactions on standalone Mongo).
4. **One canonical category set** shared by `products` and `catalog_products`. Resolution: the `products.category` field stores the long-form canonical enum value (`FRAME`, `SUNGLASS`, etc.); the `sku_prefix` field stores the short code (`FR`, `SG`, etc.); the engine translates between them. The `catalog.ProductCategory` short-code enum is retained only as the internal SKU-prefix registry — it is not the `products.category` value.
5. **`HEARING_AID` is a fully supported category**: added to the `products.$jsonSchema` category enum, GST 0% from `gst_rates.py:64`, serial-number required at create, forced `discount_category = NON_DISCOUNTABLE`.
6. **`SERVICE` is a valid `discount_category`**: added to the `products.$jsonSchema` `discount_category` enum to match `pricing_caps.CATEGORY_DISCOUNT_CAPS` (10% cap).
7. **GST/HSN is always derived server-side** from `gst_rates.gst_rate_for_category` / `hsn_for_category` unless explicitly overridden. Frontend never hard-codes rates.
8. **PIM superset attributes** (Shopify `shape`, `polarization`, `uv_protection`, `tags`, SEO slug, etc.) are stored on `catalog_products.ecom.category_specific` and round-trip cleanly to the NEXUS agent's online-catalog reader.
9. **Offer > MRP is blocked** at create AND on partial update (including raising offer above an existing MRP or lowering MRP below existing offer).
10. **Audit trail**: every create/update/delete writes an immutable `audit_log` row with actor, action, product_id, before/after snapshot, IST timestamp.
11. **Back-compat**: all existing `products` documents (no PIM link, legacy SKU format) continue to list, get, and sell without modification. The backfill script is dry-run by default.

---

## Delta to build

| # | What | Where | Status today |
|---|---|---|---|
| D1 | Add `HEARING_AID` to `PRODUCT_SCHEMA.category` enum in `schemas.py:99-101` and to `ORDER_SCHEMA.item_type` enum if needed | schemas.py:99 | MISSING — mongo validator rejects HA products |
| D2 | Add `SERVICE` to `PRODUCT_SCHEMA.discount_category` enum in `schemas.py:134` | schemas.py:134 | MISSING — valid at POS/pricing but rejected by Mongo schema |
| D3 | New service `backend/api/services/product_master.py`: canonical category map, SKU-mint (`build_sku` using Excel rule, format-permissive legacy guard), `validate_attributes`, `normalise_payload`, `create_product` (triple-write), `update_product`, `get_product`, `list_products` | NEW | Does not exist |
| D4 | Extract `CATEGORY_FIELDS` + `ProductCategory` enum from `catalog.py` into `product_master.py`; add `HEARING_AID` to `CATEGORY_FIELDS` with `serial_no` required | catalog.py:163-882 → product_master.py | Already coded in catalog.py but scattered; HA fields currently optional |
| D5 | New thin router `backend/api/routers/product_master.py`: `POST /api/v1/products/sku-preview`, refactor `POST /api/v1/products` to delegate to engine's `create_product` (triple-write), keep `/catalog/*` as a proxy | products.py, catalog.py | products.py create path only writes spine; no PIM write; no SKU minting |
| D6 | Schema/index additions: `{pim_product_id:1}` sparse, `{sku_prefix:1}`, `{(category,brand,model,color,size):1}` on `products`; `{id:1}` unique on `catalog_products`; idempotent via existing `_create_index` pattern | schemas.py:651 | Only 5 indexes exist on `products` today |
| D7 | Backfill script `scripts/_backfill_product_master.py` (dry-run default): for each `products` row, create/link `catalog_products` + `catalog_variants`, derive `sku_prefix`; leave existing SKUs as-is | NEW | Does not exist |
| D8 | Frontend: `AddProductPage.tsx` submit path posts to `POST /api/v1/products` (engine, triple-write) instead of `POST /api/v1/catalog/products`; surface HA `serial_no` as required field; restrained light UI | AddProductPage.tsx | Currently posts to `/catalog/products` only |
| D9 | Audit wiring: every engine create/update/delete writes `audit_log` row via `AuditRepository.create` | product_master.py | Not present on the catalog path today |
| D10 | RBAC registry rows for new product-master endpoints in `rbac_policy.py` | rbac_policy.py | Only old products.py entries registered |

**Not building (CORRECTIONS mandate):** no cross-collection atomic dual-write (P0-1), no new `money_accounts` SoR, no replica-set transactions. SKU rule is a **rewrite** of `generate_sku`, not a modification.

---

## Data model (collections/fields; new vs existing; migration)

### `products` (EXISTING — billing/stock spine)

**Schema changes — additive to `PRODUCT_SCHEMA` in schemas.py:**

| Field | Type | New/Existing | Notes |
|---|---|---|---|
| `pim_product_id` | string | **NEW** | FK to `catalog_products.id`; sparse optional |
| `sku_prefix` | string | **NEW** | Short code (`FR`, `SG`, etc.) derived at create; for stock-grid grouping |
| `country_of_origin` | string | **NEW** | From Shopify superset |
| `warranty_months` | int | **NEW** | From Shopify superset |
| `weight_grams` | double | **NEW** | Weight in grams (was `weight` float in ProductCreate) |

**Enum additions (breaking if not applied — validator rejects valid data):**
- `category` enum: add `HEARING_AID` alongside the existing 12 values
- `discount_category` enum: add `SERVICE` alongside the existing 4 values

**New indexes on `products` (all idempotent):**
- `{pim_product_id: 1}` sparse — foreign-key lookup
- `{sku_prefix: 1}` — category-grid grouping
- `{category: 1, brand: 1, model: 1, color: 1, size: 1}` — dedupe + stock-grid grouping

### `catalog_products` (EXISTING — PIM superset, currently schemaless)

**New additive fields** (all optional, stored in `ecom.category_specific` or top-level per existing pattern):
- Shopify superset: `subbrand`, `label`, `full_model_no`, `shape`, `frame_color`, `temple_color`, `frame_material`, `temple_material`, `frame_type`, `product_usp_1`, `product_usp_2`, `lens_usp`, `frame_size`, `bridge`, `temple_length`, `gender_label`, `country_of_origin`, `warranty`, `configurable`
- Sunglass-only: `lens_colour`, `tint`, `lens_material`, `polarization`, `uv_protection`
- Solutions-only: `recommended_for`, `instructions`, `ingredients`, `price_per_ml`

**New index:** `{id: 1}` unique (currently only app-enforced, not in Mongo)

### `catalog_variants` (EXISTING — unchanged schema)

Engine ensures one variant row per minted SKU with `parent_product_id` / `parent_sku`. No schema change; existing indexes retained.

### `counters` (EXISTING — unchanged)

Reused: `_id = "sku:{PREFIX}"`, `$inc seq`. Pattern already proven in `_next_sku_counter` (catalog.py:1101-1132).

### `product_category_specs` (NEW — optional override collection)

Per-category required/optional field config so SUPERADMIN can add a category or mark a field required without a deploy. Falls back to the in-code `CATEGORY_FIELDS` (fail-soft). Index: `{code: 1}` unique.

### Migration

**One-time backfill script `scripts/_backfill_product_master.py`** (dry-run default):
- For each `products` row: create/link a `catalog_products` PIM doc + `catalog_variants` row; write `pim_product_id` back to the spine via `find_one_and_update` (single-document, atomic).
- Derive `sku_prefix` from `category` via the canonical prefix map; write back.
- Leave existing `sku` values **as-is** — do not re-mint. POS/stock/Shopify reference them.
- Report-only mode by default (`--apply` flag to commit).

---

## Backend (endpoints + services + which ENGINE calls)

### New service `backend/api/services/product_master.py`

```python
# Category schema (single canonical source)
def category_spec(category: str) -> CategorySpec
    # Normalises long-name <-> short code; reads product_category_specs override first, falls back to CATEGORY_FIELDS
def all_category_specs() -> list[CategorySpec]
def required_fields(category: str) -> list[str]

# SKU rule: PREFIX + BRAND + MODEL + COLORCODE + SIZE
# REWRITE (not reuse of generate_sku). Deterministic concat per Excel spec.
# Allows '/' and '-' in output. Appends atomic counter suffix ONLY on collision.
# Legacy SKU acceptance: format-permissive (allow '/', '-', no length constraint).
def build_sku(category: str, attributes: dict, db=None) -> str
def parse_sku(sku: str) -> ParsedSku | None

# Validation
def validate_attributes(category: str, attributes: dict) -> None
    # Checks required fields for the category; raises ProductMasterError (-> 422) naming missing fields
    # HEARING_AID: serial_no required; CONTACT_LENS: expiry_date required
def normalise_payload(payload: ProductMasterCreate) -> dict
    # GST/HSN from gst_rates.gst_rate_for_category / hsn_for_category
    # offer <= MRP via pricing_caps.evaluate_offer_price
    # discount_category validation (allow SERVICE + existing 4)
    # sku_prefix derived from category

# Triple-write persistence (spine-first + compensation, no cross-collection transaction)
def create_product(payload: ProductMasterCreate, actor: str) -> ProductMasterDoc
    # 1. ProductRepository.create (spine) -- durable anchor
    # 2. _write_pim_doc (catalog_products upsert by id) -- fail-soft, log on error
    # 3. _write_variant_row (catalog_variants upsert by sku) -- fail-soft, log on error
    # 4. ProductRepository.update_one({product_id}, {$set: {pim_product_id}}) -- single-doc, atomic
    # 5. AuditRepository.create (audit_log row)
def update_product(product_id: str, patch: ProductMasterUpdate, actor: str) -> ProductMasterDoc
    # Validates offer<=MRP merge (existing MRP vs new offer, existing offer vs new MRP)
    # Updates spine via find_one_and_update; updates PIM doc if pim_product_id present
    # AuditRepository.create with before/after snapshot
def get_product(product_id_or_sku: str) -> ProductMasterDoc | None
def list_products(filters: ProductFilter) -> Page[ProductMasterDoc]
```

**Engine calls (shared, never reimplemented):**
- `gst_rates.gst_rate_for_category` / `hsn_for_category` — GST derivation
- `pricing_caps.evaluate_offer_price` — offer>MRP guard
- `barcode.validate_ean13` / `allocate_sequence` — barcode validation
- `ProductRepository.find_by_sku` / `create` / `find_one_and_update` — spine writes
- `AuditRepository.create` — immutable audit (not `append_audit_entry`, per P0-2 / CORRECTIONS)
- `_next_sku_counter` (catalog.py) — atomic counter; will be moved into `product_master.py`

### Endpoints

New/refactored at prefix `/api/v1/products` (router `backend/api/routers/product_master.py`):

| Method | Path | Auth | Action |
|---|---|---|---|
| `GET` | `/products/categories` | AUTHENTICATED | `all_category_specs()` |
| `GET` | `/products/categories/{category}/fields` | AUTHENTICATED | `category_spec(category)` |
| `POST` | `/products/sku-preview` | CATALOG_ROLES | `{category, attributes}` → `{sku}` |
| `POST` | `/products` | CATALOG_ROLES | `ProductMasterCreate` → triple-write |
| `PUT` | `/products/{product_id}` | CATALOG_ROLES | `ProductMasterUpdate` → engine update |
| `POST` | `/products/bulk-create` | ADMIN/CATALOG_MANAGER | Batch triple-write |
| `GET` | `/products` | AUTHENTICATED | List (unchanged) |
| `GET` | `/products/{id}` | AUTHENTICATED | Get (unchanged) |

`/catalog/*` endpoints remain unchanged as thin proxies for back-compat; they keep serving `catalog_products` reads.

`ProductMasterCreate` merges today's `ProductCreate` (products.py:287) and `ProductCreateInput` (catalog.py:946): `sku` is optional on create (engine mints it); accepted if supplied (legacy import path — format-permissive validator allows `/` and `-`).

---

## Frontend (pages/components + what they show; restrained light UI)

### `AddProductPage.tsx` — submit path fix (D8)

The wizard's `handleSubmit` currently posts to `POST /api/v1/catalog/products`. Change to post to `POST /api/v1/products` (the engine endpoint that does the triple-write). The 6-step flow is unchanged; only the submit target and response parsing change.

**HA serial_no required field**: the wizard's `validateCurrentStep` (AddProductPage.tsx:117-140) checks `field.required` against `CATEGORY_FIELDS`. When the engine serves `GET /products/categories/HA/fields`, `serial_no` will be marked `required: true`. No wizard code change needed — the existing required-field loop covers it.

**GST auto-populate**: keep the `useEffect` that calls `getGSTRateByCategory` for UI display; the server always derives the authoritative rate on create, so frontend display and server write stay in sync.

### `ProductListPage` / Inventory page — category column

When `HEARING_AID` products appear in the list, the category column must display `"Hearing Aid"` (from `CATEGORY_NAMES` map). No logic change — the existing display map covers it once the enum is extended.

### `product_category_specs` admin page (LOW priority, in-scope but minimal)

A SUPERADMIN-only page at `/settings/catalog/categories` showing the per-category required/optional fields with inline edit. Restrained light UI: neutral card rows, single-column table, edit-in-place text inputs, no colour beyond the system accent for the Save button. This page is informational; it only becomes required when a SUPERADMIN needs to add a new category without a deploy. Can be deferred to a follow-up PR if effort is tight.

**UI constraints (DECISIONS §1):** neutral/monochrome, no multi-colour category badges, no decorative icons. Category chip = plain `bg-gray-100 text-gray-700` border pill. Status enum values (`DRAFT`/`PUBLISHED`/`ARCHIVED` on PIM docs) use semantic colour only: gray/green/red.

---

## RBAC + flags

| Action | Roles | Notes |
|---|---|---|
| Read categories/fields/SKU-preview | All authenticated | No store-scope restriction |
| List/get products | All authenticated | Global catalog, store-scope via inventory |
| Create / update product | SUPERADMIN, ADMIN, CATALOG_MANAGER | Mirrors existing `_CATALOG_ROLES` in products.py:22 |
| Bulk-create | SUPERADMIN, ADMIN, CATALOG_MANAGER | Same gate |
| Edit `product_category_specs` | SUPERADMIN only | |
| Soft-delete | SUPERADMIN, ADMIN | |
| Edit PIM/Shopify superset (`ecom`) | SUPERADMIN, ADMIN, CATALOG_MANAGER | |

No POS feature-flag required — the POS billing path reads `products` spine only and is **not modified** (DECISIONS §1 POS safety, and engine contract: POS is read-only against `products`).

New RBAC policy rows to register in `backend/api/services/rbac_policy.py`:
- `POST /api/v1/products/sku-preview` → `CATALOG_ROLES`
- `POST /api/v1/products/bulk-create` → `CATALOG_ROLES`
- `GET /api/v1/products/categories` → `AUTHENTICATED`
- `GET /api/v1/products/categories/{category}/fields` → `AUTHENTICATED`

---

## Engine + CORRECTIONS folded

**CORRECTIONS entry for PM** (CORRECTIONS.md line 55, P1 block):

> **PM (Phase 1):** SKU rule is a **rewrite** (not reuse of `generate_sku`); legacy SKU acceptance must be format-permissive (allow `/`,`-`); additively add `HEARING_AID` to the `$jsonSchema` enum + reconcile the two divergent category enums BEFORE any add-flow test; triple-write order = spine first + compensation.

Folded as follows:

1. **SKU rule = rewrite**: `build_sku` in `product_master.py` is a new function, not a wrapper of `generate_sku`. `generate_sku` in `catalog.py` remains unchanged for back-compat of the `/catalog` path; `build_sku` implements the Excel rule (`PREFIX+BRAND+MODEL+COLORCODE+SIZE` verbatim, collision-only counter).
2. **Legacy SKU format-permissive**: `ProductMasterCreate.sku` validator allows `/` and `-` (regex `[A-Za-z0-9/_-]+`). No length constraint. The Mongo `sku` unique index still enforces uniqueness.
3. **`HEARING_AID` additively added**: `schemas.py` `PRODUCT_SCHEMA.category` enum gains `"HEARING_AID"`; `CATEGORY_FIELDS` in `product_master.py` sets `serial_no` as required for HA.
4. **Category enum reconciliation first**: the engine uses long-form canonical values (`FRAME`, `SUNGLASS`, etc.) for `products.category`; short-code `ProductCategory` enum is internal to SKU prefix mapping only. The `catalog_products` PIM doc stores whichever form it was given (it is schemaless). No existing document is rewritten by this packet.
5. **Triple-write order = spine first + compensation**: `create_product` writes `products` via `ProductRepository.create` first; if PIM or variant write fails it is logged and retried asynchronously but does NOT roll back the spine (no cross-collection transactions exist — CORRECTIONS P0-1).
6. **No dual-write across collections in a single `find_one_and_update`**: the `pim_product_id` back-link is written in a separate single-document `find_one_and_update` on the `products` doc after the PIM doc is created — two separate atomic operations, not one cross-collection transaction.

**Other CORRECTIONS honoured (not PM-specific but applied in this packet):**
- `AuditRepository.create` used (not `append_audit_entry`) — per P0-2.
- No `commission_ledger` / payroll incentive feed touched — per P0-3/P0-4 (PM has no incentive component).

---

## Acceptance tests (INTENT-LEVEL; a hollow shell must FAIL)

All tests live in `backend/tests/test_product_master.py`. A hollow engine that passes `attributes` through without validation, or that only writes to `products` without the PIM doc, must cause test failures.

**T1 — Category-conditional required fields (intent: server rejects missing required fields)**
- Create a Contact Lens payload missing `expiry_date` → expect HTTP 422, response body names `expiry_date`.
- Create a Hearing Aid payload missing `serial_no` → expect HTTP 422, response body names `serial_no`.
- Create a Frame payload missing `colour_code` → expect HTTP 422, response body names `colour_code`.
- Create a Lens payload missing `index` → expect HTTP 422.
- Create a valid Frame with all required fields → expect HTTP 201, `product_id` and `sku` in response.
- *Hollow shell fails: if the engine accepts any `attributes` dict without validating, the 422 cases return 201 → test fails.*

**T2 — SKU rule (intent: SKU is deterministic, format-permissive, unique)**
- A Burberry sunglass with model `B 3142` and colour code `1109/71` mints a SKU that: (a) starts with `SG`, (b) contains `BURBERRY` or `BU` brand segment, (c) contains `1109/71` (the `/` is preserved), (d) is <= the canonical Excel example `SGPRADAVPR19W1AB1O153` in structure.
- Minting the same brand/model/colour twice yields two distinct SKUs (collision appends counter suffix); the `sku` unique index must hold (no duplicate-key error on the second create).
- A legacy SKU containing `/` (e.g. `FRBURBERRYB31421109/7155`) is accepted by `ProductMasterCreate` without 422.
- *Hollow shell fails: if `build_sku` just re-calls `generate_sku`, the `/` is stripped and the counter is always appended → test (b)/(c) fail.*

**T3 — Triple-write (intent: one add-flow writes spine + PIM + variant)**
- `POST /api/v1/products` for a Frame → verify: (a) a `products` doc with the returned `product_id` exists; (b) a `catalog_products` doc with `pim_product_id` matching `product_id` exists; (c) a `catalog_variants` row with the minted SKU exists; (d) `products.pim_product_id` equals `catalog_products.id`.
- `GET /api/v1/products/sku/{sku}` resolves the product; `online_status_for_skus([sku])` (if BVI Postgres configured) resolves the variant.
- *Hollow shell fails: if `create_product` only writes `products`, checks (b)-(d) fail.*

**T4 — GST is derived, never guessed (intent: master rate == billing rate)**
- Frame persists with `gst_rate = 5.0` and `hsn_code` starting `9003` — exactly `gst_rate_for_category("FRAME")`.
- Non-corrective Sunglass persists with `gst_rate = 18.0`.
- Contact Lens persists with `gst_rate = 5.0` (DECISIONS contact-lens GST = 5%).
- Hearing Aid persists with `gst_rate = 0.0`.
- A create call that omits `gst_rate` and `hsn_code` gets the correct derived values — not 18.0 by default.
- *Hollow shell fails: if the engine hard-codes 18.0 for missing gst_rate, CL/Frame/HA tests fail.*

**T5 — Pricing invariant on create AND partial update (intent: offer>MRP blocked in both directions)**
- `POST /products` with `offer_price > mrp` → HTTP 400.
- `PUT /products/{id}` with only `offer_price` raised above existing `mrp` → HTTP 400.
- `PUT /products/{id}` with only `mrp` lowered below existing `offer_price` → HTTP 400.
- LUXURY `discount_category` on create with a legitimate offer that respects the 5% cap → HTTP 201.
- `PUT` attempting to change `discount_category` from LUXURY to MASS → HTTP 400 (CORRECTIONS E2 luxury-cap invariant: may only lower, not raise).
- *Hollow shell fails: if update path only checks new_offer vs new_mrp without reading current state, the third sub-case passes incorrectly.*

**T6 — Hearing Aid and SERVICE enum in Mongo schema (intent: no validator rejection)**
- Save a product with `category="HEARING_AID"` directly to Mongo using `ProductRepository.create` → no `WriteError` from the `$jsonSchema` validator.
- Save a product with `discount_category="SERVICE"` → no `WriteError`.
- *Hollow shell fails: if schemas.py is not patched, Mongo rejects these writes with code 121.*

**T7 — PIM superset round-trip for NEXUS (intent: Shopify attributes survive without mutation)**
- Create a product with `shape="AVIATOR"`, `polarization=True`, `uv_protection="UV400"`, `tags=["summer","bestseller"]`, `seo.handle="ray-ban-aviator-g15"`.
- Fetch via `GET /api/v1/products/{id}` → verify all five values are present and unchanged (no HTML-stripping of clean values).
- *Hollow shell fails: if PIM write is omitted, all five fields are absent from the response.*

**T8 — Back-compat (intent: legacy products unmodified, backfill dry-run non-destructive)**
- An existing legacy `products` doc (no `pim_product_id`, Shopify-style SKU `FRBURBERRYB31421109/7155`) lists and gets without error.
- Running `scripts/_backfill_product_master.py` with no `--apply` flag produces a report listing the links it would create but makes zero writes (verify write count = 0).
- *Hollow shell fails: if backfill writes unconditionally, legacy docs are mutated in dry-run.*

**T9 — Audit (intent: every mutation has an audit trail)**
- `POST /products` → verify `audit_log` collection contains one entry with `action="product.created"`, `actor` = the test user ID, `after.product_id` = the created product ID.
- `PUT /products/{id}` changing MRP → verify `audit_log` entry has `before.mrp != after.mrp`, IST `ts` present.
- Soft-delete → verify `action="product.deleted"` entry.
- *Hollow shell fails: if `AuditRepository.create` call is missing, the `audit_log` collection stays empty.*

---

## Effort (dev-days) + risk

| Component | Effort |
|---|---|
| schemas.py enum patches (D1, D2) + index additions (D6) | 0.5d |
| `product_master.py` service: category-spec extraction, `build_sku` rewrite, `validate_attributes`, `normalise_payload`, triple-write `create_product` / `update_product` | 3.0d |
| `product_master.py` router + RBAC rows + `sku-preview` endpoint | 1.0d |
| Backfill script `scripts/_backfill_product_master.py` (dry-run + apply) | 1.0d |
| Frontend: `AddProductPage` submit target fix + HA field required flag | 0.5d |
| Acceptance tests `test_product_master.py` (T1-T9, ~45 cases) | 2.0d |
| **Total** | **8.0 dev-days** |

**Risk: Medium.**
- Highest risk: the two-surface backfill must not re-mint or overwrite live SKUs referenced by POS/stock/Shopify. Mitigated by dry-run default + leaving `sku` untouched.
- Second risk: the `$jsonSchema` enum patches (D1, D2) apply at the Mongo collection level via `collMod`; they must be run via `database/migrations.py` (the existing `_run_migrations` pattern) before any new HA/SERVICE products are created. A missed migration leaves existing data valid but new creates fail with Mongo code 121.
- Low risk: the additive schema fields (`pim_product_id`, `sku_prefix`, new indexes) are all optional/sparse; existing queries are unaffected.
- POS billing path is **read-only against `products`** and is not modified. No POS feature flag required.

---

## Definition of done

- [ ] `schemas.py` `PRODUCT_SCHEMA.category` enum includes `HEARING_AID`; `discount_category` enum includes `SERVICE`; migration script applies `collMod` idempotently.
- [ ] `backend/api/services/product_master.py` exists with `build_sku`, `validate_attributes`, `normalise_payload`, `create_product` (triple-write, spine-first), `update_product`, `get_product`, `list_products`.
- [ ] `POST /api/v1/products` delegates to `product_master.create_product`; a single call creates a `products` row, a `catalog_products` PIM doc, and a `catalog_variants` row.
- [ ] `POST /api/v1/products/sku-preview` returns a deterministic SKU matching the Excel rule (PREFIX+BRAND+MODEL+COLORCODE+SIZE, `/` preserved).
- [ ] Creating a Contact Lens without `expiry_date`, a Hearing Aid without `serial_no`, or a Frame without `colour_code` returns HTTP 422 naming the missing field.
- [ ] Creating a Hearing Aid with `category="HEARING_AID"` does not raise a Mongo `$jsonSchema` WriteError.
- [ ] Saving `discount_category="SERVICE"` does not raise a Mongo WriteError.
- [ ] `PUT /products/{id}` blocks offer>MRP in both raise-offer and lower-MRP directions.
- [ ] All T1-T9 acceptance tests pass; CI green.
- [ ] `scripts/_backfill_product_master.py --dry-run` reports link plan and makes zero writes; `--apply` links existing products with zero SKU changes.
- [ ] `frontend/src/pages/catalog/AddProductPage.tsx` submits to `POST /api/v1/products`; HA `serial_no` is required in the wizard details step.
- [ ] `npx tsc -b && npx vite build` exit 0 after frontend changes.
- [ ] Backend smoke: `python -c "from api.main import app; print(len(app.routes))"` exits without import error.
- [ ] No emojis in any new Python file; light-only UI; IST timestamps on all audit rows.
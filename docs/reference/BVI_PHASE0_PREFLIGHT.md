# BVI Merge -- Phase 0 Pre-Flight Tooling

The safety net built **before any data moves**. Everything here is **read-only**:
it inspects the BVI Postgres + the IMS Mongo and reports parity, but never writes,
updates, or deletes a single row. It changes nothing live.

Three deliverables:

| Tool | What it does | DB needed? |
|------|--------------|------------|
| `scripts/bvi_parity_check.py` | Read-only PG <-> Mongo parity oracle: counts per entity + SKU diff + barcode diff + live image-storage count. | Yes (at run time, injected via `railway run`) |
| `scripts/bvi_image_audit.py` | **Static** scan of `ecommerce/` source: are images on local disk or a durable host? Quantifies the Phase-4 re-host scope. | No -- runs now |
| `backend/tests/test_bvi_parity_check.py` | Unit tests for the comparison/diff logic with in-memory fixtures. | No |

---

## 1. Image-storage finding (the Phase-4 risk)

**Verdict: `MIXED_LOCAL_FALLBACK`.**

The BVI image upload route (`ecommerce/src/app/api/images/route.ts`) does:

1. **Try the Shopify CDN first** via `uploadFileToShopify(...)` -> a durable
   `https://cdn.shopify.com/...` URL stored in `ProductImage.url` /
   `VariantImage.url`.
2. **On Shopify failure, fall back to local disk**: `writeFile()` into
   `public/uploads/` and store the **relative** url `"/uploads/<file>"`. The
   route's own comment says *"works for dev, ephemeral on Railway."*

So image URLs in the BVI Postgres are a **mix**:

- Durable rows (`http(s)://...`) -- survive BVI shutdown, **no re-host needed**.
- Local-disk rows (`/uploads/...`) -- served off the BVI app's own disk; they
  **404 the moment BVI is turned off**. **Every such row is in the Phase-4
  re-host scope.**

The Prisma image models confirm where these land:

```
ProductImage: url, originalUrl, shopifyMediaId, isProcessed, role
VariantImage: url, originalUrl, shopifyMediaId, isProcessed
```

(`originalUrl` can also be a `/uploads/...` path, so the audit counts it too.)

**Phase-4 implication:** before BVI is killed, the local-disk images must be
re-hosted to a durable URL (Shopify CDN / S3 / Vercel Blob) and the
`ProductImage`/`VariantImage` rows re-pointed. To get the **exact count** of
local-disk vs durable rows in production, run the parity checker against the live
DB (its `image_storage` section reads the real `ProductImage`/`VariantImage` rows
and reports `local_disk`, `durable`, `local_disk_pct`, and `phase4_rehost_needed`).

Run the static audit any time (no DB):

```bash
python scripts/bvi_image_audit.py          # text
python scripts/bvi_image_audit.py --json   # machine-readable
```

---

## 2. PG -> Mongo field mapping

The BVI Postgres (Prisma) catalog maps into the IMS Mongo collections as follows.
Match keys for the parity gate are **`sku`** and **`storeBarcode`**.

### Stock-model difference (do NOT copy quantity columns)

IMS uses **one row per physical unit** in the `stock` collection; on-hand =
`COUNT(stock units WHERE sku, store, AVAILABLE)`, and the **online quantity is a
calculated slice** of that on-hand. Therefore BVI's quantity columns are
**deliberately NOT migrated**:

- `ProductLocation.quantity` -- NOT copied.
- `VariantLocation.quantity` -- NOT copied.

The parity checker likewise does not compare quantities; it compares identity
(SKU + barcode), counts, and image storage.

### Product / ProductVariant -> `catalog_products` + `catalog_variants` (+ spine `products`)

| BVI Postgres (Prisma) | IMS Mongo | Notes |
|---|---|---|
| `Product` (parent) | `catalog_products` (PIM superset) | one parent product |
| `ProductVariant` | `catalog_variants` (per-SKU identity) | the billing/stock unit |
| `ProductVariant.sku` | `catalog_variants.sku` | **parity match key** |
| `ProductVariant.barcode` (GTIN/UPC, -> Shopify) | `catalog_variants.gtin` / `barcode` | the Shopify-pushed barcode |
| `ProductVariant.storeBarcode` (physical, never pushed) | `catalog_variants.store_barcode` (also mirrored to spine `products.barcode`) | **parity barcode key** -- the physical store barcode |
| `Product.category` / `brand` / `subBrand` / `modelNo` | spine `category` / `brand` / `model` (via product master registry) | category normalised to the canonical IMS taxonomy |
| `Product.mrp` / `discountedPrice` / `compareAtPrice` | spine `mrp` / `offer_price` | GST/HSN derived server-side in IMS, not copied verbatim |
| `ProductVariant.colorCode` / `colorName` / `frameSize` / `bridge` / ... | `catalog_variants.color_code` / `color_name` / `frame_size` / `bridge` / ... | variant-defining attrs |
| `ProductVariant.power` / `packSize` / `cylinder` / `axis` / `strapColor` / ... | `catalog_variants.power` / `pack_size` / `cylinder` / `axis` / `strap_color` / ... | category-specific variant fields |
| `ProductVariant.shopifyVariantId` / `shopifyInventoryItemId` | `catalog_variants.shopify_variant_id` / `shopify_inventory_item_id` | sparse/unique -- blanks omitted on migrate |
| `ProductLocation.quantity` / `VariantLocation.quantity` | **(not migrated)** | IMS = 1 stock row per unit |

### Collection / CollectionProduct -> `ecom_collections`

| BVI | IMS | Notes |
|---|---|---|
| `Collection` | `ecom_collections` | upsert key = `handle` |
| `CollectionProduct` (join) | embedded `products: [{sku, position}]` | flattened, 0-based positions |
| `Collection.rules` (Shopify shape) | `rules` (IMS engine shape) + `rules_shopify` (verbatim) | SMART-collection rules normalised |

### Menu / MenuItem -> `ecom_menus`

| BVI | IMS | Notes |
|---|---|---|
| `Menu` | `ecom_menus` | upsert key = `handle` |
| `MenuItem` (flat, `parentId` self-FK) | embedded recursive `items[].children[]` tree | parent links become structure |

### ProductImage / VariantImage -> `product_images`

| BVI | IMS | Notes |
|---|---|---|
| `ProductImage` (variant_id = null) + `VariantImage` (variant_id set) | one `product_images` collection | discriminated by `variant_id` |
| `url` / `originalUrl` | `url` / `edited_url` | **local-disk `/uploads/...` rows -> Phase-4 re-host** |
| `role` (RAW/EDITED) | `kind` + `status` (RAW->QUEUED, EDITED->APPROVED) | design-queue lifecycle |

### Customer / Order -> `customers` / `online_orders`

| BVI | IMS | Notes |
|---|---|---|
| `Customer` | `customers` | identity by email/phone (IMS dedups on normalised mobile) |
| `Order` + `OrderLineItem` | `online_orders` | store-scoped on the IMS side |

> The full, executable mapping (the four pure mapper functions) already lives in
> `scripts/migrate_bvi_pim.py`; this Phase-0 doc is the parity contract that
> proves the migration landed.

---

## 3. Runbook -- running the parity check via `railway run`

The parity checker reads **two** connection strings, **only from the
environment at run time**. It never hardcodes, logs, or prints a secret value --
status lines show `SET` / `NOT SET` only.

| Env var | Holds | Source |
|---|---|---|
| `BVI_DATABASE_URL` *or* `ECOMMERCE_DATABASE_URL` | BVI Postgres connection string | the BVI Postgres service in the IMS 2.0 Railway project. `ECOMMERCE_DATABASE_URL` is the name the live IMS backend + `migrate_bvi_pim.py` already use. |
| `MONGODB_URL` *or* `MONGO_URL` | IMS Mongo connection string | the MongoDB service in the IMS 2.0 Railway project. |
| `MONGO_DATABASE` | Mongo db name (default `ims_2_0`) | optional |

### Command shape (no secrets in the command line)

`railway run` injects the service's variables into the subprocess, so the URLs
never appear on screen or in shell history. Run it linked to the **service that
already has both `ECOMMERCE_DATABASE_URL` and `MONGODB_URL`** (the IMS backend
service), or pass the BVI Postgres service for `ECOMMERCE_DATABASE_URL` via a
Railway variable reference.

```bash
# From the repo root, linked to the IMS 2.0 Railway project + the backend service.
# (Windows venv path uses backslashes for railway run, per the project convention.)

# Human-readable report:
railway run -- .venv\Scripts\python.exe scripts/bvi_parity_check.py

# Machine-readable JSON (for CI / diffing / saving a baseline):
railway run -- .venv\Scripts\python.exe scripts/bvi_parity_check.py --json

# Bash/macOS/Linux equivalent:
railway run -- .venv/bin/python scripts/bvi_parity_check.py
```

If the BVI Postgres URL lives on a **different** service than Mongo, run with both
injected, e.g.:

```bash
railway run --service MongoDB -- bash -c \
  'BVI_DATABASE_URL="$ECOMMERCE_DATABASE_URL" MONGODB_URL="$MONGO_PUBLIC_URL" \
   .venv/bin/python scripts/bvi_parity_check.py'
```

(Use whichever variable references your services expose -- the script reads the
**keys** above and never echoes their values. This mirrors the prod-Mongo
migration pattern already used in this repo: `railway run --service MongoDB bash -c '...'`.)

### Reading the result

- **Exit 0** -- parity gate **PASS** (counts equal, no missing SKU, no barcode
  mismatch). This is the **Phase-1 exit gate** (the ~100% SKU + storeBarcode
  match).
- **Exit 2** -- ran fine but parity **FAILED** (some SKUs missing, a barcode
  mismatched, or a count delta). The report lists samples; `--json` has the full
  lists. **This is the expected state BEFORE migration** (IMS empty -> everything
  "missing"); re-run after migration until it flips to PASS.
- **Exit 1** -- **fail loud**: a missing env var, an unreachable DB, or a missing
  driver. Nothing was compared; fix the connection and re-run.

The report's `image_storage` section gives the **live** Phase-4 count
(`local_disk` vs `durable`, and `phase4_rehost_needed`).

---

## Read-only / no-secrets guarantees

- **Strictly read-only DB access.** Postgres: `SELECT` only, and the session is
  opened `set_session(readonly=True)` so the server itself rejects any write.
  Mongo: `count_documents` / `find` only. No `INSERT`/`UPDATE`/`DELETE`/DDL and
  no `update_one`/`insert_*`/`delete_*` anywhere in either script.
- **No secrets touched.** Connection strings arrive only from the environment at
  run time. The scripts never hardcode, log, or print a URL or any secret --
  status output is `SET` / `NOT SET`.
- **Fail loud, never silent.** A missing env var or unreachable DB exits non-zero
  with a clear message; a read error (count = -1) aborts rather than report false
  parity.
- **No new API routes.** These are stand-alone scripts, not endpoints -- nothing
  to add to `rbac_policy.py`.
- **No emojis in Python** (Windows cp1252 safe).

# BetterVision Inventory

> Single source of truth for **Better Vision**'s multi-location optical retail catalogue. Auto-generates SEO-optimised Shopify product data, syncs bidirectionally with the storefront, runs barcode-based physical inventory counts, and ships per-category product workflows for 12 distinct eyewear / watch / accessory categories.

[![Deploy: Railway](https://img.shields.io/badge/deploy-railway-blueviolet)](https://railway.app)
[![Storefront: Shopify](https://img.shields.io/badge/storefront-shopify%202026--01-96bf48)](https://shopify.dev)
[![Stack: Next.js 15](https://img.shields.io/badge/next.js-15.5.10-black)](https://nextjs.org)
[![DB: PostgreSQL](https://img.shields.io/badge/postgres-16-336791)](https://www.postgresql.org)
[![ORM: Prisma 6](https://img.shields.io/badge/prisma-6.19-2D3748)](https://www.prisma.io)
[![License: Proprietary](https://img.shields.io/badge/license-proprietary-lightgrey)](#license)

**Live (production):** `https://bettervision-inventory-production.up.railway.app`  
**Storefront:** `https://www.bettervision.in` · **Shopify admin:** `bokaro-better-vision.myshopify.com`  
**Owner:** Avinash · brash.akg@gmail.com

---

## Table of contents

- [Why this exists](#why-this-exists)
- [Who uses it](#who-uses-it)
- [The 30-second mental model](#the-30-second-mental-model)
- [Quick start (local dev)](#quick-start-local-dev)
- [Quick start (production deploy)](#quick-start-production-deploy)
- [Tech stack](#tech-stack)
- [System architecture](#system-architecture)
- [Database schema](#database-schema)
- [API surface (66 routes)](#api-surface-66-routes)
- [Dashboard pages (28 pages)](#dashboard-pages-28-pages)
- [Shopify integration deep dive](#shopify-integration-deep-dive)
- [Product workflows (the catalogue loop)](#product-workflows-the-catalogue-loop)
- [Auto-generation engine](#auto-generation-engine)
- [Categories (12 of them)](#categories-12-of-them)
- [Admin tools](#admin-tools)
- [Storefront theme integration](#storefront-theme-integration)
- [Environment variables](#environment-variables)
- [Common tasks](#common-tasks)
- [Troubleshooting](#troubleshooting)
- [Recent work — round-by-round changelog](#recent-work--round-by-round-changelog)
- [Known issues / technical debt](#known-issues--technical-debt)
- [Architectural decision records](#architectural-decision-records)
- [Repository structure](#repository-structure)
- [Contributing](#contributing)
- [Credits](#credits)
- [License](#license)

---

## Why this exists

**Better Vision** is an Indian optical retail chain with two physical stores (Bokaro, Jharkhand and Gangadham, Pune) and a Shopify online store. Pre-2025, their catalogue lived in Shopify alone. That was broken in five specific ways:

1. **No multi-location inventory.** Shopify treated all stock as a single pool. Stock Tally — the physical-count flow that store staff run with barcode scanners — couldn't reconcile "what's on the shelf in Bokaro" against "what Shopify thinks is in stock".
2. **No bulk Excel imports.** Adding 200 frames in one go meant typing each into Shopify by hand or wrestling with Shopify's CSV import (which fails silently on encoding, can't do variant-level barcode mapping, and has no rollback).
3. **No auto-generated SEO.** Each eyewear product has 15+ attributes (brand, model, shape, frame material, temple material, lens type, colour, size, gender, country of origin, warranty, polarization, UV, GTIN, …). Writing the title + meta-description + handle + tag-list + HTML body by hand for every product is what the catalog team spent their day on.
4. **No design queue.** Cataloguers shoot raw product photos on a phone; a designer edits them in Photoshop later. There's no system where "raw images attached, awaiting designer work" is a queryable state — products either went live with phone snaps or got stuck in a Slack thread.
5. **No source of truth.** Information was scattered across the Shopify admin, half a dozen Google Sheets, a WhatsApp group, and an old Tally accounting export.

**This app is the consolidation.**

It's a Next.js app on Railway that talks to Shopify via the Admin GraphQL API, persists every product / variant / collection / menu / customer / order locally in Postgres, auto-generates the SEO surface from structured attributes, runs barcode-driven physical inventory counts, and pushes the result back to Shopify. The storefront stays as Shopify — we don't replace it — we just feed it.

## Who uses it

- **Avinash (Admin)** — owner; runs strategy, manages users, configures discount rules, runs Shopify sync, owns the storefront theme.
- **Catalog managers** — log in daily, add new products via the **Add Product wizard**, upload raw images, push to Shopify. Bulk operations via Excel.
- **Store staff** — only need **Stock Tally** and **Stock Transfers**. Scan barcodes, reconcile against Shopify, transfer stock between Bokaro and Pune.
- **Design managers** — pull from the **Design Queue**, replace raw cataloguer-uploaded images with edited versions, push edited assets to Shopify, flip product status to Published.

Roles are enforced by `requireAuth(["ADMIN" | "CATALOG_MANAGER" | "DESIGN_MANAGER"])` on every write endpoint. Reads use plain `requireAuth()` (any signed-in user).

## The 30-second mental model

```
                 ┌────────────────────────────────────────────────────┐
                 │              Shopify (source of truth              │
                 │            for orders, customers, online stock)    │
                 └────┬───────────────────────────────────────┬───────┘
              webhooks│                                  ↑    │REST/GraphQL
                      │     pull / push                  │    │
                      ▼                                  │    ▼
┌──────────────────────────────────────────────────────────────────────┐
│              BetterVision Inventory (this app, Railway)              │
│  ┌─────────────────┐  ┌──────────────────┐  ┌─────────────────────┐  │
│  │  Next.js pages  │  │  API routes (66) │  │   Prisma + Postgres │  │
│  │  /dashboard/*   │──│  /api/*          │──│   20+ models        │  │
│  └─────────────────┘  └──────────────────┘  └─────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
                      ▲                            ▲
                      │                            │
   ┌──────────────────┴──┐              ┌─────────┴────────────┐
   │  Catalog managers   │              │  Store staff (Bokaro │
   │  (Add / Edit / push)│              │  + Pune barcode      │
   └─────────────────────┘              │  scanners)           │
                                        └──────────────────────┘
```

Every change to a product, collection, or menu either:
- Originates **in the app**, validates, persists locally, pushes to Shopify, and writes a `SyncLog` row.
- Originates **in Shopify** (admin, OAuth-app, theme editor), arrives via a webhook on `/api/webhooks/shopify`, gets HMAC-verified, persisted locally, logged in `WebhookEvent`.

The app **never** writes directly to Shopify without persisting locally first. Local is the staging area; Shopify is the storefront.

---

## Quick start (local dev)

```bash
git clone https://github.com/brashakg/bettervision-inventory.git
cd bettervision-inventory

# Node 18.17+ recommended (Next 15 + React 19 requirement).
npm install

# Local SQLite for the dev DB so you don't need Postgres running.
cat > .env <<'EOF'
DATABASE_URL="file:./dev.db"
NEXTAUTH_SECRET="bettervision-secret-change-in-production"
NEXTAUTH_URL="http://localhost:3000"
SHOPIFY_STORE_URL="bokaro-better-vision.myshopify.com"
SHOPIFY_ACCESS_TOKEN="shpat_..."
REMOVEBG_API_KEY=""
EOF

npx prisma generate
npx prisma db push
curl http://localhost:3000/api/seed   # creates admin user + attribute options + discount rules

npm run dev
# Open http://localhost:3000
# Login: admin@bettervision.in / admin123 (or set SEED_ADMIN_PASSWORD env var)
```

### Common dev commands

```bash
npm run dev               # Next 15 dev server with turbopack
npx tsc --noEmit          # Type-check the whole project
npx next build            # Production build + route audit
npx prisma studio         # GUI over the local DB
npx prisma db push        # Apply schema changes (no migration files; --accept-data-loss on Railway)
npx prisma migrate dev    # Generate a migration (rarely needed; we use db push)
npx tsx <script>.ts       # Run any TypeScript file directly (e.g. one-off scripts)
```

---

## Quick start (production deploy)

The repo auto-deploys to Railway on every push to `main`. The Railway service:

| Setting | Value |
|---|---|
| **Build command** | `npx prisma generate && npm run build` |
| **Start command** | `sleep 3 && npx prisma db push --accept-data-loss && npm start` |
| **Database** | Railway-managed PostgreSQL 16 |
| **Region** | asia-southeast1-eqsg3a |
| **Auto-deploy** | Push to `main` triggers build + deploy (~2-3 min) |

Environment variables are set in the Railway dashboard, not in the repo. See [Environment variables](#environment-variables) below.

### Deploying from a Windows mounted repo (CRLF workaround)

The mounted repo at `C:\Users\avina\INVENTORY MODULE\bv-app\` has CRLF line-ending noise that pollutes diffs. To ship changes:

```bash
# 1. Clone to a clean working dir (one-time):
git clone https://github.com/brashakg/bettervision-inventory.git ~/Downloads/bv-app-clone
cd ~/Downloads/bv-app-clone

# 2. Each release: sync to main, copy changes from the mounted repo, commit, push.
git fetch origin main && git reset --hard origin/main
cp -r "C:\Users\avina\INVENTORY MODULE\bv-app\src\." ./src/
git add . && git commit -m "..." && git push origin main
```

Railway auto-deploys from `main`. No manual deploy step.

---

## Tech stack

| Layer | Choice | Version | Notes |
|---|---|---|---|
| Framework | Next.js (App Router) | 15.5.10 | Migrated 14 → 15 in `cea0a35` (HIGH-sev CVE in 14.2.35). All dynamic routes use `params: Promise<{...}>` then `await params`. |
| UI lib | React | 19.2.6 | Paired with Next 15 upgrade. |
| Language | TypeScript | 5.9 | `strict: true`, no implicit `any` allowed (with intentional `as any` escape hatches commented). |
| Database | PostgreSQL | 16 (Railway) | Local dev uses SQLite via `file:./dev.db`. |
| ORM | Prisma | 6.19.3 | 20+ models, all wired via `src/lib/prisma.ts` singleton. |
| Auth | NextAuth | 4.24.14 | Credentials provider + JWT sessions. Custom session shape with `role` and `locationId`. |
| Styling | Tailwind CSS | 3.4 | Plus a Polaris-aligned token set scoped to the Edit-Product V2 shell (see `src/components/edit-product/primitives.tsx`). |
| Icons | lucide-react | latest | One icon set across the whole app — no Heroicons / Feather hybrid. |
| Excel | xlsx | latest | Used by Stock Import for bulk inventory updates. |
| Images | sharp | latest | Server-side image processing (resize, format conversion). |
| AI | Anthropic SDK | — (REST via `fetch`) | Claude Haiku 4.5 for SEO title/description generation. Prompt caching is GA, no beta header needed. |
| Shopify | Admin GraphQL | 2026-01 | OAuth `client_credentials` grant; legacy static token as fallback. |
| Hosting | Railway | — | Single service: Next.js app + Postgres on the same project. |

**Why these choices?**
- **Next.js App Router** — server components let us render the dashboard without a full SPA bundle. Route handlers + middleware live in the same project.
- **Prisma over raw SQL** — Better Vision's schema evolves weekly. Prisma's migration story is more important than the marginal performance hit vs. raw SQL.
- **PostgreSQL** — needed full-text search (`contains` queries with case-insensitive mode), JSON column support for `Product.categorySpecific` / `ProductVariant.extras`, and a real `@@unique` story.
- **NextAuth credentials** — staff log in with email + password (no Google OAuth in the optical retail context; the team includes store staff who don't have a Google account or share a tablet).
- **No state library (Redux/Zustand)** — every dashboard page is small enough that `useState` + `useEffect` is fine. The biggest interactive surface, Edit-Product-V2, uses controlled state with a 1.2s debounced auto-save and survives without a state library.

---

## System architecture

### Request flow (write path: catalogue manager edits a product)

```
                                         ┌────────────────────┐
[Browser]  ─── PUT /api/products/[id] ──▶ │  Next.js Route     │
                                         │  Handler           │
                                         │                    │
                                         │  1. requireAuth()  │ ─── reject if no session
                                         │  2. PUT body parse │ ─── 400 on bad JSON
                                         │  3. PRODUCT_FIELDS │ ─── allowlist filter
                                         │     allowlist      │
                                         │  4. autoGenerate.* │ ─── regenerate title, SEO,
                                         │     (regen)        │     tags, HTML description
                                         │  5. prisma.update  │ ─── persist to local DB
                                         │  6. pushProductsTo │ ─── only if shopifyProductId
                                         │     Shopify()      │     is set (otherwise create)
                                         │  7. logActivity()  │ ─── audit trail
                                         │  8. return Product │
                                         └────────────────────┘
                                                  │
                                                  ▼
                                            [Browser updates UI]
```

### Request flow (read path: dashboard loads recent products)

```
[Browser] ─── GET /api/products?status=ACTIVE&page=1&limit=50 ──▶
                                         ┌────────────────────┐
                                         │  Next.js Route     │
                                         │  Handler           │
                                         │                    │
                                         │  1. requireAuth()  │
                                         │  2. Build where{}  │ ─── filter by search/status/cat
                                         │  3. Promise.all    │ ─── parallel findMany + count
                                         │  4. Return data    │
                                         └────────────────────┘
```

### Background flow (Shopify push webhook arrives)

```
[Shopify]  ─── POST /api/webhooks/shopify ──▶
              X-Shopify-Hmac-Sha256: ...
              X-Shopify-Topic: products/update
                                         ┌─────────────────────────────────┐
                                         │  POST handler                   │
                                         │                                 │
                                         │  1. Verify HMAC                 │ ─── 401 if mismatch
                                         │  2. WebhookEvent.create         │ ─── log incoming
                                         │  3. Return 200 IMMEDIATELY      │ ─── (Shopify 5s cap)
                                         │                                 │
                                         │  background:                    │
                                         │  4. Dispatch by topic           │
                                         │     • products/update           │
                                         │       → handleProductCreateUpd  │
                                         │     • orders/create             │
                                         │       → handleOrderCreateUpdate │
                                         │     • customers/delete          │
                                         │       → handleCustomerDelete    │
                                         │     ...etc (15 topics handled)  │
                                         │  5. Update WebhookEvent.status  │ ─── PROCESSED / FAILED
                                         └─────────────────────────────────┘
```

### Edit-Product V2 architecture (the heaviest UI)

The redesigned edit page (rendered behind `?next=1`) is a 3-column Polaris-aligned shell:

```
┌──────────────────────────────────────────────────────────────────────┐
│ TopBar (53px sticky): ← Back · Title · StatusPill · SKU · …          │
│                     Save state · Issues · Preview · ⌘K · Save · Pub  │
├───────────┬──────────────────────────────────┬───────────────────────┤
│           │                                  │                       │
│ Section   │  Form (single scroll, scroll-spy)│  Live summary rail    │
│ nav (200px│   • Identity (always)            │   • Storefront preview│
│            │   • [category-specific sections] │   • Auto-derived      │
│ + per-     │   • Pricing                      │     (SKU, URL, SEO,   │
│ section    │   • Inventory                    │      tags)            │
│ issue      │   • Images                       │   • Pre-publish issues│
│ dots)      │   • Publish                      │   • Activity feed     │
│            │                                  │                       │
│ scrolls    │ scrolls + scroll-spy             │ scrolls (320px)       │
└───────────┴──────────────────────────────────┴───────────────────────┘
```

- **Scroll-spy**: `useScrollSpy(ids[])` hook updates the active section as the user scrolls. Click on a section in the left rail → smooth-scroll to that section.
- **Per-category sections**: `CAT_SPECS` map in `src/lib/products/categorySpecs.ts`. Sunglasses gets Frame · Lens (UV required) · Details; Reading Glasses gets Frame · Lens · Power (required); Contact Lenses gets Lens spec + Pack size (no Frame); Watches gets Watch specs + Strap (no Frame/Lens); etc.
- **Issues**: pure function `getIssues(product): Issue[]` in `src/lib/products/validation.ts`. Drives both the top-bar issues counter (red badge that scrolls to first issue on click) and the right-rail issues panel. Publish button is disabled while any blocking issue exists.
- **Auto-save**: `formData` changes trigger a 1.2s debounce → PUT `/api/products/[id]`. Save state machine: `idle` → `dirty` → `saving` → `saved (timestamp)` → `error`. SaveIndicator in TopBar shows it. Concurrent saves are gated by a `saveInFlight` ref so manual ⌘S can't race the debounced auto-save (hotfix `892cfc3`).

---

## Database schema

20+ Prisma models. Highlights below; full schema at `prisma/schema.prisma`.

### User + auth

```prisma
model User {
  id              String   @id @default(cuid())
  email           String   @unique
  password        String                    // bcrypt-hashed
  name            String
  role            String   @default("CATALOG_MANAGER")  // ADMIN, DESIGN_MANAGER, CATALOG_MANAGER
  enabledFeatures String?                   // CSV of FeatureKey overrides; null = use role defaults
  locationId      String?                   // user's home store, optional
  location        Location? @relation(fields: [locationId], references: [id])
  products        Product[]                 // products this user created
  createdAt       DateTime @default(now())
  updatedAt       DateTime @updatedAt
}
```

Per-user feature flags live in `enabledFeatures` (CSV of `FeatureKey` from `src/lib/features.ts`). `null` means "use the role's defaults". The Sidebar component filters its nav by `effectiveFeatures()` so a Design Manager only sees what they need.

### Product + variants

```prisma
model Product {
  id                String   @id @default(cuid())
  category          String                   // SPECTACLES, SUNGLASSES, COLOR_CONTACT_LENSES, etc.
  status            String   @default("DRAFT")  // DRAFT, PUBLISHED, ARCHIVED
  shopifyProductId  String?  @unique          // GID format: gid://shopify/Product/12345
  imageDesignStatus String?                   // PENDING_DESIGN, READY, null

  // Brand & identity
  brand        String
  subBrand     String?
  label        String?    // Storefront badge (NOT a tag — round 1 mapping 10.2)
  productName  String?
  modelNo      String?
  fullModelNo  String?

  // Frame
  shape          String?
  frameMaterial  String?
  templeMaterial String?
  frameType      String?

  // Demographics
  gender          String?
  countryOfOrigin String?    // Shopify product field, NOT a tag (mapping 4.6)
  warranty        String?

  // Lens
  lensMaterial String?
  lensUSP      String?
  polarization String?
  uvProtection String?

  // Contact-lens / solutions specifics
  recommendedFor String?
  instructions   String?
  ingredients    String?
  benefits       String?
  aboutProduct   String?

  // Pricing
  mrp             Float    @default(0)
  discountedPrice Float    @default(0)
  compareAtPrice  Float    @default(0)

  // Auto-generated SEO surface
  title           String?
  sku             String?  @unique
  seoTitle        String?
  seoDescription  String?
  pageUrl         String?
  tags            String?  // CSV "brand_rayban, shape_aviator, polarized_yes, ..."
  htmlDescription String?

  // Identifiers
  gtin String?    // 13-digit GTIN
  upc  String?

  // Round 2 mapping additions
  rxable           Boolean @default(false)  // enables prescription module on storefront
  themeSuffix      String?                  // Shopify ProductInput.templateSuffix
  categorySpecific Json?                    // free-form per-category extension data

  // Relations
  createdById String?
  createdBy   User?    @relation(fields: [createdById], references: [id])
  images      ProductImage[]
  locations   ProductLocation[]
  syncLogs    SyncLog[]
  variants    ProductVariant[]
  collections CollectionProduct[]

  createdAt DateTime @default(now())
  updatedAt DateTime @updatedAt
}

model ProductVariant {
  id        String  @id @default(cuid())
  productId String
  product   Product @relation(fields: [productId], references: [id], onDelete: Cascade)

  // Variant-defining attributes
  colorCode    String   // "086"
  colorName    String?  // "Black"
  frameColor   String?
  templeColor  String?
  frameSize    String?  // "55", "52"
  bridge       String?
  templeLength String?
  weight       String?
  lensColour   String?
  tint         String?

  // Pricing
  mrp             Float   @default(0)
  discountedPrice Float   @default(0)
  compareAtPrice  Float   @default(0)

  // Auto-generated
  sku          String?  @unique
  barcode      String?    // GTIN/UPC, syncs to Shopify
  storeBarcode String?    // local-only store barcode (mapping 4.2), used for stock tally
  title        String?    // auto-generated "Black / 55"

  // Round 2 — category-specific variant fields
  power      String?  // reading glasses, contact lenses
  packSize   String?  // contact lenses
  cylinder   String?  // contact lens (toric)
  axis       String?  // contact lens (toric)
  strapColor String?  // watches, smartwatches
  caseSize   String?  // watches, smartwatches
  dialColor  String?  // watches
  extras     Json?    // overflow for niche per-variant fields

  // Shopify sync
  shopifyVariantId       String?
  shopifyInventoryItemId String?

  images    VariantImage[]
  locations VariantLocation[]

  createdAt DateTime @default(now())
  updatedAt DateTime @updatedAt

  @@unique([productId, colorCode, frameSize])
}
```

The `categorySpecific Json?` field on Product (and `extras Json?` on ProductVariant) is the escape hatch for fields that don't deserve a typed column. We promote a Json key to a real column when it needs to be indexed or filtered on; until then it lives in JSON.

### Storefront navigation (round 2)

```prisma
model Menu {
  id               String     @id @default(cuid())
  shopifyMenuId    String?    @unique
  handle           String     @unique   // "main-menu", "footer", ...
  title            String                // "Boost Mega Menu"
  isDefault        Boolean    @default(false)  // can't delete default menus
  active           Boolean    @default(true)
  locallyModified  Boolean    @default(false)  // edited locally, push pending
  lastSyncedAt     DateTime?
  items            MenuItem[]
  createdAt        DateTime   @default(now())
  updatedAt        DateTime   @updatedAt
}

model MenuItem {
  id            String     @id @default(cuid())
  menuId        String
  menu          Menu       @relation(fields: [menuId], references: [id], onDelete: Cascade)
  parentId      String?
  parent        MenuItem?  @relation("MenuItemChildren", fields: [parentId], references: [id], onDelete: Cascade)
  children      MenuItem[] @relation("MenuItemChildren")
  position      Int        @default(0)
  shopifyItemId String?

  title       String
  itemType    String      // COLLECTION, PRODUCT, PAGE, HTTP, FRONTPAGE, BLOG, ARTICLE, SHOP_POLICY, SEARCH, CATALOG
  url         String?
  resourceId  String?     // gid://shopify/Collection/12345 etc.
  tagsFilter  String?     // CSV of tags for tagged-collection-style filters

  // Local overlay fields — not pushed to Shopify; preserved on sync
  iconUrl     String?
  bannerUrl   String?
  badgeText   String?
  badgeColor  String?
  pinnedToTop Boolean @default(false)

  createdAt DateTime @default(now())
  updatedAt DateTime @updatedAt

  @@index([menuId, parentId, position])
}
```

`MenuItem` is flat (one row per item) with `parentId` self-reference. Tree-walking happens in application code (`buildTree()` after fetching). Reorder updates run inside `prisma.$transaction` so a partial reorder can't leave the tree in a half-state.

### Collection + storefront

```prisma
model Collection {
  id                  String     @id @default(cuid())
  shopifyCollectionId String?    @unique
  title               String
  handle              String?
  description         String?
  descriptionHtml     String?
  collectionType      String     @default("CUSTOM")  // CUSTOM | SMART
  sortOrder           String?    // Shopify enum (MANUAL, BEST_SELLING, ...)
  templateSuffix      String?    // round 2 — collection.brand / collection.shape / collection.sale
  imageUrl            String?
  imageAlt            String?
  seoTitle            String?
  seoDescription      String?
  published           Boolean    @default(true)
  productsCount       Int        @default(0)
  rules               String?    // JSON-stringified CollectionRuleSet.rules
  disjunctive         Boolean    @default(false)
  lastSyncedAt        DateTime?
  locallyModified     Boolean    @default(false)

  // Round 2 mapping — auto-generation metadata
  bannerImage      String?
  shortDescription String?
  sortPriority     Int     @default(100)
  metafields       Json?
  autoSource       String?  @unique  // "brand:ray-ban" / "shape:aviator" / etc.
  categoryAnchor   String?   // category this auto-gen collection belongs to

  products CollectionProduct[]

  createdAt DateTime @default(now())
  updatedAt DateTime @updatedAt

  @@index([autoSource])
  @@index([categoryAnchor])
}
```

### Order + customer (Shopify-driven, read-only sync)

`Order`, `OrderLineItem`, `Customer` are synced FROM Shopify only — we never create them in the app. Used for reporting, customer lookup, marketing analytics.

```prisma
model Customer {
  id                String   @id @default(cuid())
  shopifyCustomerId String   @unique
  email             String?
  phone             String?
  firstName         String?
  lastName          String?
  acceptsMarketing  Boolean  @default(false)
  verified          Boolean  @default(false)
  tags              String?
  ordersCount       Int      @default(0)
  totalSpent        Float    @default(0)
  // address1, address2, city, state, zip, country
  orders            Order[]
}

model Order {
  id              String          @id @default(cuid())
  shopifyOrderId  String          @unique
  customerId      String?
  customer        Customer?       @relation(fields: [customerId], references: [id])
  orderNumber     String?
  orderStatus     String          // PENDING, CONFIRMED, FULFILLED, CANCELLED, REFUNDED
  financialStatus String?
  totalPrice      Float           @default(0)
  // ... + cancellation fields, line items
  lineItems       OrderLineItem[]
}
```

`Customer.ordersCount` and `totalSpent` are **derived** from local Order rows, not taken from Shopify's customer payload. `recomputeCustomerAggregates([customerId])` runs after every order webhook to keep them current.

### Other models worth knowing

- **`Location`** — physical store + Shopify's "Shopify Online Store" virtual location. `shopifyLocationId` maps to Shopify's location GID.
- **`ProductLocation` / `VariantLocation`** — per-location stock counts. The two-level model exists because some products (e.g. solutions) don't have variants, but stock is still tracked per location.
- **`AttributeType` / `AttributeOption`** — configurable dropdown values. Brand list, shape list, frame material list, etc. Seeded by `GET /api/seed`; editable from `/dashboard/attributes`.
- **`DiscountRule`** — category-based discount percentages (Spectacles 10%, Sunglasses 12.5%, Solutions 11%). Optionally narrowed by brand or sub-brand.
- **`SyncLog`** — every push to Shopify writes a row (CREATE, UPDATE, DELETE, STOCK_UPDATE, VARIANT_CREATE, VARIANT_UPDATE, DESIGN_UPLOAD). Status: SUCCESS / FAILED / PENDING. Drives the "Last Success" / "Failed (24h)" stats on the Shopify Sync page.
- **`WebhookEvent`** — every incoming Shopify webhook writes a row (RECEIVED → PROCESSED | FAILED). Surfaces on the Webhook Events tab.
- **`WebhookSubscription`** — registered webhook topics (cached locally; the source is Shopify but we mirror to avoid GraphQL roundtrips on every page load).
- **`ActivityLog`** — every user action: product create/update, push to Shopify, image upload, menu cleanup, sync history clear. Searchable + filterable from `/dashboard/activity-logs`.
- **`StockTransfer` / `StockTransferItem`** — inter-location transfers between Bokaro and Pune. States: PENDING → IN_TRANSIT → COMPLETED / CANCELLED.

---

## API surface (66 routes)

Grouped by area. Every route uses `requireAuth([roles?])` from `src/lib/apiAuth.ts`.

### Auth + meta

- `GET  /api/seed` — Seed admin user, attribute options, discount rules. Public (used once on first deploy).
- `GET  /api/auth/[...nextauth]` — NextAuth handlers (signin, session, csrf, providers).

### Products + variants

| Method | Route | What |
|---|---|---|
| GET | `/api/products` | List with search/filter/pagination. Filters: `search`, `status`, `category`, `brand`, `location`. |
| POST | `/api/products` | Create. Auto-generates title / SKU / SEO / tags / HTML. Sets `imageDesignStatus=PENDING_DESIGN` for cataloguer flow. |
| GET | `/api/products/[id]` | Single product with variants, images, locations, syncLogs. |
| PUT | `/api/products/[id]` | Update. PRODUCT_FIELDS allowlist filters bad keys; auto-generated fields are regenerated server-side. |
| DELETE | `/api/products/[id]` | Hard delete. Also calls `deleteProduct(shopifyId)`. |
| POST | `/api/products/bulk` | Batch operations: delete / update status / sync. |
| GET | `/api/products/stats` | Total / published / draft / archived / synced / lowStock counts. |
| GET | `/api/products/filters` | Available filter options for dropdowns (brands present in DB, shapes present, etc.). |
| GET | `/api/products/check-duplicate` | Brand + modelNo uniqueness check during Add Product wizard. |
| GET | `/api/products/orphans` | Products with `shopifyProductId IS NULL` — pushable / unpushable breakdown with reasons. |
| POST | `/api/products/orphans/push` | Bulk-push pushable orphans to Shopify. |
| GET | `/api/products/[id]/variants` | List variants for a product. |
| POST | `/api/products/[id]/variants` | Create variant. Auto-generates variant SKU. |
| PUT | `/api/products/[id]/variants/[variantId]` | Update variant. ALLOWED_VARIANT_FIELDS allowlist. |
| DELETE | `/api/products/[id]/variants/[variantId]` | Delete variant. |
| POST | `/api/products/[id]/stock` | Per-product stock adjustment. |
| POST | `/api/products/[id]/design-upload` | Designer uploads edited images. Marks `imageDesignStatus=READY`. |
| GET | `/api/products/design-queue` | List products in PENDING_DESIGN state. |
| POST | `/api/variants/[variantId]/stock` | Per-variant stock adjustment. |

### Shopify sync

| Method | Route | What |
|---|---|---|
| POST | `/api/shopify/pull` | Full pull: every product + variant + image from Shopify into local DB. Auto-syncs locations too. |
| POST | `/api/shopify/pull/chunk` | Resumable chunked pull (50/page); used when a full pull would time out. |
| POST | `/api/shopify/sync` | Push local products to Shopify. Supports `skip` (create-only) and `update` modes. |
| GET | `/api/shopify/status` | Connection check + product-state stats (Live on Shopify, Failed 24h, Last Success). |
| POST | `/api/shopify/sync-history` | Admin-only. Clears stale FAILED SyncLog rows so the dashboard counter resets. |
| GET | `/api/shopify/scopes` | OAuth scopes granted to the app. |
| POST | `/api/shopify/auth` | OAuth install callback. |
| POST | `/api/shopify/refresh-token` | Refresh OAuth access token. |
| GET/POST/DELETE | `/api/shopify/webhooks` | Manage Shopify webhook subscriptions. |
| POST | `/api/webhooks/shopify` | Public endpoint Shopify POSTs to. HMAC-verified. Returns 200 immediately, processes in background. |

### Collections (round 2)

| Method | Route | What |
|---|---|---|
| GET | `/api/collections` | List with search/filter/pagination. |
| POST | `/api/collections` | Create local collection. |
| GET | `/api/collections/[id]` | Single collection. |
| PUT | `/api/collections/[id]` | Update. |
| DELETE | `/api/collections/[id]` | Delete. |
| POST | `/api/collections/sync` | Pull all collections from Shopify (parallel chunked upsert; ~15s for 1,200 collections). |
| POST | `/api/collections/auto-generate` | Generate brand × category × shape × gender smart collections per the per-category plan. Dry-run by default. |

### Menus (round 2)

| Method | Route | What |
|---|---|---|
| GET | `/api/menus` | List menus. `?includeInactive=1` for admin tool. |
| POST | `/api/menus` | Create menu (locally + on Shopify). |
| GET | `/api/menus/[id]` | Single menu with item tree. |
| PUT | `/api/menus/[id]` | Update title/handle/active. |
| DELETE | `/api/menus/[id]` | Delete (refuses on `isDefault=true`). |
| POST | `/api/menus/bulk-sync` | **Bootstrap sync — pulls every live menu from Shopify and upserts locally.** |
| POST | `/api/menus/[id]/sync` | Re-sync a single menu from Shopify. |
| POST | `/api/menus/[id]/push` | Push local menu to Shopify (full tree replace). |
| GET/POST | `/api/menus/[id]/items` | List/create items. |
| PUT/DELETE | `/api/menus/[id]/items/[itemId]` | Update/delete item. |
| POST | `/api/menus/[id]/items/reorder` | Batch position update in a transaction. |

### Admin tools

| Method | Route | What |
|---|---|---|
| POST | `/api/admin/menu-cleanup` | Apply CL1–CL7 menu cleanup tasks from the round 2 mapping. Dry-run or commit. |
| POST | `/api/admin/tag-casing-migration` | Migrate Shopify smart-collection rule conditions to lowercase + hyphen casing. Dry-run by default. |

### Locations, stock, orders, customers, attributes

(Abbreviated — full list in the `src/app/api/` tree.)

| Area | Routes |
|---|---|
| Locations | `GET/POST /api/locations`, `PUT/DELETE /api/locations/[id]` |
| Stock tally | `POST /api/stock-tally`, `POST /api/stock-tally/reconcile` |
| Stock transfers | `POST /api/stock-transfers`, `POST /api/stock-transfers/[id]/complete` |
| Stock import | `POST /api/stock/import` (Excel .xlsx) |
| Inventory reconciliation | `POST /api/inventory/reconcile` |
| Orders | `GET /api/orders`, `GET /api/orders/[id]`, `GET /api/orders/stats`, `POST /api/orders/sync` |
| Customers | `GET/POST /api/customers`, `GET /api/customers/stats`, `POST /api/customers/sync` |
| Attributes | `GET/POST /api/attributes`, `PUT/DELETE /api/attributes/[id]` |
| Images | `POST /api/images` (multipart upload, optional remove.bg background removal) |
| Discount rules | `GET/POST /api/discount-rules`, `PUT/DELETE /api/discount-rules/[id]` |
| Users | `GET/POST /api/users`, `PUT/DELETE /api/users/[id]` |
| Activity logs | `GET /api/activity-logs` |
| Reports | `GET /api/reports` |
| Store health | `GET /api/store-health`, `GET /api/store-health/generate-seo` |
| Backup | `POST /api/backup` |
| Dashboard | `GET /api/dashboard/stats` |

---

## Dashboard pages (28 pages)

| Page | Route | Who can see |
|---|---|---|
| Dashboard home | `/dashboard` | Anyone signed in |
| Products list | `/dashboard/products` | Anyone with `products` feature |
| Add product (wizard) | `/dashboard/products/new` | ADMIN, CATALOG_MANAGER |
| Edit product (V1) | `/dashboard/products/edit/[id]` | ADMIN, CATALOG_MANAGER, DESIGN_MANAGER |
| Edit product (V2) | `/dashboard/products/edit/[id]?next=1` | Same — behind feature flag |
| Orders | `/dashboard/orders` | Anyone with `orders` feature |
| Order detail | `/dashboard/orders/[id]` | Same |
| Customers | `/dashboard/customers` | Anyone with `customers` feature |
| Customer detail | `/dashboard/customers/[id]` | Same |
| Collections | `/dashboard/collections` | Anyone with `collections` feature |
| Collection detail | `/dashboard/collections/[id]` | Same |
| Stock Tally | `/dashboard/stock-tally` | Anyone with `stock_tally` feature |
| Stock Transfers | `/dashboard/stock-transfers` | Anyone with `stock_transfers` feature |
| Backup & Restore | `/dashboard/stock-import` | Anyone with `stock_import` feature |
| Design Queue | `/dashboard/design-queue` | DESIGN_MANAGER + ADMIN |
| Shopify Sync | `/dashboard/shopify` | Anyone with `shopify_sync` feature |
| Reports | `/dashboard/reports` | Anyone with `reports` feature |
| Marketing | `/dashboard/marketing` | Anyone with `marketing` feature |
| Store Health | `/dashboard/store-health` | Anyone with `store_health` feature |
| Activity Logs | `/dashboard/activity-logs` | Anyone with `activity_logs` feature |
| Attributes | `/dashboard/attributes` | Anyone with `attributes` feature |
| Images | `/dashboard/images` | Anyone with `images` feature |
| Locations | `/dashboard/locations` | Anyone with `locations` feature |
| Users | `/dashboard/users` | ADMIN only |
| Admin / Discount Rules | `/dashboard/admin/discount-rules` | ADMIN only |
| Admin / Locations | `/dashboard/admin/locations` | ADMIN only |
| Admin / Shopify | `/dashboard/admin/shopify` | ADMIN only |
| Admin / Attributes | `/dashboard/admin/attributes` | ADMIN only |
| Admin / Users | `/dashboard/admin/users` | ADMIN only |
| Admin / Orphan Audit | `/dashboard/admin/orphans` | ADMIN only |
| **Admin / Storefront Menus** | `/dashboard/admin/menus` | ADMIN, `storefront_menus` feature |
| **Admin / Menu detail** | `/dashboard/admin/menus/[id]` | Same |
| **Admin / Tag Casing Migration** | `/dashboard/admin/tag-casing-migration` | ADMIN, `tag_casing_migration` feature |
| **Admin / Auto Collections** | `/dashboard/admin/auto-collections` | ADMIN, `auto_collections` feature |

The Sidebar (`src/components/Sidebar.tsx`) groups nav items into Main / Operations / Insights / Admin sections and filters each per the user's effective features.

---

## Shopify integration deep dive

### Authentication

The app uses Shopify's **OAuth `client_credentials` grant** (not the auth-code flow):

1. `SHOPIFY_CLIENT_ID` + `SHOPIFY_CLIENT_SECRET` are configured in Railway env vars.
2. On startup, `makeGraphQLRequest()` in `src/lib/shopify.ts` requests an access token from `{SHOPIFY_STORE_URL}/admin/oauth/access_token` with `grant_type=client_credentials`.
3. The token is cached in-memory with expiry tracking. Refreshed 5 minutes before expiry.
4. **Fallback:** if `SHOPIFY_CLIENT_ID`/`SECRET` are missing, the app falls back to a legacy static `SHOPIFY_ACCESS_TOKEN`. (Used in dev / when OAuth is in flux.)
5. API version: `2026-01`. All requests go through `makeGraphQLRequest()` which handles token caching, throttling, and version selection.

### Webhook system

**Endpoint:** `POST /api/webhooks/shopify`  
**Callback URL:** `https://bettervision-inventory-production.up.railway.app/api/webhooks/shopify`  
**HMAC verification:** SHA-256 using `SHOPIFY_CLIENT_SECRET` (falls back to `SHOPIFY_WEBHOOK_SECRET`). In dev, skipped if no secret is configured.

**Handled topics (15):**

| Topic | Handler | Action |
|---|---|---|
| `products/create` | `handleProductCreateUpdate` | Upsert by `shopifyProductId`. Parse brand from tags. Update tag-derived attributes. |
| `products/update` | `handleProductCreateUpdate` | Same. **Excludes** `productName`/`fullModelNo`/`modelNo` to avoid overwriting custom values set during pull. |
| `products/delete` | `handleProductDelete` | Set `status=ARCHIVED`. Does not hard-delete. |
| `inventory_levels/update` | `handleInventoryUpdate` | Currently logs only (TODO: map inventory_item_id back to variant). |
| `orders/create` / `orders/updated` / `orders/fulfilled` / `orders/paid` | `handleOrderCreateUpdate` | Upsert order + line items + customer. Recompute customer aggregates. |
| `orders/cancelled` | `handleOrderCancel` | Set status=CANCELLED with reason + timestamp. |
| `orders/delete` | `handleOrderDelete` | Hard-delete. |
| `customers/create` / `customers/update` | `handleCustomerCreateUpdate` | Upsert customer. |
| `customers/delete` | `handleCustomerDelete` | Hard-delete. |
| `collections/create` / `collections/update` | `handleCollectionCreateUpdate` | Upsert. Skips update if `locallyModified=true`. |
| `collections/delete` | `handleCollectionDelete` | Hard-delete. |
| `fulfillments/*` | Logs only | — |
| `locations/*` | `handleLocationCreateUpdate` / `handleLocationDelete` | Upsert by `shopifyLocationId`. |
| `inventory_items/update` | `handleInventoryItemUpdate` | Sync barcode + SKU updates. |
| `refunds/create` | `handleRefundCreate` | Update parent order's refund total. |
| `app/uninstalled` | Logs only (alarm-worthy) | — |

**Processing model:** Webhook handler returns 200 to Shopify **immediately** (Shopify's 5s timeout would otherwise retry). Actual processing happens in the background via a non-awaited promise.

**Tag parsing:** Products use a **bidirectional prefix-tag** format — `brand_rayban`, `shape_aviator`, `framecolor_black`, etc. The webhook handler's `parseWebhookTags()` reconstructs product attributes from these tags so a Shopify-side edit round-trips back into our typed columns. The `extractBrandFromTags()` function has a compound-word dictionary (`tommyhilfiger` → "Tommy Hilfiger", `rayban` → "Ray-Ban") to handle vendor names that lose their spaces in tags.

### Sync flows

**Shopify → App (Pull)**, `/api/shopify/pull`:

1. `fetchAllProducts()` paginates Shopify GraphQL (50 products/page).
2. Each product is upserted by `shopifyProductId`.
3. Variants upserted by `shopifyVariantId`.
4. Images upserted with `shopifyMediaId` tracking.
5. Inventory tracked at a single "Shopify Online Store" virtual location (code: `SHOPIFY`).
6. Real Shopify locations also synced via `fetchShopifyLocations()`.
7. Brand extracted from tags (prefix `brand_`) with vendor fallback.
8. Category mapped from `productType` field or guessed from title/tags via `guessCategory()`.

**App → Shopify (Push)**, `/api/shopify/sync` + `pushProductsToShopify()`:

1. `createProduct()` sends `productCreate` mutation with title, descriptionHtml, tags, status, productType, seo, productOptions, vendor, productCategory, templateSuffix.
2. Variants are added via a **separate** `productVariantsBulkCreate` mutation with `strategy: REMOVE_STANDALONE_VARIANT` (Shopify 2025-01+ removed `variants` from `ProductInput`).
3. SKU now lives on `inventoryItem.sku` (not top-level — Shopify 2026 change).
4. `updateProduct()` sends `productUpdate` for already-synced products.
5. `updateVariantPrice()` uses `productVariantsBulkUpdate` (Shopify removed `productVariantUpdate`).
6. Inventory operations: `updateInventory()` (adjust delta), `setInventory()` (absolute).
7. Every push writes a `SyncLog` row.

### Metafields + theme integration

`setProductMetafields()` writes custom metafields used by the storefront theme. Round-2 metafields:
- `bv.label` + `bv.label_color` — badge overlay on product cards (snippet `snippets/badge-overlay.liquid`).
- `bv.menu_image` + `bv.menu_badge` — mega-menu thumbnail + badge text (snippet `snippets/mega-menu-image-badge.liquid`).
- `bv.preorder_ship_date` — estimated ship date on pre-order template.
- `bv.face_shape_pairings` — face-shape pairing copy on `collection.shape.liquid`.
- `bv.editorial_image` + `bv.editorial_tagline` — hero on `collection.gender.liquid`.
- `bv.banner_image` + `bv.short_description` — brand-collection hero.
- `bv.sale_ends` — date_time metafield for the Sale collection countdown banner.

### Image upload

`uploadFileToShopify()` stages files via Shopify's staged upload (`stagedUploadsCreate` mutation) then attaches them to products. `/api/images` is the app-side entry point — accepts multipart uploads, handles client-side compression (≤4.5 MB target, ≤2048px), supports optional remove.bg background removal.

The shared utility `src/lib/imageUpload.ts` exposes `uploadImage(file)` and `uploadImages(files[])` with **structured failures** (`level: "hard" | "soft"`) so callers can show a modal interruption on hard failures and an inline banner on soft failures. Wired across New Product, Edit V1, Edit V2, and Design Queue.

---

## Product workflows (the catalogue loop)

### 1. Add product (cataloguer)

1. Cataloguer opens `/dashboard/products/new` — the **Identify wizard** asks for Brand + Model + Colour + Size + Category.
2. The wizard runs `/api/products/check-duplicate` to see if the (brand, modelNo) pair exists. If yes, prompts to add a variant instead of a new product.
3. The full Add Product form opens with the wizard fields pre-filled.
4. Per-category attribute inputs appear (Sunglasses → Shape, Frame Material, Lens Colour, Polarization, UV Protection, …; Watches → Movement, Strap Material, Dial Color, Water Resistance, …).
5. **Auto-generation runs in real-time** in the right sidebar — Title, SKU, SEO Title, SEO Description, Page URL, Tags update as the cataloguer types.
6. Variants are added in the VariantManager (color × size for eyewear, power × pack-size for contact lenses, strap-color × dial-color × case-size for watches).
7. Images are uploaded — stored as `ProductImage` rows with `role=RAW`.
8. Cataloguer clicks **Save as Draft** (status=DRAFT) or **Publish to Shopify** (status=PUBLISHED).
9. Because the user is a `CATALOG_MANAGER`, `imageDesignStatus` is set to `PENDING_DESIGN` automatically. The product appears in the Design Queue. (Per round 2 mapping F2: this applies to all users now, not just cataloguers.)

### 2. Design (designer)

1. Designer opens `/dashboard/design-queue` — sees all products in PENDING_DESIGN.
2. For each product, downloads raw images (the cataloguer's phone snaps), edits in Photoshop / Affinity, uploads back.
3. Designer clicks **Mark as ready** → calls `POST /api/products/[id]/design-upload`.
4. `imageDesignStatus` flips to READY. RAW images are replaced by EDITED ones. If `status=PUBLISHED`, the product pushes to Shopify.

### 3. Inventory (store staff)

1. Store staff opens `/dashboard/stock-tally`, picks their store location (Bokaro / Pune).
2. Scans every product on the shelf with a barcode scanner — each scan appends to the tally list. Stock barcode lookup uses **both** `ProductVariant.barcode` (GTIN/UPC) **and** `ProductVariant.storeBarcode` (local barcode introduced in round 2 mapping 4.2).
3. Clicks **Compare** → backend computes matched / minor-diff / major-diff / unmatched buckets.
4. Clicks **Reconcile** on a bucket → updates `VariantLocation.quantity` for that location.
5. The next `/api/shopify/pull` or webhook syncs the new quantity back.

### 4. Stock transfers (Bokaro → Pune)

1. Manager opens `/dashboard/stock-transfers`, creates a transfer with line items (variant + qty).
2. Status: PENDING (created) → IN_TRANSIT (dispatched) → COMPLETED (received) | CANCELLED.
3. On completion, both locations' quantities are adjusted atomically inside a `prisma.$transaction`.

### 5. Orders + customers (read-only)

Orders and customers are pulled from Shopify (full pull on demand; incremental via webhooks). Used for reporting, customer lookup, marketing analytics. **Never created in the app.**

---

## Auto-generation engine

`src/lib/autoGenerate.ts` — the SEO surface generator. Every product save runs through it.

**Outputs:**
- `title` — `<Brand> <SubBrand> <ModelNo> <ColorCode> <FrameSize> <Label> <Singular SEO Noun>` (e.g. "Ray-Ban Aviator RB3025 002/32 58 Classic Sunglass").
- `sku` — Brand prefix + model + color + size (e.g. `RB-3025-00232-58`).
- `seoTitle` — 55-70 chars, Title Case, brand + model + 1-2 best-selling attributes + product type. Auto-generated by Claude Haiku 4.5 with prompt-caching (`src/lib/seoGenerator.ts`).
- `seoDescription` — 140-160 chars, natural prose, mentions brand + key attributes + buy-now CTA. Haiku-generated.
- `pageUrl` (handle) — lowercase, hyphenated slug.
- `tags` — bidirectional `prefix_value` CSV used by Shopify smart-collection rules.
- `htmlDescription` — **legacy Excel format** (round 2 Option A): `<h4>Product Details</h4>` → marketing intro paragraph → `<h4>Technical Specifications</h4>` → `<h4>General Information</h4>` → `<h4>Warranty</h4>` with link to `bettervision.in/pages/warranty`.

**Per-category marketing intros (`buildMarketingIntro()`):** 11 templates, one per category. Each follows the legacy pattern (engaging verb + brand/model → look/spec line → suitability → trust + CTA → Better Vision). E.g.:

> **Sunglass:** "Reflect your style with the Ray-Ban RB3025 002/32 58 Aviator Sunglass. This Sunglass comes in Gold colour with G-15 Green lenses, with a classic aviator look that turns every step into a moment. Suitable for Unisex, this model comes in size 58. Polarized, 100% UV Protection. Enjoy unbeatable discounts and guaranteed authentic products. Discover your perfect pair at Better Vision today!"

> **Watch:** "Discover everyday craftsmanship with the Fossil FS5060 Watch. Black dial paired with a leather strap, powered by precise quartz movement, built to handle 5ATM water resistance. Perfect for Men. Authentic, fully warrantied, and shipped pan-India. Order from Better Vision."

**Tag emission rules (round 2 mapping):**

| Attribute | Emits as tag? | Why |
|---|---|---|
| brand | ✅ `brand_rayban` | Filterable on storefront. |
| subBrand | ❌ (round 1 10.1: title only) | Used in title; not a filter axis. |
| label | ❌ (round 1 10.2: badge only) | Storefront badge overlay, not a tag. |
| countryOfOrigin | ❌ (round 1 4.6: field only) | Shopify product field for shipping compliance. |
| weight | ❌ (round 1 5.2: field only) | Shopify `inventoryItem.measurement.weight`. |
| bridge / templeLength | ✅ (round 2 U3: tags) | Filterable on storefront. |
| rxable | ✅ `rxable_yes` / `rxable_no` (round 2 U5) | Storefront reads to render prescription module. |
| shape, frameMaterial, frameType, lensMaterial, lensColour, tint, polarization, uvProtection, gender, warranty, … | ✅ | All filterable. |

**Discount calculation:** `calculateDiscountedPrice(mrp, category, rules, brand, subBrand)` — looks up the most specific applicable rule (subBrand > brand > category), applies the percentage. Spectacles 10%, Sunglasses 12.5%, Solutions 11% by default. Editable from `/dashboard/admin/discount-rules`.

---

## Categories (12 of them)

| Key | Display label | SEO noun | Default RX-able | Variant options |
|---|---|---|---|---|
| `SPECTACLES` | Spectacles | Optical Frame | ON | Color × Size |
| `SUNGLASSES` | Sunglasses | Sunglass | OFF | Color × Size |
| `CLIP_ON_FRAMES` | Clip-On Frames | Clip-On Frame | ON | Color × Size |
| `READING_GLASSES` | Reading Glasses | Reading Glasses | editable | Color × Power × Size |
| `COMPUTER_GLASSES` | Computer Glasses | Blue-Light Glass | editable | Color × Size |
| `SAFETY_GLASSES` | Safety Glasses | Safety Goggle | OFF | Color × Size |
| `CONTACT_LENSES` | Contact Lenses | Contact Lenses | ON | Power × Color × Pack Size |
| `COLOR_CONTACT_LENSES` | Color Contact Lenses | Color Contact Lens | OFF | Power × Color × Pack Size |
| `SMARTGLASSES` | Smartglasses | Smartglass | editable | Frame Color × Lens Color × Size |
| `WATCHES` | Watches | Watch | N/A | Strap Color × Dial Color × Case Size |
| `SMARTWATCHES` | Smartwatches | Smartwatch | N/A | Strap Color × Case Size |
| `ACCESSORIES` | Accessories | Accessory | N/A | Single-SKU default, Color variant if applicable |

Each category has its own attribute applicability (`src/lib/categoryAttributes.ts` → `CATEGORY_ATTRIBUTES`) and section layout (`src/lib/products/categorySpecs.ts` → `CAT_SPECS`). Adding a new category = 3 file edits:

1. `src/lib/categories.ts` — add `{ key, label, seoNoun }` to `CATEGORIES`.
2. `src/lib/categoryAttributes.ts` — define the attribute list for the new category.
3. `src/lib/products/categorySpecs.ts` — define the section ordering for Edit V2.

---

## Admin tools

### Storefront Menus (`/dashboard/admin/menus`)

Drag-drop hierarchy editor for every Shopify storefront menu. Built by an isolated agent (the "in-app menu editor"). Features:

- **Bootstrap sync**: green "Sync from Shopify" button bulk-pulls every live menu via `POST /api/menus/bulk-sync` — required on first visit since the local Menu table starts empty.
- **DnD tree** in `src/components/MenuItemTree.tsx`. No external DnD lib; raw HTML5 drag events. Y-position-based drop zones (top third = before, bottom third = after, middle = nest as child). Guards: refuses self-drop, descendant-cycle drops (hotfix UI#1), and inside-drops onto leaf types (PRODUCT/HTTP/ARTICLE/SHOP_POLICY/SEARCH — hotfix UI#2).
- **Item editor** in `src/components/MenuItemEditor.tsx`. Resource picker that auto-completes against Collections / Products / Pages or accepts free-form URLs. Local-overlay fields (`iconUrl`, `bannerUrl`, `badgeText`, `badgeColor`, `pinnedToTop`) are stored locally and preserved across resyncs.
- **Push to Shopify**: writes back via `menuUpdate` (wholesale tree replace; Shopify's contract). Returns canonical Shopify item IDs which we map back to local rows for idempotent future pushes.
- **Sync from Shopify** (per-menu): destructive — deletes local items and recreates from Shopify. Skipped if `locallyModified=true` with a warning so unpushed edits aren't clobbered.
- **Brand alphabet bucketing** (round 2 M2): `bucketBrandIntoAlphabet(title)` returns "A-H" | "M-Q" | "R-Z". Exported for the catalogue flow that auto-adds new brand-collection menu items.

### Tag-Casing Migration (`/dashboard/admin/tag-casing-migration`)

One-shot migration to lowercase Shopify smart-collection rule conditions (round 2 mapping C6).

The store has 1,160+ collections with rule conditions like `TAG=gender_Women` (Title-Case + space). Our app's `slugifyTagValue()` emits `gender_women` (lowercase + hyphen). New products pushed from the app land with the lowercase tag and DON'T match the existing rules — meaning they'd silently disappear from collections.

The migration page: dry-run shows the plan (108 collections need migration, 109 rule conditions changing). Commit applies the changes via `collectionUpdate` mutations. The actual logic in `src/lib/collections/tagCasingMigration.ts`. **Empty-value guard** (hotfix TS#6): when a rule's value would slugify to empty (e.g. trailing `_`), the migration leaves the original condition untouched rather than silently changing the rule's matching semantics.

### Auto Collections (`/dashboard/admin/auto-collections`)

Auto-generates brand × category × shape × gender smart collections per the per-category plan in `src/lib/collections/perCategoryPlan.ts`. Dry-run first to review the plan, then commit.

The naming convention is `{brand}-{category}` (round 1 mapping C5). Theme template suffix is auto-assigned per the per-category plan (e.g., brand collections → `collection.brand.liquid`; shape → `collection.shape.liquid`; sale → `collection.sale.liquid`).

### Orphan Audit (`/dashboard/admin/orphans`)

Products with `shopifyProductId IS NULL` — local-only products that never reached Shopify. The page shows:

- Pushable orphans: ready to push (have brand + modelNo + price + at least one variant or image).
- Unpushable orphans: missing a required field. Reason codes: `missing_brand`, `missing_modelNo`, `missing_title`, `no_price`, `no_variants_no_images`.

Bulk-push action sends pushable orphans through the standard `pushProductsToShopify()` flow.

### Other admin tools

- **Discount Rules** — category-based discount % editor.
- **Locations** — manual location create + Shopify sync.
- **Users** — per-user role + feature toggle UI.
- **Attributes** — attribute type + option editor (dropdown values shown in the Add Product form).
- **Shopify** (`/dashboard/admin/shopify`) — alternative entry to the Shopify Sync page with admin-only controls.

---

## Storefront theme integration

The repo includes a `theme-snippets/` folder with 10 Liquid files we've installed into the UNPUBLISHED copy of the live Boost theme (theme ID `gid://shopify/OnlineStoreTheme/153462898937`).

### Snippets

- `snippets/badge-overlay.liquid` — Renders a small badge overlay on product card images. Reads `product.metafields.bv.label` + `product.metafields.bv.label_color`. Self-gating: returns nothing when label is empty so callers can include it unconditionally.
- `snippets/mega-menu-image-badge.liquid` — Image + badge for mega-menu items. Reads `link.object.metafields.bv.menu_image` + `bv.menu_badge`. Fallback to `/assets/menu-<handle>.jpg`.
- `snippets/rx-module.liquid` — Full prescription capture form (OD/OS SPH/CYL/AXIS/ADD, single/dual PD, photo upload, "skip" / "use saved" alternatives). Self-gating on `product.tags contains 'rxable_yes'`. Writes to cart line-item properties (`_rx_*`).

### Templates

- `templates/product.preorder-bv.liquid` — Pre-order layout. Replaces Add-to-Cart with "Order Now (ships when available)". Shows `bv.preorder_ship_date`. (Suffix is `preorder-bv` not `preorder` — Shopify already had a JSON template at `templates/product.preorder.json` we couldn't replace via the connector.)
- `templates/product.appointment.liquid` — Hide price; show "Request pricing & availability" enquiry form via Shopify's `{% form 'contact' %}`.
- `templates/product.rx-required.liquid` — Add-to-Cart blocked until prescription captured. Wraps `rx-module` snippet.
- `templates/collection.brand.liquid` — Brand-specific collection page with banner hero + shape/gender/material/polarized/price filters.
- `templates/collection.shape.liquid` — Shape-specific page with face-shape pairings dictionary + brand/gender/material/polarized/price filters.
- `templates/collection.gender.liquid` — Gender-specific page with editorial hero + lifestyle copy + shape/brand/material/polarized/price filters.
- `templates/collection.sale.liquid` — Sale page with discount-% sort + countdown banner + flexible filters.

### Installation

Theme files are upserted via Shopify's `themeFilesUpsert` mutation, body type `URL` pointing at GitHub raw URLs of this repo. The connector blocks `themeFilesDelete` for safety (you'd take down the live storefront), so we never delete — only upsert. To remove a file, do it from the Shopify theme editor.

### Wire-ups (manual, theme repo work)

Snippets need to be `{% render %}`'d from the theme's existing files:

- `snippets/badge-overlay` — render inside `snippets/product-card-grid.liquid` and `sections/main-product.liquid`.
- `snippets/rx-module` — render inside `sections/main-product.liquid`.
- `snippets/mega-menu-image-badge` — render inside `sections/header.liquid` for each menu link.

Plus the storefront theme needs to register the new theme templates so admin can select them in the Theme Template dropdown.

---

## Environment variables

### Production (Railway)

| Variable | Required? | Purpose |
|---|---|---|
| `DATABASE_URL` | ✅ | Postgres connection string. Railway provisions this automatically. |
| `NEXTAUTH_SECRET` | ✅ | JWT signing secret. Generate with `openssl rand -base64 32`. |
| `NEXTAUTH_URL` | ✅ | Public URL (e.g. `https://bettervision-inventory-production.up.railway.app`). |
| `SHOPIFY_STORE_URL` | ✅ | `bokaro-better-vision.myshopify.com` |
| `SHOPIFY_CLIENT_ID` | ✅ (or token) | OAuth `client_credentials` app ID. |
| `SHOPIFY_CLIENT_SECRET` | ✅ (or token) | OAuth `client_credentials` app secret. Also used as webhook HMAC secret. |
| `SHOPIFY_ACCESS_TOKEN` | optional | Legacy static token fallback. Used if `CLIENT_ID`/`SECRET` are missing. |
| `SHOPIFY_WEBHOOK_SECRET` | optional | Explicit webhook HMAC secret. Falls back to `CLIENT_SECRET` if not set. |
| `ANTHROPIC_API_KEY` | optional | Required for AI SEO generation (Claude Haiku 4.5). |
| `REMOVEBG_API_KEY` | optional | remove.bg API for background removal on uploaded product images. |
| `SEED_ADMIN_PASSWORD` | optional | Custom password for the seeded admin user. Default `admin123`. |

### Local dev (`.env`)

```
DATABASE_URL="file:./dev.db"
NEXTAUTH_SECRET="bettervision-secret-change-in-production"
NEXTAUTH_URL="http://localhost:3000"
SHOPIFY_STORE_URL="bokaro-better-vision.myshopify.com"
SHOPIFY_ACCESS_TOKEN="shpat_..."   # ask Avinash for the legacy token
REMOVEBG_API_KEY=""
ANTHROPIC_API_KEY=""               # optional, only for AI SEO
```

Local dev uses SQLite (`file:./dev.db`) so you don't need Postgres running. The Prisma schema is portable between SQLite and Postgres for all our usage.

---

## Common tasks

### Add a new attribute (e.g. "Lens Coating")

1. Add an `AttributeType` row via `/dashboard/attributes` (UI).
2. Add the attribute to `src/lib/categoryAttributes.ts` → `ATTRIBUTES` map with `hasColumn: false, tag: true`.
3. Add the key to `CATEGORY_ATTRIBUTES[<category>]` arrays for every category it applies to.
4. (Optional) Add a hardcoded form input in `src/app/dashboard/products/new/page.tsx` if it warrants its own slot.
5. Deploy. The attribute appears in the Add Product form's dynamic Category Attributes section, emits as a tag on save, round-trips on Shopify pull.

### Add a new product category

1. `src/lib/categories.ts` — add `{ key, label, seoNoun }` to `CATEGORIES`.
2. `src/lib/categoryAttributes.ts` — add a `CATEGORY_ATTRIBUTES[NEW_KEY] = [...]` entry.
3. `src/lib/products/categorySpecs.ts` — add `CAT_SPECS[NEW_KEY] = { label, sections: [...] }`.
4. `src/lib/products/validation.ts` — add category-specific `getIssues()` rules if any field is required.
5. `src/lib/autoGenerate.ts` — add a `buildMarketingIntro()` case for the new category.
6. `src/components/VariantManager.tsx` — add a `variantSpecForCategory()` case if variant options differ.
7. Deploy.

### Pull every product from Shopify

```
POST /api/shopify/pull
```

Or via UI: `/dashboard/shopify` → "Pull All Products from Shopify". This also auto-syncs Shopify locations.

### Push pending products to Shopify

```
POST /api/shopify/sync
Body: { syncedMode: "skip" }  // create-only, default
       { syncedMode: "update" } // also re-push edits for already-synced products
```

Or via UI: `/dashboard/shopify` → "Push Unsynced Products to Shopify".

### Reset the Failed counter on Shopify Sync

```
POST /api/shopify/sync-history
Body: { mode: "failed-older-than", days: 1 }
```

Or via UI: `/dashboard/shopify` → "Clear stale failures" button.

### Seed an empty database

```
GET /api/seed
```

Creates the admin user, seeds AttributeTypes (brand, shape, frameMaterial, etc.) and AttributeOptions, creates default DiscountRules.

### Run the menu cleanup (round 2 mapping CL1-CL7)

```
POST /api/admin/menu-cleanup
Body: { dryRun: true }   // see the plan
       { commit: true }   // apply CL1-CL7
```

Or via UI: invoke from a custom button on the menus page (no dedicated UI yet).

### Run the tag-casing migration

```
POST /api/admin/tag-casing-migration
Body: { dryRun: true }
       { commit: true }
```

Or via UI: `/dashboard/admin/tag-casing-migration`.

---

## Troubleshooting

### "Not Connected" banner on Shopify Sync page (transient)

If you hard-reload `/dashboard/shopify` during a Railway cold start, the connection banner briefly shows "Not Connected" / 0/0/0 while the `/api/shopify/status` request is in flight. As of commit `992e6c2` this transient state is now labelled **"Checking connection…"** with an amber pulse instead. If you still see "Not Connected" after waiting 5+ seconds, the API is failing — check Railway logs.

### "99 Failed" / inflated failure counter

The old `/api/shopify/status` endpoint counted SUCCESS/FAILED across the last 100 SyncLog rows. A bad batch could lock the counter for days. As of `931d2b1` the counter is scoped to the last 24 hours and a **"Clear stale failures"** button exists for one-click reset.

### Page shows "No menus found"

The local Menu table is empty. Click **"Sync from Shopify"** on `/dashboard/admin/menus` (bootstrap sync added in `c9e661d`).

### Image upload silently does nothing

Pre-`2cf9ef4`, the image upload handlers read `data.url` from `/api/images` but the endpoint returned `data.data.urls.original`. As of `2cf9ef4` all four callers (Add Product, Edit V1, Edit V2, Design Queue) use the shared `uploadImage()` utility with structured failure surfaces (modal + banner + activity log).

### Push to Shopify fails with "Field is not defined on ProductInput"

Shopify removed `variants` from `ProductInput` in 2025-01+. The fix landed in `75ca077`: `createProduct()` is now a two-step `productCreate` + `productVariantsBulkCreate` with `strategy: REMOVE_STANDALONE_VARIANT`. If you see this error, you're on an older deploy — push to main.

### Collections sync hangs forever

Pre-`a39d180`, the sync did 1,160 sequential findUnique + update calls, hitting Railway's request timeout. As of `a39d180` it's parallel-chunked (chunks of 20) and pre-fetches `locallyModified` flags in one bulk findMany. Total runtime ~15s for the full 1,160 collections.

### TypeScript "params.id is now a Promise" error

Next.js 15 made dynamic route params async. Every `[id]` route handler in this repo uses:

```ts
export async function GET(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  // ...
}
```

If you see this error you're touching an older handler that wasn't migrated. Reference any existing route file as a template.

---

## Recent work — round-by-round changelog

Reverse-chronological. Each commit is a single deployable unit on Railway.

| Commit | Date | What |
|---|---|---|
| `931d2b1` | 2026-05-09 | Shopify Sync: real product-state numbers + 24h scope + Clear-stale-failures button |
| `992e6c2` | 2026-05-09 | Shopify Sync: prevent transient 'Not Connected' + add Refresh button + clarify Failed label |
| `501a0e1` | 2026-05-09 | Collections page: bump LOAD_LIMIT 100 → 2500 + "X of Y in DB" indicator |
| `a39d180` | 2026-05-09 | Collections sync: parallel chunked upsert + bulk pre-fetch (60s → 15s) |
| `c9e661d` | 2026-05-09 | Menus: add Sync-from-Shopify bootstrap endpoint + button |
| `892cfc3` | 2026-05-09 | Round 2 hotfixes: 9 bugs from 4-agent review (security, data loss, UI guards) |
| `51a0abb` | 2026-05-09 | Theme templates: drop `{% schema %}` blocks (templates can't have schema; sections only) |
| `716d09f` | 2026-05-09 | Theme templates: fix Liquid alt-filter syntax + rename `product.preorder.liquid` to `preorder-bv` (JSON conflict) |
| `d70cdcb` | 2026-05-09 | **Collections + Menu round 2:** schema, smart-collection auto-gen, in-app menu editor, tag casing migration, theme templates |
| `666a264` | 2026-05-09 | Round 2 batch: schema + per-cat attributes + RX toggle + variant 3-opt + Shopify templateSuffix |
| `8cc1024` | 2026-05-09 | Description rewrite (Option A) + per-category SEO nouns + tag rule cleanup |
| `2cf9ef4` | 2026-05-09 | Image upload hotfix (F1) + design queue for all users (F2) |
| `1cc5116` | 2026-05-08 | Edit Product redesign: Polaris shell behind `?next=1` flag |
| `cea0a35` | 2026-05-08 | Upgrade to Next 15 + React 19; migrate dynamic routes to async params |
| `280794c` | 2026-05-08 | Shopify schema audit: fix removed `productVariantUpdate`; delete broken-unused `createVariant` |
| `75ca077` | 2026-05-08 | Shopify `createProduct`: split into `productCreate` + `productVariantsBulkCreate` |
| `f90eed5` | 2026-05-08 | Edit page Publish to Shopify: works for orphans (first-time push) + surfaces real Shopify error |
| `ccbf987` | 2026-05-08 | Fix: sidebar Search button + Ray-Ban edit save |
| `df8e189` | 2026-05-08 | Polaris body styling: Orders + Customers (KPI cards, chip rail, table, pagination) |
| `b218bb7` | 2026-05-08 | Login: remove demo creds line; Orphans: per-product failure modal |
| `ae3acc8` | 2026-05-08 | Orphans: clickable title + Edit action button per row |
| `f506e95` | 2026-05-08 | Topbar migration batch 2: 8 more dashboard pages |
| `2d7ecf9` | 2026-05-08 | Topbar migration batch 1: Orders, Customers, Collections, Locations, Attributes |
| `8cbc9a8` | 2026-05-08 | Identify wizard: 4-field match (Brand+Model+Colour+Size) for variant-level routing |
| `84b3924` | 2026-05-08 | Products list: Polaris redesign with saved-views, filter rail, sticky bulk bar |

### Round 1: product mapping (May 8)

Driven by `add-product-mapping.html` (interactive comment-collection tool the user filled out across categories). Findings:
- 99 of 100 Shopify pushes were failing due to **schema drift** — Shopify 2025-01+ removed `productVariantUpdate` and changed `productCreate` to disallow `variants` inline.
- Image upload was silently broken — all 4 callers read `data.url` but the endpoint returned `data.data.urls.original`.
- Tags emitted by the app didn't match the Shopify smart-collection rule conditions because of case + delimiter differences.

Round 1 shipped: image upload utility with structured failures (modal + banner + activity log), Shopify schema fixes (split product create / use bulk-update mutation), per-category Edit V2 shell, Add Product wizard with duplicate detection, Polaris-aligned redesign across Topbars + Products list + Orders + Customers.

### Round 2: collections + menus (May 9)

Driven by `collections-and-menu-mapping.html` — interactive mapping tool covering both Shopify collections and storefront menus. Findings:
- The store had **1,160 collections** with mixed-case tag-rule conditions (`gender_Women`) that mismatched our slugifier output (`gender_women`) — new products would have silently dropped out of those collections.
- The store had **5 menus** with whitespace bugs, type mismatches (HTTP type pointing to a collection URL), and duplicate items.
- No in-app menu editor existed — everything had to be done in Shopify admin.

Round 2 shipped (across `d70cdcb` and follow-ups):
- **Schema additions**: `Product.rxable`, `Product.themeSuffix`, `Product.categorySpecific Json?`, `ProductVariant.storeBarcode`, `ProductVariant.power/packSize/cylinder/axis/strapColor/caseSize/dialColor/extras Json?`. New `COLOR_CONTACT_LENSES` category. New `Menu` + `MenuItem` models. Collection extension columns (`bannerImage`, `shortDescription`, `sortPriority`, `metafields`, `autoSource`, `categoryAnchor`).
- **Per-category VariantManager**: category-aware option labels + extras row. Reading Glasses → Color × Power × Size, Contact Lenses → Power × Color × Pack, Watches → Strap Color × Dial Color × Case Size.
- **In-app menu editor**: drag-drop tree + resource picker + per-menu push to Shopify + bulk-sync from Shopify.
- **Auto-collection generator**: brand × category × shape × gender smart collection generator (dry-run + commit).
- **Tag-casing migration**: one-shot migration tool to lowercase the existing 1,160 collection rules.
- **Theme templates**: 10 Liquid files (3 snippets + 7 templates) installed into the unpublished Copy theme.

### Round 2 hotfix bundle (May 9)

4 parallel review agents found 18 bugs across the round 2 codebase. Top 9 fixes shipped in `892cfc3`:

- **H1** (security) — `tag-casing-migration` honours `dryRun:true` flag.
- **H2** (security) — `menu-cleanup` no longer creates the `cases-bags` Shopify collection during dry-run.
- **TS#2** (data loss) — `cleanupExecutor.applyMove` no longer drops the source item when the target parent doesn't exist; pre-locates target before removing.
- **TS#6** (silent corruption) — `migrateCondition` no longer drops the trailing underscore on empty-value tags.
- **UI#1+#2** — MenuItemTree refuses cycle drops (descendant guard) + inside-drops onto leaf types.
- **UI#3** — V2 `THEME_TEMPLATES` uses `preorder-bv` not `preorder` (Shopify had a JSON conflict).
- **UI#5** — V2 form null-response guard on `/api/products/[id]`.
- **UI#11** — V2 `saveDraft` concurrency lock (manual ⌘S can't race the debounced auto-save).

### Pre-round-1 history

Older commits (March-April 2026) shipped: NextAuth credentials provider, Prisma schema baseline, Shopify GraphQL client, Add Product / Edit Product / Stock Tally flows, customer + order pull, design queue model, image upload pipeline, attribute system.

---

## Known issues / technical debt

(From the round 2 hotfix review + ongoing work.)

### High priority

- **~4,191 local-only products** with `shopifyProductId IS NULL`. Created via `POST /api/products` but never pushed to Shopify. The Orphan Audit page exists; bulk-pushable subset has been pushed. The unpushable remainder needs manual review.
- **Inventory is aggregated, not per-location** during Shopify pull. We track a single "Shopify Online Store" virtual location with `sp.totalInventory`. Per-location inventory levels from Shopify's actual locations are NOT synced. Stock Tally works against local counts. To get accurate per-location counts from Shopify, query `inventoryLevels` per location via the Shopify Inventory API.
- **Customer sync overwrites order financials**. Customer sync sets `ordersCount` and `totalSpent` from Shopify's customer API, which can conflict with values calculated by orders sync when not all orders are synced yet. Workaround: always run `recomputeCustomerAggregates()` after a full orders pull.

### Medium priority

- **Theme template `product.preorder` rename**. The renamed file is `preorder-bv.liquid`. To use `preorder` instead, delete `templates/product.preorder.json` manually in Shopify admin then re-run the upsert mutation. The connector blocks `themeFilesDelete` for safety.
- **Theme file wire-ups are manual**. The badge-overlay / rx-module / mega-menu-image-badge snippets still need `{% render %}` calls added to the theme's `product-card-grid.liquid`, `main-product.liquid`, and `sections/header.liquid`. See `theme-snippets/README.md`.
- **Menu cleanup UI page**. The endpoint `/api/admin/menu-cleanup` exists; no dedicated page yet. Trigger via curl or extend the menus list page with a one-click button.
- **No SyncLog retention policy yet**. Rows accumulate forever. The `/api/shopify/sync-history` endpoint supports `mode: "all-older-than"` for retention; not yet scheduled.

### Low priority

- **Legacy variant fields on Product**. `Product.colorCode` / `frameColor` / `frameSize` / `weight` / `bridge` / `templeLength` / `lensColour` / `tint` are nullable columns that duplicate `ProductVariant` data. We don't write to them anymore (round 2 mapping U4 — moved to variant level) but they still exist for backward compat with older rows.
- **`Customer.metafields.bv.saved_rx`** — referenced by `snippets/rx-module.liquid` (for "Use my saved prescription") but never written by the app. Storefront work needed.
- **Multi-channel listing**. Optorium cross-lists on Tata Cliq Luxury + Myntra; we don't. Worth exploring.
- **No image CDN**. Product images are stored as URLs (Shopify CDN or direct links). No local optimization pipeline.

---

## Architectural decision records

Inline ADRs for non-obvious choices.

### Why `?next=1` flag for Edit V2 instead of full migration?

The V1 edit page is 1,000+ lines with bespoke state. Migration risk was high. Flag gating let us ship V2 incrementally — first the shell + auto-save, then the per-category sections, then the variant manager. V1 stays available as a fallback while V2 is dogfooded. The flag flip + V1 deletion is step 6 of the migration plan (still pending).

### Why no real DnD library in MenuItemTree?

Considered react-dnd, react-beautiful-dnd, dnd-kit. All are 50KB+ and would have added complexity for one tree. Raw HTML5 drag events + Y-position drop zones gave us 90% of the UX in ~30 lines.

### Why JSON columns for `extras` / `categorySpecific` instead of typed columns per category?

Adding a new attribute would require a Prisma migration per attribute. With JSON we can iterate without schema changes. Postgres handles JSON queries well enough for our scale (10s of attributes per product, 1000s of products). Promote a JSON key to a real column when it becomes critical for indexing or filtering.

### Why `prisma db push --accept-data-loss` on Railway startup instead of migrate?

Schema changes are frequent during early product. Migration files would generate a lot of noise. `db push` syncs the schema in one step. The `--accept-data-loss` flag accepts destructive changes (dropping columns, renaming) without prompting — risky for production but acceptable here because additive-only changes are the default and we always test against a staging branch before merging.

### Why `client_credentials` OAuth instead of code grant?

Code grant requires a user-facing install flow. `client_credentials` is server-to-server — we get a token without a user pressing "Install". The trade-off: we can't multi-tenant the app (one BetterVision install, one Shopify store). That's fine for now.

### Why parallel chunks of 20 in `collections/sync` instead of `prisma.$transaction([...])`?

`$transaction` in a single `Promise.all` over 1,160 rows would hold an aborted-on-error transaction for the whole batch — one bad row aborts everything. Chunks of 20 give us partial progress (failures are isolated to their chunk) and faster failure surfacing.

### Why `extras Json?` keyed on shopifyItemId (not on local id) for menu items?

When a menu is re-synced, every local MenuItem row gets recreated. If we keyed local overlay fields on local id, they'd be lost on every sync. shopifyItemId is stable across syncs (assuming the item hasn't been deleted on Shopify), so keying overlays on it preserves user edits like icon URLs and badge text.

---

## Repository structure

```
.
├── prisma/
│   └── schema.prisma                  # 20+ models, Postgres + Prisma 6
├── public/
│   └── uploads/                       # locally-uploaded images (ephemeral on Railway)
├── src/
│   ├── app/
│   │   ├── api/                       # 66 API route handlers
│   │   │   ├── admin/
│   │   │   │   ├── menu-cleanup/
│   │   │   │   └── tag-casing-migration/
│   │   │   ├── attributes/
│   │   │   ├── auth/
│   │   │   ├── backup/
│   │   │   ├── collections/
│   │   │   │   ├── [id]/
│   │   │   │   ├── auto-generate/
│   │   │   │   └── sync/
│   │   │   ├── customers/
│   │   │   ├── dashboard/
│   │   │   ├── discount-rules/
│   │   │   ├── images/
│   │   │   ├── inventory/
│   │   │   ├── locations/
│   │   │   ├── menus/
│   │   │   │   ├── [id]/
│   │   │   │   │   ├── items/
│   │   │   │   │   ├── push/
│   │   │   │   │   └── sync/
│   │   │   │   └── bulk-sync/
│   │   │   ├── orders/
│   │   │   ├── products/
│   │   │   ├── reports/
│   │   │   ├── seed/
│   │   │   ├── shopify/
│   │   │   │   ├── auth/
│   │   │   │   ├── pull/
│   │   │   │   ├── refresh-token/
│   │   │   │   ├── scopes/
│   │   │   │   ├── status/
│   │   │   │   ├── sync/
│   │   │   │   ├── sync-history/
│   │   │   │   └── webhooks/
│   │   │   ├── stock/
│   │   │   ├── stock-tally/
│   │   │   ├── stock-transfers/
│   │   │   ├── store-health/
│   │   │   ├── users/
│   │   │   ├── variants/
│   │   │   └── webhooks/
│   │   ├── dashboard/                 # 28 dashboard pages
│   │   │   ├── admin/
│   │   │   │   ├── auto-collections/
│   │   │   │   ├── discount-rules/
│   │   │   │   ├── menus/
│   │   │   │   │   └── [id]/
│   │   │   │   ├── orphans/
│   │   │   │   ├── shopify/
│   │   │   │   ├── tag-casing-migration/
│   │   │   │   └── users/
│   │   │   ├── activity-logs/
│   │   │   ├── attributes/
│   │   │   ├── collections/
│   │   │   ├── customers/
│   │   │   ├── design-queue/
│   │   │   ├── images/
│   │   │   ├── locations/
│   │   │   ├── marketing/
│   │   │   ├── orders/
│   │   │   ├── products/
│   │   │   │   ├── edit/[id]/         # V1 + V2 (V2 behind ?next=1)
│   │   │   │   └── new/               # Add Product wizard
│   │   │   ├── reports/
│   │   │   ├── shopify/
│   │   │   ├── stock-import/
│   │   │   ├── stock-tally/
│   │   │   ├── stock-transfers/
│   │   │   └── store-health/
│   │   ├── login/
│   │   └── layout.tsx
│   ├── components/
│   │   ├── edit-product/              # V2 shell
│   │   │   ├── EditProductV2.tsx
│   │   │   └── primitives.tsx         # TopBar, SectionNav, Section, ChipGroup, SaveIndicator, …
│   │   ├── CommandPalette.tsx
│   │   ├── ImageUploadFeedback.tsx    # modal + banner for upload failures
│   │   ├── MenuItemEditor.tsx
│   │   ├── MenuItemTree.tsx
│   │   ├── SearchableDropdown.tsx
│   │   ├── Sidebar.tsx
│   │   ├── Topbar.tsx
│   │   └── VariantManager.tsx
│   └── lib/
│       ├── activityLog.ts             # fire-and-forget activity logging
│       ├── apiAuth.ts                 # requireAuth(roles?) wrapper
│       ├── auth.ts                    # NextAuth config
│       ├── autoGenerate.ts            # title, SKU, SEO, tags, HTML generator
│       ├── categories.ts              # CATEGORIES + aliases + helpers
│       ├── categoryAttributes.ts      # ATTRIBUTES + CATEGORY_ATTRIBUTES + tag helpers
│       ├── collections/
│       │   ├── namingHelper.ts
│       │   ├── perCategoryPlan.ts
│       │   ├── ruleGenerator.ts
│       │   ├── tagCasingMigration.ts
│       │   └── themeSuffixForCollection.ts
│       ├── customerAggregates.ts
│       ├── features.ts                # FeatureKey enum + role defaults
│       ├── imageUpload.ts             # shared client-side upload utility
│       ├── menus/
│       │   ├── cleanupExecutor.ts     # round 2 CL1-CL7 executor
│       │   └── cleanupTasks.ts        # round 2 CL1-CL7 spec
│       ├── prisma.ts                  # PrismaClient singleton
│       ├── products/
│       │   ├── categorySpecs.ts       # CAT_SPECS map for Edit V2
│       │   └── validation.ts          # getIssues() pure function
│       ├── seoGenerator.ts            # Anthropic Claude Haiku 4.5 SEO generator
│       ├── shopify.ts                 # ~1100-line GraphQL client
│       ├── shopifyMenus.ts            # menu-specific helpers (separate from shopify.ts)
│       └── shopifyPush.ts             # bulk product push helper
├── theme-snippets/                     # Liquid files installed into Shopify theme
│   ├── snippets/
│   │   ├── badge-overlay.liquid
│   │   ├── mega-menu-image-badge.liquid
│   │   └── rx-module.liquid
│   ├── templates/
│   │   ├── collection.brand.liquid
│   │   ├── collection.gender.liquid
│   │   ├── collection.sale.liquid
│   │   ├── collection.shape.liquid
│   │   ├── product.appointment.liquid
│   │   ├── product.preorder-bv.liquid
│   │   └── product.rx-required.liquid
│   └── README.md
├── scripts/
│   └── upload-theme-files.ts          # one-shot theme upload helper
├── railway.json                        # Railway service config (start command, etc.)
├── package.json
├── tsconfig.json
├── next.config.js
├── tailwind.config.ts
├── README.md                           # ← this file
└── CLAUDE.md                           # AI agent handover notes
```

---

## Contributing

This is a closed-source proprietary project for Better Vision. External contributions aren't accepted. If you're an internal contributor:

### Workflow

1. Pull `main`: `git pull origin main`.
2. Make changes.
3. Run `npx tsc --noEmit` — must be clean before commit.
4. Run `npx next build` — must succeed before commit.
5. Commit with a descriptive multi-line message (see `git log --oneline` for the style).
6. Push to `main`. Railway auto-deploys.

There's no PR / review process — Avinash + the AI agent commit directly. For non-trivial changes, do work in a feature branch and merge to main only after smoke-testing.

### Style

- TypeScript strict mode. No `any` without an inline comment explaining why.
- Tailwind for layout. CSS-in-JS only for the Polaris-token scope inside Edit V2.
- File names: `camelCase.ts` for libs, `PascalCase.tsx` for components, `page.tsx` for routes.
- API responses: `{ success: true, data: ... }` or `{ success: false, error: "..." }`.
- Always log activity for any user action that mutates state: `logActivity({ action, entity, entityId, details })`.

### Testing

There are no automated tests. The smoke-test loop is:
1. `npx tsc --noEmit` — type safety.
2. `npx next build` — build safety.
3. Manually test the changed flow in production (Railway dashboard).
4. Watch the Activity Log for unexpected entries.

For high-risk changes (schema migrations, Shopify integration changes), test against a Shopify development store before merging to `main`.

---

## Credits

**Product / strategy / design / ops:** Avinash (Better Vision Bokaro)

**Engineering:** Claude Opus 4.7 (via the BetterVision AI handover workflow) — schema design, full-stack implementation, Shopify integration, agentic code review, all round-1 and round-2 work.

**Heritage:** Better Vision Bokaro (established 2010s), Gangadham Pune (recent expansion).

**Third-party:**
- Shopify GraphQL Admin API 2026-01
- Boost theme (current MAIN theme; "Dayal Inspired" variant)
- Razorpay + PhonePe for payments (Indian market)
- Anthropic Claude Haiku 4.5 (SEO generation)
- remove.bg (optional background removal)

---

## License

Proprietary. Copyright © 2026 Avinash / Better Vision. All rights reserved.

This codebase is not licensed for external use, modification, redistribution, or display. If you're reading this and aren't on the Better Vision team, you shouldn't have it.

---

## Quick reference card

| I want to… | Run / open |
|---|---|
| Log in to production | `https://bettervision-inventory-production.up.railway.app/login` — `admin@bettervision.in` / `admin123` |
| See current Shopify sync state | `/dashboard/shopify` |
| Add a new product | `/dashboard/products/new` |
| Edit a product (new shell) | `/dashboard/products/edit/<id>?next=1` |
| Bulk-sync collections | `/dashboard/collections` → "Sync from Shopify" |
| Bulk-sync menus | `/dashboard/admin/menus` → "Sync from Shopify" |
| Auto-generate collections | `/dashboard/admin/auto-collections` |
| Migrate tag casing | `/dashboard/admin/tag-casing-migration` |
| See what's in the audit trail | `/dashboard/activity-logs` |
| Review unpushed products | `/dashboard/admin/orphans` |
| Push all draft products to Shopify | `/dashboard/shopify` → "Push Unsynced Products to Shopify" |
| Pull everything fresh from Shopify | `/dashboard/shopify` → "Pull All Products from Shopify" |
| Reset stale failure counter | `/dashboard/shopify` → "Clear stale failures" |
| Run a stock tally | `/dashboard/stock-tally` |
| Transfer stock between stores | `/dashboard/stock-transfers` |
| Bulk-update inventory from Excel | `/dashboard/stock-import` |
| Designer queue | `/dashboard/design-queue` |

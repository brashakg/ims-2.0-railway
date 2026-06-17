# IMS 2.0 — Retail Operating System for Optical Chains

> A full **Retail Operating System** (not just a POS) purpose-built for Indian optical retail chains — **Better Vision** and **WizOpt**. It replaces ~15 spreadsheets, WhatsApp-based task management, and manual ledgers with one auditable, role-aware, multi-store system covering Point of Sale, Inventory, Clinical/Optometry, Workshop, Purchase, HR & Payroll, Finance, CRM/Marketing, Loyalty, Incentives, Reporting, and an in-house AI layer (**Jarvis**).

This document is written so that **any reader — human or AI agent — can understand the entire application end to end** without prior context. If you are an AI assistant taking over this repo, also read [`CLAUDE.md`](CLAUDE.md) for working preferences, then this README for the system map.

---

## Table of contents

1. [What IMS 2.0 is](#1-what-ims-20-is)
2. [The business it runs](#2-the-business-it-runs)
3. [Tech stack](#3-tech-stack)
4. [Architecture at a glance](#4-architecture-at-a-glance)
5. [Quick start (local dev)](#5-quick-start-local-dev)
6. [Repository layout](#6-repository-layout)
7. [Core concepts: roles, store-scoping, brands](#7-core-concepts-roles-store-scoping-brands)
8. [Non-negotiable business rules](#8-non-negotiable-business-rules)
9. [Domain modules (what the app does)](#9-domain-modules-what-the-app-does)
10. [The backend API](#10-the-backend-api)
11. [The frontend](#11-the-frontend)
12. [Jarvis — the AI agent subsystem](#12-jarvis--the-ai-agent-subsystem)
13. [Data layer (MongoDB)](#13-data-layer-mongodb)
14. [Security model](#14-security-model)
15. [Deployment & infrastructure](#15-deployment--infrastructure)
16. [Environment variables](#16-environment-variables)
17. [CI/CD](#17-cicd)
18. [Testing](#18-testing)
19. [Conventions & known gotchas](#19-conventions--known-gotchas)
20. [Roadmap & feature status](#20-roadmap--feature-status)
21. [Glossary (optical + Indian-retail terms)](#21-glossary)
22. [For AI agents picking up this repo](#22-for-ai-agents-picking-up-this-repo)

---

## 1. What IMS 2.0 is

IMS ("Inventory/Information Management System") 2.0 is a **multi-store, multi-entity, multi-brand retail OS**. Its design philosophy is explicit and enforced in code:

- **Control over Convenience** — every sensitive action requires explicit authorization; no silent defaults, no hidden automation.
- **Audit Everything** — who / when / where / old value / new value / why. The audit trail is immutable, even for the Superadmin.
- **Fail Loudly** — failures are visible; the system blocks rather than proceeding with corrupt data.

The authoritative business-rules document is [`docs/SYSTEM_INTENT.md`](docs/SYSTEM_INTENT.md). **Any code that violates SYSTEM_INTENT is wrong.**

**Scale (verified against current source):**

| Thing | Count |
|---|---|
| Backend routers mounted | **90+** |
| Registered API method+path pairs | **~1,206** (incl. bare/slash variants) |
| Frontend routes | **120+** (`App.tsx`) |
| MongoDB collections in use | **~69** |
| Jarvis AI agents | **8** (all implemented) |
| Roles | **12** (11 operational + `INVESTOR` read-only) |
| Backend tests | **~605** functions across **48** files |

> Note: older notes (including parts of `CLAUDE.md`) say "31 routers / 387+ endpoints / 65+ pages / CORTEX+SENTINEL only." Those figures are **stale**; the numbers in this README were verified against the live code and supersede them.

---

## 2. The business it runs

IMS 2.0 runs a chain organized as **multiple legal entities, multiple GST registrations, and two retail brands**, with stores across **Jharkhand** and **Maharashtra**:

- **Brands:** `BETTER_VISION` and `WIZOPT` (the `brand` enum on stores/products).
- **Legal entities:** the chain is split into separate legal entities (each with its own PAN, GST registration(s), and statutory codes). A single entity can hold **multiple GSTINs** when it operates in more than one state (e.g. a Jharkhand GSTIN + a Maharashtra GSTIN), because GST registration is per-state.
- **Stores:** physical retail locations, each tied to a brand, an entity, and a state. Store-level GSTIN + `gst_state_code` drive intra-vs-inter-state GST on every invoice.

This entity/GSTIN/store structure matters across the app: GST is computed per store-state, statutory payroll filings (PF/ESI/PT/TDS) are grouped per legal entity, and Professional Tax slabs differ by state.

---

## 3. Tech stack

**Frontend** (`frontend/`, deployed to **Vercel** → `ims-2-0-railway.vercel.app`)
- React **19.2** + TypeScript **~5.9**
- Vite **7** (build = `tsc -b && vite build`), Tailwind CSS **v4** (via `@tailwindcss/vite`)
- `react-router-dom` **6.30** (React-19 compatible), TanStack Query **5**, Zustand **5** (POS draft state)
- axios **1.16**, date-fns **4**, lucide-react (icons), jsbarcode/react-barcode, `@vercel/analytics` + `@vercel/speed-insights`

**Backend** (`backend/`, deployed to **Railway** → `ims-20-railway-production.up.railway.app`)
- FastAPI **0.136** + Uvicorn (4 workers in prod), Python **3.12**
- Pydantic **2.13**, PyMongo **4.17** (no ODM — direct driver), PyJWT + bcrypt, APScheduler (agent scheduler), redis (event bus + cache), httpx (provider calls)

**Database**
- **MongoDB** on Railway, database `ims_2_0`. Schema enforced via `$jsonSchema` validators on the core collections + indexes created in code.

**AI**
- Anthropic **Claude** API (ORACLE anomaly narratives + JARVIS chat). Pluggable LLM provider registry supports a premium Opus tier and any OpenAI-compatible endpoint.

---

## 4. Architecture at a glance

```
                        ┌──────────────────────────────────────────┐
   Browser (SPA)        │  Vercel — React 19 + Vite static build    │
   ───────────────────► │  ims-2-0-railway.vercel.app               │
                        └───────────────┬──────────────────────────┘
                                        │  HTTPS  /api/v1/*
                                        ▼
                        ┌──────────────────────────────────────────┐
                        │  Railway — FastAPI (Uvicorn × 4 workers)  │
                        │  ims-20-railway-production.up.railway.app  │
                        │                                            │
                        │  Middleware: rate-limit → security headers │
                        │   → dynamic CORS → process-time            │
                        │   → block-investor-writes                  │
                        │  90+ routers @ /api/v1/*                   │
                        │                                            │
                        │  ┌──────────────┐   ┌────────────────────┐ │
                        │  │ Jarvis agents│   │ APScheduler        │ │
                        │  │ (8) registry │◄──┤ ticks each agent   │ │
                        │  └──────┬───────┘   └────────────────────┘ │
                        │         │ events                            │
                        │   ┌─────▼──────────────┐                    │
                        │   │ Event bus          │ Redis pub/sub      │
                        │   │ (cross-worker)     │ + Mongo audit      │
                        │   └────────────────────┘                    │
                        └───────────────┬─────────────┬──────────────┘
                                        │             │
                              ┌─────────▼───┐   ┌─────▼─────────────┐
                              │ MongoDB     │   │ Redis (optional)  │
                              │ ims_2_0     │   │ events + cache    │
                              │ ~69 colls   │   └───────────────────┘
                              └─────────────┘
            External providers (fail-soft, gated by DISPATCH_MODE):
            Claude • MSG91 (WhatsApp/SMS) • PageSpeed • Shopify/Razorpay/Shiprocket/Tally
```

Key properties:
- **Stateless API + JWT.** Tokens are store-scoped; the active store travels in the token.
- **Fail-soft everywhere.** Missing Redis → in-process event dispatch. Missing Mongo → seeded mock DB (stub mode). Missing provider creds → calls are skipped/`SIMULATED`. A fresh deploy never spams customers.
- **Multi-worker safe.** The agent event bus uses Redis pub/sub so an event emitted in one worker reaches subscribers in all workers; every event is also persisted to Mongo for audit + replay.

---

## 5. Quick start (local dev)

Prerequisites: Python 3.12, Node 20+, and (optionally) a MongoDB connection string. Without Mongo the backend runs in **stub mode** with seeded mock data.

```bash
# 1) Backend (port 8000)
python start_backend.py
#    -> uvicorn api.main:app --reload, loads .env into the environment

# 2) Frontend (port 3000)
node start_frontend.mjs
#    -> vite dev server; proxies /api -> http://localhost:8000

# 3) Frontend checks
cd frontend && npx tsc --noEmit        # type-check
cd frontend && npx vite build          # production build

# 4) Backend smoke test (counts mounted routes)
python -c "import sys; sys.path.insert(0,'backend'); from api.main import app; print(len(app.routes))"

# 5) Backend tests
cd backend && pytest
```

**Dev login (seeded):** `admin` / `admin123` (Superadmin). Created by the DB seeder; change/disable in any real deployment.

- Interactive API docs (when backend is running): **`/docs`** (Swagger) and **`/redoc`**.
- The Vite dev proxy maps the frontend's `/api/v1/*` to `http://localhost:8000/api/v1/*` (see `frontend/vite.config.ts`).

---

## 6. Repository layout

```
ims-2.0-railway/
├── README.md                      ← this file
├── CLAUDE.md                      ← AI-session briefing & working preferences
├── start_backend.py               ← LOCAL-DEV launcher only (not used in prod)
├── start_frontend.mjs             ← LOCAL-DEV launcher only (not used in prod)
├── docker-compose.yml             ← STALE/legacy (references PostgreSQL; ignore)
├── backend/
│   ├── Dockerfile                 ← the real production image (Railway builds this)
│   ├── requirements.txt
│   ├── api/
│   │   ├── main.py                ← FastAPI app: middleware, CORS, lifespan, 90+ routers
│   │   ├── routers/               ← one file per domain (auth, orders, inventory, ...)
│   │   └── services/              ← pure logic + infra (role_caps, lens_pricing,
│   │                                 loyalty_engine, points_calculator,
│   │                                 payout_calculator, cache, file_store/GridFS,
│   │                                 audit_alerts, notification_service, ...)
│   ├── agents/                    ← Jarvis AI subsystem
│   │   ├── base.py, registry.py, scheduler.py, config.py, event_bus.py
│   │   ├── claude_client.py, llm_provider.py, providers.py, nexus_providers.py
│   │   └── implementations/       ← jarvis, cortex, sentinel, pixel, megaphone,
│   │                                 oracle, taskmaster, nexus
│   ├── database/
│   │   ├── connection.py          ← get_db() / get_seeded_db() + Mongo mock shim
│   │   ├── migrations.py          ← $jsonSchema-validated collection setup + seed
│   │   ├── schemas.py             ← authoritative collection schemas + enums
│   │   └── repositories/          ← per-domain data accessors
│   ├── observability.py           ← Slack alerts + (optional) Sentry, fail-soft
│   └── tests/                     ← ~605 tests / 48 files
├── frontend/
│   ├── vercel.json                ← Vite framework, SPA rewrite, cache headers
│   ├── vite.config.ts             ← dev proxy, path aliases, manualChunks
│   └── src/
│       ├── App.tsx                ← 120+ routes, all lazy-loaded
│       ├── components/shell/      ← Rail (left nav) + Topbar + Shell chrome
│       ├── components/layout/     ← AppLayout, ProtectedRoute, ErrorBoundary
│       ├── context/               ← Auth, Appearance, Toast, Module, Theme
│       ├── pages/                 ← module screens grouped by domain
│       ├── services/api/          ← axios client + per-domain API modules
│       ├── stores/posStore.ts     ← Zustand POS transaction state
│       └── hooks/                 ← useApiQuery, usePOSQueries, useSessionExpiry, ...
└── docs/
    ├── SYSTEM_INTENT.md           ← SUPREME authority for business rules
    ├── design/                    ← design language (tokens, shell, prototypes)
    └── reference/                 ← feature status, agent architecture, audits
```

---

## 7. Core concepts: roles, store-scoping, brands

### Roles (RBAC)

There are **12 roles** in the `users.roles[]` array. A user can hold several; the **highest** role determines authority. **SUPERADMIN and ADMIN bypass most role checks** in both the backend (`require_roles`) and the frontend (`hasRole`).

| Lvl | Role | Discount cap | Scope / notes |
|---|---|---|---|
| 1 | `SUPERADMIN` (CEO) | 100% | All stores; the only role that sees Jarvis/AI; geo-exempt |
| 2 | `ADMIN` (Director) | 100% | All stores; user management; geo-exempt |
| 3 | `AREA_MANAGER` | 25% | Multi-store (their region); geo-exempt |
| 4 | `STORE_MANAGER` | 20% | Single store; approves overrides |
| 4 | `ACCOUNTANT` | 0% | Finance/GST; no POS/inventory writes |
| 4 | `CATALOG_MANAGER` | 0% | Catalog & pricing |
| 5 | `OPTOMETRIST` | 0% | Prescription capture (required for "tested at store") |
| 6 | `SALES_CASHIER` | 10% | POS + payments |
| 6 | `SALES_STAFF` | 10% | POS + product search |
| 6 | `CASHIER` | 0% | Payment only |
| 7 | `WORKSHOP_STAFF` | 0% | Job status only |
| — | `INVESTOR` | 0% | **Read-only** seat; all writes (POST/PUT/PATCH/DELETE) are blocked at the middleware |

Discount caps live in `backend/api/services/role_caps.py`; the effective cap is `max(role_baseline, per-user override)` except SUPERADMIN/ADMIN who are always 100%.

### Store-scoping

Almost every collection carries a `store_id`. The pervasive backend pattern is:

```python
active_store = store_id or current_user.get("active_store_id")
```

HQ roles (SUPERADMIN/ADMIN/AREA_MANAGER) may pass `?store_id=` to scope into any store; lower roles are pinned to their own `active_store_id`. Users switch their active store via `POST /api/v1/auth/switch-store/{store_id}`, which re-issues the JWT.

### Brands

`brand` is `BETTER_VISION` or `WIZOPT`. The UI adapts chrome (`data-brand`) and the data model tags stores/products by brand. The left rail shows the active brand glyph.

---

## 8. Non-negotiable business rules

Source of truth: [`docs/SYSTEM_INTENT.md`](docs/SYSTEM_INTENT.md). Enforcement lives in `billing.py`, `role_caps.py`, `admin_extras.py`, `prescriptions.py`, and `auth.py`.

**Pricing (enforced at the DB level)**
- `offer_price > mrp` → **blocked** (a product can never be priced above MRP).
- `offer_price < mrp` → HQ already discounted; **no further store-level discount** (HQ override only).
- `offer_price == mrp` → role cap applies, further limited by category cap and luxury-brand cap.

**Category discount caps** (override role when lower): `MASS` 15% · `PREMIUM` 20% · `LUXURY` 5% · `SERVICE` 10% · `NON_DISCOUNTABLE` 0%.

**Luxury brand caps** (override category): Cartier / Chopard / Bvlgari **2%** · Gucci / Prada / Versace / Burberry **5%**.

**GST** (GST 2.0 rates): **5%** on frames / optical lenses / spectacles / contact lenses; **18%** on sunglasses / watches / accessories / services. **Intra-state = CGST + SGST** (split equally); **inter-state = IGST** (full rate). HSN codes mandatory; invoice generation is **blocked** if the store GSTIN is missing.

**Prescription (Rx) validation ranges**
- SPH: **−20.00 to +20.00** (0.25 steps)
- CYL: **−6.00 to +6.00** (0.25 steps)
- ADD: **+0.75 to +3.50** (0.25 steps)
- AXIS: whole number **1–180**
- PD: 20.0–80.0

Optical and contact lenses **require** a prescription; frame-only does not. Expired-Rx override requires Store Manager or above.

**Geo-fenced login** — store staff (Store Manager and below, when flagged `geo_restricted`) must be within the store radius (default **500 m**, Haversine) to log in; HQ roles (Superadmin/Admin/Area Manager) are exempt.

---

## 9. Domain modules (what the app does)

Each module spans a frontend page group, a backend router (or several), and a set of collections. Endpoint paths below are representative; the full list is in [§10](#10-the-backend-api) and at `/docs`.

### Point of Sale (POS) — *revenue-critical*
Two flows: **quick sale** and **prescription order**. Step machine: customer → prescription → products → review → payment → complete. Supports per-item discounts, an order-level cart discount (capped at the user's role cap, applied before GST), delivery date/time-slot/priority, advance payments, loyalty redemption, and gift-voucher payment. State lives in `frontend/src/stores/posStore.ts` (Zustand, persisted as a draft). GST is computed per category at checkout. Backend: `orders.py`, `billing.py`, `vouchers.py`, `loyalty.py`.

### Customers & CRM
Customer master with patients (family members under one customer), 360° view, RFM segmentation, lifecycle/churn-risk, loyalty tiers, interactions log, feedback/NPS, referrals, and follow-ups. Backend: `customers.py`, `crm.py`, `loyalty.py`, `follow_ups.py`, `marketing.py`.

### Orders & Returns
Order lifecycle DRAFT → CONFIRMED → PROCESSING → READY → DELIVERED (+ CANCELLED/RETURNED), payment recording (cash/card/UPI/gift-voucher/EMI), invoice generation, delivery tracking, and returns/exchanges. Sensitive edits emit immutable **audit alerts** (e.g. order cancellation = CRITICAL). Backend: `orders.py`.

### Clinical / Optometry
Eye-test queue, eye tests that auto-create a prescription, a 4-version prescription model (before-testing / after-testing / manual / final), Rx validation and printable Rx, optometrist stats, and contact-lens fitting. Backend: `clinical.py`, `prescriptions.py`.

### Workshop
Lens-fitting job pipeline PENDING → IN_PROGRESS → QC → READY → DELIVERED with technician assignment, QC pass/fail + rework, single-call dashboard KPIs, aging buckets, and an **external lab portal** (vendor portal) where labs update job status via a tokenized public link (no login). Backend: `workshop.py`, `vendor_portal.py`.

### Inventory
Stock levels, low-stock & expiring alerts, barcode lookup, stock counts (with scanning), aging/turnover, non-moving & overstock analysis, serial-number tracking, contact-lens expiry grid, and a **power-wise lens grid** (SPH × CYL). Backend: `inventory.py`, `transfers.py`.

### Purchase & Vendors
Vendors, purchase orders (create → send → cancel), goods-receipt notes (GRN) with variance tracking and stock-add on accept, mandatory-attachment gate, per-store/FY PO/RCPT numbering, contact-lens batch/expiry+FEFO, accountant 4-tick reconcile console, scheme credit-notes, and P3 variance-approval panel. Vendor returns, vendor performance, and replenishment suggestions (reorder points → draft POs). Backend: `vendors.py`, `vendor_returns.py`, `supply_chain.py`, `purchase_orders.py`, `grns.py`.

### Online Store (BVI / Shopify)
The **bettervision-inventory** (BVI) Next.js + Prisma/Postgres Shopify PIM app is consolidated into this repo under `ecommerce/`. It runs as a separate service in the IMS 2.0 Railway project with its own Postgres database. SSO between IMS and the Online Store is live (shared JWT). The "Online Store" nav item in the IMS left rail links into the BVI admin shell. The Shopify storefront (`bettervision.in`) stays on Shopify; BVI owns catalog/PIM sync. Backend: `ecommerce/` (Next.js + Prisma). Phases 1–5 complete.

### Catalog & Products
Product catalog with category-specific fields, brands/sub-brands, lens masters (brands/indices/coatings/add-ons) and range-based lens pricing, SKU generation, bulk import (CSV/XLSX), and optional Shopify sync. **Products convergence:** `catalog_products` (Shopify/online spine) and `products` (billing master) are unified — the catalog create-door writes the billing spine and billing requires it, eliminating the dual-master split. Backend: `catalog.py`, `products.py`, `admin_catalog.py`.

### Tasks, SOPs & Escalation
Tasks on a canonical model (UPPERCASE status, `due_at`, `source`) with priority P0–P4. A **per-priority SLA engine** (`services/task_sla.py` — the Standard matrix P0 15m/30m … P4 3d/7d, tunable via `GET/PUT /tasks/sla-config`) runs two clocks: time-to-acknowledge and overdue-grace. Breached tasks **escalate up the role ladder** (`services/task_escalation.py`: assignee → Store Manager → Area Manager → Admin → Superadmin, store-scoped, climbing past empty rungs) and are **reassigned** to the resolved owner, who is alerted **in-app** (the topbar bell + `notifications` collection) and over **WhatsApp** (quiet-hours-gated except P0/P1). The `/tasks/auto-escalate-overdue` endpoint and the **TASKMASTER** 5-minute tick share the same SLA check + resolver, so they always agree. Plus daily SOP checklists + persisted SOP templates. Backend: `tasks.py`, `notifications.py`, `services/task_{sla,escalation,notify}.py`.

### HR
Attendance (check-in/out, geo-aware), leave requests + balances, and the HR summary. Gated to finance roles. Backend: `hr.py`.

### Payroll
Full Indian statutory payroll, built end-to-end across four phases. A **legal-entity** model (PAN-grouped stores, multi-GSTIN per state); a Structured-CTC **salary master** (+ bulk CSV) with state-aware **Professional Tax** slabs; a pure, tested **computation engine** (EPF 12% + EPS 8.33% capped at ₹15k + EDLI/admin, ESI 0.75%/3.25% with the ₹21k eligibility gate, PT by state slab, manual TDS, 30-day LWP proration, incentive merge, advance recovery — integer-basis rounding for deterministic output); an idempotent **run flow** (DRAFT → APPROVED → PAID, one record per employee+month) with period lock; and **outputs**: payslip print (HTML), **Tally salary JV** (balanced XML), **PF ECR** text, and a statutory summary. Backend: `entities.py`, `payroll.py`, `services/payroll_{engine,exports}.py`. UI: `/settings/entities`, `/hr/salary-setup`, `/hr/payroll-run`.

### Incentives (Pune module)
A daily-points → monthly-payout engine: daily scorecards (9 weighted categories summing to 100), MTD leaderboard, eligibility/payout settings, payout preview → lock (one locked snapshot per store-month), and CSV export. Walkout/walk-in tracking feeds conversion metrics. Backend: `points.py`, `payout.py`, `walkouts.py`.

### Finance & Accounting
Real revenue & **P&L** (COGS from product cost with a 60%-of-line fallback, minus operating expenses + payroll), **P&L by store** and **by category**, GST summary, **GST reconciliation per legal entity** (output tax − input credit → net payable, filed via Tally), a **Tally sales-JV** XML export, outstanding receivables, vendor payables, cash flow, **period locking** (a locked month rejects new or approved expenses), and budgets (full vs survival mode) — all surfaced on the Finance dashboard with breakdown tables. Gated to finance roles. Backend: `finance.py`, `expenses.py`.

### Reports
Sales (summary/daily/by-salesperson/by-category/comparison/growth), inventory (valuation/non-moving/brand sell-through), clinical, HR, tasks, finance (outstanding/GST/GSTR-1/GSTR-3B/expense-vs-revenue), profit by category/store, discount analysis, staff ranking, customer acquisition, and a JARVIS-narrated "Growth Blueprint." Backend: `reports.py`, `analytics.py`, `analytics_v2.py`.

### Settings & Setup
Profile/preferences, business/tax/invoice settings, notification providers + templates, printers, discount rules, integrations config (secrets encrypted), system settings, audit-log viewer, approval workflows, and per-store feature toggles. Backend: `settings.py`, `admin.py`, `admin_extras.py`, `users.py`, `stores.py`.

### Print
Centralized print templates: thermal receipt, A4 receipt, GST tax invoice, PO, GRN, prescription. Frontend `print/` + `utils/receiptFormat.ts`.

---

## 10. The backend API

- **Base path:** everything is under `/api/v1/`. Title: *"IMS 2.0 - Retail Operating System"*, version `2.0.0`.
- **`redirect_slashes=False`** (deliberate — avoids 307s that break CORS), so many list/create endpoints register both `""` and `"/"`.
- **Middleware order:** `rate-limit` (per-IP sliding window) → `security headers` (HSTS/CSP/etc.) → **dynamic CORS** (strict allow-list that *reflects* requested headers; allows `*.vercel.app` / `*.up.railway.app`) → `process-time header` → **block-investor-writes** (403 on any write for INVESTOR-only users).
- **Auth core** (`backend/api/routers/auth.py`): `get_current_user` (decodes the JWT bearer token), `require_roles(*roles)` (dependency factory; SUPERADMIN always passes), `create_access_token`, bcrypt password hashing, login rate-limiting, and a token blacklist. JWT is HS256, 8-hour expiry, store-scoped.
- **Public (non-JWT) surfaces:** `/health`, `/`, the **vendor portal** (token in path), **webhooks** (HMAC-signed), and the seed endpoint (`POST /api/v1/admin/seed-database`, guarded by `SEED_SECRET`).

### Router map (mount prefix → domain)

| Prefix | Domain | Notable RBAC |
|---|---|---|
| `/api/v1/auth` | Authentication | public login/refresh |
| `/api/v1/users` | User management | admin/manager |
| `/api/v1/stores` | Stores | SUPERADMIN/ADMIN for writes |
| `/api/v1/products`, `/api/v1/catalog`, `/api/v1/admin` (catalog) | Products & catalog | CATALOG roles / admin |
| `/api/v1/inventory`, `/api/v1/transfers` | Inventory & transfers | inventory roles for writes |
| `/api/v1/customers`, `/api/v1/crm`, `/api/v1/loyalty`, `/api/v1/follow-ups`, `/api/v1/marketing` | Customers/CRM | mixed |
| `/api/v1/orders`, `/api/v1/billing`, `/api/v1/vouchers` | Sales & billing | POS roles |
| `/api/v1/prescriptions`, `/api/v1/clinical` | Clinical | OPTOMETRIST + managers |
| `/api/v1/workshop`, `/api/v1/vendor-portal` | Workshop (+ public lab portal) | workshop roles |
| `/api/v1/vendors`, `/api/v1/vendor-returns`, `/api/v1/supply-chain` | Purchase | vendor roles |
| `/api/v1/tasks` | Tasks & SOPs (+ SLA config) | mixed |
| `/api/v1/notifications` | In-app staff notifications (bell) | per-user |
| `/api/v1/hr`, `/api/v1/payroll` | HR & Payroll | **finance roles** (router-level) |
| `/api/v1/finance`, `/api/v1/expenses` | Finance | **finance roles** |
| `/api/v1/reports`, `/api/v1/analytics`, `/api/v1/analytics-v2` | Reporting | financial reports gated |
| `/api/v1/incentive/points`, `/api/v1/payout`, `/api/v1/walkouts` | Incentives | manager/accountant |
| `/api/v1/settings`, `/api/v1/admin` | Settings & admin | SUPERADMIN/ADMIN |
| `/api/v1/jarvis` | JARVIS + agents | **SUPERADMIN-only (returns 404 to others)** |
| `/api/v1/webhooks` | Inbound webhooks | public, signature-verified |
| `/api/v1/admin/techcherry` | Legacy data import | SUPERADMIN-only |
| `/api/v1` | Dashboard widgets | mounted first (exact paths win) |

`_FINANCE_ROLES = (ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT)` gates the `finance`, `hr`, and `payroll` routers at mount time.

### Representative endpoints

```
# Auth
POST   /api/v1/auth/login                       authenticate, geo-fence + rate-limit, returns JWT + user
GET    /api/v1/auth/me                           current user from token
POST   /api/v1/auth/switch-store/{store_id}      re-issue token with a new active store

# Orders / POS
POST   /api/v1/orders                            create sales order
POST   /api/v1/orders/{id}/payments              record payment (voucher/EMI handling, auto-confirm)
POST   /api/v1/orders/{id}/deliver               deliver (requires >= partial payment)
GET    /api/v1/orders/{id}/invoice               get/generate GST invoice (requires store GSTIN)

# Inventory
GET    /api/v1/inventory/low-stock               low-stock alerts
GET    /api/v1/inventory/lenses/power-grid       SPH x CYL lens availability grid
POST   /api/v1/inventory/stock-count/start       begin a physical count

# Clinical / Rx
POST   /api/v1/clinical/tests/{id}/complete      complete eye test -> auto-create Rx
GET    /api/v1/prescriptions/{id}/validate       validate ranges + expiry
POST   /api/v1/prescriptions/{id}/finalize       lock Rx, mirror final -> top-level

# Workshop
GET    /api/v1/workshop/dashboard-kpis           one-call KPIs
POST   /api/v1/workshop/jobs/{id}/qc             QC pass/fail
GET    /api/v1/vendor-portal/{token}/jobs        external lab: open jobs (public, tokenized)

# Tasks, escalation & notifications
POST   /api/v1/tasks/{id}/escalate               escalate (auto-resolves up the role ladder if no target)
POST   /api/v1/tasks/auto-escalate-overdue       SLA scan -> escalate + reassign + notify
GET    /api/v1/tasks/sla-config                  per-priority SLA matrix (PUT to tune; SUPERADMIN/ADMIN)
GET    /api/v1/notifications                      current user's bell feed (+ unread_count)

# Payroll
POST   /api/v1/payroll/config                    create salary config (ADMIN)
POST   /api/v1/payroll/salary/calculate          compute a month's salary
GET    /api/v1/payroll/payslip/{emp}/{month}/{year}  payslip

# Reports / Finance
GET    /api/v1/reports/sales/growth              MoM / YoY growth (finance roles)
GET    /api/v1/reports/gstr1                      GSTR-1 data (finance roles)
GET    /api/v1/finance/cash-flow                 cash flow

# Jarvis (SUPERADMIN-only; 404 to everyone else)
POST   /api/v1/jarvis/query                       natural-language query
GET    /api/v1/jarvis/agents                       list all 8 agents + live status
PATCH  /api/v1/jarvis/agents/{id}/toggle          enable/disable an agent
```

> **Gotcha:** the `billing` router declares an *internal* prefix on top of its mount, so its real paths are `/api/v1/billing/billing/*`. (The `finance` router previously did the same; that accidental double prefix was removed, so finance paths are now `/api/v1/finance/*` — matching the frontend `financeApi`, which had been 404ing against the double path.)

---

## 11. The frontend

A single-page React app. Every route is **lazy-loaded** and wrapped in two guard layers: a top-level `ProtectedRoute` (auth-only) renders `AppLayout`, and most child routes add their own `ProtectedRoute allowedRoles={[...]}`.

### Shell
- **`Rail`** — 64px left nav (expands to ~200px), grouped sections (Sales floor, Clinical, Stock & supply, Operations, Analysis, Growth, **AI**, System), brand glyph. Groups are **expanded by default** and reset to grouped on every login (in-session collapses persist across refresh until the next login). The **AI** group (Jarvis + the 8 agents) is SUPERADMIN-only; Entities is SUPERADMIN/ADMIN.
- **`Topbar`** — 52px bar with breadcrumbs, ⌘K command palette, a **store switcher** pill (multi-store users), a **role switcher** pill (multi-role users), and a **live notifications bell** (unread badge + dropdown, backed by `/notifications`).
- **`AppLayout`** — composes the shell, builds breadcrumbs from the URL, and shows a read-only banner for INVESTOR seats.

### Contexts (`src/context/`)
- **`AuthContext`** (`useAuth`) — auth state in `localStorage` (`ims_token`, `ims_user`), `login/logout`, `setActiveRole`, `setActiveStore`, `hasRole` (SUPERADMIN/ADMIN bypass), `hasPermission`, `isReadOnly` (INVESTOR). User shape: `{ id, email, name, phone, roles[], activeRole, storeIds[], activeStoreId, discountCap, isActive, geoRestricted }`.
- **`AppearanceContext`** — brand (`bv`/`wizopt`), density, rail-expanded; writes `data-brand`/`data-density` on `<html>`.
- **`ToastContext`** — `toast.success/error/warning/info`.
- **`ModuleContext`** — legacy per-module sidebar config (coexists with the Rail).
- **`ThemeContext`** — **locked to light mode** (dark mode was removed).

### Data layer
- **`services/api/client.ts`** — one axios instance. Base URL = `VITE_API_URL` or `/api/v1` (dev) / the Railway URL (prod), forces HTTPS in prod. Request interceptor attaches the bearer token; response interceptor retries only 5xx/429 with exponential backoff, and on 401 clears the token + redirects to `/login`. snake_case↔camelCase conversion is done **per module** (there is no global camel interceptor on `main`).
- **Per-domain API modules:** `auth`, `stores`, `products`, `inventory`, `sales` (orders/Rx/workshop), `customers`, `reports`, `analytics`, `hr` (+tasks), `clinical`, `expenses`, `finance`, `settings`, `marketing`, `loyalty`, `walkouts`, `incentive`, `payout`, `handoffs`, `vouchers`, `vendorPortal`.
- **State:** TanStack Query for server state (`useApiQuery`/`useApiMutation`); **Zustand** only for the POS draft (`posStore.ts`).

### Build (`vite.config.ts`)
- Dev proxy `/api → localhost:8000`. Path aliases (`@`, `@components`, `@pages`, `@hooks`, `@services`, `@context`, `@types`, `@utils`). `terser` minify with `drop_console`. `manualChunks` splits `vendor-react` / `vendor-ui` / `vendor-utils`; per-page splitting via `React.lazy`.

---

## 12. Jarvis — the AI agent subsystem

**Jarvis and all AI features are SUPERADMIN-only.** No other role sees the nav item or can reach `/api/v1/jarvis/*` (those endpoints return **404** to non-superadmins, hiding their existence).

Eight agents, each extending `JarvisAgent` (`backend/agents/base.py`). Schedules come from `DEFAULT_AGENT_CONFIGS` in `config.py` and are stored in the `agent_config` collection (toggleable live from the Jarvis page).

| Agent | Identity | Role | Schedule | External provider |
|---|---|---|---|---|
| **JARVIS** | Iron Man's J.A.R.V.I.S. | NLP & conversation core | event-driven | Claude (via the query endpoint) |
| **CORTEX** | Professor X | Orchestrator / intent router | event-driven | (keyword routing; Claude optional) |
| **SENTINEL** | The Sentinels | System health & monitoring | every 60s | httpx self-checks, Slack alerts |
| **PIXEL** | Batman | UI/UX audits, a11y, visual regression | daily 2 AM + on deploy | Google PageSpeed Insights |
| **MEGAPHONE** | Black Canary | Marketing — Rx expiry, birthdays, follow-ups (DND 9PM–9AM) | every 30 min | MSG91 WhatsApp + SMS |
| **ORACLE** | Oracle / Barbara Gordon | Anomaly detection + EOD analytics/fraud | hourly (+ 10 PM sweep) | Claude (narratives) |
| **TASKMASTER** | Taskmaster | SLA escalation, SOP verify, auto-reorder drafts | every 5 min | none (DB-only; the only agent that writes business state) |
| **NEXUS** | Cyborg | Integration sync + inbound webhooks | hourly (+ Tally 11 PM) | Shopify/Razorpay/Shiprocket/Tally |

Default-on: JARVIS, CORTEX, SENTINEL. The rest are opt-in. Core agents (JARVIS, CORTEX) are non-toggleable.

**Event bus** (`event_bus.py`): a single Redis pub/sub channel broadcasts every event to all workers; each worker dispatches to its locally-registered subscribers, so e.g. `stock.below_reorder` from SENTINEL in worker A wakes TASKMASTER in worker B. Every event is also persisted to the `agent_events` collection for an audit trail and the activity feed. **Without `REDIS_URL`** it falls back to identical in-process dispatch (single-worker) with one startup warning.

**Scheduler** (`scheduler.py`): APScheduler (`AsyncIOScheduler`) ticks each agent's `background_tick` per its trigger; disabled agents are paused; `run-now` forces an immediate tick. Falls back to a plain asyncio loop if APScheduler isn't installed.

**Safety gates:**
- **`DISPATCH_MODE`** ∈ `off` (default) | `test` | `live` controls all outbound messaging. `off` → never calls the external API (status `SIMULATED`); `test` → only sends to `TEST_PHONE`; `live` → real sends. A fresh deploy with real creds therefore can't spam customers.
- **PII scrubbing** in `llm_provider.py` strips customer/patient PII before any LLM call (configurable levels), so Claude never sees customer phone numbers/addresses.
- **TASKMASTER 3-tier model:** Tier 1 auto-acts (escalate overdue task), Tier 2 drafts-only requiring approval (reorder POs are created as DRAFT), Tier 3 advisory. All writes are audit-logged with before/after.

In-house error capture: any agent tick exception is persisted to `agent_errors` and re-emitted as an `agent.error` event (SENTINEL alerts on it) — no external APM required.

---

## 13. Data layer (MongoDB)

No ORM — direct PyMongo via `database/connection.py`:
- `get_db()` → singleton `DatabaseConnection` (pool 10–50); `.db` lazily connects; `get_collection(name)`.
- `get_seeded_db()` → fail-soft wrapper used by agents: real DB when connected, otherwise an in-memory **MockDatabase** seeded from `seed_data.py` (supports `$or/$and/$regex/$gt/$lt/$in/$ne`, projections, `$set/$inc/$push`). This is "stub mode."
- `ensure_indexes()` runs every startup (idempotent), creating hot-path indexes including unique/partial ones — e.g. `points_log` unique on `(store_id, date_str, staff_id)` where `deleted_at: null` (DB-level "no double save"), and `payout_snapshots` unique on `(store_id, year, month)` where `status: LOCKED`.
- `migrations.py` creates the **schema-validated** core collections (`$jsonSchema`, `validationLevel="moderate"`) from `schemas.py::COLLECTIONS` and seeds the default superadmin + HQ store.

**Collections by domain (~69 total):**
- *Core retail:* `customers`, `products`, `catalog_products`, `product_categories`, `stock`, `stock_units`, `serial_numbers`, `orders`, `prescriptions`, `stores`, `users`, `settings`
- *Inventory/supply:* `purchase_orders`, `grns`, `vendors`, `vendor_returns`, `vendor_portal_tokens`, `stock_audits`, `stock_counts`, `stock_transfers`
- *Workshop/clinical:* `workshop_jobs`, `handoffs`, `eye_tests`, `eye_test_queue`, `eye_camps`
- *HR/payroll/finance:* `attendance`, `leaves`, `payroll`, `payslips`, `salary_records`, `salary_config`, `salary_advances`, `advances`, `expenses`, `budgets`, `period_locks`
- *Incentives:* `points_log`, `incentive_settings`, `incentive_inputs`, `incentives`, `payout_snapshots`, `targets`, `walkouts`, `walk_in_counters`
- *CRM/marketing/loyalty:* `loyalty_accounts`, `loyalty_transactions`, `loyalty_settings`, `notification_logs`, `follow_ups`, `referrals`, `nps_responses`
- *Agents:* `agent_config`, `agent_events`, `agent_errors`, `agent_audit_log`, `anomalies`, `health_checks`, `alert_history`, `ui_audits`, `sync_runs`
- *Tasks/ops:* `tasks`, `sop_templates`, `task_sla_config`, `notifications` (user-targeted staff bell — distinct from customer-facing `notification_logs`)
- *Org structure:* `entities` (legal entities / GSTINs), `pt_slabs`
- *Integrations/audit:* `integrations`, `webhook_inbox`, `tally_exports`, `audit_logs`, `report_blueprints`
- Plus GridFS internal collections (`fs.files` / `fs.chunks`) for binary uploads.

**Field-naming conventions (snake_case backend):**
- IDs: every entity has a string `<entity>_id` plus a human-readable `<entity>_number` (`order_number`, `invoice_number`, `po_number`, …). `store_id` is the universal tenant key.
- **Phone is dual-key:** the validated key is `mobile` (unique, 10-digit) but `phone` is also accepted (TechCherry import + edit form write `phone`).
- **Customer store is dual-key:** `home_store_id` (original) and `preferred_store_id` (imported) both exist.
- **Pricing/tax:** products carry `mrp`, `offer_price`, `cost_price`, `hsn_code`; the API input field is `gst_rate` while the stored/order-line field is `tax_rate`. `is_discountable` + `discount_category` drive caps.
- **Stock quantity** is `quantity` (+ `reserved_quantity`), not `stock_quantity`.
- **Order money:** `subtotal`, `discount_total`, `tax_total`, `grand_total`, `amount_paid`, `balance_due`.
- Status enums are UPPERCASE; timestamps are mostly `created_at`/`updated_at` (some agent collections store ISO strings).

**Binary storage** — `services/file_store.py` wraps GridFS: `get_file_store()` returns a `GridFSFileStore` (or `InMemoryFileStore` in tests). Allowed MIME types: JPEG/PNG/HEIC/WEBP/PDF; max **25 MB**. Used by handoffs and bill uploads.

**Caching** — `services/cache.py`: Redis-first with an in-memory TTL fallback; namespaced keys `ims:<key>`; `invalidate_store(store_id)`; never raises.

---

## 14. Security model

- **Auth:** JWT HS256, 8-hour expiry, store-scoped. `JWT_SECRET_KEY` is required — the app refuses to boot with a placeholder. Login is rate-limited (per IP and per user) with a token blacklist on logout.
- **RBAC:** `require_roles(*roles)` on sensitive endpoints; SUPERADMIN always passes. Several routers gate at mount (finance/hr/payroll). Jarvis/agents return **404** to non-superadmins.
- **Investor read-only:** the `block_investor_writes` middleware 403s every POST/PUT/PATCH/DELETE for INVESTOR-only users (auth endpoints excepted).
- **Geo-fenced login** for flagged store staff (Haversine, default 500 m).
- **Immutable audit:** `services/audit_alerts.py` writes audit rows and emits `audit.alert` events for sensitive actions (order edits/cancellations, over-cap discounts, large refunds); SENTINEL forwards CRITICAL/HIGH to Slack. Audit logs are never deleted.
- **PII protection:** customer PII is scrubbed before any LLM call; integration secrets are encrypted at rest in the `integrations` collection.
- **Transport/headers:** strict dynamic CORS allow-list, HSTS/CSP/X-Frame-Options, per-IP rate limiting.
- **Webhooks** are HMAC-signature-verified with an anti-replay window.

---

## 15. Deployment & infrastructure

**Backend → Railway.** Built from **`backend/Dockerfile`** (multi-stage `python:3.12-slim`, non-root user, healthcheck on `/health`, `uvicorn api.main:app --workers 4` on port 8000). There is **no** `railway.json`/`nixpacks.toml` — Railway builds the Dockerfile directly. Runtime config comes from environment variables (see below); Mongo + Redis are Railway services.

**Frontend → Vercel.** `frontend/vercel.json` (framework `vite`, build `npm run build`, output `dist`, SPA rewrite to `/index.html`, immutable asset caching). `VITE_API_URL` is injected at build time.

> `start_backend.py` / `start_frontend.mjs` are **local-dev launchers only**. The root `docker-compose.yml` is **stale/legacy** (it references PostgreSQL and a non-existent `Dockerfile.backend`) — ignore it; the real database is MongoDB.

---

## 16. Environment variables

Backend reads vars inline via `os.getenv` (no central settings class). Frontend uses `import.meta.env.*`. **Never commit real values** — set them in Railway/Vercel. Names and purposes only:

| Variable | Used by | Purpose |
|---|---|---|
| `JWT_SECRET_KEY` | auth | JWT signing secret (**required**) |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | auth | Token TTL (default 480 = 8h) |
| `MONGODB_URL` / `MONGO_URL` | db | Mongo connection URI |
| `MONGO_HOST`/`MONGO_PORT`/`MONGO_DATABASE`/`MONGO_USERNAME`/`MONGO_PASSWORD`/… | db | Component-style Mongo config (db default `ims_2_0`) |
| `CORS_ORIGINS` | app | Extra allowed origins (comma-separated) |
| `RATE_LIMIT_PER_MINUTE` | app | Per-IP rate limit (default 120) |
| `ENVIRONMENT` | app | `test` disables rate-limit/seed gates |
| `SEED_SECRET` | seed | Guards `POST /api/v1/admin/seed-database` |
| `REDIS_URL` (or `REDIS_HOST`/`PORT`/`PASSWORD`/`DB`) | event bus + cache | Redis; if unset → in-process dispatch + in-memory cache |
| `AGENT_EVENT_CHANNEL` | event bus | Pub/sub channel (default `ims.agents.events`) |
| `ANTHROPIC_API_KEY` | ORACLE, JARVIS | Claude API key |
| `AGENT_CLAUDE_MODEL` / `JARVIS_MODEL` / `LLM_*` | LLM provider | Model selection + premium/extra provider tuning |
| `PAGESPEED_API_KEY` / `FRONTEND_BASE_URL` | PIXEL | Lighthouse audits + target URL |
| `DISPATCH_MODE` / `TEST_PHONE` | MEGAPHONE + NEXUS | Outbound safety gate (`off`/`test`/`live`) |
| `MSG91_API_KEY` / `MSG91_SENDER` / `MSG91_WHATSAPP_*` / `MSG91_SMS_TEMPLATE_ID` | MEGAPHONE | WhatsApp + SMS (DLT) |
| `SLACK_WEBHOOK_URL` / `SLACK_ALERT_SEVERITY` | observability | CRITICAL anomaly alerts |
| `SENTRY_DSN` / `SENTRY_*` | observability | Optional APM (skipped if unset) |
| `RAZORPAY_*` / `SHOPIFY_*` / `SHIPROCKET_*` / `TALLY_*` | NEXUS | Integration creds (mostly dormant) |
| `VITE_API_URL` | frontend | Backend API base URL (build-time) |

---

## 17. CI/CD

GitHub Actions in `.github/workflows/`:

- **`backend-ci.yml`** ("Backend Tests & Build") — runs on push to `main`/`develop`/`claude/**` and PRs. Jobs: **test** (mongo:7.0 service, Python **3.10 + 3.11** matrix; Black check (advisory), Pylint errors-only, mypy (advisory), pytest with coverage → Codecov); **security** (Bandit SAST — MEDIUM+ blocks merge; Safety; SBOM); **build** (docker build on main).
- **`frontend-ci.yml`** ("Frontend Tests & Build") — Node **18.x + 20.x** matrix: `npm ci` → ESLint (advisory) → `tsc --noEmit` (advisory) → unit/integration tests → `npm run build` → bundlesize → upload `dist`. Plus a `security-scan` job (npm audit + OWASP Dependency-Check).

Merge policy: auto-merge is disabled; squash-merge a PR only when `mergeStateStatus` is CLEAN.

---

## 18. Testing

Heavily backend-weighted: **~605 test functions across 48 files** in `backend/tests/`. Strong coverage of RBAC gating, pricing/discount caps, GST recompute, prescriptions, vouchers/loyalty/points/payout, the Jarvis agents + event bus + observability, inventory/serials/transfers, workshop KPIs, reports, vendor portal, webhooks, Tally export, TechCherry import, handoffs, and walkouts (the largest single file).

Frontend tests are minimal (auth flow, one modal, one hook) — a known gap. Run backend tests with `cd backend && pytest`; frontend checks with `tsc --noEmit` + `vite build` (the project's de-facto frontend gate).

---

## 19. Conventions & known gotchas

- **Commit + push after each phase**, small scoped commits — not batched.
- **Always verify the frontend after changes:** `tsc --noEmit` + `vite build` (+ browser preview). On Windows, screenshots can be flaky; prefer DOM inspection.
- **No emojis in Python** — Windows cp1252 can crash `print()`/logging. Use ASCII tags like `[AGENTS]`.
- **Theme is light-only** (dark mode removed).
- **`is not None` for PyMongo objects** — `bool(collection)` raises in PyMongo 4.x; always compare `is not None`.
- **Double-prefix path:** `billing` real paths are `/api/v1/billing/billing/*`. (`finance` was de-double-prefixed to `/api/v1/finance/*`.)
- **Shared prefixes:** `admin` / `admin_catalog` / `admin_extras` all mount at `/api/v1/admin`; `jarvis` + `agents` share `/api/v1/jarvis` (first-registered wins on collisions).
- **`redirect_slashes=False`** → many routes register both `""` and `"/"`.
- **Stale `docker-compose.yml`** references PostgreSQL — the real DB is MongoDB.
- **Rx CYL inconsistency:** the canonical/validate range is ±6.00, but one prescription-create path currently allows ±10.00 (worth reconciling).
- **camelCase aliasing is per-module** on `main` (no global response interceptor) — when reading API data on the frontend, expect snake_case unless a service module explicitly maps it.

---

## 20. Roadmap & feature status

Detailed status is tracked in [`docs/reference/IMS2_Updated_Feature_Status.md`](docs/reference/IMS2_Updated_Feature_Status.md).

**Complete + merged to main:**
- **Payroll** — full Indian statutory engine (legal entities, state-aware PT, EPS split, ₹21k ESI gate, batch run/lock, Tally JV, PF ECR, payslips). ✅
- **Finance & Accounting** — real P&L (per store + per category), GST reconciliation per entity + Tally sales-JV, receivables/payables, period lock, dashboard breakdown tables. ✅
- **Tasks/SOP escalation engine** — canonical task model, per-priority SLA, role-ladder auto-escalation **with reassignment**, in-app bell + WhatsApp alerts, daily SOP-checklist completion tracking. ✅
- **Purchase/Procurement** — PO→GRN→stock-add, mandatory-attachment gate, per-store/FY PO/RCPT numbering, CL batch/expiry+FEFO, accountant 4-tick reconcile console, scheme-CN, P3 variance-approval panel. ✅
- **BVI Online Store (Phases 1–5)** — `ecommerce/` (Next.js + Prisma/Postgres) consolidated into IMS 2.0 Railway project, SSO live, "Online Store" nav item wired. ✅
- **Products convergence** — `catalog_products` spine ↔ `products` billing master unified; the catalog create-door writes the billing spine and billing requires it. ✅
- **Jarvis (all 8 agents)** — JARVIS, CORTEX, SENTINEL, PIXEL, MEGAPHONE, ORACLE, TASKMASTER, NEXUS all implemented, registered, and live in `backend/agents/implementations/`. ✅

**Highest-impact areas still ahead (priority order):**
1. **Integration activation** — flip `DISPATCH_MODE=live` + provision MSG91/Razorpay/Shiprocket/Tally/GST-portal keys on Railway (all code exists, gated by env).
2. **Inventory depth** — daily count + barcode, inter-store transfer polish (most backend endpoints exist; verify UI wiring).
3. **CRM/marketing automation** — WhatsApp send (MEGAPHONE built; needs MSG91 activation).
4. **AI intelligence expansion** — AI change-proposal workflow (SYSTEM_INTENT §8 loop), natural-language query.

---

## 21. Glossary

**Optical**
- **Rx / Prescription** — the optometrist's lens specification.
- **SPH (Sphere)** — lens power for near/far-sightedness (diopters).
- **CYL (Cylinder)** — power correcting astigmatism.
- **AXIS** — orientation (1–180°) of the cylinder.
- **ADD** — additional reading power (bifocal/progressive).
- **PD** — pupillary distance (mm).
- **Power grid (SPH×CYL)** — a matrix view of lens stock by power combination.

**Indian retail / tax**
- **MRP** — Maximum Retail Price (legal ceiling; `offer_price` can never exceed it).
- **GST** — Goods & Services Tax. **CGST+SGST** intra-state, **IGST** inter-state.
- **GSTIN** — GST registration number (per state per entity).
- **HSN** — tax classification code on each line item.
- **GSTR-1 / GSTR-3B** — monthly GST returns.
- **PF (EPF)** — Provident Fund (retirement). **EPS** — pension portion of employer PF.
- **ESI** — Employees' State Insurance (health), for staff under the wage threshold.
- **PT (Professional Tax)** — state-levied tax on salaries (slabs vary by state).
- **TDS** — Tax Deducted at Source (income tax withheld on salary).
- **Tally** — the popular Indian accounting software the chain keeps books in (salary/sales journal vouchers can be exported to it).

**Operational**
- **PO** — Purchase Order. **GRN** — Goods Receipt Note (received-stock check vs PO).
- **SOP** — Standard Operating Procedure (e.g. daily opening/closing checklists).
- **Walkout / Walk-in** — a customer who left without buying / a counted store visit (drives conversion + incentives).
- **LWP** — Leave Without Pay (unpaid absence days that prorate salary).

---

## 22. For AI agents picking up this repo

If you are an AI assistant working on this codebase:

1. **Read `CLAUDE.md` first** for working preferences (commit-per-phase, verify-frontend-after-changes, no-emoji-in-Python, ask-before-POS, light-theme-only), then this README for the system map.
2. **`docs/SYSTEM_INTENT.md` is the supreme authority** for business rules. If code conflicts with it, the code is wrong.
3. **Verify before trusting docs** — several legacy notes have stale counts (this README's numbers are current). When in doubt, grep the source or hit `/docs`.
4. **Respect the safety architecture**: keep everything fail-soft, never remove the `DISPATCH_MODE` gate or PII scrubbing, never expose secrets (print env-var *names*, never values), and never weaken the immutable audit trail or investor read-only enforcement.
5. **Build module-by-module**: backend + tests, then frontend, then `tsc --noEmit` + `vite build`, then commit & push that phase before starting the next.
6. **Mind the gotchas in §19** (double-prefix routers, `is not None` for PyMongo, snake_case API responses, `redirect_slashes=False`).

---

*This README describes IMS 2.0 as built. For the authoritative, always-current API surface, run the backend and open `/docs`.*

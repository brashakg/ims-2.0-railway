# IMS 2.0 — Agent Briefing

Self-contained context for any Claude session (local CLI or web) picking up this repo.

---

## 👋 IF YOU ARE A FRESH SESSION TAKING OVER — READ THIS FIRST

**Where the truth lives.** [`PLAN_STATUS.md`](PLAN_STATUS.md) is the live plan — merged / in flight / waiting on the owner (§4d money-path audit · §4e POS surfaces · §4g found-not-fixed · §4h closed by owner · §5 waiting on the owner). Owner rulings and gotchas live in the owner's memory directory OUTSIDE the repo (`~/.claude/projects/C--Users-avina-IMS-2-0-CLAUDE-COWORK/memory/`, start at `MEMORY.md`). `docs/reference/*Feature_Status*`, `IMS2_COMPLETE_FEATURE_LIST.md` and the dated session summaries that used to sit here are **STALE — never trust them for what exists; verify in code.** Owner rule (2026-09-04): a briefing may only say "done" for work that is MERGED, with its PR number; designs and plans are written as designs and plans.

**State of `main` as of 2026-09-05** — every row checked against `git log origin/main` + `gh pr view <n>`:
- **#1088** — the 2026-09-03/04 wave (59 commits): Tasks / Customers / Clinical / Inventory tabs became addresses; the money-path audit's fixes (unpaid-goods doors, credit-note head + GSTIN, discount caps, loyalty double-charge, fabricated invoice numbers, blind day-end count — per-row status in §4d); general counter at parity with billing; the legacy wizard till is RETIRED — `/pos` redirects to `/pos/new` (query kept); approved-device sign-in shipped switched OFF; apply-for-leave screen; leaderboard shows your own figures only.
- **#1090** — one household per family member; one find-or-create door for a family row.
- **#1091** — commission weightings + supervisor bonuses admin-only, like salary.
- **#1093** — `/orders/overdue/list` binds `expected_delivery` as a bare-day string (it had been returning nothing in prod).
- **#1095** — till-retirement orphans deleted after salvage triage (thermal receipt components, client-side GST calculators).
- **#1096** — Add-a-Product says what is missing, and is tappable again.
- **#1097** — store picker grouped by brand, names wrap, keyboard picks.
- **#1098** — `/catalog/review` + `/catalog/missing-photos` are real addresses; ONE server rule for "has a usable photo" (`online_catalog.product_online_state`). Fixed underneath: the create door never projected the spine's `images` onto the catalog twin, so the Shopify push refused every IMS-catalogued product as photo-less. `scripts/backfill_twin_images.py` repairs existing rows (dry-run by default).
- **#1099** — the eye examination is its own page (`/clinical/test/:entryId`); the modal is deleted.

**OPEN right now:**
- **#1094** courier COD collects `balance_due`, not the whole bill — **DRAFT**. Round-1 adversarial review answered (39 probes kept in `backend/tests/test_cod_probe_review.py`); a second round is in progress before it leaves draft. Money path: a green build alone does not clear it.
- Owner decisions owed: a **COD remittance door** (nothing records the courier's collection as a payment — tender type + who reconciles the payout); **36px form controls app-wide vs the 44px the POS design assumed** (`.input-field` is 36px; the "44px standard" exists only in the design prose).
- Everything else waiting on the owner: `PLAN_STATUS.md` §5.

**Standing gates** (all re-verified 2026-09-05):
- Required checks on `main`: `test (shard 1..3)`, `test-and-build (22.x)`, `e2e`. Merge only when `mergeStateStatus` is CLEAN — never `--admin`.
- New screen ⇒ add it to `e2e/fixtures/routes.ts` or the layout gate fails BY NAME (`e2e/tests/routes-inventory.spec.ts`). `KNOWN_BROKEN` may only SHRINK — never add a row to green a red build.
- New backend route ⇒ a POLICY row in `backend/api/services/rbac_policy.py` or CI fails. A new row under an existing module broadens that module's `:write` grant-union — carve a dedicated capability key.
- `pylint api/ --enable=E,F` is a HARD gate inside `test (shard 1)`. No emojis in Python (Windows cp1252 crash).
- Frontend gate: `cd frontend && node_modules/.bin/tsc -b --force && npx vite build` (`tsc -b` without `--force` can cache-mask after scripted edits).
- Ask before touching POS (revenue-critical). Light theme only. The owner is not a developer — plain English; he does dashboard clicks and pastes credentials.

---

## What this is

**IMS 2.0** — Retail Operating System for Avinash's Indian optical chains (**Better Vision** + **WizOpt**). Replaces ~15 Excel files, WhatsApp-based task management, and manual ledgers across POS · Inventory · Clinical/Optometry · HR/Payroll · Finance · CRM/Marketing · Vendor Management · Task/SOP enforcement · AI (Jarvis).

Not a POS — a full retail OS. 12 roles. ~1,300 API routes (1,308 mounted on the 2026-09-05 smoke import). ~160 frontend routes, one file per module in `frontend/src/routes/*.tsx`.

## Stack

- **Frontend:** React 19 + TypeScript + Vite + Tailwind v4 · `frontend/` · port 3000 · deployed to Vercel (`ims-2-0-railway.vercel.app`)
- **Backend:** FastAPI + Python 3.12 · `backend/` · port 8000 · 90+ routers at `/api/v1/*` · deployed to Railway (`ims-20-railway-production.up.railway.app`)
- **DB:** MongoDB on Railway · database `ims_2_0`
- **Auth:** JWT HS256, 8h expiry, 11 roles, store-scoped tokens, geo-fenced login for store staff (roles 4-7)
- **Test login:** `admin` / `admin123`

## Recently completed initiatives

All design-language rollout phases are **DONE** (Phases 0–6.7). All 8 Jarvis agents are live. The following major modules are **COMPLETE + merged to main**:

- **Payroll** — full Indian statutory engine (entities/PAN+GSTIN, Structured-CTC, state-aware PT, EPF/EPS/ESI/TDS, run+lock, Tally JV / PF ECR / payslip). ✅
- **Finance & Accounting** — real P&L (by store + by category), GST reconciliation per entity, Tally sales-JV, AR/AP, cash flow, period lock, budgets, dashboard tables. ✅
- **Tasks/SOP escalation engine** — per-priority SLA, role-ladder auto-escalation + reassignment, in-app bell + WhatsApp alerts, daily SOP-checklist completion tracking. ✅
- **Purchase/Procurement** — PO → GRN → stock-add, mandatory-attachment gate, per-store/FY numbering, CL batch/expiry+FEFO, accountant reconcile console, scheme-CN, P3 variance-approval. ✅
- **Shopify unification** — BVI (the separate Next.js/Postgres catalog app) was retired and its `ecommerce/` subtree deleted 2026-07-20 (#922); IMS is the sole Shopify writer. "Online Store" screens live in IMS (`frontend/src/routes/onlineStoreRoutes.tsx`); IMS→Shopify push is MANUAL (a human presses publish). ✅
- **Products convergence** — `catalog_products` spine ↔ billing master unified; catalog create-door writes the billing spine; billing requires the spine. ✅

## Jarvis: 8 superhero agents (SUPERADMIN-only)

**Non-negotiable:** Jarvis and all AI features are **SUPERADMIN-only**. No other role sees the Jarvis nav item or hits `/api/v1/jarvis/*`.

Source of truth for agents: [docs/reference/IMS2_Agent_Architecture.html](docs/reference/IMS2_Agent_Architecture.html).

| Agent | Hero | Role | Schedule | Toggleable |
|-------|------|------|----------|------------|
| JARVIS | Iron Man's J.A.R.V.I.S. (Marvel) | NLP & conversation core | Always-on | No (core) |
| CORTEX | Professor X (Marvel) | Orchestrator / command router | Event-driven | No (core) |
| SENTINEL | The Sentinels (Marvel) | System health & monitoring | 60s | Yes |
| PIXEL | Batman (DC) | UI/UX quality, deploy audits, a11y, visual regression | Daily 2 AM + on deploy | Yes |
| MEGAPHONE | Black Canary (DC) | Marketing — Rx expiry, birthdays, follow-ups, WhatsApp (DND 9PM-9AM) | 30 min + daily 9 AM | Yes |
| ORACLE | Oracle / Barbara Gordon (DC) | AI analysis — hourly anomaly scan + 10 PM EOD analytics/forecast/fraud | Hourly + 10 PM | Yes |
| TASKMASTER | Taskmaster (Marvel) | Real execution — SLA escalation, SOP verify, auto-reorder, expense anomaly (3-tier safety, audit-logged) | 5 min | Yes |
| NEXUS | Cyborg (DC) | Integration sync — Shopify, Razorpay, Shiprocket, webhooks, Tally 11 PM | Hourly + webhook | Yes (when integrations active) |

All 8 agents are implemented in [backend/agents/implementations/](backend/agents/implementations/): JARVIS, CORTEX, SENTINEL, PIXEL, MEGAPHONE, ORACLE, TASKMASTER, and NEXUS.

## Non-negotiable business rules

Source: [docs/reference/IMS2_Complete_App_Summary.docx](docs/reference/IMS2_Complete_App_Summary.docx) §6.

**Pricing**
- MRP > offer_price → **blocked at DB**
- offer_price < MRP → no further store-level discount
- offer_price == MRP → role cap applies, further limited by category + brand cap

**Category caps** (override role when lower): MASS 15% · PREMIUM 20% · LUXURY 5% · SERVICE 10% · NON_DISCOUNTABLE 0%

**Luxury brand caps**: Cartier/Chopard/Bvlgari 2% · Gucci/Prada/Versace/Burberry 5%

**GST**: 5% frames/optical lenses/contacts · 18% sunglasses/watches/accessories · intra-state = CGST+SGST, inter-state = IGST

**Rx validation**: SPH -20.00 to +20.00 (0.25 step) · CYL -6.00 to +6.00 (0.25) · AXIS 1-180 whole · ADD +0.75 to +3.50 (0.25)
- **Rx required at POS**: a spectacle (Rx) lens order line must carry a valid, customer-matching, non-expired prescription (Store-Manager+ overrides an expired Rx); contact-lens lines are exempt from the hard requirement, but every line's powers are range/0.25-step/axis validated.

**Period lock**
- **Period lock** blocks ALL financial writes incl. payroll approve + lock (423 when locked).

**Geo-fenced login**: roles 4-7 must be within 500m of their store. Roles 1-3 exempt.

**Core philosophy**: Control over Convenience · Audit Everything · Fail Loudly. Audit trail is immutable even for Superadmin.

## 11 roles

| Lvl | Role | Cap | Scope |
|-----|------|-----|-------|
| 1 | Superadmin (CEO) | ∞ | All stores, AI access |
| 2 | Admin (Director) | ∞ | All stores, user mgmt |
| 3 | Area Manager | 25% | Multi-store |
| 4 | Store Manager | 20% | Single store |
| 4 | Accountant | — | Finance/GST, no POS/inv |
| 4 | Catalog Manager | — | Catalog/pricing |
| 5 | Optometrist | — | Rx capture |
| 6 | Sales Cashier | 10% | POS + payments |
| 6 | Sales Staff | 10% | POS + search |
| 6 | Cashier | — | Payment only |
| 7 | Workshop Staff | — | Job status only |

## Dev

```bash
# Start backend (port 8000)
python start_backend.py

# Start frontend (port 3000)
node start_frontend.mjs

# Frontend type-check
cd frontend && npx tsc --noEmit

# Frontend build
cd frontend && npx vite build

# Backend smoke test
python -c "import sys; sys.path.insert(0,'backend'); from api.main import app; print(len(app.routes))"
```

## Working preferences (from past sessions)

- **Commit + push after each phase**, not batched. See Phase 0 commit style.
- **Always verify frontend after changes** — `tsc --noEmit` + `vite build` + browser preview on a live dev server. Screenshots can be flaky on Windows; prefer `preview_inspect` / `preview_eval` for visual verification.
- **Avoid emojis in Python** — Windows cp1252 encoding crashes on emoji in `print()` / logger. Use ASCII tags like `[AGENTS]` instead.
- **Theme is light-only.** Dark mode was fully removed in commit 11663f9.
- **DB helper pattern in routers:** `def _get_db(): from database.connection import get_db; return get_db().db`
- **User shape:** `user?.id` (not `user_id`), `user?.roles` is an array, `user?.activeRole` is the current one.
- **Toast:** `useToast()` from `context/ToastContext`. Methods: `toast.success/error/warning/info`.

## Payroll module (complete — entities -> run -> outputs)

Full Indian statutory payroll, built in 4 phases (PRs #198-#201):
- **Entities** (`/api/v1/entities`, `backend/api/routers/entities.py`): a legal entity (PAN) groups stores; multi-GSTIN per state. UI `/settings/entities`. Stores carry `entity_id`.
- **Salary master** (Structured CTC) in `payroll.py` (`/payroll/config*`); UI `/hr/salary-setup` (incl. bulk CSV). State-aware **Professional Tax** slabs (`/payroll/pt-slabs*`, seeded Jharkhand + Maharashtra).
- **Engine** (`backend/api/services/payroll_engine.py`, pure + tested): EPF (12% + EPS 8.33% cap 15k + EDLI/admin), ESI (0.75/3.25, 21k gate), PT (state slab), TDS (manual), 30-day LWP proration, incentive merge, advance recovery. Integer-basis rounding = deterministic.
- **Run flow** (`/payroll/run|approve|lock`, idempotent per employee+month): UI `/hr/payroll-run`. DRAFT -> APPROVED -> PAID.
- **Outputs** (`backend/api/services/payroll_exports.py`): payslip print (HTML), **Tally salary JV** (balanced XML), **PF ECR** text, statutory summary.
- Tests: `test_payroll_engine.py`, `test_payroll_run.py`, `test_payroll_exports.py`, `test_payroll_foundation.py`.

## Key reference docs (in-repo)

- [README.md](README.md) — **Comprehensive system reference** (architecture, every module, full API map, agents, data layer, business rules, deployment, glossary). Start here; its verified counts supersede the stale figures elsewhere in this file.
- [docs/design/](docs/design/) — New design language handoff (tokens, shell, 9 module prototypes). Start here for any UI work.
- [docs/reference/IMS2_Agent_Architecture.html](docs/reference/IMS2_Agent_Architecture.html) — Authoritative spec for the 8 Jarvis agents
- [docs/reference/IMS2_Comprehensive_Test_Report_April2026.md](docs/reference/IMS2_Comprehensive_Test_Report_April2026.md) — April 10 audit; commit e19be44 (Apr 15) claims all 47 issues fixed, **unverified end-to-end**
- [docs/reference/IMS2_AUDIT_RECHECK_DELTA.md](docs/reference/IMS2_AUDIT_RECHECK_DELTA.md) — March audit recheck
- [docs/reference/IMS2_Claude_Code_Handover.md](docs/reference/IMS2_Claude_Code_Handover.md) — Recent CI fixes + landmines
- [docs/reference/IMS2_COMPLETE_FEATURE_LIST.md](docs/reference/IMS2_COMPLETE_FEATURE_LIST.md) / [IMS2_Updated_Feature_Status.md](docs/reference/IMS2_Updated_Feature_Status.md) — Feature catalog + status (counts may be stale)
- [docs/SYSTEM_INTENT.md](docs/SYSTEM_INTENT.md) — Supreme authority for business rules (any code violating this is wrong)
- **Note:** The full app spec (IMS2_Complete_App_Summary.docx) and 3 other .docx files live in the user's local workspace root (outside the repo) — they contain §21 Railway/MongoDB/Vercel credentials that couldn't be redacted without losing content. Ask the user to share relevant excerpts if needed.

## Current file layout

```
ims-2.0-railway/
├── CLAUDE.md                           ← you are here
├── backend/
│   ├── api/
│   │   ├── main.py                     ← FastAPI app, 90+ routers mounted at /api/v1/*
│   │   ├── routers/                    ← one file per domain
│   │   └── services/
│   ├── agents/                         ← Jarvis agents
│   │   ├── base.py, registry.py, scheduler.py, config.py
│   │   └── implementations/            ← all 8 agents (jarvis, cortex, sentinel, pixel, megaphone, oracle, taskmaster, nexus)
│   └── database/connection.py          ← get_db() pattern
├── frontend/
│   ├── index.html                      ← Google Fonts loaded here
│   └── src/
│       ├── App.tsx                     ← shell + top-level guards; routes are NOT here
│       ├── routes/                     ← 17 route files (~160 routes), lazy-loaded
│       ├── index.css                   ← Design tokens + shell CSS
│       ├── components/
│       │   ├── shell/                  ← Phase 0 primitives: Rail, Topbar, Icon, etc.
│       │   └── layout/AppLayout.tsx    ← Uses Shell wrapper
│       ├── context/
│       │   ├── AuthContext.tsx
│       │   ├── AppearanceContext.tsx   ← data-brand + data-density
│       │   └── ToastContext.tsx
│       ├── hooks/useNow.ts             ← Live countdown ticker
│       ├── pages/                      ← Module screens (Phase 1-2 targets)
│       ├── services/api/               ← domain API modules + barrel
│       └── stores/posStore.ts          ← Zustand
└── docs/
    ├── design/                         ← New design language (9 screens + shell)
    └── reference/                      ← App spec, agent arch, audit reports
```

# IMS 2.0 — Agent Briefing

Self-contained context for any Claude session (local CLI or web) picking up this repo.

---

## 👋 IF YOU ARE A FRESH SESSION TAKING OVER — READ THIS FIRST

**Last session summary** (ended 2026-04-20, 33 commits):

Shipped phases:
- **Phase 0–1.9**: all 9 design-prototype screens ported (Hub, Store Setup, Inventory, Reports, Clinical, POS, Tasks, Print, Jarvis). Design tokens + shell chrome (64px rail + 52px topbar) in `frontend/src/components/shell/`.
- **Phase 2 + 2.5 + 2.6**: all 18 interior modules re-skinned via legacy-class bridge (`.btn-primary`, `.card`, `.input-field`, `.badge-*` redefined to use design tokens). `bv-gold` palette remapped to BV red.
- **Phase 3 + 3.5**: all 7 backend agents implemented (CORTEX, SENTINEL, PIXEL, MEGAPHONE, ORACLE, TASKMASTER, NEXUS) with live toggle UI on `/jarvis`. Registry in `backend/agents/registry.py`. Each agent has seeded config in `backend/agents/config.py`.
- **Phase 4.1–4.4**: all 5 new agents wired to real providers:
  - ORACLE → Claude API (`backend/agents/claude_client.py`) for anomaly narratives + grounded on-demand analysis
  - MEGAPHONE → MSG91 WhatsApp + SMS (`backend/agents/providers.py`) with `DISPATCH_MODE=off|test|live` safety gate
  - PIXEL → Google PageSpeed Insights for real Lighthouse audits
  - NEXUS → Shopify/Razorpay/Shiprocket/Tally (`backend/agents/nexus_providers.py`)
- **Phase 5**: unified cross-agent activity feed (`GET /api/v1/jarvis/agents/activity`) + rendering panel on the Jarvis page between agent grid and chat card.
- **Phase 6.1 — Observability hardening**: Sentry APM fully wired (FastAPI + Starlette integrations, noise-filtered traces sampler, release tag, per-agent-tick transactions via `backend/observability.py::agent_tick_span`). Slack webhook alerting for CRITICAL anomalies from ORACLE (threshold tunable via `SLACK_ALERT_SEVERITY`). 13 unit tests in `backend/tests/test_observability.py`. Contract: every helper fail-soft — missing env = silent no-op.

Plus 5 infra fixes: CORS permanent (reflection + 15 regression tests), CI unbroken (safety/pydantic), admin router SUPERADMIN gate, Rx range tightening, Jarvis canned-reply fix, store pill snake_case transform.

### Phase 6 — next-session menu, pick one

1. ~~**Observability hardening**~~ — DONE in Phase 6.1. Sentry APM + Slack webhooks wired, per-agent-tick transactions tagged, 13 unit tests green. Remaining stretch: OpenTelemetry distributed tracing (Sentry already gives per-agent spans but OTel would let us ship traces to Jaeger/Honeycomb too)
2. **Event bus durability** — `backend/agents/registry.py` dispatches events in-process. Railway runs 4 uvicorn workers, so an event emitted in worker A is invisible to subscribers in worker B. Move to MongoDB change streams or Redis pub/sub so `stock.below_reorder` from SENTINEL reliably wakes TASKMASTER regardless of worker
3. **Feature backlog** — start on the ~166 deferred items in [docs/reference/IMS2_Updated_Feature_Status.md](docs/reference/IMS2_Updated_Feature_Status.md) (footfall tracking, EMI payments, credit note balance tracking per customer, workshop QC checklist, daily stock count with barcode scanning, etc.)
4. **End-to-end prod verification** — pause shipping new features, walk each of the 9 module routes in Chrome (user has the Claude-in-Chrome extension connected to `https://ims-2-0-railway.vercel.app`), log any visual/functional bugs, fix them

### Env vars needed for full agent activation on Railway

| Var | Owner | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | ORACLE, JARVIS chat | Claude calls |
| `AGENT_CLAUDE_MODEL` | ORACLE | Default `claude-haiku-4-5` |
| `PAGESPEED_API_KEY` | PIXEL | Google PageSpeed Insights |
| `FRONTEND_BASE_URL` | PIXEL | Default `https://ims-2-0-railway.vercel.app` |
| `MSG91_API_KEY` | MEGAPHONE | WhatsApp + SMS |
| `MSG91_WHATSAPP_INTEGRATED_NUMBER` | MEGAPHONE | From MSG91 dashboard |
| `MSG91_WHATSAPP_NAMESPACE` | MEGAPHONE | Per-template |
| `MSG91_SMS_TEMPLATE_ID` | MEGAPHONE | DLT-approved |
| `MSG91_SENDER` | MEGAPHONE | DLT-registered 6-char, defaults `BVOPTL` |
| `DISPATCH_MODE` | MEGAPHONE + NEXUS writes | `off` (default) / `test` / `live` |
| `TEST_PHONE` | MEGAPHONE test mode | Only recipient in `DISPATCH_MODE=test` |
| `SENTRY_DSN` | observability | If unset, Sentry quietly skipped |
| `SENTRY_TRACES_RATE` | observability | Default `0.2` — % of HTTP + agent-tick transactions sampled |
| `SENTRY_PROFILES_RATE` | observability | Default `0.1` — CPU profiling sample rate |
| `SENTRY_RELEASE` | observability | Release tag (falls back to `RAILWAY_DEPLOYMENT_ID` → `ims-2.0@dev`) |
| `SLACK_WEBHOOK_URL` | observability | Incoming-webhook URL for CRITICAL anomaly alerts from ORACLE. If unset, alerts silently skipped |
| `SLACK_ALERT_SEVERITY` | observability | Minimum severity to notify — `CRITICAL` / `HIGH` / `MEDIUM` / `LOW` (default `CRITICAL`) |

All calls **fail soft** — missing env = no degraded behavior, just no outbound call, status `SIMULATED` or `FAILED`. A fresh Railway deploy doesn't accidentally spam customers.

### Working preferences (inherited from the user)

- **Commit + push at the end of every phase.** Not batched. Small scoped commits.
- **Always verify frontend after changes.** `tsc --noEmit` + `vite build` + browser preview. Screenshots flaky on Windows; prefer `preview_inspect` / `javascript_tool` eval.
- **No emojis in Python** — Windows cp1252 crash risk.
- **Theme is light-only.** Dark mode removed pre-session.
- **Ask before touching POS** — it's revenue-critical.

### Key files the next session should skim

- [`CLAUDE.md`](CLAUDE.md) (this file) — full briefing below
- [`docs/design/README.md`](docs/design/README.md) — design handoff with tokens, shell, 9 prototypes
- [`docs/reference/IMS2_Agent_Architecture.html`](docs/reference/IMS2_Agent_Architecture.html) — 8-agent spec (hero identity, schedule, event bus)
- [`backend/agents/registry.py`](backend/agents/registry.py) — where agents register + event subscriptions
- [`backend/agents/claude_client.py`](backend/agents/claude_client.py), [`backend/agents/providers.py`](backend/agents/providers.py), [`backend/agents/nexus_providers.py`](backend/agents/nexus_providers.py) — Phase 4 provider wiring patterns
- [`frontend/src/pages/jarvis/JarvisPage.tsx`](frontend/src/pages/jarvis/JarvisPage.tsx) — live agent grid + toggles + activity feed
- [`backend/api/routers/agents.py`](backend/api/routers/agents.py) — Phase 5 `/jarvis/agents/activity` endpoint

---

## What this is

**IMS 2.0** — Retail Operating System for Avinash's Indian optical chains (**Better Vision** + **WizOpt**). Replaces ~15 Excel files, WhatsApp-based task management, and manual ledgers across POS · Inventory · Clinical/Optometry · HR/Payroll · Finance · CRM/Marketing · Vendor Management · Task/SOP enforcement · AI (Jarvis).

Not a POS — a full retail OS. 11 roles. 387+ API endpoints. 65+ frontend pages.

## Stack

- **Frontend:** React 19 + TypeScript + Vite + Tailwind v4 · `frontend/` · port 3000 · deployed to Vercel (`ims-2-0-railway.vercel.app`)
- **Backend:** FastAPI + Python 3.12 · `backend/` · port 8000 · 31 routers at `/api/v1/*` · deployed to Railway (`ims-20-railway-production.up.railway.app`)
- **DB:** MongoDB on Railway · database `ims_2_0`
- **Auth:** JWT HS256, 8h expiry, 11 roles, store-scoped tokens, geo-fenced login for store staff (roles 4-7)
- **Test login:** `admin` / `admin123`

## Active initiative: Design language rollout

The app is being re-skinned with a new operational design language. Source: [docs/design/README.md](docs/design/README.md) (formerly in the user's Downloads, now in-repo).

**Progress:**
- **Phase 0 — Foundation — DONE** (commit 5453c2d). Design tokens, 3 Google Fonts, 64px rail + 52px topbar shell chrome, 19 nav items, AppearanceContext for brand/density, Jarvis rail item gated to SUPERADMIN. See [frontend/src/components/shell/](frontend/src/components/shell/).
- **Phase 1 — Port 9 design screens** (Hub → Store Setup → Inventory → Reports → Clinical → POS → Tasks → Print → Jarvis). In progress.
- **Phase 2 — Restyle remaining ~10 modules** with the new shell (Customers/CRM, Orders, Returns, HR, Purchase, Finance, Workshop, Catalog, Storefront, Marketing).
- **Phase 3 — Agent expansion** — 8 superhero agents (see below).

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

Currently implemented in [backend/agents/implementations/](backend/agents/implementations/): CORTEX + SENTINEL only. The other 6 need to be built in Phase 3.

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

## Key reference docs (in-repo)

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
│   │   ├── main.py                     ← FastAPI app, 31 routers mounted at /api/v1/*
│   │   ├── routers/                    ← one file per domain
│   │   └── services/
│   ├── agents/                         ← Jarvis agents
│   │   ├── base.py, registry.py, scheduler.py, config.py
│   │   └── implementations/            ← CORTEX, SENTINEL (6 more pending)
│   └── database/connection.py          ← get_db() pattern
├── frontend/
│   ├── index.html                      ← Google Fonts loaded here
│   └── src/
│       ├── App.tsx                     ← 41 protected routes, lazy-loaded
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
│       ├── services/api/               ← 14 domain API modules + barrel
│       └── stores/posStore.ts          ← Zustand
└── docs/
    ├── design/                         ← New design language (9 screens + shell)
    └── reference/                      ← App spec, agent arch, audit reports
```

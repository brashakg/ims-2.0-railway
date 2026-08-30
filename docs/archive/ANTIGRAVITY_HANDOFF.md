# IMS 2.0 — Context Handoff for Google Antigravity

> **Purpose:** transfer the full context, intentions, goals, and history of the **IMS 2.0** project from the Claude Code sessions to a fresh agent running inside **Google Antigravity**, so it can continue the program, finish what's left, and debug — with no loss of context.
>
> **How to use this file:** Section 0 is the **prompt to paste into Antigravity**. Everything after it is the reference the prompt tells the agent to read. Open the repo in Antigravity, drop this file at the repo root (or keep it in the workspace), then paste Section 0.

---

## 0. KICKOFF PROMPT — paste this into Google Antigravity

```
You are taking over engineering execution for IMS 2.0, a production retail Operating System
for Avinash's Indian optical retail chains (Better Vision + WizOpt). It was built over many
months across ~10 Claude Code sessions; you are continuing that work in Antigravity.

OWNER CONTEXT (critical): Avinash (brash.akg@gmail.com) is NOT a developer. He sets product
and business direction; YOU own ALL technical execution — code, git, CI, PRs/merges, Railway +
MongoDB ops, debugging. Explain in plain English. Never assume he will read a diff, write/run
code, or use a terminal beyond a copy-paste one-liner. His hands-on tasks are dashboard clicks
(Railway / GoDaddy / gst.gov.in), pasting credentials, and approving decisions.

STEP 1 — GET UP TO SPEED (read in this order, do not skip):
  1. This file (ANTIGRAVITY_HANDOFF.md) end-to-end.
  2. In the repo: CLAUDE.md, README.md, docs/SYSTEM_INTENT.md (supreme business-rule authority),
     docs/reference/BVI_MERGE_PLAN.md.
  3. The distilled Claude-Code memory (curated summaries — your fastest path to context):
     C:\Users\avina\.claude\projects\C--Users-avina-IMS-2-0-CLAUDE-COWORK\memory\
     Read MEMORY.md first, then bvi_merge_into_ims.md, improvement_initiative_campaign.md,
     infrastructure.md, project_business_rules.md, project_agents.md, integrations_golive.md.
  4. Global owner notes: C:\Users\avina\.claude\CLAUDE.md
  5. ONLY IF you need deep history: the raw session transcripts (huge JSONL) in
     C:\Users\avina\.claude\projects\C--Users-avina-IMS-2-0-CLAUDE-COWORK\*.jsonl
     (the latest marathon session is 349ecee1-b396-4d57-a9f8-a536c41f485e.jsonl). See Section 4
     for the session map. Prefer the memory/ summaries over the raw transcripts.

STEP 2 — VERIFY THE GROUND TRUTH (don't trust any doc's counts blindly):
  - Repo: github.com/brashakg/ims-2.0-railway  (local: C:\Users\avina\IMS 2.0 CLAUDE COWORK\ims-2.0-railway)
  - Backend smoke: from repo root run the venv python and import backend/api/main.py app, print len(app.routes)
    (~920 routes expected). Frontend: cd frontend && npx tsc --noEmit && npx vite build.
  - Read Sections 6 (current state) and 7 (what's left) of this file. The app is feature-complete
    and audit-cleared; the remaining work is mostly OWNER-GATED (Section 7) + verification/debugging.

STEP 3 — DO THE WORK (Section 7 has the backlog):
  - First priority is the keystone owner action: the MongoDB volume is capped at 500MB (Section 8),
    which silently blocks ALL index builds. Walk Avinash through growing it to 5GB in the Railway
    dashboard. Then verify the attendance unique index + health_checks TTL build.
  - Then run the app screen-by-screen (test login admin/admin123 on https://ims-2-0-railway.vercel.app
    or local) and fix any bug you find. Continue the build-able backlog. Execute the owner-gated items
    once Avinash unblocks them.

WORKING RULES (non-negotiable — see Section 5 for the full list):
  - Commit + push at the END of each phase (small, conventional commits). Open a PR, watch CI,
    merge --squash ONLY when CI is green.
  - ALWAYS verify before PR: backend = py_compile + import-smoke (0 uncatalogued routes) + pytest;
    frontend = tsc --noEmit + vite build.
  - NO emojis / non-ASCII in Python (Windows cp1252 will crash). Theme is LIGHT-ONLY.
  - ASK before touching POS (revenue-critical). Never show fabricated numbers (Control over
    Convenience, Audit Everything, Fail Loudly — docs/SYSTEM_INTENT.md).
  - NEVER print or commit secret VALUES. Use Railway variable references; print env-var KEYS only.
  - Every new backend route MUST be catalogued in backend/api/services/rbac_policy.py or the
    test_rbac_policy "no uncatalogued routes" test fails.

Acknowledge by summarizing (a) what IMS 2.0 is, (b) the current state, (c) the top 3 things you'll
do next, then proceed.
```

---

## 1. What IMS 2.0 is

A full **Retail Operating System** for Avinash's Indian optical chains **Better Vision** + **WizOpt** — **6 stores** across Jharkhand + Maharashtra, **3 legal entities / 4 GSTINs**. It replaces ~15 Excel files, WhatsApp-based task management, and manual ledgers. Modules: **POS · Inventory · Catalog · Clinical/Optometry · HR/Payroll · Finance/Accounting · CRM/Marketing · Vendor/Purchase · Tasks/SOP · Online Store (e-commerce) · AI ("Jarvis" — 8 agents)**. ~12 roles, ~920 API endpoints, ~65 frontend pages. It is live in production.

**It is not a POS — it is a retail OS.** Core philosophy (docs/SYSTEM_INTENT.md): **Control over Convenience · Audit Everything · Fail Loudly.** The audit trail is immutable even for Superadmin.

---

## 2. Stack, repos, and deployment

| Thing | Detail |
|---|---|
| **GitHub repo** | `https://github.com/brashakg/ims-2.0-railway` |
| **Local clone** | `C:\Users\avina\IMS 2.0 CLAUDE COWORK\ims-2.0-railway` |
| **Backend** | FastAPI + Python, `backend/` — ~60 routers at `/api/v1/*`, ~920 routes. venv at **repo root**: `.venv\Scripts\python.exe` |
| **Frontend** | React 19 + Vite + TypeScript + Tailwind v4, `frontend/` |
| **Database** | **MongoDB** on Railway, database `ims_2_0` |
| **E-commerce (BVI)** | `ecommerce/` git subtree — a Next.js/Prisma/Postgres Shopify PIM (being retired *into* IMS; see Section 6) |
| **Backend deploy** | **Railway**, project **"IMS 2.0"** = `b9ccf10c-66d9-4632-90a7-98f6f5a23efa`, prod env `0e0919d7-bcf0-4df3-8be1-493dd03ca06f`. Services: `ims-2.0-railway` (backend), `MongoDB`, `Redis`, `Postgres`. |
| **Frontend deploy** | **Vercel** → `https://ims-2-0-railway.vercel.app` |
| **Test login** | `admin` / `admin123` (SUPERADMIN) |
| **Storefront** | `bettervision.in` (Shopify); BVI admin domain `uniparallel.com` |

**Railway CLI** is authenticated locally (`railway whoami`, `railway status`, `railway run --service <svc> <cmd>`). The Railway **MCP is NOT authorized** for the agent — use the CLI. `railway run` injects service env vars into a local subprocess (use it to reach prod Mongo without surfacing secrets).

---

## 3. Read these to get up to speed (priority order)

**In the repo (authoritative):**
- `CLAUDE.md` — agent briefing + module summaries (its counts can be stale; verify in code).
- `README.md` — comprehensive system reference (architecture, every module, API map, agents, data layer, business rules).
- `docs/SYSTEM_INTENT.md` — **supreme** business-rule authority. Any code violating it is wrong.
- `docs/reference/BVI_MERGE_PLAN.md` — the e-commerce consolidation blueprint.
- `docs/reference/SOFTWARE_IMPROVEMENT_INITIATIVE.md` — the improvement-initiative tracker.
- `docs/reference/IMS2_*Feature_Status*` — feature catalog (**STALE — verify in code, don't trust**).

**Distilled Claude-Code memory** (curated summaries — fastest context) at
`C:\Users\avina\.claude\projects\C--Users-avina-IMS-2-0-CLAUDE-COWORK\memory\`:
- `MEMORY.md` (index — start here)
- `bvi_merge_into_ims.md` (the e-commerce re-platform — current end state)
- `improvement_initiative_campaign.md` (the parallel-workflow improvement method + lessons)
- `infrastructure.md`, `project_business_rules.md`, `project_agents.md`, `integrations_golive.md`
- `claude_code_environment.md`, plus dated `session_2026_05_*.md` recaps.

**Global owner notes:** `C:\Users\avina\.claude\CLAUDE.md`.

**Raw session transcripts (deep history, large JSONL — fallback only):**
`C:\Users\avina\.claude\projects\C--Users-avina-IMS-2-0-CLAUDE-COWORK\*.jsonl`
(one file per session; sub-agent transcripts under `<sessionId>/subagents/`). The latest marathon
session — which did the BVI Phases 2–5+3b, Purchase Invoice, Campaigns, Autopilot, attendance,
audit-chain flake, and the stub audit — is `349ecee1-b396-4d57-a9f8-a536c41f485e.jsonl`.

---

## 4. Session map (what built IMS 2.0)

These are the Claude Code sessions (most recent first). The transcript JSONL files live in the
`projects\C--Users-avina-IMS-2-0-CLAUDE-COWORK\` folder above. To full-text search them from a
fresh Claude session you'd use the CCD session tools; from Antigravity, grep the JSONL or read
the `memory/session_*.md` recaps which summarize each.

| Title | When | Notes / PR |
|---|---|---|
| **(current marathon)** BVI Ph2–5+3b, Purchase Invoice, Campaigns, Autopilot, attendance, audit-chain, stub audit | 2026-06-01 → 06-04 | ~30 PRs (see Section 6) — transcript `349ecee1….jsonl` |
| Continue IMS 2.0 handover 20-05-2026 | 2026-05-31 | PR #163 (merged) |
| Git branch cleanup | 2026-05-30 | housekeeping |
| IMS app comprehensive testing | 2026-05-29 | PR #337 (Playwright E2E, OPEN) |
| Reconcile contact-lens GST rate (5% vs 12%) | 2026-05-24 | PR #255 |
| Audit IMS 2.0 visual and functional issues | 2026-05-07 | QA |
| Review codebase and get up to speed (fork) | 2026-04-20 | onboarding |
| Fix hardcoded GST rate in backend | 2026-03-27 | GST fix |
| Review project links / context (INVENTORY MODULE) | 2026-03 | earliest |

There is also a **second concurrent Claude Code session** that runs alongside the marathon (it
shipped go-live features e.g. PR #450 opening-stock importer, #451 readiness checklist). When two
agents work the same repo, take non-colliding lanes and rebase before merge.

### 4a. EXACT local transcript inventory (these are NOT in the git repo — read them on disk)

The IMS 2.0 work lives in **local Claude Code session transcripts** (JSONL) under **TWO** project
folders (Claude Code encodes the working dir into the folder name). Antigravity has no context for
these unless it reads them:

**A) `C:\Users\avina\.claude\projects\C--Users-avina-IMS-2-0-CLAUDE-COWORK\`  — 12 IMS 2.0 sessions**
- `349ecee1-b396-4d57-a9f8-a536c41f485e.jsonl` (~7.8 MB, 2026-06-04) — **LATEST marathon**: BVI Phases 2–5+3b, Purchase Invoice (council), Campaigns rebuild, Catalog Autopilot AI fix, attendance fix, audit-chain CI-flake fix, app-wide stub audit, this handoff.
- `a9b59c44-fad3-4cb5-9423-b01c9bb5192b.jsonl` (~12.5 MB, 2026-06-01)
- `12b5aaa5-f957-46aa-93b7-32f4aecf18db.jsonl` (~125 MB, 2026-05-29) — **huge**, heavy multi-agent run
- `b97c0752-…` (~11.3 MB, 05-29), `18770f2f-…` (~7.8 MB, 05-29), `795d57de-…` (05-28), `5eb75fab-…` (05-28), `cd85526d-…` (05-24), `875f56eb-…` (05-24), `f630a051-…` (05-31), `ef94c629-…`/`feecdee2-…` (05-29) — smaller.

**B) `C:\Users\avina\.claude\projects\C--Users-avina-INVENTORY-MODULE\`  — 1 earlier session**
- `a3492fcc-3ede-41d6-b0ba-a02f1a445ea7.jsonl` (~25.6 MB, 2026-06-01) — the **inventory-module root BEFORE it became IMS 2.0**; read for origin context.

(Sub-agent transcripts are under `<sessionId>\subagents\*.jsonl` — skip unless you need tool-level detail.)

**How to read (large files — be selective):** each line is one JSON event
`{"type":"user"|"assistant","message":{"content":...},...}`. Extract the owner's intent fast with:
```powershell
Get-Content "<path>.jsonl" | ForEach-Object {
  try { $o = $_ | ConvertFrom-Json
        if ($o.type -eq 'user' -and $o.message.content) {
          if ($o.message.content -is [string]) { $o.message.content }
          else { ($o.message.content | Where-Object { $_.type -eq 'text' }).text } } } catch {} }
```
(swap `'user'`→`'assistant'` to read what the agent concluded.) **Read the `memory/session_2026_05_*.md`
recaps first** (they summarize several of these); only open a raw JSONL for a session the recaps don't
cover (notably the INVENTORY-MODULE one) or to trace a specific decision/error. Start with `349ecee1`
(latest = current state), then go backward by date as needed.

---

## 5. Non-negotiable rules + working preferences

**Business rules** (docs/SYSTEM_INTENT.md + docs/reference/IMS2_Complete_App_Summary):
- **Pricing:** MRP > offer_price → blocked at DB. offer==MRP → role cap applies, further limited by category + brand cap. Category caps: MASS 15% · PREMIUM 20% · LUXURY 5% · SERVICE 10% · NON_DISCOUNTABLE 0%. Luxury brand caps: Cartier/Chopard/Bvlgari 2% · Gucci/Prada/Versace/Burberry 5%. **Pricing is GST-INCLUSIVE.**
- **GST:** 5% frames/optical-lenses/contacts/readers · 18% sunglasses/watches/accessories · NIL hearing-aid device. **Intra-state = CGST+SGST; inter-state = IGST** (driven by `place_of_supply`). Editable HSN/GST master overrides the static table.
- **Rx validation:** SPH −20..+20 (0.25) · CYL −6..+6 (0.25) · AXIS 1–180 · ADD +0.75..+3.50 (0.25).
- **Geo-fenced login:** store roles (levels 4–7) must be within 500m of their store; levels 1–3 exempt.
- **Audit trail immutable** (hash-chained `audit_logs`), even for Superadmin.

**Working preferences (apply exactly):**
- Owner is NOT a developer → explain in plain English; give exact dashboard step-by-step; prefer doing ops yourself via CLI when safe.
- Commit + push at END of each phase (not batched); small, conventional commits.
- **Always verify** before PR: backend `py_compile` + import-smoke (`JWT_SECRET_KEY=test ENVIRONMENT=test`, 0 uncatalogued routes) + `pytest`; frontend `npx tsc --noEmit` + `npx vite build`.
- PR flow: feature branch → push → `gh pr create` → watch CI → `gh pr merge <n> --squash --delete-branch` when CI is **green/CLEAN**. (git-subtree PRs merge with `--merge` to preserve history.)
- **No emojis / non-ASCII in Python** (Windows cp1252 crash). **Theme is light-only.** **Ask before touching POS.**
- **Never expose/commit secret VALUES.** Print env-var KEYS only. Use `railway run` / variable references.
- **Every new route → catalogue in `backend/api/services/rbac_policy.py`** (else `test_rbac_policy` fails).
- A **Vercel free-tier deploy rate-limit** can show PRs `UNSTABLE` while all GitHub Actions are green — that is not a code failure; judge by the required GitHub checks.

---

## 6. Current state (what is DONE)

The app is **feature-complete and audit-cleared.** Highlights shipped in the latest marathon (~30 PRs, all merged, CI green):

- **BVI e-commerce re-platform — BUILD-COMPLETE** (Phases 1–5 + 3b). The old separate BVI Shopify PIM is being rebuilt natively inside IMS as the **Online Store** module: catalog variants, Collections, Menus/mega-menu, Image Design Queue, a **DARK/gated Shopify GraphQL push** engine, and the **online-order mapper** (Shopify orders → IMS orders, count-once). Only the owner-gated **cutover** (Phase 6) + **decommission** (Phase 7) remain.
- **Purchase Invoice** (council-driven): first-class `purchase_invoice` (line items + HSN/GST), the **place-of-supply IGST fix** (inter-state purchases were mis-booked CGST+SGST; now correctly IGST), Create-Invoice-from-GRN, **3-way match** (PO↔GRN↔Invoice), and **inventory valuation** (real unit cost stamped on received stock, moving-average true-up).
- **Campaigns** rebuilt from a disconnected mock into a real module (CRUD + 6 live segments + scheduled/triggered sends through the DISPATCH_MODE-gated WhatsApp/SMS infra + analytics).
- **Catalog Autopilot** fixed — it returned 0 results in prod (brand-site scraper fails on JS sites; other sources gated). Added a reliable **Claude AI-enrichment source** (specs/description/HSN/GST from brand+model) + UI surfacing + a "create product" payoff.
- **Attendance** — fixed duplicate records (root cause: the unique index was never built at startup + a datetime/string date mismatch), idempotent check-in, admin edit, a dedicated menu, HR summary; prod duplicate cleaned.
- **Reliability:** killed a recurring **audit-chain CI flake** (tz-aware datetime broke the hash round-trip; was masked by a mongo-connect-timeout skip) which also fixed a real prod `/audit/verify` false-tamper bug; added **health_checks TTL + self-prune** (it was the biggest, unbounded collection).
- **Stub/disconnect audit** (app-wide): fixed finance screens that showed **hardcoded/blank money** (FinanceSummary KPI cards, a real persisted "Close Day", removed fake ₹0 reconciliation, wired ExecutiveDashboard), retired 3 dead duplicate pages (PromotionEngine, PurchaseOrderDashboard, VendorManagement) to their real equivalents, wired dead buttons (dead-stock, Create-PO, **real QR code**, CSV export), re-homed the vendor-portal-link button, and added a **real logo upload**.

**8 "Jarvis" AI agents** (SUPERADMIN-only): JARVIS, CORTEX, SENTINEL (health), PIXEL (UI/a11y), MEGAPHONE (marketing/WhatsApp), ORACLE (analytics, Claude-backed), TASKMASTER (SLA/reorder), NEXUS (integrations: Shopify/Razorpay/Shiprocket/Tally).

---

## 7. What's LEFT — the backlog for Antigravity

### 🔒 Owner-gated (needs Avinash, not code)
1. **MongoDB volume → 5 GB** (the keystone — see Section 8). The `mongodb-volume` is capped at **500 MB** while Redis/Postgres are 5 GB. Mongo needs 500 MB *free* to build any index, impossible on a 500 MB total → `ensure_indexes` silently fails for **every** collection (attendance unique index, health_checks TTL, etc. cannot build). **Resize is dashboard-only** (Railway MCP unauthorized; CLI has no volume-resize). Walk Avinash through: Railway → IMS 2.0 → MongoDB service → Volume → grow to 5 GB → it redeploys (~30s). Then a restart auto-builds the indexes — verify them.
2. **BVI go-live cutover** (Online Store Phase 6). After the volume + a low-traffic window: set `IMS_SHOPIFY_WRITES=1` + `DISPATCH_MODE=live` on Railway so IMS takes over Shopify writes from the old BVI app; migrate the live BVI Postgres data into Mongo; then decommission the BVI app + Postgres (Phase 7). Full plan in `docs/reference/BVI_MERGE_PLAN.md` + `memory/bvi_merge_into_ims.md`.
3. **Purchase Invoice Phase 3 / 4** — purchase-JV ledger posting + a **Tally purchase-voucher export**, and **ITC eligibility (Sec 17(5)) / RCM / GSTR-2B auto-match**. Needs the **accountant's chart-of-accounts** + the **GST-portal data feed**. (PR #411 "ITC-eligibility filter 17(5)/36(4)" already covers part of Phase 4.)
4. **Live integrations go-live** — Razorpay / Shiprocket / Tally / MSG91-WhatsApp need real creds in Railway env (or Settings→Integrations). Everything is built and fail-soft/SIMULATED until creds + `DISPATCH_MODE=live`.
5. **DNS** — point `uniparallel.com` (and subdomains) per `memory/bvi_consolidation.md`.

### 🟢 Build-able (do in Antigravity)
- **End-to-end debugging / verification:** log in (admin/admin123), walk each module screen-by-screen on prod (or a local stack), and fix anything broken. There may be more stub/disconnected spots beyond the audit's top tier — the audit method (grep for hardcoded data / "coming soon" / `useState([])`-with-no-fetch / dead `onClick`) is in `memory/improvement_initiative_campaign.md`.
- **A few intentional honest stubs** remain (e.g. Setup→Employees placeholder) — low priority, they're honest, not misrepresentations.
- **PR #337** (Playwright E2E nightly CI) is still OPEN from a prior session — rebase + green it if you want CI E2E coverage.
- **Docs cleanup** — archive the stale `docs/reference/IMS2_*Feature_Status*` / `COMPLETE_FEATURE_LIST` / `CONTINUATION_PROMPT` docs and refresh the counts.

---

## 8. Key debugging facts + gotchas (learned the hard way)

- **The 500 MB Mongo volume is the #1 latent issue** (Section 7.1). Symptom: indexes silently absent in prod even though declared in `schemas.py` + created in `connection.py`. Check `railway volume list`. `ensure_indexes` is fail-soft (logs `[WARN] Index creation error (non-fatal)` and skips).
- **`ANTHROPIC_API_KEY` IS set** on the backend service (verified) — ORACLE/JARVIS + the Catalog Autopilot AI source work.
- **Audit hash-chain determinism:** `audit_chain._json_default` must serialize datetimes identically across the Mongo write→read round-trip — truncate µs→ms AND normalize tz-aware→naive-UTC (both fixed). A non-deterministic chain hash causes a false `/audit/verify` tamper alarm. The db-backed `test_proposal_audit_chain` SKIPS without a local mongod (it ran/failed only when CI's mongo answered within 1.5s) — pure-function guards live in `test_audit_chain_canonical.py`.
- **CI is reliably green now** — merge by the required GitHub checks (`test (3.10)`, `test (3.11)`, `test-and-build (18.x/20.x)`, `security`). The lone `Vercel` red on backend-only PRs is the free-tier deploy rate-limit, not a failure.
- **Run prod-Mongo ops** via `railway run --service MongoDB bash -c 'mongosh "$MONGO_PUBLIC_URL" ...'` or `railway run --service MongoDB <repo-root-venv-python> <script.py>` (reads `MONGO_PUBLIC_URL` from the injected env — never surfaces the secret).
- **Worktrees / parallel agents:** the marathon shipped via pre-created git worktrees off `origin/main` + parallel sub-agents; if two agents edit the same file (e.g. `rbac_policy.py`, `main.py`, `App.tsx`), expect additive conflicts → resolve by keeping both blocks. Don't run two agents building the *same* feature (duplicate-PR collisions).
- **New frontend API services:** import the service DIRECTLY from its module (`import { xApi } from '../../services/api/x'`), not via the `services/api/index.ts` barrel (a re-export resolution quirk, TS2614).
- **Backend verify one-liner** (from repo root, git-bash):
  `JWT_SECRET_KEY=test ENVIRONMENT=test .venv/Scripts/python.exe -c "import sys;sys.path.insert(0,'backend');from api.main import app;print(len(app.routes))"`
- **Frontend verify:** `cd frontend && npx tsc --noEmit && npx vite build`.

---

## 9. The development method that worked (recommended for Antigravity)

For substantial work the marathon used a **research → audit → council → implement → verify → PR** pipeline with parallel agents. Antigravity can mirror this with its own agent/parallel features:
1. **Audit/Understand** — read the code + grep for the problem class; don't trust stale docs.
2. **Council** (for design decisions) — get 3–5 independent expert "lenses" (domain/GST-compliance/accounting/architecture/UX) then synthesize a SHIP-NOW / OWNER-SIGN-OFF / DEFER plan. (This is how the Purchase Invoice work was scoped.)
3. **Implement** small + additive + fail-soft, following existing patterns.
4. **Verify** (backend import-smoke + 0 uncatalogued + pytest; frontend tsc + build).
5. **PR** → CI → squash-merge when green. Commit per phase.

---

## 10. Owner action checklist (hand this to Avinash)

| # | Action | Where | Unblocks |
|---|---|---|---|
| 1 | Grow **mongodb-volume → 5 GB** | Railway dashboard → IMS 2.0 → MongoDB → Volume | All DB indexes; prerequisite for cutover |
| 2 | Set `IMS_SHOPIFY_WRITES=1` + `DISPATCH_MODE=live` (after #1 + a quiet window) | Railway env | BVI Shopify cutover go-live |
| 3 | Add integration creds (Razorpay / Shiprocket / Tally / MSG91) | Railway env / Settings→Integrations | Live payments, shipping, accounting, WhatsApp |
| 4 | Provide chart-of-accounts + GST-portal/GSTR-2B access to the accountant | — | Purchase-Invoice Phase 3/4 |
| 5 | Point `uniparallel.com` DNS | GoDaddy | BVI admin domain |

---

*Generated at the IMS 2.0 → Antigravity handoff. Repo: brashakg/ims-2.0-railway. Treat docs/SYSTEM_INTENT.md and the memory/ summaries as the source of truth; verify everything else in code.*

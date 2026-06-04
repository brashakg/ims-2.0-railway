# IMS 2.0 — Master Coverage Tracker

> The single status board for the **Total Coverage Program**. Every cell is tracked to GREEN.
> Status legend: `GREEN` (verified good) · `AUDIT` (under audit this wave) · `FIX` (defect found, fix queued/in-flight) · `BUILD` (new feature in flight) · `UNKNOWN` (not yet examined) · `GATED` (blocked on an owner-only action).
>
> This supersedes nothing in `docs/SYSTEM_INTENT.md` (supreme authority). It builds on
> [`SOFTWARE_IMPROVEMENT_INITIATIVE.md`](SOFTWARE_IMPROVEMENT_INITIATIVE.md) (modules 1–14 already run through research→audit→council→ship) and the FE↔BE seam-audit loop (PRs #460–462).

---

## Phase 0 — ground truth (verified 2026-06-04)

| Check | Result |
|---|---|
| Backend import smoke (`api.main` app) | **921 routes**, 0 import errors |
| Frontend `tsc --noEmit` | **exit 0** |
| RBAC policy gate (`test_rbac_policy.py`) | **29 passed** — 0 uncatalogued routes |
| FE page directories | 26 (`frontend/src/pages/*`) |
| FE routes (`path=` in App.tsx) | 88 |
| Backend routers | 60 files under `backend/api/routers/` |
| Working branch | `claude/hr-finance-orders-fixes` (3 ahead / 1 behind `main`) |

---

## The Coverage Matrix

### Axis 1 — Modules (status seeded from the Improvement Initiative; re-confirmed by Wave 1 seam audit)

| # | Module | Prior status | Wave-1 lane | Status |
|---|--------|--------------|-------------|--------|
| 1 | Clinical / Optometry | ✅ Shipped (#392, C1–C6-A) | clinical-rx | AUDIT |
| 2 | POS / Billing / Orders / Returns | ✅ Shipped (#393) + #368/#373/#376 | pos-orders-returns | AUDIT |
| 3 | Finance / Accounting / GST | ✅ Shipped (#394) | finance-gst | AUDIT |
| 4 | Inventory / Transfers / Stock | ✅ Shipped (#395) | inventory-transfers | AUDIT |
| 5 | CRM / Customers / Loyalty | ✅ Shipped (#396) + #402 | crm-loyalty-marketing | AUDIT |
| 6 | Purchase / Vendor | ✅ Shipped (#397) | purchase-onlinestore | AUDIT |
| 7 | Catalog / Pricing / Lens catalog | ✅ Audited mature | catalog-pricing | AUDIT |
| 8 | Workshop | ✅ Audited mature | (in pos lane: workshop auto-link) | GREEN* |
| 9 | HR / Payroll | 🔬 audit-next (engine careful) | hr-payroll | AUDIT |
| 10 | Marketing / Campaigns | ✅ Shipped (#399) | crm-loyalty-marketing | AUDIT |
| 11 | Settings / RBAC | ✅ Shipped (#398) | tasks-settings-admin | AUDIT |
| 12 | Tasks / SOP / Notifications | ✅ Audited mature | tasks-settings-admin | AUDIT |
| 13 | Online Store (BVI / Shopify) | ⏳ build-complete, cutover GATED | purchase-onlinestore | AUDIT |
| 14 | Reports / Dashboards / Analytics | ⏳ queued | reports-analytics-jarvis | AUDIT |
| 15 | Jarvis / AI agents | ✅ Audited (18 routes superadmin-gated) | reports-analytics-jarvis | AUDIT |
| 16 | Org / Settings / Entities / Stores | ✅ part of Settings | tasks-settings-admin | AUDIT |

\* GREEN with a date marker: re-verify only on regression.

### Axis 2 — Screens (~88 routes / 26 page dirs)
Status: **AUDIT** — Wave 1 seam audit reads every FE page against its API service + router. Per-screen green/red recorded in `docs/reference/deep-dive/<module>.md` after each lane closes.

### Axis 3 — Roles (12) & RBAC
Baseline **GREEN**: `test_rbac_policy` (0 uncatalogued routes) + `test_rbac_access_matrix` (455 cases) + request-time middleware (#364/#366). Per-role end-to-end "can do its whole job / blocked from what it must not" re-checked per module in Wave 1.

### Axis 4 — Users / Auth / Geo-fence
JWT HS256, store-scoped tokens, geo-fence (roles 4–7 within 500m), store-switch re-issue. Status: **AUDIT** (verify store-switch token re-issue + geo edge cases in Wave 1 tasks-settings lane).

### Axis 5 — Stores (6) / scoping & isolation
Per-store GSTIN, pricing, inventory, invoice prefix, geo; cross-store transfers + consolidated views. Status: **AUDIT**.

### Axis 6 — Brands / luxury caps
Cartier/Chopard/Bvlgari 2% · Gucci/Prada/Versace/Burberry 5%; canonical `pricing_caps` resolver = **GREEN** (min(category, luxury-brand), float-tolerant). Catalog completeness/enrichment: AUDIT.

### Axis 7 — Products / categories / GST
frames/optical-lens/contact-lens 5% · sunglasses/watches/accessories 18% · eye-test SAC 9993 0%. Per-line CGST/SGST/IGST split shipped (#368/#370). Status: **GREEN** for the rate table; serialization/Rx-applicability AUDIT.

### Axis 8 — Endpoints (FE↔BE parity)
The core Wave-1 objective. Status: **AUDIT** — every FE call → live BE route; every BE route reachable + rbac-catalogued.

### Axis 9 — Business rules (SYSTEM_INTENT)
Pricing laws, GST classification, Rx ranges, immutable hash-chained audit. Status: **GREEN** (enforced + tested) with per-module spot-checks in Wave 1.

### Axis 10 — Data integrity
Indexes + validation per collection, no orphans, money reconciles (amount_paid/balance_due, ledgers, ITC), idempotency on order/invoice/payment. Status: **AUDIT** — **NOTE the keystone GATED item below** (Mongo volume → indexes).

### Axis 11 — Non-functional
Performance (N+1, bundle), a11y, error/empty states, mobile, security, observability (Sentry/Slack wired). Status: **AUDIT**.

---

## Owner-gated items (cannot be done in code — surfaced with exact steps)

| Item | Why it blocks | Exact owner step |
|---|---|---|
| **MongoDB volume → 5GB** (keystone) | 500MB cap → no free space → `ensure_indexes` silently fails for ALL collections → data-integrity axis can't go green; also blocks BVI cutover | Railway dashboard → IMS 2.0 project → MongoDB service → Volume → resize to 5GB → restart (indexes auto-build) |
| BVI Shopify cutover | go-live of online store | Flip `IMS_SHOPIFY_WRITES=1` + `DISPATCH_MODE=live` after the volume grows + a quiet window |
| Integration creds | Razorpay/Shiprocket/Tally/MSG91 live calls | Paste keys into Railway variables |
| Chart-of-accounts mapping | Purchase-JV / ITC GSTR-2B | Accountant input |
| `JWT_SECRET_KEY` / `SEED_SECRET` | prod auth hardening | Set on Railway |

---

## Wave log

### Wave 1 (2026-06-04) — IN PROGRESS
- Workflow `ims2-total-coverage-wave1`: 10 FE↔BE seam-audit lanes + 3 research buckets (India-compliance, competitor-gap, retail-OS/AI) → lead synthesis into ranked master defect list + feature backlog.
- Output lands in: this tracker (statuses), `docs/reference/deep-dive/<module>.md` (per-module), `docs/reference/research/*` (findings + sources), `docs/reference/IMS2_FEATURE_ROADMAP.md` (backlog).
- Wave 2 = fix the top confirmed defects in parallel non-colliding lanes; build top researched features.

_(Appended each wave.)_

# IMS 2.0 — Feature Status (Ground-Truth Rebuild)

**Rebuilt 2026-05-29** from a live audit of the actual codebase. This replaces the
stale Phase-6.7 status (preserved at the bottom) whose counts were wrong by a wide
margin.

---

## ⚠️ Why this file was rebuilt

The previous version claimed **130 built / 8 partial / 157 remaining**. That was
substantially wrong: a large majority of the "❌ Not Built" items **already ship in
code**. The history just landed faster than this file got updated (a long QA sweep
+ ~50 PRs landed after the doc was last meaningfully touched). Proven during the
audit:

| Doc said ❌ "Not Built" | Reality |
|---|---|
| Payroll — *"entire module, 1/10 built"* | Full router + `payroll_engine.py` + **4 test files** |
| Outstanding receivables aging | `GET /finance/outstanding`, due-date buckets (`finance.py:633`) |
| GST filing prep (GSTR-1/3B) | `gstn_export.py` + `/reports/gstr1`, `/reports/gstr3b`, `/reports/gstr*/gstn-json` |
| Tally export | `payroll_exports.py` + finance/reports |
| P&L by store / by category | `/finance/pnl/by-store`, `/finance/pnl/by-category`, `/reports/profit/by-*` |
| Period locking | `/finance/period-lock` + `_period_locked` guards in expenses & finance |
| Cash flow statement | `/finance/cash-flow` + `/finance/cash-flow-forecast` |
| Tasks/SOPs — *"1/11 built"* | `auto-generate`, `sop-templates`, `integrity/fake-closures`, `integrity/silent`, SLA escalation, `{id}/reassign` — all exist |
| Dashboard — *"12 missing"* | **All 12** widgets exist in `dashboard_widgets.py` |
| EMI payment | `PaymentMethod.EMI` + `emi_months` + interest calc (`orders.py:316,1574`) |

**Building "from the top of the old list" would have meant rebuilding live features.**

---

## How this was verified (so you can trust it)

- **Backend surface** — imported the live FastAPI app and enumerated routes:
  **811 API routes across 50 domains** (the "387 endpoints" in CLAUDE.md and even
  the README's "~677" are stale). Endpoint existence = **HIGH confidence**.
- **Frontend wiring** — grepped `frontend/src` for each endpoint to tell "surfaced
  in UI" from "backend-only". **MEDIUM confidence** (a ref ≠ a working screen).
- **NOT verified here** — runtime correctness / end-to-end behaviour. ✅ means
  "the code path exists", **not** "QA-passed in a browser". A live click-through of
  the 9 module routes (the Phase-6 "End-to-end prod verification" item) is still the
  only way to confirm behaviour, and is the single most valuable QA action left.

### Legend
- ✅ **Done** — backend + surfaced in UI
- 🔌 **Backend-only** — endpoint exists, not (or barely) wired into the UI → *cheap win*
- 🚪 **Gated** — built but disabled by env (needs API keys / `DISPATCH_MODE=live`)
- ❌ **Genuinely missing** — no code found
- 🐞 **Bug/risk** — verified defect (see [Bugs & Risks](#-bugs--risks-verified-in-code))

### Corrected counts (honest estimate)

The old "157 remaining" is mostly fiction. After reconciliation the picture is
roughly:

| | Estimate | Notes |
|---|---|---|
| ✅ Done (built + surfaced) | **~245–260** | the bulk of every operational module |
| 🔌 Backend-only (UI wiring left) | **~10–15** | churn-risk list, profit/by-store, workshop QC UI, etc. |
| 🚪 Gated (built, needs keys/flag) | **~10** | MSG91, Shopify, Razorpay, Shiprocket, Tally, PageSpeed |
| ❌ Genuinely missing | **~25–30** | concentrated in 4 themes (below) + training content |
| 🐞 Verified bugs/risks | **4** | DND tz, scheduler singleton, AI-constitution, doc-drift |

> Precise per-item counts are deliberately avoided — false precision is what got
> the old doc into trouble. The themes below are what matters.

---

## Module reconciliation (old "missing" → reality)

### Dashboard — old: 12 missing → reality: ✅ all 12 exist
Every claimed-missing widget has an endpoint in `dashboard_widgets.py`:
pending deliveries (`orders/pending/delivery`), call reminders (`follow-ups`),
daily stock-count status, task completion %, eye-test count, store-vs-target
(`analytics/store-target-today`), escalations (`tasks/escalations`,
`admin/escalations`), attendance compliance (`hr/attendance-compliance`),
HR summary (`hr/summary-today`), system health (`admin/system-health`), AI
insights (`jarvis` activity). Whether each is *rendered* on the Hub is a UI-verify
item, not a build item.

### Tasks / SOPs — old: 1 built / 10 missing → reality: ✅ essentially all built
`auto-generate`, `scan/payment-variance`, `sop-templates` + `sop-checklist`,
P0–P4 colour coding (frontend `TasksDashboard.PRIORITY_COLORS`), `auto-escalate-overdue`
+ `task_sla`, silence detection (`integrity/silent`), fake-closure detection
(`integrity/fake-closures`), in-app assignment (`{id}/reassign`, `{id}/assign`),
SLA countdown chip (frontend), daily checkboxes (`checklists/{type}/complete-item`).
Audit trail via `audit_logs`.

### Inventory — old: 11 missing → reality: ✅ ~10 of 11 built
Stock count (`stock-count/start|items|complete`), barcode scanning
(`stock-count-scan`), variance/shrinkage (`accountability/shrinkage`), non-moving
(`non-moving`), AI transfer (`transfer-recommendations`), brand sell-through
(`sell-through-analysis` + `reports/inventory/brand-sellthrough`), CL batch/expiry
(`contact-lenses/expiry-status`), power grid SPH×CYL (`lenses/power-grid`),
dump/overstock (`overstock-analysis`), staff accountability (`accountability`).
**Possible gap:** product stock-photo gallery (see true backlog).

### Reports — old: 12 missing → reality: ✅ all 12 exist
`sales/comparison`, `sales/growth` (MoM/YoY), `profit/by-category`,
`profit/by-store`, `discount/analysis`, `staff/ranking`, `stock/count`,
`clinical/eye-tests`, `finance/expense-vs-revenue`, `customers/acquisition`,
`inventory/brand-sellthrough`. Plus extras the old doc never listed:
`sales/price-bands`, `sales/lens-deep-dive`, `sales/seasonality`,
`purchase/recommendations`, `walkouts/footfall-audit`.
🔌 `profit/by-store` has **0 frontend refs** — backend-only.

### Clinical — old: 6 missing → reality: ✅ ~5 of 6 built
Rx validity (`prescriptions/{id}/validate`, `patient/{id}/valid`), A5 print
(`clinical/prescriptions/{id}/print`), token/queue (`clinical/queue` + `queue/stats`),
redo tracking (`prescriptions/{id}/redo`, `/redos`), abuse detection
(`clinical/abuse-detection`). ❌ **Family/household Rx view** — genuinely missing.

### Workshop — old: 5 missing → reality: ✅ ~all built
Lens order tracking (`jobs/{id}/lens-status`), QC checklist (`jobs/{id}/qc`,
`/rework`), ready-notification (`jobs/{id}/notify-ready` — send 🚪 gated on
dispatch), dashboard KPIs (`dashboard-kpis`). 🔌 QC checklist **UI** is thin
(`/qc` has 0 direct frontend refs) — wiring/verify item.

### Finance / Expenses — old: 11 + 8 missing → reality: ✅ almost entirely built
Finance: AR aging, AP (`ap_engine`, `net_payable`), P&L, cash-flow + forecast,
period lock, GSTR-1/3B, ITC reconcile (`itc_reconcile`), cash register.
Expenses: per-(role,category) caps (`expense_category_caps`), bill hashing
(`hashlib`), duplicate-bill watch-list, approval roles, advances + settlement,
period-lock guard. ❌ **Dual-mode "full ops vs survival" budgeting** — not found;
the only true finance gap.

### Catalog / Purchase — old: 6 + 4 missing → reality: ✅ mostly built
Bulk import (`catalog/products/import`), export, MRP/offer validation
(`pricing_caps`), store activation (`online-status`), store barcodes
(`labels.py`, `reconcile-store-barcodes`), Shopify sync (`sync-shopify`,
`bulk-sync-shopify` — 🚪 needs keys). Purchase: PO→GRN→serialized stock, partial
receipt, vendor returns. **Possible gap:** structured product-photography workflow.

### CRM / Marketing — old: 5 + 6 missing → reality: ✅ mostly built
Customer 360 (`crm/customers/360`), RFM segmentation (`segment/rfm`), churn risk
(`churn-risk/list` — 🔌 0 frontend refs, backend-only), follow-ups (`auto-generate`,
`due-today`), referrals, NPS (`nps-survey`, `nps-dashboard`), Rx-expiry alerts,
walk-in/walkout recovery, WhatsApp via MSG91 (🚪 gated). ❌ **OTP customer
verification**, ❌ **customer-facing Rx access portal**, ❌ **marketing-agency
oversight dashboard** — genuinely missing.

### HR / Payroll — old: 7 + 9 missing → reality: mixed
Payroll: ✅ **complete** (engine + run flow + payslip/Tally JV/PF ECR exports +
4 test files). HR: ✅ attendance (`check-in/out`, `grid`, compliance), leaves +
balances. ✅ **Attendance engine** now shipped: **shift config per employee**,
**late-mark auto-calc**, **week-off swap approval** (no self-approval),
**geo-fenced enforcement on check-in** (roles 4-7), and a **monthly LWP report**
(read-only — surfaced for the accountant, never auto-applied to payroll).
**Overtime is intentionally NOT built** (product-owner decision: no overtime).

### POS / Orders / Returns — old: 5 + 2 + 3 missing → reality: ✅ mostly built
*(POS is revenue-critical — audit only, no changes without explicit go-ahead.)*
EMI (`PaymentMethod.EMI` + interest), credit/known-customer (`store-credit/*`),
voucher redemption (`vouchers/{code}/redeem`), loyalty (`loyalty/account`,
`customers/{id}/loyalty/add`), previous Rx (`customers/{id}/prescriptions`),
delivery date/slot/priority + cart discount (Phase 6.7). Returns: restock,
credit-note balance = `store-credit/ledger`, vendor returns. ❌ **Order-tracking
QR for customer**, ❌ **explicit exchange→replacement→price-adjust flow** (only
partial `exchange` refs).

### AI / Integrations / Marketplace / Training — the real frontier
- AI: 8 agents live (JARVIS, CORTEX, SENTINEL, PIXEL, MEGAPHONE, ORACLE,
  TASKMASTER, NEXUS) + cross-agent activity feed + diagnostic endpoint. ORACLE
  already does Claude-powered anomaly narratives. ❌ Missing: **AI change-proposal
  workflow** (SYSTEM_INTENT §8's "AI suggests → Superadmin approves → execute"),
  natural-language query, image-based product search.
- Integrations: code present for MSG91 / Shopify / Razorpay / Shiprocket / Tally /
  PageSpeed — all 🚪 **gated** (`DISPATCH_MODE=off`, no keys). "Missing" here means
  *activation*, not code.
- Marketplace (Amazon/Flipkart unified, omnichannel stock sync): ❌ genuinely
  missing.
- Training & rollout (curriculum, scripts, in-app help): ❌ genuinely missing —
  but this is **content/process**, not code.

---

## 🎯 The TRUE backlog (genuinely missing — prioritized)

This is the real to-do list. Everything else above is "verify in browser", not "build".

**Tier 1 — high business value, self-contained, no POS risk**
1. **Customer self-service surfaces** — order-tracking QR + a read-only customer
   Rx/prescription portal (+ OTP verify). High customer-facing value; net-new
   surface, low blast radius on existing flows.
2. ~~**HR attendance engine**~~ — ✅ DONE. Shift config per employee, late-mark
   auto-calc, week-off swap approval (no self-approval), geo-fenced check-in
   enforcement, and a monthly LWP report. The LWP number is surfaced for the
   accountant (read-only); it is NOT auto-pushed into payroll — manual entry
   stays the source of truth. Overtime intentionally excluded (product decision).
3. **🔌 Cheap UI wins** — wire the already-built backend-only endpoints:
   `finance/pnl/by-store`, `crm/customers/churn-risk/list`, workshop **QC checklist
   UI**. Hours, not days.

**Tier 2 — valuable, larger**
4. **AI change-proposal workflow** — the SYSTEM_INTENT §8 loop (suggest → review →
   approve → audited execute). This is the product's actual differentiator and the
   natural home for ORACLE/TASKMASTER.
5. **Dual-mode budgeting** (full-ops vs survival) — the one true Finance gap.
6. **Explicit exchange flow** (return → pick replacement → adjust price) — *POS-adjacent,
   confirm before touching.*

**Tier 3 — big/strategic or non-code**
7. **Marketplace/omnichannel** (Amazon/Flipkart unified, stock sync) — large.
8. **Integration activation** — provision keys + flip `DISPATCH_MODE=live` **after**
   fixing the DND bug below. Config/ops, not a build.
9. **Training & rollout content** — curriculum, scripts, in-app help.
10. **Family/household Rx view**, **product stock-photo gallery** — smaller features.

---

## 🐞 Bugs & Risks (verified in code)

Found while auditing. These are real and worth fixing **before** the integration
activation in Tier 3.

1. **DND quiet-hours computed in UTC, not IST** — `agents/implementations/megaphone.py:71`
   ```python
   now_hour = datetime.now(timezone.utc).hour   # naive — real impl uses IST
   ```
   DND is *defined* 21:00–09:00 **IST** but *checked* against the **UTC** hour. Net
   effect once `DISPATCH_MODE=live`: the system goes quiet 02:30–14:30 IST and
   **sends 21:00 IST → 02:30 IST** — i.e. WhatsApp/SMS blasts at ~1 AM, the exact
   opposite of intent and a **TRAI/DLT violation** that can blacklist the sender
   header. Currently harmless only because dispatch defaults to `off`. **Fix:**
   compute the hour in `Asia/Kolkata` before the activation work.

2. **Agent scheduler has no singleton guard** — `api/main.py:231` starts an
   `AsyncIOScheduler` in *every* process; `max_instances:1` only prevents overlap
   *within one process*. No leader election, no `SCHEDULER_ENABLED` gate. Safe only
   while Railway runs exactly one worker/replica. The day that changes, TASKMASTER
   drafts **N** duplicate POs / 5 min and MEGAPHONE sends **N** duplicate messages.
   Phase 6.2's Redis pub/sub fixed event *fan-out*, not scheduler singleton-ness.
   **Fix:** a Redis `SETNX` leader lock or an env gate so only one process schedules.

3. **TASKMASTER contradicts the constitution** — `SYSTEM_INTENT.md §8`: *"AI CANNOT
   execute changes / No auto-execution."* `taskmaster.py`: *"the ONE agent that
   actually changes state… Tier 1 (auto-act)"* — its SLA pass reassigns task
   ownership and flips status to `ESCALATED` with no human in the loop. Either amend
   SYSTEM_INTENT to bless reversible Tier-1 auto-acts, or gate TASKMASTER's writes
   behind approval. **Decision needed, not just code.**

4. **Documentation truth-decay** — counts drift across docs (CLAUDE.md "387
   endpoints", README "~677", actual **811**; README "48 test files", actual
   **130**). This file is the attempt to fix it; keep it honest by re-running the
   811-route dump when counts are quoted.

---

<details>
<summary>📜 Previous (stale) status — preserved for history</summary>

The pre-2026-05-29 version claimed **130 built / 8 partial / 157 remaining** with a
per-module table topping out at "POS 32 / Reports 6 / Payroll 1". Those numbers were
disproven module-by-module above and should not be used for planning. See git
history for the full prior text.

</details>

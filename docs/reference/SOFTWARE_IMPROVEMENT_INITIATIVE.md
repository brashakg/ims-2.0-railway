# IMS 2.0 — Software Improvement Initiative

> _"Study the best in the world, convene wise counsel, and improve the workshop one room at a time."_

A standing, multi-session program to systematically raise the quality of **every**
module of IMS 2.0 — the retail OS for Better Vision / WizOpt optical chains — using
research-grounded, council-reviewed, production-safe changes.

This is a **marathon, not a sprint**. Progress is iterative and honest: each module is
taken from research → decision → shipped, verified improvement. Nothing is faked "done."

---

## The method — one repeatable pipeline per module

For each module (and later each cross-cutting concern), run the same loop:

1. **Research (parallel web workflows)** — how do the best Indian + global optical-retail
   products do this? Domain best practices, compliance (GST, DPDP 2023, ABDM, DLT),
   UX patterns. Cite sources.
2. **Audit (code workflow)** — what does our module actually do today? Capabilities,
   bugs (file:line), UX friction, missing features, data-model issues, tech debt.
3. **Council (parallel review workflows)** — multiple expert lenses (domain / UX /
   architecture / India-compliance) deliberate over research + audit and produce a
   **prioritized, justified** modification list (impact × effort × risk).
4. **Implement** — the agreed, **scoped** changes. POS and money paths get extra care
   ("control over convenience; audit everything; fail loudly").
5. **Verify** — `tsc --noEmit` + `vite build` for FE; `pytest` + import smoke for BE;
   targeted runtime checks. No green, no ship.
6. **Commit & PR** — one branch per module (`claude/improve-<module>`), small scoped
   commits, this doc updated.

### Operating principles
- **Production-safe**: additive/reversible first; never break revenue paths.
- **Scoped**: ship the highest impact-per-risk items; defer big rewrites with sign-off.
- **Evidence-based**: every change traces to a research finding or an audit defect.
- **Verified**: every change builds + tests green before commit.
- **Honest**: a module is "done" only when shipped & verified; partials are marked in-progress.

---

## Roadmap & status

Order per owner's directive: **clinic → POS → finance → inventory → …**, then cross-cutting.

### Modules
| # | Module | Status | Branch | Notes |
|---|--------|--------|--------|-------|
| 1 | **Clinic / Optometry** | 🔬 Research+Audit running | `claude/improve-clinic` | eye-test, Rx, contact-lens, dispensing, recall, lens catalog/stock |
| 2 | POS / Billing | ⏳ Queued | — | revenue-critical; extra care |
| 3 | Finance / GST | ⏳ Queued | — | GST returns, P&L, AP/AR, Tally |
| 4 | Inventory | ⏳ Queued | — | stock, transfers, counts, serials |
| 5 | CRM / Customers | ⏳ Queued | — | (search distinction already shipped) |
| 6 | Orders / Returns | ⏳ Queued | — | |
| 7 | Purchase / Vendor | ⏳ Queued | — | |
| 8 | Workshop | ⏳ Queued | — | job tracking, QC |
| 9 | HR / Payroll | ⏳ Queued | — | |
| 10 | Catalog / Pricing | ⏳ Queued | — | |
| 11 | Marketing / Storefront | ⏳ Queued | — | |
| 12 | Reports / Dashboards | ⏳ Queued | — | |
| 13 | Settings / RBAC | ⏳ Queued | — | |
| 14 | Jarvis / AI agents | ⏳ Queued | — | |

### Cross-cutting concerns (after modules)
| Concern | Status | Notes |
|---------|--------|-------|
| Customer mobile-number handling | ⏳ Queued | normalize, dedupe, validate (6-9 + 10 digit), patient numbers |
| Barcode handling | ⏳ Queued | generation, scanning, formats, label printing |
| SKU generation & handling | ⏳ Queued | scheme, collisions, brand/category encoding |
| Marketing messages | ⏳ Queued | WhatsApp/SMS templates, DLT, DND, personalization |
| (more) | ⏳ Queued | returns, invoicing/GST split, loyalty, store credit, audit trail |

Legend: ⏳ Queued · 🔬 Research+Audit · 🏛 Council · 🛠 Implementing · ✅ Shipped

---

## Progress log

### 2026-05-31 — Initiative kicked off; Module 1 (Clinic) started
- Established this pipeline + roadmap.
- Launched 3 parallel clinic workflows: (R1) India optical-clinic domain research,
  (R2) competitor/UX research, (A1) clinic code audit.
- Next: synthesize → council → prioritized clinic modifications → implement → verify → ship.

_(Appended as each module/concern advances.)_

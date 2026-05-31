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
| 1 | **Clinic / Optometry** | 🛠 Implementing — C1, C2 shipped | `claude/improve-clinic` | eye-test, Rx, contact-lens, dispensing, recall, lens catalog/stock |
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

### 2026-05-31 — Initiative kicked off; Module 1 (Clinic)
- Established this pipeline + roadmap.
- Ran 3 parallel clinic workflows: India optical-clinic domain research,
  competitor/UX research, clinic code audit. All three converged.

**Clinic council verdict (prioritized, risk-gated):**
| ID | Modification | Source | Impact | Risk | Status |
|----|--------------|--------|--------|------|--------|
| C1 | Validate Rx powers on the version-PATCH path (close bypass) | Audit P2 | High | Low | ✅ shipped |
| C2 | Stop fabricating AXIS 180 / 0.00 powers + thread patient_id (Family Rx grouping) | Audit P1 | High | Low–Med | ✅ shipped (incl. C2-B) |
| C3 | Canonical Rx field shape (sph/cyl/axis/add) — finalized Rx prints blank, progression null | Audit P1 (root cause) | Very High | Med | ⏳ next (needs migration care) |
| C4 | Blank-powers display on Clinical/Prescriptions pages | Audit P1 | Med | Low (FE) | ✅ shipped |
| C5 | Lane→POS Rx auto-flow (zero re-keying) + GST split (exempt service vs goods) | Both research #1 | Very High | Med | ⏳ |
| C6 | CL-fitting wiring; persist full exam; DLT recall; FHIR/ABDM; DPDP consent | Research | High (strategic) | Varies | ⏳ |

**Research signal (both streams agreed):** #1 = lane→POS Rx continuity, zero re-keying;
versioned Rx (never overwrite); keyboard-first ±0.25 grid steppers; DLT-compliant recall.
India edge: GST split (eye-test SAC 9993 exempt vs lenses 12% / frames 18%), FHIR R4
`VisionPrescription` schema → cheap ABDM/ABHA later, DPDP 2023 consent/retention.

**Shipped:** C1 (`patch_prescription_version` now range/0.25-grid validated; 9 tests) ·
C2 (no fabricated AXIS 180 / 0.00 powers; forward-compatible patient_id).
**Next:** C2-B (thread patient_id queue→test→Rx + fix blank-powers display), then C3.

_(Appended as each module/concern advances.)_

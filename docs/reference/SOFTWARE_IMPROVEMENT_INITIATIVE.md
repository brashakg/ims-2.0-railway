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
| 1 | **Clinic / Optometry** | ✅ Shipped (PR #392, CI green) — C1–C5 + C6-A | `claude/improve-clinic` | eye-test, Rx, contact-lens, dispensing, recall, lens catalog/stock |
| 2 | **POS / Billing** | ✅ Shipped (PR #393, CI green) — operational wins | `claude/improve-pos` | revenue-critical; turned out already mature |
| 3 | **Finance / GST** | ✅ Shipped (PR #394, CI green) — JV paisa-balance + ITC source | `claude/improve-finance` | GST returns, P&L, AP/AR, Tally |
| 4 | **Inventory** | ✅ Shipped (PR #395, CI green) — /stock/add DoS bound | `claude/improve-inventory` | transfers idempotent; stock-count by design |
| 5 | **CRM / Customers** | ✅ Shipped (PR #396, CI green) — store-credit authoritative balance | `claude/improve-crm` | loyalty/credit `$inc` atomic; validation in #367 |
| 6 | **Orders / Returns** | ✅ Audited — already hardened (atomic restock + over-refund integrity) | — | no forced change; #373 covers the money holes |
| 7 | **Purchase / Vendor** | ✅ Shipped (PR #397, CI green) — TDS 194H/194J corrections | `claude/improve-purchase` | AP aging/outstanding mature |
| 8 | Workshop | 🔬 Audit next | — | job tracking, QC |
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
| C3 | Canonical Rx shape — finalized Rx printed blank + progression null | Audit P1 (root cause) | Very High | Med | ✅ shipped (core: mirror+progression+reader; full field migration deferred) |
| C4 | Blank-powers display on Clinical/Prescriptions pages | Audit P1 | Med | Low (FE) | ✅ shipped |
| C5 | Lane→POS Rx auto-flow (zero re-keying) + GST split (exempt service vs goods) | Both research #1 | Very High | Med | ✅ shipped (C5-A auto-attach behind default-OFF flag; C5-B GST-exempt SAC 9993) |
| C6-A | **Working Contact Lens Fitting page** (dead stub → real workflow over the already-built CL backend) | Research + C6 audit | High | Low (additive, non-POS) | ✅ shipped |
| C6-B | Full eye-exam persistence (VA/IOP/segment/diagnosis/colour-vision/cover-test/dominant-eye) | C6 audit | Med–High | Med | ⏳ needs UI + clinical sign-off on field set |
| C6-C | DLT-compliant Rx-expiry recall (MEGAPHONE + MSG91) | Research | High | Med | ⏳ needs DLT template IDs (credentials) |
| C6-D | FHIR R4 VisionPrescription export / ABDM-ABHA | Research | Strategic | Med | ⏳ needs ABDM sandbox credentials |
| C6-E | DPDP 2023 consent capture + retention | Research | Compliance | Med | ⏳ needs schema migration + policy sign-off |

**Research signal (both streams agreed):** #1 = lane→POS Rx continuity, zero re-keying;
versioned Rx (never overwrite); keyboard-first ±0.25 grid steppers; DLT-compliant recall.
India edge: GST split (eye-test SAC 9993 exempt vs lenses 12% / frames 18%), FHIR R4
`VisionPrescription` schema → cheap ABDM/ABHA later, DPDP 2023 consent/retention.

**Shipped this pass:** C1 (version-PATCH validation; 9 tests) · C2 (no fabricated
AXIS 180 / 0.00 powers) · C2-B (patient_id threaded queue→test→Rx; Family-Rx grouping) ·
C4 (blank-powers display + shared rxEye reader) · C3-core (finalized Rx prints +
progression spans both shapes; 2 tests). All verified; existing clinic suites green.

### 2026-05-31 (cont.) — Clinic C5 shipped (owner-confirmed via AskUserQuestion)
Two owner decisions gated C5; both confirmed, both shipped:
- **C5-A — lane→POS Rx auto-attach** behind a **default-OFF** build flag
  `VITE_POS_AUTO_ATTACH_SINGLE_RX`. Fires only when exactly one valid (non-expired)
  Rx exists and none is attached; ambiguous multi-Rx still falls to manual choice.
  Zero behaviour change unless the owner opts in (POS is revenue-critical). tsc 0 +
  vite build green.
- **C5-B — GST-exempt eye-test line (SAC 9993)**. EYE_TEST/EYE_EXAM/EYE_CHECKUP/
  CONSULT/CONSULTATION/OPTOMETRY → `("9993", 0.0)` in the canonical GST table, so a
  consult bills at 0% on the SAME invoice as taxable goods. Proven purely additive
  (rate-bucketed split leaves 5%/18% rows byte-identical); 5 regression tests, existing
  GST/orders suites (94) green. Eye-test item_types also added to
  `_NON_SERIALIZED_ITEM_TYPES` (a consult never demands stock).

### 2026-05-31 (cont.) — Clinic C6-A shipped; Module 2 (POS) research+audit done
- **C6-A — Contact Lens Fitting page** turned from a dead "under development" stub into a
  real workflow over the **already-built, already-validated** CL backend (`POST /prescriptions`
  `rx_kind=CONTACT_LENS`, per-eye `CLEyeData` BC/DIA/power/toric, modality/brand, CL print
  card). Search customer → optional family-member → capture per-eye CL params → save; lists
  existing CL fittings + prints the CL card. Additive, non-POS; tsc 0, vite build green,
  backend model round-trips the page payload. The remaining C6 items are genuinely gated:
  **C6-B** full-exam persistence (needs UI + clinical field-set sign-off), **C6-C** DLT recall
  (needs DLT template IDs), **C6-D** FHIR/ABDM (needs ABDM sandbox creds), **C6-E** DPDP consent
  (needs schema migration + policy sign-off). Clinic's **tractable** scope is now complete.

- **Module 2 = POS** kicked off: ran the parallel **research** + **code-audit** workflows.
  Research top-10 (job-order checkout, advance/balance, **lens-config price matrix**, GST-
  compliant numbering, per-line HSN, atomic returns/redeem, workshop auto-link, offline-first,
  integrated UPI/EMI, exception controls). Audit returned a P1/P2/P3 defect list (self-rated
  ~50–60% coverage). **Cross-checked against current main:** research items #4 (FY-sequential
  invoice numbering, **#376**) and #6 (atomic returns + loyalty/store-credit redeem, **#373**)
  and per-line CGST/SGST/IGST split + ITC residual (**#368/#370**) are **ALREADY SHIPPED** — so
  the POS council focuses on the feature-level gaps + residual edge-hardening, NOT re-doing done
  work. **Next:** POS council (parallel review) → owner sign-off (POS is revenue-critical) →
  implement on `claude/improve-pos` off latest main.

### 2026-05-31 (cont.) — Module 2 (POS) operational-wins shipped (`claude/improve-pos`)
**Council (synthesized — the two parallel review agents degraded mid-run, so the chair
synthesized from research + audit + grounded code reads).** Key finding: **POS is already
mature.** Beyond the merged #373/#376/#368/#370, grounding revealed that Park/Hold, the
workshop-job create, EMI 0%-rate guard, advance/balance (via partial payments), and
Power-Grid lens config (`lens_line_id` + sph/cyl/add) **already exist** — and the audit's
"EMI Infinity" P2 was a false positive (the guard is right there). So the genuine gaps were
smaller than research implied. **Owner chose "Operational wins"** (vs lens-pricing UX /
exception controls / move-on). Shipped:
- **Workshop auto-link safety net** — confirming a fitting order (a lens to grind, or a
  frame + Rx) now GUARANTEES a workshop/lab job. The POS client already creates one (Phase
  6.8); `_ensure_workshop_job_for_order` is the idempotent, fail-soft backend net for the
  client call failing or a non-POS confirm path. Never duplicates; never blocks a paid
  confirm. **Reverse linkage** added: both the workshop create endpoint and the net stamp
  `workshop_job_id`/`workshop_job_number` onto the order (link was one-way before).
- **Delivery-date upper bound** — reject > 365 days out (fat-finger guard); past guard kept.
- **Park/Hold hardening** — fixed two real bugs: the held snapshot dropped `cart_discount_*`
  + `delivery_*` (silently lost on recall), and recall re-added items via `addToCart` (which
  MERGES → two customers' sales could fuse). New `posStore.restoreHeldSale` does one atomic
  REPLACE (cart verbatim, per-item discounts intact, cart-discount recomputed, delivery +
  advance restored, lands on review).
- 10 new backend tests; existing orders/workshop suites (113) green; tsc 0 + vite build.
**Deferred POS bets (owner can pick later):** lens-config pricing UX, exception controls +
per-cashier report, integrated payments (UPI QR / terminal / EMI reconciliation), offline-first.

**Next: Module 3 = Finance / GST** (research → audit → council → owner sign-off → ship).

### 2026-05-31 (cont.) — Modules 3–7 shipped; 6 PRs opened + green
A grounded **bug-hunting** sweep (the app proved very mature — most best-practice
features already exist, so the value is real defects, not greenfield). Each module
got its own branch + a verified fix (or an honest "already hardened"), then PRs:
- **Finance (#394)** — (a) **Tally sales-JV CGST/SGST paisa-imbalance**: `cgst=sgst=round(tax/2)`
  over-states by a paisa on odd-paise tax → voucher rejected by Tally; residual now on SGST.
  (b) **GST-summary ITC** was summed over `purchase_orders.date` (a field POs lack) → ITC always
  0, net payable overstated; now reads `vendor_bills` (GRN-backed), tolerant date-parse. _CI:
  the first ITC commit misused a helper kwarg (Pylint E1123) and went red; re-fixed + green._
- **Inventory (#395)** — `/stock/add` minted one serialized row per unit with **no qty cap**
  (DoS via `quantity=1e9`); added `le=10000`. Transfers verified idempotent (`received_qty_committed`).
- **CRM (#396)** — **store-credit balance** read the ledger delta-sum, dropping any legacy opening
  balance (display < redeemable). Now reads the authoritative `customer.store_credit` (kept in sync
  by issue + atomic redeem); ledger stays the audit trail. Owner-approved safe read fix.
- **Returns (audited, no change)** — `/restock` is atomic-claim guarded; create-return has
  over-refund quantity integrity (#373). Mature.
- **Purchase (#397)** — **TDS** table: 194H 5%→2% (Budget 2024), added 194J_TECH 2% (non-breaking).
  CA to verify.
- **Clinic (#392) + POS (#393)** PRs opened from the earlier branches; all 6 PRs (#392–#397) CI green.

**Compliance flags raised to owner/CA (not changed unilaterally):** finance ITC eligibility
scope; the corrected TDS rates. **Next: Module 8 = Workshop** (audit-led, same pipeline).

### 2026-05-31 (cont.) — missed-module sweep merged + loyalty P2-C
After the owner asked to also cover the modules the first pass skipped (AI, Settings,
Walkouts, etc.), shipped + merged: **Settings (#398)** printer-settings role gate;
**Marketing (#399)** referral-redeem role gate + atomic idempotency; **HR (#401)** leave
approve/reject manager gate; plus a **test-isolation fix (#400)** for a pre-existing
order-dependent non-moving-stock flake on main, and an **asyncio.run** fix for two new
async-endpoint unit tests (closed-loop-safe in the full suite). Audited & verified-secure
(no change): Jarvis (all 18 endpoints SUPERADMIN), Portal + Vendor-portal (OTP lockout,
token scoping, IDOR-safe), Walkouts, Workshop, Catalog caps, Vouchers (atomic).

**Loyalty P2-C (#402)** — the documented over-expiry bug: the `/loyalty/expire` sweep
expired `min(lot.points, account_balance)` per expired lot, destroying points from NEWER
valid lots when an old spent lot expired. New pure FIFO helper
`loyalty_engine.expirable_points_by_lot` walks the full ledger (redemptions consume oldest
lots first) so each expired lot sheds only its unspent remainder. Customer-protective
(only ever expires ≤ before); 6 tests.

**CI-discipline note:** a repeat `test (3.10/3.11)` red on the new PRs was root-caused to
(a) the pre-existing non-moving flake and (b) my own `get_event_loop().run_until_complete`
tests breaking on a closed loop — both fixed, not masked; the payroll/period-lock "failures"
seen locally were a no-Mongo artifact (green in CI).

_(Appended as each module/concern advances.)_

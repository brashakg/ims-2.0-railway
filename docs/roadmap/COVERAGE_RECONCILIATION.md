# COVERAGE RECONCILIATION INDEX — Excel/recon codes -> roadmap IDs -> status

> Authored 2026-06-08 to fill the structural gap GAP_ANALYSIS sec 3 named: there was **no doc that
> maps the open-backlog feature codes (FIN/POS/INV/CLI/CRM/BVI/RPT/HR/AI/SEC/OPS) to the roadmap
> board IDs (E*/PM/SC/#NN/N*) with a built/partial/missing verdict**, so the board had no way to prove
> a backlog feature wasn't silently dropped between phases.
>
> **Honesty note (read first):** this is a **SCAFFOLD + framework**, not a finished authoritative map.
> A complete per-code crosswalk needs an owner verification pass (per-item judgment the automated
> audit could not fully ground — the GAP_ANALYSIS coverage auditor returned a contaminated list that
> mixed already-built with missing items, which is exactly why this doc exists). The families,
> counts, legend, known mappings, and true-gap candidates below ARE grounded; the per-code verdicts
> marked `?` need the owner pass.

Sources reconciled: `_analysis/_appendix_recon.md` (reuse map over the original 52 features),
`docs/reference/IMS2_OPEN_BACKLOG.md` (92 consolidated open items, P1-P3, 41 owner-gated),
`IMS2_Updated_Feature_Status.md` (~245-260 built / ~10-15 backend-only / ~10 gated / ~25-30 missing),
`IMS2_COMPLETE_FEATURE_LIST.md` (117 built / 12 partial / 166 not-built by its count),
`IMS2_MASTER_TRACKER.md` (16 module audit lanes), `EXECUTION_BOARD.md` (roadmap #NN/E*/N*).

## Two distinct numbering systems (the root of the confusion)

1. **Roadmap board IDs** (`EXECUTION_BOARD.md`): `E*`=engine, `PM`/`SC`=foundation, **`#1`..`#52`** = the
   original 52-feature roadmap, `N*`=Excel net-new. The recon appendix's reuse flags use THIS `#N`.
2. **Open-backlog codes** (`IMS2_OPEN_BACKLOG.md`): `FIN-*/POS-*/INV-*/CLI-*/CRM-*/BVI-*/RPT-*/HR-*/
   AI-*/SEC-*/OPS-*/UX-*` — a SEPARATE 92-item operational backlog. **These do NOT carry `#NN` refs.**

The gap is the crosswalk between #2 (the 92 backlog codes) and #1 (the 52 roadmap IDs). Some backlog
codes map cleanly to a roadmap item; many are sub-tasks/defects of an already-built module (not new
roadmap features); a few are genuine un-roadmapped gaps.

## Status legend

- **BUILT** — code exists in production and has executed (recon "REUSE, live").
- **PARTIAL** — structure exists, UI/integration gaps (recon "PARTIAL").
- **ROADMAPPED** — not built, but owned by a board item (gives the #NN/E*/N*).
- **GAP** — no board item owns it AND not built — a true coverage gap to add to the board.
- **DEFECT** — a bug in a built module (belongs on the QA/fix lane, not the feature board).
- **GATED** — blocked on owner/infra (MongoDB volume, BVI cutover, integration creds, WhatsApp).

## Known anchor mappings (grounded — REUSE/live over the 52 roadmap features)

| Backlog area | Roadmap ID | Status | Evidence |
|---|---|---|---|
| Multiple payment methods / split-tender | **#3, #22** | BUILT (capture) | recon "REUSE, live"; `order.payments[]` 8 tenders |
| EMI tracking | **#4** (and POS-2) | BUILT | recon "REUSE, live"; `PaymentMethod.EMI` + `emi_details` |
| Per-item / cart discounts | **#9** | BUILT | recon "REUSE, live"; orders.py discount stack |
| GST compliance (intra/inter, HSN, invoice) | **#10** | BUILT | recon "REUSE, live"; GST FY-serial in pre-flight |
| Tender -> correct ledger + non-cash reconciliation | **E5** (this gap-analysis authored the packet) | ROADMAPPED | `features/E5.md` |
| Cost+10% sell floor | **board Phase-2 "NEW"** (`features/Fcostfloor.md` now exists) | ROADMAPPED | DECISIONS sec 9 |
| Vendor SKU aliases (INV-7) | INV reuse | BUILT/PARTIAL | recon flag |
| Demand-forecast PO (INV-9) | **#7** (predictive purchasing, BACKLOG, not-quickwin) | ROADMAPPED | board Phase-0 deferred |
| CL auto-refill (CRM-2) | **#47** | ROADMAPPED (DEFERRED — message-send, WhatsApp blocked) | STATUS comms directive |

## Family scaffold (counts grounded; per-code verdict needs owner pass)

| Family | Open items (approx) | Predominant nature | Likely board home / verdict |
|---|---|---|---|
| **FIN-*** (GST e-invoicing, inter-GSTIN, GSTR-2B, ledgers) | ~11 | New GST/finance features | Mix of ROADMAPPED (#10 family, E5) + **GAP candidates** (FIN-1 e-invoicing, FIN-4 GSTR-2B have no board owner — verify) |
| **POS-*** (offline-first, exchange, BOPIS, dynamic UPI QR) | ~7 | New POS flows | POS-2=EMI BUILT; POS-1 offline-first, POS-5 exchange, POS-7 BOPIS = **GAP candidates** |
| **INV-*** (discount_category, catalog backfill, transfer/barcode/vendor) | ~15 | Inventory seams | INV-1 P1; #1/#8/#21/N6 own several; **catalog-backfill (INV-2/3/4) owner-gated** |
| **CLI-*** (Rx history, appointments, QC UI, EHR, family linkage) | ~12 | Clinical | #50/#24 own handover/conversion; appointments (CLI-2) GATED (MSG91); EHR/family = **GAP candidates** |
| **CRM-*** (DND bug, CL refill, referral, churn, NPS, WABA) | ~16 | CRM/marketing | #39/#40/#41/#45/#46 own several; **CRM-1 DND UTC-vs-IST = DEFECT (P1, fix lane)** |
| **BVI-*** (Shopify sync, creds, migration, cutover, SSO) | ~13 | E-commerce merge | All GATED (owner: BVI cutover flag, creds) — tracked outside the feature board |
| **RPT-*** (analytics honesty, print templates, channel tracking) | ~10 | Reporting | Module-14 audit lane; mostly PARTIAL/DEFECT, not new features |
| **HR-*** (payroll dashboard orphans, commission) | ~4 | HR/payroll | Engine shipped; UI corrections = DEFECT/PARTIAL (Module-9 lane) |
| **AI-*** (fabrication, change-proposal, TASKMASTER §8) | ~6 | AI governance | **AI-3 TASKMASTER auto-exec vs SYSTEM_INTENT §8 = needs owner sign-off** (see GAP_ANALYSIS I3) |
| **SEC-*/OPS-*** (scheduler guard, infra) | ~10 | Security/infra | Pre-flight + DEFECT lane (e.g. OPS-2 scheduler singleton) |

## True-gap candidates (no board item owns them — verify, then add)

These survived cross-checking as *probably* un-roadmapped new features (NOT defects of a built module).
**Owner action: confirm each is (a) genuinely missing and (b) not a sub-task of an existing #NN, then
add a board row + phase.**

1. **FIN-1 e-invoicing (IRN/IRP)** — GST e-invoice generation; no board owner. (Phase 2/finance.)
2. **FIN-4 GSTR-2B reconciliation** — purchase ITC match; no board owner. (Phase 2.)
3. **POS-1 offline-first capture** — large infra feature; no board owner. (Owner-gated; phase TBD.)
4. **POS-5 exchange flow** / **POS-7 BOPIS** — new POS journeys; verify against #38 endless-aisle.
5. **CLI EHR / family-linkage (CLI-6..12)** — clinical depth beyond #50/#24; verify scope.

## Completion method (the owner pass that finishes this index)

For each of the ~92 backlog codes, in one pass:
1. Classify: BUILT / PARTIAL / DEFECT / ROADMAPPED / GAP / GATED (legend above).
2. If ROADMAPPED, record the `#NN`/`E*`/`N*`. If GAP, add a board row + phase. If DEFECT, send to the
   QA/fix lane (not the feature board). If GATED, record the blocker.
3. Keep this table as the durable crosswalk so the board can prove no backlog feature is silently
   dropped between phases.

**Until that pass is done, treat any single code's verdict here marked `?`/"candidate" as unverified
— do NOT promote it to a board TODO on this scaffold alone (GAP_ANALYSIS sec 3 LOW-confidence rule).**

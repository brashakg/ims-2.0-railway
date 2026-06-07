# Architect council (4 lenses)



# === Lens: Quick-Wins / Momentum ===
All claims verified against the real codebase. The "extend, don't rebuild" premise is solid:
- `purchase_match.py` already computes `received_qty` vs `ordered_qty` variance with `_pct_diff` (#8 is surfacing, not building).
- `campaign_segments.py` already has a `winback` segment type (#41 reactivation is config + tick, not a rebuild).
- Feature-flag pattern lives in `settings.py` / `integrations` collection (the per-store gate the digest references is real).
- `cost_mask.py` is the only true greenfield in my batch, and it's a 2-day pure-additive helper.

I have what I need. Here is my plan through the Quick-Wins / Momentum lens.

---

# QUICK-WINS / MOMENTUM PROGRAM PLAN — IMS 2.0 (52 features)

**Lens thesis:** Ship visible value in week 1, not week 12. The fastest ROI is *finishing* features that are already 60–90% built — where the collection, router, agent hook, or aggregation already exists and the delta is a tick method, an endpoint, a config doc, or a frontend page. I verified the "already built" claims against the live repo before ordering anything below; every Phase-0/1 item has its substrate confirmed (oracle `_propose_reorders`, `serial_numbers`, `walkouts.py`, `handoffs.py`, `campaign_segments.winback`, `purchase_match` variance, `labels.py` scan, `points_calculator`).

**Sequencing rule I apply:** Within "quick win," order by **(value visible to owner) ÷ (days × risk)**, and front-load items that share infra so each phase makes the next one cheaper. POS-touching items go behind the existing per-store `integrations` flag and ship *late* within their value tier — never first.

---

## PHASE 0 — "Day-One Dopamine" (Weeks 1–2) — pure-additive, zero POS/money risk, owner sees value immediately

The whole point of quick-wins-first: get 4–5 wins on the board before any hard build. These are tiny, isolated, and each is a thing the owner can literally *look at* the day it merges.

| # | Feature | Goal | Why first | Days |
|---|---------|------|-----------|------|
| **#35** | Role-Based Cost & Margin Masking | Hide cost/margin from floor staff via one `cost_mask.py` helper + `CostCell` component | Pure-additive, zero schema/money change, smallest blast radius in the whole program. A 2-day security/trust win the owner feels instantly. Ships the masking primitive other dashboards (#24, #34) reuse. | 2 |
| **#21** | Defective Quarantine Barcoding | QUARANTINE status + red label via existing `stock_units` status + `labels.py` | 2 days, reuses 90% existing infra (labels QZ tray + audit + vendor_returns), directly stops a scratched-Cartier-resold loss. No POS path. | 2 |
| **#34** | Real-Time Global Target Ticker | Revenue-vs-budget ticker; reuses revenue aggregation + budget read | Assembly + UI only, both data sources confirmed built (`budgets.py` + reports revenue). High visibility for *the owner himself* — exactly the "visible value" the brief wants. | 4 |
| **#33** | Gamified Staff Leaderboards | Privacy layer + UX on the existing `points_calculator`/`payout_calculator` scoring | No new collections, no money risk; motivational ROI on the shop floor. Confirmed scoring engine already exists. | 5 |

**Shared infra built:** `cost_mask.py` masking primitive (reused by #24, #35-consumers everywhere), confirmed ticker pattern for budget-vs-actual reads (reused by #24).
**End of Phase 0 (~2 weeks, parallelizable to ~9 calendar days with 2 build sessions):** 4 features live, all owner-visible, zero revenue risk.

---

## PHASE 1 — "Operational Wins" (Weeks 3–5) — finish near-complete CRM/clinical/marketing features that drive revenue motions

These convert *existing data* into *outbound action*. Every one has its data join and dispatch rail already built (MEGAPHONE, `campaign_segments`, `notification_service`, `eye_tests`, `handoffs`). The delta is a tick method + endpoints + one page.

| # | Feature | Goal | Sequencing reason | Days |
|---|---------|------|-------------------|------|
| **#24** | Optometrist→Retail Conversion Dashboard | One aggregation over `eye_tests`+`orders`, fits existing Clinical tab | All raw data exists; single pipeline + reuses Phase-0 ticker/mask patterns. Coaching tool that makes exam volume → revenue visible. | 5 |
| **#50** | Clinical→Retail Digital Handover | Extend `handoffs.py` so floor staff see Rx + doctor's product pref pre-greeting | `handoffs.py` + `handoff_repository` confirmed; 80% reuse. Pairs naturally with #24 (same clinical→retail seam) — build them back-to-back to share context. | 5 |
| **#46** | Configurable Automated Reminders (WhatsApp/SMS) | Config layer + MEGAPHONE extension for RX_EXPIRY/ORDER_READY/BIRTHDAY/FEEDBACK | 80% infra exists (notification_service + templates + MEGAPHONE). This is the **reminder rail** #41/#47/#40 all lean on — build it before them. Ship 4 triggers, defer servicing-trigger. | 5 |
| **#41** | Lapsed-Patient Tiered Reactivation | `lapsed_patient` segment resolver + `_scan_lapsed_patients` tick + voucher gate | `campaign_segments.winback` confirmed already in code; reuses #46's reminder rail + vouchers. Known optical revenue leak. | 4 |
| **#45** | Walkout Module (abandoned cart) | Finish items[] + TASKMASTER scheduler + accountability panel on the 60%-built base | `walkouts.py` + repos + megaphone `walkout_recovery` hook confirmed live. Floor accountability + structured recovery, no POS risk. | 5 |

**Shared infra built:** the reusable **reminder/segment rail** (config doc + MEGAPHONE trigger pattern + voucher-gate) that #40, #47, #43, #51 all later reuse — this is why #46 is sequenced before its siblings.
**End of Phase 1 (~3 weeks):** 9 features total live. The owner now has a working CRM outbound engine + clinical-to-floor handoff — the highest-frequency daily wins.

---

## PHASE 2 — "AI Momentum + Procurement Surfacing" (Weeks 6–8) — quick wins that ride the ORACLE/proposal rails + one P2 POS gate

| # | Feature | Goal | Sequencing reason | Days |
|---|---------|------|-------------------|------|
| **#7** | AI Predictive Purchasing Recommendations | Burn-rate query + payload enrichment + filtered JarvisPage view | Confirmed: ORACLE `_propose_reorders` + full `ai_proposals` Act/Ignore lifecycle already in prod. Delta is ~50-line aggregation. **The single highest ROI-per-day in the whole program.** | 5 |
| **#39** | Next-Best-Action Daily Associate Dashboard | MEGAPHONE scoring method + `nba_scores` TTL collection + 3 endpoints + page | All scoring signals + dispatch infra exist; reuses Phase-1 segment/tag work + customers `tags[]`. Surfaces the day's best call per associate. | 6 |
| **#40** | VIP Churn Prediction (Admin) | Interval-baseline in `oracle.py` + 2 crm endpoints + watchlist page | Reuses ORACLE sweep + proposals + RFM (`lifecycle_phase` confirmed) + MEGAPHONE. 4 days, no new collections of consequence. | 4 |
| **#8** | PO vs GRN Variance & Open Backorder | Surface `purchase_match` variance + aged-backorder into existing TASKMASTER tick | Confirmed: `purchase_match.py` already computes received-vs-ordered variance + `PARTIALLY_RECEIVED` exists. This is *surfacing latent data*, not new pipeline. Fold alert into existing tick (no new scheduler). | 4 |
| **#6** | Luxury Serial Number Tracking | Wire `serial_numbers` capture into GRN + stamp on order + verify-return endpoint | Confirmed: collection + CRUD already exist. **MED risk only because of staff training**, not code. Ship behind in-app soft-flag (digest option b) to avoid blocking returns on day one. | 4 |

**Shared infra built:** the proposal-enrichment + ORACLE-interval-baseline pattern (#40's baseline math can later seed #1 Intelligent Balancing and #18 Rebate detection).
**Note where my lens diverges from a naive quick-wins read:** the digest tags **#6 phase=2** but I keep it *last in Phase 2*, after #7/#39 — because its real cost (staff capturing serials at receiving) is operational, not code, and front-loading it would burn week-1 momentum on a training-gated item. Same logic defers its hard-block variant.

---

## PHASE 3 — "Foundation-gated value" (Weeks 9–14) — still high-ROI, but each needs a money-integrity or training precondition first

This is where I **explicitly disagree with pure quick-wins-first.** The brief says ship visible value fastest, but several "looks-quick" features touch the revenue-critical order/refund/money path, and the repo has an in-flight `claude/fix-money-integrity` branch (returns over-refund, loyalty double-spend). Shipping a promo engine or split-tender *before* that merges is how you turn a quick win into a P0 incident. My lens **gates these behind the money-integrity merge**, even though some score high on raw ROI.

**3a — Margin/loss-prevention finishers (gated on money-integrity merge), Weeks 9–11:**
- **#17** Petty Cash Controls (4d — expense workflow fully built, targeted extension; near-quick-win, sequenced here only because it adds a Tally voucher path)
- **#13** Remake/Spoilage Analytics (4d — fold `spoilage_cost_mtd` KPI into existing `workshop_dashboard_kpis` *immediately* for a day-one number, then the page)
- **#25** Maker-Checker Manual JEs (5d — additive P&L source)
- **#15** Blind Stock Takes (5d — mostly RBAC gating on existing stock-count infra)

**3b — POS-path features behind `*_ENABLED` flags, Weeks 11–14 (ONLY after money-integrity branch verified):**
- **#22 Phase A** Split-Tender ledger routing (2d, low risk — fixes the real Tally bug where all tenders post as "Cash"; ship Phase A standalone, defer family-pool Phase B)
- **#27** Refund Approval Matrix (HIGH risk — flag-gated, staging-validated)
- **#43**, **#34**-adjacent CRM triggers, **#47** CL Reorder (gated on DLT + Shopify-bridge ops, not code)

---

## PHASE 4 — "Dedicated sprints" (Weeks 15+) — genuinely large/risky, NOT quick wins

These are correctly tagged L/XL or HIGH-risk and have no shortcut. My lens explicitly **does not** try to accelerate them — pretending they're quick wins is the failure mode the brief is guarding against.

- **#11/#12** Promo & Bundling engines (L, touch POS order-creation; build pure `evaluate_promos` + tests first, integrate behind flag after money-integrity)
- **#16** Automated Financial Reconciliation (L, HIGH, ROI=5 — but blocked on cashier till-discipline; prerequisite is *behavioral*, not code)
- **#36** Deep Shopify Sync (XL, HIGH — split 2A hardlock / 2B BOPIS as the digest says)
- **#1/#3/#9/#37/#42/#48/#44** — each a dedicated sprint with a real dependency (stock-data quality, AP testing, DLT approval, FK-merge regression surface)
- **#2** Lab Tray Routing, **#4** Parts & Spares, **#5** Cannibalization, **#10/#14/#18/#19/#20/#28/#29/#30/#31/#32/#38/#49/#51/#52** — Phase-3/4 per their digest tags; real value but no 60–90%-built shortcut.

---

## WHERE MY LENS DISAGREES WITH "QUICK-WINS-FIRST"

1. **#6 Serial Tracking & #16 Reconciliation are mis-read as fast.** Their gate is *human* (GRN staff training; cashier till-discipline), not code. A quick-wins reflex front-loads them; I push #6 to end-of-Phase-2 (soft-flag only) and #16 to Phase 4, because a feature is not "shippable" if the precondition is a behavior change across 6 stores.

2. **Several POS-adjacent "quick wins" must wait for the money-integrity branch.** #22, #27, and the promo engines all touch the order/refund critical path. Shipping them for momentum *before* `claude/fix-money-integrity` merges risks compounding the existing over-refund/double-spend bugs. Momentum is worthless if you have to roll back. I gate them — this is the one place I deliberately slow down.

3. **I front-load #35/#21/#34 (small, dull, invisible-feeling) over flashier CRM features** because they're truly zero-risk and build the masking + ticker primitives that make Phase 1's dashboards cheaper. Pure quick-wins-by-ROI would start with #7 (highest ROI); I'd rather spend week 1 de-risking and building shared primitives so the *whole program* accelerates — that's the Momentum half of my lens overriding the Quick-Wins half.

4. **#46 before #41/#47/#40/#43.** Naive ROI-ordering scatters the CRM features; I cluster them and build the reminder/segment rail (#46) first so four downstream features become config-only. One shared-infra investment in week 3 pays back across the whole CRM tier.

---

## THE EXACT QUICK-WIN BATCH (first ~8 weeks, 14 features) + what finishing each requires

| # | Days | What's already built (verified) | The delta to finish |
|---|------|-------------------------------|---------------------|
| #35 | 2 | nothing — but pure-additive | new `cost_mask.py` + `CostCell` + ~12 call sites |
| #21 | 2 | `stock_units` status, `labels.py` QZ tray, audit, vendor_returns | QUARANTINE status value + red-label payload + lift-approval gate |
| #34 | 4 | revenue aggregation, `budgets.py` budget read | assemble ticker endpoint + UI card + milestone push |
| #33 | 5 | `points_calculator`, `payout_calculator` scoring engine | privacy/visibility config + leaderboard UX (no new collection) |
| #24 | 5 | `eye_tests`, `orders` | one aggregation pipeline + Clinical-tab page |
| #50 | 5 | `handoffs.py`, `handoff_repository`, notifications | clinical→retail handover doc + floor inbox + TTL |
| #46 | 5 | `notification_service`, `notification_templates`, MEGAPHONE | `reminder_rules` config collection + 4 MEGAPHONE triggers |
| #41 | 4 | `campaign_segments.winback`, vouchers, MEGAPHONE | `lapsed_patient` resolver + `_scan_lapsed_patients` tick + brand-exclusion gate |
| #45 | 5 | `walkouts.py`, walkout/walkin repos, megaphone hook | items[] field + TASKMASTER scheduler + accountability panel |
| #7 | 5 | ORACLE `_propose_reorders`, `ai_proposals` Act/Ignore lifecycle | burn-rate aggregation (~50 lines) + payload enrichment + JarvisPage view |
| #39 | 6 | scoring signals, dispatch infra, `tags[]` | MEGAPHONE scoring method + `nba_scores` TTL collection + 3 endpoints + page |
| #40 | 4 | ORACLE sweep, proposals, RFM `lifecycle_phase`, Customer 360 | interval-baseline in `oracle.py` + 2 crm endpoints + watchlist page |
| #8 | 4 | `purchase_match.py` variance + `PARTIALLY_RECEIVED` | surface open-backorder structure + aged alert into existing TASKMASTER tick |
| #6 | 4 | `serial_numbers` collection + CRUD | wire capture into GRN + stamp on order + verify-return endpoint (soft-flag) |

**Total quick-win batch: ~60 build-days → ~6–8 calendar weeks with 2 parallel build sessions, delivering 14 of 52 features.** Phases 0–2 are all genuine extend-don't-rebuild work confirmed against the codebase. The deliberate slow-downs (#6 placement, gating #22/#27/promos/#16 behind the money-integrity merge) are where Momentum overrides raw Quick-Wins to protect the revenue path.


# === Lens: Money-Safety / Loss-Prevention ===
I have confirmed the key facts: the returns over-refund fix appears merged (atomic `returned_qty` reservation now exists), serial CRUD + GRN minting infra is present, cash_register money-math is pure and live, no quarantine status or cost_mask helper exists yet, and invoice numbering has a `next_invoice_number` repo method (the GST-serial gap noted in CLAUDE.md). This grounds my plan. Here is my recommendation through the Money-Safety / Loss-Prevention lens.

---

# MONEY-SAFETY / LOSS-PREVENTION LENS — PHASED PROGRAM PLAN

## Governing thesis (where I disagree with quick-wins-first)

The orchestrator's default is "quick-wins-first." Through my lens that ordering is **partially wrong**, and I'll say exactly where: a quick win that *records* fraud after it happens (a dashboard, a leaderboard, a reminder) has near-zero loss-prevention value. A medium-effort feature that *blocks money from leaving the till incorrectly* has enormous value. So my sequencing promotes a handful of MED-effort controls **ahead of** their digest "phase=3" placement, and demotes several `quickwin=yes` items that are pure visibility.

The non-negotiable principle that drives everything: **controls must precede analytics, because analytics on uncontrolled data is noise** (the digest itself admits this for #1: "run stock audit hardening before activating ORACLE IBT generation"). My plan front-loads the *control gates* and the *shared atomic-money and audit infra* they all reuse, then lets the analytics features ride on clean, tamper-evident data.

The single most important sequencing insight: **six of the highest-value loss-prevention features (#15, #16, #21, #23, #27, #6) all depend on three shared primitives** — (a) a tamper-evident `stock_units.status` state machine with audit, (b) the cash-session blind-submit→manager-lock lifecycle, and (c) the guarded `find_one_and_update` money-mutation pattern. Build those primitives once, early, and the rest become cheap.

---

## PHASE 0 — Money-mutation & audit foundation (shared infra) — ~1.5 weeks

**Features:** none ship to the owner as a "feature" — this is the spine every later phase reuses. Bundle it with the lowest-risk pure-additive wins that *create* the foundation.

**Goal:** establish the three reusable primitives so no later feature re-invents money-safety plumbing (and so two sessions don't each build a half-correct version).

**Build:**
- **Atomic balance-mutation helper** generalised from `vouchers.redeem_voucher_atomic` (the in-flight `claude/fix-money-integrity` work confirms returns now use guarded `returned_qty` reservation — extract that into a shared `services/money_guard.py` so loyalty (#49), store-credit, refund (#27), petty-cash (#17), consignment (#3) all call ONE concurrency-safe path).
- **Tamper-evident `stock_units` status transition guard + audit** — a single `transition_stock_status(unit_id, from, to, reason, actor)` that writes to `audit_logs` and rejects illegal transitions. This is the substrate for quarantine (#21), serial verify (#6), blind takes (#15), cannibalization (#5).
- **Generalised approval/PIN + maker-checker primitive** — extracted from the existing proposals + expense separation-of-duties patterns. Reused by refund matrix (#27), JE maker-checker (#25), discount approvals (#26), quarantine-lift second approver (#21).

**Sequencing reason:** Every Phase-1/2 control writes money or stock status under concurrency. If each feature ships its own read-modify-write, we re-introduce the exact double-spend class the in-flight branch is fixing. Build the guard once, audited, tested — then mandate it.

**Disagreement with quick-wins-first:** this phase produces *nothing the owner can click*, so a quick-wins lens would skip it. My lens says it is the highest-leverage 1.5 weeks in the whole program because it de-risks ~12 downstream money features.

---

## PHASE 1 — Plug the open till & re-shelf leaks (cheapest, biggest bleed) — ~2 weeks

**Features:** #21 (Defective Quarantine Barcoding, S/2d) · #35 (Role-Based Cost & Margin Masking, S/2d) · #6 (Luxury Serial Anti-Fraud, M/4d)

**Goal:** stop the three leaks that are cheap to close AND directly lose cash today: defective luxury items getting re-sold, cost/margin visible to floor staff (enabling collusive under-pricing), and serial-swap refund fraud on high-value SKUs.

**Sequencing reason:**
- **#21 and #35 are genuine quick wins that ALSO prevent loss** — the rare overlap where my lens and quick-wins-first agree. Both are S/2d, LOW risk, zero money-flow change, and both reuse Phase-0 primitives (#21 uses the stock-status guard; #35 is pure additive masking). Ship them first to bank momentum.
- **#6 is promoted from its already-Phase-2 slot to be done WITH #21** because they share the `serial_numbers` + `stock_units.status` + vendor-return surface, and the GRN capture-training rollout should happen once, not twice. Serial verify-on-return is the anti-fraud counterpart to quarantine: #21 stops the item re-entering stock, #6 stops a swapped item being refunded.

**Shared infra built:** quarantine reason taxonomy + label format (reused by #5 cannibalization, #20 RTV); serial-at-GRN capture flow (reused by #15 blind takes scanning).

**Disagreement note:** The digest rates #21 roi=3 and #6 roi=4. Through my lens #21 is effectively roi=5 *per rupee of effort* (2 days to stop a scratched Cartier being re-sold as new — a single warranty dispute exceeds the build cost). I rank it first.

---

## PHASE 2 — The cash-control core (HIGHEST loss-prevention ROI in the program) — ~3 weeks

**Features:** #23 (Blind EOD Cash Tally & Z-Read, M/5d) → #16 (Automated Bank/Cash/POS Reconciliation, L/12d, ROI=5) → #17 (Petty Cash Controls & Expense Ledger, M/4d)

**Goal:** make cash physically traceable from till-open to bank deposit, with blind counting so a cashier cannot back-fit the count to the expected number. This is where the largest silent leakage in a 6-store cash-heavy optical chain lives.

**Sequencing reason:**
- **#23 MUST come before #16.** #16 (the ROI=5 reconciliation engine) is explicitly gated in the digest on "cashier discipline with till open/close being established first." #23 *is* that discipline — the blind-submit→manager-lock lifecycle. Build the session state-machine in #23 (reusing Phase-0's maker-checker + the pure `cash_register.py` money-math that already exists), then #16 layers Razorpay T+1 + bank-CSV matching on top of a session that is already trustworthy.
- **#17 rides the same `cash_register`/Tally rails** — petty-cash float leakage is the same "money leaves the drawer untracked" problem, and the float-debit reuses Phase-0's atomic helper.

**Shared infra built:** blind-count UI component (denomination grid reused by #16), cash-variance ledger + Tally variance vouchers (reused by #16 and #18 rebates), session-lock-blocks-Tally-export rule.

**Disagreement with quick-wins-first — this is my sharpest objection:** #16 is L/12d, HIGH risk, `quickwin=no`. A quick-wins lens would push it to the back. My lens puts it as the **centrepiece of the program** because it is the only ROI=5 feature and it directly prevents cash pilferage — the textbook loss-prevention target. I accept it cannot be first (it needs #23's discipline), but it must not be deferred to "Phase 3 someday." Weeks 6–9 of the program.

---

## PHASE 3 — Refund & tender control (close the outflow valve) — ~1.5 weeks

**Features:** #27 (Refund Approval Matrix & Original-Tender Enforcement, M/5d, HIGH risk) · #22-Phase-A (Split-Tender Ledger Routing, 2d slice only)

**Goal:** control the *other* direction money leaves — refunds. Tier refunds by ₹ value with PIN approval, force refunds back to the **original tender** (the classic "pay cash, refund to my UPI" skim), and fix the Tally mis-posting where all tenders currently land as "Cash."

**Sequencing reason:**
- #27 is HIGH risk on the refund critical path, so it goes **after** Phase-0's PIN/maker-checker primitive exists and **after** the in-flight returns money-integrity branch is fully merged/stable (the digest and CLAUDE.md both flag this dependency). It reuses Phase-0's PIN primitive and the proportional-split logic pairs with split-tender routing.
- **#22 Phase-A only** (the 2-day ledger-routing fix) ships here because it's a prerequisite for #27's proportional-tender refund to post correctly to Tally. **#22 Phase-B (family loyalty pool) is explicitly excluded** from this phase — it's a CRM convenience, not loss-prevention, and belongs much later.

**Shared infra built:** `refund_settings` matrix + tender-ledger map (reused by #20 RTV, #25 JE, #14 non-adapt credit).

**Disagreement note:** quick-wins-first would never touch a HIGH-risk revenue-critical path this early. My lens insists: an uncontrolled refund desk is a fraud ATM. Behind `REFUND_GATE_ENABLED`, staged store-by-store, it's the right risk to take in week ~10.

---

## PHASE 4 — Blind stock truth + maker-checker books — ~2.5 weeks

**Features:** #15 (Blind Stock Takes, M/5d) · #25 (Maker-Checker Manual JEs, M/5d) · #20 (RTV Debit Note Automation, M/5d)

**Goal:** establish ground-truth inventory (so shrinkage is *measured*, not guessed) and put four-eyes control on the manual ledger entries that can quietly move money in the books.

**Sequencing reason:**
- **#15 is the lynchpin the digest itself names as the prerequisite for #1, #7, #10** ("run stock audit hardening before activating ORACLE"). It reuses Phase-1's serial-scan capture and Phase-0's stock-status guard + audit. Blind = response-field suppression (the digest correctly notes most work is RBAC + masking, which Phase-1's #35 already pioneered). This produces the *clean stock data* that unlocks the entire analytics tier later.
- **#25 maker-checker JEs** reuses Phase-0's maker-checker primitive directly and the Tally JV pipeline from Phase-2/3. Manual JEs are the highest-trust accounting surface — four-eyes here prevents a single accountant silently adjusting P&L.
- **#20 RTV** closes the vendor-side leak (defective goods returned but never credited) and reuses Phase-1's quarantine reason map + Phase-3's tender-ledger map + the AP hardlock pattern.

**Shared infra built:** shrinkage/variance ledger + reveal-variance gate (reused by ORACLE in the analytics tier), Tally JV maker-checker export path.

---

## PHASE 5 — Vendor/AP cost-integrity controls — ~2.5 weeks

**Features:** #8 (PO vs GRN Variance & Backorder, M/4d) · #19 (Landed Cost Matrix, L/8d) · #18 (Vendor Rebate Tracker, L/8d) · #13 (Remake/Spoilage Analytics, M/4d)

**Goal:** ensure the cost side of margin is *real* — that you actually received what you paid for (#8), that true landed cost feeds margin (#19), that contracted rebates are actually claimed (#18 — money left on the table is a loss too), and that workshop spoilage is costed (#13).

**Sequencing reason:** these all depend on a trustworthy AP/GRN flow, which is only trustworthy after Phase-4's RTV + blind-stock land. #8 is the cheapest and gates the rest (you can't allocate landed cost or compute rebates on quantities you haven't confirmed received). #13's `spoilage_cost_mtd` KPI card can be slipped in early (digest suggests folding it into existing workshop KPIs day-one) as a teaser while the full analytics waits.

**Shared infra built:** confirmed-receipt quantity source-of-truth (feeds #1, #3, #10 later), landed-cost-adjusted `cost_price` (feeds #35 masking accuracy and all margin analytics).

---

## PHASE 6 — Now (and only now) the analytics & optimisation tier — ongoing

**Features the orchestrator wants early but my lens defers until data is trustworthy:** #7 (Predictive Purchasing) · #1 (Inventory Balancing) · #10 (Ageing Auto-Liquidation) · #24 (Optometrist Conversion) · #40 (VIP Churn) · #34 (Target Ticker) · #33 (Leaderboards) · #45 (Walkout) · #39 (NBA) — plus the remaining promo/CRM/omnichannel features (#11, #12, #36, #37, etc.).

**Sequencing reason (my core disagreement, stated plainly):** the digest marks #7, #24, #34, #33, #45, #39 as `quickwin=yes` and several at `phase=3`. Through the loss-prevention lens, **every one of these is built on data that is only trustworthy after Phases 1–5.** Predictive purchasing (#7) on un-audited stock generates phantom POs (real cash committed against wrong numbers). A revenue leaderboard (#33) on a POS where refunds/discounts aren't gated *rewards the cashier who games discounts*. These are correctly built *after* the controls, not before. They are excellent features — just not loss-prevention, and their value is *amplified* by clean data, so deferring them costs little and de-risks them.

I would let the orchestrator interleave the truly zero-risk visibility quick-wins (#34, #24, #35-style additive dashboards) opportunistically as "morale" releases between heavy control phases — but never let one of them displace a Phase-2/3 control on the critical path.

---

## Critical-path summary (my recommended order, money-safety lens)

| Order | Features | Weeks | Why this slot |
|---|---|---|---|
| 0 | money_guard + stock-status guard + maker-checker primitive | 1.5 | de-risks all 12 downstream money features |
| 1 | #21, #35, #6 | 2 | cheapest leaks: re-shelf, cost-visibility, serial-swap |
| 2 | #23 → #16 → #17 | 3 | cash core; #23 unlocks the ROI=5 reconciliation |
| 3 | #27, #22-A | 1.5 | refund/tender outflow valve |
| 4 | #15, #25, #20 | 2.5 | blind stock truth + four-eyes books + vendor credit |
| 5 | #8, #19, #18, #13 | 2.5 | cost-integrity (received/landed/rebate/spoilage) |
| 6 | #7, #1, #10, #24, #40, #33, #34, #45, #39, promo/omni/CRM | ongoing | analytics on now-clean, tamper-evident data |

**Where I most strongly diverge from quick-wins-first:** I pull **#16 (HIGH risk, L/12d)** forward to the program centre and push **#7/#24/#33/#34/#45/#39 (all `quickwin=yes`)** behind the control phases. Net: ~₹-leakage stops in weeks 1–10; the dashboards the owner *enjoys* come in weeks 11+, but now report numbers that are actually true.

**Owner-decision dependencies that gate my critical path (must be answered before each phase starts):** Phase-1 = #6 brand-list for serial-tracking + hard-block-vs-soft-flag on serial mismatch; Phase-2 = #23 variance thresholds (₹0/₹50/₹200 vs ₹0/₹100/₹500) + per-shift-vs-daily + #16 bank-CSV formats per entity; Phase-3 = #27 refund ₹ tiers + global-vs-per-approver PIN + #22 Tally tender-ledger names; Phase-4 = #15 covert-vs-transparent blind + shrinkage alert threshold + #25 chart-of-accounts seed. These are all genuine *business* choices (thresholds, who-approves, brand scope) — appropriate for a non-technical owner.

Key grounding files confirmed: `backend/api/services/cash_register.py` (pure money-math, ready for #23/#16), `backend/api/routers/returns.py` (atomic `returned_qty` reservation already present — the money_guard pattern to generalise), `backend/api/services/integration_config.py` (the `integrations`-collection feature-flag pattern every POS feature ships behind), `backend/api/routers/inventory.py` (serial CRUD + `stock_units` status, the substrate for #21/#6/#15), and `backend/api/services/rbac_policy.py` (the gate registry for #35/#15 masking). No `cost_mask.py` or quarantine status exists yet — both are net-new in Phases 1.


# === Lens: Revenue-Growth ===
I have everything I need. The infrastructure is exactly as the digest claims: MEGAPHONE dispatch (DND + atomic), campaign_segments resolvers, crm Customer 360 + cl-refill-status + churn-risk, walkouts + follow-ups auto-generate, atomic vouchers/loyalty with FIFO, handoffs (clinical handover ~80% there), notification_service with DISPATCH_MODE gate. NBA is genuinely net-new. Shopify customer bridging is thin (only online-origin customers carry `shopify_customer_id`).

Here is my Revenue-Growth program plan.

---

# REVENUE-GROWTH LENS — Phased Program Plan

**My thesis:** Revenue growth in this 6-store optical chain is not a "build more features" problem — it's an **activation problem**. The engine that drives repeat sales (MEGAPHONE dispatch with DND + atomic campaign claim, campaign_segments rx_expiry/winback/birthday resolvers, Customer 360, cl-refill-status, churn-risk, walkouts+follow-ups, atomic vouchers/loyalty, clinical handoffs) is **already in the repo and inert**. The single highest-ROI act is not writing code — it's **lighting the fuse: get MSG91 to `DISPATCH_MODE=live` with DLT-approved templates**. Everything downstream multiplies off that one switch.

This is where I **disagree with quick-wins-first**: the orchestrator's quickwin flags (#21 quarantine, #35 cost-masking, #33 leaderboards, #34 ticker) are real quick wins but they are **loss-prevention / morale**, not revenue *generation*. Through my lens they are explicitly deprioritized. I will spend early weeks on a **non-quick-win that I rank #1** (the comms backbone + reminders) because nothing else moves the revenue needle until customers receive a message.

---

## THE LIVE-INTEGRATION DEPENDENCY MAP (read this first)

Almost every revenue feature here is gated by **MSG91 live**. This is the program's critical path, not any single feature.

| Dependency | Gates these features | Owner action required |
|---|---|---|
| **MSG91 `DISPATCH_MODE=live` + DLT-approved templates** | #46 reminders, #47 CL reorder, #41 lapsed reactivation, #45 walkout, #40 churn, #39 NBA (call list works without it but loses WhatsApp arm), #42 lookbooks, #50 handover ping, #51 use-it-or-lose-it, #52 PDF invoice | Approve DLT templates on MSG91 portal (utility + marketing categories), set Railway env vars, confirm WABA number |
| **Shopify live + `shopify_customer_id` bridged for offline customers** | #47 CL reorder (checkout link), #36 deep sync, #44 Shopify merge | BVI/Shopify cutover (step-7, owner-gated per memory) + a one-time customer-bridge backfill |
| **Razorpay live (payment links)** | #42 lookbook holding deposit (optional), B2B #37 advance collection | Razorpay keys on Railway; only needed if owner wants deposits |

**My rule:** ship message *generation* code behind the flag in early phases so that the day MSG91 goes live, revenue features activate **simultaneously** with zero further deploys. Don't serialize "build then wait for templates" — parallelize.

---

## PHASE R0 — "Light the fuse" comms backbone (Weeks 1–3)

**Goal:** Turn the dormant MEGAPHONE engine into live, measured, opt-out-safe outbound — and ship the highest-frequency reminder triggers. This is the foundation every later phase multiplies off.

| Feature | Why here |
|---|---|
| **#46 Configurable Automated Reminders** (RX_EXPIRY_30D, ORDER_READY, BIRTHDAY, POST_PURCHASE_FEEDBACK) | 80% built (MEGAPHONE already scans rx_expiry/birthday; campaign_segments has the resolvers). This is the **single best revenue-per-engineering-day** in the entire 52: optical Rx renewals are the bread-and-butter repeat sale. ORDER_READY also reduces uncollected-job cash drag. Ship the 4 stable triggers; defer frame/watch servicing. |
| **Shared infra: the `reminder_rules` config + cross-rule frequency cap + dispatch telemetry** | This config layer is reused by #47, #41, #43, #51. Build the per-customer 30-day message cap ONCE here so no later feature can spam a customer into opting out (an opt-out is permanent revenue loss). |
| **Owner-gated, parallel: MSG91 DLT template approval** | Not code — but it is *the* gating task. Kick it off Day 1; it runs while engineering builds. |

**Shared infra built:** `reminder_rules` collection + Settings UI; cross-rule frequency cap; a dispatch-outcome log (sent/delivered/opted-out) surfaced as a tiny KPI so the owner *sees* the engine working. Marketing-consent gate respected for marketing-category messages; utility messages (ORDER_READY) exempt.

**Rough weeks:** 3 (1 reminders config, 1 telemetry+cap, 1 buffer for DLT approval round-trips).

**Sequencing reason:** Nothing else converts a message into a sale until the pipe is live and trustworthy. Reminders are the proof-of-life that justifies every subsequent phase to the owner.

---

## PHASE R1 — High-intent repeat-purchase engines (Weeks 4–7)

**Goal:** Capture the demand that is *most predictable and most lost today*: contact-lens reorders and lapsed patients. These are the two largest known revenue leaks in optical retail.

| Feature | Why here |
|---|---|
| **#47 Contact Lens Reorder Engine** | CL is a **subscription-shaped, perfectly-predictable** revenue stream (supply runs out on a known cadence). crm.cl-refill-status already computes the runout. 3–4 day build on existing rails. Highest recurring-revenue ceiling per customer of anything in the catalog. **Needs MSG91 live + a Shopify checkout link** (fallback to "Call us" / store WhatsApp when `shopify_customer_id` unbridged — ship with the fallback so it works pre-Shopify-cutover). |
| **#41 Lapsed Patient Tiered Reactivation** | Reuses MEGAPHONE + campaign_segments `winback` + vouchers (brand-exclusion gate). Lapsed-patient winback is the classic optical reactivation play (Rx expires ~2yr, patients forget). 4 days. Voucher engine is already atomic. |

**Shared infra built:** the **incentive-voucher issuance pattern** (brand-excluded, atomic, single-use) wired into a campaign tick — reused by #51 and #42. The `cl_reorder_opt_out` / consent-bypass decision pattern (service vs marketing message) — reused by any health-reminder feature.

**Live-integration callout:** Both need MSG91 live. #47 *prefers* Shopify checkout but degrades gracefully — so it can ship and earn via WhatsApp-to-store even before the Shopify cutover. Do **not** block #47 on Shopify.

**Rough weeks:** 4.

**Sequencing reason:** Once the pipe is live (R0), the fastest incremental rupees come from customers who **already bought and will buy again on a schedule**. Acquisition and B2B are higher-effort, longer-payback — they come later.

---

## PHASE R2 — In-store conversion lift (Weeks 8–11)

**Goal:** Convert the foot traffic and clinical exams you *already pay for* into more closed sales. This is the cheapest revenue: the customer is already in the store.

| Feature | Why here |
|---|---|
| **#50 Clinical-to-Retail Digital Handover** | quickwin, 5 days, reuses handoffs.py (already ~80% built). The salesperson today walks in **blind**; handing them the Rx + the doctor's lens preference before "hello" directly lifts attachment rate (coatings, lens upgrades — the highest-margin add-ons). Ship "mark served" tracking so we can *prove* the conversion lift. In-app bell only at launch (no MSG91 dependency for the core flow). |
| **#45 Mandatory Walkout Module** | quickwin, 60% built (schema/modal/dashboard/follow_ups/MEGAPHONE walkout hook all exist). Physical abandoned-cart recovery is pure incremental revenue. Ship **soft-block** (banner + compliance score), never hard-block POS (revenue-critical). Personalised WhatsApp follow-up naming the item tried converts far better — needs items[] + MSG91 live (R0 done). |
| **#24 Optometrist-to-Retail Conversion Dashboard** | quickwin, 5 days, single aggregation over eye_tests+orders. This is the **measurement layer** that proves #50 works and turns it into a coaching tool. Build it *with* #50, not separately — they share the exam→order join. |

**Shared infra built:** the **exam/walkout → order conversion-attribution join** (which exam/walkout produced which order). This is the data spine for #39 NBA, #40 churn, and any future commission feature. Building attribution once here saves three later rebuilds.

**Rough weeks:** 4.

**Sequencing reason:** Reminders (R0) and reorders (R1) reactivate people *outside* the store. R2 squeezes more revenue from people *already inside* — lower cost-per-rupee than any outbound campaign, and the conversion-attribution spine it builds is a prerequisite for the intelligence layer in R3.

---

## PHASE R3 — The intelligence layer (Weeks 12–16)

**Goal:** Now that the pipe is live, the data spine exists, and the reactivation engines are running, add the AI/targeting layer that tells staff *who to call and why* — multiplying the manual outreach effort.

| Feature | Why here |
|---|---|
| **#39 Next Best Action Daily Dashboard** | Genuinely net-new (confirmed: no NBA in repo), but it sits **on top of everything R0–R2 built** — MEGAPHONE scoring, cl-refill, churn-risk, rx-expiry, conversion attribution. 4–6 days. Gives every associate a ranked daily call list — the single highest-leverage *human* revenue tool. Works as a pull list even without MSG91; WhatsApp arm is bonus. |
| **#40 AI VIP Churn Prediction** | 4 days, reuses ORACLE sweep + proposal + MEGAPHONE + Customer 360 + RFM. Protects the highest-LTV customers (where a single lost Cartier buyer >> 50 lost mass-market). Feeds the VIP slots in #39. |
| **#43 Centralized VIP Personal Triggers** | 6 days, reuses MEGAPHONE + Customer 360. Depends on staff tag discipline — which only exists *after* R2 makes Customer 360 a daily-use surface. That's why it's here, not earlier. |

**Shared infra built:** the `nba_scores` collection (TTL) + `tags[]` on customers + the scoring-method pattern in MEGAPHONE — the unified "who to engage next" ranking that every CRM surface reads from.

**Rough weeks:** 5.

**Sequencing reason:** Intelligence is worthless without (a) a live pipe to act on it and (b) the behavioral data to feed it. Building NBA in week 2 would rank customers on cold data with no channel to reach them. Built here, it amplifies running engines.

---

## PHASE R4 — Premium experience & basket-size lift (Weeks 17–22)

**Goal:** Lift average order value and serve the high-value/B2B segments — higher effort, longer payback, but high absolute rupee impact.

| Feature | Why here |
|---|---|
| **#42 VIP Black Book Digital Lookbooks** | 8 days. High-touch VIP retention/upsell. Needs MSG91 live + DLT *rich-media* template (start link-only template) + optional Razorpay deposit. Reuses the incentive-voucher pattern from R1. Lookbook page security surface needs review — hence later, dedicated. |
| **#11 Advanced Dynamic Promotions / #12 Cross-Category Bundling** | Build the **pure `evaluate_promos`/bundle function + Rules Manager + tests with ZERO POS wiring first**, then gate live POS integration behind `PROMO_ENGINE_ENABLED` per-store. Bundling (watch+sunglass, frame+lens) directly lifts AOV. **Hard prerequisite: the `claude/fix-money-integrity` branch (returns over-refund + loyalty double-spend) must be merged** before touching the POS order path or partial-return proration becomes a financial leak. |
| **#51 Use-It-Or-Lose-It Benefits** | 6 days, reuses campaign dispatch + voucher engine + the R0 frequency cap. Q4-timed corporate/insurance-benefit burn campaign. Manual benefit-cap entry MVP. |

**Shared infra built:** the server-side promo/bundle evaluation engine + per-store `PROMO_ENGINE_ENABLED` flag scaffolding — reused by any future offer logic. POS integration ships dark and is enabled store-by-store after the money-integrity branch is verified.

**Rough weeks:** 6.

**Sequencing reason:** AOV-lift via promos touches the **revenue-critical POS path** and *must* wait for money-integrity stabilization (explicitly flagged in CLAUDE.md as in-flight). Promos also benefit from R0–R3 customer data to target. This is high-value but correctly *late* — the cost of a promo bug on the order path is a direct cash leak.

---

## PHASE R5 — Omnichannel & B2B scale (Weeks 23–30+)

**Goal:** Open new revenue *channels* (online, corporate). Highest effort, longest payback, most external dependencies — correctly last through my lens.

| Feature | Why here |
|---|---|
| **#36 Deep Shopify Sync (2A hardlock+push, then 2B BOPIS+loyalty bridge)** | XL/18 days. Unlocks online revenue + the `shopify_customer_id` backfill that retroactively *upgrades #47 CL reorder* from "call us" to one-tap checkout. Gated by the BVI/Shopify cutover (owner-gated step-7 per memory). Phase 2A first; 2B after 2 weeks stable. |
| **#37 B2B Corporate Gifting & Bulk Orders** | L/12 days. New corporate channel (bulk eyewear, gifting). Do the zero-risk schema prep (`corporate_accounts`, `quote_id` fields) early as a no-op migration; build the quote→AR pipeline only after retail money-integrity is battle-tested. Reuses vendor_portal.py token pattern for the client portal. |
| **#49 Family/Household Loyalty Wallet** | 5 days but touches POS hot path; family-oriented optical retail makes this a real retention lever. Deploy standalone loyalty for one full cycle first, then pilot one store behind a flag. |
| **#48 Multi-Category Servicing Portal** | L/12, roi=3. New service-revenue stream but not blocking; reuses workshop/vendor-portal/SMS patterns. Last because lowest roi of the CRM cluster. |
| **#52 WhatsApp PDF Invoicing** | 5 days; eco/brand + mobile-capture value but roi-light vs revenue. Needs MSG91 *document-message* template approval. |

**Rough weeks:** 8+.

**Sequencing reason:** Channels scale revenue but carry the heaviest external dependencies (Shopify cutover, Razorpay, DLT doc templates, AR/finance integration). They pay back over quarters, not weeks. Building them before the reactivation engine is live would mean spending the most engineering on the slowest-payback work — the exact inversion of revenue-velocity optimization.

---

## EXPLICITLY DEPRIORITIZED THROUGH THE REVENUE-GROWTH LENS

These are good features — just not *revenue-generating*. I would let the Margin/Loss-Prevention and HR architects own them and **not** spend revenue-phase weeks on them, even the ones flagged `quickwin=yes`:

- **#21 Defective Quarantine, #35 Cost Masking, #33 Leaderboards, #34 Ticker** — quick wins, but loss-prevention / morale / visibility, **not incremental sales**. My disagreement-with-quick-wins-first is concentrated here.
- **#1–#10, #13–#20, #23, #25** (inventory balancing, lab routing, consignment, spares, reconciliation, petty cash, rebates, landed cost, RTV, JE maker-checker, etc.) — margin and capital-efficiency tools. Real money, but defended cash, not *new* sales. Other lenses should drive these.
- **#26–#32, #44** (approval matrices, attendance, rostering, SPIFFs, clawbacks, own-use, duplicate-merge) — control/HR/data-hygiene. **#44 duplicate-merge** has indirect revenue value (clean data sharpens #39/#40/#41 targeting) — I'd pull it forward to *late R3* only if targeting noise becomes measurable; otherwise it stays out of the revenue program.

**One nuance:** SPIFFs/commission (#30/#31) are the strongest *behavioral* revenue levers in the HR cluster — if the HR architect builds them, sequence them right after R2 so staff are incentivized on exactly the conversion/attachment behaviors R2 starts measuring.

---

## ONE-LINE SUMMARY FOR THE ORCHESTRATOR

Revenue velocity = **light MSG91 (R0) → reactivate predictable repeat buyers: CL + lapsed (R1) → convert the people already in-store: handover + walkout + measure (R2) → tell staff who to call: NBA + churn (R3) → lift basket: lookbooks + promos/bundles, gated on money-integrity merge (R4) → open new channels: Shopify + B2B (R5).** The critical path is the **MSG91-live + Shopify-cutover** owner actions, not engineering — start both Day 1, in parallel with R0.

**Files that ground these claims (all confirmed to exist):** `backend/agents/implementations/megaphone.py` (dispatch+DND+atomic claim), `backend/api/services/campaign_segments.py` (rx_expiry/winback/birthday resolvers), `backend/api/routers/crm.py` (Customer 360, `/cl-refill-status`, churn-risk, RFM), `backend/api/routers/walkouts.py` + `follow_ups.py` (auto-generate), `backend/api/routers/handoffs.py` (clinical handover ~80%), `backend/api/routers/vouchers.py` + `backend/api/services/loyalty_engine.py` (atomic + FIFO lots), `backend/api/services/notification_service.py` (DISPATCH_MODE gate). **NBA (#39) confirmed net-new** — no `nba`/`next_best` code exists. **Shopify customer bridge is thin** — `shopify_customer_id` only set on online-origin customers (`online_order_mapper.py`), confirming the #47/#36 backfill dependency.


# === Lens: Shared-Infrastructure-First ===
I now have everything I need. The codebase confirms my lens's central thesis: there are strong **primitives** (atomic `find_one_and_update`, pure ledger math, `stock_units.status` state machine, `labels.py` scan-advance, `integration_config` DB-first/env-fallback) but **no generic engines** — every feature digest assumes it will build its own approval gate, its own settings doc, its own ledger. That's the duplication my lens exists to kill.

Here is my plan.

---

# Shared-Infrastructure-First Program Plan — IMS 2.0 (52 features)

## The core argument (where I disagree with quick-wins-first)

The digest tags 11 features "quickwin=yes" (#7, #21, #33, #34, #35, #39, #45, #46, #50, plus #24, #40). A quick-wins-first orchestrator ships those first and feels productive. **I disagree for ~half of them.** Four "engines" sit underneath 24 of the 52 features. If you let quick-wins go first, those 24 features each grow their own one-off approval check, settings doc, ledger writer, and tender map — and you pay the integration tax 24 times, then a re-platforming tax later when the divergent copies must be reconciled (this is exactly the legacy `subagents.AGENT_REGISTRY` shadow bug, the 4 finance ledgers, and the "local pricing_caps table under-capped" bug the team already lived through).

My rule: **build the engine the cycle BEFORE its first dependent ships, never inside it.** A quick-win is only allowed to jump the queue if it touches **zero** shared surface (true greenfield). By that test, #35 (cost-masking), #21 (quarantine barcode), #34 (target ticker) genuinely ARE safe to ship early; #7, #40 (read-only AI) are safe; but #33, #39, #45, #46, #50 should wait a few weeks for the engine they will otherwise fork.

## The four engines (grounded in what exists)

| Engine | What exists today (reuse) | What's missing (build once) | Dependents |
|---|---|---|---|
| **E1 — Generic Approval / PIN-Override engine** | `agents/proposals.ProposalStore` (AI-only lifecycle), POS `discount_approved_by` (a free *string*, no verification), `audit_logs`, `notification_service`, `task_escalation` ladder | A human `approval_requests` collection + state machine (REQUESTED→APPROVED/REJECTED/EXPIRED), real verified PIN, role-tiered routing, TTL auto-reject, atomic single-use consume, async remote (WhatsApp) approve | #17, #25, #26, #27, #38, #44 (+ used by #1, #6, #11, #21, #32, #37) |
| **E2 — Configurable Settings-Matrix engine** | `integration_config` (DB-first/env-fallback), `business_settings` docs, `pricing_caps`, `role_caps` | A typed, versioned, store-scoped `policy_settings` registry with one Settings UI renderer + one read API (`get_policy(key, store_id)`), instead of N bespoke setting docs | #10, #14, #41, #46, #48, #28, #34, #49 (+ every flagged feature) |
| **E3 — Item-Event / Barcode Ledger** | `stock_units.status` machine, `labels.py` scan-advance (forward-only, loud), `barcode.py`, `serial_numbers` collection | A unified append-only `item_events` ledger (every status transition, scan, serial-bind, quarantine = one event) so status changes stop being scattered `$set`s | #2, #6, #9, #21 (+ #5, #15, #36 hardlock, #8) |
| **E4 — Tender-Routing / Ledger** | order tenders, `cash_register` pure math, `store_credit_ledger`, `loyalty_engine`, `vouchers.redeem_voucher_atomic` (the gold-standard concurrency primitive) | A canonical `tender_ledger_map` (tender→Tally ledger) + one routing function every money path calls, so Tally stops booking everything as "Cash" | #16, #22, #23 (+ #27 proportional refund, #17 petty cash) |

---

## Phase 0 — Engine E2 (Settings-Matrix) + true-greenfield quick wins · ~2 weeks

**Goal:** Stand up the cheapest, most-depended-on engine first, and ship the only quick-wins that touch nothing shared (morale + value with zero forking risk).

- **E2 Settings-Matrix engine** (build): typed `policy_settings` registry keyed `(key, scope=global|store|entity)`, one `get_policy()` reader with default-fallback, one generic Settings-UI card renderer driven by a schema. Reuses `integration_config`'s DB-first/env-fallback contract exactly.
- **#35** Cost/Margin Masking (S, quickwin) — pure additive `cost_mask.py` helper, zero schema. Ship immediately, in parallel with E2.
- **#21** Defective Quarantine Barcoding (S, quickwin) — reuses `stock_units.status` + `labels.py` QZ tray. Ship now; it becomes the **first event producer** that E3 will later formalize (so build it writing through a thin `record_item_event()` shim we'll harden in Phase 2).

**Sequencing reason:** E2 is the smallest engine (config-loader contract already proven) and the widest dependency — every feature flagged "per-store" or "configurable" needs it. Building it first means Phases 1–4 author a *schema row* instead of a *new settings doc + UI + endpoint* each time. **This is the highest-leverage two weeks in the program.**

---

## Phase 1 — Engine E1 (Approval/PIN) + its cheapest dependents · ~3 weeks

**Goal:** Build the human approval engine, then immediately retire the most painful manual-approval pain points.

- **E1 Approval/PIN engine** (build, ~6 days): `approval_requests` state machine, verified per-approver PIN (not a free string), role-tier routing via existing `role_caps`, TTL auto-reject, **atomic single-use consume** (mirror `redeem_voucher_atomic`), async WhatsApp approve via `notification_service`. POS-critical paths stay behind an off-by-default flag (read from E2).
- **#17** Petty Cash Controls (M, "build now") — first real dependent: float debit needs E1 for the above-threshold approval + E2 for `float_threshold`.
- **#25** Maker-Checker Journal Entries (M) — E1 *is* maker-checker; this is a thin JE collection on top.
- **#26** Remote Approval Matrix (discounts/leave) (M) — replaces the fake `discount_approved_by` string with a real E1 gate. **Disagreement note:** the digest phases this at 3 and treats the POS halt as the scary part; with E1 already built and flag-gated, the POS wiring is the *last, smallest* step, not a blocker.

**Sequencing reason:** #17/#25/#26 are the three features whose *entire* novelty is "an approval workflow." Building them after E1 turns each into a 2–3 day job. Doing them before E1 (quick-wins-first) would spawn three incompatible approval mini-engines that #27/#38/#44 would then have to reconcile.

---

## Phase 2 — Engine E3 (Item-Event Ledger) + serial/lab/inventory dependents · ~3 weeks

**Goal:** Formalize the scattered `stock_units.status` `$set`s into one append-only event ledger, then ship the inventory-integrity features that all read it.

- **E3 Item-Event ledger** (build): append-only `item_events` (transition/scan/serial-bind/quarantine), retrofit `labels.py` scan-advance and the Phase-0 quarantine shim to emit through it.
- **#6** Luxury Serial Tracking (M, phase=2 already) — serial-bind + verify-return become E3 events; the verify-return mismatch routes a P1 task through E1.
- **#2** Internal Lab Routing & Tray (M) — folds into `workshop_jobs` + extends `labels.py` scan_advance, exactly as the digest demands; now it's just new station rows + E3 events.
- **#8** PO-vs-GRN Variance / Backorder (M) — backorder lines are E3 events surfaced via the existing TASKMASTER tick.
- **#9** DC-to-Invoice Tally (M) — DC stock-minting + HARDLOCK ride E3; the HARDLOCK approval rides E1.

**Sequencing reason:** #6 is the only inventory feature the digest already puts in Phase 2, and it's the natural first event-ledger consumer. #2/#8/#9 are the digest's "Phase 3 inventory batch" — but they collapse from 6–8 day builds to 3–4 day builds *only if E3 exists first*. **Disagreement note:** the digest never names this shared ledger; it lets each inventory feature mutate status independently, which is how the team got divergent status handling before.

---

## Phase 3 — Engine E4 (Tender-Routing) + money-integrity dependents · ~3 weeks

**Goal:** One tender→ledger router so reconciliation and Tally stop being wrong, then the loss-prevention money features that consume it.

- **E4 Tender-Routing engine** (build): canonical `tender_ledger_map` (owner-supplied Tally names via E2), one routing fn on every money path, reusing `cash_register` math + `store_credit_ledger` + the `redeem_voucher_atomic` concurrency pattern.
- **#22** Split-Tender Matrix + Ledger Routing (M, phase=2) — **Phase A (ledger routing, 2 days) IS the engine's first deliverable** and fixes the live "everything books as Cash" Tally bug. Family loyalty pool (Phase B) defers behind an E2 flag.
- **#16** Automated Bank/Cash/POS Reconciliation (L, roi=5) — pure assembly once E4 routes tenders and `cash_register` is in use.
- **#23** Blind EOD Cash Tally / Z-Read (M) — reuses `cash_register` denomination grid; the blind-submit→manager-lock is an E1 approval.
- **#27** Refund Approval Matrix + original-tender enforcement (M, HIGH risk, phase=2) — proportional split-refund needs E4; the tiered PIN gate needs E1. **Sequenced here deliberately** (not Phase 1) because it depends on *both* E1 and E4 — shipping it earlier would force a tender-routing stub it'd then rebuild.

**Sequencing reason:** E4's first sub-deliverable (#22 Phase A) is a 2-day live-bug fix with ROI now, which buys the rest of the engine. Everything else in this phase is the "Margin & Loss Prevention" cash cluster, all of which silently assume a tender ledger the digest never centralizes.

---

## Phase 4 — Engine consumers fan-out (config-heavy CRM + omnichannel) · ~4 weeks

**Goal:** With all four engines live, the long tail of "configurable" CRM/marketing features becomes schema-authoring, not engine-building. Ship the genuine quick-wins **here**, where they're truly cheap.

- E2-driven config features (each = a schema row + a tick method): **#46** Configurable Reminders, **#41** Lapsed-Patient Reactivation, **#10** Ageing Auto-Liquidation flags, **#14** Non-Adapt + Vendor Credit, **#48** Servicing Portal, **#49** Family Loyalty Wallet, **#28** Geofence/IP attendance config, **#34** Global Target Ticker.
- E1-driven: **#38** Endless-Aisle inter-branch (selling-store request → fulfilling-store accept = an E1 approval over the transfer engine), **#44** Duplicate Merge (HIGH risk; the merge is an E1-approved, audited operation reading E3/E4 for FK integrity), **#37** B2B Bulk (quote approval ladder = E1; AR via E4).
- Now-cheap quick-wins: **#39** NBA dashboard, **#50** Clinical→Retail handover, **#33** Leaderboards, **#45** Walkout — all reuse MEGAPHONE/handoffs/notifications and read E2 for thresholds.
- AI read-only (parallel, zero engine dependency, can float anywhere): **#7** Predictive Purchasing, **#40** VIP Churn, **#24** Optom-conversion dashboard — all extend ORACLE/`ai_proposals`, no new engine.

**Sequencing reason:** this is where quick-wins-first and I **converge** — but my version of these features is 30–50% smaller because the approval, settings, event, and tender plumbing already exists. The digest's per-feature day estimates assume each builds its own; mine assume reuse.

---

## Phase 5 — Heavy / high-risk integration features · ~5–6 weeks

**Goal:** The XL/HIGH-risk items that need all four engines stable in prod for weeks first.

- **#36** Deep Shopify Sync (XL, HIGH) — hardlock rides E3, BOPIS rides E4 tender + transfer, loyalty bridge rides E2/E4. Split exactly as digest says (2A hardlock, 2B BOPIS), but *after* engines are battle-tested.
- **#11** Dynamic Promotions, **#12** Cross-Category Bundling (both L, touch POS hot path) — promo evaluation reads E2 config; discounts route through E4; partial-return proration reads E3/returns. Build the pure `evaluate_promos` + unit tests early-parallel (zero POS risk), wire to POS last behind flag.
- **#1** Intelligent Inventory Balancing, **#3** Consignment, **#4** Parts/Spares, **#5** Cannibalization, **#13** Remake/Spoilage, **#18** Rebate, **#19** Landed Cost, **#20** RTV Debit, **#29** Rostering, **#30** SPIFFs, **#31** Commission Clawback, **#32** Own-Use, **#42** VIP Lookbooks, **#43** VIP Triggers, **#47** CL Reorder, **#51** Use-It-Or-Lose-It, **#52** WhatsApp PDF Invoice — each lands as a thin consumer of 1–3 engines (e.g., #1/#3/#4 = E3 events + E1 approvals + E4 AP routing; #30/#31/#32 = E2 config + payroll merge; #42/#43/#47/#51/#52 = E2 + MEGAPHONE). **#1 specifically must wait for E3 + stock-count hardening (#15) so its recommendations aren't noise — agreeing with the digest's data-quality caveat.**
- **#15** Blind Stock Takes (M) — RBAC/field-suppression on existing stock-count, reading E3 events; build alongside #1's prerequisite.

**Sequencing reason:** these are the features the digest itself flags HIGH/XL risk on the revenue-critical path. By now every primitive they need is proven in production, so the risk is the *feature*, not the *plumbing*.

---

## Program shape at a glance

| Phase | Engine built | Headline dependents | Weeks |
|---|---|---|---|
| 0 | **E2 Settings-Matrix** | #35, #21 (greenfield quick wins) | 2 |
| 1 | **E1 Approval/PIN** | #17, #25, #26 | 3 |
| 2 | **E3 Item-Event Ledger** | #6, #2, #8, #9 | 3 |
| 3 | **E4 Tender-Routing** | #22, #16, #23, #27 | 3 |
| 4 | (consume all 4) | #46,#41,#10,#14,#48,#49,#28,#34,#38,#44,#37,#39,#50,#33,#45,#7,#40,#24 | 4 |
| 5 | (consume all 4) | #36,#11,#12,#1,#3,#4,#5,#13,#15,#18,#19,#20,#29,#30,#31,#32,#42,#43,#47,#51,#52 | 5–6 |

**~20 weeks total.** Four engines built once (~weeks 1–11), then 44 features land as thin consumers (weeks 4–20, heavily parallelizable across build sessions since the engine contracts are stable).

## Three opinionated stances (my lens vs. quick-wins-first)

1. **Engines before their first dependent, always.** The single most expensive mistake here is letting #17 or #46 ship before E1/E2 and quietly fork the pattern. Quick-wins-first guarantees this; I forbid it.
2. **Only true-greenfield quick wins jump the queue** (#35, #21, #7, #40, #24). The "quick wins" that touch approval/settings/tender (#33, #39, #45, #46, #50, #27) are *fake* quick wins — they're cheap only after their engine exists, so I deliberately delay them 3–8 weeks to make them genuinely cheap.
3. **#27 is sequenced to Phase 3, later than its digest phase=2**, because it's the one feature needing *both* E1 and E4. Shipping it at its nominal phase forces a tender-routing stub it would rebuild — the exact double-work my lens exists to prevent.

## Cross-cutting owner questions my lens forces up-front (answer once, unblock many)

- **E1:** Per-approver PINs or one shared PIN? (sets the engine's auth model for #17/#25/#26/#27/#38/#44 — answer once). Default PIN TTL before auto-reject?
- **E2:** Confirm store-scoped settings hierarchy: global → entity → store override order? (every "per-store" question in the digest collapses into this one).
- **E4:** The exact Tally ledger names per tender (Cash/UPI/Card/Bank/EMI/Store-Credit/Voucher/Loyalty) — asked in #22, #16, #17, #20, #25 separately; **collect once** for E4 and never ask again.
- **E3:** Are serial numbers (#6) and tray/job barcodes (#2) the same scan namespace or distinct? (decides whether one `item_events` ledger or two).

Grounding files: `backend/api/services/integration_config.py` (E2 contract to copy), `backend/api/routers/vouchers.py:170` `redeem_voucher_atomic` (E1/E4 concurrency primitive), `backend/api/services/cash_register.py` + `store_credit_ledger.py` (E4 pure-math reuse), `backend/api/routers/labels.py` scan_advance + `backend/api/services/barcode.py` (E3 base), `backend/agents/proposals.py` ProposalStore (E1 lifecycle to generalize for humans).
All four architects' core claims check out against the codebase. The substrate exists exactly as described: `cash_register.py`, `purchase_match.py`, `campaign_segments.py` (with winback/lapsed), `integration_config.py`, atomic `returned_qty` in returns, `redeem_voucher_atomic` in vouchers, serial CRUD in inventory, and all the CRM/walkout/handoff routers. The only true greenfield items are `cost_mask.py` and `money_guard.py` (no such files). Now I have what I need to synthesize.

# IMS 2.0 Enterprise Roadmap — Final Plan

## Executive summary

**How much already exists:** A lot. I verified the substrate in the live repo: the atomic-mutation primitive (`vouchers.redeem_voucher_atomic`, `returns.returned_qty` reservation), pure money math (`cash_register.py`, `store_credit_ledger.py`, `loyalty_engine.py`), the stock-status state machine + scan-advance (`stock_units.status`, `labels.py`), serial CRUD (`inventory.py`), the config/flag pattern (`integration_config.py`), the comms engine (`megaphone.py`, `notification_service.py`, `campaign_segments.py` with `winback`/`lapsed`), and the CRM/clinical/floor surfaces (`crm.py`, `handoffs.py`, `walkouts.py`, `follow_ups.py`, `purchase_match.py` variance, `points_calculator`/`payout_calculator`). Of the 52 features, roughly 14 are 60–90% built — the delta is a tick method, an aggregation, a config doc, or one page. Only two files are genuine greenfield (`cost_mask.py`, `money_guard.py`).

**The smartest path** reconciles all four lenses, which disagree less than they appear to. They converge on three facts: (1) a small set of **shared engines** sits under ~24 features and must be built once, before their first dependent; (2) the **comms pipe going live (MSG91 DLT + DISPATCH_MODE)** is an owner-action critical path, not engineering, and gates the entire CRM tier; (3) anything touching the **POS/refund/money path must wait for the in-flight `claude/fix-money-integrity` branch** to merge, and ships behind an off-by-default flag.

The synthesis: **honour quick-wins-first** for the genuinely-greenfield, zero-shared-surface wins (so the owner sees value in week 1), **but front-load the two cheapest loss-prevention controls and the cheapest shared engine alongside them** so the whole program accelerates and the worst money leaks close early. We do NOT let a flashy "quick win" that secretly forks an approval/settings/tender engine jump the queue — those are fake quick wins and we delay them a few weeks to make them genuinely cheap. We do NOT defer the ROI=5 reconciliation engine to "someday" — it's the program centrepiece, sequenced right after the cash-discipline control it depends on.

**The shape:** Wave A (weeks 1–3) — true quick wins + the Settings engine + the two cheapest controls, all owner-visible, zero revenue risk, MSG91 DLT approval kicked off in parallel. Wave B (weeks 4–9) — the comms tier lights up (reminders, reactivation, in-store conversion) on top of the Settings/reminder engine, plus the Approval and Item-Event engines and their cheap inventory/control dependents. Wave C (weeks 9–14) — the cash/tender/refund money core behind the money-integrity merge. Wave D (weeks 14–22) — intelligence + AOV-lift behind flags. Wave E (weeks 22+) — heavy omnichannel/B2B.

## Already mostly built (cheap wins)

| Feature id | Name | %done | What's left |
|---|---|---|---|
| #7 | AI Predictive Purchasing | 85% | Burn-rate aggregation (~50 lines) + payload enrichment + filtered JarvisPage view. `_propose_reorders` + `ai_proposals` Act/Ignore lifecycle live. |
| #21 | Defective Quarantine Barcoding | 90% | `QUARANTINE` status value + red-label payload + lift-approval gate. Reuses `stock_units.status`, `labels.py` QZ tray, audit, vendor_returns. |
| #45 | Walkout Module | 60% | `items[]` field + TASKMASTER scheduler + accountability panel + VIP escalation. Schema/modal/dashboard/follow_ups/megaphone hook exist. |
| #46 | Configurable Reminders | 80% | `reminder_rules` config + 4 MEGAPHONE triggers + cross-rule frequency cap. notification_service + templates + MEGAPHONE scan exist. |
| #41 | Lapsed-Patient Reactivation | 75% | `lapsed_patient` resolver + `_scan_lapsed_patients` tick + voucher brand-exclusion gate. `winback` segment already in code. |
| #50 | Clinical→Retail Handover | 80% | Handover doc + floor inbox + TTL + "mark served". `handoffs.py` + repository exist. |
| #24 | Optometrist→Retail Conversion | 70% | One aggregation over `eye_tests`+`orders` + Clinical-tab page. All raw data exists. |
| #40 | VIP Churn Prediction | 80% | Interval-baseline in `oracle.py` + 2 crm endpoints + watchlist page. ORACLE sweep + proposals + RFM `lifecycle_phase` exist. |
| #8 | PO vs GRN Variance / Backorder | 75% | Surface variance + aged-backorder alert into existing TASKMASTER tick. `purchase_match.py` variance + `PARTIALLY_RECEIVED` exist. |
| #6 | Luxury Serial Tracking | 70% | Wire capture into GRN + stamp on order + verify-return endpoint. `serial_numbers` collection + CRUD exist. (gate is staff training, not code) |
| #39 | NBA Daily Dashboard | 50% | Scoring method + `nba_scores` TTL collection + 3 endpoints + page. Signals + dispatch infra + `tags[]` exist; ranking logic net-new. |
| #33 | Gamified Leaderboards | 65% | Privacy/visibility config + leaderboard UX. `points_calculator`/`payout_calculator` scoring engine exists. |
| #34 | Global Target Ticker | 60% | Assemble ticker endpoint + UI card + milestone push. Revenue aggregation + `budgets.py` exist. |
| #35 | Cost & Margin Masking | 10% (greenfield but tiny) | New `cost_mask.py` + `CostCell` component + ~12 call sites. Pure-additive, zero schema. |
| #16 | Bank/Cash/POS Reconciliation | 40% | Assembly on top of `cash_register.py` once tenders route correctly (E4) and till discipline (#23) exists. Highest ROI in program. |

## Shared infrastructure (engines to build once)

Four engines sit under ~24 features. Building each **the cycle before its first dependent** is the single biggest accelerant and the biggest risk-avoidance (it prevents the divergent-copy class of bug the team already lived through — the legacy `AGENT_REGISTRY` shadow, the local `pricing_caps` under-cap). Each engine reuses a proven primitive already in the repo.

| Engine | Build once | Reuses (verified) | Unblocks |
|---|---|---|---|
| **E1 — Money-guard** (`services/money_guard.py`) | Generalised concurrency-safe balance mutation (guarded `find_one_and_update`) | `vouchers.redeem_voucher_atomic`, `returns.returned_qty` | every balance change: #17, #22, #27, #49, store-credit, loyalty, petty-cash, consignment #3 |
| **E2 — Settings-Matrix** (`policy_settings`) | Typed, store-scoped registry (`get_policy(key, scope)`) + one Settings-UI renderer + DB-first/env-fallback | `integration_config.py`, `business_settings`, `pricing_caps` | every "configurable/per-store" feature: #10,#14,#28,#34,#41,#46,#48,#49 (+ all flag-gated) |
| **E3 — Item-Event ledger** (`item_events`) | Append-only status/scan/serial-bind/quarantine transition guard + audit | `stock_units.status`, `labels.py` scan-advance, `serial_numbers`, `audit_logs` | #2,#5,#6,#8,#9,#15,#21,#36-hardlock |
| **E4 — Approval/PIN + maker-checker** (`approval_requests`) | Human approval state machine (REQUESTED→APPROVED/REJECTED/EXPIRED), verified per-approver PIN, role-tier routing, TTL, single-use atomic consume (via E1) | `agents/proposals.ProposalStore`, expense separation-of-duties, `role_caps`, `notification_service` | #17,#25,#26,#27,#38,#44 (+ #1,#6,#11,#21,#32,#37) |
| **E5 — Tender-routing** (`tender_ledger_map`) | One tender→Tally-ledger router on every money path (fixes "everything books as Cash") | `cash_register.py`, `store_credit_ledger.py`, E1 | #16,#22,#23,#27,#17,#20,#25 |
| **E6 — Reminder/segment rail** | `reminder_rules` config + MEGAPHONE trigger pattern + cross-rule frequency cap + voucher-gate | `megaphone.py`, `campaign_segments.py`, `notification_service.py`, `vouchers.py` | #46,#41,#47,#40,#43,#51,#52,#42 |

(E1, E2, E6 are cheap and front-loaded; E3, E4, E5 underpin the money/inventory tiers.)

## Phased plan

### Phase 0 — Day-one wins + cheapest engines + cheapest controls (weeks 1–3)
- **Feature ids:** #35, #21, #34, #7, #40 + build **E1, E2, E6**; kick off **MSG91 DLT approval** (owner, parallel).
- **Goal:** Owner-visible value in week 1; stand up the three cheap engines every later phase reuses; close the two cheapest leaks (re-shelf #21, cost-visibility #35); ship the two read-only AI wins (#7, #40) that need no engine.
- **Sequencing reason:** These are the only items that are simultaneously high-visibility, zero-shared-surface (or building the surface), and zero revenue risk. #7/#40 are read-only over existing ORACLE/proposals. E1/E2/E6 are the cheapest engines and the widest dependencies, so building them now makes Phases 1–4 author a schema row, not a new subsystem. DLT approval is a multi-week owner round-trip — start day 1 so the comms tier isn't blocked later.
- **Rough weeks:** 3 (parallelizable to ~2 with multiple build sessions).
- **POS-flag note:** None of these touch POS. No flag needed.

### Phase 1 — Comms tier lights up + Item-Event engine + cheap inventory/control dependents (weeks 4–9)
- **Feature ids:** #46, #41, #50, #24, #45, #39 (comms/conversion, on E6) + **E3, E4** + #6, #8, #2, #9 (inventory, on E3) + #17, #25, #26 (approval, on E4).
- **Goal:** Turn the dormant comms engine into live, measured, opt-out-safe outbound (the highest revenue-per-day work once the pipe is live), convert in-store traffic you already pay for, and retire the manual-approval and inventory-integrity pain points on the new engines.
- **Sequencing reason:** E6 was built in Phase 0, so the CRM cluster is now config-only — cluster them so four downstream features are cheap. #50/#24 share the exam→order join (build back-to-back). #6/#8/#2/#9 collapse from 6–8d to 3–4d builds *because E3 exists first*. #17/#25/#26 are pure approval workflows — trivial once E4 exists, three forked mini-engines if built before it. #6 sits at end of its sub-batch (training-gated, ships soft-flag).
- **Rough weeks:** 6.
- **POS-flag note:** #45 ships **soft-block only** (banner + compliance score), never hard-block POS. #26's POS-halt path is the **last** step, behind an off-by-default flag the orchestrator flips after staging validation.

### Phase 2 — Cash & tender money core (weeks 9–14) — gated on money-integrity merge
- **Feature ids:** build **E5** + #23 → #16 → #17(Tally leg) → #22-Phase-A → #27.
- **Goal:** Make cash physically traceable from till-open to bank deposit with blind counting; fix the Tally mis-posting; control the refund outflow valve. This is the largest silent leakage target in a cash-heavy 6-store chain, and #16 is the only ROI=5 feature.
- **Sequencing reason:** **#23 must precede #16** — #16 is explicitly gated on the till open/close discipline that #23 establishes. E5's first deliverable (#22-Phase-A, 2 days) fixes the live "everything books as Cash" bug and pays for the rest of the engine. #27 needs *both* E4 (PIN) and E5 (proportional tender refund), so it lands last here, not earlier.
- **Rough weeks:** 5.
- **POS-flag note:** **Hard prerequisite — `claude/fix-money-integrity` must be merged and verified before this phase starts.** #27 ships behind `REFUND_GATE_ENABLED`, staged store-by-store. #22-Phase-A (ledger routing) is low-risk and can ship first within the phase; #22-Phase-B (family pool) is deferred behind its own flag.

### Phase 3 — Margin/loss-prevention finishers + intelligence + config CRM (weeks 14–18)
- **Feature ids:** #15, #20, #13, #18, #19 (margin/AP, on E3/E5) + #43, #47, #51, #52 (CRM, on E6) + #10, #14, #28, #49, #38, #44 (config/approval consumers) + #33 (leaderboards).
- **Goal:** Establish blind stock truth (#15 — the prerequisite the digest names for #1/#7-quality), close vendor-side leaks (#20/#18/#19/#13), and fan out the long tail of config-driven CRM features that are now thin consumers of E2/E4/E5/E6.
- **Sequencing reason:** #15 reads E3 events + needs clean-data hardening; it gates the heavy analytics tier. #43/#47/#51/#52 are E6 schema rows. #38/#44 are E4-approved operations reading E3/E5. #49 touches POS hot path — pilot one store behind a flag after standalone loyalty runs a cycle.
- **Rough weeks:** 4.
- **POS-flag note:** #49 (family wallet) behind `FAMILY_WALLET_ENABLED`, pilot one store. #38 endless-aisle behind `ENDLESS_AISLE_ENABLED`. #47 CL-reorder needs MSG91 live + Shopify checkout (graceful "call us" fallback so it ships pre-cutover).

### Phase 4 — AOV-lift promos + heavy CRM (weeks 18–22) — POS path, flag-gated
- **Feature ids:** #11, #12 (promo/bundle), #42 (lookbooks), #1 (inventory balancing), #37 (B2B), #31, #30, #32 (HR commission/SPIFF/own-use).
- **Goal:** Lift average order value and serve high-value/B2B segments — higher effort, longer payback, high absolute rupee impact.
- **Sequencing reason:** Build the pure `evaluate_promos`/bundle function + tests with **zero POS wiring first** (no risk), wire to POS last behind flag. #1 needs E3 + #15's blind-stock truth so recommendations aren't noise. #30/#31/#32 share the payroll earnings-merge pattern — design together; do commission-calc fix before SPIFFs.
- **Rough weeks:** 4.
- **POS-flag note:** #11/#12 behind `PROMO_ENGINE_ENABLED`, enabled store-by-store **only after money-integrity verified** (partial-return proration on a bundle is a financial leak otherwise). #32 behind `OWN_USE_ENABLED`.

### Phase 5 — Omnichannel & B2B scale (weeks 22–30+)
- **Feature ids:** #36 (2A hardlock → 2B BOPIS), #48 (servicing portal), #3 (consignment), #4 (parts/spares), #5 (cannibalization), #29 (rostering).
- **Goal:** Open new revenue *channels* (online, corporate, service). Heaviest external dependencies (Shopify cutover, Razorpay, DLT doc/rich templates, AR integration); longest payback.
- **Sequencing reason:** All four/five engines are now battle-tested in prod, so the risk is the feature not the plumbing. #36-2A first; #36-2B after 2 weeks stable (and it retroactively upgrades #47 to one-tap checkout). #36 is owner-gated on the BVI/Shopify cutover (step-7).
- **Rough weeks:** 8+.
- **POS-flag note:** #36 hardlock + BOPIS ship in separate deploys (too much surface to verify at once). #3 behind `CONSIGNMENT_ENABLED`.

## Quick-win batch (first features the build session takes)

Take these **in this order** in the first build session. All are verified extend-don't-rebuild, owner-visible, zero revenue risk:

1. **#35** Cost/Margin Masking (2d) — new `cost_mask.py` + `CostCell` + ~12 call sites.
2. **#21** Defective Quarantine (2d) — `QUARANTINE` status + red-label + lift gate (writes through the E3 shim).
3. **E2** Settings-Matrix engine (3d, parallel) — unblocks every config feature.
4. **#7** Predictive Purchasing (5d) — burn-rate aggregation + JarvisPage view (read-only, no engine).
5. **#34** Global Target Ticker (4d) — assemble endpoint + UI card.
6. **#40** VIP Churn (4d) — interval-baseline + watchlist page (read-only).
7. **E6** Reminder/segment rail (5d) — `reminder_rules` + frequency cap, then **#46** reminders (4 triggers) rides it immediately.

**Owner action in parallel from day 1:** approve MSG91 DLT templates (utility + marketing). This is the gating task for the entire comms tier — start it before any CRM code.

## Dependency notes

- **`claude/fix-money-integrity` branch** must merge before Phase 2 (and before any POS-order promo work in Phase 4). Returns over-refund + loyalty double-spend.
- **MSG91 DLT + `DISPATCH_MODE=live`** gates #46/#41/#47/#40-WhatsApp-arm/#45-personalised/#42/#51/#52. Owner-gated, multi-week — start in Phase 0.
- **Shopify cutover (BVI step-7)** gates #36, and the `shopify_customer_id` backfill upgrades #47/#36/#44. Owner-gated.
- **Engine-before-dependent (hard rule):** E1/E2/E6 before Phase 0–1 consumers; E3 before #6/#8/#2/#9/#15; E4 before #17/#25/#26/#27/#38/#44; E5 before #16/#22/#23/#27.
- **#23 before #16** (cash discipline before reconciliation). **#15 before #1** (blind-stock truth before balancing). **#46 before #41/#47/#43/#51** (reminder rail first). **commission-calc fix before #30/#31** (shared earnings-merge).
- **#6 placement:** code is ready early but the gate is GRN-staff training — ships soft-flag at end of its inventory sub-batch, not first.

## Risk register

| Risk | Severity | Mitigation |
|---|---|---|
| Promo/refund/split-tender shipped before money-integrity merges → compounds existing over-refund/double-spend | High | Hard gate: Phase 2 and POS-promo work blocked until branch merged + verified; all POS changes behind off-by-default flags flipped only on green tests |
| Each feature forks its own approval/settings/tender/ledger logic → divergent-copy bugs (the AGENT_REGISTRY/pricing_caps class) | High | Build E1–E6 the cycle before first dependent; forbid in-feature reimplementation; mandate `get_policy()`/`money_guard`/tender-router calls |
| MSG91 DLT approval slips → entire comms tier inert | High | Start owner approval Phase 0 day 1; build trigger code behind flag in parallel so go-live = one switch, zero further deploys; SMS fallback where DLT lags |
| Customer opt-out from message spam (permanent revenue loss) | Med | Cross-rule 30-day frequency cap built once in E6; marketing-consent gate for marketing-category, utility messages (ORDER_READY) exempt |
| Quick-wins reflex front-loads training-gated #6/#16 as if they were fast | Med | Code-readiness ≠ shippable; #6 ships soft-flag end-of-batch, #16 sequenced after #23 establishes till discipline |
| Analytics (#1/#7-grade) run on un-audited stock → phantom POs commit real cash | Med | #15 blind-stock hardening before #1; #7 ships as advisory proposals (Act/Ignore), never auto-PO |
| Blind-count zone-lock disrupts live POS (#15) | Med | Default to soft "under audit" flag + reconcile-subtract-sales, not hard SOLD-block; owner-decision on lock mode |
| Shopify hardlock + loyalty codes in one deploy → unverifiable surface (#36) | Med | Ship 2A hardlock and 2B BOPIS/loyalty in separate deploys, 2-week soak between |
| GST invoice numbering not financial-year-serial (known CLAUDE.md gap) | Med | Owner sign-off (format/accounting change) before touching; atomic counter + unique index per FY; care for Tally/receipt refs |

## PRIORITIZED OWNER DECISIONS

Ordered by how many features each unblocks. Each is a genuine business choice — no technical knowledge needed.

### 1. MSG91 DLT template approval + `DISPATCH_MODE=live`
- **Decision:** Approve the WhatsApp/SMS templates on the MSG91 portal (utility + marketing categories) and confirm the WhatsApp number, so the system can actually send messages.
- **Why it matters:** This is an owner action, not code, and it's the single longest lead-time item. The entire CRM/reminder revenue tier is built but inert until this is live.
- **Blocks:** #46, #41, #47, #40 (WhatsApp arm), #45 (personalised), #42, #51, #52.
- **Options:** (a) Start approval now, utility templates first (ORDER_READY, RX_EXPIRY) — recommended, gets the operational wins live fastest; (b) all templates at once (slower round-trip); (c) defer — leaves the comms tier dark.

### 2. Store-scoped settings hierarchy (E2)
- **Decision:** Confirm the override order for all per-store settings: **global → entity → store**.
- **Why it matters:** Every "should this be per-store or global?" question across the digest collapses into this one answer; it sets the E2 engine's model once.
- **Blocks:** #10, #14, #28, #34, #41, #46, #48, #49 + every flag-gated feature.
- **Options:** (a) global default, entity can override, store can override (most flexible) — recommended; (b) global + per-store only (simpler, no entity tier); (c) global only (no overrides — not recommended for a multi-entity chain).

### 3. Tally ledger names per tender (E5)
- **Decision:** Provide the exact Tally ledger names for each payment mode: Cash, UPI, Card, Bank Transfer, EMI, Store Credit, Gift Voucher, Loyalty.
- **Why it matters:** The tender router must match Tally exactly or it silently mis-posts. Asked separately in #22/#16/#17/#20/#25 — collect once, never ask again. Fixes the live bug where everything books as "Cash."
- **Blocks:** #16, #22, #23, #27, #17, #20, #25.
- **Options:** (a) share your current Tally chart-of-accounts ledger list (with your accountant) — recommended; (b) use IMS defaults and reconcile in Tally manually (more accountant work).

### 4. Approval/PIN model (E4)
- **Decision:** Per-approver PINs or one shared PIN? And default PIN validity before auto-reject?
- **Why it matters:** Sets the auth model for every human approval (discounts, refunds, JEs, merges, petty cash). Per-approver gives a real audit trail; shared is simpler but shareable.
- **Blocks:** #17, #25, #26, #27, #38, #44.
- **Options:** (a) per-approver PIN, 10-min TTL (strongest audit) — recommended; (b) one shared admin PIN, 10-min TTL (simpler); (c) per-approver PIN, 5-min (tighter) or 15-min (more lenient when approver is off-site).

### 5. Money-integrity merge sign-off + GST invoice serial
- **Decision:** Confirm the in-flight returns/loyalty fix can merge, and sign off on moving invoice numbering to a consecutive serial per financial year (a format/accounting change).
- **Why it matters:** Gates the entire cash/refund/promo tier. The invoice-serial change touches Tally/receipt references, so it needs your explicit OK.
- **Blocks:** Phase 2 entirely (#16/#22/#23/#27), POS-promo (#11/#12), and GST compliance.
- **Options:** (a) merge money-integrity now + approve FY-serial invoice change — recommended (closes a legal-compliance gap); (b) merge money-integrity now, defer invoice-serial to a dedicated review; (c) hold both (delays Phase 2 — not recommended).

### 6. Refund/discount approval tiers (₹ thresholds + original-tender)
- **Decision:** Set the ₹ tiers that trigger each approval level (auto / store-manager / admin / superadmin) and confirm refunds go back to the **original tender** by default.
- **Why it matters:** Directly controls how much autonomy floor staff have vs. how many approvals reach you; original-tender enforcement closes the "pay cash, refund to my UPI" skim.
- **Blocks:** #27, #26, #22-A, #17 (shares the threshold pattern).
- **Options:** (a) auto < ₹500 / admin > ₹2,000 / superadmin > ₹10,000, refund to original tender always — recommended balance; (b) tighter (auto < ₹200); (c) looser (auto < ₹1,000) — fewer interruptions, less control.

### 7. Serial-tracking scope + mismatch policy (#6)
- **Decision:** Which brands enforce serial capture from day one, and on a return-serial mismatch — hard-block the refund or soft-flag for manager review?
- **Why it matters:** Hard-block stops fraud but creates a counter dispute; soft-flag keeps the counter moving. Scope sets how much the GRN workflow changes on launch day.
- **Blocks:** #6 (+ informs #15 serial-scan reuse).
- **Options:** (a) the 7 luxury brands (Cartier/Chopard/Bvlgari/Gucci/Prada/Versace/Burberry) + soft-flag with P1 task — recommended; (b) all LUXURY category + hard-block; (c) manual opt-in per SKU (slowest rollout, least friction).

### 8. Cash-control thresholds (#23/#16) + reconciliation cadence
- **Decision:** Set the cash-variance alert tiers (e.g. ₹0/₹50/₹200 vs ₹0/₹100/₹500), per-shift vs once-daily reconciliation, and which bank CSV formats you use per entity.
- **Why it matters:** Too tight = alert noise; too loose = misses skimming. Cadence sets accountability granularity.
- **Blocks:** #23, #16.
- **Options:** (a) ₹0/₹100/₹500, once-daily — recommended for single-shift stores; (b) ₹0/₹50/₹200, per-shift (tighter, for high-volume/double-shift); (c) custom per store.

### 9. Discount/liquidation floors + category-cap interactions (#10/#11/#12)
- **Decision:** Set the minimum liquidation price (% of MRP) and the maximum bundle/promo discount ceiling, and whether liquidation/promo prices still allow further staff discount.
- **Why it matters:** Prevents below-cost sales when promo + staff discount + liquidation stack.
- **Blocks:** #10, #11, #12.
- **Options:** (a) liquidation floor 40% of MRP, promo ceiling 30%, no further staff discount on liquidation items — recommended; (b) floor 30% / ceiling 25%; (c) cost_price + 10% floor (category-specific, safest on thin margins).

### 10. Walkout + reminder behaviour caps (#45/#46)
- **Decision:** Walkout logging soft-block or hard-block? Max automated messages per customer per 30 days across all rules?
- **Why it matters:** Hard-block touches revenue-critical POS; the frequency cap prevents spamming a customer into a permanent opt-out.
- **Blocks:** #45, #46 (+ all E6 consumers via the cap).
- **Options:** (a) soft-block walkout (banner + compliance score) + cap 3 msgs/30 days — recommended; (b) manager-per-store toggle for hard-block + cap 5/30 days; (c) hard-block + no cross-rule cap (highest risk).

---
Grounding files verified to exist (relevant paths): `c:\Users\avina\IMS 2.0 CLAUDE COWORK\ims-2.0-railway-1\backend\api\services\cash_register.py`, `...\purchase_match.py`, `...\campaign_segments.py`, `...\integration_config.py`, `...\notification_service.py`, `...\loyalty_engine.py`, `...\store_credit_ledger.py`, `...\points_calculator.py`, `...\payout_calculator.py`, `...\rbac_policy.py`; routers `...\backend\api\routers\crm.py`, `...\walkouts.py`, `...\handoffs.py`, `...\follow_ups.py`, `...\vouchers.py`, `...\returns.py`, `...\labels.py`, `...\inventory.py`, `...\transfers.py`, `...\workshop.py`, `...\vendor_portal.py`, `...\vendor_returns.py`. Greenfield (do not exist yet, must be created): `cost_mask.py`, `money_guard.py`.
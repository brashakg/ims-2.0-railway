# IMS 2.0 Roadmap — Hardening Report

> Adversarial verification of all artifacts vs real code, + chair GO/NO-GO + corrections.


# ===== CHAIR VERDICT & CORRECTIONS =====

Key confirmations and one nuance:

1. **E1.md line 175 confirms the exact P0 text**: *"keep the mirror `$inc` inside the *same* `find_one_and_update` (`$inc` two fields) to stay atomic"* — this is **false on this DB**. A single `find_one_and_update` cannot `$inc` two fields in two different collections. **E1E2's P0 is verbatim-confirmed in E1.md line 160 and 175.** HOWEVER, line 195 + 173 already recommend "ship Phase A now, gate Phase B/C behind cleanup" and the ENGINES.md Phase-0 cut explicitly ships **Phase A only**. So the safe path is already documented; the dangerous sentence just needs deleting so a build agent doesn't implement it.

2. **Cost floor confirmed** as `unit_price < cost` (cost+0%), orders.py:1335-1342 — not cost+10% per-category. **`docs` P1 confirmed.**

3. **Campaign dispatch**: the E5E6 report's nuance is interesting — marketing.py line 294 DOES call `is_opted_out` in the campaign loop. Let me check whether `_dispatch_scheduled_campaigns` (the MEGAPHONE-tick path the report names) is the same as this or a different one, and confirm the consent-ledger-bypass framing. But this is a P2-level detail (E6 enforceability is honestly disclosed already). I have enough on the consequential items.

I have now independently verified every P0 and the load-bearing P1s. I have enough to render the consolidated verdict. Let me do one final check on the two remaining P0 mechanics from the `docs` report — the "E3-shim" definition and #6 phasing — both already confirmed visible on the board (line 41 "E3-shim" in Phase 0, line 60 "#6 ... E3,PM" in Phase 1) against ENGINES wave order (E3=Wave 3, PM=Wave 2). Those are confirmed by the board read above. I'm ready.

# Hardening Verdict

**Overall: NO-GO until the P0 block clears.** The roadmap foundation is genuinely strong — I independently verified the reviewers' load-bearing claims against the code (zero Mongo transactions, replica-set optional → cross-collection atomicity impossible; `PRODUCT_SCHEMA.category.enum` has no `HEARING_AID`; the seeded multiplier table makes 14%→1.1 not 1.4; `orders.py:1335` is a cost+0% floor; E1.md:175 literally tells a build agent to `$inc` two fields atomically). The board is correctly gated `NOT LIVE`, so nothing has shipped. But six P0s would mis-build under auto-merge if `STATUS.md` flips to BOARD LIVE as-is. All six are surgical doc edits — fixable in one orchestrator session.

A meta-finding the chair must internalize: **the ENGINES.md build-plan preamble already resolves several issues the per-engine contracts still contain** (audit-sink unification §40, resolver naming §36, unique-index-after-dedupe §82/conflict 8, E1-Phase-A-only Phase-0 cut). The danger is that a build session reading only the per-engine `engines/E*.md` contract section — not the preamble — implements the dead/dangerous text. The fix for those is to back-port the preamble's locked decisions into the per-engine files (cheap), not to re-decide them.

---

## P0 blockers (MUST fix before handoff)

**P0-1 — E1 "atomic dual-write" sentence is false on this DB; would corrupt live balances.**
- Issue: E1.md:160 and E1.md:175 instruct keeping the mirror `$inc` "inside the *same* `find_one_and_update` (`$inc` two fields) to stay atomic." `money_accounts` (SoR) and `vouchers`/`loyalty_accounts`/`customers` (mirrors) are different collections; a single `find_one_and_update` is one-document-one-collection. Verified: backend has **zero** `with_transaction`/`start_session`; `replicaSet` only set if `MONGO_REPLICA_SET` env present (it isn't) → standalone Mongo cannot do multi-doc transactions at all. A literal build = a two-write drift window that corrupts balances under crash/concurrency.
- File/section: `docs/roadmap/engines/E1.md` "Migration impact" §B (line 160) + Risk note (line 175).
- Fix: Delete the "`$inc` two fields in the same find_one_and_update" clause. Replace with: "single-collection SoR; the mirror is updated by a separate idempotent reconcile job (eventual-consistency), NOT a cross-collection atomic write — multi-doc transactions are unavailable (standalone Mongo, no replica set)." Add a one-line banner at the top of E1.md: **"Phase B/C are DO-NOT-BUILD until a replica set + a `with_transaction` helper exist. Only Phase A (facade over the existing 3 collections, no new SoR collection, no migration, no index) is auto-mergeable."** (ENGINES.md Phase-0 cut already ships Phase A only — this just makes the per-engine file consistent with it.)

**P0-2 — E3 hash-chain reuse collides with the single global chain head on the POS sell-path.**
- Issue: E3.md routes `item_events` through `audit_chain.append_audit_entry`, which advances ONE hardcoded global head (`HEAD_DOC_ID="primary"`). Every reserve/commit/sell/scan would `$inc` the one `primary` doc → serializes all high-volume stock movement against all audit writes through a single document on the revenue-critical sell path, AND interleaves item-event seq into the chain that `GET /audit/verify` reads from `audit_logs` only (so item_events never get chain-verified — defeating the stated immutability claim). Also conflicts with the now-locked "all engines call `AuditRepository.create`, never `append_audit_entry` directly" (ENGINES.md:40).
- File/section: `docs/roadmap/engines/E3.md` §Public API + §Data model ("hash-chained at the unit level" claim).
- Fix: Drop unit-level hash-chaining for `item_events`. Keep the monotonic `event_seq` (via `barcode.allocate_sequence`) + one standard `AuditRepository.create` row per material event. Remove the "immutable even for Superadmin at the unit level" claim (or, if chain integrity is truly required, add an explicit deliverable: extend `append_audit_entry`/`_advance_head` to take a `head_id` so `item_events` gets its own head + its own verify path — but that's net-new scope and should not be auto-merged blind).

**P0-3 — #30/#31 packets contradict the board's "via SC engine" → would spawn a parallel payroll money-feed (double-pay risk).**
- Issue: `EXECUTION_BOARD.md:109-110` label #30/#31 "via SC engine," and DECISIONS §4 makes SC the incentive engine of record. But F30/F31 packets are written entirely against the OLD model (`salary_config.commission_rate_percent`, `payroll_engine._earnings()` incentive merge, new `commission_ledger`/`spiff_log`) with **zero** references to SC. Under auto-merge the build session builds a second `incentive=` feed competing with SC's `get_incentive_for_payroll` — exactly the divergent-copy bug PROTOCOL §6 forbids. (Note: these are in **Phase 4**, not Phase 2 as one report stated — but the contradiction is phase-independent and must be fixed before those packets are written/claimed.)
- File/section: `docs/roadmap/_analysis/features/F30-*.md` + `F31-*.md` (Reuse + Backend sections); `DECISIONS.md §3` commission row.
- Fix: Add a packet-header banner to F30/F31: **"SUPERSEDED-MODEL WARNING: ignore the `commission_rate_percent`/`payroll_engine.incentive` plumbing below. Incentive of record is SC (`get_incentive_for_payroll`). Build SPIFF as an SC Kicker; build clawback as a negative SC adjustment folded into the locked snapshot — NOT a parallel payroll feed."** Tighten DECISIONS §3 to: "#31 clawback is an SC adjustment; no standalone commission engine."

**P0-4 — SC payroll-incentive feed collides with the existing `_fetch_incentive`/`incentives` path → money double-count.**
- Issue: payroll-run today calls `_fetch_incentive(db, emp, month, year)` (payroll.py:1332) reading a separate `incentives` collection. SC.md adds `get_incentive_for_payroll` reading the locked snapshot and claims "no payroll-engine change required," never mentioning the existing path. If both live, payroll double-counts.
- File/section: `docs/roadmap/engines/SC.md` "How dependents call it" / migration §.
- Fix: State explicitly that payroll-run's `_fetch_incentive`/`incentives`-collection path is **replaced by (or made strictly subordinate to)** the snapshot feed, with one source-of-truth precedence rule, plus an acceptance test that the two paths never sum.

**P0-5 — SC acceptance test #3 multiplier value is mathematically wrong against the seeded engine (1.4 vs 1.1 @ 14%).**
- Issue: SC.md:12 and SC.md:160 both claim "14% discount → multiplier 1.4." Verified seeded table (`incentive_settings_repository.py:63-68`): `0.11→1.4 … 0.14→1.1`; `compute_multiplier` floor-walks ascending, so floor(0.14)=0.14 returns **1.1**. The existing test `test_payout.py:263` confirms 1.4 is the 0.11 tier. A build agent writing test #3 verbatim either ships a failing test or "fixes" the engine and corrupts payout math.
- File/section: `docs/roadmap/engines/SC.md` line 12 (reuse table) + line 160 (test #3).
- Fix: Make the example self-consistent: change input to **11%** (→1.4) OR change expected to **1.1** (@14%). Verify against the actual Excel worked example before locking. (For the record, test #4 `₹24,310 × 0.22 × 1.0 = ₹5,348.20` and the tier bands are correct.)

**P0-6 — #21's Phase-0 "E3-shim" dependency is undefined and forward-references a Wave-3 engine.**
- Issue: `EXECUTION_BOARD.md:41` puts #21 in Phase 0 with dep "E3-shim," but E3 is Phase-1/Wave-3 and "E3-shim" is defined nowhere in ENGINES.md or any contract. A Phase-0 build agent cannot build quarantine state transitions without either E3 or a shim spec → bounces forever or ships a hollow shell (violating the INTENT-FIDELITY gate).
- File/section: `docs/roadmap/ENGINES.md` (define the shim) + `EXECUTION_BOARD.md:41`.
- Fix (recommended): Add a 1-paragraph "E3-shim" contract to ENGINES.md — a minimal `status` transition `AVAILABLE↔QUARANTINED` (free string, no enum change), one `AuditRepository.create` audit row per transition, a transfers-reject guard, and the requirement that QUARANTINED be excluded from `product_repository.find_available` rollups. No ledger chain. Then #21 stays a Phase-0 quick win. (Alternative: move #21 to Phase 1 after E3.)

---

## P1 (fix during Phase 0)

- **P1-1 — E1 unique-index can silently not exist in prod (removes the double-spend guard).** `connection.py::_idx` swallows build failures and logs only; prod has live dup/null blockers. Add a startup assertion/health check that the `money_accounts` `{account_type,account_key}` unique index exists, and **fail-closed for debits if it doesn't**. (Applies whenever Phase B is eventually built — gate it.) Same partial-unique caveat for E3's `serial` idempotency index and E1's `ledger.idempotency_key`. → edit E1.md/E3.md risk notes.
- **P1-2 — PM products enum lacks `HEARING_AID` (DB-enforced `$jsonSchema` enum).** Acceptance test #1 (create HEARING_AID, expect 422 on missing serial) dies on the spine insert with a Mongo validation error first. Promote PM open-conflict #3 from "note for chair" to a **blocking prerequisite**: additively add `HEARING_AID` (+ reconcile the two divergent enums — `products` lacks HEARING_AID; catalog `ProductCategory` lacks SERVICES/COLORED_CONTACT_LENS) before any add-flow test is written. → `docs/roadmap/engines/PM.md` data-model + the additive-schema step on `backend/database/schemas.py:99-101`.
- **P1-3 — PM SKU rule is a rewrite, not a reuse of `generate_sku`.** Actual `generate_sku` (catalog.py:1135) truncates brand/model/colour and always appends a counter; PM's `PREFIX+BRAND+MODEL+COLORCODE+SIZE` is a different algorithm with collision-only counter. State in PM.md that `build_sku` is a **rewrite**, that the new format differs from existing `SG-BR-MODELCOL-1001` SKUs, and that legacy-import SKU acceptance must be **format-permissive** (allow `/`, `-`, alnum, no length cap) so existing Shopify-style SKUs still list/get/sell.
- **P1-4 — E2 secret encryption won't fire for policy values (field-name vs flag mismatch).** `settings._encrypt_config` encrypts only when the dict KEY ∈ `_SENSITIVE_FIELDS`; E2 stores values under dotted keys (e.g. `tally.ledger_map`) that won't match. Drive encryption off the registry `secret` flag per-key via `_encrypt_value`/`_decrypt_value` directly. Add a test that a `secret` key is ciphertext in Mongo. → E2.md data-model.
- **P1-5 — E2 cache invalidation is a no-op without Redis → stale policies up to 15 min.** `delete_pattern`/`invalidate_store` are Redis-only no-ops in-memory (cache.py:148-167). With no `REDIS_URL` (documented fallback), a `set_policy` write won't evict scoped reads → breaks the "no redeploy to tune a knob" promise + acceptance test #1. Invalidate via explicit `cache.delete(key)` for the exact `(key, scope_id)` keys written (works in both backends), or short-TTL policies. → E2.md integration §.
- **P1-6 — E2/PM luxury-cap override must be a locked invariant, not an open question.** E2's `pricing_caps` scope hook could let a store *raise* a luxury brand cap (Cartier 2% etc.) — a non-negotiable rule (CLAUDE.md). Hard-lock in E2.md: **E2 overrides may only LOWER, never raise; luxury brand caps are not E2 keys at all.** Make it a stated invariant + acceptance test before any build session.
- **P1-7 — E4 `approve()` atomicity under-specified → concurrent approve can mint two tokens.** Current `ProposalStore.approve` is non-atomic read-modify-write. Specify `approve()` as a single `find_one_and_update({request_id, status:"REQUESTED", consumed:false})` minting the token in the same op (mirror `redeem_voucher_atomic`); add acceptance test "two concurrent approves → exactly one APPROVED, one token." → E4.md Public API + tests.
- **P1-8 — E4 PIN brute-force throttle is a named top-risk with no acceptance test.** Either add the `pin_attempts` schema + a test ("N wrong PINs in window → 423/429 locked; correct PIN after window succeeds"), or explicitly defer throttle and remove it from the risk-mitigation claims so it isn't silently dropped on an auto-merge pipeline. → E4.md acceptance tests.
- **P1-9 — E3 ignores the existing `serial_numbers` collection + CRUD → divergent second serial store.** A full `serial_numbers` collection with its own status enum + warranty tracking already exists (inventory.py:3245+). E3 must add a reconciliation decision (fold into `stock_units.serial` and deprecate, OR bind E3 serial to the existing collection) and add the product-level `serial_required` flag as an explicit new field. → E3.md Reuse + Data model.
- **P1-10 — E3 quarantine/blind-count/under-audit are net-NEW, not "rewire."** No existing substrate (grep: zero `quarantine`/`blind`/`under_audit`). Reclassify as new features and **enumerate every on-hand rollup** that must exclude QUARANTINE/UNDER_AUDIT/DC_IN (`_on_hand_by_product:107` + the rollups at 2147/2274/2366/2731) or those units still read as sellable. Acceptance #5/#6 must enumerate the rollup endpoints, not assert one. → E3.md build-effort + tests.
- **P1-11 — E6 `fu_due_today` assumes a `mode`/channel field absent from `follow_ups`.** `follow_ups.type` is a purpose enum, not a delivery mode; acceptance test #6 references a non-existent field; E6 also never mentions the pre-existing `GET /due-today` route (follow_ups.py:326). Either add an explicit `channel` field (with default + mapping) or reframe `fu_due_today` to map `type`→channel + always create a staff task, and reconcile with `/due-today`. → E6.md "How dependents call it" + acceptance #6.
- **P1-12 — E6 3-msg/30-day cap is racy across workers; lock the semantics it can guarantee.** Honestly disclosed by E6 (Risk 2). Commit to **soft-ceiling** (and have acceptance test #1 assert soft-ceiling, not strict exactness) OR commit to a guarded counter. Don't ship a test that asserts strict exactness against a check-then-write. → E6.md + acceptance #1.
- **P1-13 — Cost+10%-per-category price floor (DECISIONS §9) is owned by NO board feature and is mis-described as "already enforced" in two packets.** `orders.py:1335` is cost+0%, not cost+10%-per-category. F32 and F41 falsely claim the existing guard satisfies §9. (a) Add an explicit board item: "Enforce cost+10% per-category floor on the POS sell-path (`orders.py:1335`), reading `pricing.cost_floor_pct` via E2." (b) Correct F32 and F41 to say the existing guard blocks below-**cost** only; the §9 floor is not yet enforced. → EXECUTION_BOARD.md, F32, F41.
- **P1-14 — Engine-wave vs board-phase mismatch is systemic; #6 and #46 are mis-phased.** #6 (board Phase-1, dep E3+PM) needs Wave-2/Wave-3 engines → move to Phase 2/3 or annotate "unblocks only after E3+PM (Wave 3)." #46 (board Phase-0, dep E6) needs the E6 **rule engine**, but the Phase-0 cut ships only E6's OTP/cap slice → move #46 to Phase 1 or pull the reminder-rule slice into the Phase-0 cut. → EXECUTION_BOARD.md:45,60 + add a phase↔wave mapping column.
- **P1-15 — Back-port the ENGINES.md locked names into the per-engine contracts.** E3.md/E5.md still cite `append_audit_entry` as the sink (locked → `AuditRepository.create`); SC.md still defines `resolve_settings` (locked → `get_policy(key, scope)`). Add a "SUPERSEDED BY ENGINES.md Cross-engine contracts" banner at the top of E3/E5/SC, or edit the signatures in place. Cheap; prevents re-introducing the exact divergence PROTOCOL §6 exists to stop.

---

## P2 (nits/backlog)

- E1 family-wallet verbs (`request_pool_redeem`/`confirm_pool_redeem`) shown `def` but their only delivery dep (`providers.send_whatsapp`) is `async` — mark them `async` or document the loop bridge. (Largely moot once E1 delegates OTP to E6 per ENGINES.md contract §37.)
- E1 idempotency generalization rests on `loyalty.reverse_for_return`'s current read-then-write scan; ensure the new partial-unique index is built+asserted (same swallow caveat as P1-1).
- E2 entity inference must fail-SAFE when a store lacks `entity_id` (prod data dirty): missing entity → silently drop to global, never raise. Add explicit statement + test.
- E4 approver notification: `send_notification` is customer-centric (requires `customer_id`/consent). Make the in-app bell (`task_notify.py`) the contracted approver channel; mark WhatsApp-to-approver optional via a staff-appropriate path.
- E3 function-name drift: `claim_returnable_qty` doesn't exist by that name (the CAS block is returns.py:415). Correct the cited name in E3/E4 Reuse.
- E3 "preserves existing CAS" mischaracterizes the transfer ship path — `stock_shipped`/`received_qty_committed` are boolean/counter flags, not CAS; the per-unit ship write has no `status:AVAILABLE` guard today. Reword to "doc-level flags preserved AND per-unit CAS newly added"; acceptance #7 asserts the per-unit guard.
- E3 scan-advance maps a workshop `job`, not a `stock_id` — tie that dependent to the serialized-vs-aggregate duality open-conflict, don't list it as a clean `record_event(stock_id=...)` caller.
- PM triple-write (`products`+`catalog_products`+`catalog_variants`) is all-new non-atomic wiring; specify write-order + compensation (spine first; if PIM/variant write fails, mark/compensate the spine) so POS never sells an orphan spine row. Add a partial-failure acceptance test.
- PM `catalog_products` has no validator and the new unique `{id:1}` index will fail on dup/null `id` in live data — add a dup/null pre-check before index creation (mirror the documented prod-blocker pattern).
- SC `resolve_settings` must be opt-in (absent global/entity → byte-identical to today's store-or-defaults); add a "no-op when only store exists" regression test.
- SC conversion auto-calc scores **0** (not null) on missing footfall (`points.py:251`) — conflicts with "Fail Loudly" and affects payout rupees. Pre-resolve to null/blocked-with-warning (or per-store toggle) before build.
- SC product-incentive kicker POS attach must be post-commit + fail-soft (never raises into the order path); specify so a kicker dup-key never blocks order finalization.
- E5 `STORE_CREDIT`-as-tender correctly deferred (not a `PaymentMethod` value; wiring it now touches POS capture). No change needed — confirmed clean.
- E6 OTP transactional bypass is a NEW `is_transactional`/`category=OTP` path (not the existing `_TRANSACTIONAL_TEMPLATES` allowlist) — fine, but ensure quiet-hours + freq-cap + consent all short-circuit on transactional FIRST; acceptance #2 asserts via the new flag.
- DECISIONS §4 POS-skip should state explicitly it does NOT block #16/#22/#23 (those read existing `order.payments[]`); mirror one line on the board Phase-2 header ("POS capture UNCHANGED; recon reads existing payments").
- DECISIONS §3 SPIFF-approval ("auto-approve") contradicts F30's still-open "manager approval / configurable" owner-question — strike or mark LOCKED in F30.
- §9 "no further staff discount on liquidation items" has no enforcement owner — flag for #10's packet.
- E6 minor: `send_notification` has no `rule_id`/`campaign_id` param today — stamping needs a new kwarg or post-insert (additive, trivial). `rbac_policy.POLICY` line ref is cosmetic-wrong (entry-shape at 42, list at 118).
- MEGAPHONE `_scan_rx_expiring` docstring says "7/30/90 days" but only one 90-day cutoff fires — pre-existing minor bug, note it for #46 so the builder doesn't preserve it.

---

## Corrections to apply to DECISIONS.md / ENGINES.md / EXECUTION_BOARD.md (precise edits)

**DECISIONS.md**
- §3 (line 51, "Commission basis (#31)"): replace the half-alive commission language with: "#31 clawback is an SC adjustment folded into the locked snapshot; **no standalone commission engine.** SC is the sole payroll incentive feed."
- §3 (line 52, "SPIFF approval (#30)"): append "(F30's owner-question on manager approval is **closed** — auto-approve is locked here)."
- §4 (line 64, POS advance/on-delivery split): append: "This skip does NOT block #16/#22/#23 — those read existing `order.payments[]` and add only derived/reconciliation records (E5). Only the phase-split (ADVANCE/ON_DELIVERY) + per-EDC capture is deferred."
- §2 (line 34, Price floor): add a pointer: "Floor enforcement is currently cost+0% at `orders.py:1335`; upgrading to cost+10%-per-category is an explicit board item (see EXECUTION_BOARD Phase-2), reading `pricing.cost_floor_pct` via E2 — it is NOT yet enforced."

**ENGINES.md**
- Add an "E3-shim" 1-paragraph contract (minimal AVAILABLE↔QUARANTINED status string transition + audit row + transfers-reject guard + rollup-exclusion; no ledger chain) so #21 has a real Phase-0 dependency.
- Add a phase↔wave mapping table reconciling board Phases 0–5 with engine Waves 0a/0b/1/2/3, flagging #6 (after E3+PM, Wave 3) and #46 (needs E6 rule engine, not the OTP slice).
- Cross-engine contracts §40 already locks the audit sink — add an explicit "back-port required into E3.md/E5.md" note.

**EXECUTION_BOARD.md**
- Line 41 (#21): change dep "E3-shim" only after the shim is defined in ENGINES.md (P0-6); otherwise move #21 to Phase 1.
- Line 60 (#6): annotate "BACKLOG — unblocks only after E3+PM (Wave 3); do not promote to TODO in Phase 1."
- Line 45 (#46): move to Phase 1 (needs E6 full rule engine) OR note "needs E6 rule-slice, not OTP slice."
- Lines 109-110 (#30/#31 "via SC engine"): keep the label; it's correct — the fix is in the F30/F31 packets to match it.
- Phase-2 header (line 68): change "POS UNCHANGED" → "POS **capture** UNCHANGED; reconciliation reads existing `order.payments[]`."
- Add one board item under Phase 2 (or fold into PM/E2 consumer work): "Enforce cost+10% per-category price floor on POS sell-path (`orders.py:1335`) via `pricing.cost_floor_pct` (E2)."

**Per-engine files**: E1.md (delete dual-write atomic clause, add Phase-A-only banner), E3.md (drop unit-level hash-chain claim, reconcile `serial_numbers`, reclassify quarantine/under-audit as net-new, fix `claim_returnable_qty` name, reword "preserves CAS"), E5.md/SC.md (back-port `AuditRepository.create` / `get_policy`), SC.md (fix test #3 multiplier, reconcile `_fetch_incentive` collision), PM.md (SKU=rewrite, HEARING_AID enum, triple-write ordering), E2.md (secret per-key encryption, explicit cache.delete invalidation, luxury-cap lower-only invariant), E4.md (atomic approve + throttle test), E6.md (`fu_due_today` channel + `/due-today` reconcile, soft-cap semantics).

---

## Phase-0 quick-win re-validation

| Feature | Genuine quick win? | Adjusted scope / verdict |
|---|---|---|
| **#35 Cost & margin masking** | Yes (with correction) | Real ~2-day quick win. Drop the false `_build_store_ledger` margin claim (it never returns `cost_price`/`margin_pct`); audit each named call-site for the field actually existing before wrapping a `None`. Intent test: SALES_CASHIER gets `cost_at_sale`/`cost_price` as `null` (present-but-null), ACCOUNTANT gets real numbers. **SAFE after the call-site correction.** |
| **#21 Defective quarantine** | Yes (with shim) | Real ~2-day quick win but IS new code (new endpoint, new label template, transfers guard, queue tab). Fix two false overlap claims: status is a **free string, no enum** (connection.py:238 is index defs); POS safety comes from `product_repository.find_available` filtering AVAILABLE (not a "catalog filters AVAILABLE" guard). Depends on the **E3-shim being defined first (P0-6).** Intent test: quarantine the only AVAILABLE unit → POS sell returns 409. |
| **#7 AI predictive purchasing** | **NO — re-tag `quickwin=no`, risk=MED.** | Packet over-claims the kernel. ORACLE `_propose_reorders` reads `stock_units` by reorder_point, has **zero burn-rate logic** and does NOT read `orders.items`; `from-forecast` computes velocity internally (doesn't "accept" it); the `draft_po` executor emits a **stub** PO, not a real vendor PO. True delta is ~full M/5-day build (new burn-rate aggregation + new ORACLE step + reconcile two divergent PO-draft paths). **Adjusted: move out of the easy Phase-0 batch or scope it explicitly to the burn-rate endpoint + dashboard only.** Intent test: SKU with on-hand=10 above reorder_point but selling 5/day → ORACLE proposes reorder with `days_of_stock_remaining≈2`. |
| **#34 Global target ticker** | Yes | Claims hold (budgets REVENUE head + `finance.get_revenue` + notifications + ORACLE tick all exist; no pre-existing ticker endpoint). One correction: no `orders` compound index exists — add `{created_at,status,store_id}` + server-side cache (it's net-new, not "check if present"). Scope: config + assembly + UI. Intent test: SALES_STAFF gets only `pct_complete` (no rupees); STORE_MANAGER gets `mtd_revenue`. **SAFE.** |
| **#40 VIP churn prediction** | Correctly tagged `quickwin=no` | Honest packet; claims verified (churn-risk/RFM/lifecycle/ORACLE/proposals all real). Delta = personalised median inter-purchase-interval baseline + new `vip_churn_snapshots`. **No correction needed.** Intent test: VIP who buys ~30d, now 80d out = HIGH; VIP who buys ~180d, now 80d out = NONE (same recency, opposite labels). |
| **#46 Configurable reminders** | Yes (but re-phase) | Claims hold (`_scan_rx_expiring` hardcoded 90d; `_resolve_rx_expiry(window_days=...)` parameterized; DISPATCH_MODE/templates/quiet_hours exist; no `reminder_rules` yet). BUT its true dependency is the **E6 full rule engine**, which the Phase-0 cut defers (only OTP/cap slice ships). **Adjusted: move #46 to Phase 1 OR pull the reminder-rule slice into the Phase-0 E6 cut.** Intent test: RX_EXPIRY rule `days_before=30` (90d default off) → exactly one PENDING `notification_log` stamped `rule_id`, second tick within cooldown → none. |

**Net Phase-0 batch adjustment:** of the 5 chair-tagged `quickwin=yes`, **#35, #34 are clean**; **#21 is clean only after the E3-shim is defined**; **#7 must be re-tagged `quickwin=no` (false-kernel)**; **#46 must be re-phased to after E6-full or have the rule-slice pulled forward.** #40 already correctly `quickwin=no`. The build engines E1/E2/E6 in the Phase-0 cut are sound at the **preamble** level — but only after the P0-1 dual-write sentence is removed and the E2 P1s (secret encryption, cache invalidation, luxury-cap lock) are fixed.

---

## HANDOFF DECISION: **NO-GO** (flip to GO when the 3 conditions below are true)

The board is correctly `NOT LIVE` and the foundation is real — but handing the artifacts to autonomous auto-merge build+test sessions as-is would corrupt money (P0-1, P0-4), serialize the POS sell-path (P0-2), spawn a parallel payroll feed (P0-3), corrupt payout math via a wrong acceptance test (P0-5), and stall a Phase-0 item on an undefined dependency (P0-6). These are all cheap doc edits — one orchestrator session, not a re-architecture.

**GO conditions (all 3 must be true before `STATUS.md` → BOARD LIVE):**

1. **All six P0s are edited in the docs** — specifically: E1.md dual-write clause deleted + Phase-A-only banner added; E3.md unit-level hash-chain dropped; F30/F31 SUPERSEDED-MODEL banner + DECISIONS §3 tightened; SC.md `_fetch_incentive` collision resolved AND test #3 multiplier corrected (1.1@14% or input→11%); E3-shim contract defined in ENGINES.md (or #21 moved to Phase 1).

2. **The per-engine contracts are reconciled with the ENGINES.md preamble** — back-port the locked `AuditRepository.create` (E3/E5) and `get_policy` (SC) names; lock the E2 luxury-cap "lower-only" invariant and add the PM `HEARING_AID` enum prerequisite; so a build agent reading only `engines/E*.md` cannot implement a dead/dangerous contract. (This closes P1-2, P1-6, P1-15, the most likely to mis-build silently.)

3. **The Phase-0 batch is re-validated and the false-kernel/mis-phase items corrected** — #7 re-tagged `quickwin=no`; #46 re-phased (or rule-slice pulled forward); #21 gated on the shim; #35/#34 call-site/index corrections applied. (The remaining P1s — E4 atomic approve + throttle test, E6 `fu_due_today`/soft-cap, E2 secret/cache, the cost+10% floor owner — can be fixed *during* Phase 0 as their packets are written, since none are in the same-day Phase-0 engine cut, but they MUST be in the packet before that item moves to TODO.)

Once those three are done, GO — the engine reuse is genuinely well-grounded, the INTENT-FIDELITY gate is sound, and the remaining items are safely fixable inside the build loop's packet-before-TODO discipline.


# ===== RAW HARDENING FINDINGS =====



## Area: E1E2
`replicaSet` is only set if `MONGO_REPLICA_SET` env var is present (optional). Railway's managed Mongo is typically a **single-node standalone** unless explicitly configured as a replica set — and standalone Mongo does NOT support multi-document transactions at all. This means the only atomic primitive available is single-document `find_one_and_update`. This reinforces the P0/P1 on cross-collection dual-write.

I have everything needed. Writing the verdict.

---

# E1/E2 Engine Contract Hardening Review (Area: E1E2)

## Reuse-claim verification (all confirmed against disk)

Every named E1/E2 reuse target exists with the claimed signature:
- `vouchers.py::redeem_voucher_atomic(db, code, amount, order_id, redeemed_by)` — lines 170-261. Single `find_one_and_update`, guard-in-filter, returns `{ok,balance,status,reason}`, never raises. **YES.**
- `loyalty_repository.py::try_debit(...)` (101-163) + `adjust_balance(...)` (62-99). **YES** (line cites off by ≤3).
- `customer_repository.py::try_debit_store_credit(customer_id, amount)` (110-152) + `add_store_credit` (95-103). Uses conditional `update_one` (not `find_one_and_update`) — doc correctly notes this. **YES.**
- `store_credit_ledger.py::make_entry / compute_balance` — pure, paisa-rounded, signed-entry. **YES.**
- `returns.py::_claim_returnable_qty` (371-438) / `_release_returnable_qty` (441-467) — atomic positional `$inc`; E1 correctly flags this is **inventory qty, not money**. **YES.**
- `audit_repository.py::AuditRepository.create` (35), `providers.py::send_whatsapp` (125), `integration_config.py::_load_db_config` (29), `settings.py` crypto/`_SENSITIVE_FIELDS`/`_get_settings_collection` (86-236), `pricing_caps.py::effective_discount_cap`, `cache.py` (TTL_LONG/invalidate_store), `stores.py` `entity_id` REQUIRED (53). **ALL YES.**

The contracts are well-grounded. The risks are in the **migration mechanics** and a few integration mismatches a build agent would implement literally and wrongly.

## Findings

**[P0] E1 cross-collection dual-write is NOT atomic as the doc claims — and Mongo here can't make it so.**
Claim: Phase B keeps "the mirror `$inc` inside the *same* `find_one_and_update` (`$inc` two fields) to stay atomic." Verified=NO. The system-of-record (`money_accounts`) and each mirror (`vouchers`/`loyalty_accounts`/`customers`) live in **different collections**; a single `find_one_and_update` touches one document in one collection only. Cross-collection atomicity requires a multi-document transaction — and the backend uses **zero transactions** (`grep with_transaction` → none) and Mongo on Railway is standalone unless `MONGO_REPLICA_SET` is set (standalone Mongo cannot do transactions at all). An auto-merge build session implementing the headline claim literally will ship a **two-write window that drifts the SoR vs mirror under crash/concurrency — i.e. corrupt live balances**, exactly the outcome this review guards against. Fix (E1.md "Migration impact" §B): delete the "`$inc` two fields in the same find_one_and_update" sentence; mandate **single-collection SoR with a periodic reconcile job** for the mirror, OR gate Phase B behind a confirmed replica-set + a `with_transaction` helper that is added first. Until then, **Phase B/C must be marked DO-NOT-BUILD** and only Phase A (facade over existing three collections, no new SoR) is auto-mergeable.

**[P1] E1 unique-index — the double-spend guard — can silently fail to exist in prod.**
`connection.py::ensure_indexes` is now per-index hardened (the old "one big try" is fixed — good), but `_idx` **swallows build failures and only logs them** (lines 181-188). E1's whole concurrency guarantee for the *new* `money_accounts` types rests on `{account_type, account_key}` unique. Prod has live dup/null blockers (memory: 5038 customers, phone `9130255390`×3, genuine dup `customer_id`s) and `account_key=customer_id`. If the unique build fails on those dups it is swallowed → the collection runs **without the uniqueness guard**, and a second account row for the same key = a parallel balance / double-spend surface. Fix: E1 must (a) run the dedupe pre-clean **before** the index build (already noted), and (b) add a **startup assertion / health check that the `money_accounts` unique index actually exists** and fails-closed for debits if it doesn't — do not rely on `_idx`'s swallow-and-log.

**[P1] E2 secret encryption won't trigger for policy values (field-name vs flag mismatch).**
E2 reuses `settings.py::_encrypt_config` for any `PolicySpec` flagged `secret: True`. Verified=PARTIAL/NO. `_encrypt_config` encrypts only when the **dict KEY name** is in `_SENSITIVE_FIELDS` (`api_key`, `password`, …). E2 stores values as `values[<dotted.key>]` (e.g. `tally.ledger_map`) — the key won't match `_SENSITIVE_FIELDS`, so a `secret` policy would be **written in plaintext** despite the flag. Fix (E2.md data-model §): do not call `_encrypt_config` on the values map; call `_encrypt_value`/`_decrypt_value` directly, driven by the registry's `secret` flag, per-key. Add an acceptance test that a `secret` key is ciphertext in Mongo (test #6 currently asserts the behavior but the reuse plumbing as written won't deliver it).

**[P1] E2 cache invalidation is a no-op on the in-memory (no-Redis) path → stale policies.**
E2 says resolved policies are cached (TTL_LONG=900s) and invalidated on write "exactly like feature_toggles" via `cache.invalidate_store`/`delete_pattern`. Verified=PARTIAL. `delete_pattern` (and thus `invalidate_store`) is **Redis-only — explicit no-op in memory** (cache.py 148-167). With no `REDIS_URL` (the documented fallback mode), a `set_policy` write won't evict scoped reads, so a STORE_MANAGER changing `cash.variance.block` keeps serving the old value for up to 15 min — breaking acceptance test #1's "clear override → resolves parent" and the "no redeploy to tune a knob" promise. Fix: invalidate via explicit `cache.delete(key)` for the exact `(key, scope_id)` cache keys you wrote (works in both backends), not pattern-delete; or set short TTL for policies. Note this in E2.md integration §.

**[P1] E2 `pricing_caps` scope hook risks under-capping luxury brands (non-negotiable rule).**
Verified the live risk: `effective_discount_cap` is a pure no-scope function and `LUXURY_BRAND_CAPS` (Cartier 2% etc.) are code constants that CLAUDE.md marks non-negotiable. E2's plan to add a `scope` arg + `get_policy("promo.ceiling_pct")` override path could, if a build agent wires brand caps through E2, let a store **raise** a luxury cap. E2's own open-conflict #3 flags this but leaves it "chair to confirm" — for an auto-merge pipeline that is not safe enough. Fix: hard-lock in E2.md — **`pricing_caps` E2 overrides may only LOWER, never raise; luxury brand caps are not E2 keys at all.** Make it a stated invariant + acceptance test, not an open question, before this reaches a build session.

**[P2] E1 `request_pool_redeem` is sync but its only delivery dependency is async.**
`providers.send_whatsapp(...)` is `async def` (line 125). E1's family-wallet verbs are shown as plain `def`. A sync service calling an async coroutine needs an event-loop bridge (or the verb must be `async`). Minor, but a literal implementation will `await` outside a loop. Fix: mark `request_pool_redeem`/`confirm_pool_redeem` `async` in the E1 signature block, or document the bridge.

**[P2] E1 idempotency generalisation rests on a currently non-atomic precedent.**
`loyalty.reverse_for_return` (955) implements idempotency by **read-scanning up to 1000 ledger rows** for an ADJUST tagged with `return_id`, then writing — a read-then-write race (two concurrent returns can both pass the scan). E1 correctly proposes replacing it with a `{account_type, "ledger.idempotency_key"}` **partial-unique** index (genuinely better). Just ensure that index is built+asserted (same swallow caveat as the P1 above) — otherwise the "idempotent retries" acceptance test #4 passes in CI but the guarantee evaporates in prod.

**[P2] E2 entity inference must fail-safe when a store lacks `entity_id`.**
`get_policy` resolves `store_id → entity_id` via a stores read. The update model allows `entity_id: Optional` (stores.py:90) and prod data is known-dirty. If `entity_id` is missing the entity tier must silently drop to global (not raise / not skip global). E2 implies this but doesn't state it. Fix: explicit fail-safe + test.

## Verdict

**FIX-FIRST.** The reuse foundation is real and accurately cited — Phase A of E1 and the additive core of E2 are sound and low-blast-radius. But three issues are unsafe to hand to an auto-merge build session as written: the **P0 cross-collection "atomic" dual-write** (false on this DB — would corrupt balances), the **P1 silently-missing unique index** (removes the double-spend guard in prod), and the **P1 luxury-cap override** (violates a non-negotiable rule). Gate E1 to **Phase A only**, convert E2's luxury-cap and secret/cache items from "open questions" to locked invariants, then it is safe to build.


## Area: E3E4
Confirmed: `task_notify.py` exists (E4 line 170 in-app bell pattern is real) and `self_enforced` / `store_scoped` / `validate_store_access` conventions exist in rbac_policy.py (E3 line 145-146 and E4 line 184 store-scope claims are grounded).

I have enough verification across both engines. Let me do one final targeted check on the E4 PIN brute-force throttle claim (risk note 4 / line 229 "Mongo `pin_attempts` sub-doc") — this is net-new with no acceptance test, and the acceptance tests (line 209-220) do NOT test throttle. Confirming this gap by checking the acceptance test list: tests 1-10 cover tier/PIN-required/single-use/TTL/maker-checker/store-scope/amount/audit/serial/fail-soft — **no brute-force throttle test**, despite it being called out as a top risk. That's a P1 gap (security control with no acceptance test on an auto-merge pipeline).

I now have a complete, verified picture. Here are my findings.

---

## E3/E4 Adversarial Verification — Findings

Verified every cited file/line on disk. The contracts are unusually well-grounded — the large majority of reuse claims (line numbers, function names, CAS patterns, RBAC primitives) are **accurate**. The defects below are real and concentrated in two areas: E3's ledger-chain design and a few unflagged collisions with existing code.

### [P0] E3 hash-chain reuse collides with the single global chain head — build-blocking on the POS path
**Claim** (E3 L17, L54, L91, L135): `item_events` is a "domain-specific audit chain" that appends via `audit_chain.append_audit_entry` to get `seq/prev_hash/entry_hash`, "so 'immutable even for Superadmin' holds at the unit level."
**Verified:** NO. `append_audit_entry` advances a **single hardcoded global head** (`audit_chain.py` L49-50 `HEAD_COLLECTION="audit_chain_head"`, `HEAD_DOC_ID="primary"`; `_advance_head` L180-181 `{"_id": HEAD_DOC_ID}`, no chain-id param). Routing `item_events` through it would (a) `$inc` the one `primary` doc for **every** reserve/commit/release/sell/scan — serializing all high-volume stock movement against all `audit_logs` writes through one document (contention on the revenue-critical POS sell path E3 itself flags as riskiest), and (b) interleave item-event seq numbers into the same chain as `audit_logs`, which `GET /api/v1/audit/verify` reads from `audit_logs` only — so item_events rows would never be chain-verified by the existing endpoint, defeating the stated immutability guarantee.
**Fix (E3 §Public API / §Data model):** Either (a) extend `audit_chain.append_audit_entry`/`_advance_head` to accept a `head_id` param so `item_events` gets its OWN chain head (`item_events_head`) + its own verify path — and add that as an explicit deliverable + build-day estimate; or (b) drop hash-chaining for `item_events`, keep only the monotonic `event_seq` (barcode.allocate_sequence) + the standard `audit_logs` row per material event, and remove the "hash-chained at the unit level" claim. State the choice explicitly before any caller is rewired.

### [P1] E3 ignores the existing `serial_numbers` collection + CRUD — will create a second, divergent serial store
**Claim** (E3 L72, L103, L125-126): serial is a new field on `stock_units` + `item_events`, bound via a new `POST /items/{stock_id}/serial-bind`; "serial OPT-IN per SKU."
**Verified:** PARTIAL. There is already a full `serial_numbers` collection with CRUD (`inventory.py` L3245+: `SerialCreate`/`SerialUpdate`, `/inventory/serials`, its own status enum `IN_STOCK/SOLD/WARRANTY_CLAIM/DAMAGED/LOST_STOLEN` L3278, warranty tracking). E3 never mentions it, so a build agent will stand up a parallel serial model on `stock_units` that diverges from `serial_numbers` (two sources of truth for "is this unit serialized / sold / under warranty"). Also: "serial OPT-IN per SKU" requires a `serial_required` flag on the **product**; E3 projects it (`stock_units.serial_required` "projected from product") but the product-level flag does not exist yet and isn't listed as a schema add.
**Fix (E3 §Reuse + §Data model):** Add a reconciliation decision — either migrate/fold `serial_numbers` into the `stock_units.serial` + `item_events` model (and deprecate the old CRUD), or explicitly scope E3 serial to bind against the existing `serial_numbers` collection. Add the product-level `serial_required` flag as an explicit new field on the products schema with where it's set.

### [P1] E3 quarantine / blind-count / under-audit are net-NEW, not "rewire the side-channel"
**Claim** (E3 framing L9, dependents L124, tests #5/#6): QUARANTINE in/out and blind-count UNDER_AUDIT flag are presented as making "the side-channel the source of truth," implying existing behavior to rewire.
**Verified:** NO existing substrate. Grep of `inventory.py` shows zero occurrences of `quarantine`, `blind`, `under_audit`/`UNDER_AUDIT`. These are entirely new states/flows (only `start/complete/reconcile_stock_count` exist — those names confirmed at L1291/L1450/L1609, and the void-oldest-AVAILABLE path confirmed at L1708 / raw `update_many` at L1724). The risk: the doc's "behavior-preserving rewire" framing under-scopes net-new feature work, and the new QUARANTINE/UNDER_AUDIT states must be added to **every on-hand rollup** (`_on_hand_by_product` L107 + the L129 `stock_units` aggregate + L2147/L2274/L2366/L2731 rollups) or quarantined/under-audit units will still read as sellable.
**Fix (E3 §Build effort + tests):** Reclassify quarantine/blind-count/under-audit as new features (not rewires); enumerate every on-hand rollup that must exclude QUARANTINE/UNDER_AUDIT/DC_IN (acceptance test #5/#6 assert "drops store on-hand immediately" — make the test enumerate the rollup endpoints, not just one).

### [P1] E4 `approve()` atomicity under-specified — concurrent approve can mint two tokens
**Claim** (E4 L14): the proposal read→check-PENDING→set guard is "hardened into an atomic transition." Acceptance test #3 covers concurrent **consume**.
**Verified:** PARTIAL. The current `ProposalStore.approve/reject` IS a non-atomic read-modify-write (`proposals.py` L279 read, L283 check, L304 `_set` via plain `update_one` L459) — E4 correctly identifies this. But E4's `approve()` Public API (L51-58) returns `{status, approval_token}` with no specified CAS filter, and there is **no acceptance test for concurrent approve**. Two simultaneous approves on one REQUESTED row could both pass the check and mint two `approval_token`s (one request, two opaque single-use handles) — the consume guard then lets each token be consumed once = double-consume of one request.
**Fix (E4 §Public API + tests):** Specify `approve()` as a single `find_one_and_update({request_id, status:"REQUESTED", consumed:false})` (mirroring `redeem_voucher_atomic`, vouchers.py L217-233, confirmed) that mints the token in the same op; add acceptance test "two concurrent approves → exactly one APPROVED, one token."

### [P1] E4 PIN brute-force throttle is a named top-risk with NO acceptance test (auto-merge pipeline)
**Claim** (E4 risk #4, L207/L229): mitigate brute-force with "per-user attempt throttle (lock after N bad PINs in 15 min)" via a Mongo `pin_attempts` sub-doc.
**Verified:** NO test. Acceptance tests #1-10 (L211-220) cover tier/PIN-required/single-use/TTL/maker-checker/store-scope/amount/audit/serial/fail-soft — none exercise the throttle. On an auto-merge pipeline whose acceptance tests gate the merge, a security control with no test will ship unverified or be silently dropped.
**Fix (E4 §Acceptance tests):** Either add a test ("N wrong PINs within window → 423/429 locked; correct PIN after window succeeds") and the `pin_attempts` schema to §Data model, or explicitly defer throttle to a follow-up and remove it from the risk-mitigation claims.

### [P2] E4 `send_notification` is customer-centric; using it for staff approvers is a semantic mismatch
**Claim** (E4 L19, L169): on `request()`, call `notification_service.send_notification(...)` to "QUEUE a WhatsApp to each eligible approver's mobile."
**Verified:** PARTIAL. `send_notification` (confirmed `notification_service.py` L107) requires `customer_id`, `customer_phone`, `customer_name` and writes a `notification_logs` row with consent logic — it is built for **customers**, not internal staff. Approvers have no `customer_id`. E4's own line 170 says the in-app bell (`task_notify.py`, confirmed to exist) is "primary" — good — but the WhatsApp claim mis-cites the customer pipeline.
**Fix (E4 §Integration):** Make the in-app bell (`task_notify.py`) the contracted approver channel; mark WhatsApp-to-approver as optional and route it through a staff-appropriate path (or pass synthetic customer fields explicitly), not the consent-gated customer `send_notification`.

### [P2] Function-name drift: `claim_returnable_qty` does not exist by that name
**Claim** (E3 L19, E4 L16): reuse `returns.py` `claim_returnable_qty` (L~430).
**Verified:** PARTIAL. The `$elemMatch` + positional `$inc` CAS pattern IS at returns.py L415-428 (confirmed, with `_already_returned_qty` at L303) — but no function is named `claim_returnable_qty`. Line ref is right, name is wrong; a build agent grepping the name finds nothing.
**Fix:** Correct the cited function name (or reference it by the L415 CAS block) in both E3 and E4 §Reuse.

### [P2] E3 "preserves the existing CAS idempotency" mischaracterizes the transfer ship path
**Claim** (E3 L167): the rewrite "preserves the existing CAS idempotency flags (`stock_shipped`, `received_qty_committed`)."
**Verified:** PARTIAL. `stock_shipped`/`received_qty_committed` are confirmed (transfers.py L382/L427/L449/L492) but they are **boolean/counter flags**, not CAS. The per-unit ship move is a plain unconditional `stock_repo.update(sid, {status:TRANSFERRED})` (L357) with **no `status:AVAILABLE` guard in the write filter** — there is no per-unit CAS today. E3's `record_event_atomic` would *add* real per-unit CAS (an improvement), but a build agent told to "preserve the existing CAS" may keep only the boolean flag and skip per-unit CAS, missing the hardening.
**Fix (E3 §Build effort):** Reword to "the doc-level idempotency *flags* are preserved AND per-unit CAS is newly added"; acceptance test #7 (double-ship/partial-receive) should assert the per-unit guard, not just the boolean.

### [P2] E3 scan-advance maps a workshop `job`, not a `stock_id`
**Claim** (E3 L76, L123): `scan.advance` is an item-event with `stock_id=<unit or job-linked>`.
**Verified:** PARTIAL. `scan_advance` (labels.py L186, confirmed) operates on a workshop **job**, not a `stock_units` row. E3's ledger is `stock_id`-keyed; the "unit or job-linked" hand-wave is exactly the serialized-vs-aggregate duality E3 already flags to the chair (open conflict #1) — acceptable to defer, but the scan-advance dependent should not be listed as a clean `record_event(stock_id=...)` caller until that's resolved.
**Fix:** Tie the scan-advance dependent (E3 L123) explicitly to the duality open-conflict resolution.

### Accurate claims confirmed (no action)
auth `hash_password`/`verify_password`/`require_roles` (L225/232/301); `effective_discount_cap`+`ROLE_DISCOUNT_CAPS` (role_caps L53/29); `redeem_voucher_atomic` LOCKED money pattern + `ReturnDocument.AFTER` + `_redeem_failure_reason` (vouchers L170/232/264); ProposalStore `_coll`/`_audit_coll`/`_write_audit`/`_audit_repo`/`approve`/`reject` (proposals L124/135/494/463/261/411); `AuditRepository.create` (L35); proposals router shape (L39/51/71/78); `block_investor_writes` middleware (main L708); `task_notify.py`; rbac coverage-lock `test_no_uncatalogued_routes`; `barcode.allocate_sequence` (L85); transfers `STOCK_STATUS_TRANSFERRED`(L28)/`_apply_ship`(L300)/`_apply_receive`(L415)/`_audit_stock_move`(L272); labels `next_stage`(L91)/`STATION_TARGET_STAGE`(L120)/`SCAN_ROLES`(L151)/`scan_advance`(L186); lens_stock `_atomic_update`(L720)/`reserve`(L741); inventory `_INVENTORY_ROLES`(L31)/`_on_hand_by_product`(L107)/cycle-count void(L1708/L1724); `self_enforced`/`store_scoped` conventions.

---

**Verdict for E3/E4: FIX-FIRST.** E4 is close (one P1 atomicity gap + one P1 missing security test + minor mis-cites — all surgical). E3 has a P0 ledger-chain design collision (single global head vs high-volume per-unit chain on the POS path) plus an unflagged duplicate serial store and net-new quarantine/under-audit work mislabeled as a rewire. Resolve the E3 P0 and the two P1s, harden E4's `approve()` atomicity + add the throttle test, before handing either to an auto-merge build session.


## Area: E5E6
Final confirmations:
1. `_dispatch_scheduled_campaigns` (the campaign send path) checks only `marketing_consent` flag directly (line 300), NOT `is_opted_out` (ledger) and NOT any frequency cap — exactly as E6 claims. E6 routing campaigns through `passes_gates`+`record_outbound` is a genuine gap-closer.
2. `notification_logs` insert is a flat dict (line 143-167); adding `rule_id` is purely additive — verified feasible. (Note: `send_notification` itself has no `campaign_id`/`rule_id` param yet — E6 would need to add the param or stamp post-insert; minor, the contract acknowledges "stamps rule_id like campaigns stamp campaign_id".)

I have enough to render the verdict. Let me compile findings.

---

# E5 (TENDER-ROUTING) + E6 (REMINDER-RAIL) — Adversarial Verification

## E5 findings

**[P0] CORE PREMISE — POS capture untouched + Tally tender-leg gap is real. Verified: YES.**
Claim: E5 operates ONLY on existing `order.payments[]` + finance reconciliation, never redesigns POS capture (DECISIONS s4 skip respected). Confirmed against code: `PaymentMethod` enum (`orders.py:376`), the `method | mode` alias (`orders.py:2281`, mirrored in `finance.py:2281`), and the central justification — `nexus_providers.tally_build_day_voucher_xml` (line 508) emits `<PARTYLEDGERNAME>{party}</PARTYLEDGERNAME>` (588) + a single `Sales A/c` credit (595) with **no per-tender bank/cash leg**, so the money instrument is genuinely invisible to Tally. E5 only adds a derived leg builder + reads payments. No `PaymentCreate`/`POST /{order_id}/payments`/`posStore` change. Contract is sound and additive-only.

**[P2] Existing-file/line anchors all accurate.** Verified yes: `finance._split_output_tax` (330), `_store_state_map` (381), `_customer_state_map` (395), `_cash_sales_for_window` (2249, hardcodes `method=="CASH"` at 2282), `_cash_expenses_for_window` (2297), `close_cash_register` (2411) → `cash_register.build_close_summary` (154), `get_tally_sales_jv` (1984), `_jv_cgst_sgst_split` (1972); `cash_register.py` (build_close_summary/compute_variance/variance_status all present); `store_credit_ledger.make_entry`(32)/`compute_balance`(27); `audit_chain.append_audit_entry`(218); `order_repository.find_one_and_update`(490); `settings._get_settings_collection`(226) + `{"_id":"default"}` pattern; `rbac_policy.POLICY`(118). No fabricated anchors.

**[P2] STORE_CREDIT tender is honestly flagged, not assumed.** Verified: `STORE_CREDIT` is NOT in `PaymentMethod` (enum ends at LOYALTY, line 391). E5 correctly notes store-credit redemption has no tender row today and includes STORE_CREDIT in the map only for future wiring, explicitly warning that wiring it now "would touch capture." No hidden capture change. Good.

**[P2] No double-count risk left unaddressed.** E5 mandates cash-register's CASH path read the SAME `split_payments_by_mode["CASH"]` (Risk 3 + acceptance test #4 "byte-identical variance"). Consistent with the existing `_cash_sales_for_window` sign-split logic. Builder must honor this; flagged adequately.

E5 verdict: **clean.** Premise verified true; all anchors real; capture untouched; the one capture-adjacent item (STORE_CREDIT) is correctly deferred.

## E6 findings

**[P1] `fu_due_today` resolver assumes a `mode`/channel field that does NOT exist on `follow_ups`.** Claim partial. The `follow_ups` schema's `type` field is a `FollowUpType` **purpose** enum (`eye_test_reminder`, `frame_replacement`, `order_delivery`, `prescription_expiry`, `general` — `follow_ups.py:175`), NOT a delivery mode (CALL/WHATSAPP/SMS/EMAIL). E6's dependent-call spec and acceptance test #6 ("`follow_ups` row `mode=WHATSAPP` is queued; `mode=CALL` is NOT messaged") reference an Excel-I.7 "FU mode" field that is absent. A build agent would either invent the field or mis-map `type`→channel. Also E6 never mentions the pre-existing `GET /due-today` endpoint (`follow_ups.py:326`), risking a duplicate. **Fix (E6 §"How dependents call it" + acceptance #6):** either (a) add an optional `channel`/`mode` field to the follow_ups create schema and default it, and state the mapping explicitly, or (b) reframe `fu_due_today` to map the existing `type` enum → channel + always create a staff task (no implicit messaging), and reconcile with the existing `/due-today` route.

**[P1] The 3-msg/30-day cross-rule cap is net-new and currently UNENFORCEABLE — confirmed, and that's the whole point.** Claim verified. There is NO existing per-customer frequency cap anywhere: `marketing._check_notification_rate` (line 34) is a per-**user** API rate-limit, not a per-customer cap; `comms_ledger` does not exist. Both MEGAPHONE scan paths (`_scan_rx_expiring` 360, `_scan_birthdays_today` 391) and `_dispatch_scheduled_campaigns` (300) gate only on the raw `marketing_consent` flag with zero frequency accounting. So E6's cap is genuinely additive and the gap is real. **Enforceability caveat (already flagged by E6 itself, Risk 2 + Open-conflict 3):** with multiple MEGAPHONE workers the check-then-write cap is racy → soft ceiling unless a guarded counter is used. This is honestly disclosed; the builder must pick (and the chair must sign off on) soft-vs-strict. Acceptable to build, but acceptance test #1 must assert the soft-ceiling semantics it actually ships, not strict exactness.

**[P2] Consent gate is enforceable AND an upgrade, not a regression. Verified.** `marketing.is_opted_out` (1463) is the unified 3-signal check (flag + ledger OPT_OUT/STOP). Critically, the existing MEGAPHONE scans and campaign dispatch only check the raw `marketing_consent` flag — they do NOT honor the consent **ledger** events. Routing all rules through `passes_gates → is_opted_out` therefore *closes a consent-ledger bypass*, not merely preserves behavior. Builder must ensure the migrated rx/birthday rules use `is_opted_out`, not the old flag-only check.

**[P2] OTP transactional-bypass mechanism is NEW, not a reuse of `_TRANSACTIONAL_TEMPLATES`.** `_TRANSACTIONAL_TEMPLATES` (marketing.py:84) = {ORDER_DELIVERED, GOOGLE_REVIEW_REQUEST, NPS_SURVEY} — contains no OTP template. E6's OTP bypass rides a NEW rule-level `is_transactional=True` + `category="OTP"` path, which is internally consistent (send_notification already accepts `category`, notification_logs stores it — verified at notification_service.py:115/150), but it is a new bypass surface, not the existing template allowlist. Fix (E6 acceptance #2): assert the bypass via the new `is_transactional` flag/`category=OTP`, and ensure quiet-hours + freq-cap + consent ALL skip for that category in `passes_gates` (the ordered gate stack must short-circuit on transactional FIRST).

**[P2] `send_whatsapp` for OTP is real and signature-compatible.** Verified: `providers.send_whatsapp(phone, message, *, template_id=None)` (line 125) and `send_sms` (230) exist; OTP correctly routes through `send_notification` (honest PENDING, DISPATCH_MODE-gated) rather than calling the provider directly. Good — no second sender.

**[P2] All other E6 anchors real.** `campaign_segments`: `winback` exists (labeled "Win-back (lapsed)" — there is no separate `lapsed` key; winback IS lapsed, claim satisfied) and `cl_reorder`/`churn_risk`/`fu_due_today` are genuinely absent (correctly "to add"). `quiet_hours.in_quiet_hours/promo_send_allowed/next_quiet_end_utc_iso` (83/91/112). `vouchers.redeem_voucher_atomic`(170)/`issue_voucher`(298)/`_REDEEM_ROLES`(54, includes SALES_STAFF+AREA_MANAGER). `campaigns._audit`(168)/`_enforce_store_scope`(195)/`_CAMPAIGN_ROLES`(62). `notification_service.send_notification`(107)/`populate_template`(68); templates NPS_SURVEY/GOOGLE_REVIEW_REQUEST/WALKOUT_RECOVERY all present. `rbac_policy.POLICY`(118) + coverage-lock `test_rbac_policy.py` exists. New files (`reminder_rail.py`, `reminders.py`, `tender_routing.py`, `tender_reconciliation.py`) confirmed absent.

**[P2] Minor doc inaccuracy.** E6 cites `rbac_policy.POLICY` "entry shape at line ~2392" — the entry-shape doc-comment is at line 42 and the POLICY list starts at line 118 (spans to ~4749). Cosmetic; the list exists. Also `send_notification` has no `rule_id`/`campaign_id` param today — stamping `rule_id` needs a new kwarg or post-insert update; E6's "stamps like campaign_id" is directionally right but the param must be added (additive, trivial).

---

**VERDICT (E5+E6): FIX-FIRST** — E5 is safe-to-build as written; E6 needs two pre-build fixes before auto-merge: (P1) resolve the `fu_due_today` non-existent `mode` field + the duplicate `/due-today` route, and (P1) lock the frequency-cap semantics to the soft-ceiling the implementation can actually guarantee (or commit to a guarded counter) so acceptance test #1 asserts shipped behavior, not strict exactness.


## Area: PMSC
Two more findings nailed down:

1. **SC test #4 math is correct** (`24310 × 0.22 × 1.0 = 5348.20`). ✓
2. **SC test #3 has a math error in the doc**: it claims "avg discount 14% → multiplier **1.4** (floor-rounded, not 1.3)". But with the actual default multiplier table, floor(0.14)=0.14 → first cap ≥ 0.14 is `0.14 → multiplier 1.1`, NOT 1.4. 1.4 is the `≤0.11` tier. The doc's own reuse table (line 12) cites "1.4 multiplier @ 14% discount" — this contradicts the seeded settings. Either the worked example uses non-default settings or the doc is wrong. This is a spec/acceptance-test correctness bug that would cause a build agent to write a failing/incorrect test.
3. **`catalog_variants` is NOT written by catalog.py today** (no matches) — so PM acceptance test #3 ("one add-flow creates products + catalog_products + catalog_variants") is entirely NEW wiring, not "ensure one variant row" against existing behavior. The collection+schema exist but nothing populates them from the add-flow. The doc slightly understates this.

I have thoroughly verified both contracts. Let me compile the findings.

---

# PMSC Foundation — Adversarial Verification

## PM (Unified Product Master)

**[P1] SKU rule in the doc ≠ the existing `generate_sku` — and the doc never reconciles the format change.**
Claim: build `SKU = PREFIX+BRAND+MODEL+COLORCODE+SIZE` reusing `catalog.py::generate_sku`. **Verified: NO.** Actual `generate_sku` (catalog.py:1135) produces `{PREFIX}-{BRAND[:2]}-{MODEL[:4]}{COLOUR[:3]}-{counter}` — brand truncated to 2 chars, model to 4, colour to 3, no SIZE component at all, and a counter always appended (not "only on collision" as open-conflict #1 proposes). The new rule is a different algorithm, not a reuse. A build agent told to "reuse `generate_sku`" will either ship the old truncated format or silently rewrite it. Fix: PM.md "Public API" must state `build_sku` is a **rewrite** of `generate_sku` (full untruncated tokens + SIZE slot + collision-only counter), and the migration note must explicitly say the prefix-format SKUs differ from existing `SG-BR-MODELCOL-1001` SKUs so search/dedupe expecting the old shape isn't broken.

**[P1] `products` schema enum is DB-enforced and lacks `HEARING_AID` — acceptance test #1 cannot pass as written.**
Claim (test #1): "creating a Hearing Aid without `serial_no` is rejected [422 names missing field]". **Verified: PARTIAL / will fail.** `PRODUCT_SCHEMA.category.enum` (schemas.py:99-101) = `[FRAME, SUNGLASS, READING_GLASSES, OPTICAL_LENS, CONTACT_LENS, COLORED_CONTACT_LENS, WATCH, SMARTWATCH, SMARTGLASSES, WALL_CLOCK, ACCESSORIES, SERVICES]` — **no `HEARING_AID`**. The catalog `ProductCategory` enum HAS `HEARING_AID`/`HA` but lacks `SERVICES`/`COLORED_CONTACT_LENS`. Writing a HEARING_AID to the `products` spine fails Mongo `$jsonSchema` validation before any field-level 422. Also `CATEGORY_FIELDS` (catalog.py:200) defines requireds per the **short-code** enum; there is no proof `serial_no` is even a required field for HA there. Fix: PM.md open-conflict #3 ("canonical category set") must be promoted from a "note for the chair" to a **P1 blocking prerequisite** — add `HEARING_AID` (+ any missing values) to `PRODUCT_SCHEMA.enum` in the additive schema step, and reconcile the two enums before any add-flow test is written. Otherwise the two-surface write in test #3 dies on the spine insert.

**[P2] Acceptance test #3 (one-add-flow → both surfaces) is all-new wiring, not "ensure" — and crosses a non-atomic two-collection write.**
Claim: engine "ensures one variant row per minted SKU"; test #3 expects `products` + `catalog_products` + `catalog_variants` from one POST. **Verified: NO existing path does this.** `catalog.py::create_catalog_product` writes ONLY `catalog_products` (grep for `catalog_variants`/`parent_product_id` in catalog.py = **0 matches**); `products.py` writes ONLY `products`. Nothing populates `catalog_variants` from any add-flow today, despite the schema existing. The doc's own "Migration impact" admits the write is "atomically-ish, fail-soft" — a Mongo multi-collection write with no transaction. Fix: PM.md should flag the triple-write as net-new with an explicit ordering/rollback contract (write spine first; if PIM/variant write fails, the spine row must be marked or compensated), since a partial write leaves a billable `products` row with no PIM/variant — and POS reads the spine, so it would sell an orphan. Add an acceptance test for the partial-failure path.

**[P2] `catalog_products` has no Mongo validator — additive "new fields" claim is true but so is "no guard against bad data".**
Verified: YES, `catalog_products` is absent from the `COLLECTIONS` validator registry (only `catalog_variants` is registered, schemas.py:1522). The doc is correct that adding fields is safe (schemaless), but it proposes a 30+ field Shopify superset with zero validation, plus a NEW unique `{id:1}` index that is "currently only app-enforced" — adding that unique index against live data will **fail at creation if any duplicate/null `id` exists**. Fix: PM.md migration step must pre-check for dup/null `id` in `catalog_products` before creating the unique index (mirror the prod-data-blocker pattern already documented in memory), and decide whether the superset gets a validator at all.

**[P0-adjacent / P1] Back-compat claim "SKUs can contain `/`" is asserted but no validator is shown to currently allow or reject it.**
Claim: validator must allow `/` (e.g. `FRBURBERRYB31421109/7155`). **Verified: UNVERIFIABLE from the cited reuse** — there is no SKU-format regex/validator in `products.py`/`catalog.py` today (SKU is server-minted, never user-validated). So the risk is the reverse of what the doc says: if the new engine *adds* a SKU validator (for the import/legacy path in `ProductMasterCreate.sku` optional-accept), it could reject existing Shopify-style SKUs. Fix: PM.md must specify the legacy-import SKU acceptance is **format-permissive** (allow `/`, `-`, alnum, no length cap) and add a regression test that an existing Shopify-style SKU still lists/gets/sells (test #7 covers list/get/sell but not the validator-accepts-`/` path on import).

## SC (Scorecard / Slab)

**[P1] Acceptance test #3 multiplier value is mathematically wrong against the seeded settings (1.4 vs 1.1 at 14%).**
Claim (test #3 + reuse table line 12): "avg discount **14%** → multiplier **1.4** (floor-rounded, not 1.3)". **Verified: NO.** With `DEFAULT_MULTIPLIERS` (incentive_settings_repository.py:62-69), `floor(0.14*100)/100 = 0.14` → first `max_pct ≥ 0.14` is `{max_pct:0.14, multiplier:1.1}`. **1.4 is the `≤0.11` tier.** I confirmed `compute_multiplier` returns 1.1 for 0.14. The doc repeats "1.4 @ 14%" twice. A build agent writing test #3 verbatim will assert `multiplier == 1.4` and the test will **fail against the real engine**, or the agent will "fix" the engine and corrupt the payout math. Fix: SC.md test #3 must use a self-consistent example — either change the input to ~11% (→1.4) or the expected to 1.1 (@14%). Verify against the actual Excel worked example before locking.
*(For the record: test #4 `₹24,310 × 0.22 × 1.0 = ₹5,348.20` is correct; tier bands `76→0.6 / 84→0.8 / 96→1.0` are correct; conversion `(10−3)/10×20 = 14` is correct.)*

**[P1] Payroll already feeds incentive from a DIFFERENT source — the doc's new feed creates a second, conflicting path.**
Claim: payroll-run will read `get_incentive_for_payroll` from the LOCKED payout snapshot; "no payroll-engine change required." **Verified: PARTIAL — and a collision is unflagged.** payroll-run today calls `_fetch_incentive(db, emp, month, year)` (payroll.py:1332) which reads a separate `incentives` collection (keys `incentive_amount/amount/total/payout/net_incentive`), and `IncentiveSummary` reads the same `incentives` collection (payroll.py:1137). The SC doc never mentions this existing path. If the new feed is added without deprecating/reconciling `_fetch_incentive`, payroll could **double-count** (old `incentives` doc + new snapshot feed) or pick the wrong one. Fix: SC.md "How dependents call it" / migration must explicitly state payroll-run's `_fetch_incentive`/`incentives`-collection path is **replaced or made subordinate** to the snapshot feed, with a single source-of-truth precedence rule, plus a test that the two paths don't sum.

**[P2] E2 settings hierarchy gap is real and correctly scoped — but the backfill claim "stamps scope:store on existing docs" must not break `get_for_store`.**
Claim: `incentive_settings` is store-only; add `scope/entity_id` + resolution. **Verified: YES** — repo is `id_field="store_id"`, `find_one({"store_id":...})`, no entity/global notion. The doc's additive plan is sound. One risk: `get_for_store` does `{**defaults, **doc}` merge; introducing `resolve_settings(global→entity→store)` changes which doc wins. Fix: SC.md should require `resolve_settings` to be **opt-in** (only global/entity docs that explicitly exist override; absent → today's exact store-or-defaults behavior) and add a test that a store with no global/entity rows yields byte-identical settings to today (test #9 covers inheritance but not the "no-op when only store exists" regression).

**[P2] Conversion auto-calc scores 0 (not null) on missing footfall — "Fail Loudly" philosophy conflict, acknowledged but unresolved.**
Verified: YES — `_conversion_score_for` returns `0` when `walk_ins <= 0` (points.py:251-252), silently dragging the /100 score down 20 points if a store forgets to enter footfall. Open-conflict #6 raises this but leaves it open. Given the repo's stated "Fail Loudly" core philosophy and that this directly affects payout rupees, this should be **decided before build**, not deferred — a silent 0 on a money input is exactly the class of bug the philosophy targets. Fix: SC.md should pre-resolve to null/blocked-with-warning (or a per-store toggle) rather than scoring 0; flag as a build-blocking decision.

**[P2] Product-Incentive Kicker POS auto-attach touches revenue-critical POS — correctly feature-flagged, but the idempotency index needs a partial-filter caveat.**
Verified plausible: the proposed `unique {order_id:1, sku:1} WHERE order_id != null` mirrors existing partial-unique patterns (points_log, payout_snapshots). Risk: a single order line with the same SKU twice (qty split) or manual re-entry could 11000-dup-key and **block the kicker write or the order save** if attach is synchronous. The doc says "feature-flagged" + "idempotent attach" — good — but doesn't specify attach must be **async/fail-soft so a kicker failure never blocks order finalisation**. Fix: SC.md must state the POS attach is post-commit and fail-soft (never raises into the order path), consistent with the "Ask before touching POS" guardrail.

---

## Verdict: **FIX-FIRST**

Both contracts reuse genuinely-existing, well-isolated code (calculators, repos, GST/pricing helpers all verified present and behaviorally as described), so the foundation is buildable — but **not safe to hand to an auto-merge agent as-is**. Blocking items before build: PM enum reconciliation incl. missing `HEARING_AID` in the DB-enforced `products` schema (P1) and the SKU-format rewrite-vs-reuse ambiguity (P1); SC's wrong multiplier in acceptance test #3 (P1, would make the agent corrupt payout math) and the unreconciled dual payroll-incentive feed (P1, money double-count). The P2s (non-atomic triple-write, unique-index dup pre-check, footfall-0 fail-loudly, POS attach isolation) should be resolved in the doc before the build session, since acceptance tests as written will either fail or pass against wrong behavior.


## Area: docs
Confirmed: F31 (commission clawbacks) and F30 (SPIFFs) packets contain ZERO references to the SC scorecard engine, "via SC," or the PUNE model. They are written entirely against the OLD per-line `commission_rate_percent` / `payroll_engine.incentive` model. Yet the EXECUTION_BOARD explicitly labels both as "via SC engine" (lines 109-110). This is a genuine packet-vs-board contradiction the build session will trip on. SC.md's "Open conflicts" also flags that SC owns the incentive-of-record but the SPIFF/commission packets don't route through it.

I have all findings verified. Writing the audit now.

---

# Roadmap Adversarial Audit — `docs/roadmap/` (DECISIONS, PROTOCOL, EXECUTION_BOARD, ENGINES build-plan)

## Question 1 — Does the POS-payment SKIP (DECISIONS §4) break #16 / #22 / #23?

**[P2] The SKIP does NOT break #16/#22/#23 — but the docs never say so explicitly, inviting a build-session over-scope.**
- **Claim:** DECISIONS §4 "POS advance+on-delivery split: SKIPPED — do NOT change the POS payment model… Revisit before #16."
- **Verified: YES, the three features survive the skip.** I checked the code. `orders.py:376` `PaymentMethod` already supports 8 tenders; `add_payment()` (line 2488) records one `PaymentEntry` per call, so **multiple calls = split-tender today** — #22's own packet (F22:5) says "IMS already has a fully-functional split-tender system." All three features are **read-only over the existing `order.payments[]`**: #16 (F16:10,161 "never writes to orders/payments"), #23 (F23:110 "expected-cash computation is READ-ONLY"), E5 (E5.md:7 "POS payment capture is untouched — E5 only reads"). The thing §4 skips is the **advance + on-delivery phase split + per-EDC-terminal capture**, which is a *different* axis than split-tender or reconciliation.
- **Concrete issue:** The skip and these features are reconcilable, but it is only spelled out inside E5.md (line 117) and F16/F23 risk notes — **not in DECISIONS §4 or on the board.** A build agent reading §4 ("Revisit before #16") could wrongly conclude #16 is blocked, or conversely try to add the phase-split to deliver "full intent" and violate the POS lock.
- **Fix:** In `DECISIONS.md §4` (the "POS advance+on-delivery split" row), append: *"This skip does NOT block #16/#22/#23 — those read the existing `order.payments[]` and add only derived/reconciliation records (E5). The phase-split (ADVANCE/ON_DELIVERY) + per-EDC capture is the only deferred part; #16/#22/#23 deliver full intent on the current single-phase, multi-tender model."* Mirror one line in `EXECUTION_BOARD.md` Phase-2 header (it already says "POS UNCHANGED" — make it "POS capture UNCHANGED; recon reads existing payments").

## Question 2 — Is the simple commission truly superseded by the SC scorecard everywhere?

**[P0] Board says #30/#31 are "via SC engine"; the F30/F31 packets are written entirely against the OLD per-line commission model and never mention SC. Direct contradiction that will mis-build under auto-merge.**
- **Claim:** `EXECUTION_BOARD.md:109-110` — "#30 Advanced SPIFFs (**via SC engine**)" / "#31 Commission clawbacks (**via SC engine**)." DECISIONS §4 — "PUNE scorecard is the official incentive engine; the simple per-line commission (§3) is secondary/superseded." PROTOCOL §6 — "Features MUST call the engines, never reimplement."
- **Verified: NO — packets contradict the board.** Grep of `F31` for `commission|SPIFF|scorecard|SC engine|incentive` → only matches the OLD model: `salary_config.commission_rate_percent`, `payroll_engine._earnings()` incentive merge, new `commission_ledger` collection (F31:7-10, 27-46). **Zero references to SC, `scorecard_engine`, `get_incentive_for_payroll`, or the PUNE model.** Same for F30 (builds a parallel `spiff_log`/`spiff_hits` + payroll `incentive` merge, F30:8,47). Yet `SC.md` declares itself "the official incentive engine… the one money seam → Payroll" via `get_incentive_for_payroll()` (SC.md:54,107) and its open-conflict #1 explicitly flags this unresolved.
- **Concrete issue:** Under auto-merge, the build session takes F31 at face value, builds a **second** payroll money-feed (`commission_ledger` → `payroll_engine.incentive`) that competes with SC's `get_incentive_for_payroll` → the exact divergent-copy bug PROTOCOL §6 forbids (it even cites the `pricing_caps` precedent). Two engines writing the `incentive` kwarg = double-pay risk.
- **Fix:** Rewrite the F30/F31 packets' "Reuse" + "Backend" sections to route through `scorecard_engine` (SPIFF = a Kicker per DECISIONS §4; clawback = a negative adjustment SC folds into the locked snapshot), OR add a packet-header note: *"SUPERSEDED-MODEL WARNING: ignore the `commission_rate_percent`/`payroll_engine.incentive` plumbing below — incentive of record is SC (`get_incentive_for_payroll`). Build clawback/SPIFF as SC inputs, not a parallel payroll feed."* Also reconcile DECISIONS §3 "Commission basis (#31)" row — it still half-lives ("Base earning measure where still needed: % of sale value, LUXURY+PREMIUM only"), which F31 then implements as the *primary* mechanism. Tighten §3 to: "#31 clawback is an SC adjustment; no standalone commission engine."

**[P1] DECISIONS §3 "SPIFF approval (#30): Auto-approve" contradicts the F30 packet's owner-decision, which still offers "manager approval required / configurable."**
- DECISIONS §3 locks "Auto-approve; shown as 'SPIFF Bonus' line on payslip." F30:105 Owner-decision still presents a/b/c including "manager approval required." A build agent following the packet's open question re-opens a locked decision.
- **Fix:** Strike the SPIFF-approval owner-question from F30 (or mark "LOCKED: auto-approve per DECISIONS §3").

## Question 3 — Any feature scheduled in a phase BEFORE the engine it needs?

**[P0] Phase-0 ships #21 against "E3-shim" but E3 is a Phase-1 engine — the dependency is forward-referenced with an undefined "shim."**
- `EXECUTION_BOARD.md:41` — "#21 Defective quarantine barcoding | **E3-shim**" sits in **Phase 0**, but E3 (Item-event ledger) is listed in **Phase 1** (board line 50) and the ENGINES build-plan puts E3 in **Wave 3** (ENGINES.md:28, "E3 is the heaviest, last, feature-flagged"). #21's natural home is E3 (E3.md:73 `POST /items/{stock_id}/quarantine` is the quarantine event).
- **Concrete issue:** "E3-shim" is named nowhere in ENGINES.md, the E3 contract, or any packet — it is an undefined dependency. A build agent in Phase 0 cannot build #21's quarantine state transitions without either E3 or a spec for the shim, and E3 is 3 waves away. Under auto-merge this either bounces forever or ships a hollow shell (violating the INTENT-FIDELITY gate).
- **Fix:** Either (a) define "E3-shim" explicitly in ENGINES.md (a minimal QUARANTINE/AVAILABLE status enum + audit row, no ledger chain) and add it to the Phase-0 engine cut, or (b) move #21 to Phase 1 after E3. Recommend (a) with a 1-paragraph shim contract, since #21 is a quick-win.

**[P1] Phase-1 #6 "Luxury serial tracking" depends on `E3,PM` (board:60) but PM is a Wave-2 engine and E3 is Wave-3 per the ENGINES build-plan — #6 cannot complete in Phase 1.**
- Board Phase-1 lists `#6 … Engine/dep: E3,PM`. But ENGINES build-plan: PM = Wave 2 (ENGINES.md:26), E3 = Wave 3 (line 28). A Phase-1 feature whose two hard deps land in Waves 2–3 is mis-phased.
- **Fix:** Move #6 to Phase 2/3 (after E3+PM), or note on the board that #6 is "Phase-1 BACKLOG, unblocks only after E3+PM (Wave 3)" so the orchestrator doesn't promote it to TODO prematurely (PROTOCOL §1 says items move to TODO "only when dependencies are met" — make the dep visible).

**[P2] Engine wave-order vs board-phase mismatch is systemic and unstated.** ENGINES.md uses Waves 0a/0b/1/2/3; EXECUTION_BOARD uses Phases 0–5. E4 is board-Phase-1 but ENGINES Wave-1; E5 is board-Phase-2 but ENGINES Wave-3; E6-full is board-Phase-1 (#46/#41/#39 consume it) but ENGINES defers the full reminder engine to Wave 3 (only the OTP/cap slice is Wave 0b).
- **Concrete issue:** #46 "Configurable reminders" is in **Phase 0** (board:45, dep E6), #41/#39 in Phase 1 (dep E6) — but the Phase-0 engine cut (ENGINES.md:94) ships **only E6's transactional OTP+cap slice and explicitly defers `reminder_rules`, the segments, and the router.** So #46's actual dependency (the reminder *rule engine*) is not built in Phase 0.
- **Fix:** Add a one-line "Engine slice required" column to the board, or a mapping table in ENGINES.md aligning each board phase to the engine waves. Specifically: move #46 to Phase 1 (it needs E6-full, not the OTP slice), OR pull the reminder-rule slice into the Phase-0 cut. As written, #46 in Phase 0 will bounce.

## Question 4 — Any Phase-0 item missing a packet/engine prerequisite?

**[P1] Phase-0 #34 "Global target ticker" depends on real-time sales/target data that is SC-derived, but SC is Wave-2 — and #34 has no packet engine-link to SC.** Board:43 lists `#34 | … | E2`. F34's ticker shows global target progress, which needs the slab-target math that lives in `payout_calculator`/SC. With only E2 (settings) available in Phase 0, #34 can render a configurable shell but cannot compute real target attainment.
- **Fix:** Confirm #34 Phase-0 scope is "config + static/manual target display only," or add SC (Wave 2) as a soft dep and move the live-attainment part later. State this in the (still-pending) #34 packet.

**[P2] Phase-0 #7 and #40 are marked dep=`—` (none) and "read-only"; that is consistent with the code (no engine needed for read-only AI), so these are clean.** No action — flagged only to show the read-only Phase-0 items were checked and pass.

**[P2] Every Phase-0 row shows `Packet: pending` and the board is `NOT LIVE` (STATUS.md + EXECUTION_BOARD:4).** PROTOCOL §1 forbids moving an item to TODO without a packet. This is correct gating, but it means **none of the above orderings have been packet-verified yet** — the contradictions in Q2/Q3 must be fixed in the packets *before* BOARD LIVE, or auto-merge will codify them.

## Question 5 — Decisions that contradict each other (price floor vs promo ceiling vs caps)

**[P1] The cost+10% price floor (DECISIONS §9) is owned by NO board feature, and two packets falsely claim it already exists in code.**
- **Claim:** DECISIONS §9 — "cost_price + 10% floor, per category (system blocks below-floor even when discounts stack)." 
- **Verified: partial / the floor is wrong-threshold and unowned.** `orders.py:1338` enforces `unit_price < cost` — a **cost+0% floor, not per-category, not cost+10%.** PM.md:92 and ENGINES.md:1363 correctly call cost+10% "the **future** floor." But **F32:107 asserts "the final price cannot go below COGS (existing cost-floor guard in `orders.py` already enforces this)"** and **F41:101 "the existing cost-floor check prevents selling below cost"** — both treat the *existing 0% floor* as if it satisfied §9's *10% per-category* floor. It does not.
- **Concrete issue:** No board item owns upgrading cost+0% → cost+10%-per-category. E2 publishes the *knob* (`liquidation.floor_pct_over_cost`, `pricing.cost_floor_pct`) and PM claims to be "the single writer enforcing… the future cost+10% floor (PM.md:92)" — but PM is a *catalog* engine enforcing floor at **create/update**, whereas the POS sell-path block lives in `orders.py` and is unowned by any packet. Result: the LOCKED §9 floor silently never ships.
- **Fix:** (1) Add an explicit board item (or fold into PM/E2 consumer work) "Enforce cost+10% per-category price floor on the POS sell-path (`orders.py:1338`), reading `pricing.cost_floor_pct` via E2." (2) Correct F32:107 and F41:101 to say "the existing guard blocks below-**cost** only; the LOCKED cost+10% per-category floor is not yet enforced — depends on the §9 floor work." 

**[P2] §9 internal layering (offer<MRP → no further discount) vs promo ceiling 30% vs role/category caps is consistent and code-grounded — no contradiction.** `orders.py:1324-1367` already enforces: MRP/offer ceiling → cost floor → "no further discount on HQ-discounted" → effective-discount cap = `min(user_cap, category/brand_cap)`. F11:94,97 correctly layers promos *inside* `effective_discount_cap` as the outer hardlock. The decision stack (caps → promo ceiling → floor) is well-ordered. The only gap is the floor *threshold* (P1 above), not the layering.

**[P2] DECISIONS §9 "no further staff discount on liquidation items" has no enforcement owner.** The "liquidation" status enum and its discount-block aren't assigned to a packet (#10 "Ageing auto-liquidation" is Phase-5, board:121, and its packet would set the flag, but the POS-side block that *reads* the flag is unspecified). Minor — flag for #10's packet.

---

## Cross-cutting

**[P1] Audit-sink and settings-resolver mismatches are RESOLVED in the ENGINES build-plan but NOT back-ported into the per-engine contracts or packets.** ENGINES.md:36-40 locks `get_policy(key, scope)` (kills `resolve_setting`/`resolve_settings`) and `AuditRepository.create` (kills direct `append_audit_entry`). But `SC.md:63` still defines `resolve_settings(store_id, …)`, `E3.md:17,135` and `E5.md:123` still cite `append_audit_entry` as the sink. A build agent reading the engine contract (not the build-plan preamble) implements the dead name.
- **Fix:** Add a one-line "SUPERSEDED BY ENGINES.md build-plan §Cross-engine contracts" banner at the top of E3/E5/SC, or edit the signatures in-place. Cheap, prevents re-introducing the divergence PROTOCOL §6 exists to stop.

---

## Verdict

**FIX-FIRST.** The board is correctly gated `NOT LIVE`, so nothing has shipped — but three P0s would mis-build under auto-merge if BOARD goes LIVE as-is: (1) #30/#31 packets contradict the board's "via SC engine" and would spawn a parallel payroll money-feed; (2) #21's Phase-0 "E3-shim" dependency is undefined and forward-references a Wave-3 engine; (3) #6 is mis-phased ahead of its PM+E3 deps. Plus the LOCKED cost+10% price floor (§9) is owned by no feature and is mis-described as "already enforced" in two packets. Resolve the P0s and the §9-floor ownership before flipping STATUS.md to BOARD LIVE.

Files to edit: `docs/roadmap/EXECUTION_BOARD.md` (lines 41, 60, 45, 109-110), `docs/roadmap/DECISIONS.md` (§3 commission/SPIFF rows, §4 POS-skip row, §9 floor ownership), `docs/roadmap/ENGINES.md` (define E3-shim; align wave/phase table), `docs/roadmap/_analysis/features/F30…md` + `F31…md` (route through SC), `F32…md:107` + `F41…md:101` (correct cost-floor claim), and `engines/E3.md`/`E5.md`/`SC.md` (back-port the locked resolver/audit names).


## Area: intent
This confirms F46's core diagnosis is accurate: MEGAPHONE's `_scan_rx_expiring` uses a hardcoded 90-day window (line ~367), and dispatch infra exists. The F46 "extend, don't rebuild" path (read from `reminder_rules` instead of constants) is real and viable. Note the docstring says "7/30/90 days" but the code only fires a single 90-day cutoff — minor existing inconsistency, not blocking.

I have enough verified evidence on all 6 features. Let me compile the audit. Let me do one last quick check — confirm whether ORACLE actually computes burn-rate from orders today (F07's core delta) vs. the packet's claim that it's "the kernel" — I already saw it queries `stock_units` by reorder_point only, NOT orders. And verify the `_resolve_rx_expiry` accepts `window_days` param (confirmed at line 200 above: `window_days: int = RX_EXPIRY_WINDOW_DAYS`). 

I now have all verifications needed.

---

# Intent-Fidelity Audit — Phase-0 Quick-Wins (Area: intent)

Verified against code on disk. The chair tagged **5 of 6 as `quickwin=yes`** (only #40 is `quickwin=no`). My finding: **two of the five "yes" packets contain false existing-overlap claims that inflate the %done**, and one ("quick win") is genuinely an M/5-day build mislabeled.

## [P1] #7 Predictive purchasing — "fake quick win" (packet over-claims the kernel)
- **Claim:** "ORACLE's `_propose_reorders()` is the kernel … reads `orders.items` … the from-forecast endpoint already accepts 90-day velocity data … executor for draft_po already auto-creates PO." Tagged `quickwin=yes, days=5`.
- **Verified? Partial / misleading.** ORACLE `_propose_reorders()` (`oracle.py:139-204`) queries **`stock_units` where `quantity < reorder_point`** — it does NOT read `orders.items` and has **zero burn-rate logic**. The burn-rate aggregation (the actual intent) is 100% net-new, not an "extend." The `from-forecast` endpoint (`vendors.py:811-839`) **computes velocity internally from orders** — it does not "accept velocity data," so ORACLE cannot "wire its burn-rate output directly here" as the packet says. Worst: the `draft_po` executor (`proposals.py:581-635`) creates a **stub single-SKU PO** (`po_id, sku, quantity, vendor_id`) — it does NOT call `from-forecast` and produces a different, thinner PO shape than the real vendor PO. So "approve → real PO" is not what happens.
- **TRUE delta:** new burn-rate aggregation endpoint + new ORACLE scan step + reconcile two divergent PO-draft paths (proposal-stub vs from-forecast) + dashboard. Realistically the full M/5 days, with design risk in the PO-shape reconciliation. **Current ~15%, not the ~60% "kernel exists" framing.**
- **Fix:** In `F07`, correct "Existing overlap" — state ORACLE is reorder-point reactive over `stock_units` (no orders read, no burn-rate); state the `draft_po` executor emits a stub PO and the build must either enrich `_exec_draft_po` or route approval through `from-forecast`. Pick ONE PO path in the packet.
- **Most important acceptance test (proves intent, not existence):** Seed a SKU with on-hand=10 and 7-day sales of 5/day (stockout in 2 days) but on-hand still **above** reorder_point → ORACLE must propose a reorder with `days_of_stock_remaining≈2` in the payload. (A reorder_point-only system would miss this entirely — this is the whole point of "predictive.")

## [P1] #21 Defective quarantine — quick win, but two false overlap claims + net-new guards
- **Claim:** "stock_units status field has DAMAGED as a valid value (connection.py:238-243); extend the existing enum"; and "POS catalog query already filters status=AVAILABLE — QUARANTINED units never appear … no POS code change, near-zero risk." `quickwin=yes`.
- **Verified? Partial.** (1) `connection.py:238-243` is **index definitions, not a status enum** — there is no enforced enum; status is a free string. Harmless to extend, but the "existing overlap" is fabricated. (2) The POS-safety claim is **right outcome, wrong mechanism**: POS reads `catalog_products` (`orders.py:861`), NOT a status-filtered stock query. Quarantine safety actually comes from `find_available`/`find_by_product_store` filtering `status:"AVAILABLE"` (`product_repository.py:84,91`) — so a non-AVAILABLE unit is correctly excluded from oversell allocation. The conclusion ("won't be sold") holds; the stated reason is incorrect, which matters because a builder trusting the wrong reason may put QUARANTINED in the wrong place. (3) The `transfers.py` "reject QUARANTINED" guard (business rule line 80) is **genuinely net-new**, not "reuse." labels.py QZ infra is real but **workshop-job-shaped** (`labels.py:414,505` are workshop/product labels) — a generic quarantine-label endpoint is net-new.
- **TRUE delta:** still a real 2-day quick win, but it IS new code: new endpoint, new label template, new transfers guard, new queue tab. **~20%, honest.**
- **Fix:** In `F21` "Existing overlap"/"Risk notes", replace the enum claim with "status is a free string (no enum)"; replace "POS catalog filters AVAILABLE" with "stock allocation (`product_repository.find_available`) filters AVAILABLE — verify QUARANTINED is treated as non-AVAILABLE in `_mark_units_sold` FIFO and the lens reserve."
- **Most important acceptance test:** Quarantine the **only** AVAILABLE unit of a serialized SKU, then attempt to sell it at POS → order creation returns **409 "Cannot oversell"** (proves the unit is removed from sellable stock, the core intent — preventing re-sale of a defective item).

## [P2] #35 Cost & margin masking — real quick win, one false call-site
- **Claim:** "`inventory.py _build_store_ledger()` surfaces `cost_price` and margin calculations … wrap `cost_price` and derived `margin_pct`." `quickwin=yes`.
- **Verified? Partial.** Cost fields genuinely exist on orders (`orders.py:911,1540 cost_at_sale`), products (`cost_price`), and finance. BUT `_build_store_ledger()` (`inventory.py:328-458`) returns only `sku/name/brand/category/mrp/offer_price/stock/reserved/barcode/location` — **it does NOT compute or return `cost_price` or `margin_pct`.** So that specific masking call-site is masking a field that isn't returned. The masking helper concept is sound and additive; the build is a real 2-day quick win, but the packet's call-site map is partly fictional.
- **TRUE delta:** new `cost_mask.py` + settings endpoint + ~8 *real* call-sites (orders detail, products, vendors PO, finance COGS) — minus the non-existent ledger margin field. **~10%, honest.**
- **Fix:** In `F35`, drop/correct the `_build_store_ledger` margin claim; audit each named call-site for the field actually existing before the builder wraps a `None`.
- **Most important acceptance test:** As SALES_CASHIER, GET an order detail and a product that carry `cost_at_sale`/`cost_price` → response has those keys **`null`** (not absent, not present); as ACCOUNTANT the same call returns the real numbers. (Proves field-level, role-driven masking — not page-level hiding.)

## [P2] #34 Global target ticker — genuine quick win, claims hold
- **Claim:** budgets REVENUE head + finance revenue aggregation + notifications + ORACLE tick all exist. `quickwin=yes, days=4`.
- **Verified? Yes.** `budgets.py:59 REVENUE_HEAD="REVENUE"`, per-(store,period,head) upsert confirmed; `finance.py:524 get_revenue` with period bucketing + prev/change_pct confirmed; no pre-existing `target-ticker` endpoint (correctly net-new). Claims are accurate. Watch item: the perf note (60s poll × sessions) is real — the suggested `{created_at,status,store_id}` index does not exist on `orders` (only `stock_units` composite indexes seen in `connection.py`), so the index is net-new, not "check if present."
- **TRUE delta:** as described, assembly + UI. **~25%, honest.**
- **Fix:** In `F34` risk notes, change "add index if not already present" → "no `orders` compound index exists; add `{created_at,status,store_id}` + a server-side cache."
- **Most important acceptance test:** As SALES_STAFF, GET `/finance/target-ticker` → response contains **no `mtd_revenue`/rupee fields**, only `pct_complete`; as STORE_MANAGER the same call returns `mtd_revenue`. (Proves the server-side `raw_visible` privacy split — the headline intent — not client-side hiding.)

## [P2] #40 VIP churn — correctly tagged `quickwin=no`; honest packet
- **Claim:** `quickwin=no, days=4`; reuses churn-risk + RFM + lifecycle + ORACLE + proposals + MEGAPHONE.
- **Verified? Yes.** `crm.py:1055 _identify_churn_risk_customers` (real, orders-backed recency bands), `:947 _perform_rfm_segmentation`, `:830 _determine_lifecycle_phase` (VIP at LTV≥₹1L) all confirmed. The delta — **personalised median inter-purchase-interval baseline** — is genuinely absent (current model is flat-recency 180/91/31). New `vip_churn_snapshots` collection is correctly flagged net-new. This is the **only** packet whose quick-win flag matches reality. No correction needed.
- **TRUE delta:** new interval-baseline compute in ORACLE + 2 crm endpoints + new collection + new page. Correctly **not** a quick win. **~25%.**
- **Most important acceptance test:** A VIP who buys every ~30 days and is now 80 days out must be labelled **HIGH/overdue**, while a VIP who genuinely buys every ~180 days and is 80 days out must be **NONE** — same recency, opposite labels. (Proves the personalised-interval intent vs the old flat-recency rule that would treat both at 80 days identically.)

## [P2] #46 Configurable reminders — genuine quick win, claims hold
- **Claim:** MEGAPHONE scans Rx/birthday/walkout on a tick (hardcoded windows); `campaign_segments` `rx_expiry` accepts `window_days`; DISPATCH_MODE + notification_templates + quiet_hours exist. `quickwin=yes, days=5`.
- **Verified? Yes.** `megaphone.py:360 _scan_rx_expiring` uses a hardcoded 90-day cutoff (docstring says "7/30/90" but only one window fires — minor existing bug, not blocking); `campaign_segments.py:197 _resolve_rx_expiry(window_days=...)` already parameterized; `settings.py:283 notification_templates`, `:1198 DISPATCH_MODE`, `quiet_hours` all confirmed; no pre-existing `reminder_rules`. Accurate. The biggest *real* work is the net-new segment resolvers (`order_ready`, `post_purchase_feedback`, `frame_service_due`) and the `workshop.notify_ready()` behavioral change (packet flags both honestly).
- **TRUE delta:** new `reminder_rules` collection + 4 settings endpoints + MEGAPHONE rule-runner + 2-3 new resolvers + Settings UI. The packet's own "ship 4 triggers, defer frame-service" scoping is correct. **~20%, honest.**
- **Most important acceptance test:** Create an `RX_EXPIRY` rule with `days_before=30`, disable the 90-day default → on the next MEGAPHONE tick, a customer with Rx expiring in 30 days gets exactly one PENDING `notification_log` stamped with `rule_id`, and a second tick within `repeat_cooldown_days` produces **none** (proves config-driven windows + per-rule cooldown — the actual intent over hardcoded constants).

---

## Cross-cutting note for the auto-merge sessions
Two packets (#7, #21, and partly #35) cite **specific line ranges and "already does X" claims that are false or describe a different mechanism**. An autonomous builder that trusts "Existing overlap" verbatim will (a) try to wire ORACLE into `from-forecast` and find the executor doesn't go there, (b) mask a `margin_pct` field that `_build_store_ledger` never returns, and (c) rely on a "POS catalog filters AVAILABLE" guard that doesn't exist (the real guard is in the repo layer). None are fatal, but each will burn a build cycle or ship a hollow shell that passes an existence test while failing the intent test. The acceptance tests above are written to catch exactly that.

## VERDICT (area: intent)
**FIX-FIRST** — correct the false "existing overlap" claims in F07, F21, and F35 (and re-tag #7 as `quickwin=no`/risk-MED) before the packets go to auto-merge; F34, F40, F46 are SAFE-TO-BUILD as written.
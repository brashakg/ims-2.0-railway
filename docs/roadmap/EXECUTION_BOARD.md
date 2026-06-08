# IMS 2.0 Roadmap — EXECUTION BOARD

> Single source of truth for who-builds-what. **Read order each loop:** `STATUS.md` →
> `PROTOCOL.md` → `DECISIONS.md` → `CORRECTIONS.md` → the item's packet in `features/`.
> **Precedence:** DECISIONS > CORRECTIONS > ENGINES/packets. Go-live is controlled by `STATUS.md`.
> Item IDs: `E*`=engine, `PM`/`SC`=foundation, `#NN`=roadmap feature, `N*`=Excel net-new.

Legend: **BACKLOG** (not ready) · **TODO** (packet ready + corrections folded, claimable) · **IN BUILD** · **IN TEST** · **DONE** · **BLOCKED**

> ⚠️ Every TODO item below has passed the adversarial hardening pass. **Read its `CORRECTIONS.md` entry before building** — some engine clauses are DO-NOT-BUILD.

---

## ▶ TODO — Phase 0 (build session: take the top claimable item)

| # | Name | Dep | Packet | MUST-READ correction |
|---|---|---|---|---|
| E6 | Reminder/segment rail (OTP+cap slice) | E2 | features/E6.md | **BOUNCED 2026-06-08 (test session) -- §6/T5 engine-reuse.** Findings 2026-06-08: vouchers are minted via a PARALLEL path in `reminder_rail.py` _mint_voucher (`db.get_collection("vouchers").insert_one(doc)` with its own RMD-prefix code), NOT via canonical `vouchers.issue_voucher` (named only in a comment; never imported/called). Packet T5: "minted via vouchers.issue_voucher, NOT a parallel path"; PROTOCOL §6: reviewer REJECTS in-feature reimplementation. The branch T5 test only asserts a voucher doc EXISTS (count/status/amount), not that the engine was the mint path -- so the parallel path passes a weakened test (§5 hollow-shell). Risk: divergent code format + possibly missing issue_voucher fields (audit/ledger/entity-binding) that E1 money-guard reads at redemption; latent now (DARK, rules active=False) -- fix BEFORE the orchestrator activates any voucher_template rule. FIX: import+call `vouchers.issue_voucher(...)` (keep idempotent per-(customer,rule,day) dedupe) + strengthen T5 to spy issue_voucher; if its signature is unsuitable, escalate BLOCKED not reimplement. repro: grep -n issue_voucher reminder_rail.py -> comment only. Rest of E6 EXCELLENT + DARK-compliant (19 tests, OTP short-circuit, soft-cap, quiet-hours defer, DISPATCH-gated, routes catalogued) -- this is the one blocker. |

_7 of 8 Phase-0 items DONE (merged): E1, E2, #35, #21, #34, #40. **E6 BOUNCED back to TODO** (voucher §6/T5 -- see row above). Phase 1 (E3, E4, PM, SC, #46, N1/#45, ...) is the next promotion wave once E6 lands -- fold each packet per PROTOCOL §11._

## 🔨 IN BUILD
_empty_

## 🧪 IN TEST

| # | Name | PR | Branch | Notes |
|---|---|---|---|---|
| E3 | Item-event ledger (spine) | [#580](https://github.com/brashakg/ims-2.0-railway/pull/580) | `feat/E3-item-event` | Append-only `item_events` spine: `record_event` = single-doc CAS on `stock_units` + monotonic `event_seq` (existing `allocate_sequence`) + ONE `AuditRepository.create` row (NO unit hash-chain, P0-2). Reconciled with existing `serial_numbers` (serial-bind, dup 409). `/api/v1/items` 9 routes (rbac-catalogued). On-hand rollups gain `EXCLUDED_STATUSES`. **Adversarial: no correctness P0/P1** (CAS + event_seq race-safe). 18 tests + rbac 29; smoke 1010; E/F clean. **Per CORRECTIONS R2: existing-path wiring + return-`SERIAL_MISMATCH`-409 + FE are split follow-ups** (validate the spine, not the deferred wiring). |
| E4 | Approval/PIN + maker-checker | [#579](https://github.com/brashakg/ims-2.0-railway/pull/579) | `feat/E4-approval-pin` | `ApprovalEngine`: atomic single-use `approve()`/`consume` (vouchers-style guarded `find_one_and_update`, two-concurrent→one token); bcrypt PIN + 5/15min throttle; tiers via E2 `get_policy` (no `_DEFAULT_TIERS`); hash-chained audit. 7 `/approvals/*` + 3 PIN routes (rbac-catalogued); TASKMASTER `expire_stale`. **Adversarial: 2 P1s FIXED** — dedupe-index null-collision (`$exists` guard) + maker tier-escalation (required_tier may only RAISE). 20 tests + rbac 29; smoke 1009; E/F clean. **Per CORRECTIONS R2: approval FE is a split follow-up** (wired by #25/#26/#27). |

## ✅ DONE

| # | Name | PR | Merged | Test-session verdict |
|---|---|---|---|---|
| E1 | Money-guard service | [#563](https://github.com/brashakg/ims-2.0-railway/pull/563) | `a8c6945` | **PASS — 2026-06-07.** Acceptance T1-T13 PASS (15/15 in `test_money_guard_e1.py`) + `test_money_integrity_guards.py` 25/25 unchanged (T6). Required CI green (`test 3.10/3.11`, `test-and-build`, `security`); `e2e` = known cold-preview flake, non-required per PROTOCOL §3. **P0-1 honored:** facade only — NO `money_accounts` SoR / index / migration; the 3 new types return `reason="unavailable"` (T13 deferral correct; orchestrator to reconcile packet T13 / item-6 / DoD-5 wording). Single-doc atomic balance guard (floor/status/expiry in the filter; loser matches nothing; `_classify_debit_failure` read-only), shims behavior-preserving (T10/T11/T12), audit via `AuditRepository.create`, fail-soft `unavailable`/fail-closed `no_atomic`, 0 emoji introduced (flagged em-dashes pre-existing), non-POS (no flag). **HARDENING FOLLOW-UP (orchestrator — fix BEFORE any idempotent direct caller, e.g. E5 returns/refund, wires onto E1):** the idempotency marker is pushed by `_append_ledger` in a SEPARATE `update_one` AFTER the balance `find_one_and_update`, so the `money_ledger.idempotency_key != key` filter is NOT truly atomic — two concurrent same-key calls can both pass the filter and double-apply. Sequential retries (T4/T4b) are safe and no Phase-A caller passes `idempotency_key`, so it is unreachable today. Fix: `$push money_ledger` the marker INSIDE the balance `find_one_and_update` (same atomic write) and correct the over-claiming "race-safe" comment at `money_guard.py:303-304`. |
| E2 | Settings-matrix engine | [#566](https://github.com/brashakg/ims-2.0-railway/pull/566) | `e13408a` | **PASS -- 2026-06-08.** Acceptance 9/9 (T1-3, T6-T10 in `test_policy_engine_e2.py`); required CI green (`test 3.10/3.11` full-suite regression, `test-and-build`, `security`); `e2e` = non-required cold-preview flake (PROTOCOL S3). **CORRECTIONS P1 honored:** per-value `_encrypt_value`/`_decrypt_value` (NOT `_encrypt_config`); explicit `cache.delete(key)` per scope (DB-down read never cached -- no scope poisoning); luxury `pricing.category_caps.*` LOWER-only guard (luxury brand caps are not E2 keys); store-missing-entity -> global (never raises); store>entity>global>env>default resolution. Backend smoke OK (993 routes); 0 emoji introduced. **T4 (cost-floor) / T5 (refund-tier) correctly DEFERRED** per PROTOCOL S11 -- they gate the `orders.py` / E4 consumers, NOT E2 (validate when those consumers read E2 in Phase 2). **Tracked follow-up (orchestrator, app-wide, NOT a bounce):** entity-scope policy writes lack an entity-ownership check -- JWT carries no entity binding and ADMIN/ACCOUNTANT are cross-entity by design (mirrors `finance.py`); pre-existing limitation, not E2-introduced; revisit if JWT gains entity binding. |
| #35 | Cost & margin masking | [#569](https://github.com/brashakg/ims-2.0-railway/pull/569) | `be9a8a7` | **PASS -- 2026-06-08.** 7/7 intent tests (`test_cost_mask_f35.py`) + required CI green (`test 3.10/3.11`, `test-and-build`, `security`); `e2e` non-required flake. Server-side strip via pure `cost_mask.py`: COST_VISIBLE={SUPERADMIN,ADMIN,ACCOUNTANT}, CATALOG_MANAGER edit-form only, all below (incl AREA_MANAGER per DECISIONS S9) stripped -- across catalog list/get, analytics non-moving, AND **finance /pnl (G1 fix: endpoint had NO cost gate before)**. mrp/offer_price preserved (regression T12). Per-call-site masking honors the substantive CORRECTIONS #35 (audit sites, drop false `_build_store_ledger` margin claim). Backend smoke OK; 0 emoji; non-POS read-path filter (no flag). **FLAG to orchestrator (precedence reconcile, NOT a bounce):** impl uses `pop()` -> cost field ABSENT, matching packet tests #1/#4; but CORRECTIONS #35 said present-but-NULL. Orchestrator never folded that into the F35 packet, and for a read-path API-response filter absent vs null is functionally identical (FE renders "-" for missing keys; no backend consumer re-reads the masked response) -- security intent fully met either way. If null is truly required, trivial follow-up: change `doc.pop(f,None)` to `doc[f]=None` in cost_mask. |
| #21 | Defective quarantine barcoding | [#573](https://github.com/brashakg/ims-2.0-railway/pull/573) | `95eb3c2` | **PASS -- 2026-06-08.** 19 tests (`test_quarantine_f21.py`) + required CI green (`test 3.10/3.11`, `test-and-build`, `security`; routes catalogued -> `test_no_uncatalogued_routes` passes); `e2e` non-required flake. **Core intent genuinely tested (not a shell):** `test_pos_sell_of_quarantined_only_unit_returns_409` -- end-to-end POS order-create rejected 409 when the product s only unit is QUARANTINED; `find_available` 1->0 after quarantine. CORRECTIONS P0-6 (E3-shim) honored: free-string `QUARANTINED` status (NO enum), excluded from every on-hand/sellable rollup by ALLOWLIST, one `AuditRepository.create` (STOCK_UNIT) per transition, transfers ship-reject 400, vendor_returns RTV-link. PATCH /quarantine + /lift-quarantine + GET /stock/quarantined + POST /labels/quarantine (luxury BRAND-AUTH line); single-doc writes; `stock.quarantined` event fail-soft. Smoke OK (992 routes); 0 emoji; no flag (packet: touches no POS-payment/pricing/order-creation logic, only the additive sellable allowlist). Deviations aligned w/ packet: SerialNumberTracker scoped-out (separate `serial_numbers` collection), period-lock fail-soft = T12 intent. **Hardened by #575 (2026-06-08, test session): RTV cross-store IDOR CLOSED** -- _link_stock_units_to_rtv now store-scopes the query ({stock_id,status:QUARANTINED,store_id}) + validate_store_access(return_data.store_id, current_user) blocks a store-role forging another store s store_id; + dead date-filter / dead RTV UI cleanup. 19/19 quarantine tests still pass; CI green. |
| #34 | Global target ticker | [#576](https://github.com/brashakg/ims-2.0-railway/pull/576) | `5de7876` | **PASS -- 2026-06-08.** 12/12 intent tests (`test_target_ticker_f34.py`) + required CI green (test 3.10/3.11, test-and-build, security; routes catalogued -> test_no_uncatalogued_routes passes); e2e non-required. **T2 server-enforced privacy wall VERIFIED (not FE-only):** `ticker_service._mask_entry` pops _MASKED_KEYS (monthly_target, mtd_revenue, pace_revenue, pace_delta) for floor roles; `raw_visible_for`=False for SALES_CASHIER/SALES_STAFF/CASHIER, True for management; tests assert each rupee key is NOT in the masked payload (floor sees pct_complete + milestones only). T1 no fabricated target (no_target:true), T5 milestone fires once (milestones_fired guard), T10 ticker.refresh_seconds live via E2 cache.delete, T11 fail-soft. Net-new {created_at,status,store_id} orders index (connection.py); ORACLE milestone bell -> FLOOR_NOTIFY_ROLES only. Smoke OK; 0 emoji; no POS/money flag (read-only finance ticker). |
| #40 | VIP churn prediction (read-only) | [#571](https://github.com/brashakg/ims-2.0-railway/pull/571) | `d48e369` | **PASS -- 2026-06-08 (re-validated after bounce).** Bounce cause CONFIRMED-FIXED: 2 `/crm/vip-churn` routes now in rbac_policy.POLICY (GET+POST intervene, SUPERADMIN/ADMIN) -> test_no_uncatalogued_routes green; rebased off the stale base. 10 model tests pass (median-interval intent: VIP=LTV>=1L AND >=3 orders, HIGH=>90d OR >50%% interval, EOD-only scan) + required CI green (test 3.10/3.11, test-and-build, security); e2e non-required. Rework also fixed a P0 (EOD tz crash: aware now_ist vs naive created_at) + a cross-store intervene IDOR (resolve customer store across canonical fields, 403 non-owners). #565-compliant: intervene WINBACK writes a PENDING notification row, never sync-sends. Read-only watchlist; no POS/money/flag. |

## ⛔ BLOCKED
_empty_

---

## BACKLOG (phased plan — corrected by hardening)

### Pre-flight (orchestrator/owner, parallel)
- Merge `claude/fix-money-integrity` + GST FY-serial invoice (DECISIONS §2.5)
- Owner: approve MSG91 DLT utility templates (DECISIONS §6)

### Phase 0 — deferred items (NOT in TODO; packet/scoping pending)
- **#7 AI predictive purchasing — re-tagged `quickwin=NO`, risk=MED.** ORACLE `_propose_reorders` has zero burn-rate logic + stub PO; true scope ~5-day build. Stays BACKLOG until a scoped packet (burn-rate endpoint + dashboard only) is written. (CORRECTIONS)

### Phase 1 — comms tier + item-event/approval engines + foundations
| ID | Name | Dep | Note |
|---|---|---|---|
| E3 | Item-event ledger | — | **IN TEST (#580).** Spine shipped; see CORRECTIONS R2 for split follow-ups. |
| E4 | Approval/PIN + maker-checker | E1 | **IN TEST (#579).** Engine shipped (2 adversarial P1s fixed); FE split per CORRECTIONS R2. |
| E3w | E3 existing-path wiring + return-SERIAL_MISMATCH-409 | E3 | **NEW (CORRECTIONS R2 follow-up):** route F21-quarantine/transfers/GRN/labels/lens/POS-sell through `record_event` + backfill (dual-write window); add the return-side serial-block 409 in `returns.py` (acceptance #8 second half). |
| E4fe | Approval inbox + PIN-modal FE | E4 | **NEW (CORRECTIONS R2 follow-up):** approval-inbox / my-requests / PIN-approve-modal / Settings PIN section — wired by #25/#26/#27. |
| SC | Scorecard + slab-incentive (N2) | E2 | **BUILT (`feat/SC-scorecard`)** — adversarial-verify pending before PR. |
| PM | Unified product master (N5) | — | CORRECTIONS: SKU = rewrite (format-permissive legacy); add `HEARING_AID` enum first; triple-write spine-first + compensation. **HELD until E3 merges (shares product_repository/products spine).** |
| #46 | Configurable reminders | E6-full | Moved from Phase 0 — needs the E6 rule engine, not the OTP slice. |
| N1/#45 | Walkout / lost-sale CRM (30-field + 2-stage FU + FU-Due-Today) | E6 | grounds #45 |
| N3 | Footfall + conversion % (manual) | SC | |
| #41 | Lapsed-patient reactivation | E6 | F41: cost+10% floor NOT yet enforced (correct the packet). |
| #50 | Clinical→retail handover | — | |
| #24 | Optometrist→retail conversion | — | revenue hidden from optometrists |
| #39 | NBA daily call list | E6 | 15/day, 2 VIP slots |
| #8 | PO vs GRN variance / backorder | — | |
| #2 | Internal lab routing (disposable job cards) | E3 | |
| #9 | Lens DC→invoice tally (hardlock) | E3 | |
| #17 | Petty cash controls | E1,E4 | |
| #25 | Maker-checker journal entries | E4 | |
| #26 | Remote approval (discount/leave) | E4 | |
| #6 | Luxury serial tracking (opt-in per SKU) | E3,PM (Wave 3) | **BACKLOG — do NOT promote in Phase 1; unblocks only after E3+PM.** |

### Phase 2 — cash & tender money core (after money-integrity merge; **POS capture UNCHANGED — reconciliation reads existing `order.payments[]`**)
| ID | Name | Dep | Note |
|---|---|---|---|
| E5 | Tender-routing + reconciliation (existing payments) | E1 | back-port `AuditRepository.create` |
| **NEW** | Enforce cost+10%/category price floor on sell-path (`orders.py:1335`) via `pricing.cost_floor_pct` (E2) | E2 | DECISIONS §9 — currently cost+0% only |
| #23 | Blind EOD cash tally & Z-read (transparent + soft-lock) | E5 | |
| #16 | Bank/cash/POS reconciliation (ROI=5) | E5,#23 | |
| #27 | Refund approval matrix + original tender | E4,E5 | |
| N4 | Vendor RMA / credit-note (lens + Luxottica + Zeiss, courier) | E4 | |
| #20 | RTV debit note (lock ALL vendor bills) | N4,E5 | |
| #14 | Non-adapt tracking + vendor credit | N4 | |

### Phase 3 — margin/loss finishers + config CRM + Excel stock
#15 (blind stock takes, transparent+soft-lock) · N6 (Base-Bank replenishment) · #1 (inventory balancing, all brands 90-day) · #38 (endless-aisle + courier N9) · #13 · #18 · #19 · #43 · #47 · N7 (CL purchase-order generator) · #48 · #49 (family wallet, max 7, OTP redeem) · #44 · #33 · #51 · #52 · N8 (owner survival cash-flow) · #28 (needs static-IP data) · #29

### Phase 4 — AOV-lift promos + heavy CRM (POS, flag-gated)
#11 (exclusive promos) · N10 (2nd-pair-50%) · #12 (bundling) · #42 (lookbooks) · **#30/#31 (SPIFF/clawback — via SC engine ONLY; F30/F31 OLD-model plumbing is SUPERSEDED, see CORRECTIONS P0-3/P0-4)** · #32 (own-use, tiered+family)

### Phase 5 — omnichannel & B2B scale (owner-gated)
#36 (Shopify brand hardlock) · #3 (consignment) · #4 (parts) · #5 (cannibalization) · #10 (ageing auto-liquidation) · #22 (split-tender ledger routing) · #37 (B2B bulk)

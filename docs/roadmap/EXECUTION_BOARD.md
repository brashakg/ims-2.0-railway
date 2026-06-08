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
| #34 | Global target ticker | E2 | features/F34.md | Add `{created_at,status,store_id}` orders index (net-new) + cache. SALES sees % only. |
| E6 | Reminder/segment rail (OTP+cap slice) | E2 | features/E6.md | `fu_due_today` needs channel/`/due-today` reconcile; freq-cap = **soft-ceiling**; OTP path short-circuits consent/quiet-hours first. |

_Build order is dependency-aware: #34/E6 after E2 merges to main. (E1 DONE; E2/#35/#40/#21 IN TEST.)_

## 🔨 IN BUILD
_empty_

## 🧪 IN TEST

| # | Name | PR | Branch | Notes |
|---|---|---|---|---|
| #35 | Cost & margin masking | [#569](https://github.com/brashakg/ims-2.0-railway/pull/569) | `feat/F35-cost-mask` | `cost_mask.py` (pure) strips cost/margin server-side: catalog list/get/create/update, analytics non-moving, **finance /pnl (G1 fix — endpoint had NO role gate)**. COST_VISIBLE = SUPERADMIN/ADMIN/ACCOUNTANT; CATALOG_MANAGER only on edit form; AREA_MANAGER+ below never (DECISIONS §9). FE CostCell/MarginCell + AddProduct guard + MultiStore/Reorder cells. 7 intent tests (incl AREA_MANAGER excluded, nested pricing, P&L strip-set); tsc/vite clean; E/F pylint clean. Tracked cosmetic follow-up: ReportsPage/InventoryValuation/FinanceDashboard cell-wraps (backend already strips the data they read). |
| #40 | VIP churn prediction (read-only) | [#571](https://github.com/brashakg/ims-2.0-railway/pull/571) | `feat/F40-vip-churn` | Pure `vip_churn.py` (median-interval model; HIGH = >90d overdue OR >50% of interval; VIP = LTV>=1L AND >=3 orders). ORACLE EOD `_scan_vip_churn` (22:00 is_eod): per-VIP `vip_churn_risk` subdoc + one per-store daily `vip_churn_snapshots` + top-10 Claude (capped, fail-soft) + HIGH anomalies emitted. `GET /crm/vip-churn` (watchlist+trend; ADMIN store-scoped; fail-soft) + `POST /crm/vip-churn/{id}/intervene` (P1 task deduped 30d; audit; WINBACK→PENDING notification row, never sync-send). FE watchlist + Customer360 card + shared modal + nav (SUPERADMIN/ADMIN). 9 tests (T1-5/11/12); tsc/vite clean; E/F pylint clean. No POS/money/flag. |
| #21 | Defective quarantine barcoding | [#573](https://github.com/brashakg/ims-2.0-railway/pull/573) | `feat/F21-quarantine` | `QUARANTINED` free-string status (no enum); excluded from every on-hand/sellable rollup by allowlist (test: quarantine the only AVAILABLE unit → POS sell **409**). `PATCH /inventory/stock/{id}/quarantine` + `/lift-quarantine` + `GET /stock/quarantined` (queue + unlabeled_count); `POST /labels/quarantine/{id}` (red DO-NOT-SHELVE + luxury brand-auth line); transfers ship-move rejects QUARANTINED (400); vendor_returns `stock_ids` RTV-link. Audit via `AuditRepository.create` (STOCK_UNIT); single-doc writes; `stock.quarantined` event. FE Quarantine Queue tab + modal + red chip. 19 tests + regression 77/15 + rbac-matrix 412; smoke 992; E/F pylint 10/10; tsc/vite clean. Deviations: SerialNumberTracker skipped (separate `serial_numbers` collection); period-lock fail-soft. No POS/money/flag. |

## ✅ DONE

| # | Name | PR | Merged | Test-session verdict |
|---|---|---|---|---|
| E1 | Money-guard service | [#563](https://github.com/brashakg/ims-2.0-railway/pull/563) | `a8c6945` | **PASS — 2026-06-07.** Acceptance T1-T13 PASS (15/15 in `test_money_guard_e1.py`) + `test_money_integrity_guards.py` 25/25 unchanged (T6). Required CI green (`test 3.10/3.11`, `test-and-build`, `security`); `e2e` = known cold-preview flake, non-required per PROTOCOL §3. **P0-1 honored:** facade only — NO `money_accounts` SoR / index / migration; the 3 new types return `reason="unavailable"` (T13 deferral correct; orchestrator to reconcile packet T13 / item-6 / DoD-5 wording). Single-doc atomic balance guard (floor/status/expiry in the filter; loser matches nothing; `_classify_debit_failure` read-only), shims behavior-preserving (T10/T11/T12), audit via `AuditRepository.create`, fail-soft `unavailable`/fail-closed `no_atomic`, 0 emoji introduced (flagged em-dashes pre-existing), non-POS (no flag). **HARDENING FOLLOW-UP (orchestrator — fix BEFORE any idempotent direct caller, e.g. E5 returns/refund, wires onto E1):** the idempotency marker is pushed by `_append_ledger` in a SEPARATE `update_one` AFTER the balance `find_one_and_update`, so the `money_ledger.idempotency_key != key` filter is NOT truly atomic — two concurrent same-key calls can both pass the filter and double-apply. Sequential retries (T4/T4b) are safe and no Phase-A caller passes `idempotency_key`, so it is unreachable today. Fix: `$push money_ledger` the marker INSIDE the balance `find_one_and_update` (same atomic write) and correct the over-claiming "race-safe" comment at `money_guard.py:303-304`. |
| E2 | Settings-matrix engine | [#566](https://github.com/brashakg/ims-2.0-railway/pull/566) | `e13408a` | **PASS -- 2026-06-08.** Acceptance 9/9 (T1-3, T6-T10 in `test_policy_engine_e2.py`); required CI green (`test 3.10/3.11` full-suite regression, `test-and-build`, `security`); `e2e` = non-required cold-preview flake (PROTOCOL S3). **CORRECTIONS P1 honored:** per-value `_encrypt_value`/`_decrypt_value` (NOT `_encrypt_config`); explicit `cache.delete(key)` per scope (DB-down read never cached -- no scope poisoning); luxury `pricing.category_caps.*` LOWER-only guard (luxury brand caps are not E2 keys); store-missing-entity -> global (never raises); store>entity>global>env>default resolution. Backend smoke OK (993 routes); 0 emoji introduced. **T4 (cost-floor) / T5 (refund-tier) correctly DEFERRED** per PROTOCOL S11 -- they gate the `orders.py` / E4 consumers, NOT E2 (validate when those consumers read E2 in Phase 2). **Tracked follow-up (orchestrator, app-wide, NOT a bounce):** entity-scope policy writes lack an entity-ownership check -- JWT carries no entity binding and ADMIN/ACCOUNTANT are cross-entity by design (mirrors `finance.py`); pre-existing limitation, not E2-introduced; revisit if JWT gains entity binding. |

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
| E3 | Item-event ledger | — | CORRECTIONS: drop unit hash-chain; reconcile existing `serial_numbers`; quarantine/blind-count NET-NEW. |
| E4 | Approval/PIN + maker-checker | E1 | CORRECTIONS: atomic `approve()`; PIN brute-force throttle + test. |
| PM | Unified product master (N5) | — | CORRECTIONS: SKU = rewrite (format-permissive legacy); add `HEARING_AID` enum first; triple-write spine-first + compensation. |
| SC | Scorecard + slab-incentive (N2) | E2 | CORRECTIONS: replaces `_fetch_incentive` (no double-count); fix multiplier example 1.1@14%. |
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

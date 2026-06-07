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
| E1 | Money-guard service | — | features/E1.md | **P0-1: PHASE-A FACADE ONLY** over existing vouchers/loyalty/store-credit. NO `money_accounts` SoR, NO migration, NO dual-write. |
| E2 | Settings-matrix engine | — | features/E2.md | P1: secret per-key encrypt (`_encrypt_value`); invalidate via explicit `cache.delete`; **luxury caps LOWER-only, never E2 keys**; entity-missing → global. |
| #35 | Cost & margin masking | — | features/F35.md | Drop the false `_build_store_ledger` margin claim; per-call-site check. Test: SALES_CASHIER sees `cost_*`=null; ACCOUNTANT real. |
| #40 | VIP churn prediction (read-only) | — | features/F40.md | Clean (not a quick-win, ~M). SUPERADMIN/ADMIN only. |
| #34 | Global target ticker | E2 | features/F34.md | Add `{created_at,status,store_id}` orders index (net-new) + cache. SALES sees % only. |
| #21 | Defective quarantine barcoding | E3-shim (ENGINES.md) | features/F21.md | Status = free string (no enum); exclude QUARANTINED from ALL on-hand rollups; intent test: sell quarantined unit → 409. |
| E6 | Reminder/segment rail (OTP+cap slice) | E2 | features/E6.md | `fu_due_today` needs channel/`/due-today` reconcile; freq-cap = **soft-ceiling**; OTP path short-circuits consent/quiet-hours first. |

_Build order is dependency-aware: E1/E2/#35/#40 have no deps (parallelizable on separate branches); #34/E6 after E2; #21 after its E3-shim._

## 🔨 IN BUILD
_empty_

## 🧪 IN TEST
_empty_

## ✅ DONE
_empty_

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

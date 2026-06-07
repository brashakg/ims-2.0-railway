# IMS 2.0 Shared-Engine Architecture (ENGINES.md)

> Authoritative engine contracts. Features MUST call these, never reimplement (PROTOCOL.md s6).

> Per-engine detail also under `engines/`.

---

> ⚠️ **`CORRECTIONS.md` overrides specific clauses below** (hardening pass). Most important: **E1 unified `money_accounts` SoR + cross-collection dual-write is DO-NOT-BUILD** (standalone Mongo, no transactions) — E1 ships Phase-A facade only. **E3 unit-level hash-chain is dropped.** Back-port `AuditRepository.create`/`get_policy` names. Read `CORRECTIONS.md` for an item before building it.

## E3-shim contract (minimal, for #21 in Phase 0)

A tiny precursor to the full E3 item-event ledger, enough to ship Defective Quarantine (#21) without the full engine:
- **Status transition:** `stock_units.status` toggles `AVAILABLE <-> QUARANTINED` (free string value; **no enum/schema change**).
- **Audit:** one `AuditRepository.create` row per transition (actor, reason, unit, store). No hash-chain, no new collection.
- **Guard:** a quarantined unit is rejected by inter-store transfer (`transfers`), and **excluded from every on-hand rollup** — `product_repository.find_available` plus the rollups at `inventory.py` ~107 / 2147 / 2274 / 2366 / 2731. Enumerate and patch each; a missed rollup keeps the unit sellable.
- **Lift:** `QUARANTINED -> AVAILABLE` requires the E4 approval (or, pre-E4, a STORE_MANAGER+ action that writes an audit row).
- The full E3 ledger (Phase 1) supersedes this; #21's shim work folds into it.

## Phase <-> wave mapping (board phase vs engine wave)

Board phases are sequencing; engine "waves" are build order. Mismatches to respect: **#6** (board Phase 1) actually unblocks only after **E3 + PM (Wave 3)** — keep in BACKLOG, don't promote in Phase 1. **#46** needs the **E6 full rule engine** (not the Phase-0 OTP/cap slice) -> Phase 1. **#21** needs the **E3-shim** above (Phase 0).

---

# Build Plan (chair synthesis)

All citations in the specs check out against the real tree. I have what I need to synthesize.

# Shared-Engine Architecture — Build Plan

The 8 contracts are internally strong but were designed in isolation. As chief architect I'm fixing the seams: they over-promise on each other's interfaces (E1/E4/E6 each independently invent OTP + tier-resolution + audit-append), and three of them re-derive E2 instead of consuming it. Below is the unified program.

## Build order

**Why this order:** Phase-0 engines (E1/E2/E6) are cheap, wide, and unblock the quick-win batch; everything money/approval/comms-adjacent depends on them. PM is the foundation for every catalog/inventory/pricing feature. SC is a near-complete extraction that depends on nothing but E2, so it runs as its own track in parallel.

| Wave | Engine(s) | Gating reason |
|---|---|---|
| **0a (foundation, parallel)** | **E2 Settings-matrix**, **E1 Money-guard Phase A** | E2 is a dependency of E1, E4, E5, E6, SC and is the *single biggest source of cross-engine inconsistency*. It must land first so no other engine ships a `_DEFAULT_TIERS`/`_DEFAULT_*` constant that later has to be ripped out. E1 Phase A (facade over the 3 existing balances, **no new collection, no migration**) is the cheapest possible money slice and unblocks every refund/redeem quick win. E1 Phase A can start same-day as E2 because Phase A doesn't read E2 (caller enforces tiers). |
| **0b** | **E6 reminder rail (transactional/OTP slice only)** | E6's OTP two-phase is the *only* dependency E1's family-wallet and E4 share. Ship E6's `send_pool_redemption_otp`/`verify_pool_redemption_otp` + `comms_ledger` first; defer the marketing rule engine. This makes OTP one implementation, not three (see Conflicts §1). |
| **1** | **E1 family-wallet + reserve/commit**, **E4 Approval/PIN** | E1's OTP flow now *calls E6* instead of re-implementing `send_whatsapp`. E4 reads E2 tier values (E2 already live) and uses E1's atomic `consume` pattern. Both are now thin. |
| **2** | **PM Unified Product Master** | The foundation for catalog/inventory/pricing/Shopify features. Depends only on E2 (price-floor/promo-ceiling overrides) — can technically start in Wave 1, but its 10-day cost + POS-read sensitivity means it gets a clean wave. |
| **2 (parallel track)** | **SC Scorecard** | Pure extraction; depends only on E2 (settings hierarchy) and Payroll (already accepts `incentive=`). No dependency on E1/E3/E4/PM. Runs alongside PM on a separate dev. |
| **3** | **E3 Item-event ledger**, **E5 Tender-routing**, **E6 full marketing engine** | E3 is the heaviest (12 d) and touches the POS sell-path — last, feature-flagged. E5 consumes E1 ledger rows + E2 ledger-map + E4 maker-checker, so all three deps must be live. E6's full rule engine consumes E2 (per-scope rules) + vouchers + segments. |

## Cross-engine contracts

I made these consistent and flagged every mismatch in the specs.

| Contract | Consumer → Provider | Resolution / mismatch fixed |
|---|---|---|
| **Settings resolution** | E1, E4, E5, E6, SC, PM → **E2** | **MISMATCH FOUND.** E2 publishes `get_policy(key, scope)` / `get_policies(...)`. But E4's spec calls it `resolve_setting(key, store_id, entity_id)` and SC's spec calls it `resolve_settings(store_id, ...)`. **Locked single signature: `get_policy(key, scope={"store_id":..}|{"entity_id":..}|{})`.** All other engines adapt to this; the `resolve_setting` name in E4/E5/SC is dead. (Code-grep confirms neither name exists yet — no back-compat cost.) |
| **OTP (family-wallet pool redeem)** | E1 → **E6** | **MISMATCH FOUND — three OTP implementations.** E1 §`request_pool_redeem` calls `providers.send_whatsapp` directly with `MSG91_OTP_TEMPLATE_ID`; E6 §`send_pool_redemption_otp` owns a `pool_otp` collection + atomic verify; E4 mentions PIN (separate, fine). **Locked: E6 owns OTP end-to-end** (`pool_otp` collection, hash/TTL/5-attempt, `send_notification` path, transactional bypass of consent/quiet-hours/cap). E1's `request_pool_redeem` = `reserve()` then `E6.send_pool_redemption_otp(...)`; `confirm_pool_redeem` = `E6.verify_pool_redemption_otp(...)` then `commit()`. E1 does **not** store `otp_hash` on the hold (its data-model `holds[].otp_hash/otp_expires_at/otp_attempts` fields are **removed** — that state lives in E6's `pool_otp`). The hold just carries `otp_id`. |
| **Atomic balance mutation** | E5, SC (payroll feed), returns, loyalty, vouchers → **E1** | E1 is the single guarded-`find_one_and_update` owner. E5 reads E1 `ledger[]` rows (typed CREDIT/DEBIT) as its reconciliation source — **does not mutate balances**. SC's payroll-feed reads a *locked snapshot*, not E1. Consistent. |
| **Approval / PIN gate** | E5 (`tender-ledger-map` PUT, `/lock`), returns (refund tiers), orders (discount override), serial-mismatch → **E4** | E4 routes/records; the *threshold values* come from **E2** (`refund.tier.*`). **MISMATCH FOUND:** E4 hardcodes `_DEFAULT_TIERS`; E2 owns `refund.tier.auto_below/admin_above/super_above`. **Locked: E4 reads E2; `_DEFAULT_TIERS` exists only as the in-code fallback E2 returns when DB+env are empty** (E2's registry default IS the fallback — so E4 should *not* carry its own constant; it calls `get_policies([...])`). |
| **Audit append** | ALL → `audit_chain.append_audit_entry` (hash-chained) or `AuditRepository.create` | **MISMATCH FOUND — two audit sinks named inconsistently.** E1/E4 cite `AuditRepository.create`; E3/E5/E6/SC cite `audit_chain.append_audit_entry`. Grep confirms `AuditRepository.create` (audit_repository.py:35) internally is the public façade and `append_audit_entry` (audit_chain.py:218) is the hash-chaining primitive. **Locked: every engine calls `AuditRepository.create(...)`** (the façade), which chains internally. No engine calls `append_audit_entry` directly. One sink, one collection `audit_logs`. |
| **Event bus** | E1 (`money.balance.low`), E3 (`quarantine.in`, `audit.flag`), E5 (`cash.variance.over_threshold`), PM (`product.created`) → `registry.dispatch_event` | Consistent; all fail-soft to in-process when `REDIS_URL` unset. No mismatch. |
| **Comms frequency cap** | E1 OTP, E4 approval-notify (internal staff) → **exempt**; E6 marketing → **capped** | **MISMATCH RISK FOUND.** E4 notifies approvers via `send_notification`; that must NOT count against the 3-msg/30-day customer cap (staff ≠ customer). E6's `record_outbound` writes `comms_ledger` only for *customer marketing*. Locked: OTP (`category=OTP`) and approval (`category=SERVICE`, staff recipient) bypass `check_frequency_cap`. |
| **Store→entity scope inference** | E2 (resolves `store_id`→`entity_id` via stores coll), reused by E1/E3/E4/E5/E6/SC | One resolver in E2. E3's `base_bank_targets` and SC's `incentive_settings` scope chains MUST call E2's resolver, not re-walk stores. Flagged in their specs as independent — **unified into E2**. |

## Dependency map (engine → features it unblocks)

- **E2** → unblocks the *configurability* of: refund/discount tiers (#refunds, #returns, POS discount-override), cash-variance thresholds (#cash-close), liquidation floor + promo ceiling (#clearance, #pricing), reminder/ageing/comms windows (#MEGAPHONE, #finance-ageing), loyalty pool size, serial-mismatch hard-block toggle. Every other engine's tunables.
- **E1** → loyalty redeem/earn/expire, gift-voucher redeem, store-credit issue/redeem, refund-to-original-tender, family loyalty wallet, petty-cash float + cash-variance source, consignment, EMI/credit-note balances. Source data for **E5**.
- **E6** → Rx-expiry, birthday, win-back, CL-reorder, churn alert, lookbook, NPS/feedback, FU-Due-Today, **and the family-wallet OTP that E1 depends on**.
- **E4** → discount override, refunds, journal-entry maker-checker (gates **E5** posting), profile merge, petty cash, endless aisle, RTV, serial-mismatch unblock.
- **PM** → #6 serial tracking (reads `serialized` flag → feeds **E3**), #10 ageing/non-moving, #36 Shopify sync (NEXUS), stock grids (Power/Rapid), pricing-floor enforcement, AddProduct wizard, Catalog Autopilot.
- **E3** → INV-12 barcode trace, transfers in-transit truth, blind-count under-audit, quarantine/RTV, Base-Bank replenishment + auto-reorder (TASKMASTER), lens DC-in, POS oversell-safe sell. Source data for **E5** (SELL/VOID/TRANSFER JV).
- **E5** → Tally sales-JV with correct tender legs, daily cash-register close by-mode, cash-variance (consumes E1+E2), finance dashboard by-tender, NEXUS Razorpay reconciliation.
- **SC** → daily scorecard, MTD leaderboard, slab payout, manager bonus, product-incentive kicker, **payroll incentive feed** (the one money seam → Payroll).

**Critical path:** E2 → E1(A) → E6(OTP) → {E4, E1-family} → E5. **Off-critical parallel:** PM and SC.

## Naming + conventions

So the build session is consistent across all 8 engines:

**Service modules** (all under `backend/api/services/`):
`policy_engine.py` + `policy_registry.py` (E2) · `money_guard.py` (E1) · `reminder_rail.py` (E6) · `approvals.py` (E4) · `tender_routing.py` + `tender_reconciliation.py` (E5) · `product_master.py` (PM) · `scorecard_engine.py` (SC). E3: `item_events.py`.

**Routers** (under `backend/api/routers/`): `approvals.py` (`/api/v1/approvals`), `reminders.py` (`/api/v1/reminders`), `item_events.py` (`/api/v1/items`), `product_master.py` (`/api/v1/products`). E2 mounts on the **existing** `settings.py` at `/api/v1/settings/policies`. E1 exposes **no router** (engine only). E5 extends **existing** `finance.py`. SC extends **existing** `points.py`/`payout.py`.

**New collections:** `policy_settings` (E2) · `money_accounts` (E1, Phase B) · `approval_requests` (E4) · `reminder_rules` + `comms_ledger` + `pool_otp` + `reminder_audit` (E6) · `item_events` + `base_bank_targets` (E3) · `payment_reconciliations` + `tender_ledger_map` (E5) · `product_category_specs` (PM) · `product_incentive_log` (SC). **Reused:** `audit_logs`, `counters`, `users`, `stores`, `entities`, `products`, `catalog_products`/`catalog_variants`, `vouchers`, `loyalty_accounts`, `customers`, `points_log`, `payout_snapshots`, `incentive_settings`, `notification_logs`.

**Settings-key namespace** (dotted, lowercase, E2 registry): `refund.tier.{auto_below,admin_above,super_above}` · `approval.pin_validity_min` · `cash.variance.{warn,block,frequency}` · `liquidation.floor_pct_over_cost` · `promo.ceiling_pct` · `reminder.{rx_expiry_days,...}` · `comms.cap_per_customer_30d` · `loyalty.{pool_max_members,pool_redeem_requires_otp}` · `serial.return_mismatch_hard_block` · `ageing.{ar_buckets,overdue_days}` · `tally.ledger_map` · `tender.ledger_map.{TENDER}` · `incentive.eligibility_bands` · `pricing.cost_floor_pct` · `pricing.category_caps.{MASS,PREMIUM,...}`. **Money values stored paisa-int** (`200000` = ₹2000.00). Standard tier defaults: `auto<₹500 / admin>₹2000 / super>₹10000`; cash variance `0/100/500`.

**Result/contract conventions:** money/atomic verbs return a `GuardResult`-style object, **never raise on business failure**; machine-code `reason` strings are shared vocabulary (`insufficient|not_found|inactive|expired|unavailable|lost_race|otp_required|otp_invalid|duplicate|no_atomic`). **Idempotency key** on every mutation that can be retried. **Fail-soft on `db=None`** (return empty/`unavailable`, raise nothing). **Fail-closed on `no_atomic`** for debits. IST timestamps, FY Apr–Mar, no emojis in Python.

## Conflicts found across the specs + the resolution

1. **OTP implemented three times** (E1 inline `send_whatsapp`, E6 `pool_otp` collection, E4 PIN-adjacent). → **E6 owns OTP**; E1 delegates; E1's `holds[].otp_*` fields deleted in favor of E6's `pool_otp`. (See Cross-engine contracts.)
2. **Settings resolver named three ways** (`get_policy` / `resolve_setting` / `resolve_settings`). → **`get_policy(key, scope)` is canonical**; E4/E5/SC adapt.
3. **Tier defaults duplicated** (E4 `_DEFAULT_TIERS` vs E2 `refund.tier.*`). → **E2 registry default is the single fallback**; E4 carries no constant.
4. **Audit sink named two ways** (`AuditRepository.create` vs `append_audit_entry`). → **All engines call `AuditRepository.create`** (the façade chains internally).
5. **E1 vs E2 vs returns ownership of refund-tier enforcement.** E1 says "caller enforces tiers"; E4 says "E4 routes"; E2 says "E2 stores numbers." → **Clean split: E2 stores the number, E4 routes/records the approval, E1 enforces only the hard floor + atomicity, the caller (returns/orders) orchestrates.** No engine owns more than one of these.
6. **E5 `STORE_CREDIT` as a tender** is in E5's map but is **not** a `PaymentMethod` enum value and store-credit redemption has no tender row today. → **E1 owns the store-credit balance; E5 includes `STORE_CREDIT` in the map for forward-compat but does NOT wire it as a capture tender now** (that would touch POS capture, which is LOCKED-skipped). Flag to chair only if store-credit-at-POS is wanted now.
7. **Scope chain re-walked independently** by E3 (`base_bank_targets`), SC (`incentive_settings`), E5 (`tender_ledger_map`), E6 (`reminder_rules`). → **All four call E2's `get_policy`/scope resolver**; their per-engine "STORE→ENTITY→GLOBAL" prose is implemented once in E2. (E3/E5/E6 store their *override docs* in their own collections but resolve precedence via E2's helper to avoid four subtly-different merge orders.)
8. **E1 Phase B unique-index build** collides with the known prod dup/null-key blocker (memory `prod_data_blockers`). → **E1 Phase A ships with zero new collection/index**; Phase B's `money_accounts` unique index is gated behind the prod dedupe pre-clean. Same caution applies to PM's new `products` indexes and E3's `serial` unique-partial — **all new unique indexes built after a dedupe pass, never on first deploy.**
9. **E6 legacy-scan cutover double-send risk** (removing MEGAPHONE rx/birthday scans while seeding inactive rules). → Same-PR removal + `active=False` seeds + `comms_ledger` 30-day backfill. Owner opts each rule on. (Deferred to Wave 3; the OTP slice in Wave 0b does not touch MEGAPHONE scans.)
10. **PM two-surface vs single-collection** + **category enum mismatch** (`FRAME` vs `FR`). → Keep two linked collections (`products` spine + `catalog_products` PIM); canonicalize `products.category`=long name, `sku_prefix`=short code; engine translates. SKU = pure concat, `-{counter}` **only on collision**. Backfill never re-mints live SKUs.

## Phase-0 engine cut (minimal slice the quick-win batch needs first)

The smallest shippable layer that unblocks the quick-win features without committing to any migration:

**E2 — minimal:** `policy_registry.py` seeded with the ~12 quick-win keys (`refund.tier.*`, `cash.variance.*`, `liquidation.floor_pct_over_cost`, `promo.ceiling_pct`, `comms.cap_per_customer_30d`, `loyalty.pool_max_members`, `serial.return_mismatch_hard_block`) + `policy_engine.get_policy/get_policies/set_policy` with the global→entity→store resolver, cache, and audit-on-write. **Defer:** the generic FE `PolicySchemaForm` (ship a minimal Settings tab), facading the 15 legacy singletons, `pricing_caps` scope hook. **~3 dev-days.**

**E1 — Phase A only:** `money_guard.py` with `_apply_atomic`, `GuardResult`, `credit`/`debit`/`get_balance`, the audit hook, and the **3 adapter shims** so `redeem_voucher_atomic`/`try_debit`/`try_debit_store_credit` route through E1 against the *existing* collections. **No `money_accounts` collection, no migration, no index.** **Defer:** family-wallet OTP, reserve/commit, petty-cash/consignment, Phase B backfill. **~3 dev-days.**

**E6 — transactional slice only:** `pool_otp` collection + `send_pool_redemption_otp`/`verify_pool_redemption_otp` (atomic consume, 5-attempt, hash/TTL) on the existing `send_notification` path, plus `comms_ledger` + `check_frequency_cap`/`record_outbound` for the 3/30-day cap. **Defer:** `reminder_rules` engine, 3 new segments, the router, MEGAPHONE cutover. **~3 dev-days.**

**Total Phase-0 ≈ 9 dev-days**, parallelizable across 2–3 devs → **~4 calendar days**, after which the quick-win batch (configurable refund tiers, cash-variance, promo ceiling, atomic loyalty/voucher/store-credit redeem, family-wallet OTP redemption) is fully unblocked with zero data migration and zero POS-capture changes.

Architecture file references (all verified present): `backend/api/services/integration_config.py`, `pricing_caps.py`, `cache.py`, `store_credit_ledger.py`, `role_caps.py`; `backend/api/routers/settings.py`, `vouchers.py`, `loyalty.py`, `returns.py`, `finance.py`, `orders.py`, `points.py`, `payout.py`, `catalog.py`, `products.py`; `backend/database/repositories/audit_repository.py` (`.create` @ line 35), `audit_chain.py` (`append_audit_entry` @ line 218), `loyalty_repository.py` (`try_debit` @ 101), `customer_repository.py` (`try_debit_store_credit` @ 110); `backend/agents/providers.py`, `proposals.py`. New modules to create: `policy_engine.py`+`policy_registry.py`, `money_guard.py`, `reminder_rail.py`, `approvals.py`, `tender_routing.py`+`tender_reconciliation.py`, `product_master.py`, `scorecard_engine.py`, `item_events.py`.


---

# Engine contracts



## ===== E1: Money-guard (concurrency-safe balance mutation) =====
I now have everything I need. I have grounded all five existing atomic patterns (voucher redeem, loyalty try_debit/adjust_balance, returns returned_qty reservation, store-credit try_debit_store_credit + store_credit_ledger pure helpers), the audit chain, the MSG91 send_whatsapp for OTP, and the family/household schema state. Here is the buildable contract.

# Engine E1: Money-guard (concurrency-safe balance mutation)

## Reuse (existing files/functions to build on — real paths)

These five live primitives ALL implement the identical guard-in-the-filter idiom. E1 generalises them into one service; the existing call sites are then refactored to call E1 (or E1 wraps them), eliminating five copies of the same logic.

- **`backend/api/routers/vouchers.py` → `redeem_voucher_atomic(db, code, amount, order_id, redeemed_by)`** (lines 170–261). The canonical pattern: a single `find_one_and_update` whose filter encodes the spend guard (`status ACTIVE`, `balance >= amount`, `expiry >= today`), `$inc balance -amount`, `$push redemptions`, then a guarded status-flip when drained. Returns `{ok, balance, status, reason}`, never raises. **This is the reference implementation E1 mirrors.**
- **`backend/database/repositories/loyalty_repository.py` → `try_debit(...)` (lines 101–159) and `adjust_balance(...)` (lines 62–99).** `try_debit` is the guarded debit (`filter: balance_points >= points`, `$inc -points`, `return_document=AFTER`); returns post-doc / `None` (insufficient) / `None` (no-atomic). `adjust_balance` is the unconditional credit (`$inc`). These map to E1's `debit` and `credit`.
- **`backend/database/repositories/customer_repository.py` → `try_debit_store_credit(customer_id, amount)` (lines 110–152)** + `add_store_credit` (95–103). Uses a conditional `update_one({store_credit: {$gte: amt}}, {$inc: -amt})`, returns post-doc / `None` / `DEBIT_NO_ATOMIC` sentinel. E1 absorbs `store_credit` as a ledger.
- **`backend/api/services/store_credit_ledger.py` → `make_entry(...)`, `compute_balance(...)`** (whole file). Pure, paisa-rounded signed-entry builder (ISSUED/REDEEMED/ADJUSTED + `balance_after`). E1 adopts this ledger-row shape and validation as its audit/ledger sub-document.
- **`backend/api/routers/returns.py` → `_claim_returnable_qty(...)` (371–438) / `_release_returnable_qty(...)` (441–467).** The reserve/release pair (atomic positional `$inc` on an array element guarded by remaining-cap). E1's `reserve`/`release` verbs generalise this for hold-then-commit flows.
- **`backend/database/repositories/audit_repository.py` → `AuditRepository.create(...)` (line 35)** — the hash-chained, fail-soft audit append every money mutation must emit.
- **`backend/agents/providers.py` → `send_whatsapp(phone, message, template_id=...)` (line 125)** + `_normalize_phone`, `DISPATCH_MODE` gate — used by E1 to send the family-wallet pool-redemption OTP.
- **`backend/database/connection.py` → `get_db()`** and the house `_get_db()` / `_coll()` accessors (vouchers.py 77–96) — the DB-absent fail-soft contract E1 inherits.

## Public API (functions and/or endpoints with signatures)

New file `backend/api/services/money_guard.py`. Pure service layer (no FastAPI). One guarded primitive + thin verbs. **Never raises on business failure** — returns a result object.

```python
# ---- Result type ----
@dataclass
class GuardResult:
    ok: bool
    balance: float            # post-op balance (or pre-op on failure), paisa-exact
    txn_id: Optional[str]     # ledger row id written on success
    reason: Optional[str]     # machine code on failure: "insufficient" | "not_found"
                              #   | "inactive" | "expired" | "unavailable"
                              #   | "lost_race" | "below_floor" | "otp_required"
                              #   | "otp_invalid" | "duplicate" | "no_atomic"
    status: Optional[str] = None   # account status after op (ACTIVE/DRAINED/...)
    detail: Optional[dict] = None  # human message + amounts for the API layer

# ---- Account-type registry (one place defines each ledger) ----
ACCOUNT_TYPES = {
    "LOYALTY":       AccountSpec(coll="money_accounts", unit="points", key="account_key", floor=0, integer=True),
    "STORE_CREDIT":  AccountSpec(coll="money_accounts", unit="INR",    key="account_key", floor=0),
    "GIFT_VOUCHER":  AccountSpec(coll="money_accounts", unit="INR",    key="account_key", floor=0, expiry=True),
    "PETTY_CASH":    AccountSpec(coll="money_accounts", unit="INR",    key="account_key", floor=None),  # may go negative? no -> 0, but variance-tracked
    "FAMILY_WALLET": AccountSpec(coll="money_accounts", unit="points", key="account_key", floor=0, integer=True, otp_redeem=True),
    "CONSIGNMENT":   AccountSpec(coll="money_accounts", unit="INR",    key="account_key", floor=None),
}

# ---- THE single guarded primitive (everything else calls this) ----
def _apply_atomic(db, account_type, account_key, delta, *, guard_extra=None,
                  ledger_row=None, expect_status=None) -> Optional[dict]:
    """ONE find_one_and_update. Filter = {account_type, account_key,
    status ACTIVE, balance + floor guard, expiry guard, *guard_extra}.
    Update = {$inc balance: delta, $push ledger: ledger_row, $set updated_at}.
    Returns post-doc or None (no match). Mirrors redeem_voucher_atomic exactly.
    Fail-soft None when coll lacks find_one_and_update (caller -> no_atomic)."""

# ---- Public verbs (thin wrappers over _apply_atomic) ----
def credit(db, account_type, account_key, amount, *, reason, actor, ref=None,
           store_id=None, entity_id=None, idempotency_key=None) -> GuardResult
def debit (db, account_type, account_key, amount, *, reason, actor, ref=None,
           store_id=None, entity_id=None, idempotency_key=None,
           floor_override=False) -> GuardResult
def reserve(db, account_type, account_key, amount, *, hold_id, reason, actor,
            ttl_seconds=3600, ref=None) -> GuardResult      # debit into a named hold
def release(db, account_type, account_key, *, hold_id, actor) -> GuardResult
def commit (db, account_type, account_key, *, hold_id, actor, ref=None) -> GuardResult
def get_balance(db, account_type, account_key) -> dict       # {balance, status, holds:[...]}

# ---- Family-wallet OTP-gated pool redemption (two-phase) ----
def request_pool_redeem(db, household_id, amount, *, requested_by, store_id,
                        ref=None) -> GuardResult
   # 1) reserve(amount) into hold_id; 2) generate 6-digit OTP -> hash+TTL on the
   #    hold; 3) send_whatsapp OTP to the PRIMARY member's mobile. Returns
   #    {ok, txn_id=hold_id, reason="otp_required"} (otp NEVER in response body).
def confirm_pool_redeem(db, household_id, *, hold_id, otp, confirmed_by) -> GuardResult
   # verifies OTP hash + not-expired (atomic single-use claim on the hold),
   # then commit(hold_id). Wrong/expired -> reason "otp_invalid"; releases hold
   # after N failed attempts.
```

`account_key` is the natural id of the ledger holder: `customer_id` (loyalty/store-credit), `voucher_code` (gift voucher), `store_id` (petty-cash float), `household_id` (family wallet), `vendor_id` (consignment). `(account_type, account_key)` is the composite identity.

**HTTP**: E1 itself exposes no router (it is an engine). Dependents keep their own endpoints (`/loyalty/redeem`, `/vouchers/{code}/redeem`, …) and call these functions. One thin admin/debug router is optional in a later phase; not required for E1.

## Data model (Mongo collection name + fields + indexes; mark new vs existing)

**NEW collection: `money_accounts`** — one document per `(account_type, account_key)`. Unifies the balances currently scattered across `vouchers.balance`, `loyalty_accounts.balance_points`, and `customers.store_credit`.

```
{
  account_type: "LOYALTY"|"STORE_CREDIT"|"GIFT_VOUCHER"|"PETTY_CASH"|"FAMILY_WALLET"|"CONSIGNMENT",
  account_key:  "<customer_id|voucher_code|store_id|household_id|vendor_id>",
  balance:      Decimal/float (paisa-exact; integer for points types),
  unit:         "INR" | "points",
  status:       "ACTIVE" | "DRAINED" | "FROZEN" | "CANCELLED",
  floor:        0.0 (null = may go negative),
  expiry_date:  ISO date | null,            # gift-voucher / expirable lots
  store_id, entity_id,                      # scope for RBAC + Settings (E2)
  holds: [ { hold_id, amount, status: HELD|COMMITTED|RELEASED, ref,
             otp_hash, otp_expires_at, otp_attempts, created_at } ],
  ledger: [ { txn_id, type: CREDIT|DEBIT|RESERVE|RELEASE|COMMIT|ADJUST,
              delta, balance_after, reason, ref, actor, store_id,
              idempotency_key, created_at } ],   # shape from store_credit_ledger.make_entry
  created_at, updated_at
}
```

Indexes (NEW):
- `{account_type:1, account_key:1}` **unique** — the guard's identity; prevents split balances.
- `{account_type:1, "ledger.idempotency_key":1}` **partial/sparse** — dedupe retried mutations.
- `{account_type:1, store_id:1, status:1}` — store-scoped listings + cash-variance scans.
- `{"holds.hold_id":1}` sparse — reserve/commit/release lookup.
- `{expiry_date:1}` sparse — voucher/lot expiry sweeps.

**EXISTING (kept, become projections, NOT abandoned):**
- `vouchers` (vouchers.py) — keep the issuance/code/cancel doc; `balance` mirror updated by E1 (or migrate read path — see Migration).
- `loyalty_accounts` (loyalty_repository.py) — `balance_points`, `lifetime_earned/redeemed`, `tier` stay; E1 owns `balance_points` mutation.
- `loyalty_transactions` — stays as the customer-facing earn/redeem/expire ledger (E1's `ledger` is the integrity record; this stays the rich domain ledger).
- `customers.store_credit` (customer_repository.py) — mirror; `store_credit_entries`/`store_credit_ledger` stays the domain ledger.
- `audit_logs` (audit_repository.py) — every E1 op emits one `AuditRepository.create` row.

## How dependents call it (list the feature numbers/names that consume it and the exact call)

Per the LOCKED decision "reuse the atomic guarded find_one_and_update for **ALL** balance changes; never read-modify-write", every feature touching a balance routes through E1:

- **Loyalty redeem** (`loyalty.py:redeem`, line 355) → `money_guard.debit(db, "LOYALTY", customer_id, capped_points, reason="redeem", actor=uid, ref=order_id)` — replaces `accounts.try_debit(...)`.
- **Loyalty earn / adjust / expire** (`loyalty.py` earn 232, adjust 421, expire 616) → `credit(...)` / `debit(...)`; `reverse_for_return` (955) → signed `credit`/`debit` with `idempotency_key=return_id` (its current manual idempotency marker becomes E1's built-in).
- **Gift-voucher redeem** (`vouchers.py:redeem_voucher_atomic`, used by `vouchers.py:redeem` + `orders.add_payment` GIFT_VOUCHER branch) → `debit(db, "GIFT_VOUCHER", code, amount, ...)`. Existing function becomes a 3-line shim calling E1 (zero call-site churn).
- **Store-credit redeem at POS** (`customers.py` / returns refund-to-credit; `try_debit_store_credit`) → `debit(db, "STORE_CREDIT", customer_id, amount, ...)`; issue credit-note → `credit(...)`.
- **Refund to original tender** (LOCKED refund tiers) — returns.py refund step → `credit("STORE_CREDIT")` when refund tender = store credit; the `returned_qty` reserve stays as-is (it's inventory-qty, not money) but uses E1's `reserve/release` verbs for symmetry.
- **Family loyalty wallet** (LOCKED: max 7, pool redemption requires OTP to primary mobile) → `request_pool_redeem(db, household_id, amount, ...)` then `confirm_pool_redeem(db, household_id, hold_id, otp, ...)`. The household membership/tier resolution is E3/CRM; E1 owns only the wallet balance + OTP-gated commit.
- **Petty-cash float / cash-variance (E2 thresholds)** — day-open float `credit("PETTY_CASH", store_id, ...)`, payouts `debit(...)`; the once-daily variance check reads `get_balance` vs counted cash.
- **Consignment** (vendor stock-on-credit) → `credit`/`debit("CONSIGNMENT", vendor_id, ...)`.
- **E5 tender→Tally routing** — consumes E1's `ledger` rows (typed CREDIT/DEBIT per tender) as the reconciliation source.

## Integration points (agents, MSG91, Tally, RBAC, audit)

- **Audit (mandatory, fail-soft):** every successful verb calls `AuditRepository.create({action:"money.<verb>", entity_type:"money_account", entity_id:account_key, store_id, user_id:actor, detail:{type, delta, balance_after, ref, idempotency_key}})`. The in-doc `ledger[]` is the integrity record; the chained `audit_logs` is the immutable trail (immutable even for SUPERADMIN, per core philosophy).
- **MSG91 / OTP:** `request_pool_redeem` calls `providers.send_whatsapp(primary_mobile, otp_template_body, template_id=MSG91_OTP_TEMPLATE_ID)` (utility template first, per LOCKED comms). OTP stored only as a salted hash + TTL on the hold; `DISPATCH_MODE` gate respected (in `off`/`test`, returns `otp_required` but routes to `TEST_PHONE`). Cross-rule 3-msg/30-day cap does **not** apply to OTP (transactional, not marketing).
- **Agents:** TASKMASTER auto-reorder/expense paths that move money use `debit/credit` (audit-logged, fits its 3-tier safety). ORACLE anomaly scan reads `money_accounts.ledger` for fraud/variance narratives. Emits `agent.event` `money.balance.low` / `cash.variance.exceeded` via the existing event bus for SENTINEL/TASKMASTER.
- **Tally (E5):** ledger rows carry enough (`type`, `ref`, `store_id`, tender on `detail`) to map to IMS-default Tally ledger names for sales-JV/reconciliation. E1 does not call Tally; it produces the source data.
- **RBAC / Settings:** floors/caps and cash-variance thresholds are read from Settings E2 (global→entity→store). Refund/discount tiers (auto<₹500 / admin>₹2000 / super>₹10000) are enforced by the **caller** (it knows the actor's tier); E1 enforces only the hard floor + atomicity. `store_id`/`entity_id` on every account enable store-scoped reads.

## RBAC (who can do what)

E1 is a service; the **caller's existing route gate is the authority** (E1 trusts `actor` + already-resolved permissions). Recommended caller gates, mirroring the grounded routers:

- **credit / issue** (gift voucher, store-credit note, petty-cash float-in): `ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT` (vouchers `_ADMIN_ROLES`).
- **debit / redeem at POS** (loyalty, voucher, store credit): `SALES_CASHIER, SALES_STAFF, CASHIER, STORE_MANAGER, AREA_MANAGER, ADMIN` (vouchers `_REDEEM_ROLES`).
- **adjust / manual signed** (loyalty.adjust): `SUPERADMIN, ADMIN` only.
- **reserve/release/commit:** same as debit (it IS a guarded debit/restore).
- **family pool redeem:** initiator = any redeem role; **commit requires the OTP** delivered to the primary member's mobile (the OTP, not a role, is the second factor) — this is the LOCKED household control.
- **petty-cash / consignment:** `STORE_MANAGER+` / `ACCOUNTANT`.
- **freeze/cancel account:** `ADMIN, AREA_MANAGER, STORE_MANAGER` (vouchers `_CANCEL_ROLES`).
- SUPERADMIN auto-passes via `require_roles`; tier-based refund/discount value limits enforced by caller against E2 settings.

## Migration impact (schema/back-compat)

Greenfield service; the risk is the **three existing live balances** (`vouchers.balance`, `loyalty_accounts.balance_points`, `customers.store_credit`). Strategy = **adapter-first, backfill-second, never a hard cutover** (matches the prod dup/null-index sensitivity in memory):

1. **Phase A (zero data change):** ship `money_guard.py` with adapters that operate on the *existing* collections (loyalty/voucher/store-credit) via their current shape, so E1 is just the unified facade over today's three primitives. New ledgers (family wallet, petty-cash, consignment) use `money_accounts` from day one. No migration, fully back-compatible.
2. **Phase B (backfill, idempotent script under `scripts/`):** project existing balances into `money_accounts` as the system-of-record; keep `vouchers.balance`/`loyalty_accounts.balance_points`/`customers.store_credit` as mirrors updated in the same atomic write (dual-write) for any legacy reader. Add the unique `(account_type, account_key)` index **after** dedupe (the memory `prod_data_blockers` warns dup/null keys block index builds — pre-clean required).
3. **Phase C:** flip reads to `money_accounts`, retire the mirror writes. Per-feature, behind a flag.
- **Back-compat contract preserved:** `redeem_voucher_atomic`, `try_debit`, `try_debit_store_credit` signatures stay (become shims) — no call-site edits required to land Phase A. `loyalty_transactions` and `store_credit` domain ledgers are untouched.

## Build effort (dev-days) + risk

- **Phase A** (engine + verbs + `_apply_atomic` + result type + audit hook + 3 adapter shims + tests): **3 dev-days.**
- **Family-wallet OTP two-phase** (reserve/hold + OTP hash/TTL/attempts + MSG91 wiring + tests): **2 dev-days.**
- **Reserve/release/commit + petty-cash/consignment account types:** **1.5 dev-days.**
- **Backfill script + dual-write + index pre-clean (Phase B):** **2 dev-days.**
- **Total ≈ 8.5 dev-days** for full scope; **5 dev-days** for the must-haves (Phase A + family OTP).

**Risk: MEDIUM-HIGH** (it is money, multi-feature, and touches three live balances).
- *Highest:* the unique-index build on `money_accounts` failing on pre-existing dup/null keys (known prod blocker) — mitigated by Phase A using existing collections + a dedupe pre-pass before index build.
- *High:* OTP delivery dependency on MSG91 + `DISPATCH_MODE` — must fail **closed** (no OTP delivered ⇒ no commit; hold auto-releases on TTL) so a comms outage can't release pool funds unverified.
- *Medium:* dual-write window in Phase B (two collections must move in one atomic op) — keep the mirror `$inc` inside the *same* `find_one_and_update` (`$inc` two fields) to stay atomic, or accept eventual-consistency reconcile.
- *Low (mitigated):* the `no_atomic` fallback path (mock/minimal collection) — must **fail closed** for debits (return `no_atomic`, never silently succeed), exactly as `try_debit` does today.

## Acceptance tests (intent-level, assert behavior not plumbing)

1. **No double-spend under race:** two concurrent `debit(LOYALTY, c, 100)` against a balance of 100 → exactly one `ok=True`, one `ok=False reason="insufficient"`; final balance == 0; exactly one ledger DEBIT row. (Mirrors `test_money_integrity_guards`.)
2. **No negative balance / floor honored:** `debit` of 101 against 100 with `floor=0` → `ok=False`, balance unchanged, no ledger row written.
3. **Credit is unconditional & exact:** `credit(STORE_CREDIT, c, 250.55)` then `get_balance` → +250.55 to the paisa; ledger `balance_after` matches.
4. **Idempotent retries:** same `idempotency_key` replayed (e.g. a retried return reversal) applies the delta exactly once; second call returns the original `txn_id` with `reason="duplicate"`. (Generalises `reverse_for_return`'s marker.)
5. **Reserve→commit moves money once; reserve→release restores it:** `reserve(40)` lowers spendable by 40 but balance is recoverable; `release` returns it; `commit` finalizes; a hold can't be both committed and released.
6. **Family pool redeem requires OTP to the PRIMARY member:** `request_pool_redeem` holds the amount, sends OTP only to the primary's mobile, and the response body NEVER contains the OTP; `confirm_pool_redeem` with the correct OTP commits, wrong OTP → `reason="otp_invalid"` and after N attempts the hold auto-releases (funds restored). (Enforces the LOCKED household rule.)
7. **Any household member can spend the pooled wallet, max 7 members** — a non-primary member can initiate, but commit still gates on the primary-mobile OTP.
8. **Expiry guard:** `debit(GIFT_VOUCHER)` on an expired voucher → `reason="expired"`, no spend (mirrors voucher `_is_expired`).
9. **Fail-soft on no DB:** every verb with `db=None` returns `ok=False reason="unavailable"`, raises nothing.
10. **Fail-closed on no-atomic collection:** a debit against a collection lacking `find_one_and_update` returns `reason="no_atomic"` and does NOT mutate (never silently succeeds).
11. **Audit emitted per mutation:** each successful verb writes exactly one chained `audit_logs` row carrying type/delta/balance_after/ref; an audit-write failure does NOT roll back or block the money move (fail-soft, per audit contract).
12. **Cross-feature reuse:** `vouchers.redeem_voucher_atomic` and `loyalty.try_debit` continue to pass their existing tests after being reshimmed onto E1 (behavior-preserving).

## Open conflicts / notes for the chair

- **System-of-record vs mirror:** Does the chair want `money_accounts` to become the authoritative balance (Phase C) or stay a unifying facade over the existing three collections indefinitely (Phase A only)? Affects effort by ~4 dev-days and the dual-write risk. Recommendation: ship Phase A now, gate Phase B/C behind the prod dup/null-index cleanup that memory already flags as blocked.
- **Petty-cash floor:** can a petty-cash float legitimately go negative (overdraw)? LOCKED cash-variance is "thresholds + once-daily" — I modeled `floor=0` with variance tracked, not a hard block. Confirm.
- **OTP delivery channel:** LOCKED comms says "MSG91 utility templates first" — confirm an OTP/transactional template id (`MSG91_OTP_TEMPLATE_ID`) will be provisioned, distinct from marketing templates, and exempt from the 3-msg/30-day cap. If MSG91 OTP product (vs WhatsApp template) is preferred, the `request_pool_redeem` send line swaps to MSG91's OTP API — one-line change.
- **Loyalty `lifetime_earned/redeemed` + tier:** these live on `loyalty_accounts` and are domain-specific (tier computation). E1 owns only `balance`. Keep tier/lifetime bookkeeping in the loyalty caller, applied in the same atomic write via E1's optional `extra_inc`/`extra_set` passthrough — confirm E1 should expose that passthrough (recommended) vs forcing loyalty to keep its own repo method.
- **Overlap with E4 (approval/PIN) and refund tiers:** E1 enforces the *hard* floor + atomicity; the *value-tier* approval (auto<₹500 / admin>₹2000 / super>₹10000) is enforced by the caller against E2/E4. Confirm E1 should stay tier-agnostic (recommended — keeps the engine reusable) rather than embedding approval logic.

Files referenced: `backend/api/routers/vouchers.py`, `backend/api/routers/loyalty.py`, `backend/api/routers/returns.py`, `backend/api/routers/points.py`, `backend/database/repositories/loyalty_repository.py`, `backend/database/repositories/customer_repository.py`, `backend/api/services/store_credit_ledger.py`, `backend/database/repositories/audit_repository.py`, `backend/agents/providers.py`, `backend/database/connection.py`. New file to create: `backend/api/services/money_guard.py` (+ new collection `money_accounts`).


## ===== E2: Settings-matrix (typed, store-scoped policy registry) =====
I have all the grounding I need. Here is the buildable contract.

---

# Engine E2: Settings-matrix (typed, store-scoped policy registry)

A single typed policy registry + `get_policy(key, scope)` resolver (global → entity → store override, DB-first with env fallback) and ONE reusable Settings-UI renderer driven by a server-published schema. Every configurable threshold in the LOCKED DECISIONS (refund tiers, cash variance, liquidation floor, promo ceiling, reminder/ageing days, comms cap, etc.) becomes a registry key — no bespoke screen, no code constant edits.

## Reuse (existing files/functions to build on — real paths)

- `backend/api/services/integration_config.py::_load_db_config` — the canonical **DB-first → env-fallback** loader (try `integrations` doc, return `{}` on miss). E2 generalises this exact pattern into a 3-level scoped resolver. **The single most reusable precedent.**
- `backend/api/routers/settings.py` — the whole **singleton-doc storage idiom**: `_get_settings_collection(name)` (lines 226-236), per-domain `collection.update_one({"_id": "default"}, {"$set": ...}, upsert=True)` (business/tax/invoice/printer/system/approval). E2 collapses ~15 of these singletons behind one collection + one renderer but keeps the existing endpoints as thin facades (see Migration).
- `backend/api/routers/settings.py::_encrypt_config / _decrypt_config / _mask_config / _SENSITIVE_FIELDS` (lines 86-218) — reuse verbatim for any policy value flagged `secret`. Do NOT reimplement encryption (integration_config already imports these as the single implementation).
- `backend/api/routers/settings.py::_INTEGRATION_CATALOG` (lines 377-605) + `GET /settings/integrations/catalog` + the FE `IntegrationsHub` — the **proven "schema row → generic FE renderer"** model. E2's `policy_catalog` and the `PolicySchemaForm` renderer are the same idea applied to scalar/threshold settings.
- `backend/api/services/pricing_caps.py` — the existing **pure resolver with code-constant defaults** (`CATEGORY_DISCOUNT_CAPS`, `effective_discount_cap`). E2 does NOT replace it; it lets the *category caps / promo ceiling / price floor %* become E2 keys that `pricing_caps` reads as overrides (defaults stay as the env/code fallback).
- `backend/api/services/cache.py` — `cache.get/set/delete`, `TTL_LONG=900`, `invalidate_store(store_id)` (lines 104-164). Resolved policies are cached and invalidated on write exactly like `feature_toggles` (settings.py:2333-2356).
- `backend/database/repositories/audit_repository.py::AuditRepository.create` — hash-chained, append-only. Every policy write logs through `get_audit_repository()` (settings.py already imports it).
- `backend/api/services/rbac_policy.py` + `backend/api/middleware/rbac_enforcement.py` — E2 endpoints get registry rows; per-key write-RBAC reuses `require_roles`.
- `backend/api/routers/entities.py` (entity = PAN, groups stores) + `stores.py` (`store.entity_id`, required) — these define the **scope chain**. `get_policy` resolves a store's `entity_id` via the stores collection to walk store → entity → global.
- FE: `frontend/src/components/settings/FeatureToggles.tsx` (fetch-schema-then-render-toggles) and `frontend/src/pages/settings/SettingsPage.tsx` `SETTINGS_GROUPS` side-nav — the renderer slots in as new tabs without new bespoke components.

## Public API (functions and/or endpoints with signatures)

**Backend service — `backend/api/services/policy_engine.py` (NEW)**

```python
# Scope is resolved global -> entity -> store; the MOST SPECIFIC present wins.
Scope = dict   # {} | {"entity_id": str} | {"store_id": str}  (store_id implies its entity)

def get_policy(key: str, scope: Scope | None = None, *, default=_UNSET) -> Any:
    """Resolve ONE policy value. Order: store override -> entity override ->
    global -> registry default -> env fallback -> `default` arg.
    Pure read; cached (TTL_LONG) per (key, resolved_scope_id). Never raises;
    a missing key with no default raises KeyError ONLY if not in the registry."""

def get_policies(keys: list[str] | None, scope: Scope | None = None) -> dict[str, Any]:
    """Batch resolve (one Mongo read per scope level). keys=None -> all registry keys."""

def get_effective(key: str, scope: Scope | None = None) -> dict:
    """Resolution trace for the UI/audit: {value, source: 'store'|'entity'|'global'|'default'|'env',
    overridden_at: scope_id|None, default, type, unit}."""

def set_policy(key: str, value, scope: Scope, *, actor: dict) -> dict:
    """Validate value against the registry spec (type/min/max/enum/regex), encrypt if
    secret, upsert the {level} doc, audit-log, invalidate cache. Returns get_effective()."""

def clear_override(key: str, scope: Scope, *, actor: dict) -> dict:
    """Remove a store/entity override so it falls back to the parent level."""

def registry() -> list[PolicySpec]:   # the typed catalog (see below), grouped, FE-renderable
```

**`PolicySpec`** (the typed registry entry, defined in `policy_registry.py`):
```python
{ "key": "refund.tier.admin_above",        # dotted namespace
  "group": "Refunds & Returns", "label": "Admin approval above (Rs)",
  "type": "money_paisa"|"int"|"float"|"bool"|"percent"|"enum"|"string"|"days"|"json",
  "default": 200000,                         # paisa-exact for money
  "min": 0, "max": None, "enum": None, "regex": None, "unit": "INR",
  "scopes": ["global","entity","store"],     # which levels may override
  "secret": False,
  "write_roles": ["SUPERADMIN","ADMIN"],
  "env": "REFUND_TIER_ADMIN_ABOVE",          # optional env fallback
  "help": "Refunds above this need an ADMIN PIN.",
  "consumers": ["E4 approvals","returns.py"] }
```

**REST — mounted on the existing settings router (`/api/v1/settings/policies`)**
```
GET    /settings/policies/registry                      -> grouped PolicySpec[] (renders the UI)
GET    /settings/policies?scope=global|entity:<id>|store:<id>   -> {key: effective}[] (batch, secrets masked)
GET    /settings/policies/{key}?scope=...               -> get_effective() (masked)
PUT    /settings/policies/{key}                          body {value, scope} -> set_policy()
DELETE /settings/policies/{key}?scope=entity:<id>|store:<id>    -> clear_override()
```
Scope query param `store:<id>` returns the fully-resolved view (so a store manager sees the value they actually operate under). Writes are per-key RBAC-gated from `PolicySpec.write_roles`.

## Data model (Mongo collection name + fields + indexes; mark new vs existing)

**NEW collection: `policy_settings`** — one doc per (scope-level, scope-id). Values stored sparsely (only set keys).
```
{ "_id": "global"  |  "entity:<entity_id>"  |  "store:<store_id>",   # scope address
  "level": "global"|"entity"|"store",
  "scope_id": null|"<entity_id>"|"<store_id>",
  "values": { "refund.tier.admin_above": 200000,
              "cash.variance.warn": 0, "cash.variance.block": 50000,
              "liquidation.floor_pct_over_cost": 10.0, "promo.ceiling_pct": 30.0,
              "reminder.rx_expiry_days": 30, "ageing.ar_buckets": [30,60,90],
              "comms.cap_per_customer_30d": 3, "loyalty.pool_max_members": 7 },
  "updated_at": ISODate, "updated_by": "<user_id>" }
```
Indexes (NEW): `{_id}` (implicit, primary lookup — only 1 global + ~3 entity + ~6 store docs, tiny). `{level:1, scope_id:1}` for admin listing.

- **Secret values** stored under the same `enc:`/`fernet:` scheme via `_encrypt_config` (reusing settings.py).
- **Registry defaults are CODE**, not DB rows (`policy_registry.py`) — the registry is version-controlled and is the schema the FE renders; the DB only holds *overrides*. This mirrors how `pricing_caps`/`feature_toggles`/`tds_rates` keep defaults in code and persist only deltas.
- Migration of existing singletons is **read-through, not move** (see Migration impact) — `business_settings`, `tax_settings`, etc. stay as-is; only genuinely *new* thresholds and the per-scope ones land in `policy_settings`.

## How dependents call it (list the feature numbers/names that consume it and the exact call)

- **E4 (Approval / refund-discount tiers)** — `get_policies(["refund.tier.auto_below","refund.tier.admin_above","refund.tier.super_above"], {"store_id": sid})`; `get_policy("approval.pin_validity_min", scope)`.
- **Money engine / `returns.py` / `loyalty.py`** — refund-to-original-tender is logic, but the *tier thresholds* come from `get_policy("refund.tier.admin_above", {"store_id": sid})`.
- **Cash reconciliation (cash variance)** — `get_policies(["cash.variance.warn","cash.variance.block","cash.variance.frequency"], {"store_id": sid})` (default `0 / 100 / 500`, once-daily) — explicitly per-store via E2 per LOCKED DECISIONS.
- **Liquidation / clearance** — `get_policy("liquidation.floor_pct_over_cost", scope)` (default 10) and **`pricing_caps.py`** reads `promo.ceiling_pct` (default 30) + per-category floor via `get_policy`, keeping its constants as the fallback.
- **MEGAPHONE / marketing** — `get_policy("reminder.rx_expiry_days", scope)`, `get_policy("comms.cap_per_customer_30d", scope)` (default 3), DND window keys.
- **Finance ageing / AR-AP** — `get_policy("ageing.ar_buckets", scope)`, `get_policy("ageing.overdue_days", scope)`.
- **Family loyalty wallet** — `get_policy("loyalty.pool_max_members", scope)` (default 7), `get_policy("loyalty.pool_redeem_requires_otp", scope)` (default True).
- **Serial / SKU** — `get_policy("serial.return_mismatch_hard_block", scope)`.
- **Existing typed settings** (`tds_rates`, `feature_toggles`, `loyalty`, `lens-pricing`) can be migrated key-by-key later; not required for v1.

## Integration points (agents, MSG91, Tally, RBAC, audit)

- **Agents**: MEGAPHONE/ORACLE/TASKMASTER read thresholds via `get_policy(...)` instead of env/constants — so the owner tunes a reminder window or comms cap in Settings, no redeploy (matches the "read fresh per-request" note in `integration_config.get_whatsapp_config`).
- **MSG91**: comms-cap + DND keys feed the dispatch gate; secret provider creds stay in the `integrations` collection (E2 does NOT take over integration credentials — different concern).
- **Tally (E5)**: tender→ledger-name map is an E2 `json` key (`tally.ledger_map`) resolved at global/entity scope.
- **RBAC**: each endpoint is added to `rbac_policy.py`; per-**key** write authorization comes from `PolicySpec.write_roles` enforced in `set_policy` via `require_roles` (a STORE_MANAGER may set store-scoped cash-variance but not the global refund tier).
- **Audit**: every `set_policy`/`clear_override` writes a hash-chained row via `get_audit_repository().create({action:"policy_update", entity_type:"policy_setting", entity_id:key, before_state, after_state, store_id})` — so `_audit_changes` (settings.py:1952) renders the old→new diff in the existing Activity Log with zero FE work.

## RBAC (who can do what)

- `GET .../registry` and read-resolve: **AUTHENTICATED** (a cashier needs to know their store's caps). Secret values masked via `_mask_config`.
- **Global** scope writes: per-key `write_roles` (default `["SUPERADMIN","ADMIN"]`; statutory/security keys SUPERADMIN-only).
- **Entity** scope writes: `["SUPERADMIN","ADMIN"]` (+ `ACCOUNTANT` for finance-group keys, mirroring tax/invoice gating).
- **Store** scope writes: `["SUPERADMIN","ADMIN","STORE_MANAGER"]` and **only for the store(s) in `current_user.store_ids`** (same store-scope check as `feature_toggles`, settings.py:2300-2307) and only for keys whose `scopes` include `"store"`.
- INVESTOR is read-only app-wide (the existing `block_investor_writes` middleware covers PUT/DELETE automatically).

## Migration impact (schema/back-compat)

- **Additive, zero break.** New collection `policy_settings`; nothing renamed/dropped. Existing `business_settings`/`tax_settings`/`invoice_settings`/`printer_settings`/`system_settings`/`approval_workflows`/`feature_toggles`/`tds_rate_config`/`discount_rules` keep their endpoints and docs.
- v1 ships only **new** thresholds + the **per-scope** ones (cash variance, refund tiers, liquidation floor, promo ceiling, reminder/ageing/comms/loyalty pool). No data migration needed.
- **Facade option (recommended, later):** the legacy singleton GET/PUT handlers can be re-pointed to `get_policy`/`set_policy` one field at a time behind their unchanged request/response shapes — callers (`settingsApi.*`, providers) never notice. `pricing_caps.effective_discount_cap` gains an optional `scope` arg and reads E2 overrides, falling back to its current constants when unset (back-compat: no scope passed → identical output today).
- Defaults living in code means a fresh DB behaves exactly as today (env fallback preserved via `PolicySpec.env`).

## Build effort (dev-days) + risk

- Backend (`policy_registry.py` ~30 seed keys, `policy_engine.py` resolver+cache+audit, 5 endpoints, rbac rows): **2.5 d**
- FE `PolicySchemaForm` renderer + scope picker (global/entity/store) + wire 2-3 new Settings tabs reusing `SettingsPage` groups: **2 d**
- Wire first 4 real consumers (cash variance, refund tiers, liquidation floor, comms cap) + `pricing_caps` scope hook: **1.5 d**
- Tests (resolution order, scope RBAC, secret round-trip, audit diff): **1 d**
- **Total ≈ 7 dev-days.**
- **Risk: Medium.** Resolution-order bugs (entity inference from `store_id`), cache-staleness on write (mitigated by reusing `cache.invalidate_store`/`delete`), and the temptation to migrate everything at once (DON'T — facade incrementally). Low blast radius: read-through with code defaults means a registry/DB miss degrades to today's behaviour.

## Acceptance tests (intent-level, assert behavior not plumbing)

1. **Override precedence**: set `cash.variance.block`=300 global, =500 for entity E, =800 for store S (S∈E). `get_policy(..., {"store_id":S})` → 800; a store S2∈E with no override → 500; a store in another entity → 300. Clearing S's override → S now resolves 500.
2. **Code-default + env fallback**: empty DB → `get_policy("promo.ceiling_pct")` returns 30 (registry default); with `PROMO_CEILING_PCT=25` in env and no DB row → returns 25; a DB global override of 20 beats env.
3. **Typed validation**: `set_policy("refund.tier.admin_above", -1, ...)` → rejected (min 0); setting a `percent` key to 150 → rejected (max 100); money keys are paisa-int (setting `200000` means Rs 2000.00).
4. **Real consumer honours the knob**: with `refund.tier.admin_above`=200000 for store S, a Rs 2500 refund at S requires ADMIN approval; lower it to 100000 and a Rs 1500 refund now requires approval — asserting E4 reads E2, not a constant.
5. **Per-store RBAC**: a STORE_MANAGER of S sets `cash.variance.block` for S → 200; the same user setting it for store S' (not theirs) → 403; setting the **global** refund tier → 403.
6. **Secret round-trip + masking**: a `secret` policy value is stored encrypted (not plaintext in Mongo) and returned masked on GET, but the internal `get_policy` resolves the cleartext.
7. **Audit diff**: changing a value writes ONE append-only audit row whose before/after surfaces as an old→new change in the existing Activity Log; the row cannot be edited/deleted.
8. **UI is generic**: adding a new `PolicySpec` row makes a fully-rendered, validated control appear in the Settings tab with no new FE component (assert the registry endpoint drives the form).

## Open conflicts / notes for the chair

- **Boundary vs E4**: E2 owns the *threshold values* (refund/discount tiers, PIN validity minutes); E4 owns the *approval mechanism* (PIN check, 60-min validity, maker-checker). Confirm E2 only stores the numbers and E4 reads them — no approval logic in E2.
- **Boundary vs existing singletons**: do we *facade* the ~15 legacy settings collections through E2 now, or only house *new* keys and migrate opportunistically? Recommendation: house new + per-scope keys now; facade legacy later (lower risk). Needs a call.
- **`pricing_caps` ownership**: category caps + luxury brand caps are currently non-negotiable code constants (CLAUDE.md). Making them E2-overridable per scope could let a store under-cap a luxury brand. Recommend: floor/promo-ceiling are E2-tunable, but **luxury brand caps stay code-locked** (or E2 can only LOWER them, never raise). Chair to confirm.
- **Scope chain depth**: LOCKED E2 says global→entity→store. Some keys are GSTIN-specific (an entity has multiple GSTINs). v1 treats GSTIN-level as out of scope (entity-level only); flag if any consumer needs per-GSTIN.
- **`store_id`→`entity_id` resolution** adds a stores-collection read per cold cache miss; acceptable given tiny cardinality (6 stores), but note it so nobody expects a zero-dependency pure function like `pricing_caps`.

Real files grounding this: resolver pattern `backend/api/services/integration_config.py`; singleton storage + crypto + catalog `backend/api/routers/settings.py`; pure-resolver-with-defaults `backend/api/services/pricing_caps.py`; cache `backend/api/services/cache.py`; audit `backend/database/repositories/audit_repository.py`; RBAC `backend/api/services/rbac_policy.py`; scope chain `backend/api/routers/entities.py` + `backend/api/routers/stores.py` (`store.entity_id`); FE renderer precedent `frontend/src/components/settings/FeatureToggles.tsx` + `frontend/src/pages/settings/SettingsPage.tsx`.


## ===== E3: Item-event ledger (append-only stock/serial/quarantine + Base-Bank states) =====
I now have complete grounding. Here is the buildable contract.

---

# Engine E3: Item-event ledger (append-only stock/serial/quarantine + Base-Bank states)

## Reuse (existing files/functions to build on — real paths)

The current code already has the *substrate* but mutates `stock_units.status` in place and writes inconsistent, best-effort side-channel audit. E3 makes the side-channel the **source of truth** and the status a *projection*. Concrete reuse:

- **`backend/api/routers/inventory.py`** — `stock_units` collection (one serialized row per physical unit, `status`/`store_id`/`barcode`/`batch_code`/`expiry_date`), the on-hand statuses (`_on_hand_by_product` L107, the `AVAILABLE/IN_STOCK/RESERVED/VOID` vocabulary), `add_stock`/`opening-stock` mint path (L780, L959), cycle-count void path (`reconcile_stock_count` L1708 — voids oldest AVAILABLE units), `stock_audit` collection (L592 — already keyed on `stock_id`), the INV-12 `barcode/{barcode}/trace` reader (L532, joins stock_units→grns→orders→transfers→returns→stock_audit). **E3 keeps `stock_units` as the state projection; the ledger becomes the immutable spine `stock_audit` was reaching for.**
- **`backend/api/routers/transfers.py`** — `_apply_ship_stock_move` (L300, AVAILABLE→TRANSFERRED, records `shipped_stock_ids` + `stock_shipped` idempotency flag), `_apply_receive_stock_move` (L415, TRANSFERRED→AVAILABLE re-home, `received_qty_committed` delta guard), `_audit_stock_move` (L272, writes `stock_audit`), `STOCK_STATUS_TRANSFERRED` (L28). **These per-unit moves are exactly E3 events; rewire them to `ItemEventLedger.record()`.**
- **`backend/api/routers/labels.py`** — `scan_advance` (L186, forward-only stage gate via `next_stage` L91 + `STATION_TARGET_STAGE` L120, writes `scan_history` + audit). **`scan.advance` is an E3 event type; the `scan_history` array becomes a ledger projection.**
- **`backend/api/routers/lens_stock.py`** — the canonical atomic CAS pattern: `_atomic_update` (L720, `find_one_and_update` + `$expr`), `reserve`/`commit`/`release` (L741/812/890), `_write_audit` → `lens_stock_audit` (L223). **E3 adopts this exact CAS shape for serial-bind and quarantine guards; lens cells are the *aggregate* (power×cyl×add) analogue of stock_units' *serialized* model — both feed Base-Bank.**
- **`backend/api/services/power_grid.py`** — `format_power` (L16, 0.25-snap signed dioptre), `sph_range`/`cyl_range`/`build_lens_grid`/`build_cl_grid`. **Base-Bank "Required = Base − In-Hand" reuses `format_power` for grid-cell keying and these grids for the power/colour axes.**
- **`backend/api/services/barcode.py`** — `allocate_sequence` (L85, atomic `find_one_and_update` counter), `next_unit_ean13` (L110). **Reuse `allocate_sequence` for the ledger's monotonic `event_seq`.**
- **`backend/database/repositories/audit_chain.py`** — `append_audit_entry` (L218, hash-chained immutable append: claims seq + prev_hash, stamps `entry_hash`, advances head). **The item-event ledger IS a domain-specific audit chain; reuse this exact hash-chaining mechanism so "Audit Everything / immutable even for Superadmin" holds at the unit level.**
- **`backend/api/services/rbac_policy.py`** (L42 entry shape) + **`backend/api/middleware/rbac_enforcement.py`** — register every new E3 endpoint.
- **`backend/api/routers/returns.py`** — `claim_returnable_qty` (L~430, `$elemMatch` + positional `$inc` CAS) — the LOCKED money pattern E3's serial-return-mismatch hard-block mirrors.

## Public API (functions and/or endpoints with signatures)

New service `backend/api/services/item_events.py` (pure-ish core + DB-touching recorder, fail-soft like `barcode.py`):

```python
# --- Vocabulary (explicit enums; supersedes scattered status strings) ---
class StockState(str, Enum):
    AVAILABLE="AVAILABLE"; RESERVED="RESERVED"; TRANSFERRED="TRANSFERRED"
    IN_TRANSIT="IN_TRANSIT"; QUARANTINE="QUARANTINE"; UNDER_AUDIT="UNDER_AUDIT"
    DC_IN="DC_IN"            # lens at de-centering / processing
    SOLD="SOLD"; VOID="VOID"; DAMAGED="DAMAGED"; RETURNED_TO_VENDOR="RTV"

class ItemEventType(str, Enum):
    MINT="mint"; SERIAL_BIND="serial.bind"; QUARANTINE_IN="quarantine.in"
    QUARANTINE_OUT="quarantine.out"; AUDIT_FLAG="audit.flag"; AUDIT_CLEAR="audit.clear"
    DC_IN="lens.dc_in"; DC_OUT="lens.dc_out"; SCAN_ADVANCE="scan.advance"
    TRANSFER_SHIP="transfer.ship"; TRANSFER_RECEIVE="transfer.receive"
    COURIER_UPDATE="transfer.courier"; RESERVE="reserve"; RELEASE="release"
    COMMIT="commit"; SELL="sell"; VOID="void"; ADJUST="adjust"

# Legal state-machine edges (forward-gated; pure, unit-tested)
ALLOWED_TRANSITIONS: dict[StockState, set[StockState]]  # mirrors transfers/labels gates

def is_legal_transition(frm: StockState|None, to: StockState) -> bool: ...

# --- Core recorder: ONE append point all stock mutations funnel through ---
def record_event(
    db, *, stock_id: str, event_type: ItemEventType,
    from_state: str|None, to_state: str|None,
    store_id: str|None, to_store_id: str|None=None,
    actor_id: str|None, source_type: str|None=None, source_id: str|None=None,
    serial: str|None=None, payload: dict|None=None,
) -> dict|None:
    """Append a hash-chained item_events row (via audit_chain.append_audit_entry)
    AND project the new state onto stock_units in ONE atomic CAS:
    find_one_and_update({stock_id, status: from_state}, {$set: status=to_state}).
    Returns the event doc, or None on CAS loss (caller -> 409). Fail-soft on no-DB."""

def record_event_atomic(db, **kw) -> tuple[bool, dict|None]:
    """CAS wrapper; (ok, event). ok=False == concurrent loser / illegal-from-state."""

# --- Base-Bank replenishment (pure; DB-free, like power_grid) ---
def required_qty(base_bank: int, in_hand: int) -> int:          # max(0?, base-in_hand) -- keep signed
def build_replenishment(slots: list[dict]) -> list[dict]:        # planogram/power/colour grid -> Required
```

REST router `backend/api/routers/item_events.py` (prefix `/api/v1/items`):

```
GET    /items/{stock_id}/events                  -> ledger for one unit (replaces ad-hoc stock_audit read)
GET    /items/events?store_id=&type=&from=&to=   -> filtered ledger (store-scoped)
POST   /items/{stock_id}/serial-bind             {serial} -> bind serial (opt-in per SKU; 409 on dup serial)
POST   /items/{stock_id}/quarantine              {reason} -> AVAILABLE->QUARANTINE
POST   /items/{stock_id}/quarantine/release      {disposition: RESTOCK|DAMAGE|RTV}
POST   /items/{stock_id}/lens/dc-in              {job_id}
POST   /items/{stock_id}/scan-advance            -> delegates to labels.scan_advance event
```

Base-Bank router additions (extend `inventory.py`, prefix `/api/v1/inventory`):

```
GET  /inventory/base-bank?store_id=&grid=READERS|CL_POWER|CL_COLOUR|PLANOGRAM
POST /inventory/base-bank                        {store_id, grid, cell_key, base_bank}  (E2-overridable target)
GET  /inventory/replenishment?store_id=&grid=    -> [{cell_key, base_bank, in_hand, required}]
```

## Data model (Mongo collection name + fields + indexes; mark new vs existing)

**`item_events`** *(NEW — the append-only spine)*
```
event_id (uuid)              event_seq (int, monotonic via barcode.allocate_sequence)
seq, prev_hash, entry_hash   (NEW chain fields via audit_chain.append_audit_entry)
stock_id (str)               serial (str|null)
event_type (ItemEventType)   from_state (str|null)   to_state (str|null)
product_id, store_id, to_store_id
actor_id                     source_type ("TRANSFER"|"GRN"|"POS"|"WORKSHOP"|"AUDIT"|"MANUAL")
source_id                    payload (dict: courier, reason, disposition, job_id, count_id, station...)
at (datetime, IST-stamped)
```
Indexes: `{stock_id:1, event_seq:1}`, `{store_id:1, at:-1}`, `{event_type:1, at:-1}`, `{serial:1}` **unique partial** (`serial != null`), `{source_type:1, source_id:1}`.

**`stock_units`** *(EXISTING — now the projection of `item_events`)*
- Add fields (back-compat, all optional): `serial` (str|null), `serial_required` *projected from product*, `quarantine_reason`, `under_audit` (bool), `dc_in_job_id`, `last_event_seq` (int), `state` (alias of `status`, canonicalised to `StockState`). Existing `status`/`transfer_id`/`shipped_stock_ids` stay.
- New index: `{serial:1}` unique partial; `{store_id:1, status:1, product_id:1}` (replenishment in-hand rollups).

**`base_bank_targets`** *(NEW — the "Base Bank" target per cell, E2-hierarchy aware)*
```
target_id   scope ("GLOBAL"|"ENTITY"|"STORE")   entity_id|null   store_id|null
grid ("READERS"|"CL_POWER"|"CL_COLOUR"|"PLANOGRAM")
cell_key (canonical: power via power_grid.format_power, e.g. "+2.00" / "-3.50"; colour "GREEN"; slot "S1","F3","B12")
product_id|product_line_id|null   base_bank (int)   display_size (int|null)  # planogram slot capacity
updated_by  updated_at
```
Index: `{scope:1, store_id:1, grid:1, cell_key:1}` unique; `{store_id:1, grid:1}`.
Resolution = STORE override → ENTITY → GLOBAL (E2 LOCKED hierarchy).

**`counters`** *(EXISTING)* — reuse for `item_event_seq`.
**`stock_audit`** *(EXISTING)* — kept as legacy reader for INV-12; **dual-write** during migration, then INV-12 reads `item_events`.

## How dependents call it (list the feature numbers/names that consume it and the exact call)

- **Transfers (transfers.py rewrite of `_apply_ship_stock_move`/`_apply_receive_stock_move`)**: replace each `stock_repo.update(sid, {...status...})` + `_audit_stock_move(...)` pair with `item_events.record_event_atomic(db, stock_id=sid, event_type=TRANSFER_SHIP, from_state=AVAILABLE, to_state=TRANSFERRED, store_id=from, to_store_id=to, source_type="TRANSFER", source_id=transfer_id)`. Courier/AWB updates → `event_type=COURIER_UPDATE, payload={awb,courier}`.
- **Workshop scan-to-advance (labels.py `scan_advance`)**: after the gate passes, `record_event(db, stock_id=<unit or job-linked>, event_type=SCAN_ADVANCE, payload={stage,station,scanned_code})`.
- **Cycle count / blind-count (inventory.py `start/complete/reconcile_stock_count`)**: snapshot start → `AUDIT_FLAG` (AVAILABLE→UNDER_AUDIT) per unit in scope (the **blind-count under-audit flag**); reconcile shrinkage → `VOID` via `record_event_atomic` instead of the raw `update_many` at L1724; clear → `AUDIT_CLEAR`.
- **GRN / opening-stock intake (inventory.py `add_stock` L780, `opening-stock/commit` L959)**: each minted unit → `MINT` event (from_state=None→AVAILABLE), serial-required SKUs → `SERIAL_BIND`.
- **Returns (returns.py)**: serial-bound restock calls `record_event_atomic(... SERIAL check ...)`; **serial mismatch returns False → caller hard-blocks refund until manager-PIN override** (LOCKED serial rule), reusing the `claim_returnable_qty` CAS pattern.
- **POS sell**: order-finalisation marks units `SELL` (AVAILABLE→SOLD) via `record_event_atomic` (oversell-safe CAS).
- **Lens stock (lens_stock.py reserve/commit/release)**: already CAS — emit a mirror `item_events` row (`RESERVE/COMMIT/RELEASE`) so the unified ledger + Base-Bank see lens-cell movements too.
- **Base-Bank replenishment report + auto-reorder (TASKMASTER agent)**: `build_replenishment(slots)` where `in_hand` = `_on_hand_by_product`-style rollup, `base_bank` from `base_bank_targets` resolved via E2.
- **Engine E5 (Tally routing)**: reads `item_events` of type SELL/VOID/TRANSFER for stock-movement JV.
- **INV-12 barcode trace**: `GET /items/{stock_id}/events` becomes its canonical movement source.

## Integration points (agents, MSG91, Tally, RBAC, audit)

- **Audit**: every `record_event` funnels through `audit_chain.append_audit_entry` → hash-chained, immutable, `verify_chain`-able. Satisfies "Audit Everything / immutable even for Superadmin."
- **Agents**: SENTINEL subscribes to `quarantine.in` / `audit.flag` (stock-health). TASKMASTER consumes `build_replenishment` for auto-reorder (3-tier safety) and raises an SLA task on `QUARANTINE` aging (reuse `task_triggers.create_system_task`, as inventory.py L1524 already does for variance). NEXUS emits `transfer.courier` from Shiprocket webhooks.
- **MSG91**: MEGAPHONE sends store-manager WhatsApp on `QUARANTINE_IN` with reason and on Base-Bank `required>0` deficits (utility template, DISPATCH_MODE gate, 3-msg/30-day cap).
- **Tally (E5)**: SELL/VOID/RTV events route to the configured Tally ledger.
- **RBAC**: register all new routes in `rbac_policy.py`; the `rbac_enforcement` middleware enforces at request time.

## RBAC (who can do what)

| Action | Roles |
|---|---|
| Read ledger (`GET /items/.../events`, store-scoped) | AUTHENTICATED + `validate_store_access` |
| `serial-bind`, `quarantine`, `quarantine/release`, `lens/dc-in` | SUPERADMIN, ADMIN, AREA_MANAGER, STORE_MANAGER, CATALOG_MANAGER, WORKSHOP_STAFF (= `_INVENTORY_ROLES`, inventory.py L31) |
| `scan-advance` | `SCAN_ROLES` (labels.py L151) incl. CASHIER at PICKUP |
| Base-Bank target write (`POST /inventory/base-bank`) | SUPERADMIN, ADMIN, AREA_MANAGER, STORE_MANAGER (store-scoped) |
| Quarantine `disposition=RTV`/`DAMAGE` (write-off) | SUPERADMIN, ADMIN, AREA_MANAGER (`_STOCK_MANAGER_ROLES`, inventory.py L1794) |
| Serial-mismatch refund override | manager PIN (E4 per-approver PIN) |
| INVESTOR | read-only everywhere (existing middleware) |

## Migration impact (schema/back-compat)

- Additive only: `item_events` + `base_bank_targets` are new; `stock_units` gains optional fields. No existing read breaks.
- **Backfill**: one-time script seeds `item_events` from existing `stock_audit` rows + current `stock_units.status` (synthesizes a `MINT`/current-state snapshot per unit so the ledger has a baseline and the chain starts clean). Pattern follows existing `scripts/_*.py` diagnostics. Heed prod-data blockers (dup/null indexes) — build the `serial` unique-partial index **after** dedup.
- **Dual-write window**: keep `_audit_stock_move`→`stock_audit` alongside `record_event` for one release; INV-12 reads both; then cut over.
- `status`↔`state` kept in sync; `state` canonicalised; legacy lowercase statuses normalised on write.

## Build effort (dev-days) + risk

- Core service + ledger + chain reuse + state-machine + tests: **3 d**
- Base-Bank targets (E2 hierarchy) + replenishment + grid keying: **2.5 d**
- Rewire transfers/labels/inventory/returns/POS callers behind `record_event` (behavior-preserving): **3 d**
- Backfill script + dual-write + INV-12 cutover + indexes: **2 d**
- RBAC rows + agents/MSG91 wiring + acceptance tests: **1.5 d**
- **Total ≈ 12 dev-days.** Risk **Medium-High**: touches revenue-critical POS sell-path and the already-subtle transfer ship/receive idempotency. Mitigation: `record_event_atomic` preserves the existing CAS idempotency flags (`stock_shipped`, `received_qty_committed`); ship transfers/labels rewires first (non-POS), POS sell last behind a feature flag; the state-machine is pure + unit-tested before any caller switches.

## Acceptance tests (intent-level, assert behavior not plumbing)

1. **Append-only + immutable**: after N events on a unit, no API can edit/delete a prior row; `verify_chain` passes; tampering a row fails verification.
2. **Illegal transition refused**: QUARANTINE→SOLD and SOLD→AVAILABLE return 409 and write **no** event and leave `stock_units.status` unchanged.
3. **Concurrency-safe**: two parallel SELLs on the same single unit → exactly one succeeds, one 409; ledger has exactly one SELL.
4. **Serial opt-in + return mismatch**: a serial-required SKU cannot reach AVAILABLE without `serial-bind`; returning a unit whose scanned serial ≠ bound serial hard-blocks the refund until manager-PIN override (asserts the LOCKED rule, not the HTTP shape).
5. **Quarantine round-trip**: AVAILABLE→QUARANTINE drops store on-hand immediately; release `RESTOCK` restores it, `DAMAGE`/`RTV` does not; each leaves the right event pair.
6. **Blind-count under-audit**: starting a count flags in-scope units UNDER_AUDIT (excluded from sellable on-hand); reconcile clears or voids; shrinkage voids exactly `|net_variance|` oldest units (reuses inventory.py L1708 intent).
7. **Transfer in-transit truth**: on ship, source on-hand falls by shipped qty and units are TRANSFERRED with `transfer.ship` events; on receive the *same* units re-home to destination with original barcode; double-ship/partial-receive never double-move (idempotency preserved).
8. **Base-Bank required**: `required = max(base_bank − in_hand)` per cell across readers (+1.00…+3.00), CL power (−0.50…−6.00), CL colour (GREEN/BLUE/GRAY/HAZEL/BROWN/SAPPHIRE…), and planogram slots (`Required = Display Size − On-Hand`); overstock shows negative; powers snap to 0.25 via `format_power`.
9. **E2 hierarchy**: a STORE base-bank override beats ENTITY beats GLOBAL for the same cell.
10. **Colour-flag migration**: legacy green/red/amber/yellow status flags map to explicit enums (`AVAILABLE`/`DAMAGED`/`QUARANTINE`/`UNDER_AUDIT`) — no behavior keys off cell colour.

## Open conflicts / notes for the chair

- **Serialized vs aggregate duality**: `stock_units` is one-row-per-unit (frames/sunglasses), but `lens_stock_lines` is aggregate cells (power×cyl×add) and the Base-Bank grids are aggregate (power/colour). E3 must serve **both**: units emit per-unit events; lens cells emit aggregate `RESERVE/COMMIT/RELEASE` events. Proposed: ledger row carries either `stock_id` (unit) **or** `cell_key`+`lens_line_id` (aggregate). Chair: confirm one ledger spans both, vs two ledgers.
- **`base_bank_targets` cell granularity**: planogram slots (S1/F3/B12) are store-physical, not product-keyed; reader/CL grids are product-line + power. The single `base_bank_targets` shape covers both via `grid` + `cell_key` + optional `product_line_id` — confirm acceptable vs separate planogram-slot collection.
- **Backfill chain start**: existing `stock_audit` rows are *unchained*; the seed creates a fresh chain from a current-state snapshot (historical rows imported as un-chained context). Chair: accept "chain starts at go-live snapshot" (historical pre-E3 moves are reference-only, not chain-verified)?
- **POS sell-path flag**: routing POS finalisation through `record_event_atomic` is the riskiest change ("Ask before touching POS"). Recommend feature-flagged rollout, POS last. Chair sign-off required.
- **Dual-write duration** before retiring `stock_audit` — propose one release; chair to confirm.


## ===== E4: Approval/PIN + maker-checker engine =====
E2 (settings hierarchy resolver) does not yet exist as a shared function — it's a sibling engine being designed in this same initiative. I have enough grounding. Let me write the contract.

# Engine E4: Approval/PIN + maker-checker engine

## Reuse (existing files/functions to build on — real paths)

E4 is a **generalization of the AI-proposal loop already in the repo**. The proposal store proves the exact lifecycle, audit-chaining, and fail-soft patterns E4 needs; E4 lifts them into a human-driven, PIN-gated, TTL'd, role-routed engine.

- **`backend/agents/proposals.py`** — `ProposalStore` (lines 106-558). Lift wholesale:
  - The status enum + lifecycle (`ProposalStatus`, lines 55-61) → E4 `RequestStatus`.
  - `_coll()` / `_audit_coll()` fail-soft collection access (lines 124-144).
  - `_write_audit()` (lines 494-558) — the **canonical way to write an immutable hash-chained row** via `AuditRepository.create → audit_chain.append_audit_entry`. E4 copies this verbatim, changing `entity_type` to `"approval_request"`.
  - `_audit_repo()` (lines 463-492) — binds the audit write to the store's own db (prod seeded db / test fake). Reuse unchanged.
  - The state-guard pattern in `approve()`/`reject()`: read → check `status == PENDING` → set. E4 hardens this into an **atomic** transition (see Money note below).
- **`backend/api/routers/proposals.py`** — the HTTP surface template: `require_superadmin` 404-guard (lines 39-43), `_get_db()` seeded resolver (lines 51-68), `_store()` factory (71-75), `_reviewer()` actor-string (78-81), 404/409/400 error mapping (156-165). E4's router copies this shape exactly (but uses `require_roles`, not superadmin-only).
- **`backend/api/routers/vouchers.py`** — `redeem_voucher_atomic` (lines 170-261). **This is the LOCKED money pattern.** E4's `consume_approval` mirrors it: a single `find_one_and_update` whose **filter encodes the guard** (status REQUESTED, not expired, not yet consumed) so a double-submit can never consume an approval twice. Note `ReturnDocument.AFTER` import (line 232) and the `_redeem_failure_reason` disambiguation read (264-283) for precise error messages.
- **`backend/api/routers/auth.py`** — `hash_password` (lines 225-229, bcrypt rounds=12) and `verify_password` (232-243). **E4 stores per-approver PINs as bcrypt hashes using these exact functions** (a PIN is a short password). Also `require_roles(*allowed_roles)` (line 301) for router/endpoint gating, `get_current_user` (imported in proposals.py line 27).
- **`backend/api/services/role_caps.py`** — `effective_discount_cap(roles, user_override)` (lines 53-74) and `ROLE_DISCOUNT_CAPS` (29-42). E4 does **not** duplicate cap logic; the *dependent* (POS) computes the breach and asks E4 to route an approval. E4 only routes/records.
- **`backend/api/services/notification_service.py`** — `send_notification(...)` (lines 107+). E4's notify step calls this to QUEUE a WhatsApp/SMS to the approver (status PENDING, gated by `DISPATCH_MODE`). Honest-status contract preserved: E4 never claims a message was sent.
- **`backend/database/repositories/audit_repository.py`** — `AuditRepository.create(data)` (line 35). The sink for every E4 transition.

New code (small): `backend/api/services/approvals.py` (the engine), `backend/api/routers/approvals.py` (HTTP), one-time PIN-set endpoint on `users.py`.

## Public API (functions and/or endpoints with signatures)

**Service module `backend/api/services/approvals.py`** — `class ApprovalEngine` (mirrors `ProposalStore`):

```python
class ApprovalEngine:
    COLLECTION = "approval_requests"
    AUDIT_COLLECTION = "audit_logs"
    def __init__(self, db=None): ...

    # --- request creation (called by dependents) ---
    def request(
        self, *,
        action_type: str,            # e.g. "discount_override","refund","journal_entry","profile_merge","petty_cash","endless_aisle","rtv"
        requested_by: str,           # user_id of the maker
        requested_by_roles: list[str],
        store_id: str | None,
        entity_id: str | None,
        amount: float | None,        # rupees; drives tier routing (E2-configured)
        context: dict,               # arbitrary maker-supplied detail (order_id, customer_id, before/after JE, etc.)
        reason: str,
        required_tier: str | None = None,   # explicit override; else derived from amount + E2 tiers
        ttl_minutes: int = 60,       # LOCKED: 60-min validity
        dedupe_key: str | None = None,
    ) -> dict   # {ok, request_id, status:"REQUESTED", required_roles:[...], expires_at}

    # --- approver acts (PIN-gated, atomic, single-use) ---
    def approve(
        self, *,
        request_id: str,
        approver_user_id: str,
        approver_roles: list[str],
        pin: str,                    # plaintext PIN; verified vs bcrypt hash on the user doc
    ) -> dict   # {ok, status:"APPROVED", approval_token, ...} | {ok:False, error:"...", http:409|403|410|404|423}

    def reject(self, *, request_id: str, approver_user_id: str, approver_roles: list[str], pin: str, reason: str = "") -> dict

    # --- maker/executor redeems an APPROVED request (LOCKED atomic single-use) ---
    def consume_approval(
        self, *,
        request_id: str | None = None,
        approval_token: str | None = None,   # one of the two
        consumed_by: str,
        action_type: str,            # must match the request; defense-in-depth
        amount: float | None = None, # must be <= approved amount; re-checked here
    ) -> dict   # {ok:True, request:{...}} | {ok:False, error:"already_consumed|expired|not_approved|amount_exceeded|action_mismatch"}

    # --- reads ---
    def get(self, request_id: str) -> dict | None
    def list_inbox(self, *, approver_roles: list[str], store_ids: list[str] | None, status: str = "REQUESTED", limit: int = 50) -> list[dict]
    def list_mine(self, *, requested_by: str, limit: int = 50) -> list[dict]
    def expire_stale(self) -> int   # sweep: REQUESTED past expires_at -> EXPIRED (idempotent; TASKMASTER tick + lazy-on-read)

# module-level convenience (mirrors agents.proposals.create_proposal)
def request_approval(db, *, action_type, requested_by, ..., ttl_minutes=60, dedupe_key=None) -> dict | None
def require_approval_or_402(db, *, action_type, amount, store_id, entity_id, context) -> ApprovalGate
```

**PIN management (service)**:
```python
def set_approver_pin(db, *, user_id: str, pin: str, set_by: str) -> dict   # bcrypt-hash -> users.approval_pin_hash; audited
def verify_approver_pin(db, *, user_id: str, pin: str) -> bool             # auth.verify_password vs users.approval_pin_hash
def clear_approver_pin(db, *, user_id: str, cleared_by: str) -> dict
```

**HTTP — `backend/api/routers/approvals.py`, mounted `/api/v1/approvals`** (gated `require_roles(*APPROVER_ROLES)` at router level; `request`/`mine` open to AUTHENTICATED):

```
POST /api/v1/approvals/requests              -> create a request (any authenticated maker)
GET  /api/v1/approvals/requests/inbox        -> requests this approver may action (role+store scoped)
GET  /api/v1/approvals/requests/mine         -> my outstanding requests
GET  /api/v1/approvals/requests/{id}         -> one request (maker, eligible approver, or HQ)
POST /api/v1/approvals/requests/{id}/approve -> body {pin}        -> APPROVED + approval_token
POST /api/v1/approvals/requests/{id}/reject  -> body {pin, reason}
POST /api/v1/approvals/requests/{id}/consume -> body {action_type, amount?} -> marks CONSUMED (idempotent)
```

**PIN HTTP — on `backend/api/routers/users.py`** (self or ADMIN):
```
PUT    /api/v1/users/{user_id}/approval-pin   -> body {pin, current_pin?}   set/rotate
DELETE /api/v1/users/{user_id}/approval-pin   -> ADMIN only
GET    /api/v1/users/{user_id}/approval-pin/status -> {has_pin: bool}  (never the hash)
```

## Data model (Mongo collection name + fields + indexes; mark new vs existing)

**NEW collection `approval_requests`** (shape mirrors `ai_proposals`):

| field | type | notes |
|---|---|---|
| `request_id` | str | `REQ-<uuid[:12]>` (mirrors `PROP-`) |
| `action_type` | str | enum string (discount_override, refund, journal_entry, profile_merge, petty_cash, endless_aisle, rtv) |
| `status` | str | `REQUESTED → APPROVED / REJECTED / EXPIRED / CONSUMED` |
| `requested_by` | str | maker user_id |
| `requested_by_roles` | [str] | snapshot |
| `store_id` / `entity_id` | str/null | for E2 tier lookup + inbox scoping |
| `amount` | float/null | paisa-exact rupee value driving the tier |
| `required_tier` | str | `auto`/`admin`/`super` resolved from E2 (default <500/>2000/>10000) |
| `required_roles` | [str] | roles allowed to approve this tier |
| `context` | object | maker detail (order_id, JE before/after, merge src/dst, etc.) |
| `reason` | str | maker justification |
| `maker_checker` | bool | true for `journal_entry` (LOCKED E4): approver ≠ maker enforced |
| `created_at` | datetime | |
| `expires_at` | datetime | `created_at + ttl_minutes` (LOCKED 60) |
| `reviewed_by` / `reviewed_at` | str/datetime | approver |
| `reject_reason` | str/null | |
| `approval_token` | str/null | `APT-<uuid>` minted on APPROVE; opaque single-use handle |
| `consumed` | bool | guard flag for atomic single-use |
| `consumed_at` / `consumed_by` | datetime/str | |
| `audit_log_id` | str/null | last hash-chained audit row |
| `dedupe_key` | str/null | de-dupe identical pending requests (mirrors proposals) |

Indexes (NEW):
- `request_id` unique
- `(status, required_roles, store_id, created_at)` — inbox query
- `(requested_by, status, created_at)` — "mine"
- `dedupe_key` partial (`status: REQUESTED`) unique — prevents duplicate live requests
- `approval_token` unique sparse
- **TTL index `expires_at` with `expireAfterSeconds: 0` is NOT used** (we must *transition to EXPIRED + audit*, not delete). Instead `expire_stale()` sweeps + a lazy check on every read flips overdue REQUESTED rows. A plain index on `expires_at` backs the sweep.

**EXISTING collection `users`** — additive fields (no migration of existing rows; absence = "no PIN set"):
- `approval_pin_hash` (str, bcrypt) — NEW
- `approval_pin_set_at` (datetime) — NEW

**EXISTING collection `audit_logs`** — unchanged; receives one hash-chained row per E4 transition via `AuditRepository.create` (`entity_type="approval_request"`, actions `approval_requested|approved|rejected|expired|consumed|pin_set|pin_cleared`).

## How dependents call it (list the feature numbers/names that consume it and the exact call)

The pattern is two-phase: **maker requests → approver approves (PIN) → maker consumes token, then performs the real DB change**. The real change still lives in the owning router; E4 only gates it.

- **Discount override (POS)** — when `posStore`/`orders.py` detects a discount above `effective_discount_cap(...)` (role_caps.py):
  `request_approval(db, action_type="discount_override", requested_by=user_id, store_id=..., amount=discount_rupees, context={"order_id","line_sku","requested_pct"}, reason=...)`. POS blocks finalize until `consume_approval(approval_token=..., consumed_by=user_id, action_type="discount_override", amount=discount_rupees)` returns ok.
- **Refunds / Returns** (`returns.py`) — tier from E2 (auto<₹500 / admin>₹2000 / super>₹10000): `request_approval(action_type="refund", amount=refund_rupees, context={"return_id","order_id","tender"})`; the atomic refund decrement (already `find_one_and_update`) runs only after `consume_approval`.
- **Journal-entry maker-checker (E5 / finance)** — `request_approval(action_type="journal_entry", maker_checker=True, context={"je_before","je_after","ledgers"})`. E4 **hard-enforces approver ≠ maker** for this action_type (LOCKED E4 maker-checker).
- **Profile merge** (customers) — `action_type="profile_merge"`, context `{src_customer_id, dst_customer_id}`.
- **Petty cash** (expenses) — `action_type="petty_cash"`; replaces the ad-hoc `/expenses/{id}/approve` role check with a PIN'd, tiered request.
- **Endless aisle** — `action_type="endless_aisle"` (cross-store fulfilment authorization).
- **RTV (return-to-vendor)** (purchase) — `action_type="rtv"`, context `{po_id, vendor_id, sku, qty}`.
- **Serial-mismatch override (LOCKED)** — return-serial mismatch hard-blocks refund "until manager PIN override": dependent calls `request_approval(action_type="refund", required_tier="admin", context={"serial_mismatch":True})`; manager PIN approval is the unblock.

Agents: **TASKMASTER** can call `request_approval` instead of acting on non-reversible tiers, and runs `expire_stale()` on its 5-min tick.

## Integration points (agents, MSG91, Tally, RBAC, audit)

- **Audit** — every transition → `AuditRepository.create` (hash-chained, surfaces at `GET /api/v1/audit/verify`). `before_state`/`after_state` carry the request snapshot; PIN hashes are **never** logged.
- **MSG91 / notifications** — on `request()`, call `notification_service.send_notification(... template_id="APPROVAL_REQUEST", category="SERVICE" ...)` to QUEUE a WhatsApp to each eligible approver's mobile (PENDING; `DISPATCH_MODE` gate; honest-status). Cross-rule cap (3 msgs/customer/30d) does **not** apply — these are internal staff, not customers — but use the in-app bell as primary.
- **In-app push** — write a `notifications`/bell row (same shape `task_notify.py` uses) so approvers see the inbox badge without WhatsApp.
- **RBAC** — `required_roles` per tier is the gate; the router uses `require_roles`; `rbac_policy.py` POLICY gains the new `/api/v1/approvals/*` rows (the coverage-lock test in `test_rbac_policy.py` forces this). Middleware `rbac_enforcement.py` enforces at request time as the second layer.
- **TASKMASTER** — runs `expire_stale()`; may auto-create requests.
- **Tally / E5** — journal-entry maker-checker is the prerequisite gate before E5 routes a tender→Tally ledger; E4 supplies the approved/consumed signal, E5 does the posting.
- **E2 (settings hierarchy)** — E4 resolves `required_tier`/thresholds via E2's `resolve_setting(key="approval.refund_tiers", store_id, entity_id)`. Until E2 lands, E4 ships a `_DEFAULT_TIERS` constant (auto<500/admin>2000/super>10000, cash-variance 0/100/500) and swaps to E2 with no API change.

## RBAC (who can do what)

- **Create a request**: any AUTHENTICATED maker (the action they're requesting is what's gated, not the asking).
- **Approve/reject**: only roles in the request's `required_tier`:
  - `auto` (< threshold): STORE_MANAGER, AREA_MANAGER, ADMIN, SUPERADMIN
  - `admin` (> mid threshold): ADMIN, AREA_MANAGER (config), SUPERADMIN
  - `super` (> high threshold): SUPERADMIN
  - **AND** the approver must have an `approval_pin_hash` set and supply the correct PIN.
  - **AND** store-scope: a STORE_MANAGER can only approve requests for their own store (HQ roles bypass), reusing the `store_scoped` flag convention.
  - **maker-checker**: for `journal_entry`, approver `user_id` ≠ `requested_by` (403 `cannot_approve_own`).
- **Consume**: the maker (`requested_by`) or any role in `required_roles`; `consume` re-verifies action_type + amount.
- **Set own PIN**: any user on self. **Clear/force-set another user's PIN**: ADMIN/SUPERADMIN only. PIN hash is never returned by any endpoint (only `has_pin`).
- **INVESTOR**: read-only via existing app-wide `block_investor_writes` middleware — cannot create/approve.

## Migration impact (schema/back-compat)

- **No backfill.** `approval_requests` is new; `users.approval_pin_hash` is additive — absent = "no PIN". A user with no PIN simply cannot approve until they set one (UI prompts on first approve attempt → 423 Locked `pin_not_set`).
- **No existing behavior changes** until a dependent opts in. Wiring E4 into a router replaces that router's ad-hoc approval check (e.g. expenses `/approve`), which is a per-feature, reviewable change — not a global flip.
- **rbac_policy.py POLICY** must gain the new routes (mechanical; the regression test enforces it).
- **Index creation** is idempotent (create-if-absent at startup, matching how other collections register indexes).
- POS payment capture is **untouched** (LOCKED: E5 skips advance/on-delivery split). E4 gates discount/refund only, never the tender capture flow.

## Build effort (dev-days) + risk

- Engine `approvals.py` (lift from `ProposalStore` + atomic `consume` from `redeem_voucher_atomic`): **2.0d**
- PIN set/verify/clear + users endpoints + bcrypt reuse: **0.75d**
- Router `approvals.py` + RBAC POLICY rows + middleware: **1.0d**
- Tier resolution (`_DEFAULT_TIERS`, E2 seam) + notify wiring: **0.75d**
- Tests (atomic single-use, TTL expiry, maker-checker, PIN, RBAC matrix): **1.0d**
- **Total ≈ 5.5 dev-days.**

**Risk**: MEDIUM. The hard parts are already-solved patterns (proposal lifecycle, atomic guarded update, hash-chained audit, bcrypt). Top risks: (1) **double-consume race** — mitigated by the LOCKED `find_one_and_update` filter-guard on `consumed:false` + `status:APPROVED` + `expires_at>now`; (2) **TTL vs deletion** — using sweep+lazy-expire (not a Mongo TTL index) so we never delete an auditable record; (3) **E2 not yet shipped** — mitigated by the `_DEFAULT_TIERS` seam; (4) **PIN brute-force** — mitigate with per-user attempt throttle (lock after N bad PINs in 15 min) + constant-time bcrypt verify (already in `verify_password`).

## Acceptance tests (intent-level, assert behavior not plumbing)

1. **Tier routing**: a ₹400 refund request routes to `auto` (store-manager-approvable); ₹3,000 routes to `admin`; ₹12,000 routes to `super`. A STORE_MANAGER approving the ₹12k request is **403'd**.
2. **PIN required**: an ADMIN with no `approval_pin_hash` calling approve gets **423 pin_not_set**; after setting a PIN and supplying the correct one, the same approve succeeds; a wrong PIN gets **403**.
3. **Single-use (the money guard)**: two concurrent `consume_approval` calls on one APPROVED request → exactly one returns ok, the other returns `already_consumed`; the gated balance/discount is applied **once**. (Mirrors the voucher double-redeem test.)
4. **60-min TTL**: a request created 61 minutes ago that an approver tries to approve returns **410 expired** and the row is now `EXPIRED`; `expire_stale()` flips stale REQUESTED rows and writes an `approval_expired` audit entry.
5. **Maker-checker (journal_entry)**: the maker attempting to approve their own JE request is **403 cannot_approve_own**; a different ADMIN can.
6. **Store scope**: a STORE_MANAGER for store A cannot see/approve a store-B request (404/empty inbox); an AREA_MANAGER over both can.
7. **Consume re-checks amount**: a request approved for ≤₹500 cannot be consumed for ₹600 (`amount_exceeded`).
8. **Audit completeness**: requested→approved→consumed produces three hash-chained `audit_logs` rows that pass `GET /api/v1/audit/verify`; **no PIN value or hash appears** in any audit row.
9. **Serial-mismatch unblock (LOCKED)**: a return flagged `serial_mismatch` hard-blocks refund; only a manager-PIN-approved `refund` request unblocks `consume`.
10. **Fail-soft**: with `db=None` the engine returns empty reads / no-op writes and never raises (cold-boot safe, mirrors `ProposalStore`).

## Open conflicts / notes for the chair

1. **E4 depends on E2 for tier thresholds.** Sequencing: build E4 against `_DEFAULT_TIERS` now, cut over to `resolve_setting` when E2 lands. Chair should confirm the E2 setting keys (`approval.refund_tiers`, `approval.discount_tiers`, `approval.cash_variance`) so both engines agree.
2. **One PIN vs per-action PIN.** I propose a **single per-approver PIN** reused across all action_types (simpler, matches LOCKED "per-approver PIN"). If finance wants a *separate* JE PIN, that's a second hash field — flag if desired.
3. **`ai_proposals` overlap.** The AI proposal loop (superadmin auto-execute) and E4 (human maker-checker) are deliberately **separate collections** — different actors, gates, and lifecycles. I recommend NOT merging them; E4 reuses the *code patterns*, not the *table*. Chair to confirm we don't want one unified queue.
4. **Approval token vs request_id for consume.** I expose both; the token is the safer handle to pass back through the dependent (opaque, single-use). Chair: mandate token-only for high-value tiers?
5. **Discount-cap source of truth.** E4 routes the approval but `role_caps.effective_discount_cap` decides *whether one is needed*. Keep that split (engine = routing/record, caller = policy) — confirm no one expects E4 to own the cap math.
6. **Throttle store.** PIN brute-force throttle needs a small counter (in `users` doc or Redis). Redis is available (event bus) but optional; I default to a Mongo `pin_attempts` sub-doc with a 15-min window to stay fail-soft when Redis is absent.

Files to create: `backend/api/services/approvals.py`, `backend/api/routers/approvals.py`. Files to touch: `backend/api/routers/users.py` (PIN endpoints), `backend/api/services/rbac_policy.py` (POLICY rows), `backend/api/main.py` (mount router). All grounded in `backend/agents/proposals.py`, `backend/api/routers/vouchers.py`, `backend/api/routers/auth.py`, `backend/api/services/role_caps.py`, `backend/api/services/notification_service.py`, `backend/database/repositories/audit_repository.py`.


## ===== E5: Tender-routing + reconciliation (POS payment capture UNCHANGED) =====
I have everything I need. Here is the complete, buildable contract.

---

# Engine E5: Tender-routing + reconciliation (POS payment capture UNCHANGED)

A pure, settings-driven mapper that resolves each EXISTING `order.payments[]` instrument to a correct Tally ledger (and a normalized canonical tender), then a reconciliation reader that aggregates payments by mode for daily close + cash-variance + Tally JV. POS payment *capture* is untouched — E5 only reads `order.payments[]` and writes derived records (a denormalized stamp + a daily reconciliation snapshot). It fixes the current "everything books as Cash / only CASH is reconciled" gap.

## Reuse (existing files/functions to build on — real paths)

- `backend/api/routers/orders.py` — `PaymentMethod` enum (lines 376-391: CASH/UPI/CARD/BANK_TRANSFER/EMI/CREDIT/GIFT_VOUCHER/LOYALTY) and the persisted payment-row shape (lines 2660-2670: `payment_id`, `method`, `amount`, `reference`, `received_by`, `received_at`). E5 reads these; it does NOT change `PaymentCreate` or `POST /{order_id}/payments`. The legacy `method`/`mode` alias (lines 451-475, 2281) is the canonicalization precedent E5 mirrors.
- `backend/api/routers/finance.py`
  - `_cash_sales_for_window(db, store_id, start_iso, end_iso)` (line 2249) and `_cash_expenses_for_window` (line 2297) — currently hardcode `method == "CASH"`. E5 generalizes these into a per-mode aggregator the cash-register close reuses unchanged for the CASH row.
  - `close_cash_register` (line ~2453) → `cash_register.build_close_summary` — E5 feeds the same `cash_sales`/`cash_refunds` numbers but additionally persists the full by-mode breakdown.
  - `get_tally_sales_jv` (line 1983) + `_jv_cgst_sgst_split` (line 2025) — the sales-JV export. E5's ledger map is consumed by the receipt/party leg.
  - `_split_output_tax` (line 330), `_store_state_map`, `_customer_state_map` — unchanged; E5 is orthogonal to GST split (it routes the *party/cash* leg, not the tax leg).
- `backend/agents/nexus_providers.py` — `tally_build_day_voucher_xml` (line 508). Today the party/receipt leg is always `<PARTYLEDGERNAME>{party}</PARTYLEDGERNAME>` with a single `Sales A/c` (line 595) — there is NO per-tender bank/cash ledger leg, so a paid order's money instrument is invisible to Tally. E5 adds a tender-ledger leg builder this function calls.
- `backend/api/routers/settings.py` — `_get_settings_collection(name)` (line 226) + the `find_one({"_id":"default"})` pattern (lines 239-298). E5's `tender_ledger_map` is a new settings doc read through this exact pattern, layered for E2 (global→entity→store).
- `backend/api/services/cash_register.py` — pure `build_close_summary` / `compute_variance` / `variance_status`. Reused as-is; E5 only widens the inputs.
- `backend/api/services/store_credit_ledger.py` — `make_entry` / `compute_balance` (lines 27-60). Pattern precedent for E5's pure, unit-tested mapper module (no DB in the math layer).
- `backend/database/repositories/audit_chain.py` — `append_audit_entry(audit_collection, doc, db)` (line 218). E5 audits every mapping-config change and every reconciliation lock through this single hash-chained append point.
- `backend/database/repositories/order_repository.py` — `find_one_and_update` counter (line 490) and `customer_repository.py` guarded update (line 168) — the atomic-balance precedent for E5's reconciliation-snapshot lock (`find_one_and_update` with a `status: OPEN` filter guard).
- `backend/api/services/rbac_policy.py` — `POLICY` registry (line 118). E5's new endpoints get rows added here.

## Public API (functions and/or endpoints with signatures)

**Pure mapper module — `backend/api/services/tender_routing.py` (NEW, no DB):**

```python
CANONICAL_TENDERS = {"CASH","UPI","CARD","BANK_TRANSFER","GIFT_VOUCHER","LOYALTY","EMI","CREDIT","STORE_CREDIT"}

def canonicalize_tender(method: str | None, mode: str | None = None) -> str:
    """Normalize a payment row's instrument. Mirrors orders.py method|mode alias.
    Unknown/blank -> 'CASH' is FORBIDDEN here (that was the bug); -> 'UNKNOWN'."""

def resolve_ledger(tender: str, tender_map: dict, *, is_refund: bool = False) -> str:
    """Map a canonical tender -> Tally ledger name using tender_map; falls back
    to IMS_DEFAULT_LEDGERS. Refund routes to the same ledger (contra/negative)."""

IMS_DEFAULT_LEDGERS: dict = {
    "CASH": "Cash A/c", "UPI": "Bank A/c - UPI", "CARD": "Bank A/c - Card EDC",
    "BANK_TRANSFER": "Bank A/c", "GIFT_VOUCHER": "Gift Voucher Liability",
    "LOYALTY": "Loyalty Points Liability", "EMI": "EMI Finance Receivable",
    "CREDIT": "Sundry Debtors", "STORE_CREDIT": "Customer Store Credit Liability",
    "UNKNOWN": "Suspense A/c",
}

def split_payments_by_mode(payments: list[dict]) -> dict[str, dict]:
    """{tender: {"collected": x, "refunded": y, "net": x-y, "count": n}}.
    Reads each row's method|mode + amount; sign-splits like _cash_sales_for_window.
    Paise-exact: round only at the boundary."""
```

**DB-facing service — `backend/api/services/tender_reconciliation.py` (NEW):**

```python
def get_effective_tender_map(db, store_id: str|None, entity_id: str|None) -> dict   # E2 layering
def stamp_payment_ledgers(db, order_doc: dict, tender_map: dict) -> dict            # denormalize ledger onto rows
def reconcile_window(db, store_id: str, start_iso: str, end_iso: str|None) -> dict  # by-mode aggregate over order.payments[]
def build_reconciliation_snapshot(db, store_id, start_iso, end_iso, user_id) -> dict
def lock_reconciliation(db, snapshot_id: str, user_id: str) -> dict                 # atomic find_one_and_update guard
```

**Endpoints (router `backend/api/routers/finance.py`, prefix `/api/v1/finance`):**

| Method | Path | Purpose |
|---|---|---|
| GET | `/tender-ledger-map` | effective map for `?store_id=&entity_id=` (with inheritance source per row) |
| PUT | `/tender-ledger-map` | upsert global/entity/store map override (maker leg of maker-checker) |
| GET | `/reconciliation/by-mode` | live by-mode breakdown for `?store_id=&from=&to=` |
| POST | `/reconciliation/snapshot` | freeze a day's by-mode reconciliation (DRAFT) |
| POST | `/reconciliation/{snapshot_id}/lock` | atomic lock (period close) |

## Data model (Mongo collection name + fields + indexes; mark new vs existing)

**`tender_ledger_map` (NEW)** — one doc per scope, read via `_get_settings_collection`:
```
_id: "global" | "entity:{entity_id}" | "store:{store_id}"
scope: "GLOBAL"|"ENTITY"|"STORE"
scope_id: entity_id | store_id | null
ledgers: { "<CANONICAL_TENDER>": "<Tally ledger name>", ... }   # partial override allowed
updated_by, updated_at (ISO, IST)
```
Index (NEW): `{_id: 1}` (implicit). No other index needed (≤ ~50 docs).

**`payment_reconciliations` (NEW)** — daily by-mode snapshot:
```
_id: ObjectId
snapshot_id: uuid
store_id, entity_id
window_start, window_end (ISO IST)
by_mode: { "<TENDER>": {collected, refunded, net, count, ledger} }
cash_expected, cash_counted, cash_variance        # mirrors cash_register close (CASH row only)
totals: {gross_collected, gross_refunded, net}
status: "OPEN" | "LOCKED"
created_by, created_at, locked_by, locked_at
```
Indexes (NEW): `{store_id:1, window_start:1}` (unique partial where status=LOCKED — one locked snapshot per store/day), `{snapshot_id:1}` unique, `{status:1}`.

**`orders` (EXISTING — additive only, never rewrites capture):** each `payments[]` row gains derived, optional fields written by `stamp_payment_ledgers`:
```
payments[].canonical_tender: str     # NEW derived
payments[].ledger: str               # NEW derived (resolved Tally ledger)
payments[].ledger_stamped_at: ISO    # NEW derived
```
No index change; `method`/`amount`/`payment_id` untouched.

**`cash_register_sessions` (EXISTING)** — `close` already stores `cash_sales/cash_refunds/cash_expenses`; E5 adds one field: `by_mode_breakdown: {...}` (NEW key, additive).

## How dependents call it (list the feature numbers/names that consume it and the exact call)

- **E5 itself / Tally Sales-JV** (`finance.py get_tally_sales_jv`, `nexus_providers.tally_build_day_voucher_xml`): per order, `ledger = resolve_ledger(canonicalize_tender(p["method"]), tender_map)` to emit the receipt/bank leg instead of a bare party debit.
- **Daily cash-register close** (`finance.close_cash_register`): `by_mode = reconcile_window(db, store_id, opened_at, now)` → store `by_mode_breakdown`; the CASH row still feeds `build_close_summary` (CASH-only variance, behavior preserved).
- **Cash-variance engine (E2 thresholds)**: reads `payment_reconciliations.cash_variance` vs per-store E2 threshold (Rs0/Rs100/Rs500) — no recompute, consumes E5's snapshot.
- **Finance dashboard / P&L by tender**: `GET /reconciliation/by-mode` for the "payments by instrument" card.
- **NEXUS agent (Tally 11 PM sync)** `backend/agents/implementations/nexus.py`: calls `build_reconciliation_snapshot` then exports the JV with E5 ledgers; reconciles Razorpay (`razorpay_list_payments`) against UPI/CARD net.
- **Future advance+on-delivery model (Excel I.6, NOT built now)**: E5's `split_payments_by_mode` is forward-compatible — when payments later carry `phase: "ADVANCE"|"ON_DELIVERY"` and `edc_terminal`/`upi_handle`, the mapper keys can extend without schema break (mapper takes the row, not a fixed schema).

## Integration points (agents, MSG91, Tally, RBAC, audit)

- **Tally:** primary consumer — fixes the missing bank/cash leg in `tally_build_day_voucher_xml`. Ledger names = IMS defaults (locked decision), overridable in Settings. GST tax-leg split (`_split_output_tax`) is untouched.
- **NEXUS:** nightly snapshot + JV export; Razorpay reconciliation cross-check (read-only).
- **Audit:** every PUT `/tender-ledger-map` and every `/lock` writes via `append_audit_entry(audit_coll, {...}, db)` (hash-chained, immutable even for Superadmin per core philosophy).
- **RBAC:** add POLICY rows in `rbac_policy.py`; middleware `rbac_enforcement.py` enforces request-time.
- **MSG91:** none directly. Cash-variance breach can emit `cash.variance.over_threshold` on the event bus (`registry.dispatch_event`) for TASKMASTER → MEGAPHONE alert; E5 only raises the event, doesn't send.
- **Maker-checker (E4):** `PUT /tender-ledger-map` and `/lock` are maker-checker-eligible mutations — request carries approver PIN (E4 per-approver PIN, 60-min validity) when the change touches a LOCKED-period scope.

## RBAC (who can do what)

| Action | Allowed roles |
|---|---|
| GET `/tender-ledger-map`, GET `/reconciliation/by-mode` | SUPERADMIN, ADMIN, ACCOUNTANT, AREA_MANAGER (own scope), STORE_MANAGER (own store) |
| PUT `/tender-ledger-map` (global/entity) | SUPERADMIN, ADMIN |
| PUT `/tender-ledger-map` (store override) | SUPERADMIN, ADMIN, ACCOUNTANT |
| POST `/reconciliation/snapshot` | SUPERADMIN, ADMIN, ACCOUNTANT, STORE_MANAGER (own store) |
| POST `/reconciliation/{id}/lock` | SUPERADMIN, ADMIN, ACCOUNTANT (maker-checker for locked periods) |

Store scoping via existing `validate_store_access`. CASHIER/SALES/OPTOM/WORKSHOP: no access (read of own-store reconciliation is manager+).

## Migration impact (schema/back-compat)

- **Zero capture changes.** `PaymentCreate`, `POST /{order_id}/payments`, `posStore` untouched. Owner's skipped advance/on-delivery redesign is respected.
- All new fields on `orders.payments[]`, `cash_register_sessions` are **additive + optional** — old docs read fine (resolver derives on the fly when stamp is absent).
- **Backfill (idempotent, fail-soft):** `scripts/backfill_payment_ledgers.py` iterates paid orders, runs `stamp_payment_ledgers` with the effective map. Re-runnable; rows already stamped are skipped. No-op for orders with no payments.
- Two new collections; no rename/drop. Indexes created on startup (existing `ensure_indexes` pattern). The unique partial index on `payment_reconciliations` must be created on an empty/clean collection (new, so safe — avoids the prod dup-index blocker noted in `prod_data_blockers.md`).
- Settings layering reuses `business_settings`/`tax_settings` precedent — no new settings infra.

## Build effort (dev-days) + risk

- Pure mapper + tests (`tender_routing.py`): **1.0 d**
- Reconciliation service + E2 layering + atomic lock: **1.5 d**
- Endpoints + RBAC rows + audit wiring: **1.0 d**
- `tally_build_day_voucher_xml` tender-leg + JV balance verification: **1.0 d**
- Backfill script + dry-run report: **0.5 d**
- Settings UI (tender→ledger grid) + reconciliation-by-mode card: **1.5 d**
- Acceptance + integration tests: **1.0 d**

**Total ≈ 7.5 dev-days.**

**Risks:** (1) *Tally voucher imbalance* — adding a tender leg must net to zero against party+sales+tax; mitigate with a `assert_voucher_balanced` paise-check before emit (precedent: the existing `_jv_cgst_sgst_split` balance note at line 1976). (2) *GIFT_VOUCHER/LOYALTY/CREDIT are not cash-in* — these route to liability/receivable ledgers, NOT bank; mis-mapping inflates revenue. Defaults encode this; tests assert it. (3) *Double-count vs cash-register* — E5's by-mode is the superset; cash-register's CASH-only path must read the SAME `split_payments_by_mode["CASH"]` to avoid drift.

## Acceptance tests (intent-level, assert behavior not plumbing)

1. **Instruments stop booking as Cash.** An order paid 60% UPI / 40% CARD produces a JV whose receipt legs hit `Bank A/c - UPI` and `Bank A/c - Card EDC`, zero rupees on `Cash A/c`.
2. **Unknown tender never silently becomes Cash.** A legacy row with `method=""` maps to `Suspense A/c` (UNKNOWN), surfaced in reconciliation as `count>0` under UNKNOWN — not folded into CASH.
3. **By-mode reconciliation sums to the order total.** For a day, `sum(by_mode[*].net)` equals `sum(grand_total of paid orders)` ± paise tolerance.
4. **Cash variance unchanged.** Cash-register close variance for a CASH-only day is byte-identical to pre-E5 (CASH row drives `build_close_summary`); the only diff is the added `by_mode_breakdown`.
5. **E2 inheritance.** Store with no override inherits entity map; entity with no override inherits global; a store override for CARD wins only for that store, others inherit.
6. **Non-cash tenders book to liabilities, not bank.** GIFT_VOUCHER → liability ledger, LOYALTY → liability, CREDIT → Sundry Debtors; none appear as cash/bank inflow.
7. **Refunds contra the same ledger.** A negative UPI tender reduces `Bank A/c - UPI` net (refunded>0), never creates a positive cash entry.
8. **Tally voucher balances.** Generated JV for any order satisfies sum(debits)==sum(credits) to the paise.
9. **Lock is atomic + immutable.** Concurrent `/lock` on one snapshot: exactly one succeeds (find_one_and_update guard on `status:OPEN`); the lock writes one hash-chained audit row; a locked snapshot cannot be re-locked or edited.
10. **Capture is untouched.** `POST /{order_id}/payments` request/response is unchanged; an order created before E5 reconciles correctly via on-the-fly resolution (no stamp required).

## Open conflicts / notes for the chair

- **Ledger granularity vs Excel I.6.** Excel splits CARD by EDC terminal (HDFC 42666319/20, AMEX) and UPI by handle. E5 v1 maps at the *instrument* level only (one CARD ledger). Forward-compatible (mapper takes the row), but if the owner wants per-EDC Tally ledgers now, the `ledgers` map keys must extend to composite (`CARD:42666319`) and capture must persist `edc_terminal` — which touches POS capture (explicitly skipped). **Recommend: instrument-level v1, defer per-EDC to the advance/on-delivery phase.**
- **EMI/CREDIT booking.** EMI down-payment is real cash; the financed balance is a receivable. E5 routes the EMI *tender row* to `EMI Finance Receivable` — but the down-payment is a separate CASH/CARD row already, so this is consistent. Confirm the chair wants CREDIT → `Sundry Debtors` (AR) rather than deferred revenue.
- **STORE_CREDIT as a tender.** `store_credit_ledger.py` exists but is not yet a `PaymentMethod` enum value — store-credit redemption currently has no tender row on orders. E5 includes `STORE_CREDIT` in the map for when it becomes a tender; flag if the chair wants that wired now (would touch capture).
- **Snapshot window = IST day.** Cash-register session window ≠ calendar IST day. E5 reconciliation snapshots default to IST day; cash-register close uses session window. These are intentionally two views over the same `order.payments[]` — chair should confirm the daily snapshot is the system-of-record for cash-variance (E2), with cash-register as the operational drawer count.

Files referenced (all absolute): `backend/api/routers/finance.py`, `backend/api/routers/orders.py`, `backend/agents/nexus_providers.py`, `backend/api/routers/settings.py`, `backend/api/services/cash_register.py`, `backend/api/services/store_credit_ledger.py`, `backend/database/repositories/audit_chain.py`, `backend/database/repositories/order_repository.py`, `backend/api/services/rbac_policy.py`. New files to create: `backend/api/services/tender_routing.py`, `backend/api/services/tender_reconciliation.py`, `scripts/backfill_payment_ledgers.py`.


## ===== E6: Reminder/segment rail (config-driven outbound) =====
I have complete grounding. Now I'll write the E6 contract.

# Engine E6: Reminder/segment rail (config-driven outbound)

A single config-driven outbound rail. Every recurring/triggered customer message in IMS — Rx-expiry, birthday, win-back/reactivation, CL-reorder, churn alert, lookbook invite, NPS/feedback, FU-Due-Today, and family-wallet OTP — becomes a `reminder_rule` row evaluated by one engine that resolves an audience (via existing segments), passes it through one ordered gate stack (consent → quiet-hours → voucher-gate → 30-day frequency cap), and queues through the one existing send path (`send_notification` → `notification_logs` → MEGAPHONE drain). E6 is the policy/eligibility layer; it does **not** introduce a second sender, a second consent store, or a second clock.

## Reuse (existing files/functions to build on — real paths)

- `backend/api/services/campaign_segments.py` — `resolve_segment(db, key, store_id, params)`, `count_segment`, `SEGMENT_KEYS`, `SEGMENT_DEFS`. Audience resolution is **already built**. E6 adds three missing segment keys here (`cl_reorder`, `churn_risk`, `fu_due_today`), reusing `_audience_row`, `_customers_query`, `_coerce_dt`, `_SCAN_LIMIT`.
- `backend/api/services/notification_service.py` — `send_notification(...)` (the honest-status PENDING queue path with DLT audit fields) and `populate_template`. E6 NEVER calls MSG91 directly; it queues through this.
- `backend/agents/implementations/megaphone.py` — `_drain_pending` (PENDING→SENT/SIMULATED/FAILED via `agents.providers`), `_dispatch_scheduled_campaigns`, the atomic-claim pattern (`update_one(filter incl. status, $set status)`), `_in_dnd_window`, `_next_dnd_end_utc_iso`. E6 adds one new tick step `_run_reminder_rules()` called from `_do_background_work`, mirroring `_dispatch_scheduled_campaigns`.
- `backend/agents/quiet_hours.py` — `in_quiet_hours`, `promo_send_allowed`, `next_quiet_end_utc_iso`. The ONE clock. E6's quiet-hours gate calls these; no new TZ logic.
- `backend/api/routers/marketing.py` — `is_opted_out(customer_id, db)` (the unified 3-signal consent check: flag + `whatsapp_consent_ledger`), `_TRANSACTIONAL_TEMPLATES`, `_enforce_promo_window`. E6's consent gate calls `is_opted_out`; transactional rules (OTP) bypass it exactly like these templates do.
- `backend/api/routers/vouchers.py` — `redeem_voucher_atomic(db, code, amount, order_id, redeemed_by)` and `issue_voucher`. The voucher-gate uses the existing voucher engine to mint/attach a code when a rule has `voucher_template`; no parallel coupon store.
- `backend/api/routers/campaigns.py` — `_audit(db, id, action, user, detail)` (immutable `campaign_audit`), `seg.resolve_segment` usage, `_enforce_store_scope`. E6 reuses `_enforce_store_scope` and an analogous audit writer.
- `backend/api/routers/settings.py` — `_get_settings_collection(name)` + the `{"_id": "default"}` doc pattern. E6's per-E2 config (global→entity→store) follows the E2 resolver convention.
- `backend/api/routers/follow_ups.py` — the `follow_ups` collection (statuses `pending/completed/skipped`; `scheduled_date <= today` = due). E6's `fu_due_today` segment reads this; rail send updates outcome. Maps Excel I.7 FU modes/statuses.
- `backend/api/services/rbac_policy.py` POLICY table (entry shape at line ~2392) — every new endpoint gets a row; coverage-lock test `tests/test_rbac_policy.py` enforces it.

## Public API (functions and/or endpoints with signatures)

**Engine service — `backend/api/services/reminder_rail.py` (NEW)**

```python
# The one place a rule's full lifecycle (resolve -> gate -> queue) lives.
def evaluate_rule(db, rule: dict, *, now: datetime|None=None,
                  dry_run: bool=False) -> dict
    # Returns {"rule_id","resolved","skipped_consent","skipped_freqcap",
    #          "skipped_quiet","skipped_no_phone","queued","voucher_minted",
    #          "errors":[...]}. dry_run resolves+gates but does NOT queue.

def passes_gates(db, rule: dict, recipient: dict, *, now=None
                 ) -> tuple[bool, str|None]
    # Ordered: consent -> quiet-hours(promo only) -> voucher precheck ->
    # 30-day frequency cap. Returns (allowed, skip_reason).

def check_frequency_cap(db, customer_id: str, *, now=None,
                        window_days:int=30, max_msgs:int=3,
                        category:str="MARKETING") -> bool
    # True if SENDING is allowed (customer under cap). Counts non-transactional
    # notification_logs rows in the window. Transactional/OTP exempt.

def record_outbound(db, customer_id: str, *, channel:str, category:str,
                    rule_id:str|None=None) -> None
    # Append to comms_ledger (the cross-rule frequency-cap source of truth).

# --- OTP (family-wallet pool redemption; LOCKED decision) ---
def send_pool_redemption_otp(db, *, primary_customer_id: str,
                             household_id: str, amount: float,
                             requested_by: str) -> dict
    # Mints a 6-digit OTP (5-min TTL, hashed at rest), queues an OTP-category
    # (transactional => bypasses consent + quiet-hours + freq-cap) message to
    # the PRIMARY member's mobile. Returns {"otp_id","expires_at","masked_to"}.
def verify_pool_redemption_otp(db, *, otp_id: str, code: str) -> dict
    # Atomic find_one_and_update (consumes on match, increments attempts on
    # miss, hard-fails after 5). Returns {"ok":bool,"reason":str|None}.
```

**HTTP — `backend/api/routers/reminders.py` (NEW)**, mounted `/api/v1/reminders`:

```
GET    /reminders/rules?store_id=&entity_id=&active=     # list resolved rules (E2 hierarchy)
POST   /reminders/rules                                  # create a rule (ReminderRuleCreate)
GET    /reminders/rules/{rule_id}
PUT    /reminders/rules/{rule_id}                        # edit (counters never editable)
DELETE /reminders/rules/{rule_id}
POST   /reminders/rules/{rule_id}/toggle                 # active on/off
POST   /reminders/rules/{rule_id}/preview               # dry-run: audience + per-gate suppression counts
POST   /reminders/rules/{rule_id}/run-now               # manual fire (admin) -> evaluate_rule
GET    /reminders/rules/{rule_id}/history               # comms sent by this rule (from notification_logs by rule_id)
```

OTP endpoints live with their consumer (E-loyalty / `points.py`), calling `send_pool_redemption_otp` / `verify_pool_redemption_otp` — not exposed as generic reminder routes.

## Data model (Mongo collection + fields + indexes; new vs existing)

**`reminder_rules` (NEW)** — the config rows (per E2 scope):
```
rule_id (str, "RMD-YYYYMMDD-XXXXXXXX")    scope: "GLOBAL"|"ENTITY"|"STORE"
entity_id (str|None)  store_id (str|None)   # which E2 level owns this row
name  rule_type: rx_expiry|birthday|winback|cl_reorder|churn_risk|
                 lookbook|feedback|fu_due_today|custom
segment_key (str, must be in seg.SEGMENT_KEYS)   segment_params (dict)
channel: "WHATSAPP"|"SMS"|"EMAIL"                template_id (str)
trigger: {kind:"CRON"|"EVENT", cron:"DAILY 09:00", event_key:str|None}
is_transactional (bool, default False)   # True => OTP/service: bypasses gates
voucher_template (dict|None)             # {type,amount,validity_days} -> mint via issue_voucher
freq_cap_exempt (bool, default False)    # rare opt-out from the global cap
active (bool)  last_run_at  last_resolved  sent_count  skipped_count  failed_count
created_by  created_at  updated_at
```
Indexes: `rule_id` unique; `(scope, entity_id, store_id, active)`; `(active, rule_type)`.

**`comms_ledger` (NEW)** — cross-rule 30-day frequency-cap source of truth (cheap to count, independent of `notification_logs` retention):
```
ledger_id  customer_id  channel  category  rule_id  campaign_id  sent_at (ISO UTC)
```
Indexes: `(customer_id, sent_at)` (the cap query); TTL index on `sent_at` 90d (cap only needs 30d).

**`pool_otp` (NEW)** — family-wallet OTP (LOCKED: pool redemption requires OTP to primary):
```
otp_id  household_id  primary_customer_id  code_hash (sha256)  amount
status: "PENDING"|"VERIFIED"|"EXPIRED"|"FAILED"  attempts (int, max 5)
created_at  expires_at (UTC, +5min)  consumed_at  requested_by
```
Indexes: `otp_id` unique; TTL on `expires_at`.

**`reminder_rules_seed` defaults** — 6 seeded GLOBAL rules (rx_expiry, birthday, winback, cl_reorder, churn_risk, feedback), all `active=False` by default (no surprise sends on deploy; mirrors `DISPATCH_MODE=off`).

**Existing, reused (no schema change):** `notification_logs` (E6 stamps `rule_id` like campaigns stamp `campaign_id`), `customers.marketing_consent`, `whatsapp_consent_ledger`, `vouchers`, `follow_ups`, `prescriptions`, `orders`, `business_settings`/E2 docs, `campaign_audit` (reused as `reminder_audit` via the same writer).

## How dependents call it (feature names/numbers + exact call)

- **Rx-expiry reminder / Birthday (MEGAPHONE current behavior)** — replace the hand-rolled `_scan_rx_expiring`/`_scan_birthdays_today` inserts with `reminder_rail.evaluate_rule(db, rule)` per active rule. MEGAPHONE tick:
  `for rule in active_cron_rules(db): reminder_rail.evaluate_rule(db, rule, now=now_ist())`
- **Reactivation / Win-back** — a `reminder_rules` row `rule_type=winback, segment_key="winback"`. No new code; config only.
- **CL-reorder (contact-lens replenishment)** — row `rule_type=cl_reorder, segment_key="cl_reorder"` (new resolver in `campaign_segments.py` reading CL purchase dates from `orders`/`prescriptions` per `test_cl_rx.py` shape).
- **Churn alert** — row `segment_key="churn_risk"`; ORACLE may emit a `churn.detected` EVENT-trigger rule via `registry.dispatch_event`.
- **Lookbook invite / Feedback (NPS)** — rows reusing `recent_buyers`/`by_store` + `NPS_SURVEY`/`GOOGLE_REVIEW_REQUEST` templates (already in `notification_service.TEMPLATES`).
- **FU-Due-Today (Excel I.7)** — row `rule_type=fu_due_today, segment_key="fu_due_today"`, resolver reads `follow_ups` (`status=pending, scheduled_date<=today`) and maps Excel FU modes (CALL/WHATSAPP/SMS/EMAIL) to channel; rail send for WHATSAPP/SMS modes, staff task for CALL/IN-PERSON.
- **Family loyalty wallet pool redemption (E-loyalty / `points.py`)** — `reminder_rail.send_pool_redemption_otp(db, primary_customer_id=..., household_id=..., amount=..., requested_by=user_id)` then `verify_pool_redemption_otp(...)` before `redeem` is allowed.
- **Campaign engine (`campaigns.py`)** — its `send_campaign` audience loop calls `reminder_rail.passes_gates(...)` + `record_outbound(...)` so manual campaigns share the same 30-day cap (today they only check consent, not frequency — this closes that gap without a second cap).
- **Voucher-gated promos** — any rule with `voucher_template` calls `vouchers.issue_voucher`-equivalent and injects `{voucher_code}` into template `variables`.

## Integration points (agents, MSG91, Tally, RBAC, audit)

- **Agents/MEGAPHONE** — E6 is invoked from MEGAPHONE's 30-min tick (`_do_background_work`) as a new step; CRON rules fire there, EVENT rules fire from `registry.dispatch_event` subscriptions (e.g. ORACLE `churn.detected`, NEXUS `stock.restocked` for "notify on restock" walkout policy in Excel I.7). The drain (`_drain_pending`) is unchanged and remains the sole dispatcher.
- **MSG91** — only via `send_notification` → `notification_logs` (PENDING) → `agents.providers.send_whatsapp/send_sms`, honoring `DISPATCH_MODE off/test/live`. No new provider client. OTP rides the same path with `category="OTP"`.
- **Tally** — none. E6 sends comms; no ledger postings.
- **RBAC** — every new endpoint added to `rbac_policy.POLICY`; request-time middleware (`rbac_enforcement.py`) enforces. Store-scoped rules use `campaigns._enforce_store_scope`/`validate_store_access`.
- **Audit** — reuse `campaigns._audit` writer (new `reminder_audit` collection) for every rule CRUD, toggle, run-now, and OTP issue/verify (who/when/what — SYSTEM_INTENT "Audit Everything"). OTP `code` is never logged (only `code_hash`, masked recipient).

## RBAC (who can do what)

- **Rule CRUD / toggle / run-now / preview** — `("ADMIN","AREA_MANAGER","STORE_MANAGER")` + SUPERADMIN (mirrors `_CAMPAIGN_ROLES`). STORE_MANAGER limited to `store_id` rules via `_enforce_store_scope`. GLOBAL/ENTITY rules require ADMIN+.
- **Rule history / preview-count** — same read roles.
- **`run-now`** — additionally rate-limited per user via `marketing._check_notification_rate` (it actually queues sends).
- **OTP send/verify** — `send_pool_redemption_otp` allowed for POS redeem roles (`SALES_CASHIER, CASHIER, STORE_MANAGER, ...`, mirrors `vouchers._REDEEM_ROLES`); the OTP goes to the **primary member**, not the staff. INVESTOR read-only everywhere (blocked by `block_investor_writes`).
- **Engine functions** are service-internal (no direct HTTP); callers carry their own gate.

## Migration impact (schema/back-compat)

- **Additive only.** Three new collections (`reminder_rules`, `comms_ledger`, `pool_otp`) + `reminder_audit`. No existing collection altered; `notification_logs` gains an optional `rule_id` tag (additive, like `campaign_id`).
- **MEGAPHONE back-compat:** the legacy `_scan_rx_expiring`/`_scan_birthdays_today` paths are migrated INTO seeded rules. To avoid double-sends during cutover, the two legacy scans are removed in the SAME PR that seeds the equivalent `active=False` rules; owner activates rules explicitly. With all E6 rules inactive, behavior = no automated reminders (safe default), drain still serves campaigns + manual sends.
- **Frequency cap is net-new restriction:** a `comms_ledger` backfill (last 30d from `notification_logs`) is run once so the cap is correct on day one; otherwise no historical sends count and the cap under-counts for 30 days (acceptable; backfill recommended).
- **E2 hierarchy:** rule resolution = STORE override → ENTITY → GLOBAL (first match per `(rule_type)`), matching the locked E2 settings precedence; no E2 schema change, uses the same `{"_id": scope_key}` doc convention.

## Build effort (dev-days) + risk

- Engine `reminder_rail.py` (evaluate/gates/freq-cap/record + OTP): **2.5 d**
- 3 new segment resolvers in `campaign_segments.py` (cl_reorder, churn_risk, fu_due_today): **1.5 d**
- Router `reminders.py` CRUD/preview/run-now/history + RBAC rows + seed: **2 d**
- MEGAPHONE tick wiring + remove legacy scans + EVENT subscriptions: **1 d**
- `comms_ledger` cap + backfill script + TTL indexes: **0.5 d**
- OTP collection + atomic verify + points.py wiring: **1 d**
- Tests (intent-level, below): **2 d**
- **Total ≈ 10.5 dev-days.**

**Risk:** MEDIUM. Money-adjacent only via voucher-gate (delegated to the already-safe `redeem_voucher_atomic`/`issue_voucher`) and OTP (atomic consume). Top risks: (1) **double-send** during legacy-scan cutover — mitigated by same-PR removal + inactive seeds + per-recipient dedupe in `resolve_segment`; (2) **frequency-cap correctness under concurrency** — two ticks could both pass the cap for the same customer; mitigate by checking the cap and writing `comms_ledger` in the same logical step and treating the cap as best-effort soft-ceiling (TRAI cap 3/30d has a 1-message tolerance), or use a guarded counter if hard exactness is required; (3) **clock drift** — eliminated by reusing `quiet_hours` (the one clock). LOW risk to revenue/POS (no POS changes).

## Acceptance tests (intent-level, assert behavior not plumbing)

1. **Cross-rule cap holds (LOCKED 3/customer/30d):** given a customer who already received 3 marketing comms in 30 days, when ANY two different active rules (e.g. winback + lookbook) resolve them, then BOTH suppress them and `skipped_freqcap` counts them; a 31-day-old comm does NOT count.
2. **Consent gate wins:** a customer with `marketing_consent=False` (or a `STOP` ledger event) is suppressed by every promotional rule, but STILL receives a `is_transactional=True` OTP rule (asserts transactional bypass parity with `_TRANSACTIONAL_TEMPLATES`).
3. **Quiet-hours defer, not drop:** a CRON promo rule evaluated at 22:30 IST queues with `scheduled_for = next 09:00 IST` and is NOT dispatched until the drain runs after 09:00; the OTP rule sends immediately at 22:30.
4. **E2 hierarchy override:** a STORE-level rx_expiry rule with a custom template overrides the GLOBAL one for that store; other stores get GLOBAL — assert the resolved template_id per store, not which doc was read.
5. **Voucher-gate atomicity:** a rule with `voucher_template` mints exactly one ACTIVE voucher per resolved recipient, the code appears in the queued message variables, and re-running the rule the same day does not double-mint (idempotent per customer+rule+day).
6. **FU-Due-Today wiring (Excel I.7):** a `follow_ups` row `status=pending, scheduled_date<=today, mode=WHATSAPP` is resolved by `fu_due_today` and queued; a `mode=CALL` row is NOT messaged (creates a staff task instead); a `completed` row is never resolved.
7. **Family-wallet OTP (LOCKED):** pool redemption requires `verify_pool_redemption_otp` to return ok before `redeem`; the OTP message goes to the **primary** member's mobile, expires after 5 min, and hard-fails after 5 wrong attempts (atomic — concurrent verifies can't both succeed).
8. **Honest status / no fake sends:** with `DISPATCH_MODE=off`, evaluating any rule produces `notification_logs` rows at `PENDING` (`dispatched=False`), zero real provider calls, and `comms_ledger` rows written for cap accounting.
9. **Preview is read-only:** `run-now` with `dry_run` returns audience size + per-gate suppression breakdown and writes NOTHING (no `notification_logs`, no `comms_ledger`).
10. **RBAC:** a STORE_MANAGER cannot create/preview a GLOBAL or another store's rule (403 at middleware), and the new routes are all present in `rbac_policy.POLICY` (coverage-lock test passes).

## Open conflicts / notes for the chair

- **Two consent/comms ledgers:** `whatsapp_consent_ledger` (consent events) already exists; E6 adds `comms_ledger` (send events) for the frequency cap. They're distinct concerns but the chair should confirm we don't instead derive the cap from `notification_logs` directly (cheaper schema, but couples the cap to log retention — I chose a dedicated ledger + TTL for a stable 30-day window).
- **Frequency-cap scope ambiguity:** LOCKED says "3 messages / customer / 30 days" — I assumed this counts **promotional/reminder** comms only (transactional/OTP/order-status exempt), per TRAI norms. Confirm whether reminder-class (rx-expiry, FU) counts against the cap or is exempt as service-class. This materially changes suppression volume.
- **Cap exactness vs availability:** the cross-worker double-pass edge (two MEGAPHONE workers) means the cap is a soft ceiling unless we add a guarded counter. Recommend soft (TRAI tolerates ±1); chair to confirm we don't need strict.
- **Cutover sequencing:** removing MEGAPHONE's legacy rx/birthday scans and seeding inactive rules in one PR means **no automated reminders fire until the owner toggles each rule on**. That's the safe default but is a behavior change (today rx/birthday auto-queue). Confirm the owner wants opt-in activation vs. seeding the two legacy rules as `active=True` to preserve current behavior.
- **CL-reorder data source:** there's no explicit contact-lens reorder-interval field; the resolver infers from last CL order date + a default cadence (e.g. 30/90 days by lens type). Chair to confirm the cadence source (per-SKU config vs. fixed default).
- **EVENT-triggered rules** depend on the agent event bus (`registry.dispatch_event`, Redis pub/sub) being live in prod (`REDIS_URL`). If unset, EVENT rules fall back to in-process single-worker dispatch — fine for one Railway worker, but flag if multi-worker EVENT rules are expected before Redis is configured.

Key files: engine `backend/api/services/reminder_rail.py` (new), router `backend/api/routers/reminders.py` (new), segment additions `backend/api/services/campaign_segments.py`, tick wiring `backend/agents/implementations/megaphone.py`, gates reused from `backend/api/routers/marketing.py` (`is_opted_out`) + `backend/agents/quiet_hours.py` + `backend/api/routers/vouchers.py` (`redeem_voucher_atomic`/`issue_voucher`) + `backend/api/services/notification_service.py` (`send_notification`).


## ===== PM: Unified Product Master (foundation) =====
I now have a complete grounding. There are two parallel product surfaces today: the canonical `products` collection (POS/billing reads, flat fields, `category` enum, GST from table, unique SKU index) and a second `catalog_products`/`catalog_variants` PIM lineage (BVI/Shopify, `attributes` dict, Shopify GIDs). The Excel spec asks for ONE category-conditional master with a strict SKU rule and the Shopify superset. Here is the buildable contract.

# Engine PM: Unified Product Master (foundation)

The system today has **two divergent product surfaces** that must be unified behind one engine:
1. `products` (canonical, POS/billing) — flat fields, `category` enum, unique `sku`, GST from `gst_rates` table. Written by `backend/api/routers/products.py` via `ProductRepository`.
2. `catalog_products` + `catalog_variants` (PIM/Shopify lineage) — `attributes` dict, Shopify GIDs, two-barcode model. Written by `backend/api/routers/catalog.py`.

The category→prefix→required-field spec (`No of fields required product category wise.xlsx`) and the 70-column Shopify PIM (`Shopify sheet...xlsx`) are the authoritative schema. **The engine is a thin service layer (`product_master`) that owns category schema, SKU minting, and validation; `products` stays the billing/stock spine; `catalog_products` becomes the editable PIM superset hung off it.** No collection is dropped.

## Reuse (existing files/functions to build on — real paths)

- `backend/api/routers/catalog.py` — `ProductCategory` enum (12 codes incl. SMTSG/SMTFR/SMTWT), `CATEGORY_FIELDS` (per-category required/optional/field-specs), `CATEGORY_NAMES`, `generate_sku()`, `_next_sku_counter()` (atomic `counters` collection `$inc`, multi-worker safe), `generate_product_title()`. **Extract these into the new engine module; keep router thin.**
- `backend/api/services/gst_rates.py` — `GST_CATEGORY_TABLE`, `gst_rate_for_category()`, `hsn_for_category()`, `resolve_gst_rate()` (DB-override layer). Engine calls these; never re-derives GST.
- `backend/api/services/pricing_caps.py` — `evaluate_offer_price()` (MRP≥offer guard), `CATEGORY_DISCOUNT_CAPS`. Engine reuses for the offer>MRP block + `discount_category` validation.
- `backend/api/services/barcode.py` — `validate_ean13()`, `allocate_sequence()`. Engine reuses for GTIN/barcode validation.
- `backend/database/repositories/product_repository.py` — `ProductRepository.find_by_sku/find_by_category/create`. Engine writes the billing spine through this.
- `backend/database/schemas.py` — `PRODUCT_SCHEMA` (line 92), `CATALOG_VARIANT_SCHEMA` (line 1453), `ECOM_SUBDOC_SCHEMA` (line 1495), `INDEXES["products"]` (line 651). Extend these; do not rewrite.
- `backend/api/routers/products.py` — `_build_product_data()`, `_validate_category_or_422()`, `_assert_mrp_ge_offer()`, `ProductCreate`/`ProductUpdate`. Refactor `create_product`/`update_product` to delegate to the engine.
- `backend/api/dependencies.py` — `get_product_repository()`. Add a sibling `get_product_master()`.
- Frontend: `frontend/src/pages/catalog/AddProductPage.tsx` (6-step wizard, `validateCurrentStep`), `ProductAddShell.tsx`, `frontend/src/constants/gst.ts`, `frontend/src/services/api/products.ts`. The category/field/SKU-preview endpoints already exist (`GET /catalog/categories/{category}/fields`); reuse them.

## Public API (functions and/or endpoints with signatures)

New service module `backend/api/services/product_master.py`:

```python
# --- Category schema (single source of truth, served to FE) ---
def category_spec(category: str) -> CategorySpec
    # -> {code, name, sku_prefix, required: [field], optional: [field], superset: [field]}
def all_category_specs() -> list[CategorySpec]
def required_fields(category: str) -> list[str]

# --- SKU rule: PREFIX + BRAND + MODEL + COLORCODE + SIZE ---
def build_sku(category: str, attributes: dict, db=None) -> str
    # deterministic concat per Excel rule; sanitises to [A-Z0-9/], uppercases,
    # appends atomic counter suffix ONLY on collision (see Open conflicts)
def parse_sku(sku: str) -> ParsedSku | None      # reverse for search/dedupe

# --- Validation (raises ProductMasterError -> mapped to HTTP) ---
def validate_attributes(category: str, attributes: dict) -> None
    # every required field present+non-empty; rejects unknown category
def normalise_payload(payload: ProductMasterCreate) -> dict
    # GST/HSN derived (gst_rates), offer<=MRP (pricing_caps), discount_category,
    # category-conditional coercion (e.g. CL expiry_date required), title built

# --- Persistence (writes BOTH spine + PIM atomically-ish, fail-soft) ---
def create_product(payload: ProductMasterCreate, actor: str) -> ProductMasterDoc
def update_product(product_id: str, patch: ProductMasterUpdate, actor: str) -> ProductMasterDoc
def get_product(product_id_or_sku: str) -> ProductMasterDoc | None
def list_products(filters: ProductFilter) -> Page[ProductMasterDoc]
```

New/refactored endpoints (router `backend/api/routers/product_master.py`, prefix `/api/v1/products` — **subsumes the legacy split**, with `/catalog/*` and `/products/*` kept as thin proxies for back-compat):

```
GET  /api/v1/products/categories                      -> all_category_specs()        [AUTHENTICATED]
GET  /api/v1/products/categories/{category}/fields    -> category_spec(category)      [AUTHENTICATED]
POST /api/v1/products/sku-preview                      {category, attributes} -> {sku} [CATALOG_ROLES]
POST /api/v1/products                                  ProductMasterCreate -> {product_id, sku} [CATALOG_ROLES]
PUT  /api/v1/products/{product_id}                     ProductMasterUpdate -> doc      [CATALOG_ROLES]
POST /api/v1/products/bulk-create                      [ProductMasterCreate] -> {created, errors[]} [ADMIN/CATALOG_MANAGER]
GET  /api/v1/products / /{id} / /sku/{sku}             (unchanged, served from engine)
```

`ProductMasterCreate` (Pydantic) merges today's `ProductCreate` (products.py) + `ProductCreateInput` (catalog.py): `{category, brand, model, color, size, attributes: dict, pricing: {mrp, offer_price, cost_price, discount_category}, hsn_code?, gst_rate?, weight?, images?, ecom?: EcomSubdoc, cl_*, sph/cyl/axis/add}`. `sku` is **optional on create** (engine mints it); accepted if supplied (legacy import).

## Data model (Mongo collection name + fields + indexes; mark new vs existing)

**`products`** (EXISTING — the billing/stock spine; engine writes here via `ProductRepository`):
- Existing fields per `PRODUCT_SCHEMA` (product_id, sku, category, brand, model, variant, color, size, material, gender, mrp/offer_price/cost_price decimal, hsn_code, gst_rate, attributes, all `cl_*`, discount_category, is_active, created_*).
- **NEW additive fields** (all optional, validator already allows `attributes`): `pim_product_id` (str, FK → `catalog_products.id`), `sku_prefix` (str), `country_of_origin` (str), `warranty_months` (int), `weight_grams` (double).
- Existing indexes (line 651): `sku` unique, `category`, `brand`, `is_active`, `(brand,category)`. **NEW indexes:** `{pim_product_id:1}` sparse; `{sku_prefix:1}`; `{(category:1, brand:1, model:1, color:1, size:1)}` (dedupe + stock-grid grouping).

**`catalog_products`** (EXISTING — promoted to editable PIM superset; currently schemaless):
- Existing: `id`, `sku`, `category`, `attributes`, `pricing`, `inventory`, `shopify`, `seo`, `images`, `is_active`.
- **NEW additive (Shopify-superset, all optional)** sourced from `Shopify sheet...xlsx` cols T–BH: `subbrand`, `label` (e.g. "Limited Edition"), `full_model_no`, `shape` (enum candidate, 22 values), `frame_color`, `temple_color`, `frame_material`, `temple_material`, `frame_type`, `product_usp_1/2`, `lens_usp`, `frame_size`, `bridge`, `temple_length`, `gender_label`, `country_of_origin`, `warranty`, `configurable`; sunglass-only: `lens_colour`, `tint`, `lens_material`, `polarization`, `uv_protection`; solutions-only: `recommended_for`, `instructions`, `ingredients`, `price_per_ml`. Embed via existing `ECOM_SUBDOC_SCHEMA.category_specific`. **NEW index:** `{id:1}` unique (currently only app-enforced).

**`catalog_variants`** (EXISTING, line 1453 — colour/size SKU children → stock join). Unchanged; engine ensures one variant row per minted SKU with `parent_product_id`/`parent_sku`.

**`counters`** (EXISTING) — `_id="sku:{prefix}"` atomic `$inc`. Reused unchanged.

**NEW collection `product_category_specs`** (optional, owner-overridable): per-category required/optional/superset field config so SUPERADMIN can add a category or mark a field required without a deploy. Falls back to the in-code `CATEGORY_FIELDS` (fail-soft, mirrors the `hsn_gst_master` override pattern). Indexes: `{code:1}` unique.

## How dependents call it (list the feature numbers/names that consume it and the exact call)

- **#6 Serial tracking** — at GRN, calls `product_master.get_product(sku)` to read the per-category `serialized` flag (HA always serial; opt-in per SKU per LOCKED serial decision); stock_units rows key off the minted SKU.
- **#10 Ageing/non-moving** — joins `reports/inventory/non-moving-stock` on `products.sku`; reads `category`+`sku_prefix` for category-bucketed ageing.
- **#36 Shopify sync** — NEXUS agent (`backend/agents/nexus_providers.py`) reads `catalog_products.ecom` superset (`shape`, `tags`, `seo`, `polarization`, `uv_protection`) for the storefront PDP; pushes per `catalog_variants` GTIN; `online_catalog.online_status_for_skus()` joins on SKU.
- **Stock grids (Power Grid / Rapid Grid)** — `POST /products/bulk-create` per row; group by `(category,brand,model,color,size)` index.
- **POS** (`backend/api/routers/orders.py`) — reads `products` spine for `gst_rate`/`hsn_code`/`offer_price`/`discount_category` at billing; **untouched** (no POS payment redesign per LOCKED).
- **Pricing engine** — `pricing_caps.evaluate_offer_price` already shared; engine is the single writer enforcing offer≤MRP and the future cost+10% floor (LOCKED price floor) at create/update.
- **Catalog Autopilot** (`catalog_autopilot.py`) and **AddProductPage** wizard — call `categories/{category}/fields` + `sku-preview` + `POST /products`.

## Integration points (agents, MSG91, Tally, RBAC, audit)

- **Agents:** NEXUS (Shopify push from PIM superset); SENTINEL can subscribe to a new `product.created` / `product.price_changed` event via `backend/agents/registry.py::dispatch_event` (fail-soft, in-process when no Redis).
- **MSG91:** none directly (no customer comms on product CRUD).
- **Tally:** none at master level; HSN/GST written here flow into the existing sales-JV (`payroll_exports`/finance) via order lines — ledger names unchanged (LOCKED E5).
- **RBAC:** all write endpoints gated through `require_roles(*_CATALOG_ROLES)` and registered in `backend/api/services/rbac_policy.py` so the `rbac_enforcement` middleware double-checks. Category-spec read = AUTHENTICATED.
- **Audit:** every create/update/delete writes an immutable `audit_log` entry (`{actor, action, product_id, before, after, ts}`) — Audit-Everything / Fail-Loudly. Price changes log old→new MRP/offer (mirrors `ProductRepository.update_price` `price_updated_by`).

## RBAC (who can do what)

| Action | Roles |
|---|---|
| Read categories/fields/SKU-preview | All authenticated |
| List/get products | All authenticated (store-scoped read) |
| Create / update product | SUPERADMIN, ADMIN, CATALOG_MANAGER |
| Bulk-create | SUPERADMIN, ADMIN, CATALOG_MANAGER |
| Edit category-spec (`product_category_specs`) | SUPERADMIN only |
| Soft-delete | SUPERADMIN, ADMIN |
| Edit PIM/Shopify superset (`ecom`) | SUPERADMIN, ADMIN, CATALOG_MANAGER |

Mirrors the existing role gates in `products.py` (`_CATALOG_ROLES`) and `catalog.py`. No store-staff/optometrist write access.

## Migration impact (schema/back-compat)

- **Additive only** — every new field is optional; `PRODUCT_SCHEMA` already permits free-form `attributes`, so no existing `products` doc fails validation. Add new fields + indexes to `schemas.py` `INDEXES`/`CATALOG_VARIANT_SCHEMA`; `database/migrations.py::_create_index` is idempotent.
- **Two-surface reconciliation (one-time backfill script `scripts/_backfill_product_master.py`, dry-run default):** (a) for each `products` row, create/link a `catalog_products` PIM doc + `catalog_variants` row (set `pim_product_id`/`parent_product_id`); (b) re-derive `sku_prefix` from `category`; (c) leave existing SKUs **as-is** (do not re-mint — POS/stock/Shopify reference them).
- **SKU rule:** new rule (`PREFIX+BRAND+MODEL+COLORCODE+SIZE`) applies to **new** products only; legacy `SG-BR-MODELCOL-1001` and Shopify-style `FRBURBERRYB31421109/7155` SKUs stay valid (SKUs can contain `/`, per Excel — validator must allow `/`).
- **Back-compat:** `/catalog/products` and `/products` endpoints keep working (proxy to engine); the in-memory `CATALOG_PRODUCTS` fallback stays for offline/test.
- **GST:** no rate changes — engine routes through the existing `gst_rates` table/override.

## Build effort (dev-days) + risk

- Engine module + category-spec extraction + SKU build/parse + validation: **2.5d**
- Endpoint refactor (`/products`, `/catalog` proxies, `sku-preview`) + RBAC registry rows: **1.5d**
- Schema/index additions + migration backfill script (dry-run + apply): **1.5d**
- PIM superset fields + `catalog_products` editable surface wiring: **1d**
- Frontend: point AddProductPage at engine endpoints, surface superset fields conditionally: **2d**
- Tests (intent-level) + audit wiring: **1.5d**
- **Total ≈ 10 dev-days.**

**Risk:** *Medium.* Highest risk = the two-surface backfill (must not re-mint live SKUs referenced by POS/stock/Shopify — guarded by leaving SKUs untouched + dry-run). Secondary = SKU collisions on the deterministic rule (mitigated by atomic counter suffix). Low risk on the additive schema changes. **POS billing path is read-only against `products` and is not modified.**

## Acceptance tests (intent-level, assert behavior not plumbing)

1. **Category-conditional required fields:** creating a Contact Lens without `expiry_date` is rejected; creating a Hearing Aid without `serial_no` is rejected; a Frame needs brand+model_no+colour_code; assert the 422 names the missing field. (Driven by spec table, not hardcoded per test.)
2. **SKU rule:** a Burberry sunglass with model `B 3142`, colour `1109/71` mints a SKU that starts with `SG`, contains the brand+model+colourcode, and preserves the `/`; minting the same product twice yields **distinct, unique** SKUs (counter suffix), and the `sku` unique index holds.
3. **One add-flow → both surfaces:** `POST /products` for a frame creates a `products` spine row AND a linked `catalog_products` PIM doc AND a `catalog_variants` row sharing the SKU; `GET /products/sku/{sku}` and the online-status join both resolve it.
4. **GST is derived, never guessed:** a frame persists at 5% (HSN 9003xx), a non-corrective sunglass at 18%, a contact lens at 5% — exactly what POS bills (assert master rate == `resolve_gst_rate` for the category).
5. **Pricing invariant:** offer_price > MRP is blocked on create AND on partial update (raising offer above existing MRP, or lowering MRP below existing offer). LUXURY `discount_category` cannot be silently downgraded to MASS.
6. **Superset round-trip for Shopify:** a product saved with `shape`, `polarization`, `uv_protection`, `tags`, SEO slug exposes those exact values to the NEXUS/online-catalog reader unchanged (HTML stripped on import).
7. **Back-compat:** an existing legacy `products` row (no PIM link, Shopify-style SKU) still lists, gets, and sells without modification; the backfill in dry-run reports the link it *would* create without writing.
8. **Audit:** every create/update/delete emits an immutable `audit_log` row with actor + before/after; a price change records old→new.

## Open conflicts / notes for the chair

1. **SKU determinism vs uniqueness.** Excel says SKU = pure concat (`FRBURBERRYB31421109/7155` — no counter). Current `generate_sku` appends a numeric counter. Two genuinely-different products can share brand/model/colour/size, so a pure concat collides. **Recommendation:** mint pure-concat first; append `-{counter}` **only on collision** (keeps the Excel form for the common case, guarantees uniqueness). Chair to confirm.
2. **Two collections or one?** This design keeps `products` (billing spine) + `catalog_products` (PIM) as **two linked collections** rather than collapsing into one mega-doc, because POS/stock/orders all key off `products.sku` and `catalog_variants` already exists with that lineage. Collapsing would be a large, POS-touching rewrite. Chair: confirm two-surface-with-link over single-collection.
3. **Category enum mismatch.** `products` enum uses long names (`FRAME`, `OPTICAL_LENS`, `WATCH`); `catalog.py` uses short codes (`FR`, `LS`, `WT`); `gst_rates` maps both. The engine must canonicalise to one set. **Recommendation:** store long name in `products.category` + short code in `sku_prefix`; engine translates. Confirm canonical set.
4. **Lens (`LS`) has no model/colour** — its identity is brand+index+coating+add-ons. SKU rule `PREFIX+BRAND+MODEL+COLOR+SIZE` doesn't fit lenses cleanly; for `LS` the engine should substitute `INDEX+COATING` for the MODEL/COLOR slots. Confirm.
5. **HEARING_AID GST = 0% (NIL)** in the table but devices vs parts differ; flagged in `gst_rates` already. No engine action, but note for go-live.
6. **`shape`/`frame_material`/`USP` enums are dirty in source** (typos: `AVAITOR`/`AVIATOR`, `STLISH`/`STYLISH`). Per LOCKED "every legacy colour-flag becomes an explicit status enum" — recommend the engine normalises to a clean enum with an alias map on import, but keep them free-text on the PIM superset to avoid blocking imports. Confirm enum-vs-freetext for these merch attributes.

Key files: `backend/api/services/product_master.py` (NEW), `backend/api/routers/product_master.py` (NEW), `backend/api/routers/catalog.py:200` (`CATEGORY_FIELDS` to extract), `backend/api/routers/products.py:287` (`ProductCreate` to merge), `backend/api/services/gst_rates.py:44`, `backend/api/services/pricing_caps.py`, `backend/database/schemas.py:92` (`PRODUCT_SCHEMA`) + `:1453` (`CATALOG_VARIANT_SCHEMA`) + `:651` (`INDEXES`), `backend/database/repositories/product_repository.py`, `frontend/src/pages/catalog/AddProductPage.tsx`.


## ===== SC: Staff Scorecard + Slab-Incentive engine (foundation) =====
I have full grounding. The payroll engine accepts an `incentive` float kwarg (line 223). Now I'll write the architecture contract.

# Engine SC: Staff Scorecard + Slab-Incentive engine (foundation)

The PUNE 9-component scorecard → eligibility tier → monthly slab-pool × weightage → manager bonus is **already ~70% built** across two routers and two pure calculators. The "engine" today is *implicit*: business logic is duplicated in private router helpers (`points.py::_conversion_score_for`, `payout.py::_aggregate_sales`/`_build_mtd_data`/`_compute_payout`) and split from the truly-pure math in `points_calculator.py` + `payout_calculator.py`. This contract extracts ONE importable engine surface, closes the gaps to the full PUNE model (Product-Incentive Kicker, Visufit source, E2 hierarchy, Payroll feed, model-of-record tier reconciliation), and makes every dependent call one function.

## Reuse (existing files/functions to build on — real paths)

| Path | What to reuse | Verdict |
|---|---|---|
| `backend/api/services/points_calculator.py` | `CATEGORY_MAX`, `TOTAL_MAX`, `compute_total`, `apply_visufit_gate`, `compute_eligibility`, `aggregate_mtd`, `leaderboard_sort_key` | Keep as-is; becomes the engine's daily-scoring core. The 9-component ceilings + tier walk already match Excel I.8. |
| `backend/api/services/payout_calculator.py` | `compute_targets`, `compute_multiplier` (floor-rounding + discount-kill), `compute_best_level`, `compute_pools` (best-level-only), `compute_individual_payouts`, `compute_manager_bonuses`, `assemble_payout` | Keep; this is the slab-pool math. Verified against Excel `Dashboard`/Settings worked example (L1 2210000 × 0.01 × 1.4 multiplier @ 14% discount; weightages 0.22/0.24/0.27/0.05/0.10/0.10/0.02 sum=1.0; manager bonus 0.25/0.30/0.35). |
| `backend/api/routers/points.py` | `_conversion_score_for` (footfall-vs-walkout auto-calc), `_build_row`, `_save_row`, RBAC sets `_GLOBAL_ROLES`/`_STORE_ROLES`/`_LOG_ANY_STAFF_ROLES`/`_DELETE_ROLES`, `_resolve_store`, `_audit` | **Extract** the business helpers down into the engine; the router keeps only HTTP glue. |
| `backend/api/routers/payout.py` | `_aggregate_sales`, `_last_year_sale`, `_build_mtd_data`, `_name_lookup_for`, `_month_window`, `_compute_payout`, `can_access_store_scoped` IDOR guard | **Extract** `_compute_payout` → engine; keep `/preview`,`/lock`,`/snapshots`,`/mark-paid`,`/export` HTTP-only. |
| `backend/database/repositories/points_log_repository.py` | `create_points_log` (DuplicateKeyError→409), unique partial index `(store_id,date_str,staff_id) WHERE deleted_at=null`, `list_for_mtd`, `soft_delete` | Reuse unchanged. |
| `backend/database/repositories/payout_snapshot_repository.py` | `create_snapshot`, `find_locked`, `mark_paid`, unique partial index `(store_id,year,month) WHERE status="LOCKED"` | Reuse; **add** `PAID` transition feed-marker (see Data model). |
| `backend/database/repositories/incentive_settings_repository.py` | `get_for_store`, `_defaults`, `update_eligibility_bands`, `update_visufit_gate` | **Extend** to honor E2 global→entity→store resolution (currently store-only). |
| `backend/database/repositories/walkin_counter_repository.py::get_today(store_id, date_str)` · `walkout_repository.py::list_walkouts(...)` | Conversion-score inputs (manual footfall vs logged walkouts) | Reuse; already wired in `_conversion_score_for`. |
| `backend/api/services/payroll_engine.py` (`_earnings` L140; `compute_payslip(..., incentive: float=0.0)` L223/287/312) | The **Payroll feed sink** — payroll already accepts a per-employee `incentive` float | Engine exposes `get_incentive_for_payroll(...)`; payroll-run reads it. No payroll-engine change required. |
| `backend/api/services/csv_safe.py` (`safe_writer`, `BOM`) | Formula-injection-safe export | Reuse for the engine's export builder. |
| `frontend/src/pages/incentive/*` + `services/api/{incentive,payout,walkouts}.ts` | DailyScorecardPage, MTDLeaderboardPage, PayoutDashboardPage, PayoutSnapshotsPage, PointsHistoryPage, IncentiveSettingsPage | Keep; only add a Kicker/Product-Incentive panel + a "Visufit % source" indicator + entity-level settings tab. |

## Public API (functions and/or endpoints with signatures)

**New engine module: `backend/api/services/scorecard_engine.py`** (the ONE foundation; pure-ish — DB only via injected repos, never `get_db()` directly, so it's unit-testable like the calculators).

```python
# ---- Daily scoring ----
def score_daily(payload: DailyScoreInput, *, settings: dict,
                conversion_provider: Callable[[str,str,str], Optional[int]] | None,
                kicker_provider: Callable[[str,str,str], dict] | None,
                visufit_provider: Callable[[str,str,str], Optional[float]] | None,
                ) -> dict:
    """Compose one points_log row: auto-fill conversion (today only), fold in
    Product-Incentive kicker, apply Visufit gate, total, snapshot eligibility.
    Pure: providers are the only outside world. Wraps points_calculator.*"""

def aggregate_mtd_scores(rows: list[dict]) -> dict[str, dict]:   # = points_calculator.aggregate_mtd
def leaderboard(rows: list[dict]) -> list[dict]                  # sorted via leaderboard_sort_key

# ---- Conversion auto-calc (footfall vs walkouts) ----
def conversion_score(store_id: str, date_str: str, staff_id: str,
                     *, walkout_repo, walkin_repo) -> Optional[int]   # = extracted _conversion_score_for

# ---- Product-Incentive Kicker (NEW) ----
def kicker_for(store_id: str, ym: str, staff_id: str, *, kicker_repo) -> dict
    # -> {"product_incentive_amount": float, "kicker_points": {"kicker_1": int|None, "kicker_2": int|None}, "sale_count": int}

# ---- Monthly payout (slab pool) ----
def compute_payout(store_id: str, year: int, month: int, *, settings: dict,
                   sales_provider, last_year_provider, mtd_data: dict,
                   name_lookup: dict, overrides: dict | None = None) -> dict
    # wraps payout_calculator.assemble_payout; adds product_incentive_total per staff

# ---- Payroll feed (NEW; the "feeds Payroll" half of scope) ----
def get_incentive_for_payroll(store_id: str, year: int, month: int,
                              *, snapshot_repo) -> dict[str, float]
    """{staff_id: total_incentive_rupees} from the LOCKED/PAID payout snapshot
       (slab payout + manager bonus + product-incentive kicker). Returns {} if
       no locked snapshot — payroll then uses 0 (fail-safe, never estimate)."""

# ---- E2 settings resolution (NEW) ----
def resolve_settings(store_id: str, *, settings_repo, store_repo) -> dict
    """global -> entity -> store override merge (LOCKED DECISION E2)."""
```

**Endpoints** — keep the existing surface (already mounted: `points_router` @ `/api/v1/incentive/points`, `payout_router` @ `/api/v1/payout`), routers become thin wrappers over the engine. **Add:**

```
POST   /api/v1/incentive/kicker/product-sale         log one premium/ZEISS-PAL sale (Product-Incentive)
GET    /api/v1/incentive/kicker/{ym}                  per-staff product-incentive MTD (store-scoped)
GET    /api/v1/payout/payroll-feed?year&month         {staff_id: incentive_rupees} (read by payroll-run)
GET    /api/v1/incentive/points/settings/effective    E2-resolved settings (global->entity->store)
```

## Data model (Mongo collection name + fields + indexes; mark new vs existing)

**`points_log`** (EXISTING) — keep all fields. **ADD** (new, optional, back-compat):
- `product_incentive_amount: float` (kicker rupees attributed to that staff/day, null-safe)
- `visufit_source: "clinical"|"manual"|null` (provenance of the gate input)
- existing unique partial index `(store_id, date_str, staff_id) WHERE deleted_at=null` — unchanged.

**`incentive_settings`** (EXISTING) — keep. **ADD** scope fields for E2:
- `scope: "global"|"entity"|"store"` (NEW; existing docs default to `"store"`)
- `entity_id: str|null` (NEW) — for entity-scoped rows
- New index: `{scope:1, entity_id:1, store_id:1}` (non-unique) for the resolution lookup.

**`incentive_inputs`** (EXISTING) — `(store_id, year, month, last_year_sale)`. Keep; **add** index `{store_id:1, year:1, month:1}` unique (currently relied on by find_one but not enforced).

**`payout_snapshots`** (EXISTING) — keep. **ADD**:
- `staff_payouts[].product_incentive: float` (NEW; kicker rolled into the locked snapshot so the Payroll feed is a single immutable read)
- `payroll_fed_at: datetime|null`, `payroll_run_id: str|null` (NEW; stamped when payroll consumes it — prevents double-feed)
- existing unique partial index `(store_id, year, month) WHERE status="LOCKED"` — unchanged.

**`product_incentive_log`** (**NEW collection**) — the Kicker source (mirrors `PRODUCT INCENTIVE FILE` Excel §II.10):
- `entry_id: str` (id), `store_id`, `date`/`date_str`, `staff_id`, `staff_name`
- `sku`/`product_id`, `brand` (e.g. ZEISS), `category` (e.g. PAL/Progressive), `description`
- `order_id: str|null` (link to the POS order line that triggered it; nullable for manual entry)
- `incentive_amount: float`, `created_by`, `created_at`, soft-delete trio (`deleted_at/by/reason`)
- Indexes: `{store_id:1, date_str:1, staff_id:1}`; unique `{order_id:1, sku:1} WHERE order_id != null` (idempotent attach so re-saving an order doesn't double-pay).

## How dependents call it (list the feature numbers/names that consume it and the exact call)

- **Daily Scorecard UI / `POST /incentive/points/daily`** → `scorecard_engine.score_daily(payload, settings=resolve_settings(...), conversion_provider=..., kicker_provider=kicker_for, visufit_provider=...)`.
- **MTD Leaderboard / `GET /incentive/points/mtd|leaderboard`** → `aggregate_mtd_scores(repo.list_for_mtd(...))` then `leaderboard(...)`.
- **Payout Dashboard / `GET /payout/preview` + `POST /payout/lock`** → `compute_payout(store, y, m, settings=..., sales_provider=_aggregate_sales, last_year_provider=_last_year_sale, mtd_data=_build_mtd_data, name_lookup=...)`.
- **Payroll module (`payroll-run`)** → `get_incentive_for_payroll(store, y, m, snapshot_repo=...)` → feeds `payroll_engine.compute_payslip(..., incentive=<per-staff>)`. (Closes the "Distinct from Payroll but feeds it" requirement.)
- **Product-Incentive (Kicker)** → POS order-finalisation OR manual entry calls `POST /incentive/kicker/product-sale`; `score_daily` reads it via `kicker_for` so the per-sale ZEISS/PAL incentive surfaces both as rupees (snapshot) and optionally as kicker_1/kicker_2 points.
- **TASKMASTER agent** → reads `get_incentive_for_payroll` / leaderboard for "auto-nudge low performers" + month-close reminders.
- **MEGAPHONE agent** → leaderboard rank → WhatsApp staff digest (utility template, DISPATCH_MODE-gated, 3-msg/30-day cap).
- **Walkout/Footfall CRM (Engine adjacent)** → `conversion_score(...)` is the shared seam; the walkout CRM owns capture, Engine SC owns the score derivation.

## Integration points (agents, MSG91, Tally, RBAC, audit)

- **Agents:** ORACLE can narrate anomalies (e.g. staff conversion drop); TASKMASTER nudges + month-close; MEGAPHONE sends leaderboard digests. All read-only against the engine. Jarvis surfaces stay SUPERADMIN-only.
- **MSG91 (MEGAPHONE):** utility-template leaderboard/achievement messages; honors `DISPATCH_MODE` + the cross-rule 3-msg/customer/30-day cap (locked decision). Staff comms reuse the same gate.
- **Tally (E5):** payout snapshot → incentive payable JV is **out of scope here** but the snapshot's per-staff totals are the source; the existing Tally salary-JV export (`payroll_exports.py`) already books incentive as part of gross once fed. No new Tally code in this engine; it produces the numbers the existing exporter consumes.
- **RBAC:** reuse `rbac_policy.py` + `rbac_enforcement.py` middleware. Daily score write = staff-self or manager; settings = SUPERADMIN; lock/mark-paid = SUPERADMIN; product-sale kicker = sales staff (own) + manager. Cross-store reads existence-hidden via `can_access_store_scoped`.
- **Audit:** every write goes through the existing `_audit` helper (`points.create/delete`, `incentive.settings.update`, `payout.lock/mark_paid`, NEW `incentive.kicker.create`, NEW `payout.payroll_fed`). Snapshots are immutable post-lock (philosophy: Audit Everything).

## RBAC (who can do what)

| Capability | Roles |
|---|---|
| Log own daily score | SALES_STAFF, SALES_CASHIER, CASHIER (self only) |
| Log any staff's daily score / bulk EOD | SUPERADMIN, ADMIN, STORE_MANAGER, AREA_MANAGER, ACCOUNTANT |
| Soft-delete a points row | SUPERADMIN (any), STORE_MANAGER (own store) |
| Log product-incentive sale (kicker) | sales roles (self), managers (any in store); POS auto-attach as system |
| View MTD / leaderboard / payout preview | SUPERADMIN, ADMIN, STORE_MANAGER, AREA_MANAGER, ACCOUNTANT |
| Edit eligibility bands / visufit gate / payout settings / weightages / supervisor bonuses | SUPERADMIN only |
| Edit **global/entity** scoped settings (E2) | SUPERADMIN only |
| Lock payout snapshot / mark-paid | SUPERADMIN only |
| Read payroll-feed | ACCOUNTANT, SUPERADMIN, ADMIN (consumed by payroll-run) |
| Individual staff sees own scores/payout | each staff their own rows; never peers' rupee figures |

## Migration impact (schema/back-compat)

- **Additive only.** New fields on `points_log`/`payout_snapshots`/`incentive_settings` are nullable/defaulted — existing rows read fine via `get_for_store`'s defaults-merge pattern.
- **`scope` backfill:** one idempotent migration stamps `scope:"store"` on existing `incentive_settings` docs so E2 resolution treats them as store-overrides (preserving today's behavior). Global/entity rows are opt-in.
- **New `product_incentive_log` collection + indexes** created via `ensure_indexes` in `database/connection.py` (same place the existing partial indexes live).
- **No breaking endpoint changes.** Routers keep their paths/shapes; engine extraction is behavior-preserving (router helpers move, callers unchanged). New endpoints are net-new.
- **Tier reconciliation (data-correctness, not schema):** Excel I.8 says `<70→0`, `70–79→0.6`, `80–94→0.8`, `95–100→1.0`. Current `DEFAULT_ELIGIBILITY_BANDS` uses half-open `[min,max)` with `{70,80}`,`{80,95}`,`{95,1000}` — **matches** (76→0.6, 84→0.8 verified against real Points-Log rows). No change needed; document the boundary convention (a clean 80 → 0.8, a 79 → 0.6).

## Build effort (dev-days) + risk

| Slice | Days | Risk |
|---|---|---|
| Extract `scorecard_engine.py` from the two routers (behavior-preserving), repoint routers, port existing tests | 1.5 | **Low** — mechanical; pure calculators already isolated. |
| Product-Incentive Kicker: collection + repo + 2 endpoints + `kicker_for` + POS auto-attach hook (feature-flagged; POS is revenue-critical) | 2.0 | **Med** — touches POS finalisation; gate behind flag, idempotent attach. |
| Payroll feed (`get_incentive_for_payroll`, snapshot `product_incentive` roll-in, `payroll_fed_at` guard, payroll-run wiring) | 1.0 | **Med** — money path; needs double-feed guard + paisa-exact rounding. |
| E2 settings hierarchy (global→entity→store resolution + scope index + backfill) | 1.0 | **Low–Med** — additive but resolution-order bugs are subtle. |
| Visufit-source wiring (replace placeholder `visufit_usage_pct_mtd` with a clinical provider; fail-soft when absent) | 0.5 | **Low** — provider seam already exists. |
| Frontend: Kicker panel, entity-settings tab, Visufit-source indicator, payroll-feed badge | 1.5 | **Low**. |
| **Total** | **~7.5 dev-days** | Highest risk = POS attach + payroll money path; both isolatable behind flags + the established atomic patterns. |

## Acceptance tests (intent-level, assert behavior not plumbing)

1. **Tier correctness (model-of-record):** a staff with daily total **76** → eligibility **0.6**; **84** → **0.8**; **96** → **1.0**; **69** → **0** (mirrors the real `2026-01-27 AKSHAY 76→0.6`, `MAHENDRA 84→0.8` rows).
2. **Conversion auto-calc:** with 10 walk-ins (manual footfall) and 3 walkouts for a staff today, `conversion_score` = round((10−3)/10 × 20) = **14**; 0 walk-ins → **0** (never divides by zero); past date with no footfall → not auto-filled.
3. **Slab pool, winner-takes-all:** last-year ₹16,72,000, this-year ₹33,00,000 (hits L3), avg discount 14% → multiplier **1.4** (floor-rounded, not 1.3), pool sized on **L3 only** (L1/L2 pools = 0); 16% discount → **discount-kill**, entire pool **0**.
4. **Per-staff payout = pool × weightage × eligibility:** Rupesh weightage 0.22, eligibility 1.0 on a ₹24,310 pool → **₹5,348.20** (matches Excel Settings row).
5. **Manager bonus stacks:** Sameer gets BOTH his individual staff payout AND `pool × 0.25(L1)/0.30(L2)/0.35(L3) × his eligibility`, not one-or-the-other.
6. **Visufit gate:** with gate enabled @ 90% threshold and a staff at 85% MTD usage → that day's `visufit` score forced to **0**, `visufit_gate_applied=true`; usage unknown (None) → gate **not** applied (no penalty for missing data).
7. **Product-Incentive Kicker:** logging a ZEISS PAL sale attributes `incentive_amount` to the seller and surfaces in `kicker_for(ym, staff)`; re-saving the same `order_id+sku` does **not** double-count (idempotent).
8. **Payroll feed integrity:** after a payout is LOCKED, `get_incentive_for_payroll` returns each staff's (slab + manager-bonus + product-incentive) rupees; payroll-run picks it up as `incentive`; a second payroll-run for the same month does **not** re-add (`payroll_fed_at` guard); no locked snapshot → payroll uses **0**, never an estimate.
9. **E2 resolution:** an entity-level eligibility-band override beats global defaults; a store-level override beats the entity; a store with no override inherits entity→global.
10. **Snapshot immutability + RBAC:** a non-SUPERADMIN cannot lock/mark-paid (403); a manager cannot read another store's snapshot (404 existence-hide); a locked snapshot's numbers never change even for SUPERADMIN.
11. **Export safety:** a staff name like `=cmd|'/C calc'!A0` is neutralised in the CSV (reuses `safe_writer`).

## Open conflicts / notes for the chair

1. **Product Incentive: Kicker points vs. separate rupee line?** Excel §II.10 logs per-sale rupee incentives independent of the /100 scorecard, while I.8 has manual `kicker_1/kicker_2` (0–10) for "premium-frame push". **Decision (per LOCKED "PRODUCT INCENTIVE = a Kicker"):** treat product-incentive as a **rupee line that also can drive kicker points**, rolled into the snapshot and the payroll feed — NOT a second parallel payout run. Confirm we do **not** want product incentive paid outside the slab snapshot.
2. **Visufit gate input has no live source.** Today `visufit_usage_pct_mtd` is caller-supplied and effectively never set in prod, so the gate is dormant. Needs a clinical/Visufit-demo source (per-staff MTD % of customers given a Visufit demo). Until that lands, gate stays fail-soft (no penalty). Chair: is the gate **store-team-level** (Excel "90% of customers", a team gate) or **per-staff**? The Excel "TEAM INCENTIVE APPLICABLE ONLY IF VISUFIT USED FOR 90% CUSTOMERS" reads as a **team-level pool gate**, but the scorecard zeroes an **individual's** visufit points — these are two different gates; current code only does the individual one.
3. **"Reviews" max:** scorecard model uses 10; one Excel Settings sheet labels it "Review Points (20)". Current engine uses **10** (matches the canonical I.8 table summing to 100). Flag if the 20 variant is authoritative for any store.
4. **Conversion retro-attribution window** (90-day walkout→conversion lookback in `_conversion_score_for`) is a heuristic the Excel doesn't specify exactly. Keep 90 days? Make it an E2 setting?
5. **Multi-store vs Pune-only.** All math is store-scoped today; weightages/supervisor-bonuses are per-store lists. Across 6 stores this means per-store config. E2 lets global defaults seed them, but **staff_weightages must remain store/entity-specific** (named people). Confirm weightages never live at global scope.
6. **Conversion "walk-ins are manual"** (Excel edge): the engine can only auto-fill conversion when footfall is entered; if a store forgets footfall, conversion silently = 0. Should the engine instead leave it **null/blocked** (fail-loudly) rather than score 0? Current code scores 0.

All file paths above are real and verified in this checkout. The dominant build cost is *extraction + the two new money seams (Kicker, Payroll feed)*, not green-field — the PUNE math already exists and is test-backed.
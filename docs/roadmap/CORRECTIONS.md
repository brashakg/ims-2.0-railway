# IMS 2.0 Roadmap — BINDING CORRECTIONS (errata)

> **Precedence: `DECISIONS.md` > `CORRECTIONS.md` > `ENGINES.md` / `engines/*` / packets.**
> This file OVERRIDES any conflicting text in the engine contracts and build packets. The
> build session MUST read the entry for an item **before** building it; an item cannot move
> to `TODO` until its corrections are folded into its packet (PROTOCOL §11). Source: the
> adversarial hardening pass — full detail in `HARDENING.md`.

---

## P0 — blockers (must be respected; a literal build without these = corruption / breakage)

### P0-1 — E1 money-guard: NO cross-collection "atomic dual-write" (it's impossible here)
- This Mongo is **standalone** (no `MONGO_REPLICA_SET`) → **zero multi-document transactions**. A single `find_one_and_update` touches **one document in one collection**.
- **DELETE** any instruction to "`$inc` two fields in the same `find_one_and_update`" across collections.
- **E1 is PHASE-A ONLY** for this handoff: a thin facade over the **existing** `vouchers` / `loyalty_accounts` / `customers.store_credit` collections (reuse `redeem_voucher_atomic`, `try_debit`, `try_debit_store_credit`). **NO new `money_accounts` SoR collection, NO migration, NO new index, NO dual-write.** Phase B/C (unified SoR) are **DO-NOT-BUILD** until a replica set + a `with_transaction` helper exist.

### P0-2 — E3 item-event: NO unit-level hash-chain on the audit head
- Do **not** route `item_events` through `audit_chain.append_audit_entry` (single global `HEAD_DOC_ID="primary"` → serializes the POS sell-path + never gets chain-verified).
- Use a monotonic `event_seq` + one `AuditRepository.create` row per material event. Drop the "immutable at the unit level" claim. (E3 is Phase-1, not in this handoff — fold before its packet → TODO.)

### P0-3 — #30/#31 incentive: SC engine is the ONLY payroll incentive feed
- F30/F31 packets are written against the OLD model (`commission_rate_percent`, `payroll_engine._earnings()` merge). **Ignore that plumbing.** SPIFF = an **SC Kicker**; clawback = a **negative SC adjustment in the locked snapshot**. Do NOT build a parallel `commission_ledger`/`spiff_log` payroll feed (double-pay risk, PROTOCOL §6). (Phase 4 — fold before TODO.)

### P0-4 — SC ↔ payroll: one incentive source of truth
- Payroll-run today calls `_fetch_incentive` (payroll.py:1332) reading the `incentives` collection. SC's `get_incentive_for_payroll` (locked snapshot) must **replace / strictly subordinate** that path — never sum both. Add an acceptance test that the two never double-count. (Phase 4.)

### P0-5 — SC test math: 14% discount → multiplier **1.1**, not 1.4
- Seeded table (`incentive_settings_repository.py:63-68`) floor-walks ascending: 0.11→1.4 … 0.14→1.1. Fix the SC example/test #3 to be self-consistent (input **11%**→1.4, OR expected **1.1**@14%). Do NOT "fix" the engine to match a wrong test. (Phase 4.)

### P0-6 — #21 needs the **E3-shim** (now defined in ENGINES.md)
- "E3-shim" = minimal `status` transition `AVAILABLE ↔ QUARANTINED` (free string, no enum change) + one `AuditRepository.create` audit row per transition + a transfers-reject guard + **exclusion of QUARANTINED from every on-hand rollup** (`product_repository.find_available` + rollups at inventory.py 107/2147/2274/2366/2731). No ledger chain. #21 builds this shim as part of its packet.

---

## P1 — fix in the item's packet before it moves to TODO

**Going-live Phase-0 items (corrections folded into TODO):**
- **E2 secret encryption:** drive encryption off the registry `secret` flag per-key via `_encrypt_value`/`_decrypt_value` — NOT `_encrypt_config` (which only matches `_SENSITIVE_FIELDS` key-names; dotted policy keys won't match → plaintext). Test: a `secret` key is ciphertext in Mongo.
- **E2 cache invalidation:** use explicit `cache.delete(key)` for the exact `(key, scope_id)` written — `delete_pattern`/`invalidate_store` are **Redis-only no-ops** in the in-memory fallback → stale policies up to 15 min. Or short-TTL policies.
- **E2 luxury-cap invariant (LOCKED):** E2 `pricing_caps` overrides may only **LOWER**, never raise. Luxury brand caps (Cartier 2% etc.) are **not E2 keys at all**. Acceptance test enforces it.
- **E2 entity fail-safe:** a store missing `entity_id` (dirty prod data) drops silently to global — never raise.
- **E6 `fu_due_today`:** `follow_ups.type` is a purpose enum, not a delivery channel; reconcile with the pre-existing `GET /due-today` (follow_ups.py:326); add a `channel` field or map type→channel + always create a staff task.
- **E6 freq-cap semantics:** commit to **soft-ceiling** (check-then-write is racy across workers); acceptance test asserts soft-ceiling, not strict exactness.
- **E6 OTP path:** transactional/OTP sends short-circuit quiet-hours + freq-cap + consent FIRST (new `is_transactional` flag, not the `_TRANSACTIONAL_TEMPLATES` allowlist).
- **#35 cost-masking:** the `_build_store_ledger` "margin" claim is false (never returns `cost_price`/`margin_pct`); audit each named call-site for the field actually existing before wrapping. Intent test: SALES_CASHIER gets `cost_*` as **present-but-null**; ACCOUNTANT gets real numbers.
- **#34 ticker:** no `orders` compound index exists — add `{created_at,status,store_id}` + server-side cache (net-new, not "verify present"). Intent test: SALES_STAFF sees only `pct_complete`; STORE_MANAGER sees `mtd_revenue`.
- **#21 quarantine:** status is a **free string (no enum)**; POS safety comes from `product_repository.find_available` filtering AVAILABLE (not a catalog guard). Needs the E3-shim (P0-6). Intent test: quarantine the only AVAILABLE unit → POS sell returns 409.

**Deferred / re-phased (NOT going live this handoff):**
- **#7 predictive purchasing → re-tagged `quickwin=NO`, risk=MED.** ORACLE `_propose_reorders` has **zero burn-rate logic** and doesn't read `orders.items`; `draft_po` emits a stub. True scope = full ~5-day build (burn-rate aggregation + new ORACLE step + reconcile two PO-draft paths). Stays BACKLOG until a scoped packet (burn-rate endpoint + dashboard) is written.
- **#46 reminders → moved to Phase 1.** Needs the **E6 full rule engine**, not the Phase-0 OTP/cap slice. Intent test: RX_EXPIRY rule `days_before=30` → exactly one PENDING `notification_log` stamped `rule_id`; second tick within cooldown → none.
- **E3 (Phase 1):** reconcile with the EXISTING `serial_numbers` collection + CRUD (inventory.py:3245+) — fold into `stock_units.serial` or bind to it (no divergent second store); quarantine/blind-count/under-audit are **net-new** (enumerate every on-hand rollup to exclude them); fix `claim_returnable_qty` name; reword "preserves CAS".
- **E4 (Phase 1):** `approve()` must be a single atomic `find_one_and_update({request_id,status:REQUESTED,consumed:false})` minting the token in the same op; add concurrency test (two approves → one token) + PIN brute-force throttle test (`pin_attempts`).
- **PM (Phase 1):** SKU rule is a **rewrite** (not reuse of `generate_sku`); legacy SKU acceptance must be format-permissive (allow `/`,`-`); additively add `HEARING_AID` to the `$jsonSchema` enum + reconcile the two divergent category enums BEFORE any add-flow test; triple-write order = spine first + compensation.
- **E5 (Phase 2):** back-port `AuditRepository.create` (not `append_audit_entry`); operates on existing `order.payments[]` only (POS capture UNCHANGED).
- **#13/#15/#16/#22/#23/#32/#41 etc.:** the cost+10%-per-category price floor (DECISIONS §9) is **net-new** (orders.py:1335 is cost+0%); it's a board item, not "already enforced." F32/F41 claims of "already satisfied" are wrong.

---

## Per-file edit log (applied by orchestrator)
- `DECISIONS.md` — §3 commission/SPIFF clarified; §4 POS-skip "does NOT block #16/#22/#23"; §2 price-floor pointer. ✅ applied
- `EXECUTION_BOARD.md` — #7→BACKLOG(not-quickwin), #46→Phase 1, #21 dep=E3-shim, #6 annotate, Phase-2 header, cost-floor item added, TODO populated. ✅ applied
- `ENGINES.md` — E3-shim contract appended; phase↔wave note. ✅ applied
- `PROTOCOL.md` — precedence + CORRECTIONS-binding + packet-before-TODO gate. ✅ applied
- `engines/*` + `_analysis/features/F30,F31` — superseded clauses overridden HERE (precedence); fold when each item's packet is written.


---

## R1 (resolution) -- money_guard = per-type single-doc collections (closes P0-1 / E1 T13)

Orchestrator call 2026-06-07 after E1 (PR #563): the unified `money_accounts` SoR is **CANCELLED**,
not deferred. `money_guard` operates per account-type on that type's own collection via single-document
`find_one_and_update` (no transactions, no replica set, ever). New balance types are added by their
owning feature as a dedicated single-doc collection and registered in `money_guard` ACCOUNT_TYPES:
- **#17 petty cash** -> `petty_cash_floats` (one doc per store).
- **#49 family wallet** -> `family_wallets` (one doc per household; pool redeem still OTP-gated to the primary member).
- **#3 consignment** -> `consignment_accounts` (one doc per vendor/agreement).
When you build #17 / #49 / #3: add the collection + register the type in money_guard; do NOT build a
unified SoR or any cross-collection write. This supersedes P0-1's "Phase B/C DO-NOT-BUILD until replica
set" -- there is no Phase B/C to build.

## R2 (2026-06-08) -- Engine packets ship BACKEND-FIRST; FE + existing-path wiring are split follow-ups

The shared-engine packets (E3, E4, E5, E6) bundle the backend engine + FE surfaces + (E3) migration of
the existing state-change paths onto the new ledger. **An engine PR delivers the backend engine + its
own new routes + backend acceptance tests + rbac coverage; the FE surfaces and the existing-path wiring
are SPLIT to named follow-up items wired by the consuming features.** An engine's DoD = its backend
acceptance + rbac-coverage-lock; the test session must NOT bounce an engine for a deferred FE/wiring that
has a tracked follow-up here. Deferred to follow-ups (added to the Phase-1 BACKLOG):
- **E4-FE** -- approval inbox / PIN-approve modal / Settings "set approval PIN" section. Wired by #25 / #26 / #27.
- **E3-wiring** -- route the existing F21 quarantine, transfers ship/receive, GRN mint, labels scan-advance,
  lens reserve/commit, and POS sell through `item_events.record_event` + a backfill. The E3 packet itself
  stages this behind a dual-write window (medium-high regression risk); consumers #2 / #9 drive it.
- **E3-return-serial-block** -- acceptance #8's return-side `SERIAL_MISMATCH` 409 hard-block in `returns.py`
  (the serial-BIND half ships in E3; the return-COMPARE half is the follow-up).
The E4/E3 engine cores themselves are adversarially verified (E4: atomic single-use approve + bcrypt/throttled
PIN + E2 tiers, 2 P1s fixed; E3: CAS recorder + monotonic event_seq + serial-reconciled, no correctness P1).

# Feature #23: Strict "Blind" End-of-Day Cash Tally & Z-Read
META: effort=M days=5 risk=MED roi=4 quickwin=no deps=none phase=3

## Existing overlap
- **CashRegisterPage.tsx** (frontend) already has till session opening by denomination and EOD closing with variance reconciliation and session history. This is a partial build of the same concept — denomination-by-denomination float entry and a closing delta. It is **not** blind (expected total is visible) and has no lock or ledger.
- **finance.py** `/cash-flow` (lines 1112–1223) tracks PAID order inflows vs expense outflows but is a reporting aggregate, not a till-session ledger.
- **period_locks** collection (finance.py:446–481) already demonstrates the "lock a period, prevent posting" pattern — the same guard used by orders, returns, and payroll.
- **audit_logs** collection is used throughout for before/after immutable trails; the same insert pattern can anchor the Cash Variance Ledger.
- **expense_category_caps** with ACCOUNTANT approval gates (expenses.py) already demonstrates maker-checker for money flows — the same role separation is needed here.
- **notifications.py** in-app bell already supports admin-targeted alerts.
- **orders** collection already has `payment_method`, `payment_status=PAID`, `grand_total`, `created_at` (BSON datetime, real Date type) — all needed to compute expected cash inflows.

## Reuse (extend, don't rebuild)
- **CashRegisterPage.tsx** — extend to add blind-mode: hide expected total until cashier submits the blind count; only then reveal variance. Replace the current open-faced EOD form with a two-step blind-submit-then-reveal flow.
- **finance.py** `/cash-flow` cash inflow aggregation — reuse the `payment_status=PAID`, `payment_method=CASH`, `store_id`, date-range Mongo aggregation to derive the expected CASH figure at Z-Read time.
- **period_locks** pattern — replicate the `find_one_and_update` lock doc pattern for `till_sessions`; a locked session cannot be re-submitted.
- **audit_logs** collection — append variance events as `action="till.zread"`, `entity_type="till_session"` with `before_state={expected}`, `after_state={counted, variance}`.
- **notifications.py** bell API — reuse `create_notification(user_id, ...)` to alert STORE_MANAGER/ADMIN when variance exceeds threshold.
- **expenses.py** APPROVED + SENT_TO_ACCOUNTANT state machine — mirror the two-step cashier-submits → manager-reviews flow.

## Data model
- **New collection: `till_sessions`**
  - `session_id` (uuid, unique index)
  - `store_id`, `cashier_id`, `cashier_name`
  - `session_date` (YYYY-MM-DD, index)
  - `opened_at` (datetime), `closed_at` (datetime or null)
  - `opening_float` (int, paisa) — starting cash declared at open
  - `opening_denominations` (dict: `{"2000":0,"500":2,"200":0,...,"1":0}`) — Indian denomination keys
  - `status`: `OPEN` → `BLIND_SUBMITTED` → `LOCKED` → `REVIEWED`
  - `blind_count_paisa` (int, null until submitted) — cashier's denomination-by-denomination tally (sum)
  - `blind_denominations` (dict, same shape as opening) — raw counts per note/coin
  - `expected_cash_paisa` (int) — system-computed at lock time (opening_float + CASH inflows − CASH payouts)
  - `variance_paisa` (int, signed) — blind_count − expected (positive = overage, negative = shortage)
  - `variance_acknowledged_by` (user_id), `variance_acknowledged_at`, `variance_note`
  - `locked_by` (user_id), `locked_at`
  - `reviewed_by` (user_id, STORE_MANAGER/ADMIN), `reviewed_at`, `review_note`
  - `zread_number` (auto-incrementing per store per date, e.g. `BV-KAND/2026-06-07/001`)
  - `order_ids_counted` (array, snapshot of CASH PAID order IDs included in expected)
  - `cash_payouts_paisa` (int) — petty-cash expenses paid out in cash during session (from `expenses` CASH payments)
  - `idempotency_key` (uuid) — prevent double-submit

- **New fields on `audit_logs`** (no schema change needed, just new `action` values):
  - `action` values: `till.open`, `till.blind_submit`, `till.lock`, `till.review`, `till.variance_acknowledged`

- **New collection: `cash_variance_ledger`** (append-only, HR/Admin view)
  - `ledger_id` (uuid)
  - `session_id`, `store_id`, `cashier_id`, `cashier_name`
  - `session_date`, `zread_number`
  - `variance_paisa` (signed int)
  - `variance_type`: `OVERAGE` | `SHORTAGE` | `BALANCED`
  - `severity`: computed at write — `CLEAN` (variance=0) | `MINOR` (≤ threshold_minor) | `MAJOR` (> threshold_minor, ≤ threshold_lock) | `CRITICAL` (> threshold_lock)
  - `auto_locked` (bool) — true if variance exceeded lock threshold and session was auto-locked
  - `acknowledged_by`, `acknowledged_at`, `review_note`
  - `created_at`

## Backend
- **POST `/api/v1/till/sessions`** — Open a till session; captures opening float + denominations; sets status=OPEN; enforces one OPEN session per (store, cashier, date); returns session_id. Role: SALES_CASHIER, CASHIER, STORE_MANAGER.
- **GET `/api/v1/till/sessions?store_id=&date=&status=`** — List sessions; store-scoped for non-HQ; returns summary rows. Role: STORE_MANAGER, ADMIN, ACCOUNTANT, SUPERADMIN.
- **GET `/api/v1/till/sessions/{session_id}/expected`** — INTERNAL: compute expected CASH (opening_float + sum of CASH PAID orders in window − cash expense payouts). Used server-side only; never exposed to cashier before blind submit. Role: STORE_MANAGER, ADMIN, ACCOUNTANT, SUPERADMIN only (cashier endpoint does NOT have this).
- **POST `/api/v1/till/sessions/{session_id}/blind-submit`** — Cashier posts denomination counts; server stores `blind_denominations` + `blind_count_paisa`; transitions OPEN → BLIND_SUBMITTED; server NOW computes `expected_cash_paisa` and `variance_paisa` (stored, not returned to cashier yet); writes `till.blind_submit` to audit_logs; idempotency_key guard (duplicate submit returns existing state). Role: SALES_CASHIER, CASHIER.
- **POST `/api/v1/till/sessions/{session_id}/lock`** — STORE_MANAGER or above calls after blind-submit; response reveals variance to manager; transitions BLIND_SUBMITTED → LOCKED; writes session to `cash_variance_ledger`; if |variance| > CRITICAL threshold, auto-notifies ADMIN via bell; writes `till.lock` to audit_logs. Role: STORE_MANAGER, AREA_MANAGER, ADMIN, SUPERADMIN.
- **POST `/api/v1/till/sessions/{session_id}/review`** — ADMIN/ACCOUNTANT marks reviewed with note; transitions LOCKED → REVIEWED; writes `till.review` to audit_logs. Role: ADMIN, ACCOUNTANT, SUPERADMIN.
- **GET `/api/v1/till/variance-ledger?store_id=&from_date=&to_date=&severity=`** — Paginated Cash Variance Ledger for HR/Admin. Role: ADMIN, ACCOUNTANT, AREA_MANAGER, SUPERADMIN.
- **GET `/api/v1/till/sessions/{session_id}/zread`** — Full Z-Read report payload (JSON) for print; includes: session metadata, opening float, denomination breakdown, expected (MANAGER+ only), blind count, variance, order count, cash payout total, reviewer. Role: STORE_MANAGER, ADMIN, ACCOUNTANT, SUPERADMIN.

All endpoints inherit the period-lock guard pattern from finance.py: if the accounting period for `session_date` is locked, POST endpoints return HTTP 423.

## Frontend
- **Extend `CashRegisterPage.tsx`** into a proper two-phase till flow:
  - **Open phase**: denomination table (Indian notes: 2000/500/200/100/50/20/10 + coins 5/2/1), total auto-summed, "Open Till" button. Expected total shown.
  - **Blind-close phase** (new): same denomination grid, but expected total and POS revenue are hidden. Cashier sees only "Enter your count." Running total shown as cashier types. "Submit Count" disables all fields and posts blind-submit. Confirmation dialog: "Once submitted, this cannot be edited. Are you sure?" No expected total is shown until manager unlocks.
  - **Manager reveal panel** (new, visible only to STORE_MANAGER+): After blind-submit, manager sees a "Lock Z-Read" button. On lock, reveals expected vs counted vs variance in a styled card (green = balanced/minor, amber = major, red = critical). One-tap lock commits to ledger.
  - **Z-Read print view** (new): Clean single-page printable layout (reuse receipt format style from `frontend/src/utils/receiptFormat.ts`). Shows store, date, cashier, zread_number, denomination breakdown, expected, counted, variance, reviewer signature line.

- **New page: `CashVarianceLedgerPage.tsx`** (under Finance or HR section, role-gated ADMIN/ACCOUNTANT/AREA_MANAGER):
  - Table: date | store | cashier | Z-Read # | variance (color-coded) | severity chip | reviewed status.
  - Filters: date range, store, severity, status.
  - Row click → session detail drawer showing full denomination breakdown + audit trail.
  - CSV export (formula-neutralized per existing CSV export pattern).
  - Running variance totals per cashier (MTD) surfaced as a summary bar.

- **Nav placement**: "Cash Register" entry already implied by CashRegisterPage; add "Variance Ledger" as a sub-item under Finance, visible to ADMIN/ACCOUNTANT/AREA_MANAGER only.

## Business rules
- **Blind enforcement hard-lock**: The `/expected` endpoint and the `expected_cash_paisa` field in the session document are NEVER returned to SALES_CASHIER or CASHIER roles at any point before the manager calls `/lock`. Middleware enforces this at the data layer, not just the UI.
- **One active session per (store, cashier, date)**: Opening a second session while one is OPEN → 409 Conflict.
- **No edit after blind-submit**: BLIND_SUBMITTED sessions cannot be re-submitted; cashier must call manager. Manager can VOID (with mandatory reason, role: ADMIN+) and re-open — voided sessions write a `till.void` audit entry and are preserved in the ledger as VOIDED (not deleted).
- **Auto-lock on CRITICAL variance**: If |variance_paisa| > CRITICAL_THRESHOLD, the lock endpoint atomically writes the session, creates the ledger entry, and fires an in-app bell notification to all STORE_MANAGER, AREA_MANAGER, ADMIN users in that store. The session is still moved to LOCKED, not blocked — the manager completes the lock and the system flags it.
- **Period lock guard**: All POST operations on a till session for a date in a locked accounting period return HTTP 423 (same as orders and expenses).
- **Denomination integrity**: `sum(denomination_value × count)` must equal the submitted `blind_count_paisa` exactly — server recomputes and rejects if there is a mismatch (prevents UI bugs passing wrong totals).
- **Expected cash formula**: `expected_cash_paisa = opening_float + sum(orders.grand_total where payment_method=CASH AND payment_status=PAID AND store_id=session.store_id AND created_at ∈ session window) − cash_payouts_paisa`. Cash payouts come from APPROVED expenses in the same window where `payment_mode=CASH` and store matches.
- **Immutable ledger**: `cash_variance_ledger` documents are insert-only. Reviews and acknowledgements add fields; they never overwrite. Matches the `audit_logs` and `loyalty_transactions` immutability pattern.
- **Z-Read number format**: Auto-incremented per (store, date) using the same atomic `counters` collection pattern as invoice serial numbers (`find_one_and_update` with `$inc` on key `till:{store_id}:{date}`).

## RBAC
- **SALES_CASHIER, CASHIER**: Open session, submit blind count. Cannot view expected total. Cannot lock. Cannot access variance ledger.
- **STORE_MANAGER**: All cashier actions + can view expected after blind-submit + lock Z-Read + view own-store variance ledger + void sessions (with mandatory reason, writes audit).
- **AREA_MANAGER, ADMIN**: All of the above across their stores + mark sessions REVIEWED + access cross-store variance ledger + acknowledge CRITICAL variances.
- **ACCOUNTANT**: Read-only access to variance ledger and Z-Read exports for reconciliation. Cannot open/close sessions.
- **SUPERADMIN**: Full access including void, override, export all stores.
- All till endpoints inherit store-scoped validation (`validate_store_access` pattern): non-HQ roles can only operate on sessions from their own `store_ids`.

## Integrations
- **Tally**: NEXUS nightly export (23:00) should include variance entries from `cash_variance_ledger` as a `Receipt/Payment` voucher in the Tally JV XML (extend `_build_tally_export` in nexus.py). OVERAGE = other income entry; SHORTAGE = loss/expense entry. This is a SUPERADMIN-configurable toggle in Tally export settings.
- **In-app bell (notifications.py)**: Notify STORE_MANAGER/ADMIN on CRITICAL variance — reuse existing `create_notification` pattern.
- **MSG91 WhatsApp (MEGAPHONE)**: Optional CRITICAL-variance WhatsApp alert to STORE_MANAGER mobile — behind DISPATCH_MODE gate, follows quiet hours (agents/quiet_hours.py). Not on by default; owner decides.
- **No Shopify/Razorpay/Shiprocket integration needed.**

## Risk notes
- **POS is revenue-critical**: This feature touches the same `orders` collection that drives all POS revenue. The expected-cash computation is READ-ONLY (aggregation) — it must never write to orders. Use a separate aggregation pipeline, not any order-mutating path.
- **Feature flag**: Gate the entire till module behind `TILL_BLIND_MODE=enabled` env var (Railway setting). When unset, `CashRegisterPage.tsx` falls back to the current open-faced behavior to prevent disruption before training is done.
- **Race condition on expected computation**: If a cashier submits their blind count and a POS sale finalizes in the same second, the expected total could miss that order. Mitigate by computing expected at `blind-submit` time (server stamps `computed_at`) and including any CASH PAID order with `created_at < computed_at`. The window is documented in the session document.
- **Indian denomination completeness**: Must include all legal Indian denominations (1, 2, 5, 10, 20, 50, 100, 200, 500, 2000 rupees) plus coins (0.50 is no longer in common circulation but the UI should allow it as an optional row). The 2000-rupee note withdrawal affects this — owner must confirm whether to include it.
- **Offline/network failure during blind submit**: If the cashier's browser loses connection mid-count, the idempotency_key on blind-submit ensures a retry posts the same data without duplication.
- **Threshold values**: The MINOR/MAJOR/CRITICAL variance thresholds are a business policy (e.g., ₹0 = clean, ₹1–50 = minor, ₹51–500 = major, >₹500 = critical). These must be configurable in Settings, not hardcoded — store in `business_settings` or a new `till_settings` singleton.

## Recommendation
Build in Phase 3 after POS stabilization. Not a quick win (requires cashier training and a settings UI for thresholds), but ROI is strong for loss prevention in multi-cashier stores. Do NOT fold into the existing CashRegisterPage as a minor patch — it requires a state-machine rework of that page. Build on the existing page's denomination grid (reuse the component), but replace the session lifecycle with the blind-submit → manager-lock two-step flow described above.

## Owner decisions
- Q: What are the variance thresholds (MINOR / MAJOR / CRITICAL in rupees) that trigger different alert levels and auto-lock? | Why: Determines how aggressively the system flags discrepancies; too tight = constant false alarms, too loose = misses real skimming. | Options: (a) ₹0/₹50/₹200 — tight, retail-grade; (b) ₹0/₹100/₹500 — standard; (c) custom per-store.
- Q: Should CRITICAL variance auto-send a WhatsApp alert to the Store Manager's mobile (via MSG91), or is the in-app bell sufficient? | Why: WhatsApp is more intrusive but catches the manager when off-site; in-app only works if they check the app. | Options: (a) in-app bell only; (b) WhatsApp for CRITICAL only; (c) WhatsApp for MAJOR and CRITICAL.
- Q: Should cash variance entries appear in the Tally JV export as income/expense entries (overages as misc. income, shortages as cash loss), or should Tally remain unaware of till discrepancies? | Why: If Tally exports include these, the books balance automatically; if not, the accountant must post manual journal entries. | Options: (a) include in Tally export (auto-reconciling); (b) exclude from Tally (accountant posts manually); (c) include but behind a toggle in Tally settings.
- Q: Should cashiers be allowed to open multiple till sessions per day (e.g., morning shift and afternoon shift), or is one session per cashier per store per day the rule? | Why: Multiple sessions require linking expected cash to a time window, not just a date; one session is simpler but breaks multi-shift stores. | Options: (a) one session per cashier per day (simpler, recommended for single-shift stores); (b) multiple sessions per cashier per day (needed for double-shift operations).
- Q: Should the 2000-rupee note denomination be included in the tally grid, given the RBI withdrawal? | Why: Some stores may still accept them temporarily; excluding it simplifies the UI for stores that have fully stopped. | Options: (a) include but mark as "withdrawn — only if held"; (b) exclude entirely; (c) configurable per store in till settings.
- Q: How long should `cash_variance_ledger` records be retained before archiving? | Why: Drives storage cost and compliance window (typically 7 years for financial records under Indian law). | Options: (a) 7 years (statutory minimum for financial records); (b) 3 years (operational); (c) indefinitely (safest, low volume).
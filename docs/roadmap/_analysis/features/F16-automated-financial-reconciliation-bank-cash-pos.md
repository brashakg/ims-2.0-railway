# Feature #16: Automated Financial Reconciliation (Bank/Cash/POS)
META: effort=L days=12 risk=HIGH roi=5 quickwin=no deps=none phase=3

## Existing overlap
IMS already has substantial finance infrastructure this feature extends directly:

- **Cash flow tracking**: `backend/api/routers/finance.py:1112-1223` aggregates POS inflows (PAID orders) and expense/vendor outflows by date. The logic exists but has no "expected vs actual" comparison.
- **Till session management**: `frontend/src/pages/finance/CashRegisterPage.tsx` handles denomination-by-denomination till opening, closing, and variance surfacing. The till variance (over/short) is already computed — it just lacks a root-cause audit trail and bank-deposit reconciliation.
- **Period locking**: `finance.py:446-481` blocks posting to closed months (HTTP 423). This is the right gating mechanism for reconciliation sign-off.
- **Payment tender capture**: `backend/database/repositories/order_repository.py:199` persists per-order `payments[]` with method (CASH/UPI/CARD/BANK_TRANSFER/EMI/GIFT_VOUCHER/LOYALTY). This is the raw source-of-truth for expected POS collections.
- **UPI auto-reconcile hook**: `backend/api/routers/webhooks.py:428-524` already calls `upi_qr.reconcile_upi_payment()` on Razorpay `payment.captured`. This is half of the POS-vs-actual match for UPI already working in prod.
- **Razorpay settlement pull**: `backend/agents/nexus_providers.py` (NEXUS agent) can pull settlements. Wire this into the reconciliation engine rather than re-building it.
- **Tally nightly export**: NEXUS at 23:00 exports sales vouchers to `tally_exports` collection. The reconciliation sign-off should gate or complement this export.
- **GSTR-2B reconciliation pattern**: `backend/api/routers/finance.py:1675-1690` and `backend/api/services/itc_reconcile.py` show the exact matched/mismatch/only-in-books/only-in-source bucket model to reuse for bank reconciliation.

## Reuse (extend, don't rebuild)
- **`finance.py` cash-flow endpoints**: Extend `get_cash_flow()` to expose per-tender daily expected totals. Add a `/reconciliation/session` family of endpoints in the same router (already the finance authority).
- **`order_repository.py:add_payment()`**: No change needed — `payments[]` is the source. Add an aggregation pipeline `recon_expected_by_tender_and_date()` on the existing collection.
- **`CashRegisterPage.tsx`**: Extend with a "Reconcile" tab showing the till session + card batch + UPI settlement side-by-side. The denomination entry form already exists; add the bank-deposit confirmation input.
- **`webhooks.py` Razorpay DLR + UPI reconcile**: Already fires on `payment.captured`. Extend `upi_qr.reconcile_upi_payment()` to write to the new `recon_sessions` collection.
- **`itc_reconcile.py` bucket model**: Copy the matched/mismatch/only-in-books/only-in-source pattern verbatim for tender reconciliation buckets.
- **`period_locks` collection + `check_period_locked()`**: Recon sign-off triggers or checks the same period lock — no new gating mechanism needed.
- **NEXUS agent `_run_integration_sync()`**: Add Razorpay settlement pull to the existing nightly sync loop rather than a standalone cron.
- **`audit_logs` collection**: All reconciliation actions (session open, match, override, sign-off) write here using the existing pattern.

## Data model

**New collection: `recon_sessions`**
```
recon_session_id    string (uuid)
store_id            string
session_date        string (YYYY-MM-DD, IST)
shift               string (MORNING | AFTERNOON | FULL_DAY)
status              enum: OPEN | PENDING_REVIEW | SIGNED_OFF | DISPUTED
till_opening_float  int (paise)
created_by          string (user_id)
created_at          datetime
signed_off_by       string (user_id)
signed_off_at       datetime
```

**New collection: `recon_lines`** (one row per tender × session)
```
recon_line_id       string
session_id          string (FK → recon_sessions)
store_id            string
session_date        string
tender              enum: CASH | UPI | CARD | BANK_TRANSFER | EMI
expected_amount     int (paise) — summed from orders.payments[] for this date/store/tender
actual_amount       int (paise) — entered by cashier or pulled from gateway
variance_paise      int (expected - actual; negative = short)
match_status        enum: MATCHED | SHORT | OVER | UNMATCHED
gateway_reference   string (Razorpay settlement_id / card batch_id / bank UTR)
gateway_raw         object (raw API response, stored for audit)
reconciled_by       string (user_id)
reconciled_at       datetime
override_reason     string (required when STORE_MANAGER overrides a variance)
override_by         string (user_id)
```

**New fields on existing `cash_register_sessions`** (extends CashRegisterPage's existing till docs):
```
recon_session_id    string (link to recon_sessions once cashier closes till)
deposit_reference   string (bank deposit slip number or UTR)
deposit_confirmed   bool
deposit_confirmed_at datetime
deposit_confirmed_by string
```

**New collection: `bank_statement_uploads`** (for manual CSV/OFX bank statement upload path)
```
upload_id           string
store_id            string
entity_id           string (for multi-entity support)
bank_account_last4  string
period_start        string (YYYY-MM-DD)
period_end          string (YYYY-MM-DD)
uploaded_by         string
uploaded_at         datetime
row_count           int
matched_count       int
unmatched_count     int
status              enum: PROCESSING | DONE | FAILED
```

**New collection: `bank_statement_rows`** (parsed lines from upload)
```
row_id              string
upload_id           string
txn_date            string
description         string
debit_paise         int
credit_paise        int
balance_paise       int
matched_recon_line  string (recon_line_id, null if unmatched)
match_confidence    float (0-1)
match_method        enum: AUTO_UTR | AUTO_AMOUNT_DATE | MANUAL | UNMATCHED
```

## Backend

**`GET /api/v1/finance/reconciliation/sessions`**
List recon sessions for a store/date range. Filters: status, store_id, date range. Returns summary (expected_total, actual_total, variance, unmatched_line_count) per session. STORE_MANAGER+ only.

**`POST /api/v1/finance/reconciliation/sessions`**
Open a new session for a store + date + shift. Auto-computes `expected_amount` per tender by aggregating `orders.payments[]` for the session window (IST date bounds, excludes DRAFT/CANCELLED). Writes `recon_lines` rows with `match_status=UNMATCHED`. Idempotent on (store_id, session_date, shift).

**`GET /api/v1/finance/reconciliation/sessions/{session_id}/lines`**
Returns all `recon_lines` for a session with expected/actual/variance per tender. Includes gateway pull status per tender.

**`PATCH /api/v1/finance/reconciliation/sessions/{session_id}/lines/{line_id}`**
Cashier or accountant enters `actual_amount` + optional `gateway_reference`. Recomputes `variance_paise` and `match_status` (MATCHED if |variance| ≤ tolerance_paise, SHORT/OVER otherwise). Stamps `reconciled_by/at`. If STORE_MANAGER overrides a non-zero variance, `override_reason` is required — written to `audit_logs`.

**`POST /api/v1/finance/reconciliation/sessions/{session_id}/pull-gateway/{tender}`**
On-demand pull from Razorpay (UPI/CARD) or Shiprocket (COD) for the session date. Calls NEXUS provider methods already in `nexus_providers.py`. Populates `actual_amount` + `gateway_reference` + `gateway_raw` on the matching `recon_line`. Gated on DISPATCH_MODE (SIMULATED when off). ACCOUNTANT/STORE_MANAGER+.

**`POST /api/v1/finance/reconciliation/sessions/{session_id}/sign-off`**
Marks session SIGNED_OFF. Requires all lines to be either MATCHED or have an `override_reason`. Stamps `signed_off_by/at`. Optionally triggers period-lock check via existing `check_period_locked()`. Writes to `audit_logs`. ACCOUNTANT/ADMIN only.

**`POST /api/v1/finance/reconciliation/bank-upload`**
Accepts CSV/OFX file (multipart). Parses rows, stores to `bank_statement_uploads` + `bank_statement_rows`. Auto-matches rows to `recon_lines` by UTR string match (exact) then by (amount ± tolerance, date ±1 day) fallback. Returns upload_id + match summary. ACCOUNTANT/ADMIN only. File size cap: 5 MB.

**`GET /api/v1/finance/reconciliation/bank-upload/{upload_id}/rows`**
Returns parsed + matched rows. Supports filter: match_method=UNMATCHED to surface exceptions only. ACCOUNTANT/ADMIN only.

**`PATCH /api/v1/finance/reconciliation/bank-upload/{upload_id}/rows/{row_id}/match`**
Manual match: cashier links a `bank_statement_rows` row to a `recon_line_id`. Stamps match_method=MANUAL. Writes `audit_logs`.

**`GET /api/v1/finance/reconciliation/summary`**
Org-level or store-level reconciliation health: sessions signed off this month, outstanding variance total (paise), oldest unsigned session date. Used by Finance Dashboard. ACCOUNTANT/ADMIN/SUPERADMIN.

**Extend `finance.py` cash-flow aggregate**: Add a helper `_expected_by_tender(store_id, date_start, date_end)` that the session-open endpoint calls. Reuses the existing order aggregation pipeline structure at `finance.py:1127-1142`.

## Frontend

**Extend `CashRegisterPage.tsx`** — add "Reconcile" tab alongside existing till open/close:
- Shows the active or last `recon_session` for today's store
- Per-tender row: Expected (auto-computed, read-only) | Actual (editable input) | Variance (colour-coded: green=MATCHED, amber=≤5%, red=>5% or SHORT)
- "Pull from Gateway" button per tender (UPI, CARD) — calls pull-gateway endpoint; shows SIMULATED badge when DISPATCH_MODE≠live
- Override input appears inline when variance is non-zero and user is STORE_MANAGER+
- "Sign Off" button (ACCOUNTANT/ADMIN only) — disabled until all lines resolved

**New page `ReconciliationDashboard.tsx`** at `/finance/reconciliation`:
- Org-level summary card: sessions signed off this month, total outstanding variance in ₹, oldest unreconciled date
- Session list (filterable by store, date range, status) — same restrained table style as FinanceDashboard
- Click row → session detail with line items + gateway raw data collapsible
- Bank statement upload panel: drag-drop CSV/OFX, auto-match results in a matched/unmatched split table (mirrors GSTR-2B reconciliation UI in `ItcReconcilePage.tsx`)
- Unmatched rows highlighted in amber with manual-match dropdown to select the correct recon_line

**Extend `FinanceDashboard.tsx`**:
- Add "Reconciliation Health" card alongside existing AR/AP aging: sessions pending sign-off count + total outstanding variance. Links to `/finance/reconciliation`.

## Business rules

- **Variance tolerance**: Amounts within ±`recon_tolerance_paise` are auto-marked MATCHED. Owner sets this threshold (see Owner Decisions). Stored in `business_settings`.
- **Override requires reason**: Any STORE_MANAGER sign-off of a non-zero variance must supply a free-text reason (minimum 10 chars). Written immutably to `audit_logs` with `before_state` = expected, `after_state` = actual.
- **Sign-off blocks**: Session cannot be SIGNED_OFF if any line is UNMATCHED (no actual entered at all). SHORT/OVER lines require an override_reason to proceed.
- **Period lock integration**: Signing off a session for a date in a locked period is blocked (HTTP 423 via existing `check_period_locked()`). Conversely, locking a period should warn if any recon sessions for that period are not SIGNED_OFF (add a pre-lock check).
- **Idempotent session creation**: Only one session per (store_id, session_date, shift). Re-opening recalculates expected amounts (re-runs aggregation) without wiping existing actual entries — preserves cashier work.
- **CASH shortfall ≥ configurable threshold triggers in-app alert**: If CASH variance is a shortfall exceeding the owner-set alert threshold, an in-app notification is queued to the STORE_MANAGER immediately (uses existing `notifications` collection + bell API). No WhatsApp — in-app only, since this is a sensitive internal matter.
- **No auto-correcting stock or orders**: Reconciliation is a financial record only. It never modifies `orders`, `payments[]`, or `stock_units`. Read aggregation only.
- **Audit immutability**: Every state change on `recon_sessions` and `recon_lines` appends to `audit_logs`. No deletes, no updates without audit.
- **Bank upload**: Only accepts standard CSV formats (comma-separated, date in first column, debit/credit in paise or rupees with configurable unit). File is stored as metadata only — raw bytes are NOT stored in MongoDB (too large); only parsed rows are persisted. The parsed `bank_statement_rows` collection has a 90-day TTL index.

## RBAC

| Role | Access |
|---|---|
| SUPERADMIN / ADMIN | Full: create/view/sign-off all stores, bank upload, view all lines including gateway_raw |
| AREA_MANAGER | View sessions + lines for their stores; cannot sign off; can pull gateway |
| STORE_MANAGER | Create session for own store, enter actuals, override with reason; cannot sign off |
| ACCOUNTANT | Enter actuals, bank upload, sign off sessions; view all stores in their entity |
| SALES_CASHIER / CASHIER | View own store's today session (read-only); cannot enter actuals or sign off |
| All other roles | No access |

Gateway raw data (`gateway_raw` field) is masked to ACCOUNTANT/ADMIN/SUPERADMIN only — STORE_MANAGER sees only the reference number, not the full API response.

## Integrations

- **Razorpay**: `nexus_providers.py` pull settlements endpoint (NEXUS already has this). Wire `pull-gateway/UPI` and `pull-gateway/CARD` to NEXUS provider. DISPATCH_MODE gated (SIMULATED when off; shows badge in UI).
- **Jarvis/NEXUS agent**: Add `reconciliation.variance_alert` event emitted when CASH shortfall exceeds threshold. TASKMASTER can subscribe and create a task for the STORE_MANAGER to investigate. Reuses existing event bus and proposals pattern.
- **Tally**: Reconciliation sign-off status should be a flag NEXUS checks before the 23:00 nightly Tally export — warn (not block) if any session for today is unsigned. Add a pre-export check in `nexus.py:_build_tally_export()`.
- **No MSG91 / WhatsApp / Shopify / Shiprocket** needed for this feature.

## Risk notes

- **POS revenue-critical**: This feature is read-only against `orders` and `payments[]`. It never writes to those collections. Still, the aggregation pipeline runs against the orders collection on session open — use a covered index on `(store_id, created_at, payment_status, status)` and time-bound the query (IST day bounds) to avoid collection scans during business hours. Session open should be an async background job if the store has >10k orders/day.
- **Gateway pull latency**: Razorpay settlement data is available T+1 business day. The pull-gateway endpoint must clearly surface "settlement not yet available" vs "no transactions" — use the `gateway_raw` field to distinguish. Show a "Settlements typically available by 11 AM next day" tooltip in the UI.
- **Bank CSV format variability**: Different banks export different CSV schemas. The parser must handle at minimum: HDFC, ICICI, SBI, Axis, Kotak. Build a bank-format config in `business_settings` (bank_csv_format: HDFC | ICICI | SBI | GENERIC). Failure to parse returns a 400 with the failing row number — never silently drops rows.
- **Rounding drift**: POS stores amounts in paise (integers). Razorpay settlements may include gateway charges deducted before credit. The `gateway_reference` must carry the settlement ID so the accountant can reconcile the deduction separately (it is an AP item, not a shortfall). Document this in the UI.
- **Feature flag**: The entire reconciliation module is behind `RECON_ENABLED=1` environment variable checked in the router. Default off. Turn on per-store via the `business_settings` singleton. This keeps the feature invisible until the owner is ready to train cashiers.
- **Multi-shift complexity**: If the owner enables multi-shift (MORNING/AFTERNOON), session creation must correctly split the order aggregation window. This is a config choice — see Owner Decisions. Incorrect split would show wrong expected amounts and create cashier confusion.

## Recommendation
Build later (Phase 3) — not a quick win due to cashier training overhead and Razorpay T+1 settlement lag, but ROI=5 (directly prevents cash pilferage and saves accountant hours at month-end). Prerequisite: cashier discipline with till open/close on CashRegisterPage must be established first. Once CashRegisterPage usage is consistent across all stores (owner can confirm via usage data), this extends it naturally with low additional code risk.

## Owner decisions
- Q: What variance tolerance (in rupees) should auto-mark a tender as MATCHED without requiring an override reason? | Why: Sets the sensitivity of the reconciliation — too tight creates noise for every rounding difference; too loose misses real shortfalls. | Options: ₹0 (exact match only) / ₹5 (covers rounding) / ₹50 (covers petty errors)
- Q: At what CASH shortfall amount should an immediate in-app alert fire to the Store Manager? | Why: Below this amount it is probably a counting error; above it warrants investigation. | Options: ₹200 / ₹500 / ₹1,000 / disable alerts
- Q: Should the reconciliation run per-shift (Morning / Afternoon) or once per day per store? | Why: Per-shift gives finer accountability but requires cashiers to hand over and open/close the till twice daily. Once-per-day is simpler but loses intra-day visibility. | Options: Once daily / Two shifts (Morning 9AM-2PM, Afternoon 2PM-close) / Configurable per store
- Q: Which bank accounts need statement upload reconciliation? | Why: The parser needs to be pre-configured for the specific bank CSV format (HDFC, ICICI, SBI, Axis, Kotak, etc.). | Options: List the bank(s) used per store/entity — one format per entity is simplest; mixed formats add parser complexity
- Q: Should a session that is NOT signed off block the Tally nightly export, or only warn? | Why: Blocking is safer (no unreconciled data goes to Tally) but could hold up accounting if a cashier is absent. Warn-only keeps Tally running but may export unreconciled data. | Options: Hard block / Soft warn + proceed / No check (reconciliation is independent of Tally)
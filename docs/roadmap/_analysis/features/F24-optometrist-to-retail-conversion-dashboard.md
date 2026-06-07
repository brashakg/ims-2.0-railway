# Feature #24: Optometrist-to-Retail Conversion Dashboard
META: effort=M days=5 risk=LOW roi=4 quickwin=yes deps=none phase=3

## Existing overlap
IMS already has all the raw data this feature needs:

- `eye_tests` collection (clinical.py:449-964) stamps `optometrist_id`, `optometrist_name`, `customer_id`, `patient_id`, `store_id`, `created_at`, and `status=COMPLETED`
- `orders` collection (orders.py) stamps `customer_id`, `store_id`, `created_at`, and has no optometrist link yet — but the same `customer_id` exists in both
- `GET /api/v1/clinical/optometrist/{id}/stats` (clinical.py, near line 1200) already returns total test count and `tested_at_store` count per optometrist — it just does not look at orders
- The Patient 360 view (`Customer360Dashboard.tsx`) already surfaces a customer's test history alongside their order history, proving the join is feasible
- Workshop jobs (`workshop_jobs`) carry `prescription_id` which links back to a test, giving a secondary signal (test → workshop job booked)
- `reports.py:58-94` already has the canonical order-revenue aggregation pattern (exclude CANCELLED/DRAFT, field-name fallback chain) that this dashboard will reuse

Nothing in IMS currently computes the conversion rate or surfaces it per-optometrist. This is genuinely new analytics, not a duplicate.

## Reuse (extend, don't rebuild)
- **`eye_tests` collection** — primary fact table; no new fields needed, just aggregate against it
- **`orders` collection** — join on `customer_id` + time window; reuse existing `reports.py` aggregation pattern
- **`GET /api/v1/clinical/optometrist/{id}/stats`** — extend this endpoint to accept `?from_date&to_date` and add `converted_count`, `conversion_rate_pct`, `revenue_from_converted`, `avg_days_to_order` to the response; do not create a new endpoint
- **`backend/api/routers/clinical.py`** — add a new route `GET /api/v1/clinical/conversion-dashboard` (list view, all optometrists for a store/date range) alongside the existing per-optometrist stats; same router, same auth guard
- **`reports.py` aggregation helpers** — reuse `_order_revenue_aggregation()` field-fallback chain and date-range pattern verbatim
- **`frontend/src/pages/clinical/ClinicalPage.tsx`** — add a "Conversion" tab alongside the existing Queue / History tabs; do not create a separate page
- **`frontend/src/components/shell/` design tokens** — use existing table + badge + card primitives; no new design work

## Data model
No new collection needed. One Mongo aggregation pipeline joining two existing collections:

New fields computed on-the-fly (not persisted — always fresh):
```
eye_tests  →  [match store+date+status=COMPLETED]
            →  lookup orders on (customer_id, order.created_at within N days of test.created_at)
            →  group by optometrist_id / optometrist_name
            →  project:
                  tests_completed   int
                  converted_count   int      # tests where ≥1 order found within window
                  conversion_rate   float    # converted / tests * 100
                  revenue_generated float    # sum grand_total of converted orders
                  avg_order_value   float
                  avg_days_to_order float    # mean(order.created_at - test.created_at) in days
                  workshop_booked   int      # tests where a workshop_job exists (secondary signal)
```

One optional **persisted snapshot** for trend history — only if the owner wants month-over-month comparison (see Owner Decisions). If yes, a lightweight `conversion_snapshots` collection:
```
{ snapshot_id, store_id, year, month, optometrist_id, optometrist_name,
  tests_completed, converted_count, conversion_rate, revenue_generated,
  avg_order_value, workshop_booked, generated_at }
```
This mirrors the `payout_snapshots` pattern already in `payout.py`.

## Backend

**Extend existing endpoint** `GET /api/v1/clinical/optometrist/{id}/stats`:
- Add query params: `from_date` (ISO date, default 30 days ago), `to_date` (ISO date, default today), `conversion_window_days` (int, default from settings or owner decision)
- Add to response: `converted_count`, `conversion_rate_pct`, `revenue_from_converted`, `avg_days_to_order`, `workshop_booked_count`
- Implementation: two-stage aggregation — first count COMPLETED tests in window, then `$lookup` orders on `customer_id` with `$expr` date filter within `conversion_window_days`

**New endpoint** `GET /api/v1/clinical/conversion-dashboard`:
- Query params: `store_id` (required for non-HQ roles), `from_date`, `to_date`, `conversion_window_days`
- Returns: array of per-optometrist rows (same fields as above), sorted by `conversion_rate` DESC
- Includes store-level aggregate row (totals) as the first element
- Uses `validate_store_access` pattern from rbac_policy.py for store scoping
- Auth: same `require_roles` as existing clinical endpoints

**Optional** `POST /api/v1/clinical/conversion-snapshot` (SUPERADMIN/ADMIN only):
- Runs the aggregation for a completed calendar month and persists to `conversion_snapshots`
- Idempotent on `(store_id, year, month, optometrist_id)`
- TASKMASTER can call this nightly on month-end (no new agent needed — hook into existing TASKMASTER `_do_background_work`)

## Frontend

**Extend `frontend/src/pages/clinical/ClinicalPage.tsx`** — add a third tab "Conversion" (alongside Queue / History):

Tab contents:
- **Date-range picker** (from/to, defaults to current month) + **store selector** (for ADMIN/AREA_MANAGER)
- **Store-level summary bar**: total tests, total conversions, overall rate %, total revenue attributed
- **Optometrist table** (sortable columns): Name | Tests | Converted | Rate% | Revenue | Avg Days to Order | Workshop Booked
  - Rate% column: colour-coded badge using existing badge primitives — green (≥ owner threshold), amber (50–threshold), red (< 50%) — colour carries semantic meaning only, no decorative use
  - Clicking a row drills into that optometrist's detail (reuses existing `/optometrist/{id}/stats` modal or a side drawer)
- **Detail drawer** (extend existing optometrist stats modal): adds conversion timeline — a simple bar chart (recharts, already a dep) showing weekly conversion rate for the selected date range; list of converted orders with order_number, amount, days-after-test

No new page route needed. No new design primitives.

## Business rules
- **Conversion window**: an eye test counts as "converted" only if an order from the **same customer** is created within N days of test completion. N is owner-configured (see Owner Decisions); default 7 days.
- **Order eligibility**: only CONFIRMED/DELIVERED/PROCESSING orders count (exclude DRAFT, CANCELLED). Uses same exclusion list as `reports.py`.
- **Attribution**: conversion is attributed to the **optometrist who conducted the test**, not the salesperson on the order. If a customer has multiple tests in the window, the most recent test before the order is the attributed one.
- **Self-purchase exclusion** (optional, owner decision): optometrist orders for themselves should not inflate their own conversion rate. Filter by `order.created_by != test.optometrist_id` if owner requests.
- **No PII exposure**: the dashboard shows optometrist names (staff, not customers) and aggregate revenue. No customer names/phones in the list view. Detail drawer shows order_number only (not customer_name), consistent with existing order list RBAC.
- **Audit**: reads are non-mutating; no audit log entry needed. Snapshot writes use the existing `agent_audit_log` pattern.

## RBAC
- **SUPERADMIN, ADMIN**: all stores, all optometrists, can trigger snapshots
- **AREA_MANAGER**: stores in their `store_ids` array only
- **STORE_MANAGER**: their own store only; can see all optometrists at that store
- **OPTOMETRIST**: their own row only (`optometrist_id == current_user.id`); no cross-optometrist view; no revenue column (they see test count + conversion count only, not revenue — revenue is a management metric)
- **ACCOUNTANT, CATALOG_MANAGER, SALES_CASHIER, SALES_STAFF, CASHIER, WORKSHOP_STAFF**: no access (404 or excluded from clinical tab)
- Implementation: extend existing `require_roles(SUPERADMIN, ADMIN, AREA_MANAGER, STORE_MANAGER, OPTOMETRIST)` guard; add a `_filter_for_optometrist(user, rows)` helper that strips cross-optometrist rows and revenue column when `activeRole == OPTOMETRIST`

## Integrations
- **Jarvis / ORACLE**: ORACLE's `_detect_sales_anomalies()` (oracle.py) can be extended to emit an `anomaly.low_conversion` proposal if a store's 30-day conversion rate drops below threshold — uses existing proposal + notification infrastructure. No new agent needed; one new anomaly check in ORACLE's hourly sweep. Proposal type: advisory (tier-3, ORACLE surfaces it, manager decides coaching).
- **TASKMASTER**: wire month-end snapshot into TASKMASTER's `_do_background_work()` via a new `_snapshot_conversion_rates()` method — consistent with existing pattern in taskmaster.py. Triggers on the 1st of each month for the prior month.
- **MSG91 / WhatsApp**: none at launch. Future: MEGAPHONE can send a weekly "your conversion rate this week" digest to Store Managers — not in scope for this build.
- **Shopify / Razorpay / Tally**: none.

## Risk notes
- **Aggregation performance**: the join between `eye_tests` and `orders` on `customer_id` + date range will do a collection scan on `orders` for each test unless indexed. Existing index on `orders` is `(store_id, created_at)`. The lookup needs `(customer_id, created_at)` — add a compound index `{customer_id: 1, created_at: 1}` on `orders` during migration. This is non-blocking (background index build on Railway).
- **Attribution ambiguity**: if a customer visits two optometrists in the same month, revenue is attributed to the most recent pre-order test. This is a deliberate simplification — document it on the UI as a tooltip. Do not over-engineer multi-touch attribution at this stage.
- **No POS/money risk**: this is read-only analytics. No pricing, no payment, no order mutation. Zero POS risk. No feature flag needed.
- **Clinical data completeness**: older eye tests (pre-session, before `optometrist_id` was stamped consistently) will appear as unattributed. The dashboard should surface an "Unattributed tests" count separately so managers know the data quality boundary.

## Recommendation
Build now (quick win). All raw data already exists in `eye_tests` + `orders`. The backend is a single aggregation pipeline extending an existing endpoint. The frontend fits inside the existing Clinical page tab structure. No new collections, no schema migrations, no POS risk. 5-day effort delivers a high-ROI coaching tool that directly converts exam volume into revenue visibility.

## Owner decisions
- Q: What is the conversion window (days after a test that an order still counts as "converted")? | Why: too short (e.g. 2 days) under-counts customers who think it over; too long (e.g. 30 days) over-credits the optometrist for walk-in sales | Options: (a) 7 days — recommended, aligns with typical lens/frame decision cycle; (b) 14 days — more generous; (c) configurable per store in Settings
- Q: Should optometrists see their own conversion rate and revenue number, or only test count? | Why: showing revenue to clinical staff can feel transactional and conflict with their professional identity; hiding it keeps clinical neutral | Options: (a) show tests + conversion rate only, hide revenue — recommended; (b) show everything; (c) show revenue only to STORE_MANAGER and above
- Q: Should a below-threshold conversion rate trigger an automatic ORACLE alert to the Store Manager? | Why: determines whether ORACLE emits a proposal (noise vs proactive coaching) | Options: (a) yes, alert when store 30-day rate drops below X% — owner sets X; (b) no, dashboard is pull-only (manager checks it); (c) weekly digest to Store Manager via WhatsApp (MEGAPHONE)
- Q: Do you want month-over-month trend history (conversion snapshots persisted per month), or is a rolling date-range picker sufficient? | Why: snapshots add a nightly TASKMASTER job and a new collection; date-range picker is simpler but loses data if old tests are purged | Options: (a) rolling date-range only — simpler, no storage cost; (b) persist monthly snapshots — enables trend charts and YoY comparison
# Feature #30: Advanced Optical & Cross-Category SPIFFs
META: effort=M days=6 risk=MED roi=4 quickwin=no deps=none phase=3

## Existing overlap
IMS already has the daily points engine (9 categories, `points_log` collection, `points.py`) and incentive settings (`incentive_settings` collection). The Structured CTC already has a `commission_rate_percent` field on `salary_config` that is stored but never consumed (`payroll_engine.py` has no commission logic — confirmed gap from HR audit). The leaderboard (30-day rolling average, MTD) in `points.py:621-645` is live. TASKMASTER tier-1 can auto-escalate goals and ORACLE can detect anomaly dips. There is no SPIFF/bonus layer, no brand/collection-level target, and no approval flow for incremental payouts.

## Reuse (extend, don't rebuild)
- `backend/api/routers/points.py` — extend the existing 9-category scoring model; add a 10th dynamic SPIFF score slot (or a parallel `spiff_log` collection keyed the same way as `points_log`)
- `incentive_settings` collection — already has `eligibility_bands`, `visufit_gate`, `payout_settings`; extend with a `spiff_programs` array
- `backend/api/services/points_calculator.py` — extend MTD aggregation to include SPIFF totals alongside base points
- `frontend/src/pages/incentive/DailyScorecardPage.tsx` — extend to show active SPIFFs and today's SPIFF hits alongside the 9 base scores
- `frontend/src/pages/incentive/MTDLeaderboardPage.tsx` — extend leaderboard card to show MTD SPIFF earnings separately from base incentive
- `frontend/src/pages/incentive/IncentiveSettingsPage.tsx` — extend settings tab to add SPIFF program management (SUPERADMIN/ADMIN only)
- `backend/api/routers/payroll.py` + `payroll_engine.py` — wire SPIFF payout into the payroll run so it lands as a line item in the salary breakup (reuses existing earnings merge pattern at `payroll_engine.py:287-290`)
- `backend/agents/implementations/oracle.py` — ORACLE already detects sales anomalies; extend to flag when a SPIFF program is underperforming (no hits in N days)
- `audit_logs` collection — all SPIFF approvals and payout calculations write here (immutable, before/after)

## Data model
- **New collection: `spiff_programs`**
  - `spiff_id` (UUID, unique)
  - `name` (str, display label e.g. "May Zeiss Push")
  - `store_ids` (array of str — which stores the SPIFF applies to; empty = all)
  - `scope` (`brand` | `collection` | `category` | `product_ids`)
  - `scope_value` (str or array — e.g. brand name "Zeiss", collection slug "house-collection-2025", category "SUNGLASS")
  - `trigger` (`per_unit_sold` | `monthly_target_hit` | `tiered_target`)
  - `tiers` (array of `{units: int, bonus_per_unit: float, lump_sum: float}`) — supports both flat-per-unit and tiered lump sums
  - `currency` always INR
  - `period_start` / `period_end` (ISO dates, financial-month aligned)
  - `eligible_roles` (array of role strings — typically SALES_CASHIER, SALES_STAFF)
  - `status` (`DRAFT` | `ACTIVE` | `PAUSED` | `CLOSED`)
  - `approved_by` / `approved_at`
  - `payout_locked` (bool — flipped true when payroll run locks the period)
  - `created_by` / `created_at` / `updated_at`

- **New collection: `spiff_hits`** (one row per qualifying sale event)
  - `hit_id` (UUID, unique, idempotent key = `order_id + line_item_id + spiff_id`)
  - `spiff_id`
  - `order_id` / `order_line_item_id`
  - `staff_id` / `staff_name` / `store_id`
  - `product_id` / `sku` / `brand` / `collection`
  - `units_sold` / `unit_bonus` / `hit_bonus` (computed at sale time, frozen)
  - `sale_date`
  - `status` (`PENDING` | `APPROVED` | `PAID` | `REVERSED`)
  - `reversal_reason` / `reversed_by` / `reversed_at`
  - `created_at`

- **Extension to `salary_config`**: no new fields needed — SPIFF payouts merge via the existing `incentive` field at payroll-run time (same pattern as daily points payout, `payroll_engine.py:287-290`)

- **Extension to `incentive_settings`**: add `spiff_auto_detect: bool` (whether the backend silently matches sales against active SPIFFs) and `spiff_approval_required: bool` (whether each hit needs manager sign-off before counting toward payout)

## Backend
- `POST /api/v1/incentive/spiff-programs` — create a SPIFF program (SUPERADMIN/ADMIN); validates period dates are within current or next financial month, scope value exists in catalog/brand master, tiers are strictly ascending
- `GET /api/v1/incentive/spiff-programs` — list (SUPERADMIN/ADMIN see all; STORE_MANAGER sees their store's active programs)
- `PUT /api/v1/incentive/spiff-programs/{spiff_id}` — edit while DRAFT; locked once ACTIVE and any hit exists
- `POST /api/v1/incentive/spiff-programs/{spiff_id}/activate` — SUPERADMIN/ADMIN; transitions DRAFT → ACTIVE, stamps `approved_by`/`approved_at`, writes audit log
- `POST /api/v1/incentive/spiff-programs/{spiff_id}/pause` — SUPERADMIN/ADMIN; ACTIVE → PAUSED (no new hits recorded)
- `GET /api/v1/incentive/spiff-hits` — list hits (store-scoped for managers; staff see own hits only); filters: spiff_id, staff_id, date range, status
- `POST /api/v1/incentive/spiff-hits` (internal, called from `orders.py` post-commit) — match completed order line items against ACTIVE SPIFFs for that store; idempotent on `order_id + line_item_id + spiff_id`; if `spiff_approval_required=true` → status PENDING, else APPROVED
- `POST /api/v1/incentive/spiff-hits/{hit_id}/approve` — STORE_MANAGER/ADMIN/SUPERADMIN; PENDING → APPROVED; writes audit
- `POST /api/v1/incentive/spiff-hits/{hit_id}/reverse` — ADMIN/SUPERADMIN; any non-PAID hit; stamps reversal_reason; writes audit; if points already fed to leaderboard, triggers recalculation
- `GET /api/v1/incentive/spiff-summary/{staff_id}?period=YYYY-MM` — MTD SPIFF total per staff (used by payroll run to pull the incentive line item)
- Extend `POST /api/v1/payroll/run` in `payroll.py` — after computing base earnings, call `spiff_summary` per employee and merge into `breakdown.earnings.spiff_bonus`; stamp `payout_locked=true` on all APPROVED hits for that period; status → PAID

## Frontend
- **IncentiveSettingsPage.tsx** (extend) — add "SPIFF Programs" tab (SUPERADMIN/ADMIN only); table of programs with status chips (DRAFT/ACTIVE/PAUSED/CLOSED); "New SPIFF" drawer with scope selector (brand dropdown fed from catalog brand list, collection dropdown, category enum, or free-text product SKU list), trigger type radio (per unit / monthly target / tiered), tier rows with unit threshold + bonus inputs, date range picker (month-aligned), store multi-select, eligible roles checkboxes
- **DailyScorecardPage.tsx** (extend) — below the 9-category grid, add a collapsible "Active SPIFFs Today" section showing which programs are live for this store today and the staff member's hit count for each; neutral card style, single accent colour for hit count
- **MTDLeaderboardPage.tsx** (extend) — add a secondary "SPIFF MTD" column next to base-points column; shows cumulative approved SPIFF bonus in rupees (not points) so it is immediately meaningful to staff; sortable
- **New page: `/incentive/spiff-hits`** (STORE_MANAGER/ADMIN/SUPERADMIN) — approval queue for PENDING hits when `spiff_approval_required=true`; table with staff name, product sold, units, computed bonus, sale date; bulk approve/reject; restrained table UI, no colour except status chips

## Business rules
- A SPIFF program cannot overlap another ACTIVE program for the same store + scope combination (backend 409 guard)
- `period_end` must not exceed the current financial year end (Apr–Mar); cross-FY SPIFFs are blocked
- `payout_locked=true` hits are immutable — no reversal after payroll run locks the period (HTTP 423)
- SPIFF bonus is an above-the-line earning item — it does NOT enter the PF/ESI/PT base (same as incentive merge at `payroll_engine.py:287`)
- A returned order line item triggers automatic SPIFF hit reversal (hook in `returns.py` post-restock); reversed hits cannot be re-approved
- Discount-killed orders (order cancelled or refunded in full) also reverse hits
- Hits are computed from `orders.items.brand` / `orders.items.collection` / `orders.items.category` — these fields must be stamped at order-create time (they already are via the catalog product lookup in `orders.py`)
- Audit log entry required for every status transition: create, activate, pause, approve-hit, reverse-hit, payout-lock

## RBAC
- SUPERADMIN: full CRUD on programs + hits, global view across all stores
- ADMIN: full CRUD on programs + hits, all stores they manage
- STORE_MANAGER: read programs for their store, approve/reject PENDING hits for their store, view hits for their store's staff
- AREA_MANAGER: read-only across their stores; no hit approval (kept to store manager)
- ACCOUNTANT: read-only on SPIFF summary per staff for payroll reconciliation; no program edits
- SALES_CASHIER / SALES_STAFF: read own hits and active programs for their store only; no approval, no program edit
- All other roles: no access

## Integrations
- **Tally**: SPIFF bonus surfaces as a separate earning line in the existing Tally salary JV XML (`payroll_exports.py`); extend `_earnings()` to include `spiff_bonus` as a named component so Tally sees it as a distinct head rather than folded into "Incentive"
- **Jarvis/ORACLE**: extend `oracle.py` anomaly scan to flag a SPIFF program that has zero hits after 7 days of being ACTIVE (possible scope misconfiguration or staff unaware) — surfaces as an advisory proposal, not auto-execute
- **MEGAPHONE**: on SPIFF activation, send an in-app bell notification to all eligible staff in the target stores (reuse `notifications` collection + existing notify helper); optionally WhatsApp if `DISPATCH_MODE=live`

## Risk notes
- **POS revenue-critical**: the SPIFF hit recorder runs as a post-commit hook inside `orders.py` after the order is already saved — a bug here must never roll back the order; wrap in a try/except that logs to `agent_errors` and continues; this is a fire-and-forget side-effect, not a transaction participant
- **Return-reversal race**: if a return is processed concurrently with a payroll lock, use `find_one_and_update` with a filter on `status IN [PENDING, APPROVED]` to prevent reversing an already-PAID hit
- **Idempotency key** (`order_id + line_item_id + spiff_id`) must have a unique MongoDB index on `spiff_hits` to prevent duplicate hits on order retry or webhook replay
- **Overlapping programs**: the overlap guard is important — without it, a single premium watch sale could trigger multiple SPIFFs stacked; enforce at activation time and re-check on each hit match
- **Feature flag**: the entire SPIFF hit-matching hook in `orders.py` should be gated on an `SPIFF_ENABLED` env var (default false) so it can be deployed dark and switched on per store without a redeploy

## Recommendation
Build later — not a quick win. The payroll integration (wiring SPIFF bonus into the salary run) requires careful coordination with the payroll lock mechanism and Tally JV export. Build after the commission calculation gap (also in `payroll_engine.py`) is resolved, since they share the same earnings-merge pattern and should be designed together to avoid two separate "extra earnings" bolt-ons. Estimated sequencing: fix commission first (3 days), then build SPIFFs on top of the established pattern (6 days).

## Owner decisions
- Q: Should SPIFF hits require explicit store-manager approval before counting toward payout, or should qualifying sales auto-approve? | Why: If auto-approve, payroll is hands-off but a mis-configured scope could pay out on unintended products with no gate; if approval-required, manager reviews a queue daily but has a correction window | Options: a) auto-approve (simpler, suitable if scope is tight brand/SKU) / b) manager approval required for all hits / c) configurable per program (some programs auto-approve, others need sign-off)
- Q: Should SPIFF bonuses appear as a named line on the printed payslip, or be folded into the existing "Incentive" total? | Why: A named line is transparent to staff and auditable by a CA; folded is cleaner but obscures what drove the bonus | Options: a) named "SPIFF Bonus" line on payslip / b) folded into Incentive total
- Q: Which stores and which month should the first SPIFF program go live? | Why: This determines the pilot scope for QA and the earliest payroll run that will include SPIFF amounts — sets the go-live date and lets the team verify Tally JV balances before rolling out to all stores | Options: a) one store, next calendar month / b) all stores simultaneously / c) start with a zero-payout dry run (hits recorded, payout = ₹0) to validate hit logic before real money moves
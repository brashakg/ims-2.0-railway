I have all the grounding I need. Now I'll write the packet:

---

# Packet SC: Scorecard + slab-incentive engine

## Current behavior (file:line)

**What exists and works:**

- `backend/api/services/points_calculator.py` (143 lines) — pure functions: `CATEGORY_MAX` (9 cats, sum=100), `compute_total`, `apply_visufit_gate`, `compute_eligibility`, `aggregate_mtd`, `leaderboard_sort_key`. Correct tier math: bands `[0,70)=0`, `[70,80)=0.6`, `[80,95)=0.8`, `[95,1000)=1.0`.

- `backend/api/services/payout_calculator.py` (272 lines) — pure functions: `compute_targets`, `compute_multiplier` (floor-rounded discount, kill-switch), `compute_best_level` (L3 wins all), `compute_pools` (best-level-only), `compute_individual_payouts` (pool x weightage x eligibility), `compute_manager_bonuses` (stacks), `assemble_payout`. Verified against Excel _archive_may26: last_year=₹16,72,000, avg_disc=14%, multiplier=1.1 (floor rounds 0.14 to the `max_pct: 0.14 → multiplier: 1.1` tier), L1 pool @ 14% disc = 22,10,000 x 0.01 x 1.1 = ₹24,310; Rupesh 0.22 x 1.0 = ₹5,348.20.

- `backend/api/routers/points.py` (909 lines) — HTTP layer containing embedded business helpers: `_conversion_score_for` (footfall vs walkouts, 90-day retro), `_build_row` (auto-fill conversion, apply gate, compute total+eligibility), `_save_row` (DuplicateKeyError→409, audit), `_audit`. Endpoints: `POST /daily`, `POST /daily/bulk`, `GET /daily`, `DELETE /daily/{log_id}`, `GET /mtd`, `GET /leaderboard`, `GET /staff/{staff_id}/history`, `PATCH /settings/eligibility`, `PATCH /settings/payout`, `POST /inputs/last-year-sale`, `PATCH /settings/visufit-gate`.

- `backend/api/routers/payout.py` (727 lines) — HTTP layer with embedded helpers `_aggregate_sales`, `_last_year_sale`, `_build_mtd_data`, `_name_lookup_for`, `_compute_payout`. Endpoints: `GET /preview`, `POST /lock`, `GET /snapshots`, `GET /snapshot/{id}`, `PATCH /snapshot/{id}/mark-paid`, `GET /export/{id}.csv`.

- `backend/database/repositories/incentive_settings_repository.py` (132 lines) — store-scoped only, no E2 resolution. Default `discount_multipliers` seeded correctly: 0.10→1.5, 0.11→1.4, 0.12→1.3, 0.13→1.2, 0.14→1.1, 0.15→1.0.

- `backend/database/repositories/payout_snapshot_repository.py` (143 lines) — `create_snapshot`, `find_locked`, `mark_paid`, `list_for_store_year`. No `payroll_fed_at` or `product_incentive` fields.

- `backend/database/repositories/points_log_repository.py` — `create_points_log` (unique partial index), `list_for_mtd`, `soft_delete`. No `product_incentive_amount` field.

- Frontend: `frontend/src/pages/incentive/` has 6 pages (DailyScorecardPage, MTDLeaderboardPage, PayoutDashboardPage, PayoutSnapshotsPage, PointsHistoryPage, IncentiveSettingsPage). `kicker_1`/`kicker_2` rendered as generic score fields — no Product-Incentive rupee panel, no E2 entity-settings tab, no Visufit-source indicator, no payroll-feed status badge.

**What is absent (gaps = the delta):**

- No `scorecard_engine.py` service module — business logic is embedded in two routers, untestable in isolation.
- No Product-Incentive Kicker: no `product_incentive_log` collection, no kicker repo, no `POST /incentive/kicker/product-sale`, no `GET /incentive/kicker/{ym}`, no `kicker_for()`.
- No Payroll feed: `payroll.py:1332 _fetch_incentive` reads a generic `incentives` collection (returns 0.0 in prod — that collection is empty); the LOCKED snapshot has no `payroll_fed_at` guard and no `product_incentive` per-staff field; `GET /payout/payroll-feed` does not exist.
- No E2 settings resolution: `incentive_settings` has no `scope`/`entity_id` fields; `IncentiveSettingsRepository.get_for_store` is store-only with no global→entity→store fallback.
- No `visufit_source` provenance on `points_log`.
- `GET /incentive/points/settings/effective` endpoint does not exist.
- `incentive_inputs` lacks unique index (relied on by `find_one` but not enforced — silent double-entry risk).

## Intended behavior (full intent)

The PUNE INCENTIVE workbook's full chain runs inside IMS as the sole incentive computation path:

**Daily scoring:** A supervisor or staff member enters 9 component scores for the day. For today's entry, `conversion` auto-fills from (walk-ins − walkouts + 90-day retro conversions) / walk-ins × 20, rounded. If the Visufit gate is enabled and the staff's MTD usage is below 90%, their `visufit` score is forced to 0. Total /100 is computed, eligibility tier snapped (0/0.6/0.8/1.0). Duplicate same-day entries are blocked (409). The stored row snapshots the bands used so future band changes don't mutate historical scores.

**Product-Incentive Kicker:** When a staff member sells a qualifying premium product (e.g. ZEISS PAL), a rupee incentive is logged to `product_incentive_log` with `order_id + sku` as the idempotency key. This can be triggered automatically at POS order finalisation (feature-flagged, off by default) or entered manually by a manager. The kicker also optionally drives `kicker_1`/`kicker_2` point fields on the daily scorecard. The monthly total per staff surfaces in the payout snapshot and the payroll feed.

**Monthly payout:** At month-end, `GET /payout/preview` assembles the full PUNE model: last-year sales vs this-year → best level (L1/L2/L3) → slab pool (best-level-only, zeroed on discount-kill > 15%) → per-staff split by weightage × eligibility × pool, plus manager bonus stacking. Product-incentive rupees are added per staff. SUPERADMIN locks the result as an immutable snapshot. The snapshot is the single source of truth for payroll.

**Payroll feed:** When payroll-run executes for a month, it calls `get_incentive_for_payroll(store, year, month)` which reads the LOCKED/PAID snapshot and returns `{staff_id: total_incentive_rupees}` (slab + manager bonus + product incentive). The existing `_fetch_incentive` in `payroll.py:1332` is replaced/subordinated by this path. A `payroll_fed_at` guard on the snapshot prevents double-feed. If no locked snapshot exists, payroll uses 0 — never estimates.

**E2 settings:** Settings resolve global → entity → store. Entity or global overrides allow chain-wide defaults without per-store duplication. Luxury brand caps are not E2 keys (they are code-enforced). SUPERADMIN-only writes.

## Delta to build

1. **`scorecard_engine.py`** — extract `_conversion_score_for`, `_build_row` logic, and `_compute_payout` orchestration out of the two routers into a single importable module. Routers become thin HTTP shells calling the engine. Add `visufit_source` tracking. Add `resolve_settings` (E2 hierarchy). Zero behavior change for existing callers.

2. **Product-Incentive Kicker** — new `product_incentive_log` collection + `ProductIncentiveLogRepository` + `kicker_for()` + `POST /api/v1/incentive/kicker/product-sale` + `GET /api/v1/incentive/kicker/{ym}`. Unique index on `{order_id, sku}` WHERE `order_id != null` for idempotency. POS auto-attach hook behind `FEATURE_PRODUCT_INCENTIVE_AUTOLOG=false`.

3. **Payroll feed** — add `staff_payouts[].product_incentive: float` + `payroll_fed_at: datetime|null` + `payroll_run_id: str|null` to `payout_snapshots`; add `get_incentive_for_payroll()` to the engine; add `GET /api/v1/payout/payroll-feed`; replace `_fetch_incentive` in `payroll.py:1332` with a call to `get_incentive_for_payroll` via the snapshot repo (P0-4: never sum both paths).

4. **E2 settings resolution** — add `scope`/`entity_id` fields to `incentive_settings`; add `{scope,entity_id,store_id}` index; backfill migration stamping `scope:"store"` on existing docs; extend `IncentiveSettingsRepository` with `resolve_for_store(store_id, entity_id)` method; add `GET /api/v1/incentive/points/settings/effective`; wire `resolve_settings()` in the engine.

5. **`incentive_inputs` unique index** — add `{store_id,year,month}` unique index via `ensure_indexes` in `connection.py`.

6. **Frontend additions** — Kicker panel on PayoutDashboardPage and PayoutSnapshotsPage (per-staff product-incentive column); entity-level settings tab on IncentiveSettingsPage; Visufit-source indicator on DailyScorecardPage; payroll-feed badge on PayoutSnapshotsPage.

## Data model (collections/fields; new vs existing; migration)

**`points_log`** (EXISTING — additive only)

New fields (nullable, back-compat):
- `product_incentive_amount: float | null` — kicker rupees for the day (null on legacy rows)
- `visufit_source: "clinical" | "manual" | null` — provenance of the gate input

No index change. Existing unique partial index `{store_id, date_str, staff_id} WHERE deleted_at=null` unchanged.

**`incentive_settings`** (EXISTING — additive + scope backfill)

New fields:
- `scope: "global" | "entity" | "store"` — resolution tier (default "store" for all existing docs)
- `entity_id: str | null` — set for entity-scoped rows; null for global and store rows

New index: `{scope: 1, entity_id: 1, store_id: 1}` (non-unique, background).

Migration (idempotent, one-time): `db.incentive_settings.updateMany({scope: {$exists: false}}, {$set: {scope: "store", entity_id: null}})` — preserves all existing store-scoped behavior.

**`incentive_inputs`** (EXISTING — index only)

New unique index: `{store_id: 1, year: 1, month: 1}` unique. Safe to add because `payout.py` already does `find_one` before `insert_one`; the index just enforces at DB level what was previously best-effort.

**`payout_snapshots`** (EXISTING — additive only)

New fields on each doc:
- `staff_payouts[].product_incentive: float` (default 0.0 for pre-migration snapshots)
- `payroll_fed_at: datetime | null`
- `payroll_run_id: str | null`

No index change.

**`product_incentive_log`** (NEW collection)

```
entry_id: str (uuid hex, _id alias)
store_id: str
date: datetime (IST midnight)
date_str: str (YYYY-MM-DD, for efficient range queries)
ym: str (YYYY-MM, for monthly rollup)
staff_id: str
staff_name: str | null
sku: str
product_id: str | null
brand: str (e.g. "ZEISS")
category: str (e.g. "PAL")
description: str | null
order_id: str | null (null = manual entry)
incentive_amount: float (rupees, paisa-exact)
created_by: str
created_at: datetime
deleted_at: datetime | null
deleted_by: str | null
deleted_reason: str | null
```

Indexes (created in `connection.py::ensure_indexes`):
- `{store_id: 1, date_str: 1, staff_id: 1}` (background, non-unique)
- `{store_id: 1, ym: 1, staff_id: 1}` (background, non-unique — for monthly rollup)
- `{order_id: 1, sku: 1} WHERE order_id != null` (unique partial — idempotency guard)

## Backend (endpoints + services + which ENGINE calls)

**New module: `backend/api/services/scorecard_engine.py`**

Functions extracted from routers (behavior-preserving, zero new imports from `get_db` directly):

```python
def score_daily(payload, *, settings, conversion_provider, kicker_provider,
                visufit_provider) -> dict
    # Wraps _build_row logic; calls providers as seams; tracks visufit_source.

def conversion_score(store_id, date_str, staff_id, *, walkout_repo, walkin_repo)
    # = extracted _conversion_score_for from points.py:194-254

def kicker_for(store_id, ym, staff_id, *, kicker_repo) -> dict
    # -> {product_incentive_amount: float, sale_count: int}

def compute_payout(store_id, year, month, *, settings, sales_provider,
                   last_year_provider, mtd_data, name_lookup, overrides=None,
                   kicker_provider=None) -> dict
    # Wraps assemble_payout; adds product_incentive_total per staff.

def get_incentive_for_payroll(store_id, year, month, *, snapshot_repo)
    -> dict[str, float]
    # Reads LOCKED/PAID snapshot; returns {staff_id: rupees}; {} if none.

def resolve_settings(store_id, entity_id=None, *, settings_repo) -> dict
    # global -> entity -> store override merge.

def aggregate_mtd_scores(rows) -> dict  # = points_calculator.aggregate_mtd
def leaderboard(rows) -> list           # sorted via leaderboard_sort_key
```

**New repository: `backend/database/repositories/product_incentive_log_repository.py`**

```python
class ProductIncentiveLogRepository(BaseRepository):
    def log_entry(self, doc) -> dict
        # insert; raises DuplicateKeyError on order_id+sku collision -> idempotent
    def list_for_ym(self, store_id, ym, staff_id=None) -> list[dict]
    def staff_total_for_ym(self, store_id, ym) -> dict[str, float]
        # {staff_id: sum(incentive_amount)} via aggregation
    def soft_delete(self, entry_id, deleted_by, reason) -> bool
```

**Existing routers — changes:**

`points.py`: replace `_conversion_score_for` and `_build_row` body with calls to `scorecard_engine.score_daily(...)` and `scorecard_engine.conversion_score(...)`. Remove the 60-line inline implementations. Existing endpoints and HTTP signatures unchanged.

`payout.py`: replace `_compute_payout` body with call to `scorecard_engine.compute_payout(...)`. Add `kicker_repo` injection. Existing endpoints unchanged.

`payroll.py:1332 _fetch_incentive`: replace with `scorecard_engine.get_incentive_for_payroll(store_id, year, month, snapshot_repo=...)`. Guard: if `payroll_fed_at` is already set on the snapshot for this `payroll_run_id`, skip re-fetch. Stamp `payroll_fed_at = datetime.now(IST)` + `payroll_run_id` on the snapshot via a single `find_one_and_update({snapshot_id, payroll_fed_at: null}, {$set: {payroll_fed_at, payroll_run_id}})` (atomic, no transactions needed — single doc single collection). (P0-4 compliant.)

**New endpoints:**

```
POST   /api/v1/incentive/kicker/product-sale
    Body: {store_id?, staff_id, date, sku, brand, category, description?,
           order_id?, incentive_amount, product_id?}
    Roles: sales staff (own), managers (any in store), SYSTEM (POS auto)
    Returns: created entry_id
    409 on order_id+sku duplicate (idempotent)

GET    /api/v1/incentive/kicker/{ym}?store_id=&staff_id=
    Roles: VIEW_ROLES (managers/admin/accountant); own-only for sales staff
    Returns: [{staff_id, staff_name, total_rupees, sale_count, entries: [...]}]

GET    /api/v1/payout/payroll-feed?year=&month=&store_id=
    Roles: ACCOUNTANT, SUPERADMIN, ADMIN
    Returns: {store_id, year, month, snapshot_id, status,
              feed: {staff_id: float}, payroll_fed_at}
    404 if no locked snapshot

GET    /api/v1/incentive/points/settings/effective?store_id=
    Roles: VIEW_ROLES
    Returns: E2-resolved settings doc (global->entity->store merged)
```

**`connection.py` additions** (in `ensure_indexes`):

```python
# product_incentive_log
_idx("product_incentive_log", [("store_id",1),("date_str",1),("staff_id",1)])
_idx("product_incentive_log", [("store_id",1),("ym",1),("staff_id",1)])
_idx("product_incentive_log",
     [("order_id",1),("sku",1)],
     unique=True,
     partialFilterExpression={"order_id": {"$type": "string"}})
# incentive_inputs unique
_idx("incentive_inputs", [("store_id",1),("year",1),("month",1)], unique=True)
# incentive_settings E2 scope lookup
_idx("incentive_settings", [("scope",1),("entity_id",1),("store_id",1)])
```

**Feature flag:** `FEATURE_PRODUCT_INCENTIVE_AUTOLOG` env var, defaults `false`. When `true`, POS order-finalisation (`orders.py` complete/deliver transition) calls `POST /incentive/kicker/product-sale` for qualifying SKUs. The flag is checked inside a POS-safe guard — if the env var is absent or false, the POS path is completely unchanged.

## Frontend (pages/components + what they show; restrained light UI)

All UI is neutral/monochrome with single accent (`text-bv-red-*` or gray-700), colour only for semantic meaning (green = paid/eligible, amber = pending/partial, red = zero/killed). No multi-colour decorative elements.

**`IncentiveSettingsPage.tsx`** — add "Entity & Global Defaults" tab (SUPERADMIN only):
- Tab shows the resolved effective settings for the current store with a two-column diff: "Store-specific override" vs "Inherited from entity/global"
- Form to PATCH entity-level eligibility bands and weightage defaults
- Existing store-specific tab unchanged

**`DailyScorecardPage.tsx`** — add Visufit-source indicator:
- Small gray chip next to the visufit score field: "Clinical" / "Manual" / "Not set"
- When gate is applied, show a muted amber warning: "Visufit gate active — score zeroed (usage below threshold)"
- No layout changes otherwise

**`PayoutDashboardPage.tsx`** — add Product-Incentive column:
- Per-staff payout table gains a "Product Incentive" column (rupees from kicker)
- "Grand Total" row sums it
- A collapsible "Product Incentive Detail" section lists individual kicker entries for the month
- Visufit-source indicator: a small note on the inputs card saying "Visufit source: Manual override" vs "Clinical (auto)"

**`PayoutSnapshotsPage.tsx`** — add Payroll Feed status:
- Each snapshot row shows: Status chip (LOCKED / PAID) + "Payroll Fed" chip (gray = not yet, green = fed, with timestamp)
- A "Mark Fed" action is removed — feed is auto-stamped when payroll-run consumes it
- CSV export gains a "Product Incentive" column per staff

**New page: `frontend/src/pages/incentive/KickerLogPage.tsx`** (linked from IncentiveSettingsPage or a sub-nav):
- Monthly view of product-incentive log entries per store
- Columns: Date, Staff, Brand, Category, SKU, Order ID, Amount
- Total per staff at the bottom
- SUPERADMIN/Manager can add manual entry via a modal
- Sales staff sees own entries only

**No new nav item required** — accessible from the existing Incentive section sub-navigation.

## RBAC + flags (roles; feature-flag if POS/money)

| Action | Roles |
|---|---|
| Log own daily scorecard | SALES_STAFF, SALES_CASHIER, CASHIER (self only) |
| Log any staff daily scorecard / bulk EOD | SUPERADMIN, ADMIN, STORE_MANAGER, AREA_MANAGER, ACCOUNTANT |
| Soft-delete a points row | SUPERADMIN (any store), STORE_MANAGER (own store) |
| Log product-incentive sale (kicker) — manual | Sales roles (own), managers (any in store) |
| Log product-incentive sale — POS auto-attach | SYSTEM (internal, gated by `FEATURE_PRODUCT_INCENTIVE_AUTOLOG`) |
| View kicker log MTD | VIEW_ROLES (managers/admin/accountant); own-only for sales staff |
| View MTD / leaderboard / payout preview / payroll-feed | VIEW_ROLES (SUPERADMIN, ADMIN, STORE_MANAGER, AREA_MANAGER, ACCOUNTANT) |
| Edit eligibility bands / payout settings | SUPERADMIN only |
| Edit entity/global settings (E2) | SUPERADMIN only |
| Lock snapshot / mark-paid | SUPERADMIN only |
| Read payroll-feed endpoint | ACCOUNTANT, SUPERADMIN, ADMIN |
| Individual staff reads own scores/payout | Each staff their own rows; never peers' rupee figures |
| Cross-store reads | 404-hide for non-GLOBAL roles (existing `can_access_store_scoped` pattern) |

Feature flag: `FEATURE_PRODUCT_INCENTIVE_AUTOLOG=false` (default off). Governs only the POS order-finalisation hook. All other kicker functionality (manual entry, kicker endpoints, payout integration) is always on.

## Engine + CORRECTIONS folded

**P0-3 (CORRECTIONS.md)** — SPIFF is built as an SC Kicker, not a parallel commission feed. No `commission_ledger` or `spiff_log`. The Kicker endpoint (`POST /incentive/kicker/product-sale`) handles SPIFFs by passing `category="SPIFF"` — same collection, same payout rollup. This packet builds zero parallel payroll feeds.

**P0-4 (CORRECTIONS.md)** — `payroll.py:1332 _fetch_incentive` is replaced by `scorecard_engine.get_incentive_for_payroll()` which reads exclusively from the LOCKED/PAID `payout_snapshots` doc. The old `incentives` collection read path is removed (or made a fallback-that-logs-a-warning with `payroll_fed_at` guard before it can be reached). Acceptance test #8 below asserts the two never double-count.

**P0-5 (CORRECTIONS.md)** — The seeded `discount_multipliers` table in `incentive_settings_repository.py:63-68` already has `0.14 → 1.1` (correct). The SC example test at `avg_disc=14%` must use either (a) input `11%` → multiplier `1.4`, or (b) input `14%` → expected multiplier `1.1`. The packet standardises on the Excel _archive_may26 worked example: `avg_disc=0.14 → floored=0.14 → tier max_pct=0.14 → multiplier=1.1`. Any existing test asserting `1.4` at `14%` is wrong and must be corrected (do not fix the engine to match the test; fix the test).

**P0-1 (CORRECTIONS.md)** — No cross-collection atomic dual-write anywhere in this engine. The payroll-feed stamp uses `find_one_and_update` on a single `payout_snapshots` document. The kicker idempotency uses a unique index (DB-level, single collection). No multi-document transactions attempted.

**DECISIONS.md §4 — PRODUCT INCENTIVE = a Kicker** — Product incentive is a rupee line rolled into the snapshot (not a separate payout run). SPIFF = Kicker with `category="SPIFF"`, auto-approved (no manager-approval gate). Clawback = a negative kicker entry (`incentive_amount < 0`), recorded as an SC negative adjustment within the locked snapshot month.

**DECISIONS.md §3 — Commission superseded** — No standalone commission engine. SC is the sole payroll incentive feed.

**E2 luxury-cap invariant** — E2 resolution for `incentive_settings` does not affect pricing caps. Luxury brand caps (Cartier 2%, etc.) are code-enforced in `orders.py` and are not E2 keys. This packet adds no pricing-cap keys to `incentive_settings`.

## Acceptance tests (INTENT-LEVEL; a hollow shell must FAIL)

All tests assert business behavior. A router that returns HTTP 200 with a stub envelope fails every test below.

**SC-T1 — Tier math (model-of-record):**
Insert a `points_log` row with `total=76`. Assert `eligibility=0.6`. Insert `total=84` → `eligibility=0.8`. Insert `total=96` → `eligibility=1.0`. Insert `total=69` → `eligibility=0.0`. Insert `total=80` → `eligibility=0.8` (boundary: 80 is in `[80,95)` band). Verify against real rows: AKSHAY `2026-01-27 total=76 eligibility=0.6`, MAHENDRA `2026-01-27 total=84 eligibility=0.8` (Excel Points Log). A hollow shell that hardcodes 0.6 fails on total=80 and total=96.

**SC-T2 — Conversion auto-calc:**
Mock `walkin_repo.get_today` returning `{per_staff: {"S1": 10}}`. Mock `walkout_repo.list_walkouts` returning 3 walkouts for staff S1 on date_str. `conversion_score("store", date_str, "S1")` must return `round((10-3)/10*20) = 14`. With 0 walk-ins → returns `0` (no division). Past date with no footfall entry → returns `0` (not None; past-date path). A shell returning a constant 10 fails.

**SC-T3 — Slab pool (P0-5 multiplier):**
`last_year=1672000`, `this_year=2267000` (hits L1 only: target=2010000), `avg_disc=0.14`. `compute_multiplier(0.14, multipliers, 0.15)` must return `1.1` (floor(0.14*100)/100=0.14; tier `max_pct:0.14` → `1.1`). Pool = max(2267000, 2010000) × 0.01 × 1.1 = 2267000 × 0.011 = ₹24,937. With `avg_disc=0.11` → floor=0.11 → tier `max_pct:0.11` → multiplier `1.4`. With `avg_disc=0.16` → kill → multiplier=0 → pool=0. A shell that returns 1.4 at 14% fails (P0-5).

**SC-T4 — Per-staff payout:**
Using pool=₹24,310 (L1 @ 14% disc, last_year=1672000 per archive), Rupesh weightage=0.22, eligibility=1.0: payout = 24310 × 0.22 × 1.0 = ₹5,348.20. Sameer (manager) weightage=0.27, eligibility=1.0: individual = 24310 × 0.27 = ₹6,563.70. A shell returning zeros fails.

**SC-T5 — Manager bonus stacks:**
Sameer is in `supervisor_bonuses` with `bonus_pct: {L1: 0.25}`. Pool=₹24,310. His eligibility=1.0. Individual payout = ₹6,563.70. Manager bonus = 24310 × 0.25 × 1.0 = ₹6,077.50. Total for Sameer = ₹12,641.20. Assert `total_payout + total_bonus` equals that sum (two separate fields — stacked, not one-or-the-other). A shell that returns only individual payout fails.

**SC-T6 — Visufit gate:**
Gate enabled, threshold=0.90, staff MTD usage=0.85. `apply_visufit_gate(scores, visufit_usage_pct_mtd=0.85, threshold=0.90, enabled=True)` returns scores with `visufit=0` and `gate_applied=True`. With `visufit_usage_pct_mtd=None` → gate NOT applied (`gate_applied=False`). With usage=0.95 → gate not applied. The stored row has `visufit_gate_applied=True` and `visufit_source` set. A shell that never zeroes the visufit score fails.

**SC-T7 — Product-Incentive Kicker idempotency:**
`POST /incentive/kicker/product-sale` with `{order_id: "O1", sku: "ZEISS-PAL-1.5", staff_id: "S1", incentive_amount: 500}` → 201, entry created. Same request again → 409 (DuplicateKeyError on unique index). `kicker_for("store", "2026-06", "S1")` returns `product_incentive_amount=500.0, sale_count=1`. A second POST with different `order_id` for the same staff adds correctly (not blocked). A shell that accepts all POSTs and returns 0.0 from kicker_for fails.

**SC-T8 — Payroll feed: no double-count (P0-4):**
Lock a snapshot with staff S1 slab payout=₹5,000, product_incentive=₹500. `get_incentive_for_payroll("store", 2026, 6, snapshot_repo=...)` returns `{"S1": 5500.0}`. Run payroll-run #1: it reads 5500, stamps `payroll_fed_at` + `payroll_run_id="RUN1"` on the snapshot via `find_one_and_update`. Run payroll-run #2 for the same month: the snapshot's `payroll_fed_at` is already set. The second run must NOT re-add the incentive (returns the same ₹5,500 with a warning log, does not double-count by summing 5500+5500). The old `_fetch_incentive` path reading the `incentives` collection must NOT also fire (P0-4). Assert the payroll breakdown's `earnings.incentive` for S1 = ₹5,500 not ₹11,000. A hollow engine that allows both paths to fire fails.

**SC-T9 — E2 settings resolution:**
Seed three docs: `{scope:"global", entity_id:null, store_id:null, discount_kill_threshold:0.15}`, `{scope:"entity", entity_id:"E1", store_id:null, discount_kill_threshold:0.12}`, `{scope:"store", store_id:"S1", entity_id:"E1", discount_kill_threshold:0.10}`. `resolve_settings("S1", entity_id="E1")` must return `discount_kill_threshold=0.10` (store wins). Delete the store doc: resolve returns `0.12` (entity). Delete the entity doc: resolve returns `0.15` (global). A shell that always returns store-only defaults fails.

**SC-T10 — Snapshot immutability + RBAC:**
A non-SUPERADMIN calling `POST /payout/lock` gets 403. A STORE_MANAGER reading another store's snapshot via `GET /payout/snapshot/{id}` gets 404 (existence-hide). After locking, calling `POST /payout/lock` again for the same `(store, year, month)` gets 409 (unique partial index). The locked snapshot's `staff_payouts[0].total_payout` is the same on GET one hour later (immutable). A shell that returns 200 on all lock attempts fails.

**SC-T11 — Export formula-injection safety:**
Lock a snapshot where `staff_payouts[0].name = "=cmd|'/C calc'!A0"`. `GET /payout/export/{id}.csv` must return that cell prefixed with `'` (single-quote, per `safe_writer`). The raw CSV cell content must not begin with `=`. A shell that passes the name through verbatim fails.

**SC-T12 — No locked snapshot → payroll uses zero:**
`get_incentive_for_payroll("store", 2026, 7)` when no LOCKED or PAID snapshot exists for July → returns `{}`. Payroll-run for a staff member in July passes `incentive=0.0` to `compute_payroll`. Net pay equals earned_gross - deductions with zero incentive (not a crash, not an estimate). A shell that returns a non-zero estimate fails.

## Effort (dev-days) + risk

| Slice | Dev-days | Risk |
|---|---|---|
| Extract `scorecard_engine.py` (behavior-preserving refactor), repoint routers, port existing tests | 1.5 | Low — mechanical extraction; pure calculators already isolated |
| Product-Incentive Kicker: collection + index + repo + 2 endpoints + `kicker_for` | 1.5 | Medium — new collection; idempotency via unique index; no POS touch yet |
| Payroll feed: `get_incentive_for_payroll`, snapshot fields, `payroll_fed_at` guard, replace `_fetch_incentive`, new endpoint | 1.0 | Medium — money path; double-count prevention; paisa-exact |
| E2 settings: `scope` field + index + backfill + `resolve_settings` + effective-settings endpoint | 1.0 | Low-Medium — additive schema; resolution-order bugs are subtle |
| `incentive_inputs` unique index + `visufit_source` wiring | 0.5 | Low — additive |
| Frontend: Kicker panel, entity-settings tab, Visufit-source indicator, payroll-feed badge, KickerLogPage | 1.5 | Low |
| Tests (12 intent-level acceptance + regression for P0-4/P0-5) | 1.0 | Low |
| **Total** | **~8.0 dev-days** | Highest risk = payroll money path + POS auto-attach; both isolated behind the double-count guard and the `FEATURE_PRODUCT_INCENTIVE_AUTOLOG` flag |

## Definition of done

- `scorecard_engine.py` exists and all router methods call it; `points.py` and `payout.py` contain zero inline business logic (only HTTP glue).
- `product_incentive_log` collection created with all three indexes in `ensure_indexes`; duplicate `order_id+sku` returns 409 from the endpoint.
- `GET /payout/payroll-feed` returns `{staff_id: float}` from the LOCKED snapshot; returns 404 when no locked snapshot exists.
- `payroll.py:1332 _fetch_incentive` no longer reads the `incentives` collection in the primary code path; `payroll_fed_at` is stamped on the snapshot after the first payroll-run consumes it.
- `incentive_settings` docs all have `scope` field after backfill migration; `resolve_settings("store", entity_id)` walks global→entity→store correctly.
- `incentive_inputs` unique index exists; a second `POST /inputs/last-year-sale` for the same `(store, year, month)` updates (upsert), not inserts.
- All 12 acceptance tests (SC-T1 through SC-T12) pass; SC-T3 specifically asserts multiplier=1.1 at avg_disc=14% (P0-5).
- No test asserts the old `_fetch_incentive` path; the only payroll incentive source is `get_incentive_for_payroll`.
- `npx tsc -b && npx vite build` exits 0 on frontend.
- Backend smoke test (`import app; print(len(app.routes))`) exits 0 with at least the 4 new endpoints registered.
- No emojis in any new Python file. Light-only UI (no `bg-gray-800`, `text-white` on dark surfaces, no dark-mode tokens).
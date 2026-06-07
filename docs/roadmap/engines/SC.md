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
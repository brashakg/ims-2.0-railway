I now have comprehensive grounding. Let me produce the build packet.

---

# Packet F3footfall: N3 Footfall + conversion %

## Current behavior (file:line)

**Backend — storage already exists, entry point is partial:**
- `WalkInCounterRepository` (`backend/database/repositories/walkin_counter_repository.py`): one doc per `(store_id, date_str)` with `pos_auto_count`, `manual_topup`, `total`, `per_staff: {staff_id: int}`, `manual_log[]`. Doc id = `"{store_id}_{date_str}"`.
- `GET /api/v1/walkouts/walkins/today` and `GET /api/v1/walkouts/walkins/mtd` (`walkouts.py:953,973`): read the repo and return totals + per-staff breakdown. These endpoints exist and return data.
- `POST /api/v1/walkouts/walkins/manual-topup` (`walkouts.py:1000`): manager-gated; adds `delta` to `manual_topup` + appends to `manual_log`. Audited as `walkin.manual_topup`.
- `POST /api/v1/walkouts/walkins/pos-increment` (`walkouts.py:1057`): sales-staff-allowed; bumps by 1, attributes to `sales_person_id`, deduplicates per `(mobile, day)`.
- `GET /api/v1/walkouts/conversion-feed` (`walkouts.py:1331`): per-staff `conversion_score = min(20, max(0, (walk_ins - walkouts + retro) / walk_ins * 20))`. If `walk_ins == 0`, score = **0** (no null, no block). Returns raw list, not a named envelope.
- `_conversion_score_for()` (`points.py:194`): in-process equivalent used by the daily scorecard save path. Also returns **0** when `walk_ins <= 0` (`points.py:251-252`).
- `_build_row()` (`points.py:334-344`): if `conversion` is `None` and date is today, auto-fills from `_conversion_score_for(...) or 0`. For past dates with `None` → silently scores **0** with a comment "accept as 0".
- The SC scorecard page (`DailyScorecardPage.tsx`) shows an "AUTO" badge on the conversion column for today's date, but never warns when footfall is missing.

**Frontend — dedicated footfall page is a hollow shell:**
- Route `pos/footfall` (`App.tsx:228`): renders `<div>Footfall Tracking — Coming soon</div>`. No component exists.
- `WalkoutsDashboardPage.tsx`: shows walk-ins today (headline KPI) and per-staff `walk_ins_today` / `walk_ins_mtd` / `conversion_pct_mtd`. This is the only live footfall-adjacent UI.
- `WalkinCaptureModal.tsx` (`marketing/`): calls `marketingApi.createWalkin()` — this is the CRM "log a prospect" path (generates a follow-up), NOT the footfall counter.
- `FootfallAuditCard.tsx` (`reports/sections/`): cross-reference audit card in the Reports page. Calls `reportsApi.getFootfallAudit()` → `GET /api/v1/reports/walkouts/footfall-audit` (`reports.py:3188`). This is read-only analytics, not entry.

**Missing / wrong:**
1. No dedicated footfall entry UI. The route `pos/footfall` is empty.
2. No per-staff manual walk-in entry UI (the existing `manual-topup` is a manager aggregate delta, no per-staff attribution UI).
3. `conversion_score` = 0 when footfall is missing — violates "Fail Loudly" and the HARDENING.md correction (line 92): should be `null` (unscored) with a warning, not 0.
4. No today/historical view of footfall + conversion % side-by-side per staff in a single dedicated page.
5. No footfall status enum: today the counter is a bare integer; the DECISIONS mandate every legacy colour-flag becomes an explicit status enum. The daily entry state (PENDING / PARTIAL / COMPLETE) is unrepresented.

---

## Intended behavior (full intent)

N3 Footfall + Conversion % is the per-store, per-staff, per-day count of physical walk-ins, with conversion % auto-computed as `(walk_ins - walkouts) / walk_ins` and surfaced both as a live scorecard feed and as a standalone management view.

**The full intent:**
1. **Manual walk-in entry per staff per day** is the primary operation. A store manager or senior staff opens the Footfall page for today (or a past date), sees a row per active staff member, and enters the number of walk-ins that staff personally handled. Entry is a number, not a yes/no; it can be updated intra-day. Attribution to staff is mandatory for the conversion score to be valid.
2. **POS auto-increment** (already implemented) silently bumps the counter when an order is placed; it is a floor, not the full count (browsers who don't buy are not POS customers). Manual entry for non-POS browsers goes through the manager `manual-topup` path, now extended to allow per-staff attribution via UI.
3. **Conversion % auto-computes** from the existing `walk_in_counters.per_staff` (numerator) and `walkouts` logged by that staff member (denominator side). Formula: `(walk_ins - walkouts_logged + retro_conversions) / walk_ins * 100%`. This is already implemented in `_conversion_score_for` and `conversion-feed` — F3footfall does NOT re-implement it.
4. **Missing footfall = null (not 0).** When no walk-in count exists for a staff on a given day, the conversion score is `null` (unscored), and the Daily Scorecard entry is **blocked** for that staff+date unless the submitter explicitly provides a manual conversion override. This prevents payout-corrupting silent zeros. The block is surfaced as a warning banner ("No walk-in count for [date] — enter footfall first or provide a manual conversion value.").
5. **Footfall entry status enum** per `(store, date)`:
   - `PENDING` — no walk-in data yet for the day (store has not opened the footfall page)
   - `PARTIAL` — at least one staff has data, but at least one active staff member is missing
   - `COMPLETE` — all active staff (or an explicit "no staff" entry) have a walk-in count for the day
   This status drives a compliance badge on the manager dashboard and the SC scorecard header.
6. The **Footfall page** replaces the hollow `pos/footfall` shell. It shows: date picker, per-staff table with walk-ins-today (editable), auto-computed conversion % (read-only), walkouts count (read-only from walkouts collection), and a summary row (store total). Managers can also trigger the aggregate `manual-topup` (for unattributed browse-and-leave). The page is NOT POS-critical — no feature flag required.
7. Optometrists see test count and conversion rate only; revenue is hidden (DECISIONS §3 Optometrist dashboard).

---

## Delta to build

| # | Delta | Size |
|---|---|---|
| D1 | `null`-return from `_conversion_score_for` when `walk_ins == 0` (replaces hard `return 0`); update `conversion-feed` endpoint to emit `null` for 0-footfall staff | Small — 2 lines + tests |
| D2 | `_build_row()` in `points.py`: when auto-fill returns `None` (missing footfall), leave `conversion=None` in the stored row + set `conversion_missing_footfall=True` flag. Block the save with HTTP 422 and a descriptive message UNLESS the caller has explicitly provided a numeric `conversion` value. (Applies to today only; past dates already accept 0.) | Small — ~10 lines |
| D3 | Add footfall `entry_status` field to the daily read endpoints: `GET /api/v1/walkouts/walkins/today` and the conversion-feed now also annotate each staff row with `footfall_missing: bool`. Add a store-level `GET /api/v1/walkouts/walkins/status?date=` endpoint returning `{status: "PENDING"|"PARTIAL"|"COMPLETE", staff_with_data: [...], staff_missing: [...]}` | Small — new endpoint + helper |
| D4 | Per-staff walk-in entry endpoint: `PATCH /api/v1/walkouts/walkins/per-staff` — allows manager to set/update `per_staff[staff_id]` for a date (today or past, no future). Uses atomic `find_one_and_update` on the walk_in_counters doc. Audited as `walkin.per_staff_update`. | Small-Med |
| D5 | `FootfallPage` component (`frontend/src/pages/pos/FootfallPage.tsx`) replacing the hollow shell. Per-staff table, editable walk-in count, auto-computed conversion %, date picker, status badge, manager aggregate topup inline. | Med — new page |
| D6 | SC Scorecard warning banner: when fetching daily rows, if `footfall_missing=true` for any staff on today's date, render a warning banner above the table ("Walk-in count missing for X staff — conversion score will be blocked until entered"). | Small — FE only |
| D7 | Tests: 6 intent-level pytest tests | Med |

---

## Data model (collections/fields; new vs existing; migration)

**`walk_in_counters`** (EXISTING collection):

| Field | Type | Status | Notes |
|---|---|---|---|
| `_id` | `str` = `"{store_id}_{date_str}"` | Existing | Composite key |
| `store_id` | `str` | Existing | |
| `date_str` | `str` ISO | Existing | |
| `pos_auto_count` | `int` | Existing | Bumped by POS hook |
| `manual_topup` | `int` | Existing | Manager aggregate delta |
| `manual_log` | `list[dict]` | Existing | Audit trail for manual topup |
| `total` | `int` | Existing | `pos_auto_count + manual_topup` |
| `per_staff` | `dict[str,int]` | Existing | Staff ID → walk-in count |
| `mobiles_seen` | `list[str]` | Existing | POS dedup; never returned in API |
| `updated_at` | `datetime` | Existing | |
| `entry_status` | `str` enum `PENDING\|PARTIAL\|COMPLETE` | **NEW** | Computed on write; avoids re-query |
| `per_staff_log` | `list[dict]` | **NEW** | Append-only audit of per-staff edits: `{staff_id, old_val, new_val, updated_by, updated_at}` |

No new collection. No migration — new fields are nullable/defaulted; existing docs read fine.

**Indexes (existing)** — none defined on this collection beyond `_id`. No new index needed for F3footfall (the doc ID is already the store+date composite key, queries are point lookups).

**`points_log`** (EXISTING — touch only for D2):

Add optional field `conversion_missing_footfall: bool | None` — when `True`, the saved row has `conversion=None` or was explicitly overridden. Existing rows read fine (field absent = `None`).

---

## Backend (endpoints + services + which ENGINE calls)

All existing endpoints are at `GET /api/v1/walkouts/walkins/*` (mounted in `walkouts.py`). F3footfall touches `walkouts.py` and `points.py` only. No new router file required.

**Modified endpoints:**

| Endpoint | Change |
|---|---|
| `GET /api/v1/walkouts/conversion-feed` (`walkouts.py:1331`) | When `walk_ins == 0`: emit `conversion_score: null` and `footfall_missing: true` instead of `score=0.0`. Callers that need a numeric default must handle `null`. |
| `GET /api/v1/walkouts/walkins/today` (`walkouts.py:953`) | Add `entry_status` field to the response (computed from `per_staff` vs. active staff list). |
| `_conversion_score_for()` (`points.py:194`) | Return `None` when `walk_ins <= 0` (currently `return 0` at line 251). |
| `_build_row()` (`points.py:318`) | When auto-fill returns `None`: if caller did not supply an explicit numeric `conversion`, raise `HTTPException(422, detail="Footfall missing for [date]. Enter walk-in count or supply an explicit conversion score.")`. If caller did supply a numeric conversion, accept it (manager override). |

**New endpoint:**

```
GET  /api/v1/walkouts/walkins/status
     ?date=YYYY-MM-DD (default today)
     &store_id=...
     Response: {
       "store_id": str,
       "date_str": str,
       "status": "PENDING" | "PARTIAL" | "COMPLETE",
       "staff_with_data": [{"staff_id": str, "walk_ins": int}],
       "staff_missing": [str],   # staff IDs with no walk-in entry
       "total_walk_ins": int,
       "store_conversion_pct": float | null   # null if any staff missing
     }
     RBAC: same as walkins/today

PATCH /api/v1/walkouts/walkins/per-staff
      Body: { staff_id: str, walk_ins: int (>=0), date_str?: str, reason?: str }
      ?store_id=...
      Response: updated walk_in_counters doc (minus mobiles_seen)
      RBAC: STORE_MANAGER, AREA_MANAGER, SUPERADMIN, ADMIN (not SALES_STAFF — prevents self-inflation)
      Audit: action="walkin.per_staff_update", appends to per_staff_log[]
      Atomic: find_one_and_update on the doc (single-doc write, no transaction needed)
      Validation: date must not be in the future (IST); walk_ins >= 0
```

**Engine calls:**
- F3footfall calls **SC engine's `conversion_score()` seam** (`_conversion_score_for` in `points.py`) — it does NOT reimplement the formula. The formula lives in exactly one place.
- F3footfall does NOT call E1/E2/E4/E5/E6.
- The status endpoint calls `get_user_repository()` to enumerate active staff (same pattern as `points.py:_resolve_staff_name`).

---

## Frontend (pages/components + what they show; restrained light UI)

**New page: `frontend/src/pages/pos/FootfallPage.tsx`**

Replaces the hollow `pos/footfall` shell. Layout:

- **Header row**: store name + date picker (today default; past dates allowed for correction; future dates blocked). Status badge: `PENDING` (gray), `PARTIAL` (amber text, no background fill), `COMPLETE` (green text). Refresh button.
- **Per-staff table** (one row per active staff, sorted by name):
  | Column | Source | Notes |
  |---|---|---|
  | Staff name | `users` list | |
  | Walk-ins today | `walk_in_counters.per_staff[staff_id]` | Editable integer input (0+). Placeholder "—" when absent. MANAGER can edit; SALES_STAFF sees own row read-only. |
  | Walkouts today | `conversion-feed.walkouts_today` | Read-only |
  | Conversion % | Auto-computed `(walk_ins - walkouts) / walk_ins * 100` | Read-only; shows "—" when `walk_ins = 0`; shows `null` badge ("No data") when missing footfall |
  | Status | Per-row: blank if COMPLETE, "Missing" label (amber, text-only, no bright bg) if `footfall_missing` | |
- **Store total row**: sum of walk-ins, sum of walkouts, store-level conversion %.
- **Manager aggregate topup section** (collapsed, visible to MANAGER+): delta input + reason text → `POST /walkins/manual-topup`. For unattributed browse-and-leave customers.
- **Save button**: calls `PATCH /walkins/per-staff` for each changed row. Per-row error display (422 shown inline).

**Modified: `DailyScorecardPage.tsx`**

Add a warning banner above the staff table: when `GET /walkins/walkins/today` returns `entry_status !== "COMPLETE"` and the date is today, show: "Walk-in count incomplete for today — conversion scores will be blocked. [Go to Footfall page]". Link to `/pos/footfall`. Banner is info-level (gray border, neutral text), not a red error — it does not block the page.

**API service additions:**
- `walkoutsApi.walkinsStatus(storeId?, date?)` → `GET /walkins/status`
- `walkoutsApi.walkinsPerStaffUpdate(payload, storeId?)` → `PATCH /walkins/per-staff`

**Design constraints (all applied):**
- Light-only. No dark token classes.
- Restrained/executive: neutral grays + single `bv-red` accent only for primary action. Status colors: amber text (`text-amber-700`) for PARTIAL/warning, green text (`text-green-700`) for COMPLETE, gray (`text-gray-400`) for PENDING. No colored card backgrounds for status.
- Color only for semantic meaning: the "Missing" per-row label is `text-amber-700` only.
- No emojis anywhere in Python or TypeScript.

---

## RBAC + flags

| Capability | Roles |
|---|---|
| View Footfall page | SUPERADMIN, ADMIN, AREA_MANAGER, STORE_MANAGER, SALES_STAFF, SALES_CASHIER, OPTOMETRIST |
| Edit per-staff walk-in count (PATCH /walkins/per-staff) | SUPERADMIN, ADMIN, AREA_MANAGER, STORE_MANAGER |
| Aggregate manual-topup (POST /walkins/manual-topup) | SUPERADMIN, ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT (existing gate, unchanged) |
| POS auto-increment (POST /walkins/pos-increment) | All customer-facing in-store roles (existing, unchanged) |
| Read conversion-feed | All authenticated (same as today — conversion-feed is the SC input) |
| Read walkins/status | All authenticated (same store-scoping as walkins/today) |
| Optometrist | Sees conversion % only; walk-in counts visible (they are operational staff); revenue is hidden on OTHER pages per DECISIONS §3. Footfall numbers are not revenue. |

**Feature flag:** NOT required. Footfall entry is not POS/checkout-critical. The `pos/footfall` route is already in `App.tsx`. No flag needed per DECISIONS §1 POS-safety rule (which applies only to POS/checkout changes).

---

## Engine + CORRECTIONS folded

**SC engine contract (SC.md):**
- F3footfall IS the footfall-capture layer that feeds `conversion_score()` in the SC engine. It does not reimplement the formula — it calls `_conversion_score_for` / `conversion-feed`.
- SC.md Open Conflict #6: "if a store forgets footfall, conversion silently = 0. Should the engine instead leave it null/blocked?" — **RESOLVED here as CORRECTIONS binding**: null/blocked (see below).

**HARDENING.md line 92 (the binding CORRECTION for this packet):**
> "SC conversion auto-calc scores 0 (not null) on missing footfall (`points.py:251`) — conflicts with 'Fail Loudly' and affects payout rupees. Pre-resolve to null/blocked-with-warning (or per-store toggle) before build."

**Applied as:** `_conversion_score_for()` returns `None` (not `0`) when `walk_ins <= 0`. `_build_row()` raises HTTP 422 with a descriptive message when auto-fill returns `None` AND the caller has not supplied an explicit `conversion` value. The block is a save-time error, not a read-time block — the manager can always override by supplying an explicit numeric `conversion` value in the request.

**DECISIONS.md §4 "Footfall capture":** "Manual only — staff enter walk-in counts; conversion % auto-computes from walk-ins vs walkouts." Applied: per-staff manual entry is the primary flow. POS auto-increment remains as a floor.

**DECISIONS.md §1 constraints applied:**
- No emojis in Python (all print/log use ASCII tags like `[WALKIN]`).
- Light-only, restrained UI.
- IST tz: date validation for "no future dates" uses IST aware comparison.
- Explicit status enum: `PENDING | PARTIAL | COMPLETE` (replaces the implicit "integer is 0 = nothing logged" color-as-meaning pattern from Excel).

**DECISIONS.md §3 Optometrist dashboard:** "Optometrists see test count + conversion rate only; revenue hidden." Applied: Footfall page shows walk-ins + conversion % to all roles including OPTOMETRIST; revenue fields are not present on this page.

---

## Acceptance tests (INTENT-LEVEL; a hollow shell must FAIL)

All tests live in `backend/tests/test_footfall_conversion.py` (new file). A hollow shell (e.g. endpoint that always returns HTTP 200 with dummy data, or stores walk_ins=0 silently) must fail these tests.

1. **Missing footfall blocks conversion score (the core correction):**
   Create a staff user. POST `/incentive/points/daily` with `conversion=null` for today. Assert HTTP 422 and response detail contains "footfall" when `walk_in_counters` has no entry for that staff. If the caller then supplies an explicit `conversion=15`, the save succeeds (manager override path).

2. **Walk-in entry sets per_staff and updates total:**
   PATCH `/walkins/per-staff` with `{staff_id: "S1", walk_ins: 8}`. Assert the `walk_in_counters` doc's `per_staff["S1"] == 8` and `total == pos_auto_count + manual_topup` (unchanged). Assert `per_staff_log` has exactly one entry with `new_val=8`. A second PATCH with `walk_ins=10` updates to 10 and appends a second log entry (old_val=8, new_val=10). A third PATCH with `walk_ins=0` sets to 0 (allowed — staff had no customers today).

3. **Entry status transitions correctly:**
   Store has 2 active staff (S1, S2). GET `/walkins/status` → `status=PENDING`. PATCH per-staff for S1 (walk_ins=5) → GET status → `status=PARTIAL`, `staff_with_data=[S1]`, `staff_missing=[S2]`. PATCH per-staff for S2 (walk_ins=0) → GET status → `status=COMPLETE`.

4. **Conversion-feed emits null (not 0) for missing footfall:**
   Staff S1 has 3 walkouts logged today but `per_staff` has no entry for S1. GET `/walkouts/conversion-feed?date=today`. Assert S1's `conversion_score == null` and `footfall_missing == true`. Another staff S2 has `walk_ins=10, walkouts=2` → `conversion_score == round((10-2)/10 * 20) = 16.0`.

5. **Conversion formula is correct (and not re-implemented):**
   Staff S1: walk_ins=10, walkouts_today=3, retro_conversions=1. `conversion_score = round((10-3+1)/10 * 20) = round(16.0) = 16`. Walk_ins=10, walkouts=12 → score capped at 0 (max(0, ...)). Walk_ins=10, walkouts=0, retro=0 → score = 20 (full conversion).

6. **RBAC: SALES_STAFF cannot call PATCH /walkins/per-staff for another staff:**
   SALES_STAFF user attempts PATCH with `staff_id` = another staff's ID → assert HTTP 403. STORE_MANAGER can update any staff in their store. Cross-store attempt by STORE_MANAGER → assert HTTP 403.

7. **Future date blocked:**
   PATCH `/walkins/per-staff` with `date_str` = tomorrow (IST) → assert HTTP 422. GET `/walkins/status?date=` tomorrow → assert HTTP 422.

8. **No double-count from POS auto-increment:**
   `walkin_counter_repo.auto_increment(store_id, sales_person_id="S1", mobile="9876543210")` called twice for the same mobile on the same day. Assert `per_staff["S1"] == 1` (not 2). PATCH per-staff with `walk_ins=5` does NOT reset `pos_auto_count` — the two paths are additive (manual is the `per_staff` dict; pos auto is `pos_auto_count`). Clarification: the PATCH sets the `per_staff` dict entry directly; it does not alter `pos_auto_count`. `total = pos_auto_count + manual_topup` remains the store-level total; `per_staff` is the attribution layer for SC scoring only.

---

## Effort (dev-days) + risk

| Slice | Days | Risk |
|---|---|---|
| D1+D2: null return from `_conversion_score_for` + `_build_row` 422 block | 0.5 | Low — 2-file change, well-contained. Risk: any existing test that asserts `conversion_score=0` on no footfall will need updating (intent correction). |
| D3: `walkins/status` endpoint + `entry_status` field | 0.5 | Low — new read endpoint, no money path. Requires enumerating active staff (user repo call). |
| D4: `PATCH /walkins/per-staff` endpoint + audit | 0.5 | Low-Med — single-doc `find_one_and_update`, audited. No transaction. Edge: PATCH with walk_ins=0 must be allowed (staff with zero customers is valid data, not missing data). |
| D5: FootfallPage frontend | 1.0 | Low — no new API contract; reuses existing endpoints. Editable per-staff table is new but straightforward. |
| D6: Scorecard warning banner | 0.25 | Low — FE-only. |
| D7: 8 intent tests | 0.75 | Low. |
| **Total** | **~3.5 dev-days** | Highest risk = the null correction (D1+D2) which changes behavior that existing tests may assert against. |

---

## Definition of done

- [ ] `_conversion_score_for()` returns `None` (not `0`) when `walk_ins <= 0`.
- [ ] `GET /walkouts/conversion-feed` emits `conversion_score: null, footfall_missing: true` for staff with no walk-in entry.
- [ ] `POST /incentive/points/daily` returns HTTP 422 with a footfall-explaining message when `conversion=null` (auto-fill) and no walk-in exists for that staff today. Explicit numeric `conversion` bypasses the block.
- [ ] `PATCH /api/v1/walkouts/walkins/per-staff` is implemented, RBAC-gated, audited, and atomically updates `per_staff` + appends `per_staff_log`.
- [ ] `GET /api/v1/walkouts/walkins/status` is implemented and returns `PENDING|PARTIAL|COMPLETE` with staff lists.
- [ ] `walk_in_counters` docs gain `entry_status` field on write.
- [ ] `pos/footfall` route renders `FootfallPage` (not the "Coming soon" placeholder). Page shows per-staff table with editable walk-in counts, auto-computed conversion %, date picker, status badge, and manager topup section.
- [ ] `DailyScorecardPage` shows a warning banner when today's footfall is `PARTIAL` or `PENDING`.
- [ ] All 8 acceptance tests pass; any pre-existing test that asserted `conversion_score=0` on missing footfall has been updated to assert `null`.
- [ ] No emojis in any new Python file. No dark-theme CSS tokens. IST-aware future-date guard on `PATCH /walkins/per-staff`.
- [ ] `tsc -b && vite build` exits 0.
- [ ] Backend smoke: `import backend.api.main` with `len(app.routes)` shows count increased by 2 (the two new endpoints).
# Feature #2: Internal Lab Routing & Tray Management
META: effort=M days=6 risk=MED roi=4 quickwin=no deps=none phase=3

## Existing overlap
Substantial overlap — this feature extends existing infrastructure rather than building from scratch:

- **Workshop job lifecycle** (`backend/api/routers/workshop.py`, 1636 lines): Full PENDING→IN_PROGRESS→COMPLETED→QC_FAILED→READY→DELIVERED state machine with immutable status_history, rework_count, QC checklist, technician assignment, lens-status sub-lifecycle (NOT_ORDERED→MOUNTED). The "tray scan to advance job stage" pattern is already built for the external vendor portal flow.
- **Scan-to-advance for workshop labels** (`backend/api/routers/labels.py`): `scan_advance()` endpoint already implements barcode-scan→stage-gate→timestamp pattern with `STATION_TARGET_STAGE` mapping and an immutable `scan_history` trail. This is 80% of the tray-routing concept.
- **Fitting details capture** (`workshop.py` `FittingDetails` schema, lines 99–148): `confirmed_by_sales` gate already blocks `IN_PROGRESS` unless sales confirmed the job. The "tray is handed to lab" checkpoint mirrors this.
- **Barcode lifecycle trace** (`backend/api/routers/inventory.py:532-595`): Cross-collection join tracing a barcode through stock→purchase→sales→transfer→return. Tray barcode trace follows the same pattern.
- **Workshop dashboard KPIs** (`GET /api/v1/workshop/dashboard-kpis`): pending/in_progress/qc_failed/ready_for_pickup/overdue/avg_turnaround_days already surfaced. Tray-level metrics (station dwell times) extend this.
- **QZ Tray label printing** (`printer_settings.qz_enabled`): Print infrastructure for job-card stickers already in settings.
- **Audit trail** (`audit_logs` collection + `workshop_jobs.status_history[]`): Every status change is already timestamped and stored.

## Reuse (extend, don't rebuild)
- `backend/api/routers/labels.py` — extend `STATION_TARGET_STAGE` map and `scan_advance()` to support in-house station codes (SURFACING, EDGING, COATING, QC_LAB, DISPATCH); the forward-only gate and `scan_history` array already exist
- `backend/api/routers/workshop.py` — extend `WorkshopJob` doc with `tray_id` field + `tray_scan_history[]` sub-array; reuse `update_job_status()` + `_append_status_history()` patterns
- `workshop_jobs` collection — add `tray_id`, `tray_barcode`, `current_station`, `station_timestamps{}` fields; no new collection needed for job-tray linkage
- `backend/database/repositories/workshop_repository.py` — add `find_by_tray_id()`, `find_jobs_at_station()` query methods
- `GET /workshop/dashboard-kpis` — extend response with per-station queue counts and average dwell time
- `frontend/src/pages/workshop/WorkshopPage.tsx` — extend kanban columns to show station-level sub-status within IN_PROGRESS; add tray-scan input bar

## Data model
**New fields on `workshop_jobs`** (no new collection needed for core routing):
- `tray_id`: string (human-readable, e.g. `TRY-2026-001`) — assigned at job creation or first scan
- `tray_barcode`: string — scannable code printed on physical tray label
- `current_station`: enum `INTAKE|SURFACING|EDGING|COATING|QC_LAB|DISPATCH` (null until first scan)
- `station_timestamps`: object `{SURFACING: datetime, EDGING: datetime, ...}` — stamped on each scan-in
- `station_dwell_ms`: object `{SURFACING: int, ...}` — computed on scan-out (next station arrival minus current station arrival)
- `tray_scan_history[]`: array of `{station, scanned_by, scanned_by_name, scanned_at, event: SCAN_IN|SCAN_OUT}` — immutable append

**New collection: `lab_stations`** (store-configurable station registry):
- `station_id`, `store_id`, `code` (SURFACING/EDGING/COATING/QC_LAB/DISPATCH), `label` (display name), `sequence_order` (int, 1-N), `is_active`, `target_dwell_minutes` (SLA threshold for TASKMASTER alerts)

**New collection: `tray_master`** (optional — only if owner wants trays independent of jobs, e.g. reusable physical trays):
- `tray_id`, `tray_barcode`, `store_id`, `status` (EMPTY|IN_USE|RETIRED), `current_job_id`, `created_at`

## Backend
- `POST /workshop/trays` — mint a tray (auto-generate `tray_barcode`, print via QZ); WORKSHOP_STAFF / STORE_MANAGER
- `GET /workshop/trays/{tray_id}` — tray detail: current job, full scan history, station dwell times
- `POST /workshop/trays/scan` — core endpoint: `{tray_barcode, station_code, scanned_by}` → validates tray exists + job is open + station is next-in-sequence → advances `current_station`, appends `tray_scan_history`, stamps `station_timestamps`, emits `workshop.tray_scanned` event; returns job summary for display terminal; reuses `labels.scan_advance()` guard pattern
- `GET /workshop/stations/{station_code}/queue` — jobs currently at a given station (by `current_station` field); store-scoped; used by station display screens
- `GET /workshop/dashboard-kpis` — **extend** existing endpoint to add `per_station_counts{}` and `avg_dwell_by_station{}` fields (additive, no breaking change)
- `GET /workshop/trays/{tray_barcode}/trace` — full movement history across stations; mirrors `GET /inventory/barcode/{barcode}/trace` pattern
- `POST /workshop/jobs/{job_id}/assign-tray` — link an existing job to a tray_id (for jobs created before tray-routing rollout); STORE_MANAGER / ADMIN

## Frontend
- **Station Scan Terminal** (`frontend/src/pages/workshop/StationScanPage.tsx`) — single-purpose fullscreen page for tablet/desktop at each bench; shows station name + queue count; large barcode input (autofocus); on successful scan shows job summary (customer name, frame+lens description, current step, job number) for 3 seconds then clears; on error shows red flash with reason (wrong station / job closed / tray not found); restrained light-only, minimal chrome, high-contrast text for workshop floor lighting
- **Tray Status Panel** (component within `WorkshopPage.tsx`) — add "Tray" column to existing kanban cards showing `tray_id` badge and `current_station` chip; clicking opens tray trace drawer (scan history timeline); no new page required
- **Station Queue View** (tab on `WorkshopPage.tsx`) — list of jobs per station with dwell-time aging (green <50% SLA, amber 50-80%, red >80%); sortable by dwell time; STORE_MANAGER can reassign technician from here
- **Tray Label Print** (extend `BarcodeManagementModal.tsx`) — "Print Tray Label" action triggers QZ Tray raw print of tray_barcode in Code128 + human-readable tray_id; uses existing `printer_settings.qz_enabled`

## Business rules
- Station sequence is **forward-only**: scanning a tray at EDGING when it is still at INTAKE is rejected with a clear error (same guard as `labels.next_stage()`)
- A tray can hold **one active job** at a time; assigning a second job while status=IN_USE is a 409 conflict
- Jobs cannot reach READY (QC pass) without passing through `QC_LAB` station (enforced alongside existing `qc_passed=True` guard)
- `station_dwell_ms` is **computed** on write (never client-supplied); immutable once written
- Scan history is **append-only**; no delete or edit endpoints
- A tray scan on a CANCELLED or DELIVERED job returns 422 with reason "job is closed"
- If `DISPATCH` station is scanned, job auto-transitions to READY (triggering existing `notify_ready()` WhatsApp via `workshop.notify-ready`)
- All tray assignments and scans written to `audit_logs` with `entity_type=workshop_tray`

## RBAC
| Role | Permission |
|---|---|
| WORKSHOP_STAFF | Scan trays at any station; view station queue for their store; view tray trace |
| STORE_MANAGER | All of above + mint trays, assign trays to jobs, view dwell-time report, configure station SLA thresholds |
| AREA_MANAGER / ADMIN / SUPERADMIN | All stores; view cross-store station analytics |
| OPTOMETRIST / SALES_CASHIER / SALES_STAFF | Read-only: tray_id + current_station visible on job cards (so floor knows if job is in QC) |
| ACCOUNTANT / CATALOG_MANAGER | No access |

## Integrations
- **TASKMASTER agent** — subscribe to `workshop.tray_dwell_exceeded` event (emitted when `station_dwell_ms` > `target_dwell_minutes * 60000`); TASKMASTER creates a P2 task assigned to STORE_MANAGER ("Job #X stuck at EDGING for 4h 20m — SLA threshold 3h"); reuses existing `_escalate_overdue_tasks()` pattern
- **QZ Tray / printer_settings** — tray label printing via existing `qz_enabled` flag and `label_size` config; no new integration
- **MSG91 / MEGAPHONE** — no new trigger; existing `notify_ready()` WhatsApp fires when DISPATCH station is scanned (job transitions to READY)
- **Shopify / Razorpay / Tally** — none

## Risk notes
- **POS/money risk: none** — this is a pure operational/logistics feature; no pricing, payment, or ledger changes; no feature flag required
- **Data migration**: existing `workshop_jobs` without `tray_id` will have null tray fields; station scan and tray-trace endpoints must handle null gracefully (return empty history, not 500)
- **Station sequence is store-specific**: a store may skip a station (e.g. no in-house coating); `lab_stations` must be configurable per store and sequence gaps handled without rejecting scans
- **Concurrent scans**: two technicians scanning the same tray simultaneously → use `find_one_and_update` with `current_station` in the filter (same guard pattern as `vouchers.redeem_voucher_atomic`); loser gets 409
- **Offline / tablet connectivity**: station scan terminals on workshop floor may have spotty wifi; recommend optimistic local queue (IndexedDB) with sync-on-reconnect — this is a Phase 2 enhancement, not MVP
- **Barcode collision**: `tray_barcode` must be globally unique per store; use `generate_barcode(store_id, tray_sequence)` pattern from `inventory.py:88`

## Recommendation
Build in Phase 3 alongside workshop hardening — not a quick win (requires physical tray hardware + label printer setup), but high ROI for stores processing >10 jobs/day. The scan-to-advance infrastructure in `labels.py` means backend effort is 2–3 days; the station terminal UI is the main build. Do **not** build a separate tray-routing system — fold entirely into `workshop_jobs` + extend `labels.py` scan_advance.

## Owner decisions
- Q: Should trays be **reusable** (physical tray returned after job delivers) or **disposable** (paper job card with barcode, one per job)? | Why: Reusable trays need a `tray_master` collection and a "clear tray" scan at DISPATCH; disposable trays skip that collection entirely and are cheaper to operate | Options: (a) reusable physical trays — better for volume, needs a tray-return workflow / (b) disposable printed job cards — simpler, zero tray management overhead, suitable for <20 jobs/day
- Q: Which stations apply to your in-house lab, and in what order? | Why: The `lab_stations` sequence drives the forward-only scan guard; if you only do edging + QC in-house (lenses sourced pre-surfaced), the station list is shorter | Options: examples — (a) INTAKE → EDGING → COATING → QC_LAB → DISPATCH / (b) INTAKE → QC_LAB → DISPATCH (for pass-through labs) / (c) custom sequence you define
- Q: What is the SLA threshold (in hours) per station before TASKMASTER alerts the Store Manager? | Why: This sets `target_dwell_minutes` in `lab_stations`; too tight = alert noise, too loose = misses real delays | Options: suggest 2h SURFACING / 3h EDGING / 2h COATING / 1h QC_LAB as defaults — confirm or adjust per your workflow
- Q: Should scanning the DISPATCH station **automatically notify the customer** (WhatsApp "your eyewear is ready") or should staff trigger it manually as today? | Why: Auto-notify on scan removes a manual step but fires the message the moment the job leaves the lab bench — before the retail staff has physically received it | Options: (a) auto-notify on DISPATCH scan / (b) keep manual notify button on WorkshopPage as today / (c) auto-notify only after Store Manager confirms receipt at front desk (adds a FRONT_DESK station)
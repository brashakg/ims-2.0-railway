# Feature #13: Remake Justification & Spoilage Analytics
META: effort=M days=4 risk=MED roi=4 quickwin=no deps=none phase=3

## Existing overlap
IMS already has partial remake tracking in the workshop module:
- `rework_count` field on `workshop_jobs` documents (workshop.py:1217-1236) — incremented on each QC_FAILED → IN_PROGRESS transition
- `qc_history` array (workshop.py:1072) — per-attempt checklist items, pass/fail, notes, waiver reason
- `qc_waive_reason` field — captures waiver context but no structured reason taxonomy
- `lens_status` lifecycle (NOT_ORDERED → ORDERED → RECEIVED → MOUNTED, workshop.py:36-89) — tracks lens physical movement but not cost or spoilage disposition
- `vendor_status_history` (workshop.py:1551-1630) — records lab-side events (DISPATCHED, DELIVERED, ON_HOLD) but no "spoilage confirmed by lab" state
- `workshop_kpis` endpoint (workshop.py, Phase 6.4) — surfaces qc_failed count and avg_turnaround but no spoilage cost or reason breakdown

No structured remake reason taxonomy, no spoilage cost capture, no margin-bleed dashboard exists today. The lens ledger (lens_stock_lines, lens_stock_audit from lens_stock.py) tracks on_hand/reserved/committed but does not record a unit as "spoiled" — MOUNTED commits a lens cell and that is the end of the trail regardless of whether the lens was broken or returned by the lab.

## Reuse (extend, don't rebuild)
- `workshop_jobs` collection — add `remake_reason`, `spoilage_category`, `spoilage_cost`, `spoilage_confirmed_by_lab` fields; no new collection for job-level data
- `qc_history` array on `workshop_jobs` — already records per-attempt notes; extend each entry with a `remake_reason_code` field
- `backend/api/routers/workshop.py` `rework_job()` endpoint (line 1198) — extend to accept structured `remake_reason_code` + `spoilage_category` + optional `spoilage_cost_override`; currently only accepts `notes`
- `lens_stock_audit` collection (lens_stock.py) — add `source_type=SPOILAGE` rows when a remade lens cell is written off; reuses existing audit schema (lens_line_id, store_id, sph, cyl, qty, notes)
- `backend/api/routers/workshop.py` `workshop_dashboard_kpis()` — extend to include spoilage_cost_mtd, top_remake_reason, remake_rate_pct alongside existing KPI cards
- `frontend/src/pages/workshop/WorkshopPage.tsx` — extend the existing kanban/job detail panel; no new page needed for job-level capture
- `backend/api/services/purchase_match.py` `moving_average_cost()` — reuse WAC cost per lens SKU for spoilage cost valuation when no manual override provided

## Data model
New fields on existing `workshop_jobs` documents:
- `remake_reasons[]` — array of objects `{attempt: int, reason_code: str, spoilage_category: str, notes: str, logged_by: str, logged_at: datetime}` — one entry per rework attempt, appended in `rework_job()`
- `spoilage_cost` — float, total lens cost written off across all remake attempts for this job (sum of per-attempt costs)
- `spoilage_confirmed_by_lab` — bool, set when lab portal posts a status of `SPOILED` or vendor marks the lens defective (extends `vendor_status_history` source)

New fields on existing `lens_stock_audit` documents (no schema change required — all fields already exist):
- Populate `source_type='SPOILAGE'`, `source_id=job_id`, `notes=remake_reason_code` when a remade lens is written off

New collection `remake_reason_codes` (singleton config, SUPERADMIN-editable):
- `_id: "default"`, `codes[]` each with `{code: str, label: str, category: "DOCTOR_RX_ERROR" | "LAB_SPOILAGE" | "FITTING_ERROR" | "CUSTOMER_CHANGE" | "FRAME_BREAKAGE" | "OTHER"}`, `is_active: bool`
- Seeds six default codes on first read if collection is empty; owner can add/retire codes without a deploy

## Backend

`POST /api/v1/workshop/jobs/{job_id}/rework` (extend existing `rework_job()` in workshop.py:1198)
- Accept `remake_reason_code` (required string, validated against active codes), `spoilage_category` (derived from code if not supplied), `spoilage_cost_override` (optional float, falls back to WAC from products.cost_price)
- Append entry to `job.remake_reasons[]`; increment `rework_count` (existing); compute and accumulate `spoilage_cost` on the job doc
- Write `lens_stock_audit` row with `source_type=SPOILAGE`, `qty=-1` (or actual remade qty), `source_id=job_id`
- Existing audit trail (`audit_logs`) entry already written by rework_job; extend `detail` dict with `{remake_reason_code, spoilage_cost}`

`GET /api/v1/workshop/jobs/{job_id}/remake-history` (new, lightweight)
- Returns `job.remake_reasons[]` enriched with code labels from `remake_reason_codes`
- Reuses the existing job fetch pattern

`GET /api/v1/workshop/spoilage-analytics` (new)
- Query params: `store_id`, `from_date`, `to_date`, `group_by=reason|category|technician|vendor`
- Aggregates across `workshop_jobs` where `rework_count > 0` in date range
- Returns: `total_remakes`, `total_spoilage_cost`, `remake_rate_pct` (remakes / total_jobs), breakdown array sorted by cost descending
- RBAC: STORE_MANAGER / AREA_MANAGER / ADMIN / SUPERADMIN / ACCOUNTANT

`GET /api/v1/workshop/remake-reason-codes` and `PUT /api/v1/workshop/remake-reason-codes` (new, SUPERADMIN only)
- Read/write the `remake_reason_codes` singleton; validate code uniqueness; cannot delete a code that has been used (soft-retire via `is_active=false`)

Extend `GET /api/v1/workshop/dashboard-kpis` (existing, workshop.py Phase 6.4):
- Add `spoilage_cost_mtd`, `remake_rate_pct`, `top_remake_category` to the existing KPI payload; no breaking change

## Frontend

**WorkshopPage.tsx — rework modal extension**
- When staff clicks "Mark for Rework" (QC_FAILED → IN_PROGRESS transition), the existing confirmation dialog gains:
  - Required dropdown: Remake Reason (populated from `GET /remake-reason-codes`); grouped by category
  - Optional text: Additional notes (existing field)
  - Read-only computed field: Estimated Spoilage Cost (WAC from product, shown in gray)
- No new page; the rework modal is already present

**WorkshopPage.tsx — job detail panel extension**
- "Remake History" collapsible section below the existing QC history card
- Lists each `remake_reasons[]` entry: attempt number, reason label, category badge, cost, logged by, date
- Neutral color palette: category badges use single semantic colors (red = LAB_SPOILAGE, amber = DOCTOR_RX_ERROR, gray = OTHER)

**New page: `/workshop/spoilage-analytics` (extend existing WorkshopPage tab bar or add as a Reports sub-tab)**
- Summary cards (top row, light gray background, single accent): Total Remakes MTD, Spoilage Cost MTD, Remake Rate %, Lab-Confirmed Spoilages
- Bar chart: Spoilage cost by reason category (current month; prior month comparison line)
- Table: Per-job detail — job number, customer, lens SKU, reason, cost, technician, vendor, date; sortable; CSV export
- Store filter (AREA_MANAGER and above see all stores; STORE_MANAGER sees own)
- No dark tokens; restrained executive palette consistent with existing WorkshopPage

## Business rules
- `remake_reason_code` is required when triggering rework — the rework endpoint must reject (422) if absent; no silent null
- Spoilage cost defaults to product WAC (`products.cost_price` or `moving_average_cost()` result); a manual override is allowed only for ADMIN / SUPERADMIN (not STORE_MANAGER or WORKSHOP_STAFF)
- A reason code cannot be deleted once used on any job — only retired (`is_active=false`); retired codes remain readable on historical jobs
- `spoilage_confirmed_by_lab` can only be set to `true` by the vendor portal (`vendor_portal.py`) posting status `SPOILED`, or by ADMIN/SUPERADMIN via the job detail panel — WORKSHOP_STAFF cannot self-certify lab spoilage
- The `lens_stock_audit` SPOILAGE entry is written atomically with the rework transition; if the lens audit write fails, the rework transition still completes (fail-soft, logged as WARNING) — a lens ledger discrepancy is preferable to blocking a job
- Spoilage cost is informational and advisory only — it does NOT automatically create an accounting entry, AP debit note, or vendor claim; those remain manual workflows (vendor_returns.py debit note flow is the human path)
- Period lock check: if the job's `created_at` month is locked (`period_locks`), spoilage cost cannot be back-attributed to that month; it falls to the current open period

## RBAC
| Role | Spoilage analytics page | Rework + reason logging | Cost override | Reason code admin |
|---|---|---|---|---|
| SUPERADMIN | Full (all stores) | Yes | Yes | Yes |
| ADMIN | Full (all stores) | Yes | Yes | No |
| AREA_MANAGER | Own area stores | Yes | No | No |
| STORE_MANAGER | Own store | Yes | No | No |
| ACCOUNTANT | Full (all stores, read-only) | No | No | No |
| OPTOMETRIST | No | No | No | No |
| WORKSHOP_STAFF | No | Yes (reason required, no cost override) | No | No |
| All other roles | No | No | No | No |

## Integrations
- **Tally**: Spoilage cost is informational today; if owner decides spoilage should flow into P&L as a loss line, NEXUS nightly Tally export (nexus.py `_build_tally_export()`) would need a new voucher type (JOURNAL — Lens Spoilage Loss). This is an owner decision; not wired by default.
- **Vendor portal** (`vendor_portal.py`): Extend the lab's allowed status values to include `SPOILED`; receiving `SPOILED` from lab auto-sets `spoilage_confirmed_by_lab=True` on the job and appends to `vendor_status_history`. This is the only external integration needed.
- **ORACLE agent**: After this feature ships, ORACLE's `_detect_anomalies()` tick can be extended to flag stores where remake_rate_pct exceeds a configurable threshold and surface it as a proposal for Superadmin review. Not in scope for this build; add as a future ORACLE hook.

## Risk notes
- **Lens ledger accuracy depends on consistent data entry**: If WORKSHOP_STAFF skip the reason code (currently allowed via notes field), the analytics are incomplete. The backend enforcement (require reason on rework) is the primary control; training is a human dependency.
- **Cost valuation accuracy**: WAC from `products.cost_price` is a lagging average; it may not reflect the specific lens batch cost (especially for high-index or branded lenses). The cost override for ADMIN mitigates this but adds a manual step.
- **No accounting automation by default**: Spoilage cost shown in dashboard does not flow into P&L without Tally integration. If owner expects the P&L to reflect spoilage automatically, that requires the Tally voucher extension and is a separate scoped effort (~0.5 days).
- **No POS impact**: This feature is workshop-only; POS is not touched. No feature flag needed.
- **Backward compatibility**: Existing `rework_job()` callers (frontend rework modal) currently send only `notes`. The endpoint extension must keep `remake_reason_code` required for new calls but accept the absence gracefully on any legacy direct API calls (return 422 with clear message rather than 500). The frontend update ships atomically with the backend change.

## Recommendation
Build in Phase 3 as a self-contained workshop extension. It is not a quick win (requires reason-code taxonomy agreement from owner first) but has strong ROI for margin visibility — spoilage is a direct, often invisible cost center in optical retail. Fold the `spoilage_cost_mtd` KPI card into the existing `workshop_dashboard_kpis` endpoint immediately (even before the full analytics page) so the owner sees the number on day one.

## Owner decisions
- Q: Which remake reason codes should be in the default taxonomy, and are any specific to your lab/vendor relationships? | Why: The reason codes drive the analytics breakdown; if codes are too generic (e.g., only "Lab Error" and "Store Error") the dashboard cannot pinpoint whether spoilage comes from a specific vendor, a specific optometrist's Rx style, or a frame-fitting issue. | Options: (a) Use the six built-in defaults (Doctor Rx Error, Lab Spoilage, Fitting Error, Customer Change, Frame Breakage, Other) and refine after 30 days of data; (b) Specify a custom list now before go-live; (c) Start with only two codes (Lab vs Store) and expand later
- Q: Should spoilage cost automatically create a journal entry in Tally as a loss, or remain dashboard-only until reviewed manually? | Why: Automatic Tally posting means the P&L reflects spoilage in real time but requires the NEXUS Tally export to be extended and tested; manual means the accountant reviews the spoilage report monthly and books it themselves. | Options: (a) Dashboard-only for now, accountant books manually; (b) Auto-post to Tally as a JOURNAL voucher (adds ~0.5 days effort); (c) Auto-post only when spoilage_confirmed_by_lab is true (hybrid)
- Q: Should the vendor (lens lab) be able to mark a lens as spoiled through their portal, and does that trigger an automatic debit note against them? | Why: If the lab confirms spoilage, there may be a commercial claim (debit note / credit from vendor); deciding this now determines whether the vendor portal extension needs to connect to vendor_returns.py or just record the status. | Options: (a) Lab marks spoiled via portal for visibility only, no automatic debit note; (b) Lab marking spoiled auto-drafts a debit note in vendor_returns for the accountant to review and approve; (c) Not applicable — spoilage is always the store's cost regardless of lab fault
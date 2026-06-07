# Feature #29: Skills-Based Rostering & Alerts
META: effort=L days=12 risk=MED roi=4 quickwin=no deps=none phase=3

## Existing overlap
IMS already has substantial HR/attendance groundwork to build on:

- **12 roles with shift assignment**: `hr.py` shift config (shift_id, start_time, end_time, grace_minutes, weekly_off) + user.shift_id assignment (`hr.py:1621-1654`). The OPTOMETRIST role is a first-class citizen in `rbac_policy.py`.
- **Attendance tracking with geo-fence**: `attendance` collection with check-in/check-out, `attendance_engine.py` resolves shift for each employee at check-in time.
- **Leave management**: `leaves` collection with APPROVED/PENDING/REJECTED status and overlap detection — this directly affects who is actually available on a given day.
- **In-app bell notifications**: `notifications` collection + bell API (`notifications.py`), already used by TASKMASTER for escalation alerts.
- **WhatsApp/SMS dispatch**: MEGAPHONE agent + `notification_service.py` with DISPATCH_MODE gating — already sends operational alerts.
- **TASKMASTER SLA escalation**: `taskmaster.py` already auto-escalates overdue tasks up the role ladder (Store Manager → Area Manager → Admin → Superadmin). The roster-breach alert follows the exact same escalation pattern.
- **No rostering engine**: `hr.py` audit confirms shift assignment is per-employee only — no "assign staff to date slots" or "view week schedule by store/date" exists. Greenfield for the schedule matrix itself.
- **No skills/license registry**: No `license_number`, `license_expiry`, `skills[]`, or `certifications[]` on users or salary_config. Greenfield.
- **No ratio enforcement**: No rule engine checking doctor:retail ratio per shift.

## Reuse (extend, don't rebuild)
- `backend/api/routers/hr.py` — extend with roster CRUD endpoints; reuse `_resolve_employee_shift()` (line 669) and shift schema as foundation
- `backend/api/services/attendance_engine.py` — extend `evaluate_geofence()` to call a new `check_roster_coverage()` on check-in; reuse `can_approve_swap()` pattern for roster approval
- `backend/database/schemas.py` — add new `rosters` + `staff_skills` indexes here alongside existing `attendance`, `shifts`, `leaves`
- `backend/api/routers/notifications.py` + `notification_service.py` — reuse `send_notification()` for HQ alerts; reuse bell API for in-app roster warnings
- `backend/agents/implementations/taskmaster.py` — extend `_do_background_work()` with a `_check_roster_coverage()` sweep (runs every 5 min already); reuse `_audit_log()` and `notify_escalation()` patterns
- `backend/agents/quiet_hours.py` — reuse `in_quiet_hours()` to suppress non-urgent roster alerts at night
- `frontend/src/pages/hr/PayrollDashboard.tsx` + `frontend/src/pages/attendance/AttendancePage.tsx` — extend nav/sidebar to surface the new Rostering tab within the existing HR module
- `backend/api/services/rbac_policy.py` — extend POLICY list with new roster endpoints; reuse `store_scoped` flag for per-store roster data

## Data model

**New collection: `staff_skills`** (one doc per user)
```
{
  user_id: str,               // FK → users
  store_id: str,              // home store
  role: str,                  // canonical role (e.g. OPTOMETRIST)
  skills: [str],              // e.g. ["DISPENSING", "CONTACT_LENS_FITTING", "REFRACTION"]
  license_number: str|null,   // e.g. "OPT-MH-12345" (required for OPTOMETRIST)
  license_expiry: date|null,  // enforced: must be future for licensed roles
  license_verified: bool,     // admin toggles after seeing original doc
  license_verified_by: str,   // user_id of verifier
  license_verified_at: datetime,
  updated_at: datetime
}
```

**New collection: `rosters`** (one doc per store-date-shift slot)
```
{
  roster_id: str,             // UUID
  store_id: str,
  shift_id: str,              // FK → shifts
  roster_date: str,           // "YYYY-MM-DD"
  assigned_staff: [           // array of slot assignments
    {
      user_id: str,
      user_name: str,
      role: str,
      slot_status: str,       // SCHEDULED | CONFIRMED | ABSENT | SWAPPED
      swap_with: str|null,    // user_id if swapped
      note: str|null
    }
  ],
  coverage_status: str,       // OK | WARNING | BREACH   (computed on write)
  breach_reason: str|null,    // e.g. "No licensed optometrist assigned"
  alert_sent_at: datetime|null,
  created_by: str,
  created_at: datetime,
  updated_at: datetime
}
```

**New fields on existing `shifts` collection:**
```
required_roles: [             // NEW — minimum role requirements per shift
  { role: str, min_count: int, license_required: bool }
]
// e.g. [{"role":"OPTOMETRIST","min_count":1,"license_required":true},
//        {"role":"SALES_STAFF","min_count":2,"license_required":false}]
```

**New fields on existing `stores` collection:**
```
clinical_store: bool          // NEW — true = doctor:retail ratio enforced
                              // false = sales-only store, no optometrist required
```

## Backend

- `GET /api/v1/hr/roster?store_id=&week_start=YYYY-MM-DD` — fetch 7-day roster grid for a store (STORE_MANAGER/AREA_MANAGER/ADMIN/SUPERADMIN); returns roster docs + leave-approved absences + coverage_status per day
- `POST /api/v1/hr/roster` — create/update roster slot (assign staff to a date+shift); triggers coverage recompute; STORE_MANAGER/AREA_MANAGER/ADMIN; validates license_expiry for OPTOMETRIST slots
- `POST /api/v1/hr/roster/{roster_id}/swap` — record a slot swap (slot_status=SWAPPED, swap_with=user_id); rechecks coverage
- `GET /api/v1/hr/roster/coverage-alerts?store_id=&date=` — returns all BREACH/WARNING roster docs for a store on a date; used by TASKMASTER sweep and HQ dashboard
- `GET /api/v1/hr/staff-skills?store_id=` — list staff with their skills + license status (STORE_MANAGER+); includes `license_expiry_warning` flag for licenses expiring within 30 days
- `POST/PUT /api/v1/hr/staff-skills/{user_id}` — create/update skills + license fields (ADMIN/SUPERADMIN only for `license_verified` toggle; STORE_MANAGER can edit skills/license_number but not verify)
- `GET /api/v1/hr/roster/weekly-summary?store_id=&week_start=` — aggregated coverage OK/WARNING/BREACH counts per day for the week; feeds the dashboard heatmap
- `POST /api/v1/hr/attendance/check-in` (EXTEND existing) — after geo-fence passes, call `check_roster_coverage(store_id, date, user_id)` and attach `roster_coverage_ok: bool` to response; if BREACH still active after check-in, emit `roster.breach` event to TASKMASTER
- Internal service `backend/api/services/roster_engine.py` (new pure module):
  - `compute_coverage(store_id, date, shift_id, assigned_staff, approved_leaves)` → `{status, breach_reason}`
  - `check_license_valid(user_id, role)` → bool (queries `staff_skills`, checks `license_expiry > today` and `license_verified=true`)
  - `get_effective_roster(store_id, date)` → merged view of roster assignments minus leave-approved absences

## Frontend

Extend the existing HR module (tab within `frontend/src/pages/hr/` area, restrained light-only):

- **RosteringPage.tsx** (new page, route `/hr/rostering`) — 7-day grid view per store; rows = shifts, columns = days; each cell shows assigned staff chips (name + role icon); coverage status badge (green OK / amber WARNING / red BREACH) per cell; click cell → assignment drawer
- **RosterSlotDrawer.tsx** (new component) — slide-in panel showing: current assignees for that shift/date, add/remove staff picker (filtered by store), swap button, leave conflicts highlighted in amber, license-expiry warning banner for OPTOMETRIST slots
- **CoverageAlertBanner.tsx** (new component, reused across HR and HQ Hub) — inline alert strip: "Store X has no licensed optometrist on [date]" with escalation status; appears on Hub dashboard for AREA_MANAGER+ if any store has BREACH
- **StaffSkillsTab.tsx** (new tab within existing HR settings or staff profile) — table of staff with role, skills chips, license number, expiry date, verified badge; ADMIN sees "Verify" toggle; license-expiry-warning rows highlighted in amber
- **AttendancePage.tsx** (EXTEND existing) — add coverage status chip next to date in manager's daily view: shows whether that day's roster was OK/WARNING/BREACH at shift start

## Business rules

- A roster slot for an OPTOMETRIST must have `license_verified=true` AND `license_expiry > roster_date`; if not, slot is flagged WARNING and cannot be set CONFIRMED
- If a `clinical_store` has no verified OPTOMETRIST assigned (or assigned one goes ABSENT at check-in), coverage_status becomes BREACH
- `required_roles` on the shift define the minimum floor; falling below any `min_count` triggers WARNING; zero of a `license_required` role triggers BREACH
- An approved leave (`leaves.status=APPROVED`) automatically removes that staff from effective coverage — `roster_engine.get_effective_roster()` subtracts approved leaves before computing coverage
- BREACH at shift-start (defined as 30 minutes before `shift.start_time`) triggers HQ alert via WhatsApp (DISPATCH_MODE-gated) + in-app bell to AREA_MANAGER + ADMIN; alert is idempotent (once per roster_id per day, `alert_sent_at` stamp prevents duplicates)
- License expiry within 30 days triggers a WARNING (not BREACH) and a weekly reminder to ADMIN; expired license transitions any assigned slot to BREACH immediately
- Roster changes within 4 hours of shift start require AREA_MANAGER or above (not STORE_MANAGER alone) — enforced in `POST /roster` based on `roster_date + shift.start_time vs now()`
- All roster mutations write an immutable `audit_logs` entry (action=ROSTER_ASSIGN/SWAP/REMOVE, before/after state)
- Non-clinical stores (`clinical_store=false`) skip OPTOMETRIST coverage checks entirely

## RBAC

| Role | Roster Read | Roster Write | Skills Read | Skills Write (own store) | License Verify |
|---|---|---|---|---|---|
| SUPERADMIN | All stores | All stores | All stores | All stores | Yes |
| ADMIN | All stores | All stores | All stores | All stores | Yes |
| AREA_MANAGER | Managed stores | Managed stores | Managed stores | Managed stores | No |
| STORE_MANAGER | Own store | Own store | Own store | Own store (no verify) | No |
| OPTOMETRIST | Own store (read only) | No | Own profile | No | No |
| SALES_STAFF / CASHIER / others | No | No | No | No | No |

All roster endpoints flagged `store_scoped: True` in POLICY — non-HQ roles see only their store's data.

## Integrations

- **MSG91 WhatsApp** (via `notification_service.py` + MEGAPHONE) — BREACH alerts to AREA_MANAGER/ADMIN phone; DISPATCH_MODE-gated (no real sends in off/test mode)
- **TASKMASTER agent** — extend `_do_background_work()` with `_check_roster_coverage()` sweep at 05:30 IST daily (before shift start for most stores) and at shift-start time; emits `roster.breach` event consumed by SENTINEL for monitoring
- **In-app bell** (`notifications.py`) — BREACH and license-expiry warnings surfaced in the HQ bell
- No Shopify / Razorpay / Tally dependency

## Risk notes

- **Leave integration coupling**: `leaves` collection must be queried at roster-compute time; if a leave is approved after the roster is built, coverage re-check must fire — requires a leave-approval side-effect hook (extend `PUT /hr/leaves/{id}/approve` to call `roster_engine.compute_coverage()` for affected date)
- **No real-time push**: Coverage status is computed on write and on TASKMASTER sweep; there is no websocket, so the roster grid needs a manual refresh or short poll (30s) to stay live — acceptable for a planning tool
- **License data quality cold-start**: `staff_skills` starts empty; stores need to enter license data before the rule bites. Ship with `license_required` rules defaulting to WARNING (not BREACH) for the first 30 days post-launch (a `grace_period_until` field on `required_roles`), then auto-tighten. This prevents day-1 false alarms.
- **No POS/money touch**: Feature is entirely HR/operational — no pricing, payment, or GST involvement. No feature flag needed for the roster engine itself. The WhatsApp alert path is already behind DISPATCH_MODE.
- **Shift-start timing**: TASKMASTER runs every 5 minutes; the 30-min-before-shift-start window is a best-effort trigger, not exact. Acceptable for an alert tool.

## Recommendation

Build later (Phase 3, after core HR leave-balance accrual is filled — that gap directly affects coverage accuracy). The license registry (`staff_skills`) is the highest-value quick sub-task and can be shipped standalone in ~2 days as a prerequisite; the full rostering grid and TASKMASTER sweep follow in the remaining 10 days.

## Owner decisions

- Q: Which stores are `clinical_store=true`? | Why: Only these stores enforce the doctor:retail ratio; sales-only kiosk stores would be flagged erroneously otherwise | Options: Mark all current Better Vision + WizOpt full-format stores as clinical; exclude any kiosk/pop-up locations
- Q: What is the minimum doctor:retail ratio per shift? | Why: This becomes the `required_roles` config on each shift template (e.g., 1 optometrist : 2 sales staff minimum) | Options: 1:2 (conservative) / 1:3 (current informal practice) / variable by store size
- Q: Who should receive the BREACH WhatsApp alert — Area Manager only, or also Admin? | Why: Determines who gets the MSG91 message and who gets the in-app bell; too many recipients causes alert fatigue | Options: Area Manager only / Area Manager + Admin / Area Manager + Admin + Superadmin
- Q: Should an expired-license optometrist be blocked from check-in (hard block) or only flagged (soft warning)? | Why: Hard block protects the business legally but could strand a store with no doctor; soft warning preserves operations but creates liability | Options: Hard block check-in / Soft warning + mandatory override note by Store Manager / Soft warning only
- Q: How far in advance should the weekly roster be planned (and locked)? | Why: Sets the "lock horizon" after which only AREA_MANAGER+ can edit; too short = chaos, too long = inflexible | Options: Lock 48 hours before shift start / Lock 24 hours before / No lock (always editable with escalating role requirement)
# Pune Incentive System — Full Build Plan

**Status:** Ready for implementation. All ambiguities resolved with the user. Spec is closed.
**Source:** Lift `PUNE INCENTIVE.xlsx` (10 sheets, 7 staff, 1 store manager, daily-driven payout) into IMS 2.0 as a 3-module integrated subsystem.
**Programme size:** 6.5 calendar weeks single-threaded, ~5 weeks parallelized.

This document is the complete build plan. Each module is specified to SYSTEM_INTENT-grade detail: schemas, API contracts, file paths, phase breakdown, tests, verification. **A CLI Claude session can pick up Phase 1 of Module (i) immediately using §M1-PHASE1-PROMPT below.** Subsequent phases reuse the same template — fill in the per-phase scope.

---

## Table of contents

- [Decisions log (resolved ambiguities)](#decisions-log-resolved-ambiguities)
- [Programme overview](#programme-overview)
- [Module (i) — Walkouts + Walkout-FU Log + Walkouts Dashboard](#module-i--walkouts--walkout-fu-log--walkouts-dashboard)
  - [§M1-PHASE1-PROMPT](#m1-phase1-prompt) ← copy this into a fresh `claude` session to start
- [Module (ii) — Daily Points + MTD Scoring Engine](#module-ii--daily-points--mtd-scoring-engine)
- [Module (iii) — Payout Calculation + Manager Bonus](#module-iii--payout-calculation--manager-bonus)
- [End-to-end verification](#end-to-end-verification)

---

## Decisions log (resolved ambiguities)

Every choice below is locked. Don't re-ask.

| # | Topic | Decision |
|---|---|---|
| 1 | Re-save same (date, staff) in Points Log | **Refuse second save.** Correction requires explicit chat-driven delete by SUPERADMIN/STORE_MANAGER, then re-POST. Both events audit-logged. |
| 2 | Walk-Ins source-of-truth | **POS auto + manual top-up.** Every POS order intake increments daily counter; staff can manually add browse-and-leave traffic. |
| 3 | Manager add-on bonus | **Stack on top of staff weightage.** SAMEER (or any supervisor) earns BOTH a sales-staff slice AND the manager bonus, each eligibility-gated separately. |
| 4 | Discount tier rounding | **Floor / truncate.** 11.99% hits the 11% bracket (1.4×). Reward staff for keeping discount low; never punish near-misses. |
| 5 | Conversion timing for FU recoveries | **Credit on conversion day, numerator-only.** Monday's score stays as-is; Wednesday gets +1 in numerator when a Monday walkout flips. Today's conversion can theoretically score >100% (capped at 20/20). |
| 6 | Multi-supervisor handling | **Per-supervisor custom %.** Each supervisor row in incentive_settings carries its own L1/L2/L3 bonus % triple. Default 25/30/35 if unset. |
| 7 | Visufit-90% rule | **Category gate.** If MTD Visufit-usage < 90% for the store, the Visufit category writes as 0 for everyone that month. Doesn't kill the team pool outright. |
| 8 | Audit Log scope | **Saves + edits/corrections + config changes.** Skip read events. Includes role/threshold/weightage edits. |
| 9 | Eligibility history on threshold change | **Snapshot.** Historical Points Log rows keep their write-time eligibility value. Threshold changes don't rewrite payroll history. |
| 10 | Eligibility-tier table in Settings | **Make it source-of-truth.** Refactor formulas to read from `incentive_settings.eligibility_bands`, not hardcoded thresholds. |
| 11 | Branch column on Walkouts Log | **Auto-fill from session active_store_id.** Read-only on append. Removes typos; matches IMS 2.0's multi-store auth model. |
| 12 | Cleanup batch | **All four approved.** Delete empty Sheet1; freeze _archive_may26 read-only; fix Entry F14/F15 cells; drop the 19,581-row cap. |
| A | Refuse-resave delete flow | **A1: Chat-command delete with role gate.** Staff says "delete points for RUPESH 2026-05-06"; only SUPERADMIN + STORE_MANAGER allowed; audit-logged with reason. |
| B | Conversion retro-credit math | **B1: Numerator only.** `score = MIN(20, MAX(0, (WalkInsToday − WalkoutsToday + RetroConversionsToday) / WalkInsToday × 20))`. The 20-point cap implicitly bounds. Surplus FU work is absorbed silently (not redirected to Kicker). |

---

## Programme overview

```
                  WALKOUT INTAKE                DAILY POINTS              PAYOUT CALC
                  (Module i)                    (Module ii)               (Module iii)
                  ┌─────────────┐              ┌─────────────┐           ┌─────────────┐
                  │   /walkouts │   conversion │ /points/    │   MTD     │  /payout/   │
   Sales staff →  │  intake form├──────feed───→│   daily     ├──────────→│   preview   │
                  │             │              │             │           │             │
                  │   FU panel  │              │  MTD calc   │           │  pool calc  │
                  │             │              │  visufit    │           │  per-staff  │
                  │  dashboard  │              │  gate       │           │  + manager  │
                  └──────┬──────┘              └──────┬──────┘           └──────┬──────┘
                         │                            │                          │
                         ▼                            ▼                          ▼
                   walkouts                     points_log              payout_snapshots
                   walk_in_counters             incentive_settings      (immutable when locked)

       Audit log writes (saves + edits + config) feed back into all three.
```

**Module dependency:** (ii) needs (i) Phase 5's `/walkouts/conversion-feed` and (clinical's `visufit-usage`). (iii) needs (ii)'s `/points/mtd`. (i) is foundational and depends only on existing IMS 2.0 (customers, users, tasks, audit, POS).

**Staffing-week summary:**

| Module | Phases | Backend | Frontend | Total | Calendar |
|---|---|---|---|---|---|
| (i) Walkouts | 5 | 10.5d | 11d | 21.5d | 3 weeks |
| (ii) Daily Points | 4 | 5d | 5d | 10d | 2 weeks |
| (iii) Payout | 3 | 5d | 4d | 9d | 1.5 weeks |
| **TOTAL** | **12** | **20.5d** | **20d** | **40.5d** | **6.5 weeks 1 dev / ~5 weeks 2 devs** |

---

## Module (i) — Walkouts + Walkout-FU Log + Walkouts Dashboard

### Goals
Lift the Walkouts Log + 2 FU rounds + Walkouts Dashboard from Excel into IMS 2.0. End state: Pune team logs walkouts in IMS, store managers see live MTD numbers, FU SLAs auto-escalate via tasks, and Module (ii) gets a clean conversion feed.

### Naming collision warning
The existing `backend/api/routers/follow_ups.py` handles **clinical** reminders (eye-test, frame-replacement). DO NOT reuse `/api/v1/follow-ups`. Walkout follow-ups are sub-records of a walkout — they live under `/api/v1/walkouts/{id}/followups`.

### Schema — `walkouts` collection

```js
{
  walkout_id: "WO-PNE-2026-A1B2C3",      // WO-{STORE3}-{YYYY}-{6HEX}
  store_id: "BV-PNE-01",                  // session.active_store_id, READ-ONLY on append
  date: ISODate("2026-05-07"),
  date_str: "2026-05-07",                 // for index queries

  // Customer (denormalized snapshot + FK)
  customer_id: "cust-9f352422",           // null if mobile not in customers (auto-create instead)
  customer_name: "Avinash Kumar Gupta",
  mobile: "9473457157",                   // 10-digit, validated
  age_group: "26-35",                     // enum
  gender: "MALE",                         // enum

  // Discovery
  product_interested: "FRAME",            // enum, must match catalog category
  has_prescription: "YES",                // YES / NO
  displayed_price_range: "5000-10000",    // enum
  required_price_range: "3000-5000",      // enum
  primary_walkout_reason: "BUDGET/PRICE", // enum
  secondary_walkout_reason: "BRAND",      // enum, nullable
  brand_interest: "Ray-Ban",              // free text
  competitor_mentioned: "Lenskart",       // free text, nullable
  purchase_planned_in: "1-7 DAYS",        // enum

  // Sales attribution
  sales_person_id: "user-akshay",
  sales_person_name: "AKSHAY",            // denormalized

  // Embedded follow-ups (Phase 3+)
  followups: [
    {
      round: 1,
      scheduled_date: ISODate("2026-05-08"),
      scheduled_time: "10:30",
      mode: "WHATSAPP",                   // CALL / WHATSAPP / SMS / EMAIL / IN-PERSON
      supervisor_id: "user-sameer",
      supervisor_name: "SAMEER",
      status: "PENDING",                  // PENDING / DONE / NOT REACHABLE / NOT REQUIRED
      notes: "",
      completed_at: null,
      completed_by: null,
      escalation_task_id: null            // set when SLA breach creates a task
    }
  ],

  // Outcome (Phase 3+)
  result: null,                            // DUE / NEGATIVE / CONVERTED, null until set
  result_set_at: null,
  result_set_by: null,
  converted_order_id: null,                // FK to orders if CONVERTED

  // Free-text
  action_remarks: "",

  // Soft-delete + metadata
  deleted_at: null,
  deleted_by: null,
  delete_reason: null,
  created_at: ISODate(),
  created_by: "user-akshay",
  updated_at: ISODate(),
  updated_by: "user-akshay"
}
```

### Schema — `walk_in_counters` collection (per-store-per-day)

```js
{
  _id: "BV-PNE-01_2026-05-07",
  store_id: "BV-PNE-01",
  date_str: "2026-05-07",
  pos_auto_count: 23,                  // POS order intake increments
  manual_topup: 4,                      // browse-and-leave additions
  manual_log: [
    { added_by: "user-akshay", added_at: ISODate(), delta: 1, reason: "browse only" }
  ],
  total: 27,
  per_staff: { "user-akshay": 8, "user-rupesh": 6, "user-sameer": 13 },
  updated_at: ISODate()
}
```

### Indexes

```js
db.walkouts.createIndex({ store_id: 1, date_str: -1 })
db.walkouts.createIndex({ store_id: 1, sales_person_id: 1, date_str: -1 })
db.walkouts.createIndex({ "followups.scheduled_date": 1, "followups.status": 1 })
db.walkouts.createIndex({ store_id: 1, primary_walkout_reason: 1, date_str: -1 })
db.walkouts.createIndex({ mobile: 1 })
db.walkouts.createIndex({ customer_id: 1 })
db.walk_in_counters.createIndex({ store_id: 1, date_str: -1 })
```

### Enum values (must match Excel exactly)

```python
class AgeGroup(str, Enum):
    UNDER_15 = "<15"
    G_15_25 = "15-25"
    G_26_35 = "26-35"
    G_36_45 = "36-45"
    G_46_55 = "46-55"
    G_56_65 = "56-65"
    OVER_65 = "65+"

class Gender(str, Enum):
    MALE = "MALE"; FEMALE = "FEMALE"; OTHER = "OTHER"

class ProductCategory(str, Enum):
    FRAME = "FRAME"; SUNGLASS = "SUNGLASS"; WATCH = "WATCH"; CLOCK = "CLOCK"
    LENS = "LENS"; CONTACT_LENS = "CONTACT LENS"; ACCESSORY = "ACCESSORY"; OTHER = "OTHER"

class YesNo(str, Enum):
    YES = "YES"; NO = "NO"

class PriceRange(str, Enum):
    UNDER_1K = "<1000"
    R_1K_2K = "1000-2000"; R_2K_3K = "2000-3000"; R_3K_5K = "3000-5000"
    R_5K_10K = "5000-10000"; R_10K_20K = "10000-20000"
    R_20K_50K = "20000-50000"; OVER_50K = "50000+"

class WalkoutReason(str, Enum):
    BUDGET_PRICE = "BUDGET/PRICE"; COLLECTION = "COLLECTION"
    COLOR = "COLOR"; BRAND = "BRAND"; ENQUIRY_ONLY = "ENQUIRY ONLY"
    STAFF_BEHAVIOUR = "STAFF BEHAVIOUR"; NOT_AVAILABLE = "NOT AVAILABLE"
    STYLE_DESIGN = "STYLE/DESIGN"; FIT_SIZE = "FIT/SIZE"; OTHER = "OTHER"

class PurchasePlan(str, Enum):
    NEXT_DAY = "NEXT DAY"; P_1_7 = "1-7 DAYS"; P_8_15 = "8-15 DAYS"
    P_16_30 = "16-30 DAYS"; AFTER_MONTH = "AFTER A MONTH"; UNDECIDED = "UNDECIDED"

class FUMode(str, Enum):
    CALL = "CALL"; WHATSAPP = "WHATSAPP"; SMS = "SMS"; EMAIL = "EMAIL"; IN_PERSON = "IN-PERSON"

class FUStatus(str, Enum):
    PENDING = "PENDING"; DONE = "DONE"
    NOT_REACHABLE = "NOT REACHABLE"; NOT_REQUIRED = "NOT REQUIRED"

class WalkoutResult(str, Enum):
    DUE = "DUE"; NEGATIVE = "NEGATIVE"; CONVERTED = "CONVERTED"
```

### API surface (all endpoints)

```
# CRUD
POST   /api/v1/walkouts                    # P1 — create one walkout
GET    /api/v1/walkouts                    # P2 — list with filters (date_from, date_to, sales_person, reason, result, store_id; default limit 50)
GET    /api/v1/walkouts/{walkout_id}       # P1 — get one
PATCH  /api/v1/walkouts/{walkout_id}       # P2 — edit (limited fields, RBAC enforced)
DELETE /api/v1/walkouts/{walkout_id}       # P2 — soft-delete (SUPERADMIN / STORE_MANAGER only)

# Follow-ups
POST   /api/v1/walkouts/{walkout_id}/followups            # P3 — append round (1 or 2; rejects 3+)
PATCH  /api/v1/walkouts/{walkout_id}/followups/{round}    # P3 — update FU status / notes / completed_at
GET    /api/v1/walkouts/followups/due-today               # P3 — list FUs due today, scoped by RBAC
POST   /api/v1/walkouts/followups/escalate-overdue        # P3 — cron-callable; pending FU past scheduled_date → tasks

# Outcome
PATCH  /api/v1/walkouts/{walkout_id}/result               # P3 — set DUE/NEGATIVE/CONVERTED + optional converted_order_id

# Walk-in counters (P4)
POST   /api/v1/walkouts/walkins/auto-increment            # internal, called by POS on order intake
POST   /api/v1/walkouts/walkins/manual-topup              # UI; { store_id, sales_person_id?, delta, reason }
GET    /api/v1/walkouts/walkins/today                     # { store_id } → today's counts (total + per-staff)
GET    /api/v1/walkouts/walkins/mtd                       # { store_id, year, month } → MTD per-staff

# Dashboard aggregations (P4)
GET    /api/v1/walkouts/dashboard/per-staff               # 7-staff cards: walkouts MTD/today/walk-ins/sales/conversion%/FU-due
GET    /api/v1/walkouts/dashboard/top-reasons             # sorted desc, limit 10
GET    /api/v1/walkouts/dashboard/result-breakdown        # { DUE, NEGATIVE, CONVERTED, no_result }
GET    /api/v1/walkouts/dashboard/fu-status               # { fu1: {DONE, PENDING, ...}, fu2: {...} }

# Module (ii) consumer feed (P5)
GET    /api/v1/walkouts/conversion-feed                   # for given (store, date), per-staff conversion math
```

### Conversion-feed shape (the contract Module (ii) consumes)

```json
GET /api/v1/walkouts/conversion-feed?store_id=BV-PNE-01&date=2026-05-07
→ [
  {
    "sales_person_id": "user-akshay",
    "name": "AKSHAY",
    "walk_ins_today": 8,
    "walkouts_today": 3,
    "retro_conversions_today": 1,    // walkouts from prior days flipped to CONVERTED today
    "conversion_score": 15           // MIN(20, MAX(0, (8-3+1)/8 × 20)) = 15
  },
  ...
]
```

### File paths — backend

```
backend/
├── api/routers/walkouts.py                          NEW (~600 lines)
├── api/main.py                                       +1 line: include_router
├── api/dependencies.py                               +imports for new repos
├── database/repositories/
│   ├── walkout_repository.py                        NEW (~300 lines)
│   └── walkin_counter_repository.py                 NEW (~150 lines)
├── database/migrations.py                            +1 migration for indexes
└── tests/test_walkouts.py                            NEW (~30 tests across phases)
```

### File paths — frontend

```
frontend/src/
├── pages/walkouts/
│   ├── WalkoutsPage.tsx                              NEW — list + filters (P2)
│   ├── WalkoutIntakeModal.tsx                        NEW — 30-field form (P1)
│   ├── WalkoutDetailPage.tsx                         NEW — edit + FU + result (P2/P3)
│   ├── FollowUpPanel.tsx                             NEW (P3)
│   ├── FUDueTodayWidget.tsx                          NEW (P3)
│   └── WalkoutsDashboardPage.tsx                     NEW (P4)
├── services/api/walkouts.ts                          NEW (typed API client)
├── components/walkouts/
│   ├── EnumSelect.tsx                                NEW (shared dropdown)
│   └── WalkoutResultBadge.tsx                        NEW
├── types/index.ts                                    +Walkout, WalkoutFollowUp, WalkInCounter types
└── App.tsx                                           +/walkouts, /walkouts/:id, /walkouts-dashboard routes
                                                      +nav rail entry between Customers and Orders
```

### Phased plan

#### Phase 1 — Schema + intake (Week 1, 5 dev-days)
- Backend: `walkout_repository.py`, `walkouts.py` router with `POST /walkouts` + `GET /walkouts/{id}`, all enums + Pydantic models, indexes migration, audit-log integration.
- Frontend: `services/api/walkouts.ts`, `WalkoutIntakeModal.tsx`, minimal `WalkoutsPage.tsx` stub.
- Customer auto-create: when intake mobile not in `customers`, auto-create skeleton with `source: "walkout"`.
- 6 backend tests; 1 manual E2E.

#### Phase 2 — List + edit (Week 1.5, 4.5 dev-days)
- Backend: list with filters + pagination; PATCH with diff-audit; soft DELETE (SUPERADMIN/STORE_MANAGER).
- Frontend: WalkoutsPage list + filter sidebar; WalkoutDetailPage with edit form; RBAC-aware UI.
- 8 backend tests; RBAC tests.

#### Phase 3 — FU + result (Week 2, 4.5 dev-days)
- Backend: FU CRUD on embedded sub-docs; `escalate-overdue` cron creates tasks via `tasks_router.create_task` (priority P2 for FU1, P1 for FU2); result endpoint with `converted_order_id` validation.
- Frontend: FollowUpPanel; WalkoutResultBadge; "Convert to order" CTA opens POS pre-filled.
- 10 backend tests including escalation-creates-task.

#### Phase 4 — Dashboard + walk-in counter (Week 2.5, 4 dev-days)
- Backend: `walkin_counter_repository`; auto-increment hook in `orders.py` (single-line addition on order DRAFT→CONFIRMED, dedup'd by mobile+date); manual-topup endpoint; dashboard aggregations using MongoDB `$facet`.
- Frontend: WalkoutsDashboardPage with per-staff cards, top-reasons bar chart, result donut, FU-due-today table.
- 8 backend tests including walk-in-dedup.

#### Phase 5 — Conversion feed + polish + backfill (Week 3, 3.5 dev-days)
- Backend: `GET /walkouts/conversion-feed` — the Module (ii) contract.
- Backfill script `scripts/migrate_pune_walkouts.py` — runbook only, NOT in CI; idempotent on mobile+date hash.
- Frontend: bug-fix sweep, perf audit (cache dashboard 60s), empty-state polish, mobile responsive on intake modal.
- Pune cutover: switch from Excel to IMS for new walkouts.

### Tests (must-pass per phase)

```
P1: test_walkout_create_full_30_fields
    test_mobile_validation_rejects_9_and_11_digits
    test_invalid_enum_value_returns_400
    test_walkout_id_format_matches_pattern
    test_audit_log_walkout_create_row_written
    test_customer_auto_created_when_mobile_new

P2: test_list_by_store_filters
    test_patch_diff_audited
    test_rbac_sales_staff_cannot_edit_others
    test_soft_delete_excludes_from_list
    test_pagination_returns_default_50

P3: test_followup_append_round_1_and_2
    test_followup_round_3_rejected
    test_overdue_fu_creates_escalation_task
    test_set_result_converted_validates_order_id
    test_set_result_audit_logged

P4: test_walkin_increment_dedups_same_mobile_day
    test_manual_topup_audit_logged
    test_dashboard_per_staff_aggregation_correct
    test_dashboard_top_reasons_sorted_desc

P5: test_conversion_feed_includes_retro_conversions
    test_conversion_feed_score_capped_at_20
    test_backfill_idempotent_on_mobile_date
```

### Cross-module integration

| Module | Hook | Direction |
|---|---|---|
| **POS** | `POST /api/v1/orders` | walkouts ← POS (auto-increment walk-in counter) |
| **Customers** | walkout intake | walkouts → customers (auto-create skeleton) |
| **Tasks** | FU SLA breach | walkouts → tasks (escalation) |
| **HR / Users** | sales_person dropdown | walkouts ← users (filter by store + role) |
| **Audit Log** | every write | walkouts → audit |
| **Module (ii)** | conversion feed | walkouts → ii (P5 contract) |

### Phase 1 verification

```bash
cd backend && python -m pytest tests/test_walkouts.py -v   # 6 tests green

# Login + create walkout via curl
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin123"}' | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

curl -X POST http://localhost:8000/api/v1/walkouts \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"customer_name":"Test","mobile":"9876543210","age_group":"26-35",
       "gender":"MALE","product_interested":"FRAME","has_prescription":"YES",
       "displayed_price_range":"5000-10000","required_price_range":"3000-5000",
       "primary_walkout_reason":"BUDGET/PRICE","purchase_planned_in":"1-7 DAYS",
       "brand_interest":"Ray-Ban","sales_person_id":"<real user_id>",
       "action_remarks":""}'
# Expect: 201, walkout_id of shape WO-XXX-2026-XXXXXX

# Frontend smoke
# Visit http://localhost:3000/walkouts → click "+ Log Walkout" → fill → submit → 201 in DevTools
```

---

### M1-PHASE1-PROMPT

> **Copy everything between the `===PROMPT START===` and `===PROMPT END===` markers below into a fresh `claude` CLI session in the IMS 2.0 repo root. The CLI Claude has no memory of this conversation — the prompt is self-contained.**

````
===PROMPT START===

# IMS 2.0 — Walkouts Module (i) Phase 1: Intake + Schema

You're implementing Phase 1 of the Pune-incentive Walkouts module. Full spec
lives in `docs/PUNE_INCENTIVE_BUILD_PLAN.md` in this repo. Read the
"Module (i) — Walkouts" section there for context, then return here for
the Phase 1-specific work.

## Repo
- Local: `C:/Users/avina/IMS 2.0 CLAUDE COWORK/ims-2.0-railway`
- Branch: create `claude/walkouts-phase-1` off current `main`
- Stack: FastAPI 3.12 (Railway), React 19+TS+Vite (Vercel), MongoDB
- Auth: JWT — admin/admin123, token stored as `ims_token` in localStorage
- Dev start: `python start_backend.py` (port 8000) + `node start_frontend.mjs` (port 3000)
- Vite proxy: `/api` → `http://localhost:8000/api/v1`

## Phase 1 Goal
A logged-in sales staff at any store opens a 30-field intake form and POSTs
a walkout. The walkout persists with full schema, hits the audit log, and
returns a `walkout_id` of shape `WO-{STORE3}-{YYYY}-{6HEX}`.

End-of-phase verification: `POST /api/v1/walkouts` with the example payload
in `docs/PUNE_INCENTIVE_BUILD_PLAN.md` returns 201, the row is queryable
via `GET /api/v1/walkouts/{id}`, and the audit log gains a `walkout.create`
entry.

NOTHING ELSE in Phase 1 — no list view, no follow-ups, no dashboard.
Phases 2-5 are separate sessions.

## Files to create

### Backend
1. `backend/database/repositories/walkout_repository.py`
2. `backend/api/routers/walkouts.py`
3. `backend/database/migrations.py` (add migration entry for walkouts indexes)
4. `backend/api/main.py` (add 1 line: register the router)
5. `backend/api/dependencies.py` (import + inject `WalkoutRepository`)
6. `backend/tests/test_walkouts.py` (6 tests — see verification section)

### Frontend
1. `frontend/src/services/api/walkouts.ts`
2. `frontend/src/pages/walkouts/WalkoutIntakeModal.tsx`
3. `frontend/src/pages/walkouts/WalkoutsPage.tsx` (minimal stub — just renders modal trigger)
4. `frontend/src/types/index.ts` (add `Walkout`, `WalkoutFollowUp`, enum types)
5. `frontend/src/App.tsx` (add `/walkouts` route + nav rail entry between Customers and Orders)

## Pydantic request body

```python
class CreateWalkoutRequest(BaseModel):
    customer_name: str = Field(..., min_length=1)
    mobile: str = Field(..., regex=r"^\d{10}$")
    age_group: AgeGroup
    gender: Gender
    product_interested: ProductCategory
    has_prescription: YesNo
    displayed_price_range: PriceRange
    required_price_range: PriceRange
    primary_walkout_reason: WalkoutReason
    secondary_walkout_reason: Optional[WalkoutReason] = None
    brand_interest: str = ""
    competitor_mentioned: str = ""
    purchase_planned_in: PurchasePlan
    sales_person_id: str  # validate exists in users + same store
    action_remarks: str = ""
    date: Optional[date] = None  # defaults to today
```

Server fills: `walkout_id`, `store_id` from session, `date_str`,
`sales_person_name` from user lookup, `customer_id` from mobile lookup
(see "Customer auto-create" section), all metadata.

## Customer auto-create (Phase 1 only — no toggle in UI)

When `mobile` is NOT in `customers` collection:
1. Auto-create a skeleton customer doc:
   `{ customer_id: generated, name: <walkout name>, mobile: <walkout mobile>,
     primary_store_id: <session store>, source: "walkout",
     created_via: "walkout_intake", created_at: now, ... }`
2. Set walkout's `customer_id` to the new id.
3. Both rows hit audit log: `walkout.create` + `customer.create` with
   `via_walkout: true` flag.

When mobile IS in `customers`, just link `customer_id`. Don't update the
existing customer record.

## Indexes to add (migrations.py)

```python
db.walkouts.create_index([("store_id", 1), ("date_str", -1)])
db.walkouts.create_index([("store_id", 1), ("sales_person_id", 1), ("date_str", -1)])
db.walkouts.create_index([("mobile", 1)])
db.walkouts.create_index([("customer_id", 1)])
# followups + result indexes will be added in Phases 3 & 4 — don't add yet
```

## Audit log row (use existing audit_repository.py)

On every successful `POST /walkouts`:
```python
audit_repo.create({
    "log_id": uuid,
    "timestamp": now,
    "user_id": current_user.user_id,
    "action": "walkout.create",
    "entity_type": "walkout",
    "entity_id": walkout_id,
    "store_id": store_id,
    "severity": "info",
    "detail": { "mobile": mobile, "sales_person": sales_person_name }
})
```

If `audit_repository.py` doesn't have a generic `create()`, add one mirroring
`customer_repository.py`'s pattern. DO NOT redesign audit schema.

## Repository pattern (mirror customer_repository.py exactly)

```python
class WalkoutRepository(BaseRepository):
    @property
    def entity_name(self) -> str: return "Walkout"
    @property
    def id_field(self) -> str: return "walkout_id"

    def _generate_walkout_id(self, store_id: str) -> str:
        # "BV-PNE-01" → "PNE"; fallback "XXX"
        parts = store_id.split("-")
        code = parts[1][:3].upper() if len(parts) >= 2 else "XXX"
        year = datetime.utcnow().year
        suffix = uuid.uuid4().hex[:6].upper()
        return f"WO-{code}-{year}-{suffix}"

    def create_walkout(self, data: Dict) -> Dict: ...
    def find_by_id(self, walkout_id: str) -> Optional[Dict]: ...
    def find_by_mobile_recent(self, mobile: str, days: int = 30) -> Optional[Dict]:
        # OK to stub return None for P1; needed in P2
        return None
```

## Router pattern (mirror follow_ups.py)

- `from .auth import get_current_user`
- `from ..dependencies import get_db as _dep_get_db`
- `current_user.active_store_id` for store_id
- Pydantic models defined inline at top of file (existing convention)

RBAC for POST:
```python
ALLOWED_ROLES = {"SUPERADMIN", "STORE_MANAGER", "SALES_STAFF", "CASHIER", "ADMIN"}
if not (set(current_user.roles) & ALLOWED_ROLES):
    raise HTTPException(403, "Not allowed to log walkouts")
```

## Frontend WalkoutIntakeModal — 4 sections

1. **Customer** (4 fields): name, mobile, age, gender
2. **Discovery** (8 fields): product, has_rx, displayed_price, required_price, reasons, brand, competitor, purchase_plan
3. **Sales Attribution** (1 field): sales_person dropdown — `GET /api/v1/users?store_id=<active>&roles=SALES_STAFF,STORE_MANAGER`
4. **Notes** (1 field): action_remarks textarea

All enum fields: `<select>` with options from a shared `ENUM_VALUES` constant in `frontend/src/types/index.ts`.

Validation:
- Mobile: regex `^\d{10}$`, error "Mobile must be 10 digits"
- Required fields enforced before submit (button disabled)
- Submit: POST via `services/api/walkouts.ts.createWalkout()`
- Success: toast "Walkout logged · {walkout_id}", close modal

Modal trigger: a button labeled "+ Log Walkout" on `WalkoutsPage.tsx`.

## Tests (backend) — `backend/tests/test_walkouts.py`

Use existing `conftest.py` fixtures (study `test_core_endpoints.py` for the pattern):

```
test_create_walkout_minimal_required_fields_returns_201
test_create_walkout_full_payload_persists_all_fields
test_mobile_validation_rejects_9_and_11_digits
test_invalid_enum_value_returns_400
test_walkout_id_format_matches_WO_STORE3_YYYY_6HEX_pattern
test_audit_log_row_written_with_walkout_create_action
```

## Out of scope (do NOT build in Phase 1)

- List view, filtering, pagination
- Edit / delete / soft-delete
- Follow-up records
- Result setting
- Dashboard, aggregations
- Walk-in counter / POS hook
- Conversion-feed endpoint
- Excel backfill script

## Verification commands

```bash
# Backend tests
cd backend && python -m pytest tests/test_walkouts.py -v

# Manual API check (server running on :8000)
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}' | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

curl -X POST http://localhost:8000/api/v1/walkouts \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"customer_name":"Test","mobile":"9876543210","age_group":"26-35",
       "gender":"MALE","product_interested":"FRAME","has_prescription":"YES",
       "displayed_price_range":"5000-10000","required_price_range":"3000-5000",
       "primary_walkout_reason":"BUDGET/PRICE","purchase_planned_in":"1-7 DAYS",
       "brand_interest":"Ray-Ban","sales_person_id":"<a real user_id>",
       "action_remarks":""}'
# Expect: 201, walkout_id of form WO-{STORE3}-2026-{6HEX}

# Verify DB row + audit row
mongosh ims_2_0 --eval 'db.walkouts.findOne({}, {_id:0})'
mongosh ims_2_0 --eval 'db.audit_log.find({action:"walkout.create"}).sort({timestamp:-1}).limit(1).pretty()'

# Frontend smoke
# Visit http://localhost:3000/walkouts → click "+ Log Walkout" → fill all 14 fields → submit
# DevTools network: POST /api/v1/walkouts → 201
```

## When done

```bash
git checkout -b claude/walkouts-phase-1
git add -A
git commit -m "Walkouts Phase 1: intake + schema (Module i)

- New collection: walkouts
- Endpoints: POST /api/v1/walkouts, GET /api/v1/walkouts/{id}
- Auto-create skeleton customer when mobile is new
- 4 indexes added; all writes audit-logged
- Frontend: WalkoutIntakeModal (30 fields, 4 sections) + nav rail entry
- Tests: 6 backend tests, all green"

git push -u origin claude/walkouts-phase-1
gh pr create --title "Walkouts Phase 1: intake + schema" --body "Phase 1 of Module (i). See docs/PUNE_INCENTIVE_BUILD_PLAN.md for full programme context."
```

Reply with:
1. PR URL
2. Confirmation all 6 tests pass
3. The walkout_id you created end-to-end
4. Any spec ambiguity hit (parent session resolves before Phase 2 kickoff)

===PROMPT END===
````

---

## Module (ii) — Daily Points + MTD Scoring Engine

### Goals
9-category daily score per staff with audit-replayable history; MTD aggregation that drives eligibility tiers; conversion auto-fill from Module (i); visufit-90% gate. Output contract: `GET /points/mtd` for Module (iii).

### Schemas

#### `points_log` collection (append-only with controlled delete)

```js
{
  log_id: "PL-PNE-2026-05-07-AKSHAY-A1B2C3",
  store_id: "BV-PNE-01",
  date: ISODate("2026-05-07"),
  date_str: "2026-05-07",
  staff_id: "user-akshay",
  staff_name: "AKSHAY",

  // 9 categories
  attendance: 8,           // 0..10
  conversion: 16,          // 0..20 (auto from walkouts feed; manual override allowed for past dates)
  task: 9,                 // 0..10
  visufit: 7,              // 0..10 (0 if visufit_gate_active and usage<90%)
  punctuality: 10,
  behaviour: 9,
  kicker_1: 0,
  kicker_2: 0,
  reviews: 8,
  total: 67,

  // Eligibility (snapshot at write-time per decision §9)
  eligibility: 0.0,        // 0 / 0.6 / 0.8 / 1.0
  eligibility_thresholds_used: {
    bands: [
      { min: 0, max: 70, value: 0.0 },
      { min: 70, max: 80, value: 0.6 },
      { min: 80, max: 95, value: 0.8 },
      { min: 95, max: 1000, value: 1.0 }
    ]
  },

  // Visufit gate metadata
  visufit_gate_applied: false,
  visufit_usage_pct_mtd: 0.94,

  created_at: ISODate(),
  created_by: "user-sameer",
  updated_at: ISODate(),
  deleted_at: null,
  deleted_by: null,
  delete_reason: null
}
```

#### `incentive_settings` collection (one doc per store; shared with Module iii)

```js
{
  store_id: "BV-PNE-01",
  staff_weightages: { "user-akshay": 0.24, "user-rupesh": 0.22, ... },  // sum = 1.0
  eligibility_bands: [
    { min: 0, max: 70, value: 0.0 },
    { min: 70, max: 80, value: 0.6 },
    { min: 80, max: 95, value: 0.8 },
    { min: 95, max: 1000, value: 1.0 }
  ],
  growth_targets: { L1: 0.20, L2: 0.25, L3: 0.30 },              // module iii
  base_rates: { L1: 0.01, L2: 0.0125, L3: 0.015 },               // module iii
  discount_kill_threshold: 0.15,                                  // module iii
  discount_multipliers: [
    { max_pct: 0.10, multiplier: 1.5 },
    { max_pct: 0.11, multiplier: 1.4 },
    { max_pct: 0.12, multiplier: 1.3 },
    { max_pct: 0.13, multiplier: 1.2 },
    { max_pct: 0.14, multiplier: 1.1 },
    { max_pct: 0.15, multiplier: 1.0 }
  ],
  visufit_gate_threshold: 0.90,
  visufit_gate_enabled: true,
  supervisor_bonuses: [
    { user_id: "user-sameer", role: "STORE_MANAGER",
      bonus_pct: { L1: 0.25, L2: 0.30, L3: 0.35 } }
  ],
  updated_at: ISODate(),
  updated_by: "user-superadmin"
}
```

### Indexes

```js
db.points_log.createIndex(
  { store_id: 1, date_str: -1, staff_id: 1 },
  { unique: true, partialFilterExpression: { deleted_at: null } }
)
db.points_log.createIndex({ store_id: 1, staff_id: 1, date_str: -1 })
db.points_log.createIndex({ store_id: 1, deleted_at: 1 })
db.incentive_settings.createIndex({ store_id: 1 }, { unique: true })
```

The unique partial index on `(store, date_str, staff)` is **the DB-level enforcement of "refuse second save"**.

### API surface

```
POST   /api/v1/incentive/points/daily              Create one row; 409 if (date,staff) exists
POST   /api/v1/incentive/points/daily/bulk         End-of-day batch (typical use); per-row success/failure
GET    /api/v1/incentive/points/daily?date=...     Today's rows for store
DELETE /api/v1/incentive/points/daily/{log_id}     SUPERADMIN/STORE_MGR; soft-delete with reason
GET    /api/v1/incentive/points/mtd                Per-staff MTD aggregation (Module iii contract)
GET    /api/v1/incentive/points/leaderboard        Sorted by avg.total desc, tie-broken by days_logged
GET    /api/v1/incentive/points/staff/{id}/history All rows for one staff in date range
GET    /api/v1/incentive/settings/eligibility      Current bands
PATCH  /api/v1/incentive/settings/eligibility      SUPERADMIN; audit-logged
```

### POST shape

```python
class DailyScores(BaseModel):
    attendance: int = Field(..., ge=0, le=10)
    conversion: Optional[int] = Field(None, ge=0, le=20)  # null → auto-fill if today
    task: int = Field(..., ge=0, le=10)
    visufit: int = Field(..., ge=0, le=10)
    punctuality: int = Field(..., ge=0, le=10)
    behaviour: int = Field(..., ge=0, le=10)
    kicker_1: int = Field(..., ge=0, le=10)
    kicker_2: int = Field(..., ge=0, le=10)
    reviews: int = Field(..., ge=0, le=10)

class CreateDailyPointsRequest(BaseModel):
    date: date
    staff_id: str
    scores: DailyScores
```

### Server flow on POST

1. RBAC: STORE_MANAGER+ can post for any staff in store; SALES_STAFF only for self.
2. Resolve `conversion` if null: fetch `/walkouts/conversion-feed?store_id=...&date=...`, find this staff's row, take `conversion_score`.
3. If `visufit_gate_enabled` and MTD-Visufit-usage < `visufit_gate_threshold`:
   - Override `visufit` → 0
   - Set `visufit_gate_applied = true`
4. Compute `total` = sum of 9 categories.
5. Compute `eligibility`: walk `eligibility_bands`, pick band where `min ≤ total < max`, take `value`.
6. Snapshot `eligibility_thresholds_used` from current settings.
7. Insert with unique-index protection. On `DuplicateKeyError`, return 409 `{error: "Already logged. Delete first to re-save."}`.

### File paths

#### Backend
```
backend/
├── api/routers/points.py                                NEW (~500 lines)
├── api/services/points_calculator.py                    NEW (eligibility + visufit gate, pure functions)
├── database/repositories/
│   ├── points_log_repository.py                         NEW
│   └── incentive_settings_repository.py                 NEW (shared with iii)
├── database/migrations.py                               +1 migration
├── api/main.py                                          +include_router under /api/v1/incentive/points
└── tests/test_points.py                                 NEW (~25 tests)
```

> Note: `backend/api/routers/incentives.py` (615 lines) exists. Inspect before P1: if stub/skeleton, replace; if different domain, mount the new router under `/api/v1/incentive/points` (with hyphen-or-no-hyphen matching whatever main.py uses) and leave the existing one alone. The new router belongs at `points.py` to disambiguate.

#### Frontend
```
frontend/src/
├── pages/incentive/
│   ├── DailyScorecardPage.tsx                           NEW (~600 lines, mirrors Excel "Daily Points Entry")
│   ├── MTDLeaderboardPage.tsx                           NEW (~400 lines)
│   ├── PointsHistoryPage.tsx                            NEW (~300 lines)
│   └── components/
│       ├── ScoreCellInput.tsx                           NEW
│       ├── EligibilityChip.tsx                          NEW
│       └── ConversionAutoBadge.tsx                      NEW
├── services/api/incentive.ts                            NEW
└── App.tsx                                              +/incentive/* routes; nav rail entry "Incentive" between HR and Reports
```

### Phased plan — 2 weeks

#### Phase 1 — Schema + single POST + GET (3 days)
Backend: `points_log_repository`, `incentive_settings_repository` (read-only stub), `points.py` router with `POST /points/daily` + `GET` + 409-on-duplicate.
Frontend: `DailyScorecardPage.tsx` minimal — 1 staff row at a time.
Tests: 8 (uniqueness, validation, conversion auto-fill mock, RBAC).

#### Phase 2 — Bulk POST + Delete + Conversion auto-fill wiring (3 days)
Backend: bulk endpoint, soft-delete with audit, real conversion auto-fill calling Module (i).
Frontend: 7-staff-at-once table; conversion auto-shows for today; manual override for past dates.

#### Phase 3 — MTD aggregation + Leaderboard + Visufit gate (3 days)
Backend: `points_calculator.py` pure functions; `GET /points/mtd`, `GET /leaderboard`, `GET /staff/{id}/history`. Settings-driven thresholds.
Frontend: MTDLeaderboardPage with per-category averages, eligibility chip, MTD trend chart.
Tests: 12 more (visufit gate edge cases, leaderboard tie-breaks).

#### Phase 4 — Settings + iii feed contract + polish (1 day)
Backend: `PATCH /incentive/settings/eligibility` SUPERADMIN-only.
Frontend: Settings section "Eligibility tiers" + "Visufit gate" toggles.

### Cross-module integration

| Module | Hook | Direction |
|---|---|---|
| **Walkouts (i)** | `GET /walkouts/conversion-feed` | (ii) reads on POST + GET MTD |
| **Clinical** | Visufit-usage % | (ii) reads (needs `GET /clinical/visufit-usage?store=&month=` — 1 day to add) |
| **HR / Users** | staff list, weightages | (ii) reads users + writes weightages |
| **Audit Log** | every write | (ii) writes |
| **Module (iii)** | `GET /points/mtd` | (iii) reads |

### Verification

- [ ] All 4 phases merged
- [ ] 7 staff × 5 days of test data POSTed; MTD avg matches manual calc within 0.01
- [ ] Re-POST same (date, staff) → 409
- [ ] DELETE then re-POST → 201
- [ ] Visufit gate: simulate usage < 90% → category writes 0; ≥ 90% → preserved
- [ ] Leaderboard sorted desc by total, tie-broken by days_logged
- [ ] Threshold change from 70→65 doesn't rewrite history (snapshot semantics)
- [ ] Conversion auto-fill: place a walkout, then POST today's points without conversion → conversion populated correctly

---

## Module (iii) — Payout Calculation + Manager Bonus

### Goals
Pure read-model over Modules (i) and (ii) + finance + clinical. Computes pool sizing, individual payouts, manager bonuses, and produces an immutable `payout_snapshots` doc on month-close.

### Schema — `payout_snapshots`

```js
{
  snapshot_id: "PAY-PNE-2026-05",
  store_id: "BV-PNE-01",
  year: 2026,
  month: 5,

  // Inputs captured at snapshot time
  inputs: {
    last_year_sale: 1838000,
    this_year_sale: 2600000,
    avg_discount_pct: 0.10,
    visufit_usage_pct: 0.94
  },

  // Targets + achievement
  targets: {
    L1: { growth: 0.20, target: 2210000, achieved: true },
    L2: { growth: 0.25, target: 2300000, achieved: true },
    L3: { growth: 0.30, target: 2390000, achieved: true }
  },
  best_level_achieved: "L3",

  // Pool sizing
  discount_kill_active: false,
  multiplier: 1.5,
  multiplier_tier: "≤10%",
  pools: { L1: 0, L2: 0, L3: 58500 },     // best-level-only
  total_team_pool: 58500,

  // Per-staff payouts
  staff_payouts: [
    { user_id: "user-akshay", name: "AKSHAY", weightage: 0.24,
      mtd_avg_total: 87, eligibility: 0.8,
      payout_by_level: { L1: 0, L2: 0, L3: 11232 }, total_payout: 11232 }
    /* ...7 rows */
  ],

  // Manager bonuses (per-supervisor stacking — decision §3)
  manager_bonuses: [
    { user_id: "user-sameer", role: "STORE_MANAGER", eligibility: 0.8,
      bonus_pct: { L1: 0.25, L2: 0.30, L3: 0.35 },
      bonus_by_level: { L1: 0, L2: 0, L3: 16380 }, total_bonus: 16380 }
  ],

  // Grand total
  grand_total: { staff: 27950, manager: 16380, all: 44330 },

  // Lifecycle
  status: "DRAFT" | "LOCKED" | "PAID",
  locked_at: null, locked_by: null,
  paid_at: null, paid_by: null,

  created_at: ISODate(),
  created_by: "user-superadmin"
}
```

### Indexes

```js
db.payout_snapshots.createIndex(
  { store_id: 1, year: 1, month: 1 },
  { unique: true, partialFilterExpression: { status: "LOCKED" } }
)
// Multiple DRAFTs allowed; only one LOCKED per (store, month).
```

### Calculator — pseudocode (`backend/api/services/payout_calculator.py`)

```python
def compute_targets(last_year_sale, growth_pcts):
    return { lvl: round_up(last_year_sale * (1 + g), -4) for lvl, g in growth_pcts.items() }

def compute_multiplier(avg_discount_pct, multipliers, kill_threshold):
    if avg_discount_pct > kill_threshold:
        return 0  # discount kill switch
    floored_pct = floor(avg_discount_pct * 100) / 100
    for tier in sorted(multipliers, key=lambda t: t["max_pct"]):
        if floored_pct <= tier["max_pct"]:
            return tier["multiplier"]
    return 0

def compute_best_level(this_year_sale, targets):
    if this_year_sale >= targets["L3"]: return "L3"
    if this_year_sale >= targets["L2"]: return "L2"
    if this_year_sale >= targets["L1"]: return "L1"
    return None

def compute_pools(this_year_sale, targets, base_rates, multiplier, best_level):
    pools = { "L1": 0, "L2": 0, "L3": 0 }
    if best_level is None or multiplier == 0:
        return pools
    pools[best_level] = max(this_year_sale, targets[best_level]) * base_rates[best_level] * multiplier
    return pools

def compute_individual_payouts(pools, staff_weightages, mtd_data):
    out = []
    for user_id, weightage in staff_weightages.items():
        eligibility = mtd_data[user_id]["eligibility"]
        per_level = { lvl: pool * weightage * eligibility for lvl, pool in pools.items() }
        out.append({
            "user_id": user_id,
            "weightage": weightage,
            "eligibility": eligibility,
            "payout_by_level": per_level,
            "total_payout": sum(per_level.values())
        })
    return out

def compute_manager_bonuses(pools, supervisor_bonuses, mtd_data):
    out = []
    for sup in supervisor_bonuses:
        eligibility = mtd_data[sup["user_id"]]["eligibility"]
        bonus = { lvl: pools[lvl] * sup["bonus_pct"][lvl] * eligibility for lvl in pools }
        out.append({
            "user_id": sup["user_id"], "role": sup["role"],
            "eligibility": eligibility,
            "bonus_pct": sup["bonus_pct"],
            "bonus_by_level": bonus,
            "total_bonus": sum(bonus.values())
        })
    return out
```

### API surface

```
GET    /api/v1/payout/preview?store_id=&year=&month=         Live computation (no persist)
POST   /api/v1/payout/lock                                    SUPERADMIN; persist as LOCKED snapshot; 409 if exists
GET    /api/v1/payout/snapshots?store_id=&year=               All snapshots for store-year
GET    /api/v1/payout/snapshot/{id}                           One snapshot
PATCH  /api/v1/payout/snapshot/{id}/mark-paid                 SUPERADMIN; PAID; audit
GET    /api/v1/payout/export/{id}.pdf                         Excel-Dashboard-style PDF
GET    /api/v1/payout/export/{id}.csv                         Per-staff CSV
PATCH  /api/v1/incentive/settings/payout                      SUPERADMIN; growth/rates/multipliers/supervisors
POST   /api/v1/incentive/settings/inputs/last-year-sale       Per-(store, year, month) manual input
```

### File paths

#### Backend
```
backend/
├── api/routers/payout.py                                NEW (~600 lines)
├── api/services/
│   ├── payout_calculator.py                             NEW (~400 lines, pure functions)
│   └── payout_pdf_generator.py                          NEW (reportlab or weasyprint)
├── database/repositories/payout_snapshot_repository.py  NEW
├── api/main.py                                          +include_router
└── tests/test_payout.py                                 NEW (~30 tests)
```

#### Frontend
```
frontend/src/pages/incentive/
├── PayoutDashboardPage.tsx                              NEW (~700 lines; mirrors Excel Dashboard)
├── PayoutSnapshotsPage.tsx                              NEW (~250 lines)
└── components/
    ├── PoolSizingCard.tsx                               NEW
    ├── EligibilityMatrix.tsx                            NEW
    └── ManagerBonusCard.tsx                             NEW
```

### Phased plan — 1.5 weeks

#### Phase 1 — Calculator + preview (3 days)
- `payout_calculator.py` — all pure functions above. 25 unit tests.
- `GET /payout/preview` reads (i), (ii), finance, settings; returns live computation.

#### Phase 2 — Snapshot + lock + frontend dashboard (4 days)
- Snapshot repo + lock endpoint + audit.
- PayoutDashboardPage.tsx — full Excel-mirror UI.
- Mark-paid endpoint.

#### Phase 3 — Export + Settings + polish (2 days)
- PDF generator.
- CSV export.
- SUPERADMIN settings page for growth targets, multipliers, manager bonuses, last-year-sale input.
- Cross-check: run on Pune May-26 data, compare to Excel cell-by-cell.

### Critical test fixture — Pune May-26 cross-check

Create `backend/tests/fixtures/pune_may26_cross_check.json`:

```json
{
  "inputs": {
    "last_year_sale": 1838000,
    "this_year_sale": 2600000,
    "avg_discount_pct": 0.10,
    "visufit_usage_pct": 0.94
  },
  "expected": {
    "targets": { "L1": 2210000, "L2": 2300000, "L3": 2390000 },
    "best_level": "L3",
    "multiplier": 1.5,
    "pool_total": 58500,
    "discount_kill_active": false
  }
}
```

Test `test_payout_matches_excel_pune_may26` runs the calculator on these inputs, asserts every output matches Excel ±₹1. **If this test ever drifts, the calculator regressed.**

### Verification

- [ ] All 3 phases merged
- [ ] `/payout/preview` for Pune May-26 returns pool=₹58,500
- [ ] Discount-kill: avg_discount=0.16 → pool=0
- [ ] Best-level-only: achieving L3 zeroes L1+L2 pools
- [ ] Manager bonus stacks correctly with SAMEER's individual payout
- [ ] Floor rounding: 11.99% → 1.4× multiplier (not 1.3×)
- [ ] Lock month creates immutable snapshot; second lock → 409
- [ ] PDF export renders Pune May-26 data cleanly
- [ ] Audit log captures lock + mark-paid

---

## End-to-end verification (programme-level)

After all 12 phases ship:

- [ ] Pune team has stopped using Excel for new walkouts (Module i Phase 5 cutover complete)
- [ ] 30 days of real walkout + points data in MongoDB
- [ ] `/payout/preview` for Pune current-month matches Excel-side calc within ±₹1
- [ ] FU SLA cron has run nightly; first overdue FU produced an escalation task in the Tasks module
- [ ] POS walk-in counter increments and reconciles with manual top-ups
- [ ] All audit log writes traceable: every walkout intake, every points-log save, every payout lock
- [ ] Documentation updated:
  - `docs/SYSTEM_INTENT.md` references the incentive subsystem
  - `docs/API_DOCUMENTATION.md` lists all new `/api/v1/walkouts/*`, `/api/v1/incentive/*`, `/api/v1/payout/*` endpoints
  - This file (`PUNE_INCENTIVE_BUILD_PLAN.md`) has its phase checkboxes ticked

---

## Per-phase prompts (for CLI Claude sessions)

The §M1-PHASE1-PROMPT above is the only fully-drafted starting prompt. For subsequent phases, use this template:

```
You're implementing {MODULE} Phase {N}. Full spec in
docs/PUNE_INCENTIVE_BUILD_PLAN.md — read the "{MODULE}" section
and the "Phased plan" subsection there. This phase covers:

  {bullet list of phase scope from the doc}

Branch off main as `claude/{module-slug}-phase-{N}`.

Files to create/modify (from §"File paths"):
  {list}

Tests to add: see §"Tests" — phase {N} group.

Verification: see §"Verification" checklist; run the phase-{N}
items before declaring done.

When done: PR with title "{Module} Phase {N}: {one-line goal}"
and body referencing this doc.
```

That template + `docs/PUNE_INCENTIVE_BUILD_PLAN.md` is enough context for any CLI Claude session to pick up any phase.

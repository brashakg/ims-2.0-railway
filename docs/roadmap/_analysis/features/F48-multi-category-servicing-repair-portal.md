# Feature #48: Multi-Category Servicing & Repair Portal
META: effort=L days=12 risk=MED roi=3 quickwin=no deps=none phase=3

## Existing overlap
IMS already has a near-complete foundation for this feature:

- **Workshop job lifecycle** (`backend/api/routers/workshop.py`): PENDING → IN_PROGRESS → COMPLETED → QC_FAILED → READY → DELIVERED state machine with lens-order sub-lifecycle, rework tracking, vendor/lab assignment, and SMS ready-notification (`notify_ready()`, line 1379). The only missing pieces are multi-category service types, per-store configurability, and customer-facing status.
- **Workshop KPI dashboard** (`backend/api/routers/workshop.py`, `GET /workshop/dashboard-kpis`): already aggregates pending/overdue/in-progress/completed-today counts.
- **Workshop scan-to-advance** (`backend/api/routers/labels.py`): stage-gated barcode scan pipeline (INTAKE → FITTING → QC → PICKUP stages) already exists for the lens workflow — directly reusable for repair check-in/dispatch.
- **MSG91 WhatsApp/SMS** (`backend/agents/providers.py`, MEGAPHONE agent, `DISPATCH_MODE` gate): customer notifications with quiet-hours, DND, and delivery-receipt logging in `notification_logs` already wired.
- **Vendor/lab portal** (`backend/api/routers/vendor_portal.py`): token-auth external portal (rate-limited, PII-redacted) for labs to update job status — extends naturally for external repair agents (watch-battery vendor, HA calibration lab).
- **RBAC** (`backend/api/services/rbac_policy.py`): WORKSHOP_STAFF role exists; store-scoped data filtering pattern established.
- **Notification templates** (`notification_templates` collection, `settings.py`): per-template_id SMS/WhatsApp templates already configurable from settings UI.

The key gap is: workshop jobs are lens-only (tightly coupled to `prescription_id`, `frame_details`, `lens_details`). Servicing/repair needs a parallel or extended job type for non-optical categories (watches, hearing aids, frames, contact-lens accessories) with store-level service-catalog gating.

## Reuse (extend, don't rebuild)
- **`workshop_jobs` collection** (`backend/database/repositories/workshop_repository.py`): add `job_category` discriminator field (`LENS_ORDER` | `REPAIR_SERVICE`) and `service_type_id` FK. Lens-order jobs remain unchanged; repair jobs skip lens-specific fields.
- **`backend/api/routers/workshop.py`**: add repair-specific endpoints alongside existing lens endpoints; shared status PATCH, QC, notify-ready, vendor-assign, scan-advance all apply unchanged.
- **`backend/api/routers/labels.py`**: reuse `scan_advance()` for repair check-in/dispatch scanning; map new `REPAIR_INTAKE` and `REPAIR_DISPATCH` stations to existing stage pipeline.
- **`backend/api/routers/vendor_portal.py`**: register repair vendors with scoped tokens to post status updates (RECEIVED → IN_REPAIR → DISPATCHED → DELIVERED) — same auth pattern.
- **`backend/agents/providers.py` + MEGAPHONE**: reuse `send_whatsapp` / `send_sms` + existing quiet-hours + DND for automated customer SMS updates at status transitions.
- **`backend/api/routers/settings.py`**: add `service_catalog` settings endpoint (SUPERADMIN configures which service types exist, maps to categories/stores).
- **`notification_logs` collection**: reuse for all repair SMS delivery receipts.
- **`frontend/src/pages/workshop/WorkshopPage.tsx`**: extend with a "Repairs" tab/view alongside the existing lens-order kanban.
- **`audit_logs` collection**: reuse for all repair status transitions (same immutable pattern).

## Data model

**New collection: `service_catalog`** (SUPERADMIN-managed, global)
```
service_type_id   (unique)
name              (e.g., "Watch Battery Replacement", "Hearing Aid Calibration")
category          (WATCH | HEARING_AID | FRAME_REPAIR | ACCESSORY | OTHER)
description
standard_turnaround_days  (integer, drives SLA / overdue flag)
base_price_inr    (optional display estimate; actual quote captured per job)
active            (bool)
requires_vendor   (bool — send to external lab vs in-house)
enabled_store_ids (array of store_id — controls store-level visibility)
created_at, updated_at
```

**New collection: `repair_jobs`** (separate from `workshop_jobs` to avoid schema pollution)
```
repair_id         (unique)
repair_number     (e.g., "REP/BV-RNCHI/2026-27/0042" — same invoice-counter pattern)
store_id
customer_id, customer_name, customer_phone
service_type_id   → FK service_catalog
service_type_name (denormalised for display)
category          (copied from service_catalog at create time)
item_description  (free text: "Rolex Submariner, serial #xyz")
item_condition    (FREE_TEXT, captured at intake)
quoted_price_inr  (optional; owner-decision: mandatory or optional)
status            (INTAKE | IN_PROGRESS | SENT_TO_VENDOR | RECEIVED_FROM_VENDOR | QC | READY | DELIVERED | CANCELLED)
status_history    (array: {status, changed_by, changed_at, notes})
vendor_id, vendor_name, vendor_tracking_url, vendor_dispatch_date, vendor_received_date
vendor_status_history (array, same shape as workshop_jobs)
qc_passed (bool), qc_notes, qc_by, qc_at, qc_waived, qc_waive_reason
rework_count
expected_date     (promised to customer)
completed_at, delivered_at, delivered_by
ready_notified_at, ready_notified_by
sms_log           (array: {event, sent_at, status — SENT/FAILED/SIMULATED})
created_by, created_at, updated_at, updated_by
```

**New fields on `stores` collection** (or `store_settings` sub-doc):
```
enabled_service_categories  (array of category strings — drives portal filter)
```

## Backend

- `GET /api/v1/service-catalog` (SUPERADMIN/ADMIN): list all service types with store-availability.
- `POST /api/v1/service-catalog` (SUPERADMIN): create service type; validate `enabled_store_ids` are real stores.
- `PUT /api/v1/service-catalog/{id}` (SUPERADMIN): edit name, price, turnaround, enabled stores, active flag.
- `GET /api/v1/service-catalog/for-store/{store_id}` (STORE_MANAGER/SALES_CASHIER/WORKSHOP_STAFF): returns only active service types enabled for that store — drives the intake form dropdown.
- `POST /api/v1/repairs` (STORE_MANAGER/SALES_CASHIER): create repair job; auto-assign `repair_number` via atomic counter (reuse `counters` collection pattern with prefix `REP/{store_prefix}/{FY}`); validate `service_type_id` is active + enabled for caller's store.
- `GET /api/v1/repairs` (STORE_MANAGER/AREA_MANAGER/ADMIN/SUPERADMIN/WORKSHOP_STAFF): list with filters (status, category, store_id, date range); store-scoped for non-HQ roles.
- `GET /api/v1/repairs/{repair_id}` (same roles): detail view.
- `PATCH /api/v1/repairs/{repair_id}/status` (WORKSHOP_STAFF/STORE_MANAGER): forward-only status transitions with guard table (same `VALID_JOB_TRANSITIONS` pattern from `workshop.py`); auto-triggers SMS on READY and DELIVERED transitions.
- `POST /api/v1/repairs/{repair_id}/qc` (WORKSHOP_STAFF/STORE_MANAGER): pass/fail; QC_FAILED → rework count increment; requires QC pass or waiver before READY.
- `POST /api/v1/repairs/{repair_id}/notify-ready` (STORE_MANAGER/WORKSHOP_STAFF): manual re-trigger of SMS if auto-send failed; stamps `ready_notified_at`; uses existing `send_whatsapp` / `send_sms` + DISPATCH_MODE gate.
- `PATCH /api/v1/repairs/{repair_id}/vendor` (STORE_MANAGER/ADMIN): assign external vendor; status transitions to SENT_TO_VENDOR; sets expected_date.
- `POST /api/v1/repairs/{repair_id}/scan-advance` (WORKSHOP_STAFF): barcode scan → auto-advance status; reuse `labels.py` scan pipeline pattern with `REPAIR_INTAKE` / `REPAIR_DISPATCH` station map.
- `GET /api/v1/repairs/dashboard-kpis` (STORE_MANAGER/AREA_MANAGER/ADMIN): intake-today / in-progress / overdue (past expected_date) / ready-for-pickup / delivered-today per store; extend existing `workshop/dashboard-kpis` shape.
- **Vendor portal extension**: Register repair vendors with scoped tokens in `vendor_portal_tokens`; `GET /vendor-portal/{token}/repairs` and `POST /vendor-portal/{token}/repairs/{repair_id}/status` — same rate-limit + PII-redact pattern as `vendor_portal.py`.
- **TASKMASTER hook**: expose `repair_jobs` overdue check (past expected_date + status not READY/DELIVERED/CANCELLED) as a new escalation source — emit P2 system task to STORE_MANAGER when a job goes overdue.

## Frontend

- **`frontend/src/pages/workshop/RepairsPage.tsx`** (new page, linked from workshop nav or as its own nav item under Operations): Kanban/list view of repair jobs for the current store. Columns: INTAKE / IN_PROGRESS / QC / READY / DELIVERED. Cards show: repair_number, customer name, service type, item description, expected date, days-in-shop badge (red if overdue). Filter bar: category, status, date range.
- **Repair Intake Modal** (new component, `RepairIntakeModal.tsx`): triggered from RepairsPage "New Repair" button. Fields: customer search (reuse existing customer search component), service type dropdown (populated from `/service-catalog/for-store/{store_id}` — only store-relevant options shown), item description, item condition, quoted price (if owner makes it optional, show as optional), expected date. On submit → POST `/repairs`.
- **Repair Detail Drawer** (new component, `RepairDetailDrawer.tsx`): slide-in panel on card click. Shows full timeline (status_history), vendor info, QC result, SMS log, expected vs actual date. Actions: Update Status, Record QC, Assign Vendor, Re-send SMS.
- **Service Catalog Settings Page** (`frontend/src/pages/settings/ServiceCatalogPage.tsx`, new): SUPERADMIN only. Table of service types (name, category, turnaround, price, enabled stores, active toggle). Add/edit drawer. Per-row store multi-select (checkboxes by store name) — drives `enabled_store_ids`. Deactivating a type hides it from all store intake forms immediately.
- **Workshop KPI card extension**: add "Repairs" row to existing `WorkshopPage.tsx` KPI bar (intake-today, overdue repairs, ready-for-pickup count).
- **SMS log sub-panel** inside RepairDetailDrawer: shows each automated SMS event (status trigger, timestamp, SENT/FAILED/SIMULATED), honest delivery status from `notification_logs`. No "send status is unknown" — same honest-status contract as rest of system.
- All UI: neutral palette, single accent (`bv-red`), status chips using existing badge classes, no emojis, no dark tokens.

## Business rules

- **Service type enabled per store is mandatory**: a store cannot accept a repair type not in its `enabled_store_ids` — validated server-side (not just UI filter). This prevents watch-repair intake at a store with no watchmaker.
- **Status is forward-only**: INTAKE → IN_PROGRESS → (optionally SENT_TO_VENDOR → RECEIVED_FROM_VENDOR) → QC → READY → DELIVERED. CANCELLED allowed from any non-terminal state. No backward transitions except QC_FAILED → IN_PROGRESS (rework) — enforced by `VALID_REPAIR_TRANSITIONS` dict on the backend.
- **QC gate before READY**: `qc_passed=True` OR `qc_waived=True` (with `qc_waive_reason` required) before status can advance to READY. Enforced in PATCH status handler and in QC endpoint.
- **Automated SMS triggers** (business-configurable thresholds owned by owner): fire on READY (item ready for pickup) and DELIVERED (confirmed handover). Optionally on IN_PROGRESS (work started) — owner decision. All sends respect DISPATCH_MODE gate and quiet hours (21:00–09:00 IST).
- **Quoted price**: captured at intake and immutable after job moves to IN_PROGRESS (prevents bait-and-switch). Actual final price on delivery captured separately if changed (requires STORE_MANAGER+ to override, with reason — audit-logged).
- **Overdue flag**: job is overdue when `now > expected_date` and status not in (READY, DELIVERED, CANCELLED). TASKMASTER raises P2 task to STORE_MANAGER on overdue detection (same pattern as existing SLA escalation).
- **Audit everything**: every status change, QC result, vendor assignment, SMS re-trigger, price override writes to `audit_logs` with before/after state. Immutable.
- **repair_number uniqueness**: atomic counter per (store_prefix, FY) using `counters` collection — same pattern as `invoice_number`. Unique sparse index on `repair_number`.
- **Period lock check**: PATCH status to DELIVERED blocked if accounting period is locked (reuse `check_period_locked` from `finance.py`) only if financial settlement is recorded at delivery — owner decision on whether repair delivery creates a sale transaction.

## RBAC

| Role | Can do |
|---|---|
| SUPERADMIN | Full access: service catalog CRUD, all stores' repair queues, settings |
| ADMIN | Full access to all stores' repair queues; cannot edit service catalog (SUPERADMIN-only) |
| AREA_MANAGER | View + status-update repairs across their stores; cannot create service types |
| STORE_MANAGER | Full repair CRUD for own store: intake, status updates, QC, vendor assign, SMS re-trigger |
| WORKSHOP_STAFF | View + status-update (INTAKE → IN_PROGRESS → QC → READY) for own store; cannot deliver (delivery = STORE_MANAGER/SALES_CASHIER gate to capture payment) |
| SALES_CASHIER / SALES_STAFF | Create repair intake (customer-facing); mark DELIVERED after payment collected |
| ACCOUNTANT | Read-only (for billing reconciliation); cannot touch job status |
| OPTOMETRIST / CASHIER / CATALOG_MANAGER / DESIGN_MANAGER | No access |

Vendor portal token holders (external repair labs): token-scoped read of their assigned jobs + status POST only (no PII beyond customer initials).

## Integrations

- **MSG91** (MEGAPHONE path): automated SMS/WhatsApp on READY and DELIVERED transitions; reuse `send_whatsapp` / `send_sms` from `agents/providers.py`, DISPATCH_MODE-gated, delivery receipts via existing MSG91 DLR webhook → `notification_logs`.
- **TASKMASTER**: overdue-repair escalation hook (new escalation source type `repair_overdue` alongside existing `task_overdue`); emits P2 system task to STORE_MANAGER.
- **Vendor portal** (`vendor_portal.py`): extend with repair-job endpoints; same token-auth, rate-limit (60 req/min), PII redaction.
- **Tally**: if repair delivery creates a sale transaction (owner decision), the resulting order flows into the existing Tally sales-JV XML export via NEXUS nightly — no new Tally integration needed.
- No Shopify, Razorpay, or ONDC involvement unless repair payments are processed through POS (owner decision).

## Risk notes

- **Schema separation risk**: keeping `repair_jobs` as a separate collection (not extending `workshop_jobs`) avoids lens-workflow regression but means some dashboard aggregation must union two collections. Acceptable trade-off given how lens-specific `workshop_jobs` fields are (`prescription_id`, `lens_status`, `fitting_details`).
- **POS/money risk**: if repair delivery is gated on payment collection (owner decision), it creates a code path that touches order creation — that is POS-adjacent and must ship behind a feature flag (`REPAIR_PAYMENT_INTEGRATED=false` default). Until the flag is on, delivery is a status-only action with no financial side effect.
- **SMS volume**: automated SMS on status transitions could generate significant MSG91 cost if repair volume is high. Batch or template-level opt-out should be configurable.
- **Overdue escalation noise**: if TASKMASTER fires a P2 task per overdue job per tick (every 5 min), a store with 20 overdue repairs would spam the task queue. Deduplicate: one open task per repair_id, close when status advances.
- **Vendor portal security**: repair items (watches, hearing aids) are high-value; vendor portal must never expose customer full name, phone, or address — enforce initials-only redaction same as `vendor_portal.py` current implementation.
- **Feature flag**: entire repair portal should be behind `REPAIR_PORTAL_ENABLED=false` env flag so it can be deployed to Railway without being visible to store staff until the owner is ready to train them.

## Recommendation
Build later (Phase 3, after core POS/clinical/finance hardening is stable) — it is a genuine revenue-generating feature (service revenue stream beyond product sales) but not blocking daily operations. When built, fold the QC, vendor-portal, and SMS-notification patterns directly from `workshop.py` and `vendor_portal.py` rather than rebuilding; the 12-day estimate assumes that reuse is maximised. Do not fold into existing `workshop_jobs` — the lens workflow is too tightly coupled and mixing would increase regression risk on revenue-critical POS-adjacent code.

## Owner decisions
- Q: Should repair delivery require payment collection at the counter (i.e., does DELIVERED status trigger a POS sale transaction), or is repair billed separately / on credit? | Why: If integrated with POS, repair revenue flows into daily sales, GST calculations, and Tally automatically. If separate, it stays a status tracker with manual billing. Integrated is cleaner accounting but adds POS-adjacent code risk. | Options: a) POS-integrated (DELIVERED creates an order, payment collected at counter) / b) Status-only + manual invoice (simpler, no POS risk) / c) Status-only now, POS-integrated in a later sub-phase
- Q: Which service categories should the system support at launch, and which stores carry which categories? | Why: This directly populates `service_catalog` and `enabled_store_ids` — the store-to-service mapping is the core configuration the system enforces. | Options: Start with the categories you already handle today (e.g., frame adjustments, watch battery) and expand later; or launch with a comprehensive list and enable per-store
- Q: Is the quoted price at intake mandatory or optional? | Why: Mandatory enforces price transparency and prevents disputes; optional gives staff flexibility for jobs where cost is unknown until diagnosis. | Options: a) Mandatory at intake / b) Optional at intake, mandatory before IN_PROGRESS / c) Optional throughout (display "TBD")
- Q: Should automated SMS fire only on READY and DELIVERED, or also on IN_PROGRESS (work started) and SENT_TO_VENDOR (sent to external lab)? | Why: More touchpoints increase customer confidence but also increase MSG91 cost and potential for annoyance. | Options: a) READY + DELIVERED only (minimal) / b) IN_PROGRESS + READY + DELIVERED / c) All transitions including SENT_TO_VENDOR
- Q: Should repair jobs appear as a separate nav item ("Repairs") or as a tab within the existing Workshop module? | Why: Separate nav makes the feature more visible and easier to discover; tab within Workshop keeps the navigation cleaner if repair volume is low. | Options: a) Separate "Repairs" nav item under Operations / b) Tab within Workshop page / c) Combined "Workshop & Repairs" page with a toggle
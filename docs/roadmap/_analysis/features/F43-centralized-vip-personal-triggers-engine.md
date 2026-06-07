# Feature #43: Centralized VIP "Personal Triggers" Engine
META: effort=M days=6 risk=LOW roi=4 quickwin=no deps=none phase=3

## Existing overlap
- **CRM 360 view** (`backend/api/routers/crm.py`, `_determine_lifecycle_phase()` lines 830-882): lifecycle phase "VIP" (LTV ≥ ₹1L or freq ≥ 20) already flags customers — extend rather than re-define.
- **Follow-ups module** (`backend/api/routers/follow_ups.py`, `follow_ups` collection): task type "general" + scheduled_date already models "do this on this date" — personal triggers are a new follow_up source.
- **Customer 360 Dashboard** (`frontend/src/pages/customers/Customer360Dashboard.tsx`): the existing VIP-customer detail page is where tags/notes should live and where the trigger panel should surface.
- **MEGAPHONE agent** (`backend/agents/implementations/megaphone.py`, `_scan_rx_expiring()` pattern): the agent already drains date-triggered notifications per customer on a 30-min tick — add a new `_scan_personal_triggers()` pass using the same pattern.
- **In-app bell notifications** (`backend/api/routers/notifications.py`, `notifications` collection): staff alert infrastructure already works — personal trigger alerts go here (no new channel needed for internal staff nudges).
- **notification_logs** (`backend/api/routers/marketing.py`): outbound WhatsApp/SMS to customers already dispatched by MEGAPHONE through this collection — reuse for customer-facing messages.
- **Quiet hours** (`backend/agents/quiet_hours.py`): already enforces 21:00-09:00 IST — all outbound messages from triggers respect it automatically via existing gate.
- **Loyalty tier** (`backend/api/routers/loyalty.py`, `loyalty_accounts` collection, `tier` field): GOLD/PLATINUM already computed — use as default VIP gate if owner does not want a separate VIP flag.

## Reuse (extend, don't rebuild)
- `customers` collection: add `vip_tags` array and `personal_notes` free-text field to existing customer doc (no new collection for the tags themselves).
- `follow_ups` collection + `follow_ups.py` router: add `source="personal_trigger"` and `trigger_id` field; the existing list/filter/complete endpoints work unchanged.
- `backend/api/routers/crm.py`: extend customer 360 endpoint to return `vip_tags` and upcoming trigger summary.
- `backend/agents/implementations/megaphone.py` `_tick()`: add `_scan_personal_triggers()` alongside existing `_scan_rx_expiring()` — same date-window pattern, same drain loop.
- `frontend/src/pages/customers/Customer360Dashboard.tsx`: add a "VIP Notes & Triggers" card below the existing loyalty/interaction cards.
- `backend/api/services/notification_service.py`: reuse `send_notification()` for outbound customer messages tied to triggers.

## Data model
New fields on `customers` (no new collection for tags):
```
vip_tags: [
  { tag_id, key, value, added_by, added_at }
  // e.g. { key: "preference", value: "hates gold frames" }
  //      { key: "anniversary", value: "2024-10-12" }
  //      { key: "brand_affinity", value: "Chopard" }
]
personal_notes: str   // free-text, max 2000 chars
vip_override: bool    // manual VIP flag (owner decision Q1 below)
```

New collection `personal_triggers`:
```
{
  trigger_id,
  customer_id,
  store_id,
  trigger_type,         // ANNIVERSARY | BIRTHDAY_PLUS_N | RECURRING_ANNUAL | ONE_TIME | TAG_MATCH
  trigger_date,         // ISO date (YYYY-MM-DD); null for TAG_MATCH
  recurs_annually: bool,
  lead_days: int,       // notify staff N days before (default 3)
  pitch_template: str,  // e.g. "anniversary coming - pitch the silver Omega"
  channel: str,         // STAFF_ALERT | WHATSAPP | SMS | EMAIL
  assigned_staff_id,    // null = store manager
  status,               // ACTIVE | FIRED | SNOOZED | CANCELLED
  last_fired_at,
  created_by,
  created_at,
  updated_at
}
```

Index: `(customer_id, trigger_date, status)` and `(store_id, trigger_date, status)` for MEGAPHONE date-window scan.

New field on `follow_ups`:
```
source: str            // add "personal_trigger" to existing type enum
trigger_id: str        // links back to personal_triggers doc
```

## Backend
- `POST /api/v1/crm/customers/{customer_id}/vip-tags` — upsert a tag (key/value pair) on the customer doc; roles: STORE_MANAGER, AREA_MANAGER, ADMIN, SUPERADMIN.
- `DELETE /api/v1/crm/customers/{customer_id}/vip-tags/{tag_id}` — remove a tag; same roles.
- `PUT /api/v1/crm/customers/{customer_id}/personal-notes` — overwrite free-text notes; same roles.
- `GET /api/v1/crm/customers/{customer_id}/vip-tags` — list tags + personal notes + upcoming triggers (next 30 days); same roles.
- `POST /api/v1/crm/personal-triggers` — create a trigger (all fields); STORE_MANAGER+.
- `GET /api/v1/crm/personal-triggers?store_id=&customer_id=&status=&upcoming_days=` — list; store-scoped.
- `PATCH /api/v1/crm/personal-triggers/{trigger_id}` — edit lead_days, pitch_template, assigned_staff_id, cancel; STORE_MANAGER+.
- `GET /api/v1/crm/personal-triggers/due-today?store_id=` — MEGAPHONE internal use + staff dashboard widget; returns triggers firing within lead_days of today.
- MEGAPHONE agent `_scan_personal_triggers()` (new method in `megaphone.py`): runs every tick, queries `personal_triggers` where `trigger_date - lead_days <= today <= trigger_date` and `status=ACTIVE` and `last_fired_at` not today. For STAFF_ALERT channel: creates a `follow_up` doc (source="personal_trigger") and an in-app `notification` for assigned_staff_id (or store manager). For WHATSAPP/SMS/EMAIL: calls `send_notification()` to customer. Stamps `last_fired_at=today`; sets `status=FIRED` for ONE_TIME; leaves ACTIVE for RECURRING_ANNUAL. Respects quiet hours via existing gate.

## Frontend
- **Customer360Dashboard.tsx** — add "VIP Notes & Triggers" card (restrained, single-accent): top section is a plain textarea for `personal_notes` (auto-save on blur); below it a compact tag list (`key: value` chips, add/delete inline, no color coding except semantic red for delete icon); bottom is a mini-table of upcoming triggers (date, type, pitch, channel, assigned staff) with a "+ Add Trigger" button.
- **New modal: `PersonalTriggerModal.tsx`** — minimal form: trigger type select, date picker (hidden when TAG_MATCH), recurs annually toggle, lead days number input (1-14), pitch template textarea (max 500 chars), channel select, assign to staff dropdown. Single "Save" button.
- **Staff Home / Hub widget** — extend existing today-tasks section (or the Hub `DueTodayPanel` if it exists) with a "VIP Triggers Today" row: customer name, pitch text, one-tap "Mark done" that completes the linked `follow_up`. No new page needed.
- **CRM segment page** (`frontend/src/pages/customers/CustomerSegmentation.tsx`) — add a "VIP" tab that lists customers where `vip_override=true` OR loyalty tier IN [GOLD, PLATINUM]. Each row links to their Customer360. No new page.

## Business rules
- Tags are purely qualitative (no PII-sensitive fields like medical history — that lives in prescriptions). Tags with `key="anniversary"` or `key="birthday_override"` whose value is a valid date (YYYY-MM-DD or DD-MM) are automatically parsed as trigger candidates by the create-trigger endpoint.
- `personal_notes` max 2000 chars; tags max 50 per customer; tag value max 200 chars.
- `pitch_template` max 500 chars; must not be blank when saving a trigger.
- `lead_days` range 1-14; default 3.
- STAFF_ALERT triggers always create a `follow_up` doc so completion is tracked.
- Customer-facing channels (WHATSAPP/SMS/EMAIL) require `marketing_consent=True` on the customer doc — enforced in `_scan_personal_triggers()` same as existing MEGAPHONE consent gate (`campaigns.py` line 710-717).
- Triggers that fired today cannot re-fire the same calendar day (idempotency via `last_fired_at` date comparison).
- RECURRING_ANNUAL triggers auto-advance `trigger_date` to next year after firing (MEGAPHONE stamps `trigger_date += 1 year`).
- Audit: every tag add/remove and trigger create/edit writes to `audit_logs` (action="vip_tag.add" / "personal_trigger.create" etc.) — same immutable pattern as existing customer update audit.
- No discount or pricing logic touches this feature — zero POS risk.

## RBAC
- **Create/edit/delete tags, notes, triggers**: STORE_MANAGER, AREA_MANAGER, ADMIN, SUPERADMIN (sales staff cannot write — avoids cluttered/abusive tags).
- **Read tags, notes, upcoming triggers**: all roles who can view a customer (SALES_STAFF, SALES_CASHIER, OPTOMETRIST can see tags on the 360 view — read-only for them).
- **VIP override flag** (`vip_override=true`): ADMIN and SUPERADMIN only.
- **MEGAPHONE scan + fire**: runs as SYSTEM (no user JWT); audit stamped with `agent_id="megaphone"`.
- **Due-today endpoint**: any role with store access (used by Hub widget).
- Jarvis page + agent management: existing SUPERADMIN-only gate unchanged.

## Integrations
- **MEGAPHONE agent**: primary integration — existing 30-min tick extended with `_scan_personal_triggers()`.
- **MSG91 WhatsApp/SMS**: customer-facing trigger messages routed through existing `send_notification()` → MSG91 (DISPATCH_MODE-gated, fail-soft).
- **In-app bell** (`notifications` collection): STAFF_ALERT channel writes here — no new infra.
- No Shopify, Razorpay, Tally, or POS involvement.

## Risk notes
- Zero POS/money/accounting risk — feature touches only CRM, notifications, and follow-ups.
- DISPATCH_MODE gate is already in place; customer-facing messages are simulated until owner sets `DISPATCH_MODE=live`.
- Tag parsing (detecting dates in tag values) must be lenient and fail-soft — malformed dates skip auto-trigger creation, never throw.
- RECURRING_ANNUAL date-advance logic must handle Feb 29 (leap-year birthdays) — advance to Mar 1 on non-leap years.
- No feature flag required (feature is additive — existing customer docs unaffected until a tag or trigger is added).
- Data volume: trigger scan is an indexed date-range query on a small collection (triggers per store are in the dozens, not millions) — no performance concern.

## Recommendation
Build later (Phase 3, after core financials and P0/P1 bug backlog are clear). It is a revenue-multiplier feature (VIP retention) but depends on staff discipline to log tags consistently — launch it only after store managers are actively using the Customer 360 view.

## Owner decisions
- Q: Should "VIP" require a manual override flag, or auto-promote customers who reach GOLD/PLATINUM loyalty tier? | Why: Determines whether the VIP segment is loyalty-math-driven (zero effort) or relationship-driven (curated). | Options: a) Auto from GOLD+ tier only / b) Manual override only / c) Either (auto OR manual flag)
- Q: Which staff roles should receive STAFF_ALERT in-app nudges — only the assigned staff member, or the full store-manager team? | Why: If alerts go to all managers, they act as a safety net when the assigned person is absent; if only assigned, it keeps accountability clear. | Options: a) Assigned staff only / b) Assigned staff + store manager always / c) Assigned staff + store manager only when unassigned
- Q: For customer-facing trigger messages (WhatsApp/SMS), should the pitch template be sent verbatim to the customer, or should it stay internal (staff-only coaching note)? | Why: If pitch template is internal only, the customer message needs a separate "customer message" field on the trigger; if sent verbatim, the template must be customer-appropriate (no "upsell the Omega"). | Options: a) Pitch template is internal only; add a separate customer-message field / b) Pitch template IS the customer message (owner writes customer-ready copy) / c) STAFF_ALERT only — no outbound customer messages from this feature
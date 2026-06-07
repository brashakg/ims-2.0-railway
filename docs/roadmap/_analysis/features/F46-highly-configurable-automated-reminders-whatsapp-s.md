# Feature #46: Highly Configurable Automated Reminders (WhatsApp/SMS)
META: effort=M days=5 risk=LOW roi=4 quickwin=yes deps=none phase=3

## Existing overlap
IMS already has significant infrastructure for this feature — this is primarily a configuration UI + trigger-engine layer over existing components:

- **MEGAPHONE agent** (`backend/agents/implementations/megaphone.py`) already scans for Rx expiry, birthdays, and walkout follow-ups on a 30-minute tick, dispatching via `send_notification()`. The Rx-expiry logic fires at 90/30/7 day windows (hardcoded).
- **notification_templates collection** (`backend/api/routers/settings.py:283-291`) stores per-template_id docs for SMS/Email/WhatsApp. Templates editable via admin settings UI.
- **campaigns collection + campaign_segments.py** (`backend/api/routers/campaigns.py`, `backend/api/services/campaign_segments.py`) — 6 predefined segments including `rx_expiry` (90-day window), `birthday` (next 7 days), `winback` (6-month inactive). Campaigns already have ONE_TIME/RECURRING/TRIGGERED schedule kinds.
- **quiet_hours.py** (`backend/agents/quiet_hours.py`) — shared 21:00–09:00 IST DND window already enforced by MEGAPHONE.
- **notification_logs collection** — every outbound notification written with channel, status, template_id, sent_at, customer_id.
- **marketing_consent gate** (`campaigns.py:710-717`) — consent checked before dispatch.
- **DISPATCH_MODE gating** (`settings.py:1361`) — off/test/live, honest SIMULATED/SENT/FAILED status.
- **Workshop "ready for pickup" notification** (`backend/api/routers/workshop.py:1379-1463`) — `notify_ready()` already sends WhatsApp + in-app notification, stamps `ready_notified_at`. Currently hardcoded trigger (manual staff action or workshop status → READY).

The gap is: all triggers are **hardcoded** (fixed windows, fixed templates). There is no runtime-configurable rule matrix letting SUPERADMIN/ADMIN define which reminders fire, on what schedule, with which template, for which store/brand.

## Reuse (extend, don't rebuild)
- **`backend/agents/implementations/megaphone.py`** — extend `_scan_rx_expiring()`, `_scan_birthdays_today()`, `_drain_pending()` to read from the new `reminder_rules` collection instead of hardcoded constants
- **`backend/api/services/campaign_segments.py`** — extend `rx_expiry` segment to accept configurable `window_days` from the rule; add new segment resolvers: `order_ready`, `frame_service_due`, `post_purchase_feedback`
- **`backend/api/services/notification_service.py`** — reuse `send_notification()` as the dispatch leaf; rules emit PENDING rows → MEGAPHONE drains them
- **`backend/api/routers/campaigns.py`** — reuse TRIGGERED campaign type; a reminder_rule can reference a campaign_id so analytics flow through existing campaign_analytics aggregation
- **`backend/api/routers/settings.py`** — add new `reminder_rules` settings endpoints alongside existing `notification_templates` endpoints (same router, same encryption pattern)
- **`backend/api/routers/workshop.py:notify_ready()`** — extend to check the `order_ready` rule config (channel, template, delay) rather than hardcoded WhatsApp-only
- **`notification_logs` collection** — no schema change; reminder-dispatched notifications already land here with `campaign_id` stamped
- **`backend/agents/quiet_hours.py`** — no change; already shared, all new dispatches respect DND window

## Data model
New collection: **`reminder_rules`** (upsert by `rule_id`):
```
{
  rule_id: str (slug: "rx_expiry_90d", "order_ready", "frame_service_6mo", "post_purchase_3d"),
  trigger_type: enum (RX_EXPIRY | ORDER_READY | FRAME_SERVICE_DUE | POST_PURCHASE_FEEDBACK | BIRTHDAY | WALKOUT_FOLLOWUP),
  enabled: bool,
  store_ids: list[str] | null (null = all stores),
  channels: list[enum] (WHATSAPP | SMS | EMAIL),
  template_id: str (references notification_templates),
  days_before: int | null (for expiry-style triggers; e.g., 90, 30, 7),
  days_after: int | null (for post-event triggers; e.g., order_delivered + 3 days),
  repeat_allowed: bool (can same customer get same rule twice in a period),
  repeat_cooldown_days: int (min days between repeat sends to same customer),
  quiet_hours_exempt: bool (always false — never override 21:00-09:00 gate),
  updated_by: str,
  updated_at: datetime
}
```

New field on **`notification_logs`**: `rule_id: str | null` — stamps which reminder_rule triggered the send. Enables per-rule analytics without schema breakage (existing rows have null).

New field on **`orders`** (if not present): `feedback_reminder_sent_at: datetime | null` — idempotency guard for post-purchase rule. Check with `Grep` before adding — may already exist.

New field on **`workshop_jobs`** (if not present): `service_due_date: date | null` — for frame/watch servicing reminder trigger. Currently no servicing-due field exists.

## Backend

**`GET /api/v1/settings/reminder-rules`** (SUPERADMIN, ADMIN)
- Returns all `reminder_rules` docs, sorted by trigger_type. Includes `enabled`, `template_id`, per-channel flags, window config.

**`PUT /api/v1/settings/reminder-rules/{rule_id}`** (SUPERADMIN, ADMIN)
- Upsert a single rule. Validates: template_id exists in `notification_templates`; days_before/days_after non-negative; channels subset of VALID_CHANNELS; store_ids valid store IDs. Writes audit_logs entry (before/after state).

**`POST /api/v1/settings/reminder-rules/{rule_id}/test`** (SUPERADMIN)
- Sends a test notification for the rule to the configured TEST_PHONE (DISPATCH_MODE=test path). Returns honest SIMULATED/SENT status. Reuses existing settings test-send pattern (`settings.py:1361-1383`).

**`GET /api/v1/settings/reminder-rules/{rule_id}/analytics`** (SUPERADMIN, ADMIN)
- Aggregates `notification_logs` by `rule_id` for last 30/60/90 days. Returns sent_count, delivered_count, failed_count. Reuses existing `campaign_analytics` aggregation pattern from `campaigns.py:223-298`.

**Extend `megaphone.py` `_do_background_work()`**:
- Load all `enabled=True` reminder_rules from db on each 30-min tick.
- For each rule, call the appropriate segment resolver with rule-specific params (e.g., `rx_expiry` with `window_days=rule.days_before`).
- Check `notification_logs` for `rule_id` + `customer_id` within `repeat_cooldown_days` (dedup guard).
- Emit PENDING rows to `notification_logs` with `rule_id` stamped.
- Post-purchase feedback rule: query orders with `status=DELIVERED`, `feedback_reminder_sent_at=null`, delivered within `days_after` window.
- Frame/watch service rule: query `stock_units` or `orders` for items in `frame`/`watch` categories with `sold_at` + 6 months (configurable) approaching.

**Extend `workshop.py:notify_ready()`**:
- Load `ORDER_READY` rule from `reminder_rules`. Use rule's `channels`, `template_id`, `enabled` flag. Fall back to current hardcoded WhatsApp if rule missing (backward compat).

**Extend `campaign_segments.py`**:
- Add `order_ready` segment resolver: orders in status READY with `ready_notified_at=null` (or re-notify after cooldown).
- Add `post_purchase_feedback` segment resolver: delivered orders within configurable days_after window.
- Add `frame_service_due` segment resolver: customers with sold frames/watches approaching service date.

## Frontend

**Extend `frontend/src/pages/settings/` — new tab "Reminders"** inside the existing Settings page (same pattern as Notifications tab):
- Rule list: one card per `reminder_rules` doc. Card shows trigger label, enabled toggle, channels chips (WhatsApp/SMS/Email), days window, template name, last-sent count (from analytics endpoint).
- Rule editor drawer (slide-in panel, not a modal): fields for enabled toggle, store scope (multi-select store picker or "All stores"), channel checkboxes, template dropdown (populated from `notification_templates`), days_before/days_after number input (shown conditionally by trigger_type), repeat cooldown number input.
- Test button per rule (calls `/test` endpoint, shows toast with SIMULATED/SENT result).
- Analytics row below each rule: "Sent 47 · Delivered 43 · Failed 4 (last 30 days)" in small muted text.
- Design: neutral card list, single accent (bv-red) for enabled toggle, color only for semantic meaning (green chip = delivered, red = failed). No emoji. Restrained executive look consistent with existing Settings page.

**Extend `frontend/src/pages/settings/NotificationTemplates.tsx`** (or equivalent) — no new page needed; template editing already lives here. Add a "Used by rules" badge showing which reminder_rules reference each template.

## Business rules
- A rule with `quiet_hours_exempt=false` (always false — hard-locked) never sends between 21:00 and 09:00 IST. No override path exists, even for SUPERADMIN. This is TRAI DND compliance.
- `marketing_consent=false` customers are skipped for all reminder types except `ORDER_READY` (operational, not marketing — consent not required for order status).
- Repeat cooldown is per-(customer, rule_id): if a customer received an RX_EXPIRY_30D reminder within `repeat_cooldown_days`, skip them this tick. Checked via `notification_logs` count query.
- `channels` on a rule must be a subset of the store's enabled providers (integrations collection). If MSG91 creds absent, SMS/WhatsApp channels auto-downgrade to SIMULATED (existing DISPATCH_MODE logic handles this).
- Template must exist in `notification_templates` before rule can be activated. PUT endpoint validates this.
- `store_ids=null` means all stores; `store_ids=[]` is invalid (reject with 422).
- All rule changes write an audit_logs entry with before/after state (action="reminder_rule.updated", entity_type="reminder_rule").
- MEGAPHONE tick failure on one rule does not block other rules — per-rule try/except, same pattern as existing `_safe_register` in `registry.py`.

## RBAC
- **SUPERADMIN**: full access — read, write, test, analytics for all rules, all stores.
- **ADMIN**: read and write rules scoped to stores they manage; can test; can view analytics.
- **STORE_MANAGER**: read-only view of rules applicable to their store; cannot create/edit/test.
- **ACCOUNTANT, OPTOMETRIST, and below**: no access to reminder_rules settings.
- Workshop `notify_ready()` endpoint (existing): remains gated to STORE_MANAGER and above (no change).

Add to `rbac_policy.py` POLICY list:
```
{"method": "GET",  "path": "/api/v1/settings/reminder-rules",            "roles": ["SUPERADMIN","ADMIN","STORE_MANAGER"]},
{"method": "PUT",  "path": "/api/v1/settings/reminder-rules/{rule_id}",  "roles": ["SUPERADMIN","ADMIN"]},
{"method": "POST", "path": "/api/v1/settings/reminder-rules/{rule_id}/test", "roles": ["SUPERADMIN"]},
{"method": "GET",  "path": "/api/v1/settings/reminder-rules/{rule_id}/analytics", "roles": ["SUPERADMIN","ADMIN"]},
```

## Integrations
- **MSG91**: all WhatsApp/SMS dispatch routes through existing `agents/providers.py` `send_whatsapp()`/`send_sms()`. No new integration needed; DISPATCH_MODE gate inherited.
- **Jarvis / MEGAPHONE agent**: core consumer of reminder_rules. MEGAPHONE's 30-minute tick becomes the rule-engine runner. No new agent needed.
- **MongoDB**: new `reminder_rules` collection; new `rule_id` field on `notification_logs` (additive, no migration needed for existing rows).
- **Shopify / Razorpay / Tally**: not involved.

## Risk notes
- **Low technical risk**: all dispatch infrastructure (MEGAPHONE, quiet_hours, notification_logs, DISPATCH_MODE) exists and is tested. This feature adds a config layer, not new dispatch infrastructure.
- **MEGAPHONE tick load**: if many rules are enabled and the customer base is large, the 30-min tick could slow. Mitigate by capping segment resolution at 500 customers per rule per tick (existing `head_limit` pattern in campaign_segments.py).
- **Template drift**: if a `template_id` referenced by a rule is deleted from `notification_templates`, the rule will fail silently at dispatch. Add a startup/tick validation that logs a warning when a rule references a non-existent template. No hard-block needed (fail-soft is the repo contract).
- **No POS or money risk**: this feature does not touch orders, payments, inventory, or accounting. No feature flag needed for the reminder config UI itself.
- **ORDER_READY workshop path change**: modifying `workshop.py:notify_ready()` to read from `reminder_rules` is a behavioral change to a live workflow. Ship with a fallback — if `ORDER_READY` rule missing from db, use hardcoded defaults (backward compat).
- **Frame/watch servicing trigger**: requires a `service_due_date` field on workshop_jobs or a derived calculation from `sold_at + N months`. The derivation approach (no new field) is safer for launch; add the stored field in a follow-on PR.

## Recommendation
**Build now (quick win).** 80% of the infrastructure already exists. The build is a config-layer + MEGAPHONE extension. 5 days of effort, LOW risk, directly reduces no-shows and drives repeat visits. Frame/watch servicing trigger can be deferred to a follow-on PR — ship the other four triggers (RX_EXPIRY, ORDER_READY, BIRTHDAY, POST_PURCHASE_FEEDBACK) first.

## Owner decisions
- Q: Which reminder triggers should be ON by default for new stores (pre-configured seeds), and which should stay OFF until staff enables them? | Why: Seeds in `reminder_rules` collection determine what fires on day one without any setup. Too many default-on rules could spam customers before staff review templates. | Options: (a) All OFF by default — staff must enable each; (b) RX_EXPIRY_30D + ORDER_READY ON by default (operational/expected), all others OFF; (c) All ON by default with a 7-day grace period after store activation.

- Q: For post-purchase feedback, how many days after delivery should the reminder fire? | Why: The `days_after` value in the rule determines when the WhatsApp message reaches the customer. Too soon feels pushy; too late loses recall. | Options: (a) 1 day after delivery; (b) 3 days; (c) 7 days.

- Q: Should frame/watch servicing reminders be sent to all frame-purchase customers, or only customers who opted into a "service plan" at point of sale? | Why: Sending unsolicited servicing reminders to all frame buyers may feel intrusive; but a service-plan flag requires adding a checkbox at POS checkout. | Options: (a) Send to all frame buyers (simpler, wider reach, opt-out via marketing_consent); (b) Only customers who explicitly opted into a servicing plan at POS (requires POS change, narrower but higher-intent audience); (c) Send to all buyers of frames above a price threshold (e.g., MRP > ₹5,000).

- Q: What is the maximum number of automated reminders a single customer can receive in a 30-day window across all rules combined? | Why: Without a cross-rule cap, a customer buying frames, having an Rx expiring, and having a birthday in the same week could receive 5+ messages in one week from your store. A cross-rule cap prevents this. | Options: (a) No cross-rule cap — rely on per-rule cooldown only; (b) Cap at 3 messages per customer per 30-day period across all rules; (c) Cap at 5 messages per customer per 30-day period.
# Feature #45: Mandatory "Walkout" Module (Physical Abandoned Cart)
META: effort=M days=5 risk=LOW roi=4 quickwin=yes deps=none phase=3

## Existing overlap
IMS already has the Phase 1 walkout intake foundation built:
- `backend/api/routers/walkouts.py` — POST create with schema (customer_name, mobile, age_group, gender, product_interested, has_prescription, displayed_price_range, required_price_range, primary/secondary_walkout_reason, brand_interest, competitor_mentioned, purchase_planned_in, sales_person_id, action_remarks, date), auto-creates skeleton customer when mobile is supplied, writes audit row.
- `frontend/src/pages/walkouts/WalkoutIntakeModal.tsx` — form with enum dropdowns for age_group, gender, reason, purchase_plan.
- `frontend/src/pages/walkouts/WalkoutsDashboardPage.tsx` — list + summary by reason, Phase 3 follow-up pipeline stubbed.
- `frontend/src/pages/walkouts/WalkoutDetailPage.tsx` — detail view stub.
- `follow_ups` collection + `backend/api/routers/follow_ups.py` — type/status/date filters, store-scoped, summary endpoint exists.
- MEGAPHONE agent (megaphone.py) already handles rx_expiry, birthday, walkout types via `notification_logs`.
- TASKMASTER agent (taskmaster.py) already auto-creates and escalates tasks per SLA role-ladder.

What is MISSING for the feature spec:
1. Items browsed/tried (SKU-level cart) not captured — only `product_interested` (free text or enum).
2. No "mandatory before next customer" enforcement (no POS-level gate).
3. No automatic follow-up task creation 3 days post-walkout.
4. No VIP/luxury trigger filter for escalated handling.
5. No staff accountability report (walkout-logging completion rate).

## Reuse (extend, don't rebuild)
- `backend/api/routers/walkouts.py` — extend POST /walkouts to accept `items[]` (SKU + product_name + mrp + tried_duration) and `vip_flag`; add GET list, GET summary with staff leaderboard.
- `backend/api/routers/follow_ups.py` — extend to accept `source=WALKOUT` + `walkout_id` linkage; reuse POST create follow-up endpoint for auto-scheduling.
- `backend/agents/implementations/taskmaster.py` — extend `_background_work()` to scan recent walkouts with no follow-up task and create them (mirrors existing SLA escalation pattern).
- `backend/agents/implementations/megaphone.py` — extend `_scan_walkouts()` (walkout_recovery campaign type already referenced in campaign_segments.py winback segment) to send WhatsApp/SMS on day 3.
- `frontend/src/pages/walkouts/WalkoutIntakeModal.tsx` — extend form to add product item rows (SKU search or free-entry) and VIP toggle.
- `frontend/src/pages/walkouts/WalkoutsDashboardPage.tsx` — extend with staff accountability panel (who logged, who didn't, conversion rate).
- `walkouts` collection — add new fields (see Data model below).
- `follow_ups` collection — no schema change needed; `walkout_id` linkage is a new optional field.

## Data model
**Extend `walkouts` collection** (new fields on existing docs):

```
items: [
  {
    sku: str | null,          # null = tried item not in catalog (e.g. competitor product)
    product_name: str,        # free text if no SKU
    mrp: float | null,
    brand: str | null,
    category: str | null,     # LUXURY / PREMIUM / MASS etc.
    tried: bool,              # physically tried (worn/fitted) vs just enquired
    tried_duration_min: int | null
  }
]
vip_flag: bool                # associate manually marks customer as VIP / high intent
follow_up_task_id: str | null # set when auto-task is created (idempotency)
follow_up_sent_at: datetime | null  # when MEGAPHONE dispatched the message
converted_order_id: str | null      # set if walkout converts to order (closure loop)
```

**No new collection needed** — follow_ups already has `type`, add `walkout` to its allowed enum; `source_id` or `walkout_id` as optional reference field.

## Backend

- `PATCH /walkouts/{walkout_id}` — edit walkout (items, vip_flag, action_remarks) within same business day; stamps `updated_by`/`updated_at`; blocked after 24h (immutable for audit).
- `GET /walkouts` — list with filters: store_id, date range, sales_person_id, has_items, vip_flag, converted; pagination.
- `GET /walkouts/summary` — staff accountability report: per-staff walkout count, item-capture rate (% logs with items[]), conversion rate (converted_order_id set), avg mrp of lost items. Date range param.
- `POST /walkouts/{walkout_id}/convert` — mark walkout as converted; accepts `order_id`; stamps `converted_order_id` + `converted_at`; closes linked follow_up task.
- Extend `POST /walkouts` — accept `items[]` + `vip_flag`; after creating walkout doc, call `_maybe_create_followup_task(walkout_id, vip_flag, mobile)` to immediately schedule the follow-up (3-day offset, or 1-day for VIP if owner decides). Return `follow_up_task_id` in response.
- Extend TASKMASTER sweep — add `_auto_schedule_walkout_followups()`: query walkouts where `follow_up_task_id=null` and `created_at < now - 1h` (grace for associate to add items), auto-create follow_up doc, stamp `follow_up_task_id` on walkout (idempotent via findOneAndUpdate).

## Frontend

- `WalkoutIntakeModal.tsx` — extend with:
  - Product row builder: search bar (calls `/catalog/products?q=` for SKU lookup) + manual free-text fallback; each row shows name, MRP, tried toggle, tried-duration input; add/remove rows; max 10 items.
  - VIP toggle (single checkbox, label "High-value customer / VIP").
  - Submit disables for 2s after click (prevent double-submit).
  - On success: toast "Walkout logged. Follow-up scheduled in 3 days." (or 1 day for VIP, per owner decision).
- `WalkoutsDashboardPage.tsx` — extend with:
  - Staff accountability panel: table of associates, walkout count, % with items captured, conversion %. STORE_MANAGER and above see all staff; SALES_STAFF/CASHIER see own row only.
  - Conversion funnel card: walkouts this month → follow-up sent → converted.
  - Filter by VIP only.
- `WalkoutDetailPage.tsx` — extend to show items[], VIP badge, linked follow-up task status, "Mark Converted" button (links to order search).
- No new pages needed; all extension of existing stubs.

## Business rules

- Walkout log is **mandatory before next POS transaction** only if owner enables the enforcement gate (see Owner Decisions). Default: strongly encouraged (banner) but not a hard POS block — POS is revenue-critical.
- Items are optional but staff accountability score penalises logs with zero items (visible in summary report).
- VIP flag on a walkout with any LUXURY category item auto-escalates follow-up priority to HIGH.
- Follow-up task auto-created by TASKMASTER exactly once per walkout (`follow_up_task_id` idempotency guard); never re-created after closure.
- Follow-up task SLA: 3 days for standard, 1 day for VIP (owner-configurable). After SLA breach, TASKMASTER escalates per existing role-ladder (SALES_STAFF → STORE_MANAGER → AREA_MANAGER).
- Walkout doc is immutable after 24h (only `converted_order_id` can be set after that).
- `converted_order_id` linkage closes the follow-up task automatically (status → COMPLETED, outcome = CONVERTED).
- MEGAPHONE sends WhatsApp/SMS to customer on day 3 only if `mobile` was captured and `marketing_consent=true`; respects quiet hours (21:00–09:00 IST).
- Staff cannot log a walkout for themselves as the customer (sales_person_id ≠ customer's resolved user_id).
- All state changes write to `audit_logs` (action=`walkout.items_added`, `walkout.converted`, `walkout.followup_created`).

## RBAC

| Role | Can log walkout | Can view all store walkouts | Can view staff summary | Can mark converted | Can edit after creation |
|---|---|---|---|---|---|
| SUPERADMIN / ADMIN | Yes | Yes (all stores) | Yes | Yes | Yes (any) |
| AREA_MANAGER | Yes | Yes (their stores) | Yes | Yes | Yes (24h) |
| STORE_MANAGER | Yes | Yes (own store) | Yes | Yes | Yes (24h) |
| SALES_CASHIER / SALES_STAFF | Yes | Own logs only | Own row only | Yes (own) | Yes (24h, own) |
| CASHIER / ACCOUNTANT | No | No | No | No | No |
| OPTOMETRIST / WORKSHOP_STAFF / CATALOG_MANAGER | No | No | No | No | No |

## Integrations

- **TASKMASTER agent** — extends `_background_work()` with `_auto_schedule_walkout_followups()` sweep; no new infrastructure.
- **MEGAPHONE agent** — extends walkout_recovery campaign type (already in campaign_segments.py winback logic); sends day-3 WhatsApp/SMS via MSG91 (DISPATCH_MODE-gated, fail-soft).
- **MSG91 WhatsApp** — reuses existing template infra; add `WALKOUT_RECOVERY` template to `notification_templates` collection (already referenced in notifications service defaults).
- No Shopify / Razorpay / Tally impact.

## Risk notes

- **POS enforcement gate** is the only real risk: if owner enables mandatory pre-sale walkout logging, a bug in the enforcement check could block revenue. Must ship behind an off-by-default feature flag (`walkout_enforcement_enabled` in `business_settings`) and be store-manager toggleable, not hard-coded.
- SKU search in the modal calls the catalog API — ensure it is debounced and does not slow down the post-sale floor rush (400ms debounce, max 8 results, graceful empty state).
- MEGAPHONE WhatsApp on day 3 requires `mobile` to be captured at intake; associates will skip mobile for browse-only customers — the system must degrade gracefully (skip send, log `no_mobile_skipped`).
- Double-submit on the intake form during a busy floor (associate taps Submit twice) — the 2s disable + backend idempotency key (hash of `sales_person_id + date + first product_interested`) prevents duplicate docs.
- No POS or accounting money flows touched; risk is LOW overall.

## Recommendation

Build now (quick win) — Phase 1 foundation is already 60% done (schema, modal, dashboard stub, follow_ups collection, MEGAPHONE walkout_recovery hook all exist). Remaining work is the items[] extension, TASKMASTER auto-scheduler, staff accountability panel, and VIP escalation. Delivers immediate floor accountability and structured recovery pipeline with no POS/accounting risk.

## Owner decisions

- Q: Should walkout logging be **mandatory before the next POS sale** (hard block) or **strongly encouraged** (banner/badge)? | Why: Hard block requires a POS gate — POS is revenue-critical and a bad deploy could block sales; encouraged mode has no revenue risk but lower compliance. | Options: (a) Hard block — associate cannot open a new POS session until walkout is logged for the previous customer; (b) Soft block — banner + daily compliance score visible to manager but no transaction block; (c) Manager-controlled per-store toggle (ship as soft, manager can escalate to hard for their store).

- Q: What is the follow-up delay for a **standard walkout vs a VIP/luxury walkout**? | Why: This sets the TASKMASTER auto-scheduler offset and MEGAPHONE send timing. | Options: (a) 3 days standard, 1 day VIP (same day for luxury ≥₹X MRP); (b) 2 days standard, same day VIP; (c) Configurable per store in settings.

- Q: What MRP threshold (or which brands/categories) automatically sets `vip_flag=true` even if the associate does not tick it? | Why: Associates may forget to flag VIP customers; an auto-flag rule on item MRP or brand prevents missed escalations. | Options: (a) Auto-flag when any item MRP ≥ ₹15,000; (b) Auto-flag when brand is in luxury-brand list (Cartier/Chopard/Bvlgari/Gucci/Prada/Versace/Burberry); (c) No auto-flag — associate judgment only; (d) Both (a) and (b).

- Q: Should the **WhatsApp follow-up message** name the specific items the customer tried, or be a generic "we noticed you visited" message? | Why: Naming items (e.g., "the Ray-Ban Aviator you tried") is highly personalised and converts better, but requires items[] to be captured and a more complex MSG91 template. | Options: (a) Personalised with item names if items[] captured, generic fallback if not; (b) Always generic (simpler template, lower compliance risk); (c) No WhatsApp — only an internal task for the associate to call the customer manually.

- Q: Who owns the **conversion credit** when a walkout converts to a sale — the original associate who logged the walkout, or the associate who closes the sale? | Why: This affects staff incentive points (daily scorecard) and commission attribution if commission is later built. | Options: (a) Original associate who logged the walkout; (b) Closing associate; (c) Split 50/50; (d) No attribution — walkout conversion is store-level metric only.
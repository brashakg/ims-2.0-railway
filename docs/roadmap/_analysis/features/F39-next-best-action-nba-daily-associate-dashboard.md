# Feature #39: "Next Best Action" (NBA) Daily Associate Dashboard
META: effort=M days=6 risk=LOW roi=4 quickwin=yes deps=none phase=3

## Existing overlap
IMS already has substantial infrastructure this feature can build on directly:

- **CRM segmentation** (`backend/api/routers/crm.py`): `_perform_rfm_segmentation()`, `_identify_churn_risk_customers()`, `_determine_lifecycle_phase()` — already classifies customers as Champions/At Risk/VIP/Lost with recency/frequency/monetary signals.
- **Prescription expiry detection** (`backend/database/repositories/prescription_repository.py`: `find_expiring_soon()`) and `campaign_segments.py` rx_expiry segment (90/30/7-day windows) — already queries `prescriptions` collection for expiring Rx.
- **Contact-lens refill signal** (`backend/api/routers/crm.py`: `/customers/{id}/cl-refill-status`) — already computes `days_remaining` to refill by modality (DAILY/MONTHLY/BIWEEKLY).
- **Customer 360 view** (`frontend/src/pages/customers/Customer360Dashboard.tsx`) — already surfaces LTV, loyalty tier, churn risk, prescriptions, interactions per customer.
- **Follow-up task system** (`backend/api/routers/follow_ups.py`, `follow_ups` collection) — already has `type` (eye_test_reminder/frame_replacement/general), `scheduled_date`, `status` (pending/completed/skipped), `outcome`, assignable to staff.
- **Walkout intake** (`backend/api/routers/walkouts.py`, `walkouts` collection) — already captures `purchase_planned_in` enum and `sales_person_id`, creating a recovery signal.
- **Order history** (`orders` collection with `customer_id`, `created_at`, `items[].brand`, `items[].category`, `grand_total`) — anniversary/upgrade signals derivable from purchase date + category.
- **Custom CRM tags** (`customers` collection) — no `tags[]` field yet; this is the one schema gap to close.
- **MEGAPHONE agent** (`backend/agents/implementations/megaphone.py`) — already runs birthday/Rx-expiry scans on a 30-min tick, queues WhatsApp/SMS via `notification_logs`. NBA scores can be a new scan added to this agent's tick.
- **In-app bell** (`backend/api/routers/notifications.py`, `notifications` collection) — already supports per-user notifications with snooze.
- **Store scoping and RBAC** (`backend/api/services/rbac_policy.py`) — SALES_STAFF / SALES_CASHIER / STORE_MANAGER roles and `validate_store_access` pattern are in place.

## Reuse (extend, don't rebuild)
- **`crm.py` router** — add `GET /api/v1/crm/nba/{store_id}` endpoint that aggregates signals; do not create a new router file.
- **`campaign_segments.py`** — reuse `_get_rx_expiring()` and birthday segment logic as sub-signals; import, don't duplicate.
- **`follow_ups` collection** — NBA card creation writes a new follow-up doc (type=`nba_call`, status=`pending`); completes / skips use the existing PATCH endpoint. No new collection needed for task tracking.
- **`customers` collection** — extend with a `tags: list[str]` field (e.g. `["VIP", "Cartier buyer", "Call this week"]`). No new collection; add field to `CustomerUpdate` schema in `customers.py`.
- **`crm.py` customer 360 enrichment** — the NBA API response reuses the same enrichment helpers (`_determine_lifecycle_phase`, `_identify_churn_risk_customers`) already in crm.py; no new DB joins.
- **`Customer360Dashboard.tsx`** — add an "NBA Prompt" card to this existing page; the associate clicks a customer name from the daily list and lands here.
- **MEGAPHONE agent** (`megaphone.py`) — add `_score_nba_daily()` method to the existing 30-min tick; it pre-computes and caches today's top-N per store into a new `nba_scores` collection so the API endpoint is a fast read, not a live aggregation.

## Data model
**New collection: `nba_scores`** (pre-computed daily, one doc per store per day)
```
{
  "_id": ObjectId,
  "store_id": str,
  "date": "YYYY-MM-DD",         # date string (IST) for the day
  "generated_at": datetime,
  "cards": [                    # top N cards, already sorted by score desc
    {
      "rank": int,
      "customer_id": str,
      "customer_name": str,
      "customer_mobile": str,
      "signals": ["rx_expiry_7d", "cl_refill_due", "anniversary_today"],
      "score": float,           # 0–100, computed internally, NOT shown to associate
      "headline": str,          # "Rx expires in 5 days — call to book eye test"
      "sub_headlines": [str],   # ["Last visited 4 months ago", "CL refill due tomorrow"]
      "suggested_action": str,  # "BOOK_EYE_TEST" | "CL_REORDER" | "UPGRADE_CALL" | "ANNIVERSARY_CALL" | "WINBACK_CALL" | "GENERAL_FOLLOWUP"
      "loyalty_tier": str,
      "lifetime_value": float,
      "last_purchase_date": datetime | null,
      "last_purchase_brand": str | null,
      "tags": [str],            # from customers.tags[]
      "follow_up_id": str | null  # linked follow_up doc if pre-created
    }
  ],
  "ttl_expires_at": datetime    # set to end of today (IST midnight); MongoDB TTL index drops old docs
}
```

**`customers` collection — add field:**
```
"tags": list[str]   # free-form, e.g. ["VIP", "Cartier buyer", "call this week"]
```

**`follow_ups` collection — add type value:** `"nba_call"` (extend the existing type enum; no schema migration needed in MongoDB).

**Index on `nba_scores`:** `{store_id: 1, date: 1}` unique; TTL index on `ttl_expires_at`.

## Backend

**`GET /api/v1/crm/nba/{store_id}?date=YYYY-MM-DD`** (new, in `crm.py`)
- Returns today's pre-computed `nba_scores` doc for the store. If no doc exists for today (first call before MEGAPHONE has run, or forced refresh), triggers synchronous computation with a cap of 200 customers scanned (fast path). Response: `{generated_at, cards[], store_id, date}`.
- Store-scoped: validates caller has access to `store_id` via existing `validate_store_access`.
- Roles: SALES_STAFF, SALES_CASHIER, STORE_MANAGER, AREA_MANAGER, ADMIN, SUPERADMIN.

**`POST /api/v1/crm/nba/{store_id}/dismiss`** (new, in `crm.py`)
- Body: `{customer_id, reason: "not_interested"|"already_called"|"no_answer"|"wrong_number"}`.
- Writes outcome to linked `follow_ups` doc (status=`skipped`, outcome=reason) and removes the card from that store's `nba_scores.cards[]` for today.
- Audit: writes to `audit_logs` (action=`nba.dismissed`, entity_type=`customer`).

**`POST /api/v1/crm/nba/{store_id}/complete`** (new, in `crm.py`)
- Body: `{customer_id, outcome_notes, follow_up_scheduled_date?}`.
- Marks linked follow-up as `completed`; if `follow_up_scheduled_date` provided, creates a NEW follow-up for that date.

**`PATCH /api/v1/customers/{customer_id}/tags`** (new, in `customers.py`)
- Body: `{tags: list[str]}`. Replaces `customers.tags[]`. Validates: max 10 tags, each max 50 chars, no HTML. Roles: STORE_MANAGER, AREA_MANAGER, ADMIN, SUPERADMIN.
- Audit: `audit_logs` entry with before/after.

**MEGAPHONE agent addition** (`megaphone.py: _score_nba_daily()`):
- Runs once per day at a configurable time (default 07:30 IST, before store opens) as part of the existing daily 09:00 scan.
- For each active store: pulls all customers with `home_store_id` or last-order at store in last 18 months (cap: 500 customers per store per run to stay under tick budget).
- Computes a score per customer by summing weighted signals (weights are constants, not exposed to owner as config — owner decides threshold policy only):

| Signal | Weight | Source |
|---|---|---|
| Rx expiry ≤ 7 days | 30 | `prescriptions.expiry_date` |
| CL refill due ≤ 3 days | 25 | `crm._cl_refill_status()` |
| Birthday today | 20 | `customers.dob` |
| Purchase anniversary today (±3 days) | 15 | `orders.created_at` |
| Winback: no purchase ≥ 180 days, was active | 12 | `orders` recency |
| VIP tag present (`customers.tags`) | 8 bonus | `customers.tags` |
| Rx expiry 8–30 days | 10 | `prescriptions.expiry_date` |
| CL refill due 4–14 days | 10 | |
| Loyalty tier GOLD/PLATINUM | 5 bonus | `loyalty_accounts.tier` |

- Takes top 10 per store (configurable via owner decision, see below), writes `nba_scores` doc, creates corresponding `follow_ups` docs (type=`nba_call`, status=`pending`, scheduled_date=today).
- Idempotent: if today's `nba_scores` doc already exists, skips (re-run safe).
- Fail-soft: any per-store failure logged to `agent_errors`, does not block other stores.

## Frontend

**New page: `frontend/src/pages/crm/NBADashboardPage.tsx`**
- Route: `/crm/nba` (add to `App.tsx` under existing CRM group).
- Shows today's date (IST) and store selector (for STORE_MANAGER+; SALES_STAFF sees own store only).
- Renders a ranked list of up to 10 cards (restrained, light-only):
  - Customer name + mobile (tap-to-call on mobile).
  - Headline in sentence case, single line (e.g. "Rx expires in 5 days — book eye test").
  - Up to 2 sub-headlines in smaller muted text.
  - Loyalty tier chip (BRONZE/SILVER/GOLD/PLATINUM) using existing badge pattern.
  - Tags as small neutral chips (no colour, just outline).
  - Two action buttons per card: "Done" (opens quick outcome modal) and "Skip" (one-tap dismiss with reason select).
  - Clicking the customer name navigates to existing `Customer360Dashboard` for that customer.
- Empty state: "All caught up — no calls needed today." (no emoji).
- Pull-to-refresh / manual refresh button (re-fetches from API; does not re-score, just re-reads cache).

**Extend `Customer360Dashboard.tsx`:**
- Add a "Tags" row in the customer header section with editable chips (STORE_MANAGER+ can click to edit tags inline via the new PATCH endpoint).
- No other changes to 360 view needed.

**Extend `FollowUpDashboard.tsx`:**
- Add `nba_call` to the type filter dropdown so managers can see NBA-generated calls in the existing follow-up list. Label: "NBA Call".

**Sidebar nav:** Add "Daily Calls" item under the existing CRM group (visible to SALES_STAFF, SALES_CASHIER, STORE_MANAGER+). Use a neutral icon (phone or list), no emoji.

## Business rules
- Cards are read-only ranked; associates cannot reorder them (rank reflects score, not preference).
- "Done" outcome is required before a card can be marked complete — free-text `outcome_notes` mandatory (min 10 chars). This prevents fake completions.
- "Skip" requires selecting a reason from the fixed enum; reason stored on the follow-up doc (audit trail).
- A customer dismissed today does not reappear until the next day's NBA run (enforced by removing from `nba_scores.cards[]` and not re-inserting within same `date`).
- Tags are store-visible (any user at the store can see tags on Customer360); only STORE_MANAGER+ can set/remove tags.
- Tags must not contain phone numbers, emails, or GSTIN (server-side regex strip before save).
- NBA score value is internal-only — it is stored in `nba_scores` but never returned in API responses (only rank is exposed). This prevents gaming.
- `nba_scores` docs auto-expire at end of day (MongoDB TTL on `ttl_expires_at` = IST midnight). Historic NBA scores are not queryable; follow-up completion history is the durable record.
- No WhatsApp or SMS is sent by NBA itself. If the associate decides to send a message, they use the existing manual send path (`/notifications/send` or WhatsApp direct from Customer360). NBA is a call-prioritisation tool only.
- Audit: every dismiss and complete action written to `audit_logs`.

## RBAC
- **SALES_STAFF, SALES_CASHIER**: read own store's NBA list; can mark done/skip; cannot edit tags.
- **STORE_MANAGER, AREA_MANAGER**: read any store in their scope; can mark done/skip; can edit tags; can see which associate completed/skipped each card (via linked follow-up `completed_by` field).
- **ADMIN, SUPERADMIN**: full read/write across all stores; can see aggregate completion rate per store.
- **ACCOUNTANT, OPTOMETRIST, WORKSHOP_STAFF, CATALOG_MANAGER**: no access (NBA route not in their role set).

## Integrations
- **MEGAPHONE agent**: NBA scoring is added as a new method in the existing MEGAPHONE tick — no new agent or scheduler needed.
- **No MSG91/Shopify/Razorpay/Tally involvement**: NBA is a read + prioritise + record-outcome flow. Outbound communication (if associate decides to call or WhatsApp) uses existing paths outside NBA.
- **Jarvis / ORACLE**: future enhancement — ORACLE can add an upgrade-signal (e.g. "bought Cartier 2 years ago, suggest premium frame") as an additional NBA sub-headline by writing to the signal inputs before MEGAPHONE scores; not in scope for this phase.

## Risk notes
- **No POS or money risk**: NBA only reads data and writes follow-up/audit docs. No order creation, payment, or price change occurs. No feature flag required.
- **MEGAPHONE tick budget**: adding NBA scoring to the 07:30 scan must respect the existing tick timeout. Cap of 500 customers per store per run and a 200-customer fast-path for synchronous fallback keeps latency under control. If a store has >500 active customers, score only the highest-recency cohort (last-order ≤ 18 months); add a warning log.
- **Tag abuse**: free-form tags can contain discriminatory or PII content. Server-side strip of phone/email/GSTIN regex is not foolproof. Owner must set a tag usage policy for staff (business decision, not a build constraint).
- **Stale scores on fresh deploy**: if MEGAPHONE hasn't run yet for the day (e.g. early-morning deploy), the synchronous fallback in the API endpoint covers it — but with a 200-customer cap it may miss low-frequency customers. This is acceptable for an advisory tool.
- **Follow-up doc proliferation**: MEGAPHONE creates up to 10 follow-up docs per store per day. With 6 stores that is 60 docs/day, well within MongoDB capacity. TTL on `nba_scores` keeps that collection lean; `follow_ups` docs persist (durable record of calls made).

## Recommendation
Build now (quick win). All scoring signals, data collections, and dispatch infrastructure already exist. The net-new code is: one scoring method in MEGAPHONE, one new `nba_scores` collection with TTL, three new API endpoints in `crm.py`, one `tags[]` field on `customers`, and one new frontend page. Estimated 4–6 days. ROI is high: surfaces the highest-value outbound call for each associate every morning without requiring them to triage the CRM manually, directly increasing conversion on Rx renewals, CL reorders, and VIP upgrades.

## Owner decisions
- Q: How many cards should each associate see per day? | Why: Determines the top-N cutoff in MEGAPHONE scoring and the UI list length. Too many (>15) overwhelms; too few (<5) misses opportunities. | Options: 5 (focused, high-quality) / 10 (default recommended) / 15 (high-volume stores with dedicated outreach staff)
- Q: Should STORE_MANAGER see each associate's completion rate (who called, who skipped) as a daily report, or just the aggregate? | Why: Associate-level tracking enables coaching but may feel like surveillance. Affects whether the `completed_by` field is surfaced in a manager view. | Options: Associate-level visible to STORE_MANAGER+ / Aggregate only (store total done vs skipped) / No tracking beyond follow-up history
- Q: Should VIP customers (e.g. Cartier buyers, high-LTV) be given a fixed top-3 slots regardless of signal score, or purely score-ranked? | Why: A fixed VIP slot guarantees premium customers are always called; pure scoring may bury them on days with many Rx expiries. | Options: Reserve top 2 slots for customers with tags containing "VIP" or LTV > threshold / Pure score rank always / Manager-configurable per store
- Q: What LTV threshold qualifies a customer as "high-value" for the upgrade-call signal? | Why: The purchase-anniversary upgrade signal should fire for premium buyers (e.g. spent > ₹15,000 on a single frame), not for everyday frame replacements. This threshold sets which anniversaries surface. | Options: ₹10,000 / ₹15,000 / ₹25,000 / category-based (LUXURY always, PREMIUM above ₹8,000)
- Q: Should associates be able to add custom tags on their own, or only STORE_MANAGER+ sets tags? | Why: If associates can tag customers themselves, tags reflect ground-level intelligence ("very price-sensitive", "prefers Ray-Ban") but quality varies. If only managers tag, quality is higher but coverage is lower. | Options: SALES_STAFF can add/remove own tags / Only STORE_MANAGER+ can tag / SALES_STAFF can suggest tags, STORE_MANAGER approves
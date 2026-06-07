# Feature #51: "Use-It-Or-Lose-It" Benefits Campaign
META: effort=M days=6 risk=MED roi=4 quickwin=no deps=none phase=3

## Existing overlap
- **Campaign engine** (`backend/api/routers/campaigns.py`): DRAFT→SCHEDULED→ACTIVE→COMPLETED lifecycle, segment targeting, ONE_TIME / RECURRING / TRIGGERED schedule kinds, WhatsApp/SMS/EMAIL channels — the entire dispatch infrastructure is live.
- **Segment resolver** (`backend/api/services/campaign_segments.py`): 6 predefined segments including `by_customer_type` (B2B/B2C) and `recent_buyers`; the resolver pattern is the exact extension point needed.
- **Notification dispatch** (`notification_service.py`, MEGAPHONE agent): queues PENDING rows in `notification_logs`, drains 60/tick, respects DND 21:00–09:00 IST, DISPATCH_MODE gated — no new infrastructure required.
- **Customer schema** (`customers` collection): `customer_type` (B2B/B2C), `marketing_consent`, `home_store_id`, embedded `patients[]` — audience targeting is already field-available.
- **CRM 360 view** (`crm.py`): lifecycle phases (active/at_risk/VIP), LTV field, last-purchase recency — benefit-eligibility scoring can reuse these signals.
- **Loyalty + Store Credit** (`loyalty.py`, `credit_note_ledger`): if the campaign offers a bonus incentive on redemption, these are the exact collections to debit/credit.
- **Finance period lock** (`finance.py:446-481`): Dec 31 FY boundary awareness is already in the codebase; the campaign must fire before period closure.

## Reuse (extend, don't rebuild)
- `backend/api/services/campaign_segments.py` — add `benefits_expiry` segment type alongside existing six; resolver logic follows the same `{customer_id, phone, name, variables{}}` contract
- `backend/api/routers/campaigns.py` — no changes needed; campaign CREATE with `segment_key="benefits_expiry"` works today once the segment is registered
- `notification_templates` collection — add `BENEFITS_EXPIRY_NOV`, `BENEFITS_EXPIRY_DEC`, `BENEFITS_EXPIRY_FINAL` template docs (same upsert pattern as `RX_EXPIRY_90`)
- MEGAPHONE agent `_dispatch_scheduled_campaigns()` — already dispatches SCHEDULED campaigns; a Nov 1, Dec 1, Dec 20 triple-schedule covers the cadence without agent changes
- `backend/api/routers/crm.py` `_determine_lifecycle_phase()` — reuse `VIP` and `active` lifecycle checks to exclude already-purchased Q4 customers from follow-up waves
- `backend/api/routers/customers.py` B2B customer search — `customer_type=B2B` + `marketing_consent=true` filter is a two-line query extension

## Data model
- **New field on `customers`**: `benefits_config: {benefit_year: int, benefit_cap_inr: float, benefit_used_inr: float, benefit_provider: str, renewal_month: int}` — owner-entered per B2B account; optional (B2C customers have no field)
- **New collection `benefit_utilisation_log`**: `{log_id, customer_id, store_id, order_id, amount_applied, logged_at, logged_by}` — append-only; used to compute `benefit_used_inr` without scanning all orders; auditable
- **New field on `notification_templates`**: `campaign_tag: "benefits_expiry"` — allows analytics to bucket these sends separately from Rx-expiry sends in `notification_logs`
- No changes to `orders`, `loyalty_accounts`, or `credit_note_ledger` — benefit tracking is advisory, not a tender type

## Backend
- `POST /api/v1/customers/{customer_id}/benefits-config` (ADMIN/STORE_MANAGER) — upsert `benefits_config` sub-doc; validates `benefit_cap_inr > 0`, `renewal_month 1-12`; writes audit row
- `GET /api/v1/customers/{customer_id}/benefits-config` (ADMIN/STORE_MANAGER/SALES_STAFF) — returns current config + computed `benefit_remaining_inr` (cap minus sum of `benefit_utilisation_log` for the year)
- `POST /api/v1/customers/{customer_id}/benefits-config/log-usage` (STORE_MANAGER/SALES_CASHIER) — appends to `benefit_utilisation_log`, recomputes `benefit_used_inr` on customer doc via `find_one_and_update`; called manually at POS or auto-triggered on order confirmation (see Risk notes)
- `GET /api/v1/crm/benefits-expiry-audience` (ADMIN/SUPERADMIN) — dry-run audience preview: B2B customers with `marketing_consent=true`, `benefit_remaining_inr > threshold`, `renewal_month=12` (or parameterised), optionally filtered by store/brand; returns count + sample rows; this is what the segment resolver calls internally
- Extend `campaign_segments.py`: register `benefits_expiry` key → calls `benefits-expiry-audience` aggregation, returns `variables: {benefit_remaining, customer_name, store_name, expiry_date}` for template interpolation
- `POST /api/v1/campaigns` (existing endpoint, no changes) — campaign creator selects `segment_key="benefits_expiry"` from dropdown; schedules three ONE_TIME sends at Nov 1 / Dec 1 / Dec 20 09:30 IST

## Frontend
- **`CustomerDetailPage` or `Customer360Dashboard`** — add "Benefits" tab (existing tabs: Overview, Rx, Orders, Loyalty, Consent); shows `benefit_cap_inr`, `benefit_used_inr`, `benefit_remaining_inr` as three neutral stat cards; "Log Usage" button opens a minimal amount-entry modal (STORE_MANAGER+ only)
- **`CampaignCreate` flow** (`frontend/src/pages/marketing/`) — benefits_expiry appears in the segment dropdown; selecting it shows a live audience count card ("N corporate clients with remaining benefits"); campaign preview renders the interpolated template with `{benefit_remaining}` variable highlighted
- **`BenefitsAudiencePage`** (new, minimal) — at `/crm/benefits-audience`; table of B2B accounts with columns: customer name, contact, store, benefit cap, used, remaining, last-purchase date; filter by store; export CSV; no charts (restrained executive UI); ADMIN/SUPERADMIN only
- **`OnboardBenefitsModal`** — triggered from Customer 360 "Benefits" tab when `benefits_config` is absent; two numeric fields (cap amount, renewal month) + provider name; single Save; no wizard

## Business rules
- `marketing_consent=true` is a hard gate — no send regardless of benefit balance (enforced in existing `campaigns.py:710-717`, no changes needed)
- DND 21:00–09:00 IST respected by MEGAPHONE — no override for this campaign
- `benefit_remaining_inr` floor: do not send if remaining < owner-defined minimum (e.g., ₹500); configurable per campaign via a custom `min_benefit_remaining` field on the segment resolver call — owner sets this at campaign create time, stored on the campaign doc
- `benefit_used_inr` is advisory, not a ledger tender; it does not block orders or change invoice amounts; it is informational for the sales staff at POS
- Benefit logging is idempotent on `order_id` — if the same order triggers two log-usage calls, the second is a no-op (unique index on `{customer_id, order_id}` in `benefit_utilisation_log`)
- Audit trail: every `benefits-config` write and every `log-usage` call is written to `audit_logs` (standard pattern)
- Period lock awareness: campaign send dates (Nov, Dec) are before year-end close; log-usage calls on orders in a locked period are blocked by existing `check_period_locked` guard (reuse, no change)

## RBAC
- SUPERADMIN, ADMIN: full access (config, audience view, campaign create, CSV export)
- AREA_MANAGER, STORE_MANAGER: read + log-usage for their own store's customers; cannot view cross-store audience
- SALES_CASHIER, SALES_STAFF: read-only benefit balance on Customer 360 (so they can mention remaining benefits at POS); no config, no log-usage
- ACCOUNTANT: read-only on benefit utilisation log (for reconciliation); no config writes
- All other roles: no access

## Integrations
- **MSG91 (WhatsApp + SMS)** via MEGAPHONE — primary dispatch channel; existing DISPATCH_MODE gate applies; no new provider setup
- **Jarvis / ORACLE** — optional: ORACLE's `_detect_discount_abuse()` pattern can be reused to flag unusual benefit-usage spikes (e.g., one customer logging ₹50k in one day); surfaces as an advisory proposal, not a block
- No Shopify, Razorpay, Tally, or Shiprocket involvement for MVP

## Risk notes
- **Auto-trigger on order confirmation is the main risk**: automatically calling `log-usage` when a B2B order is confirmed touches `orders.py` (revenue-critical POS router); this must go behind a feature flag (`BENEFITS_AUTO_LOG=false` default) and ship as a Phase 2 addition; MVP uses manual "Log Usage" button only
- **Benefit_used_inr drift**: if the manual log-usage is forgotten, the balance shown is wrong; position this explicitly as "advisory tracking" in the UI label to avoid owner expectation of perfect accuracy
- **Segment size estimation**: corporate (B2B) customer count in IMS may be small; campaign ROI depends on store having an active corporate sales program; worth confirming audience size before building the full segment resolver
- **Template variable interpolation**: `notification_service.py:populate_template()` uses simple `{key}` replacement; `benefit_remaining` must be formatted as `₹{amount:,.0f}` in the resolver before passing to the template to avoid raw float display; small but required change in the resolver

## Recommendation
Build later — not a quick win. The campaign dispatch infrastructure is ready today, but the benefit-config data model and manual log-usage workflow need to be in place first, and auto-trigger requires a feature flag against the POS router. Recommend building the data model + Customer 360 "Benefits" tab as a Phase 3 addition immediately after the BVI merge stabilises, then running the first campaign manually via the existing campaign builder within two weeks of data entry completing.

## Owner decisions
- Q: Which customers should be enrolled — only B2B accounts, or also high-LTV B2C individuals who have insurance reimbursement? | Why: changes the segment filter (customer_type=B2B only vs LTV threshold on B2C); affects audience size and template copy | Options: (a) B2B only — simpler, clean corporate focus / (b) B2B + B2C with LTV ≥ ₹25k — wider reach but murkier "benefits" concept for retail customers / (c) owner manually tags eligible customers — most accurate but high maintenance
- Q: What is the minimum remaining benefit amount to trigger a campaign message (e.g., ₹500, ₹1000, ₹2000)? | Why: sets the `min_benefit_remaining` floor in the segment resolver; too low = noise, too high = missed revenue | Options: (a) ₹500 / (b) ₹1,000 / (c) ₹2,000 / (d) owner sets per-campaign at creation time
- Q: Should the campaign offer an incentive (e.g., extra loyalty points or a small discount) to customers who redeem before Dec 20, or is the "use it or lose it" message sufficient? | Why: if an incentive is added, it must be created as a voucher or loyalty bonus via the existing voucher/loyalty engine, adding 1 day of build; no incentive = simpler | Options: (a) no incentive — message-only / (b) bonus loyalty points on qualifying Q4 purchase / (c) a fixed-value discount voucher (e.g., ₹200 off ₹3,000+)
- Q: How is "benefit amount" determined — does your team manually enter a cap per corporate client, or is there an external corporate insurer / TPA portal that holds the real balance? | Why: if there is an external TPA portal, a future integration phase is needed; for MVP, manual entry is the only viable path | Options: (a) manual entry by store manager per client (MVP) / (b) CSV bulk upload of benefit caps at year start / (c) future TPA API integration (out of scope for this feature)
- Q: Which stores should this campaign cover for the first run — all stores, or only Better Vision locations, or only stores with an active corporate B2B sales program? | Why: determines default store filter on the audience page and the campaign segment query | Options: (a) all stores simultaneously / (b) pilot with one store first / (c) owner selects stores at campaign-create time each year
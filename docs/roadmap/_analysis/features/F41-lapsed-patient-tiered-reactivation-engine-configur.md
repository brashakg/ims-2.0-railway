# Feature #41: "Lapsed Patient" Tiered Reactivation Engine (Configurable)
META: effort=M days=4 risk=LOW roi=4 quickwin=no deps=none phase=3

## Existing overlap
Substantial overlap with existing infrastructure — this is an extension, not a greenfield build:

- **Winback segment** (`backend/api/services/campaign_segments.py`): `winback` segment already identifies customers with no order in 6+ months. Lapsed patient logic extends this with prescription-gap detection (no exam >2yr) and tiered escalation.
- **Campaign engine** (`backend/api/routers/campaigns.py`): RECURRING campaign type with `frequency` + `time_of_day` already exists. The two-tier send (Week1 → Week3 if ignored) maps to two distinct ONE_TIME campaigns triggered by the reactivation scheduler, or a TRIGGERED campaign with follow-up logic.
- **Rx expiry segment** (`campaign_segments.py`: `rx_expiry`, 90/30/7-day windows): The segment resolver already joins `prescriptions` to find patients with expiring/expired Rx. Lapsed logic uses the same join — extend with a `>2yr since last prescription_date or last order` filter.
- **MEGAPHONE agent** (`backend/agents/implementations/megaphone.py`): `_scan_rx_expiring()` and `_dispatch_scheduled_campaigns()` are the exact hooks where lapsed-patient scanning would live. Add `_scan_lapsed_patients()` alongside.
- **Notification dispatch** (`backend/api/services/notification_service.py`, `backend/api/routers/marketing.py`): Full SMS/WhatsApp pipeline with consent gate, quiet-hours, DND (21:00–09:00 IST), DISPATCH_MODE, DLT audit, and `notification_logs` persistence already exists.
- **Voucher/discount system** (`backend/api/routers/vouchers.py`, `redeem_voucher_atomic`): Atomic gift-card issuance exists. The 10%/20% incentives would mint vouchers from this system.
- **Campaign analytics** (`campaigns.py:223-298`): sent/delivered/failed/opened/converted already tracked in `notification_logs` + `campaign_audit`. "Ignored" = sent but no opened/converted within N days — readable from existing fields.
- **Luxury brand caps** (`backend/api/services/pricing_caps.py`): Cartier/Chopard/Bvlgari 2%, Gucci/Prada/Versace/Burberry 5% caps already enforced at order time. The exclude-Cartier (and full luxury-brand exclusion) rule is a segment filter, not a pricing override.

## Reuse (extend, don't rebuild)
- **`campaign_segments.py`** — add `lapsed_patient` segment resolver: join `prescriptions` (last `prescription_date` per patient) + `orders` (last `created_at` per customer), surface patients where BOTH are >N months ago (configurable, default 24 months). Respect `marketing_consent=True`.
- **`backend/api/implementations/megaphone.py`** — add `_scan_lapsed_patients()` tick method (alongside `_scan_rx_expiring`). Reads `lapsed_reactivation_settings` config, resolves lapsed segment, checks `notification_logs` for prior Week1 send, decides Week1 vs Week3 tier.
- **`campaigns.py`** (`create_campaign`, `send_campaign`) — reuse for the two campaign docs (LAPSED_W1, LAPSED_W3). No schema change needed; segment_key='lapsed_patient'.
- **`vouchers.py`** (`redeem_voucher_atomic`, `create_voucher`) — mint a single-use, store-scoped, time-limited voucher per patient when campaign sends. Voucher carries `campaign_id` + `customer_id` for attribution.
- **`notification_logs`** collection — "ignored" detection: query `notification_logs` where `campaign_id=LAPSED_W1`, `customer_id=X`, `created_at` > 14 days ago, `opened_at IS NULL` and `converted_at IS NULL`. No new collection needed.
- **`marketing.py`** quiet-hours + consent gate — already enforced on every send path; no additional work.
- **`pricing_caps.py` / `LUXURY_BRAND_CAPS`** — the existing brand list (`Cartier`, `Chopard`, `Bvlgari`, `Gucci`, `Prada`, `Versace`, `Burberry`) is the exclusion source. Segment resolver filters out customers whose last purchase was exclusively luxury-brand items — or, simpler and correct: the voucher discount applies to non-luxury categories only (voucher `applicable_categories` field on the voucher doc).
- **`backend/api/routers/campaigns.py`** analytics endpoints — reuse for reactivation dashboard (sent/converted per tier).

## Data model
New collection `lapsed_reactivation_settings` (singleton `{"_id": "default"}`):
```
{
  enabled: bool,                        # master on/off
  lapse_threshold_months: int,          # default 24 — no purchase AND no exam since N months
  week1_discount_pct: float,            # default 10.0
  week3_discount_pct: float,            # default 20.0
  week3_delay_days: int,                # default 14 — days after W1 with no open/convert
  voucher_validity_days: int,           # default 30
  applicable_stores: [store_id],        # [] = all stores
  excluded_categories: [str],           # e.g. ["LUXURY"] — voucher not valid here
  excluded_brands: [str],               # e.g. ["Cartier","Chopard","Bvlgari"] — owner-set
  channel: "WHATSAPP"|"SMS",            # primary channel
  template_w1_id: str,                  # references notification_templates
  template_w3_id: str,
  last_scan_at: datetime,
  updated_by: str,
  updated_at: datetime
}
```

New field on `notification_logs` docs (already a collection): `reactivation_tier: "W1"|"W3"|null` — stamped when MEGAPHONE sends a lapsed-patient message. Enables tier-level analytics without a separate collection.

New field on `vouchers` docs (already a collection): `applicable_categories: [str]` and `excluded_brands: [str]` — enforced at voucher redemption in `redeem_voucher_atomic` (add category/brand check before debit).

## Backend
- **`GET /api/v1/marketing/lapsed-reactivation/settings`** (SUPERADMIN/ADMIN) — returns current `lapsed_reactivation_settings` doc.
- **`PUT /api/v1/marketing/lapsed-reactivation/settings`** (SUPERADMIN only) — upserts settings; validates `week3_discount_pct >= week1_discount_pct`, `lapse_threshold_months >= 6`, brand/category lists against known enums. Writes audit log (before/after).
- **`GET /api/v1/marketing/lapsed-reactivation/preview`** (SUPERADMIN/ADMIN) — dry-run: returns count of currently lapsed patients per store, breakdown by W1-eligible vs W3-eligible, sample of 10 names (no PII beyond first name + store). Zero DB writes.
- **`POST /api/v1/marketing/lapsed-reactivation/trigger`** (SUPERADMIN only) — manual one-off trigger (bypasses MEGAPHONE schedule). Useful for testing. Gated on DISPATCH_MODE; returns job_id.
- **`GET /api/v1/marketing/lapsed-reactivation/analytics`** (SUPERADMIN/ADMIN/ACCOUNTANT) — aggregates `notification_logs` by `reactivation_tier`, returns per-tier sent/delivered/opened/converted counts + voucher redemption count (join `vouchers` on `campaign_id`).
- **Extend `campaign_segments.py`** — add `lapsed_patient(config)` resolver: pipeline over `customers` → left-join latest `prescriptions.prescription_date` per patient → left-join latest `orders.created_at` per customer → filter where both are older than `lapse_threshold_months` → filter `marketing_consent=True` → filter `store_id IN applicable_stores` (if set) → exclude customers whose last order contained only `excluded_brands` items.
- **Extend `megaphone.py`** — `_scan_lapsed_patients()`: called on the 30-min tick. Reads settings (skip if `enabled=False`). Calls `lapsed_patient` segment. For each customer: checks `notification_logs` for prior W1 send in the current reactivation cycle (last 90 days). If no W1: mint voucher (W1 discount), send W1 message, stamp `reactivation_tier="W1"`. If W1 exists AND `created_at` > `week3_delay_days` ago AND no opened/converted: mint W2 voucher (W3 discount), send W3 message, stamp `reactivation_tier="W3"`. Skip if W3 already sent in same cycle. Write `last_scan_at` to settings.
- **Extend `vouchers.py` `redeem_voucher_atomic`** — before debit, if voucher has `excluded_brands`, verify the cart's order items do not include those brands (check against `products` collection by `brand` field). If cart is exclusively excluded-brand items, return 422 "voucher not applicable to these items." If mixed cart: apply voucher to non-excluded subtotal only (requires `applicable_amount` param from caller).
- **Add RBAC policy rows** for the four new endpoints in `rbac_policy.py`.

## Frontend
- **Extend `frontend/src/pages/marketing/` (or Settings)** — new tab/page "Lapsed Reactivation" (SUPERADMIN/ADMIN only):
  - **Config panel**: toggle (enabled/disabled), lapse threshold slider (6–60 months), W1 discount % input, W3 discount % input, W3 delay days, voucher validity days, store multi-select (leave blank = all), excluded brands multi-select (pre-populated from `pricing_caps` luxury list), channel selector (WhatsApp/SMS). Save button with confirmation dialog showing "This will affect ~N patients (see preview)."
  - **Preview card**: shows live patient count per store, W1-eligible vs W3-eligible breakdown. Auto-refreshes on config change (debounced 1s). Uses `/preview` endpoint.
  - **Analytics panel**: three KPI chips (W1 sent / W3 sent / Vouchers redeemed), conversion funnel (sent → opened → converted), store-breakdown table. Restrained monochrome table; single accent color for "converted" column only.
  - **Activity log**: last 20 `notification_logs` rows with `reactivation_tier` set, showing customer first name, store, tier, sent_at, status (SENT/DELIVERED/FAILED), voucher code (masked last 4).
- All UI uses existing Tailwind light-only tokens; no new design components needed beyond standard table/card/toggle patterns already in the codebase.

## Business rules
- **Lapse definition**: customer has NO confirmed order (`status NOT IN [DRAFT, CANCELLED]`) AND no prescription (`prescription_date`) within `lapse_threshold_months` months. Both conditions must be true (patient who tested but didn't buy is still lapsed; patient who bought frames without an exam is still lapsed on the clinical side).
- **Consent gate**: `marketing_consent=True` is a hard pre-condition; never bypassed. Checked in segment resolver and again in notification_service before send.
- **Quiet hours**: 21:00–09:00 IST — enforced by `megaphone._scan_lapsed_patients()` via existing `in_quiet_hours()` check before every send batch.
- **Tier progression**: W3 is only sent if W1 was sent AND `week3_delay_days` have elapsed AND no `opened_at` or `converted_at` on the W1 log row. A customer who opened W1 (even without buying) does NOT get W3 — they showed intent.
- **One voucher per cycle**: a customer receives at most one W1 voucher and one W3 voucher per 90-day reactivation window. Enforced by querying `notification_logs` before minting.
- **Luxury/brand exclusion**: excluded brands' items are ineligible for the voucher discount at redemption. The voucher is still mintable; the redemption gate in `redeem_voucher_atomic` enforces the restriction per-line. If 100% of cart is excluded brands, the voucher returns 422 with a clear message.
- **Voucher time-limit**: voucher `expiry_date = now + voucher_validity_days`. After expiry, `redeem_voucher_atomic` already rejects it (existing `EXPIRED` status check).
- **Discount cap compatibility**: the W1/W3 voucher discount must not push the effective item discount below cost (existing `cost_at_sale` floor in `orders.py`). The voucher is applied at order level (cart discount, not per-item), so it reduces `taxable_subtotal` before GST — same mechanism as `cart_discount_percent` in posStore.ts. POS cashier sees the voucher applied and can flag if it triggers the ₹0/100%-discount approval gate.
- **Audit**: every voucher mint, every send, and every redemption is immutably logged (`notification_logs`, `vouchers.redemptions[]`, `audit_logs`). Settings changes write before/after to `audit_logs`.
- **DISPATCH_MODE gate**: manual trigger endpoint respects `DISPATCH_MODE`; in `off` or `test` mode, sends are SIMULATED and vouchers are minted with `status=SIMULATED` (not ACTIVE) so they cannot be accidentally redeemed.

## RBAC
| Role | Capability |
|---|---|
| SUPERADMIN | Full: configure settings, view preview, manual trigger, view analytics |
| ADMIN | Read settings, view preview (no edit), view analytics |
| ACCOUNTANT | View analytics only (voucher redemption counts for P&L reconciliation) |
| AREA_MANAGER | View analytics scoped to their stores |
| All other roles | No access |

MEGAPHONE agent runs as system (no user JWT); its actions are attributed to `agent_id="megaphone"` in audit logs.

## Integrations
- **MSG91** (MEGAPHONE → `providers.py` `send_sms`/`send_whatsapp`): primary send path. Template IDs stored in `lapsed_reactivation_settings.template_w1_id/template_w3_id`; must be pre-registered DLT templates. DISPATCH_MODE gated.
- **Jarvis / MEGAPHONE agent**: the engine lives inside MEGAPHONE's 30-min tick. No new agent needed.
- **Vouchers** (existing `vouchers.py`): voucher minting and redemption; no external integration.
- No Shopify, Razorpay, Tally, or ONDC involvement.

## Risk notes
- **Low financial risk**: vouchers are single-use, time-limited, and category/brand restricted. The atomic redemption guard prevents double-spend. The existing cost-floor check prevents selling below cost.
- **Consent compliance (DPDP)**: sending marketing SMS/WhatsApp requires `marketing_consent=True`. The segment resolver enforces this. The `dpdp_consent_ledger` tracks consent grants/withdrawals; if a patient withdraws consent between W1 and W3, the W3 scan will exclude them (consent checked fresh on each tick).
- **Segment size on first run**: if the feature is enabled on a large customer base after months of inactivity, the first scan could produce a very large W1 batch. The MEGAPHONE drain pattern (60 sends/tick) throttles this naturally, but the preview endpoint should be used first to size the audience.
- **W3 "ignored" detection** relies on `opened_at` being set by MSG91 DLR webhook (`webhooks.py:338-426` already handles delivery reports). Read-receipts require WhatsApp Business API delivery; SMS has no open-tracking. For SMS channel, "ignored" should fall back to "no `converted_at`" (no purchase) rather than "no `opened_at`".
- **No POS revenue risk**: this feature does not touch POS order creation, pricing logic, or cashier flows. The voucher redemption path (`redeem_voucher_atomic`) is already production-hardened with the atomic guard.
- **Feature flag**: `lapsed_reactivation_settings.enabled` is the runtime toggle. MEGAPHONE checks it at the top of `_scan_lapsed_patients()` before any DB reads. Safe to deploy disabled.

## Recommendation
Build now — the infrastructure cost is low (all heavy lifting is reused: MEGAPHONE agent, campaign_segments, notification_service, vouchers). The marginal work is the config UI, the `lapsed_patient` segment resolver, the `_scan_lapsed_patients` tick method, and the voucher brand-exclusion gate. Estimated 4 days. ROI is high: lapsed patients are a known revenue leak in optical retail (average optical Rx expires in 2yr; patients forget to return). The winback segment already proves the data join is feasible.

## Owner decisions
- Q: What is the lapse threshold — 24 months (standard Rx validity) or a different number? | Why: This defines how large the segment is. 24 months aligns with standard spectacle Rx validity; 18 months catches patients earlier. | Options: 18 months / 24 months / configurable per store (slightly more build)
- Q: Should W3 be sent only if the patient showed zero engagement (no open), or also if they opened but did not buy? | Why: Sending W3 to someone who opened W1 but didn't buy is more aggressive (and may convert more); skipping them is more conservative. | Options: Skip if opened (default above) / Send regardless of open, only skip if purchased
- Q: Which brands should be fully excluded from the voucher discount — only the luxury-brand list (Cartier, Chopard, Bvlgari, Gucci, Prada, Versace, Burberry) or a wider set? | Why: The build pre-populates with the existing luxury brand cap list. If you want to exclude additional house brands or non-luxury lines, those must be named. | Options: Use existing luxury-brand cap list as-is / Add specific brand names / Owner-editable list in settings UI (default recommendation)
- Q: Should lapsed patients who have already received a reactivation voucher in the last 90 days be re-enrolled in the next cycle, or should the cooling-off period be longer (e.g., 6 months)? | Why: A 90-day cooldown means a patient could theoretically receive 4 reactivation campaigns per year, which may feel spammy. | Options: 90-day cooldown / 180-day cooldown / 1-year cooldown
- Q: For stores not yet live on WhatsApp Business (MSG91 WABA), should the fallback be SMS or skip those stores entirely until they are onboarded? | Why: SMS has no open-tracking, so W3 "ignored" detection degrades to conversion-only. | Options: Fall back to SMS automatically / Skip stores without WhatsApp configured / Owner decides per store in settings
# Feature #11: Advanced Dynamic Promotions & Offer Tallying
META: effort=L days=12 risk=HIGH roi=4 quickwin=no deps=none phase=3

## Existing overlap
IMS already has significant promotions infrastructure that must be extended rather than rebuilt:

- **Per-item discount caps** with role, category, and luxury-brand enforcement: `backend/api/routers/orders.py:1195-1312`, `backend/api/services/role_caps.py`, `backend/api/services/pricing_caps.py`. These are the hardlocks this feature sits on top of.
- **Cart-level discount** with reason and approval tracking: `posStore.ts:146-149`, `orders.py:499`. Already captures `cart_discount_approved_by` and `cart_discount_reason`.
- **Promo templates collection** (`promo_templates`): already defined in the CRM audit — type enum includes BOGO, COMBO, THRESHOLD. Schema exists; no executor wired.
- **Vouchers / gift cards** (atomic redemption): `backend/api/routers/vouchers.py`, `vouchers.redeem_voucher_atomic`. The free-item reward path can reuse this.
- **Loyalty reward catalog** (`loyalty_rewards`): `loyalty.py:757-875` — DISCOUNT/FREE_ITEM/VOUCHER/EXPERIENCE types already modelled.
- **Campaign analytics** (`campaign_audit`, `notification_logs`): conversion tracking hooks exist; offer attribution can slot in here.
- **ORACLE agent** discount-abuse detection: `oracle.py:249+`. Already flags anomalies; extend to flag promo-stacking abuse.
- **Tally nightly export** (NEXUS, `tally_exports`): offer discounts must flow through as discount vouchers in the JV — already has a balanced/unbalanced flag.

## Reuse (extend, don't rebuild)
- `promo_templates` collection — add rule engine fields (`trigger_conditions`, `reward_action`, `stacking_policy`, `valid_store_ids`, `active`, `schedule`)
- `backend/api/routers/orders.py` — extend `create_order` and the per-item pricing block to evaluate active promo rules before finalising line prices
- `backend/api/services/pricing_caps.py` — add `evaluate_promos(cart, store_id, user_role)` pure function; caps remain the outer hardlock
- `posStore.ts` — extend review step to show applied promos + margin impact card
- `backend/api/routers/campaigns.py` — promo campaigns already reference `template_id`; extend `send_campaign` to stamp `promo_id` on triggered orders
- `backend/agents/implementations/oracle.py` — extend `_detect_discount_abuse()` to flag promo-stacking and free-rider patterns
- `backend/api/services/notification_service.py` — reuse for "promo unlocked" WhatsApp/SMS trigger

## Data model
New fields on existing `promo_templates` collection (extend, not new):
```
trigger_conditions: {
  min_cart_value: Decimal | null,
  required_skus: [sku] | null,       # BOGO / COMBO triggers
  required_category: str | null,
  min_qty: int | null,
  customer_tier: [BRONZE|SILVER|...] | null,
  first_purchase_only: bool
}
reward_action: {
  type: PERCENT_OFF | FIXED_OFF | BOGO_HALF | FREE_ITEM_SKU | UNLOCK_TIER,
  value: Decimal,                    # percent or rupee amount
  apply_to: CART | SPECIFIC_CATEGORY | SPECIFIC_SKU | CHEAPEST_ITEM,
  free_sku: str | null               # for FREE_ITEM_SKU type
}
stacking_policy: EXCLUSIVE | ADDITIVE | BEST_OF
priority: int                        # tiebreak when multiple promos match
max_uses_per_customer: int | null
max_uses_total: int | null
uses_count: int                      # atomic $inc on apply
valid_store_ids: [str] | null        # null = all stores
schedule: {active_from: datetime, active_until: datetime} | null
```

New collection `promo_applications` (append-only, per order line application):
```
{
  promo_application_id: str,
  promo_id: str,
  order_id: str,
  order_number: str,
  store_id: str,
  customer_id: str,
  cashier_id: str,
  applied_at: datetime,
  lines_affected: [{sku, original_price, promo_price, qty, gross_discount_given, margin_before, margin_after}],
  total_discount_given: Decimal,      # rupee value of offer
  estimated_cogs: Decimal,            # from cost_at_sale / cost_price
  net_margin_after_promo: Decimal,
  cogs_is_estimated: bool,
  campaign_id: str | null             # if triggered by a campaign
}
```

New fields on `orders` (existing collection):
```
applied_promos: [{promo_id, promo_name, discount_given, reward_type}]
promo_override_by: str | null        # user_id if cashier manually applied
promo_override_reason: str | null
```

## Backend
- `GET /api/v1/promos` — list active promo rules (store-scoped, role-filtered); Admin CRUD
- `POST /api/v1/promos` — create promo rule (ADMIN/SUPERADMIN only); validates no overlap with hardlock caps
- `PUT /api/v1/promos/{promo_id}` — update; cannot change `uses_count`; deactivate-only when uses_count > 0
- `POST /api/v1/promos/evaluate` — **pure evaluation endpoint** (called by POS at cart-review step): receives cart payload, returns matched promos + projected prices + margin impact; NO side effects; used for preview
- `POST /api/v1/promos/{promo_id}/apply` — called from `create_order` internally (not a public POS endpoint); atomically $inc `uses_count`, guards `max_uses_total` and `max_uses_per_customer` via `find_one_and_update`; writes `promo_applications` row; stamps `applied_promos` on order doc
- `GET /api/v1/promos/tally` — **Offer Tally dashboard** (ADMIN/ACCOUNTANT/SUPERADMIN): aggregates `promo_applications` by promo_id + period; returns per-promo: orders_count, total_discount_given, total_estimated_cogs, net_margin_after_promo, avg_basket_size, conversion_rate (promo_evaluated vs promo_applied)
- `GET /api/v1/promos/{promo_id}/tally` — drill-down for one promo: per-store, per-cashier, per-day breakdown; flags cashiers with anomalous apply-rate
- Extend `backend/api/routers/orders.py:create_order` — after per-item cap validation, call `evaluate_promos(cart)`, apply reward mutations to line prices (AFTER caps, never below caps), then write `promo_applications`; all within the same DB transaction scope (fail-safe: promo evaluation error must not block the order)

## Frontend
- **Promo Rules Manager** (`/settings/promotions`, extend `SettingsPage` or new route) — ADMIN/SUPERADMIN: table of active/expired promos; create/edit drawer with trigger and reward fields; preview card showing "a cart of Rs.X triggers this as: ..."; active/inactive toggle. Restrained light UI: single-column form, `bg-white border border-gray-200`, status chips in semantic colors only.
- **POS Review Step enhancement** (extend `frontend/src/stores/posStore.ts` + review component) — show "Promos Applied" collapsible section listing each matched promo name + rupee saving; show "Estimated Margin After Promos" (with estimated flag when COGS unknown); cashier cannot remove auto-applied promos but can note a manual override reason (stored as `promo_override_reason`)
- **Offer Tally Dashboard** (`/reports/promotions`, new page) — ADMIN/ACCOUNTANT/SUPERADMIN: date-range picker, promo selector; summary cards (Total Discount Given / Est. Margin Impact / Orders with Promos / Avg. Basket Lift); table per promo row; drill-down modal per promo showing per-cashier and per-store breakdown. Flag rows where `net_margin_after_promo < 0` in `text-red-600`. No color used decoratively.

## Business rules
- A promo reward **can never push the effective price below the hardlock floor** (existing `pricing_caps.effective_discount_cap()` remains the outer cap; promo engine runs inside it)
- `EXCLUSIVE` promos: only one promo applies per cart; highest-value wins (by `total_discount_given`)
- `BEST_OF`: system picks the single promo that gives the customer the most savings
- `ADDITIVE`: promos stack additively but combined discount still cannot breach role/category cap
- `max_uses_per_customer` check uses `promo_applications` count by `(promo_id, customer_id)` — atomic guard via `find_one_and_update` with $expr filter, same pattern as `vouchers.redeem_voucher_atomic`
- Free-item SKU rewards: the free item is added as a zero-price line with `promo_id` reference; it flows through GST calculation at 0 value but the HSN/category must still be valid
- Promo application is **audited immutably** in `promo_applications`; cashier cannot retroactively remove an applied promo after order is CONFIRMED
- A cancelled order: `promo_applications` row is kept; `uses_count` is NOT decremented (conservative — prevents gaming via cancel+reorder)
- Estimated-COGS flag (`cogs_is_estimated: true`) must propagate to Offer Tally so the dashboard never shows fabricated margin as real
- Period-lock: if the accounting period is locked, `promo_tally` read-only queries still work; no new applications can be written to a locked period (inherits the existing `check_period_locked` guard in orders)

## RBAC
- SUPERADMIN: full CRUD on promo rules, full tally, can override any stacking restriction
- ADMIN: full CRUD on promo rules for their stores, full tally
- AREA_MANAGER: read promo rules, read tally for their stores; cannot create/edit rules
- STORE_MANAGER: read active promo rules for their store, read tally for their store only
- ACCOUNTANT: read-only tally (for margin/P&L reconciliation); no promo rule access
- SALES_CASHIER / SALES_STAFF: promo rules are auto-evaluated by POS — they see what applied; no ability to manually add/remove promos (only note override reason if MANAGER-approved)
- CATALOG_MANAGER: read promo rules only (needs visibility for pricing decisions)
- All other roles: no access

## Integrations
- **Tally (NEXUS nightly)**: `promo_applications.total_discount_given` per order must appear as a discount voucher line in the Tally sales JV XML — extend `_build_tally_export()` in `nexus.py` to include a "Promotional Discount" ledger entry per order that had promos applied; keeps the JV balanced
- **ORACLE agent**: extend `_detect_discount_abuse()` to scan `promo_applications` for cashiers applying promos at a rate > 2 standard deviations above store average; emit `anomaly.detected` event with `source=promo_abuse`
- **MEGAPHONE agent**: on promo unlock (THRESHOLD trigger met), queue a "You've unlocked Offer X" WhatsApp via `notification_service.queue_notification` — fire-and-forget, fail-soft
- **Shopify / ONDC**: online orders ingested via `online_order_mapper` do NOT run the promo engine (Shopify handles its own discounts); `applied_promos` field is left empty for `channel=ONLINE`; the Offer Tally filters by `channel=POS` by default

## Risk notes
- **POS revenue-critical**: the promo evaluation runs inside `create_order` — any exception must fail-soft (log + skip promo, never block the sale). Ship behind `PROMO_ENGINE_ENABLED=false` env flag defaulting off; flip per-store via `integrations` collection.
- **Stacking complexity**: ADDITIVE stacking interacting with per-item caps + cart-level discount is the highest-risk calculation path. Pure `evaluate_promos` function must be unit-tested for all three stacking modes × all cap scenarios before the flag is enabled in production.
- **Uses-count race**: high-traffic BOGO promos need the atomic `find_one_and_update` guard on `uses_count`; a plain read-then-write will allow overshoots. Use the same guard pattern as `vouchers.redeem_voucher_atomic`.
- **Free-item GST edge case**: a zero-value line still needs a valid HSN and GST rate for GSTR-1 (value = 0, tax = 0, but the line must exist). Coordinate with the finance team on whether free items need a "deemed supply" disclosure — this is an owner/accountant decision.
- **Margin reporting with estimated COGS**: when `cost_at_sale` is missing (older orders or non-serialised stock), COGS is estimated. The Offer Tally must visibly flag estimated rows so the owner does not mistake estimated margin for real margin.

## Recommendation
Build later (phase 3, after returns/inventory hardening stabilises). The promo engine touches the POS order-creation critical path, which is revenue-critical and currently undergoing concurrent hardening (P1/P2 money-integrity fixes). Start with the Promo Rules Manager and the `evaluate_promos` pure function + unit tests (zero POS risk), then gate the live POS integration behind the feature flag and enable store-by-store after the P1/P2 branch merges.

## Owner decisions
- Q: Which stacking policy should be the default for all new promos? | Why: EXCLUSIVE is safest for margin (only best promo fires); ADDITIVE maximises customer value but requires tighter cap tuning to stay profitable | Options: EXCLUSIVE (recommended) / BEST_OF / ADDITIVE
- Q: Should cancelled orders decrement the promo uses-count, allowing the customer to re-use the promo on a re-order? | Why: Decrementing is customer-friendly but opens a cancel-and-reorder gaming loop; not decrementing is conservative and prevents abuse | Options: Never decrement (recommended, conservative) / Always decrement / Decrement only if cancelled within 30 minutes by the same cashier
- Q: Should free-item BOGO lines appear on the printed GST tax invoice as a zero-value line (legally required for deemed supply disclosure), or should the invoice show only the paid items? | Why: A zero-value line is the correct GST treatment but adds visual clutter to the customer receipt; your accountant/CA must confirm whether "deemed supply" rules apply to these promotional free items in your trade | Options: Show zero-value line on invoice / Show only paid items / Show on internal copy, suppress on customer receipt
- Q: Which stores/brands should the promo engine be enabled for first (pilot)? | Why: The feature flag (`PROMO_ENGINE_ENABLED`) can be set per-store via the `integrations` collection — piloting one store limits blast radius during rollout | Options: One store (low-volume, recommended for pilot) / All stores simultaneously / Better Vision only / WizOpt only
- Q: Should cashiers be able to manually apply a promo that did not auto-trigger (e.g., a walk-in customer shows a printed voucher)? | Why: Manual application requires a manager PIN/approval and opens a fraud vector; auto-only is safer but less flexible for edge cases | Options: Auto-only, no manual application / Manual allowed with manager approval + reason (audit-logged) / Manual allowed for Store Manager and above without extra approval
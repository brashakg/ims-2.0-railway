# Feature #36: Deep Shopify Sync (Strict Brand Compliance)
META: effort=XL days=18 risk=HIGH roi=4 quickwin=no deps=none phase=2

## Existing overlap
IMS already has substantial Shopify infrastructure that covers roughly 60% of this feature:

- **Inventory push**: `backend/api/services/online_stock_writeback.py` — on every in-store POS sale, pushes absolute on-hand minus safety buffer to Shopify via `inventorySetQuantities`. Safety buffer config lives in `integrations.shopify.config.safety_buffer`.
- **Shopify order pull**: `backend/agents/nexus_providers.py:96-136` — NEXUS agent pulls orders created in last N hours via Admin API v2024-01. Already wired to hourly tick.
- **Product push (DARK)**: `backend/api/routers/online_store_push.py` stubs exist for product/collection/menu/image push. Gated behind `IMS_SHOPIFY_WRITES=1` AND `DISPATCH_MODE=live` AND creds. Currently returns SIMULATED.
- **Variant-SKU resolution**: `backend/api/services/online_order_mapper.py:104-138` resolves Shopify `variant_id` → `catalog_variants.shopify_variant_id` → IMS SKU. Customer match/create (by phone/email) already in this mapper.
- **Online catalog bridge**: `backend/api/services/online_catalog.py` reads BVI Postgres to get `inventory_item_id` and `location_id` per SKU for Shopify API calls.
- **Customer bridging**: `customers` collection has `shopify_customer_id` field. `online_order_mapper` matches by phone/email and stamps this field.
- **Webhook receiver**: `backend/api/routers/webhooks.py:526-535` — signed Shopify webhook (HMAC-SHA256) verified, persisted to `webhook_inbox`, dispatches to NEXUS.
- **NEXUS agent**: `backend/agents/implementations/nexus.py` orchestrates Shopify/Razorpay/Shiprocket/Tally sync on hourly tick and webhook events.
- **Collections management (Phase 2)**: `backend/api/routers/online_store_collections.py` — CRUD for `ecom_collections` with SMART rule support and dirty flag for push.
- **Loyalty engine**: `backend/api/routers/loyalty.py` — full earn/redeem with atomic `try_debit()`, tier multipliers, rewards catalog. Not yet bridged to Shopify.
- **Gift vouchers**: `backend/api/routers/vouchers.py` — atomic `redeem_voucher_atomic()`, multi-use/single-use codes. Not yet bridged.

What does NOT exist yet: brand/category hardlock enforcement on push, BOPIS (Buy Online Pick Up In Store) multi-location routing, and loyalty/gift card redemption on Shopify checkout.

## Reuse (extend, don't rebuild)
- `backend/api/services/online_stock_writeback.py` — extend to check brand/category hardlock before computing push qty; if hardlocked, push 0 (not skip, actively zero out)
- `backend/api/routers/online_store_push.py` — extend existing product push stubs to read `ecom` sub-doc + hardlock flags before pushing; turn DARK stubs LIVE behind the existing three-gate system
- `backend/agents/implementations/nexus.py` — add `_sync_online_hardlocks()` method to existing hourly tick; add `_reconcile_loyalty_redemptions()` for Shopify → IMS loyalty sync
- `backend/api/services/online_catalog.py` — extend `variant_targets()` to return per-location inventory items for all stores (BOPIS), not just single `SHOPIFY_ONLINE_LOCATION_ID`
- `backend/api/services/online_order_mapper.py` — extend to parse `fulfillment_location_id` from Shopify order → route to correct IMS store for picking
- `backend/api/routers/loyalty.py` — add `POST /loyalty/shopify-redeem` endpoint (Shopify discount code webhook path); reuse existing `try_debit()` atomic guard
- `backend/api/routers/vouchers.py:redeem_voucher_atomic()` — reuse directly; add a thin webhook-triggered path for Shopify gift card redemptions
- `catalog_variants` collection — add `shopify_variant_id` (already exists), add `online_blocked` boolean (new field, cheap extension)
- `catalog_products` collection — add `online_blocked` boolean + `online_blocked_reason` (enum: BRAND_POLICY / CATEGORY_RULE / MANUAL) + `online_blocked_by` + `online_blocked_at`
- `integrations` collection (type=shopify) — extend `config` dict with `bopis_enabled`, `bopis_store_location_map` (IMS store_id → Shopify location_id), `loyalty_discount_code_prefix`, `gift_card_sync_enabled`
- `ecom_collections` — already has dirty flag; extend with `hardlock_applies` boolean to prevent hardlocked SKUs from appearing even in manually curated collections
- `backend/api/routers/settings.py` — extend `_INTEGRATION_CATALOG["shopify"]` field list with new BOPIS and loyalty config fields; already handles encrypted fields and masking
- `frontend/src/pages/online-store/OnlineStorePage.tsx` — extend landing shell to show hardlock summary counts
- `frontend/src/pages/online-store/OnlineOrdersPage.tsx` — extend to show fulfillment_store assignment per BOPIS order

## Data model

**New fields on `catalog_products`** (existing collection, extend):
```
online_blocked: bool (default false)
online_blocked_reason: "BRAND_POLICY" | "CATEGORY_RULE" | "MANUAL" | null
online_blocked_by: user_id | "SYSTEM"
online_blocked_at: datetime | null
```

**New fields on `catalog_variants`** (existing collection, extend):
```
online_blocked: bool (inherited from parent product or overridden)
shopify_inventory_item_id: str (already partially present; make canonical)
```

**New collection: `shopify_bopis_slots`**
```
slot_id: str (uuid)
store_id: str
shopify_location_id: str  ← Shopify's GID for this store
slot_label: str           ← display name e.g. "Ranchi Main" shown in Shopify checkout
pickup_sla_hours: int     ← e.g. 4 hours
is_active: bool
created_at: datetime
```

**New collection: `shopify_loyalty_codes`** (bridge table for issued Shopify discount codes)
```
code_id: str (uuid)
customer_id: str
loyalty_txn_id: str       ← points transaction that funded this code
shopify_price_rule_id: str
shopify_discount_code: str
points_burned: int
rupee_value: float
status: "ACTIVE" | "USED" | "EXPIRED" | "CANCELLED"
shopify_order_id: str | null   ← populated when Shopify confirms use
created_at: datetime
expires_at: datetime
used_at: datetime | null
```

**New fields on `integrations` (type=shopify) config dict**:
```
bopis_enabled: bool
bopis_store_location_map: [{"store_id": str, "shopify_location_id": str, "pickup_sla_hours": int}]
loyalty_sync_enabled: bool
loyalty_discount_code_prefix: str   e.g. "LYL-"
gift_card_sync_enabled: bool
hardlock_auto_zero_on_push: bool    ← push qty=0 to Shopify when product is hardlocked
drift_check_interval_minutes: int
```

**New fields on `webhook_inbox`** (extend existing):
```
processed_at: datetime | null   ← stamp when NEXUS drains it
retry_count: int (default 0)
```

## Backend

**Hardlock enforcement layer** (`backend/api/services/online_hardlock.py` — new service):
- `is_hardlocked(product_id, db) -> bool` — reads `catalog_products.online_blocked` (cached 60s in Redis/in-process)
- `apply_hardlock(product_id, reason, blocked_by, db)` — sets fields, writes audit_log, enqueues push-zero job
- `lift_hardlock(product_id, lifted_by, db)` — clears fields, writes audit_log, enqueues push-qty job
- `bulk_hardlock_by_category(category, reason, db)` — for CATEGORY_RULE type
- `bulk_hardlock_by_brand(brand, reason, db)` — for BRAND_POLICY type

**Extend `online_stock_writeback.py`**:
- Before computing push qty, call `is_hardlocked(product_id)`. If true and `hardlock_auto_zero_on_push=true`, push qty=0 to all locations. Write audit_log row with action=`HARDLOCK_ZERO_PUSH`.
- Never skip silently — always push (either real qty or 0) so Shopify stays consistent.

**New endpoint: `POST /api/v1/online-store/hardlock`** (ADMIN/SUPERADMIN):
- Body: `{product_ids: [], brand: null, category: null, reason: str, scope: "PRODUCTS"|"BRAND"|"CATEGORY"}`
- Calls `apply_hardlock` or `bulk_*` helpers
- Enqueues async push-zero for all affected variants via NEXUS event `hardlock.applied`
- Returns: `{affected_count, queued_push_count}`

**New endpoint: `DELETE /api/v1/online-store/hardlock/{product_id}`** (ADMIN/SUPERADMIN):
- Lifts hardlock, restores real qty push

**New endpoint: `GET /api/v1/online-store/hardlock`** (ADMIN/SUPERADMIN):
- Lists all currently hardlocked products with reason/who/when + current Shopify qty (from last push record)

**Extend `backend/api/routers/online_store_push.py`**:
- `POST /push/product/{id}`: before building GraphQL payload, call `is_hardlocked()`. Blocked products cannot be pushed as published; can be pushed as `status: DRAFT` (hides from storefront) or skipped.
- Add `POST /push/hardlock-sweep` (ADMIN/SUPERADMIN): re-push qty=0 for all currently hardlocked SKUs. NEXUS calls this nightly.

**BOPIS routing — extend `online_order_mapper.py`**:
- Read `fulfillment_location_id` from Shopify order's fulfillment object.
- Look up `integrations.shopify.config.bopis_store_location_map` to resolve → IMS `store_id`.
- Stamp `fulfillment_store_id` on the IMS order doc (new field on `orders`).
- Route pick-ticket creation to that store's workshop/picking queue.

**BOPIS inventory — extend `online_catalog.py`**:
- `multi_location_targets(sku, db)` — returns list of `{store_id, shopify_location_id, inventory_item_id, on_hand}` for all BOPIS-enabled stores.
- Called by extended `online_stock_writeback.py` to push per-location qty instead of single location.

**New endpoint: `POST /api/v1/online-store/bopis/locations`** (SUPERADMIN):
- Upsert `shopify_bopis_slots` and sync to `integrations` config map.

**New endpoint: `GET /api/v1/online-store/bopis/orders`** (STORE_MANAGER/AREA_MANAGER/ADMIN):
- Lists ONLINE orders with `fulfillment_store_id = current_store_id` that are pending pickup. Store-scoped.

**Loyalty → Shopify discount code bridge**:
- `POST /api/v1/loyalty/generate-shopify-code` (authenticated customer or POS staff):
  - Burns N points via existing `try_debit()` atomic guard.
  - Calls Shopify `priceRuleCreate` + `discountCodeCreate` GraphQL (via `nexus_providers.py` extension).
  - Persists to `shopify_loyalty_codes`.
  - Returns discount code string to show customer.
- `POST /api/v1/webhooks/shopify` already receives `orders/paid` event; extend handler in `webhooks.py` to check `discount_codes[]` array; if code matches `loyalty_discount_code_prefix`, stamp `shopify_loyalty_codes.status=USED` + `shopify_order_id`.
- Add nightly reconciliation in NEXUS tick: `shopify_loyalty_codes` where `status=ACTIVE` and `expires_at < now` → mark EXPIRED, reverse points debit via `loyalty.adjust_points()`.

**Gift card redemption on Shopify** (if owner enables):
- Shopify natively handles gift cards as payment methods. Bridge: when IMS gift voucher is issued, also create Shopify gift card via `giftCardCreate` mutation, store `shopify_gift_card_id` on `vouchers` doc.
- Redemption syncs back via `orders/paid` webhook: check `payment_gateway_names` for gift card, match by amount + timing, mark IMS voucher REDEEMED.

**Drift detector (extend existing `admin.py:837-932`)**:
- Extend `/online-store/drift` to also flag hardlocked products that still show qty>0 on Shopify (push missed). Include in nightly NEXUS sweep.

## Frontend

**Extend `frontend/src/pages/online-store/OnlineStorePage.tsx`**:
- Add "Brand Compliance" section to the module landing page showing: hardlocked product count, last hardlock sweep timestamp, BOPIS-enabled stores count. All in neutral summary cards (no color except semantic red for hardlock violations).

**New page: `frontend/src/pages/online-store/HardlockManagerPage.tsx`** (ADMIN/SUPERADMIN):
- Table of all products with `online_blocked=true`. Columns: product name, brand, category, reason, blocked_by, blocked_at, current Shopify qty (from last sync record), action (Lift hardlock).
- Filter bar: by brand, by category, by reason.
- Bulk action: "Hardlock by brand" dropdown + confirm modal. "Hardlock by category" same. Both show count of affected SKUs before confirming.
- Each row: "Lift" button → confirm modal showing the business impact (X units will go back online at qty N).
- Restrained UI: single-column table, neutral grays, red badge only on `BRAND_POLICY` reason (highest severity), amber on `CATEGORY_RULE`, gray on `MANUAL`.

**New page: `frontend/src/pages/online-store/BopisSettingsPage.tsx`** (SUPERADMIN):
- List of stores with toggles to enable BOPIS per store.
- Per-store: Shopify Location ID input, pickup SLA hours (dropdown: 2h / 4h / Same-day / Next-day), active toggle.
- Save writes to `integrations.shopify.config.bopis_store_location_map`.
- Plain table, no decorative elements.

**Extend `frontend/src/pages/online-store/OnlineOrdersPage.tsx`**:
- Add "Fulfillment Store" column to orders table for BOPIS orders.
- Filter tab: "BOPIS" (orders with `fulfillment_store_id` set) vs "Ship" vs "All".
- BOPIS order row: shows pickup store name + SLA deadline (order created_at + pickup_sla_hours).

**New panel: BOPIS pick queue on `frontend/src/pages/workshop/WorkshopPage.tsx`** (STORE_MANAGER/SALES_STAFF):
- New kanban column "Online Pickup" showing ONLINE channel orders routed to this store.
- Card: order number, customer name, items, pickup SLA countdown. "Mark Ready" → stamps `ready_for_pickup_at`, triggers WhatsApp notification to customer.

**Loyalty code generator** (extend `frontend/src/pages/pos/POSPage.tsx` or Customer 360):
- On Customer 360 sidebar: "Generate Shopify Discount Code" button (visible only if `loyalty_sync_enabled=true`).
- Modal: shows current balance, input for points to burn, preview of rupee value and code prefix.
- On confirm: calls `POST /loyalty/generate-shopify-code`, shows generated code in a copyable field.
- No emojis. Neutral modal, single blue primary button.

**Settings integration** (extend `frontend/src/pages/settings/IntegrationsPage.tsx`):
- Shopify integration card: add BOPIS toggle + loyalty sync toggle as new config fields in the existing integration form. Rendered as labeled toggles below existing fields. On save, calls existing integration upsert endpoint.

## Business rules

- **Hardlock is absolute**: if `online_blocked=true`, Shopify qty is forced to 0 on every sync cycle regardless of actual warehouse stock. No staff below ADMIN can override this.
- **Hardlock propagates to collections**: when a product is hardlocked, it is excluded from SMART collection resolution (`online_store_collections.py` resolver must filter `online_blocked=true` products). Manual CUSTOM collection membership is preserved in the doc but the product will show qty=0, effectively unavailable.
- **Hardlock does not delete the Shopify listing**: the product stays in Shopify as DRAFT or with qty=0. Deleting a product from Shopify is a separate, irreversible owner decision. The system never deletes Shopify products automatically.
- **Lift is not instant**: lifting a hardlock enqueues a push job (NEXUS next tick or manual sweep). Qty is not restored until push completes and NEXUS confirms success. Audit log captures the gap.
- **BOPIS stock**: each store's BOPIS-available qty = `stock_units` AVAILABLE count for that `store_id` minus safety buffer. If a store is not BOPIS-enabled, its location inventory on Shopify is set to 0 on every push.
- **BOPIS SLA breach**: if pickup SLA hours pass and order is still not marked READY, TASKMASTER auto-escalates to STORE_MANAGER (reusing existing SLA escalation ladder in `taskmaster.py`).
- **Loyalty codes**: one code per redemption session. Minimum burn is defined by `loyalty_settings.min_redeem_points` (existing field). Code expires in `loyalty_settings.expiry_days` or 7 days, whichever is shorter.
- **Loyalty code value cap**: code value cannot exceed the order's `max_redeem_pct` of order total (existing business rule in `loyalty_engine.py`; enforce the same cap at code generation time by requiring customer to specify the order value estimate, or apply conservatively at 10% of average order value).
- **Gift card**: if `gift_card_sync_enabled=false` (default), IMS vouchers are not pushed to Shopify. Enabling requires owner explicitly turning it on; once enabled, all subsequently issued vouchers get a Shopify gift card twin. Existing pre-enablement vouchers are NOT retroactively synced.
- **Push-zero audit**: every forced push of qty=0 due to hardlock writes an audit_log row with `action=SHOPIFY_HARDLOCK_PUSH`, `entity_type=product`, `entity_id=product_id`, before/after qty, triggered_by (`SYNC`/`MANUAL`/`HARDLOCK_APPLY`). Immutable.
- **Indian data residency**: customer phone/email sent to Shopify only for order fulfillment. Shopify customer records are not enriched with IMS clinical/loyalty data. Customer bridging is one-way (Shopify → IMS match only).

## RBAC

| Role | Hardlock manage | Hardlock view | BOPIS settings | BOPIS pick queue | Loyalty code generate | Online orders view |
|------|----------------|---------------|----------------|------------------|-----------------------|--------------------|
| SUPERADMIN | Full (apply/lift/bulk/settings) | Yes | Full | Yes (all stores) | Yes | Yes |
| ADMIN | Apply/lift/bulk | Yes | View only | Yes (all stores) | Yes | Yes |
| AREA_MANAGER | None | View only | None | Yes (their stores) | Yes (for customer) | Yes (their stores) |
| STORE_MANAGER | None | View own store impact | None | Yes (own store) | Yes (for customer) | Yes (own store) |
| SALES_CASHIER / SALES_STAFF | None | None | None | Yes (own store — pick/ready) | Yes (for customer at POS) | Own store BOPIS only |
| ACCOUNTANT | None | View | None | None | None | Yes (all) |
| Others | None | None | None | None | None | None |

Enforcement: extend `rbac_policy.py` POLICY list with new routes. Hardlock apply/lift gates on `ADMIN_ROLES`. BOPIS settings gate on `SUPERADMIN`. BOPIS pick queue uses existing store-scoped `validate_store_access` pattern.

## Integrations

- **Shopify Admin API (GraphQL)**: `inventorySetQuantities` mutation (multi-location, already in `nexus_providers.py`; extend to accept location list); `productUpdate` mutation (status → DRAFT for hardlocked); `priceRuleCreate` + `discountCodeCreate` (loyalty codes); `giftCardCreate` (gift card sync, optional); `webhooks/orders/paid` (already wired).
- **NEXUS agent** (`backend/agents/implementations/nexus.py`): add `_sync_hardlocks()`, `_sync_bopis_qty()`, `_reconcile_loyalty_codes()` to hourly tick.
- **TASKMASTER agent** (`backend/agents/implementations/taskmaster.py`): add BOPIS SLA breach escalation check alongside existing `_escalate_overdue_tasks()` — same pattern, different collection query.
- **MEGAPHONE agent**: add `ORDER_READY_FOR_PICKUP` notification template. Triggered by STORE_MANAGER clicking "Mark Ready" on BOPIS order. Sends WhatsApp (existing `send_notification` path, DISPATCH_MODE gated).
- **MSG91**: no new integration; reuses existing WhatsApp channel for pickup-ready notification.
- **Tally**: no change. NEXUS Tally export already treats ONLINE orders as normal sales vouchers (channel is metadata only).

## Risk notes

- **Inventory oversell on BOPIS**: pushing multi-location qty is a net-new Shopify API call pattern. If IMS stock and Shopify get out of sync during a network failure between the `online_stock_writeback` push and the NEXUS confirmation, a physical unit could be double-sold (once in-store, once online). Mitigation: safety buffer (already exists), atomic stock claim on BOPIS order ingest (mark unit RESERVED immediately when Shopify order lands), and the drift detector nightly sweep.
- **Hardlock race condition**: between lifting a hardlock and NEXUS pushing the real qty, there is a window where Shopify shows qty=0 but stock is available. Customers will see "out of stock" briefly. This is acceptable and safe; the alternative (pushing qty before lift is confirmed) risks selling a hardlocked item.
- **Loyalty code misuse**: a customer could generate a code and use it on a competitor store if they share the code. Mitigation: codes should be customer-email-locked at Shopify `priceRule` level (`customer_selection: {emails: [customer_email]}`). Implement this in `priceRuleCreate` payload.
- **Gift card sync complexity**: Shopify gift cards are a payment method, not a discount code. Their redemption flows through Shopify's payment processing. Mapping IMS voucher balance to Shopify gift card balance requires careful reconciliation and is the highest-risk part of this feature. Recommend shipping as a separate sub-feature behind its own feature flag (`gift_card_sync_enabled`) and defer until loyalty code bridge is stable.
- **POS impact**: `online_stock_writeback.py` is called on every POS sale (fire-and-forget async). The hardlock check adds one Redis/Mongo read per sale. Must be cached (60s TTL) or this becomes a latency risk on the POS hot path. Cache invalidation fires on hardlock apply/lift.
- **Feature flag**: all LIVE Shopify write paths already behind `IMS_SHOPIFY_WRITES=1` AND `DISPATCH_MODE=live`. No additional flag needed for hardlock (it only writes qty=0, which is safe). BOPIS multi-location push requires `bopis_enabled=true` in integration config (off by default). Loyalty code generation requires `loyalty_sync_enabled=true` (off by default).
- **No emojis in Python**: MEGAPHONE pickup-ready WhatsApp message must use ASCII only in the Python template string (existing constraint in all notification_service.py templates).

## Recommendation
Build in two phases: Phase 2A (hardlock + push infrastructure, 8 days) is the highest-ROI piece — it unblocks going live with Shopify for optical lenses and luxury brands without compliance risk. Phase 2B (BOPIS + loyalty bridge, 10 days) follows once hardlock is stable in prod for 2 weeks. Gift card sync defer to Phase 3 (separate feature). Do not ship hardlock and loyalty codes in the same deploy — too much surface area to verify at once.

## Owner decisions
- Q: Which specific brands should be hardlocked from day one (BRAND_POLICY)? | Why: The initial bulk-hardlock call must name these brands; the system will zero out their Shopify inventory immediately on first deploy. Wrong list = either compliance violation or revenue loss. | Options: (a) Provide list of brands (e.g. Cartier, Chopard, Bvlgari as per existing luxury caps), (b) start with no hardlocks and apply manually after launch, (c) hardlock by category (e.g. all LUXURY category items)
- Q: Which stores should be BOPIS-enabled at launch? | Why: Each store needs a Shopify Location ID mapped to it; Shopify must have a matching pickup location configured on the storefront. If a store is not ready for in-store pickup ops, enabling it will create unfulfillable orders. | Options: (a) One pilot store only, (b) all stores simultaneously, (c) no stores (launch inventory sync only, add BOPIS later)
- Q: Should loyalty points be redeemable on Shopify at launch, or deferred? | Why: Issuing Shopify discount codes requires creating Price Rules in Shopify (affects Shopify discount analytics and reporting). If loyalty codes are issued but the Shopify integration is not live yet, codes will be valid but unusable — confusing for customers. | Options: (a) Launch loyalty bridge only after Shopify store is live with real traffic, (b) issue codes in test mode first (test Shopify store), (c) skip loyalty bridge for now
- Q: What is the minimum points redemption for a Shopify code? | Why: Too low = customers generate codes for trivial amounts (1 point = Re 0.01 worth), creating thousands of price rules in Shopify and administrative noise. | Options: (a) 500 points minimum (Rs 50 value), (b) 1000 points minimum (Rs 100 value), (c) same as POS minimum (existing `min_redeem_points` setting, currently unset — you would need to set it)
- Q: Should hardlocked products remain visible on Shopify as "Out of Stock" (qty=0, listing stays) or be set to DRAFT (hidden from storefront entirely)? | Why: Out-of-Stock shows the brand/product exists and customers can request notification; DRAFT hides it completely. For luxury compliance, hidden may be required. | Options: (a) Out of Stock (qty=0, visible), (b) DRAFT (hidden from storefront), (c) owner chooses per brand
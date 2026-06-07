# Feature #12: Cross-Category Promotional Bundling Engine
META: effort=L days=8 risk=MED roi=4 quickwin=no deps=none phase=3

## Existing overlap
- **Discount enforcement** already built: per-item `discount_percent` + approval, category caps (MASS15/PREMIUM20/LUXURY5/SERVICE10/NON_DISCOUNTABLE0), luxury brand caps, role-based `effective_discount_cap()` — `backend/api/routers/orders.py:1195-1312`, `backend/api/services/role_caps.py`, `backend/api/services/pricing_caps.py`
- **Cart-level discount** already built: `cart_discount_percent/amount/reason/approved_by` on `posStore.ts:146-149` and `orders.py:499`; applied to taxable subtotal before GST
- **Promo templates** collection and CRUD already exist: `promo_templates` (type: BOGO/COMBO/THRESHOLD), referenced in `backend/api/routers/campaigns.py:920-1044` — but no POS evaluation engine wired to it yet
- **GST-aware line-item pricing**: `_compute_per_category_gst` in `orders.py:107-196` already handles per-line GST after discounts; bundling discount can slot in at the same layer
- **Idempotency-Key on order-create** already implemented (`orders.py`), so bundle evaluation on POS won't risk duplicate orders
- **Vouchers** (`vouchers.py`) and **loyalty** (`loyalty.py`) already handle atomic redemption with `find_one_and_update` guards — same pattern needed for bundle usage caps

## Reuse (extend, don't rebuild)
- `backend/api/routers/orders.py` — extend `create_order()` to call bundle-evaluation before per-item discount capping; bundle discount applied as a named line-item adjustment (not cart_discount, which is a separate concept)
- `backend/api/services/pricing_caps.py` — extend `effective_discount_cap()` to accept a `bundle_discount_pct` override that can exceed role cap when a valid bundle is applied (bundle acts as approved-by-HQ authority)
- `promo_templates` collection — extend schema with `bundle_type`, `trigger_category`/`trigger_sku_group`, `reward_category`/`reward_sku_group`, `trigger_min_value`, `reward_discount_pct`, `usage_cap_per_order`, `usage_cap_total`, `times_used` (atomic $inc)
- `frontend/src/pages/pos/POSPage.tsx` + `posStore.ts` — extend posStore with `appliedBundles[]` state; review step shows bundle badges per qualifying line item
- `frontend/src/pages/purchase/` or a new `Settings > Promotions` tab — bundle CRUD admin UI (ADMIN/SUPERADMIN only)
- `backend/api/routers/campaigns.py` — existing `promo_templates` CRUD endpoints (lines 920-1044) can be extended to cover bundle-type templates rather than building a separate router

## Data model
- **Extend `promo_templates` collection** (already exists) with new fields:
  - `bundle_type`: `"CROSS_CATEGORY"` (new enum value; existing: BOGO/COMBO/THRESHOLD)
  - `trigger_rules`: `[{category: str | null, brand: str | null, min_value: float, min_qty: int}]` — at least one rule required; all must be satisfied in cart
  - `reward_rules`: `[{category: str | null, brand: str | null, discount_pct: float, max_discount_amount: float | null, applies_to: "cheapest|most_expensive|all"}]`
  - `stacking`: `"NONE" | "SAME_BUNDLE" | "ANY"` — whether multiple bundles can apply to same cart
  - `usage_cap_per_order`: int (default 1)
  - `usage_cap_total`: int | null (null = unlimited)
  - `times_used`: int (atomic $inc on redemption, reset on campaign period rollover)
  - `valid_from`, `valid_until`: datetime (already partially on promo_templates)
  - `store_ids`: list (empty = all stores)
  - `enabled`: bool
- **New field on `orders` collection** — `applied_bundles: [{bundle_id, bundle_name, trigger_lines: [order_item_index], reward_lines: [order_item_index], discount_amount}]` for audit and Tally JV annotation

## Backend
- `POST /api/v1/promotions/bundles` (new, or extend existing campaigns router `/promotions`) — create/update bundle config (ADMIN/SUPERADMIN); validates trigger+reward category/brand values against known enums
- `GET /api/v1/promotions/bundles` — list active bundles for a store (called by POS on session start; cached 5 min TTL in Redis if available)
- `POST /api/v1/promotions/bundles/evaluate` — stateless pure evaluation: accepts `{store_id, items: [{category, brand, unit_price, quantity}]}`, returns `{bundles_matched: [{bundle_id, trigger_lines, reward_lines, discount_amount}]}`; no DB write, idempotent; POS calls this live as cart changes
- Extend `create_order()` in `orders.py`: after per-item price validation and before GST computation, call internal `_evaluate_and_apply_bundles(db, store_id, items, applied_bundles_from_request)`; atomically `$inc times_used` on each applied bundle doc (same `find_one_and_update` guard pattern as `vouchers.redeem_voucher_atomic`); store `applied_bundles[]` on order doc
- Extend `pricing_caps.effective_discount_cap()`: when a line carries a `bundle_discount_pct`, treat it as HQ-authorized (bypasses role cap but not NON_DISCOUNTABLE=0 and not luxury hard caps below bundle discount)

## Frontend
- **POS Review Step** (`frontend/src/pages/pos/POSReviewStep.tsx` or equivalent): add a "Promotions Applied" section below line items listing matched bundles with badge (e.g., "Watch + Sunglass Bundle — Rs.450 saved"); reward lines show discounted price in green with original struck through
- **posStore.ts**: add `availableBundles: Bundle[]` (loaded on store login), `appliedBundles: AppliedBundle[]` (computed live as cart changes via debounced call to `/evaluate`); `setAppliedBundles()` action
- **Settings > Promotions** (new tab under existing settings shell, restrained table layout): ADMIN/SUPERADMIN-only; table of bundles (name, trigger, reward, discount, valid dates, usage count vs cap, enabled toggle); create/edit drawer with category/brand pickers (dropdowns from existing catalog enums), min-value input, discount-% input, store selector, date range, usage cap; no colour decoration beyond status chip (enabled=slate, disabled=gray)
- **Bundle badge on cart line item**: small pill tag "BUNDLE" in `text-bv-red-600 border border-bv-red-200 bg-bv-red-50` on reward lines in cart; trigger lines show "Qualifies for bundle" tooltip on hover (no badge to avoid clutter)

## Business rules
- Bundle discount on reward lines is applied AFTER offer_price (HQ pricing) but treated as HQ-approved, so it can exceed the cashier/manager role cap — it cannot, however, discount a NON_DISCOUNTABLE item or breach luxury hard caps (Cartier 2%, Gucci 5% etc.) if the bundle discount would go lower
- If bundle `stacking=NONE` and two bundles both match the same cart, the bundle with the higher `discount_amount` wins (server resolves, client shows the winner)
- `times_used` atomic increment fails loudly (returns 409 + `bundle_exhausted` error) when `times_used >= usage_cap_total`; POS falls back to non-bundled pricing and shows "Promotion expired" toast
- Bundle evaluation is RE-RUN server-side on `create_order()` even if client passed `applied_bundles` — client result is advisory only; server is authoritative (prevents price tampering)
- Applied bundles are recorded on the order doc and surfaced on the Tally sales JV as a line-level discount annotation (not a separate voucher line)
- Returns: if a return partially covers a bundle (only reward item returned, trigger item kept), the bundle discount is prorated by returned-line value and included in the refund; `returns.py` must read `order.applied_bundles` to compute correctly — this is a genuine extension point in `_priced_return_lines()`
- Period lock check (`check_period_locked`) applies to `times_used` rollback — if an order is cancelled in a locked period, `times_used` is NOT decremented (conservative; avoids reopening a closed month)

## RBAC
- `SUPERADMIN`, `ADMIN`: full CRUD on bundle config
- `CATALOG_MANAGER`: read bundle list + create/edit (no delete, no store-all override)
- `STORE_MANAGER`, `AREA_MANAGER`: read-only view of active bundles for their store(s)
- `SALES_CASHIER`, `SALES_STAFF`: bundle auto-applies in POS; no manual override of which bundle applies; cannot bypass bundle evaluation
- `ACCOUNTANT`: read-only on bundle usage stats (for margin analysis)
- All other roles: no access to bundle config endpoints; POS evaluation endpoint is internal (called server-side only during order create)

## Integrations
- **Tally**: extend `backend/agents/nexus_providers.py` Tally JV builder to include bundle discount as a `<DISCOUNT>` narration tag on reward voucher lines; no new ledger head needed — maps to existing Discount ledger
- **Shopify**: DARK for now — online store bundles are a separate Shopify Discount API concept; this engine is POS-only until BVI merge step 7 completes
- **Jarvis / ORACLE agent**: after build, ORACLE's `_detect_discount_abuse()` should whitelist bundle-discount lines (they are HQ-authorized); extend oracle filter to exclude `applied_bundles` lines from abuse flagging
- **MSG91 / WhatsApp**: none at launch; MEGAPHONE campaign can reference bundle promotions in future (bundle_id field on campaign docs)

## Risk notes
- **POS revenue risk** (HIGH): bundle discounts bypass role caps by design — a misconfigured bundle (e.g., 80% off all sunglasses, no min-value trigger) would silently crater margins; mitigate with server-side `max_discount_amount` field, ADMIN-only write gate, and mandatory approval trail on bundle creation
- **Partial-return complexity** (MED): `returns.py` `_priced_return_lines()` currently ignores `applied_bundles`; must be extended or returns will over-refund the bundle discount; ship the return-proration fix in the same PR as bundle creation — never let them diverge
- **Concurrency on `times_used`** (LOW-MED): atomic `$inc` + filter guard (same pattern as vouchers) handles concurrent POS terminals; exhaustion error 409 must be handled gracefully in the POS error boundary (show toast, recalculate without bundle)
- **Feature flag**: ship behind `BUNDLES_ENABLED=true` env flag (default `false`); evaluate endpoint returns empty match list when flag off; zero change to existing order flow when disabled
- **GST compliance**: bundle discount is a trade discount applied before GST (consistent with current per-item and cart-discount treatment); Tally JV narration must make this clear; non-compliant to apply post-GST discounts on the invoice

## Recommendation
Build later (after P1/P2 money-integrity fixes merge). The promo_templates foundation is there, but the partial-return interaction and the pricing-cap bypass make this a careful, non-trivial build. Schedule as Phase 3 after `claude/fix-money-integrity` is merged and verified. Do not ship as a quick win — the partial-return proration gap alone is a financial integrity risk if bundled orders are returned before that code exists.

## Owner decisions
- Q: Which store(s) should bundles be active in at launch? | Why: determines `store_ids` default and whether to build a store-picker UI vs "all stores" toggle | Options: a) all stores from day one / b) pilot in one store first (e.g., Better Vision Ranchi) / c) per-bundle store selection (builds picker UI)
- Q: Should the watch+sunglass example bundle have a hard cap on total discount rupee value per transaction (e.g., max Rs.2000 off regardless of cart size)? | Why: `max_discount_amount` on the reward rule; without it a cart of 10 luxury sunglasses gets 20% off all of them | Options: a) yes, cap per reward line / b) yes, cap per order / c) no cap (rely on category caps only)
- Q: Can a customer benefit from the same bundle more than once in a single order (e.g., buy 2 watches + 2 sunglasses → 2× bundle discount)? | Why: sets `usage_cap_per_order`; most retail chains allow only 1 per visit | Options: a) 1× per order (recommended) / b) unlimited within order / c) 1× per distinct trigger item
- Q: Should bundle discounts show on the printed GST invoice as a line-level trade discount, or as a separate "Promotional Discount" line at the bottom? | Why: affects Tally JV structure and GSTR-1 invoice format; trade-discount-per-line is simpler and GST-compliant | Options: a) per-line trade discount (recommended) / b) separate invoice footer line
- Q: What is the maximum bundle discount percentage you want to allow (hard ceiling in the admin UI)? | Why: prevents accidental configuration of 80%-off bundles; a hard ceiling (e.g., 30%) is enforced server-side regardless of what an admin enters | Options: a) 25% / b) 30% / c) 40% / d) no ceiling (rely on category caps)
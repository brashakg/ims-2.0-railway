# Feature #10: Configurable Ageing Inventory Auto-Liquidation Flags
META: effort=M days=6 risk=MED roi=4 quickwin=no deps=none phase=3

## Existing overlap
- **Non-moving stock report** already exists: `GET /api/v1/reports/inventory/non-moving-stock?days=N&limit=M` (backend/api/routers/reports.py, Phase 6.3) — identifies SKUs with no sales in last N days, flags `never_sold: true`, surfaces `last_sold_at` and `total_sold_all_time`. This is the exact detection engine; the new feature adds the *action layer* on top of it.
- **Expiry/aging helpers** already exist in `backend/api/routers/inventory.py:147-243`: `compute_days_until_expiry()`, `fefo_sort()`, `partition_by_expiry()` — partition into buckets by staleness. `StockAgingReport.tsx` already color-codes by staleness (≥180 days red, ≥120 orange).
- **Aging display** already in `frontend/src/pages/inventory/InventoryPage.tsx` (non-moving tab) and `frontend/src/components/inventory/StockAgingReport.tsx`.
- **Per-product `offer_price`** field already exists on `products` collection and is enforced by `pricing_caps.effective_discount_cap()` — the liquidation price drop plugs directly into this field.
- **Shopify inventory push** already exists via `online_stock_writeback.py` + `nexus_providers.shopify_set_inventory_available()` — online promotion requires only a product-status or collection-membership push, not a new integration.
- **ORACLE agent** (`backend/agents/implementations/oracle.py`) already scans for anomalies hourly; adding a liquidation-flag sweep to its tick avoids a new scheduler.
- **Proposal system** (`backend/agents/proposals.py`) already has tier-1 reversible and tier-2 ask-confirm proposal types — price drops are tier-2 (human confirms), tag application is tier-1 (reversible auto-act).
- **`ai_proposals` collection** already stores before/after state; liquidation proposals fit the existing schema without new collections.

## Reuse (extend, don't rebuild)
- `backend/api/routers/reports.py` → extend the existing non-moving-stock aggregation to return `days_since_last_sold` and `liquidation_eligible` flag per SKU, derived from the new ageing rules config
- `backend/api/routers/inventory.py` → add one new endpoint `PATCH /products/{product_id}/liquidation-tag` (set/clear tag + reason + proposed_price); extend `_build_store_ledger` to surface `liquidation_tag` field
- `products` collection → add three fields: `liquidation_tag` (bool), `liquidation_tagged_at` (datetime), `liquidation_proposed_price` (Decimal128)
- `backend/agents/implementations/oracle.py` → add `_sweep_ageing_inventory()` method called on the existing hourly tick; emits tier-2 proposals for SUPERADMIN approval when an SKU crosses its configured threshold
- `backend/agents/proposals.py` → register `liquidation_flag` as a new reversible tier-1 type (tag-apply is safe to auto-act) and `liquidation_price_drop` as tier-2 (requires human confirm before `offer_price` changes)
- `frontend/src/components/inventory/StockAgingReport.tsx` → extend to show "Liquidation Eligible" badge and proposed price alongside existing staleness color-coding
- `frontend/src/pages/inventory/InventoryPage.tsx` → extend the non-moving tab to add a "Flag for Liquidation" action button (SUPERADMIN/ADMIN only) and a filter for `liquidation_tag=true`

## Data model
**New collection: `liquidation_rules`** (singleton-ish, one doc per rule scope)
```
{
  rule_id: UUID,
  scope_type: "global" | "category" | "brand",   // precedence: brand > category > global
  scope_value: string,        // category name or brand name; null for global
  ageing_threshold_days: int, // e.g. 90 for fast-fashion, 180 for standard, null = "never"
  proposed_price_pct_of_mrp: decimal,  // e.g. 0.60 = 60% of MRP as proposed liquidation price
  auto_push_to_online_sale: bool,      // whether to push to Shopify Sale collection on flag
  notify_superadmin: bool,
  created_by: string,
  updated_at: datetime
}
```

**New fields on existing `products` collection:**
```
liquidation_tag: bool (default false)
liquidation_tagged_at: datetime | null
liquidation_proposed_price: Decimal128 | null
liquidation_rule_id: string | null    // which rule triggered this
liquidation_auto_cleared_at: datetime | null  // stamped when item sells / is cleared
```

**New field on existing `ai_proposals` collection** (no schema change needed — `payload` dict carries):
```
payload.product_id, payload.sku, payload.brand, payload.category,
payload.days_stale, payload.current_offer_price, payload.proposed_price,
payload.rule_id, payload.store_id
```

## Backend

- `GET /api/v1/inventory/liquidation-rules` (SUPERADMIN/ADMIN) — list all rules ordered by precedence (brand > category > global)
- `POST /api/v1/inventory/liquidation-rules` (SUPERADMIN) — create rule; validate `ageing_threshold_days` > 0 or null ("never"), `proposed_price_pct_of_mrp` between 0.30 and 1.00 (no below-cost destruction without explicit override)
- `PUT /api/v1/inventory/liquidation-rules/{rule_id}` (SUPERADMIN) — update rule; change is audit-logged to `audit_logs`
- `DELETE /api/v1/inventory/liquidation-rules/{rule_id}` (SUPERADMIN) — soft-delete (set `active=false`); existing tagged products retain their tag until sold or manually cleared
- `GET /api/v1/inventory/liquidation-candidates?store_id=&category=&brand=` (ADMIN/SUPERADMIN) — returns products currently eligible (days_stale ≥ threshold per applicable rule) with proposed price, whether already tagged, whether a pending proposal exists
- `PATCH /api/v1/inventory/liquidation-tag` (SUPERADMIN/ADMIN) — bulk apply or clear `liquidation_tag` on a list of `product_id`s; accepts `action: "apply"|"clear"`, `proposed_price` (optional manual override), `reason`; writes `audit_logs` entry with before/after; does NOT change `offer_price` directly — that requires a separate SUPERADMIN-approved price edit through existing pricing route
- Extend `backend/agents/implementations/oracle.py` → `_sweep_ageing_inventory()`: runs on hourly tick; resolves applicable rule per SKU (brand-rule wins over category-rule wins over global-rule); skips SKUs with `ageing_threshold_days=null` ("never liquidate"); for newly eligible SKUs not yet tagged, creates a tier-2 `ai_proposal` of type `liquidation_flag`; ORACLE does not touch `offer_price` directly — that stays human-confirmed
- Extend `backend/agents/proposals.py` → add `liquidation_flag` to `reversible_types` (auto-executes tag-apply on Superadmin approval); add `liquidation_price_drop` to `requires_confirmation` (approval records intent, human applies price change via existing pricing route with the proposed value pre-filled in the UI)
- Auto-clear hook: extend `backend/api/routers/orders.py` `create_order()` — when an item with `liquidation_tag=true` is sold, stamp `liquidation_auto_cleared_at` on the product (fail-soft, never blocks sale)

## Frontend

**New page: `/settings/liquidation-rules`** (SUPERADMIN only, linked from Settings sidebar under "Inventory")
- Table of rules showing scope (Global / Category: Frames / Brand: Cartier), ageing threshold ("Never" for null, else "X days"), proposed price ("60% of MRP"), online push toggle
- Inline "Edit" and "Deactivate" per row; "Add Rule" drawer with scope picker (radio: Global / Category / Brand), threshold input, price-percentage slider (30%–100%), toggles
- Scope picker for Brand shows a searchable dropdown of distinct brands from `products` collection; for Category shows the canonical category list

**Extend existing `frontend/src/components/inventory/StockAgingReport.tsx`:**
- Add "Liquidation Eligible" amber badge next to staleness chip for products crossing their rule threshold
- Add "Flag for Liquidation" button (ADMIN/SUPERADMIN only) — opens a confirmation drawer showing proposed price, rule that triggered it, days stale; on confirm calls `PATCH /inventory/liquidation-tag`
- Add filter chip "Show: Liquidation Flagged" to filter table to `liquidation_tag=true` items only

**Extend existing `frontend/src/pages/inventory/InventoryPage.tsx` non-moving tab:**
- Add a summary card: "X SKUs eligible for liquidation · Y already flagged · ₹Z estimated tied capital"
- Tied capital = `sum(cost_price * on_hand_quantity)` for eligible SKUs (uses existing `stock_units` aggregation)

**Extend Jarvis proposal review UI (existing):**
- `liquidation_flag` proposals surface in the existing Superadmin proposal queue with: SKU name, brand, category, days stale, applicable rule, proposed price vs current MRP/offer_price
- "Approve" auto-applies the tag; "Reject" with optional override note closes the proposal

## Business rules

- **Rule precedence (hardlock):** Brand-specific rule always wins over category rule; category rule wins over global. If no rule matches, item is never flagged automatically. Implemented in `_resolve_applicable_rule(product)` — checked at ORACLE sweep time and at the `/liquidation-candidates` endpoint.
- **"Never liquidate" escape hatch (hardlock):** Setting `ageing_threshold_days=null` on a rule means items matching that scope are permanently excluded from auto-flagging. Luxury brands (Cartier, Chopard, Bvlgari) should default to `null` — the seed migration creates these rules; Superadmin can override.
- **Price floor (hardlock):** `proposed_price_pct_of_mrp` cannot be set below 0.30 (30% of MRP) at the rule level. Below-cost liquidation is blocked at the `PATCH /liquidation-tag` endpoint too: if `proposed_price < cost_price`, endpoint returns 422 with message "Proposed liquidation price is below cost — contact SUPERADMIN for write-off approval." This protects margin integrity.
- **`offer_price` is never auto-changed (hardlock):** The feature only sets `liquidation_tag=true` and `liquidation_proposed_price`. Changing `offer_price` always requires a separate SUPERADMIN action through the existing product pricing route — this keeps the POS pricing pipeline untouched.
- **Period lock awareness:** The liquidation tag is a metadata flag, not a financial transaction, so period lock does not apply. However, any price change actioned as a follow-up goes through the existing pricing route which respects period lock.
- **Audit trail (hardlock):** Every tag apply/clear (manual or agent-proposal-executed) writes an `audit_logs` entry with `entity_type="product"`, `action="LIQUIDATION_TAG_APPLIED"|"LIQUIDATION_TAG_CLEARED"`, `before_state={liquidation_tag: false}`, `after_state={liquidation_tag: true, proposed_price: X}`, `actor` (user_id or `ORACLE`).
- **Shopify online push (conditional):** If `auto_push_to_online_sale=true` on the rule, NEXUS adds the product to the Shopify "Sale" collection on tag-apply. This is gated on `DISPATCH_MODE=live` and `IMS_SHOPIFY_WRITES=1` — same guards as all Shopify writes. If gates are off, push is logged as SIMULATED and deferred to next NEXUS tick.
- **De-duplication:** ORACLE checks for an existing `PENDING` proposal of type `liquidation_flag` for the same `product_id` before creating a new one — no stacking.

## RBAC

| Role | Liquidation Rules (config) | View Candidates | Apply/Clear Tag | Approve Proposal |
|---|---|---|---|---|
| SUPERADMIN | Full CRUD | Yes | Yes | Yes |
| ADMIN | Read-only | Yes | Yes (clear only — cannot override rule thresholds) | No |
| AREA_MANAGER | None | Yes (own stores) | No | No |
| STORE_MANAGER | None | Yes (own store) | No | No |
| All others | None | No | No | No |

Rule config (`/settings/liquidation-rules`) is SUPERADMIN-only. Viewing candidates and the flagged-items filter in the aging report is ADMIN and above. Applying/clearing the tag manually is SUPERADMIN (apply) and ADMIN (clear only — e.g., if an item is erroneously flagged). ORACLE proposal approval follows the existing Jarvis proposal RBAC (SUPERADMIN only).

## Integrations

- **ORACLE agent** — sweeps ageing inventory hourly, creates `ai_proposals` for eligible SKUs; no new scheduler needed
- **Shopify (NEXUS agent)** — conditional online "Sale" collection membership push on tag-apply, gated on `IMS_SHOPIFY_WRITES=1` + `DISPATCH_MODE=live`; reuses `nexus_providers.shopify_set_inventory_available()` pattern; collection membership update uses existing `ecom_collections` membership endpoint pattern (Phase 2 online store)
- **MSG91 (MEGAPHONE)** — no customer-facing notification triggered; in-app bell notification to SUPERADMIN when ORACLE creates a liquidation proposal (reuses existing `notifications` collection + bell API)
- **Tally** — no direct impact; if Superadmin follows through and drops `offer_price`, any resulting order discount flows through the existing Tally sales-JV export unchanged

## Risk notes

- **POS pricing pipeline must not be disturbed:** The feature intentionally never touches `offer_price` directly — only writes `liquidation_proposed_price` as a separate field. This is the primary safety decision. Any code path that accidentally overwrites `offer_price` would corrupt live POS pricing. The `PATCH /liquidation-tag` endpoint must be reviewed to confirm it has no side-effect on pricing fields.
- **Shopify push timing:** If `auto_push_to_online_sale=true` and DISPATCH_MODE is live, a product tagged at 2 AM (ORACLE hourly tick) goes online immediately. For luxury items that *do* have a rule with a threshold (not null), this could be brand-damaging. The "never liquidate" null-threshold escape is the control — Superadmin must explicitly set it for prestige brands.
- **Rule precedence ambiguity:** A product that is both a "Frames" category AND a "Gucci" brand needs a clear winner. Brand-beats-category is hardlocked in code so there is no ambiguity, but the settings UI must show the effective rule for any given product so Superadmin can audit what will actually apply.
- **Cost-price data quality:** The price-floor guard (`proposed_price >= cost_price`) depends on `products.cost_price` being accurate. If cost_price is null or stale (common for legacy SKUs without GRN), the floor check falls back to 30%-of-MRP. A `cost_price_missing` warning should surface in the proposal UI.
- **Feature flag:** The entire ORACLE sweep (`_sweep_ageing_inventory`) should be behind a `liquidation_rules_enabled` boolean in the global `liquidation_rules` config (or `agent_config` for ORACLE). Default off on deploy; Superadmin flips it on after reviewing seed rules.

## Recommendation

Build later (Phase 3, after foundational inventory and POS hardening is stable). The non-moving stock report is already live, giving Superadmin manual visibility today. The auto-flagging layer adds genuine value but is not blocking any revenue flow — it is a capital-efficiency tool. The Shopify push dependency (Phase 2 online store collections) should be complete before this ships so the online Sale collection membership is reliable.

## Owner decisions

- Q: Which brands should be "never liquidate" by default in the seed rules? | Why: These brands get `ageing_threshold_days=null` seeded at deploy time; Superadmin can change them but they start protected. | Options: Cartier / Chopard / Bvlgari / Gucci / Prada / Versace / Burberry (all luxury-cap brands already in pricing_caps), or a shorter list, or none seeded (manual setup)
- Q: What ageing threshold should the global default rule use? | Why: This applies to all SKUs with no brand- or category-specific rule; sets the baseline for "too old." | Options: 90 days (fast-moving optical), 120 days, 180 days
- Q: Should liquidation-flagged items automatically appear in a Shopify "Sale" collection when online push is live, or should that always require a separate manual step? | Why: Auto-push is convenient but means a price-drop intent (proposed_price not yet applied to offer_price) is visible online before the price is actually changed — potentially misleading customers. | Options: (a) auto-push to Sale collection on flag (visible online immediately), (b) push only after offer_price is actually updated, (c) never auto-push (manual Shopify collection management only)
- Q: What is the minimum proposed liquidation price as a percentage of MRP — is 30% the right floor, or should it be higher (e.g., 50%)? | Why: The floor prevents below-cost destruction; if cost margins are thin in some categories, a 30% floor might still be below cost. | Options: 30% / 40% / 50% / cost_price + 10% (category-specific)
- Q: Should liquidation-flagged frames still allow the standard in-store staff discount caps, or should they be locked at the proposed_price with zero further discount? | Why: If a frame is already at 60% of MRP as liquidation price, allowing a further 15% staff discount could push the sale below cost. | Options: (a) normal discount caps still apply on top of liquidation price, (b) liquidation price is the floor — no further discount, (c) only SUPERADMIN can apply additional discount on a liquidation item
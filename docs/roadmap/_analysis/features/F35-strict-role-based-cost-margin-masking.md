# Feature #35: Strict Role-Based Cost & Margin Masking
META: effort=S days=2 risk=LOW roi=4 quickwin=yes deps=none phase=1

## Existing overlap
- `backend/api/services/rbac_policy.py` — 12 canonical roles already fully defined; `check_access()` + store-scoped flag pattern is the blueprint.
- `backend/api/middleware/rbac_enforcement.py` — request-time defense-in-depth layer; can be extended for field-level masking signals.
- `frontend/src/stores/posStore.ts` — POS cart items carry `unit_price`, `cost_price` / `cost_at_sale` fields; no masking today.
- `backend/api/routers/orders.py` lines 1210+ — per-item `unit_price`, `cost_at_sale`, `discount_percent` already persisted on order items.
- `backend/api/routers/inventory.py` lines 328-458 — `_build_store_ledger()` surfaces `cost_price` and margin calculations.
- `backend/database/repositories/product_repository.py` — `ProductRepository` returns `cost_price`, `offer_price`, `mrp` as raw fields; no caller-side masking.
- `backend/api/routers/vendors.py` line 989+ — PO unit_price (landed cost) visible to any authenticated caller; no role gate on cost fields.
- **No existing field-level masking anywhere in the codebase — greenfield for the masking layer itself.**

## Reuse (extend, don't rebuild)
- `backend/api/services/rbac_policy.py` — add `COST_VISIBLE_ROLES` constant (set of roles allowed to see cost/margin); import everywhere cost is returned.
- `backend/api/routers/inventory.py` `_build_store_ledger()` — wrap `cost_price` and derived `margin_pct` fields through a new `_mask_cost(field, user_roles)` helper before returning response.
- `backend/api/routers/orders.py` `create_order` + order-detail serialization — strip `cost_at_sale` from response when caller is not in `COST_VISIBLE_ROLES`.
- `backend/database/repositories/product_repository.py` — add a `to_frontend_dict(redact_cost: bool)` helper that nulls `cost_price` / `offer_price` (if internal) fields; call it from every router that returns product records.
- `frontend/src/context/AuthContext.tsx` — `user?.activeRole` is already available; add a derived boolean `canViewCost` to the auth context and thread it into every component that renders cost/margin.
- `frontend/src/pages/pos/POSPage.tsx` + product grid components — already reads from posStore; replace raw cost display with `{canViewCost ? formatCurrency(item.cost) : '—'}`.
- `frontend/src/pages/inventory/InventoryPage.tsx` — cost column in stock ledger table; gate with `canViewCost`.
- `frontend/src/pages/purchase/PurchaseOrderForm.tsx` — `unitCost` field; gate input + display with `canViewCost`.

## Data model
No new collection needed. Add one field to the existing `business_settings` singleton:

```
business_settings._id = "default"
+ cost_visible_roles: string[]   // default ["SUPERADMIN","ADMIN","ACCOUNTANT"]
                                  // persisted in existing business_settings collection
```

This makes the role whitelist runtime-configurable without a deploy.

## Backend
- **`GET /api/v1/settings/cost-visibility`** (SUPERADMIN/ADMIN only) — returns current `cost_visible_roles` list from `business_settings`.
- **`PUT /api/v1/settings/cost-visibility`** (SUPERADMIN only) — updates `cost_visible_roles`; validates that SUPERADMIN is always in the list (cannot lock out self); writes audit_log entry.
- **`backend/api/services/cost_mask.py`** (new pure helper, ~30 lines):
  - `COST_FIELDS = {"cost_price", "cost_at_sale", "unit_cost", "margin_pct", "margin_amount", "gross_profit"}`
  - `is_cost_visible(user_roles: list[str], db) -> bool` — reads `business_settings.cost_visible_roles` (cached in-process, 60 s TTL via `functools.lru_cache` keyed on a generation counter that increments on PUT).
  - `mask_doc(doc: dict, visible: bool) -> dict` — replaces every `COST_FIELDS` key with `None` when `visible=False`; leaves all other fields untouched.
  - `mask_list(docs: list[dict], visible: bool) -> list[dict]` — maps over mask_doc.
- **Apply in existing routers (no new endpoints):**
  - `inventory.py` `_build_store_ledger()` — call `mask_doc(row, visible)` before appending to response.
  - `inventory.py` serial listing — strip `unit_cost` when not visible.
  - `orders.py` order-detail + order-list serializers — strip `cost_at_sale` per item.
  - `products.py` / `catalog.py` product-list and product-detail — strip `cost_price`.
  - `vendors.py` PO line items + GRN — strip `unit_price` (landed cost) when not visible.
  - `finance.py` `pnl_by_category()` and `get_pnl()` — COGS line and margin_pct: if caller lacks visibility, return `null` for those fields and set `cogs_is_masked: true` flag (parallel to existing `cogs_is_estimated` flag).

## Frontend
- **`frontend/src/context/AuthContext.tsx`** — add `canViewCost: boolean` derived from `user?.activeRole` membership in the whitelist; fetch whitelist once on login from `GET /settings/cost-visibility` and store in context (no polling needed; changes take effect on next login).
- **`frontend/src/components/CostCell.tsx`** (new, ~20 lines) — renders `formatCurrency(value)` when `canViewCost`, otherwise renders a neutral `—` in `text-gray-400`; accepts `label` prop for accessibility. Import in all tables.
- **`frontend/src/pages/inventory/InventoryPage.tsx`** — replace raw cost column with `<CostCell value={row.cost_price} />`.
- **`frontend/src/pages/pos/components/ProductCard.tsx`** and cart line items — wrap cost display in `<CostCell />`.
- **`frontend/src/pages/purchase/PurchaseOrderForm.tsx`** — `unitCost` input: if `!canViewCost`, hide field entirely (not just blank); show a static `—` row instead so layout does not shift.
- **`frontend/src/pages/finance/FinanceDashboard.tsx`** — COGS row and Gross Margin card: when server returns `cogs_is_masked: true`, replace value with a padlock icon + "Cost data hidden" label (restrained, no colour beyond text-gray-500).
- **Settings page** (`frontend/src/pages/settings/SettingsPage.tsx` or equivalent) — under an "Access Control" sub-section (SUPERADMIN only): multi-select of the 12 roles for cost visibility; save calls `PUT /settings/cost-visibility`; shows current effective list.

## Business rules
- SUPERADMIN can never be removed from `cost_visible_roles` — hard-blocked at the PUT endpoint (returns 422).
- Masking is **field-level, not page-level**: a Sales Cashier can still access the POS page and inventory list; they just never see cost or margin numbers.
- `cost_at_sale` on a completed order is masked in API responses for non-visible roles but is **never deleted from the DB** — it remains for audit, finance, and payroll reconciliation.
- The masking helper checks roles against the DB whitelist, not a hard-coded list, so the owner can promote CATALOG_MANAGER to cost-visible without a deploy.
- Any change to `cost_visible_roles` is written to `audit_logs` with `before_state` / `after_state` (existing audit pattern in settings.py).
- Export endpoints (CSV, Tally JV) that include cost data must also pass through `mask_doc`; the Tally JV XML export in `nexus.py` is NEXUS/SUPERADMIN-only already, so no change needed there.

## RBAC
| Role | Sees MRP/Offer Price | Sees Cost Price / Landed Cost | Sees Margin % |
|---|---|---|---|
| SUPERADMIN | Yes | Yes (always) | Yes |
| ADMIN | Yes | Yes (default) | Yes |
| ACCOUNTANT | Yes | Yes (default) | Yes |
| AREA_MANAGER | Yes | Owner decision — see Q1 | Owner decision |
| STORE_MANAGER | Yes | No (default) | No (default) |
| CATALOG_MANAGER | Yes | No (default) | No (default) |
| OPTOMETRIST | Yes | No | No |
| SALES_CASHIER | Yes | No | No |
| SALES_STAFF | Yes | No | No |
| CASHIER | Yes | No | No |
| WORKSHOP_STAFF | No cost / price displayed | No | No |
| DESIGN_MANAGER | Yes (catalog) | No | No |

Default `cost_visible_roles = ["SUPERADMIN", "ADMIN", "ACCOUNTANT"]`; owner can add AREA_MANAGER or CATALOG_MANAGER via Settings.

## Integrations
- None (no MSG91 / Shopify / Razorpay / Tally changes; Tally export is already NEXUS/SUPERADMIN-only).
- Jarvis/ORACLE: ORACLE's anomaly narrative (Claude API) may reference margin figures internally — those stay server-side and are never surfaced to non-visible roles via the API response.

## Risk notes
- **POS risk is LOW** — masking is display-only; order pricing, discount caps, and GST calculations are unaffected. No money-flow change.
- **Finance risk is LOW** — COGS and margin are masked in the *response*; the underlying `cost_at_sale` field on order items and `cost_price` on products remain intact for Tally, payroll, and audit.
- **Feature flag**: the `cost_visible_roles` list in `business_settings` is itself the feature flag. Setting it to all 12 roles is equivalent to turning the feature off. Default (3 roles) turns it fully on. No code-level flag needed.
- **LRU cache invalidation**: the 60 s TTL cache in `cost_mask.py` means a role change takes up to 60 s to propagate — acceptable for a settings change that happens rarely. Document this in the Settings UI tooltip.
- **Existing reports with cost data**: `docs/reference/IMS2_Updated_Feature_Status.md` mentions cost-visibility nowhere; no existing frontend test asserts on cost values in non-admin views, so no regression risk.

## Recommendation
Build now (quick win) — pure additive, zero schema migration, zero money-flow change, ships entirely in a new `cost_mask.py` helper + 10-15 call sites + one `CostCell` component. Two-day effort with low regression surface.

## Owner decisions
- Q: Should AREA_MANAGER see cost prices and margins? | Why: AREA_MANAGER oversees multi-store performance; cost visibility lets them compare margins across stores, but also means any Area Manager can see supplier landing costs. | Options: (a) Yes — add to default `cost_visible_roles`; (b) No — leave out of default, owner can grant per-person by promoting to ADMIN temporarily; (c) Partial — Area Manager sees margin % but not raw cost price (requires a second masking tier, adds effort).
- Q: Should the masked `—` placeholder be visible to the staff member, or should the entire cost column be hidden from their table view? | Why: Showing `—` signals that cost data exists but is restricted (staff know it's being hidden). Hiding the column entirely is cleaner but requires different component logic per role. | Options: (a) Show `—` placeholder in column (simpler, consistent table layout); (b) Hide column entirely for non-visible roles (cleaner UI, slightly more frontend work).
- Q: Should CATALOG_MANAGER see cost price when editing a product? | Why: Catalog Manager sets MRP and offer_price; if they can't see cost, they may unknowingly price below cost on a manual edit. | Options: (a) Grant CATALOG_MANAGER cost visibility by default; (b) Keep them masked and rely on the existing server-side `offer_price ≤ MRP` guard + `cost floor` check in orders.py to prevent below-cost sales; (c) Show cost only on the product-edit form for CATALOG_MANAGER, nowhere else (requires a scoped exception in `is_cost_visible`).
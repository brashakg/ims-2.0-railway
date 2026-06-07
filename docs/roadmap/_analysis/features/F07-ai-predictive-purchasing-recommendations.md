# Feature #7: AI Predictive Purchasing Recommendations
META: effort=M days=5 risk=LOW roi=4 quickwin=yes deps=none phase=3

## Existing overlap
ORACLE agent already implements `_propose_reorders()` (backend/agents/implementations/oracle.py) — it detects low-stock SKUs and creates `ai_proposals` docs of type `draft_po` (reversible Tier-1). This is the kernel of the feature, but it fires only when stock falls below `reorder_point` (reactive), not on burn-rate trend (predictive). The proposal workflow and "Act On It"/"Ignore" UI pattern are fully built. TASKMASTER also does `_draft_reorders()` (backend/agents/implementations/taskmaster.py) as a 5-minute sweep — same reactive trigger, same proposal output. The vendor PO creation path (backend/api/routers/vendors.py, `POST /purchase-orders/from-forecast`, lines 759-987) already accepts 90-day velocity data and creates DRAFT POs grouped by `preferred_vendor_id`. All that is missing is the burn-rate computation layer and a dedicated dashboard surface.

## Reuse (extend, don't rebuild)
- `backend/agents/implementations/oracle.py` — extend `_propose_reorders()` to run burn-rate analysis (not just reorder_point breach); add 7-day / 30-day velocity window
- `backend/api/routers/vendors.py:759-987` — `POST /purchase-orders/from-forecast` already creates DRAFT POs from velocity; wire ORACLE's new burn-rate output directly here
- `ai_proposals` collection — existing schema (`proposal_id`, `type=draft_po`, `status`, `before_state`, `payload`, `created_by_agent`) handles the full Act-On-It / Ignore lifecycle without schema change
- `backend/agents/proposals.py` — `ProposalStore.create()`, `approve()`, `reject()` — no changes needed; executor for `draft_po` already auto-creates PO on approval
- `orders` collection — existing `items[].product_id` + `created_at` (BSON datetime) is the burn-rate source; reuse the same aggregation pattern as `reports.py:58-94` (correct datetime bounds, excludes DRAFT/CANCELLED)
- `stock_units` collection — current on-hand per (product_id, store_id, status=AVAILABLE) already queryable
- `products` collection — `reorder_point`, `safety_stock`, `preferred_vendor_id`, `lead_time_days` fields already exist (or add `lead_time_days` as a new field — see Data Model)
- Frontend: existing Jarvis proposals panel (frontend/src/pages/jarvis/JarvisPage.tsx) already renders `ai_proposals` with Act / Ignore buttons — extend filter to show `draft_po` type; no new page required for MVP

## Data model
- **New field on `products`**: `lead_time_days` (int, default 7) — owner-configurable per SKU; used to project days-of-stock-remaining vs replenishment horizon. No new collection.
- **New field on `products`**: `min_order_qty` (int, default 1) — minimum units per PO line (vendor MOQ). Already referenced in vendor logic; add if absent.
- **New field on `ai_proposals.payload`** (no schema change, payload is a free dict): add `burn_rate_7d`, `burn_rate_30d`, `days_of_stock_remaining`, `projected_stockout_date`, `recommended_qty`, `store_id`, `vendor_id` — surfaced in the dashboard card so the owner sees the reasoning, not just the SKU name.
- **No new collection needed.** All state lives in `ai_proposals` (proposal) + `purchase_orders` (executed PO).

## Backend
- `GET /api/v1/reports/inventory/burn-rate` (new, extend `backend/api/routers/reports.py` or `inventory.py`) — aggregates 7-day and 30-day units-sold per (product_id, store_id) from `orders.items`; returns `[{product_id, store_id, burn_rate_7d, burn_rate_30d, on_hand, days_remaining, projected_stockout_date, preferred_vendor_id, lead_time_days}]`; roles ADMIN/SUPERADMIN/AREA_MANAGER; store-scoped for AREA_MANAGER. This is a read-only analytics endpoint with no money or stock mutations.
- `POST /api/v1/jarvis/agents/oracle/trigger-reorder-scan` (new, extend `backend/api/routers/agents.py`) — SUPERADMIN-only; manually triggers ORACLE's burn-rate scan outside the hourly schedule; returns `{proposals_created: N}`; idempotent (ORACLE dedup by SKU+day already in `proposals.py:177-187`).
- Extend `backend/agents/implementations/oracle.py:_propose_reorders()` — replace the single `reorder_point` threshold check with: (1) compute 7-day burn rate from orders aggregation, (2) compute days_of_stock_remaining = on_hand / burn_rate_7d, (3) propose when days_remaining < lead_time_days + safety_days (owner-configurable threshold, default 14), (4) include burn_rate fields in proposal payload. No new endpoint — this is internal agent logic. The existing ORACLE hourly + 22:00 tick picks it up automatically.

## Frontend
- **Extend `frontend/src/pages/jarvis/JarvisPage.tsx`** — add a "Recommended POs" tab or section (alongside the existing activity feed). Filter `ai_proposals` where `type=draft_po` and `status=PENDING`. Each card shows: SKU name, brand, store, on-hand, 7-day burn rate, projected stockout date, recommended order qty, suggested vendor — all from `proposal.payload`. "Act On It" calls existing `approve_proposal()` endpoint (auto-creates DRAFT PO via existing executor). "Ignore" calls existing `reject_proposal()`. No new API calls — the proposal system handles it.
- **Extend `frontend/src/pages/purchase/PurchaseOrderForm.tsx`** — when a PO is created via proposal approval, the DRAFT PO pre-populates from the proposal payload (vendor, items, quantities). This already works via `vendors.py:from-forecast`; no UI change needed.
- **New card on Admin/Superadmin dashboard** (extend `frontend/src/pages/hub/HubPage.tsx` or equivalent dashboard) — "Stockout Risk" summary: count of SKUs with days_remaining < 7 (critical), < 14 (warning). Clicking opens the JarvisPage proposals tab. Restrained: two neutral-tone count chips (red semantic for critical, amber for warning), no decorative elements.

## Business rules
- Proposals are ADVISORY until approved — no auto-ordering, no vendor spend without explicit "Act On It" click (existing Tier-1 reversible draft_po executor creates only a DRAFT PO, not a SENT PO)
- Duplicate suppression: ORACLE's existing dedup key `{type}:{sku}:{date}` (proposals.py:177-187) prevents stacking proposals for the same SKU on the same day
- Burn rate computed from CONFIRMED/DELIVERED orders only (excludes DRAFT/CANCELLED) — matches existing reports.py pattern
- Recommended qty = max(reorder_point, burn_rate_7d × (lead_time_days + safety_days)) − on_hand, rounded up to `min_order_qty` multiple
- If burn_rate_7d = 0 (no sales in 7 days) but burn_rate_30d > 0, use burn_rate_30d / 4.33 as weekly proxy — avoid zero-division phantom recommendations
- Proposals older than 3 days that are still PENDING auto-expire (set status=EXPIRED by ORACLE on next tick) — prevents stale recommendations cluttering the inbox
- All proposal approvals and rejections write to `audit_logs` (existing proposals.py:117-145 already does this)
- PO created on approval is DRAFT status — purchasing manager must still review line items, adjust qty, and manually SEND to vendor (existing PO lifecycle enforced)

## RBAC
- `GET /reports/inventory/burn-rate`: SUPERADMIN, ADMIN, AREA_MANAGER (store-scoped for AREA_MANAGER via existing `validate_store_access` pattern)
- `POST /jarvis/agents/oracle/trigger-reorder-scan`: SUPERADMIN only
- View proposals (Act / Ignore) on JarvisPage: SUPERADMIN, ADMIN (existing Jarvis SUPERADMIN-only gate needs to be extended to ADMIN for this surface — owner decision Q1 below)
- STORE_MANAGER: cannot see proposals or trigger scans; can see resulting DRAFT POs in their store's purchase order queue (existing PO RBAC already allows this)

## Integrations
- **Jarvis / ORACLE agent**: core compute lives here; extends existing `_propose_reorders()` tick
- **vendors.py `from-forecast` endpoint**: proposal executor calls this on approval (already wired)
- No MSG91, Shopify, Razorpay, or Tally involvement — this is a pure internal analytics + workflow feature

## Risk notes
- **Low financial risk**: no money moves until a human sends the PO to a vendor; the auto-executor only creates a DRAFT
- **Data quality dependency**: burn rate is only as good as the orders data; stores that process sales outside IMS (e.g., cash sales logged in Excel) will produce under-estimated burn rates — surface this as a data-completeness warning if orders count < expected
- **SKUs with no `preferred_vendor_id`**: the from-forecast grouping silently drops these; add a "no vendor assigned" warning bucket in the dashboard card
- **Zero-sales new SKUs**: if a product was added this week, burn_rate = 0 even though it may be a reorder candidate; the 30-day fallback above handles this partially — but new SKUs genuinely need manual PO (out of scope for this feature)
- **No feature flag required**: this is an additive read-only + proposal-workflow change with no POS/accounting/pricing mutation; can ship directly to main

## Recommendation
Build now (quick win) — ORACLE's `_propose_reorders()` and the full `ai_proposals` Act/Ignore lifecycle are already in production; the delta is the burn-rate aggregation query (~50 lines), a payload enrichment, and a filtered view on the existing JarvisPage. No new collection, no new RBAC gates to negotiate, no money mutation risk. Estimated 3 backend days + 2 frontend days.

## Owner decisions
- Q: Should Admin-level users (store directors) see and act on Recommended POs, or only Superadmin? | Why: Currently Jarvis is SUPERADMIN-only; opening it to ADMIN means directors can approve draft POs without CEO review — changes the approval authority. | Options: (a) SUPERADMIN only — CEO controls all vendor spend recommendations; (b) ADMIN can view and approve — directors manage their stores' purchasing autonomously; (c) ADMIN can view but not approve — directors see the recommendations, CEO clicks Act On It
- Q: What is the stockout-risk horizon that triggers a recommendation? | Why: This sets how far in advance you want to be warned — too short means you'll run out before stock arrives, too long means you carry excess inventory. | Options: (a) 7 days (tight — fine if vendors deliver in 3-4 days); (b) 14 days (standard — good for most frame/lens vendors with 7-10 day lead times); (c) 21 days (conservative — good for imported luxury brands with long lead times); you can also set different horizons per brand/category
- Q: Should the system generate separate Recommended POs per store, or consolidate all stores into one PO per vendor? | Why: Separate POs give store managers visibility into their own stock needs; consolidated POs may get better pricing from vendors (bulk discount) but require central purchasing to split delivery. | Options: (a) Separate per store — each store's manager sees their own recommended quantities; (b) Consolidated per vendor — one PO covers all stores, delivery split at the vendor level; (c) Start separate, add consolidation later
- Q: How should the system handle SKUs with no vendor assigned in IMS? | Why: The from-forecast grouping drops them silently; they are genuine reorder candidates but cannot be auto-proposed. | Options: (a) Show a "missing vendor" alert list alongside the proposals — someone assigns vendors before next scan; (b) Skip them silently (current behavior); (c) Create a generic "Unassigned Vendor" proposal bucket for manual follow-up
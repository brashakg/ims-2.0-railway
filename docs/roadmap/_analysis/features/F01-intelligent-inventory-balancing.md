# Feature #1: Intelligent Inventory Balancing
META: effort=L days=12 risk=MED roi=4 quickwin=no deps=none phase=3

## Existing overlap
- **Stock transfers** (backend/api/routers/transfers.py, full DRAFT→APPROVED→IN_TRANSIT→RECEIVED lifecycle) already exists; this feature generates the *input* to that flow, not a replacement.
- **Non-moving stock report** (GET /api/v1/reports/inventory/non-moving-stock, backend/api/routers/reports.py and backend/agents/implementations/oracle.py:_detect_sales_anomalies) surfaces dead SKUs but per-store, not cross-store.
- **ORACLE agent** (backend/agents/implementations/oracle.py) already does demand analysis, low-stock anomaly detection, and draft reorder proposals via the proposals system (proposals.py). Extend this — do not build a separate AI engine.
- **TASKMASTER auto-reorder** (backend/agents/implementations/taskmaster.py:_draft_reorders) fires when stock < reorder_point. IBT recommendation is an alternative to buying new — slot in here before a PO is drafted.
- **Stock units + transfers collections** already exist with the right schema (stock_units, stock_transfers, stock_audit).
- **Transfer analytics** (GET /api/v1/transfers/analytics) — reuse for measuring balance improvement post-transfer.

## Reuse (extend, don't rebuild)
- **backend/api/routers/transfers.py** — extend create_transfer() to accept `source=IBT_RECOMMENDATION` and stamp the `recommendation_id` on the draft transfer doc so the manifest links back to the AI rationale.
- **backend/agents/implementations/oracle.py** — add `_recommend_ibt()` method alongside existing `_propose_reorders()`; reuse the same `proposals.py` tier-1 reversible proposal path (type=`inter_store_transfer_suggestion` is already in the reversible whitelist at proposals.py:76-81).
- **backend/api/routers/reports.py** (non-moving-stock endpoint) — extend to add a `?cross_store=true` flag that joins across stores for the same brand/category, exposing the supply/demand gap that the IBT engine will read from.
- **backend/database/repositories/order_repository.py** — reuse sales velocity aggregation (already used by non-moving-stock and ORACLE); add a store-pair velocity comparison helper.
- **frontend/src/pages/inventory/InventoryPage.tsx** — add an "IBT Recommendations" tab alongside existing stock ledger tabs; do not build a new page.
- **frontend/src/components/inventory/StockTransferManagement.tsx** — extend to highlight transfers with source=IBT_RECOMMENDATION in a distinct section at the top.

## Data model
- **New collection: `ibt_recommendations`**
  - Fields: recommendation_id, generated_at, generated_by='ORACLE', status (PENDING_REVIEW | APPROVED | REJECTED | TRANSFERRED | EXPIRED), expires_at (72h TTL), items[] (product_id, brand, category, from_store_id, to_store_id, qty_recommended, velocity_delta, days_stale_at_source, days_stockout_risk_at_dest), rationale (plain-English string from Claude narrative), proposal_id (link to ai_proposals), transfer_id (stamped when approved and transfer created), reviewed_by, reviewed_at, review_notes.
  - Index: (status, expires_at) for ORACLE tick query; (from_store_id, to_store_id) for store-pair lookups.
- **Extend `stock_transfers` collection** — add fields: recommendation_id (nullable), source ('MANUAL' | 'IBT_RECOMMENDATION'), to distinguish AI-suggested transfers from manual ones in analytics.
- **Extend `products` collection** — add optional `ibt_excluded: bool` flag (default false) so owner can blacklist specific SKUs (e.g., demo frames, display-only units) from IBT analysis. Set via catalog editor, not a new page.

## Backend
- **ORACLE agent: `_recommend_ibt()`** (backend/agents/implementations/oracle.py) — runs on hourly tick. Aggregates per (product, store): on_hand from stock_units, 90-day sales velocity from orders. Computes cross-store supply/demand gap at brand+category granularity (not SKU-level to reduce noise). Identifies pairs where: source.on_hand > safety_stock AND days_since_last_sale_at_source > dead_stock_threshold AND dest.velocity > 0 AND dest.on_hand < reorder_point. Ranks by (velocity_delta × qty_available). Caps manifest at configurable max_lines (default 20 items). Skips ibt_excluded products. Writes to ibt_recommendations collection. Creates tier-1 reversible proposal (type=inter_store_transfer_suggestion). Calls ORACLE Claude narrative to generate one-paragraph rationale. Idempotent: skips if a PENDING_REVIEW recommendation already exists for same store-pair generated within 24h.
- **GET /api/v1/inventory/ibt-recommendations** (extend backend/api/routers/inventory.py) — lists recommendations (PENDING_REVIEW, optionally filtered by status/store). Role-gated: AREA_MANAGER, ADMIN, SUPERADMIN. Returns items with velocity_delta and rationale.
- **POST /api/v1/inventory/ibt-recommendations/{id}/approve** — marks status=APPROVED, auto-creates a DRAFT transfer in stock_transfers (source=IBT_RECOMMENDATION, recommendation_id stamped), then pushes through existing transfer approval flow. Writes audit_log entry (before: PENDING_REVIEW, after: APPROVED + transfer_id). Role: AREA_MANAGER, ADMIN, SUPERADMIN.
- **POST /api/v1/inventory/ibt-recommendations/{id}/reject** — marks status=REJECTED, records review_notes. Role: AREA_MANAGER, ADMIN, SUPERADMIN.
- **GET /api/v1/inventory/ibt-recommendations/{id}/manifest** — returns printable IBT manifest (HTML, same pattern as /prescriptions/{id}/print). Lists from-store, to-store, items with qty, rationale, generated_at. Role: same as approve.
- **Extend GET /api/v1/reports/inventory/non-moving-stock** — add `cross_store_demand` boolean. When true, joins with velocity at other stores and returns a `demand_at_other_stores` array per SKU (store_id, velocity_30d, on_hand). This is what feeds ORACLE's gap computation — no separate aggregation pipeline needed.

## Frontend
- **InventoryPage.tsx — new "IBT Recommendations" tab**: Table with columns: Brand / Category / From Store / To Store / Items / Qty / Velocity Delta / Generated / Rationale (expandable) / Status / Actions (Approve / Reject / Print Manifest). Status chips: PENDING_REVIEW (amber), APPROVED (blue), TRANSFERRED (green), EXPIRED (gray). Approve action opens a single-confirm modal ("Create transfer for N items from Store A to Store B?") — no multi-step wizard; it is a business decision, not a technical one. Approve button triggers the POST endpoint; on success the modal shows the auto-created transfer_id as a clickable link to the transfers view.
- **StockTransferManagement.tsx** — add a top section "AI-Suggested Transfers" that filters source=IBT_RECOMMENDATION transfers; keeps existing manual transfers below. No layout change to the existing flow.
- **Print Manifest**: reuse the existing print-dialog pattern (HTMLResponse, auto-opens print dialog onload, A4 portrait). Header shows generated_at, from/to store names, item table, rationale paragraph, ORACLE signature line.

## Business rules
- Dead-stock threshold: on_hand > 0 AND days_since_last_sale >= 90 at source store (hardlock; 90 is the system floor; owner can raise via ibt_settings, never lower below 30).
- Destination eligibility: dest store must have had at least one sale of the product (or same brand+category) in the last 60 days (prevents pushing dead stock to another dead location).
- Minimum transfer quantity: max(1, qty that brings source.on_hand down to safety_stock) — never strip a store below its reorder_point.
- Maximum transfer quantity per line: qty that brings dest.on_hand up to 2× reorder_point — prevents over-stuffing.
- ibt_excluded products are never included.
- A recommendation expires 72h after generation (TTL index on expires_at). Expired recommendations cannot be approved; a fresh run must regenerate.
- Approval auto-creates the transfer in DRAFT status (not IN_TRANSIT); the store manager at the source store must still ship using the existing transfers flow. No stock physically moves on approval — the proposal system's reversible flag ensures clean rollback if the transfer is later cancelled.
- Audit trail: every approve/reject writes an immutable agent_audit_log entry (before_state: PENDING_REVIEW, after_state: APPROVED/REJECTED, agent_id: ORACLE, tier: 1).
- No POS interaction; no money changes hands; no accounting entries. This is a logistics-only feature.

## RBAC
- View recommendations + print manifest: AREA_MANAGER, ADMIN, SUPERADMIN (store managers see only recommendations involving their own stores via store_ids scoping)
- Approve / reject: AREA_MANAGER (for transfers within their area), ADMIN, SUPERADMIN
- ORACLE generates recommendations autonomously (no role; agent context)
- STORE_MANAGER: read-only view of transfers involving their store (via existing StockTransferManagement filter, no new gate needed)
- All other roles: no access

## Integrations
- **ORACLE agent** — generates recommendations on hourly tick; uses Claude API (ANTHROPIC_API_KEY) to write the one-paragraph rationale (same claude_client.py pattern already in oracle.py); fails soft if key absent (rationale = empty string, recommendation still created).
- **MSG91 / MEGAPHONE** — optional: when a recommendation is generated, MEGAPHONE can send an in-app notification to AREA_MANAGER/ADMIN ("ORACLE found X items to balance across stores — review by [expiry_time]"). Reuse existing in-app notifications.py bell pattern; no WhatsApp message for this.
- **Tally / Shopify / Razorpay** — none. IBT is internal logistics only.

## Risk notes
- **Stock correctness dependency**: recommendations are only as good as on_hand accuracy. If stock_units is stale (missing GRNs, unprocessed returns), ORACLE will recommend meaningless transfers. Pair with periodic stock-count reminder (existing stock audit flow).
- **Multi-store auth boundary**: velocity aggregation queries ALL stores' orders in one pipeline. Ensure the ORACLE agent context uses the global DB connection (not store-scoped token) — it already does (agent ticks run as background service, not per-user request).
- **No feature flag required**: this feature generates DRAFT proposals and recommendations only; no auto-execution without human approval. Zero revenue risk. The existing tier-1 reversible proposal gate is the safety net.
- **Clock risk on expiry**: if ORACLE runs infrequently (agent disabled, Redis down), recommendations expire before review. Surface a warning in the UI when a recommendation is within 12h of expiry.
- **Moderate implementation complexity** (L estimate): the aggregation pipeline joining velocity at multiple stores is the hardest part; the rest reuses existing infra.

## Recommendation
Build later (Phase 3) — valuable for cash-flow optimisation but requires reliable stock data quality first; run stock audit hardening (stock-count reconciliation) before activating ORACLE IBT generation, otherwise recommendations will be noise.

## Owner decisions
- Q: What is your dead-stock threshold — 90 days with zero sales, or a lower number like 60 days? | Why: Lower threshold = more aggressive rebalancing, more transfers to approve; higher = fewer but higher-confidence recommendations. | Options: 60 days (aggressive) / 90 days (default, recommended) / 120 days (conservative, fewer transfers)
- Q: Should this apply to ALL product categories, or exclude certain ones — for example, high-value luxury frames (Cartier/Chopard) or contact-lens consumables? | Why: Luxury frames may need to stay at specific stores for brand-image reasons; CL consumables have short shelf life and high velocity so standard reorder is better. | Options: All categories / Exclude luxury brands / Exclude CL consumables / Exclude both
- Q: Who should be allowed to approve IBT transfers — Area Manager alone, or also Store Managers for transfers within their own store? | Why: Giving Store Managers approval power is faster but means less oversight; Area Manager gate ensures cross-store coordination. | Options: Area Manager and above only (default) / Store Manager can approve for their own store as source
- Q: Should the system send an in-app alert to Area Managers when a new recommendation is generated, or only show it passively in the Inventory tab? | Why: Active alerts drive faster review before 72h expiry; passive display risks recommendations expiring unnoticed. | Options: In-app bell notification (recommended) / Passive tab only / Both bell + WhatsApp summary to Area Manager
- Q: Maximum number of IBT transfer lines per recommendation batch — 20 items (default) or a different cap? | Why: Smaller batches are easier to review and approve; larger batches reduce admin overhead but may overwhelm the receiving store. | Options: 10 lines (focused) / 20 lines (default) / 50 lines (bulk)
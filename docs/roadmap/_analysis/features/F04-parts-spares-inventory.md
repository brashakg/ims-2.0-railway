# Feature #4: Parts & Spares Inventory
META: effort=M days=8 risk=MED roi=4 quickwin=no deps=none phase=3

## Existing overlap
- `workshop_jobs` collection and `backend/api/routers/workshop.py` — repair jobs already track `frame_details`, `lens_details`, `fitting_details`; spare-part consumption is the missing inventory leg
- `stock_units` collection and `backend/api/routers/inventory.py` — serialized per-unit inventory engine (status AVAILABLE/RESERVED/SOLD/DAMAGED) already handles unit lifecycle; spares need the same engine with a "CONSUMED" terminal status added
- `backend/api/routers/vendors.py` + `purchase_orders` / `grns` collections — GRN already mints `stock_units` with `source_type='GRN'`; spare parts arrive the same way
- `backend/api/services/purchase_match.py` + `backend/api/routers/purchase_invoices.py` — 3-way match and AP invoicing already apply; no new AP workflow needed
- `backend/api/routers/returns.py` + `_restock_good_items()` — restocking logic already handles condition-based return to AVAILABLE; spare returns from completed jobs can reuse this path
- `backend/api/routers/orders.py` `_compute_per_category_gst` — GST rate resolution by category already works; a new `SPARES` item_type maps cleanly
- `pricing_caps.py` / `role_caps.py` — discount caps already enforced per category; SERVICE 10% cap applies to repair orders
- `backend/api/middleware/rbac_enforcement.py` + `backend/api/services/rbac_policy.py` — RBAC enforcement is in place; just register new routes in the policy table

## Reuse (extend, don't rebuild)
- `stock_units` collection — add `item_class='SPARE'` discriminator field; existing status machine (AVAILABLE → RESERVED → CONSUMED/DAMAGED) maps exactly; `inventory.py` ledger aggregation already groups by store_id + status
- `backend/api/routers/inventory.py` — extend `_build_store_ledger()` to include SPARE class; extend `/low-stock` endpoint to surface spare parts below reorder_point; reuse barcode generation (`generate_barcode`) and barcode lifecycle trace (`GET /barcode/{barcode}/trace`) without modification
- `backend/api/routers/vendors.py` — no change needed; POs and GRNs already accept any product_id; spare parts are products with category=SPARES
- `backend/api/routers/purchase_invoices.py` — no change; 3-way match and AP booking apply unchanged
- `backend/api/routers/workshop.py` — extend job create/update to accept `spare_parts_used[]` (product_id + qty consumed); on job COMPLETED, atomically consume reserved spare units (mirrors `commit_for_workshop_dispatch` pattern in lens_stock_hook)
- `backend/api/routers/orders.py` — extend `create_order` to allow `item_type='SERVICE'` line items representing repair labour + `item_type='SPARE'` lines for parts billed to customer; GST at 18% for spare parts (accessories rate) unless owner defines separate HSN
- `backend/database/repositories/product_repository.py` — reuse `StockRepository.find_low_stock()` with an added filter on `item_class='SPARE'`
- `backend/agents/implementations/oracle.py` + `backend/agents/proposals.py` — ORACLE already proposes draft reorders for low stock (tier-1 reversible); spare parts feed into the same reorder proposal loop without any agent changes
- `backend/api/services/audit_log.py` (or `audit_logs` collection) — immutable audit trail already used by workshop; all spare consumption events write here
- `frontend/src/pages/workshop/WorkshopPage.tsx` — extend job detail panel to show spare parts picker and consumed-parts summary
- `frontend/src/pages/inventory/InventoryPage.tsx` — extend existing low-stock tab filter to show SPARE class items separately

## Data model
- **Extend `products` collection** — add fields: `item_class: "SPARE" | "PRODUCT"` (default "PRODUCT"), `compatible_brands: [str]`, `compatible_frame_types: [str]`, `repair_labour_rate_per_unit: float` (optional: labour cost bundled with the spare for margin calculation)
- **No new collection for spares catalog** — spares are products with `item_class='SPARE'`; all existing product CRUD, barcode, pricing, and vendor-link logic applies unchanged
- **Extend `workshop_jobs` collection** — add field: `spare_parts_used: [{product_id, sku, name, qty_consumed, unit_cost_at_consume, stock_unit_ids: [str], consumed_at, consumed_by}]`; `spare_parts_reserved: [{product_id, qty_reserved, stock_unit_ids: [str], reserved_at}]`
- **Add terminal status to `stock_units`** — add `"CONSUMED"` to the status enum alongside existing AVAILABLE/RESERVED/SOLD/DAMAGED/SCRAPPED/TRANSFERRED; stamp `consumed_at`, `consumed_by`, `job_id` when a spare is pulled for a repair job
- **New collection `repair_orders`** — links a workshop_job to a POS order for billing: `{repair_order_id, job_id, order_id, store_id, customer_id, items: [{product_id, qty, unit_price, gst_rate, is_spare, is_labour}], total, status: OPEN|INVOICED|PAID, created_at, created_by}`; this is the bridge between workshop (cost centre) and POS (revenue)

## Backend
- `POST /api/v1/inventory/spares` — create a spare-part product (sets `item_class='SPARE'`); reuses existing product create logic with enforced category=SPARES; CATALOG_MANAGER / ADMIN only
- `GET /api/v1/inventory/spares` — list spare parts with on-hand qty, reorder status, compatible_brands filter; extends `_build_store_ledger()` result filtered to `item_class='SPARE'`
- `POST /api/v1/workshop/jobs/{job_id}/spare-parts/reserve` — reserve N units of a spare for this job (atomic `find_one_and_update` on `stock_units` status AVAILABLE → RESERVED, stamps `job_id`); mirrors `lens_stock.reserve` atomicity pattern; WORKSHOP_STAFF / STORE_MANAGER
- `POST /api/v1/workshop/jobs/{job_id}/spare-parts/consume` — called when job reaches COMPLETED; flips reserved units to CONSUMED, appends to `spare_parts_used[]`, writes audit row; idempotent on retry; WORKSHOP_STAFF / STORE_MANAGER
- `POST /api/v1/workshop/jobs/{job_id}/spare-parts/release` — release reserved-but-unconsumed units back to AVAILABLE (job cancelled/voided); WORKSHOP_STAFF / STORE_MANAGER
- `POST /api/v1/repair-orders` — create a repair order from a completed workshop job; pulls `spare_parts_used[]` + labour from job, computes GST (18% spares, 18% labour under SAC 998719 unless owner provides different HSN), links to a POS order via `create_order`; SALES_CASHIER / STORE_MANAGER
- `GET /api/v1/repair-orders` — list repair orders by store/status/date; STORE_MANAGER / ACCOUNTANT / ADMIN
- `GET /api/v1/repair-orders/{id}` — repair order detail with linked job + invoice number; same roles
- `GET /api/v1/inventory/spares/consumption-report` — per-spare consumption over date range; used for reorder planning and profitability analysis; STORE_MANAGER / ACCOUNTANT / ADMIN
- Extend `POST /api/v1/workshop/jobs/{job_id}/status` (existing) — when status transitions to COMPLETED, auto-trigger consume if `spare_parts_reserved` is non-empty and no manual consume call was made (fail-soft: log warning if consume fails, do not block COMPLETED transition)
- Extend RBAC policy table in `backend/api/services/rbac_policy.py` — register all new `/inventory/spares*` and `/repair-orders*` routes with their role gates

## Frontend
- **Spare Parts Catalog tab** on `/inventory` (extend `InventoryPage.tsx`) — table of `item_class='SPARE'` products: name, SKU, barcode, on-hand, reorder point, compatible brands; inline "Add Stock" opens existing GRN flow pre-filtered to spares; low-stock rows highlighted in amber (reuse existing low-stock color coding from `StockAgingReport.tsx`)
- **Spare Parts Panel** on workshop job detail (extend `WorkshopPage.tsx` job drawer) — searchable spare picker (by name / SKU / barcode scan); shows on-hand at current store; "Reserve" button per line; reserved parts list with "Release" option; on job completion, "Confirm Consumption" button posts consume endpoint; consumes are shown read-only after COMPLETED
- **Repair Order modal** — triggered from job detail after COMPLETED; pre-populated with consumed spares + labour line; editable unit prices (capped at SERVICE 10% discount cap); shows GST breakdown; "Generate Invoice" posts to `/repair-orders` and opens existing POS invoice print flow (`receiptFormat.ts` / A4 invoice)
- **Consumption Report** under Inventory → Reports tab (extend existing reports page) — date range picker, per-spare bar chart (qty consumed), per-job breakdown table, CSV export (reuse existing CSV sanitization from `frontend/src/utils/csvExport.ts` to prevent CSV injection — already in place per PR #549)
- All UI: neutral/monochrome, single accent colour for semantic states only (reorder=amber, consumed=gray, available=green); no emojis; restrained executive aesthetic per standing design constraint

## Business rules
- A spare part unit can only be consumed against one job (one-to-one unit-to-job linkage via `job_id` on stock_unit)
- Consumption is only allowed when job status is IN_PROGRESS or COMPLETED; not on PENDING or CANCELLED jobs
- A cancelled job must release all reserved spare units (automatic on cancellation via `release` call)
- Spare parts pulled for a repair are billed to the customer at retail price (MRP or offer_price); the margin is MRP minus `unit_cost_at_consume` (from the PO/GRN cost); COGS is stamped at consume time, not billing time, so P&L is accurate
- GST on spare parts billed to customer: 18% (accessories rate) unless a specific HSN code with a different rate is assigned on the product; intra-state = CGST+SGST, inter-state = IGST — reuses existing `_compute_per_category_gst` logic
- Discount cap on repair orders: SERVICE category cap (10%) applies to labour lines; SPARE category cap applies to spare lines; role caps (SALES_CASHIER 10%, STORE_MANAGER 20%) further constrain
- Repair orders that include a spare part sale MUST generate a GST-compliant invoice with consecutive serial number (existing `next_invoice_number` atomic counter applies)
- Reorder point breach triggers ORACLE's existing `_propose_reorders()` — no new agent logic needed; the draft-PO proposal is tier-1 reversible and auto-executes on SUPERADMIN approval
- Audit trail: every reserve, consume, release, and repair-order creation writes an immutable row to `audit_logs`; before_state (stock_unit.status) and after_state captured

## RBAC
| Role | Spare Parts Catalog | Reserve / Consume / Release | View Repair Orders | Create Repair Order / Invoice |
|---|---|---|---|---|
| SUPERADMIN / ADMIN | Full CRUD | Yes | All stores | Yes |
| AREA_MANAGER | Read + reorder trigger | Yes (own stores) | Own stores | Yes |
| STORE_MANAGER | Read + reorder trigger | Yes | Own store | Yes |
| CATALOG_MANAGER | Full CRUD (spare products) | No | No | No |
| ACCOUNTANT | Read + consumption report | No | Own store (read) | No |
| WORKSHOP_STAFF | Read (own store on-hand only) | Yes (own store) | No | No |
| SALES_CASHIER / SALES_STAFF | No catalog access | No | No | Yes (create invoice from completed job) |
| OPTOMETRIST / CASHIER / DESIGN_MANAGER | No access | No | No | No |

## Integrations
- **Tally** — repair order invoices flow into the existing Tally sales-JV XML export (NEXUS nightly 23:00); spare-part purchases flow through existing vendor-bill AP JV; no new Tally integration work needed
- **ORACLE agent** — existing `_propose_reorders()` picks up spares automatically once they are products with reorder_point set; no agent code change
- **MSG91 / WhatsApp** — MEGAPHONE can send "your repair is ready, please collect" via the existing workshop ready-notification (`notify_ready` in `workshop.py:1379`); no new channel integration needed
- **Shopify** — spare parts are NOT listed online; `ecom` sub-doc absent by policy; no Shopify push for spare products
- **Razorpay** — payment collection uses existing POS tender flow; no new payment integration

## Risk notes
- **POS / money risk (HIGH)**: the repair-order-to-POS-order link creates a billing path that touches GST, invoice serials, and AR; this must ship behind a feature flag (`ENABLE_REPAIR_ORDERS=false` default) and be enabled per-store by ADMIN only after accounting sign-off; any bug here produces incorrect tax invoices
- **Stock unit status extension (MED)**: adding `CONSUMED` to an existing enum affects all inventory queries that filter by status; audit every aggregation in `inventory.py` that uses `{"status": {"$in": [...]}}` to ensure CONSUMED units are excluded from on-hand counts (they should be, since on-hand queries include only AVAILABLE + RESERVED)
- **Concurrent reservation race (LOW-MED)**: the atomic `find_one_and_update` pattern from `lens_stock.reserve` prevents double-reservation; same pattern applied here eliminates the race, but the implementation must copy that guard-in-filter approach exactly — not a simple `update_one`
- **COGS accuracy (MED)**: `unit_cost_at_consume` must be frozen at consume time from the GRN/PO cost, not looked up later from product.cost_price (which can change); same pattern as `cost_at_sale` on order line items; if no cost is on the stock_unit (pre-feature GRN), fall back to product.cost_price and flag `cogs_is_estimated=true` in the repair order
- **Workshop job complexity creep (LOW)**: adding spare-parts tracking to the job detail makes the workshop UI heavier; keep the spare panel collapsed by default; show consumed count badge on the job card so the tab stays clean

## Recommendation
Build in Phase 3 after the current financial hardening PRs land. This is not a quick win (8 days, MED risk) but has strong ROI (4/5): it converts currently untracked repair revenue into audited, GST-compliant invoices and prevents stock shrinkage from unrecorded spare consumption. Do not fold into Workshop (different revenue + cost-centre concern) or Inventory (needs billing integration). Ship behind `ENABLE_REPAIR_ORDERS` feature flag.

## Owner decisions
- Q: Which spare part categories should be tracked at launch — frame parts (temples, nose pads, hinges), watch parts (batteries, straps, crowns), hearing-aid parts (domes, tubes, wax guards), or all? | Why: determines how many SKUs to pre-seed in the catalog and whether category-level reorder rules differ | Options: (a) all categories from day one / (b) frame + watch only (highest volume) / (c) one category as pilot for 30 days then expand
- Q: Should spare parts billed in a repair order be invoiced as a separate GST invoice, or bundled with the repair labour into a single service invoice using SAC code 998719? | Why: separate invoices simplify HSN-level GSTR-1 but create more paperwork; bundled needs a single mixed-supply GST rate decision (the higher of goods vs service rate applies under mixed supply rules — consult your CA) | Options: (a) separate invoice per component type / (b) single repair invoice with itemised lines and blended GST at 18%
- Q: What is the reorder point and safety stock quantity for each spare category — or should workshop staff set these manually per SKU? | Why: determines whether ORACLE auto-proposes replenishment or whether it is purely manual | Options: (a) fixed defaults (e.g., 5 units reorder point, 2 safety stock) applied to all spares at creation / (b) manual per-SKU configuration by Catalog Manager / (c) no reorder automation for spares initially
- Q: Should customers receive an itemised line showing the spare part name and price on the repair invoice, or a single "Parts & Labour" consolidated line? | Why: itemised is GST-compliant best practice and supports returns/disputes; consolidated is simpler but may cause GSTR-1 classification issues | Options: (a) always itemised / (b) consolidated with breakdown on request
- Q: Should the same repair-order flow apply to all six stores, or roll out to Better Vision stores first and WizOpt later? | Why: affects which store_ids the ENABLE_REPAIR_ORDERS flag activates for, and which store's accounting Avinash wants to validate first | Options: (a) all stores simultaneously / (b) Better Vision (BV) stores first, WizOpt after 60 days
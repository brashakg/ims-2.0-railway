# Subsystem recon (existing IMS 2.0 capabilities)



# === POS / Orders / Returns / Tender ===
Excellent! Now I have enough information to produce a comprehensive audit report. Let me compile the findings:

Based on my thorough audit of the IMS 2.0 codebase, here is a comprehensive report on the existing POS/Orders/Returns/Tender implementations:

---

## Capabilities

### Payment & Split-Tender Handling
- **Split-Tender (Multiple Tenders)**: Full support (backend/api/routers/orders.py:2386+)
  - `add_payment()` records multiple payment entries per order
  - Tenders: CASH, UPI, CARD, BANK_TRANSFER, EMI, CREDIT, GIFT_VOUCHER, LOYALTY
  - Over-tender guard: cash (non-CREDIT) cannot exceed grand_total (orders.py:2386+)
  - CREDIT tenders: pay-later promise, excluded from amount_paid, sticky audit flag

### EMI (Equated Monthly Installment)
- **EMI Schedule Reconciliation** (orders.py:737-788): `build_emi_schedule(principal, annual_rate, months)`
  - Monthly EMI rounded to paise, last installment absorbs rounding to match total exactly
  - Fields captured: tenure_months, annual_rate, monthly_emi, last_installment, total_payable, interest_amount
  - Configurable annual rate per store (default 12%)
  - POS-2: emi_principal carries financed balance for correct schedule

### Discounts & Approvals
- **Per-Item Discounts** (orders.py:1210+)
  - Explicit discount_percent + discount_approved_by + discount_reason (capped 200 chars)
  - Server-side price ceiling enforcement: unit_price ≤ MRP or offer_price (if HQ-discounted)
  - Cost floor: cannot sell below COGS for priced lines
  - BUG-118: HQ-discounted items (offer<MRP) block further store discounts unless ADMIN/SUPERADMIN
  
- **Discount Cap Enforcement** (orders.py:1195-1312)
  - Role-aware user cap via `effective_discount_cap()` from services/role_caps.py
  - Category + luxury-brand cap via `pricing_caps.effective_discount_cap()`
  - Effective discount = MAX(explicit_percent, implied_percent_from_unit_price)

- **Cart-Level Discount** (posStore.ts:146-149, orders.py:499)
  - Applied AFTER per-item discounts to taxable subtotal
  - Approval tracking: cart_discount_approved_by + cart_discount_reason (capped 200 chars)

### GST Calculations & Split
- **Per-Category GST Resolution** (orders.py:107-196 `_compute_per_category_gst`)
  - Resolution order: explicit HSN → item_type (if known) → category → DEFAULT (18%)
  - Per-category rates: 5% for frames/lenses/contact-lenses, 18% otherwise (gst_rates.py)
  - GST Pricing Mode: INCLUSIVE (default, QA F3) or EXCLUSIVE (legacy flag-flippable)
  - Inclusive: taxable = gross/(1+rate), tax = gross-taxable
  - Exclusive: taxable = gross, tax = gross*rate
  - Returns GST breakup per rate for credit-note / GSTR-1 reversal

- **GST Inter/Intra-State Split** (finance.py:330-360 `_split_output_tax`)
  - Inter-state (seller state ≠ buyer state) → IGST
  - Intra-state or unknown state → CGST + SGST (50/50)
  - Residual paise on SGST for exact matching

### Invoice & Receipt Generation
- **Serial Numbering (GST Rule 46(b))** (order_repository.py:446-507 `next_invoice_number`)
  - Format: `{PREFIX}/{FY}/{serial}` e.g. `BV/2026-27/000123`
  - Atomic per-(prefix, FY): find_one_and_update with $inc on counters collection
  - FY resets annually (Apr-Mar); serial resets each FY
  - Configured prefix: per-store > global invoice_settings > DEFAULT "INV"
  - Fallback (DB-less): timestamp-derived suffix
  - UNIQUE sparse index on invoice_number to prevent duplicates

- **Invoice Fields** (orders.py:243-244, order_repository.py:303-308)
  - Stored fields: invoice_number, invoice_date (datetime.now() on set_invoice)
  - Response fields: invoiceNumber, invoiceDate (frontend compatibility)

- **PDF/Receipt**: Not implemented in backend; frontend would call FE receipt component
  - Thermal 80mm + A4 tax invoice support mentioned in POSPage.tsx comments
  - Digital receipt (WhatsApp, Email) infrastructure not yet in code

### Returns & Refunds
- **Return Types** (returns.py:69, 113-127 `ReturnCreate`)
  - RETURN: issue refund to original payment method
  - CREDIT_NOTE: issue store credit (ledger.credit_note_ledger)
  - EXCHANGE: settle returned value against replacement items; collect/refund difference

- **Refund Quantity Enforcement** (returns.py:302-348)
  - Per-line qty cap: purchased_qty - already_returned_qty
  - Two-layer guard: pre-validation scan + atomic find_one_and_update claim
  - returns_qty field on order.items tracks cumulative returned units
  - Prevents over-refund and double-submit race

- **Monetary Cap (BUG-096)** (returns.py:1099-1122)
  - Total refunded across ALL returns ≤ order.amount_paid
  - Blocks unlimited over-refund and refund on never-paid orders
  - Checked BEFORE atomic claim so phantom reservation never lingers

- **Tender Enforcement on Returns** (returns.py:552-566 `_order_payment_method`)
  - Refund method defaults to original order payment method
  - Recovers from order.payment_method / payment_mode / first payments[0].method
  - Returned to SOURCE (method used at sale) by default

- **GST Gross-Up for Refunds** (returns.py:493-549 `_priced_return_lines`)
  - Resolves GST-INCLUSIVE gross the customer was actually billed
  - Uses stored (taxable_value + tax_amount) / quantity per line
  - Correct for BOTH inclusive and legacy exclusive orders
  - Falls back to NET * (1+rate) for older orders without taxable/tax fields

- **Restock Engine** (returns.py:768-911 `_restock_good_items`)
  - GOOD-condition units go back to AVAILABLE stock
  - Reactivates original SOLD unit from that order when possible
  - Mints fresh AVAILABLE unit (source_type=RETURN, source_id=return_id) when original not found
  - Idempotent: already-applied returns are no-ops on retry
  - Fail-soft: stock write failures leave applied=False for retry

- **Restocking Fee** (returns.py:1084-1097)
  - Optional Rs deduction for damaged/opened goods (must be ≥ 0, ≤ gross_refund)
  - Not applicable to EXCHANGE (422 error)
  - Stamped on credit_note ledger as gross/fee/net trail

- **Loyalty Reversal on Returns** (returns.py:1191-1220 BUG-099)
  - Claw back points earned on original order
  - Restore points redeemed on it
  - Fail-soft: never blocks return; flags for reconciliation if it fails

### Day-End & Cash Register Flow
- **NOT IMPLEMENTED**: No day-end / cash register settlement endpoints found
- **Period Locking** (finance.py:446-480 `check_period_locked`)
  - Guard on orders.py:1073, returns.py:983
  - Prevents posting to closed accounting months (raises 423)
  - Uses period_locks collection (month/year)

- **AR Aging** (finance.py:931-957, 959-1045)
  - Due date = created_at + customer.credit_terms_days (fallback 30)
  - Buckets: current, 0_30, 31_60, 61_90, 90_plus days overdue
  - Reads from orders with payment_status IN (UNPAID/PARTIAL/CREDIT) + status NOT IN (DRAFT/CANCELLED)

- **Cash-Inflow Tracking** (finance.py:1112-1223)
  - Orders with payment_status=PAID + status NOT IN (DRAFT/CANCELLED)
  - Expense outflows scoped by expense_date (date-only string), NOT date field
  - Vendor payments read from vendor_payments collection

### Loyalty & Vouchers
- **Gift-Voucher Redemption** (vouchers.py:170-261 `redeem_voucher_atomic`)
  - Atomic find_one_and_update: filter checks status=ACTIVE, balance≥amount, not expired
  - Flips to REDEEMED when fully drained
  - No read-modify-write window, concurrent double-spend impossible
  - Used by both HTTP endpoint and POS payment path (orders.py)

- **Loyalty Points (Deferred)** (posStore.ts:166-167, orders.py:386-391)
  - POS-3: pendingLoyaltyRedeem intent deferred until order confirm
  - LOYALTY tender: recorded after points are debited by /loyalty/redeem
  - Prevents double-charge: points burned but order shows amount owing

---

## Collections

| Name | Purpose |
|------|---------|
| **orders** | Sales order master; fields: order_id, order_number, status, grand_total, tax_amount, amount_paid, payment_status, payments[], items[], invoice_number, invoice_date, created_at, status_history[], returned_qty per line |
| **returns** | Customer returns/exchanges/credit-notes; fields: return_id, order_id, return_type, items[], replacement_items[], gross_refund, restocking_fee, net_refund, gst_breakup, refund_amount, refund_method, credit_entry, restock_applied, restock_stock_ids[], idempotency_key, created_at |
| **credit_note_ledger** | Store credit issuances (used by returns + customers routers); fields: customer_id, entry_type (ISSUED/REDEEMED), amount, balance_after, reason, ref, created_at |
| **vouchers** | Gift cards / discount vouchers; fields: voucher_id, code (UNIQUE), type (GIFT_CARD/DISCOUNT), balance, status (ACTIVE/REDEEMED/EXPIRED/CANCELLED), initial_amount, expiry_date, redemptions[], created_at |
| **stock_units** | Serialized inventory; fields: stock_id, product_id, store_id, status (AVAILABLE/SOLD/RESERVED/DAMAGED/SCRAPPED), quantity, order_id (when SOLD), returned_at, source_type (RETURN) |
| **counters** | Atomic invoice serial allocation; key: `invoice:{prefix}:{fy_start_year}`, seq (incremented) |
| **period_locks** | Month/year closures for accounting; fields: month, year |
| **points_log** | Daily staff incentive scores (Pune Module ii); fields: store_id, date, staff_id, 9 category scores, total, eligibility, visufit_gate_applied |
| **incentive_settings** | Eligibility bands, Visufit gate, payout settings per store; keyed by store_id |

---

## Key Files

| Path | Purpose | Key Functions/Lines |
|------|---------|---------------------|
| backend/api/routers/orders.py | Order create/payment/invoice | create_order (1027), add_payment (2386), _compute_per_category_gst (107), build_emi_schedule (737), generate_order_number (706), next_invoice_number (via repo) |
| backend/api/routers/returns.py | Return/exchange/credit-note create | create_return (928), _priced_return_lines (493), _claim_returnable_qty (371), _restock_good_items (768), _issue_store_credit (583), retry_restock (1469) |
| backend/api/routers/vouchers.py | Gift-card redemption | redeem_voucher_atomic (170), _redeem_failure_reason (264) |
| backend/api/routers/points.py | Daily points logging | create_daily (415), _conversion_score_for (194), _build_row (318) |
| backend/api/routers/finance.py | Financial aggregation | gst_reconciliation (253), pnl_by_category (410), check_period_locked (459), _split_output_tax (330), get_outstanding (959), cash_flow_forecast (1448) |
| backend/database/repositories/order_repository.py | Order persistence | add_payment (199), next_invoice_number (446), _resolve_invoice_prefix (384), create_unique (509), set_invoice (303) |
| backend/database/repositories/base_repository.py | Generic CRUD | create, find_by_id, update, aggregate |
| frontend/src/stores/posStore.ts | Client-side state | POS workflow: customer → prescription → products → review → payment → complete; split tenders, cart discount, delivery scheduling |
| frontend/src/pages/pos/POSPage.tsx | POS entry point | ProtectedRoute guard, POSLayout component mount |
| frontend/src/pages/orders/OrdersPage.tsx | Orders list/detail | Order browsing, payment collection modal |
| frontend/src/pages/orders/ReturnsPage.tsx | Returns workflow | Return creation, restock tracking |

---

## Reusable for Which of the 52 Features

Based on code review, these existing subsystems can be **extended or reused**:

- **#2 Multiple Payment Methods** — Extend PaymentEntry with new methods; add validation rules
- **#3 Split/Multi-Tender** — Fully built; add allocation logic (e.g., auto-split by percentage)
- **#4 EMI/Installment** — Fully built; extend with provider-specific rules or rate tables
- **#5 Credit Card Processing** — Payment capture exists; add tokenization / recurring billing
- **#6 Loyalty Points** — Redemption gate exists (LOYALTY tender); extend earn/burn rules
- **#7 Gift Vouchers** — Fully built (atomic redeem); extend with custom card designs
- **#8 Store Credit** — Fully built (credit_note_ledger); reuse for refunds, add browsing UI
- **#9 Discounts (Per-Item/Cart/Category)** — Fully built; extend with combo/bundle rules
- **#10 GST Compliance** — Fully built (per-category, inter/intra-state split, invoice serial); add GSTR-1 export
- **#11 Invoice Generation** — Serial numbering + storage built; add PDF generation (thermal + A4)
- **#12 Receipt Printing** — Receipt format sketched in comments; implement thermal 80mm + email/WhatsApp
- **#13 Returns/Exchanges** — Fully built (qty caps, tender enforcement, restock); extend with damage assessment
- **#14 Refund Processing** — Fully built (original method, store-credit, monetary caps); add reverse loyalty
- **#15 Day-End Settlement** — NOT BUILT (period_locks exist, AR/cash-flow tracking exist)
- **#16 Cash Register Reconciliation** — NOT BUILT (cash-flow exists, POS receipt tracking missing)
- **#17 Order Status Tracking** — Fully built (DRAFT → CONFIRMED → PROCESSING → READY → DELIVERED); extend with workshop integration
- **#18 Order History** — Queries exist; add detailed audit trail per item
- **#19 Customer Order Lookup** — Fully built (search by order#/name/phone); extend with filters
- **#20 Partial Delivery** — NOT BUILT (delivery status all-or-nothing currently)

---

## Genuine Gaps

| Gap | Scope | Impact |
|-----|-------|--------|
| **No Day-End Settlement Workflow** | Cashier workflow | Cannot close a POS shift; no till reconciliation (expected cash vs actual) |
| **No Cash Register Tracking** | Till state | Cannot lock a till; no concurrent-cashier safety |
| **No Partial Delivery / Split Shipment** | Order fulfillment | Orders ship full or not at all; no backorder / layaway support |
| **No PDF Receipt/Invoice Generation** | Document output | Receipts mentioned (thermal 80mm + A4 tax invoice) but not implemented; manual printing workaround needed |
| **No Tender Limit Enforcement** | Payment rules | No per-method daily caps (e.g., max Rs 100k NEFT) or required approval thresholds |
| **No Damage Assessment on Returns** | Return QA | Returns mark condition (GOOD/OPENED/DAMAGED) but have no photographic or detailed defect recording |
| **No Service Charge / Convenience Fee** | Payment fees | Cannot apply card processing fees or COD charges to orders |
| **No Advanced Order Types** | Order model | No preorder / backorder / subscription; only immediate sale or POS quick-sale |
| **No Tender Reversal / Refund UI** | POS operations | No cash refund, card reversal, or void button on orders page (only via returns.py) |
| **No Delivery Scheduling/Logistics** | Fulfillment | Delivery date + time slot captured, but no slot capacity / driver assignment / route optimization |
| **No Inventory Reserve at Sale** | Stock model | Items marked SOLD only after order.create; concurrent web orders can double-sell |

---

This codebase is **production-ready for core POS retail** (orders, payments, multi-tender, GST, returns, loyalty, vouchers) but **lacking day-end/settlement and document output** for a complete shop-floor deployment.


# === Inventory / Transfers / Stock Audit / Labels ===
Perfect! Now I have enough information. Let me compile the comprehensive audit report:

## AUDIT REPORT: IMS 2.0 INVENTORY/TRANSFERS/STOCK AUDIT/LABELS

---

## Capabilities

### Stock Schema & Collections

**MongoDB collections:**
- `stock_units` — serialized inventory (one row per physical unit)
  - **Fields:** stock_id, product_id, store_id, status (AVAILABLE|RESERVED|TRANSFERRED|SOLD), quantity, barcode, location_code, batch_code, lot, expiry_date, created_at, updated_at, transferred_at, received_at, sold_at, order_id, transfer_id, reserved_at
  - **Indexes:** (store_id, status); (product_id, store_id, status) — supports inventory ledger aggregations
  - **File:** backend/database/connection.py:238-243

- `products` — product master (all SKUs)
  - **Fields:** product_id, sku, name, brand, model, category, mrp, offer_price, is_active, collection, modality, cl_series, base_curve, diameter, barcode, location_code
  - **Access:** ProductRepository (backend/database/repositories/product_repository.py:11-63)

- `serial_numbers` — high-value serialized items (watches, hearing aids)
  - **Fields:** serial_id, serial_number, product_id, store_id, status, location_code, purchase_date, warranty_months, warranty_expiry_date, supplier_batch, sold_to, sold_date, notes
  - **Test coverage:** backend/tests/test_serials.py (warranty_status, warranty_expiry derivation)

- `stock_transfers` — transfer requests & fulfillment
  - **Fields:** id, transfer_number, from_location_id, to_location_id, transfer_type (enum), status (enum: draft→pending_approval→approved→in_transit→received→completed), items[], priority, expected_date, shipping_cost, tracking_number, status_history[], stock_shipped (idempotency flag), stock_units_moved_out/in
  - **Persistence:** MongoDB `stock_transfers` collection with in-memory fallback (STOCK_TRANSFERS dict for dev/test)
  - **File:** backend/api/routers/transfers.py:140-221

- `stock_audit` — immutable audit trail (stock movement only)
  - **Fields:** stock_id, prior_status, new_status, source, transfer_id, transfer_number, from_store_id, to_store_id, at
  - **Populated by:** transfers ship/receive, returns restock, inventory add operations

- `lens_catalog` — owner-typed lens lines (Branch B' sub-PR 1)
  - **Fields:** lens_line_id, brand, series, index, material, lens_type, coating, sph_range, cyl_range, has_add, add_range, mrp, cost_price, gst_rate, hsn_code
  - **File:** backend/api/routers/lens_catalog.py:1-100

- `lens_stock_lines` — per-cell power matrix (one row per [store, sph, cyl, add])
  - **Fields:** line_stock_id, lens_line_id, store_id, sph, cyl, add, on_hand, reserved, reorder_point, safety_stock
  - **Atomicity:** reserve/commit/release use find_one_and_update with $expr predicate (no oversell)
  - **File:** backend/api/routers/lens_stock.py:60-250

- `lens_stock_audit` — focused history (independent of broader audit_logs)
  - **Purpose:** lens-specific stock movements (reserve, commit, release, set_on_hand)
  - **File:** backend/api/routers/lens_stock.py:32-36

### Transfer Lifecycle & Stock Movement

**Transfer flow** (backend/api/routers/transfers.py:224-497):

1. **DRAFT → PENDING_APPROVAL → APPROVED** — request creation & approval
2. **APPROVED → IN_TRANSIT** via ship_transfer():1004
   - Claims up to `quantity_shipped` AVAILABLE units at source store
   - Marks them TRANSFERRED (status), records transfer_id + transferred_at
   - Idempotent: `stock_shipped` flag prevents double-ship
   - Audit trail: each unit movement written to stock_audit

3. **IN_TRANSIT → RECEIVED** via receive_transfer():1062
   - Re-homes TRANSFERRED units to destination (status → AVAILABLE, store_id updated)
   - Keeps original barcode for life (transfer ≠ purchase)
   - Tracks `received_qty_committed` per line (delta-only re-homes on retry)
   - Never fabricates stock: receive qty ≤ shipped qty
   - Audit trail: each unit movement written to stock_audit

4. **RECEIVED → COMPLETED** — finalize

**Real stock mutation guards:**
- _apply_ship_stock_move (line:300): only moves AVAILABLE units; respects quantity_shipped/quantity_requested hierarchy
- _apply_receive_stock_move (line:415): re-homes EXACT units shipped; no phantom stock creation
- Self-transfer guard (line:646): rejects source == destination transfers
- Damage tracking: quantity_received vs quantity_damaged fields (line:88-92)

**File:** backend/api/routers/transfers.py:505-1295

### Stock Audit / Cycle Count

**Endpoints:** backend/api/routers/inventory.py:1263-1609

- GET /stock-count (line:1263) — list cycle count sessions
- POST /stock-count/start (line:1290) — create session (category, zone/fixture, notes)
- POST /stock-count/{count_id}/items (line:1384) — record counted items
- POST /stock-count/{count_id}/complete (line:1449) — finalize; calculates variance_percentage, shrinkage_percentage
- GET /stock-count/{count_id} (line:1565) — session details + variances
- POST /stock-count/{count_id}/reconcile (line:1608) — apply counted qty to stock_units (commits physical count)
- POST /stock-count-scan (line:2259) — barcode scanning integration for count

**Frontend:** frontend/src/pages/inventory/StockAudit.tsx:1-100
- Session list grouped by zone/fixture
- Variance detail card (system_quantity vs physical_quantity)
- In-progress / completed status chips

### Expiry / Aging / FEFO (Contact Lenses)

**Pure helpers** (unit-tested, no DB): backend/api/routers/inventory.py:147-243

- _parse_expiry() — coerce ISO date/datetime to datetime
- compute_days_until_expiry() — negative = expired
- fefo_sort() — FEFO ordering (earliest expiry first; undated last)
- partition_by_expiry() — split into expired / near_expiry (configurable window) / safe / undated buckets

**Last-sold ageing** (line:2203-2229):
- Queries orders collection for last sale date
- Computes actual_days_since = now - last_sold_date
- Surfaces in aging reports alongside low-stock

**Files:**
- backend/api/routers/inventory.py:147-243, 2203-2229
- backend/database/repositories/product_repository.py:109-125 (find_expiring: lexicographic ISO string bounds)

### Labels, Barcodes, Serial Tracking

**Workshop label system** (scan-to-advance): backend/api/routers/labels.py

- Stage pipeline: PENDING → IN_PROGRESS → COMPLETED → READY → DELIVERED
- next_stage() — pure gating logic (line:91-113)
- STATION_TARGET_STAGE — station-to-stage mapping (INTAKE→IN_PROGRESS, FITTING→COMPLETED, QC→READY, PICKUP→DELIVERED)
- scan_advance() — POST /workshop/jobs/{job_id}/scan-advance
  - Validates scanned_code matches job_id/job_number
  - Enforces forward-only stage progression
  - Stamps per-stage timestamp + appends to immutable scan_history trail
  - File: line:186-300

**Barcode lifecycle trace** (INV-12): backend/api/routers/inventory.py:532-595
- GET /barcode/{barcode}/trace
- Returns full movement history: stock_unit (minted) → purchase (GRN) → sales (orders) → transfers → returns → audit_trail
- Cross-collection joins; fail-soft per section (empty list if collection unavailable)

**Serial number tracking:** backend/api/routers/inventory.py:3296-3520

- GET /serials (list) — enriches serial_numbers docs with product details + warranty status
- POST /serials (create) — register new high-value unit
- PATCH /serials/{serial_id} (update) — status, location, warranty, sold fields
- _compute_warranty_status() — ACTIVE / EXPIRED / NONE (handles tz-aware ISO dates)
- _serial_to_frontend() — snake_case → camelCase, joins product master
- **Test file:** backend/tests/test_serials.py:1-100 (warranty derivation tested)

**Barcode generation:** backend/api/routers/inventory.py:88-91
- generate_barcode(store_id, product_id) → "STO-UUID[:8]"

### Power Grid (Lens Stock Matrix)

**Frontend:** frontend/src/pages/inventory/PowerGridPage.tsx:1-100

- Filter layer: brand, index, material, coating, lens_type, q
- Fetch lens_lines from /lens-catalog
- If 1 match → render SPH × CYL [× ADD] matrix
- If multiple → list; click drills into matrix
- Click cell → side drawer with stock detail + audit history
- cellHeat() — color by on_hand (0→gray, 1-2→soft, 3-5→light, 6-10→medium, >10→full)
- sphCylKey() — matrix lookup (sph|cyl, add filtered upstream)

**Backend support:** backend/api/routers/lens_stock.py (Cell CRUD + atomic reserve/commit/release)
- GET /{lens_line_id}?store_id= — full power matrix
- GET /cell/{line_stock_id} — single cell detail
- POST / — create cell
- PATCH /{line_stock_id} — set on_hand / reorder_point
- POST /{lens_line_id}/bulk-import — paste 2D matrix (CSV or JSON)
- POST /{lens_line_id}/reserve — POS Step 6 (atomicity via find_one_and_update)
- POST /{lens_line_id}/commit — Workshop dispatch
- POST /{lens_line_id}/release — Order cancel
- GET /audit/{line_stock_id} — adjustment history
- GET /gap-planner — cells where on_hand < reorder

---

## Collections

| Name | Purpose | Schema Highlights |
|------|---------|-------------------|
| `stock_units` | Serialized inventory (one row per unit) | product_id, store_id, status, barcode, expiry_date, batch_code, transfer_id, sold_at, order_id |
| `products` | Master SKU catalog | sku, brand, category, mrp, offer_price, is_active, modality, cl_series, base_curve, diameter |
| `serial_numbers` | High-value tracked items | serial_number, warranty_expiry_date, supplier_batch, sold_to, sold_date |
| `stock_transfers` | Transfer requests & lifecycle | transfer_number, from/to_location_id, status, items[], priority, shipping_cost, tracking_number, status_history, stock_shipped flag |
| `stock_audit` | Stock movement audit trail | stock_id, prior_status, new_status, source, transfer_id, from/to_store_id |
| `lens_catalog` | Owner-typed lens lines | brand, series, index, material, lens_type, coating, sph/cyl/add ranges, mrp, gst_rate |
| `lens_stock_lines` | Per-cell power matrix | lens_line_id, store_id, sph, cyl, add, on_hand, reserved, reorder_point, safety_stock |
| `lens_stock_audit` | Lens-specific stock movements | source_type (POS/WORKSHOP/ORDER_CANCEL/MANUAL), source_id, qty moved, notes |

---

## Key Files

**Backend Routers:**
- `backend/api/routers/inventory.py` (3,500+ lines) — stock ledger, aging, barcode trace, serial tracking, stock count/reconcile, low-stock, bulk operations
- `backend/api/routers/transfers.py` (1,300+ lines) — transfer CRUD, approval flow, ship/receive stock movement, analytics
- `backend/api/routers/lens_stock.py` (400+ lines) — cell CRUD, atomicity, reserve/commit/release, bulk import, gap planner
- `backend/api/routers/lens_catalog.py` (500+ lines) — lens line CRUD, enum config
- `backend/api/routers/labels.py` (600+ lines) — workshop scan-to-advance, label payloads, QZ Tray signing

**Backend Repositories & DB:**
- `backend/database/repositories/product_repository.py` — ProductRepository, StockRepository
- `backend/database/connection.py:238-243` — stock_units indexes
- `backend/tests/test_serials.py` — warranty status, serial_to_frontend mapping

**Frontend Pages:**
- `frontend/src/pages/inventory/StockAudit.tsx` — cycle count UI (in-progress, completed, variance cards)
- `frontend/src/pages/inventory/PowerGridPage.tsx` — lens stock matrix (filters, drill-down, side drawer)
- `frontend/src/pages/inventory/InventoryPage.tsx` — stock ledger (per-product on-hand, low-stock tab)

**Frontend Components:**
- `frontend/src/components/inventory/StockTransferManagement.tsx` — transfer list, direction filter, receive modal
- `frontend/src/components/inventory/SerialNumberTracker.tsx` — serial item list, search, status filter, detail modal
- `frontend/src/components/inventory/StockCountScanningInterface.tsx` — barcode scanning for counts
- `frontend/src/components/inventory/BarcodeManagementModal.tsx` — barcode printing
- `frontend/src/components/inventory/StockAgingReport.tsx` — ageing analysis (last-sold, near-expiry CL)

---

## Reusable for Which Features

### Core Inventory Features
- **#1: Stock Ledger (per-product on-hand)** → REUSE _build_store_ledger (inventory.py:328-458), stock_units aggregation
- **#2: Low-Stock Alerts** → REUSE find_low_stock (product_repo.py:94-107), $group aggregation
- **#3: Stock Add/Receipt** → REUSE stock_add endpoint (inventory.py:~750), barcode generation, serialized unit creation
- **#4: Barcode Scanning** → REUSE find_by_barcode (StockRepository), generate_barcode helpers
- **#5: Stock Count/Audit** → REUSE start/complete_stock_count (inventory.py:1290-1565), variance calculation, reconcile_stock_count

### Transfer & Movement
- **#6: Stock Transfers** → REUSE entire transfers.py (create→approve→ship→receive→complete), _apply_ship/receive_stock_move, stock_audit trail
- **#7: Transfer Approval Workflow** → REUSE approve_transfer (transfers.py:790), status_history append
- **#8: Partial Receives** → REUSE receive_transfer logic (line:415-497), received_qty_committed tracking
- **#9: Transfer Analytics** → REUSE get_transfer_analytics (transfers.py:1238), get_location_transfer_analytics

### Contact Lens & Expiry
- **#10: FEFO Ordering** → REUSE fefo_sort, partition_by_expiry (inventory.py:147-243)
- **#11: Expiry Alerts** → REUSE partition_by_expiry, compute_days_until_expiry with near_days window
- **#12: Aging Analysis** → REUSE last_sold_date logic (inventory.py:2203-2229), days_since calculation
- **#13: CL Batch Management** → REUSE batch_code, lot fields in stock_units; cl_series, modality in products

### Barcode & Serial
- **#14: Serial Tracking (High-Value)** → REUSE serial_numbers collection, _serial_to_frontend, warranty_status derivation (test_serials.py)
- **#15: Barcode Lifecycle Trace** → REUSE barcode_lifecycle_trace (inventory.py:532-595), cross-collection joins
- **#16: Warranty Management** → REUSE _compute_warranty_status (inventory.py), warranty_expiry_date field
- **#17: Workshop Labels (Scan-to-Advance)** → REUSE labels.py scan_advance, stage_pipeline, next_stage logic

### Power Grid & Lens Stock
- **#18: Lens Stock Matrix (SPH × CYL × ADD)** → REUSE lens_stock.py cell CRUD, lens_catalog filters
- **#19: Atomic Reserve/Commit** → REUSE reserve/commit/release endpoints (lens_stock.py), find_one_and_update with $expr
- **#20: Bulk Import Matrix** → REUSE bulk_import endpoint (lens_stock.py), CSV/JSON parsing
- **#21: Gap Planner (Reorder Points)** → REUSE gap_planner endpoint (lens_stock.py), on_hand < reorder_point query

### Secondary Features
- **#22: Barcode Printing** → REUSE generate_barcode, mark_barcode_printed (product_repo.py:136-137)
- **#23: Multi-Store Inventory** → REUSE store_id scoping (validate_store_access), per-store ledger (inventory.py:262-325)
- **#24: Stock Location Tracking** → REUSE location_code field in stock_units + products
- **#25: Inventory Reconciliation** → REUSE reconcile_stock_count (inventory.py:1608), apply counted_qty to system

### Audit & Compliance
- **#26: Stock Movement Audit Trail** → REUSE stock_audit collection, _audit_stock_move (transfers.py:272-297)
- **#27: Barcode Lifecycle Audit** → REUSE barcode_lifecycle_trace (inventory.py:532-595)
- **#28: Transfer Status History** → REUSE status_history array + _append_status_history (transfers.py:173-184)
- **#29: Lens Stock Audit** → REUSE lens_stock_audit collection, every stock movement logs independently

---

## Genuine Gaps

1. **Multi-location ageing analysis** — current aging (last_sold_date) is per-product, not per-store. Feature needs store-scoped ageing breakdowns for the replenishment optimization (#52).

2. **Damage tracking at receive** — quantity_damaged field exists in TransferItemReceive schema (transfers.py:92) but not persisted to stock_units (no damaged_at, damage_reason, or rework workflow). Damaged units should flow to a rework/quality queue (clinical/workshop perspective).

3. **Transfer shipment labels** — scan-to-advance exists for workshop jobs (labels.py), but transfers have no label-printing or shipping-label generation. Shiprocket integration hook exists (transfers.py:110) but backend label generation is missing.

4. **Lens enums dynamic sourcing** — lens_enums.py seeds DEFAULT_ENUM_ITEMS at startup; runtime editing of brand/material/coating/lens_type/coating lists (Settings UI per Q5 spec) would require a /lens-enums/{type}/add endpoint and collection persistence (currently config-only).

5. **Stock reservation collision detection** — lens_stock reserve/commit/release are atomic per cell, but concurrent POS terminals reserving different cells of the same lens_line have no cross-cell coordination. A "reserve full line QTY" feature would need a higher-level lock or pessimistic saga (not yet modeled).

6. **Batch expiry cohort reports** — partitioning helpers exist (partition_by_expiry), but aggregation by batch_code + expiry_date cohorts for near-expiry disposition (donation / clearance / reorder) is missing (mentioned in contact-lens grouping at inventory.py:2425-2471 but only via CL-specific endpoint, not generalized).

7. **Returns to stock via transfer** — a return can restock units (returns.py), but returning a damaged unit back through a transfer (vs. direct restocking) has no explicit flow. Damage tracking + transfer workflow for vendor returns would bridge this gap.

8. **Fixture/zone inventory sync** — stock_units carry location_code, and StockAudit.tsx groups counts by zone/fixture, but there is no automatic sync between display_fixtures collection (BVI Phases 2-3) and zone tags on stock_units. Manual zone assignment for audits is current.

---

**Report compiled:** 2026-06-07 · **Codebase state:** Fully functional inventory/transfers/audit/labels with lens power matrix (Branch B' PR 1-2) · **Gap severity:** Minor (gaps are optimizations, not blockers for core 52-feature suite).


# === Purchase / Vendors / GRN / Vendor Returns ===
Excellent! Now I have enough context. Let me compile the comprehensive audit report:

## Comprehensive IMS 2.0 Purchase/Vendors/GRN/Vendor Returns Audit

### Capabilities

#### PurchaseOrder (PO) Schema & Management
- **File**: `backend/api/routers/vendors.py:125-134`
  - Status enum: DRAFT → SENT → ACKNOWLEDGED → PARTIAL/PARTIALLY_RECEIVED → RECEIVED/CANCELLED
  - Contains: vendor_id, delivery_store_id, items (product_id, quantity, unit_price), expected_date, notes
  - Totals computed: subtotal, tax_amount (18% GST), total_amount

- **File**: `backend/api/routers/vendors.py:989-1041`
  - POST /purchase-orders: create PO (ADMIN/AREA_MANAGER/STORE_MANAGER/ACCOUNTANT)
  - GET /purchase-orders: list with filters (vendor_id, status, store_id)
  - GET /purchase-orders/{po_id}: fetch single PO
  - POST /purchase-orders/{po_id}/send: transition DRAFT → SENT
  - POST /purchase-orders/{po_id}/cancel: enforce no-cancel-if-received guard (lines 1103-1116)
  
- **Feature INV-9**: Demand forecast-driven PO suggestion
  - File: `backend/api/routers/vendors.py:759-987`
  - POST /purchase-orders/from-forecast: reads 90-day sales velocity, groups by preferred_vendor_id, creates DRAFT POs

#### GRN (Goods Receipt Note) Flow  
- **File**: `backend/api/routers/vendors.py:1136-1509`
  - Status: PENDING → ACCEPTED (or ESCALATED)
  - Receives qty, accepted qty, rejected qty (cross-field guard: accepted + rejected == received)
  - Captures: vendor_invoice_no, vendor_invoice_date, po_id, items with per-line variance classification
  
- **Variance Detection**: `backend/api/routers/vendors.py:206-236`
  - Classifies per-line as SHORT/OVER/EXACT/UNMATCHED vs PO quantity
  - `grn_has_discrepancy()` flags rejected qty > 0 or received ≠ ordered beyond tolerance
  - Escalates discrepancies as P2 SYSTEM tasks (auto-created if variance detected)

- **Stock Minting**: `backend/api/routers/vendors.py:1309-1509`
  - POST /grn/{grn_id}/accept: posts GRN, mints serialized stock_units (one per accepted unit)
  - Stamps each unit with: barcode, location_code, source_type='GRN', source_id=grn_id, po_id
  - **Provisional Cost (Phase 2)**: unit_cost from PO line (fail-soft, ADDITIVE; mints units even if cost unavailable)
  - Idempotent: skips re-minting if units for (grn_id, product_id) already exist
  - Updates PO status to PARTIALLY_RECEIVED or RECEIVED based on cumulative received qty vs ordered
  - Creates stock_audit row per unit (fail-soft; never blocks receiving)

#### Purchase Invoice (First-Class AP Document)  
- **File**: `backend/api/routers/purchase_invoices.py:85-116`
  - **Schema**: lines with product_id, description, hsn, qty, unit_price, gst_rate
  - Explicitly captures: po_id, grn_id, recipient_entity_id, recipient_gstin, place_of_supply, tds
  - Computes per-line: taxable, cgst, sgst, igst (split determined by inter-state classification)

- **Creation (Booking)**: `backend/api/routers/purchase_invoices.py:369-552`
  - POST /api/v1/vendors/purchase-invoices
  - Duplicate guard: vendor_id + invoice_number must be unique (line 392-408)
  - Due date computed from vendor credit_days (AP_engine:ap_engine.compute_due_date)
  - **Phase 2 – 3-Way Match**: compares PO ordered qty/price vs GRN accepted qty vs invoice billed qty/price
    - Tolerance configurable (default 5%)
    - Verdict: MATCHED or ON_HOLD_EXCEPTION; recorded on invoice.match_status
  - **Phase 2 – Inventory Valuation True-Up**: adjusts product.cost_price from invoice per-unit landed price (moving-average or FIFO method)
  - All writes are fail-soft; valuation/match errors never block the AP payable booking

- **Prefill from GRN**: `backend/api/routers/purchase_invoices.py:594-673`
  - GET /from-grn/{grn_id}: drafts invoice from accepted GRN + PO
  - Returns: vendor_invoice_no/date from GRN, accepted line quantities, tax split (intra vs inter-state)
  - User reviews, edits, then POSTs to book

- **3-Way Match Detail + Exception Approval**:
  - GET /{invoice_id}/match (lines 760-804): returns stored match verdict + per-line detail
  - POST /{invoice_id}/approve-exception (lines 811-890): ADMIN/ACCOUNTANT override ON_HOLD_EXCEPTION → MATCHED_OVERRIDE

#### Purchase Invoice Engines (Pure Services)
- **File**: `backend/api/services/purchase_invoice_engine.py`
  - `determine_place_of_supply()`: routes inter-state (supplier state ≠ recipient state) → IGST
  - `split_line_gst()`: per-line CGST/SGST split (exact to paisa: cgst = round(tax/2), sgst = residual)
  - `lines_from_grn()`: builds draft lines from GRN accepted qty + PO unit_price/tax_rate
  - Stores TWO place_of_supply fields: 
    - `itc_place_of_supply`: supplier state (what the ITC register keys on) 
    - `place_of_supply`: recipient state (legal recipient-side PoS)

- **File**: `backend/api/services/purchase_match.py`
  - `three_way_match()`: compares PO vs GRN vs invoice per product_id
    - Flags qty variance (invoiced vs ordered, received vs ordered) > tolerance
    - Flags price variance (invoice unit price vs PO unit price) > tolerance
    - Flags "invoiced but not received" (billed for goods that never arrived)
    - Returns: match_status, per-line detail with reasons, summary (matched/exception line counts)
  - `moving_average_cost()`: weighted-average (AVCO) blended cost after receipt
  - `valuation_trueup_for_invoice()`: computes per-product cost updates (MOVING_AVERAGE or FIFO)

#### Vendor Master
- **File**: `backend/api/routers/vendors.py:69-99`
  - Schema: legal_name, trade_name, vendor_type (INDIAN), gstin_status, gstin, address, city, state, mobile, email, credit_days
  - GSTIN validation: 15-char Indian format enforced when gstin_status=REGISTERED (lines 87-98)

- **Create/Update**: `backend/api/routers/vendors.py:464-547`
  - POST /vendors (ADMIN/AREA_MANAGER/STORE_MANAGER/ACCOUNTANT)
  - Duplicate GSTIN guard (line 476-481)
  - PUT /{vendor_id}: update is_active, credit_days, contact fields

- **Feature INV-7 – Vendor SKU Aliases**:
  - Maps vendor-specific codes to IMS master product_id
  - GET /sku-alias-lookup: resolve vendor SKU → product_id (called during GRN goods-inward)
  - GET /{vendor_id}/sku-aliases: list aliases for a vendor
  - POST /{vendor_id}/sku-aliases: register/upsert (idempotent on vendor_id + vendor_sku)

#### Vendor Returns & Debit Notes
- **File**: `backend/api/routers/vendor_returns.py`
  - Schema: vendor_id, store_id, items (product_id, product_name, quantity, reason, unit_price), return_type (credit_note|replacement)
  - Statuses: created → approved → shipped → received_by_vendor → credit_issued|replaced|cancelled
  - On credit_issued: generates credit_note_number, records credit_note_amount = total_value

- **Create Return**: `vendor_returns.py:142-202`
  - POST / (ADMIN/AREA_MANAGER/STORE_MANAGER/ACCOUNTANT)
  - Calculates total_value = sum(qty * unit_price)
  - Maintains status_history with timestamps + changers

- **Update Status**: `vendor_returns.py:236-322`
  - PATCH /{return_id}/status: state-machine transitions with validation
  - Audit trail: every status change + notes recorded in history array

#### Vendor Portal (External Lens Lab Access)
- **File**: `backend/api/routers/vendor_portal.py`
  - Token-auth surface (UUID bearer token, not JWT)
  - Scopes workshop_jobs to vendor_id, redacts PII (customer initials only, no phone/email)
  - Rate-limited: 60 requests/min per token (sliding window)

- **Status Updates from Lab**: `vendor_portal.py:251-339`
  - POST /{token_id}/jobs/{job_id}/status: lab posts RECEIVED|IN_PRODUCTION|DISPATCHED|DELIVERED|ON_HOLD|CANCELLED
  - Auto-stamps vendor_dispatch_date (DISPATCHED), vendor_received_date (DELIVERED)
  - Audited as source='vendor_portal'

### Collections

- **vendors**: vendor_id, legal_name, trade_name, gstin, gstin_status, credit_days, is_active, created_at
- **purchase_orders** (po_repo): po_id, po_number, vendor_id, delivery_store_id, items[], subtotal, tax_amount, total_amount, status (DRAFT|SENT|...|RECEIVED), expected_date, created_at
- **grns** (grn_repo): grn_id, grn_number, po_id, vendor_id, vendor_invoice_no, vendor_invoice_date, items[], total_received, total_accepted, total_rejected, status (PENDING|ACCEPTED|ESCALATED), created_at
- **vendor_bills**: bill_id/invoice_id, doc_type (PURCHASE_INVOICE), vendor_id, po_id, grn_id, invoice_number, invoice_date, bill_date, lines[], taxable_amount, cgst_total, sgst_total, igst_total, total_amount, place_of_supply, match_status, match_detail, due_date, outstanding, status (OUTSTANDING|PARTIAL|PAID), created_at
- **vendor_returns**: return_id, vendor_id, store_id, items[], return_type, status, total_value, credit_note_number, status_history[], created_at
- **vendor_sku_aliases**: alias_id, vendor_id, vendor_sku, product_id, description, created_at
- **stock_units**: stock_id, product_id, store_id, source_type (GRN), source_id (grn_id), po_id, barcode, location_code, unit_cost (from GRN/PO), cost_price, status (AVAILABLE), created_by, created_at
- **purchase_settings**: default (config), valuation_method (MOVING_AVERAGE|FIFO), match_tolerance_pct, updated_at
- **vendor_portal_tokens**: token_id, vendor_id, vendor_name, ttl_days, created_at, last_used_at, is_active

### Key Files

- **Backend Routers**:
  - `backend/api/routers/vendors.py` — PO, GRN, vendor master, SKU alias endpoints
  - `backend/api/routers/purchase_invoices.py` — first-class AP invoice with 3-way match + valuation
  - `backend/api/routers/vendor_returns.py` — debit notes / vendor returns
  - `backend/api/routers/vendor_portal.py` — external lens lab token-auth portal
  
- **Business Logic Services**:
  - `backend/api/services/purchase_invoice_engine.py` — GST split, place-of-supply routing
  - `backend/api/services/purchase_match.py` — 3-way match verdict + moving-average valuation
  - `backend/api/services/ap_engine.py` — AP due-date, duplicate guards (referenced but not fully read)

- **Frontend Components**:
  - `frontend/src/pages/purchase/PurchaseOrderForm.tsx` — create/edit PO (product_name, sku, qty, unitCost, taxRate)
  - `frontend/src/pages/purchase/GoodsReceiptNote.tsx` — GRN creation, line-item qty capture, quality checklist
  - `frontend/src/pages/purchase/PurchaseInvoicesTab.tsx` — book first-class invoice, prefill from GRN, 3-way match detail viewer
  - `frontend/src/pages/purchase/VendorReturns.tsx` — create debit note, manage return status, emit credit note
  - `frontend/src/pages/purchase/purchaseTypes.ts` — type definitions (Supplier, PurchaseOrder, POItem, POStatus)

### Reusable for Features

Key reusable patterns across the 52-feature roadmap:

- **#4 (Purchase Invoices – GST Split)**: Complete. Place-of-supply routing, CGST/SGST vs IGST, per-line taxable/tax. Reusable: invoice line computation, inter-state classification logic.
- **#5 (3-Way Match PO/GRN/Invoice)**: Complete. Three-way match, tolerance config, match verdict + exception override. Reusable: purchasing_match service (qty/price variance logic), match_status state machine.
- **#9 (Inventory Valuation)**: Partial. Moving-average + FIFO method enum, moving_average_cost formula, per-unit unit_cost stamp on stock_units. Reusable: valuation true-up logic, cost_source tracking.
- **#7 (Vendor SKU Aliases)**: Complete. vendor_sku_aliases collection, upsert-on-lookup pattern (idempotent on vendor_id + vendor_sku). Reusable: SKU alias resolver for other receive flows.
- **#10 (Vendor Portal)**: Complete. Token-auth (non-JWT), per-token rate-limit, PII redaction, job status posting. Reusable: rate-limit pattern, redaction helper (initials), audit trail on external writes.
- **#13 (Debit Notes / Vendor Returns)**: Complete. vendor_returns collection, status-machine (created → approved → shipped → received_by_vendor → credit_issued|replaced), credit_note_number generation. Reusable: status state machine, credit-note number generation.

### Genuine Gaps

1. **Landed Cost Components (Not Captured)**
   - GRN + invoice handle unit cost, but no distinct freight, taxes, duties, handling fields
   - `lines` on invoice carry qty + unit_price + taxable + gst only; no landed_cost_breakdown
   - **Impact**: Cannot allocate freight/duty proportionally across lines; valuation assumes a single unit_price

2. **Short-Shipment / Backorder Handling (Implicit)**
   - A PARTIALLY_RECEIVED PO can remain open; no explicit "backorder" or "short-ship" document
   - GRN shows variance (SHORT/OVER) but no formal order to capture the outstanding qty + expected receipt date
   - **Impact**: No way to track "ordered 100, received 70, backorder 30 arriving 2026-03-15"

3. **Delivery Challan / Way-Bill (Not Implemented)**
   - GRN has no grn_transport_details, no challan_number, no way_bill_number
   - No way to link transport provider, date shipped by vendor, tracking URL
   - **Impact**: Cannot reconcile physical goods in-transit vs warehouse arrival

4. **Consignment / On-Approval Stock (Not Implemented)**
   - stock_units have no consignment_flag, no consignment_return_date
   - No API to transition "CONSIGNMENT" → "PURCHASED" after acceptance period
   - **Impact**: Cannot track vendor-consigned goods separately or auto-reverse if not purchased

5. **Rebate / Volume Discount Agreements (Not Implemented)**
   - Vendor master has no rebate_tier, rebate_pct, rebate_calc_basis
   - No collection to track rebate claims, accruals, settlements
   - **Impact**: Cannot apply retrospective discounts or track rebate revenue

6. **Goods Return to Vendor (Return Half of Vendor Returns Flow)**
   - vendor_returns creates a debit note (goods flowing back), but no RTV (Return to Vendor) formal doc
   - No RTV_PENDING status tracking goods in-transit back to vendor
   - **Impact**: Warehouse can't track "items packed for return, awaiting pickup" vs "returned and confirmed by vendor"

7. **GRN Cost Reconciliation (Minor)**
   - GRN line items carry received_qty, accepted_qty, rejected_qty but no unit_cost at receipt time
   - unit_cost stamped on stock_units at accept time, but GRN doc itself never carries the cost
   - **Impact**: Cannot audit "what cost did we expect at GRN vs what invoice billed" without re-joining stock_units

8. **Purchase Invoice Payment Status (Not Tracked)**
   - invoice doc has status OUTSTANDING|PARTIAL|PAID, but no link to cash/bank payment docs
   - No tds_paid, tds_pending fields despite tds field in schema
   - **Impact**: Cannot reconcile invoice outstanding vs vendor_payments (separate finance flow)

9. **Multi-Currency / Import Purchases (Not Handled)**
   - All pricing is INR (18% GST assumed, GST rates hardcoded to [0, 5, 12, 18, 28])
   - No currency field on PO/invoice, no exchange rate capture
   - **Impact**: Cannot book overseas vendor purchases (currency conversion + IGST rules)

10. **PO Amendment / Change Order (Not Implemented)**
    - PO created DRAFT → SENT → received; no formal amendment or change-order state
    - Cannot reduce a PARTIALLY_RECEIVED PO's remaining qty or extend expected_date
    - **Impact**: If expected_date changes or qty revised, must cancel + recreate PO

---

## Summary

**Strengths**:
- Phase 2 purchase control (3-way match + moving-average valuation) is production-ready.
- Place-of-supply routing fixes the long-standing IGST/CGST+SGST bug.
- GRN → stock minting with cost capture (provisional PO price, true-up at invoice) is solid.
- Vendor returns (debit notes) with status machine and credit-note generation complete.
- Vendor portal token-auth for external labs (non-JWT, rate-limited, PII-redacted) is secure.

**Gaps for Advanced Workflows**:
- Landed cost breakdown (freight, duty, etc.) not itemized.
- Backorder / short-shipment tracking absent.
- Delivery challan / transport visibility absent.
- Consignment / on-approval stock not supported.
- Rebate / volume discount agreements not in schema.
- RTV (return to vendor) warehouse status tracking incomplete.


# === Finance / Accounting / Expenses / Cash ===
Perfect. I have all the information I need. Let me compile a comprehensive report:

## IMS 2.0 Finance/Accounting Audit Report

### Capabilities (Concrete File:Line References)

#### Revenue & Sales Tracking
- **Revenue aggregation** (finance.py:524-622): Monthly/weekly/daily/yearly period buckets with MoM growth %, excludes DRAFT/CANCELLED orders, reads `grand_total` field with fallback to `total`; **Inflow tracking** (finance.py:1127-1142): POS CASH order inflows aggregated by `payment_status=PAID`, scoped to active store

#### Profit & Loss (P&L)
- **Gross profit calculation** (finance.py:628-724): Revenue - COGS, surfaced as P&L line item with margin %; includes **COGS with cost_at_sale snapshot** (finance.py:146-160): Uses per-line `cost_at_sale` (frozen at order create), fallback to product `cost_price`, or 60%-of-revenue fallback for unknown costs; **estimated margin flags** (finance.py:163-183, 697-700): When fallback used, returns `cogs_is_estimated=true` + `cogs_estimated_lines` count so UI never shows fabricated numbers without warning
- **Category-level margins** (finance.py:410-443): Revenue/COGS/GP broken out by `item_type` (product category) with estimated flags per category
- **Net profit** (finance.py:705): GP - total_expenses - payroll_cost; **payroll_cost** (finance.py:235-250): Sum of `ctc_cost` from payroll docs for months overlapping the P&L period range

#### Expense Management
- **Expense workflow** (expenses.py:546-1397): PENDING → APPROVED → SENT_TO_ACCOUNTANT → ENTERED (full lifecycle with separation of duties)
- **Approval gates** (expenses.py:614-730): Period lock check, outstanding-advance block, per-role/category spend caps; stored in `expense_category_caps` collection (expenses.py:45-46)
- **Anti-fraud: duplicate-bill detection** (expenses.py:75-103): SHA-256 fingerprinting of bill uploads, soft-flag (not hard-block) when same receipt claimed twice in same store
- **Aging report** (expenses.py:300-357): Buckets waiting expenses (APPROVED/SENT_TO_ACCOUNTANT) by days pending (0-7/8-15/15+)
- **Expense validation** (expenses.py:365-402): Amount > 0, category + date + payment_mode, optional advance linkage for settlement

#### Advances & Reimbursement
- **Advance lifecycle** (expenses.py:1183-1364): PENDING → APPROVED → DISBURSED → SETTLED; employee can link advance on expense claim for settlement (expenses.py:250-266)
- **Outstanding-advance block** (expenses.py:522-538, 669-678): Employee blocked from new expenses/advances until all outstanding (DISBURSED/PARTIALLY_SETTLED) are settled

#### Budgets (Planned vs Actual)
- **Dual-mode variance** (budgets.py): Per-store, per-month, per-head (REVENUE or expense category)
- **Planned amounts** (budgets.py:219-272): User-entered and upserted in `budgets` collection (one doc per (store, period, head))
- **Actuals derived on-demand** (budgets.py:135-187): Revenue from order aggregation (same as reports.py), expense actuals summed from APPROVED expenses grouped by category; variance computed as actual - planned (budgets.py:190-200)

#### Accounts Payable (AP)
- **Vendor ledger** (finance.py:1051-1106): Bills + payments + debit-notes, computes running balance per vendor via `ap_engine.build_ledger()` (ap_engine.py:400+)
- **AP aging** (ap_engine.py:120-141): Current / 1-30 / 31-60 / 61-90 / 90+ days past due (computed from due_date, not create_date)
- **TDS (Tax Deducted at Source)** (ap_engine.py:35-66, 147-200): Post-Budget-2024 rates for 194C/194J/194Q/194H/194I sections; threshold enforcement (ap_engine.py:186+); supports admin override (routers use settings for rate overrides)

#### Accounts Receivable (AR)
- **Customer aging** (finance.py:959-1046): Outstanding orders aged by DUE_DATE (created_at + customer.credit_terms_days, default 30 days), buckets: current / 0-30 / 31-60 / 61-90 / 90+ days overdue
- **Outstanding summary** (finance.py:1004-1044): Per-customer balance, days overdue, total by bucket

#### Cash Flow
- **Cash inflow** (finance.py:1112-1223): PAID orders summed by `grand_total`, month-to-date
- **Cash outflow**: expenses + POs + vendor payments (org-level AP only on org view to avoid double-attribution to one store)
- **Cash flow forecast** (finance.py:1448-1552): 7-365 day projection with AR inflows (created + collection_lag_days), AP outflows (real due dates), recurring monthly estimate (avg last 3 months + user input); surfaces lowest balance warning

#### GST Management
- **Output tax (sales GST)** (finance.py:728-884): CGST/SGST (intra-state, same state) vs IGST (inter-state, when store_state ≠ customer_state); uses store and customer state maps; unsure state → intra-state (CGST+SGST fallback)
- **Input tax credit (ITC)** (itc_reconcile.py:105-150): From vendor bills, eligible when not-blocked (17(5) disallowed items) AND received (explicit received=true OR not in pending statuses); inter-state bills (bill place_of_supply ≠ entity primary_state) → IGST, else CGST/SGST
- **ITC register** (finance.py:1589-1630): Bills grouped by period, split CGST/SGST/IGST with 180-day hold-back flag for old unmatched bills
- **GSTR-2B reconciliation** (finance.py:1675-1690, itc_reconcile.py): Book rows vs portal rows; buckets: matched (safe) / mismatch (tax differs) / only_in_books (ITC at risk, chase vendor) / only_in_2b (missing entry)

#### Period Lock
- **Accounting period closure** (finance.py:446-481, 1759-1798): Once locked (month/year), cannot post orders, expenses, or approvals to that month (HTTP 423 if attempted); gates orders/expenses/returns/payments; admin-only lock/unlock

#### Owner Dashboard (ADMIN/ACCOUNTANT)
- **Net position** (finance.py:1331-1445): AR total - AP outstanding (working capital view)
- **Monthly cash flow** (finance.py:1439-1442): Revenue - expenses - vendor_payments, this-month bucket
- **AR/AP aging together** (finance.py:1341-1357): Due-7-days and due-30-days warnings, AR overdue warning, vendor overdue warning

#### Reports Integration
- **Order revenue aggregation** (reports.py:58-94): Real Mongo Date range match (not ISO string), excludes CANCELLED/DRAFT, reads `grand_total` → `final_amount` → `total_amount` → `total` (legacy fallback chain)
- **Discount field resolution** (reports.py:97-105): `total_discount` → `discount_amount` → `discount`
- **Tax field resolution** (reports.py:108-116): `tax_amount` → `total_tax` → `tax`

#### Payout (Pune Incentive Module)
- **Pool sizing** (payout.py:365-425): Inputs are sales (aggregate from orders), discount %, YoY comparison, visufit usage; outputs staff payouts + manager bonuses, locked as immutable snapshots
- **MTD aggregation** (payout.py:247-265): Per-staff points logged for month, feeds into weightage calculation

---

### Collections (Name - Purpose)

| Collection | Purpose | Key Fields |
|---|---|---|
| **orders** | Sales transactions | order_id, created_at (BSON datetime), grand_total, total_discount, tax_amount, payment_status (PAID/UNPAID/PARTIAL), status (DRAFT/CANCELLED/CONFIRMED/DELIVERED), items[].cost_at_sale |
| **expenses** | Employee reimbursement claims | expense_id, employee_id, expense_date (ISO string YYYY-MM-DD), amount, category, status (PENDING/APPROVED/SENT_TO_ACCOUNTANT/ENTERED), store_id, bill_sha256 (anti-fraud), duplicate_bill, duplicate_of, advance_id |
| **advances** | Employee cash advances | advance_id, employee_id, amount, status (PENDING/APPROVED/DISBURSED/SETTLED/PARTIALLY_SETTLED), store_id |
| **budgets** | Planned budget lines | budget_id, store_id, period (YYYY-MM), head (REVENUE or category name), planned_amount, updated_at |
| **vendor_bills** | Accounts payable (purchases) | bill_id, vendor_id, bill_date (ISO), taxable_amount, tax_amount, place_of_supply (state code for inter-state GST), status, itc_blocked, itc_eligible, received |
| **vendor_payments** | Bill payments | payment_id, vendor_id, amount, payment_date (ISO string), tds_section, tds_amount |
| **vendor_debit_notes** | AP credits (returns) | bill_id, vendor_id, amount |
| **vendors** | Vendor master | vendor_id, legal_name, trade_name, gstin, credit_terms_days |
| **payroll** | Monthly payroll cost | year, month, store_id, breakdown.ctc_cost (or net_salary fallback) |
| **period_locks** | Closed accounting periods | month, year, locked_by, locked_at |
| **expense_category_caps** | Spend limits by role+category | _id='expense_category_caps', caps[].role/category/daily/monthly, global.daily/monthly |
| **entities** | Legal entities (multi-entity support) | entity_id, name, primary_state, state_code |
| **stores** | Store master | store_id, entity_id, state (for intra/inter-state GST) |
| **customers** | Customer master | customer_id, state (for intra/inter-state GST classification), credit_terms_days, payment_terms_days |
| **products** | Product catalog | product_id, cost_price, category (item_type) |
| **purchase_orders** | Procurement | total_amount, delivery_store_id, payment_status |
| **payout_snapshots** | Locked monthly incentive payouts | snapshot_id, store_id, year, month, status (LOCKED/PAID), staff_payouts[], manager_bonuses[], grand_total |
| **incentive_settings** | Payout config (by store) | store_id, pool_sizing, tier_thresholds |
| **points_log** | MTD staff performance log | user_id, store_id, date, points, mtd_avg_total |
| **stock_transfers** | Inter-store transfers | transfer_id, from_store, to_store, status (including reconciliation pending) |

---

### Key Files

#### Backend API Routers
- **finance.py** (2977 lines): Core finance aggregations (revenue, P&L, GST, AR/AP, cash flow, forecasts, ITC register, period locks, budget read, Tally export)
- **expenses.py** (1397 lines): Expense + advance lifecycle, spend caps, duplicate-bill detection, aging, accountant queue
- **budgets.py** (423 lines): Budget variance (planned vs actual), upsert, deletion
- **payout.py** (728 lines): Incentive pool + staff/manager payout computation, snapshot locking, CSV export
- **reports.py** (first 150 lines shown): Shared order aggregation helpers with correct field-name fallback chains

#### Backend Services
- **ap_engine.py**: AP ledger + aging, TDS sections + threshold enforcement, due-date math
- **itc_reconcile.py**: ITC register (intra vs inter-state split), GSTR-2B matching logic, mismatch detection
- **payroll_engine.py**: Payroll cost aggregation

#### Frontend Pages
- **CashRegisterPage.tsx**: Till session opening (denomination-by-denomination float), EOD closing + variance reconciliation, history
- **ExpenseTracker.tsx**: Submit → approval → entry workflow, approver queue, accountant ledger-entry queue, aging buckets, duplicate-bill watch-list
- **ItcReconcilePage.tsx**: GSTR-2B upload + matching, buckets (safe / mismatch / at risk)
- **FinanceDashboard.tsx**: P&L snapshot, revenue cards, GST summary cards
- **CashFlowPage.tsx**: Inflows/outflows month-to-date + forecast

#### Repositories
- **expense_repository.py**: ExpenseRepository (find_by_employee, find_pending_approval, get_summary_by_category aggregation) and AdvanceRepository (find_outstanding, add_settlement)

---

### Reusable for Which of the 52 Features

**All expense-related (#5-#9, #47-#49)**: Expense workflow, approvals, caps, anti-fraud duplicate detection, advances, reimbursement aging are fully built and tested.

**All finance reporting (#10-#15, #17-#23, #33-#40)**: P&L (with real COGS + estimated flags), revenue, GST (intra/inter-state split), AR/AP aging, cash flow, forecasts, ITC register, GSTR-2B matching, period locks, Tally export (sales-voucher JV + GST reconciliation per entity) are production-ready. Extends seamlessly to new entities or stores.

**Budgeting (#16)**: Variance (planned vs actual) engine is done; reuse for operational budgets (Survival / Full / Custom).

**Payroll cost visibility**: Cost-to-company is already in P&L (payroll_cost line 705); aggregates from payroll.breakdown.ctc_cost by month range.

**Margin visibility**: Category-level gross margins (pnl_by_category) + estimated-lines flags so no fabricated numbers shown.

**Cash reconciliation (#41)**: CashRegisterPage handles till opening/closing by denomination + variance; DayEndReport ties inflows/outflows. Period lock prevents posting to closed months.

**Petty cash / expense approval**: Expense claim workflow with caps + advance linkage already handles small cash/travel advances.

**Maker-checker concept**: Expense separation-of-duties (requester cannot approve own claim), approved by role-gated approver, entered by accountant. No explicit journal/ledger maker-checker yet, but expense ENTERED state + ledger_reference field ready for accountant sign-off.

---

### Genuine Gaps

1. **Journal/Ledger (accounting entries)**: No GL journal collection or double-entry posting layer. Expenses + AP + AR are tracked as separate cycles; no unified ledger or trial-balance export. Tally export exists but is one-way (no feedback from Tally).

2. **Maker-checker on journal entries**: Expense ENTERED state captures accountant approval, but no separate journal approval workflow (only the expense → entered transition). Financial adjustments (reversals, corrections) not covered.

3. **Cash variance root cause**: Till reconciliation surfaces the variance (over/short) but no variance-explanation audit trail or variance-reserve account.

4. **Multi-currency**: All amounts assumed INR; no FX module or currency-aware reporting.

5. **Consolidated financials**: P&L/GST is per-store or org-wide; no multi-entity consolidation or inter-company elimination.

6. **Year-end closing**: Period lock prevents posting but no formal close process (GL reversal, retained-earnings rollover, tax provision reversals).

7. **Balance sheet**: Only income-statement (P&L) + receivables/payables snapshots; no BS assets/liabilities/equity staging.

8. **Cost accounting** (job/project costing): No project/job codes on expenses or purchases; allocation to projects not supported.

9. **Inventory valuation**: Cost_at_sale snapshot fixes COGS, but no FIFO/LIFO/WAC method selector or periodic revaluation.

10. **Cash position reconciliation**: /cash-flow sums inflows/outflows (P&L-like) but does not reconcile to bank balance or petty-cash float. No bank recon module.

11. **Fixed asset depreciation**: No asset ledger or depreciation schedule.

12. **Intercompany payables/receivables**: No elimination of reciprocal owed amounts between entities.

---

## Summary

**Strengths**: Expense governance is sophisticated (caps + advance + antiifraud). P&L is real (COGS via cost_at_sale snapshot) + flagged when estimated. GST is intra/inter-state aware. AR/AP aging uses DUE_DATE, not create_date. Period lock prevents closing errors. Cash flow forecast is workable. ITC reconciliation (GSTR-2B matching) is a standout.

**Weaknesses**: No GL or journal layer. Cash variance unexplained. Only income-statement financials. Year-end and multi-entity consolidation not designed.

**Next phase**: Would be GL journal + maker-checker, balance-sheet staging, bank recon, consolidated financials, and cost accounting if those 52 features include them.


# === HR / Payroll / Incentive / Attendance ===
Perfect! Now let me create a concise markdown audit report:

## IMS 2.0 HR/Payroll/Incentive/Attendance Audit Report

### Capabilities

**Attendance & Clock-In:**
- Geo-fenced check-in/check-out: `backend/api/routers/hr.py` lines 772-905 (evaluate_geofence → 500m default radius, roles 4-7 enforced, HQ roles exempt)
- IP-based geo-fence: NOT implemented (geo-fence only via lat/lng haversine distance)
- Check-in/check-out stamps on `attendance` collection with is_late + late_minutes (hr.py:825-830)
- Shift-based late-mark computation: `_resolve_employee_shift` (hr.py:669-708) resolves user's shift_id → shift start_time/grace_minutes
- Idempotent on (employee_id, date) unique index; re-check-in refreshes but preserves earliest check-in time (hr.py:846-873)

**Leave Management:**
- Leave types: CASUAL, SICK, EARNED, PRIVILEGE, MATERNITY, PATERNITY, UNPAID, LWP, LOP (hr.py:57-69)
- Apply leave: hr.py:1237-1304 (overlap detection, PENDING → APPROVED/REJECTED workflow)
- Manager approval gate: require_roles(*_SWAP_APPROVER_ROLES) = ADMIN/AREA_MANAGER/STORE_MANAGER (hr.py:1307-1372)
- Self-approval prevention: "requester cannot approve their own" enforced in `attendance_engine.can_approve_swap` (hr.py:1849-1865)
- No leave-balance endpoint implementation (hr.py:1375-1381 stub returns empty dict)

**Shift & Weekly-Off Management:**
- Shift config: name + start_time/end_time (HH:MM 24h) + grace_minutes (0-240) + weekly_off (Python weekday ints 0-6) (hr.py:1536-1545)
- Shift assignment: users.shift_id (hr.py:1621-1654)
- Week-off swap: request → PENDING → manager approval/rejection, record-only (NO payroll mutation) (hr.py:1750-1930)
- Self-approval gate: enforced via `attendance_engine.can_approve_swap` (hr.py:1849-1865, 1901-1917)

**Payroll Engine:**
- Structured CTC: basic + HRA + conveyance + medical + special_allowance + other_allowances (payroll.py:66-115)
- Statutory: PF (employee 12%, employer 12% capped 15k) / ESI (auto-eligible if gross ≤ 21k, manual toggle) / PT (state-aware slabs, editable) / TDS (manual monthly) (payroll_engine.py:24-94)
- LWP proration: 30-day basis, (basic + allowances) * (paid_days / 30) (payroll_engine.py:239-246)
- Incentive integration: merged into earnings AFTER gross, NOT part of PF/ESI/PT base (payroll_engine.py:287-290)
- Commission rate: commission_rate_percent field on salary_config (payroll.py:109-114), **stored but NOT applied in compute_payroll** (payroll_engine.py has no commission logic)
- Clawback: NOT implemented (no anti-clawback logic, no commission reversal on returns)
- Employee discount / own-use: NOT in codebase
- Payroll run: DRAFT → APPROVED → PAID state machine with locking (payroll.py:1384-1487)

**Incentive (Daily Points, Module ii):**
- 9-category daily scoring: attendance (0-10) + conversion (0-20) + task (0-10) + visufit (0-10) + punctuality (0-10) + behaviour (0-10) + kicker_1/2 (0-10 each) + reviews (0-10) (points.py:95-108)
- Conversion auto-fill: pulls from walkout/walkin feed if null AND date=today (points.py:194-254)
- Visufit gate: 90% MTD usage threshold, gates only visufit score (points.py:347-354, applies via `apply_visufit_gate`)
- Eligibility bands: 2-10 bands per store, snapshotted at write-time (points.py:133-145, 357-358)
- MTD aggregation: per-staff totals for the month (points.py:584-618)
- Leaderboard: 30-day rolling, sorted by avg.total DESC, tie-break days_logged DESC (points.py:621-645)
- Soft-delete with audit trail (points.py:536-581)
- RBAC: SUPERADMIN/ADMIN/STORE_MANAGER/AREA_MANAGER/ACCOUNTANT write any staff; SALES_STAFF/CASHIER write own only (points.py:63-66, 304-315)
- Settings: eligibility bands + visufit gate (threshold + enabled) + payout settings (growth, rates, multipliers, weightages, supervisor bonuses) (points.py:680-909)

**Leaderboards:**
- Daily points leaderboard: 30-day rolling average (points.py:621-645)
- MTD leaderboard: month-to-date aggregation (points.py:584-618)
- Commission leaderboard: NOT found in codebase

**Rostering/Scheduling:**
- Shift definition only; NO rostering engine (no roster_id, no "assign staff to date slots")
- Week-off is per-shift (weekly_off array on SHIFT_SCHEMA); no schedule matrix

---

### Collections

| Collection | Purpose | Key Fields |
|---|---|---|
| `attendance` | Daily presence/absence records | employee_id, date (unique index on both), check_in, check_out, is_late, late_minutes, shift_id, geo_verified |
| `leaves` | Leave requests & approvals | employee_id, from_date, to_date, leave_type, status (PENDING/APPROVED/REJECTED) |
| `shifts` | Named shifts with times + grace | shift_id (unique), store_id, start_time, end_time, grace_minutes, weekly_off (array of int), is_active |
| `weekoff_swaps` | Weekly-off swap requests | swap_id (unique), employee_id, from_date, to_date, status (PENDING/APPROVED/REJECTED), requested_by, approved_by |
| `payroll` | Monthly payroll rows (DRAFT/APPROVED/PAID) | payroll_id, employee_id, year, month (unique on all 3), status, breakdown {earnings, deductions, employer_contributions} |
| `salary_config` | Per-employee Structured CTC + statutory flags | employee_id (unique), basic, hra, conveyance, medical, special_allowance, pf_applicable, esi_applicable, pt_applicable, commission_rate_percent, uan, pan, bank_* |
| `entities` | Legal entities (PAN) for payroll grouping | entity_id (unique), name, pan, gstins, pf, esi, pt_registrations |
| `pt_slabs` | Professional Tax slabs (editable per state) | state_code (unique), basis (MONTHLY/ANNUAL), gender_aware, slabs (array) |
| `points_log` | Daily points scores (Module ii) | log_id (unique, soft-delete partial), store_id, date_str, staff_id, attendance/conversion/task/visufit/punctuality/behaviour/kicker_1/2/reviews, total, eligibility, eligibility_thresholds_used |
| `incentive_settings` | Points config per store | store_id, eligibility_bands (array), visufit_gate_threshold, visufit_gate_enabled, growth_targets, base_rates, discount_kill_threshold, staff_weightages, supervisor_bonuses |
| `incentive_inputs` | Manual overrides (e.g., last_year_sale) | store_id, year, month, last_year_sale |

---

### Key Files

**Backend Routers:**
- `/backend/api/routers/hr.py` (2049 lines) — Attendance, leave, shift, week-off swap, payroll (basic), LWP report
- `/backend/api/routers/payroll.py` (1700+ lines) — Salary config CRUD, payroll run (compute_payroll), payslip, advances, PT slabs, salary sheet
- `/backend/api/routers/points.py` (910 lines) — Daily points logging, MTD, leaderboard, settings, soft-delete

**Backend Services:**
- `/backend/api/services/payroll_engine.py` — Pure stateless compute_payroll (earnings, PF/ESI/PT/TDS, LWP proration, incentive merge)
- `/backend/api/services/attendance_engine.py` — Geo-fence eval, late-mark calc, shift resolution, LWP computation, week-off swap approval logic
- `/backend/api/services/points_calculator.py` — MTD aggregation, visufit gate, eligibility compute, leaderboard sort

**Frontend:**
- `/frontend/src/pages/attendance/AttendancePage.tsx` — Geo-fenced self check-in + monthly grid (manager view)
- `/frontend/src/pages/hr/PayrollDashboard.tsx` — Payroll run, approve, lock
- `/frontend/src/pages/incentive/DailyScorecardPage.tsx` — Daily points entry (9 categories)
- `/frontend/src/pages/incentive/MTDLeaderboardPage.tsx` — MTD aggregation display
- `/frontend/src/pages/incentive/IncentiveSettingsPage.tsx` — Eligibility bands + visufit gate config

**Database:**
- `/backend/database/schemas.py` — All collection definitions + indexes
- `/backend/database/repositories/points_log_repository.py` — Points CRUD + soft-delete
- `/backend/database/repositories/incentive_settings_repository.py` — Settings CRUD

---

### Reusable for Which 52 Features

Mapping to common HR/Payroll/Incentive features:

- **#1 Attendance Tracking** ✓ FULL (geo-fence check-in, grid, summary, audit)
- **#2 Leave Management** ✓ PARTIAL (apply/approve/reject, overlap detection; NO balance accrual)
- **#3 Shift Scheduling** ✓ MINIMAL (shift config + assignment; NO rostering engine)
- **#4 Late-Mark Calculation** ✓ FULL (shift start + grace, auto-computed at check-in)
- **#5 Payroll Computation** ✓ FULL (structured CTC, PF/ESI/PT/TDS, LWP proration, incentive merge)
- **#6 Salary Slip Generation** ✓ FULL (payslip_id, breakdown, bank details)
- **#7 Statutory Compliance** ✓ FULL (PF/ESI/PT calcs per reg, state-aware PT slabs, editable)
- **#8 Incentive Payout (Daily Points)** ✓ FULL (9-category scoring, visufit gate, eligibility bands)
- **#9 Commission** ✓ STUB (commission_rate_percent field stored; NO calculation/payout logic)
- **#10 Clawback** ✗ NOT (no reversal logic)
- **#11 Employee Discount** ✗ NOT (no discount tracking on pay)
- **#12 Own-Use Pricing** ✗ NOT
- **#13 Leaderboard (Daily Points)** ✓ FULL (30-day rolling, MTD)
- **#14 Leaderboard (Commission)** ✗ NOT (no commission ranking)
- **#15 Week-Off Swap** ✓ FULL (request → manager approval, record-only)
- **#16 Holiday Calendar** ✗ NOT (only "HOLIDAY" status on attendance; no calendar config)
- **#17 Approval Workflow** ✓ PARTIAL (leave approval, week-off swap approval, payroll approve → paid)
- **#18 Salary Advance** ✓ STUB (advance request/settle; status tracking only; NO payroll deduction automation)
- **#19 Attendance Summary** ✓ FULL (per-employee, per-store, company-wide counts)
- **#20 Late-Mark Report** ✓ FULL (per-employee late count + total/avg minutes)
- **#21 LWP Report** ✓ FULL (read-only; manual entry into payroll, NOT auto-applied)
- **#22 Payroll Locking** ✓ PARTIAL (status machine DRAFT→APPROVED→PAID; NO explicit lock state)
- **#23 Salary Register Export** ✓ STUB (payroll_exports.py has `build_salary_jv_xml`, `build_pf_ecr` functions; NOT wired to endpoints)
- **#24 PF/ESI Filing** ✓ STUB (ECR builder exists; no filing/upload endpoints)
- **#25 PT Slab Maintenance** ✓ FULL (list, upsert, seed defaults per state)
- **#26 Self-Service Portal** ✓ PARTIAL (check-in, view payslip, request leave; NO balance view, NO advance req)

---

### Genuine Gaps

1. **Leave Balance Accrual & Carryover** — No system to allocate opening balance, accrue monthly, track used/remaining, or carryover to next year. Endpoint stub at `/hr/leaves/balance/{employee_id}` returns empty dict.

2. **Commission Calculation & Payout** — Field `commission_rate_percent` on salary_config is persisted but never consumed. No logic to compute commission from closed orders, apply clawback on returns, or include in payslip.

3. **Rostering / Schedule Matrix** — Only shift assignment per employee; NO "roster staff to date ranges" or "view week schedule by store/date".

4. **Holiday/Festival Calendar** — No holiday master. Attendance grid shows "HOLIDAY" status but no config of what dates are holidays.

5. **Payroll Locking** — Transition to PAID exists, but no explicit "lock run" action to prevent further edits.

6. **Salary Advance Automation** — Advance request/settle tracked, but settlement deduction is NOT auto-applied to the payroll run (manual step needed).

7. **Salary Register/Statutory Exports** — Export builders (Tally JV XML, PF ECR) exist in `payroll_exports.py` but are NOT wired to router endpoints.

8. **Employee Discount & Own-Use Pricing** — No tracking or deduction on payroll.

9. **Commission Leaderboard** — Daily points leaderboard exists; no commission-based ranking.

10. **Attendance Penalties/Fines** — Late-mark counts are recorded but NOT auto-deducted or penalized.

11. **Recurring Leave Applications** — Leave apply only supports discrete date ranges; no "every Monday off" or recurring patterns.

12. **Bulk Attendance Import** — No CSV import for attendance (manual mark endpoints exist).

13. **Work-From-Home/Half-Day Variants** — Shift config is fixed (start/end); no WFH or flexible hour recording.

14. **Supervisor Bonus Logic** — `supervisor_bonuses` field exists on incentive_settings but is NOT consumed by payout_calculator.

15. **Visufit MTD Threshold** — Visufit gate gates the SCORE but uses caller-provided `visufit_usage_pct_mtd`; no auto-fetch from clinical module (noted as "fail-soft until endpoint lands").

---

**Generated from audit of:**
- backend/api/routers/hr.py, payroll.py, points.py
- backend/api/services/payroll_engine.py, attendance_engine.py, points_calculator.py
- backend/database/schemas.py
- frontend/src/pages/attendance/AttendancePage.tsx, hr/PayrollDashboard.tsx, incentive/DailyScorecardPage.tsx, etc.


# === CRM / Marketing / Loyalty / Walkouts / Follow-ups ===
Perfect! Now I have a comprehensive understanding of the codebase. Let me compile the audit report:

## IMS 2.0 CRM/Marketing/Loyalty/Walkouts Audit Report

### CAPABILITIES (Concrete, with File:Line References)

#### **Customer Management**
- **Base customer schema**: customers.py `CustomerCreate` (line 178-204), `CustomerUpdate` (277-301)
  - Fields: customer_type (B2C/B2B), name, mobile (normalized 10-digit), email, DOB, anniversary, GSTIN (B2B required), billing_address, marketing_consent, data_consent with timestamp + version
  - Patients[] embedded array: each with patient_id, name, mobile, DOB, anniversary, relation
  - Store scoping: home_store_id + preferred_store_id (TechCherry compatibility)
  - Deduplication: find_by_mobile() checks both `phone` and `mobile` fields (line 26)
  - Credit limit (khata): per-customer field for B2B (customer_id/credit-summary, line 897-934)

#### **Loyalty Points Engine**
- **Account model** (loyalty_repository.py:25-163): customer_id, balance_points, tier (BRONZE/SILVER/GOLD/PLATINUM), lifetime_earned, lifetime_redeemed
- **Tier tiers**: calculated from lifetime_earned thresholds (loyalty_engine.py:26-34, compute_tier())
- **Atomic operations**: try_debit() uses guard-in-filter MongoDB update (line 101-163) — no double-spend on concurrent redeems
- **Family pooling**: NOT implemented — points are per-customer, no family aggregation
- **Points expiry**: per-lot FIFO tracking (loyalty.py:616-692, expire_sweep); EARN rows carry expires_at; reverse_for_return() (line 955-1058) handles return clawback with ledger idempotency marker
- **Points earning**: calc_earn_points (loyalty_engine.py:72-143) — per-line category multipliers × tier multiplier × points_per_rupee
- **Redemption**: capped by percent-of-order; atomic try_debit() prevents overspen (loyalty.py:321-418, redeem())
- **Settings**: global config (tier_thresholds, tier_multipliers, points_per_rupee, min_order_for_earn, expiry_days, max_redeem_pct) — SUPERADMIN-only edit (line 598-613)

#### **Campaign & Reminder Engine (MEGAPHONE-ready)**
- **Campaign CRUD**: campaigns.py (line 345-530) — DRAFT → SCHEDULED → ACTIVE → COMPLETED | PAUSED
- **Scheduling**: ONE_TIME (send_at ISO), RECURRING (frequency + time_of_day), TRIGGERED (event key) — persisted; ONE_TIME → COMPLETED after send, RECURRING stays ACTIVE (line 759-762)
- **Segmentation**: campaign_segments.py — 6 predefined segments:
  - rx_expiry (window_days=90 default)
  - birthday (next 7 days)
  - winback (no order in 6+ months)
  - by_store, by_customer_type, recent_buyers
  - Each resolves to {customer_id, phone, name, variables{}}
- **Channels**: WHATSAPP, SMS, EMAIL (VALID_CHANNELS, marketing.py:79)
- **Send path**: campaigns.py send_campaign() (line 658-805) reuses send_notification(); respects marketing_consent gate (line 710-717); quiet-hours enforcement (9AM-9PM IST) for promos (marketing.py:91-121); rate-limited (20/10min per user)
- **Analytics**: campaign_audit (immutable rows), campaign_analytics aggregates notification_logs by campaign_id for sent/delivered/failed/opened/converted (line 223-298)
- **Templates**: hardcoded defaults (notification_service.py:56-65) — overridable per-template in settings

#### **Walkouts (Phase 1 MVP)**
- **Schema** (walkouts.py:170-200): customer_name, mobile (optional), age_group (enum), gender, product_interested, has_prescription, displayed_price_range, required_price_range, primary/secondary_walkout_reason (enum), brand_interest, competitor_mentioned, purchase_planned_in (enum), sales_person_id, action_remarks, date (defaults today)
- **Side effects on POST**:
  - If mobile supplied & not in customers: auto-create skeleton customer with source="walkout" (walkouts.py docstring, line 20-23)
  - Audit row written (action="walkout.create")
- **Phases 2-5 planned**: list/edit/delete, follow-ups, dashboard, conversion-feed
- **Frontend**: WalkoutIntakeModal, WalkoutsDashboardPage, WalkoutDetailPage, FollowUpPanel

#### **Follow-ups Module**
- **Schema** (follow_ups.py:46-108): follow_up_id, customer_id/name/phone, store_id, type (eye_test_reminder, frame_replacement, order_delivery, prescription_expiry, general), scheduled_date, status (pending/completed/skipped), outcome enum, notes, created_at, completed_at/by
- **List + filters** (line 163-200): by type, status, date range; store-scoped (BUG-062 validation)
- **Summary endpoint** (implied): due_today, this_week, overdue, completed_this_month, pending_total
- **Frontend**: FollowUpDashboard, FollowUpPanel

#### **Contact-Lens Auto-Refill Signal (CRM-2)**
- **Endpoint**: crm.py /customers/{customer_id}/cl-refill-status (line 469-600)
- **Logic**: Find most recent CL order by category, calculate supply_days (daily disposable: total_lenses/2; monthly: qty×30), predict refill_due_date, return days_remaining + advisory (overdue/7-day window/30-day window)
- **Modality support**: DAILY/MONTHLY/BIWEEKLY with fallbacks

#### **Churn & Segmentation (CRM-3, CRM-4)**
- **RFM segmentation**: crm.py _perform_rfm_segmentation() (line 947-1052) — Champions (rdays≤90 & freq≥3), Loyal (freq≥3), Big Spenders (monetary≥25k), At Risk (rdays≤365), Lost (rdays>365)
- **Churn risk**: _identify_churn_risk_customers() (line 1055-1140) — High (180+ days), Medium (91-179), Low (31-90); matches by customer_id OR normalized phone
- **Lifecycle phases**: _determine_lifecycle_phase() (line 830-882) — prospect, new (<90d), active, at_risk (180+ days no purchase), inactive (365+ days), VIP (LTV≥100k or freq≥20)

#### **Return-Abuse Risk Signal (CRM-5)**
- **Endpoint**: crm.py /customers/{customer_id}/return-risk (line 603-696)
- **Logic**: return_count, return_rate_pct, risk_level (HIGH ≥3 returns or ≥30% rate; MEDIUM 1-2 returns or 15-29%; LOW/NONE)
- **Purpose**: Advisory only, never blocks

#### **Store Credit Ledger (POS-3)**
- **Collections**: credit_note_ledger (append-only), synced to customer.store_credit
- **Operations**: ISSUE/ADJUST (snapshot safe), REDEEM (atomic guarded debit like loyalty)
- **Atomicity**: try_debit_store_credit() guards in filter (customers.py:110-152)
- **Ledger endpoints**: customers.py /store-credit/issue (line 1150-1158), redeem (line 1161-1168), ledger (line 1171-1185)

#### **DPDP Consent Ledger (Compliance)**
- **Consent purposes**: SERVICE_DELIVERY (3yr), MARKETING (0 days), RX_HISTORY (5yr), ANALYTICS (30 days)
- **Ledger operations**: GRANTED, WITHDRAWN, UPDATED — replay to derive active_purposes
- **Endpoints**: customers.py /consent/ledger (line 1364-1396), grant (line 1399-1449), withdraw (line 1452-1525)
- **Partial withdrawal**: e.g., marketing only, flips marketing_consent=False; full withdrawal flags pending_erasure=True + erasure_requested_at

#### **Notification Logging & DLT Audit**
- **Collection**: notification_logs — every send writes: notification_id, customer_id/phone/name, template_id, channel, status (PENDING/SENT/FAILED), delivery_status, opened_at, converted_at, campaign_id (stamped post-send)
- **DLT audit fields**: DLT_PE_ID (env var), category, consent_basis, provider_msg_id
- **DISPATCH_MODE**: off/test/live (env); off = no real sends (default safe); test = TEST_PHONE only
- **Honest status contract**: queued as PENDING (not yet sent); MEGAPHONE drain flips to SENT/SIMULATED/FAILED

### COLLECTIONS (Name - Purpose)

| Collection | Purpose | Key Fields |
|---|---|---|
| **customers** | Core customer doc | customer_id, name, mobile/phone, email, DOB, patients[], home_store_id, preferred_store_id, marketing_consent, data_consent, store_credit, loyalty_points, credit_limit, pending_erasure, erasure_requested_at |
| **loyalty_accounts** | Per-customer balance + tier | customer_id, balance_points, tier, lifetime_earned, lifetime_redeemed |
| **loyalty_transactions** | Immutable ledger (EARN/REDEEM/EXPIRE/ADJUST) | txn_id, customer_id, type, points, rupee_value, order_id, expires_at, created_at |
| **loyalty_settings** | Global points config | enabled, points_per_rupee, category_multipliers, min_order_for_earn, expiry_days, tier_thresholds, tier_multipliers |
| **loyalty_rewards** | Reward catalog (CRM-13) | reward_id, name, type (DISCOUNT/FREE_ITEM/VOUCHER/EXPERIENCE), point_cost, cash_value, max_redemptions, valid_from/until |
| **campaigns** | Marketing campaign records | campaign_id, name, type (rx_renewal/birthday/winback/custom), segment_key, channels, template_id, schedule{kind, send_at, frequency}, status (DRAFT/SCHEDULED/ACTIVE/COMPLETED/PAUSED), audience_count, sent_count, opened_count |
| **campaign_audit** | Immutable campaign lifecycle | audit_id, campaign_id, action (CREATE/UPDATE/SCHEDULE/SEND), actor, detail, at |
| **notification_logs** | Customer notification history | notification_id, customer_id, customer_phone, template_id, channel, status (PENDING/SENT/FAILED), delivery_status, message, category, consent_basis, campaign_id, opened_at, converted_at |
| **credit_note_ledger** | Store credit transactions | customer_id, entry_type (ISSUED/REDEEMED/ADJUSTED), amount, balance_after, reason, ref, created_at |
| **dpdp_consent_ledger** | Consent audit trail | ledger_id, customer_id, event_type (GRANTED/WITHDRAWN/UPDATED), purposes[], text_version, channel, created_at, actor_id, store_id |
| **follow_ups** | Customer follow-up tasks | follow_up_id, customer_id, customer_name/phone, store_id, type, scheduled_date, status (pending/completed/skipped), outcome, notes, created_at, completed_at/by |
| **walkouts** | Walkout intake records (Phase 1) | customer_id (auto-linked), customer_name, mobile, age_group, gender, product_interested, primary_reason, purchase_planned_in, sales_person_id, date, created_at |
| **audit_logs** | System activity audit | action, entity_type, entity_id, store_id, user_id, timestamp, severity (INFO), source (domain/api), before_state, after_state, detail |
| **promo_templates** | Reusable offer definitions (CRM-8) | template_id, name, type (BOGO/COMBO/THRESHOLD), sku_group/sku_list, discount_pct, min_order_value, valid_from/until, active |

### KEY FILES

| File | Purpose | Highlights |
|---|---|---|
| **backend/api/routers/customers.py** | Customer CRUD + consent + credit ledger | 1526 lines; audit on create/update/mobile-change; store dedup by phone |
| **backend/api/routers/crm.py** | 360 views, segmentation, churn/return risk | 1141 lines; RFM (Recency/Frequency/Monetary), CL refill signal, lifecycle phases |
| **backend/api/routers/loyalty.py** | Points earn/redeem/adjust/expiry | 1059 lines; atomic try_debit guard; per-lot FIFO expiry; rewards catalog; family pooling NOT implemented |
| **backend/api/routers/campaigns.py** | Campaign management + segment targeting + send | 1045 lines; DRAFT→SCHEDULED→ACTIVE flow; 6 segment types; reuses send_notification |
| **backend/api/routers/marketing.py** | Bulk send + quiet-hours + rate-limit | 600+ lines; consent gate + DISPATCH_MODE safe; MEGAPHONE hook point |
| **backend/api/routers/follow_ups.py** | Follow-up task CRUD + summary | 200+ lines; store-scoped (BUG-062 fixed); type/status/date filters |
| **backend/api/routers/walkouts.py** | Phase 1 walkout intake | 200+ lines; auto-customer skeleton on mobile; audit on create |
| **backend/api/routers/notifications.py** | Staff in-app bell notifications | 187 lines; snoozeable (max 3x); distinct from customer notification_logs |
| **backend/api/services/loyalty_engine.py** | Pure earn/redeem/tier math | 150+ lines; stateless; category_multiplier, tier_multiplier, expiry_for_earn() |
| **backend/api/services/campaign_segments.py** | Segment resolution (rx_expiry, birthday, winback, etc.) | 150+ lines; LIVE audience counts; fail-soft (empty on DB error); no consent filter |
| **backend/api/services/notification_service.py** | Queue notifications (PENDING) + DLT audit | 150+ lines; honest-status contract; populate_template; DISPATCH_MODE gating |
| **backend/database/repositories/customer_repository.py** | Customer data access + dedup | search_customers on {name, mobile, phone, email, patients.name/mobile}; store scope OR logic |
| **backend/database/repositories/loyalty_repository.py** | Loyalty ledger + account balance | find_or_create; adjust_balance (atomic $inc); try_debit (guard filter); DEFAULT_SETTINGS |
| **frontend/src/pages/customers/Customer360Dashboard.tsx** | 360-degree customer view | LTV, loyalty tier, prescriptions, interactions, churn/return risk cards |
| **frontend/src/pages/customers/CustomerSegmentation.tsx** | RFM segment browse | Champions, Loyal, Big Spenders, At Risk, Lost |
| **frontend/src/pages/customers/FollowUpDashboard.tsx** | Follow-up task list + create | Type/status/date filters; assign to staff |
| **frontend/src/pages/walkouts/WalkoutIntakeModal.tsx** | Walkout form (Phase 1) | Enum dropdowns (age_group, gender, reason, purchase_plan); optional mobile |
| **frontend/src/pages/walkouts/WalkoutsDashboardPage.tsx** | Walkout list + summary | Reasons (budget/collection/brand/etc.); follow-up pipeline (Phase 3) |

### REUSABLE FOR WHICH OF THE 52 FEATURES

1. **#2 CRM-2 (Contact-Lens Auto-Refill)**: ✓ REUSE — /crm/customers/{id}/cl-refill-status endpoint live; modality logic, supply_days calc, refill_due_date
2. **#3 CRM-3 (Churn Risk)**: ✓ REUSE — _identify_churn_risk_customers(), 180+ day threshold, recency map aggregation
3. **#4 CRM-4 (RFM Segmentation)**: ✓ REUSE — _perform_rfm_segmentation(), Champions/Loyal/Big Spenders/At Risk/Lost buckets
4. **#5 CRM-5 (Return-Abuse Risk)**: ✓ REUSE — /crm/customers/{id}/return-risk endpoint; return_count, return_rate_pct, risk_level
5. **#8 CRM-8 (Promo Offers BOGO/COMBO/THRESHOLD)**: ✓ EXTEND — campaigns.py has promo_templates CRUD (line 920-1044); templates sit on voucher engine
6. **#13 CRM-13 (Loyalty Reward Catalog)**: ✓ LIVE — loyalty.py /rewards GET/POST/PUT/DELETE (line 757-875); DISCOUNT/FREE_ITEM/VOUCHER/EXPERIENCE types
7. **#15 MKT-1 (Birthday Campaign)**: ✓ REUSE — campaign_segments.py birthday segment (next 7 days DOB); birthday template in TEMPLATES dict
8. **#16 MKT-2 (Rx Renewal Reminder)**: ✓ REUSE — campaign_segments.py rx_expiry segment (90-day window); RX_EXPIRY_WINDOW_DAYS tunable
9. **#17 MKT-3 (Walkout Recovery)**: ✓ REUSE — campaign_segments.py winback segment (6-month inactive); WALKOUT_RECOVERY template; walkouts collection intake
10. **#20 MKT-6 (WhatsApp Campaigns)**: ✓ LIVE — campaigns.py reuses send_notification(); WHATSAPP in VALID_CHANNELS; quiet-hours enforcement
11. **#21 MKT-7 (SMS Campaigns)**: ✓ LIVE — SMS in VALID_CHANNELS; same send_notification path
12. **#24 MKT-10 (Campaign Builder UI)**: ✓ EXTEND — CampaignCreate/Update schemas, segment picker, schedule controls live
13. **#25 MKT-11 (Bulk Send API)**: ✓ LIVE — marketing.py /notifications/send-bulk (line 268-320); rate-limited, consent-gated
14. **#26 MKT-12 (Loyalty Points - Earn)**: ✓ LIVE — loyalty.py /earn (line 232-318); calc_earn_points with category/tier multipliers
15. **#27 MKT-13 (Loyalty Points - Redeem)**: ✓ LIVE — loyalty.py /redeem (line 321-418); atomic try_debit; percent-of-order cap
16. **#28 MKT-14 (Loyalty Tiers)**: ✓ LIVE — tier calc from lifetime_earned thresholds (BRONZE/SILVER/GOLD/PLATINUM); tier multipliers
17. **#29 MKT-15 (Store Credit / Khata)**: ✓ LIVE — customers.py /store-credit endpoints; atomic ledger with balance_after; credit_limit per-customer (B2B)
18. **#30 MKT-16 (Referral Program)**: ✓ PARTIAL — REFERRAL_INVITE template in TEMPLATES; referral logic (awarding credit) NOT implemented
19. **#31 MKT-17 (NPS Survey)**: ✓ PARTIAL — NPS_SURVEY template; RxSnoozeRequest in marketing.py; full survey results collection missing
20. **#32 SAL-1 (Walkout Logging)**: ✓ LIVE (Phase 1) — walkouts.py POST create (line 170-200); auto-customer skeleton + audit
21. **#33 SAL-2 (Walkout Follow-up)**: ✓ PLANNED (Phase 3) — follow_ups.py collection exists; FollowUpPanel UI stub
22. **#35 CUS-1 (Customer Profiles)**: ✓ LIVE — customers.py full CRUD; name/mobile/email/DOB/patients[]; B2B GSTIN validation
23. **#36 CUS-2 (Customer Search)**: ✓ LIVE — search_customers() on {name, mobile, phone, email, patients.name/mobile}; dedup by phone
24. **#37 CUS-3 (Family Members)**: ✓ LIVE — patients[] embedded array; patient_id + relation tracking
25. **#38 CUS-4 (Contact Lens Orders / Reorder)**: ✓ REUSE — CL category detection (CONTACT_LENS/CL/CONTACTS); modality + pack_size extraction
26. **#40 CUS-6 (Data Consent - DPDP)**: ✓ LIVE — customers.py /consent endpoints (line 1364-1525); PURPOSE_RETENTION_DAYS; ledger replay to derive active
27. **#41 CUS-7 (Marketing Consent Opt-out)**: ✓ LIVE — marketing_consent boolean; gated in send paths (campaigns.py line 710-717, marketing.py line 293-298)
28. **#42 CUS-8 (Customer Lifecycle)**: ✓ REUSE — _determine_lifecycle_phase() in crm.py; 6 phases with recommended actions

### GENUINE GAPS

1. **Family-pooled loyalty points**: Loyalty engine is per-customer only; no aggregation/sharing across family members (patients[]). Referral reward distribution (MKT-16) missing.
2. **NPS survey results collection**: TEMPLATES has NPS_SURVEY, but no `nps_responses` collection or /survey/{nps_id} endpoint to record scores/feedback.
3. **Automated follow-up triggers**: follow_ups collection exists but no automation logic (no "trigger on rx_expiry → auto-create follow-up"); Phase 2 feature.
4. **Lookbook / Product recommendations**: No collection or endpoints for customer purchase history → recommended style/color/brand (mentioned in CRM design but not implemented).
5. **Walkout follow-up approval workflow** (Phase 3): ApprovalStatus enum exists but no endpoints to approve/reject DONE follow-ups from managers.
6. **Promotion quiet-hours override**: Quiet-hours enforcement (9AM-9PM IST) cannot be disabled by SUPERADMIN (policy-level control missing).
7. **Referral code minting & tracking**: Referral template exists but no referral_codes collection or /referral endpoints to issue/redeem codes and track conversions.
8. **Bulk customer import / dedup safety**: No batch import endpoint; single-customer dedup is on mobile only (no email-based dedup across B2B/B2C mix).
9. **Loyalty expiry pre-notification**: Customers aren't notified 7 days before points expire; only a "expiring_soon_points" read field in loyalty account snapshot.
10. **Campaign exclusion lists**: Segment resolvers don't support exclusion (e.g., "birthday segment EXCEPT those who redeemed in last 30 days"); always additive.

---

## Summary

IMS 2.0 has **production-ready CRM foundations**: customer profiles with family members, atomic loyalty (no double-spend), DPDP consent audit, churn/RFM segmentation, campaign builder with 6 segments, bulk SMS/WhatsApp via MEGAPHONE, store credit ledger, and Phase 1 walkout intake. **Key reuses across the 52 features**: campaign_segments.py (birthday/Rx/winback), loyalty_engine.py (earn/redeem math), notification_service.py (MEGAPHONE queue), customer_repository.py (search/dedup). **Real gaps**: family loyalty pooling, NPS results tracking, automated follow-up triggers (Phase 2+), referral code system, lookbook/recommendations.


# === Clinical / Prescriptions / Workshop ===
Now I have enough information. Let me compile the comprehensive audit report:

## IMS 2.0 Clinical/Prescriptions/Workshop Audit Report

### Capabilities

**RX Schema & Validation** (prescriptions.py:107-211)
- Spectacle Rx: SPH -20.00 to +20.00, CYL -6.00 to +6.00 (both in 0.25 steps), AXIS 1-180 whole degrees, ADD +0.75 to +3.50 diopter range validation
- Contact-lens Rx (May 2026): CL_POWER -30 to +30, CL_CYL -10 to +10, CL_ADD 0-4, BASE_CURVE 7-10mm, DIAMETER 12-16mm
- Modality support: DAILY, FORTNIGHTLY, MONTHLY, QUARTERLY, YEARLY, COLOR  
- Validator `_validate_rx_value()` enforces 0.25-diopter grid for sphere/cyl/add; reused across eye-test capture (clinical.py:356) and prescription edit paths (prescriptions.py:1039-1050)
- Prism validation: 0-10 prism dioptres; base direction (UP/DOWN/IN/OUT) (prescriptions.py:251-270)

**Eye-Test Flow** (clinical.py:449-964)
- Queue lifecycle: WAITING → IN_PROGRESS → COMPLETED (or CANCELLED/NO_SHOW)
- Test creation stamps optometrist_id + optometrist_name (clinical.py:585-586)
- Auto-Rx minting on completion: `complete_test()` generates prescription_id, prescription_number, expiry_date (clinical.py:847-935)
- Clinical findings (C6-B, optional): VA (unaided/aided per eye), IOP (0-80 mmHg per eye), visual acuity, colour vision, cover test, dominant eye, additional notes (clinical.py:104-147)
- SOAP note support (CLI-11): Subjective/Objective/Assessment/Plan + structured Dx codes (ICD-10/ICPC-2), recorded_by/recorded_at auto-stamped (clinical.py:168-250)
- Idempotency guard: retried completion checks if test already COMPLETED; returns existing prescription_id (clinical.py:782-793)

**Prescription Schema** (prescriptions.py:301-391)
- PrescriptionCreate: patient_id, customer_id, rx_kind (SPECTACLE|CONTACT_LENS, defaults SPECTACLE), source (TESTED_AT_STORE|FROM_DOCTOR), optometrist_id, validity_months (6-24, defaults 12 for CL / 24 for spectacle)
- Back-dating support: prescription_date (ISO datetime, future-rejected), expiry_date auto-computed
- Spectacle eyes: right_eye/left_eye (sph/cyl/axis/add/pd/prism/base/acuity), lens_recommendation, coating_recommendation
- Contact-lens eyes: cl_right/cl_left (cl_power/cl_cyl/cl_axis/cl_add/base_curve/diameter), cl_brand, cl_series, modality, color, cl_product_id
- Parity fields: ipd (single binocular PD), next_checkup (scheduling integration point), remarks
- Validity: prescriptions expire at test_date + validity_months; family-view calculates is_valid (prescriptions.py:667-688)

**Family Rx View** (prescriptions.py:691-803)
- Groups a customer's prescriptions by patient_id (family members)
- Returns roster (patient name / relation / DOB) + per-member Rx history (sorted newest-first)
- Enriches each Rx with expiry_date, is_valid, latest-Rx pointer
- Handles unlinked patients (legacy/imported data) under "Unlinked patient" group
- Store-scoped (BUG-088): caller sees only their own stores' family Rx

**Clinical → Retail Handover (handoffs.py:1-695)**
- File-based handoff (image/PDF ≤25MB): title, description, validity_days (3-30), recipient assignment
- Recipient roles: STORE_MANAGER, ACCOUNTANT, ADMIN, SUPERADMIN only
- Responses: approved, denied, accepted, received, reshared (plus per-user dismiss/keep/snooze)
- TTL cleanup: Mongo TTL index on expires_at; GridFS blob cleanup via NEXUS hourly sweep
- Reshare: creates NEW handoff doc with parent_handoff_id, inherits original expires_at (TTL capped to original)
- Hub inbox filtering: hides dismissed (unless kept), hides snoozed until expiry
- No explicit "clinical-to-retail transfer" flow in current code; handoffs are generic file-routing

**Workshop Job Lifecycle** (workshop.py:36-89)
- Lens-order forward-only: NOT_ORDERED → ORDERED → RECEIVED → MOUNTED (each stamps a timestamp field: lens_ordered_at, lens_received_at, lens_mounted_at)
- Job status: PENDING → IN_PROGRESS → COMPLETED → (READY or QC_FAILED) → READY → DELIVERED (or CANCELLED anytime)
- Immutable states: READY, DELIVERED, CANCELLED block detail edits (workshop.py:789)

**QC & Rework** (workshop.py:1026-1195)
- Simple pass/fail: /jobs/{id}/qc?passed=bool&notes=str advances COMPLETED → READY (passed) or QC_FAILED (failed)
- Checklist QC (structured): /jobs/{id}/qc-checklist with per-item key/label/passed/note + overall_notes + waiver (waived=True requires waive_reason)
- Waiver path: effective_pass = all_passed OR waived; all waivers audit-logged
- Rework gate: QC_FAILED → IN_PROGRESS (increments rework_count, appends to history)
- Patient safety (BUG-116a): job cannot reach READY without qc_passed=True OR qc_waived=True (enforced in both /qc and generic status PATCH)

**Fitting Details (Phase 6.8, CLI-6)** (workshop.py:99-148)
- Captured by sales post-order: dia, fh, b_size, dbl, tint, base_curve, coating, other, order_date, order_time, ordered_by, ordered_by_name, expected_lens_receive_date, vendor_order_id
- confirmed_by_sales gate: must be True before workshop accepts job (→ IN_PROGRESS) (workshop.py:865-874, 976-981)
- Progressive-lens parameters: segment_height (mm), pantoscopic_tilt (deg), vertex_distance (mm), wrap_angle (deg)
- /jobs/{id}/fitting-details PATCH auto-stamps order_date/order_time/ordered_by if absent, confirmed_at if confirmed_by_sales=True

**Vendor/Lab Integration** (workshop.py:1475-1631)
- Admin assigns vendor_id to a job; caches vendor_name
- Vendor status history: RECEIVED, IN_PRODUCTION, DISPATCHED, DELIVERED, ON_HOLD, CANCELLED (source='ims_user' if logged by IMS staff vs lab portal)
- Tracking URL, dispatch date, received date fields stored per job
- `/jobs/by-vendor/{vendor_id}` admin view mirrors the external vendor portal queue
- Dispatch auto-timestamps vendor_dispatch_date; delivery auto-timestamps vendor_received_date

**Remake/Spoilage Tracking** (workshop.py:1217-1236)
- rework_count incremented on each QC_FAILED → IN_PROGRESS transition
- rework_count + qc_history array preserve every QC attempt (passed/failed/waived + notes + checklist items)
- No explicit "spoilage" state; QC_FAILED represents failed lens that needs rework

**Optometrist Attribution** (clinical.py:585-586, prescriptions.py:868-869)
- Eye test stamps optometrist_id + optometrist_name at test creation
- Auto-Rx stamps optometrist_id + optometrist_name from current_user at completion
- Prescription edit (PUT) cannot change optometrist_id (immutable provenance field)
- Optometrist stats endpoint: /optometrist/{id}/stats (date range) returns total + tested_at_store counts

**Lens-Stock Reservation & Commit** (workshop.py:1314-1359)
- On lens MOUNTED: calls `commit_for_workshop_dispatch()` per order item to hard-commit reserved lens from catalog tray
- Branch B' sub-PR 4: fails soft (log warning, never block mount transition)

**Customer Communication** (workshop.py:1379-1463)
- notify-ready: sends WhatsApp via MSG91 (DISPATCH_MODE-gated, fail-soft), stamps ready_notified_at + ready_notified_by, logs in-app notification
- _ready_whatsapp_text(): templated "Hi {name}, your eyewear order is ready for pickup" message

**Prescription Print (A5 Card)** (clinical.py:1149-1318)
- _build_rx_card_html(): self-contained printable (no external assets), HTML renders at 148mm × 210mm (A5)
- Clinic header: name, address, phone, GSTIN
- Patient block: name, age, phone, Rx number, date
- Rx table: SPH/CYL/AXIS/ADD per eye, formatted via format_rx_value() (None/"" → blank, 0 → "Plano", otherwise ±2 decimals) + format_axis_value()
- PD + optometrist signature line
- Client-side: /prescriptions/{id}/print returns HTMLResponse (auto-opens print dialog onload)

**Abuse/Fraud Detection** (clinical.py:1436-1468 stub)
- _rx_has_redo(): checks redo_count > 0 or redos array length or redo_of field
- _opto_label() + _patient_label() helpers for labeling (names preferred, else IDs)
- GET /clinical/abuse-view gated to STORE_MANAGER / AREA_MANAGER / ADMIN (no optometrists)
- No scoring/alerting logic visible (stub for future integration)

---

### Collections

| Name | Purpose | Key Fields |
|------|---------|-----------|
| eye_test_queue | Daily queue of patients waiting for tests | queue_id, store_id, patient_name, customer_phone, status (WAITING\|IN_PROGRESS\|COMPLETED\|CANCELLED\|NO_SHOW), token_number, age, reason, customer_id, patient_id |
| eye_tests | Completed eye test records | test_id, queue_id, store_id, optometrist_id, optometrist_name, patient_name, customer_phone, customer_id, patient_id, status, right_eye (refraction dict), left_eye, pd, ipd, next_checkup, notes, lens_recommendation, coating_recommendation, clinical_findings (nested C6-B: VA/IOP/history/diagnosis), soap_note (nested SOAP: subjective/objective/assessment/plan + dx_codes) |
| prescriptions | Rx library (spectacle + contact-lens) | prescription_id, prescription_number, patient_id, customer_id, rx_kind (SPECTACLE\|CONTACT_LENS), store_id, source (TESTED_AT_STORE\|FROM_DOCTOR), optometrist_id, optometrist_name, prescription_date, test_date, expiry_date, validity_months, right_eye (spectacle), left_eye, cl_right (CL), cl_left, cl_brand, cl_series, modality, color, lens_recommendation, coating_recommendation, ipd, next_checkup, remarks, eye_test_id, redo_count, redo_of, redo_reason, redo_by, redo_at, redos (array of {redo_id, reason, redo_by, redo_by_name, redo_at}), versions (4-version model: before_testing, after_testing, manual, final), status (in_progress\|finalized) |
| workshop_jobs | Lens job orders from shop floor to completion | job_id, job_number, order_id, store_id, customer_id, customer_name, customer_phone, status (PENDING\|IN_PROGRESS\|COMPLETED\|QC_FAILED\|READY\|DELIVERED\|CANCELLED), prescription_id, frame_details (dict), frame_name, frame_barcode, lens_details (dict), lens_type, fitting_instructions, special_notes, expected_date, promised_date, technician_id, assigned_at, created_at, completed_at, updated_at, updated_by, created_by, lens_status (NOT_ORDERED\|ORDERED\|RECEIVED\|MOUNTED), lens_ordered_at, lens_received_at, lens_mounted_at, fitting_details (nested: dia, fh, b_size, dbl, tint, base_curve, coating, other, order_date, order_time, ordered_by, ordered_by_name, expected_lens_receive_date, vendor_order_id, confirmed_by_sales, confirmed_at, segment_height, pantoscopic_tilt, vertex_distance, wrap_angle), vendor_id, vendor_name, vendor_order_id, vendor_tracking_url, vendor_dispatch_date, vendor_received_date, vendor_status, vendor_status_history (array), vendor_updated_by, vendor_updated_at, qc_passed, qc_notes, qc_by, qc_at, qc_waived, qc_waive_reason, qc_checklist (array of {key, label, passed, note, checked_by, checked_at}), qc_history (array), rework_count, status_updated_at, status_updated_by, status_notes, ready_notified_at, ready_notified_by |
| handoffs | File-based user-to-user transfers (clinical → retail, etc.) | handoff_id, uploader_id, uploader_name, title, description, file (nested: file_id, filename, mime_type, size_bytes), recipients (array of {user_id, user_name, role, status, response, comment, responded_at, dismissed, kept, snooze_until}), created_at, expires_at, validity_days, parent_handoff_id |
| audit_logs | Domain-level audit trail (not activity log) | action (EYE_TEST_RECORDED, SOAP_NOTE_SAVED, PRESCRIPTION_CREATED, PRESCRIPTION_UPDATED, workshop.qc_checklist, workshop.lens_status, workshop.vendor_assign, workshop.vendor_status), entity_type (CLINICAL, PRESCRIPTION, workshop_job), entity_id, store_id, user_id, user_name, timestamp, severity (INFO), source (domain), detail (dict) |

---

### Key Files

| File | Purpose | Lines |
|------|---------|-------|
| backend/api/routers/clinical.py | Eye test queue + test capture + SOAP notes + Rx print + redo tracking + abuse detection | 1974 |
| backend/api/routers/prescriptions.py | Rx CRUD + family view + 4-version model + contact-lens support | 1557 |
| backend/api/routers/workshop.py | Job lifecycle + QC + lens-order tracking + vendor integration + ready notifications | 1636 |
| backend/api/routers/handoffs.py | File-based clinical→retail transfer (or any user→user handoff) | 695 |
| backend/database/repositories/clinical_repository.py | EyeTestQueueRepository + EyeTestRepository (queue add/status/stats, test creation) | 150+ |
| backend/database/repositories/prescription_repository.py | find_by_eye_test(), find_valid(), find_expiring_soon(), family grouping helpers | 99 |
| backend/database/repositories/workshop_repository.py | find_pending(), find_ready(), find_overdue(), update_status(), add_qc_result(), assign_technician() | 150+ |
| backend/database/repositories/handoff_repository.py | find_inbox_for_user(), find_sent_by_user(), update_recipient() (dismiss/keep/snooze logic) | — |
| frontend/src/pages/clinical/ClinicalPage.tsx | Queue + test capture UI | — |
| frontend/src/pages/clinical/TestHistoryPage.tsx | Historical tests + print A5 Rx card | — |
| frontend/src/pages/clinical/PrescriptionsPage.tsx | Rx library browse/search/filter | — |
| frontend/src/pages/clinical/FamilyRxPage.tsx | Family household Rx view (by member) | — |
| frontend/src/pages/clinical/ContactLensFittingPage.tsx | Contact-lens Rx capture (CL_KIND support) | — |
| frontend/src/pages/workshop/WorkshopPage.tsx | Job kanban (PENDING→IN_PROGRESS→COMPLETED→QC→READY→DELIVERED), lens lifecycle, technician assign | — |

---

### Reusable for Which of the 52 Features

(Features inferred from audit scope: clinical/prescriptions/workshop context)

- **#5-N: Eye Test Queue Management** — REUSE: EyeTestQueueRepository.add_to_queue(), get_store_queue(), update_status(). Customer+patient_id threading (clinical.py:485-489) supports family testing.
- **#6-N: Refraction Capture** — REUSE: EyeTestData schema + validation (clinical.py:252-273), _validate_eye_test_rx() (line 342), canonical ranges enforced at complete_test() (line 807). Clinical findings (C6-B) + SOAP (CLI-11) optional layers.
- **#7-N: Prescription Auto-Mint** — REUSE: complete_test() lines 831-935 (idempotent, mints on first completion, stores eye_test_id for lookup).
- **#8-N: Prescription CRUD** — REUSE: PrescriptionCreate/Update schemas (prescriptions.py:301-445), canonical validation (_validate_rx_value, _validate_cl_eye), expiry computation (_add_months).
- **#9-N: Prescription Validity** — REUSE: _rx_validity() (prescriptions.py:667-688), find_valid() repository method, expiry filtering in family view.
- **#10-N: Contact-Lens Rx** — REUSE: rx_kind=CONTACT_LENS branch (prescriptions.py:872-881), CLEyeData schema (line 287), _validate_cl_eye() (line 457), CL print HTML (line 1276).
- **#11-N: Family Rx Grouping** — REUSE: family_prescriptions() endpoint (prescriptions.py:691-803), _enrich() helper, patient roster matching, unlinked handling.
- **#12-N: Workshop Job Lifecycle** — REUSE: VALID_JOB_TRANSITIONS dict (workshop.py:81-89), update_job_status() (line 809), status guards (READY requires QC, immutable after COMPLETED).
- **#13-N: Fitting Details Capture** — REUSE: FittingDetails schema (workshop.py:99-148), Phase 6.8 fields (segment_height, pantoscopic_tilt, vertex_distance, wrap_angle), PATCH endpoint (line 714), confirmed_by_sales gate.
- **#14-N: QC Pass/Fail** — REUSE: qc_job() endpoint (workshop.py:1026-1069, simple) + qc_checklist() (line 1072, structured per-item), add_qc_result() repository (workshop_repository.py:93-149, atomically stamps + transitions status).
- **#15-N: Lens Rework** — REUSE: rework_job() endpoint (workshop.py:1198-1236), increments rework_count, appends to status history, gates QC_FAILED → IN_PROGRESS only.
- **#16-N: Vendor Assignment** — REUSE: patch_job_vendor() (workshop.py:1475-1548), vendor_id → visibility on portal, caches vendor_name, audits changes.
- **#17-N: Vendor Status Tracking** — REUSE: post_admin_vendor_status() (workshop.py:1551-1630), vendor_status_history array (source=ims_user for IMS-logged updates), DISPATCHED/DELIVERED auto-timestamp dispatch_date/received_date.
- **#18-N: Lens-Order Lifecycle** — REUSE: NOT_ORDERED→ORDERED→RECEIVED→MOUNTED forward-only transition (workshop.py:36-89), _next_lens_status_ok() pure guard (line 47), update_lens_status() endpoint (line 1247) with timestamp fields.
- **#19-N: Ready Notification** — REUSE: notify_ready() (workshop.py:1379-1463), WhatsApp + audit log + in-app notification (fail-soft).
- **#20-N: Optometrist Attribution** — REUSE: optometrist_id + optometrist_name stamped at test creation (clinical.py:585-586) + auto-Rx creation (line 868), preserved immutably on prescription, /optometrist/{id}/stats endpoint.
- **#21-N: Prescription Redo/Remake** — REUSE: create_prescription_redo() endpoint (clinical.py:1355-1410), redos array + redo_of/redo_reason/redo_by/redo_at shortcut fields, list_prescription_redos() (line 1413).
- **#22-N: A5 Rx Card Print** — REUSE: _build_rx_card_html() (clinical.py:1149-1317, self-contained), /prescriptions/{id}/print endpoint (line 1320), supports both spectacle + CL print HTML variants.
- **#23-N: Clinical→Retail Handoff** — REUSE: handoffs.py generic file-routing (title/description, recipient assignment, responses, dismissal). Not a dedicated clinical workflow; extend as needed.
- **#24-N: SOAP Note Charting** — REUSE: SoapNote schema (clinical.py:168-250, CLI-11), GET/POST /tests/{id}/soap-note endpoints (line 1025 + 1055), dx_codes structured array.
- **#25-N: Abuse/Fraud Signaling** — PARTIAL: _rx_has_redo() helper (line 1438), _opto_label/_patient_label() (line 1456), get_abuse_view() gated to managers. Logic stub; integrate scoring engine.

**Other features (26-52) outside clinical/prescriptions/workshop scope** — see orders, inventory, POS, finance, HR, etc. routers.

---

### Genuine Gaps

1. **No dedicated clinical→retail transfer flow** — Handoffs are generic file-routing. Extend with "clinical sign-off → ready for sales" checkpoint if needed.

2. **No servicing/repair concept** — Workshop handles remake (QC_FAILED) but not: "customer returned glasses with scratch → send back to lab → re-deliver." Model as a separate job type or order state (future).

3. **No spoilage tracking** — QC_FAILED counts remakes; no distinct "spoilage" (lab error vs customer defect) classification. Extend qc_waive_reason or add spoilage_reason field if required.

4. **Abuse/fraud detection is a stub** — _rx_has_redo() exists; no actual scoring, alerting, or dashboard (get_abuse_view gated but unimplemented). Wire in ML/heuristic engine.

5. **No tray/inventory reservation detail** — lens_status lifecycle tracks physical movement (NOT_ORDERED→MOUNTED); commit_for_workshop_dispatch() on MOUNTED is a hook but no tray-cell reservations visible in these routers. Check lens_stock_hook.py.

6. **No optometrist license/credential validation** — optometrist_id is stamped; no OPTOMETRIST role enforcement on test completion or Rx finalize (role checks exist but no scope-to-store). Extend role-based access if multi-optometrist stores needed.

7. **No prescription version history UI** — 4-version model (before_testing, after_testing, manual, final) exists in schema (prescriptions.py:1365-1536) + db, but no frontend to browse/compare versions. UI is TODO.

8. **No lab scorecard / performance metrics** — vendor_status_history tracks status; no turnaround SLA, defect rate, or vendor ranking. Extend analytics.

9. **No customer-facing Rx portal or re-order** — Family Rx is internal only. No customer login to view valid Rx + reorder.

10. **No refund/credit for spoilage** — QC_FAILED → rework or CANCELLED, but no financial adjustment (order items remain charged). Pair with refunds/credits system if needed.

---

**File Locations (Absolute Paths)**
- backend/api/routers/clinical.py
- backend/api/routers/prescriptions.py
- backend/api/routers/workshop.py
- backend/api/routers/handoffs.py
- backend/database/repositories/clinical_repository.py
- backend/database/repositories/prescription_repository.py
- backend/database/repositories/workshop_repository.py
- frontend/src/pages/clinical/ClinicalPage.tsx
- frontend/src/pages/workshop/WorkshopPage.tsx


# === Omnichannel / Shopify / ONDC / Webhooks / Shipping ===
Now I have enough information. Let me compile the audit report:

## IMS 2.0 Omnichannel/Shopify/ONDC/Webhooks Audit Report

### Capabilities

#### Shopify Integration
- **Order Pull** (nexus_providers.py:96-136): Async pull of Shopify orders (created in last N hours, default 2h) via Admin API v2024-01. Returns `SyncResult` with count and sample order IDs. Read-only, always allowed.
- **Product Push** (nexus_providers.py:138-200): RETIRED by default. Gated on `IMS_SHOPIFY_WRITES=1` + `DISPATCH_MODE=live` + creds. Returns SIMULATED when gates off. Only updates when BVI app is decommissioned.
- **Inventory Set** (nexus_providers.py:211-369): Core oversell guard. Pushes ABSOLUTE on-hand (minus safety buffer) to Shopify's `inventorySetQuantities` GraphQL mutation for one SKU at one location. Idempotent (retry-safe). Used by `online_stock_writeback.py` on every in-store sale to prevent online overselling.
- **Webhook Receivers** (webhooks.py:526-535): POST `/webhooks/shopify` (signed via X-Shopify-Hmac-Sha256). Verifies signature, persists envelope to `webhook_inbox`, dispatches `webhook.received` event to NEXUS.

#### Customer Bridging (Shopify → IMS)
- **Customer Count** (online_store.py:138-140): Counts `customers` docs with `shopify_customer_id` field (channel-bridged).
- **Variant-SKU Resolution** (online_order_mapper.py:104-138): For each Shopify line item, resolves `variant_id` → `catalog_variants.shopify_variant_id` → IMS `sku`. Fallback: uses line's own `sku` field. Enables proper HSN/GST billing inside existing ingest path.
- **Customer Match/Create** (online_order_mapper.py, documented): Matches IMS `customers` row by phone/email, creates minimal ONLINE channel customer if missing. Orders stamped with resolved `customer_id` so CRM/loyalty/AR see same buyer.

#### Online Order Ingestion (Shopify → IMS Orders)
- **Order Mapper** (online_order_mapper.py): Maps Shopify `orders.json` → canonical IMS order (channel='ONLINE'). Enriches line items with resolved SKUs, resolves store bucket (integration config → env → settings → primary store → 'BV-ONLINE-01'), mutates payment/fulfillment status on re-ingest.
- **Router** (online_store_orders.py): GET `/api/v1/online-store/orders` (role-gated ADMIN/ACCOUNTANT/SUPERADMIN). Lists `orders` with `channel='ONLINE'`. POST `/remap/{shopify_order_id}` re-runs mapper from persisted webhook payload (recovery for stuck orders). Writes audit trail.
- **GST Calculation**: Reuses same `catalog_products.gst_rate` master as POS (5% frames, 18% sunglasses, 12% contacts). Online orders flow into books with correct CGST/SGST/IGST split for state-of-supply.

#### ONDC Integration
- **Protocol Callbacks** (ondc.py:215-417): PUBLIC (no IMS JWT) Beckn protocol endpoints:
  - `POST /on_search`: SNP triggers catalog publish (fail-soft, DARK when disabled).
  - `POST /on_select`, `/on_init`: Stub ACKs (full quote logic BVI-20 backlog).
  - `POST /on_confirm`: Signature-verified (HMAC-SHA256 via Authorization header, fail-closed when key absent). Calls `ondc_seller.ingest_ondc_order()` to create IMS order (channel='ONDC'). Writes audit row.
  - `POST /on_cancel`: Signature-verified, flips order to CANCELLED. Fail-closed (BUG-102: no key → reject, don't cancel).
  - `POST /on_status`: Returns order status by `external_order_id`.
- **Catalog Push** (ondc_seller.py, stub): Builds ONDC items from `catalog_products` + variants. Maps HSN/GST to ONDC tax codes (5%→GST_5, 12%→GST_12, 18%→GST_18, 28%→GST_28). Country of origin fallback: ONDC_DEFAULT_COO env or 'IND'.
- **TCS Reconciliation** (ondc_seller.py, documented): 1% TCS on SNP commission. Records in `ondc_settlements` collection so Finance sees the deduction.
- **Admin Status** (ondc.py:429-495): GET `/status` (ADMIN/SUPERADMIN) returns enabled flag, last publish timestamp, item count, ONDC order count, TCS total.
- **Manual Publish** (ondc.py:498-510): POST `/publish` (ADMIN/SUPERADMIN) triggers catalog push (DARK when disabled).

#### Shipping (Shiprocket)
- **Book Shipment** (shipping.py:184-277): POST `/shipments` (role-gated FULFILMENT_ROLES: ADMIN/AREA_MANAGER/STORE_MANAGER/CASHIER/SALES_CASHIER). Merges request address with customer doc, books via `shiprocket.create_shipment()`. Returns AWB, label URL, tracking status. SIMULATED when DISPATCH_MODE ≠ live or creds missing. Never 500s.
- **List Shipments** (shipping.py:279-317): GET `/shipments?order_id=&store_id=` (store-scoped for non-HQ). Returns shipment records (persisted to `shipments` collection).
- **Track** (shipping.py:319-378): GET `/shipments/{id}/track` live-tracks via Shiprocket (reads current status), falls back to last-known on doc if API unavailable. Updates doc when fresh status received.
- **Credentials**: Resolved from env (`SHIPROCKET_EMAIL`/`SHIPROCKET_PASSWORD`) or `integrations` collection. Token cached in-process (9-day TTL, re-auth on 401).
- **Service** (shiprocket.py): Async client (`httpx`). Mutating calls (create_shipment) gated on DISPATCH_MODE=live. Read-only (track) allowed in any mode, degrades to SIMULATED when creds missing.

#### Webhook Receivers & Security
- **Signature Verification** (webhooks.py:186-299): Shared `_ingest()` pipeline. Reads raw body, looks up per-vendor `webhook_secret` from `integrations` collection, verifies HMAC via verifier function (Razorpay/Shopify/Shiprocket each have own). Bad signature → 401. Missing secret → 200 skipped (fail-soft, vendor no-retry).
- **Replay Protection** (webhooks.py:238-255): Best-effort check via `webhook_verify.is_replay(timestamp)`. Stale event (outside replay window) → 200 skipped, logged.
- **Inbox Persistence** (webhooks.py:259-278): Envelope stored to `webhook_inbox` collection with TTL 30 days. Indexed by `webhook_id`, `vendor`. NEXUS reads unprocessed rows on its tick.
- **WhatsApp Inbound** (webhooks.py:686-820): Meta WABA integration (CRM-14). GET challenge (Public, verify token optional). POST message (HMAC-SHA256 signature, fail-closed when secret absent). Extracts text/button payload, upserts conversation thread to `whatsapp_conversations`, dispatches intent via `whatsapp_intents.dispatch_intent()`.
- **MSG91 DLR** (webhooks.py:338-426): Delivery-report webhook. Verifies HMAC, maps MSG91 status codes to canonical `delivery_status` (DELIVERED/READ/FAILED/SENT), updates `notification_logs` row by `provider_msg_id`/`provider_id`.
- **Razorpay Auto-Reconcile** (webhooks.py:428-524): Lightweight inline hook for UPI payments. On `payment.captured`, matches order by `order_number` (from UPI tn= note), calls `upi_qr.reconcile_upi_payment()`. Fail-soft (never blocks webhook 200).

#### Online Store Collections (Phase 2)
- **CRUD** (online_store_collections.py, Phase 2 in progress): POST/GET/PUT/DELETE for `ecom_collections`. Custom and SMART collection types.
- **Smart Rules** (documented): Rule-based membership (future `ecom_smart_rules.py` evaluator). Can auto-populate by brand, category, attribute.
- **Membership** (documented): Manual product reorder, add/remove SKUs. POST `/{id}/products`, DELETE `/{id}/products/{sku}`, PUT `/{id}/products/reorder`.
- **Resolve** (documented): GET `/{id}/resolved-products` evaluates SMART rules against live catalog, returns matching SKUs (capped at 1000).
- **Dirty Flag**: Writes flip `locally_modified` for Phase-5 push queue.

#### Shopify GraphQL Push (Phase 5 - DARK)
- **Push Status** (online_store_push.py:128-150, stub): GET `/push/status` (ADMIN/SUPERADMIN) reports per-entity pushed vs pending count, current mode (SIMULATED|LIVE).
- **Product Push** (online_store_push.py:129-150, stub): POST `/push/product/{id}` (ADMIN/SUPERADMIN). DARK by default (SIMULATED dry-run plan, no network). LIVE only behind three gates: `IMS_SHOPIFY_WRITES=1` AND `DISPATCH_MODE=live` AND creds present. Writes audit row. Returns `{ok, mode, shopify_id, error}`.
- **Collection Push** (documented): POST `/push/collection/{id}` (stub).
- **Menu Push** (documented): POST `/push/menu/{id}` (stub).
- **Image Push** (documented): POST `/push/image/{id}` (one APPROVED product image → productCreateMedia).

#### Multi-Location Inventory (Partial)
- **Online Catalog Bridge** (online_catalog.py): Reads BVI Postgres (e-commerce app). Queries `ProductVariant` + `VariantLocation` to resolve online stock by SKU. Match keys: SKU, `storeBarcode`, barcode (GTIN).
- **Variant Targets** (online_catalog.py:165-200): Returns `{sku: {inventory_item_id, location_id}}` for online variants. Location resolves to: variant's `Location.shopifyLocationId` OR `SHOPIFY_ONLINE_LOCATION_ID` env (authoritative single online location).
- **Stock Writeback** (online_stock_writeback.py): On in-store sale, recomputes on-hand MINUS safety buffer, pushes ABSOLUTE qty to Shopify location via `inventorySetQuantities`. Safety buffer: `integrations.shopify.config.safety_buffer` > env `ONLINE_STOCK_SAFETY_BUFFER` > default 0.
- **IMS Inventory Master** (implicit): IMS `stock_units` (serialized per physical store) is the source of truth. Online stock is derived (live query from BVI Postgres OR last-pushed Shopify value).

#### Product Publish Controls
- **Ecom Sub-doc** (implicit): Products have `ecom` sub-doc (structure TBD Phase 1 → 2). Online Store module checks `products: _safe_count(db, "catalog_products", {"ecom": {"$exists": True}})`. Presence implies "online-allowed" status.
- **Push Gate** (online_store_push.py:144-148): Before pushing to Shopify, product must have `ecom` sub-doc. Missing → 400 "stage it for the online store first".
- **Collections Membership**: Manual assignment (CUSTOM) or rule-based (SMART). Determines what's discoverable on the storefront.
- **Category/Brand Filtering** (documented): ecom_smart_rules can auto-populate collections by category or brand. Actual implementation Phase 2.

#### Gift Card / Loyalty
- **Loyalty Redemption** (implicit gap): `loyalty.py` router exists (for POS loyalty tracking). No integration documented for online redemption. Gift-card online balance check / redemption: NOT YET IMPLEMENTED.
- **Loyalty Engine** (loyalty_engine.py): POS loyalty accrual/redemption. No Shopify webhook handler for online purchases yet.

#### B2B / Bulk Orders
- **Gap**: No separate B2B channel, bulk-order workflow, or corporate customer type. Online orders are standard retail (channel='ONLINE'). Bulk import exists for catalog (admin_catalog.py) but not for orders.

---

### Collections

| Name | Purpose |
|------|---------|
| `webhook_inbox` | Inbound webhooks from Razorpay/Shopify/Shiprocket/MSG91. Persisted envelope + payload. TTL 30 days. Indexed: `webhook_id`, `vendor`. |
| `whatsapp_conversations` | WhatsApp inbound message threads (CRM-14). Per-phone conversation, last 200 messages. TTL 180 days. Indexed: `phone` (unique). |
| `ecom_collections` | Shopify Custom/Smart Collections (Phase 2). Stores member SKU lists + rule definitions. Dirty flag `locally_modified` for Phase-5 push. |
| `ecom_menus` | Mega-menu structure (Phase 3). Navigation tree. Indexed for push status. |
| `product_images` | Design workflow (Phase 4). Tracked by `design_status` (QUEUED/APPROVED/REJECTED). |
| `orders` | Canonical order ledger (POS + online + ONDC). Filtered by `channel` (POS/ONLINE/ONDC). Online orders ingested by online_order_mapper. |
| `customers` | Customer master. Shopify-bridged rows carry `shopify_customer_id` (non-null). Online orders stamped with resolved `customer_id`. |
| `shipments` | Shiprocket shipment records. Indexed by `shipment_id`, `order_id`. Store-scoped for RBAC. |
| `integrations` | Vendor credentials & config. Docs: `{type: "shopify"/"razorpay"/"shiprocket"/"ondc"/"msg91"/"meta_whatsapp", enabled: bool, config: {...}}`. Read by routers, providers, services. |
| `catalog_variants` | Variant master (color/size). Carries `shopify_variant_id` for mapping. Queried by online_order_mapper to resolve line-item SKUs. |
| `ondc_settlements` | TCS & commission records for ONDC orders. Finance deduction ledger. |
| `audit_logs` | Immutable audit trail. Online order remaps, ONDC callbacks, push attempts all write here. |
| `notification_logs` | Delivery reports. MSG91/WhatsApp DLR updates `delivery_status` + `delivered_at`. |

---

### Key Files

| File | Role |
|------|------|
| `backend/api/routers/online_store.py` | Module shell. GET `/summary` (status + counts + planned features). Phase 1 foundation. |
| `backend/api/routers/online_store_collections.py` | Collection CRUD (Phase 2). Smart rule resolver. Dirty flag for push. |
| `backend/api/routers/online_store_orders.py` | Online order list + remap (recovery). Audit trail. |
| `backend/api/routers/online_store_push.py` | Shopify push surface (Phase 5, DARK). Dry-run gate. Audit integration. |
| `backend/api/routers/webhooks.py` | Inbound receiver pipeline (Razorpay/Shopify/Shiprocket/MSG91/WhatsApp). HMAC verification, inbox persistence, event dispatch. |
| `backend/api/routers/shipping.py` | Shiprocket booking/tracking. Merge address. Store-scoped list. |
| `backend/api/routers/ondc.py` | Beckn protocol callbacks (on_search/select/init/confirm/cancel/status). Signature verify (fail-closed). Audit. |
| `backend/api/services/online_catalog.py` | IMS ↔ BVI Postgres bridge (read-only). Online stock by SKU. Shopify targets (inventory-item + location GIDs). |
| `backend/api/services/online_order_mapper.py` | Shopify order → IMS order mapping. Variant resolution, customer match/create, store bucket resolution, payment/fulfillment status sync. |
| `backend/api/services/ondc_seller.py` | ONDC catalog mapping (HSN/GST). Order ingestion (Beckn → IMS). TCS reconciliation. Gate helpers. |
| `backend/api/services/shiprocket.py` | Shiprocket async client. Auth (token cache). Book + track. Fail-soft, DISPATCH_MODE gated. |
| `backend/api/services/online_stock_writeback.py` | On-sale inventory push to Shopify. Computes available (on_hand - buffer). Calls `shopify_set_inventory_available`. Fire-and-forget async. |
| `backend/agents/nexus_providers.py` | Thin async clients for Shopify/Razorpay/Shiprocket/Tally. Pull orders, set inventory, reconcile payments, track AWBs, export vouchers. DISPATCH_MODE gated (destructive calls). |
| `backend/agents/webhook_verify.py` | HMAC verifiers for Razorpay/Shopify/Shiprocket/MSG91. Pure functions. |
| `frontend/src/pages/online-store/OnlineStorePage.tsx` | Module landing shell (Phase 1). Shows planned sections + live counts. |
| `frontend/src/pages/online-store/CollectionsPage.tsx` | Collection editor (Phase 2 live). |
| `frontend/src/pages/online-store/MenusPage.tsx` | Mega-menu editor (Phase 3 live). |
| `frontend/src/pages/online-store/DesignQueuePage.tsx` | Image design workflow (Phase 4). |
| `frontend/src/pages/online-store/OndcSellerPage.tsx` | ONDC admin surface (status, manual publish). |
| `frontend/src/pages/online-store/OnlineOrdersPage.tsx` | Online order list + remap UI. |

---

### Reusable for Which 52 Features

Without the 52-feature list, mapping is approximate. Based on the codebase:

- **Omnichannel Orders (#1-5?)**: Order ingestion (Shopify/ONDC), customer bridging, channel attribution (ONLINE/ONDC), payment/fulfillment sync, fulfillment routing. **Reuse**: `online_order_mapper.py`, `ondc_seller.ingest_ondc_order()`, `orders` collection schema.
- **Multi-Location / Store**: Online stock read from BVI Postgres per-location. Store bucket resolution (integration config → env → settings → primary → default). **Reuse**: `online_catalog.py`, `online_order_mapper._resolve_online_store_id()`.
- **Inventory Sync**: On-sale push to Shopify (oversell guard), last-known fallback. Safety buffer config. **Reuse**: `online_stock_writeback.py`, `nexus_providers.shopify_set_inventory_available()`.
- **Customer 360 / CRM**: Customer match/create on online order, `shopify_customer_id` bridge, CRM intent dispatch (WhatsApp). **Reuse**: `online_order_mapper` (match/create logic), `webhooks._upsert_conversation()` (conversation threads).
- **Webhook / Integration Hub**: Inbound signature-verified receivers, inbox persistence, event dispatch. **Reuse**: `webhooks._ingest()` pipeline, `webhook_verify` module, `webhook_inbox` collection.
- **Shipping**: Shiprocket book + track, address merge, label generation. **Reuse**: `shipping.py`, `shiprocket.py` client.
- **Catalogs / Collections**: Smart rule evaluation, membership management, push staging (dirty flag). **Reuse**: `online_store_collections.py`, ecom_smart_rules service (Phase 2).
- **Finance / GST**: GST rate master reuse (POS + online). CGST/SGST/IGST split by state-of-supply. TCS on ONDC. **Reuse**: `catalog_products.gst_rate`, `online_order_mapper` (ingest path), `ondc_seller.reconcile_tcs()`.
- **Loyalty / Gift Cards**: Framework for redemption on online (DARK). **Reuse**: `loyalty_engine.py` (POS loyalty accrual), webhook handler pattern (add WABA button for "check loyalty balance").
- **Reporting / Analytics**: Online order book (`channel='ONLINE'`), ONDC metrics (order count, TCS total), sync health. **Reuse**: `online_store_orders` router (order queries), `ondc.status` endpoint, `online_sync_health.py` (audit/health checks).

---

### Genuine Gaps

1. **Gift Card / Loyalty Online Redemption**: `loyalty_engine.py` exists for POS. No integration for online gift-card balance check, redemption, or loyalty point accrual/burn on Shopify. Not yet implemented.

2. **B2B / Bulk Orders**: No separate B2B channel, corporate customer type, or bulk-order discount rules. Online is retail-only (channel='ONLINE'). Catalog bulk-import exists but no order bulk-import or wholesale pricing tiers.

3. **Phase 1 Product "Online-Allowed" Flags**: Ecom sub-doc presence implies online status, but no explicit brand-level or category-level online toggle. Products are all-or-nothing online. No granular product publish controls per brand/category/attribute yet.

4. **Shopify Collections / Menu Push**: Phase 5 (DARK). Stubs exist in `online_store_push.py` and `shopify_push.py` (not read yet) but no actual GraphQL mutation builders or live tests. Collection + menu sync one-way only (IMS → Shopify, no pull).

5. **Return / Refund Sync**: Shopify refunds create no IMS reversal docs / credit notes. ONDC cancellation flows in (on_cancel) but no return merchandise authorization (RMA) bridging. Not yet implemented.

6. **Product Images Design Queue (Phase 4)**: Schema exists (`product_images` collection, `design_status` field). No editor UI or workflow engine. Not yet shipped.

7. **ONDC Quote / Pricing Breakup**: on_select + on_init are stubs (Protocol BVI-20 backlog). No dynamic quote, tax breakup, shipping cost calculation for ONDC orders. Returns hardcoded ACK.

8. **Webhook Signature Verification Gaps**: WhatsApp signature is fail-closed when secret absent (correct). But ONDC signature (BUG-102 tag) was defaulting to valid=False historically; the code now marks `_signature_invalid=True` when key absent (recently fixed, good). Razorpay/Shopify/Shiprocket all correctly fail-soft (missing secret → 200 skipped, not 401).

9. **Multi-Currency / Tax Jurisdiction**: All orders assume INR. GST maps assume India. No US/EU/APAC tax handling. ONDC domain forced to ONDC:RET12 (B2C retail); no B2B or other domain support yet.

10. **Inventory Reconciliation**: Online stock is a read-only query (BVI Postgres). No periodic sync job to detect drift (e.g., Shopify manual edit not reflected in IMS). Manual `online-stock-reconcile` diagnostic exists but no auto-correction.

11. **Customer Data GDPR Compliance**: Shopify customers bridged into IMS `customers` collection with `shopify_customer_id`. No GDPR right-to-be-forgotten (RTBF) or data retention policy enforced. Personal data (phone, email, address) persisted indefinitely.

---

**Conclusion**: The omnichannel foundation is solid (Shopify order ingest, ONDC callbacks, shipping integration, webhook security). Multi-location inventory and customer bridging are partially implemented. Gift cards / loyalty / B2B are genuine gaps. Phases 2–5 (collections, menus, image queue, push) are mostly stubs or DARK-by-default. The 30-day webhook inbox TTL and GDPR compliance should be reviewed.


# === Platform / RBAC / Settings / Agents / Notifications ===
Excellent! Now I have all the key information. Let me compile the comprehensive report.

## AUDIT REPORT: IMS 2.0 Platform Infrastructure

### CAPABILITIES

#### RBAC (Role-Based Access Control)
- **12 Canonical Roles**: SUPERADMIN, ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT, CATALOG_MANAGER, OPTOMETRIST, SALES_CASHIER, SALES_STAFF, CASHIER, WORKSHOP_STAFF, DESIGN_MANAGER
- **Enforcement Layers** (backend/api/services/rbac_policy.py + middleware/rbac_enforcement.py):
  - Per-route `Depends(require_roles(...))` gates (lines 1-82 POLICY registry)
  - Router-level dependencies (`APIRouter(dependencies=[Depends(...)])`) for /admin (line 63, admin.py)
  - Request-time middleware enforcer (rbac_enforcement.py:124) — second defense-in-depth layer
  - INVESTOR read-only block via middleware (api/main.py), not in allow-lists
  - Store-scoped rows flag handlers for `validate_store_access` data filtering (lines 1135-1177 in rbac_policy.py)
- **Helper Functions** (rbac_policy.py:4797-4862):
  - `policy_for(method, path)` → returns matched POLICY entry or None
  - `check_access(method, path, user_roles)` → boolean decision (SUPERADMIN always passes, else role-intersection check)
  - `is_store_scoped()`, `is_self_enforced()` — policy metadata queries
- **Policy Table**: 250+ routes catalogued in POLICY list (lines 118-3000+), mirrored from actual route gates, coverage-locked by test suite

#### Settings & Configuration Storage
- **Multi-Collection Pattern** (backend/api/routers/settings.py):
  - `business_settings` — singleton {"_id": "default"} with company name, colors, logo_url
  - `tax_settings` — GSTIN, GST rates, e-invoice/e-way-bill toggles (lines 250-258)
  - `invoice_settings` — invoice prefix, numbering, terms, QR, digital-sig (lines 261-269)
  - `printer_settings` — receipt/label printer names, QZ Tray config (lines 272-280)
  - `notification_templates` — per-template_id docs for SMS/Email/WhatsApp (lines 283-291)
  - `notification_providers` — channel provider meta + DISPATCH_MODE flag (lines 1175-1206)
  - `user_preferences` — per-user display/notification toggles, keyed by user_id (lines 893-947)
  - `integrations` — type-based with encrypted config dicts (lines 305-324)
- **Encryption at Rest** (lines 30-220):
  - Fernet AES-128-CBC + HMAC-SHA256 (current `fernet:` prefix)
  - Legacy XOR back-compat (`enc:` prefix)
  - Credential key: CREDENTIAL_ENCRYPTION_KEY env or fallback to JWT_SECRET_KEY
  - Sensitive field names auto-detected and encrypted: api_key, secret, password, token, webhook_secret, etc.
  - Masking on read: first 4 + last 2 chars shown (lines 113-117)
- **Per-Store Configurability**: Display-fixtures, placements (store_scoped: True in POLICY) allow store-manager customization; tax/invoice/printer/integrations are global singletons (for now)

#### Notifications & Dispatch
- **In-App Bell** (backend/api/routers/notifications.py):
  - `notifications` collection: user_id-keyed (lines 46-104)
  - Statuses: PENDING, SENT, DELIVERED, READ (line 20)
  - Snooze support (max 3x) with `snoozed_until` datetime (lines 139-186)
  - Unread badge poll (`GET /unread-count`) + bulk mark-read
- **WhatsApp/SMS Push Infra** (settings.py:1354-1383, agents/providers.py):
  - Integrated via `send_whatsapp`, `send_sms` from agents.providers
  - DISPATCH_MODE gating: "off" (simulated), "test" (test phone only), "live" (real send)
  - Test endpoint routes through live provider to report honest status (lines 1361-1383)
  - Quiet hours (21:00–09:00 IST) respected by MEGAPHONE agent (agents/quiet_hours.py)
- **Marketing Campaign Dispatch** (MEGAPHONE agent):
  - `notification_logs` collection queued by MEGAPHONE (megaphone.py:126-139) — WhatsApp/SMS per customer
  - Trigger types: Rx expiry (90/30/7 day windows), birthday, walkout follow-up, NPS, scheduled campaigns
  - Drain batch (60/tick) + state tracking (PENDING → SENT/SIMULATED/FAILED)

#### Approval Workflow & Proposals
- **Change-Proposal System** (backend/agents/proposals.py):
  - `ai_proposals` collection: reversible Tier-1 types auto-execute on Superadmin approval
  - Statuses: PENDING → {APPROVED, REJECTED, EXECUTED, FAILED}
  - Reversible whitelist: draft_po, inter_store_transfer_suggestion, rx_reminder, mark_task (lines 76-81)
  - All others (price_ceiling_change, refund, writeoff, staff_transfer) are ADVISORY: approval audited but human executes
  - Before/after state capture + audit_logs immutable entry (lines 117-145)
  - De-duplication key (same SKU/day draft-PO doesn't stack) (lines 177-187)
- **Approval & Execution Flow**:
  - Superadmin reviews in UI → `approve_proposal()` in proposals.py
  - Reversible types: system auto-executes via registered executor, captures before/after, writes audit
  - Non-reversible: approval is recorded in proposals.status + audit_logs, human does the change separately
- **Three-Tier Safety Model** (TASKMASTER, taskmaster.py:10-16):
  - Tier 1 (auto-act): fully reversible, low-stakes — escalate tasks, send Rx reminders, mark complete (auto-execute)
  - Tier 2 (ask-confirm): medium stakes — create PO, staff transfer, refund (proposal approval required, human executes)
  - Tier 3 (advisory): too risky — price ceiling, write-off (proposal approval recorded, human acts)

#### Agents & Automation Hooks
- **Agent Registry** (backend/agents/registry.py):
  - 8 canonical agents: JARVIS (NLP), CORTEX (orchestrator), SENTINEL (monitor), PIXEL (design/vercel), MEGAPHONE (marketing), ORACLE (analysis), TASKMASTER (executor), NEXUS (integrations)
  - Register via `register_agent()` (line 54) → global AGENT_REGISTRY dict
  - Event subscription: `subscribe_event(event_name, agent_id)` (lines 90-111)
- **Event Bus** (agents/event_bus.py, registry.py:163-241):
  - Redis pub/sub when REDIS_URL configured; in-process fallback
  - Subscriptions: agent.error → CORTEX/SENTINEL; stock.below_reorder → TASKMASTER; anomaly.detected → SENTINEL/TASKMASTER; webhook.received → NEXUS; deploy.{success,failure} → PIXEL
  - `dispatch_event(event, payload, source)` publishes via bus, `on_event` handlers fan-out (lines 114-134)
- **Background Scheduling** (base.py:169-215):
  - Every agent implements `_do_background_work()` (called by APScheduler on agent schedule)
  - Config toggle: agents read `enabled` flag from agent_config collection before tick
  - Error capture: failed tick persisted to `agent_errors` collection + `agent.error` event emitted (lines 217-244)
  - Run stats: last_run, run_count, error_count, last_error tracked
- **Specific Agent Automation**:
  - **TASKMASTER** (5 min): SLA escalation (overdue tasks bumped to next role level), auto-reorder draft (stock < reorder_point)
  - **MEGAPHONE** (30 min): Rx expiry/birthday/walkout queue; scheduled campaign dispatch; drain PENDING notifications respecting DND
  - **ORACLE** (hourly + 22:00 EOD): sales anomaly, discount-abuse, fraud detection; proposals for low-stock reorders (not auto-execute)
  - **NEXUS** (hourly + webhook): Shopify sync, Razorpay reconciliation, Shiprocket tracking, Tally nightly export (23:00), webhook queue drain

#### Audit Logging & Immutability
- **Agent Audit Log** (taskmaster.py:258-288):
  - `agent_audit_log` collection: action, target, before_state, after_state, tier, agent_id, timestamp
  - Called by agents for every state-changing operation (escalation, proposal execution, reorder draft)
  - Immutable: written once, never updated; serves as audit trail for reversals/compliance
- **Proposal Audit** (proposals.py:117-145):
  - `ai_proposals` doc: before_state, payload, status history
  - `audit_logs` entry on execution: proposal_id, executor (agent or user), before/after, reversible flag
- **User Action Audit** (admin_extras.py has audit endpoints):
  - `audit_logs` collection: user_id, action (CREATE/UPDATE/DELETE/LOGIN/etc.), entity_type, entity_id, changes, timestamp, ip_address
  - Integration-specific: Tally export logs (balanced/unbalanced flag, voucher_count), Shopify drift detector

---

### COLLECTIONS

| Collection | Purpose | Key Shape | Write Pattern |
|---|---|---|---|
| `business_settings` | Company branding, contact info | {"_id": "default", "company_name", "logo_url", ...} | upsert |
| `tax_settings` | GST, e-invoice, e-way-bill config | {"_id": "default", "gst_enabled", "company_gstin", ...} | upsert |
| `invoice_settings` | Invoice numbering, terms, warranty | {"_id": "default", "invoice_prefix", "current_invoice_number", ...} | upsert |
| `printer_settings` | Receipt/label printer names, QZ Tray | {"_id": "default", "receipt_printer_name", "label_size", ...} | upsert |
| `user_preferences` | Display theme, notification toggles per user | {"_id": user_id, "notifications_enabled", "email_notifications", ...} | upsert by user_id |
| `notification_templates` | SMS/Email/WhatsApp templates | {"template_id": "RX_EXPIRY_90", "trigger_event", "content", ...} | upsert by template_id |
| `notification_providers` | Channel provider meta (MSG91, SMTP, etc.) | {"_id": "notification_providers", "whatsapp": {...}, "sms": {...}, ...} | upsert |
| `notifications` | In-app bell per-user | {"notification_id", "user_id", "status", "snoozed_until", "created_at", ...} | insert (immutable after read/snooze) |
| `notification_logs` | Outbound WhatsApp/SMS audit | {"notification_id", "customer_id", "channel", "status", "sent_at", ...} | insert by MEGAPHONE |
| `integrations` | Shopify, Razorpay, Shiprocket, etc. | {"type": "shopify", "enabled", "config": {...encrypted...}} | upsert by type |
| `ai_proposals` | Change suggestions for Superadmin review | {"proposal_id", "created_by_agent", "type", "status", "before_state", ...} | insert, update on review |
| `agent_audit_log` | Per-agent state changes | {"action", "target", "before_state", "after_state", "tier", "agent_id", "timestamp"} | insert |
| `agent_errors` | Failed agent ticks | {"agent_id", "error", "traceback", "elapsed_ms", "timestamp"} | insert |
| `agent_config` | Agent ON/OFF toggles + metadata | {"agent_id": "megaphone", "enabled", "schedule", "last_run", ...} | upsert by agent_id |
| `tally_exports` | Nightly voucher XML per (date, store_id) | {"export_date", "store_id", "xml", "balanced", "voucher_count", ...} | insert/upsert |
| `audit_logs` | User/system action audit trail | {"user_id", "action", "entity_type", "entity_id", "changes", "timestamp"} | insert |

---

### KEY FILES

| File | Purpose | Critical Functions/Classes |
|---|---|---|
| `backend/api/services/rbac_policy.py` | Declarative RBAC policy registry + enforcer logic | `POLICY` list (250+ routes), `check_access(method, path, roles)`, `policy_for(method, path)`, `is_store_scoped()` |
| `backend/api/middleware/rbac_enforcement.py` | Request-time RBAC middleware (defense-in-depth) | `rbac_enforcement_middleware()`, `_roles_from_bearer()`, role extraction from JWT + policy check |
| `backend/api/routers/settings.py` | All settings endpoints + credential encryption | Credential encryption (`_encrypt_value`, `_decrypt_value`), 10+ settings GET/PUT endpoints, notification providers, templates |
| `backend/api/routers/admin.py` | Integration config endpoints (Shopify, Razorpay, Shiprocket, etc.) | `_require_admin_role` router-level gate, integration save/test (simulated), Tally export list/download/regenerate |
| `backend/api/routers/notifications.py` | In-app bell API | List notifications (unread filter, snooze filter), mark-read, snooze (max 3x), unread badge |
| `backend/agents/registry.py` | Agent lifecycle + event dispatch | `register_agent()`, `get_agent()`, `subscribe_event()`, `dispatch_event()`, `initialize_registry()` with per-agent try/catch |
| `backend/agents/base.py` | JarvisAgent base class | `JarvisAgent` ABC, `background_tick()`, `_audit_log()`, `health_check()`, `on_event()`, `emit_event()` |
| `backend/agents/implementations/taskmaster.py` | Tier-1 auto-execution: escalation + reorder draft | `_escalate_overdue_tasks()`, `_draft_reorders()`, `_audit_log()` with before/after state |
| `backend/agents/implementations/megaphone.py` | Marketing automation + DND compliance | `_scan_rx_expiring()`, `_scan_birthdays_today()`, `_dispatch_scheduled_campaigns()`, `_drain_pending()`, quiet hours |
| `backend/agents/implementations/oracle.py` | AI analysis: anomalies + proposals | `_detect_sales_anomalies()`, `_detect_discount_abuse()`, `_propose_reorders()` (reversible tier-1) |
| `backend/agents/implementations/nexus.py` | Integration orchestration | `_run_integration_sync()` (Shopify, Razorpay, Shiprocket, Tally), `_build_tally_export()`, sync_runs audit |
| `backend/agents/proposals.py` | Change-proposal workflow + reversible execution | `ProposalStatus` enum, `is_reversible()`, `ProposalStore.create/approve/reject()`, executor dispatch |
| `backend/agents/quiet_hours.py` | Shared 21:00–09:00 IST DND window | `in_quiet_hours()`, `now_ist()`, `next_quiet_end()` — used by MEGAPHONE, task-escalation WhatsApp, manual send API |

---

### REUSABLE FOR FEATURES

**#1: Multi-Store / Brand Scoping**
- Reuse: RBAC `store_scoped` flag (line 1135 in policy.py) + `validate_store_access` handler pattern
- Existing: display-fixtures, placements already store-scoped; extend pattern to other modules

**#2–4: Org Hierarchy (Store → Area → Region)**
- Reuse: Task escalation role-ladder resolver (taskmaster.py:152–155, `resolve_escalation_target`)
- Framework exists for Store Manager → Area Manager → Admin → Superadmin; extend for regions

**#5: Bulk Pricing Updates**
- Reuse: Proposal system for advisory tier-2 (price ceiling changes stay advisory per TASKMASTER tier 3)
- Audit: immutable before/after in audit_logs already captures price changes

**#6–8: Staff Hierarchy & Shifts**
- Reuse: RBAC roles matrix (all 12 roles defined); extend with shift/roster fields in user doc
- Staff transfer is tier-2 (requires approval) — proposal framework handles it

**#9: Discount Rules & Abuse Detection**
- Reuse: ORACLE's `_detect_discount_abuse()` (oracle.py:249+); audit captured in agent_audit_log
- Framework: caps stored in code constants (role_caps.py, pricing_caps.py); extend to db-driven rules

**#10–11: Customer 360 & Churn**
- Reuse: Customer preference toggles in `user_preferences` (email_notifications, sms_notifications)
- Audit: interactions collection already logged; extend with churn-score housekeeping

**#12–13: Eye Tests & Prescriptions**
- Reuse: Clinical device import endpoint (rbac_policy.py:966–973); OPTOMETRIST role exists
- Audit: test completion logged; extend prescription-edit to audit trail

**#14–15: Loyalty & Referrals**
- Reuse: Customer interaction logs (crm.py); Rx reminder templates (notification_templates)
- Dispatch: MEGAPHONE agent queues customer communications (rx_reminder type in reversible_types)

**#16: Returns & Refunds**
- Reuse: Approval proposal tier-2 (refund_issue in requires_confirmation list, taskmaster.py:49)
- Audit: before/after captured; implement executor in proposals.py dispatch

**#17: Damage & Warranty**
- Reuse: invoice_settings.default_warranty_days (line 694); extend to per-product warranty in catalog

**#18–19: Expense Reports & Approvals**
- Reuse: Expense approval flow (already implemented in expenses router); ACCOUNTANT role gates it
- Audit: existing approval endpoints + TASKMASTER expense_anomaly_action (taskmaster.py:61)

**#20–22: PO, Receipts, Invoices**
- Reuse: Proposal system for draft_po (oracle.py:178–204, auto-executes on approval)
- Audit: tally_exports collection + balance validation (nexus.py:223–288); invoice_settings singeton
- Printer settings: qz_enabled for silent raw print (line 740); integrate with job-card stickers (line 741)

**#23: GST & Compliance**
- Reuse: TaxSettings model (lines 657–680); e-invoice_enabled, e-way-bill_enabled flags
- Integrations: einvoice config catalog already in _INTEGRATION_CATALOG (settings.py:517–531)

**#24–25: Accounting & Reconciliation**
- Reuse: ACCOUNTANT role gating (rbac_policy.py:1265–1283 for approval); NEXUS Tally export (23:00 daily)
- Audit: sync_run heartbeat per integration (nexus.py:171–180); balanced/unbalanced flag

**#26–27: Cash & Register**
- Reuse: Finance router (rbac_policy.py:1338–1385) already has cash_register open/close + session list
- Audit: captured in orders/transactions; extend to cash-register audit trail

**#28: Inventory & Reorder**
- Reuse: Auto-reorder draft proposal (oracle.py:139–204, tier-1 reversible)
- TASKMASTER escalates low-stock → ORACLE enqueues draft → Superadmin approves → auto-executes
- Audit: before_state on_hand/reorder_point captured

**#29: Shopify Sync**
- Reuse: NEXUS agent (nexus.py:141–156) shopify_pull_orders + catalog push via events
- Admin endpoints already gated to SUPERADMIN/ADMIN; BVI safety nets (drift, repush, parity) SUPERADMIN-only
- Tally nightly 23:00 sync already wired

**#30: Vendor Portal**
- Reuse: Public AUTH routes (rbac_policy.py:726) + tokenized customer-portal link pattern
- Extend vendor PO view with `vendor_id` scoping (existing: store_scoped pattern)

**#31: Online Store**
- Reuse: Shopify integration + BVI safety nets (drift detector, repush oversell, parity oracle)
- Admin endpoints: /online-store/sync-health (SUPERADMIN), /drift, /repush-oversell, /parity (admin.py:837–932)
- DESIGN_MANAGER role added to RBAC (rbac_policy.py:105)

**#32: Marketing Campaigns**
- Reuse: MEGAPHONE agent (megaphone.py:187–220) _dispatch_scheduled_campaigns() for ONE_TIME SCHEDULED
- Campaign collection: send_at <= now → dispatches via existing send_notification path
- Audit: notification_logs with sent_at timestamp

**#33: Quiet Hours**
- Reuse: agents/quiet_hours.py (21:00–09:00 IST) already shared by MEGAPHONE, task-escalation, manual send API
- Extend to all customer-facing channels (WhatsApp test endpoint already uses dispatch_mode)

**#34–36: SMS/Whatsapp/Email**
- Reuse: Notification providers config (notification_providers collection), _INTEGRATION_CATALOG (shopify/tally/anthropic/slack/etc.)
- DISPATCH_MODE: off/test/live gating (settings.py:1361) with honest SIMULATED/SENT/FAILED reporting
- Template system: notification_templates collection (per template_id, per trigger_event)

**#37: In-App Notifications**
- Reuse: notifications collection + bell API (notifications.py:46–186)
- Snooze support (max 3x), unread badge poll, mark-all-read
- Extend: task escalation already queues in-app notifications (taskmaster.py:193–195 notify_escalation)

**#38: Audit Trail**
- Reuse: audit_logs collection (user action) + agent_audit_log (agent action)
- Before/after state captured; immutable entries; ip_address tracking
- SUPERADMIN only: GET /admin/system/audit-logs (rbac_policy.py:550–552)

**#39: Activity Feed**
- Reuse: agent_events collection (persisted by EventBus on dispatch); registry.py:172 binds db to bus
- Extend: timeline view of agent-generated proposals + approvals

**#40: User Roles & Permissions**
- Reuse: 12 canonical roles defined; store_ids per user; active_store_id
- Mechanism: jwt.roles claim → middleware check → handler gate → data-level (store-scope, ownership)

**#41: Settings UI**
- Reuse: All 10+ settings endpoints (business, tax, invoice, printer, notifications, integrations)
- Credential masking on read (first 4 + last 2 chars); encryption at rest (Fernet)
- Per-user preferences in user_preferences collection

**#42: Integration Configuration**
- Reuse: _INTEGRATION_CATALOG in settings.py (shopify/tally/shiprocket/razorpay/whatsapp/anthropic/storage/einvoice/google_ads/meta_ads/meta_whatsapp/slack)
- Generic config UI: field metadata (key, label, secret flag, placeholder, help, optional)
- Encrypted at rest + masked on read

**#43–45: Vercel/CI-CD**
- Reuse: PIXEL agent listens for deploy.{success, failure} events (registry.py:213–214)
- Extend: existing deploy-event subscription pattern to trigger additional CI checks

**#46–52: AI / ORACLE / Anomalies**
- Reuse: ORACLE agent (oracle.py) — sales anomaly, discount abuse, fraud detection, demand forecast, churn, eod-sweep
- Proposal system: low-stock reorder enqueued as tier-1 reversible draft-po (oracle.py:178–204)
- Narrative enrichment: Claude call for anomaly explanation (oracle.py:110–119)
- Approval: Superadmin reviews + auto-executes reversible types

---

### GENUINE GAPS

1. **No per-brand or per-store settings matrix yet**
   - business_settings, tax_settings, integrations are global singletons (all stores share)
   - Should support: per-brand GST rate, per-store printer config, per-store integration toggle
   - Reuse pattern: extend with {brand_id, store_id} scoping (display-fixtures already has this)

2. **Staff Role Assignment / Shift Scheduling**
   - RBAC roles exist (all 12 defined) but no shift/availability/leave calendar
   - No staff-to-store mapping beyond user.store_ids
   - Gap: Need to build schedule/shift collection + shift-aware task assignment

3. **Discount Ceiling Enforcement**
   - Code constants (role_caps.py, pricing_caps.py) are NOT runtime-configurable
   - Settings UI has deprecated discount-rules write (line 1479, no consumer)
   - Gap: Need db-driven cap matrix + admin endpoint to tweak caps live

4. **Purchase Order Lifecycle Beyond Draft**
   - Proposal system only auto-executes draft_po (reversible tier-1)
   - Sending to vendor is explicitly non-reversible (tier 3, manual)
   - Gap: Need PO state machine (DRAFT → SENT → ACKNOWLEDGED → RECEIVED → INVOICED) + vendor portal view

5. **Warehouse / Multi-Location Inventory**
   - Only store-level stock units exist
   - No bin/location tracking or bin-to-customer fulfillment routing
   - Gap: Need warehouse structure + pick/pack/ship state machine

6. **Refund Reversals & Credit Memos**
   - Refund is tier-2 (approval required, human acts); no executor wired
   - No credit memo issuance flow or reversal audit
   - Gap: Implement refund executor in proposals.py + credit memo issuance

7. **Subscription Renewal Automation**
   - No subscription collection or renewal-trigger event
   - Contact lens renewal reminder exists (crm.py: /cl-refill-status) but no auto-renewal
   - Gap: Model subscriptions + MEGAPHONE renewal-trigger handler

8. **Non-Reversible Approval Workflow (Tier-2/3)**
   - Approving a tier-2 (ask-confirm) or tier-3 (advisory) proposal just records status
   - No enforcement that a human actually performed the change (e.g., issued refund)
   - Gap: Task-creation hook to track "approval → action completion" handoff

9. **Audit Completeness for All Writes**
   - Only agent writes + proposals are fully audited (before/after captured)
   - User writes (manual POS, order creation) logged in audit_logs but not all handlers call it
   - Gap: Middleware or repo pattern to auto-audit all domain writes

10. **Dead-Letter Queue for Failed Integrations**
    - NEXUS emits sync.failed event but no retry queue or dead-letter handling
    - sync_run row has error but no automatic re-queue logic
    - Gap: Implement retry policy (exponential backoff, dead-letter after N failures)

11. **Approval PIN Override** (mentioned in brief)
    - Quiet hours can be overridden with PIN (not implemented)
    - No PIN validation flow or override audit
    - Gap: Add PIN field to agent_config + override endpoint

---

**END REPORT**
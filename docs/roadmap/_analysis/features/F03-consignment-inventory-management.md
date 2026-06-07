# Feature #3: Consignment Inventory Management
META: effort=L days=12 risk=MED roi=4 quickwin=no deps=none phase=3

## Existing overlap
- `stock_units` collection (backend/database/connection.py:238-243) already tracks status (AVAILABLE/RESERVED/TRANSFERRED/SOLD), source_type, source_id, and unit_cost — consignment units are a new source_type on this same schema.
- `vendors.py` already has vendor master (GSTIN, credit_days, preferred_vendor_id) — consignment agreements map to existing vendor docs.
- `purchase_orders` + GRN flow (vendors.py:989-1509) handles goods receipt and stock minting — consignment receipt is a variant of GRN with deferred payment, not a rebuild.
- `stock_audit` collection and `_audit_stock_move()` (transfers.py:272-297) provide the immutable movement trail.
- `purchase_invoices.py` already handles AP booking with 3-way match; the auto-consumption micro-PO will reuse this path.
- `finance.py` AP engine (`ap_engine.py`) handles vendor ledger, aging, and due-date computation — consignment settlement slots into this as a PARTIAL bill.
- `orders.py` already stamps `cost_at_sale` on each line item at POS sale — this is the exact trigger point for consignment consumption.
- TASKMASTER agent tier-2 proposal pattern (proposals.py) can drive the micro-PO auto-creation approval flow.

## Reuse (extend, don't rebuild)
- **`stock_units` collection**: add `source_type='CONSIGNMENT'`, `consignment_id` (ref to new collection), `consignment_status` (ON_HAND / SOLD / RETURNED_TO_VENDOR), `consignment_unit_cost` (agreed price, encrypted at rest not needed — it is a commercial rate, not a credential).
- **`backend/api/routers/vendors.py`**: extend with `/consignment-agreements` sub-resource (CRUD) and `/grn/{grn_id}/accept` to handle a `receipt_type='CONSIGNMENT'` flag that mints units with `source_type='CONSIGNMENT'` and skips AP booking.
- **`backend/api/routers/purchase_invoices.py`**: extend to accept `doc_type='CONSIGNMENT_CONSUMPTION'` — the auto-consumption invoice that books AP only for units sold.
- **`backend/api/services/purchase_match.py`**: extend `three_way_match()` to accept consignment mode where PO qty = 0 (no upfront PO) and match is against the micro-PO generated at sale.
- **`backend/api/routers/orders.py`**: extend `create_order()` (line ~1027) — after stamping `cost_at_sale`, emit a `consignment.unit_sold` internal event when the sold `stock_unit.source_type == 'CONSIGNMENT'`.
- **`backend/agents/registry.py` / TASKMASTER**: subscribe `consignment.unit_sold` → TASKMASTER tier-1 handler that accumulates sold consignment units per vendor and proposes micro-PO at settlement frequency.
- **`backend/api/routers/inventory.py`**: extend `barcode_lifecycle_trace` (line 532) to surface consignment status on the unit trace.
- **`frontend/src/pages/purchase/`**: extend existing `GoodsReceiptNote.tsx` and `PurchaseInvoicesTab.tsx` rather than building new pages.
- **`backend/api/services/ap_engine.py`**: reuse `compute_due_date()` for consignment settlement due date (agreement credit_days or settlement_cycle_days, whichever applies).

## Data model
- **New collection `consignment_agreements`**: agreement_id (unique), vendor_id, store_id (which store holds the stock), brand (free-text, matches product.brand), agreement_date, settlement_cycle (WEEKLY / FORTNIGHTLY / MONTHLY), max_units_on_consignment, agreed_cost_per_unit (per product_id or category), return_window_days (how long unsold units can stay before mandatory return), status (ACTIVE / SUSPENDED / CLOSED), notes, created_by, created_at, updated_at.
- **New collection `consignment_returns`**: return_id, agreement_id, vendor_id, store_id, items [{stock_id, product_id, unit_cost, condition (GOOD/DAMAGED)}], return_date, courier_details, status (PENDING / DISPATCHED / CONFIRMED_BY_VENDOR), created_by, created_at.
- **New collection `consignment_micro_pos`**: micro_po_id, agreement_id, vendor_id, store_id, period_start, period_end, items [{stock_id, order_id, product_id, sold_at, unit_cost}], total_value, status (DRAFT / SUBMITTED / INVOICED), linked_invoice_id, created_at.
- **`stock_units` (extend existing)**: add fields `consignment_id` (ref to agreement), `consignment_status` (ON_HAND / SOLD / RETURNED), `consignment_unit_cost`.
- **`products` (extend existing)**: add boolean `is_consignment_eligible` — controls which SKUs can be received on consignment (catalog manager sets this per SKU).

## Backend
- `POST /api/v1/vendors/consignment-agreements` — create agreement (ADMIN/AREA_MANAGER/STORE_MANAGER). Validates vendor exists, brand is valid, settlement_cycle is in enum. Returns agreement_id.
- `GET /api/v1/vendors/consignment-agreements` — list (store-scoped for STORE_MANAGER, org-wide for ADMIN/SUPERADMIN). Filter by vendor_id, status, store_id.
- `PATCH /api/v1/vendors/consignment-agreements/{id}` — update status/terms (ADMIN only — prevents store managers from self-authorizing agreement changes).
- `POST /api/v1/vendors/grn` (extend existing) — accept `receipt_type='CONSIGNMENT'` query param. When set: mints `stock_units` with `source_type='CONSIGNMENT'`, links `consignment_id`, sets `consignment_unit_cost` from agreement, skips AP invoice booking, writes stock_audit row. Does NOT create a purchase_order row.
- `GET /api/v1/vendors/consignment-agreements/{id}/ledger` — per-agreement ledger: units received, units sold (with order_id + sold_at), units returned, unsettled value, next settlement date. Derived from stock_units aggregation.
- `POST /api/v1/vendors/consignment-agreements/{id}/close-period` (ADMIN/ACCOUNTANT) — manually trigger micro-PO generation for the period; normally TASKMASTER does this automatically.
- Internal event handler in TASKMASTER `_handle_consignment_sold(event)` — accumulates units sold since last settlement, checks if settlement_cycle boundary crossed, creates `consignment_micro_pos` DRAFT doc, emits tier-2 proposal (`type='consignment_micro_po'`, reversible=False — accountant must book the invoice).
- `POST /api/v1/vendors/consignment-micro-pos/{id}/submit` (ACCOUNTANT) — marks micro-PO as SUBMITTED, books a `vendor_bills` doc via existing `purchase_invoices.py` path with `doc_type='CONSIGNMENT_CONSUMPTION'`. This creates the AP payable entry.
- `POST /api/v1/vendors/consignment-returns` — initiate return of unsold units to vendor (ADMIN/STORE_MANAGER). Validates units are in `consignment_status=ON_HAND`, sets `consignment_status=RETURNED`, creates `consignment_returns` doc, updates stock_unit status to TRANSFERRED (then SCRAPPED after vendor confirms — matching existing damage/scrap flow).
- `PATCH /api/v1/vendors/consignment-returns/{id}/confirm` — vendor confirms receipt of returned units (ADMIN/ACCOUNTANT). Finalises unit status.

## Frontend
- **Extend `frontend/src/pages/purchase/GoodsReceiptNote.tsx`**: add a `receipt_type` toggle (Standard | Consignment) at the top of the form. When Consignment is selected, show agreement picker (fetches active agreements for the vendor), hide the "vendor invoice" fields (no invoice at receipt), show `consignment_unit_cost` per line pre-filled from agreement. Neutral toggle — no colour coding, just a labelled segmented control.
- **New tab in `frontend/src/pages/purchase/`**: `ConsignmentTab.tsx` — shows agreements table (vendor, brand, store, settlement cycle, units on-hand, unsettled value, next settlement date) + per-agreement drill-down (ledger: received / sold / returned rows). Restrained table-only layout — no charts; a single accent colour on the "Unsettled" amount column when overdue.
- **Extend `frontend/src/pages/purchase/PurchaseInvoicesTab.tsx`**: add a "Consignment" filter chip on the invoice list. Micro-PO backed invoices show a "CONSIGNMENT" badge (grey, not coloured) and a read-only "Consumption period" field.
- **Extend `frontend/src/pages/inventory/InventoryPage.tsx`**: consignment units visible in the stock ledger with a "C" indicator tag on the row. Filter: "Show consignment only" toggle.
- **Extend `frontend/src/pages/inventory/StockAudit.tsx`**: cycle count surfaces consignment units in the variance grid with consignment_status shown.
- **SUPERADMIN Jarvis proposal card** (already in `JarvisPage.tsx`): micro-PO proposals from TASKMASTER surface in the existing proposals review panel — no new UI needed.

## Business rules
- A consignment unit can only be received against an ACTIVE agreement for that vendor + store combination. No agreement = GRN rejected at backend with 422.
- `is_consignment_eligible=True` must be set on the product before it can be received on consignment. Prevents inadvertent tagging of regular stock.
- At POS sale: if `stock_unit.source_type == 'CONSIGNMENT'` and `consignment_status == ON_HAND`, the sale proceeds normally (no POS change visible to cashier). Backend atomically flips `consignment_status = SOLD` and emits `consignment.unit_sold` event. The `cost_at_sale` already captured on the order line item is the consignment unit cost — used directly in P&L (no estimated-COGS flag needed for consignment units).
- Micro-PO value = sum of `consignment_unit_cost` for all SOLD units in the settlement period. No markup, no GST at this stage — vendor invoice (the submitted AP bill) carries GST per the standard purchase invoice engine.
- Units unsold beyond `return_window_days` surface in the consignment ledger as "Overdue for return" — a TASKMASTER tier-1 task is created for the store manager.
- Damaged consignment units: follow the standard damage flow (status → DAMAGED in stock_units) but also set `consignment_status = RETURNED` and create a `consignment_returns` doc with condition=DAMAGED. Financial liability for damaged units is a vendor negotiation (owner decision Q1 below).
- Period lock (finance.py:446-481) applies: micro-PO cannot be submitted for a locked accounting period.
- Audit: every consignment status change writes to `stock_audit` (source='CONSIGNMENT') and `audit_logs`. Immutable.

## RBAC
- **SUPERADMIN / ADMIN**: full access — create/update/close agreements, approve micro-PO proposals, book AP invoices, confirm vendor returns.
- **AREA_MANAGER**: create agreements for their stores; view ledger for their stores; initiate returns.
- **STORE_MANAGER**: view agreements and ledger for own store; receive consignment GRN; initiate returns; cannot create or modify agreement terms.
- **ACCOUNTANT**: submit micro-POs to AP (booking the invoice); view full AP ledger including consignment bills; cannot create agreements.
- **CATALOG_MANAGER**: set `is_consignment_eligible` flag on products.
- **SALES_CASHIER / SALES_STAFF**: no visibility into consignment mechanics — POS is unchanged for them; units behave as normal AVAILABLE stock.
- **All other roles**: no access.

## Integrations
- **TASKMASTER agent**: subscribe `consignment.unit_sold` → accumulate sold units → propose micro-PO at settlement cycle boundary (tier-2 ask-confirm, human accountant books the AP).
- **Tally JV (NEXUS)**: micro-PO AP booking flows through the existing `vendor_bills` → Tally nightly export path (nexus.py:223-288). No new Tally mapping needed — `doc_type='CONSIGNMENT_CONSUMPTION'` maps to the same Tally Purchases ledger head as a standard purchase bill. Consignment receipt (no AP at GRN time) generates no Tally entry — correct, since liability only arises at sale.
- **MSG91 (MEGAPHONE)**: optional — TASKMASTER can dispatch a WhatsApp notification to ACCOUNTANT when a micro-PO is ready for submission (reuses existing `notify_escalation` pattern). DISPATCH_MODE gated.
- **Shopify / Razorpay / ONDC**: none — consignment is an internal inventory accounting feature; no channel impact.

## Risk notes
- **POS revenue-critical path**: the only POS-touching change is in `create_order()` — a conditional block that fires after the order is committed (read `source_type` from the already-fetched stock_unit, emit event). Zero change to pricing, payment, or GST logic. Ship behind feature flag `CONSIGNMENT_ENABLED=1` (env var, default off) — the event emission and stock_unit update are inside `if settings.get("consignment_enabled")`.
- **Accounting risk**: if TASKMASTER fails to emit `consignment.unit_sold` (e.g., Redis down, agent off), units can be SOLD without a micro-PO being created. Mitigation: a nightly reconciliation query in ORACLE — `stock_units` where `source_type=CONSIGNMENT AND consignment_status=SOLD` joined against `consignment_micro_pos.items` — surfaces any SOLD units not yet in a micro-PO. Flag as ORACLE anomaly, not silent gap.
- **Vendor return liability**: if a unit is sold, consignment_status flips to SOLD — the return flow must reject attempts to return already-sold units. Guard: `consignment_returns` endpoint checks `consignment_status == ON_HAND` before accepting any unit.
- **Duplicate GRN prevention**: existing GRN idempotency guard (skip re-minting if units for grn_id + product_id already exist) applies to consignment GRNs too — safe.
- **GST on consignment**: in Indian GST law, title does not pass at consignment receipt — no GST liability arises until sale. The micro-PO → AP invoice path (booked AFTER sale) correctly triggers GST only at that point. However, if the vendor issues a Tax Invoice at delivery (some vendors do), that changes the liability timing. This is Q3 below.

## Recommendation
Build later — not a quick win. The core inventory, POS, and AP engines are solid foundations to build on, but the TASKMASTER event subscription + micro-PO lifecycle + Tally mapping interaction requires careful end-to-end testing before touching a revenue-critical flow. Prioritise after the P1-A returns fix (in-flight branch `claude/fix-money-integrity`) is merged and stabilised. Deliver in a dedicated sprint behind the `CONSIGNMENT_ENABLED` feature flag.

## Owner decisions
- Q: When a consignment unit is damaged in-store, who bears the cost — the store (your business) or the vendor (deducted from settlement)? | Why: If store bears it, no AP deduction is created and the unit is scrapped at your cost; if vendor bears it, the micro-PO reduces by that unit's cost and a damage note is sent to the vendor, changing the AP booking logic. | Options: (a) Store absorbs all damage — simplest, no vendor negotiation in the system / (b) Vendor absorbs damage — system auto-deducts from micro-PO, generates a damage report for vendor acknowledgment / (c) Negotiate case-by-case — system flags damaged units for manual resolution, accountant adjusts micro-PO before submission.
- Q: What is the settlement cycle for your luxury consignment vendors (Cartier, Chopard, Bvlgari, etc.) — weekly, fortnightly, or monthly? | Why: This sets the `settlement_cycle` default on new agreements and determines how frequently TASKMASTER generates micro-POs; monthly is least admin overhead, weekly gives vendors faster cash visibility. | Options: (a) Monthly — one payment run per month, matches your payroll/expense cycle / (b) Fortnightly — balances vendor relationship with your cash flow / (c) Per-vendor — configured individually in each agreement (most flexible, most setup work).
- Q: Should the system enforce a hard cap on total consignment stock value per store (e.g., max ₹5 lakh of un-billed frames on-hand at any time)? | Why: A cap prevents over-stocking high-value frames without upfront commitment; the system can block new consignment GRNs once the cap is hit, or just alert the store manager. | Options: (a) Hard block — new consignment GRN rejected if cap exceeded / (b) Soft alert — TASKMASTER notifies store manager and ADMIN but GRN is not blocked / (c) No cap — trust the vendor agreement's `max_units_on_consignment` field as the only limit.
- Q: Do any of your current luxury vendors issue a GST Tax Invoice at the time of physical delivery (rather than at point of sale)? | Why: If yes, your ITC clock starts at delivery, but AP liability only crystallises at sale — creating a timing mismatch that needs a separate ITC-tracking entry at GRN time. If no, the current design (AP only at micro-PO submission) is fully correct. | Options: (a) No — vendors bill only after we report sales; current design is correct / (b) Yes, some do — need a "provisional ITC" entry at GRN and reconciliation at micro-PO time / (c) Unsure — flag for your CA to confirm before go-live.
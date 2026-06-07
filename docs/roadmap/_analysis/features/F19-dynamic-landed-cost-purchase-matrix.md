# Feature #19: Dynamic Landed Cost Purchase Matrix
META: effort=L days=8 risk=MED roi=4 quickwin=no deps=none phase=3

## Existing overlap
IMS already has significant purchase infrastructure to build on:

- `backend/api/routers/purchase_invoices.py` — first-class AP invoice with line-level `unit_price`, `gst_rate`, `taxable`, `cgst`, `sgst`, `igst` fields. The 3-way match engine (`backend/api/services/purchase_match.py`) already compares PO vs GRN vs invoice price.
- `backend/api/services/purchase_invoice_engine.py` — `moving_average_cost()` and `valuation_trueup_for_invoice()` perform AVCO cost updates on `products.cost_price` after each invoice booking. This is the exact hook where landed cost must flow in.
- `backend/api/routers/vendors.py` — GRN acceptance (`POST /grn/{grn_id}/accept`) mints `stock_units` with `unit_cost` from the PO line and a `cost_source='GRN'` stamp. This provisional cost gets trued-up at invoice time — landed cost allocation must participate in the same true-up.
- `backend/database/repositories/order_repository.py` — Orders freeze `cost_at_sale` per line at sale time. The landed cost update must land before sale for margin to be accurate; it cannot retroactively fix already-sold stock.
- The identified genuine gap from the audit ("Landed Cost Components Not Captured — no freight, taxes, duties, handling fields") is precisely what this feature fills.
- `frontend/src/pages/purchase/PurchaseInvoicesTab.tsx` — existing invoice booking UI prefilled from GRN; this is the page to extend with the landed cost matrix panel.

## Reuse (extend, don't rebuild)
- `backend/api/routers/purchase_invoices.py` — extend `PurchaseInvoiceCreate` schema and `POST /api/v1/vendors/purchase-invoices` to accept a `landed_cost_components` array; extend the booking handler to call the new allocator before the existing `valuation_trueup_for_invoice()`.
- `backend/api/services/purchase_invoice_engine.py` — add `allocate_landed_costs(lines, landed_cost_components, method)` pure function here alongside the existing `moving_average_cost()` and `valuation_trueup_for_invoice()`. Keeps all cost math in one tested service.
- `backend/api/services/purchase_match.py` — extend 3-way match to include landed cost total in the "total invoice value" comparison so the match verdict accounts for freight/duty.
- `vendor_bills` collection (MongoDB) — add `landed_cost_components` array and `landed_cost_total` field to the existing invoice document rather than a separate collection.
- `backend/api/routers/purchase_invoices.py:GET /from-grn/{grn_id}` — extend GRN prefill to return empty `landed_cost_components` scaffold with mandatory zero-entry prompts for the UI.
- `frontend/src/pages/purchase/PurchaseInvoicesTab.tsx` — extend with a `LandedCostMatrix` sub-panel rendered between the line-items table and the totals summary.
- `audit_logs` collection — reuse existing write pattern for cost-change events (already used by `valuation_trueup_for_invoice`).

## Data model
New fields on existing `vendor_bills` collection (no new collection needed):

```
vendor_bills.landed_cost_components: [
  {
    component_type: enum("FREIGHT", "IMPORT_DUTY", "CUSTOMS", "FOREX_ADJUSTMENT", "INSURANCE", "OTHER"),
    description: str (max 120 chars),
    amount_inr: Decimal128,       // always stored in INR; user enters foreign amount + rate if FOREX
    foreign_currency: str | null, // e.g. "USD", "EUR" — null for domestic charges
    forex_rate: Decimal128 | null, // INR per 1 unit of foreign_currency
    vendor_ref: str | null        // customs challan number, freight AWB, etc.
  }
]
vendor_bills.landed_cost_total: Decimal128   // sum of all component amount_inr
vendor_bills.landed_cost_allocation_method: enum("VALUE", "WEIGHT", "QUANTITY")
vendor_bills.landed_cost_allocated: bool     // True once allocation written to stock_units / products
```

New fields on `stock_units` (already has `unit_cost`):
```
stock_units.landed_cost_per_unit: Decimal128   // allocated landed cost portion for this unit
stock_units.true_unit_cost: Decimal128         // unit_cost (from PO/GRN) + landed_cost_per_unit
stock_units.cost_source: str                   // extend existing field: "GRN+LANDED" after allocation
```

New field on `products`:
```
products.last_landed_cost_per_unit: Decimal128   // for reorder and pricing reference
products.last_landed_cost_invoice_id: str
products.last_landed_cost_at: datetime
```

## Backend
- `PUT /api/v1/vendors/purchase-invoices/{invoice_id}/landed-costs` — Update or set landed cost components on an already-booked invoice (before `landed_cost_allocated=True`). Recalculates `landed_cost_total`. Role: ADMIN/ACCOUNTANT/SUPERADMIN.
- Extend `POST /api/v1/vendors/purchase-invoices` — Accept optional `landed_cost_components` at booking time. If provided and `auto_allocate=True` (default), immediately runs allocation after invoice is booked.
- `POST /api/v1/vendors/purchase-invoices/{invoice_id}/allocate-landed-costs` — Explicitly trigger allocation run. Validates invoice status (not PAID is fine; must be OUTSTANDING or PARTIAL), checks `landed_cost_allocated=False`, runs `allocate_landed_costs()`, writes `true_unit_cost` to matching `stock_units`, updates `products.cost_price` via AVCO blend, stamps `landed_cost_allocated=True`. Idempotent guard: second call returns 409 with existing allocation summary. Role: ADMIN/ACCOUNTANT/SUPERADMIN.
- `GET /api/v1/vendors/purchase-invoices/{invoice_id}/landed-cost-allocation` — Returns per-SKU allocation breakdown (product_id, sku, qty, base_unit_cost, landed_cost_per_unit, true_unit_cost, stock_units_updated). For the review modal before confirming allocation.
- Extend `purchase_invoice_engine.allocate_landed_costs(lines, components, method)` — Pure function. Allocation methods:
  - VALUE (default): distribute proportional to each line's `taxable_value`.
  - QUANTITY: distribute proportional to `accepted_qty`.
  - WEIGHT: distribute proportional to `weight_kg` (requires weight field on GRN line — new optional field).
- Extend `purchase_match.three_way_match()` — Include `landed_cost_total` in the invoice-side total so match verdict is on full landed cost, not just product value.
- Write `audit_logs` entry on every allocation run: `action="landed_cost.allocated"`, `entity_type="purchase_invoice"`, `entity_id=invoice_id`, `detail={method, components_count, lines_affected, cost_delta_per_sku}`.

## Frontend
- `frontend/src/pages/purchase/PurchaseInvoicesTab.tsx` — Add `LandedCostMatrix` panel below the existing line-items table:
  - A compact table with one mandatory row per component type (FREIGHT, IMPORT_DUTY, CUSTOMS, FOREX_ADJUSTMENT; rows pre-populated with 0 so users must consciously enter or confirm zero).
  - FOREX row: shows sub-fields for foreign currency selector and exchange rate; computes INR equivalent inline.
  - Running total "Landed Cost Total" shown in the invoice summary alongside taxable and GST totals.
  - Allocation method selector (radio: By Value / By Quantity / By Weight) with a tooltip explaining each.
  - "Preview Allocation" button — calls `GET .../landed-cost-allocation`, opens a read-only drawer showing per-SKU landed cost breakdown before committing.
  - "Confirm and Allocate" button — calls `POST .../allocate-landed-costs`; disabled if `landed_cost_allocated=True` (shows "Allocated on {date}" badge instead).
- `frontend/src/pages/purchase/GoodsReceiptNote.tsx` — Add optional `weight_kg` per line (for weight-based allocation method). Light neutral input; only visible when weight allocation method is selected on the downstream invoice.
- Extend `frontend/src/pages/purchase/PurchaseInvoicesTab.tsx` invoice detail drawer — Show `landed_cost_components` breakdown and `true_unit_cost` per SKU as read-only once allocated.

## Business rules
- All five component types (FREIGHT, IMPORT_DUTY, CUSTOMS, FOREX_ADJUSTMENT, INSURANCE) must appear in the matrix UI. Each is mandatory in the sense that the field must be explicitly filled — cannot leave blank, must enter 0 if not applicable. This prevents accidental omission.
- `landed_cost_allocated` is a one-way flag. Once set to `True`, the allocation cannot be re-run without SUPERADMIN override. Any re-run must write a reversal audit entry and recompute AVCO from scratch for affected SKUs.
- Allocation only updates `stock_units` where `status='AVAILABLE'` (already-sold units had `cost_at_sale` frozen at sale time — this is correct accounting behavior; do not retroactively change sold-unit cost).
- If all units for a GRN line are already SOLD before allocation runs, log a warning in the audit entry (`units_available=0, cost_update_skipped=True`). Do not block the allocation.
- `true_unit_cost` must always be `>= unit_cost` (landed costs cannot produce a negative addition). Negative component amounts are rejected with HTTP 422.
- FOREX components: `amount_inr = foreign_amount * forex_rate`; both must be positive. The rate entered is the rate on the Bill of Entry date (customs valuation date), not today's spot rate. The system stores what the user enters — it does not pull live FX rates.
- The 3-way match tolerance (`purchase_settings.match_tolerance_pct`, default 5%) applies to the full landed invoice value (product value + landed cost total) vs PO total. A heavily freighted import will likely breach 5% vs the PO and surface as ON_HOLD_EXCEPTION — the accountant must use the existing exception-approval flow.
- Tally export (NEXUS nightly 23:00): landed cost components must be included as separate expense voucher lines in the Tally JV XML (`backend/agents/implementations/nexus.py`, `_build_tally_export()`), mapped to the correct expense ledger heads (FREIGHT A/C, CUSTOMS DUTY A/C, etc.) so the Tally trial balance is accurate.

## RBAC
- ADMIN, ACCOUNTANT, SUPERADMIN: full read + write (enter components, trigger allocation, view breakdown).
- STORE_MANAGER, AREA_MANAGER: read-only (can see landed cost breakdown on invoices for their store's GRNs; cannot edit or allocate).
- CATALOG_MANAGER: read-only on `products.last_landed_cost_per_unit` (for pricing reference).
- All other roles: no access to purchase invoice or landed cost endpoints.
- Re-run / reversal of allocation: SUPERADMIN only.

## Integrations
- **Tally** — NEXUS `_build_tally_export()` must emit landed cost component lines as separate expense vouchers (e.g., Freight Charges A/C Dr, Supplier A/C Cr). This is a required extension to the existing Tally JV builder in `backend/agents/implementations/nexus.py`.
- **No Shopify, Razorpay, MSG91, or Jarvis agent involvement needed** for the core feature. ORACLE could surface a future insight ("your landed cost ratio on imported frames is averaging 18% — consider domestic sourcing") but that is out of scope for this build packet.

## Risk notes
- **Money integrity risk (MED):** Landed cost allocation mutates `products.cost_price` via the existing AVCO path. If invoked twice (bug or race), AVCO will compound incorrectly. The `landed_cost_allocated` idempotency flag + the SUPERADMIN-only re-run gate are the primary guards. Test this path exhaustively.
- **Already-sold stock (LOW):** `cost_at_sale` is frozen at sale; landed cost allocation correctly skips SOLD units. However, if a batch sells out between GRN acceptance and invoice allocation (common for fast-moving stock), the P&L for those sales will never reflect the true landed cost. This is an inherent limitation of post-sale cost true-up, not a bug. Flag it clearly in the allocation preview UI.
- **Tally ledger mapping (MED):** Each component type must map to a specific Tally expense ledger name. If the user's Tally chart of accounts uses non-standard names (e.g., "Import Duty Charges" vs "Customs Duty"), the JV XML will post to the wrong ledger. Owner must confirm the exact Tally ledger names for each component type before the Tally integration is built.
- **No feature flag needed** for the purchase invoice flow itself (it is additive — existing invoices without `landed_cost_components` work unchanged). However, the Tally JV extension should be deployed only after confirming Tally ledger names with the owner.
- **Weight-based allocation**: Requires `weight_kg` on GRN lines, which is a new optional field. If weight is missing and the user selects weight-based allocation, return HTTP 422 with a clear message listing the affected lines.

## Recommendation
Build later — depends on importing luxury goods (Cartier, Bvlgari, Gucci) being an active buying channel. For domestic frame/lens procurement the landed cost is typically small freight only, which is already loosely absorbed in the PO unit price. Build this in Phase 4 when NEXUS Tally export is stabilized, so the Tally ledger mapping can be shipped together with the allocation engine rather than as two separate deployments.

## Owner decisions
- Q: Which landed cost component types are relevant to your buying today — domestic freight only, or also import duty/customs/forex? | Why: If only domestic freight is relevant now, the matrix can ship with just FREIGHT and OTHER, reducing complexity and Tally ledger mapping work to two lines instead of five. | Options: (a) Full matrix — FREIGHT, IMPORT_DUTY, CUSTOMS, FOREX_ADJUSTMENT, INSURANCE, OTHER; (b) Domestic-only — FREIGHT and OTHER for now, importcomponents added later; (c) Build only for specific brands (e.g., Cartier, Bvlgari imports only).

- Q: Which allocation method should be the default when a user books an invoice — By Value, By Quantity, or By Weight? | Why: By Value is the most common accounting practice and requires no extra data; By Weight is more accurate for heavy goods like frames but requires weight entry on every GRN line. | Options: (a) By Value (recommended — no extra data needed); (b) By Quantity; (c) By Weight (requires adding weight_kg field to GRN).

- Q: What are the exact Tally ledger account names you use for freight charges, import duty, and customs duty in your Tally books? | Why: The nightly Tally JV export must post each component to the correct ledger head by exact name, or Tally will create a new ledger automatically (which corrupts your chart of accounts). | Options: (a) Share the names and the Tally integration can be built to match; (b) Skip Tally integration for landed costs for now and only update product cost_price in IMS.

- Q: Should the landed cost allocation run automatically when an invoice is booked, or should the accountant always preview and confirm manually before it commits? | Why: Auto-allocation is faster but removes a review step for expensive imports where a keying error (e.g., wrong forex rate) could silently distort all SKU costs. | Options: (a) Auto-allocate on booking (fast, less control); (b) Always require manual "Confirm and Allocate" after preview (safer, one extra click); (c) Auto-allocate for domestic invoices, manual confirmation for any invoice with FOREX or IMPORT_DUTY components.
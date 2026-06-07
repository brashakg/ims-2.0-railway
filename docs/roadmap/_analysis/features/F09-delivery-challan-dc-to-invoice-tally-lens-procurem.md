# Feature #9: Delivery Challan (DC) to Invoice Tally (Lens Procurement)
META: effort=M days=8 risk=MED roi=4 quickwin=no deps=none phase=3

## Existing overlap
- **GRN flow** (`backend/api/routers/vendors.py:1136-1509`): already models goods receipt (grn_number, po_id, vendor_id, items with accepted/rejected qty, stock minting on accept). The DC is logically a GRN variant — goods arrive before invoice. **This feature extends GRN, not replaces it.**
- **Purchase invoice (first-class AP doc)** (`backend/api/routers/purchase_invoices.py:369-552`): bulk invoice already books against grn_id; `prefill from GRN` (GET `/from-grn/{grn_id}`) already drafts invoice lines from one GRN. DC-to-invoice matching is a 1:N version of this same flow.
- **3-way match** (`backend/api/services/purchase_match.py`): `three_way_match()` compares PO qty / GRN qty / invoice qty. The bulk-invoice case will produce a new verdict: N DCs vs 1 invoice (vs PO). The engine needs a small extension, not a rewrite.
- **Period lock** (`backend/api/routers/finance.py:446-481`): already blocks posting to closed months. DC logging and bulk-invoice booking both respect this automatically.
- **Workshop job creation guard** (`backend/api/routers/workshop.py:865-874`): `confirmed_by_sales` gate already exists as a pattern for blocking a job from advancing until a prerequisite is met. The DC-log HARDLOCK follows the same pattern.
- **Stock minting on GRN accept** (`vendors.py:1309-1509`): mints serialized `stock_units` with `source_type='GRN'`, `source_id=grn_id`. DCs will mint units the same way; the barcode is already the handle the workshop uses for scanning.
- **Audit logging** (`audit_logs` collection): all state transitions already write immutable audit rows. DC lifecycle events fold in here.

## Reuse (extend, don't rebuild)
- **`grns` collection** — add `grn_subtype` field (`STANDARD` | `DELIVERY_CHALLAN`); add `dc_number`, `dc_date`, `linked_bulk_invoice_id`, `dc_matched` boolean. No new collection needed for DC itself.
- **`vendor_bills` collection** — add `linked_dc_ids` (array), `dc_match_status` (`PENDING` | `MATCHED` | `EXCEPTION`), `dc_match_detail` (per-line variance). Existing `match_status` field stays for 3-way PO/GRN/invoice match.
- **`backend/api/routers/vendors.py`** — extend GRN create endpoint: when `grn_subtype=DELIVERY_CHALLAN`, capture `dc_number` + `dc_date`; keep stock-minting path identical (barcode generated on accept, exactly as today).
- **`backend/api/services/purchase_match.py`** — add `dc_bulk_match(dc_ids, invoice_lines, tolerance_pct)` function: aggregates accepted qty across all DCs for that vendor/period, compares to invoice billed qty per SKU, returns `MATCHED` / `EXCEPTION` verdict. Pure function, unit-testable.
- **`backend/api/routers/purchase_invoices.py`** — extend `prefill_from_grn` → new variant `prefill_from_dcs`: accepts a list of dc_ids, aggregates lines, returns consolidated draft invoice. Extend book endpoint to accept `linked_dc_ids[]` and trigger `dc_bulk_match`.
- **`frontend/src/pages/purchase/GoodsReceiptNote.tsx`** — add a toggle "This is a Delivery Challan (no invoice yet)" which shows `dc_number` + `dc_date` fields, hides `vendor_invoice_no` (not mandatory for DCs). Same form, conditional fields.
- **`frontend/src/pages/purchase/PurchaseInvoicesTab.tsx`** — add a "Match DCs to Invoice" panel: vendor + date-range filter shows open (unmatched) DCs; accountant selects the set, enters bulk invoice details, books in one action.
- **Workshop HARDLOCK** — extend `backend/api/routers/workshop.py` job-create path: before creating a workshop job for a lens order line, check that at least one accepted DC exists for that `product_id` + `store_id` with `dc_matched=False` OR a matched DC already covers it. If no DC found → 422 with a clear message. This is a config-flag (`REQUIRE_DC_FOR_WORKSHOP`) defaulting `true` in prod, `false` in dev/test.

## Data model
New fields on **`grns`** (existing collection):
- `grn_subtype`: `"STANDARD"` | `"DELIVERY_CHALLAN"` (default `"STANDARD"` for backward compat)
- `dc_number`: string, required when subtype=DC
- `dc_date`: ISO date string, required when subtype=DC
- `linked_bulk_invoice_id`: ObjectId ref to `vendor_bills`, null until matched
- `dc_matched`: boolean, default false

New fields on **`vendor_bills`** (existing collection):
- `linked_dc_ids`: array of grn_ids
- `dc_match_status`: `"PENDING"` | `"MATCHED"` | `"EXCEPTION"` | `"N_A"` (N_A for non-DC invoices)
- `dc_match_detail`: array `{product_id, dc_qty_total, invoice_qty, variance_pct, verdict}`

No new collection. All existing indexes on `grns` (vendor_id, po_id, status) cover the new queries.

## Backend
- **`POST /api/v1/vendors/grns`** (existing) — extend: if request body has `grn_subtype=DELIVERY_CHALLAN`, require `dc_number` + `dc_date`; skip `vendor_invoice_no` mandatory check; stock-mint path unchanged.
- **`GET /api/v1/vendors/grns?grn_subtype=DELIVERY_CHALLAN&dc_matched=false&vendor_id=&store_id=&from=&to=`** (extend existing list) — returns open DCs for accountant matching panel. Add these filter params to existing GRN list endpoint.
- **`GET /api/v1/vendors/purchase-invoices/from-dcs`** — new: accepts `dc_ids[]` query param, aggregates accepted lines across those DCs, returns a draft invoice payload (same shape as existing `from-grn` response). Calls `dc_bulk_match` internally to pre-compute expected quantities.
- **`POST /api/v1/vendors/purchase-invoices`** (existing book endpoint) — extend: if `linked_dc_ids` present in request, run `dc_bulk_match`, store result on the bill, flip `dc_matched=true` on each linked GRN, write `linked_bulk_invoice_id` back on each DC doc.
- **`GET /api/v1/vendors/purchase-invoices/{invoice_id}/dc-match`** — new: returns stored `dc_match_detail` (same pattern as existing `/match` endpoint for 3-way match).
- **Workshop HARDLOCK check** — internal helper called from `POST /api/v1/workshop/jobs` (existing): `_check_dc_exists(product_id, store_id)` → queries `grns` for accepted DCs for that product at that store. Returns bool; 422 if false and flag enabled.

## Frontend
- **`GoodsReceiptNote.tsx`** (extend, not new page) — add a "DC mode" toggle at the top. When on: `dc_number` + `dc_date` fields appear (required), `vendor_invoice_no` field becomes optional with a grey label "Invoice arrives later". All existing quality checklist, qty fields, barcode print unchanged.
- **`PurchaseInvoicesTab.tsx`** (extend) — add a second tab "Match DCs to Invoice" alongside "Book Invoice": date-range + vendor filter shows a table of open (unmatched) DCs with product, qty, dc_number, dc_date. Accountant selects rows → "Generate Bulk Invoice" → pre-fills the book-invoice form with aggregated lines. On save, match results (MATCHED / EXCEPTION lines) appear inline with a colour-coded variance column (green = within tolerance, amber = exception).
- **`WorkshopPage.tsx`** (extend) — when HARDLOCK triggers, job-create modal shows a banner "No DC logged for this lens. Ask the Store Manager to log the DC first." with a link to the GRN/DC entry page. No new page; a conditional banner in the existing create-job modal.
- **No new pages required.** All surfaces are extensions of existing pages.

## Business rules
- **DC HARDLOCK**: Workshop job cannot be created for a lens SKU at a store unless at least one accepted DC exists covering that SKU at that store. This is a store-level toggle (default: ON for all lens-type products).
- **DC number is mandatory and unique per vendor**: duplicate `dc_number + vendor_id` combination must be rejected (unique index on `grns` scoped to `{vendor_id, dc_number}` when `grn_subtype=DELIVERY_CHALLAN`).
- **DC date cannot be in a period-locked month**: same period-lock check already applied to all GRNs.
- **Stock is minted on DC accept, not on invoice**: barcodes and serialized units are created when the Store Manager accepts the DC, before the invoice arrives. This is correct and intentional.
- **Bulk invoice must cover at least one DC**: `linked_dc_ids` cannot be empty for a DC-mode invoice.
- **DC match tolerance**: configurable (default 5%, same as existing `match_tolerance_pct` in `purchase_settings`). Lines outside tolerance → `EXCEPTION` status, not a hard block (accountant can approve-exception as today).
- **DC → invoice linkage is one-way**: a DC can only be linked to one bulk invoice. Re-linking must be blocked once `dc_matched=true`.
- **Audit immutability**: every DC accept, bulk-invoice match, and workshop HARDLOCK trigger writes an `audit_logs` row. These rows are never updated.
- **Backward compatibility**: existing GRNs with `grn_subtype=STANDARD` (or no subtype field) are unaffected. HARDLOCK only fires for lens-category products (identified by `item_type` on the product doc).

## RBAC
| Action | Roles |
|---|---|
| Log a DC (create GRN with subtype=DELIVERY_CHALLAN) | STORE_MANAGER, AREA_MANAGER, ADMIN, SUPERADMIN |
| View open (unmatched) DCs | ACCOUNTANT, STORE_MANAGER, AREA_MANAGER, ADMIN, SUPERADMIN |
| Match DCs to bulk invoice (book invoice with linked_dc_ids) | ACCOUNTANT, ADMIN, SUPERADMIN |
| Approve DC-match exception | ACCOUNTANT, ADMIN, SUPERADMIN |
| Override HARDLOCK (create workshop job without DC) | ADMIN, SUPERADMIN only — requires `override_reason` field, audited |
| View DC-match detail on invoice | ACCOUNTANT, ADMIN, SUPERADMIN |

All other roles see no DC-specific UI. OPTOMETRIST, SALES_CASHIER, WORKSHOP_STAFF have no visibility.

## Integrations
- **Tally**: The existing nightly NEXUS Tally export (`tally_exports` collection, `nexus.py`) exports purchase vouchers. DC accept does NOT generate a Tally voucher (it is a goods movement, not an accounting entry). The bulk invoice booking triggers the purchase voucher — same as today. No change to Tally integration.
- **None else**: Shopify, Razorpay, MSG91, Shiprocket have no role in this feature.
- **Jarvis / ORACLE**: ORACLE's demand-forecast already reads from accepted GRNs for reorder proposals. DCs (as GRN variants) will automatically feed into those projections without any agent change.

## Risk notes
- **Workshop HARDLOCK is a process change, not just a code change**: Store Managers must learn to log DCs before handing lenses to workshop. Training and a 2-week grace period (HARDLOCK in warning-only mode first) are strongly recommended. The flag `REQUIRE_DC_FOR_WORKSHOP` allows the grace period without a redeploy.
- **Backward migration**: existing workshop jobs created before this feature have no DCs. The HARDLOCK must only apply to jobs created after a cutover date, not retroactively block existing pending jobs.
- **Duplicate DC numbers from same vendor**: vendors sometimes re-use DC numbers across branches. The uniqueness constraint must be scoped to `(vendor_id, dc_number, store_id)` not just `(vendor_id, dc_number)` — confirm with owner if vendor sends from a central warehouse vs per-branch.
- **Bulk invoice splitting across periods**: if DCs span two accounting months and one is period-locked, the bulk invoice booking may partially fail. The period-lock check needs to run on the earliest DC date, not just the invoice date.
- **No money movement risk**: DCs do not touch `orders`, payments, or financial balances. The only financial write is the bulk invoice booking (AP), which already exists and is tested.
- **POS not touched**: zero POS impact. No feature flag needed for POS.

## Recommendation
Build in Phase 3 (next feature batch). Not a quick win due to the process-change risk (HARDLOCK requires Store Manager training), but the ROI is high — lost lenses and phantom invoice payments are real cash leakage. Build the DC logging and stock-minting first (1 week), then the HARDLOCK and bulk-match panel as a second step after a 2-week observation period.

## Owner decisions
- Q: Should the DC HARDLOCK apply to ALL lens products, or only to external-lab lenses (vendor_id is a lens lab), leaving in-store stock free? | Why: If all lenses are covered, stock pulled from your own inventory for a job also needs a DC logged — which is operationally different from receiving from a lab. | Options: (a) Only jobs where lens_status=ORDERED (lens sourced from external vendor) require a DC — jobs using in-house stock are exempt. (b) All lens jobs require a DC regardless of source. (c) Configurable per vendor type.
- Q: What is the acceptable bulk-invoice matching window — how many days of DCs can one invoice cover? | Why: This sets the date-range filter default in the accountant matching panel and determines whether a DC from 45 days ago can be matched to today's invoice. | Options: (a) 30 days (monthly billing cycle). (b) 15 days (fortnightly). (c) No cap — accountant selects any open DCs manually.
- Q: When a DC-to-invoice match shows an EXCEPTION (vendor billed more than received), should the system auto-hold the invoice for approval, or just warn and allow the accountant to proceed? | Why: Auto-hold (ON_HOLD_EXCEPTION) prevents overpayment but slows processing. Warn-only lets the accountant decide case by case. | Options: (a) Auto-hold — same behavior as today's 3-way match (existing `ON_HOLD_EXCEPTION` status). (b) Warn only — exception flagged but invoice posts immediately. (c) Auto-hold only when variance exceeds a second, higher threshold (e.g., 15%).
- Q: Should the DC number be printed on the barcode label that goes on the physical lens tray, so workshop staff can visually cross-check? | Why: This requires adding dc_number to the label print payload in `labels.py`. It is a one-line change but needs your confirmation that the physical label format has room for it. | Options: (a) Yes — print DC number on label. (b) No — barcode/job number is sufficient.
# Feature #6: Luxury Serial Number Tracking (Anti-Fraud)
META: effort=M days=4 risk=MED roi=4 quickwin=no deps=none phase=2

## Existing overlap
IMS already has a `serial_numbers` collection and full CRUD endpoints in `backend/api/routers/inventory.py` (lines 3296–3520). The `_compute_warranty_status()` helper, `_serial_to_frontend()` mapper, and `SerialNumberTracker.tsx` frontend component are fully built. GRN stock-minting already stamps `source_type='GRN'`, `source_id=grn_id`, `po_id` on each `stock_units` row. The barcode lifecycle trace at `GET /barcode/{barcode}/trace` (inventory.py:532–595) already joins purchase → orders → transfers → returns into one audit chain. Returns quantity enforcement in `returns.py` (lines 302–348) uses atomic `find_one_and_update` with per-line qty caps. The existing system is missing the link between `serial_numbers` and `stock_units`, GRN capture of manufacturer serials, and any return-time serial verification step.

## Reuse (extend, don't rebuild)
- `serial_numbers` collection — add `stock_unit_id`, `grn_id`, `po_id`, `shopify_order_id` linkage fields; add `sold_to_order_id` and `return_verified` flag
- `backend/api/routers/inventory.py` POST `/serials` and PATCH `/serials/{serial_id}` — extend to accept `stock_unit_id` at GRN-mint time; add `PUT /serials/{serial_id}/verify-return` endpoint
- `backend/api/routers/vendors.py` accept_grn() (lines 1309–1509) — hook serial capture into the stock-minting loop; for eligible SKUs, require serial_number before accepting
- `backend/api/routers/orders.py` create_order() (line 1027) — for serialized items, stamp `serial_id` on the order line and flip `serial_numbers.sold_to_order_id`
- `backend/api/routers/returns.py` create_return() (line 928) + `_claim_returnable_qty()` (line 371) — add serial scan step before claim is granted; flag mismatch and block or escalate
- `frontend/src/components/inventory/SerialNumberTracker.tsx` — extend search/filter; add "link to GRN" column and return-verification status badge
- `frontend/src/pages/purchase/GoodsReceiptNote.tsx` — add per-line serial input field for tracked SKUs
- Barcode lifecycle trace at `GET /barcode/{barcode}/trace` — extend to join `serial_numbers` row so the trace shows manufacturer serial alongside IMS barcode

## Data model
New fields on existing `serial_numbers` collection (no new collection needed):
- `stock_unit_id` (str, indexed) — links to the `stock_units` row minted at GRN
- `grn_id` (str) — which GRN introduced this unit
- `po_id` (str) — originating PO
- `sold_to_order_id` (str, nullable) — stamped at POS sale
- `sold_to_order_line` (int, nullable) — line index within that order
- `return_serial_scanned` (str, nullable) — the serial the return agent actually scanned
- `return_verified` (bool, default null) — true=match, false=mismatch, null=not yet returned
- `return_verified_at` (datetime, nullable)
- `return_verified_by` (str, nullable)
- `mismatch_flagged` (bool, default false) — set true when return_serial_scanned ≠ serial_number
- `mismatch_escalation_id` (str, nullable) — task_id of the auto-created P1 escalation task

New compound index on `serial_numbers`: `(serial_number, status)` for fast scan lookup.

New field on `orders.items[]`:
- `serial_id` (str, nullable) — reference to `serial_numbers._id` for serialized line items

New field on `products` (or `catalog_products`):
- `track_serial` (bool, default false) — set true by Catalog Manager for SKUs requiring serial capture

## Backend
- `GET /inventory/serials/lookup?serial_number=X` — fast lookup by manufacturer serial; returns current status, linked order, linked return; used at GRN scan and return desk; no auth change (same roles as existing serial endpoints)
- `PUT /inventory/serials/{serial_id}/verify-return` — accepts `{scanned_serial: str, return_id: str}`; compares to `serial_numbers.serial_number`; on match sets `return_verified=true`; on mismatch sets `return_verified=false`, `mismatch_flagged=true`, creates P1 task via existing task-creation pattern, returns `{verified: bool, mismatch: bool, task_id}`; role-gated SALES_CASHIER/SALES_STAFF/STORE_MANAGER/ADMIN/SUPERADMIN
- Extend `vendors.py` accept_grn() — before minting a `stock_units` row for a `track_serial=true` SKU, require `serial_number` in the GRN line payload; call `POST /serials` inline, link `stock_unit_id` back; if serial missing → 422 with message "Serial number required for this SKU before GRN can be accepted"
- Extend `orders.py` create_order() — for items where `products.track_serial=true`, look up the AVAILABLE `serial_numbers` row matching `stock_unit_id` on the reserved unit; stamp `serial_id` on order line and `sold_to_order_id` on the serial record (atomic update)
- Extend `returns.py` `_claim_returnable_qty()` — for serialized items, add pre-claim check: was a serial registered at sale? If yes, require `scanned_serial` in the return payload before granting the claim. Mismatch does not hard-block by default (configurable — see Owner decisions) but always flags and escalates

## Frontend
- `GoodsReceiptNote.tsx` — for each GRN line where the product has `track_serial=true`, show a serial number input field (text input + barcode scan button via existing `StockCountScanningInterface` pattern); field is required before the "Accept GRN" button enables; bulk serial paste (newline-separated) for multi-unit lines
- `ReturnsPage.tsx` — for a return line tied to a serialized item, add a "Scan manufacturer serial" step before submitting; show green checkmark on match, red warning on mismatch with escalation note; if owner chooses soft-block, allow manager override with reason
- `SerialNumberTracker.tsx` — add columns: GRN date, linked order number, return verification status (Verified / Mismatch / Pending); add filter by `mismatch_flagged=true` for fraud review queue
- `OrdersPage.tsx` order detail view — show serial number alongside line item for serialized SKUs (read-only, sourced from `orders.items[].serial_id` join)
- `ProductCatalogPage.tsx` or equivalent — add `Track Serial Number` toggle per SKU (Catalog Manager only); show warning "Enabling this requires serial capture at every future GRN for this product"

## Business rules
- Serial numbers must be globally unique within the `serial_numbers` collection — unique index on `serial_number` field; duplicate at GRN → 409 error, reject the unit
- A serial can only be linked to one open order at a time — if `sold_to_order_id` is already set and status is SOLD, block re-sale of same serial
- At GRN, serial capture is enforced only for SKUs where `products.track_serial=true`; all other SKUs follow existing flow unchanged
- At return, serial verification is enforced only when the original order line has a `serial_id` stamped; legacy orders without serial linkage bypass the check gracefully (no block)
- Mismatch must always be audit-logged in `audit_logs` with `action='serial_mismatch'`, `before_state={expected_serial}`, `after_state={scanned_serial}`, `entity_type='return'`, `entity_id=return_id` — this is immutable, cannot be deleted even by SUPERADMIN
- A P1 escalation task is auto-created on mismatch, assigned to STORE_MANAGER, with description containing order number, expected serial, scanned serial; task follows existing SLA escalation engine in TASKMASTER
- Return hard-block vs soft-flag is owner-configurable (see Owner decisions); hard-block requires STORE_MANAGER override with written reason stored on the mismatch record
- `track_serial` flag on a product cannot be disabled once any serial has been captured for that SKU (prevent retroactive removal of audit trail)

## RBAC
- `track_serial` toggle on products: CATALOG_MANAGER, ADMIN, SUPERADMIN only
- Serial capture at GRN: STORE_MANAGER, AREA_MANAGER, ADMIN, SUPERADMIN (same roles as existing GRN accept)
- Serial lookup (`GET /serials/lookup`): SALES_CASHIER, SALES_STAFF, STORE_MANAGER, AREA_MANAGER, ADMIN, SUPERADMIN
- Return serial verify endpoint: SALES_CASHIER, SALES_STAFF, STORE_MANAGER, AREA_MANAGER, ADMIN, SUPERADMIN
- Mismatch override (allow return despite mismatch): STORE_MANAGER, AREA_MANAGER, ADMIN, SUPERADMIN only — override requires written reason
- Mismatch fraud review queue (filter by `mismatch_flagged=true`): STORE_MANAGER, AREA_MANAGER, ADMIN, SUPERADMIN
- Disable `track_serial` on a SKU that already has serials: SUPERADMIN only

## Integrations
- TASKMASTER agent — auto-creates P1 escalation task on serial mismatch; reuses existing `_escalate_task()` pattern from `taskmaster.py`; no new agent work required beyond wiring the task-create call from the verify-return endpoint
- No Shopify, Razorpay, Tally, or MSG91 changes required for core feature; if owner wants WhatsApp alert to Store Manager on mismatch, that is a MEGAPHONE extension (one `send_notification` call in the verify-return handler, DISPATCH_MODE gated)

## Risk notes
- POS impact is additive only (reading `serial_id` from the order line, no change to payment or discount logic) — no feature flag needed for the read path
- GRN accept change is the highest-risk touch point: adding a required field for `track_serial` SKUs will break any automated or scripted GRN acceptance that doesn't pass `serial_number` — test against the GRN acceptance API before enabling `track_serial` on any live SKU
- The return flow change touches `_claim_returnable_qty()` which is already guarded by atomic `find_one_and_update`; the serial check is a pre-claim validation, so it does not widen the concurrency window
- Hard-block mode (owner decision below) makes the return desk dependent on a working barcode scanner — plan fallback (manager override) for hardware failure

## Recommendation
Build in Phase 2 as a standalone hardening PR. The `serial_numbers` collection and CRUD are already there; this is primarily wiring serial capture into GRN, stamping it on orders, and adding the verify-return endpoint. It is not a quick win only because the GRN change requires coordinated staff training on capture at receiving time, but the code effort is medium and the fraud-prevention ROI is high for luxury SKUs.

## Owner decisions
- Q: Which brands or product categories should have `track_serial=true` enforced from day one? | Why: Determines how many SKUs trigger mandatory serial capture at GRN and how much the receiving workflow changes on launch day | Options: (a) All luxury brands (Cartier, Chopard, Bvlgari, Gucci, Prada, Versace, Burberry) by default — highest protection, most friction at GRN; (b) LUXURY category flag only — broader but may include non-serialized items; (c) Manual opt-in per SKU by Catalog Manager — lowest day-one friction, slower rollout
- Q: When a return serial scan does not match the original sale serial, should the system hard-block the refund or soft-flag it and allow a manager override? | Why: Hard-block stops the fraud but creates a counter-dispute queue; soft-flag keeps the counter moving but relies on managers acting on the flagged queue | Options: (a) Hard-block — refund cannot proceed without STORE_MANAGER override + written reason; (b) Soft-flag — refund proceeds, P1 task is created, manager reviews asynchronously; (c) Store-level config — each store manager chooses their own policy
- Q: Should serial mismatch trigger a WhatsApp alert to the Store Manager in addition to the in-app P1 task? | Why: If yes, MSG91 DISPATCH_MODE must be set to "live" and the store manager's phone number must be in the system | Options: (a) Yes — WhatsApp + in-app; (b) In-app task only (simpler, no MSG91 dependency)
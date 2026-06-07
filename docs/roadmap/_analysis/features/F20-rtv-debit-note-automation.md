# Feature #20: RTV Debit Note Automation
META: effort=M days=5 risk=MED roi=4 quickwin=no deps=none phase=3

## Existing overlap
IMS already has a vendor returns module (`backend/api/routers/vendor_returns.py`) with a status machine (created → approved → shipped → received_by_vendor → credit_issued|replaced|cancelled), a `credit_note_number` field, and `total_value` calculation. The `vendor_bills` collection (`backend/api/routers/purchase_invoices.py`) tracks AP outstanding with `status` (OUTSTANDING|PARTIAL|PAID) and a `match_status` field already used to hold invoices (ON_HOLD_EXCEPTION). The 3-way match engine (`backend/api/services/purchase_match.py`) already has the concept of blocking a bill pending reconciliation. The AP engine (`backend/api/services/ap_engine.py`) computes ledger balances. The audit trail (`audit_logs` collection) and notification system (in-app bell via `backend/api/routers/notifications.py`) are fully reusable.

The gap is: RTV creation does not auto-generate a debit note document, and approved vendor bills have no linkage to pending vendor credits that would surface a hardlock.

## Reuse (extend, don't rebuild)
- `backend/api/routers/vendor_returns.py` — extend `create_return()` and the `credit_issued` status transition to auto-mint a debit note and fire the invoice hardlock
- `vendor_bills` collection — add a `held_by_debit_note_ids` array field; the existing `match_status` ON_HOLD_EXCEPTION pattern drives the payment-block UI
- `backend/api/services/ap_engine.py` — extend `build_ledger()` to subtract pending debit-note amounts from net outstanding, and surface "held" invoices separately
- `backend/api/routers/purchase_invoices.py` — extend invoice payment endpoint to enforce hardlock check before recording any payment
- `audit_logs` collection — write debit-note creation and hardlock events (already used by the purchase module)
- `backend/api/routers/notifications.py` — send in-app bell to ACCOUNTANT/ADMIN when a debit note is created and when a vendor credit is received
- `backend/api/services/purchase_invoice_engine.py` — reuse `split_line_gst()` and `determine_place_of_supply()` for debit note GST computation

## Data model
- New collection `vendor_debit_notes` (the existing `vendor_debit_notes` field referenced in the AP ledger is a stub — make it first-class):
  - `debit_note_id` (unique), `debit_note_number` (format: DN/{FY}/{serial}, atomic counter reusing `counters` collection pattern from `order_repository.py:446`), `vendor_id`, `store_id`, `return_id` (FK to `vendor_returns`), `grn_id` (optional, if linked to original GRN), `lines[]` (product_id, qty, unit_price, taxable, cgst, sgst, igst, gst_rate), `total_value`, `cgst_total`, `sgst_total`, `igst_total`, `place_of_supply`, `status` (DRAFT|ISSUED|SETTLED|CANCELLED), `linked_bill_ids[]` (bills hardlocked by this note), `vendor_credit_ref` (vendor's credit note number, filled on settlement), `created_at`, `created_by`, `settled_at`, `settled_by`
- New fields on `vendor_bills`: `held_by_debit_note_ids: []` (array of debit_note_ids), `hold_reason: "PENDING_VENDOR_CREDIT"` (string, nullable), `net_payable` (computed: total_amount - sum of ISSUED debit notes against this vendor, surfaced in AP ledger)
- New fields on `vendor_returns`: `debit_note_id` (FK, set when auto-minted), `debit_note_number` (for display)

## Backend
- `POST /api/v1/vendors/vendor-returns` (extend existing `create_return`) — on creation, if return_type has returned goods (not replacement-only), auto-mint a DRAFT debit note in `vendor_debit_notes`; stamp `return.debit_note_id`
- `POST /api/v1/vendors/vendor-returns/{return_id}/status` (extend existing status PATCH) — on transition to `shipped` status, flip debit note to ISSUED and call `_apply_invoice_hardlock(vendor_id, debit_note_id)` which pushes `debit_note_id` into `vendor_bills.held_by_debit_note_ids` for all OUTSTANDING/PARTIAL bills for that vendor
- `POST /api/v1/vendors/debit-notes/{debit_note_id}/settle` (new) — ACCOUNTANT/ADMIN only; records `vendor_credit_ref`, sets status=SETTLED, removes `debit_note_id` from all `vendor_bills.held_by_debit_note_ids`, re-enables those bills for payment; writes audit row
- `GET /api/v1/vendors/debit-notes` (new) — list with filters: vendor_id, status, store_id, date range; returns debit_note_number, vendor name, total_value, status, linked bills count
- `GET /api/v1/vendors/debit-notes/{debit_note_id}` (new) — full detail including lines with GST split, linked bills, RTV reference
- `POST /api/v1/vendors/purchase-invoices/{invoice_id}/pay` (extend existing payment recording) — before recording payment, call `_check_invoice_hardlock(invoice_id)` which raises HTTP 409 with `{"error": "INVOICE_HELD", "held_by": [...debit_note_numbers...], "message": "Settle debit notes before paying this invoice"}` if `held_by_debit_note_ids` is non-empty
- `GET /api/v1/vendors/{vendor_id}/debit-note-summary` (new) — total pending debit value vs outstanding AP; used by AP ledger view

## Frontend
- Extend `frontend/src/pages/purchase/VendorReturns.tsx` — add a "Debit Note" column in the returns table showing the auto-generated DN number (clickable link) and status chip (DRAFT / ISSUED / SETTLED); no new page needed for creation since it's auto-generated
- New read-only panel `DebitNoteDetailPanel.tsx` (side drawer, not a full page) — shows DN number, lines with GST split, linked vendor bills with their hold status, vendor credit reference input field (for ACCOUNTANT to record when vendor sends credit), and a "Mark as Settled" button
- Extend `frontend/src/pages/purchase/PurchaseInvoicesTab.tsx` — when a bill has `held_by_debit_note_ids.length > 0`, show a red "HELD — Pending Credit" chip on the invoice row; disable the "Record Payment" button with tooltip listing the open debit note numbers; show a "View Debit Notes" link
- Extend vendor AP ledger view (within `FinanceDashboard.tsx` or the existing AP section) — add a "Pending Debit Notes" summary card per vendor showing total amount in dispute; this reduces net payable shown to the accountant

## Business rules
- Debit note is auto-generated the moment an RTV is created (status=created, note is DRAFT); it becomes ISSUED (and triggers hardlock) only when the return is marked as `shipped` (goods have left the store)
- Hardlock applies to ALL outstanding bills for that vendor, not just the bill the defective goods came from — vendor owes a credit regardless of which invoice is next for payment
- A bill with `held_by_debit_note_ids` non-empty cannot have any payment recorded against it (HTTP 409 enforced at backend; UI also disables the button)
- Hardlock is released only when a debit note is explicitly settled by ACCOUNTANT/ADMIN by recording the vendor's credit reference number — no auto-release on a timer
- Debit note GST mirrors the original GRN/PO line: if original was intra-state (CGST+SGST), debit note is also CGST+SGST; compute using `purchase_invoice_engine.determine_place_of_supply()` with the store's entity state
- Debit note number is consecutive per financial year (Apr-Mar), same atomic counter pattern as invoice numbers (`counters` collection, key `dn:{FY_start}`)
- Partial credit is supported: if vendor settles 60% of the debit note value, ACCOUNTANT can record a partial settlement; remaining amount keeps the hardlock active until fully settled; `vendor_debit_notes.status` stays ISSUED until fully settled
- All hardlock apply/release events written to `audit_logs` with `entity_type="VENDOR_DEBIT_NOTE"`, `before_state`, `after_state`

## RBAC
- SUPERADMIN, ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT: can view debit notes and the hold status on invoices
- ACCOUNTANT, ADMIN, SUPERADMIN: can settle a debit note (record vendor credit reference)
- STORE_MANAGER, AREA_MANAGER: can create RTVs (existing permission) which auto-triggers debit note creation — no additional permission needed
- CATALOG_MANAGER, OPTOMETRIST, SALES_CASHIER, SALES_STAFF, CASHIER, WORKSHOP_STAFF: no access to debit notes or vendor bills

## Integrations
- **Tally**: NEXUS nightly export (23:00) must include debit note vouchers. Extend `backend/agents/nexus_providers.py` `build_tally_export()` to generate a Tally "Debit Note" voucher type (Dr: Vendor ledger, Cr: Purchase Returns account) for all ISSUED debit notes not yet exported. Use existing `tally_exports` collection pattern; add `debit_notes_exported: []` field on export doc.
- **In-app notifications** (MSG91 not needed here): On hardlock apply, send in-app bell to ACCOUNTANT and ADMIN via existing `notifications.py` — "Vendor [name] invoice held: Debit Note [DN/2026-27/001] pending settlement (₹X)". On settlement, send confirmation bell.
- **Jarvis / ORACLE**: ORACLE's nightly EOD sweep should flag vendors with debit notes older than 30 days without settlement as anomalies; use existing `_detect_discount_abuse()` pattern to add a `_check_stale_debit_notes()` scan. Advisory only — no auto-execute.

## Risk notes
- **AP payment disruption**: The hardlock blocks ALL bills for a vendor, not just one. If a vendor has many outstanding bills and the accountant is unaware, this could stall all payments to that vendor until they notice the hold. The UI must make the hold reason and resolution path extremely clear (the "HELD" chip and tooltip are critical).
- **Partial credit complexity**: If the vendor sends a partial credit (e.g., disputes 2 of 5 returned items), the debit note must be partially settled with the remaining amount tracked. This is the trickiest business logic — if not handled, accountants will manually override or ignore the system. Needs careful testing.
- **Tally reconciliation**: Adding a new voucher type (Debit Note) to the Tally export requires the owner to confirm the Tally ledger name for "Purchase Returns" or "Vendor Claims" before building the XML. Wrong ledger name breaks Tally import.
- **No POS risk**: This feature is entirely within the purchase/vendor workflow, not POS. No feature flag needed for POS safety.
- **RTV without invoice link**: Some RTVs may be for goods received without a formal PO/invoice (e.g., consignment return). The hardlock logic must gracefully handle "no bill found for this vendor" — create the debit note but log a warning that no invoice was found to lock; notify the accountant to manually associate.

## Recommendation
Build later (after core purchase flow is stabilized). The foundation (vendor_returns, vendor_bills, AP engine, 3-way match) is solid enough to build on, but this feature changes AP payment behavior systemically — the invoice hardlock will surprise accountants if introduced before they are trained on the RTV workflow. Recommended sequencing: complete and stabilize the purchase invoice payment flow first, then add this as a Phase 3 enhancement once accountants are familiar with the AP module.

## Owner decisions
- Q: Should the hardlock apply to ALL outstanding bills for that vendor, or only to the specific invoice the defective goods were purchased on? | Why: Vendor-wide lock is simpler and ensures no payment escapes, but could stall legitimate invoices for unrelated goods from the same vendor. Invoice-specific lock is more surgical but requires linking each return line back to an original invoice line (which may not exist for older purchases). | Options: a) Vendor-wide lock on all outstanding bills (recommended for simplicity) / b) Lock only the specific invoice linked to the original GRN / c) Lock only invoices from the same period (financial month) as the RTV
- Q: What is the maximum number of days a debit note can remain ISSUED before ORACLE flags it as a stale dispute? | Why: This sets the alert threshold in the nightly EOD sweep; too short causes alert fatigue, too long lets disputes sit unresolved and distorts AP aging. | Options: a) 15 days / b) 30 days (recommended standard) / c) 45 days / d) no automatic alerting
- Q: When a vendor sends a partial credit (e.g., they accept 3 of 5 returned items), should the accountant be able to partially settle the debit note and release the hold proportionally, or must the full amount be settled before any bill is unlocked? | Why: Partial settlement is more flexible but significantly more complex to implement; full-only settlement is simpler but may cause friction with vendors who dispute partial quantities. | Options: a) Full settlement only — all or nothing (simpler, recommended for V1) / b) Partial settlement allowed — proportional hold release / c) ADMIN override to manually release specific bill holds while debit note remains open
- Q: What Tally ledger name should be used for the "Purchase Returns" credit side of the debit note voucher? | Why: The Tally XML export must reference exact ledger names as configured in your Tally company file, or the import will fail silently. | Options: a) "Purchase Returns" / b) "Vendor Claims Receivable" / c) share the exact ledger name from your Tally chart of accounts
# Feature #5: Warranty Parts "Cannibalization" Ledger
META: effort=M days=5 risk=MED roi=3 quickwin=no deps=none phase=3

## Existing overlap
- `stock_units` collection (backend/database/connection.py:238-243) already carries `status` enum (AVAILABLE|RESERVED|TRANSFERRED|SOLD) — cannibalization needs a new `CANNIBALIZED` status and a `AWAITING_PARTS` status, both absent today.
- `workshop_jobs` collection (backend/api/routers/workshop.py) already tracks `lens_status` (NOT_ORDERED→ORDERED→RECEIVED→MOUNTED), `vendor_id`, `vendor_status_history`, and `rework_count`. The "cannibalizing for a VIP job" is a new cross-job stock move that sits on top of this existing job model.
- `stock_audit` collection (populated by transfers.py ship/receive and returns.py restock) already holds per-unit movement history (prior_status, new_status, source, transfer_id). Cannibalization moves fit naturally here as a new `source_type='CANNIBALIZATION'`.
- `serial_numbers` collection (backend/api/routers/inventory.py:3296-3520) tracks high-value serialized items with warranty, supplier_batch, status. Luxury/VIP frames may already be registered here — cannibalization of a serial unit should update this table too.
- `audit_logs` collection is already written for every workshop state change and stock move — the cannibalization event will add entries here at no extra cost.
- `labels.py` scan-to-advance pipeline (stage gating, immutable scan_history) could be extended to gate the "release from AWAITING_PARTS back to AVAILABLE" transition when the vendor part arrives.
- `transfers.py` ship/receive pattern (`_apply_ship_stock_move`, idempotency flag, delta-only re-homes on retry) is the right mechanical model to copy for the cannibalization claim and later restoration.

## Reuse (extend, don't rebuild)
- `stock_units` collection — add two new status values: `CANNIBALIZED` and `AWAITING_PARTS`; add fields `cannibalization_id`, `donor_job_id` (job that consumed the part), `recipient_job_id` (VIP job that received it), `expected_part_arrival_date`, `part_po_id`.
- `stock_audit` collection — reuse as-is; new writes use `source_type='CANNIBALIZATION'` and `source_type='CANNIBALIZATION_RESTORE'`.
- `workshop_jobs` collection — add optional field `cannibalization_ids: []` array on both the donor job (parts were taken from its unsold unit) and the recipient (VIP) job.
- `backend/api/routers/workshop.py` — add three new endpoints inside this existing router rather than creating a new file.
- `backend/api/routers/inventory.py` barcode lifecycle trace (`GET /barcode/{barcode}/trace`, line 532-595) — extend the cross-collection join to include `cannibalization_ledger` so a unit's full life is traceable.
- `backend/api/routers/transfers.py` `_apply_ship_stock_move` guard pattern — copy the "only move AVAILABLE units, idempotency flag, delta-only" pattern for the claim step.
- `backend/api/routers/vendors.py` PO creation path — when a cannibalization is logged, optionally auto-create a DRAFT purchase order (same pattern as ORACLE's `_propose_reorders()` in oracle.py:178-204, tier-1 reversible proposal).
- `backend/agents/proposals.py` reversible tier-1 executor — the "create replacement PO" action maps cleanly to the existing `draft_po` reversible type; no new executor needed.
- `notifications.py` in-app bell and `agents/providers.py` WhatsApp — reuse existing notify pattern to alert Store Manager when donor unit enters AWAITING_PARTS and again when part arrives.

## Data model
New collection `cannibalization_ledger`:
- `cannibalization_id` (uuid, unique index)
- `store_id`
- `donor_stock_id` — the stock_unit physically stripped for parts
- `donor_product_id`, `donor_product_name`, `donor_sku`
- `recipient_job_id` — the VIP workshop job that received the part
- `recipient_customer_name`, `recipient_customer_id`
- `authorized_by` (user_id + name — must be STORE_MANAGER or above)
- `authorized_at`
- `reason` (text, required, max 500 chars)
- `part_description` — what specific part was taken (lens, temple, hinge, nose pad, etc.)
- `replacement_po_id` (nullable — linked PO for the replacement part)
- `expected_part_arrival_date` (nullable)
- `status`: `ACTIVE` (donor unit awaiting parts) → `PARTS_RECEIVED` → `RESTORED` (unit back to AVAILABLE) or `WRITTEN_OFF` (part never came, unit scrapped)
- `restored_at`, `restored_by`
- `written_off_at`, `written_off_by`, `write_off_reason`
- `created_at`

New fields on `stock_units` (extend existing doc):
- `status` — add enum values `CANNIBALIZED` and `AWAITING_PARTS` (existing field, extend the allowed set)
- `cannibalization_id` (nullable FK)
- `cannibalized_at` (nullable datetime)

New fields on `workshop_jobs` (extend existing doc):
- `cannibalization_ids: []` — array of cannibalization_ids where this job is the recipient
- `donor_job_ids: []` — array of cannibalization_ids where this job's unsold unit was the donor

## Backend
All endpoints added inside `backend/api/routers/workshop.py` (existing router, same prefix `/api/v1/workshop`):

- `POST /cannibalize` — Claim a cannibalization. Body: `{donor_stock_id, recipient_job_id, part_description, reason, expected_part_arrival_date?}`. Guards: donor unit must be AVAILABLE; recipient job must be IN_PROGRESS or PENDING; atomic `find_one_and_update` on stock_units (filter: status=AVAILABLE, _id=donor_stock_id) flips status to CANNIBALIZED; creates `cannibalization_ledger` doc; writes `stock_audit` row (source_type='CANNIBALIZATION'); optionally calls proposals.create() for a draft_po if no existing open PO covers this product; emits `cannibalization.created` event for TASKMASTER/SENTINEL; queues in-app notification to STORE_MANAGER.

- `GET /cannibalization-ledger` — List all active cannibalization records for a store (store-scoped). Filters: status, date range, recipient_job_id. Returns donor unit info, recipient job, days in AWAITING_PARTS state, linked PO status. Used by the new frontend ledger page.

- `GET /cannibalization-ledger/{cannibalization_id}` — Single record detail with full audit trail joined from stock_audit.

- `POST /cannibalization-ledger/{cannibalization_id}/restore` — Part has arrived; restore donor unit to AVAILABLE. Guards: status must be ACTIVE or PARTS_RECEIVED; atomic flip stock_units status back to AVAILABLE; clears cannibalization_id, cannibalized_at; updates ledger doc status=RESTORED; writes stock_audit CANNIBALIZATION_RESTORE row; notifies Store Manager.

- `POST /cannibalization-ledger/{cannibalization_id}/write-off` — Part will never arrive; declare donor unit scrapped. Body: `{write_off_reason}`. Flips stock_units status to SCRAPPED (existing status from inventory module); updates ledger status=WRITTEN_OFF; writes stock_audit row; creates an advisory tier-3 proposal for the financial write-off (mirrors existing write_off advisory type in proposals.py) so accountant sees it in Finance.

- Extend `GET /api/v1/inventory/barcode/{barcode}/trace` (inventory.py:532-595) — add a `cannibalization` section to the cross-collection join, so scanning a donor barcode shows its full life including cannibalization event and restoration.

## Frontend
New page `frontend/src/pages/workshop/CannibalizationLedgerPage.tsx`:
- Table view: donor SKU | donor barcode | part taken | recipient job | VIP customer | days waiting | linked PO status | status badge
- Status badges: ACTIVE (amber), PARTS_RECEIVED (blue), RESTORED (green), WRITTEN_OFF (red/muted)
- Row expand: shows authorized_by, reason, expected arrival date, audit trail
- "Mark Parts Received" button (STORE_MANAGER+): calls restore endpoint
- "Write Off" button (STORE_MANAGER+): confirmation modal with write_off_reason required
- Filter bar: status, date range (restrained, no multi-color decoration)
- Linked from WorkshopPage.tsx as a tab or sidebar link — not a standalone nav item

Extend `frontend/src/pages/workshop/WorkshopPage.tsx`:
- On job detail drawer: if `cannibalization_ids.length > 0`, show a "VIP Cannibalization" badge and link to the ledger record
- On job creation / IN_PROGRESS transition: surface a "Cannibalize a unit for this job" action button (STORE_MANAGER+ only)

Extend `frontend/src/pages/inventory/InventoryPage.tsx`:
- In the stock ledger table, units with status CANNIBALIZED or AWAITING_PARTS display a distinct badge and a link to the cannibalization ledger record — prevents staff from wondering why a unit is "missing" from AVAILABLE stock.

## Business rules
- Only AVAILABLE stock_units may be cannibalized (no reserving from already-RESERVED, TRANSFERRED, or SOLD units).
- Cannibalization requires explicit `authorized_by` from STORE_MANAGER or above — a SALES_STAFF cannot initiate it.
- `reason` field is mandatory, min 20 chars, max 500 chars — auditable justification required.
- A cannibalized unit cannot be sold, transferred, or reserved while status is CANNIBALIZED/AWAITING_PARTS — the claim is enforced at the DB filter level (same guard as transfers.py's AVAILABLE-only ship move).
- Restoration is the only valid exit from AWAITING_PARTS back to AVAILABLE. Write-off is the only valid exit to SCRAPPED. No other status transitions are permitted (forward-only state machine).
- Write-off must generate an advisory proposal (tier-3) for the accountant — financial write-off of inventory value is never auto-executed by the system.
- A unit's cannibalization history is immutable — ledger rows are never deleted or edited after creation; only status transitions (with timestamps and actor) are appended.
- If a replacement PO is auto-proposed (draft_po tier-1), the expected_part_arrival_date from the PO's `expected_date` should backfill onto the cannibalization ledger record.
- Maximum concurrent cannibalizations per store (to prevent abuse): a configurable cap stored in `business_settings` or `workshop_settings`; default suggested 5 — owner decides.

## RBAC
- `POST /cannibalize`: STORE_MANAGER, AREA_MANAGER, ADMIN, SUPERADMIN
- `GET /cannibalization-ledger` (list/detail): STORE_MANAGER, AREA_MANAGER, ADMIN, ACCOUNTANT, SUPERADMIN
- `POST /.../restore`: STORE_MANAGER, AREA_MANAGER, ADMIN, SUPERADMIN
- `POST /.../write-off`: STORE_MANAGER, AREA_MANAGER, ADMIN, SUPERADMIN (write-off advisory proposal visible to ACCOUNTANT in Finance proposals queue)
- WORKSHOP_STAFF, SALES_STAFF, OPTOMETRIST, CASHIER: read-only (can see cannibalization badge on job detail, cannot initiate or resolve)

## Integrations
- **Jarvis / TASKMASTER**: `cannibalization.created` event triggers TASKMASTER to check if the days-awaiting count exceeds a threshold (e.g., 14 days) and auto-escalate to AREA_MANAGER — reuses existing SLA escalation ladder.
- **ORACLE**: includes CANNIBALIZED/AWAITING_PARTS units in its low-stock anomaly scan so the AI narrative flags "3 units cannibalized this month in BV-Ranchi — pattern may indicate chronic lens shortage."
- **Proposals / draft_po**: cannibalize endpoint optionally creates a tier-1 reversible draft_po proposal for the replacement part — Superadmin reviews and approves → PO auto-drafts in the vendors router.
- **MSG91 / in-app notifications**: notify Store Manager at cannibalization creation (unit entered AWAITING_PARTS) and at restoration (unit returned to AVAILABLE). Reuses existing `notifications.py` + `agents/providers.py` send path with DISPATCH_MODE gating.
- **Tally**: write-off generates an advisory proposal that maps to a journal entry for inventory write-down — NEXUS nightly Tally export will pick it up once accountant processes the write-off in Finance (no new Tally integration work needed).

## Risk notes
- Stock integrity is the primary risk: the atomic `find_one_and_update` guard (filter: status=AVAILABLE) prevents double-cannibalization of the same unit, but must be tested under concurrent load. Pattern is identical to `vouchers.redeem_voucher_atomic` — safe if implemented correctly.
- The new `CANNIBALIZED` and `AWAITING_PARTS` statuses must be propagated everywhere `status` is read — stock ledger aggregation, low-stock queries, transfer ship guards, POS orderable-catalog queries. Missing one guard risks showing AWAITING_PARTS units as sellable.
- Write-off is an accounting event (reduces inventory asset value) — this must never auto-execute; the advisory tier-3 proposal gate is non-negotiable. Flag for accountant review in Finance module.
- No POS impact if guards are correct — but treat the new status values as requiring a feature flag (`CANNIBALIZATION_ENABLED=true`) on Railway so it can be disabled without a code deploy if an edge case surfaces in production.
- Workshop page is already revenue-critical-adjacent (CLAUDE.md: "Ask before touching POS") — this feature only adds endpoints/tabs to the workshop module and does not modify any POS or order creation paths.

## Recommendation
Build later (Phase 3, after core workshop and serial-number tracking are stable). The mechanical model is straightforward and maps cleanly onto existing patterns, but the multi-status stock propagation risk means it should ship in a dedicated PR with a feature flag, not bundled with other changes. ROI is moderate (3/5) — it solves a real VIP-service gap but affects a small subset of high-value jobs. Prioritize after the day-end settlement gap and invoice PDF generation, which block more daily workflows.

## Owner decisions
- Q: Should the maximum concurrent cannibalizations per store be enforced at all, and if so what is the cap? | Why: A cap prevents accidental abuse (e.g., staff cannibalizing stock freely without oversight) but may block legitimate urgent repairs if set too low. | Options: a) No cap, rely on authorization requirement alone / b) Soft cap with warning only (system warns but does not block) / c) Hard cap of 3 per store per month (blocks further cannibalizations until restored/written off)
- Q: Which item categories or brands are eligible for cannibalization? | Why: Allowing cannibalization of a ₹500 basic frame is very different from a ₹50,000 Cartier frame — the business risk and audit scrutiny should differ. | Options: a) All categories allowed equally / b) LUXURY and PREMIUM only (since VIP wait is most damaging there) / c) All categories but write-off advisory triggers only for LUXURY/PREMIUM
- Q: When a cannibalized unit is eventually restored to AVAILABLE, should it return to full-price sale or be automatically discounted/flagged as "repaired stock"? | Why: A unit that has been partially disassembled and reassembled may have different resale value or warranty status. | Options: a) Restore to AVAILABLE at full price with no flag / b) Restore to AVAILABLE but add a `refurbished` tag visible to staff on POS / c) Require Store Manager to manually set a new offer_price before restoring to AVAILABLE
- Q: Should the system auto-draft a replacement purchase order when a cannibalization is logged, or should that be a manual step? | Why: Auto-drafting the PO speeds up replenishment but may create duplicate POs if the Store Manager was already ordering the part. | Options: a) Always auto-draft a PO proposal (tier-1 reversible, Superadmin approves) / b) Only auto-draft if no open PO exists for that product from that vendor / c) Never auto-draft — flag for manual PO by Store Manager
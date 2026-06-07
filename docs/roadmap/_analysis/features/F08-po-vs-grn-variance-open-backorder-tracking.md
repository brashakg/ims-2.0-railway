# Feature #8: PO vs GRN Variance & Open Backorder Tracking
META: effort=M days=4 risk=MED roi=4 quickwin=no deps=none phase=3

## Existing overlap
IMS already has the core purchase plumbing this feature extends:

- **PO lifecycle** (`backend/api/routers/vendors.py:989-1041`): DRAFT → SENT → ACKNOWLEDGED → PARTIAL/PARTIALLY_RECEIVED → RECEIVED/CANCELLED. The PARTIALLY_RECEIVED status already exists but carries no formal backorder document.
- **GRN schema** (`vendors.py:1136-1509`): `total_received`, `total_accepted`, `total_rejected` per GRN; per-line `received_qty`, `accepted_qty`, `rejected_qty`; variance classification (`SHORT/OVER/EXACT/UNMATCHED`) via `grn_has_discrepancy()` (`vendors.py:206-236`).
- **GRN accept → stock minting** (`vendors.py:1309-1509`): idempotent, already stamps `source_type='GRN'` on `stock_units`; already transitions PO to PARTIALLY_RECEIVED when cumulative accepted < ordered.
- **Variance escalation** (`vendors.py:206-236`): already auto-creates a P2 SYSTEM task when `grn_has_discrepancy()` is true. The task creation hook can be extended.
- **PO cancel guard** (`vendors.py:1103-1116`): already blocks cancel if units have been received. The same guard must be tightened to account for backorder lines.
- **Frontend purchase pages**: `PurchaseOrderForm.tsx`, `GoodsReceiptNote.tsx`, `PurchaseInvoicesTab.tsx` are the extension points.

What is genuinely missing: a structured backorder record (remaining qty + expected date per PO line), a "dismiss line" flow with justification, and a backorder dashboard / age-tracking view.

## Reuse (extend, don't rebuild)
- `backend/api/routers/vendors.py` — extend `accept_grn()` to auto-create/update backorder lines; extend PO response schema to surface `backorder_lines[]`.
- `purchase_orders` collection — add `backorder_lines` subdocument array to existing PO docs (no new collection needed; see Data model).
- `grns` collection — already records accepted vs received qty; add `grn_id` back-reference on each backorder line.
- `backend/api/services/purchase_match.py` — extend `three_way_match()` to include backorder qty in the match verdict (invoiced qty vs accepted qty vs outstanding backorder).
- `vendor_bills` / `purchase_invoices.py` — backorder qty visible on the 3-way match detail so accountant is not confused by ordered-vs-accepted gap.
- `audit_logs` collection — all dismiss/update operations write here; re-use existing `action + entity_type + before_state + after_state` pattern.
- Frontend `PurchaseOrderForm.tsx` — extend to render per-line backorder status badge and outstanding qty.
- Frontend `GoodsReceiptNote.tsx` — extend to show "previously received" qty and auto-compute remaining backorder on this GRN.
- TASKMASTER `_escalate_overdue_tasks()` pattern — add a parallel `_flag_aged_backorders()` sweep (backorder lines older than threshold → P1 task).

## Data model
New subdocument array on existing `purchase_orders` docs — no separate collection:

```
purchase_orders.backorder_lines[]:
  line_id         string   (uuid, stable per PO line)
  product_id      string
  sku             string
  ordered_qty     int
  received_qty    int      (cumulative across all GRNs)
  backorder_qty   int      (ordered_qty - received_qty; recomputed on each GRN accept)
  status          enum     OPEN | DISMISSED | FULFILLED
  expected_date   date     (vendor's committed delivery date; nullable)
  last_grn_id     string   (most recent GRN that touched this line)
  created_at      datetime
  updated_at      datetime
  dismissed_at    datetime (nullable)
  dismissed_by    string   user_id
  dismiss_reason  string   (required when status → DISMISSED; ≤ 500 chars)
```

PO-level computed field (not stored, derived on read):
```
has_open_backorder   bool    (any backorder_line.status == OPEN and backorder_qty > 0)
```

PO status machine addition: PO remains PARTIALLY_RECEIVED until all `backorder_lines` are FULFILLED or DISMISSED, then auto-transitions to RECEIVED.

New field on `grns`:
```
grns.lines[].backorder_line_id   string   (back-reference to the PO backorder_line_id this GRN line closes/reduces)
```

## Backend
All changes inside `backend/api/routers/vendors.py` and `backend/api/services/purchase_match.py`:

- **`accept_grn()` extension** — after minting stock units, iterate each GRN line; find the matching `backorder_line` by `product_id`; decrement `backorder_qty` by `accepted_qty`; flip `status` to FULFILLED when `backorder_qty` reaches 0; create or upsert the `backorder_line` on first short-shipment. Re-evaluate PO status: if all lines are FULFILLED or DISMISSED, transition to RECEIVED.
- **`PATCH /purchase-orders/{po_id}/backorder-lines/{line_id}` (new)** — allows ADMIN/AREA_MANAGER/ACCOUNTANT to set `expected_date` (vendor promise update) or dismiss a line (`status → DISMISSED`, `dismiss_reason` required, `dismissed_by` stamped, `dismissed_at` stamped). Writes immutable `audit_logs` entry with `before_state` (backorder_qty, expected_date) and `after_state`. After dismiss, re-evaluates PO status.
- **`GET /purchase-orders` extension** — add optional query param `has_open_backorder=true` to filter to POs with outstanding lines; add `backorder_summary` to list response (`{open_lines, total_backorder_qty, oldest_line_age_days}`).
- **`GET /purchase-orders/{po_id}` extension** — include full `backorder_lines[]` array in PO detail response.
- **`GET /purchase-orders/backorders` (new)** — dedicated backorder dashboard endpoint: returns all OPEN backorder lines across all POs, enriched with `vendor_name`, `product_name`, `sku`, `days_open` (today - created_at), `expected_date`. Supports filters: `vendor_id`, `store_id`, `days_open_gt`. Role-gated (see RBAC).
- **`three_way_match()` extension** (`purchase_match.py`) — add `backorder_qty` to the per-line match detail; mark a line `PARTIALLY_MATCHED` when invoice billed qty > accepted qty but backorder is still OPEN (not an exception, expected behavior). Prevent false ON_HOLD_EXCEPTION for short-shipped lines that are legitimately in backorder.
- **TASKMASTER hook** (`taskmaster.py`) — add `_flag_aged_backorders()` to the 5-minute tick: query `purchase_orders` for backorder lines with `status=OPEN` and `created_at < (now - backorder_alert_days)`; create a P2 SYSTEM task "Backorder aged X days — follow up with vendor" if no task already exists for this `line_id` (de-duplicate by `entity_id=line_id`).

## Frontend
All in `frontend/src/pages/purchase/`:

- **`PurchaseOrderForm.tsx` extension** — add a "Backorder" section below the line items table (only visible when PO status is PARTIALLY_RECEIVED). Per-line row: product name / SKU / ordered / received / outstanding qty / status badge (OPEN=amber, FULFILLED=green, DISMISSED=gray) / expected date (inline editable by ADMIN/AREA_MANAGER/ACCOUNTANT) / "Dismiss line" button (opens confirmation modal requiring reason text ≤ 500 chars). No new page; folds naturally into existing PO detail view.
- **`GoodsReceiptNote.tsx` extension** — on the GRN creation form, show a "Previously received" column per line (fetched from current `backorder_lines[].received_qty`) so the receiving clerk sees at a glance how much is still outstanding before entering today's qty.
- **New `BackorderDashboardTab.tsx`** — new tab on the existing `PurchasePage` (which already has PO list, GRN list, Invoice tabs). Shows a flat table of all OPEN backorder lines across all vendors: Vendor / PO# / SKU / Ordered / Received / Outstanding / Age (days) / Expected Date / Actions (update date, dismiss). Sortable by age descending (oldest first). Amber row highlight when age > owner-configured threshold. ADMIN/AREA_MANAGER/ACCOUNTANT see all stores; STORE_MANAGER sees their store's lines only.
- **PO list badge** — add a small amber "Backorder" chip to PO list rows where `has_open_backorder=true`, same pattern as existing status chips. No new component needed.
- Restrained light-only: amber `bg-amber-50 text-amber-800 border-amber-200` for OPEN backorders; standard green/gray for FULFILLED/DISMISSED. No gradients, no icons beyond existing pattern.

## Business rules
- A GRN accepted qty can never exceed PO ordered qty for that line (already enforced; backorder_qty floor is 0).
- Dismiss requires a written reason (≤ 500 chars, non-empty after trim). No silent line closure.
- Once DISMISSED, a backorder line cannot be re-opened. If vendor later delivers the dismissed qty, a new GRN must be raised against a new PO (or an amendment workflow — see Owner decisions).
- PO auto-transitions to RECEIVED only when every line is FULFILLED or DISMISSED. Mixed state (some lines OPEN) keeps PO at PARTIALLY_RECEIVED.
- Dismissing the last open line writes one consolidated `audit_logs` entry with `action: PO_CLOSED_VIA_DISMISS` so the accountant can verify no ghost inventory entered.
- `backorder_qty` is recomputed from `ordered_qty - received_qty` on every GRN accept (not a user-editable field). Only `expected_date` and `status` (via dismiss) are user-modifiable.
- TASKMASTER aged-backorder alert threshold: configurable per-store in `agent_config` (`backorder_alert_days`, default 14). TASKMASTER does not auto-dismiss; it only creates a task. Human must dismiss.
- The 3-way match must NOT raise an ON_HOLD_EXCEPTION for a line where invoice qty = accepted qty (even though invoice qty < ordered qty), provided a backorder is OPEN. Exception only fires if invoice qty > accepted qty with no open backorder to explain the gap.

## RBAC
- **SUPERADMIN / ADMIN**: full read + write (dismiss, update expected date, view all stores).
- **AREA_MANAGER**: full read + write for their stores.
- **STORE_MANAGER**: read + dismiss/update expected date for their store's PO lines.
- **ACCOUNTANT**: read-only backorder lines (needed for 3-way match reconciliation); cannot dismiss.
- **CATALOG_MANAGER / all other roles**: no access to purchase module (existing gate preserved).
- Backorder dashboard endpoint gated: `require_roles(SUPERADMIN, ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT)`.
- Dismiss endpoint gated: `require_roles(SUPERADMIN, ADMIN, AREA_MANAGER, STORE_MANAGER)`.

## Integrations
- **TASKMASTER agent** — `_flag_aged_backorders()` sweep on the existing 5-minute tick. No new agent.
- **Tally / NEXUS** — no change. Tally JV exports use accepted GRN qty as cost basis; backorder lines (not yet received) do not enter accounting. NEXUS Tally nightly export is unaffected.
- **MSG91 / WhatsApp (MEGAPHONE)** — not triggered by backorders (staff-facing task is sufficient; no customer communication).
- **Shopify / Razorpay / Shiprocket** — none.

## Risk notes
- **PO status integrity**: The auto-transition PARTIALLY_RECEIVED → RECEIVED on last-dismiss must be atomic. Use a `find_one_and_update` with a filter that checks all `backorder_lines` are non-OPEN rather than a read-modify-write to avoid race with a concurrent GRN accept. This is the only money-adjacent risk.
- **3-way match logic change**: Modifying `three_way_match()` in `purchase_match.py` affects existing invoice matching behavior. Existing matched invoices are unaffected (stored verdict is not recomputed). Only new invoices booked after the deploy pick up the updated logic. Low regression risk if the change is gated behind an `if backorder_line is not None` branch.
- **Dismiss is irreversible by design**: Closing a PO via dismiss means those units will never appear in IMS stock. If the vendor later delivers them anyway, there is no PO to receive against. This is intentional (no ghost inventory), but the owner must decide what the receiving clerk does in that scenario (see Owner decisions).
- **No POS/payment touch**: This feature has zero contact with POS, order creation, payments, or customer-facing flows. Feature flag not required; it can ship as a straightforward PARTIALLY_RECEIVED-state enhancement behind ADMIN/AREA_MANAGER roles.
- **Backfill for existing PARTIALLY_RECEIVED POs**: Any PO already in PARTIALLY_RECEIVED status at deploy time has no `backorder_lines` array. A one-time migration script must synthesize backorder lines from the existing GRN accept history (query `grns` by `po_id`, compute cumulative accepted per product, populate `backorder_lines`). This is read-only data reconstruction — no stock or accounting change.

## Recommendation
Build now. This is a 4-day medium effort that closes a genuine operational gap (inventory ordered but never tracked as outstanding). The PO lifecycle, GRN variance classification, and PARTIALLY_RECEIVED status already exist — this is a structured formalization of data the system implicitly has but does not surface. ROI is high for any store that places multi-SKU orders with vendors who routinely short-ship (common in Indian optical imports). Fold the aged-backorder alert into the existing TASKMASTER tick rather than any new scheduler.

## Owner decisions
- Q: What is the default number of days before an open backorder line triggers a staff task? | Why: Controls how aggressively TASKMASTER nags the team — too short and it's noise, too long and lines go unnoticed. | Options: 7 days / 14 days (recommended default) / 30 days / per-vendor configurable
- Q: If a vendor delivers dismissed backorder qty without a matching PO, what should the receiving clerk do? | Why: Determines whether to build a "receive against no-PO" emergency GRN flow or simply instruct staff to reject/return the goods at the door. | Options: (a) Reject at door — no system change needed / (b) Allow a no-PO emergency GRN (requires an additional receiving path) / (c) Treat as a vendor return and issue a debit note
- Q: Should backorder expected dates be communicated to store managers automatically when a vendor updates them via the vendor portal? | Why: If yes, the vendor portal `POST /{token_id}/jobs/{job_id}/status` pattern needs a parallel `PATCH /vendor-portal/{token}/po-backorders/{line_id}/expected-date` endpoint so labs/vendors can self-update without calling the store. | Options: Yes, expose a vendor-portal endpoint for date updates / No, store manager manually updates after a phone call
- Q: Should a dismissed backorder line reduce the outstanding AP liability on the vendor ledger (i.e., reduce PO value for AP purposes)? | Why: If the 20 dismissed units were included in a vendor invoice that was already booked, the accountant needs a debit note for the over-billed amount. If no invoice was booked yet, no AP action is needed. | Options: (a) Prompt accountant to raise a debit note when dismissing a line that has an associated OUTSTANDING or PARTIAL invoice / (b) Advisory only — show a warning but take no automatic AP action / (c) Out of scope for this feature; accountant handles manually
# Feature #21: "Defective Quarantine" Barcoding
META: effort=S days=2 risk=LOW roi=3 quickwin=yes deps=none phase=1

## Existing overlap
- `stock_units` collection already has `status` field with DAMAGED as a valid value (`backend/api/routers/inventory.py:88`, `backend/database/connection.py:238-243`). DAMAGED status is referenced in the returns restock engine (`backend/api/routers/returns.py:768-911 _restock_good_items`) — GOOD units go to AVAILABLE, implicitly leaving DAMAGED units un-restocked.
- `backend/api/routers/labels.py` is the existing label/sticker print infrastructure (QZ Tray signing, workshop scan-to-advance stage pipeline). The `generate_barcode()` helper at `inventory.py:88-91` already mints `"STO-UUID[:8]"` barcodes per unit.
- Vendor returns (`backend/api/routers/vendor_returns.py`) have a DRAFT→APPROVED→SHIPPED→RECEIVED_BY_VENDOR→CREDIT_ISSUED/REPLACED lifecycle — the RTV flow exists but has no physical label step.
- `backend/api/routers/transfers.py` tracks `quantity_damaged` on GRN receives (`line 88-92`) but does not trigger any label or quarantine action.
- The workshop QC path (`workshop.py:1026-1195`) already distinguishes QC_FAILED (rework) from a CANCELLED/scrapped unit, but workshop units are lenses, not POS retail items.

## Reuse (extend, don't rebuild)
- `stock_units` collection — add `quarantine_reason`, `quarantine_at`, `quarantine_by`, `rtv_vendor_id`, `quarantine_label_printed` fields on the existing doc rather than a new collection.
- `backend/api/routers/labels.py` — add a `POST /api/v1/labels/quarantine/{stock_id}` endpoint here, following the same QZ Tray signing pattern already used for workshop job-card stickers.
- `backend/api/routers/inventory.py` — extend the existing stock-status PATCH path (the stock-add / status-update flow around line 750) to accept `status=QUARANTINED` and record reason + actor.
- `backend/api/routers/vendor_returns.py` — add an optional `stock_ids[]` field to VendorReturnCreate so the quarantine label's embedded stock_id links directly to the open RTV.
- `audit_logs` collection — reuse the existing immutable audit trail (same pattern as workshop QC waivers: action, entity_type=STOCK_UNIT, before/after state, user_id, timestamp).
- `frontend/src/pages/inventory/InventoryPage.tsx` — extend the existing stock ledger with a "Quarantine" action button per serialized unit row, gated to STORE_MANAGER+.
- `frontend/src/components/inventory/SerialNumberTracker.tsx` — add a "Mark Quarantine" action in the serial detail modal for high-value items (watches).

## Data model
New fields on existing `stock_units` documents (no new collection needed):
- `status` — extend the existing enum to include `QUARANTINED` alongside AVAILABLE/RESERVED/TRANSFERRED/SOLD/DAMAGED/SCRAPPED. (QUARANTINED is a superset of DAMAGED — it means DAMAGED + physically labeled + pending RTV.)
- `quarantine_reason` — string, one of: `DEFECTIVE`, `SCRATCHED`, `CUSTOMER_RETURN_DAMAGED`, `QC_FAILED_WORKSHOP`, `RECEIVED_DAMAGED` (from GRN), `OTHER`.
- `quarantine_at` — datetime, auto-stamped.
- `quarantine_by` — user_id, auto-stamped from JWT.
- `quarantine_by_name` — denormalized string for display.
- `rtv_vendor_id` — optional, links to `vendors` collection if RTV is already known at quarantine time.
- `quarantine_label_printed` — bool, default false; flipped to true when the label endpoint is called. Prevents accidental re-shelving before label is printed.
- `quarantine_notes` — free-text, max 200 chars.

No new collection. The quarantine ledger is the `audit_logs` collection (same pattern as QC waivers).

## Backend

**`PATCH /api/v1/inventory/stock/{stock_id}/quarantine`** (extend `inventory.py`)
- Roles: STORE_MANAGER, AREA_MANAGER, ADMIN, SUPERADMIN.
- Body: `{quarantine_reason, rtv_vendor_id?, quarantine_notes?}`.
- Guards: unit must be AVAILABLE or DAMAGED (cannot quarantine an already-SOLD or TRANSFERRED unit). Period-lock check via `finance.check_period_locked` (same guard used on orders and returns).
- Sets `status=QUARANTINED`, stamps `quarantine_at/by/by_name`, sets `quarantine_label_printed=false`.
- Writes immutable `audit_logs` entry: `{action: "STOCK_QUARANTINED", entity_type: "STOCK_UNIT", entity_id: stock_id, before_state: {status: "AVAILABLE"}, after_state: {status: "QUARANTINED", quarantine_reason}, user_id, store_id, timestamp}`.
- Returns the updated stock_unit doc.

**`POST /api/v1/labels/quarantine/{stock_id}`** (extend `labels.py`)
- Roles: STORE_MANAGER, AREA_MANAGER, ADMIN, SUPERADMIN.
- Fetches stock_unit + product name + brand + barcode + quarantine_reason + quarantine_at.
- Builds a QZ Tray label payload: bright-red background (inline CSS in the HTML/ZPL template), large "QUARANTINE — DO NOT SHELVE" header, "RTV Pending" sub-header, product name + brand, stock_id barcode (Code128), quarantine reason + date, store name.
- Flips `quarantine_label_printed=true` on the stock_unit doc.
- Writes `audit_logs` entry: `{action: "QUARANTINE_LABEL_PRINTED", ...}`.
- Returns the label payload (same structure as existing workshop label responses).

**`GET /api/v1/inventory/stock/quarantined`** (extend `inventory.py`)
- Roles: STORE_MANAGER+. Store-scoped (non-HQ sees own store only).
- Query params: `store_id`, `rtv_vendor_id`, `label_printed` (bool filter), `date_from/to`.
- Aggregates `stock_units` where `status=QUARANTINED`, joins product name/brand/category.
- Returns list with `quarantine_label_printed` flag so manager can see unlabeled units.

**Extend `vendor_returns.py` VendorReturnCreate schema**
- Add optional `stock_ids: list[str]` field. When supplied, those stock_unit docs get `rtv_vendor_id` backfilled and a `audit_logs` row linking the return.

## Frontend

**`frontend/src/pages/inventory/InventoryPage.tsx`** — extend the serialized stock ledger tab:
- Add "Mark Quarantine" button per unit row (visible to STORE_MANAGER+, hidden for SOLD/TRANSFERRED units).
- Opens a small modal: reason dropdown (DEFECTIVE / SCRATCHED / CUSTOMER_RETURN_DAMAGED / RECEIVED_DAMAGED / OTHER), optional vendor selector (pre-populated from `vendors` list), notes field (max 200 chars), "Mark + Print Label" CTA.
- On confirm: calls PATCH quarantine, then immediately calls POST label — label opens browser print dialog via QZ Tray (same pattern as workshop labels).
- Show a "QUARANTINED" status chip (red) in the status column alongside existing AVAILABLE/SOLD chips.

**`frontend/src/components/inventory/SerialNumberTracker.tsx`** — extend the serial detail modal:
- Add "Mark Quarantine" action button (same flow as above, for high-value serialized items like watches).
- Show quarantine metadata (reason, date, who marked it) when status is QUARANTINED.

**New tab: Quarantine Queue** — add a lightweight tab to `InventoryPage.tsx` (alongside existing Low-Stock tab):
- Lists all `status=QUARANTINED` units for the store.
- Red dot badge on tab shows count of unlabeled quarantine units (label_printed=false).
- Columns: product name, brand, stock_id/barcode, quarantine reason, date, labeled? (yes/no with "Print" CTA if no), RTV vendor (if set).
- "Create RTV" button per row that pre-fills `VendorReturnCreate` with the stock_id (links to existing vendor returns flow).

## Business rules
- A QUARANTINED unit is invisible to POS product search and cannot be added to a cart (POS catalog query already filters `status=AVAILABLE` — QUARANTINED units simply never appear).
- A QUARANTINED unit cannot be transferred to another store until the quarantine is lifted or an RTV is created (guard in `transfers.py _apply_ship_stock_move`: reject QUARANTINED units).
- Only STORE_MANAGER, AREA_MANAGER, ADMIN, SUPERADMIN can mark a unit quarantined or lift quarantine. Sales staff and cashiers cannot.
- Quarantine is reversible (manager can set `status` back to AVAILABLE with an audit reason) — for mis-quarantine correction — but lifting quarantine requires an explicit `PATCH /stock/{id}/lift-quarantine` with a mandatory `lift_reason` (audited).
- Label must be printed before quarantine can be "acknowledged" in the queue (system flags unlabeled units; manager must print before closing the queue item).
- Audit trail is immutable — every status transition (AVAILABLE→QUARANTINED, QUARANTINED→AVAILABLE, QUARANTINED→SCRAPPED) writes an `audit_logs` row.
- Quarantine does not automatically trigger an RTV or debit note — it is a physical control step; the vendor return is a separate business action.

## RBAC
| Role | Can mark quarantine | Can print label | Can lift quarantine | Can view queue | Can link RTV |
|---|---|---|---|---|---|
| SUPERADMIN | Yes | Yes | Yes | Yes | Yes |
| ADMIN | Yes | Yes | Yes | Yes | Yes |
| AREA_MANAGER | Yes | Yes | Yes | Yes | Yes |
| STORE_MANAGER | Yes | Yes | Yes | Own store | Yes |
| ACCOUNTANT | No | No | No | Read-only | No |
| CATALOG_MANAGER | No | No | No | No | No |
| SALES_CASHIER / SALES_STAFF / CASHIER | No | No | No | No | No |
| OPTOMETRIST / WORKSHOP_STAFF / DESIGN_MANAGER | No | No | No | No | No |

## Integrations
- **QZ Tray** (existing, `labels.py`): label print. No new integration needed — reuse the existing signing + payload pattern.
- **Jarvis / TASKMASTER**: optionally, emit a `stock.quarantined` event via the existing event bus (`registry.dispatch_event`) so TASKMASTER can auto-create a follow-up task ("Create RTV for quarantined unit") if the unit stays unlabeled or unlinked to a vendor return for more than N days. This is a soft enhancement, not required for v1.
- No Shopify, Razorpay, MSG91, or Tally integration needed for this feature. If the unit is quarantined and was part of an online-sold order, that return flow is already handled by `returns.py` independently.

## Risk notes
- **POS safety**: The POS catalog filter (`status=AVAILABLE`) already excludes non-AVAILABLE units — no POS code change needed. Risk of regression is near-zero.
- **Existing DAMAGED units**: The codebase has `DAMAGED` as a status but it is not consistently used (returns.py marks units as AVAILABLE or leaves them; transfers.py captures `quantity_damaged` count but does not flip status). Adding QUARANTINED as a distinct status (rather than overloading DAMAGED) avoids breaking existing DAMAGED logic.
- **Label printer dependency**: Feature requires QZ Tray installed on the store PC and a compatible label printer. If QZ Tray is not set up, the quarantine marking still works (status flips, audit logged) but the label cannot print — the queue will show the unit as "unlabeled." This is acceptable and self-healing (manager prints when printer is available).
- **No financial impact**: Quarantine is a physical/status control only. It does not create a journal entry, affect COGS, or trigger a debit note — those happen only when a vendor return is formally created. Zero accounting risk.
- **No feature flag needed**: This feature touches no POS payment path, no pricing logic, and no order creation. It can ship without a feature flag.

## Recommendation
Build now (quick win) — 2-day effort, zero POS/money/accounting risk, directly prevents the described loss (defective luxury item re-shelved and re-sold). Reuses 90% of existing infrastructure (stock_units status, labels.py QZ Tray, audit_logs, vendor_returns). High operational value for the optical chain context where a scratched Cartier frame or a mismounted lens could cause a customer complaint or warranty dispute.

## Owner decisions
- Q: Which quarantine reasons should appear in the dropdown? | Why: Determines what gets printed on the sticker and how the RTV reason maps to vendor debit notes. | Options: (a) DEFECTIVE / SCRATCHED / CUSTOMER_RETURN_DAMAGED / RECEIVED_DAMAGED / QC_FAILED_WORKSHOP / OTHER — covers all current flows; (b) owner adds/removes specific reasons (e.g., separate "LENS_POWER_MISMATCH" for optical errors); (c) free-text only (simpler but loses aggregation capability for reports)
- Q: Should QUARANTINED units auto-appear in Tally as a write-down or only when an RTV / debit note is formally created? | Why: If write-down at quarantine time, the P&L impact is immediate; if only on RTV creation, there is a lag between physical quarantine and book entry. | Options: (a) No Tally entry at quarantine — only when vendor return is formally booked (recommended, matches current practice and avoids premature P&L hit); (b) Raise a provision entry at quarantine time (requires accountant sign-off flow)
- Q: After how many days should an unlabeled or RTV-unlinked quarantine unit trigger a TASKMASTER follow-up task? | Why: Sets the SLA for clearing quarantine queue; too short creates noise, too long defeats the purpose. | Options: (a) 3 days; (b) 7 days; (c) 14 days; (d) disable auto-task (manager monitors queue manually)
- Q: Should lifting a quarantine (putting a unit back to AVAILABLE) require a second approver (area manager or above), or is store manager self-approval sufficient? | Why: A second approver adds a control point for luxury brands but adds friction. | Options: (a) Store manager can lift quarantine unilaterally (audit logged); (b) Require AREA_MANAGER or above to approve lift (stricter for luxury items)
- Q: Should the quarantine label format differ for luxury brands (Cartier, Chopard, Bvlgari, Gucci, etc.) versus standard items — e.g., a different color or additional "BRAND AUTHORIZATION REQUIRED" line? | Why: Some luxury brand agreements require brand-authorized inspection before RTV — the label could prompt store staff. | Options: (a) Uniform red label for all brands; (b) Add an extra "BRAND AUTH REQUIRED" line for the luxury brand list already defined in pricing_caps (Cartier/Chopard/Bvlgari/Gucci/Prada/Versace/Burberry)
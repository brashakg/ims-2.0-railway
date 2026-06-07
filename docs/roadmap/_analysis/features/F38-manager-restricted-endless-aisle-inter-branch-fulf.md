# Feature #38: Manager-Restricted "Endless Aisle" Inter-Branch Fulfillment
META: effort=M days=6 risk=MED roi=4 quickwin=no deps=none phase=3

## Existing overlap
IMS already has substantial overlap across multiple subsystems:

**Stock Transfers (backend/api/routers/transfers.py):** Full transfer lifecycle (DRAFT → PENDING_APPROVAL → APPROVED → IN_TRANSIT → RECEIVED → COMPLETED) with ship/receive stock movement, `_apply_ship_stock_move`, `_apply_receive_stock_move`, and `stock_audit` trail. Self-transfer guard exists. Priority, expected_date, tracking_number, shipping_cost fields already on transfer schema.

**Cross-store inventory visibility (backend/api/routers/inventory.py):** Per-store ledger (`_build_store_ledger`), `find_low_stock` aggregation, and `StockRepository.find_by_product_and_store` all exist. The barcode lifecycle trace (`GET /barcode/{barcode}/trace`) already does cross-collection joins.

**Orders with delivery scheduling (frontend/src/stores/posStore.ts, backend/api/routers/orders.py):** `delivery_time_slot`, `delivery_priority` (NORMAL/EXPRESS/URGENT), `delivery_date` captured on POS orders. `OrderCreate` schema has these fields persisted.

**Shipping integration (backend/api/routers/shipping.py, backend/api/services/shiprocket.py):** Shiprocket book/track already wired. `POST /shipments` merges customer address and books AWB. DISPATCH_MODE gated.

**Discount/approval gates (backend/api/routers/orders.py:1195-1312):** `discount_approved_by`, `discount_reason`, `require-approval` on ₹0/100%-discount patterns already established. Approval capture pattern is reusable.

**RBAC enforcement (backend/api/services/rbac_policy.py, backend/api/middleware/rbac_enforcement.py):** STORE_MANAGER, AREA_MANAGER, ADMIN, SUPERADMIN roles defined. `validate_store_access` store-scoping pattern exists across all routers.

**Proposals/audit (backend/agents/proposals.py, collections: ai_proposals, agent_audit_log):** Before/after state capture, immutable audit_logs entries, tier-2 ask-confirm pattern (inter_store_transfer_suggestion already in the reversible whitelist at proposals.py:78).

**In-app notifications (backend/api/routers/notifications.py):** Bell notification queuing to specific user_ids already works. `notify_escalation` pattern in taskmaster.py:193-195 is the template.

## Reuse (extend, don't rebuild)
- **`backend/api/routers/transfers.py`** — extend to add an `ENDLESS_AISLE` transfer_type enum value and a new `fulfillment_order_id` linking field; reuse the existing ship/receive/audit pipeline entirely
- **`backend/api/routers/inventory.py`** — extend `GET /inventory/ledger` or add a thin `GET /inventory/cross-store-availability?product_id=&exclude_store_id=` endpoint using the existing `_build_store_ledger` aggregation scoped to other stores
- **`backend/api/routers/orders.py`** — extend `OrderCreate` with `fulfillment_store_id` (the branch that will ship) and `fulfillment_transfer_id` (link back to the created transfer); no new order schema needed beyond these two fields
- **`backend/api/routers/shipping.py` + `backend/api/services/shiprocket.py`** — reuse `POST /shipments` directly; the fulfilling store's manager books the AWB using customer delivery address already on the order
- **`frontend/src/stores/posStore.ts`** — extend the POS cart with an `endlessAisle` flag and `fulfillmentStoreId` field; these flow through to `OrderCreate`
- **`backend/api/routers/notifications.py`** — reuse bell notification queuing to notify the fulfilling store's manager that a request has arrived
- **`collections: stock_transfers, orders, audit_logs, notifications`** — all extend, nothing new minted

## Data model
New fields on **existing** `orders` collection (no new collection needed):
- `fulfillment_mode: "ENDLESS_AISLE" | null` — marks this order as cross-branch
- `fulfillment_store_id: string` — the branch that holds stock and will ship
- `fulfillment_transfer_id: string` — FK to the `stock_transfers` doc created to move the unit
- `fulfillment_status: "PENDING" | "TRANSFER_CREATED" | "SHIPPED" | "DELIVERED"` — denormalized for POS display
- `fulfillment_requested_by: string` — user_id of the manager who initiated
- `fulfillment_approved_by: string | null` — user_id of approver at fulfilling branch (if owner chooses 2-store approval; see owner decisions)
- `fulfillment_notes: string` — reason/customer-facing notes (capped 300 chars)

New `transfer_type` enum value on **existing** `stock_transfers` collection:
- `"ENDLESS_AISLE"` — signals this transfer was auto-created from a cross-branch sale; links back via `source_order_id` field (add this field)

No new collections required.

## Backend

**`GET /api/v1/inventory/cross-store-availability`** (new, in `inventory.py`)
- Params: `product_id`, `exclude_store_id` (the selling store), optional `min_qty=1`
- Runs `_build_store_ledger` aggregation across all stores except the selling store; returns list of `{store_id, store_name, available_qty}` sorted by qty desc
- Role gate: STORE_MANAGER, AREA_MANAGER, ADMIN, SUPERADMIN
- Store-scoped: AREA_MANAGER sees only their managed stores; ADMIN/SUPERADMIN see all

**`POST /api/v1/orders/{order_id}/endless-aisle-fulfillment`** (new, in `orders.py`)
- Body: `{fulfillment_store_id, fulfillment_notes}`
- Validates: order exists and belongs to caller's store; caller is STORE_MANAGER+; order is in CONFIRMED/PROCESSING status; `fulfillment_mode` not already set; fulfilling store actually has `available_qty >= 1` for every line item (re-checks live, not cached)
- Creates a `stock_transfers` doc of type `ENDLESS_AISLE` from fulfilling store to customer address (uses existing `create_transfer` logic, sets `to_location_id` to a virtual "CUSTOMER_DELIVERY" sentinel or the selling store as waypoint — see owner decisions)
- Updates order: sets `fulfillment_mode`, `fulfillment_store_id`, `fulfillment_transfer_id`, `fulfillment_status="TRANSFER_CREATED"`, stamps `fulfillment_requested_by`
- Queues in-app bell notification to all STORE_MANAGER users at the fulfilling store
- Writes immutable `audit_logs` entry: action=`ENDLESS_AISLE_REQUESTED`, before_state (order snapshot), after_state
- Period-lock check: reuses `check_period_locked` (finance.py:446) — cannot initiate on a locked period

**`PATCH /api/v1/orders/{order_id}/endless-aisle-fulfillment/status`** (new, in `orders.py`)
- Body: `{fulfillment_status, tracking_number?, notes?}`
- Called by the fulfilling store's manager to advance status (TRANSFER_CREATED → SHIPPED → DELIVERED)
- On SHIPPED: validates `tracking_number` present; calls existing `POST /shipments` internally to book Shiprocket AWB against customer delivery address; stamps AWB on both the order and the transfer doc
- On DELIVERED: flips order `payment_status` if it was CREDIT (outstanding); writes audit entry
- Role gate at fulfilling store: STORE_MANAGER, AREA_MANAGER, ADMIN, SUPERADMIN

**Extend `POST /api/v1/orders` (existing `orders.py` `create_order`):**
- Accept optional `fulfillment_store_id` in `OrderCreate`; if present, validate caller is STORE_MANAGER+, validate fulfilling store has stock (same cross-store-availability check), set `fulfillment_mode="ENDLESS_AISLE"` on the doc at creation time
- Payment is collected at the selling store (normal POS tender flow) — no change to payment logic

## Frontend

**`frontend/src/components/pos/EndlessAislePanel.tsx`** (new component, shown inside POS Review step)
- Shown only when current user's role is STORE_MANAGER+ AND at least one cart item is out of stock at the current store
- Displays a table: each out-of-stock SKU → list of other branches with available qty (calls `GET /inventory/cross-store-availability`)
- Manager selects a single fulfilling branch from a dropdown (only branches that can cover ALL out-of-stock items shown; partial coverage flagged clearly)
- Text field for fulfillment notes (customer context, VIP reason)
- Prominent warning banner: "Payment collected here. Fulfillment from [Branch]. Shipment to customer address." No ambiguity.
- Restrained UI: neutral gray card, single bv-red accent on the branch selector confirm button; no icons

**`frontend/src/stores/posStore.ts`** (extend)
- Add `endlessAisle: { enabled: boolean; fulfillmentStoreId: string | null; fulfillmentNotes: string }` to store state
- `setEndlessAisle(storeId, notes)` action; clears on cart reset
- `getOrderPayload()` includes `fulfillment_store_id` when `endlessAisle.enabled`

**`frontend/src/pages/orders/OrdersPage.tsx`** (extend existing)
- Add "Endless Aisle" filter chip to order list (filters by `fulfillment_mode="ENDLESS_AISLE"`)
- On order detail expand: show fulfillment status badge (PENDING / TRANSFER_CREATED / SHIPPED / DELIVERED), fulfilling branch name, tracking number when available
- "Mark Shipped" and "Mark Delivered" action buttons visible only to STORE_MANAGER+ users of the fulfilling store; open a small inline form (tracking number input on SHIPPED)

**`frontend/src/pages/inventory/InventoryPage.tsx`** (extend existing, new tab)
- Add "Endless Aisle Orders" sub-tab visible to STORE_MANAGER+
- Lists all ENDLESS_AISLE orders where `fulfillment_store_id` matches the user's active store
- Columns: Order #, Selling Branch, Customer, Items, Status, Expected Date, Action buttons
- Source: filtered query on existing orders list endpoint with `fulfillment_mode=ENDLESS_AISLE&fulfillment_store_id={active_store}`

## Business rules
- **Role hard-lock:** Only STORE_MANAGER, AREA_MANAGER, ADMIN, SUPERADMIN can initiate. SALES_STAFF, SALES_CASHIER, CASHIER cannot see or trigger the panel. Enforced server-side, not just UI.
- **Stock re-validation at submit:** Cross-store availability is re-checked at the moment the fulfillment request is submitted (not just when the panel renders). If stock was sold out between panel-open and submit → 409 Conflict with clear message.
- **Payment is always at selling store:** The order grand_total and tender are captured at Store A using the existing POS payment flow. No split payment, no deferred payment. The selling store is responsible for the full amount.
- **Fulfillment is always to customer's delivery address:** The transfer destination is the customer's saved delivery address (order.delivery_address). It is never store-to-store for this fulfillment_mode. If customer has no address, the manager must enter one before initiating — enforce at the endpoint.
- **Shipping cost handling:** Shipping cost is either absorbed by the business or charged to the customer as a line item at order creation (owner decision Q2 below). This must be decided before build since it affects `OrderCreate` schema.
- **Audit immutability:** Every state transition (REQUESTED, SHIPPED, DELIVERED) writes an `audit_logs` entry with before/after state. No transition can be reversed once past SHIPPED.
- **Period lock:** Cannot initiate an endless aisle fulfillment if the current accounting period is locked (`check_period_locked`).
- **Discount caps still apply:** All per-item and cart-level discounts are governed by the selling store manager's role cap. Fulfillment mode does not elevate discount permissions.
- **One fulfilling branch per order:** A single order can only be fulfilled from one branch. Splitting across multiple fulfilling branches is explicitly out of scope.
- **No auto-trigger:** The feature has zero automation. No Jarvis agent, no TASKMASTER, no MEGAPHONE trigger. A human manager initiates every time.

## RBAC
- **Initiate (selling store):** STORE_MANAGER, AREA_MANAGER, ADMIN, SUPERADMIN — enforced by `require_roles` on the new endpoint; middleware enforcer as second layer
- **Advance status (fulfilling store):** STORE_MANAGER, AREA_MANAGER, ADMIN, SUPERADMIN of the fulfilling branch — endpoint validates `fulfillment_store_id` matches caller's `active_store_id` (store-scoped check using existing `validate_store_access` pattern)
- **View cross-store inventory:** Same roles as initiate; AREA_MANAGER sees only their managed stores via existing store-scope logic
- **View endless aisle order queue (fulfilling store tab):** STORE_MANAGER, AREA_MANAGER, ADMIN, SUPERADMIN
- **SALES_STAFF, SALES_CASHIER, CASHIER, OPTOMETRIST, WORKSHOP_STAFF, ACCOUNTANT, CATALOG_MANAGER:** No visibility, no action. The `EndlessAislePanel` component is not rendered for these roles.
- Add entries to `backend/api/services/rbac_policy.py` POLICY list for all three new endpoints.

## Integrations
- **Shiprocket:** Reuse existing `POST /shipments` + `shiprocket.py` client. The "Mark Shipped" action on the fulfilling branch side calls the existing endpoint with customer delivery address. AWB stored on both the order and the transfer doc.
- **MSG91 / MEGAPHONE:** One in-app bell notification to fulfilling store managers (reuse `notifications.py` queuing pattern). No WhatsApp or SMS for this feature — the communication is internal branch-to-branch, not customer-facing. Customer gets the normal order confirmation they already receive.
- **Tally:** No change. The order is a normal IMS order; Tally JV is generated by NEXUS's nightly export from the `orders` collection as usual. The `fulfillment_mode` field is ignored by Tally export.
- **Jarvis agents:** None. This feature deliberately has no AI or automation components.

## Risk notes
- **POS is revenue-critical:** The `EndlessAislePanel` component renders inside the POS Review step. Per standing convention, ship this behind an off-by-default feature flag (`ENDLESS_AISLE_ENABLED` env var checked both in the backend endpoint and the frontend component). Default: disabled.
- **Stock race condition:** Two POS terminals at the fulfilling branch could sell the last unit between the availability check and the fulfillment request submission. The re-validation at submit catches this, but there is no reservation/hold mechanism. The endpoint will 409 and the manager must choose a different branch. Acceptable for low-volume VIP scenarios; not acceptable for high-velocity SKUs.
- **Accounting clarity:** The order books revenue at the selling store. The fulfilling store bears the COGS and shipping cost. If inter-branch cost accounting is needed (transfer pricing), that is a Finance module concern and out of scope for this feature — but the `fulfillment_store_id` field on the order doc is the hook for a future allocation query.
- **Address requirement:** If a VIP customer has no saved delivery address, the POS flow must capture one before the panel can be used. This is a UX friction point — the panel should surface the address-missing state early (before payment) rather than failing at submit.
- **Shiprocket in test mode:** `DISPATCH_MODE != live` means AWB booking returns SIMULATED. Managers in non-live environments will see "SIMULATED" tracking. This is the existing behavior and is correct — do not suppress it.

## Recommendation
Build in Phase 3 after the core stock transfer module is stable. Do not fold into the existing `transfers.py` fulfillment flow as a generic transfer — the ENDLESS_AISLE type needs the order linkage and selling-store payment semantics that generic transfers do not have. It is a distinct workflow that happens to reuse the transfer's stock movement engine. Gate behind `ENDLESS_AISLE_ENABLED=false` at launch.

## Owner decisions
- Q: Should the fulfilling branch manager need to explicitly accept/confirm the request before shipping, or does the notification alone suffice and they just mark it shipped when ready? | Why: If a two-step accept is required, the endpoint needs an ACCEPTED status and the order sits in PENDING until the fulfilling branch accepts. If notification-only, the fulfilling branch can mark it shipped immediately. | Options: (a) Notification only — simpler, trusts inter-branch coordination / (b) Explicit accept/decline — adds accountability, lets the fulfilling branch decline if something changed, requires a third endpoint and UI action

- Q: Who bears the shipping cost, and how is it reflected on the customer receipt? | Why: If the business absorbs it, no change to OrderCreate. If charged to customer, a `shipping_charge` line item must be added to the order at creation time (before payment is collected). | Options: (a) Business absorbs — simplest, no receipt change / (b) Charge customer a flat fee at order creation (you set the amount, e.g. ₹99 / ₹149) / (c) Actual Shiprocket cost added after AWB booking — but this means the customer is charged after payment, which creates an AR gap

- Q: Which stores are eligible as fulfilling branches? | Why: You may want to restrict endless aisle to certain store pairs (e.g., only within the same legal entity, or only between stores you manage operationally). | Options: (a) Any active IMS store can fulfill for any other / (b) Only stores under the same entity_id (Better Vision fulfills for Better Vision only, WizOpt for WizOpt) / (c) A configurable allow-list per store in business_settings

- Q: Should AREA_MANAGER be able to see cross-store availability for all stores in their region, or only the stores explicitly in their `store_ids` array? | Why: This is a data-access boundary question — if an AREA_MANAGER's `store_ids` list is not kept up to date, they may have blind spots. | Options: (a) Strictly `store_ids` from their JWT token — safe but requires store list to be accurate / (b) All stores tagged with the same entity_id — broader but less granular
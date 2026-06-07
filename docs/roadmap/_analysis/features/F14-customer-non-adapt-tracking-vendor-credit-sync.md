# Feature #14: Customer Non-Adapt Tracking & Vendor Credit Sync
META: effort=M days=6 risk=MED roi=4 quickwin=no deps=none phase=3

## Existing overlap
- **Returns flow** (`backend/api/routers/returns.py`): `ReturnCreate` schema, `_claim_returnable_qty`, `_restock_good_items`, `_issue_store_credit`, monetary cap guard, tender reversal. A non-adapt return IS a standard return with `return_type=RETURN` or `CREDIT_NOTE`; the qty-cap and restock logic can be reused verbatim.
- **Workshop jobs** (`backend/api/routers/workshop.py`): `workshop_jobs` collection already carries `vendor_id`, `vendor_name`, `qc_history`, `rework_count`, `qc_waived`. The vendor assignment and vendor_status_history patterns are directly reusable for credit-request state machine.
- **Vendor returns / debit notes** (`backend/api/routers/vendor_returns.py`): `vendor_returns` collection with status machine (created → approved → shipped → received_by_vendor → credit_issued). The `credit_note_number` generation and `total_value` calculation already exist.
- **Purchase orders / GRN** (`backend/api/routers/vendors.py`): `vendor_id` + `preferred_vendor_id` already on products; existing vendor-SKU alias lookup for resolving vendor credit targets.
- **AI proposals** (`backend/agents/proposals.py`): Tier-2 "ask-confirm" pattern for sending credit request to vendor — proposal created by system, ADMIN approves, human executes. Reversible tier registry in `reversible_types`.
- **MEGAPHONE agent** (`backend/agents/implementations/megaphone.py`): Notification infra already used for Rx expiry; can trigger vendor credit-request email/WhatsApp using same `send_notification` → `notification_logs` path.
- **Settings** (`backend/api/routers/settings.py`): `business_settings` singleton and multi-collection pattern. Non-adapt policy matrix can live in a new `non_adapt_settings` collection following the same upsert pattern.
- **Audit logs** (`audit_logs` collection): Immutable before/after capture used throughout; non-adapt decisions and credit request state changes must write here.

## Reuse (extend, don't rebuild)
- `backend/api/routers/returns.py` — extend `ReturnCreate` with optional `non_adapt_claim=True` flag and `non_adapt_window_days` context; the core qty-cap, restock, and monetary-cap logic runs unchanged
- `vendor_returns` collection and `backend/api/routers/vendor_returns.py` — add new `vendor_credit_request` sub-type (distinct from physical goods return; no shipment required, replaces `credit_note_number` with `vendor_credit_ref`)
- `workshop_jobs.vendor_status_history` pattern — reuse for `non_adapt_credit_requests.status_history` array
- `backend/agents/proposals.py` — register `vendor_credit_request` as a new tier-2 proposal type (ask-confirm); ADMIN approves, NEXUS/human emails vendor
- `backend/agents/implementations/megaphone.py` — add vendor-facing notification channel (email to vendor contact, optional WhatsApp to vendor mobile from `vendors.mobile`)
- `frontend/src/pages/purchase/VendorReturns.tsx` — extend with a "Non-Adapt Credit Requests" tab; same status machine UI pattern

## Data model
**New collection: `non_adapt_settings`** (singleton, `_id: "default"` plus optional per-brand overrides)
```
{
  "_id": "default",
  "global_window_days": 90,           // owner-set adapt window
  "brand_overrides": [                 // per-brand or per-category thresholds
    { "brand": "Zeiss", "window_days": 60, "eligible_lens_types": ["progressive"] },
    ...
  ],
  "auto_queue_credit_request": true,  // auto-create vendor credit request on non-adapt return
  "require_optometrist_sign_off": true,
  "updated_by": "user_id",
  "updated_at": "datetime"
}
```

**New fields on `orders.items[]`** (extended at order-create for progressive lens lines):
```
"non_adapt_eligible": bool,         // computed at sale from non_adapt_settings + item_type
"non_adapt_window_expires": datetime,
"non_adapt_claimed": bool,          // flipped when non-adapt return created
"non_adapt_return_id": str          // link to returns doc
```

**New fields on `returns` collection** (extend existing schema):
```
"is_non_adapt": bool,
"non_adapt_optometrist_id": str,    // optometrist who reviewed (if require_optometrist_sign_off)
"non_adapt_optometrist_name": str,
"non_adapt_notes": str,
"vendor_credit_request_id": str     // link to vendor_credit_requests doc
```

**New collection: `vendor_credit_requests`**
```
{
  "credit_request_id": str,          // UUID
  "credit_request_number": str,      // e.g. NCR/2026-27/00042
  "return_id": str,
  "order_id": str,
  "vendor_id": str,
  "vendor_name": str,
  "store_id": str,
  "items": [
    {
      "product_id": str,
      "product_name": str,
      "lens_type": str,
      "quantity": int,
      "unit_cost": float,            // cost_at_sale from order.items
      "claim_amount": float          // unit_cost * quantity
    }
  ],
  "total_claim_amount": float,
  "currency": "INR",
  "status": "DRAFT|SUBMITTED|ACKNOWLEDGED|APPROVED|REJECTED|CREDIT_ISSUED|CLOSED",
  "status_history": [...],           // {status, changed_by, changed_at, notes}
  "vendor_credit_ref": str,          // vendor's credit note / debit note number when issued
  "vendor_credit_amount": float,     // may differ from claim_amount (partial vendor approval)
  "submitted_at": datetime,
  "acknowledged_at": datetime,
  "credit_issued_at": datetime,
  "optometrist_id": str,
  "optometrist_name": str,
  "non_adapt_notes": str,
  "created_by": str,
  "created_at": datetime,
  "updated_at": datetime,
  "audit_trail": [...],              // immutable, mirrors agent_audit_log pattern
  "ai_proposal_id": str             // link to proposals doc if auto-generated
}
```

## Backend

**`GET /api/v1/vendors/non-adapt-settings`** (ADMIN/SUPERADMIN) — returns current non-adapt policy matrix

**`PUT /api/v1/vendors/non-adapt-settings`** (SUPERADMIN only) — upsert policy matrix (global window, brand overrides, auto-queue flag, optometrist sign-off flag); writes audit_log entry with before/after

**`GET /api/v1/returns/{return_id}/non-adapt-eligibility`** (STORE_MANAGER+) — checks order item against non_adapt_settings (window not expired, item_type=progressive, brand in policy, not already claimed); returns `{eligible: bool, window_expires: datetime, vendor_id: str, claim_amount: float}`

**`POST /api/v1/returns`** (existing endpoint, extended) — when `non_adapt_claim=True` on `ReturnCreate`:
  1. Runs standard return flow unchanged (qty-cap, restock, refund — no bypass)
  2. Stamps `returns.is_non_adapt=True`, `non_adapt_optometrist_id/name`, `non_adapt_notes`
  3. Stamps `orders.items[].non_adapt_claimed=True`, `non_adapt_return_id`
  4. If `auto_queue_credit_request=True`: auto-creates `vendor_credit_requests` doc in DRAFT, links `return.vendor_credit_request_id`; if `require_optometrist_sign_off=True` and no optometrist_id supplied → 422

**`GET /api/v1/vendors/credit-requests`** (ADMIN/AREA_MANAGER/ACCOUNTANT/SUPERADMIN) — list with filters (vendor_id, status, store_id, date range); store-scoped for STORE_MANAGER

**`GET /api/v1/vendors/credit-requests/{credit_request_id}`** (ADMIN+) — full detail including items, status_history, linked return/order

**`POST /api/v1/vendors/credit-requests/{credit_request_id}/submit`** (ADMIN/SUPERADMIN) — transitions DRAFT → SUBMITTED; creates tier-2 AI proposal (`vendor_credit_request` type) for Superadmin review queue; stamps `submitted_at`; queues vendor notification via MEGAPHONE

**`PATCH /api/v1/vendors/credit-requests/{credit_request_id}/status`** (ADMIN/SUPERADMIN) — human-operated state transitions: SUBMITTED→ACKNOWLEDGED, ACKNOWLEDGED→APPROVED/REJECTED, APPROVED→CREDIT_ISSUED; captures `vendor_credit_ref`, `vendor_credit_amount`; writes status_history + audit_log

**`GET /api/v1/vendors/credit-requests/dashboard`** (ADMIN/SUPERADMIN) — KPIs: total_claims_pending, total_claim_amount, total_recovered, recovery_rate_pct, top_vendors_by_claim, avg_days_to_resolution

**`GET /api/v1/reports/non-adapt`** (STORE_MANAGER+) — non-adapt returns by store/period with vendor credit status; feeds finance reconciliation

## Frontend

**Extend `frontend/src/pages/purchase/VendorReturns.tsx`** — add "Non-Adapt Credits" tab alongside existing vendor returns; shows credit_requests list with status chip (DRAFT/SUBMITTED/ACKNOWLEDGED/CREDIT_ISSUED), claim amount, vendor name, linked order, days open; click → detail drawer with status history timeline and submit/acknowledge/close actions

**New `frontend/src/pages/returns/ReturnForm.tsx` extension** — add "Non-Adapt Return" checkbox (visible only when order contains a progressive lens item within window); when checked: reveal optometrist-picker (if require_optometrist_sign_off), non-adapt notes textarea, computed claim amount preview; all inside existing return creation flow

**Settings panel in `frontend/src/pages/settings/` — `NonAdaptSettingsPanel.tsx`** — SUPERADMIN-only; global window field + brand-override rows (brand name, window days, lens types); auto-queue toggle; optometrist sign-off toggle; restrained table layout (no card-grid, neutral colours, single accent on save button)

**Extend `frontend/src/pages/purchase/PurchaseOrderForm.tsx` or VendorReturns detail** — vendor credit reference field (APPROVED → CREDIT_ISSUED transition); partial credit amount input with variance flag if vendor_credit_amount < total_claim_amount

**Non-adapt dashboard card on `frontend/src/pages/reports/`** — small KPI card block: claims open, ₹ pending recovery, ₹ recovered MTD; link to full credit-requests list; neutral/monochrome, single red accent on overdue claims (>60 days open)

## Business rules
- Non-adapt window is measured from `orders.created_at` (sale date), not prescription date; window expiry is a hard cutoff — the return creation endpoint rejects `non_adapt_claim=True` if `now > non_adapt_window_expires`
- Only `item_type=PROGRESSIVE` (or owner-configured lens types per brand) qualify; standard single-vision lenses do not; this is enforced server-side against `non_adapt_settings.brand_overrides`
- A given order line can only be claimed as non-adapt once (`non_adapt_claimed=True` blocks re-claim with 409)
- The customer refund follows the standard return flow unchanged (tender reversal, restock, monetary cap) — the non-adapt flag does NOT create a parallel refund; it only triggers the vendor credit request on the back end
- If `require_optometrist_sign_off=True`, `non_adapt_optometrist_id` must be a real user with OPTOMETRIST role in the same store; missing or wrong-role → 422
- `vendor_credit_amount` may be less than `total_claim_amount` (partial vendor approval); the variance must be flagged in the dashboard and written to audit_log with reason
- Period lock (`period_locks` collection checked via `check_period_locked`) applies to the underlying return; vendor credit request submission does NOT require an open period (it is a vendor-side document, not a posted accounting entry) — but CREDIT_ISSUED transition must record `credit_issued_at` for ITC/AP reconciliation
- All status transitions write to `audit_logs` (immutable) and `vendor_credit_requests.status_history`; no status can be reversed (forward-only state machine)
- Credit request numbers follow the same atomic-counter pattern as invoice numbers (`counters` collection, key `ncr:{store_prefix}:{fy}`, reset each financial year Apr-Mar)

## RBAC
| Action | Roles |
|---|---|
| View non-adapt settings | ADMIN, SUPERADMIN |
| Edit non-adapt settings | SUPERADMIN only |
| Create non-adapt return (with claim flag) | STORE_MANAGER, AREA_MANAGER, ADMIN, SUPERADMIN |
| View credit requests (own store) | STORE_MANAGER, ACCOUNTANT, AREA_MANAGER, ADMIN, SUPERADMIN |
| Submit credit request to vendor | ADMIN, SUPERADMIN |
| Acknowledge / approve / record credit issued | ADMIN, SUPERADMIN |
| View non-adapt dashboard / reports | STORE_MANAGER+, ACCOUNTANT, ADMIN, SUPERADMIN |
| Optometrist sign-off | OPTOMETRIST (own store) |

SALES_CASHIER and SALES_STAFF cannot initiate a non-adapt return (STORE_MANAGER minimum). ACCOUNTANT is read + status-update only (not create). Workshop Staff and Cashier have no access.

## Integrations
- **MEGAPHONE agent** — on SUBMITTED status: queue vendor notification (email to `vendors.email` via MSG91 or SMTP, WhatsApp to `vendors.mobile` if configured); use existing `send_notification` → `notification_logs` path with new template `VENDOR_CREDIT_REQUEST_SUBMITTED`; subject/body includes credit_request_number, claim_amount, item list; gated on DISPATCH_MODE
- **AI proposals (TASKMASTER/ORACLE)** — `vendor_credit_request` registered as tier-2 in `requires_confirmation` list in `taskmaster.py`; ORACLE can auto-flag non-adapt rate spikes by brand as anomaly (reuse `_detect_sales_anomalies` pattern for `non_adapt_rate_pct > threshold`)
- **Tally / AP reconciliation** — when `credit_issued_at` is stamped, NEXUS nightly export should include a corresponding debit note entry in the Tally JV XML (extend `_build_tally_export` in `nexus.py`); amount = `vendor_credit_amount`; treated as reduction of AP payable against vendor
- **No Shopify / Razorpay / Shiprocket involvement** — this is a B2B vendor credit flow, not a customer-facing payment

## Risk notes
- **POS / money risk (MED)**: The customer refund runs through the existing `returns.py` path which has the monetary-cap guard and atomic qty-claim — do NOT bypass any of these guards when the non_adapt_claim flag is present. The new code only adds a side-effect (vendor credit doc creation) after the standard return commits; if the vendor doc creation fails, it must NOT roll back the customer return (fail-soft with audit flag).
- **Accounting risk**: Vendor credit amount reduces AP payable; if CREDIT_ISSUED is recorded in a period-locked month, the Tally debit note would land in the wrong period. Add a soft warning (not a hard block) at CREDIT_ISSUED transition if the credit_issued_at month is locked.
- **Optometrist sign-off coupling**: If the optometrist who did the original test has left the store or been deactivated, the sign-off gate will block the claim. The system should allow ADMIN to override the optometrist requirement on a per-case basis (add `optometrist_override_by` field + ADMIN-only flag in the PATCH endpoint).
- **Feature flag**: The entire non-adapt claim path should be toggled by `non_adapt_settings.enabled` (default `false`) so stores not selling progressive lenses see no UI change; the return form extension renders only when `enabled=true` AND the order contains a qualifying item.
- **Vendor credit partial approval variance**: If `vendor_credit_amount < total_claim_amount`, the delta is a loss that should surface in P&L as a separate line (cost of non-adapt). This is a finance-side concern; a story for the Finance module rather than this feature.

## Recommendation
Build later — not a quick win. The customer refund side works today via existing returns; the only loss is that the vendor credit recovery happens manually outside IMS. Build this in Phase 3 after the returns and vendor-returns modules are confirmed stable in production, and after at least one progressive-lens brand vendor has agreed to the credit process. The MEGAPHONE notification and Tally debit-note integration should be staged: ship the credit-request tracker first (pure record-keeping, no integrations), then add MEGAPHONE notification in a follow-on PR, then Tally in a third PR.

## Owner decisions
- Q: Which brands/lens types qualify for non-adapt credit claims, and what is the return window per brand? | Why: Determines the `non_adapt_settings.brand_overrides` seed data; wrong defaults mean stores either miss eligible claims or submit ineligible ones | Options: (a) Start with a single global window (e.g., 90 days, all progressive lenses) and add brand overrides later; (b) Configure per-brand from day one (e.g., Zeiss 60 days, Essilor 90 days, others 30 days); (c) Let store managers set their own windows per vendor agreement
- Q: Does the customer get a full refund, a replacement lens, or store credit when they cannot adapt — and does the answer differ by brand? | Why: Determines `return_type` (RETURN vs CREDIT_NOTE vs EXCHANGE) in `ReturnCreate`; the vendor credit request amount must match what the store actually gave back | Options: (a) Always full refund to original payment method; (b) Store credit only (store keeps cash, reduces exposure); (c) Exchange for a different lens (no cash out, vendor credit still claimed)
- Q: Should the optometrist sign-off be mandatory before a non-adapt return is accepted, or is it advisory? | Why: If mandatory, a missing/departed optometrist blocks the claim entirely; if advisory, there is a fraud risk (staff could abuse the non-adapt route for ordinary returns) | Options: (a) Mandatory for all progressive non-adapts; (b) Mandatory only above a configurable ₹ threshold (e.g., above ₹5,000 lens cost); (c) Advisory — record but do not block
- Q: Who submits the credit request to the vendor — IMS automatically emails the vendor, or your purchase/accounts team does it manually using the report IMS generates? | Why: Determines whether MEGAPHONE vendor notification is enabled and whether vendor email/mobile must be captured on the vendor master | Options: (a) IMS emails vendor automatically on SUBMITTED (requires vendor email on master, DISPATCH_MODE=live); (b) IMS generates a PDF Credit Request Report; accounts team emails it manually; (c) Phone/WhatsApp only, no email automation
- Q: When the vendor pays a partial credit (e.g., claims ₹8,000 but vendor pays ₹6,000) — should IMS record the ₹2,000 variance as a store loss in P&L, or is it written off silently? | Why: Determines whether the CREDIT_ISSUED transition triggers a P&L adjustment entry or just a reconciliation note | Options: (a) Record variance in a "non-adapt loss" expense category automatically; (b) Flag it for the accountant to post manually in the expense tracker; (c) Ignore the variance in IMS (reconcile in Tally only)
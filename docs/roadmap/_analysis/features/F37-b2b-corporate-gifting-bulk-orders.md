# Feature #37: B2B Corporate Gifting & Bulk Orders
META: effort=L days=12 risk=MED roi=4 quickwin=no deps=none phase=3

## Existing overlap
IMS already has substantial B2B foundations:
- **Customer B2B type + GSTIN validation** (`backend/api/routers/customers.py:178-204`) — `customer_type=B2B`, GSTIN required, `credit_limit` per-customer (khata), `billing_address` already stored
- **B2B GSTIN enforcement** (`customers.py:367-389`) — blocks B2B create without valid GSTIN
- **Credit limit / khata** (`customers.py:897-934`) — per-customer `credit_limit`, AR tracking already in `finance.py:959-1046` (AR aging by `credit_terms_days`)
- **Discount approval workflow** (`orders.py:1195-1312`) — `discount_approved_by`, `discount_reason`, role-capped discounts with `pricing_caps.effective_discount_cap()` and cart-level `cart_discount_percent` with `cart_discount_approved_by` — the approval rails exist
- **Order-level delivery scheduling** (`orders.py:499`, `posStore.ts:146-149`) — `delivery_time_slot`, `delivery_priority`, `cart_discount_percent` already captured on orders
- **GST tax invoice** (`order_repository.py:446-507`) — `{PREFIX}/{FY}/{serial}` serial, CGST/SGST/IGST split, per-line HSN/taxable/tax — already GST-legal for B2B invoices
- **CREDIT tender** (`orders.py:2386+`) — pay-later promise, excluded from `amount_paid`, sticky audit flag — B2B on-credit flow already exists
- **AR aging** (`finance.py:959-1046`) — overdue B2B balances already surfaced per customer with bucket buckets
- **Tally Sales JV export** (NEXUS nightly) — B2B orders already flow into Tally XML
- **Vendor portal pattern** (`backend/api/routers/vendor_portal.py`) — UUID-bearer token auth for external non-JWT parties; same pattern reusable for a corporate client portal
- **Proposal / approval system** (`backend/agents/proposals.py`) — tier-2 "ask-confirm" approval rails exist; bulk discount above cap can route here
- **Idempotency-Key on order-create** (PR #368) — prevents double-submit on large bulk orders

What is **not** built: dedicated bulk-order quote → approval → PO-acknowledgement state machine, per-line custom engraving/logo job field, consolidated multi-delivery scheduling, bulk invoice bundling, and a named "corporate account" entity above the individual `customers` doc.

## Reuse (extend, don't rebuild)
- **`customers` collection** — add `corporate_account_id` FK, `bulk_discount_tier` (STANDARD/SILVER/GOLD/PLATINUM), `po_required` flag, `approved_bulk_discount_pct` ceiling per account
- **`orders` collection + `orders.py` router** — add `channel=B2B_BULK`, `quote_id`, `corporate_po_number`, `corporate_po_date`, `engraving_instructions` (per-line), `delivery_schedule` (array of {qty, store_id, requested_date}); reuse existing CREDIT tender + AR aging; reuse cart-level `cart_discount_percent` + `cart_discount_approved_by`
- **`order_repository.py` `next_invoice_number()`** — reuse as-is; B2B bulk invoice is still a standard GST tax invoice on the existing serial
- **`finance.py` AR aging** (`get_outstanding`, line 959-1046) — B2B bulk orders flow into the same AR pipeline; no change needed
- **`pricing_caps.effective_discount_cap()`** — extend to read `customers.approved_bulk_discount_pct` as an account-level ceiling (overrides category cap when explicitly approved by ADMIN/SUPERADMIN)
- **`vouchers.redeem_voucher_atomic()`** pattern — reuse atomic find_one_and_update guard for bulk-quote reservation (prevent two reps from double-booking the same stock allocation)
- **`vendor_portal.py` UUID-bearer pattern** — reuse for a read-only corporate client portal (order status, invoice download) without exposing IMS JWT
- **Proposal system (`proposals.py`)** — route bulk discount above the approved ceiling to tier-2 "ask-confirm" proposal; Superadmin approves, system stamps `cart_discount_approved_by`
- **Tally NEXUS nightly export** — no change; B2B bulk orders are standard orders; Tally JV picks them up by `payment_status`
- **`notification_service.py` + MEGAPHONE** — reuse for quote-expiry alerts, order-ready WhatsApp, invoice-delivery to corporate email

## Data model
**New collection: `corporate_accounts`**
```
corporate_account_id  (unique)
company_name
gstin                 (validated 15-char Indian)
pan
billing_address       {line1, city, state, pincode}
shipping_addresses[]  {label, address}
credit_limit          (INR, editable by ADMIN)
credit_terms_days     (default 30)
approved_bulk_discount_pct   (ceiling set by ADMIN/SUPERADMIN, e.g. 25%)
bulk_discount_tier    STANDARD | SILVER | GOLD | PLATINUM
po_required           bool (must supply corporate PO number before order confirm)
primary_contact       {name, mobile, email}
secondary_contacts[]
assigned_rep_id       (IMS user_id of the account manager)
is_active
created_at, updated_at, created_by
```

**New collection: `bulk_quotes`**
```
quote_id              (unique, BQ/{FY}/{serial})
corporate_account_id
store_id
status                DRAFT | SENT | ACCEPTED | EXPIRED | CONVERTED
valid_until           (datetime, owner-configured TTL)
items[]               {product_id, sku, name, qty, mrp, unit_price, discount_pct,
                       engraving_text, engraving_logo_url, hsn, gst_rate,
                       taxable_value, tax_amount}
delivery_schedule[]   {qty, delivery_store_id, requested_date, address_label}
cart_discount_pct     (account-level bulk discount, capped by approved_bulk_discount_pct)
cart_discount_approved_by
corporate_po_number
corporate_po_date
subtotal, tax_amount, grand_total
notes
converted_order_ids[] (when CONVERTED)
created_by, created_at, sent_at, accepted_at
```

**Extended fields on `orders`** (new fields, all optional, no breaking change):
```
channel               already exists — add enum value B2B_BULK
quote_id              FK → bulk_quotes
corporate_account_id  FK → corporate_accounts
corporate_po_number
corporate_po_date
engraving_instructions_per_line  [{item_index, text, logo_url}]
bulk_delivery_sequence           int (1-of-N for split deliveries)
```

**Extended fields on `customers`** (non-breaking):
```
corporate_account_id  (FK — links individual contact to their corporate account)
```

## Backend
- `POST /api/v1/corporate-accounts` — create/update corporate account (ADMIN/SUPERADMIN); validates GSTIN, sets approved_bulk_discount_pct ceiling
- `GET /api/v1/corporate-accounts` — list accounts (ADMIN/AREA_MANAGER/STORE_MANAGER); store-scoped for non-HQ
- `GET /api/v1/corporate-accounts/{id}` — account 360: contacts, open quotes, AR balance, credit utilisation
- `POST /api/v1/bulk-quotes` — create quote (ADMIN/AREA_MANAGER/STORE_MANAGER); validates item discounts against `approved_bulk_discount_pct`; if cart discount exceeds ceiling → auto-routes to tier-2 proposal for ADMIN approval before SENT
- `GET /api/v1/bulk-quotes` — list (ADMIN/AREA_MANAGER/STORE_MANAGER); filter by status/account/store
- `GET /api/v1/bulk-quotes/{id}` — detail with delivery schedule + engraving specs
- `POST /api/v1/bulk-quotes/{id}/send` — DRAFT → SENT; triggers WhatsApp/email to primary_contact via MEGAPHONE; stamps `sent_at`
- `POST /api/v1/bulk-quotes/{id}/accept` — SENT → ACCEPTED (can be called by internal rep on verbal confirmation, or future client portal)
- `POST /api/v1/bulk-quotes/{id}/convert` — ACCEPTED → CONVERTED; creates one `orders` doc per `delivery_schedule` entry (channel=B2B_BULK, CREDIT tender for on-credit); stamps `quote_id` on each order; checks `period_locks` and `credit_limit` utilisation before writing; idempotent (re-run safe via `quote_id` dedup)
- `GET /api/v1/bulk-quotes/{id}/invoice-bundle` — returns a single consolidated GST invoice PDF (or array of individual invoice URLs) for all converted orders under this quote — reuses `next_invoice_number()` per order; bundle merges line items for the consolidated view
- `GET /api/v1/corporate-accounts/{id}/ar-summary` — credit limit, utilised amount, available headroom, overdue buckets — delegates to existing `finance.py get_outstanding()` filtered by `corporate_account_id`
- Extend `pricing_caps.effective_discount_cap()` — when `corporate_account_id` present on order, read `corporate_accounts.approved_bulk_discount_pct` as an account-level ceiling (ADMIN-approved, overrides category cap downward only — never upward past role cap)

## Frontend
- **`/corporate-accounts`** — Corporate Accounts list page (ADMIN/AREA_MANAGER): table of accounts (name, GSTIN, tier, credit limit, AR balance, open quotes count); "New Account" button
- **`/corporate-accounts/{id}`** — Account 360 page: header (company name, tier badge, assigned rep), tabs: Contacts | Open Quotes | Order History | AR Aging | Credit Limit; restrained card layout, single accent for tier badge colour (GOLD = amber-600, PLATINUM = slate-600, else neutral)
- **`/bulk-quotes/new`** — Quote builder: corporate account picker → product search (reuse existing catalog search) → per-line qty + unit price + engraving text field + optional logo URL → delivery schedule builder (add rows: qty / store / date) → discount summary card (shows approved ceiling, warns if exceeded before submit) → Notes → Save Draft / Send
- **`/bulk-quotes/{id}`** — Quote detail: status chip, timeline (DRAFT→SENT→ACCEPTED→CONVERTED), line-item table with engraving column, delivery schedule, "Convert to Orders" button (ACCEPTED state only), "Download Invoice Bundle" link (CONVERTED state)
- **`/bulk-quotes`** — Quotes list: filter by status/account/store; status chips (DRAFT=gray, SENT=blue, ACCEPTED=green, EXPIRED=amber, CONVERTED=slate); link to account 360
- Extend **`/customers/{id}`** — add "Corporate Account" chip linking to `/corporate-accounts/{account_id}` when `corporate_account_id` is set
- Extend **Finance Dashboard** (`FinanceDashboard.tsx`) — add B2B AR card: total B2B outstanding, overdue >30d amount, top 3 overdue accounts; link to AR aging filtered by B2B_BULK channel

## Business rules
- **Credit limit hard-lock**: `convert` endpoint rejects if `(existing AR balance + new order grand_total) > corporate_accounts.credit_limit`; surfaces exact overrun amount; ADMIN can override with `credit_override_reason` (audited)
- **Bulk discount ceiling**: discount per line or cart-level discount cannot exceed `corporate_accounts.approved_bulk_discount_pct`; exceeding → auto-creates tier-2 proposal; quote stays in DRAFT until proposal approved
- **PO required gate**: if `po_required=true`, `send` endpoint rejects unless `corporate_po_number` + `corporate_po_date` are populated
- **Engraving job auto-creation**: on `convert`, if any line has `engraving_text` or `engraving_logo_url`, auto-create a `workshop_jobs` doc (type=ENGRAVING) linked to the order; sets `fitting_instructions` to engraving spec; ensures QC before DELIVERED
- **Quote expiry**: expired quotes (`valid_until` < now, status=SENT/ACCEPTED) cannot be converted; TASKMASTER auto-flips status to EXPIRED on its 5-min tick; MEGAPHONE sends 24h-before expiry WhatsApp to assigned rep
- **Minimum order qty**: owner-configured per account tier (e.g., GOLD ≥ 50 units); enforced at `send` not at quote creation (lets rep build draft freely)
- **Split delivery stock reservation**: each `delivery_schedule` entry reserves stock at the named store at `convert` time using the existing atomic stock-unit claim pattern; if any store is short → error lists the shortage per SKU (never partial-commit)
- **Audit**: every status transition on `bulk_quotes` + every `corporate_accounts` field change writes an immutable `audit_logs` entry with before/after; `cart_discount_approved_by` stamped on both quote and converted orders
- **Period lock**: `convert` checks `check_period_locked()` for the current month before creating orders (consistent with existing gate at `orders.py:1073`)
- **GST invoice correctness**: each converted order gets its own `invoice_number` (existing serial); consolidated invoice bundle is a presentation layer (PDF merge) — books remain individual orders in Tally

## RBAC
- **SUPERADMIN / ADMIN**: full CRUD on corporate accounts (including setting `approved_bulk_discount_pct`, `credit_limit`, `po_required`); approve tier-2 proposals for over-ceiling discounts; credit override
- **AREA_MANAGER / STORE_MANAGER**: create/edit quotes for their stores, view accounts linked to their stores, convert accepted quotes, download invoice bundles; cannot change `approved_bulk_discount_pct` or `credit_limit`
- **SALES_CASHIER / SALES_STAFF**: read-only on quotes and accounts (so they can look up an order tied to a bulk quote); cannot create quotes or accounts
- **ACCOUNTANT**: read-only on accounts + AR summary; can see invoice bundles; no quote creation
- **All other roles**: no access to `/corporate-accounts` or `/bulk-quotes`

## Integrations
- **MSG91 / MEGAPHONE**: quote-sent notification (WhatsApp to corporate primary_contact — template: "Your quote BQ/2026-27/001 for ₹X is ready; valid until DD-MMM"); quote-expiry-24h alert to assigned rep; order-ready-for-delivery WhatsApp
- **Tally / NEXUS**: no change to nightly JV export — B2B bulk orders (channel=B2B_BULK) are standard orders; NEXUS picks them up; party name resolves to `corporate_accounts.company_name` via `corporate_account_id` on the order
- **Razorpay**: no change for on-credit (CREDIT tender) orders; if corporate pays upfront via NEFT/bank transfer, existing BANK_TRANSFER tender handles it
- **Shopify**: not applicable (B2B bulk is in-store/offline channel)
- **Jarvis / ORACLE agent**: extend `_detect_discount_abuse()` to flag when corporate account discount exceeds tier-appropriate ceiling without an approved proposal; extend `_propose_reorders()` to factor in open (ACCEPTED but not yet converted) bulk quote quantities when computing reorder demand

## Risk notes
- **POS revenue-critical path**: `convert` creates orders via the same `orders` collection; any bug in conversion could corrupt AR or double-mint stock units. Ship behind a feature flag (`B2B_BULK_ENABLED` env var, default off); enable per-store in `integrations` collection. Full integration test suite covering convert → stock claim → invoice serial → AR aging before enabling live.
- **Stock reservation race**: split-delivery conversion reserves across multiple stores atomically in a loop — if store 2 fails after store 1 succeeds, partial reservation occurs. Implement saga-style rollback (release store 1 units if store 2 claim fails) before go-live; same pattern as `vouchers.redeem_voucher_atomic`.
- **Credit limit bypass**: a rep could create multiple quotes simultaneously to exceed limit. Guard: `convert` must re-check AR balance + all ACCEPTED unconverted quotes' `grand_total` combined against `credit_limit` (not just existing orders).
- **Engraving as workshop job**: auto-creating workshop jobs from `convert` couples two systems; if workshop router is down, conversion should still succeed — make workshop job creation fail-soft (log warning, return `engraving_job_ids: []` with flag `engraving_jobs_pending_manual_create: true`).
- **Consolidated invoice PDF**: PDF generation is not currently implemented anywhere in the backend (noted gap in the POS/Orders audit). Either (a) generate server-side with a lightweight lib (WeasyPrint or reportlab — no new infra), or (b) return an array of existing `invoice_number` strings and let frontend render + print each. Option (b) is safer for the first version.

## Recommendation
Build later (Phase 3, after core retail stabilises). The quote→convert→AR pipeline touches orders, stock, workshop, and finance simultaneously — worth a dedicated sprint once the P1/P2 money-integrity fixes (returns over-refund, loyalty double-spend) are fully merged and the existing order flow is battle-tested. The data model above is non-breaking so schema prep (adding `corporate_accounts` collection, `quote_id`/`corporate_account_id` fields on orders) can be done in Phase 2 as a zero-risk migration.

## Owner decisions
- Q: What is the minimum order value or quantity threshold to qualify a customer as a corporate/bulk account? | Why: Sets the `minimum_order_qty` per tier and the `approved_bulk_discount_pct` ceiling displayed in the UI; determines whether a walk-in buying 10 units gets a quote or just a normal POS sale | Options: (a) ≥25 units OR ≥₹50,000 order value — auto-qualify as bulk; (b) manual ADMIN designation only (no automatic threshold); (c) tiered: ≥25 units = STANDARD, ≥100 = SILVER, ≥500 = GOLD, ≥1,000 = PLATINUM with different discount ceilings per tier
- Q: What is the maximum bulk discount ceiling you are willing to approve for each tier (STANDARD / SILVER / GOLD / PLATINUM)? | Why: These become the `approved_bulk_discount_pct` defaults per tier in the system; any quote above these routes to Superadmin approval | Options: (a) 15% / 20% / 25% / 30%; (b) same as current role caps (STORE_MANAGER=20%, AREA_MANAGER=25%); (c) custom per-account (no tier defaults, every account gets an individually negotiated ceiling set by Admin)
- Q: Should corporate clients receive a consolidated single GST invoice for the entire bulk order, or one invoice per delivery (per store / per shipment)? | Why: Determines whether to build a PDF-merge "invoice bundle" (a presentation layer) or whether each delivery leg is a standalone tax document; Indian GST rules require a tax invoice per supply event, so consolidated is a presentation merge only — one invoice per delivery in the books | Options: (a) one invoice per delivery leg (simplest, GST-compliant, Tally-safe); (b) one consolidated invoice for the full order with all delivery details listed (presentation PDF only; books still have per-delivery entries); (c) both — individual invoices generated per delivery, plus a summary statement (not a tax invoice) for the corporate finance team
- Q: Should engraving / logo customisation be handled in-house (workshop job) or outsourced to an external vendor (existing vendor portal)? | Why: In-house = auto-create workshop_job on conversion (adds workshop load + QC step); outsourced = assign a vendor on the bulk quote line item (uses existing vendor assignment flow) | Options: (a) always in-house workshop job; (b) always vendor-outsourced; (c) per-line choice at quote time (rep selects "workshop" or a vendor from dropdown)
- Q: Do corporate clients need a self-service read-only portal (view their quotes, order status, download invoices) or is all communication through your sales rep via WhatsApp/email? | Why: Self-service portal requires building a UUID-bearer token auth surface (the vendor_portal.py pattern exists and can be reused — low effort); no portal = simpler build but rep handles all queries | Options: (a) no portal — rep + WhatsApp/email is sufficient for now; (b) yes — simple read-only portal (quote status, invoice PDFs, AR balance) accessible via a secure link sent per quote; (c) yes — full portal with PO upload, payment history, reorder capability (significantly more scope)
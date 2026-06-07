# Feature #42: VIP "Black Book" Personalized Digital Lookbooks
META: effort=M days=8 risk=MED roi=4 quickwin=no deps=none phase=3

## Existing overlap
- **Campaign builder** (`backend/api/routers/campaigns.py:345-530`, `campaign_segments.py`) — SCHEDULED/ONE_TIME campaigns with WhatsApp send via MEGAPHONE. Lookbook share is effectively a targeted one-shot campaign.
- **Notification dispatch** (`backend/api/services/notification_service.py:56-65`) — `send_whatsapp()` via MSG91, DISPATCH_MODE-gated, quiet-hours-aware. Reuse directly for sending the lookbook link.
- **WhatsApp inbound** (`backend/api/routers/webhooks.py:686-820`) — conversation thread upsert (`whatsapp_conversations`), intent dispatch. Inbound reservation reply can route here.
- **Customer 360 + RFM** (`backend/api/routers/crm.py:830-882`, `_determine_lifecycle_phase()`) — VIP tier = lifecycle_phase='VIP' (LTV ≥ 100k or freq ≥ 20). Segment already computable without new code.
- **Loyalty tier** (`backend/api/routers/loyalty.py`, `loyalty_accounts.tier`) — PLATINUM/GOLD customers are a natural VIP filter.
- **Promo templates** (`promo_templates` collection, `campaigns.py:920-1044`) — BOGO/COMBO/THRESHOLD structure. Lookbook "reserve" is a new type but shares the template CRUD pattern.
- **Store credit / voucher** (`vouchers.py`, `credit_note_ledger`) — if a holding deposit is required, `redeem_voucher_atomic` pattern is reusable.
- **Audit trail** (`audit_logs` collection) — all lookbook create/send/view/reserve events flow here.
- **RBAC policy** (`backend/api/services/rbac_policy.py`) — SUPERADMIN/ADMIN gates already wired; adding new routes follows the same `require_roles` pattern.
- **Product catalog** (`catalog_products`, `catalog_variants`) — product images, MRP, brand, category already stored; lookbook items are a curated subset.
- **product_images collection** — design-queue schema exists (design_status: QUEUED/APPROVED/REJECTED) but no live UI. Lookbooks share the approved-image concept; can pull `APPROVED` images.

## Reuse (extend, don't rebuild)
- `backend/api/routers/campaigns.py` — add `lookbook` as a new campaign type with a `lookbook_id` reference; reuse `send_campaign()` send path and MEGAPHONE drain
- `backend/api/services/notification_service.py` — reuse `send_whatsapp()` to deliver the secure link; add `LOOKBOOK_INVITE` template to the default TEMPLATES dict
- `backend/api/services/campaign_segments.py` — add `vip_customers` segment (lifecycle_phase='VIP' OR loyalty tier IN [GOLD, PLATINUM])
- `backend/api/routers/crm.py::_determine_lifecycle_phase()` — already returns 'VIP'; use directly to populate the segment
- `backend/agents/implementations/megaphone.py` — lookbook sends drain through existing PENDING → SENT/SIMULATED/FAILED path; no new drain loop needed
- `audit_logs` collection — reuse existing insert pattern for create/send/view/reserve events
- `product_images` collection — pull `design_status='APPROVED'` images per product_id for the lookbook card carousel
- `integrations` collection — Razorpay config already stored; reuse for optional holding-deposit payment link generation

## Data model
- **New collection: `lookbooks`**
  ```
  lookbook_id        (UUID, unique)
  title              (str, 1-80 chars)
  subtitle           (str, optional, 100 chars)
  store_id           (store that owns / ships)
  curated_by         (user_id of ADMIN/SUPERADMIN)
  brand_theme        (enum: BV_RED | NEUTRAL — controls header color on the public page)
  items[]
    product_id
    variant_id       (optional — specific color/size)
    display_price    (MRP shown; offer_price shown only if owner opts in)
    highlight_note   (str, 100 chars — e.g. "last pair in India")
    image_url        (pulled from product_images where design_status=APPROVED; fallback catalog product photo)
    sort_order       (int)
  status             (DRAFT | PUBLISHED | EXPIRED | RECALLED)
  valid_until        (datetime — auto-expires; server refuses reservations after this)
  recipients[]
    customer_id
    token            (UUID, cryptographically random, per-customer — never shared)
    sent_at
    viewed_at
    reservation      (sub-doc — see lookbook_reservations)
  created_at
  updated_at
  recalled_at        (if RECALLED — admin can kill a live lookbook)
  recall_reason
  ```

- **New collection: `lookbook_reservations`** (or embedded as recipients[].reservation)
  ```
  reservation_id
  lookbook_id
  customer_id
  product_id
  variant_id
  reserved_at
  status             (PENDING | CONFIRMED | CANCELLED | CONVERTED_TO_ORDER)
  order_id           (set when staff converts to POS order)
  hold_expires_at    (48h/72h window — owner decides)
  deposit_paid       (bool)
  deposit_amount
  deposit_payment_id (Razorpay payment_id if collected)
  notes              (staff notes on follow-through)
  converted_by       (user_id — staff who turned reservation → order)
  ```

- **New fields on `notification_templates`** — add `LOOKBOOK_INVITE` template row (WhatsApp body with {{name}}, {{title}}, {{url}}, {{valid_until}})

## Backend
- `POST /api/v1/lookbooks` — create lookbook (ADMIN/SUPERADMIN); validates items exist in catalog, at least 1 item, valid_until in future; status=DRAFT
- `GET /api/v1/lookbooks` — list (ADMIN/SUPERADMIN); filter by store_id, status, curated_by
- `GET /api/v1/lookbooks/{id}` — detail (ADMIN/SUPERADMIN)
- `PUT /api/v1/lookbooks/{id}` — update items/title/valid_until while DRAFT; block edits once PUBLISHED
- `POST /api/v1/lookbooks/{id}/publish` — transition DRAFT → PUBLISHED; requires at least 1 recipient added
- `POST /api/v1/lookbooks/{id}/recipients` — add customers (resolve via customer_id, mint per-customer token, status=not-yet-sent); idempotent on customer_id
- `POST /api/v1/lookbooks/{id}/send` — dispatch WhatsApp via MEGAPHONE to all unsent recipients; sets sent_at; queues `notification_logs` rows with campaign_id=lookbook_id; DISPATCH_MODE-gated
- `POST /api/v1/lookbooks/{id}/recall` — transition PUBLISHED → RECALLED; all pending reservations auto-cancelled; requires recall_reason; audit-logged
- `GET /api/v1/lookbooks/{id}/analytics` — sent/viewed/reserved/converted counts; per-item interest heatmap
- `GET /public/lb/{token}` — **unauthenticated** public endpoint; validates token exists, lookbook PUBLISHED, valid_until not past; records viewed_at; returns lookbook payload (no customer PII in response)
- `POST /public/lb/{token}/reserve` — **unauthenticated**; customer taps "Reserve" on an item; validates hold window available, status idempotent (one active reservation per token); creates `lookbook_reservations` row; triggers in-app notification to store staff (re-uses `notifications` bell collection); optional Razorpay payment link generated server-side if deposit_required=True
- `PATCH /api/v1/lookbooks/reservations/{reservation_id}` — staff converts reservation to order (sets order_id, status=CONVERTED_TO_ORDER) or cancels; ADMIN/STORE_MANAGER/SALES_CASHIER

## Frontend
- **`/lookbooks` (new page, ADMIN/SUPERADMIN)** — list of lookbooks (title, store, status badge, sent/viewed/reserved counts, valid_until countdown chip); "New Lookbook" CTA
- **`/lookbooks/new` + `/lookbooks/{id}/edit` (new page)** — product picker (search catalog by brand/category, drag to reorder, per-item highlight note field, price display toggle); recipient picker (search customers, shows VIP/PLATINUM badge, adds to list); valid-until date picker; brand_theme selector; "Publish & Send" button
- **`/lookbooks/{id}` detail page** — analytics summary (sent/viewed/reserved/converted funnel), recipient list with per-row status chips, "Send to unsent" button, "Recall" button (requires reason input)
- **Public landing `/lb/:token` (unauthenticated, no nav shell)** — branded A4-card layout (BV red or neutral header per brand_theme); item cards with image, brand, model, highlight note, MRP; single "Reserve this item" button per card; after tap shows confirmation card with hold expiry and store contact; no login required; no customer PII displayed; restrained light-only UI matching design guidelines
- **Reservations panel on `/pos`** — small "Lookbook Reservations" tab in the existing POSPage sidebar; lists PENDING reservations for today's store; "Convert to Order" action pre-populates cart with reserved product (re-uses posStore.addProduct flow)
- **Notification bell** — re-uses existing bell (`notifications` collection); staff get in-app alert when a VIP reserves

## Business rules
- One active reservation per token (per customer per lookbook) — server enforces; second tap returns existing reservation
- Reservations auto-expire at `hold_expires_at`; expired reservations cannot be converted to orders
- `valid_until` < 30 days from publish date (server caps — prevents stale lookbooks)
- Minimum 1 item, maximum 20 items per lookbook (server validates)
- Item price shown is MRP only by default; showing offer_price requires owner opt-in per lookbook (display_price field)
- RECALLED lookbook: public URL returns 410 Gone immediately; all PENDING reservations cancelled; audit row written
- Deposit: if collected, Razorpay payment_id stored; refund on cancellation is a manual staff action (no auto-refund — avoids money risk without explicit owner sign-off)
- WhatsApp sends respect quiet hours (21:00–09:00 IST) via existing `quiet_hours.in_quiet_hours()` check
- Marketing consent gate: recipient must have `marketing_consent=True`; non-consenting customers silently skipped with a warning count returned to the sender
- All create/send/view/reserve/recall events written to `audit_logs` with entity_type='lookbook'
- Lookbook items must exist and be `is_active=True` in `catalog_products` at publish time; deactivated products after publish show as "Currently unavailable" on the public page (server checks at reserve time)

## RBAC
- `SUPERADMIN`, `ADMIN` — full CRUD (create, publish, recall, analytics, send)
- `STORE_MANAGER` — read own store's lookbooks, convert reservations to orders, view analytics for own store
- `SALES_CASHIER`, `SALES_STAFF` — read reservations for own store, convert to order
- `OPTOMETRIST`, `ACCOUNTANT`, `WORKSHOP_STAFF`, `CASHIER`, `CATALOG_MANAGER` — no access
- Public endpoints (`/public/lb/:token`) — no IMS auth; token is the credential

## Integrations
- **MSG91 WhatsApp** — primary delivery channel via existing `send_whatsapp()` in `notification_service.py`; DISPATCH_MODE-gated (off/test/live); uses `LOOKBOOK_INVITE` template (must be DLT-approved by owner before live sends)
- **Razorpay** — optional holding-deposit payment link (`rzp.orders.create` → return `short_url`); only if owner enables deposit on the lookbook; uses existing `integrations` collection config; DISPATCH_MODE-gated; refunds are manual
- **MEGAPHONE agent** — lookbook sends drain through existing `notification_logs` PENDING queue; no new agent tick required; `campaign_id=lookbook_id` stamps the log rows for analytics
- **Tally** — no direct integration; converted reservations become normal POS orders which flow into existing Tally sales-JV export via NEXUS

## Risk notes
- **Public URL with no auth**: token must be cryptographically random (UUID v4, 128-bit entropy); server must never leak token in logs or error messages; rate-limit `POST /public/lb/:token/reserve` per IP (20/hour) to prevent enumeration
- **Oversell risk**: "Reserve" does not lock stock (no `stock_units` mutation); two VIPs can reserve the same item; staff must manually adjudicate at conversion — acceptable for low-volume VIP use but document clearly; if stock reservation is needed, extend `lens_stock.reserve()` pattern or add a flag-based soft-hold on `stock_units`
- **Deposit money risk**: Razorpay deposit collected but refund is manual; if staff forget, customer dispute arises; flag in UI prominently; auto-refund requires explicit owner decision and additional Razorpay Refund API wiring
- **DLT template approval**: `LOOKBOOK_INVITE` WhatsApp template must be pre-registered with Meta/DLT by the owner before any live sends; build can be completed and tested in DISPATCH_MODE=test first
- **POS impact**: "Convert to Order" pre-fills cart but goes through normal POS discount-cap enforcement; no bypass — LOW risk
- **Feature flag**: guard the public `/lb/:token` route and the lookbook nav item behind env var `FEATURE_LOOKBOOKS=1` (default off); flip on only after DLT template is approved

## Recommendation
Build later (Phase 3, after core modules stable) — not a quick win because it needs DLT WhatsApp template approval (owner-gated, can block launch) and the public URL security surface needs careful review. Once MEGAPHONE is live and MSG91 is in `DISPATCH_MODE=live`, this feature is 8 days of focused work and high-ROI for VIP retention.

## Owner decisions
- Q: Should a holding deposit be mandatory, optional, or not collected at all for reservations? | Why: mandatory = Razorpay integration required + refund policy needed; optional = complexity added for a toggle; none = simpler build, reservation is a soft hold only | Options: a) No deposit — soft hold only (simpler, ships faster) / b) Optional deposit per lookbook / c) Mandatory deposit (most complex, highest commitment signal)
- Q: How long should a reservation hold last before it auto-expires? | Why: sets `hold_expires_at` server-side; too short = customer frustration; too long = inventory blocked | Options: a) 24 hours / b) 48 hours / c) 72 hours / d) Custom per lookbook (owner sets at create time, within 24-96h bounds)
- Q: Should the public lookbook page show the offer price (actual selling price) or only MRP? | Why: showing offer price signals the deal to the VIP but exposes pricing publicly; MRP-only is safer | Options: a) MRP only (safe default) / b) Owner toggles per lookbook whether offer price is shown
- Q: Which stores should have access to create lookbooks — all stores, or only flagship/HQ? | Why: determines whether STORE_MANAGER can create or only ADMIN/SUPERADMIN can | Options: a) ADMIN/SUPERADMIN only (centralized, brand-consistent) / b) STORE_MANAGER can create for their own store (decentralized)
- Q: Should the `LOOKBOOK_INVITE` WhatsApp template include the item names/images inline, or just a link? | Why: inline items require a rich-media template (more DLT approval effort); link-only is simpler and faster to approve | Options: a) Link-only message ("Your exclusive lookbook is ready: {url}") / b) Rich card with first item image + title + link
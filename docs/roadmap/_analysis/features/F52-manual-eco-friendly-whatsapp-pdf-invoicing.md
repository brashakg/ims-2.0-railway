# Feature #52: Manual Eco-Friendly WhatsApp PDF Invoicing
META: effort=M days=5 risk=MED roi=4 quickwin=no deps=none phase=3

## Existing overlap
- **WhatsApp dispatch infrastructure**: `backend/agents/implementations/megaphone.py`, `backend/agents/providers.py` (`send_whatsapp`), `DISPATCH_MODE` gate, quiet-hours enforcement (`backend/agents/quiet_hours.py`). MSG91 creds already in `integrations` collection.
- **Notification logging**: `notification_logs` collection written by `backend/api/services/notification_service.py`; delivery-status DLR webhook already updates it (`backend/api/routers/webhooks.py:338-426`).
- **Invoice data + serial**: `next_invoice_number` atomic counter in `backend/database/repositories/order_repository.py:446-507`; invoice fields (`invoice_number`, `invoice_date`, `grand_total`, `tax_amount`, CGST/SGST/IGST split) already on every order doc.
- **Prescription data**: `prescriptions` collection; `prescription_id` already stored on order items where applicable.
- **A5 Rx card HTML printer**: `backend/api/routers/clinical.py:1149-1318` (`_build_rx_card_html`) shows the HTML-to-print pattern reusable for PDF generation.
- **GST line-item breakdown**: `_compute_per_category_gst` in `backend/api/routers/orders.py:107-196` — already computes per-line taxable/CGST/SGST/IGST.
- **Customer mobile**: normalised 10-digit in `customers.mobile`; WhatsApp requires `91{mobile}`.
- **POS Review step**: `frontend/src/stores/posStore.ts` + `frontend/src/pages/pos/POSPage.tsx` — the "Review" step is where the Send button belongs.
- **RBAC pattern**: `require_roles` + `rbac_policy.py` already defines SALES_CASHIER / SALES_STAFF / STORE_MANAGER gating for POS actions.

## Reuse (extend, don't rebuild)
- `backend/api/routers/orders.py` — add a `POST /orders/{order_id}/send-invoice-whatsapp` endpoint here (alongside existing `add_payment`, `set_invoice`)
- `backend/api/services/notification_service.py` — extend `send_whatsapp` to accept a `media_url` (document) parameter for MSG91 document messages; queue row still goes to `notification_logs`
- `backend/agents/providers.py` `send_whatsapp()` — add `document_url` kwarg; MSG91 WhatsApp document message type (`type: "document"`)
- `notification_logs` collection — add fields `document_url`, `invoice_id`, `send_trigger` (`MANUAL_POS` vs future `AUTO`)
- `backend/api/routers/orders.py` `_compute_per_category_gst` — reuse for line-level tax rows in the PDF template
- `backend/agents/quiet_hours.py` `in_quiet_hours()` — reuse; invoices are transactional (not promotional) so quiet hours should NOT block, but the function is reused to log the IST timestamp
- `frontend/src/pages/pos/POSPage.tsx` Review step — extend with a "Send Invoice to WhatsApp" button (post-payment confirmation step)
- `frontend/src/pages/orders/OrdersPage.tsx` order detail panel — extend with same button for already-completed orders (resend)

## Data model
No new collection needed. Extend existing docs:

**`orders`** (add fields):
- `whatsapp_invoice_sent_at`: datetime (IST) — null until first successful send
- `whatsapp_invoice_sent_by`: user_id string
- `whatsapp_invoice_send_count`: int (default 0; cap enforcement)

**`notification_logs`** (add fields to existing schema):
- `document_url`: string (signed URL to the generated PDF)
- `invoice_id`: string (order's `invoice_number`)
- `send_trigger`: enum `MANUAL_POS | MANUAL_ORDERS | AUTO_FUTURE`

**`business_settings`** (add one field):
- `whatsapp_invoice_enabled`: bool (default false — owner opt-in per store or globally)

## Backend
- **`POST /api/v1/orders/{order_id}/send-invoice-whatsapp`** (new, in `orders.py`):
  1. Load order; assert `status` not in `[DRAFT, CANCELLED]` and `invoice_number` is set (auto-set it if missing, same as existing `set_invoice` flow).
  2. Resolve customer mobile; return 422 if absent or order has no customer (walk-in no-mobile).
  3. Generate PDF bytes in-process using `weasyprint` (or `xhtml2pdf`) from a Jinja2 HTML template (`backend/templates/invoice_whatsapp.html`) — branded, A4, includes: store logo URL, invoice serial, date, line items with HSN + taxable + CGST/SGST/IGST, grand total, Rx summary (if `prescription_id` present on any item), warranty line, QR code (UPI deeplink from `invoice_settings.upi_id` if set).
  4. Upload PDF to Railway-mounted `/uploads/invoices/{order_id}.pdf` (or object storage bucket already used by `handoffs.py` GridFS pattern) — return a signed/public URL.
  5. Call `send_whatsapp(phone=f"91{mobile}", document_url=url, caption=f"Invoice {invoice_number} from {store_name}")` via existing `providers.send_whatsapp` (extended with `document_url` param).
  6. Write `notification_logs` row (channel=WHATSAPP, template_id="INVOICE_DOCUMENT", status=PENDING, document_url, invoice_id, send_trigger=MANUAL_POS).
  7. Atomically `$inc` `whatsapp_invoice_send_count`, stamp `whatsapp_invoice_sent_at` + `whatsapp_invoice_sent_by` on order doc.
  8. Return `{ok: true, mode: "SENT"|"SIMULATED", notification_id}`.
  - Guards: `DISPATCH_MODE` gating (SIMULATED when off/test); send count cap (owner-decided, default 3 per order); `whatsapp_invoice_enabled` flag check; period-lock NOT applicable (read-only for invoice).

- **`GET /api/v1/orders/{order_id}/invoice-pdf`** (new, in `orders.py`):
  - Generates same PDF, returns as `application/pdf` `FileResponse` — for browser download / reprint. Same template, no MSG91 call.

- **`backend/templates/invoice_whatsapp.html`** (new Jinja2 template):
  - Light, restrained — store name/logo, address, GSTIN, invoice serial, date, customer name+GSTIN (B2B), line table (description, HSN, qty, MRP, taxable, GST%, CGST, SGST/IGST, line total), summary (subtotal, total GST, grand total), payment method(s), Rx summary block (conditional on prescription_id present), warranty text, eco-footer ("Go green — paperless invoice").

## Frontend
- **`frontend/src/pages/pos/POSPage.tsx` — Payment Confirmation step** (extend):
  - After payment recorded and `invoice_number` confirmed, show a "Send Invoice to WhatsApp" button (only if `customer.mobile` exists and `whatsapp_invoice_enabled` is true).
  - Button state: idle → loading → sent (shows tick + "Sent to +91-XXXXXX06") → error (retry allowed up to cap).
  - If customer has no mobile, show a one-field inline "Enter WhatsApp number" input (saved to order, NOT permanently to customer record — avoids accidental CRM overwrite of verified mobile).

- **`frontend/src/pages/orders/OrdersPage.tsx` — Order detail panel** (extend):
  - Add "Resend Invoice" button in the action bar (visible to STORE_MANAGER and above).
  - Shows `whatsapp_invoice_sent_at` timestamp + `send_count` badge if already sent.
  - Download PDF link (calls `GET /orders/{id}/invoice-pdf`).

- **`frontend/src/pages/settings/BusinessSettingsPage.tsx`** (extend):
  - Add a "WhatsApp Invoicing" toggle card: enable/disable globally, set max resend cap, set eco-footer text (single line, plain text only).

## Business rules
- Invoice must have `invoice_number` before sending — auto-assign if absent (same atomic counter flow as existing `set_invoice`).
- Only send to orders in status `CONFIRMED | PROCESSING | READY | DELIVERED | PAID`; block on `DRAFT | CANCELLED`.
- **Resend cap**: max N sends per order (owner decides N; default 3 in code). Exceeding → 409 with message "Resend limit reached; contact store manager."
- `DISPATCH_MODE=off|test`: returns `SIMULATED` response, no real MSG91 call, still writes `notification_logs` row with status=SIMULATED.
- If `invoice_number` is missing and order is in a period-locked month, do NOT auto-assign (return 423 "Period locked — invoice cannot be issued").
- PDF must always display CGST+SGST for intra-state, IGST for inter-state (resolved from store state vs customer state, same logic as `_split_output_tax` in `finance.py:330-360`).
- Audit: every send attempt (success or failure) written to `notification_logs`; `whatsapp_invoice_sent_by` stamped on order — immutable audit trail.
- No PII in PDF filename on public URL (use `order_id` UUID, not customer name).

## RBAC
- **Send invoice** (`POST .../send-invoice-whatsapp`): SALES_CASHIER, SALES_STAFF, STORE_MANAGER, AREA_MANAGER, ADMIN, SUPERADMIN — same roles that can complete a POS sale.
- **Resend from Orders page**: STORE_MANAGER, AREA_MANAGER, ADMIN, SUPERADMIN only (not cashier-level; resend is a manager action).
- **Download PDF** (`GET .../invoice-pdf`): all roles above + ACCOUNTANT (for audit/reconciliation).
- **Toggle setting** (`whatsapp_invoice_enabled`): ADMIN, SUPERADMIN only.
- OPTOMETRIST, WORKSHOP_STAFF, CASHIER (payment-only): no access.

## Integrations
- **MSG91 WhatsApp Business API** — extend existing `send_whatsapp` in `backend/agents/providers.py` with `document_url` parameter; MSG91 supports document messages (`type: "document"`, `url`, `filename`, `caption`). Requires a pre-approved HSM template for the document message OR use the "session message" window (24h after customer last message). Owner must check with MSG91 account whether document messages are enabled on their WABA number.
- **WeasyPrint / xhtml2pdf** — new Python dependency for server-side PDF generation from HTML template (no external API; runs in Railway container). WeasyPrint preferred (better CSS support for tables/layout); add to `requirements.txt`.
- **File storage** — PDF stored on Railway ephemeral disk (`/uploads/invoices/`) OR GridFS (same pattern as `handoffs.py`). If Railway restarts wipe the disk, GridFS is safer; generated PDFs are cheap to regenerate so ephemeral disk + regenerate-on-download is acceptable and simpler.
- **No Shopify / Razorpay / Tally changes required** — this is a pure post-sale notification.
- **NEXUS / Jarvis agents**: no agent changes needed. Future phase-2 could wire MEGAPHONE to auto-send invoices on order status → DELIVERED, but that is not in scope here.

## Risk notes
- **MSG91 WhatsApp document message type**: MSG91's WhatsApp API may require a pre-approved template for document messages outside the 24-hour session window. If most sends happen at point of sale (within seconds of the customer's last interaction or first contact), session window may cover it — but this needs MSG91 account verification before build. If templates are needed, there is a 1-3 day DLT/Meta approval lag.
- **PDF generation memory on Railway**: WeasyPrint can be memory-hungry for complex layouts. Keep the template simple (no embedded images beyond a small logo served via URL, no base64 inlining). On Railway's 512MB hobby plan this is fine; on 256MB it may OOM. Test with 10 concurrent generates.
- **Ephemeral file storage**: Railway's filesystem is wiped on redeploy. If using disk (not GridFS), PDF URLs break after redeploy. Recommendation: generate PDF on-demand (no persistent storage) — `GET /invoice-pdf` regenerates each time; `send-invoice-whatsapp` uploads to GridFS or returns a short-lived stream URL directly from MSG91's media upload API.
- **POS revenue-critical path**: the Send button is AFTER payment is recorded, not before — so a failure here cannot affect the sale. Still, wrap the entire endpoint in try/except and return a non-500 degraded response so the cashier is not blocked at close.
- **Feature flag required**: `whatsapp_invoice_enabled` in `business_settings` must default to `false`. The MSG91 WABA account and document-message capability must be confirmed live before enabling.
- **No quiet-hours block needed**: transactional invoices (not promotional) are exempt from DND restrictions under TRAI; but add a log comment in code for clarity.

## Recommendation
Build later (not a quick win). The MSG91 document-message template approval and WeasyPrint dependency are non-trivial setup steps. Sequence correctly: (1) confirm MSG91 WABA document-message support with owner's MSG91 account team, (2) get template approved, (3) build. Can ship in ~5 days once those gates clear. High ROI (reduces paper cost, captures accurate mobiles, improves brand perception) — worth doing in Phase 3 after core gaps are closed.

## Owner decisions
- Q: Should WhatsApp invoicing be enabled for all stores from day one, or rolled out store-by-store? | Why: Determines whether `whatsapp_invoice_enabled` is a global toggle or a per-store field (per-store adds a store_id field to `business_settings` or a new `store_settings` sub-doc — ~1 extra day of work). | Options: a) Global on/off (simpler, faster) / b) Per-store toggle (more control, slightly more build)

- Q: What is the maximum number of times a cashier can resend the same invoice to WhatsApp? | Why: Sets the `whatsapp_invoice_send_count` cap enforced server-side. Too low frustrates customers who change phones; too high risks spam flags on the WABA number. | Options: a) 3 times (recommended) / b) 5 times / c) Unlimited (not recommended — WABA spam risk)

- Q: Should the PDF include the patient's full prescription (SPH/CYL/AXIS/ADD) or only a summary line ("Spectacle Rx issued — collect card at clinic")? | Why: Full Rx on a WhatsApp-forwarded PDF is a privacy exposure (forwarded to family/friends). Summary line is safer. Affects the Jinja2 template and whether `prescriptions` data is fetched. | Options: a) Full Rx detail in PDF / b) Summary line only ("Rx on file, valid until DD-MM-YYYY") / c) No Rx mention at all

- Q: Where should the generated PDFs be stored — regenerate on every request (no storage cost, no broken links after redeploy) or persist in GridFS (stable URLs, small storage cost)? | Why: Regenerate-on-demand is simpler and avoids Railway ephemeral-disk risk, but means every "Resend" re-runs WeasyPrint. GridFS persists the file but adds GridFS cleanup logic. | Options: a) Regenerate on every send/download (recommended) / b) Persist in GridFS with 30-day TTL

- Q: Should the invoice PDF carry the store's logo image, or text-only branding? | Why: Logo requires a stable public URL (currently `business_settings.logo_url` stores it, but if it is a Railway-local path it will not render in WeasyPrint). Text-only is safe everywhere. | Options: a) Logo from a public URL (Vercel-hosted or CDN) / b) Text-only store name + address (safe, no dependency)
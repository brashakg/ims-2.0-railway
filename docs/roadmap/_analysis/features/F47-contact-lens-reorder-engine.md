# Feature #47: Contact Lens Reorder Engine
META: effort=M days=5 risk=MED roi=4 quickwin=no deps=none phase=3

## Existing overlap
Substantial foundation already exists:

- **CL refill status signal** — `backend/api/routers/crm.py` lines 469–600: `/customers/{customer_id}/cl-refill-status` computes `supply_days` from last CL order, `refill_due_date`, and `days_remaining` advisory. Modality logic (DAILY/MONTHLY/BIWEEKLY) and pack-size extraction already written.
- **MEGAPHONE dispatch** — `backend/agents/implementations/megaphone.py`: `_scan_rx_expiring()` pattern (lines 187–220) already scans customers, queues notifications via `notification_service.py`, respects DND quiet hours (21:00–09:00 IST) and `marketing_consent`. The Rx-expiry trigger is nearly identical in shape to a CL-reorder trigger.
- **Notification templates** — `notification_templates` collection: `RX_EXPIRY_90/30/7` templates exist. CL-reorder needs 2 new templates (reorder link, Rx-expired book-exam), same schema.
- **Shopify checkout link generation** — `backend/agents/nexus_providers.py` already calls Shopify Admin API. A Shopify draft-order or variant-level checkout URL can be generated via `POST /admin/api/2024-01/draft_orders.json` using the customer's `shopify_customer_id` (bridged in `customers` collection).
- **Prescription expiry** — `backend/database/repositories/prescription_repository.py`: `find_valid()` / `find_expiring_soon()` already surface valid vs expired Rx by customer. If Rx is expired, the routing branch (book exam vs direct reorder) is decidable in code.
- **MEGAPHONE tick & campaign audit** — `notification_logs` collection already records every outbound send with `customer_id`, `channel`, `template_id`, `status`, `campaign_id`. No new audit infra needed.
- **Campaign segments** — `backend/api/services/campaign_segments.py`: `rx_expiry` segment resolves customers with expiring Rx; extend the same pattern to resolve customers with expiring CL supply.

Nothing about this feature is greenfield. It is an extension of three existing subsystems: the CL refill signal, the MEGAPHONE scan loop, and the Shopify draft-order API.

## Reuse (extend, don't rebuild)
- `backend/api/routers/crm.py` — extend `cl_refill_status()` to return `shopify_checkout_url` (generated on demand if Shopify linked) and `rx_expired` boolean; used by both the agent and the frontend.
- `backend/agents/implementations/megaphone.py` — add `_scan_cl_reorder()` method alongside `_scan_rx_expiring()`; same scan → queue → drain pattern.
- `backend/api/services/campaign_segments.py` — add `cl_reorder_due` segment (customers where `days_remaining ≤ window_days` AND `marketing_consent=True`); reuses existing segment resolver shape.
- `backend/api/services/notification_service.py` — add two new template IDs (`CL_REORDER_LINK`, `CL_BOOK_EXAM`); same `_populate_template()` / `_queue_notification()` flow.
- `notification_templates` collection — insert two new docs (same schema); admin editable via existing `/api/v1/settings/notification-templates` endpoint.
- `customers` collection — add `cl_reorder_last_sent_at` field (ISO datetime) to prevent re-sending within the cooldown window; no schema migration needed (MongoDB is schemaless, field added on first write).
- `notification_logs` collection — no change; MEGAPHONE already writes here on every send; used for dedup check.
- `backend/api/routers/campaigns.py` — add `cl_reorder_due` as a valid segment key in the segment picker; owner can schedule one-time or recurring campaigns targeting this segment from the existing Campaign Builder UI.
- `frontend/src/pages/customers/Customer360Dashboard.tsx` — extend the existing CL refill card to show days remaining, supply status badge, and a "Send reorder link now" manual-trigger button (ADMIN/STORE_MANAGER only).

## Data model
No new collections. New fields on existing documents only:

- `customers` collection — add:
  - `cl_reorder_last_sent_at`: ISODate — timestamp of last reorder notification sent (prevents duplicate sends within cooldown).
  - `cl_reorder_opt_out`: Boolean (default false) — CL-reorder specific opt-out separate from blanket `marketing_consent`; owner decision on whether to expose this.

- `notification_templates` collection — insert two new template docs:
  - `CL_REORDER_LINK`: WhatsApp + SMS template; variables `{name}`, `{cl_brand}`, `{days_remaining}`, `{checkout_url}`.
  - `CL_BOOK_EXAM`: WhatsApp + SMS template; variables `{name}`, `{clinic_phone}`, `{store_name}`.

- `integrations` collection (`type: "cl_reorder_engine"`) — new config doc:
  - `alert_windows_days`: list of integers (e.g., `[14, 7, 3]`) — days-before-depletion to trigger alerts (owner-configurable, defaults owner must decide).
  - `cooldown_days`: integer — minimum days between two sends for the same customer.
  - `enabled_store_ids`: list — which stores participate (owner decision).
  - `shopify_checkout_mode`: `"draft_order"` | `"buy_now_link"` — how the Shopify link is generated.

## Backend

- **`GET /api/v1/crm/customers/{customer_id}/cl-refill-status`** (EXTEND existing) — add `shopify_checkout_url` (generated via Shopify draft-order API if `shopify_customer_id` present and `cl_product_id` resolvable from last CL order items), `rx_expired` boolean, `days_remaining` (already returned). No breaking change.

- **`POST /api/v1/crm/customers/{customer_id}/cl-reorder/send-link`** (NEW, ADMIN/STORE_MANAGER/AREA_MANAGER/SUPERADMIN) — manual one-shot trigger: resolves refill status, picks correct template (reorder vs book-exam), queues notification via `notification_service`, stamps `cl_reorder_last_sent_at`. Returns `{sent: bool, channel, template_id, checkout_url}`. Used by the Customer 360 manual-trigger button.

- **`GET /api/v1/crm/cl-reorder/segment`** (NEW, ADMIN/SUPERADMIN) — returns audience count and sample rows for the `cl_reorder_due` segment; mirrors existing segment-preview pattern in `campaign_segments.py`. Used by Campaign Builder audience picker.

- **`backend/agents/implementations/megaphone.py` — `_scan_cl_reorder()`** (NEW method, called on every MEGAPHONE 30-min tick):
  1. Read `cl_reorder_engine` config from `integrations` (get alert windows, cooldown, enabled stores).
  2. For each enabled store, query customers with CL prescription history (use `prescriptions` collection `rx_kind=CONTACT_LENS` + `is_valid`).
  3. For each customer call `cl_refill_status()` logic (reuse crm.py helper, extracted to a shared service function).
  4. If `days_remaining` falls within any alert window AND `cl_reorder_last_sent_at` is outside cooldown AND `marketing_consent=True` AND not in `cl_reorder_opt_out`:
     - If Rx valid → queue `CL_REORDER_LINK` with Shopify checkout URL.
     - If Rx expired → queue `CL_BOOK_EXAM` with clinic phone.
  5. Stamp `cl_reorder_last_sent_at` on customer doc.
  6. Write to `notification_logs` (existing MEGAPHONE drain path handles actual send).

- **`backend/agents/nexus_providers.py` — `shopify_create_draft_order(customer_id, variant_id, qty)`** (NEW helper) — calls Shopify `POST /admin/api/2024-01/draft_orders.json`; returns `invoice_url` (Shopify's pre-filled checkout link). Gated on `DISPATCH_MODE=live` + Shopify creds. Returns SIMULATED URL in test/off mode. Used by both the manual endpoint and the MEGAPHONE scan.

## Frontend

- **`frontend/src/pages/customers/Customer360Dashboard.tsx`** (EXTEND) — the existing CL refill card already shows `days_remaining` and `refill_due_date`. Add:
  - Supply status badge: `SAFE` (>14d, green) / `RUNNING LOW` (7–14d, amber) / `URGENT` (<7d, red) / `EXPIRED` (gray) — color used only for semantic meaning per design constraint.
  - "Send Reorder Link" button (visible to ADMIN/STORE_MANAGER only, hidden for SALES_STAFF/CASHIER): calls `POST /cl-reorder/send-link`, shows inline success/error toast.
  - If Rx expired, button label changes to "Send Book-Exam Link".

- **`frontend/src/pages/marketing/CampaignBuilderPage.tsx`** (EXTEND existing Campaign Builder) — add `cl_reorder_due` to the segment picker dropdown alongside existing `rx_expiry`, `birthday`, `winback`. Audience count fetched from the new `GET /cl-reorder/segment` endpoint. No new page needed.

- **`frontend/src/pages/settings/IntegrationsPage.tsx` or a new Settings sub-tab** — CL Reorder Engine config panel: alert windows (multi-value chip input), cooldown days (number input), enabled store picker (multi-select), Shopify checkout mode toggle. Saves to `integrations` collection via existing `/api/v1/settings/integrations` pattern.

## Business rules
- A customer must have `marketing_consent=True` to receive any notification (enforced identically to existing MEGAPHONE sends, `campaigns.py` line 710–717).
- Notifications respect DND quiet hours 21:00–09:00 IST (existing `agents/quiet_hours.py`; MEGAPHONE drain already checks this).
- Cooldown period must be enforced: no two CL-reorder sends to the same customer within `cooldown_days` (prevent spam); enforced via `cl_reorder_last_sent_at` field comparison.
- Routing branch is deterministic — no ambiguity: if the customer's most recent CL Rx (any CONTACT_LENS kind prescription) has `expiry_date < today`, send book-exam link; otherwise send reorder link with Shopify URL.
- Shopify checkout URL generation is fire-and-forget fail-soft: if Shopify creds absent or `shopify_customer_id` not bridged, notification is still sent without a URL (template variable `{checkout_url}` falls back to store phone number or empty string — owner to decide fallback text).
- Reorder supply calculation reuses existing `cl_refill_status()` logic exactly; no new math. If pack-size cannot be resolved from last order items, skip that customer and log a warning (fail-soft, no crash).
- The `CL_REORDER_LINK` WhatsApp template must be DLT-registered before sending (MSG91 requirement); template text is set at config time, not at runtime. MEGAPHONE always includes `DLT_PE_ID` in the send payload.
- No financial transaction is initiated by this feature. The Shopify draft-order link takes the customer to Shopify's own checkout; payment flows through Shopify/Razorpay, not through IMS. No IMS order is created at send time.

## RBAC
| Role | Can see CL refill card on Customer 360 | Can trigger manual send | Can configure engine settings |
|---|---|---|---|
| SUPERADMIN | Yes | Yes | Yes |
| ADMIN | Yes | Yes | Yes |
| AREA_MANAGER | Yes | Yes (own stores) | No |
| STORE_MANAGER | Yes | Yes (own store) | No |
| ACCOUNTANT | No | No | No |
| CATALOG_MANAGER | No | No | No |
| OPTOMETRIST | Yes (read-only refill status) | No | No |
| SALES_CASHIER / SALES_STAFF | Yes (read-only) | No | No |
| CASHIER / WORKSHOP_STAFF | No | No | No |

MEGAPHONE agent runs as system (no user context); existing agent SUPERADMIN gate on `/jarvis/*` unchanged.

## Integrations
- **MSG91 (WhatsApp + SMS)** — existing MEGAPHONE dispatch path; two new DLT-registered templates required before go-live.
- **Shopify Admin API** — new `shopify_create_draft_order()` helper in `nexus_providers.py`; gated on `DISPATCH_MODE=live` and existing Shopify integration creds in `integrations` collection.
- **Jarvis / MEGAPHONE agent** — `_scan_cl_reorder()` added to the 30-min MEGAPHONE tick; no new agent or scheduler needed.
- **No Razorpay / Tally / Shiprocket involvement** — payment and accounting happen entirely on Shopify's side.

## Risk notes
- **Shopify draft-order API is a write call** — gated by the triple lock (`IMS_SHOPIFY_WRITES=1`, `DISPATCH_MODE=live`, creds present) inherited from `nexus_providers.py`. In test/off mode the URL is SIMULATED and no Shopify order is created. Needs explicit owner action to go live.
- **DLT template registration** — MSG91 requires pre-approval of WhatsApp/SMS templates in India. The two new templates (`CL_REORDER_LINK`, `CL_BOOK_EXAM`) must be submitted and approved before the feature can send to real customers. This is an operational step, not a code step, but it gates the live launch.
- **`cl_refill_status()` accuracy depends on order data quality** — if pack-size or modality is not captured on the CL order items (older orders may lack this), `days_remaining` cannot be computed and the customer is silently skipped. The existing `crm.py` helper already handles this with a fail-soft skip; no new risk introduced.
- **Shopify customer bridge gaps** — if `shopify_customer_id` is not set on an IMS customer (not yet bridged), the reorder link falls back to store contact. This is an existing data-quality gap, not introduced by this feature.
- **No POS or accounting risk** — this feature has no write path to `orders`, `returns`, `stock_units`, or any financial collection. It is read + notify only on the IMS side.
- **Feature flag**: Add `cl_reorder_engine.enabled` boolean to the `integrations` config doc (defaults `false`). The MEGAPHONE scan method checks this flag before running. Owner flips it to `true` after DLT templates are approved and Shopify connection is verified.

## Recommendation
Build later — after verifying MSG91 DLT templates are approved and Shopify customer bridging (`shopify_customer_id`) is populated for the active CL customer base. The IMS-side code is a 3–4 day build against solid existing rails, but the feature is inert until both external dependencies (DLT + Shopify) are unblocked. Prioritise those operational steps first; schedule the code sprint in parallel.

## Owner decisions
- Q: What are the alert windows (days before supply runs out) that should trigger a notification? | Why: Determines how many sends a customer gets per supply cycle — e.g., `[14, 7, 3]` means three messages; `[7]` means one. More sends = more revenue but more opt-outs. | Options: a) Single alert at 7 days (minimal friction) / b) Two alerts at 14 and 7 days (balanced) / c) Three alerts at 14, 7, 3 days (maximum coverage)
- Q: What is the minimum cooldown between two CL-reorder notifications for the same customer? | Why: Prevents the same customer from getting duplicate sends if the MEGAPHONE tick fires multiple times near a window boundary. | Options: a) 5 days / b) 7 days / c) 14 days
- Q: Should customers who have opted out of general marketing (`marketing_consent=False`) still receive CL reorder links, since these are supply/health reminders rather than promotional messages? | Why: If yes, a separate `cl_reorder_opt_out` field is added and the consent gate bypassed for this trigger; if no, existing `marketing_consent` gate applies and opted-out customers are silently skipped. | Options: a) Respect existing `marketing_consent` (simpler, fewer sends) / b) Treat as service communication, bypass marketing consent (more reach, needs legal review under DPDP)
- Q: Which stores should this feature be enabled for at launch? | Why: Determines `enabled_store_ids` in the config; allows a phased rollout (e.g., only Better Vision stores first). | Options: a) All stores at once / b) One pilot store first / c) All Better Vision stores, WizOpt later
- Q: What should the Shopify checkout link fall back to if a customer's `shopify_customer_id` is not bridged or Shopify is not connected? | Why: The notification still goes out but the `{checkout_url}` variable needs a value. | Options: a) Include store WhatsApp number ("Call us to reorder") / b) Include a static Shopify storefront URL (bettervision.in) / c) Skip sending entirely if no checkout URL can be generated
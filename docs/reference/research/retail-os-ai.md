# Research — Retail-OS, omnichannel, AI + optical depth

> Total Coverage research buckets C/D + optical-specific. Each item notes the existing IMS foundation. See [`COMPETITOR_FEATURE_GAP.md`](COMPETITOR_FEATURE_GAP.md) + [`../IMS2_FEATURE_ROADMAP.md`](../IMS2_FEATURE_ROADMAP.md).

## Omnichannel / unified commerce
- **P1 — Single source of truth for stock:** wire IMS→Shopify inventory sync into the live POS sale/return path (oversell guard). *IMS:* STRONG — `nexus_providers.shopify_set_inventory_available` (absolute set, idempotent) + `shopify_pull_orders` + `online_store_orders` mapping exist, but gated behind `IMS_SHOPIFY_WRITES=0` and not invoked from `orders.py`/`returns.py` per sale. Needs a product↔Shopify-variant map + a fail-soft hook in order-finalise + restock. (This is the BVI cutover.)
- **P2 — BOPIS / ship-from-store / endless-aisle:** *IMS:* PARTIAL — `transfers.py` + `/transfer-recommendations` + walkouts (lost-sale logging) + portal tracking exist; missing the counter flow that, on an out-of-stock SKU, finds the nearest store with stock, creates the sale + a linked transfer/reservation, tracks to pickup.
- **P3 — Marketplace/ONDC listing + unified stock sync.** Largest, most strategic; sequence AFTER single-inventory sync is solid (multi-channel amplifies any duplicate-write risk).

## Optical-specific
- **P1 — Contact-lens auto-refill/subscription:** predicted run-out (pack-size × daily-wear) → WhatsApp reorder nudge → one-tap reorder draft. *IMS:* PARTIAL — `inventory.py /contact-lenses` + expiry-status + power-grid + `follow_ups.py` + MEGAPHONE exist; add the per-customer run-out date + the auto refill-due trigger + one-tap reorder. **Fix the UTC-vs-IST DND bug (`megaphone.py:71`) before going live.**
- **P2 — Appointment booking + in-store queue link:** public/WhatsApp eye-test booking dropping into the existing optometry queue, slot mgmt + reminders. *IMS:* clinical queue (WAITING/IN_PROGRESS/COMPLETED/NO_SHOW) + OTP portal + follow-ups exist; missing bookable slots + the public booking endpoint.
- **P2 — Demand forecasting + nightly auto-reorder** with safety stock + lead-time, per store + per SKU/lens-power. *IMS:* reorder_point + alert tiers + sell-through/overstock/non-moving + velocity exist; add the seasonality-aware forecast → reviewable draft PO + lens-power-grid gap detection.
- **P2 — Return-abuse / serial-returner detection:** per-customer return-rate scoring + a soft-flag at the refund step. *IMS:* returns hard-guards (qty cap, atomic over-refund, serialized restock) + CRM churn/RFM exist; add the aggregate return-rate signal on customer-360.
- **P3 — Frame+lens manufacturability pre-check / lens-job spec + remake/warranty + lab scorecards.** *IMS:* `lens_catalog_validation` + workshop QC/rework + vendor-portal status exist; add structured fitting params (PD/seg-height) + a remake-reason taxonomy + per-lab scorecards.
- **P3 — Virtual try-on + PD capture** on the storefront/portal. *IMS:* none in-repo; integrate a proven SDK (Fittingbox/Banuba/MirrAR/Auglio) + store captured PD back on the customer/Rx. The PD-capture half is the high-value part (feeds dispensing).

## AI / agentic (on top of the existing 8-agent Jarvis layer)
- **P3 — RAG staff-copilot in POS/clinical:** "ask Jarvis" grounded on SOPs, lens-compatibility rules, pricing/discount caps, warranty/remake policy. *IMS:* JARVIS chat + `claude_client.py` exist (SUPERADMIN-only today); needs a retrieval layer + a deliberate role-scoping decision (non-SUPERADMIN read-only use) + an in-POS entry point.
- **P3 — Two-way WhatsApp conversational commerce:** inbound webhook + a lightweight conversation/inbox + template-button actions (Book eye test / Reorder lenses) routed through JARVIS/CORTEX. *IMS:* OUTBOUND MEGAPHONE works; inbound is net-new (Meta WABA + webhook).
- **P3 — CV for product images** (background edit / shadows / try-on / shelf audit) — *being scoped via a dedicated council; see the image-editing integration decision.*
- **P3 — Regional-language (Hindi/Marathi) UX.**

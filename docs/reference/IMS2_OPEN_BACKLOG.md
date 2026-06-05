# IMS 2.0 - Open Backlog (consolidated 2026-06-06)

Consolidated 5 raw backlog lanes (~130 raw items) into ONE owner-facing approval list. After dropping items already shipped this session and deduping aggressively across lanes (e-invoicing appeared 3x; GSTR-2B, DPDP, inter-GSTIN mirror, offline POS, IMS->Shopify sync, commission ledger, vendor-SKU-alias, cycle-count, credit-limit/khata, demand-forecast, return-abuse, e-way bill, GSP filing, churn-risk, NPS-on-delivery, progressive-fitting, manufacturability, EHR, referral program, promo templates, bank-recon, RAG copilot, inbound WhatsApp, Hindi/Marathi, and ~20 owner-gated infra/cred items each appeared 2-3x), 92 distinct open items remain. They fall into 13 themes. The dominant lanes are Finance/GST + Compliance (GST e-invoicing, GSTR-2B/3B filing, DPDP consent, inter-GSTIN mirror, plus a cluster of dashboard-fabrication defects), Online-Store/BVI (cutover ops + a tight cluster of FE<->BE seam defects + Shopify creds), and POS/Orders (offline-first, EMI/loyalty correctness, credit-limit). Counts: 92 total open / 18 P1 / 47 P2 / 27 P3; 41 are owner-gated (need creds, sign-off, or a cutover decision). The owner-gated set is mostly the "go-live keystone": resize Mongo volume, paste Shopify+integration creds, set auth secrets, run the BVI migration, and flip the cutover — these unblock a large fraction of the gated features at once. Highest-leverage non-gated P1 work is the money/correctness defect cluster (discount_category dropped on create -> luxury over-discount; GST 50/50 split bug; MEGAPHONE UTC/IST DND bug; scheduler singleton guard; ensure_indexes per-index hardening). Each surviving item carries a stable ref (FIN-, POS-, INV-, etc.), is deduped to a single best phrasing, and is ranked P1>P2>P3 then effort within its theme.

Counts: {"total_open": 92, "p1": 18, "p2": 47, "p3": 27, "owner_gated": 41}

## Compliance & GST (statutory)

- **[FIN-1]** P1/L **[OWNER-GATED]** - GST e-invoicing (IRN + signed QR) via per-GSTIN GSP/IRP at billing
  - Wire a GSP/ASP client to mint IRN + signed QR per GSTIN at billing (with 24h-cancel + QR render) because B2B e-invoicing is legally mandatory at scale across the 4 GSTINs and only GSTR export exists today; needs an owner-provided GSP account.
- **[FIN-2]** P1/L - DPDP Act 2023 consent ledger + withdrawal + purpose-based retention
  - Build a consent-ledger collection, withdrawal endpoint, purpose tagging, retention job and itemised notice because no consent concept exists today and DPDP is a legal obligation for customer PII.
- **[FIN-3]** P1/M - Inter-GSTIN transfer auto-books the mirror purchase (ITC in receiving entity)
  - When transfers.py moves stock between GSTINs, auto-create the mirror purchase-in with ITC in the receiving GSTIN because an inter-GSTIN transfer is a taxable supply and the 3-entity/4-GSTIN structure requires the booking for correctness.
- **[FIN-4]** P1/M - GSTR-2B auto-fetch + one-click ITC reconciliation UI
  - Add a 2B file importer/parser plus an actionable Finance UI and vendor-chase task creation on top of the already-built itc_reconcile.py buckets so unmatched ITC-at-risk surfaces and gets chased automatically.
- **[FIN-5]** P2/S - Hard-block invoice generation when store GSTIN is missing
  - GSTIN presence is only a soft WARN today, so a tax invoice can be issued for a store with no GSTIN; make invoice creation hard-block (422) on missing store GSTIN.
- **[FIN-6]** P2/M **[OWNER-GATED]** - E-way bill generation + register for inter-state / high-value movement
  - Generate e-way bills (JH/MH-aware, reusing the GSTN state-code map + the e-invoice GSP) for inter-state JH<->MH movement over Rs 50k because only a dead e_way_bill_enabled flag exists; rides the e-invoice GSP account.
- **[FIN-7]** P2/M **[OWNER-GATED]** - Direct GSTR-1/3B/2B GSP filing + two-way Tally (replace manual offline-tool upload)
  - Add the GSP submit API plus a multi-GSTIN filing-status UI and live 2-way Tally bridge on top of the existing gstn_export/balanced-JV builder so returns file directly instead of manual offline-tool uploads; needs GSP creds.
- **[FIN-8]** P2/S **[OWNER-GATED]** - Chart-of-accounts mapping for Purchase-JV / ITC GSTR-2B
  - Purchase-JV and ITC reconciliation need an accountant-provided chart-of-accounts mapping before they can post correctly.
- **[FIN-9]** P2/S **[OWNER-GATED]** - SMARTGLASSES GST/HSN classification sign-off (currently flagged GST-REVIEW)
  - Smart/electronic eyewear is left at 18% under HSN 852580 as a flagged placeholder pending accountant confirmation, since it could legally fall under 5% corrective eyewear -- a money/legal decision needing owner sign-off.
- **[FIN-10]** P3/S - GST-compliant invoice numbering -- FY-serial atomic counter
  - Add the consecutive-per-financial-year, per-GSTIN atomic counter (the uniq_invoice_number index backstop already exists) so numbering is legally compliant instead of random hex.
- **[FIN-11]** P3/M - TDS/TCS threshold gating + 206C(1H) + quarterly 26Q/27EQ export
  - Add per-counterparty cumulative tracking, the threshold switch-on, 206C(1H), and quarterly 26Q/27EQ returns because compute_tds has CA-verified rates but explicitly enforces no thresholds and 206C is absent.

## Finance/GST defects & honesty

- **[FIND-1]** P1/S - GST summary cards split all output tax 50/50 CGST/SGST (never IGST)
  - finance.py:717 splits all output tax 50/50 CGST+SGST and never IGST, mis-classifying inter-state sales on the dashboard; honor the inter-state split.
- **[FIND-2]** P2/S - Tally sales JV drops IGST on inter-state vouchers
  - The Tally sales JV drops IGST on inter-state vouchers, producing an unbalanced/incorrect export; add the IGST ledger line to the voucher.
- **[FIND-3]** P2/S - Budgets tab seeds fabricated allocations when no budget doc exists
  - finance.py:1673 invents budget allocations when no budget doc exists, showing fake numbers; replace with an honest empty state.
- **[FIND-4]** P2/S - P&L COGS 60% fallback presents a fabricated margin as real
  - P&L uses a 60% COGS fallback that can present a fabricated margin; flag the value as estimated rather than reporting it as real.
- **[FIND-5]** P3/M - Bank statement import + auto-reconciliation vs receipts/payments
  - Add bank-statement import with auto-matching against recorded receipts/payments because no bank reconciliation exists today.
- **[FIND-6]** P2/S - Wire finance P&L-by-store endpoint into the UI
  - The /finance/pnl/by-store endpoint exists but is barely surfaced; add the report screen so profit-by-store is actually visible (hours, not days).
- **[FIND-7]** P2/M - Dual-mode budgeting (full-ops vs survival)
  - Add a Superadmin-toggled mode that switches expense-head budgets between full-operations and survival targets -- the one genuine remaining Finance gap.

## POS / Orders

- **[POS-1]** P1/L **[OWNER-GATED]** - Offline-first POS (service worker + idempotency-keyed replay queue)
  - Add a service worker + idempotent replay queue (PWA manifest exists, no service worker) so POS keeps billing through outages, neutralizing TechCherry/Lensorix's offline edge; needs POS sign-off (revenue-critical).
- **[POS-2]** P2/S - POS EMI tender records only the down-payment, not the financed balance
  - An EMI tender records only the down-payment on the order, leaving the financed balance untracked; record the full EMI schedule/balance.
- **[POS-3]** P2/S - Loyalty points redeemed before the order exists (spend on a failed order)
  - Loyalty points are redeemed before order create, risking spend on a failed order; defer/reverse the redeem to post-create.
- **[POS-4]** P2/M - Per-customer credit-limit (khata) guard + party-wise outstanding at POS
  - Enforce a per-customer credit-limit guard with a running khata / party-wise outstanding view at POS on top of the existing CREDIT tender + AR, for Lensorix-style B2B/wholesale parity.
- **[POS-5]** P2/M - Explicit exchange flow (return -> pick replacement -> price-adjust)
  - Only partial exchange refs exist; build the full return->select-replacement->adjust-price flow, but confirm with owner first since it is POS-adjacent and revenue-critical.
- **[POS-6]** P2/M **[OWNER-GATED]** - Dynamic UPI QR on POS bills + auto payment reconciliation
  - Render an NPCI-spec dynamic UPI QR per order (Razorpay/Bharat-QR per store-VPA) and auto-match order<->UPI-credit because Razorpay is registered but unwired and NEXUS already handles webhooks; needs Razorpay creds.
- **[POS-7]** P2/L - BOPIS / ship-from-store / endless-aisle counter flow
  - On an out-of-stock SKU, find the nearest store with stock and create the sale plus a linked transfer/reservation tracked to pickup, on top of existing transfers/recommendations/portal-tracking pieces.
- **[POS-8]** P2/S - Tasks create with default-today due date rejected as past-dated (422)
  - Creating a task with the default today due-date is rejected as past-dated (422); send an end-of-day/now+1h due time so same-day tasks save.
- **[POS-9]** P3/S - Server-side length cap + sanitization on order/cart text fields
  - XSS/10k-char/unicode/null-byte payloads in order notes and product_name are stored verbatim with no cap or sanitization; add server-side limits to protect receipts/Tally/reports.
- **[POS-10]** P3/S - item_note / order_type dropped on order create
  - item_note and order_type are dropped on order create; persist these so per-line notes and order type survive.
- **[POS-11]** P3/S - cancelOrder sends reason as raw body, not the route's contract shape
  - cancelOrder sends the reason as a raw body that doesn't match the route contract; send the reason in the shape the endpoint expects.
- **[POS-12]** P3/S - Order status timeline/history with timestamps
  - Record and display each order's status-change history with timestamps so staff can see when an order moved Draft->Ready->Delivered.
- **[POS-13]** P3/M - Footfall Tracking page is a 'Coming soon' stub
  - The /pos/footfall route renders only a placeholder and the nav item is commented out, so store walk-in capture is unbuilt despite being wired into analytics.
- **[POS-14]** P3/M - Extend Idempotency-Key to payments / returns / expense-create writes
  - Order-create honors Idempotency-Key but other double-submit-prone writes (payments, refunds, expense create) don't, so retries can duplicate financial records; extend the same guarded pattern.

## Inventory / Catalog

- **[INV-1]** P1/S - Catalog drops discount_category on product create (luxury over-discount risk)
  - products.py:276 drops discount_category on create, so LUXURY items fall back to the MASS 15% cap and can be over-discounted (money); persist discount_category on create.
- **[INV-2]** P2/M **[OWNER-GATED]** - Backfill products.product_id (10,805 of 10,820 null)
  - Nearly all imported catalog products have product_id:null which blocks the unique index; backfill from sku/_id to restore catalog identity (deferred pending owner sign-off on approach).
- **[INV-3]** P2/S **[OWNER-GATED]** - Resolve 5 genuine duplicate product SKUs
  - Five products share duplicate SKUs (e.g. 'ASPIRE PRO' x2) colliding on the unique sku index; needs an owner decision to rename/merge before the index can enforce uniqueness.
- **[INV-4]** P2/S **[OWNER-GATED]** - Clear empty-string products.barcode values (612 products)
  - 612 products have barcode:'' (empty string, not null) colliding on the sparse unique index; convert '' to unset so the sparse index works (owner held the clear).
- **[INV-5]** P2/M - Inventory Transfers FE envelope/field/status seams
  - StockTransfer screens treat the {transfers:[...]} envelope as a raw array, mislabel from_location_id vs from_store_id, crash the status badge on enum case, and .filter() an {items} object; fix the seams (coordinate on inventory.ts).
- **[INV-6]** P2/S - Category-scoped stock count filters a non-existent field
  - Category-scoped stock count filters on a field that doesn't exist; resolve product_ids by category first so category-limited counts return real items.
- **[INV-7]** P2/M - Vendor-SKU-alias -> single lens master at goods-inward
  - Map each vendor's SKU alias to one canonical lens master so the same lens from different suppliers doesn't duplicate at goods-inward (Lensorix-parity gap).
- **[INV-8]** P2/M - Guided cycle-count -> variance -> reconcile (with shrinkage reporting)
  - Add a guided physical count -> variance -> reconcile flow (non-moving report exists but no guided count) for Lensorix/OpticalCRM-parity stock-audit.
- **[INV-9]** P2/M - Demand forecast -> nightly draft-PO suggestions (+ lens-power-grid gap)
  - Extend reorder logic with seasonality + safety-stock + lead-time (per store + per SKU/lens-power) to emit reviewable nightly draft POs plus lens-power-grid gap detection.
- **[INV-10]** P2/M **[OWNER-GATED]** - Shiprocket shipment/tracking is mocked in transfers (4 sites)
  - Stock-transfer create/track fabricate a fake SR_ order id at four sites, so inter-store transfer logistics aren't really integrated; needs Shiprocket credentials.
- **[INV-11]** P3/S - Transfer receive-payload assumes a line.id that isn't reliably present
  - The transfer-receive payload assumes a line.id that isn't reliably present; thread the real line id through the receive path.
- **[INV-12]** P3/M - Barcode lifecycle audit trace view (purchase->sale->transfer->return)
  - Add a single trace lookup for a barcode's full movement history (recorded across modules but not unified) to satisfy Audit-Everything.
- **[INV-13]** P3/M - Vendor performance scoring + purchase-history analytics
  - Score vendors on delivery/quality and surface purchase-history analytics so buying decisions and vendor follow-ups are data-driven, not WhatsApp-tracked.
- **[INV-14]** P3/M **[OWNER-GATED]** - Product stock-photo gallery per SKU
  - Add a structured per-SKU photo-gallery workflow so products carry multiple images for catalog and online listing; needs an image-storage bucket.
- **[INV-15]** P3/S - Retire legacy split-brain product importer + dead bulkImportProducts
  - Retire the legacy importer and dead bulkImportProducts method and fix lens-pricing camel-vs-snake key drift to remove goods-inward confusion.

## Online-Store / BVI

- **[BVI-1]** P1/M **[OWNER-GATED]** - Wire live IMS->Shopify inventory sync into the POS sale/return path
  - Add a product<->Shopify-variant map and a fail-soft hook in order-finalise/restock to invoke the existing shopify_set_inventory_available (gated behind IMS_SHOPIFY_WRITES=0) so online stock can't oversell after an in-store sale; blocked on BVI cutover/creds.
- **[BVI-2]** P1/S **[OWNER-GATED]** - Paste Shopify creds + register order webhooks + set location ID
  - The DARK Shopify push engine is gated behind shop_url+access_token+webhook_secret + SHOPIFY_ONLINE_LOCATION_ID + registered order webhooks; owner must paste these to enable any live online-store sync.
- **[BVI-3]** P1/M **[OWNER-GATED]** - Provision durable image bucket + re-host BVI /uploads/ assets (BVI step 7)
  - BVI image URLs point at local disk, a HARD cutover prereq; owner must provision an S3/R2 bucket then run the /uploads/ asset re-host job (uses object_storage.py) before the Shopify migration is safe.
- **[BVI-4]** P1/M **[OWNER-GATED]** - Execute the BVI Postgres->Mongo PIM migration for real
  - The migration script + 4 mappers are written and tested in --dry-run only; owner must set ECOMMERCE_DATABASE_URL (BVI PG 18.3) + confirm a PG18 client so the real catalog/collection/menu/image load can run.
- **[BVI-5]** P1/M **[OWNER-GATED]** - Atomic Shopify single-writer cutover flip
  - Cutover requires setting IMS_SHOPIFY_WRITES=1 AND scaling the legacy BVI writer read-only in ONE sitting plus repointing Shopify webhooks, to preserve the exactly-one-writer invariant; owner-triggered by design.
- **[BVI-6]** P2/M - Online Store collection/menu/image id normalization in FE mappers
  - Backend rows are keyed collection_id/menu_id/image_id but the FE reads .id, leaving every row action undefined; normalize id in services/api/onlineStore.ts mappers.
- **[BVI-7]** P2/S - Online Store add/reorder/remove product SKU-keying mismatch (422s)
  - addProduct/reorder/removeProduct send product_id/product_ids while the backend wants sku/skus, causing 422s and wrong-key deletes; align the FE to SKU keys.
- **[BVI-8]** P2/S - Online Store single-object envelope left un-unwrapped
  - FE get/create/update return the {collection:...} envelope un-unwrapped so .id/.smart_rules are undefined; unwrap the single-object responses in the service.
- **[BVI-9]** P2/M - Online Store smart-rules payload shape reconciliation
  - FE nests smart_rules{field,op,value} but the backend wants top-level rules[{field,relation,value}] + disjunctive, so smart rules are silently dropped on save; reconcile both ways.
- **[BVI-10]** P2/S - Online Store collection-preview endpoint mismatch (404)
  - Preview posts POST /collections/resolve which doesn't exist (only GET /{id}/resolved-products), returning 404; point the FE at the real route.
- **[BVI-11]** P2/M - Wire BVI/Online-Store SSO + unified nav into the IMS shell
  - While the legacy BVI Next.js app still runs separately, an SSO bridge + unified Online Store nav entry are needed so staff don't manage a second login/admin domain.
- **[BVI-12]** P2/S **[OWNER-GATED]** - Activate Photoroom image-editing pipeline (key + storage + template sign-off)
  - The auto-edit catalog-image pipeline ships disabled/fail-soft; owner must set PHOTOROOM_API_KEY + an S3/R2 bucket and sign off the catalog template (backdrop/ratio/shadow) to turn it on.
- **[BVI-13]** P2/M - Customer order-tracking QR / short-link (no login)
  - Generate a per-order QR/short-link that lets a customer track order status without logging in, replacing WhatsApp status-chasing.
- **[BVI-14]** P3/S - Online Store menus addItem/moveItem payload shape mismatch
  - Menus addItem posts a flat body where backend wants {item:{...}} and moveItem posts {parent_id} vs {new_parent_id}; align both (dormant since MenusPage uses saveTree).
- **[BVI-15]** P3/S - Online Store summary counts shape mismatch (cards show 0)
  - The online-store summary count response shape doesn't match the FE so module cards display 0; map the summary shape.
- **[BVI-16]** P3/L - Online Store hub: several BVI sections still 'Coming soon' pills
  - Several Online-Store sections render a 'Coming soon' pill with no href because the BVI merge is ~90% but those module routes aren't live yet.
- **[BVI-17]** P3/M - Self-host BiRefNet background-removal fallback
  - Build the rembg/BiRefNet (MIT) provider path already seamed in image_editor.py as the no-per-call-cost fallback to Photoroom for background removal.
- **[BVI-18]** P3/L **[OWNER-GATED]** - Marketplace / omnichannel unified panel (Amazon/Flipkart + stock sync)
  - Build a unified Amazon/Flipkart/Shopify control panel with omnichannel stock sync (sync_marketplace_channel is a SIMULATED stub) so an online sale decrements store stock; needs marketplace seller accounts/keys.
- **[BVI-19]** P3/L **[OWNER-GATED]** - Multiple-website management (category-specific storefronts)
  - Support running multiple category-specific storefronts (e.g. a watches-only site) from one catalog; needs Shopify/domain setup and owner direction.
- **[BVI-20]** P3/L **[OWNER-GATED]** - ONDC seller node (Seller-NP via an SNP) with TCS-payout reconciliation
  - Connect IMS as an ONDC seller via an SNP with TCS-payout reconciliation, sequenced after single-inventory sync; catalog foundation exists but no ONDC connector does.

## Clinical / Optometry / Workshop

- **[CLI-1]** P2/S - Rx-history button passes queue/test id as customer_id (404)
  - The Rx-history button passes a queue/test id where a customer_id is expected, returning 404; guard the button on a real customerId.
- **[CLI-2]** P2/L **[OWNER-GATED]** - Appointment booking + optometrist diary feeding the walk-in queue
  - Add bookable slots, a public/WhatsApp eye-test booking endpoint, slot mgmt + reminders that drop into the existing optometry queue (only a same-day walk-in queue exists today); WhatsApp path needs MSG91 creds.
- **[CLI-3]** P2/S - Workshop QC checklist UI (power/fitting/cosmetic)
  - The jobs/{id}/qc + /rework endpoints exist but the QC checklist UI is thin/absent; build the verify/fitting/cosmetic checklist screen so QC is actually captured.
- **[CLI-4]** P2/M - Eye-test clinical report endpoint returns empty data
  - GET /reports/clinical/eye-tests is hard-coded to return {data:[],total:0}, so the clinical eye-test report screen is permanently blank; wire it to the clinical repository.
- **[CLI-5]** P2/M - Customer-facing read-only Rx/prescription portal
  - A net-new public surface where a customer views their own prescription via a link/QR -- high-value self-service with low blast radius.
- **[CLI-6]** P3/M - Progressive fitting params + remake-reason taxonomy + per-lab scorecards
  - Capture PD/seg-height/pantoscopic/vertex/wrap on the workshop job, plus a remake-reason taxonomy and per-lab scorecards on top of lens validation + QC/rework, to cut remakes and rate labs.
- **[CLI-7]** P3/M - Frame+lens+Rx manufacturability pre-check before booking
  - Validate frame+lens+Rx feasibility before the job is sent to workshop (bolting onto lens_catalog + workshop) to slash remakes.
- **[CLI-8]** P3/M - Family/household Rx view (one customer -> multiple patients)
  - View all family members' prescriptions under one customer account so staff manage household eyewear in one place.
- **[CLI-9]** P3/S - Save-and-reuse named lens-power combos
  - Let staff save and reuse named lens-power combinations to speed up repeat Rx/lens configuration.
- **[CLI-10]** P3/S - Rx progression-delta view reads wrong keys (renders blank)
  - The Rx progression-delta view reads wrong field keys and renders blank; align the delta field names so change-over-time displays.
- **[CLI-11]** P3/L - Structured optometric EHR (SOAP/Dx templates)
  - Add templated SOAP exam + Dx coding on top of the existing IOP/VA/cover-test + validated Rx capture; deferred as lower priority for cash retail.
- **[CLI-12]** P3/L **[OWNER-GATED]** - Ophthalmic equipment integration (autorefractor -> Rx)
  - Integrate exportable ophthalmic devices to auto-populate Rx; high-ROI only where stores run such devices (zero device integration today).

## CRM / Marketing

- **[CRM-1]** P1/S - Fix MEGAPHONE UTC-vs-IST DND comparison (megaphone.py:71)
  - Fix the UTC-vs-IST DND comparison at megaphone.py:71 before go-live so WhatsApp nudges respect the 9PM-9AM quiet window in local time.
- **[CRM-2]** P1/M - Contact-lens auto-refill / subscription with run-out prediction
  - Add per-customer run-out date (pack-size x daily-wear), an auto refill-due trigger, and a one-tap reorder draft on top of existing CL/expiry/follow-up/MEGAPHONE pieces to drive repeat CL revenue.
- **[CRM-3]** P2/S - Referral reward $incs a phantom loyalty_points field (rewards lost)
  - marketing.py:770 $incs a parallel loyalty_points field the real engine never reads, so referral rewards are lost (money); route them through the real loyalty/store-credit ledgers.
- **[CRM-4]** P2/M - CRM churn-risk is a mock loyalty-points heuristic; wire real list into UI
  - crm.py:824 flags 'high' churn purely on loyalty_points<1000 (explicit mock) and the churn-risk list endpoint is thinly wired; rebuild on real recency/frequency and surface an at-risk-customer screen.
- **[CRM-5]** P2/M - Return-abuse / serial-returner scoring surfaced at refund + on customer-360
  - Add a per-customer return-rate score with a soft-flag advisory at the refund step (surfaced on customer-360) on top of existing return guards + CRM RFM, to curb return abuse.
- **[CRM-6]** P2/M **[OWNER-GATED]** - OTP-based customer verification + consented marketing opt-in (DLT)
  - OTP-verify a customer's mobile and capture consented marketing opt-in (TRAI/DLT requirement before bulk WhatsApp/SMS); gated on an SMS/WhatsApp provider key.
- **[CRM-7]** P3/M - Named referral program + auto accounting
  - Package a refer-a-friend program with automatic reward accounting on top of existing loyalty/vouchers/payout primitives, which lack a named referral flow.
- **[CRM-8]** P3/M - Promo offer-template library (BOGO/combo/threshold) on the voucher engine
  - Add reusable BOGO/combo/threshold promo templates on the existing voucher engine so campaigns launch from saved templates.
- **[CRM-9]** P3/S - Auto-trigger NPS on delivery + show on customer-360
  - Auto-fire the already-built NPS/feedback flow on order delivery and surface the score on the customer-360 view (loop built, not auto-triggered).
- **[CRM-10]** P3/S - NPS-detractor follow-up writes field names the dashboard doesn't read
  - marketing.py:873 detractor follow-up writes fields the dashboard doesn't read, leaving it blank; align the field names.
- **[CRM-11]** P3/M - CRM churn medium/low bands are a stub
  - crm.py:824 leaves the churn medium/low bands as a stub; implement them on real recency so segmentation is meaningful.
- **[CRM-12]** P3/M - Walkout-recovery / scheduled campaigns only logged SCHEDULED, not dispatched
  - Walkout-recovery sends + scheduled campaigns are flagged SCHEDULED awaiting a future MEGAPHONE tick, so time-based dispatch isn't closed-loop yet; action them on a tick.
- **[CRM-13]** P3/M - Loyalty reward-catalog + promotions tabs are empty 'coming soon' panels
  - The Rewards and Promotions tabs render coming-soon empty states with non-functional Add Reward / Create Campaign buttons; build reward-catalog + promo management.
- **[CRM-14]** P3/L **[OWNER-GATED]** - Inbound / two-way WhatsApp conversational commerce
  - Add an inbound Meta WABA webhook, a lightweight conversation/inbox, and template-button actions (Book eye test / Reorder lenses) routed through JARVIS/CORTEX; only outbound MEGAPHONE works today. Needs WABA creds.
- **[CRM-15]** P3/S - WhatsApp utility-template hardening + opt-in/out STOP ledger
  - Add template classification + an opt-in/out STOP ledger (ties to DPDP) on top of the wired MSG91/DND/DLT setup to use the cheap utility category compliantly.
- **[CRM-16]** P3/M **[OWNER-GATED]** - Marketing-agency oversight dashboard (Google/Meta ad performance)
  - A dashboard to oversee the agency's Google/Meta ad spend/performance via API so the owner can hold them accountable; needs Ads API access.

## AI / Jarvis

- **[AI-1]** P2/S - Jarvis chat fabricates fake numbers on a backend error
  - Jarvis chat fabricates fake numbers on a backend error (a SYSTEM_INTENT 'fail loudly / never fabricate' violation); replace the fabricated fallback with an honest 'no data' response.
- **[AI-2]** P2/L - AI change-proposal workflow (suggest -> approve -> audited execute)
  - Build the SYSTEM_INTENT section-8 loop where an agent proposes, Superadmin approves, and execution is audited -- the product's actual differentiator and home for ORACLE/TASKMASTER.
- **[AI-3]** P2/S **[OWNER-GATED]** - Resolve TASKMASTER vs SYSTEM_INTENT auto-execution contradiction
  - SYSTEM_INTENT says 'AI cannot auto-execute' but TASKMASTER reassigns/escalates with no human; either bless reversible Tier-1 acts or gate TASKMASTER writes behind approval -- a sign-off decision.
- **[AI-4]** P3/L **[OWNER-GATED]** - RAG staff-copilot in POS/clinical (role-scoped Ask Jarvis)
  - Add a retrieval layer grounded on SOPs/lens-compatibility/pricing-caps/warranty, a deliberate non-SUPERADMIN read-only role-scoping, and an in-POS entry point on top of JARVIS chat/claude_client; needs an LLM key.
- **[AI-5]** P3/M **[OWNER-GATED]** - Natural-language inventory query
  - Let Superadmin ask 'show all gold rimless frames in Bokaro between 5k-10k' in plain English and get a structured catalog result; depends on the Claude/LLM key.
- **[AI-6]** P3/L **[OWNER-GATED]** - AI image-based product search (visual similarity)
  - Search the catalog by an uploaded product photo, useful for trade-fair purchase decisions; needs a vision model/embedding service.

## HR / Payroll

- **[HR-1]** P2/M - PayrollDashboard/EmployeeSelfService read orphaned legacy collections (always empty)
  - Both read legacy payroll collections the run page never writes, so salary sheets/payslips are always empty; point them at the payroll collection or retire the legacy tabs.
- **[HR-2]** P2/M - Legacy /payroll/salary/calculate uses a simplified TDS stub diverging from the engine
  - The live calculate endpoint calls a hand-rolled 'simplified' TDS slab + 40% HRA default instead of the canonical payroll_engine, so a preview can report numbers that won't match the locked run.
- **[HR-3]** P2/M - Per-staff sales-attribution commission ledger + MTD leaderboard
  - Add per-order attribution by order.created_by plus an MTD leaderboard because payout today is only a store-level profit-pool, not per-order commission.
- **[HR-4]** P2/M - Employee self-service mobile view (own attendance/salary/incentives)
  - Let an employee view their own attendance, salary and incentives on mobile to cut HR queries (payroll engine + attendance grid already exist to feed it).

## Reports / Analytics

- **[RPT-1]** P2/S - Analytics dashboard counts include CANCELLED + DRAFT orders (overstates)
  - analytics.py:224 includes CANCELLED + DRAFT orders in revenue/order counts, overstating the dashboard; exclude those statuses.
- **[RPT-2]** P2/S - Analytics dashboard silently capped at 500 orders (undercounts busy periods)
  - order_repository.py:113 silently caps the analytics dashboard at 500 orders; aggregate server-side instead of capping the fetch.
- **[RPT-3]** P2/M - Net-margin uses a hard-coded 10% opex placeholder (fabricated profitability)
  - analytics.py:884 assumes opex = 10% of revenue and a /2 inventory-turnover average, so store-performance net-margin reports fabricated numbers; compute or flag as estimated.
- **[RPT-4]** P2/M - Additional print templates (PO, GRN, eye-test token, job card, challan, credit note, quotation)
  - Several operational documents still lack printable templates, forcing manual paperwork; build the missing print templates.
- **[RPT-5]** P3/S - Analytics top-customers name never joined ("Unknown")
  - analytics.py:790 never joins the customer name so top-customers show 'Unknown'; join the customer record.
- **[RPT-6]** P3/S - Analytics store name synthetic (store-001)
  - analytics.py:525 emits a synthetic store name like store-001; resolve and display the actual store name.
- **[RPT-7]** P3/S - Reports 'View' actions are dead stubs
  - Several Reports 'View' buttons are non-functional stubs; wire them to the report detail or remove the dead controls.
- **[RPT-8]** P3/S - Enterprise analytics 'Export report' is a coming-soon toast
  - handleExportReport just fires toast.info('coming soon'); wire the enterprise dashboard's export button to a real export.
- **[RPT-9]** P3/M **[OWNER-GATED]** - Channel-wise online sales tracking
  - Break down sales by channel (store vs Shopify vs marketplace) so the owner sees revenue mix; depends on the marketplace/Shopify integrations being live.
- **[RPT-10]** P3/S - Fix documentation truth-decay (endpoint/test counts drift)
  - Counts disagree across docs (387 vs ~677 vs actual 811 endpoints; 48 vs 130 tests); re-run the route/test dump and reconcile so planning isn't done off stale numbers.
  - **VERIFIED 2026-06-05**: `app.routes` returns 965 FastAPI routes (including GET+HEAD duplicates, OPTIONS, docs). Unique business endpoints are approximately 400+. Test count: 48 new tests added this session alone (test_fin11/test_find5/test_rpt5_6). The README/CLAUDE.md "387" figure is the router-registered count from mid-May; the actual route count grows with each PR.

## Security

- **[SEC-1]** P1/S **[OWNER-GATED]** - Set prod auth secrets (JWT_SECRET_KEY / SEED_SECRET) on Railway
  - JWT_SECRET_KEY signs auth tokens and SEED_SECRET guards the seed endpoint; owner must set both (plus optional CREDENTIAL_ENCRYPTION_KEY) in Railway env so prod tokens are signed with a real secret.
- **[SEC-2]** P2/S - Gate un-gated legacy /hr/payroll/* routes for RBAC
  - The legacy /hr/payroll/* routes are un-gated for RBAC; add the role gate (or retire the routes) to close the access hole.
- **[SEC-3]** P2/M **[OWNER-GATED]** - Replace homegrown XOR credential encryption with Fernet/KMS
  - Integration API keys are encrypted at rest with a self-noted placeholder XOR+HMAC scheme the code itself flags as 'replace with Fernet for production', leaving stored creds weakly protected against a DB dump.
- **[SEC-4]** P3/M - Encrypt or stop persisting customer PII / Rx in browser localStorage
  - POS draft state stores customer PII + prescription data as plaintext localStorage (readable via DevTools on a shared terminal); encrypt, move to sessionStorage, or clear aggressively.
- **[SEC-5]** P3/S - Settings adminIntegration camelCase setters rejected by backend
  - adminIntegration camelCase setter payloads are rejected by the backend; drop or snake-case the unused setters so integration settings save.

## Infra / Ops

- **[OPS-1]** P1/S **[OWNER-GATED]** - MongoDB volume resize to 5GB (keystone unblock)
  - The 500MB Mongo volume is full so ensure_indexes silently fails for all collections, blocking the data-integrity axis and BVI cutover; owner must resize the Railway volume to 5GB and restart.
- **[OPS-2]** P1/S - Fix: agent scheduler has no singleton/leader guard (duplicate POs/messages)
  - main.py starts an AsyncIOScheduler in every process with only a test-env skip; the day Railway runs >1 worker, TASKMASTER drafts N duplicate POs and MEGAPHONE sends N duplicate messages -- add a Redis SETNX leader lock or SCHEDULER_ENABLED gate before integration go-live.
- **[OPS-3]** P1/M **[OWNER-GATED]** - Resolve customers.customer_id genuine duplicates (merge/reassign decision)
  - ~5038 customers have genuine duplicate customer_ids (e.g. one phone x3) linked to orders/Rx, blocking the unique index; needs an owner-approved merge/reassign strategy, not a blind dedupe.
- **[OPS-4]** P1/S **[OWNER-GATED]** - BVI cutover: flip Shopify writes + dispatch to live
  - Going live on the online store requires the owner to set IMS_SHOPIFY_WRITES=1 + DISPATCH_MODE=live after the volume grows and a quiet window.
- **[OPS-5]** P2/S **[OWNER-GATED]** - Wire live integration creds (Razorpay/Shiprocket/Tally/MSG91)
  - Live Razorpay/Shiprocket/Tally/MSG91 calls are blocked until the owner pastes the API keys into Railway variables (covers MEGAPHONE + NEXUS activation).
- **[OPS-6]** P2/S **[OWNER-GATED]** - Provision Redis URL on Railway for cross-worker event bus + cache
  - Without REDIS_URL the agent event bus falls back to single-worker in-process dispatch and an in-memory cache; wiring the Railway Redis URL enables cross-worker events (SENTINEL->TASKMASTER) + shared caching.
- **[OPS-7]** P2/S **[OWNER-GATED]** - Set ANTHROPIC_API_KEY to activate ORACLE + JARVIS chat
  - ORACLE anomaly narratives and JARVIS conversation are fully wired but inert without an Anthropic key; owner sets ANTHROPIC_API_KEY (and optionally AGENT_CLAUDE_MODEL).
- **[OPS-8]** P3/S **[OWNER-GATED]** - Enforce upload-bill size cap before storage write
  - The expenses upload-bill endpoint has no confirmed pre-storage size cap (storage was down during QA), risking unbounded uploads; add a max-size guard verified once object storage is configured.
- **[OPS-9]** P3/S **[OWNER-GATED]** - Set PAGESPEED_API_KEY for PIXEL Lighthouse audits
  - PIXEL's deploy/daily UI-quality audits call Google PageSpeed Insights and stay simulated until owner provides PAGESPEED_API_KEY (and optionally FRONTEND_BASE_URL).
- **[OPS-10]** P3/S **[OWNER-GATED]** - Wire SLACK_WEBHOOK_URL for CRITICAL anomaly alerts
  - ORACLE's CRITICAL-anomaly Slack alerting is fail-soft and silent until owner sets SLACK_WEBHOOK_URL (+ optional SLACK_ALERT_SEVERITY), leaving no real-time ops channel for severe anomalies.
- **[OPS-11]** P3/S **[OWNER-GATED]** - Enable Sentry APM via SENTRY_DSN
  - Sentry FastAPI/Starlette tracing + per-agent-tick spans are wired but skipped unless owner sets SENTRY_DSN (+ optional rates/release), so prod has no error/perf APM today.
- **[OPS-12]** P3/S **[OWNER-GATED]** - Decommission legacy BVI Railway project after 2-week bake
  - After a clean 2-week post-cutover bake, retire the standalone BVI app (uniparallel.com DNS removal + delete the separate Railway project); owner-only and must never use top-level railway delete.
- **[OPS-13]** P3/L **[OWNER-GATED]** - Bulk data export / backup-restore endpoints return 501
  - system/backups/restore + system/export/{type} intentionally raise 501 (infra-layer only) and their FE buttons are hidden, leaving no in-app data export/restore path.
- **[OPS-14]** P3/M - Available-printers detection returns empty list
  - GET /settings/printers/available always returns {printers:[]}, so the printer-settings UI can never auto-discover network printers.
- **[OPS-15]** P3/S - Commit local prod-diagnostic scripts into the repo
  - The untracked scripts/_*.py prod tools (index-blocker scan, orders backfill, BVI PG inspect/fix) only exist on one machine; commit them as repeatable, documented ops scripts.
- **[OPS-16]** P3/S - Clean up live-QA prod test data
  - Live QA on prod created throwaway records (customer 'QA TEST Workflow'/9000000077 + Self patient, token T001, an Rx, an in-flight POS order) that must be deleted along with any resulting order/workshop job.
- **[OPS-17]** P3/S - Align local Python runtime to 3.12 to match production
  - Local dev/CI runs Python 3.11 while prod/Railway target 3.12; the drift can hide version-specific bugs and should be pinned/aligned.

## UI/UX, localization & other

- **[UX-1]** P2/M - SOP checklist-template endpoint returns hard-coded placeholder items
  - tasks.py:625 returns a static ['Check inventory','Verify stock count','Update system'] list instead of querying sop_templates, so this checklist path ignores the editable per-store SOPs.
- **[UX-2]** P3/S - Inventory/loyalty primary buttons use raw bg-blue-600 instead of bv-red
  - Several inventory/loyalty primary buttons are hard-coded bg-blue-600 instead of the bv-red brand token, breaking the light-only brand-consistency design language.
- **[UX-3]** P3/L - Hindi/Marathi UX localization
  - Localize the UI into Hindi and Marathi to fit the Jharkhand/Maharashtra store staff.
- **[UX-4]** P3/L **[OWNER-GATED]** - Virtual try-on + PD capture on storefront/portal
  - Integrate a proven try-on/PD SDK (Fittingbox/Banuba/MirrAR/Auglio) and store captured PD back on the customer/Rx, prioritising the PD-capture half that feeds dispensing; needs an SDK license.
- **[UX-5]** P3/L - Training & rollout content (curriculum, scripts, in-app help)
  - Build a 7-day role-wise training curriculum, trainer scripts, success metrics, and per-feature in-app help text -- content/process work to drive staff adoption.

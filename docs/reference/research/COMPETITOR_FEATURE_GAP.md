# IMS 2.0 — Competitor Feature-Gap Matrix (optical retail SOFTWARE)

> Output of the Total Coverage competitor deep-dive (workflow `ims2-optical-pms-competitor-gap`).
> Benchmarks IMS 2.0 against the **optical retail-software vendors** it actually competes with — NOT eyewear retailers (Lenskart/Titan/Warby Parker are the wrong comparison set).
> Verified against the codebase (44 routers inspected). Companion to [`IMS2_FEATURE_ROADMAP.md`](../IMS2_FEATURE_ROADMAP.md).

## The vendor set
- **OpticalCRM** (DNB/Multiapps, Vadodara) — most-used cloud optical-shop software in India; flat all-in-one (~₹4k/yr).
- **Gofrugal Opticals** (Zoho) — retail ERP with an eyewear edition; strong omnichannel + native mobile apps (GoBill/GoSure/WhatsNow) + e-invoice/e-way-bill.
- **TechCherry** — the **legacy optical POS IMS migrated FROM** (`backend/api/routers/techcherry_import.py`); offline billing is its biggest edge.
- **Lensorix** (JoiningEnds, founder Saket Bohania, Kolkata; joiningends.com — not search-indexed) — "Smart Optical ERP": retail + **distribution/wholesale** + e-commerce, location-wise pricing, frame/lens-wise shrinkage + overstock reports, job tracking, stock-audit.
- Indian peers: OptoSoft, Marg, LogicERP, CloudFrames, Optiware. Global PMS bar: Ocuco Acuitas, Optix, Eyefinity, Crystal/Rev360 (clinical-depth aspirational bar).

## Verdict
**IMS 2.0 leads the optical-software field on retail-OS breadth + rigor**, out-classing every Indian peer: multi-entity / 4-GSTIN finance, real P&L, 11-role RBAC + request-time middleware, audit-everything, an 8-agent AI layer no peer has, atomic loyalty/vouchers, idempotent POS, validated Rx ranges, OpticalCRM's 4-version Rx module, power-grid + range-wise lens pricing, a workshop QC lifecycle beating Gofrugal/CloudFrames, and a full online-store + Shopify storefront. **NPS/feedback is BUILT** (the OpticalCRM "feedback gap" claim is wrong). Insurance/claims + UK eGOS are correctly-absent India non-goals.

## The them-vs-IMS gap matrix
`GAP` = absent · `PARTIAL` = foundation exists · `EXISTS` = IMS already has it · effort S/M/L.

| Gap | IMS status | Pri/Effort | Note |
|---|---|---|---|
| **E-invoice (IRN + signed QR)** via IRP/GSP at billing | GAP | P1/L | Only GSTR export today. Legally mandatory for B2B at scale (4 GSTINs). Biggest compliance gap. |
| **E-way bill** generation from invoice | GAP | P2/M | Dead `e_way_bill_enabled` flag only. Inter-state JH↔MH > ₹50k. Same GSP. |
| **Inter-GSTIN transfer auto-books the mirror purchase** | PARTIAL | P1/M | `transfers.py` moves stock; verify it posts the GST purchase-in (ITC) in the receiving GSTIN. Fits the 3-entity/4-GSTIN structure exactly. |
| **Offline-first POS** (service worker + replay queue) | GAP | P1/L | PWA manifest but no service worker. Neutralizes TechCherry's offline-billing edge. POS sign-off. |
| **Per-sale commission ledger + staff leaderboard** | PARTIAL | P2/M | Payout is a store-level profit-pool (Pune incentive), not per-order. Add sales-attribution by `order.created_by` + MTD leaderboard. |
| **Appointment diary + online self-booking + recall groups** | GAP | P2/L | Only a same-day walk-in queue. WhatsApp eye-test slot booking (MEGAPHONE/MSG91) + optometrist calendar feeding the queue. |
| **Structured optometric EHR** (SOAP/Dx templates) | PARTIAL | P3/L | Captures IOP/VA/cover-test + validated Rx; no templated exam/Dx coding. Defer for cash retail. |
| **Ophthalmic equipment integration** (autorefractor→Rx) | GAP | P3/L | Zero device integration. High-ROI only if stores run exportable devices. |
| **Frame+lens+Rx manufacturability pre-check** | GAP | P3/M | Validate feasibility before booking → slash remakes. Bolts onto lens_catalog + workshop. |
| **Vendor-SKU-alias → single lens master** | PARTIAL | P2/M | No vendor-code→master alias; same lens from different suppliers duplicates at goods-inward. |
| **Progressive fitting params** (seg height/pantoscopic/vertex/wrap) | PARTIAL | P3/S | Only fitting-height + PD today. Small schema + form add for premium PAL. |
| **Per-customer credit-limit at POS (khata/B2B)** | PARTIAL | P2/M | CREDIT tender + AR exist; no limit guard or running khata view. Matters for Lensorix-style wholesale. |
| **Guided physical stock audit / cycle count** | PARTIAL | P2/M | Non-moving report exists; no guided count→variance→reconcile flow. |
| **Barcode lifecycle audit view** (purchase→sale→transfer→return) | PARTIAL | P3/M | Movements recorded across modules; no single trace lookup. Fits Audit-Everything. |
| **Bank statement auto-reconciliation** | GAP | P3/M | Add statement import + auto-match vs receipts/payments. |
| **Two-way Tally connector** (live masters/ledgers) | PARTIAL | P3/M | One-way JV XML exists; XML covers the core need. |
| **GSTR-1/3B direct GSP filing** | PARTIAL | P2/M | Filing-prep + GSTN-offline JSON exist; no direct push. Rides the IRN GSP. |
| **Named referral program + auto accounting** | PARTIAL | P3/M | Loyalty/vouchers/payout primitives exist; no packaged refer-a-friend. |
| **Promo offer-template library** (BOGO/combo/threshold) | PARTIAL | P3/M | No ready promo templates on the voucher engine. |
| **Daily owner day-close digest** | **DONE** ✅ | — | Shipped as an in-app Hub card (SUPERADMIN/ADMIN, brief+expanded) 2026-06-05 — owner chose in-app over WhatsApp. |
| **Customer feedback / NPS loop** | EXISTS | P3/S | BUILT (`marketing.py` NPS + `CustomerFeedback.tsx`). Polish: auto-trigger on delivery + show on customer 360. |
| Bilingual invoice · tablet signature/consent · insurance claims | GAP | — | **Dropped** per owner (signature/bilingual) or India non-goal (insurance). |

## Strategic bets (in priority order)
1. **E-invoice IRN + e-way-bill via per-GSTIN GSP/IRP** (P1/P2, L) — biggest compliance gap; same integration unlocks direct GSTR filing.
2. **Offline-first POS** (service worker + idempotency-keyed replay) (P1, L) — neutralizes TechCherry's edge. POS sign-off.
3. **Inter-GSTIN transfer mirror-purchase** across the 3 entities / 4 GSTINs (P1, M) — correctness for IMS's exact legal structure.
4. **WhatsApp appointment booking + optometrist diary** feeding the walk-in queue (P2, L).
5. **Vendor-SKU-alias → lens master + frame+lens+Rx manufacturability pre-check** (P2/P3, M).

## Quick wins (small effort, high value)
- ✅ Owner day-close digest (shipped) · Per-staff sales attribution + MTD leaderboard (P2, M) · Auto-NPS-on-delivery (P3, S) · Progressive fitting params (P3, S) · Save-and-reuse named lens-power combos (P3, S).

> **Lensorix-driven parity to watch:** wholesale/distribution depth · guided stock-audit reconciliation · frame/lens-wise shrinkage + overstock reporting.

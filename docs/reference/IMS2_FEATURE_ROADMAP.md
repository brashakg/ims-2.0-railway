# IMS 2.0 — Feature Roadmap (prioritized backlog)

> The prioritized build backlog from the Total Coverage Program: research-backed new features + confirmed defects, scored by priority (P1>P2>P3) × effort (S/M/L) × value. Sources: [`research/COMPETITOR_FEATURE_GAP.md`](research/COMPETITOR_FEATURE_GAP.md), [`research/india-compliance.md`](research/india-compliance.md), [`research/retail-os-ai.md`](research/retail-os-ai.md), [`IMS2_WAVE2_BACKLOG.md`](IMS2_WAVE2_BACKLOG.md).
> Status: ✅ shipped · 🔵 in-flight · ⬜ open · 🔒 owner-gated execution.

## Shipped (this program)
- ✅ Owner day-close digest — Hub card, SUPERADMIN/ADMIN, brief+expanded (not WhatsApp, owner's call)
- ✅ DB: per-index `ensure_indexes` resilience
- ✅ SUPERADMIN Activity Log (name resolution + change diff + today summary) + own "Audit" nav group
- ✅ Wave-2 live bugs: Clinical TestHistory crash · Tasks `task_id` · Orders payment-badge enum · Finance payout CSV
- ✅ Finance GST summary CGST/SGST-vs-IGST split (was blind 50/50)
- ✅ BVI step 1 (collections membership list) + step 2 (single-writer startup assertion)

## P1 — do next (high value / compliance / correctness)
| Item | Effort | Why |
|---|---|---|
| 🔒 **GST e-invoice (IRN + signed QR)** via per-GSTIN GSP/IRP | L | Legally mandatory for B2B at scale (4 GSTINs). Needs a GSP account (owner). |
| ⬜ **Inter-GSTIN transfer auto-books the mirror purchase** (ITC in receiving entity) | M | Correctness for the exact 3-entity / 4-GSTIN structure. |
| 🔒 **Offline-first POS** (service worker + idempotent replay queue) | L | Neutralizes TechCherry/Lensorix offline edge. POS sign-off. |
| ⬜ **GSTR-2B auto-fetch + one-click reconciliation** (close the existing matcher loop) | M | `itc_reconcile.py` exists; add the 2B importer + Finance UI + vendor-chase tasks. |
| ⬜ **DPDP 2023 consent ledger** + withdrawal + purpose retention | M | Compliance; no consent layer today. |
| ⬜ **Wire IMS→Shopify live inventory sync into the POS sale/return path** | M | Engine exists, gated off (BVI cutover). |

## P2 — strong value
| Item | Effort | Why |
|---|---|---|
| ⬜ Per-sale **commission ledger + staff leaderboard** | M | Morale; payout is store-pool today. Quick-ish via `order.created_by`. |
| ⬜ WhatsApp **appointment booking + optometrist diary** → walk-in queue | L | Optical-fit; only same-day queue today. |
| ⬜ **E-way bill** generation from invoice | M | Inter-state JH↔MH. Rides the e-invoice GSP. |
| ⬜ Per-customer **credit-limit at POS (khata)** + party-wise outstanding | M | B2B/wholesale (Lensorix parity). |
| ⬜ **Guided cycle-count** → variance → reconcile | M | Lensorix/OpticalCRM ship it. |
| ⬜ **Vendor-SKU-alias → single lens master** | M | Kills duplicate lens entries at goods-inward. |
| ⬜ **Demand forecast → nightly draft-PO** suggestions | M | Reorder logic exists; add seasonality + safety stock. |
| ⬜ **Return-abuse / serial-returner** scoring at POS | S | Data exists; advisory warning at refund. |
| ⬜ Direct **GSTR-1/3B GSP filing** + two-way Tally | M | Prep done; add push. |

## P3 — opportunistic / quick wins
- ⬜ Auto-trigger NPS on delivery + show on customer 360 (S) · Progressive fitting params (S) · Save-and-reuse named lens-power combos (S) · Barcode lifecycle trace view (M) · Named referral program (M) · Promo offer-template library (M) · Bank statement auto-recon (M) · Frame+lens+Rx manufacturability pre-check (M) · Structured optometric EHR (L) · Inbound WhatsApp commerce (L) · Hindi/Marathi UX (M) · RAG staff copilot (L).
- **Dropped per owner:** bilingual invoice, tablet signature/consent. **India non-goal:** insurance/vision-plan claims.

## Open defects (from the audit — see IMS2_WAVE2_BACKLOG.md for file:line)
- 🟠 Catalog `discount_category` dropped on create (money) · Analytics counts include CANCELLED+DRAFT · CRM referral phantom-field + churn stub · Online Store smart-rules/menus residual seams · ~30 MINOR (dead methods, unhandled statuses, Jarvis fabricated fallback numbers).

## BVI merge completion (cutover-prep, in progress)
- 🔵 Steps 3–7: dual-writer drift detector · NEXUS re-push sweep · Postgres→Mongo migration + 4 mappers · parity oracle + `/uploads/` audit + asset re-host. 🔒 Execution owner-gated (Shopify creds, live Postgres, image bucket, atomic flip). See [`bvi_merge_completion_state`](../../) memory + `BVI_MERGE_PLAN.md`.

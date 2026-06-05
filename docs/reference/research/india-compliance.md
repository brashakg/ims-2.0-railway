# Research — India retail + compliance

> Total Coverage research bucket A. Where IMS leads/lags on India compliance + what's worth building. Each item notes the existing IMS foundation. See [`COMPETITOR_FEATURE_GAP.md`](COMPETITOR_FEATURE_GAP.md) + [`../IMS2_FEATURE_ROADMAP.md`](../IMS2_FEATURE_ROADMAP.md).

## P1 — highest stakes
- **GST e-invoicing (IRN + signed QR)** per GSTIN via a GSP/ASP. *IMS:* strong scaffolding, no IRN. `print_legal.py` builds the Rule-46 identity block + Rule-48 markers + HSN summary; `gstn_export.py` already emits the per-invoice b2b/itm_det shape (maps near-1:1 to the e-invoice schema). Missing: the GSP client, IRN/QR persistence, 24h-cancel, signed-QR render. `integration_status.py` flags `gst_portal` as `not_wired`.
- **GSTR-2B auto-fetch + one-click reconciliation.** *IMS:* largely built — `itc_reconcile.py::reconcile_gstr2b()` has matched / mismatch / only-in-books (ITC-at-risk) / only-in-2b buckets, normalised keys, rupee tolerance, 180-day aging. Missing: the 2B file importer/parser + the actionable Finance UI + vendor-chase task creation (fire via TASKMASTER).
- **DPDP Act 2023 consent ledger** + withdrawal link + purpose-based retention. *IMS:* behavioural pieces only (MEGAPHONE DND, #367 PII validation); no consent concept exists. Missing: a consent-ledger collection, withdrawal endpoint, purpose tagging, retention job, itemised notice.
- **Inter-GSTIN transfer mirror-purchase** (taxable supply needs sale-out + purchase-in with ITC in the receiving GSTIN). *IMS:* `transfers.py` moves stock; the GST purchase-in posting needs verifying/adding. Fits the 3-entity/4-GSTIN reality exactly.

## P2
- **E-way bill** generation + register for inter-state transfers + high-value deliveries (JH+MH aware). *IMS:* nothing wired; reuse the GSTN state-code map in `gstn_export._STATE_CODES`; rides the e-invoice GSP.
- **Dynamic UPI QR on POS bills + auto payment reconciliation** (Razorpay/Bharat-QR per store-VPA). *IMS:* Razorpay registered but unwired for action; NEXUS handles webhooks. Missing: NPCI-spec dynamic-QR per order + order↔UPI-credit auto-match.
- **GSTR-1 / 3B / 2B direct GSP filing** (replace the manual offline-tool upload). *IMS:* compute + export done (`gstn_export.py` to_gstr1_json/to_gstr3b_json); only the GSP submit API + multi-GSTIN status UI remain — shares the e-invoice connector.
- **Live Tally 2-way bridge** (push day JVs to Tally HTTP-Gateway; pull masters). *IMS:* `nexus_providers.tally_build_day_voucher_xml` + balanced JVs exist; live HTTP push + master pull-back are TODO.

## P3
- **TDS/TCS threshold gating + 206C(1H) + quarterly 26Q/27EQ export.** *IMS:* `ap_engine.compute_tds` has CA-verified rates (194H 2%/194J/194C/194Q/194I) but explicitly enforces NO thresholds; 206C absent. Add per-counterparty cumulative tracking + threshold switch-on + the quarterly return.
- **WhatsApp utility-template hardening** — opt-in/out ledger + STOP handling, leveraging the cheap utility category. *IMS:* MSG91 + DISPATCH_MODE + DND + DLT template id already wired; add the classification + opt-out ledger (ties to DPDP).
- **ONDC seller node** (Seller-NP via an SNP) with TCS-payout reconciliation. *IMS:* catalog foundation exists; no ONDC connector. Recommend an SNP rather than raw Seller-NP. Sequence AFTER single-inventory sync.
- **GST-compliant invoice numbering** — consecutive serial per financial year, per GSTIN, atomic counter + unique index. *Acknowledged-broken in CLAUDE.md (P3-A); the `uniq_invoice_number` index backstop exists, the FY-serial counter is the remaining piece.*

> **Owner-gated for any GSP item:** a licensed GSP/ASP account + per-GSTIN credentials.

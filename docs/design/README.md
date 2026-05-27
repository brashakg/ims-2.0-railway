# IMS 2.0 — Design Handoff

A complete design-reference package for **Better Vision's IMS 2.0**, an in-store inventory + POS system for an Indian optical retail chain.

---

## ⚠️ Read this first

The files in this bundle are **HTML design references**, not production code. They are pixel-precise prototypes meant to communicate **what** to build and **why**. The implementation task is to **recreate these designs in the target codebase's existing environment** (React, Vue, Swift, Flutter, native — whatever the production stack already is), using its established design system, component library, routing, state management, and data layer.

If no codebase exists yet, the developer should pick a framework appropriate for the use-case (this is a multi-screen, data-heavy, on-counter app primarily used on iPad horizontal in stores — Next.js, Remix, or a modern React-based stack with tablet-first layouts is a sensible default).

## Fidelity

**High-fidelity.** Final colors, typography, spacing, copy, interaction states, and document layouts are all decision-grade. Where the developer should deviate, it's noted explicitly.

## What's in this bundle

| File | Purpose |
| --- | --- |
| `README.md` | This file — orientation + table of contents |
| `INTENT.md` | Why each screen exists, the user's job-to-be-done, design rationale |
| `STATUTORY_NOTES.md` | Indian GST / CGST law context driving the print templates |
| `MOCK_DATA.md` | The shape of the mock data layer; what your real backend needs to supply |
| `RESPONSIVE.md` | Breakpoint strategy (desktop / iPad-H / iPad-V / mobile) |
| `hub.html` … `setup.html` | 11 screen prototypes |
| `print.html` | The print module surfacing 26 paper templates |
| `shell/` | Cross-screen shell (rail nav, tokens, fonts, mock data) |
| `print/` | The 26 print templates split across `templates.jsx` … `templates5.jsx` + `legal_helpers.jsx` + `addons.jsx` |
| `pos/` | POS-specific helper components |

## Project at a glance

**Better Vision Opticals Pvt. Ltd.** — fictional Delhi-based optical retail chain. Flagship store: **GK-I Flagship** (Greater Kailash-I, Delhi). The IMS handles every operational surface of running an optical store in India: customer eye-exam → prescription → frame + lens selection → POS checkout → lens job to lab → QC → customer delivery → loyalty/wallet → ledgers → daily cash reconciliation → vendor procurement → statutory GST compliance.

The system is designed for **store-level staff** as primary users (Store Manager, Optician, Optometrist) with **HQ Ops Head** as a secondary user for approval workflows.

## Screen inventory (11 screens)

| Screen | File | Primary user | Job-to-be-done |
| --- | --- | --- | --- |
| **Hub** | `hub.html` | All staff | Landing page · entry to every module · what's happening now |
| **POS** | `pos.html` | Store Manager · Cashier | 6-step checkout: customer → Rx → products → cart → payment → review |
| **Clinical** | `clinical.html` | Optometrist | Eye exam queue + refraction recording → Rx card output |
| **Inventory** | `inventory.html` | Store Manager · Inventory clerk | Stock ledger · display zones · cycle counts · transfers · GRN receive · reorder drafts |
| **Print** | `print.html` | All staff | Catalog of 26 print templates (invoices, Rx cards, GRNs, etc.) |
| **Accounts** | `accounts.html` | Store Manager · Accounts | Expense approval · cash register · damage register · vendor / customer ledgers |
| **Pricing & Offers** | `pricing.html` | Ops Head | Bulk price update across SKUs + offer creation / scheduling |
| **Tasks** | `tasks.html` | All staff | Priority-tagged task inbox · auto-tasks from Jarvis agents |
| **Reports** | `reports.html` | Store Manager | Day-end · sales · GST · cohort · inventory reports |
| **Jarvis** | `jarvis.html` | Superadmin | Always-on agents (Stock Sentinel, Price Patrol, Rx Reconciler, etc.) |
| **Setup** | `setup.html` | Store Manager | Per-store configuration (POS behaviour, GST, surcharges, etc.) |

## Print template inventory (26 documents)

Customer-facing (full statutory header — GSTIN, CIN, place of supply, copy markers):

1. **Tax Invoice** (A4) — Rule 46 CGST 21-field invoice
2. **Prescription Card** (A5) — patient Rx + optometrist DMC/NCAHP registration
3. **Lens Job Card** (A5) — internal lab dispatch + workflow checklist
4. **Queue Token** (80mm thermal) — clinical waiting room
5. **Delivery Challan** (A4) — Rule 55 CGST triplicate
6. **Credit Note** (A5) — Sec. 34 CGST refund/exchange
7. **Thermal Receipt** (80mm) — POS customer copy with HSN-wise tax split
8. **Purchase Order** (A4) — vendor procurement
9. **Goods Receipt Note** (A4) — GRN against PO + variance handling
10. **Day-end Z-report** (A4) — SOP-FIN-02 cash drawer close
11. **Appointment Slip** (A5) — booked eye-exam confirmation
12. **Warranty Card** (A6) — every frame + lens sale
13. **Gift Voucher** (A5) — bearer instrument with check-digits
14. **Customer Statement** (A4) — monthly account · loyalty + wallet
15. **Debit Note** (A4) — Sec. 34 CGST raised against vendor
16. **Sale Order / Estimate** (A4) — pro-forma with advance confirmation
17. **Sale Return** (A5) — against original invoice
18. **Vendor Ledger Statement** (A4) — AP statement
19. **Customer Delivery Handover** (A4) — proof of receipt + fitting (loop closer)

Staff-facing (minimal header — logo + branch only):

20. **Barcode Shelf Labels** (A4, 32-up · Avery L7160)
21. **Stock Cycle-Count Sheet** (A4 · grouped by fixture)
22. **Stock Purchase Tally** (A4 · delivery person handoff, pre-GRN)
23. **Shift Handover Sheet** (A4 · between cashiers)
24. **Damage / Write-off Register** (A4 · weekly)
25. **Workshop QC Sticker** (60×90mm thermal · lens packet)
26. **Expense Voucher** (A6 · petty cash + approval)

## Visual language

| Concept | Decision |
| --- | --- |
| Type | Inter (sans), Instrument Serif (display, sparingly), JetBrains Mono (mono) |
| Numerals | `font-variant-numeric: tabular-nums` everywhere financial / countable |
| Indian numbering | `Intl.NumberFormat('en-IN')` — Lakh / Crore grouping |
| Date format | `DD-Mon-YYYY` — `19-Apr-2026` |
| Time format | 24-hour IST — `14:22` |
| Currency | ₹ prefix · paise to 2 decimals on tax docs · whole rupees on summaries |
| Color tone | Warm-cool neutral palette, single brand red `--bv: #B22317`, status colors (ok / warn / err / info) |
| Surfaces | `--surface` (white), `--surface-2` (warm-grey-50), `--bg-sunk` (warm-grey-100), `--bg` (page) |
| Borders | Hairline `--line` for primary structure, `--line-soft` for table rows, `--line-strong` for inputs |
| Radius scale | 3 / 6 / 8 / 12px (`--r-sm` / `--r-md` / `--r-lg`) |
| Shadows | Pre-tokenized `--sh-xs` / `--sh-sm` etc. — sparingly |
| Hover states | All interactive rows / cards have a subtle bg-sunk hover (~100ms) |
| Statutory documents | Bordered, ALL-CAPS labels, no editorial flourish, copy markers per Rule 48/55 |

## Indian optical-retail context (essentials)

- **GST**: Optical goods classified under HSN 9001 (lenses), 9003 (frames), 9605 (accessories); GST rate 12% on goods, 18% on services like fitting. Intra-state sales attract CGST + SGST split equally; inter-state sales attract IGST.
- **NCAHP**: National Commission for Allied & Healthcare Professions Act — optometrists must be registered. Rx cards must show both state council registration (e.g. `DMC/R-4412/2014`) and NCAHP UID (`OPT-IN-22-04412`).
- **Rule 46 CGST**: Mandates 21 fields on every tax invoice (supplier GSTIN, recipient GSTIN, HSN, place of supply, reverse-charge declaration, amount in words, etc.).
- **Rule 55 CGST**: Delivery challans must be in **triplicate** — "Original for Consignee / Duplicate for Transporter / Triplicate for Consignor".
- **Rule 48**: Tax invoice copies marked similarly: "Original for Recipient / Duplicate for Transporter / Triplicate for Supplier".
- **Rule 56**: Records retention — 7 years from due date of furnishing of annual return.
- **Sec. 34**: Credit notes and debit notes — must be issued within 30 days, reported in GSTR-1, output tax reversed in GSTR-3B.
- **Sec. 17(5)(h)**: ITC must be reversed on goods destroyed / lost / written-off.
- **Legal Metrology**: MRP printed on shelf labels must be inclusive of all taxes.
- **Drug Licence**: Optical shops dispensing contact lenses need a Drug Licence (shown as `DL-OPT-GK1-2018-0421` on Rx cards).

See `STATUTORY_NOTES.md` for the detailed mapping of each print template to its statutory basis.

## Responsive strategy

All screens are tested at four breakpoints:

| Breakpoint | Width | Pattern |
| --- | --- | --- |
| Desktop | ≥ 1280px | Full 3-column layouts, full nav rail |
| iPad H | 1024–1280px | Side panels shrink; stat strips collapse 5→3 cols; nav rail becomes icon-only |
| iPad V | 768–1024px | Side panels become drawers (toggle button in header); stat strips → 2 cols |
| Mobile | < 640px | Everything stacks 1-col; tab bars scroll horizontally; dense tables scroll horizontally |

See `RESPONSIVE.md` for per-screen specifics.

## How to use this package

1. **Read `INTENT.md` end-to-end** before writing any code. Each screen's job-to-be-done and constraints are explained.
2. **Read `STATUTORY_NOTES.md`** before implementing any document — the legal basis dictates which fields are non-negotiable.
3. **Pull mock data shapes from `MOCK_DATA.md`** to understand what your real APIs must return.
4. **Open the HTML files** in a browser (no build step needed — pure HTML + babel-transpiled JSX) to inspect interactions live.
5. **Inspect computed styles** to lift exact spacing / color / type values when implementing.
6. Implement screen-by-screen — the order in INTENT.md is the suggested order, starting from POS (highest user value) and working outward.

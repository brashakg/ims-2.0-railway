# CHANGELOG — Conversation-driven build history

This is the chronology of the design conversation that produced this package. Each entry maps a user intent to the design decision that resulted. Read top-to-bottom for the full story; useful when revisiting "why is X done this way?".

---

## Phase 1 · Print module foundation (initial 6 templates)

**User**: "Search and find all sections having print details, tax invoice, barcode print, prescription and so on — start working on each. I like high quality looking detailed data heavy formatting. Fetch data from the corresponding section."

**Discovered intent**: 6 docs already existed in `print/templates.jsx` — Tax invoice, Rx card, Job card, Queue token, Delivery challan, Credit note. The user wanted them refined and 6 more added.

**Added (round 1)**:
1. Barcode shelf labels (A4, 32-up)
2. Stock count sheet (A4, by shelf — later refactored to by-fixture)
3. Thermal receipt (80mm POS)
4. Purchase order (A4)
5. Goods receipt note (A4)
6. Day-end Z-report (A4)

**Files**: `print/templates2.jsx`, wired into `print.html`.

---

## Phase 2 · Statutory redesign

**User**: "Do a deep research on how Indian optical POS softwares handle these, also search for legal and statutory requirements then proceed with editing and redesigning, these feel too radical. For many of these documents more than matching the look and feel of our software generating trust and reliability is more important."

**Research undertaken**:
- Rule 46 CGST mandatory fields (21 fields on tax invoices)
- Rule 55 triplicate marking on delivery challans
- Rule 48 copy markers
- NCAHP registration requirement for optometrists
- HSN / SAC codes for optical goods
- Aesthetic conventions of real Indian tax invoices

**Decision**: Strip editorial flourish (Instrument Serif italic, brand red banner) from statutory documents. Adopt a bordered, ALL-CAPS, sans-only aesthetic with copy markers, declarations, authorised-signatory blocks.

**Files**: `print/legal_helpers.jsx` (new), `print/templates.jsx` (rewritten), `print/templates2.jsx` (rewritten).

---

## Phase 3 · Header strategy split

**User**: "We need to add a proper header with many details on top of customer facing prints and just a header with our logo and branch name for staff facing prints."

**Decision**:
- `LegalHeader` component (full statutory) for customer-facing + vendor-facing docs
- `StaffHeader` component (minimal) for internal docs (job card, labels, count sheet, Z-report, shift handover, damage report, expense voucher, QC sticker)

**Files**: `print/legal_helpers.jsx` (added `StaffHeader`), templates updated to use the right header.

---

## Phase 4 · 6 more customer/staff prints

**User**: "Make more such templates for features and functions throughout the app."

**Added**:
1. Appointment slip (A5)
2. Warranty card (A6)
3. Gift voucher (A5 with security features)
4. Customer ledger statement (A4)
5. Debit note (A4)
6. Shift handover sheet (A4)

**Files**: `print/templates3.jsx`.

---

## Phase 5 · Display fixture / zone system

**User**: "In inventory I would like to have a system through which I can define which product is in what section — wall display, counter display, pillar display etc. Should also be included in stock count sheet. Can you design a system for the same."

**Built**:
- 13 fixtures in `shell/data.js` (`MOCK.fixtures`)
- 13 SKU placements in `shell/data.js` (`MOCK.placements`)
- New **Display layout** tab in `inventory.html` — floor cards + side detail panel
- **Zone column** added to Stock ledger
- **Stock count sheet print template refactored** to group by fixture (`print/templates2.jsx`)

---

## Phase 6 · GRN receive flow with zone assignment

**User**: "Make the system in catalog/purchase to add zone."

**Built**: `Receive GRN` modal in `inventory.html` triggered by the existing `+ Receive GRN` button. 4-step stepper (Match PO → Verify boxes → **Assign placement** → Post). Per line: editable rec qty, primary fixture dropdown filtered by SKU type, qty + position-within-fixture, split-row support for display + back-stock.

---

## Phase 7 · 8 more documents (statutory + operational)

**User**: "Make Sale order/Estimate, Sale Return print, Stock Purchase Tally, DayBook Print, Damage Product section screen and print, Workshop and QC sticker, Expense Approval, Ledgers (Vendor, Customer, etc.)."

**Added prints**:
1. Sale Order / Estimate (A4) — pro-forma with advance
2. Sale Return (A5)
3. Stock Purchase Tally (A4) — pre-GRN delivery person handoff
4. Day Book (A4) — primary daily journal
5. Damage / write-off report (A4)
6. Workshop QC sticker (60×90mm thermal)
7. Expense Voucher (A6)
8. Vendor Ledger statement (A4)

**Added screens**:
- `accounts.html` — Expense approval / Damage register / Vendor ledger / Customer ledger
- `pricing.html` — Bulk price update / Bulk offer update
- New **Tally inbox** tab in `inventory.html`

**Files**: `print/templates4.jsx`, new screens, shell rail updated to add Accounts + Pricing nav items.

---

## Phase 8 · Customer Delivery Handover

**User**: "Create handover document."

**Added**: Delivery Handover (A4) — closes the order loop (Job Card → QC Sticker → Handover). 9-step dispensing checklist, free-services list, adaptation watch callout, customer + dispenser sign-off.

**Files**: `print/templates5.jsx`.

---

## Phase 9 · Phase A — Clinical ↔ Rx alignment

**User**: "Prescription module needs to be connected and linked to the clinic part, so keep the fields and layout linked and similar looking."

**Decision** (after a back-and-forth where the user asked for analysis-first):
- Refraction grid in `clinical.html` extended from 5 cols → 8 cols to match Rx card (added Prism + Base)
- In-screen Rx preview extended from 6 cols → 9 cols (added PD)
- Doctor reg unified: `DMC reg. 4412/2014` + `NCAHP UID OPT-IN-22-04412`

---

## Phase 10 · Phase C — Responsive pass (POS, Inventory, Accounts, Pricing)

**User**: "Optimize the app screen by screen for both iPad horizontal and vertical, mobile, and desktop/laptop."

**Approach**: Added media queries at 1280px / 1024px / 640px to each screen. Side panels collapse to drawers progressively; stat strips shrink columns; tab bars become scrollable; dense tables get horizontal scroll.

**Files**: per-screen `<style>` blocks updated with responsive sections. See `RESPONSIVE.md` for per-screen specifics.

---

## Phase 11 · Cash register feature

**User**: "Add cash register in accounts page with denomination opening day cash closing cash responsible person and more."

**Built**: New **Cash register** tab in `accounts.html`. 5-KPI top strip + 2-col denomination + reconciliation + session history table + cash policy summary.

**User followup**: "Expand coins, remove 2000 note."

**Refined**: Denomination list now: ₹500 / ₹200 / ₹100 / ₹50 / ₹20 / ₹10 notes + ₹10 / ₹5 / ₹2 / ₹1 coins (each as individual rows with ± steppers). ₹2000 note removed (demonetised by RBI in 2023).

---

## Phase 12 · Visual polish pass (11 screens)

**User**: "Go through the entire software screen by screen section by section making small tweaks to make the UI look cleaner and easy to understand and use."

**Constraints set**: No structural changes; no new features; no copy rewrites; small CSS-only tweaks; one screen per round-trip.

**Applied to all 11 screens** (Hub → POS → Clinical → Inventory → Print → Accounts → Pricing → Tasks → Reports → Jarvis → Setup):
- Subtle row / card / cell hover bg-sunk transitions
- `::before` ink-bar prefixes on section h3 headers (4×12px, ink color)
- Pulsing green dots on live Jarvis agents (`@keyframes pulse`, 2.5s)
- kbd-style boxed hotkey hints in POS action bar
- Tighter letter-spacing on large KPI values
- Stat-strip cells get hover bg + smoother transitions
- Cart items in POS get hover bg + grand-total stronger contrast

Each screen verified via `fork_verifier_agent` before moving on.

---

## What was explicitly declined / deferred

During Phase 9 analysis, two phases were declined by the user:

- **Phase B · Indianized patient/customer header** — bilingual labels (Hindi sub-text under English labels), honorifics (Smt./Shri/Ms./Mr.) in screen UI, family bundles, WhatsApp send buttons made consistent across screens, festival promo callouts.
- **Phase D · Indian micro-touches** — bilingual section headers everywhere, family-bundle pricing call-outs, festival promo strips throughout.

User explicitly said: *"Don't do D and B. Do A then C."*

These remain available as future passes if desired.

---

## Order of file production

1. Mock data layer (`shell/data.js`) — store, cashier, catalog, queue, tasks, agents
2. Shell scaffolding (`shell/shell.jsx`, tokens, mobile.css)
3. Hub
4. POS, with cart sidecar
5. Clinical, with Rx preview
6. Inventory (stock ledger + matrix + non-moving + transfers + reorder)
7. Print module foundation (6 base templates)
8. Print expansion to 12, then 18, then 26 (across `templates2-5.jsx`)
9. Display fixtures + placements added to MOCK
10. Inventory: Display layout tab + Zone column + Tally inbox tab + GRN modal
11. Tasks, Reports, Jarvis, Setup (built progressively)
12. Accounts (Expense / Damage / Ledgers / Cash register)
13. Pricing & Offers
14. Responsive pass
15. Visual polish pass

---

## Snippets for the implementing developer

- The mock data shapes in `MOCK_DATA.md` are the contract for your backend
- The statutory mandates in `STATUTORY_NOTES.md` are non-negotiable on the print side
- The breakpoint patterns in `RESPONSIVE.md` should be implemented with container queries where the framework supports
- The 26 print templates in `print/` are the largest pixel-precise spec — implement them by recreating in your codebase's PDF rendering library (react-pdf, puppeteer, or server-side LaTeX/Typst), not by trying to ship the HTML directly to browser-print
- The Shell component in `shell/shell.jsx` is the cross-screen scaffold and should be your starting point — every screen depends on it

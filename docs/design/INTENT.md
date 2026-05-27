# INTENT — Why each screen exists, what it does, and how decisions were made

This document is the conversation distilled. It captures the user's intent behind every screen, the jobs-to-be-done, the constraints discovered along the way, and the design decisions that flowed from them. Read this before writing implementation code.

---

## Project context

**Better Vision Opticals Pvt. Ltd.** runs a chain of optical retail stores in India. The flagship is "GK-I Flagship" in Greater Kailash-I, Delhi. The IMS handles every operational surface of an Indian optical store: clinical (eye exam → Rx), POS (sale), inventory (catalog, stock, display), accounts (cash, expenses, ledgers), pricing (bulk update, offers), tasks (auto + manual), reports (sales, GST, cohorts), automation (Jarvis agents), and configuration.

The primary user is a **Store Manager** (Sonia Khatri · EMP-0142 in the mocks). Secondary users include Opticians (Karan T.), Optometrists (Dr. R. Malhotra), Inventory clerks (Riya P.), HQ Ops Head (Priya B.), and a Superadmin for Jarvis.

The system is designed for **iPad horizontal at the counter** as the primary form factor, but works on desktop, iPad vertical, and mobile.

---

## Hub (`hub.html`) — the landing page

**Intent**: Give every staff member a clear "what now?" picture in <5 seconds. Surface the day's pulse, urgent escalations, and quick links to every module.

**Layout**:
- Wide hero with role-aware greeting + key metrics
- News strip (3-column) with timestamped operational events
- 12-column grid of module cards (POS, Clinical, Inventory, Print, Accounts, Pricing, Tasks, Reports, Jarvis, Setup)
- Each card has an icon, name, badge (Most used / Super-admin / new), one-line description, and pulse stat

**Why the news strip**: Indian stores run on real-time awareness — a delivery person walked in, an invoice was raised, a cycle count started. The news strip is the calmest place to surface those moments without requiring tab-switching.

**Visual polish pass**:
- Module icons get a 1px border + bg swap on card hover
- News-strip `ago` timestamp gets a dot prefix (`● Posted 38m ago`)
- Module badges on feature cards use translucent white-on-dark instead of brand red

---

## POS (`pos.html`) — the 6-step checkout

**Intent**: Indian optical sales involve frame + lens + sometimes contact lens + accessories, often with a stored Rx and a partial advance. The 6-step wizard isolates each decision so the cashier never feels overwhelmed and so cart-state is always recoverable.

**The 6 steps**:
1. **Customer** — search by phone, scan QR, walk-in pass-through
2. **Rx** — auto-pull latest prescription; optometrist-only edits
3. **Products** — frame, lens, CL, accessory scan or browse
4. **Cart** — running tally on the right, hold/save for later
5. **Payment** — Cash / Card / UPI / BV-Wallet / Gift voucher · split allowed
6. **Review** — confirm, print invoice + receipt + warranty + lens-job card

**Design language**:
- 220px left steps-rail (icon-only at iPad-H)
- 1fr center work area
- 380px right cart sidecar (becomes drawer at iPad-V)
- Action bar at the bottom with `Esc`, `←`, `→`, `Enter` hotkey hints in `kbd`-style boxed keys
- Completed steps in left rail get a faint ok-tinted background

**Why split payments are first-class**: It's normal for a customer to pay ₹20,000 by card + ₹8,110 by UPI. The split-lines pattern (with tender + reference + amount) supports up to 4 simultaneous tenders without UI gymnastics.

---

## Clinical (`clinical.html`) — eye-exam queue + Rx capture

**Intent**: An optometrist runs back-to-back appointments. They need to see who's waiting, who's currently in the chamber, and to capture refraction values quickly. The captured Rx must flow straight to the print module's Rx card.

**Layout**:
- 320px left queue (status-tabbed: Waiting / In exam / Called / All)
- 1fr center exam form
- 360px right Rx card preview (collapses to drawer < 1280px)

**Exam form**:
- Pre-test card (6 cells: auto-ref OD/OS, keratometry, IOP, pupil size, colour vision)
- **Refraction grid — 8 columns (matches print Rx card)**: Eye / Sph / Cyl / Axis / Add / Prism / Base / VA
- Lens recommendation chips (4 options each)
- Assessment notes textarea

**Critical design decision — Phase A field alignment** (resolved during conversation):
The clinical refraction grid was originally 5 columns (Sph/Cyl/Axis/Add/VA), but the printed Rx card has **8 columns** (adds Prism, Base, PD). This caused data loss when sending from clinical → print. Now both surfaces are 8-column. Same for the PD column in the sidebar preview (5 → 9 cols).

**Doctor registration aligned**: `DMC reg. 4412/2014` + `NCAHP UID OPT-IN-22-04412` (was inconsistent before).

**iPad responsive**: At ≤1280px, Rx column becomes a `Rx →` drawer toggle. At ≤1024px, queue also becomes a `☰ Queue` drawer.

---

## Inventory (`inventory.html`) — stock + display zones

**Intent**: Indian optical inventory has unusual physics — every SKU lives on a **specific fixture** (wall, counter, pillar, locked cabinet, drawer, CL fridge, window). Staff must be able to (1) find anything in 5 seconds, (2) audit visual merchandising, (3) count by fixture.

**8 tabs**:
1. **Stock ledger** — table of every SKU with brand/model/type/zone/MRP/cost/stock/value
2. **Display layout** — floor map of fixtures + side detail panel
3. **Lens power matrix** — power-grid view of lens inventory
4. **Cycle count** — open counts in progress
5. **Non-moving** — items not sold in 90+ days
6. **Transfers** — inter-store stock movement
7. **Tally inbox** — open stock-purchase tallies awaiting GRN conversion
8. **Reorder drafts** — auto-drafted POs from Jarvis · Stock Sentinel

### Display fixture system (user-requested feature)

**Intent expressed**: *"I would like to have a system through which I can define which product is in what section — wall display, counter display, pillar display etc. Should also be included in stock count sheet."*

**Design**:
- 13 fixtures across 3 floors (Ground floor, Storage, Clinic chamber)
- Each fixture has: code, name, type (window/wall/pillar/counter/cabinet/gondola/drawer/fridge), zone (A/B/C), capacity, lockable flag, merch tags (which categories belong), last audit, special attrs (temp-controlled, spotlit, mannequin, anti-theft)
- 13 SKU→fixture placements with exact position within fixture ("shelf-2 · slot-04", "mannequin · centre", "bin-A1 · power matrix")
- Each SKU can have a primary display placement + back-stock drawer placement (split)
- Stock count sheet now **groups by fixture** instead of by shelf range — section header strip per fixture (code · name · zone · capacity %) followed by its SKUs, then a fixture sign-off row

### GRN Receive flow (user-requested feature)

**Intent expressed**: *"Make the system in catalog/purchase to add zone."*

**Design**: The Receive GRN modal (triggered from inventory header `+ Receive GRN` button) is where stock physically enters the system, so it's the right moment to declare which fixture each unit lives on.

The modal has a 4-step stepper:
1. ✓ Match PO & vendor invoice (done)
2. ✓ Verify boxes & seal (done)
3. **Assign placement** (active step — the new bit)
4. Post & close

Per-line:
- Editable received quantity (auto-detects shortages)
- Primary fixture dropdown filtered by category (CL items show only CL-tagged fixtures)
- Qty + position-within-fixture text
- **+ Split button** opens a secondary fixture row (storage drawer) — for one-line spanning display + back-stock with qty validation

---

## Print (`print.html`) — 26 statutory + operational templates

**Intent expressed**: *"For many of these documents more than matching the look and feel of our software generating trust and reliability is more important."*

This module is the single largest piece of work. The user pushed back hard on initial "editorial" designs and we converged on a statutory aesthetic — bordered, ALL-CAPS, sans-only, copy-marker bars, declarations, authorized-signatory blocks. The visual language borrows directly from real Indian tax invoices and delivery challans.

### Statutory aesthetic principles

| Concept | Implementation |
| --- | --- |
| Copy markers | Top strip on every doc: `ORIGINAL FOR RECIPIENT · ☐ DUPLICATE FOR TRANSPORTER · ☐ TRIPLICATE FOR SUPPLIER` |
| Statutory rule reference | Footer line: `Issued under Sec. 31 CGST Act 2017 r/w Rule 46` |
| Place of supply | Header meta: `07 · Delhi (intra-state)` |
| Reverse charge | Header meta: `Reverse charge: No` |
| HSN per line | Every line item has an HSN/SAC column |
| HSN-wise tax summary | Consolidated table after line items |
| Amount in words | "Indian Rupees Twenty-Eight Thousand One Hundred Ten Only" (Indian numbering — uses `Crore`, `Lakh`) |
| Declarations | "We declare that this invoice shows the actual price…" footer block |
| Signature block | Authorized signatory + `[Seal]` placeholder |
| Retention | Footer line: `retain for 7 years per CGST Rule 56` |

### Header strategy (user-requested clarification)

**Intent expressed**: *"We need to add a proper header with many details on top of customer facing prints and just a header with our logo and branch name for staff facing prints."*

Two reusable header components in `legal_helpers.jsx`:

- **LegalHeader** — full statutory: legal name, trade name, registered office, GSTIN, PAN, CIN, drug licence, place of supply with state code, contact details, copy marker, doc number, doc-type meta strip. Used on customer-facing and vendor-facing documents (invoices, Rx cards, GRNs, POs, etc.).
- **StaffHeader** — minimal: logo + branch name + doc number + a chip strip of operational meta. Used on internal documents (job card, barcode labels, count sheet, Z-report, etc.).

### The 26 templates by category

Customer-facing (LegalHeader):
- Tax Invoice (A4) · Prescription Card (A5) · Lens Job Card (A5)¹ · Queue Token (80mm)² · Delivery Challan (A4) · Credit Note (A5) · Thermal Receipt (80mm) · Purchase Order (A4)³ · Goods Receipt Note (A4)³ · Z-Report (A4)¹ · Appointment Slip (A5) · Warranty Card (A6) · Gift Voucher (A5) · Customer Statement (A4) · Debit Note (A4)³ · Sale Order (A4) · Sale Return (A5) · Vendor Ledger (A4)³ · Delivery Handover (A4)

Staff-facing (StaffHeader):
- Barcode Labels (A4 32-up) · Stock Count Sheet (A4) · Stock Tally (A4) · Shift Handover (A4) · Damage Report (A4) · QC Sticker (60×90mm) · Expense Voucher (A6)

¹ Job card was reclassified from customer-facing to staff-facing per user request
² Token is technically customer-facing but uses a thermal-receipt style (minimal header) due to format
³ Vendor-facing but treated with LegalHeader since they have a counter-party reading them

### Notable per-document decisions

- **Tax invoice**: Includes a HSN-wise consolidated tax summary table after line items, an `IRN` field flagged as `e-invoice exempt` (Better Vision is below the ₹5cr e-invoice threshold), and a `Payment terms` field showing the actual split (Card 70% · UPI 30%).
- **Delivery Challan**: Triplicate marking + e-Way Bill number + Transport mode + Vehicle number + Driver + Boxes/Seals block — all per Rule 55 mandated fields.
- **Prescription Card**: Both DMC reg + NCAHP UID shown — NCAHP registration became mandatory in 2024 for all practising optometrists in India.
- **Z-Report**: Includes a 6-KPI strip (gross sales, net sales, transactions, avg basket, refunds, cash variance), tender reconciliation, denomination count, HSN-wise GST output, and an ops events timeline.
- **GRN**: Variance handling with auto-generated debit note callout ("Discrepancy log · short shipment" warn-tinted block).
- **Delivery Handover** (closes the loop): 9-step dispensing checklist, free-services list, adaptation watch callout, customer + dispenser sign-off — issued at the moment customer picks up finished spectacles.

---

## Accounts (`accounts.html`) — back-office hub

**Intent**: Back-office surfaces (expense approval, damage, ledgers, cash register) belong together — they share the "money in / money out" framing.

**5 tabs**:
1. **Expense approval** — inbox + detail panel + approval chain (raised → SM PIN → ASM if >₹2k)
2. **Cash register** — denomination count + reconciliation (user-requested feature)
3. **Damage register** — weekly write-off table with reason codes (R-1…R-6)
4. **Vendor ledger** — AP statement view
5. **Customer ledger** — wallet + loyalty + invoice history per customer

### Cash register (user-requested feature)

**Intent expressed**: *"Add cash register in accounts page with denomination opening day cash closing cash responsible person and more."* Then refined: *"Expand coins, remove 2000 note."*

**Design**:
- 5-KPI top strip (Open since, Responsible, Counted cash, Variance with tolerance check, MTD variance)
- 2-column layout: denomination count (left) + reconciliation (right)
- **Denomination breakdown** (notes + coins, no ₹2000 since RBI demonetised it):
  - Notes: ₹500 / ₹200 / ₹100 / ₹50 / ₹20 / ₹10
  - Coins: ₹10 (bi-metallic) / ₹5 (nickel-brass) / ₹2 (nickel-brass) / ₹1 (stainless steel)
  - Each row has ± stepper input + auto-computed line total
- **Reconciliation**: Opening float + cash sales − refunds − expenses − bank deposit = expected. Counted from drawer. Variance shown with warn-tinted row.
- Session history table (7 sessions, AM/PM shifts, sign-off status)
- Cash policy summary footer (tolerance, approval matrix, deposit cadence, drawer lock, retention)

---

## Pricing & Offers (`pricing.html`)

**Intent expressed**: *"Bulk Price updation section screen / Bulk Offer Updation Section screen."*

**Two tabs**:
1. **Bulk price update** — scope picker (left sidebar with brand / type / preset filters) + editable price grid + dark bulk-action bar + audit log
2. **Bulk offer update** — offer cards (live / scheduled / draft / ended) + schedule timeline

**Bulk price update specifics**:
- Scope picker filters by Brand, Type, Quick presets (Non-moving 90+, MRP > ₹10k, Stock < 3), Margin gate
- Each SKU row: checkbox · brand/model · cost (read-only) · MRP (editable) · Sell (editable) · delta chip · margin %
- Dirty rows highlighted with warn-tinted background + left ink-bar
- **Dark bulk-action bar at top** (high contrast on purpose — destructive operation):
  - "X SKUs selected" counter
  - Apply [percentage|absolute ₹] amount to [Sell|MRP|Both]
  - Schedule: Apply now / Tomorrow / Custom date
  - Apply button
- Margin gate option: "Block if margin < 25%", "Require ASM PIN if > 10% drop"
- Audit log shows last 5 bulk operations with diff (was → now) + who

**Bulk offer specifics**:
- 6 sample offers in 4 states (live, scheduled, draft, ended)
- Each offer card: code · name · pitch text · discount type · scope · items count · window · redemptions + revenue · status badge · actions
- Schedule timeline shows offer bars on a March → May axis with a "today" marker

---

## Tasks (`tasks.html`)

**Intent**: Tasks are a mix of auto-tasks raised by Jarvis agents (e.g. cash variance > tolerance) and manual SOP follow-ups. Priority-tagged, due-time aware.

**Layout**: List on left (1fr) + side panel on right (400px) with full task detail.

**Priority strip** at top: P1 (red) / P2 (warn) / P3 (info) / P4 (mute).

**Per task**: pri chip · ref code · title · description · due-time chip · assignee avatar · linked artifact (invoice, GRN, etc.).

---

## Reports (`reports.html`)

**Intent**: Stock manager needs a single screen with daily / weekly / monthly business metrics. Day-end, sales, GST, cohort, inventory tabs.

**Layout**: KPI cards (4 per row) + chart row (2fr/1fr) + drilldown tables.

**Visual decisions**:
- KPI cards have a 4-col layout with delta chips (up/down with bg color)
- Chart head h3 gets a small ink-bar prefix (consistency token across the app)
- Bar charts fade non-hovered bars on hover

---

## Jarvis (`jarvis.html`) — always-on agents

**Intent**: Background automation that quietly keeps the store in line. 8 agents, each with a single, narrow job. Surfaces actions taken in 24h, awaiting-approval queue, median act time.

**The 8 agents**:
| ID | Name | Job |
| --- | --- | --- |
| AG-INV | Stock Sentinel | Flags low stock, auto-drafts reorder POs |
| AG-PRI | Price Patrol | Watches MRP/competitor deltas |
| AG-AGE | Aging Advisor | Surfaces aged inventory for clearance |
| AG-ESC | Escalation Engine | Auto-escalates overdue P1/P2 tasks |
| AG-RX | Rx Reconciler | Verifies Rx ↔ lens-order alignment |
| AG-CX | NPS Nudger | Schedules follow-ups post-delivery |
| AG-ATT | Attendance Sentinel | Shift & break anomaly detection |
| AG-COPY | Copy & Content | Generates SMS / WhatsApp offers |

**Visual signature**: Running agents have a **pulsing green dot** beside the h3 (`box-shadow: 0 0 0 3px rgba(13, 123, 76, .15)` with a 2.5s `pulse` animation). Paused agents get a static grey dot.

---

## Setup (`setup.html`)

**Intent**: Per-store configuration. Some settings are locked at HQ level — changes require superadmin approval and audit log entries.

**Layout**: 240px left section nav + 1fr content + 380px right audit log.

**Sections** (8 of them, 84 total settings):
- Billing & GST · POS behaviour · Clinical · Inventory · Roles & permissions · Notifications · Payments · Hardware

**Each setting (`s-row`)**: title · description · setting reference code (`POS.CUST_PROMPT`) · `locked` flag if HQ-only · control (toggle / select / number input) · `changed` chip if recently modified.

**Audit log** (right column) shows every config change with diff (was → now), timestamp, who.

---

## Cross-cutting design decisions

### 1. Statutory aesthetic across all prints
*Driven by user pushback*: "These feel too radical. For many of these documents more than matching the look and feel of our software generating trust and reliability is more important."

Result: All print templates have bordered tables, ALL-CAPS labels, copy markers, declarations, statutory rule references. The brand softens but trust gets dialed up.

### 2. Indian numbering + ₹ symbol
Every financial number uses `Intl.NumberFormat('en-IN')` for Lakh/Crore grouping. ₹ is prefixed (not `Rs.`). Amount-in-words helper handles Crore/Lakh/Thousand correctly.

### 3. Display fixture system
*Driven by user request*: 13 fixtures across 3 floors, 13 placements mapping SKUs to specific positions. Stock count sheet refactored to group by fixture. GRN receive modal asks for placement at the moment goods arrive.

### 4. Header strategy split
*Driven by user request*: Customer / vendor-facing prints use full statutory header; staff-facing prints use minimal logo + branch.

### 5. Cash register denominations
*Driven by user request*: All notes (₹500 down to ₹10, no ₹2000 since demonetised) + all coins (₹10 / ₹5 / ₹2 / ₹1) as individual rows with ± steppers.

### 6. Responsive coverage
All screens collapse cleanly at 1280 / 1024 / 640 — see `RESPONSIVE.md` for per-screen patterns.

### 7. Visual polish pass (final round)
A polish pass touched all 11 screens with consistent small details:
- Subtle row/card hover states
- Ink-bar prefix on section h3 headers (`::before` pseudo-element, 4×12px ink bar)
- Pulsing green status dots for live agents
- Kbd-style boxed hotkey hints in POS action bar
- Tabular-nums on every financial / countable figure
- Stat-strip cells hover with surface-2 bg

---

## What's NOT in this design

Decisions that were explicitly **deferred** during the conversation:

- **Hindi / bilingual labels** in screens (Phase B / D were declined by user)
- **Family bundle pricing call-outs** (declined)
- **Festival promo strips** beyond the pricing offers tab (declined)
- **WhatsApp send buttons** (visible only in some places, not made consistent)
- **Honorific prefix** (Mr./Ms./Smt./Shri) in screen UI (only in prints)

These can be revisited in a future pass.

---

## Suggested implementation order

If the developer has limited time and needs to ship incrementally, this is the recommended order based on user value and complexity:

1. **Shell + Hub** — establishes the visual system, nav, tokens
2. **POS** — highest counter-side value
3. **Clinical** — closes the Rx → POS loop
4. **Print module** (start with Tax Invoice + Thermal Receipt + Rx Card — the daily three)
5. **Inventory** — Stock ledger + Display layout + GRN receive
6. **Accounts** — Cash register + Expense approval first
7. **Print module** (the rest of the 26 templates)
8. **Pricing & Offers** — Ops Head tool, can ship later
9. **Tasks** — depends on Jarvis being live
10. **Reports** — read-only, build on top of finalized data shapes
11. **Jarvis** — agent orchestration, lots of backend work
12. **Setup** — last, lowest user value (admin only)

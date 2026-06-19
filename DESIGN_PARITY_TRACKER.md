# IMS 2.0 — Design-Parity Tracker

Screen-by-screen inventory of the REAL app, mapped to the 11 Claude Design files in `design_import/`. **Source of truth for the reskin loop: every later design change must keep every feature listed here reachable.** Status: Pending / Design-updated / Implementing / Done.


> Generated 2026-06-19 from an 8-agent read-only audit (wf_0cfc807f). POS is Done (condensed-flow #783) — deep POS list also in `POS_CURRENT_INVENTORY.md`.


## Overview

| Module | Screen | Route | Design file | Status |
|---|---|---|---|---|
| POS + Walkouts + Incentives | POS Main | `/pos` | pos.html | Done |
| POS + Walkouts + Incentives | Walkouts List | `/walkouts` | none (no design yet) | Pending |
| POS + Walkouts + Incentives | Walkout Intake Modal | `Modal on /walkouts; also accessible from POS layout (WalkoutIntakeModal component)` | none (no design yet) | Pending |
| POS + Walkouts + Incentives | Walkout Detail | `/walkouts/{walkoutId}` | none (no design yet) | Pending |
| POS + Walkouts + Incentives | Walkouts Dashboard | `/walkouts/dashboard` | none (no design yet) | Pending |
| POS + Walkouts + Incentives | Daily Scorecard | `/incentive` | none (no design yet) | Pending |
| POS + Walkouts + Incentives | MTD Leaderboard | `/incentive/leaderboard` | none (no design yet) | Pending |
| POS + Walkouts + Incentives | Points History | `/incentive/staff/{staffId}` | none (no design yet) | Pending |
| POS + Walkouts + Incentives | Payout Dashboard | `/incentive/payout` | none (no design yet) | Pending |
| POS + Walkouts + Incentives | Payout Snapshots | `/incentive/payouts` | none (no design yet) | Pending |
| POS + Walkouts + Incentives | Incentive Settings | `/incentive/settings` | none (no design yet) | Pending |
| Clinical / Optometry + Workshop | Clinical: Patient Queue & Eye Test Management | `/clinical` | clinical.html | Done |
| Clinical / Optometry + Workshop | Clinical: Prescriptions Library | `/prescriptions` | clinical.html | Done |
| Clinical / Optometry + Workshop | Clinical: Test History | `/clinical/history` | clinical.html | Done |
| Clinical / Optometry + Workshop | Clinical: Family Prescriptions | `/clinical/family-rx` | clinical.html | Done |
| Clinical / Optometry + Workshop | Clinical: Contact Lens Fitting | `/clinical/contact-lens` | clinical.html | Done |
| Clinical / Optometry + Workshop | Workshop: Job Pipeline & QC | `/workshop` | none (no design yet) | Pending |
| Inventory + Catalog + Transfers + Barcode/Labels | Inventory List/Stock Ledger | `/inventory (tab: catalog)` | inventory.html | Done |
| Inventory + Catalog + Transfers + Barcode/Labels | Stock Alerts | `/inventory (tab: alerts)` | inventory.html | Done |
| Inventory + Catalog + Transfers + Barcode/Labels | Low Stock / Reorder Dashboard | `/inventory (tabs: low-stock, reorders)` | inventory.html | Done |
| Inventory + Catalog + Transfers + Barcode/Labels | Contact Lens FEFO (First Expiry First Out) | `/inventory (tabs: contact-lens, power-grid)` | inventory.html | Done |
| Inventory + Catalog + Transfers + Barcode/Labels | Stock Transfers (Ship/Receive/Quarantine) | `/inventory (tab: transfers) OR /inventory/transfers (dedicated page)` | inventory.html | Done |
| Inventory + Catalog + Transfers + Barcode/Labels | Stock Quarantine Queue | `/inventory (tab: quarantine)` | inventory.html | Done |
| Inventory + Catalog + Transfers + Barcode/Labels | Stock Count Scanning Interface | `/inventory (tab: stock-count)` | inventory.html | Done |
| Inventory + Catalog + Transfers + Barcode/Labels | Stock Aging Report | `/inventory (tab: aging)` | inventory.html | Done |
| Inventory + Catalog + Transfers + Barcode/Labels | Serial Number Tracker | `/inventory (tab: serial-numbers)` | inventory.html | Done |
| Inventory + Catalog + Transfers + Barcode/Labels | Non-Moving Stock Analysis | `/inventory (tab: non-moving)` | inventory.html | Done |
| Inventory + Catalog + Transfers + Barcode/Labels | Sell-Through & Overstock Analysis | `/inventory (tabs: sell-through, overstock)` | inventory.html | Done |
| Inventory + Catalog + Transfers + Barcode/Labels | Display Fixture & Layout Management | `/inventory (tab: display-layout)` | inventory.html | Done |
| Inventory + Catalog + Transfers + Barcode/Labels | Catalog/Product Add (Quick Add Mode) | `/catalog/add (default: ?mode=single)` | inventory.html | Done |
| Inventory + Catalog + Transfers + Barcode/Labels | Catalog/Product Add (Guided Mode - Wizard) | `/catalog/add?mode=guided` | inventory.html | Done |
| Inventory + Catalog + Transfers + Barcode/Labels | Catalog/Product Add (Bulk Mode - Rapid Grid) | `/catalog/add?mode=bulk` | inventory.html | Done |
| Inventory + Catalog + Transfers + Barcode/Labels | Buy Desk (Catalog → Purchase Landing) | `/catalog/buy-desk` | inventory.html | Done |
| Inventory + Catalog + Transfers + Barcode/Labels | Catalog Autopilot (Brand+Model Search → Approve → Publish) | `/catalog/autopilot` | inventory.html | Done |
| Inventory + Catalog + Transfers + Barcode/Labels | Pricing & Offers (Bulk Price + Discount Cap Enforcement) | `/catalog/pricing` | pricing.html | Done |
| Inventory + Catalog + Transfers + Barcode/Labels | Stock Replenishment (Auto-Reorder) | `/inventory/replenishment` | inventory.html | Done |
| Inventory + Catalog + Transfers + Barcode/Labels | Stock Audit | `/inventory/audit` | inventory.html | Done |
| Inventory + Catalog + Transfers + Barcode/Labels | Opening Stock Import | `/inventory/opening-stock` | inventory.html | Done |
| Inventory + Catalog + Transfers + Barcode/Labels | Online Stock Sync (Shopify/E-Commerce) | `/inventory/online-sync` | inventory.html | Done |
| Inventory + Catalog + Transfers + Barcode/Labels | Power Grid (Contact Lens Multi-Dimension View) | `/inventory/power-grid` | inventory.html | Done |
| Customers / CRM / Marketing / Loyalty / Vouchers / Store-credit | Customers List | `/customers` | accounts.html | Done |
| Customers / CRM / Marketing / Loyalty / Vouchers / Store-credit | Customer 360 Dashboard | `/customers/360, /customers/:customerId/360` | accounts.html | Done |
| Customers / CRM / Marketing / Loyalty / Vouchers / Store-credit | Customer Segmentation | `/customers/segmentation` | accounts.html | Done |
| Customers / CRM / Marketing / Loyalty / Vouchers / Store-credit | VIP Churn Watchlist | `/customers/vip-churn-watchlist` | accounts.html | Done |
| Customers / CRM / Marketing / Loyalty / Vouchers / Store-credit | NBA (Next-Best-Action) Call List | `/customers/nba` | accounts.html | Done |
| Customers / CRM / Marketing / Loyalty / Vouchers / Store-credit | Lapsed Reactivation Worklist | `/customers/reactivation` | accounts.html | Done |
| Customers / CRM / Marketing / Loyalty / Vouchers / Store-credit | Family/Household Loyalty Wallet | `/customers/family-wallet` | accounts.html | Done |
| Customers / CRM / Marketing / Loyalty / Vouchers / Store-credit | Contact Lens Refill Worklist | `/customers/cl-refill` | accounts.html | Done |
| Customers / CRM / Marketing / Loyalty / Vouchers / Store-credit | Loyalty Program Manager | `/customers/loyalty` | accounts.html | Done |
| Customers / CRM / Marketing / Loyalty / Vouchers / Store-credit | Per-Customer Loyalty Ledger | `/customers/:customerId/loyalty` | accounts.html | Done |
| Customers / CRM / Marketing / Loyalty / Vouchers / Store-credit | Campaign Manager | `/customers/campaigns` | accounts.html | Done |
| Customers / CRM / Marketing / Loyalty / Vouchers / Store-credit | Referral Tracker | `/customers/referrals` | accounts.html | Done |
| Customers / CRM / Marketing / Loyalty / Vouchers / Store-credit | Customer Feedback & NPS | `/customers/feedback` | accounts.html | Done |
| Customers / CRM / Marketing / Loyalty / Vouchers / Store-credit | Follow-up Dashboard | `/customers/follow-ups` | accounts.html | Done |
| Customers / CRM / Marketing / Loyalty / Vouchers / Store-credit | WhatsApp Inbox | `/customers/whatsapp-inbox` | accounts.html | Done |
| Customers / CRM / Marketing / Loyalty / Vouchers / Store-credit | Ad Performance Dashboard | `/marketing/ad-performance` | accounts.html | Done |
| HR + Payroll + Attendance | HR Page / Dashboard | `/hr` | none (no design yet) | Pending |
| HR + Payroll + Attendance | Attendance Page | `/attendance` | none (no design yet) | Pending |
| HR + Payroll + Attendance | Monthly Attendance Grid | `N/A (component used in Attendance and HR page)` | none (no design yet) | Pending |
| HR + Payroll + Attendance | Payroll Dashboard | `/hr/payroll` | none (no design yet) | Pending |
| HR + Payroll + Attendance | Salary Setup Page | `/hr/salary-setup` | none (no design yet) | Pending |
| HR + Payroll + Attendance | Payroll Run Page | `/hr/payroll-run` | none (no design yet) | Pending |
| HR + Payroll + Attendance | Employee Self-Service (My Work) | `/my-work` | none (no design yet) | Pending |
| HR + Payroll + Attendance | Week-Off Swap Component | `N/A (tab in /hr, embedded component)` | none (no design yet) | Pending |
| HR + Payroll + Attendance | Shift Setup Component | `N/A (tab in /hr, manager-only, embedded component)` | none (no design yet) | Pending |
| HR + Payroll + Attendance | Commission Leaderboard Component | `N/A (tab in /hr, embedded component)` | none (no design yet) | Pending |
| Finance/Accounting + Purchase/Vendors | Finance Dashboard (P&L Overview) | `/finance/dashboard` | accounts.html | Done |
| Finance/Accounting + Purchase/Vendors | Cash Flow & Payables (Owner Dashboard) | `/finance/cash-flow` | accounts.html | Done |
| Finance/Accounting + Purchase/Vendors | Cash Register (EOD Reconciliation) | `/finance/cash-register` | accounts.html | Done |
| Finance/Accounting + Purchase/Vendors | Blind EOD Tally & Z-Read | `/finance/blind-eod` | accounts.html | Done |
| Finance/Accounting + Purchase/Vendors | GST Input Credit (ITC) Reconciliation | `/finance/itc` | accounts.html | Done |
| Finance/Accounting + Purchase/Vendors | Budgeting (Planned vs Actual) | `/finance/budgeting` | accounts.html | Done |
| Finance/Accounting + Purchase/Vendors | B2B Tally Export Console | `/finance/b2b-tally-export` | accounts.html | Done |
| Finance/Accounting + Purchase/Vendors | B2B Tally Worklist (Reminders) | `/finance/b2b-tally-worklist` | accounts.html | Done |
| Finance/Accounting + Purchase/Vendors | Purchase Management (Main Module) | `/purchase (tabs: purchase-orders | purchase-invoices | variance | suppliers | vendor-returns | analytics)` | accounts.html | Done |
| Finance/Accounting + Purchase/Vendors | Goods Receipt Note (GRN) | `/purchase/grn` | inventory.html | Done |
| Finance/Accounting + Purchase/Vendors | Goods Receipt Cockpit (Vendor-first Receiving) | `/purchase/receive` | inventory.html | Done |
| Finance/Accounting + Purchase/Vendors | Vendor RMA (Return Merchandise Authorization) | `/purchase/vendor-rma` | accounts.html | Done |
| Finance/Accounting + Purchase/Vendors | Reconciliation Console (Accountant's 4-flag + Worklists) | `/purchase/recon-console` | accounts.html | Done |
| Tasks/SOP + Hub/Dashboard + Reports/Analytics | Hub/Dashboard Home Page | `/dashboard` | hub.html | Done |
| Tasks/SOP + Hub/Dashboard + Reports/Analytics | Task Management Page — Tabs: My Tasks, Team Tasks, SOP Editor, Analytics | `/tasks` | tasks.html | Done |
| Tasks/SOP + Hub/Dashboard + Reports/Analytics | Tasks Dashboard — My Tasks, Daily Checklists, Team Tasks (Manager-only) | `/tasks/dashboard, /tasks/checklists` | tasks.html | Done |
| Tasks/SOP + Hub/Dashboard + Reports/Analytics | Reports Page — Tabs: Sales, Inventory, Customers, GST; Cards: 6 deep-dive sections | `/reports` | reports.html | Done |
| Tasks/SOP + Hub/Dashboard + Reports/Analytics | Activity Log (Audit Trail) — SUPERADMIN only | `/admin/activity-log` | none (no design yet) | Done |
| Settings/Org/Users + Print templates + Jarvis/AI + Online Store + Auth/login | Login | `/login` | accounts.html | Done |
| Settings/Org/Users + Print templates + Jarvis/AI + Online Store + Auth/login | Organization (Entities + Stores) | `/organization` | setup.html | Done |
| Settings/Org/Users + Print templates + Jarvis/AI + Online Store + Auth/login | Settings (Tabbed Container) | `/settings` | setup.html | Done |
| Settings/Org/Users + Print templates + Jarvis/AI + Online Store + Auth/login | Employee Onboarding Wizard (Setup Page) | `/setup` | setup.html | Done |
| Settings/Org/Users + Print templates + Jarvis/AI + Online Store + Auth/login | Go-Live Readiness Checklist | `/go-live` | setup.html | Pending |
| Settings/Org/Users + Print templates + Jarvis/AI + Online Store + Auth/login | User Management (Settings Auth Tab) | `/settings (tab=users)` | setup.html | Done |
| Settings/Org/Users + Print templates + Jarvis/AI + Online Store + Auth/login | User Modal (Create/Edit) | `Modal within /settings tab=users` | setup.html | Done |
| Settings/Org/Users + Print templates + Jarvis/AI + Online Store + Auth/login | Print Templates Index | `/print` | print.html | Done |
| Settings/Org/Users + Print templates + Jarvis/AI + Online Store + Auth/login | Jarvis AI Control Interface | `/jarvis` | jarvis.html | Done |
| Settings/Org/Users + Print templates + Jarvis/AI + Online Store + Auth/login | Online Store (Module Shell) | `/online-store` | none (no design yet) | Pending |
| Settings/Org/Users + Print templates + Jarvis/AI + Online Store + Auth/login | Collections Editor | `/online-store/collections` | none (no design yet) | Pending |
| Settings/Org/Users + Print templates + Jarvis/AI + Online Store + Auth/login | Menus / Mega-menu Editor | `/online-store/menus` | none (no design yet) | Pending |
| Settings/Org/Users + Print templates + Jarvis/AI + Online Store + Auth/login | Image Design Queue | `/online-store/images` | none (no design yet) | Pending |
| Settings/Org/Users + Print templates + Jarvis/AI + Online Store + Auth/login | Online Orders | `/online-store/orders` | none (no design yet) | Pending |
| Settings/Org/Users + Print templates + Jarvis/AI + Online Store + Auth/login | Activity Log (Admin) | `/admin/activity-log` | setup.html | Pending |

---


## POS + Walkouts + Incentives

_IMS 2.0 Design-Parity Tracker — POS + Walkouts + Incentives Module

SCOPE: Comprehensive inventory of every control, field, dropdown, toggle, modal, tab and key business rules/validations across 10 screens covering Point of Sale (already redesigned PR #783), Walkouts intake system (Pune Incentive Module i), and Incentive/Points management (Pune Incentive Module ii–iii).

POS (1 screen, status=Done, design=pos.html):
- /pos: 6-step wizard (Customer → Prescription → Products → Review → Payment → Complete) with Rx validation (SPH -20 to +20, CYL -6 to +6, AXIS 1-180, ADD +0.75 to +3.5, all 0.25 step), spectacle lenses REQUIRE non-expired customer-matching prescription (Store-Manager+ override available), contact lenses exempt from hard requirement, GST splits per item_type (5% for frames/lenses/contacts, 18% for sunglasses/watches/accessories), discount caps (MASS 15%, PREMIUM 20%, LUXURY 5%, SERVICE 10%, NON_DISCOUNTABLE 0%), luxury brand sub-caps (Cartier/Chopard/Bvlgari 2%, Gucci/Prada/Versace/Burberry 5%), held bills with auto-park/resume, idempotency keys on order create (DELTA 3), delivery date/time-slot/priority fields, overall cart discount (capped at role discountCap).

Walkouts (4 screens, status=Pending, design=none):
- /walkouts: List with 50-item pagination, filter sidebar (date range, sales person, reason, result), "Log Walkout" primary button, "Dashboard" link.
  - WalkoutIntakeModal: 4 sections (Customer name + mobile [optional, 10 digits] + age/gender; Discovery: product + has_prescription + price_ranges; Why: reason + purchase_plan; Attribution: sales_person_id picker), validation (mobile empty or 10 digits), policy suggestion toasts (MANAGER_ESCALATE, PROMO_VOUCHER, RESTOCK_WATCH), auto-create customer from mobile, server-stamped walkout_id/store_id/sales_person_name.
  - /walkouts/{walkoutId}: Edit walkout, follow-up panel (Phase 3), result panel (Phase 3), delete with reason (elevated roles only), RBAC: SUPERADMIN/ADMIN edit any+reattribute+delete; STORE_MANAGER/AREA_MANAGER edit store+reattribute+delete; ACCOUNTANT edit+reattribute (no delete); SALES_STAFF/SALES_CASHIER/CASHIER edit own only.
  - /walkouts/dashboard: KPI strip (walk-ins today, walkouts, converted count/%, staff active), per-staff MTD cards, top-reasons chart (top 10), result-breakdown donut (DUE/NEGATIVE/CONVERTED), FU-status table, manual walk-in topup form (elevated roles only: delta + reason), 7/30/90 days window.

Incentives (5 screens, status=Pending, design=none):
- /incentive (Daily Scorecard): Date picker (default today), staff list (managers see store; staff see self), 9 score columns (Attendance max 10, Conversion max 20 with auto-fill for today, Task/Visufit/Punctuality/Behaviour/Kicker1/Kicker2/Reviews all max 10), computed Total + Eligibility chip, Save all button, Delete row modal, footfall warning banner (N3), RBAC: canEditAny check against ELEVATED_ROLES.
  - /incentive/leaderboard (MTD Leaderboard): Podium (top 3 with tier + badges), full table sorted by avg_total DESC (tie-break days_logged), 9 category averages (Att, Conv, Task, Visu, Punct, Behav, K1, K2, Rev), rank delta arrows, tier chips (PODIUM/CONTENDER/BUILDING), badge labels (eligibility_100, logged_every_day, top_riser, consistent_90), scope toggle (store/area/org, managers only), 7/30/90 days window.
  - /incentive/staff/{staffId} (Points History): Date range filter (default month-to-date), per-day table (9 scores, total, eligibility, visufit gate), staffName display.
  - /incentive/payout (Payout Dashboard): Period picker (year/month), editable inputs (last-year-sale, this-year-sale, avg-discount-%, visufit-usage-%), pool-sizing display (level/multiplier/₹), targets table, per-staff payouts grid, manager-bonuses, grand totals, SUPERADMIN-only Lock (immutable, confirm modal) + Mark Paid (with audit note), CSV export (snapshot only), snapshot status chip (PREVIEW/LOCKED/PAID).
  - /incentive/payouts (Snapshots): Snapshot history list (month, status, pool ₹, created date/by), per-row click nav to payout dashboard.
  - /incentive/settings (SUPERADMIN only): 8 independent SectionCards with per-section Save buttons: Eligibility bands (min/max/value table, add/delete), Staff weightages (9 fields), Growth factors (L1/L2/L3 %), Payout rates (L1/L2/L3 ₹/point), Multiplier curve (max_pct/multiplier table, add/delete), Visufit gate (toggle + threshold %), Supervisor bonuses (supervisor picker + amount, add/delete), Last-year-sale overrides (year/month/amount form). Each section excludes_unset on save (partial failures isolated).

All 10 screens have full RBAC enforcement (front-end route gating + server-side re-validation on write). Walkouts & Incentives are greenfield features (no prior design files) and require separate design work; POS is complete (PR #783, design_file=pos.html)._


### POS Main  ·  `/pos`
- **Design file:** pos.html  ·  **Status:** Done
- **Backend:** `orders.py`
- **MUST-PRESERVE features (11):**
  - 6-step wizard flow: Customer → Prescription → Products → Review → Payment → Complete
  - Step 1 - Customer: Search/autocomplete customer picker; add-new customer modal; selected customer card with loyalty points display; salesperson picker (for walk-in attribution); walk-in counter button (+1 walk-in)
  - Step 2 - Prescription: Optional Rx selection modal (customer's existing non-expired Rx); new Rx form modal; prescription form with SPH/CYL/ADD/AXIS entry (validated ranges: SPH -20/+20, CYL -6/+6, AXIS 1-180, ADD +0.75/+3.5, all 0.25 step); POS auto-attach single Rx (owner-gated feature flag VITE_POS_AUTO_ATTACH_SINGLE_RX); lens fitting form modal (new Phase 6.8)
  - Step 3 - Products: Barcode scanner; product search/autocomplete; product grid with images; add-to-cart per product; quantity entry; per-line discount (capped by role + category + brand); lens details modal (power/coating/etc); lens suggestion panel (auto-suggest based on Rx); add-new-product quick form
  - Step 4 - Review: Cart sidebar (persistent throughout); per-line view: product, qty, unit price, discount, GST rate, item total; overall subtotal (before GST); item-level discount detail; overall cart discount section (%, amount, reason, approved_by, capped at discountCap); GST split (CGST/SGST per item); tax invoice display; delivery date + time slot + priority (NORMAL/EXPRESS/URGENT) fields
  - Step 5 - Payment: Multiple tender types (CASH, CARD, UPI, CREDIT, EMI, STORE_CREDIT, INSURANCE_CLAIM, VOUCHER); tender amount inputs; auto-calculate change; loyalty redeem option; discount code entry; loyalty points redeem modal; order-level discount recap; approval gate for ₹0 or 100% discount orders (manager+)
  - Step 6 - Complete: Thermal receipt (80mm); A4 tax invoice (GST-compliant with HSN, per-line tax math); invoice number (GSTcompliant sequential); whatsapp/email digital receipt sharing; new-bill reset; held-bills recall/resume
  - Held bills: Auto-park (idle timeout), manual hold, recall, per-user scoping (held_by === user.id), survive logout, localStorage persistence
  - Order state: Idempotency key (DELTA 3) on order create (re-submit retry returns same order), cleared on success/new order
  - POS data validations: Rx power ranges enforced + 0.25 step validated; spectacle (Rx) lens lines REQUIRE linked non-expired customer-matching prescription (Store-Manager+ overrides expired Rx); contact lens lines EXEMPT from hard Rx requirement but power still validated; GST split per item_type (FRAMES/LENSES/CONTACTS/SUNGLASSES/WATCHES/ACCESSORIES/SERVICES); MRP > offer_price blocked at DB; offer_price < MRP locks further store-level discount; discount caps: MASS 15%, PREMIUM 20%, LUXURY 5%, SERVICE 10%, NON_DISCOUNTABLE 0%; luxury brand caps: Cartier/Chopard/Bvlgari 2%, Gucci/Prada/Versace/Burberry 5%; delivery_time_slot (2-hour window select), delivery_priority (NORMAL|EXPRESS|URGENT), cart_discount_percent/amount/reason/approved_by fields
  - Walkout compliance banner (soft-block nudge, never blocks sale, feature-flag VITE_ENABLE_POS_WALKOUT_COMPLIANCE_BANNER)

### Walkouts List  ·  `/walkouts`
- **Design file:** none (no design yet)  ·  **Status:** Pending
- **Backend:** `walkouts.py`
- **MUST-PRESERVE features (8):**
  - List view with pagination (50 items/page); total count display
  - Filter sidebar: Date range (from/to inputs), Sales person ID (text), Primary walkout reason (select dropdown), Result filter (DUE|NEGATIVE|CONVERTED|none)
  - Active filter indicator + Clear all filters button
  - Column layout: Walkout ID, Customer name, Age group, Gender, Product interested, Primary reason, Result badge (WalkoutResultBadge component), Date created
  - Page control: pagination buttons (prev/next), page start/end counter
  - Header actions: Log Walkout button (primary, opens WalkoutIntakeModal), Refresh button (with loader), Dashboard link (nav to /walkouts/dashboard)
  - Per-row click: Navigate to walkout detail (/walkouts/{walkoutId})
  - Business rules: Filter list by date range (inclusive); sales_person_id exact match; reason exact match; result value matching; pagination via skip/limit; 50 items constant PAGE_SIZE

### Walkout Intake Modal  ·  `Modal on /walkouts; also accessible from POS layout (WalkoutIntakeModal component)`
- **Design file:** none (no design yet)  ·  **Status:** Pending
- **Backend:** `walkouts.py (POST /walkouts)`
- **MUST-PRESERVE features (11):**
  - Modal dialog (fixed overlay, max-width-3xl, scrollable body)
  - Header: 'Log Walkout' title + subtitle (auto-create customer from mobile) + close button
  - SECTION 1 - CUSTOMER: Customer full name (text input, required, autoFocus); Mobile (tel input, optional, 10 digits only, numeric mask); Age group (select, required, enum: WALKOUT_AGE_GROUPS); Gender (select, required, enum: WALKOUT_GENDERS)
  - SECTION 2 - DISCOVERY: Product interested (select, required, enum: WALKOUT_PRODUCT_CATEGORIES); Has prescription (select, required, enum: YesNo); Displayed price range (select, required, enum: WALKOUT_PRICE_RANGES); Required price range (select, required, enum: WALKOUT_PRICE_RANGES)
  - SECTION 3 - WHY: Primary walkout reason (select, required, enum: WALKOUT_REASONS); Purchase planned in (select, required, enum: WALKOUT_PURCHASE_PLANS)
  - SECTION 4 - ATTRIBUTION: Sales person ID (select, required); dropdown loads SALES_ATTRIBUTABLE_ROLES from getStoreUsers (adminStoreApi); non-elevated roles see only themselves; elevated roles (SUPERADMIN/ADMIN/AREA_MANAGER/STORE_MANAGER/ACCOUNTANT) can reattribute
  - Actions: Save button (disabled while saving), Cancel/close button
  - Validation (client-side, re-validated server): customer_name required; mobile empty OR 10 digits (regex /^\d{10}$/); age_group required; gender required; product_interested required; has_prescription required; displayed_price_range required; required_price_range required; primary_walkout_reason required; purchase_planned_in required; sales_person_id required
  - No-mobile warning modal: Shows if user tries to save with empty mobile; allows explicit confirmation (impossible to follow up without number) or go back to add one
  - Policy suggestion toast messages (non-blocking) after save: MANAGER_ESCALATE → 'Staff-behaviour flagged — a manager escalation task was created'; PROMO_VOUCHER → 'Price objection — this customer may be eligible for a promo voucher'; RESTOCK_WATCH → 'Availability objection — flagged for a restock follow-up'
  - Business rules: Mobile optional (some customers don't share); server auto-creates customer if mobile is new and valid; roles without REATTRIBUTE permission auto-locked to themselves; server stamps walkout_id, store_id, sales_person_name; mobile trimmed, then normalized; empty string valid, converted to None by server; server returns policy_suggestion with action field

### Walkout Detail  ·  `/walkouts/{walkoutId}`
- **Design file:** none (no design yet)  ·  **Status:** Pending
- **Backend:** `walkouts.py (GET, PUT, DELETE /walkouts/{walkoutId})`
- **MUST-PRESERVE features (14):**
  - Back link to /walkouts
  - Title: Walkout ID + date created + customer name
  - Editable form (mirrors intake modal fields): customer_name, mobile, age_group, gender, product_interested, has_prescription, displayed_price_range, required_price_range, primary_walkout_reason, purchase_planned_in
  - Sales person picker (elevated roles only): dropdown loads staff for reattribution
  - Follow-up panel (Phase 3, FollowUpPanel component)
  - Result panel (Phase 3, ResultPanel component with WalkoutResultBadge)
  - Save button (enabled only if dirty, i.e., draft changes exist)
  - Delete button (elevated roles only): opens confirm modal requiring delete_reason (text, required)
  - RBAC edit rules: canEdit=true if canReattribute OR walkout.sales_person_id===userId; canReattribute=SUPERADMIN|ADMIN|AREA_MANAGER|STORE_MANAGER|ACCOUNTANT; canDelete=SUPERADMIN|STORE_MANAGER|AREA_MANAGER|ADMIN
  - Loading state: spinner + 'Loading walkout…'
  - Error state: AlertTriangle icon, error message, back link
  - Dirty tracking: isDirty = Object.keys(draft).length > 0
  - Mobile validation on save: must be 10 digits or left blank (regex /^\d{10}$/)
  - Business rules: Sales-staff-and-below edit only own walkouts; managers can edit and reattribute any in store; ACCOUNTANT can edit + reattribute but not delete; delete requires audit reason

### Walkouts Dashboard  ·  `/walkouts/dashboard`
- **Design file:** none (no design yet)  ·  **Status:** Pending
- **Backend:** `walkouts.py (GET /walkouts/dashboard/*, GET /walkouts/walkinstoday/*, POST /walkouts/topup)`
- **MUST-PRESERVE features (12):**
  - Header: Title, back link to /walkouts, days-window filter (7/30/90 default 30), Refresh button
  - KPI card strip (4 cards): Walk-ins today (with POS auto count vs manual topup breakdown), Walkouts last Nd (total), Converted (count + % of walkouts), Sales staff active (count)
  - Per-salesperson cards grid: One card per active staff member showing staff_name, walkouts MTD, walk-ins, conversion%, follow-up due today; clickable to detail
  - Top reasons bar chart: Displays walkoutApi.dashboardTopReasons(days, limit=10) — top 10 reasons by frequency
  - Result breakdown donut chart: Displays walkoutApi.dashboardResultBreakdown(days) — DUE|NEGATIVE|CONVERTED buckets
  - Follow-up status table: walkoutApi.dashboardFuStatus(days) — rows per follow-up round with status breakdown
  - Manual walk-in topup form (managers/admin/accountant only): Modal form with delta (number spinner, min 1), reason (text, required); 'Log walk-in' button (secondary); submit button (disabled while busy)
  - Walk-in today summary: walkoutApi.walkinsToday() — total, pos_auto_count, manual_topup breakdown
  - Days window select: 7/30/90 option, updates all KPIs + charts on change
  - Error state: AlertCircle icon + error message
  - Loading state: Loader2 spinner on refresh, KPI cards, per-staff grid
  - Business rules: canTopup = hasAnyRole(REATTRIBUTE_ROLES); topup delta ≥ 1, reason required; conversion % = CONVERTED / total; walk-in auto-incremented from POS or manual topup; same-day deduping (walking-in customer is attributed only once per day per salesperson)

### Daily Scorecard  ·  `/incentive`
- **Design file:** none (no design yet)  ·  **Status:** Pending
- **Backend:** `points.py (GET /incentive/points/daily/{date}, PATCH /incentive/points/daily)`
- **MUST-PRESERVE features (22):**
  - Date picker (default today's date as YYYY-MM-DD string); isToday flag computed
  - Staff list: Managers see whole store; sales staff see only self; loaded from adminUserApi.getUsers or self
  - Score grid: One row per staff member; 9 score columns (max displayed in parentheses):
  -   - Attendance (max 10)
  -   - Conversion (max 20, auto-filled for today from /walkouts/conversion-feed, shows 'AUTO' badge when today, past dates require explicit number)
  -   - Task (max 10)
  -   - Visufit (max 10)
  -   - Punctuality (max 10)
  -   - Behaviour (max 10)
  -   - Kicker 1 (max 10)
  -   - Kicker 2 (max 10)
  -   - Reviews (max 10)
  - Computed columns (client-side, re-computed on server save): Total (sum of 9), Eligibility band chip (colored by band value)
  - Visufit usage % display (MTD, if available from saved row)
  - Per-row actions: Delete row (if already saved; skip new rows), Refresh conversion auto-fill (if today + conversionAuto=true)
  - Page actions: Save all button (saves all unsaved rows), Delete existing row modal (confirm, then delete + reload)
  - Footfall warning banner (N3): Shows if today's footfall capture is incomplete (walkinsStatus call to walkoutsApi); nudges to complete footfall before auto-fill works
  - Warnings/validation (client-side): Error message if save fails, success toast on save, info toast if nothing to save
  - RBAC: canEditAny = user has ELEVATED_ROLES (SUPERADMIN|ADMIN|AREA_MANAGER|STORE_MANAGER|ACCOUNTANT); non-elevated roles see only self
  - Settings link: Nav to /incentive/settings
  - Links: Leaderboard (/incentive/leaderboard), Payout (/incentive/payout), Payouts history (/incentive/payouts)
  - Business rules: Conversion cell auto-filled only for today (isToday=true, conversionAuto=true); past dates require explicit entry; each score clamped to its max; server re-computes total + eligibility on save; non-elevated roles can only save their own scores; bands server-defined from settings

### MTD Leaderboard  ·  `/incentive/leaderboard`
- **Design file:** none (no design yet)  ·  **Status:** Pending
- **Backend:** `points.py (GET /incentive/leaderboard)`
- **MUST-PRESERVE features (10):**
  - Header: Back link to /incentive (Daily scorecard), Title (Trophy icon), subtitle (period description), days-window select (7/30/90 default 30), Refresh button
  - Scope toggle (managers only): Store | Area | Org buttons; canWidenScope = SUPERADMIN|ADMIN|AREA_MANAGER; non-managers locked to store view
  - Podium display (top 3): Ranked #1, #2, #3 cards with staff name, avg total, tier (PODIUM), badges (eligibility_100, logged_every_day, top_riser, consistent_90), tier styling (border + text color per tier)
  - Full leaderboard table: Sorted by avg_total DESC, tie-broken by days_logged DESC
  - Columns: Rank, Staff name, Days logged, 9 category averages (abbrev: Att, Conv, Task, Visu, Punct, Behav, K1, K2, Rev), Avg total (bg-gray-100), Eligibility band, Tier chip, Rank delta arrow (↑/↓/—)
  - Per-staff row: Clickable to /incentive/staff/{staffId} (PointsHistoryPage)
  - Tier chips: PODIUM (border-gray-900 text-gray-900), CONTENDER (border-gray-400 text-gray-600), BUILDING (border-gray-200 text-gray-400)
  - Badge labels: eligibility_100 → 'Full eligibility', logged_every_day → 'Logged every day', top_riser → 'Top riser', consistent_90 → '90+ average'
  - Rank delta: compared to prior period (if data available)
  - Business rules: Earnings/rupee fields stripped for junior roles server-side; leaderboard API returns MTDStaffEntry[] with ranks, tiers, badges; days window (7/30/90) changes aggregation period

### Points History  ·  `/incentive/staff/{staffId}`
- **Design file:** none (no design yet)  ·  **Status:** Pending
- **Backend:** `points.py (GET /incentive/staff/{staffId}/history)`
- **MUST-PRESERVE features (7):**
  - Header: Back link to /incentive/leaderboard, Title (History icon + staff name or staffId), Staff ID display (monospace, xs text)
  - Date range filter: From (date input, default month-start), To (date input, default today), Refresh button
  - Per-day history table: Sorted by date DESC
  - Columns: Date, 9 score columns (Attendance, Conversion, Task, Visufit, Punctuality, Behaviour, Kicker 1, Kicker 2, Reviews), Total (bg-gray-100), Eligibility (band chip, bg-gray-100), Visufit gate status
  - Empty state: 'No records for this period'
  - Loading state: Loader2 spinner
  - Business rules: Date range inclusive; staffId required to load; staffName loaded from first item in response if available

### Payout Dashboard  ·  `/incentive/payout`
- **Design file:** none (no design yet)  ·  **Status:** Pending
- **Backend:** `payouts.py (GET /incentive/payouts/preview, POST /incentive/payouts/lock, POST /incentive/payouts/{snapshot_id}/mark-paid, GET /incentive/payouts/{snapshot_id}/csv)`
- **MUST-PRESERVE features (13):**
  - Header: Back link to /incentive (Daily scorecard), Title (Calculator icon), Subtitle (Pool sizing • per-staff allocation • manager bonus + status chip if snapshot exists)
  - Period picker: Year input, Month input, updated via /incentive/payouts/preview API call
  - Links: All snapshots (/incentive/payouts), Settings (/incentive/settings)
  - Refresh button, CSV export button (snapshot only, disabled on preview)
  - SUPERADMIN only: Lock month button (immutable, confirm modal), Mark paid button (with audit note prompt)
  - Snapshot status display: PREVIEW | LOCKED | PAID (color-coded chips: amber for preview, blue for locked, green for paid)
  - Inputs section (editable inline, preview-sourced): Last-year sale (₹ input), This-year sale (₹ input), Avg discount % (% input), Visufit usage % (% input); re-computes preview as user types
  - Pool sizing section: Best level (L1|L2|L3 text), Multiplier (× display), Pool amount (₹ computed)
  - Targets table: Achieved vs missed rows
  - Per-staff payouts grid: Staff name, eligible amount, level, payout (₹)
  - Manager bonuses section: Supervisor names + amounts
  - Grand totals row: Team pool, Manager bonus pool, Total payout
  - Business rules: SUPERADMIN sees Lock + Mark-Paid; other roles see read-only banner; inputs editable only in preview (not locked snapshot); Lock button disabled if pool=₹0; Lock is immutable; Mark paid records audit note; CSV export available only for locked/paid snapshots; year/month selector triggers preview re-run

### Payout Snapshots  ·  `/incentive/payouts`
- **Design file:** none (no design yet)  ·  **Status:** Pending
- **Backend:** `payouts.py (GET /incentive/payouts)`
- **MUST-PRESERVE features (6):**
  - Header: Title, optional year filter (select dropdown)
  - Snapshot history list/table: All locked payouts per user's store
  - Columns: Month (YYYY-MM), Status (PREVIEW|LOCKED|PAID chip), Pool amount (₹), Created date, Created by (staff name)
  - Per-row click: Nav to /incentive/payout?snapshot_id={snapshot_id} (or route param) to view snapshot in read-only mode
  - Pagination: If large dataset, implement list pagination
  - Business rules: Display only locked/paid snapshots (exclude previews); sort by month DESC; store-scoped visibility

### Incentive Settings  ·  `/incentive/settings`
- **Design file:** none (no design yet)  ·  **Status:** Pending
- **Backend:** `points.py (PATCH /incentive/points/settings/eligibility, /settings/payout, /settings/visufit-gate, POST /incentive/points/inputs/last-year-sale)`
- **MUST-PRESERVE features (12):**
  - SUPERADMIN only: Read-only banner + alert for non-SUPERADMIN users
  - Back link to /incentive (Daily scorecard)
  - Each setting section is a SectionCard with: Title, optional description, children (form fields), independent Save button, dirty flag, saving indicator
  - SECTION 1 - ELIGIBILITY BANDS: Table (min threshold, max threshold, payout value per point); Add row button, Delete row buttons per row; all editable text inputs
  - SECTION 2 - STAFF WEIGHTAGES: Editable per-category weights (9 fields corresponding to 9 score categories)
  - SECTION 3 - GROWTH FACTORS: Per-level (L1/L2/L3) growth % inputs; Editable text fields, parsed on save
  - SECTION 4 - PAYOUT RATES: Per-level (L1/L2/L3) ₹/point rates; Editable text fields
  - SECTION 5 - MULTIPLIER CURVE: Table (max_pct, multiplier); Add row button, Delete row buttons; Editable text inputs
  - SECTION 6 - VISUFIT GATE: Toggle (enabled/disabled), Threshold % input (when enabled); Section warning if threshold is very low
  - SECTION 7 - SUPERVISOR BONUSES: Table (supervisor user, bonus amount, tier); Dropdown for supervisor selection (staff list), Add row button, Delete row buttons, Editable amount inputs
  - SECTION 8 - LAST-YEAR-SALE OVERRIDES: Form (year input, month input, amount input); Save button; Per-month override storage
  - Business rules: Each section has independent Save button (PATCHes only its own slice, exclude_unset semantics); partial failures stay isolated; dirty flag per section; saving state per section; inputs are strings (user can type freely), parsed on save; NaN values handled gracefully; Staff list loaded from adminUserApi; Year/month default to today

## Clinical / Optometry + Workshop

_Inventory of all screens in the Clinical/Optometry module (patient queue, eye tests, prescriptions, family Rx, contact lens fitting) and Workshop module (job pipeline, QC, thermal labels, station monitoring). All screens cross-mapped to backend routers and the 11 design prototypes. Clinical module maps to clinical.html design; Workshop has no design yet._


### Clinical: Patient Queue & Eye Test Management  ·  `/clinical`
- **Design file:** clinical.html  ·  **Status:** Done
- **Frontend:** `ClinicalPage.tsx (main queue screen)`, `EyeTestForm.tsx (refraction + SOAP + clinical findings)`, `PatientIntakeModal.tsx (token-first intake)`, `QueueExistingCustomerModal.tsx (search + add to queue)`, `ClinicPrescriptionHistory.tsx (per-customer Rx viewer + editor)`, `AbuseDetection.tsx (fraud-control dashboard)`, `ConversionTab.tsx (optometrist -> retail conversion)`, `SendToFloorDrawer.tsx (F50 handoff)`
- **Backend:** `clinical.py: GET /clinical/queue, POST /clinical/queue, GET /clinical/tests, POST /clinical/start-test, POST /clinical/complete-test, GET /clinical/soap-note/{test_id}, POST /clinical/mark-redo, GET /clinical/abuse-detection, GET /clinical/conversion-dashboard`
- **Notes:** All data from API; no mock. Patient-vs-customer split surfaced when they differ (dependent on parent account). Rx validation enforced server-side per #759. Contact lenses exempt from hard Rx-required gate; spectacles require valid, non-expired, customer-matching prescription.
- **MUST-PRESERVE features (14):**
  - Queue view: token number (mono), patient name, phone, age, reason, status badge (Waiting/In Progress/Completed)
  - Status filter: Waiting, In Progress, Completed
  - Wait time display: mins elapsed, red when >10m late
  - 3-card stat strip: Waiting count, In exam count, Completed today count
  - Tabs: Queue, Completed today, Abuse alerts (SUPERADMIN+ only), Conversion (OPTOMETRIST+ only)
  - Actions per queue row: Print token, Rx history (modal), Start test (WAITING status), Continue (IN_PROGRESS status)
  - New patient button: Opens search-existing-customer modal first, fallback to create-new (PatientIntakeModal)
  - Queue existing customer flow: search by name/phone, pick patient, add to queue
  - Eye test form modal: Refraction fields (SPH/CYL/AXIS/ADD/PD per eye), VA per eye, Final Rx (lens type, next checkup), SOAP note (optional multi-field), clinical findings (IOP, diagnosis, colour vision, cover test, dominant eye), notes
  - Refraction validation: SPH -20 to +20 (0.25 step), CYL -6 to +6 (0.25), AXIS 1-180 whole, ADD +0.75 to +3.50 (0.25)
  - Eye test save: auto-creates prescription doc with all captured fields
  - Completed tests tab: patient cards with R/L power preview, Rx history button, Print Rx (A5) button, Send to Floor button (F50)
  - Send to Floor drawer (F50): clinical -> retail handover, idempotent 'Sent' button flip
  - Prescription history modal (per customer): grouped by family member, Edit last Rx, New Rx, Print Rx (A5), per-member toggle

### Clinical: Prescriptions Library  ·  `/prescriptions`
- **Design file:** clinical.html  ·  **Status:** Done
- **Frontend:** `PrescriptionsPage.tsx`, `PrescriptionPrint.tsx (modal + HTML render)`
- **Backend:** `prescriptions.py: GET /prescriptions/list (date-windowed, store-scoped), GET /prescriptions/{id}/print-html, POST /prescriptions/{id}/redo`
- **Notes:** Rx library list across all dates (not just today's tests). Normalises various field shapes from backend (patient_id/customer_id missing from auto-created Rx). Print validates rxId present before calling API.
- **MUST-PRESERVE features (9):**
  - Search box: by patient name or phone, live datalist suggestions
  - Date filter dropdown: Today, This Week, This Month, All Time
  - Prescriptions grid (3-col tablet, 2-col mobile): card per Rx
  - Prescription card: patient avatar, name, phone, date, R/L power grid (SPH/CYL), PD
  - Card actions: View Details (modal), Print (A5 card), Mark redo (with reason prompt)
  - Detail modal: patient info, full Rx table (Eye / SPH / CYL / AXIS / ADD columns), PD, notes, Print Rx (A5) + Mark redo buttons
  - Print Rx (A5): server-rendered HTML via authenticated fetch, opens in new window for browser print
  - Mark redo: required free-text reason, endpoint gates to OPTOMETRIST+ roles
  - Empty states: per date range, searchable prompt

### Clinical: Test History  ·  `/clinical/history`
- **Design file:** clinical.html  ·  **Status:** Done
- **Frontend:** `TestHistoryPage.tsx`
- **Backend:** `clinical.py: GET /clinical/tests (date-windowed), GET /clinical/soap-note/{test_id}`
- **Notes:** Replaces old 'today only' hack. Server applies date window. SOAP note load-on-demand so quick test listing isn't blocked.
- **MUST-PRESERVE features (6):**
  - Search box: by patient name or phone, live datalist suggestions
  - Date filter: Today, This Week, This Month, All Time (server-side date window)
  - Test cards grid: patient avatar, name, phone, date+time, R/L power (SPH/CYL), links
  - Actions: Print (A5 Rx card if prescription_id linked), View (detail modal)
  - Detail modal: full test record, SOAP note (load on-demand), refraction table
  - SOAP note display (CLI-11): optional multi-field structured note (history, findings, diagnosis, plan, etc.), load-on-demand with spinner

### Clinical: Family Prescriptions  ·  `/clinical/family-rx`
- **Design file:** clinical.html  ·  **Status:** Done
- **Frontend:** `FamilyRxPage.tsx`
- **Backend:** `sales.py: GET /prescriptions/family/{customer_id}`
- **Notes:** Read-only presentation of family Rx lifecycle. Normalises snake_case/camelCase variations from backend docs. Defaults to SPECTACLE if rx_kind absent.
- **MUST-PRESERVE features (7):**
  - Customer search (AutoSearch): find by name/phone
  - Family member cards: per patient on the account, grouped
  - Relation badge: family member type (spouse, child, etc.), or 'Unlinked' if no relation
  - Per-member Rx table: test date, R/L power (SPH/CYL × AXIS), ADD, expiry, validity badge (Valid/Expired/Unknown)
  - Row colour-coding: green (valid), red (expired), gray (unknown)
  - Empty family: placeholder when no Rx exist
  - Validity calculation: based on test_date + validity_months or explicit expiry_date

### Clinical: Contact Lens Fitting  ·  `/clinical/contact-lens`
- **Design file:** clinical.html  ·  **Status:** Done
- **Frontend:** `ContactLensFittingPage.tsx`
- **Backend:** `prescriptions.py: POST /prescriptions (rx_kind=CONTACT_LENS, CLEyeData schema), GET /prescriptions/{customer_id} (filter rx_kind)`
- **Notes:** C6-A: distinct from spectacle Rx. Backend models CL range validation (cl_power, cl_cyl, cl_axis, base_curve, diameter). Print card differs from spectacle card layout.
- **MUST-PRESERVE features (8):**
  - Customer search: by name/phone, auto-complete
  - Patient picker: when customer has multiple dependents
  - Right & Left eye form grids: CL power, CYL, AXIS, ADD, base curve (BC), diameter (DIA), acuity
  - Fit metadata: brand (e.g. Bausch Lomb), series, modality (Daily/Fortnightly/Monthly/Quarterly/Yearly/Color), colour, validity (months), remarks
  - Validation: at least one eye must have data; only finite numbers accepted
  - Submit: creates CONTACT_LENS rx doc with CLEyeData payload, auto-determines patient_id
  - Fittings list: grid of prior fittings for the selected customer, filterable by rx_kind=CONTACT_LENS
  - Print CL card: server-rendered HTML per prescription_id

### Workshop: Job Pipeline & QC  ·  `/workshop`
- **Design file:** none (no design yet)  ·  **Status:** Pending
- **Frontend:** `WorkshopPage.tsx (main job list + create modal)`, `ScanToAdvance.tsx (keyboard wedge)`, `StageMonitorBoard.tsx (jobs-by-status grid)`, `StationQueueBoard.tsx (per-station queues)`, `LabelPreviewModal.tsx (thermal label preview)`, `WorkshopJobCardPrint.tsx (print template)`, `QcChecklistModal.tsx (structured QC form)`
- **Backend:** `workshop.py: GET /workshop/jobs, POST /workshop/jobs (with F9 DC hardlock check + override), PATCH /workshop/jobs/{id}/status, POST /workshop/jobs/{id}/qc, POST /workshop/jobs/{id}/qc-checklist, POST /workshop/jobs/{id}/rework, PATCH /workshop/jobs/{id}/lens-status, POST /workshop/jobs/{id}/notify-ready, GET /workshop/dashboard-kpis, GET /workshop/remake-reason-codes, GET /workshop/scan-advance (F2)`
- **Notes:** All data from API. F9 DC hardlock: ORDERED lenses require accepted DELIVERY_CHALLAN GRN at store (enforced server-side, ADMIN+ can override with reason). Lens lifecycle (NOT_ORDERED -> ORDERED -> RECEIVED -> MOUNTED) is forward-only and independent of job status. Auto-print stage sticker honours printer settings (auto_print_stage_sticker). Thermal labels via QZ (Zebra printer) with HTML fallback. Rework costs the spoiled lens (audit logged). Notify ready sends WhatsApp (fail-soft). Job status machine: PENDING -> IN_PROGRESS -> COMPLETED -> (QC_FAILED ← COMPLETED, rework resets to PENDING) / READY -> DELIVERED. All QC + rework + notify actions gated to WORKSHOP_STAFF + manager roles. F13 rework justification required (reason code dropdown).
- **MUST-PRESERVE features (19):**
  - 4-card KPI strip: Active jobs, Urgent count (+ QC rework sub-count), Ready for pickup (+ avg turnaround days), Overdue
  - KPIs via GET /workshop/dashboard-kpis (server-side counts + avg turnaround when ≥5 samples)
  - Scan-to-advance keyboard wedge: scan job number/ID barcode, advance stage, auto-print stage sticker (fail-soft)
  - Station queue board (F2): per-station queues with SLA-aged dwell chips, edit SLA inline (managers only)
  - Stage monitor board: jobs-by-status grid, live visibility, print stage sticker action
  - Search box: by job number, customer name, order number
  - Status filter: Active jobs (default), All status, per-status option
  - Priority filter: All (default), Urgent, Express, Normal
  - Job card per row: job number, status badge, priority badge (icon + label), lens status badge, overdue badge (red), customer name + phone, frame + lens type, promise date (red if overdue), Assign, progress bar
  - Create job modal: search order, select order (shows order details + items), priority select (3-button toggle), expected delivery date (date picker), fitting instructions (textarea), notes (textarea)
  - F9 DC hardlock banner: blocks ORDERED-lens jobs without Delivery Challan (gated to ADMIN+ override with reason)
  - Job detail modal: status + priority + lens-status badges, customer block, order/frame/lens/barcode fields, dates (created/promised/completed), notes, vendor capture block (lab order ID + tracking), progress bar, lens-order lifecycle (NOT_ORDERED -> ORDERED -> RECEIVED -> MOUNTED, forward-only), status-transition buttons (Start → IN_PROGRESS, Mark Completed, Run QC, Send for rework, Mark Delivered), lens-lifecycle buttons (Mark ordered/received/mounted), notify ready button, thermal label actions (traveler/stage/ready), print job card
  - QC checklist modal: structured pass/fail per-item (power, fit, surface, coating, etc.), per-item notes, overall pass/fail toggle, summary notes, submit → READY or QC_FAILED
  - Rework modal: remake reason code dropdown (AXIS_ERROR, POWER_ERROR, FITTING_ERROR, SURFACE_DEFECT, COATING_DEFECT, BREAKAGE_IN_LAB, WRONG_LENS_PICKED, CUSTOMER_CHANGED_RX, OTHER), optional notes, confirm sends job back for rework + costs spoiled lens
  - Lens-order lifecycle: independent of job workflow, forward-only, timestamp each transition
  - Notify ready: WhatsApp dispatch (with DISPATCH_MODE gate: off/test/live), fallback to phone unavailable or dispatch failed warnings
  - Thermal label preview modal: traveler/stage/ready/product label types, preview + QZ print (silent fail-soft, HTML fallback)
  - Auto-print on status change: stage sticker (configurable via printer settings), pickup label on READY
  - Job print card: job number, order number, customer, frame brand/model/color, lens type, priority, due date, assigned tech, status, created date, store info

## Inventory + Catalog + Transfers + Barcode/Labels

_Comprehensive design-parity inventory for the Inventory + Catalog + Transfers + Barcode/Labels module of IMS 2.0. Total of 20 screens covering: (1) Stock Ledger with 17 tabs (alerts, catalog, display-layout, low-stock, reorders, serial-numbers, aging, transfers, movements, non-moving, stock-count, contact-lens, power-grid, sell-through, overstock, rebalance, quarantine); (2) Stock Transfers with 3-step workflow (Details → Items → Review), ship/receive/complete states, damaged unit rehoming as QUARANTINED, inter-state GST handling; (3) Catalog Product Add in 3 modes (Quick Add single-screen, Guided 6-step wizard, Bulk rapid-grid); (4) Pricing & Offers with dry-run preview, cap enforcement (category 15-20%, brand 2-5%, MRP>offer validation), and store-scoped bulk operations; (5) Buy Desk, Autopilot, Replenishment, Audit, Opening Stock Import, Online Stock Sync (Shopify), Power Grid (CL matrix), Contact Lens FEFO, Stock Aging, Serial Numbers, Non-Moving Stock, Sell-Through/Overstock analysis, Display Fixtures. All features grounded in real React/TypeScript page components, FastAPI routers with documented field validations (quantity ge=1 le=10000, unit_cost ge=0, damaged<=received, etc.), and business rules (FEFO sort, QUARANTINED vs AVAILABLE states, audit trails, role-based access). Design files mapped: inventory.html (stock ledger, transfers, catalog, stock count, quarantine, power grid), pricing.html (pricing offers). Status: all screens are Done (implemented, not pending design)._


### Inventory List/Stock Ledger  ·  `/inventory (tab: catalog)`
- **Design file:** inventory.html  ·  **Status:** Done
- **Frontend:** `frontend/src/pages/inventory/InventoryPage.tsx`, `frontend/src/components/inventory/StockTransferModal.tsx`, `frontend/src/components/inventory/StockTransferManagement.tsx`, `frontend/src/components/inventory/BarcodeManagementModal.tsx`, `frontend/src/components/inventory/ReorderDashboard.tsx`, `frontend/src/components/inventory/SerialNumberTracker.tsx`, `frontend/src/components/inventory/StockAgingReport.tsx`, `frontend/src/components/inventory/StockAlertsOverview.tsx`, `frontend/src/components/inventory/NonMovingStockWidget.tsx`, `frontend/src/components/inventory/StockCountScanningInterface.tsx`, `frontend/src/components/inventory/AdvancedInventoryFeatures.tsx`, `frontend/src/components/inventory/QuarantineQueue.tsx`, `frontend/src/components/inventory/DisplayLayoutPanel.tsx`
- **Backend:** `backend/api/routers/inventory.py::get_stock`, `backend/api/routers/inventory.py::get_low_stock_alerts`, `backend/api/routers/inventory.py::get_stock_by_barcode`, `backend/api/routers/inventory.py::add_stock`, `backend/api/routers/inventory.py::get_expiring_stock`, `backend/api/routers/inventory.py::get_stock_aging_report`, `backend/api/routers/inventory.py::list_stock_counts`, `backend/api/routers/inventory.py::get_serials`, `backend/api/routers/inventory.py::get_non_moving_stock`, `backend/api/routers/inventory.py::get_contact_lenses`, `backend/api/routers/inventory.py::get_transfer_recommendations`, `backend/api/routers/inventory.py::get_cross_store_stock`, `backend/api/routers/inventory.py::quarantine_stock`, `backend/api/routers/inventory.py::lift_quarantine`, `backend/api/routers/inventory.py::get_quarantined_stock`
- **MUST-PRESERVE features (17):**
  - Stock ledger table with columns: Product, SKU, Barcode, Category, MRP, Offer Price, In-Store qty, Zone, Online status, Location, Status badge, Actions
  - Multi-tab navigation: alerts, catalog (stock ledger), display-layout, low-stock, reorders, serial-numbers, aging, transfers, movements, non-moving, stock-count, contact-lens, power-grid, sell-through, overstock, rebalance, quarantine
  - Search field (product/SKU/barcode autocomplete)
  - Category filter (multi-select chips: Frames, Sunglasses, Reading Glasses, Optical Lenses, Contact Lenses, Watches, Smartwatches, Accessories, Wall Clocks, Hearing Aids)
  - Store filter (dropdown, scoped by user's storeIds)
  - Availability filter toggle (all/online/offline)
  - Pagination (50 items per page, prev/next + current page display)
  - Row actions: View Details (eye icon, opens read-only drawer), Transfer, Barcode Management, Quarantine, Unlabeled label count badge (F21)
  - Add Product button (nav to /catalog/add)
  - Add Stock button (opens StockAddRequest form: product_id, quantity 1-10000, location_code, batch_code/lot, expiry_date)
  - Export CSV button (client-side CSV download of visible rows with proper CSV escaping)
  - CSV Import button (file upload + preview of first 10 rows, bulk-create endpoint validation)
  - Refresh button (re-fetch data)
  - Status badge: Out of Stock (red), Low Stock (yellow, based on reorder_level), In Stock (green)
  - Zone column shows DisplayFixture + DisplayPlacement via deep-link (?fixture={fixture_id})
  - Online status indicator for each SKU (Shopify/ecommerce sync status)
  - Business rules: on-hand computed from stock_units where status is ON_HAND (excludes QUARANTINED, UNDER_AUDIT, BLIND_COUNT, TRANSFERRED, SOLD, VOID, DAMAGED, RTV); reserved qty tracked separately

### Stock Alerts  ·  `/inventory (tab: alerts)`
- **Design file:** inventory.html  ·  **Status:** Done
- **Frontend:** `frontend/src/components/inventory/StockAlertsOverview.tsx`
- **Backend:** `backend/api/routers/inventory.py::get_alerts`
- **MUST-PRESERVE features (3):**
  - Alert cards for: expiring stock (CL batches with days_until_expiry < 30), low stock (qty <= reorder_level), pending stock counts (count_id status PENDING), overstock situations
  - Each alert is a clickable card with: title, count, action button (view, count, reorder)
  - Color-coded: red for critical (expiring <7 days), orange for warning (expiring <30 days), yellow for info

### Low Stock / Reorder Dashboard  ·  `/inventory (tabs: low-stock, reorders)`
- **Design file:** inventory.html  ·  **Status:** Done
- **Frontend:** `frontend/src/components/inventory/ReorderDashboard.tsx`
- **Backend:** `backend/api/routers/inventory.py::get_low_stock_alerts`, `backend/api/routers/inventory.py::get_transfer_recommendations`
- **MUST-PRESERVE features (6):**
  - Table: Product, SKU, Current Stock, Reorder Level, Recommended Qty, Status, Action
  - Reorder-level configurable per product (default 5)
  - Recommended qty = reorder_level * 1.5 (auto-calc for PO draft)
  - One-click action: Draft PO button (nav to /catalog/buy-desk with product pre-filled)
  - Filter by category, sort by urgency (most below reorder level first)
  - Transfer recommendations widget showing which products should move between stores based on stock imbalance

### Contact Lens FEFO (First Expiry First Out)  ·  `/inventory (tabs: contact-lens, power-grid)`
- **Design file:** inventory.html  ·  **Status:** Done
- **Frontend:** `frontend/src/components/inventory/ContactLensInventoryWidget.tsx`, `frontend/src/components/inventory/ContactLensExpiryWidget.tsx`, `frontend/src/components/inventory/LensPowerGridWidget.tsx`
- **Backend:** `backend/api/routers/inventory.py::get_contact_lenses`, `backend/api/routers/inventory.py::get_contact_lenses_expiry_status`, `backend/api/routers/inventory.py::get_power_grid`
- **MUST-PRESERVE features (6):**
  - Contact Lens Inventory Widget: list all CL SKUs with batch/lot tracking, expiry dates, qty on hand
  - Contact Lens Expiry Widget: FEFO-sorted view (sort_key = min(expiry_date) per SKU), visual aging buckets (fresh <30d, aging 30-60d, critical 60+d)
  - Power Grid Widget: matrix view of power combinations (SPH x CYL x ADD) for each CL product, qty-filled cells, quick reorder from grid
  - FEFO sort function: compute_days_until_expiry() for each batch_code, sort ascending (soonest-expiring first for dispense)
  - One-click dispense: select qty + batch (FEFO pre-filled), confirm to move stock to SOLD
  - Expiry validation: warn if any batch_code is >90 days from expiry, block if 0 days (expired)

### Stock Transfers (Ship/Receive/Quarantine)  ·  `/inventory (tab: transfers) OR /inventory/transfers (dedicated page)`
- **Design file:** inventory.html  ·  **Status:** Done
- **Frontend:** `frontend/src/components/inventory/StockTransferModal.tsx`, `frontend/src/components/inventory/StockTransferManagement.tsx`
- **Backend:** `backend/api/routers/transfers.py::list_transfers`, `backend/api/routers/transfers.py::get_pending_transfers`, `backend/api/routers/transfers.py::get_transfer`, `backend/api/routers/transfers.py::create_transfer`, `backend/api/routers/transfers.py::update_transfer`, `backend/api/routers/transfers.py::approve_transfer`, `backend/api/routers/transfers.py::start_picking`, `backend/api/routers/transfers.py::complete_picking`, `backend/api/routers/transfers.py::ship_transfer`, `backend/api/routers/transfers.py::receive_transfer`, `backend/api/routers/transfers.py::complete_transfer`, `backend/api/routers/transfers.py::cancel_transfer`, `backend/api/routers/transfers.py::bulk_approve_transfers`
- **MUST-PRESERVE features (17):**
  - Transfer workflow: DRAFT → APPROVED → PICKING → PACKED → IN_TRANSIT → PARTIALLY_RECEIVED / RECEIVED → COMPLETED (or CANCELLED)
  - Create Transfer Modal (3 steps):
  -   Step 1 - Transfer Details: From Store (auto-current), To Store (dropdown), Transfer Type (store-to-store/warehouse-to-store/return-to-vendor), Priority (low/normal/high/urgent), Expected Date (date input), Notes (textarea), Shipping Cost (₹, non-negative), Shipping Method (dropdown), Create Shiprocket Shipment (toggle)
  -   Step 2 - Items: Product Search (autocomplete), Add Item button, Items list with: Product Name, SKU, Qty Requested (ge=1), Unit Cost (ge=0), Notes, Damage Notes, Remove button
  -   Step 3 - Review: Summary table (Product, Qty, Cost, Total), Grand Total calc, Send button (creates transfer with status=DRAFT), Cancel
  - Transfer Management Table: Transfer #, From → To, Status (badge: color-coded), Item Count, Total Value, Expected Date, Created Date, Actions (View, Approve [if DRAFT], Pick [if APPROVED], Ship [if PACKED], Receive [if IN_TRANSIT], Complete [if RECEIVED], Cancel [if <COMPLETED])
  - Approve Transfer: bulk action on pending transfers, sets status=APPROVED
  - Pick Transfer: set status=PICKING, commit stock to TRANSFERRED state (not sellable until shipped)
  - Ship Transfer: PATCH /transfers/{id}/ship, set status=IN_TRANSIT, generate optional Shiprocket shipment (if create_shiprocket_shipment=true), move stock from TRANSFERRED → SHIPPED
  - Receive Transfer: PATCH /transfers/{id}/receive, accept items with TransferItemReceive (transfer_item_id, quantity_received ge=0, quantity_damaged ge=0 and <=quantity_received), damaged units → QUARANTINED state, received units → AVAILABLE, creates follow-up task if damage_count > 0
  - Quantity cap: quantity_received <= quantity_shipped (enforce in endpoint)
  - Damaged units re-homed as QUARANTINED (not AVAILABLE/sellable)
  - Complete Transfer: set status=COMPLETED after all items received/acknowledged
  - Cancel Transfer: revert TRANSFERRED stock back to AVAILABLE, set status=CANCELLED
  - Inter-state transfers: compute IGST vs CGST+SGST via entity/GSTIN for mirror purchase booking in Tally (backend books tax-safe JV)
  - Status history: append-only audit trail per transfer doc
  - Business rules: quantity_requested ge=1 (0 or negative lines corrupt ship/receive math), unit_cost ge=0, damaged_qty<=received_qty, received_qty<=shipped_qty

### Stock Quarantine Queue  ·  `/inventory (tab: quarantine)`
- **Design file:** inventory.html  ·  **Status:** Done
- **Frontend:** `frontend/src/components/inventory/QuarantineQueue.tsx`
- **Backend:** `backend/api/routers/inventory.py::quarantine_stock`, `backend/api/routers/inventory.py::lift_quarantine`, `backend/api/routers/inventory.py::get_quarantined_stock`
- **MUST-PRESERVE features (9):**
  - Quarantine Queue table: Product, Barcode, Reason, Notes, Qty, Days in Quarantine, Print Label Status, Actions
  - Badge: count of unlabeled QUARANTINED units (F21 — drives the Quarantine tab badge on InventoryPage)
  - Quarantine reasons: damaged (received/workshop), scratched, customer return, QC failed, other (custom notes)
  - Quarantine flow: PATCH /stock/{stock_id}/quarantine with { reason, notes, rtv_vendor_id }, sets unit.status=QUARANTINED
  - Lift Quarantine: PATCH /stock/{stock_id}/lift-quarantine with { lift_reason >=5 chars }, justification required (audit trail), sets unit.status=AVAILABLE
  - Print Red Label action: print barcode label for visual quarantine marker (integrates with print.html templates)
  - Mark Labeled toggle: toggle unlabeled_count on a QUARANTINED unit (visual confirmation printed)
  - Return to Vendor (RTV): if quarantine.rtv_vendor_id set, escalate to VendorRMA workflow
  - Business rule: quarantine is NOT a disposal — units can be lifted back to AVAILABLE or escalated to RTV/scrap workflows

### Stock Count Scanning Interface  ·  `/inventory (tab: stock-count)`
- **Design file:** inventory.html  ·  **Status:** Done
- **Frontend:** `frontend/src/components/inventory/StockCountScanningInterface.tsx`
- **Backend:** `backend/api/routers/inventory.py::list_stock_counts`, `backend/api/routers/inventory.py::start_stock_count`, `backend/api/routers/inventory.py::record_count_item`, `backend/api/routers/inventory.py::complete_stock_count`, `backend/api/routers/inventory.py::get_stock_count`, `backend/api/routers/inventory.py::reconcile_stock_count`
- **MUST-PRESERVE features (7):**
  - Start Stock Count: POST /inventory/stock-count/start with { category, zone, notes }, creates a COUNT with status=PENDING
  - Count item: POST /inventory/stock-count/{count_id}/items with StockCountItem { product_id, counted_quantity ge=0, notes }, accumulates counted qty
  - Complete Count: POST /inventory/stock-count/{count_id}/complete, set status=COMPLETED, compute variance (counted - on_hand), flag discrepancies
  - Reconcile Count: POST /inventory/stock-count/{count_id}/reconcile, apply counted qty as new source-of-truth on_hand, set status=RECONCILED
  - UI: barcode scanner input (real-time qty entry), category/zone pre-filter for count scope, autocomplete product search, live tally (counted vs expected), action to complete
  - Variance report: show SKUs with counted!=on_hand, color-coded (green no variance, yellow <10%, red >=10%)
  - Audit trail: preserve original on_hand before reconcile (immutable history per COUNT doc)

### Stock Aging Report  ·  `/inventory (tab: aging)`
- **Design file:** inventory.html  ·  **Status:** Done
- **Frontend:** `frontend/src/components/inventory/StockAgingReport.tsx`
- **Backend:** `backend/api/routers/inventory.py::get_stock_aging_report`
- **MUST-PRESERVE features (6):**
  - Aging buckets: 0-30 days, 30-60 days, 60-90 days, 90-180 days, 180+ days
  - Table: Product, Barcode, Received Date, Days on Hand, Qty, Total Value (mrp*qty), Bucket, Status
  - Color-coded: green 0-30d, yellow 30-90d, orange 90-180d, red 180+d
  - Sort by days_on_hand descending (oldest first)
  - Action: transfer to slow-moving pool or quarantine decision
  - Compute: created_at (when unit was added to stock) vs now() = days_on_hand

### Serial Number Tracker  ·  `/inventory (tab: serial-numbers)`
- **Design file:** inventory.html  ·  **Status:** Done
- **Frontend:** `frontend/src/components/inventory/SerialNumberTracker.tsx`
- **Backend:** `backend/api/routers/inventory.py::get_serials`, `backend/api/routers/inventory.py::post_serials`, `backend/api/routers/inventory.py::patch_serials`
- **MUST-PRESERVE features (6):**
  - Serial tracking for high-value items (watches, smartwatches, hearing aids, etc.)
  - Table: Product, Serial Number, Status (AVAILABLE/SOLD/RESERVED/DAMAGED/VOID), Assigned To, Date Assigned, Notes
  - Add Serial: input serial_number, product_id, auto-generate barcode
  - Edit Serial: PATCH /serials/{serial_id} with { status, assigned_to, notes }
  - Link to order/sale: show order_id + customer when status=SOLD
  - Warranty tracking: optional warranty_expiry date per serial (linked to product warranty policy)

### Non-Moving Stock Analysis  ·  `/inventory (tab: non-moving)`
- **Design file:** inventory.html  ·  **Status:** Done
- **Frontend:** `frontend/src/components/inventory/NonMovingStockWidget.tsx`
- **Backend:** `backend/api/routers/inventory.py::get_non_moving_stock`
- **MUST-PRESERVE features (7):**
  - Report on SKUs with zero sales in last N days (default 90 days)
  - Table: Product, SKU, Category, Brand, On Hand, MRP, Offer, Last Sold, Days Since Last Sale, Never Sold badge
  - Filter by category, brand, days threshold (default 90)
  - Sort by days descending (most stale first)
  - Color-coded: yellow >=120d, orange >=180d, red >=365d, critical never sold
  - Action: mark for donation/disposal, transfer to another store, or re-price
  - Export CSV for merchandising review

### Sell-Through & Overstock Analysis  ·  `/inventory (tabs: sell-through, overstock)`
- **Design file:** inventory.html  ·  **Status:** Done
- **Frontend:** `frontend/src/components/inventory/SellThroughAnalysisWidget.tsx`, `frontend/src/components/inventory/OverstockAnalysisWidget.tsx`, `frontend/src/components/inventory/TransferRecommendationsWidget.tsx`
- **Backend:** `backend/api/routers/inventory.py::get_sell_through_analysis`, `backend/api/routers/inventory.py::get_overstock_analysis`, `backend/api/routers/inventory.py::get_transfer_recommendations`
- **MUST-PRESERVE features (4):**
  - Sell-Through: MTD/QTD sell-through % (qty_sold / (qty_sold + on_hand) * 100), targets per category, color-coded vs target
  - Overstock: SKUs where on_hand > reorder_level * 3 (oversized inventory position), suggest transfer or discount
  - Transfer Recommendations: multi-store balancing algorithm, suggest inter-store transfers to optimize availability (reduce OOS in high-demand stores, reduce overstock in low-demand stores)
  - Rebalance tab: drag-drop or action buttons to create recommended transfers

### Display Fixture & Layout Management  ·  `/inventory (tab: display-layout)`
- **Design file:** inventory.html  ·  **Status:** Done
- **Frontend:** `frontend/src/components/inventory/DisplayLayoutPanel.tsx`
- **Backend:** `backend/api/routers/display_placements.py (external, not in inventory.py)`, `backend/api/routers/display_fixtures.py (external, not in inventory.py)`
- **MUST-PRESERVE features (6):**
  - Visual floor plan editor: drag-drop display fixtures onto store layout
  - Fixture config: name, zone (e.g., 'Front Counter', 'Wall A', 'Window'), fixture_type (shelf/wall/counter), capacity (max units)
  - Placement assignment: assign SKUs to fixtures with planned_qty, visual qty on fixture tile
  - Deep-link: click Zone cell in stock ledger (e.g., ?fixture={fixture_id}) to jump here and highlight the fixture
  - Real-time sync: on_hand updates reflect in fixture placement availability
  - Audit: change history per placement (who assigned, when, qty change)

### Catalog/Product Add (Quick Add Mode)  ·  `/catalog/add (default: ?mode=single)`
- **Design file:** inventory.html  ·  **Status:** Done
- **Frontend:** `frontend/src/pages/catalog/ProductAddShell.tsx`, `frontend/src/pages/catalog/QuickAddPage.tsx`, `frontend/src/pages/catalog/productAddShared.ts`
- **Backend:** `backend/api/routers/catalog.py::create_catalog_product`, `backend/api/routers/catalog.py::get_category_fields`, `backend/api/routers/catalog.py::get_hsn_options`
- **MUST-PRESERVE features (24):**
  - Fast one-screen form with accordion sections: Identity, Pricing, Inventory, Online (collapsed by default)
  - Identity section:
  -   - Category dropdown (FR, SG, RG, LS, CL, WT, SMTWT, SMTSG, SMTFR, CK, ACC, HA)
  -   - Dynamic fields per category (e.g., CL: power, brand, base curve; Frames: material, style, color)
  -   - Description textarea
  -   - Advanced toggle for manual HSN/GST (auto-calc from category by default)
  - Pricing section:
  -   - MRP (number, required)
  -   - Offer Price (number, required, must be < MRP or = MRP)
  -   - Cost Price (number, optional)
  -   - Discount Category dropdown (MASS/PREMIUM/LUXURY/SERVICE/NON_DISCOUNTABLE) — caps apply: MASS 15%, PREMIUM 20%, LUXURY 5%, SERVICE 10%, NON_DISCOUNTABLE 0%
  - Inventory section:
  -   - Initial Quantity (number, default 0)
  -   - Barcode (text, optional, auto-gen if blank)
  -   - Reorder Level (number, default 5)
  - Online section (collapsed):
  -   - Sync to Shopify toggle
  -   - Shopify Tags (tag input, multi-select)
  -   - Publish POS toggle (default true)
  - Right rail: Live Review summary (SKU auto-gen, HSN/GST, final price, discountable?)
  - Buttons: Save (POST /products), Reset, Save + New (keeps category + brand, clears fields)
  - Keyboard shortcuts: Ctrl+Enter = Save, Ctrl+Shift+Enter = Save + New
  - Validations: category required, MRP > offer_price, quantity <= 10000, HSN required after save
  - Business rules: on product CREATE, auto-mint HSN from category (never NULL); category caps enforce floor discount_pct check

### Catalog/Product Add (Guided Mode - Wizard)  ·  `/catalog/add?mode=guided`
- **Design file:** inventory.html  ·  **Status:** Done
- **Frontend:** `frontend/src/pages/catalog/AddProductPage.tsx`, `frontend/src/pages/catalog/productAddShared.ts`
- **Backend:** `backend/api/routers/catalog.py::create_catalog_product`
- **MUST-PRESERVE features (8):**
  - 6-step wizard (same fields as Quick Add but step-by-step):
  -   Step 1: Category selection
  -   Step 2: Attributes (dynamic per category)
  -   Step 3: Pricing (MRP, Offer, Cost, Discount Category)
  -   Step 4: Inventory (Qty, Barcode, Reorder Level)
  -   Step 5: Online (Shopify Sync, Tags, POS Publish)
  -   Step 6: Review + Confirm
  - Next/Back buttons, progress indicator, validation on each step

### Catalog/Product Add (Bulk Mode - Rapid Grid)  ·  `/catalog/add?mode=bulk`
- **Design file:** inventory.html  ·  **Status:** Done
- **Frontend:** `frontend/src/pages/catalog/RapidGridPage.tsx`, `frontend/src/pages/catalog/productAddShared.ts`
- **Backend:** `backend/api/routers/catalog.py::create_catalog_product (bulk via POST /products with array)`
- **MUST-PRESERVE features (8):**
  - Grid/spreadsheet view for rapid multi-product entry
  - Columns: Category, Brand, Model, MRP, Offer Price, Cost, Discount Cat, Qty, Barcode, Reorder Level, HSN, GST
  - Inline edit cells with validation (category required, MRP > offer, etc.)
  - Add Row button (insert blank row)
  - Delete Row (remove row)
  - Batch validation: check all rows before save, highlight errors
  - Save All: POST bulk array of CreateProductPayload
  - CSV paste: paste from Excel, auto-parse rows

### Buy Desk (Catalog → Purchase Landing)  ·  `/catalog/buy-desk`
- **Design file:** inventory.html  ·  **Status:** Done
- **Frontend:** `frontend/src/pages/catalog/BuyDeskPage.tsx`, `frontend/src/pages/catalog/BuyDeskDraftPOModal.tsx`
- **Backend:** `backend/api/routers/catalog.py::list_catalog_products`, `backend/api/routers/purchase.py::create_purchase_order`
- **MUST-PRESERVE features (6):**
  - One-screen PO drafter: search/filter catalog products, add to cart (qty + unit_cost), draft a PO, save for approval
  - Left: Product search (category/brand/name filter) with sortable product list
  - Right: Cart (selected products, qty, unit_cost, total_value), Grand Total, Draft PO button
  - Draft PO form: Supplier (select), Payment Terms (dropdown), Target Delivery Date, Notes
  - Saves PO with status=DRAFT, ready for approval by STORE_MANAGER+ (Approvals inbox flow)
  - Pre-fill from low-stock: if nav to /catalog/buy-desk?product_id={id}, cart auto-populated with that product

### Catalog Autopilot (Brand+Model Search → Approve → Publish)  ·  `/catalog/autopilot`
- **Design file:** inventory.html  ·  **Status:** Done
- **Frontend:** `frontend/src/pages/catalog/CatalogAutopilotPage.tsx`
- **Backend:** `backend/api/routers/catalog_autopilot.py::search_brand_model`, `backend/api/routers/catalog_autopilot.py::approve_candidate`, `backend/api/routers/catalog_autopilot.py::publish_candidate`
- **MUST-PRESERVE features (5):**
  - Brand dropdown (all brands) + Model search (autocomplete within brand)
  - Candidate product from external DB (Zeiss, Ray-Ban, etc.), fetch details (category, power, attributes, image, MRP suggestion)
  - Prefill Quick Add: Approve button populates QuickAddPage with candidate data (AUTOPILOT_PREFILL_PARAM query string)
  - User reviews + adjusts pricing, publishes to catalog
  - Audit: track candidate → published product mapping (fulfillment of brand-model request)

### Pricing & Offers (Bulk Price + Discount Cap Enforcement)  ·  `/catalog/pricing`
- **Design file:** pricing.html  ·  **Status:** Done
- **Frontend:** `frontend/src/pages/pricing/PricingOffersPage.tsx`
- **Backend:** `backend/api/routers/catalog.py::bulk_price_update`, `backend/api/routers/catalog.py::bulk_offer_update`, `backend/api/routers/pricing_caps.py::get_pricing_caps`
- **MUST-PRESERVE features (23):**
  - Scope Picker sidebar (left):
  -   - Category dropdown (All categories, or select one)
  -   - Brand dropdown (All brands, or select one)
  -   - Store dropdown (All stores, or select one)
  - Operation tabs (top): Bulk Price Update | Bulk Offer Update
  - Bulk Price Update tab:
  -   - Amount: ₹{input} or {input}% increase/decrease
  -   - Preview button: POST /products/bulk-price (dry-run=true) with { scope, amount, operation }
  -   - Preview table: Product | SKU | Current MRP | New MRP | Change | Action (shows OK or BLOCKED reason)
  -   - Apply Valid Rows button: POST /products/bulk-price (dry-run=false), apply only ok=true rows
  - Bulk Offer Update tab:
  -   - Amount: ₹{input} or {input}% discount
  -   - Preview: POST /products/bulk-offer (dry-run=true)
  -   - Table: Product | Current Offer | New Offer | Cap | Status (chip: OK {discount}%, Over cap {cap}%, MRP < offer)
  -   - Apply button: POST /products/bulk-offer (dry-run=false)
  - Cap enforcement (single source of truth: backend/api/services/pricing_caps.py):
  -   - Category caps: MASS 15%, PREMIUM 20%, LUXURY 5%, SERVICE 10%, NON_DISCOUNTABLE 0%
  -   - Luxury brand caps: Cartier/Chopard/Bvlgari 2%, Gucci/Prada/Versace/Burberry 5%
  -   - MRP > offer_price (enforced at DB schema level, blocked at endpoint)
  -   - Offer price < MRP: no further store-level discount allowed (cap := 0)
  - Row-level feedback: green OK chip with implied_discount_pct, red Over cap {effective_cap_pct}%, amber MRP < offer
  - Export CSV: download preview + applied results for reconciliation
  - Business rules: caps are enforced at bulk-update time (fail-fast); a row that violates cap gets changed=false, ok=false, not applied; role caps (STORE_MANAGER 20%, etc.) are separate from category/brand caps (less restrictive role cap is fine if it's under category cap)

### Stock Replenishment (Auto-Reorder)  ·  `/inventory/replenishment`
- **Design file:** inventory.html  ·  **Status:** Done
- **Frontend:** `frontend/src/pages/inventory/StockReplenishment.tsx`
- **Backend:** `backend/api/routers/inventory.py::get_replenishment_suggestions`
- **MUST-PRESERVE features (6):**
  - Automated reorder suggestions: products where on_hand <= reorder_level
  - Table: Product, Current Stock, Reorder Level, Recommended Qty (reorder_level * 1.5), Supplier, Lead Time, Action
  - One-click Draft PO: creates PO line with product, qty, supplier, set PO status=DRAFT
  - Filter by category, supplier, urgency
  - Bulk Draft: select multiple products, create one PO with all lines
  - Integration: ties to Purchase Management module (PO approval flow)

### Stock Audit  ·  `/inventory/audit`
- **Design file:** inventory.html  ·  **Status:** Done
- **Frontend:** `frontend/src/pages/inventory/StockAudit.tsx`
- **Backend:** `backend/api/routers/inventory.py::get_stock_variance`, `backend/api/routers/inventory.py::post_variance_adjustment`
- **MUST-PRESERVE features (6):**
  - Variance report: compare stock_units on-hand vs system on_hand (from orders, transfers, receipts)
  - Table: Product, SKU, System On-Hand, Physical Counted, Variance, Variance %, Reason (on dropdown), Approve Adjustment
  - Adjust: set new on_hand (creates adjustment entry in stock ledger, audit-logged)
  - Bulk approve: accept multiple variances at once
  - Reason dropdown: inventory shrinkage, damage, theft, data entry error, other
  - Immutable history: preserve prior on_hand value (never delete, append adjustment)

### Opening Stock Import  ·  `/inventory/opening-stock`
- **Design file:** inventory.html  ·  **Status:** Done
- **Frontend:** `frontend/src/pages/inventory/OpeningStockImport.tsx`
- **Backend:** `backend/api/routers/inventory.py::opening_stock_preview`, `backend/api/routers/inventory.py::opening_stock_commit`
- **MUST-PRESERVE features (6):**
  - CSV upload: SKU, Product Name, Category, Qty, MRP, Offer Price, Barcode
  - Preview: POST /inventory/opening-stock/preview, shows validation results (✓ valid rows, ✗ errors per row)
  - Validation: SKU must exist or match category+brand, Qty must be >0, pricing checks (MRP > offer), HSN auto-mint from category
  - Commit: POST /inventory/opening-stock/commit, bulk INSERT stock_units with status=AVAILABLE (opening inventory is sellable)
  - Audit: linked to period (store opening date or go-live), immutable after commit
  - Progress bar: show upload progress + validation feedback

### Online Stock Sync (Shopify/E-Commerce)  ·  `/inventory/online-sync`
- **Design file:** inventory.html  ·  **Status:** Done
- **Frontend:** `frontend/src/pages/inventory/OnlineStockPage.tsx`
- **Backend:** `backend/api/routers/catalog.py::get_online_status`, `backend/api/routers/catalog.py::post_online_status`, `backend/api/routers/catalog.py::get_online_summary`, `backend/api/routers/catalog.py::online_stock_reconcile`
- **MUST-PRESERVE features (8):**
  - Shopify/e-commerce inventory sync dashboard
  - Table: Product, SKU, In-Store Qty, Shopify Qty, Status (In Sync / Out of Sync / Not Published)
  - Sync Status badges: green in-sync, yellow minor variance (<5%), red major variance (>=5%)
  - One-click Sync: push store stock to Shopify (POST /products/{id}/sync-shopify)
  - Bulk Sync: select multiple products, bulk update Shopify via /products/bulk-sync-shopify
  - Auto-Sync toggle: if enabled, sync runs every 30 min via backend NEXUS agent
  - Reconcile: flag and investigate discrepancies, manually override Shopify qty if out of sync
  - Publish/Unpublish: toggle product online visibility (affects /online-store availability)

### Power Grid (Contact Lens Multi-Dimension View)  ·  `/inventory/power-grid`
- **Design file:** inventory.html  ·  **Status:** Done
- **Frontend:** `frontend/src/components/inventory/LensPowerGridWidget.tsx`
- **Backend:** `backend/api/routers/inventory.py::get_power_grid`
- **MUST-PRESERVE features (6):**
  - Matrix grid for contact lens SKU: rows = CYL values (-6 to +6, 0.25 steps), cols = SPH values (-20 to +20, 0.25 steps), cells = qty on hand for that power combo
  - Color gradient: green high stock, yellow medium, red low, gray out-of-stock
  - Cell click: open transfer/order modal for that power combo
  - Filter by brand, product name, ADD (additional power)
  - Hot spots: highlight cells with <reorder_level qty (visual hot-spot for restocking)
  - Reorder from grid: select qty, click Reorder, draft PO or transfer

## Customers / CRM / Marketing / Loyalty / Vouchers / Store-credit

_16-screen CRM and marketing module covering customer lifecycle, loyalty program management, campaigns, referrals, NPS feedback, walkout recovery, WhatsApp inbox, and ad performance. All screens have real backend APIs. Screens map to accounts.html design (unverified). Status: Most screens Done; some partner tabs (Sentiment, Complaints, Comparison, Promotions) show empty states pending backend implementation._


### Customers List  ·  `/customers`
- **Design file:** accounts.html  ·  **Status:** Done
- **Frontend:** `frontend/src/pages/customers/CustomersPage.tsx`, `frontend/src/components/customers/AddCustomerModal.tsx`, `frontend/src/components/crm/RecallManager.tsx`
- **Backend:** `/crm`, `/customers`, `/prescriptions`
- **MUST-PRESERVE features (22):**
  - Tab navigation: Customers | Recalls | Campaigns
  - Search field: auto-search across name, mobile, email, relation names (family members)
  - Filter dropdown: ALL / B2C / B2B (customer_type)
  - Pagination: 50 per page with page navigation
  - Customer list table: ID, Name, Phone, Email, Address, Type badges, Actions
  - Add Customer button (+ icon) -> AddCustomerModal (modal creates new customer)
  - View customer detail panel (inline expansion or drawer)
  - Edit customer modal: name, phone, email, address fields
  - View customer prescriptions list
  - Add Prescription button -> PrescriptionForm modal (new Rx capture)
  - Edit/delete prescription actions
  - View purchase history table: order number, date, total, item count
  - Display real loyalty account data (from loyalty API, not fabricated)
  - Patient/family member list (embedded in customer record): name, relation, mobile, DOB
  - Add patient modal: name, mobile, DOB, relation dropdown
  - Edit/delete patient actions
  - RecallManager component (Recalls tab): recall type selector, list view
  - Add recall ability (trigger follow-up task engine)
  - Campaign quick-access tab (Campaigns tab): displays link to campaign manager
  - Search auto-focus trigger via URL param ?search=true
  - Business rule: customer_type dedup via mobile/phone normalization
  - Business rule: patient relation field must be one of: Self, Spouse, Child, Parent, Sibling, Other

### Customer 360 Dashboard  ·  `/customers/360, /customers/:customerId/360`
- **Design file:** accounts.html  ·  **Status:** Done
- **Frontend:** `frontend/src/pages/customers/Customer360Dashboard.tsx`, `frontend/src/components/customers/VipInterveneModal.tsx`, `frontend/src/components/customers/StoreCreditLedgerCard.tsx`, `frontend/src/components/customers/CustomerTagsPanel.tsx`
- **Backend:** `/crm`, `/customers`, `/orders`, `/prescriptions`, `/loyalty`, `/marketing`
- **MUST-PRESERVE features (18):**
  - Search modal (no customerId): search by name, phone, email; displays matching customer list
  - Customer profile header card: name, phone, email, address, customer_type badge
  - Tab navigation: Overview | Prescriptions | Orders | Interactions | Loyalty | Preferences
  - Overview tab KPI cards: Total Lifetime Value, Total Orders, Last Order Date & Amount, Customer Since Date, Avg Order Value, Visit Frequency, Referral Count, Active Loans
  - Last NPS score display (from nps_responses via marketing API)
  - Loyalty tier display with points balance (from real loyalty API)
  - Demographics section: age, preferred store
  - VIP Intervene button (SUPERADMIN/ADMIN only) -> VipInterveneModal with engagement actions
  - Prescriptions tab: table with date, Rx powers (SPH/CYL/AXIS/ADD), status badge (current/upcoming/expired), days until renewal countdown
  - Prescription editor modal with version history tracking
  - Orders tab: order list with date, total, item count, status badges
  - Interactions tab: interaction history table (call, SMS, email, WhatsApp, in_person), date, duration, notes, initiated_by
  - Loyalty tab: tier display, points balance, points to next tier, redeemed points total, total earned, member since date, birthday month
  - Preferences tab: empty state (not yet implemented)
  - Store Credit Ledger card (displays store credit/voucher balance)
  - Customer Tags panel (editable tags/labels)
  - Business rule: Rx renewal status = current (if expiry > today) | upcoming (expiry within 30 days) | expired (expiry < today)
  - Business rule: days_until_renewal = max(0, days between today and expiry_date)

### Customer Segmentation  ·  `/customers/segmentation`
- **Design file:** accounts.html  ·  **Status:** Done
- **Frontend:** `frontend/src/pages/customers/CustomerSegmentation.tsx`
- **Backend:** `/crm`
- **MUST-PRESERVE features (13):**
  - RFM Segments panel: displays 5 segment cards computed from real orders
  - Champions card: count, avg LTV, horizontal bar chart, segment description
  - Loyal card: count, avg LTV, horizontal bar chart, segment description
  - Big Spenders card: count, avg LTV, horizontal bar chart, segment description
  - At Risk card: count, avg LTV, horizontal bar chart, segment description
  - Lost card: count, avg LTV, horizontal bar chart, segment description
  - Churn Risk panel: filter dropdown (High | Medium | Low risk bands)
  - Churn customer list: table with name, phone, LTV, last purchase date, risk badge (text color only, no fill)
  - Risk band explanation text (hardcoded by band: '6+ months inactive', 'Declining frequency', 'Minor decline')
  - Alert message when no customers match selected band
  - Business rule: High risk = no purchases in 6+ months (was previously active)
  - Business rule: Medium risk = declining purchase frequency
  - Business rule: Low risk = minor engagement decline

### VIP Churn Watchlist  ·  `/customers/vip-churn-watchlist`
- **Design file:** accounts.html  ·  **Status:** Done
- **Frontend:** `frontend/src/pages/customers/VipChurnWatchlistPage.tsx`
- **Backend:** `/crm`
- **MUST-PRESERVE features (11):**
  - Store filter (free-text input for SUPERADMIN, implicit/read-only for ADMIN)
  - Risk filter dropdown: (blank) | HIGH | WATCH
  - Sort dropdown: overdue_by_days | ltv_desc | recency_asc
  - Trend summary line (single-line text, no stat tiles)
  - Customer table: Name, LTV (₹ format), Last Order Date, Days Overdue, Risk Label (text-color HIGH=red/WATCH=amber/blank=gray, no background fill)
  - Intervene button per row -> VipInterveneModal (SUPERADMIN/ADMIN only)
  - Last ORACLE scan date display (vip_churn_risk.scan_timestamp)
  - Loading state with spinner
  - Empty state: plain text 'No VIP customers at risk'
  - Business rule: VIP criteria = LTV >= 100,000 AND completed_orders >= 3
  - Business rule: Overdue = days since last order > customer's personal buying rhythm (ORACLE-computed)

### NBA (Next-Best-Action) Call List  ·  `/customers/nba`
- **Design file:** accounts.html  ·  **Status:** Done
- **Frontend:** `frontend/src/pages/customers/NBADashboardPage.tsx`
- **Backend:** `/crm`
- **MUST-PRESERVE features (15):**
  - Store selector (HQ roles: free dropdown; store staff: their store only, read-only)
  - Call list date display (ISO date)
  - Maximum 15 cards per day (2 reserved VIP slots)
  - Card layout per customer: name, loyalty tier chip (neutral colors, gold/platinum get subtle tint), last purchase date, LTV display
  - VIP indicator: amber left-edge accent (visual only, no badge)
  - Done button per card -> outcome modal
  - Skip button per card -> skip modal
  - Done modal: outcome input (required), notes textarea, next follow-up date input
  - Skip modal: skip reason dropdown (not_interested, already_called, no_answer, wrong_number), notes textarea, next date input
  - Tier chip color style: neutral gray (default), amber for GOLD, slate for PLATINUM
  - Loading spinner
  - Empty state: plain text (when no store selected or no customers for today)
  - Business rule: maximum 15 cards per day, VIP priority = top 2 slots reserved
  - Business rule: CALL LIST ONLY - no message send, no voucher mint, no outbound SMS/WhatsApp
  - Business rule: marking Done/Skip records in-app follow-up outcome in follow_ups collection

### Lapsed Reactivation Worklist  ·  `/customers/reactivation`
- **Design file:** accounts.html  ·  **Status:** Done
- **Frontend:** `frontend/src/pages/customers/LapsedReactivationPage.tsx`
- **Backend:** `/crm`
- **MUST-PRESERVE features (14):**
  - Store selector (HQ roles: free dropdown; store staff: their store only)
  - Analytics strip: Total Customers, VIP At Risk, Reactivated This Month, Avg Days Lapsed (KPI chips, monochrome, no charts)
  - Entry cards per customer: name, lapse label (e.g., 'Lapsed ~3y' or 'Lapsed 18mo'), last visit date, LTV display
  - VIP indicator: amber left-edge accent on VIP entries
  - Reached button -> outcome modal
  - Skip button -> skip modal
  - Outcome modal: outcome dropdown (reached, scheduled_visit, no_answer, not_interested, wrong_number), notes textarea, next follow-up date
  - List date display
  - Loading spinner
  - Empty state: plain text
  - Business rule: lapsed = no confirmed order AND no Rx exam in the lapse window (default 24 months)
  - Business rule: VIP prioritized at top of worklist
  - Business rule: WORK-LIST ONLY - no message send, no voucher mint
  - Business rule: marking Reached/Skip records in-app reactivation outcome

### Family/Household Loyalty Wallet  ·  `/customers/family-wallet`
- **Design file:** accounts.html  ·  **Status:** Done
- **Frontend:** `frontend/src/pages/customers/FamilyWalletPage.tsx`
- **Backend:** `/family-wallet`, `/customers`
- **MUST-PRESERVE features (13):**
  - Customer lookup section: phone number input, search button
  - Found customer display: name, phone
  - Household panel (post-lookup): household_id display, pooled points balance (₹ or points format)
  - Members list (max 7 members): member name/phone per row, membership role, remove button (except primary member)
  - Add member section: phone input, search button to find customer, select from results, Add button
  - Redeem section: points amount input, member selector dropdown (which member redeems), Redeem button
  - Points deduction confirmation message post-redeem
  - OTP delivery disabled warning (when backend requires OTP but outbound SMS/WhatsApp is disabled)
  - Store-wide redemption: store_id implicit (chain-wide, any member can redeem at any store)
  - Business rule: household max 7 members, any member can redeem from chain-wide pool
  - Business rule: redemption mints a store-credit voucher (not a physical gift)
  - Business rule: OTP gating deferred (outbound SMS/WhatsApp disabled), no-OTP redeem attempted
  - Role gate: manager+ can manage households; all roles can lookup

### Contact Lens Refill Worklist  ·  `/customers/cl-refill`
- **Design file:** accounts.html  ·  **Status:** Done
- **Frontend:** `frontend/src/pages/customers/CLRefillWorklistPage.tsx`
- **Backend:** `/crm`
- **MUST-PRESERVE features (14):**
  - Store selector (implicit for store staff, free for manager+)
  - 'Due within days' slider/input field (default 14, user adjustable)
  - Analytics KPI chips: Total Due, Overdue Count
  - Worklist table: Customer Name, Phone, Brand, Days Until Due (or 'OVERDUE' badge for negative), Last Refill Date, Actions column
  - Refresh button (RefreshCw icon)
  - Create Reminders button (manager+ only): POST /crm/cl-refill/reminders with dueWithinDays, returns created/deduped counts
  - Success toast: 'Created N follow-up task(s)'
  - Dedup toast: 'No new tasks — N already exist for these refills'
  - Empty state toast: 'No refills due in this window'
  - Loading spinner
  - Business rule: WORK-LIST ONLY - no message send, no voucher mint
  - Business rule: 'Create Reminders' turns list into follow-up tasks (uses task escalation engine, not direct message)
  - Business rule: tasks deduped (no duplicate task per customer in window)
  - Business rule: Contact Lens items only (frames/Rx spectacles excluded from this worklist)

### Loyalty Program Manager  ·  `/customers/loyalty`
- **Design file:** accounts.html  ·  **Status:** Done
- **Frontend:** `frontend/src/pages/customers/LoyaltyProgram.tsx`
- **Backend:** `/loyalty`
- **MUST-PRESERVE features (15):**
  - Tab navigation: Overview | Tiers | Rewards | Promotions
  - Overview tab: KPI cards - Total Members, Total Points Issued, Avg Points Per Member, Redemptions (from ProgramStats)
  - Tier breakdown cards: Bronze count, Silver count, Gold count, Platinum count (from stats.by_tier)
  - Tiers tab: 4 tier cards (Bronze/Silver/Gold/Platinum)
  -   Each tier card: name, badge emoji (🥉/🥈/🥇/💎), color-coded background, benefits list, customer count, LTV range
  - Rewards tab: Add Reward button (+ icon) -> add reward modal
  - Reward catalog (lazy-loaded when tab opens): reward list
  -   Each reward row: name, type badge (Discount/Free Item/Voucher/Experience, neutral gray style), point cost, description, Active toggle, Delete button
  - Promotions tab: (empty state: not yet implemented)
  - Add Reward modal: name input (required), type dropdown (DISCOUNT|FREE_ITEM|VOUCHER|EXPERIENCE), point_cost input (min 1, required), description textarea, Save button
  - Delete confirmation before remove
  - Active/Inactive toggle: calls loyaltyApi.updateReward with {active: boolean}
  - Business rule: 4 tiers only (BRONZE, SILVER, GOLD, PLATINUM) — no 'Diamond' or custom tiers
  - Business rule: tier thresholds + multipliers come from loyalty engine (loyaltyApi.getSettings), UI never disagrees
  - Business rule: reward_type must be one of: DISCOUNT, FREE_ITEM, VOUCHER, EXPERIENCE

### Per-Customer Loyalty Ledger  ·  `/customers/:customerId/loyalty`
- **Design file:** accounts.html  ·  **Status:** Done
- **Frontend:** `frontend/src/pages/customers/LoyaltyLedger.tsx`
- **Backend:** `/loyalty`
- **MUST-PRESERVE features (11):**
  - Back button -> navigate to parent view
  - Loyalty account summary card: current tier badge (color-coded), points balance, member since date
  - Transaction filter dropdown: All | EARN | REDEEM | EXPIRE | ADJUST
  - Pagination controls: 25 per page with skip/take params
  - Transaction table: Date, Type badge (EARN=green, REDEEM=blue, EXPIRE=amber, ADJUST=purple), Amount, Description, Reference ID
  - Refresh button
  - Loading spinner
  - Empty state message if no transactions match filter
  - URL params: ?type=EARN&skip=25 (filter + pagination state persisted)
  - Business rule: Transaction types = EARN (purchase), REDEEM (customer redeemed), EXPIRE (points expired), ADJUST (admin adjustment)
  - Business rule: ADJUST transactions only visible to SUPERADMIN (not to tier members)

### Campaign Manager  ·  `/customers/campaigns`
- **Design file:** accounts.html  ·  **Status:** Done
- **Frontend:** `frontend/src/pages/customers/CampaignManager.tsx`
- **Backend:** `/marketing`
- **MUST-PRESERVE features (16):**
  - Tab navigation: Campaigns | Builder
  - Campaigns tab: campaign summary stats (Active, Scheduled, Completed, Paused count)
  - Campaign list table: Name, Type badge (rx_renewal, birthday, winback, custom), Status badge (color-coded: ACTIVE=green, SCHEDULED=blue, COMPLETED=gray, PAUSED=orange), Audience size, Send date, Actions
  - Actions per row: Send button (with confirmation), Pause/Resume toggle, Duplicate button, Delete button, Refresh button
  - Send action: queues campaign via DISPATCH_MODE (off|test|live), respects opt-outs
  - Toast feedback: 'N queued, M skipped'
  - Builder tab: segment builder UI (complex, with LIVE audience count preview)
  - Segment criteria builder: saved segments dropdown
  - Template selector dropdown: PRESCRIPTION_EXPIRY, BIRTHDAY_WISH, ANNUAL_CHECKUP_REMINDER, WALKOUT_RECOVERY, REFERRAL_INVITE, GOOGLE_REVIEW_REQUEST, NPS_SURVEY
  - Channel selector: WHATSAPP | SMS | EMAIL
  - Scheduling options: immediate, scheduled (datetime picker), recurring
  - Dry-run preview before send
  - Save as draft button
  - Business rule: campaign types = rx_renewal, birthday, winback, custom
  - Business rule: status = ACTIVE (running), SCHEDULED (future), COMPLETED (sent, no more delivery), PAUSED (user-paused)
  - Business rule: DISPATCH_MODE controls message send (off=no send, test=test audience only, live=full send)

### Referral Tracker  ·  `/customers/referrals`
- **Design file:** accounts.html  ·  **Status:** Done
- **Frontend:** `frontend/src/pages/customers/ReferralTracker.tsx`
- **Backend:** `/marketing`
- **MUST-PRESERVE features (10):**
  - Tab navigation: Overview | Leaderboard | History
  - Overview tab: KPI cards - Total Referrals, Confirmed Referrals (REWARD_CREDITED|CONFIRMED|COMPLETED status), Total Earnings (₹ format)
  - Leaderboard tab: referrer aggregates (from real records) - Name, Referral Code (copy button with 2s confirmation), Total Referrals, Earnings
  - Sorted by referral count descending
  - History tab: referral records table - Referrer Name, Referrer Code, Referee Name, Status badge, Reward Amount, Invite Sent Date, Reward Credited Date
  - Copy code button: navigates.clipboard.writeText, shows 'Copied' checkmark for 2 seconds
  - Loading spinner
  - Empty state: plain text 'No referrals yet'
  - Business rule: status tracking = INVITED, REWARD_CREDITED, CONFIRMED, COMPLETED (only REWARD_CREDITED+ count as confirmed)
  - Business rule: aggregates computed from real referral records, no fabricated numbers

### Customer Feedback & NPS  ·  `/customers/feedback`
- **Design file:** accounts.html  ·  **Status:** Done
- **Frontend:** `frontend/src/pages/customers/CustomerFeedback.tsx`
- **Backend:** `/marketing`
- **MUST-PRESERVE features (13):**
  - Tab navigation: NPS | Sentiment | Complaints | Comparison
  - NPS tab: KPI cards - NPS Score (-100 to +100, formatted as .toFixed(0)), Avg Score, Response Rate (%), Promoters count, Passives count, Detractors count
  - NPS gauge visualization (semicircle or similar)
  - Score distribution chart (0-10)
  - Responses list: Customer Name, Score, Feedback text, Responded At date
  - Sentiment tab: empty state 'Sentiment analysis not yet implemented'
  - Complaints tab: empty state 'Complaint workflow pending backend'
  - Comparison tab: empty state 'Store-vs-store comparison deferred'
  - Store scope: implicit for staff, free for manager+
  - Loading spinner
  - Business rule: NPS = ((promoters - detractors) / total_responses) * 100
  - Business rule: Promoters = score 9-10, Passives = 7-8, Detractors = 0-6
  - Business rule: response_rate = total_responses / total_surveys (displayed as %)

### Follow-up Dashboard  ·  `/customers/follow-ups`
- **Design file:** accounts.html  ·  **Status:** Done
- **Frontend:** `frontend/src/pages/customers/FollowUpDashboard.tsx`
- **Backend:** `/follow-ups`
- **MUST-PRESERVE features (14):**
  - Follow-up type selector chips: All, Eye Test, Frame Replacement, Order Delivery, Prescription, NBA Call (displays icon + label)
  - Summary stats: Due Today count, This Week count, Overdue count, Completed This Month count, Pending Total count
  - Follow-up list table: Customer Name, Phone, Type badge, Scheduled Date, Status badge (pending=gray, completed=blue, skipped=gray)
  - Notes column (displays notes text)
  - Complete button per row -> outcome modal
  - Skip button per row
  - Complete modal: outcome dropdown (called_interested, called_not_interested, no_answer, rescheduled, completed), notes textarea, Save button
  - Outcome outcome badge colors: called_interested=green, called_not_interested=gray, no_answer=orange, rescheduled=yellow, completed=blue
  - Loading spinner
  - Empty state: plain text
  - Store scope: implicit for staff
  - Business rule: follow_up types = eye_test_reminder, frame_replacement, order_delivery, prescription_expiry, general, nba_call
  - Business rule: 'Due Today' = scheduled_date == today, 'This Week' = scheduled_date <= today+7, 'Overdue' = scheduled_date < today
  - Business rule: marking Complete/Skip creates audit log entry

### WhatsApp Inbox  ·  `/customers/whatsapp-inbox`
- **Design file:** accounts.html  ·  **Status:** Done
- **Frontend:** `frontend/src/pages/customers/WhatsAppInboxPage.tsx`
- **Backend:** `/webhooks`
- **MUST-PRESERVE features (13):**
  - Conversation list: Phone, Customer Name, Last Message Date, Needs Human indicator (boolean), Intent badge (book|reorder|agent|opt-out, inferred from message text)
  - Click conversation -> message thread view
  - Message thread: list of messages with sender name, text, timestamp (formatted as '27 Jun, 14:30'), type (text|button), direction (inbound|outbound)
  - Button payload display (if type=button)
  - 'Needs human' filter toggle (needsHuman param in API)
  - Pagination: 50 per page with offset
  - Intent badge colors: book=semantic color, reorder=semantic color, agent=semantic color, opt-out=semantic color
  - Loading spinner
  - Empty state: plain text 'No conversations'
  - Role gate: SUPERADMIN / ADMIN / STORE_MANAGER only
  - Business rule: intent detection regex patterns (book: /\b(book|eyetest|appointment)\b/, reorder: /\b(reorder|lens|contact)\b/, agent: /\b(agent|help|human)\b/, opt-out: /\b(stop|optout|unsubscribe)\b/)
  - Business rule: direction = inbound (customer->store) | outbound (store->customer)
  - Business rule: read-only v1 (no reply composition yet)

### Ad Performance Dashboard  ·  `/marketing/ad-performance`
- **Design file:** accounts.html  ·  **Status:** Done
- **Frontend:** `frontend/src/pages/marketing/AdPerformancePage.tsx`
- **Backend:** `/marketing`
- **MUST-PRESERVE features (12):**
  - Date range picker (default: last 30 days, ISO format)
  - Channel filter dropdown: All | Google | Meta
  - Summary cards: Total Spend (₹, formatted fmtINR), Total Leads (count), Total ROAS (%), Impressions (fmtNum)
  - Campaign performance table: Campaign Name, Channel badge (Google=blue, Meta=indigo), Spend (₹), Leads (count), ROAS (%), CPC (cost per click), Impressions (count), CTR (%)
  - Spend trend chart (line/bar over date range)
  - Empty state when ad accounts not configured: 'Connect your ad account'
  - Refresh button (RefreshCw icon)
  - Role gate: SUPERADMIN / ADMIN only (finance-sensitive spend data)
  - Business rule: ROAS = revenue / spend (calculated server-side)
  - Business rule: CPC = spend / clicks
  - Business rule: CTR = clicks / impressions * 100
  - Business rule: fail-soft: missing env vars (Google/Meta API keys) -> empty state, not error page

## HR + Payroll + Attendance

_Complete inventory of the HR, Payroll, and Attendance module for IMS 2.0. Covers 7 main screens and 3 sub-components with all controls, fields, modals, tabs, and Indian statutory payroll business rules._


### HR Page / Dashboard  ·  `/hr`
- **Design file:** none (no design yet)  ·  **Status:** Pending
- **Frontend:** `frontend/src/pages/hr/HRPage.tsx`, `frontend/src/components/hr/AttendanceSummaryCard.tsx`, `frontend/src/components/hr/EmployeeSelfService.tsx`, `frontend/src/components/hr/ShiftSetup.tsx`, `frontend/src/components/hr/WeekOffSwap.tsx`, `frontend/src/components/hr/CommissionLeaderboard.tsx`
- **Backend:** `/api/v1/hr/attendance (GET - fetches records)`, `/api/v1/hr/check-in (POST - geo-fenced)`, `/api/v1/hr/check-out (POST)`, `/api/v1/hr/leaves (GET - all leaves)`, `/api/v1/hr/leaves/{id}/approve (POST)`, `/api/v1/stores/{id} (GET - store location for geofence)`
- **MUST-PRESERVE features (17):**
  - Header: Editorial title 'HR & Attendance' with 'Who's on the floor.' subtitle
  - Check In button (geo-fenced, 500m radius required, Haversine distance calc, requires GPS permission)
  - Check Out button (finds open check-in via getAttendance, requires ID match)
  - Refresh button (reloads all data)
  - Stats cards: Present Today count, Absent count, On Leave count, Pending Leaves count
  - Tab bar: Today's Attendance, Leave Requests, Monthly Summary, Week-off Swaps, Shifts (manager-only), My Dashboard, Leaderboard
  - TODAY'S ATTENDANCE TAB: Table with columns: Employee, Role, Check In (time + late minutes), Check Out (time), Status (colored badge: PRESENT/ABSENT/HALF_DAY/LEAVE/LATE), Geo (map icon or warning)
  - TODAY'S ATTENDANCE: No records state shows empty icon + 'No attendance records for today'
  - LEAVE REQUESTS TAB: Cards per leave request with employee avatar, name, role, leave status badge (PENDING/APPROVED/REJECTED), leave type, date range (formatted), days count, reason, Fast-path badge (if applicable for short-notice CASUAL/SICK)
  - LEAVE REQUESTS: Approve button (green), Reject button (red border) - only visible for PENDING leaves to authorized users
  - LEAVE REQUESTS: Approved indicator shows 'Approved by [name] (remote)' if via fast_path
  - LEAVE REQUESTS: No leaves state shows empty icon + 'No leave requests'
  - MONTHLY SUMMARY TAB: Embedded AttendanceSummaryCard component
  - WEEK-OFF SWAPS TAB: Embedded WeekOffSwap component
  - SHIFTS TAB (manager-only): Embedded ShiftSetup component
  - MY DASHBOARD TAB: Embedded EmployeeSelfService component
  - LEADERBOARD TAB: Embedded CommissionLeaderboard component

### Attendance Page  ·  `/attendance`
- **Design file:** none (no design yet)  ·  **Status:** Pending
- **Frontend:** `frontend/src/pages/attendance/AttendancePage.tsx`, `frontend/src/components/hr/AttendanceWidget.tsx`, `frontend/src/components/hr/MonthlyAttendanceGrid.tsx`
- **Backend:** `/api/v1/hr/check-in (POST)`, `/api/v1/hr/attendance/grid (GET - server computes grid with day math + roster join)`, `/api/v1/hr/attendance/mark (POST - admin edit)`, `/api/v1/stores (GET - for store selector)`
- **MUST-PRESERVE features (6):**
  - Header: 'Attendance' eyebrow, 'Clock in. Track the floor.' title, 'Geo-fenced check-in for every employee. Managers see the monthly grid and can correct any day.' hint
  - Link to /my-work (My Work button)
  - Check In button (geo-fenced, same as HR page)
  - AttendanceWidget component (self check-in card, guards against double check-in)
  - Store selector dropdown (visible to SUPERADMIN/ADMIN/AREA_MANAGER only)
  - Monthly Attendance Grid component (visible to manager/accountant tier: SUPERADMIN/ADMIN/AREA_MANAGER/STORE_MANAGER/ACCOUNTANT)

### Monthly Attendance Grid  ·  `N/A (component used in Attendance and HR page)`
- **Design file:** none (no design yet)  ·  **Status:** Pending
- **Frontend:** `frontend/src/components/hr/MonthlyAttendanceGrid.tsx`
- **Backend:** `/api/v1/hr/attendance/grid (GET - with month + storeId params)`, `/api/v1/hr/attendance/mark (POST - upsert with employee_id + date + status)`, `/api/v1/hr/attendance/lwp-report (GET - optional LWP summary)`
- **MUST-PRESERVE features (14):**
  - Month navigation: Previous/Next arrow buttons, current month label (e.g. 'June 2026')
  - Legend: Color-coded status codes (P=green, A=red, L/HD=amber, LWP=orange, WO=gray)
  - Grid: Rows=Employees, Columns=Days 1-31 (or fewer per month), cell values = attendance code (P/A/L/HD/LWP/WO/-)
  - Summary column (right): P/A/L/LWP/WO counts per employee
  - Totals row (bottom): Daily totals across all employees
  - Day headers: Day number + weekday initial (Su/Mo/Tu/etc), weekend days (Su/Sa) have different styling
  - Admin edit: Click cell or row pencil icon (SUPERADMIN/ADMIN/STORE_MANAGER only) opens modal
  - EDIT MODAL: Status dropdown (PRESENT, ABSENT, HALF_DAY, LEAVE, HOLIDAY/WEEK_OFF)
  - EDIT MODAL: Check In time input (optional)
  - EDIT MODAL: Check Out time input (optional)
  - EDIT MODAL: Save button, Cancel button
  - LWP Report section (if available): Shows leave without pay summary
  - Loading state: Spinner centered
  - Empty state: No employees message

### Payroll Dashboard  ·  `/hr/payroll`
- **Design file:** none (no design yet)  ·  **Status:** Pending
- **Frontend:** `frontend/src/pages/hr/PayrollDashboard.tsx`
- **Backend:** `/api/v1/payroll/salary-sheet (GET - month, year, store_id params)`, `/api/v1/payroll/advances (POST - record), (GET - fetch per employee)`, `/api/v1/payroll/payslip/{employee_id}/{month}/{year} (GET)`
- **MUST-PRESERVE features (24):**
  - Header: 'Payroll' eyebrow, 'Month-end, by the rupee.' title, 'Basic + HRA + allowances − PF − ESI − PT − TDS − advances. Payslips, salary sheet export, month-lock after close.' hint
  - Tab bar: Salary Sheet, Advances, Payslips
  - SALARY SHEET TAB:
  - Month dropdown (1-12)
  - Year dropdown (current-1, current, current+1)
  - Export button (CSV with BOM for Excel, filename: payroll_MM_YYYY.csv)
  - Table: Headers = Employee, Basic, HRA, Allow., Gross, PF, ESI, PT, TDS, LWP, Advance, Net Pay
  - Table: Rows = one per employee, all numbers right-aligned, Gross in green, Net Pay in yellow bold
  - No data state: 'No salary data for [Month] [Year]'
  - ADVANCES TAB:
  - Employee dropdown (populated from salary sheet, empty=select employee option)
  - Record Advance button (only visible when employee selected)
  - Advance form: Amount input (number, required), Reason textarea (optional)
  - Form buttons: Submit, Cancel
  - Advances list: Cards with amount (₹), date_requested, status badge (pending=yellow, approved=green, settled=blue, deducted=gray)
  - No advances state: 'No advances recorded'
  - PAYSLIPS TAB:
  - Employee dropdown (populated from salary sheet)
  - Month dropdown
  - Year dropdown
  - Payslip display card (when employee selected): Title = employee name, subtitle = designation, date = [Month] [Year]
  - Earnings section: Basic, HRA, Conveyance, Medical, Gross (total in green bold with border-top)
  - Deductions section: PF, ESI, PT, TDS, LWP, Advance, Net Pay (total in yellow bold with border-top)
  - No payslip state: 'No payslip found for selected month'

### Salary Setup Page  ·  `/hr/salary-setup`
- **Design file:** none (no design yet)  ·  **Status:** Pending
- **Frontend:** `frontend/src/pages/hr/SalarySetupPage.tsx`
- **Backend:** `/api/v1/payroll/config (GET all, POST create)`, `/api/v1/payroll/config/{employee_id} (PUT update)`, `/api/v1/payroll/pt-slabs (GET all)`, `/api/v1/payroll/pt-slabs/seed (POST - seeds defaults)`, `/api/v1/payroll/config/bulk (POST - CSV import)`, `/api/v1/entities (GET - for entity dropdown)`
- **MUST-PRESERVE features (36):**
  - Header: 'Salary Setup' title, 'Structured-CTC salary master. Statutory deductions are computed at payroll run.' subtitle
  - Buttons: 'Seed PT slabs' (secondary), 'Bulk import (CSV)' (secondary), '+ Add salary' (primary) - only visible to ADMIN/SUPERADMIN
  - PT Slabs card: Shows all loaded PT slabs as badges with format 'State · Basis (e.g. Jharkhand · 2500-3500)', fallback message 'No PT slabs configured' with hint
  - Salary configs table: Headers = Employee, Designation, Entity, Basic (right-aligned), Gross (right-aligned), PF/ESI/PT (center), Edit (right, admin-only)
  - Table rows: Employee ID (bold), Designation, Entity name, Basic (₹), Gross (₹ bold), PF/ESI/PT flags (Y/N/—), Edit link (red text)
  - Empty state: 'No salary configs yet. Add one or bulk-import via CSV.' (for non-admin: without add instruction)
  - CREATE/EDIT MODAL:
  - Title: 'Add salary config' or 'Edit salary — [employee_id]'
  - 2-column grid of form fields:
  - Employee ID (text, required, disabled in edit mode)
  - Designation (text, optional)
  - Entity dropdown (optional, with fallback '— no entities yet —')
  - Store dropdown (optional, with fallback '— no stores found —')
  - Basic (number, required, >0)
  - HRA (number, ≥0)
  - Conveyance (number, ≥0)
  - Medical (number, ≥0)
  - Special allowance (number, ≥0)
  - Gross (auto-calculated, disabled read-only)
  - PF applicable dropdown (yes/no)
  - PF on ₹15k ceiling dropdown (yes 'cap at 15,000' / no 'on actual basic')
  - ESI applicable dropdown (auto 'gross ≤ 21k', yes, no)
  - PT applicable dropdown (yes/no)
  - TDS / month (number, optional)
  - PAN (text, optional)
  - UAN / PF (text, optional)
  - ESI IP number (text, optional)
  - Bank account no. (text, optional)
  - Bank IFSC (text, optional)
  - Modal buttons: Cancel, Create/Update (with save state 'Saving...')
  - BULK IMPORT MODAL:
  - Title: 'Bulk import (CSV)'
  - Help text: 'First line is the header. Supported columns: [list]. employee_id and basic are required.'
  - CSV textarea (monospace font, placeholder with example)
  - Modal buttons: Cancel, Import (with import state 'Importing...')
  - Validation: Employee ID required, Basic required + >0, field validation errors toast

### Payroll Run Page  ·  `/hr/payroll-run`
- **Design file:** none (no design yet)  ·  **Status:** Pending
- **Frontend:** `frontend/src/pages/hr/PayrollRunPage.tsx`
- **Backend:** `/api/v1/payroll/config (GET - list configs for selected entity)`, `/api/v1/payroll/run (GET - list rows, POST - compute dry run or actual)`, `/api/v1/payroll/run/approve (POST - APPROVED status)`, `/api/v1/payroll/run/lock (POST - PAID status, checks period lock)`, `/api/v1/payroll/summary (GET - statutory summary)`, `/api/v1/payroll/tally-jv (GET - download as blob)`, `/api/v1/payroll/pf-ecr (GET - download as blob)`, `/api/v1/payroll/payslip-html/{employee_id}/{month}/{year} (GET - print HTML)`, `/api/v1/entities (GET - for entity dropdown)`
- **MUST-PRESERVE features (18):**
  - Header: 'Payroll Run' title, 'Key unpaid-leave days, preview the computed payslips, then run → approve → lock.' subtitle
  - Controls: Month dropdown (1-12), Year dropdown, Entity dropdown ('All entities' option + list)
  - LWP Input section: Table with columns = Employee, Basic, Current LWP (input field, number)
  - Advances section: Table with columns = Employee, Advance pending (read-only amount), Advance to recover (input field, number)
  - Action buttons: Preview (secondary - dry run), Run (primary - save), Approve (if DRAFT rows exist), Lock (if APPROVED rows exist, SUPERADMIN/ADMIN only)
  - Export buttons: Download Tally JV (XML), Download PF ECR (text)
  - Payroll rows table:
  - Headers = Employee, Gross, Deductions, Net, Status (badge: DRAFT/APPROVED/PAID), Print Payslip (action link)
  - Status color: DRAFT=gray, APPROVED=blue, PAID=green
  - Summary totals row: Shows total Gross, total Deductions, total Net
  - Statutory Summary card (if available): Shows EPF, ESI, PT, TDS summary
  - Preview state: Message 'Preview: X employees, net [amount]'
  - Loading state: Centered spinner
  - Business rules:
  - LWP days: Proration calculated as (30 - LWP) / 30 × salary component
  - Statutory: EPF = 12% of wage-ceiling (₹15k cap if enabled), EPS = min(8.33%, capped ₹1250), ESI = 0.75% if gross ≤ ₹21k, PT = state slab based, TDS = manual entry
  - Month lock: Payroll approve + lock enforce period-lock (returns 423 if locked)
  - Idempotency: Per employee + month (dry run preview-only, actual run persists to DRAFT)

### Employee Self-Service (My Work)  ·  `/my-work`
- **Design file:** none (no design yet)  ·  **Status:** Pending
- **Frontend:** `frontend/src/pages/hr/EmployeeSelfService.tsx`
- **Backend:** `/api/v1/hr/me/attendance (GET - month + year params)`, `/api/v1/hr/me/payslip (GET - latest)`, `/api/v1/hr/me/commission (GET - month + year params)`, `/api/v1/hr/me/leaves (GET - year param)`
- **MUST-PRESERVE features (27):**
  - Layout: Single column, max-width-md (phone-friendly, 390px minimum comfortable)
  - Header: 'My Work' eyebrow, 'Hi, [First Name]' title (personalized), 'Your attendance, salary, leaves and commission.' subtitle
  - Month navigation: Previous button (disabled if current month), [Month Year] label, Next button (disabled if at/beyond current month)
  - ATTENDANCE CARD:
  - Section title: 'Attendance' (calendar icon)
  - Stats: 4 stat tiles (2 cols on phone, 4 on tablet+) = Present, Absent, Late, Half Day (all counts)
  - Day grid: Compact calendar grid showing attendance codes per day (P/A/L/HD/LWP/-), color-coded
  - PAYSLIP CARD:
  - Section title: 'Latest Payslip' (document icon)
  - Employee name (heading), month/year (subtext), employee ID (small text)
  - Earnings table: Gross Salary (₹), Total Deductions (₹)
  - Net Pay (large bold)
  - Download button (generates print-ready HTML in new window)
  - Empty state: 'No payslip available'
  - COMMISSION CARD:
  - Section title: 'Commission This Month' (trending icon)
  - Total commission amount (₹ large bold)
  - Sales count
  - Month-scoped (uses month selector)
  - Empty state: 'No commission data'
  - LEAVE BALANCE CARD:
  - Section title: 'Leave Balance' (calendar icon)
  - Breakdown: Casual, Sick, Earned, Privilege (each with count, or '—' if none)
  - Year-scoped (displays current year)
  - Empty state: 'No leave balance data'
  - Loading state: Spinners on each card independently
  - All reads are self-scoped (/hr/me/* endpoints) — no other employee's data visible

### Week-Off Swap Component  ·  `N/A (tab in /hr, embedded component)`
- **Design file:** none (no design yet)  ·  **Status:** Pending
- **Frontend:** `frontend/src/components/hr/WeekOffSwap.tsx`
- **Backend:** `/api/v1/hr/week-off-swaps (GET - all, POST - request)`, `/api/v1/hr/week-off-swaps/{id}/approve (POST)`, `/api/v1/hr/week-off-swaps/{id}/reject (POST)`
- **MUST-PRESERVE features (13):**
  - REQUEST FORM section:
  - Title: 'Request a Week-off Swap' (calendar icon)
  - Subtitle: 'Move your scheduled weekly-off to another date. A manager must approve.'
  - 3-column grid: Current week-off (date input), New week-off date (date input), Reason (text input, optional)
  - Submit request button (primary, disabled when submitting)
  - REQUESTS LIST section:
  - Pending count badge (if > 0)
  - Card per swap request: From date, To date, Reason, Status badge (PENDING/APPROVED/REJECTED/CANCELLED)
  - PENDING swaps show Approve (green) + Reject (red border) buttons (for authorized users only)
  - APPROVED swaps show approval info
  - Loading state: Centered spinner
  - Empty state: 'No week-off swaps'
  - Business rules: Requester cannot approve their own request (enforced server-side), dates must be different, dates must not be null

### Shift Setup Component  ·  `N/A (tab in /hr, manager-only, embedded component)`
- **Design file:** none (no design yet)  ·  **Status:** Pending
- **Frontend:** `frontend/src/components/hr/ShiftSetup.tsx`
- **Backend:** `/api/v1/hr/shifts (GET - list per store, POST - create, PUT - update)`
- **MUST-PRESERVE features (5):**
  - Manager-tier only (SUPERADMIN/ADMIN/AREA_MANAGER/STORE_MANAGER)
  - Purpose: Configure daily shift start/end times, apply to store roster
  - Components expected: Shift name, start time, end time, applicable days (checkboxes or multi-select), save/cancel buttons
  - Validation: Start time < End time, at least one day selected
  - Late-minute calculation: Based on shift start time (checked in HRPage header comment)

### Commission Leaderboard Component  ·  `N/A (tab in /hr, embedded component)`
- **Design file:** none (no design yet)  ·  **Status:** Pending
- **Frontend:** `frontend/src/components/hr/CommissionLeaderboard.tsx`
- **Backend:** `/api/v1/payroll/commission/leaderboard (GET - period + store_id params)`, `/api/v1/payroll/commission/summary (GET - month + year + store_id params)`
- **MUST-PRESERVE features (8):**
  - Period selector buttons: Today, Week, Month (segmented control, active tab highlighted)
  - Leaderboard table: Rank (1/2/3 with icon/color, >3 as number), Name (hidden for non-managers as 'Staff Member'), Badge (Champion/Star Performer/Rising Star/Team Player), Revenue (₹ right-aligned bold), Sales Count (right-aligned small)
  - Self-row highlighting: Bordered with red/pink background, '(you)' label appended to name
  - Manager visibility: Names visible only to managers (SUPERADMIN/ADMIN/AREA_MANAGER/STORE_MANAGER/ACCOUNTANT), others anonymized
  - Commission Summary card (if available): Monthly commission breakdown
  - Loading state: Centered spinner
  - Empty state: 'No sales recorded for this period.'
  - Business rules: Data is store-scoped if storeId prop provided

## Finance/Accounting + Purchase/Vendors

_Design-parity inventory for Finance/Accounting + Purchase/Vendors modules (11 screens). All controls, fields, dropdowns, toggles, chips, modals, tabs documented. Key business rules: order status filtering (exclude CANCELLED/DRAFT), period lock enforcement, blind EOD redaction at data layer, mandatory GRN attachment validation, RMA state machine, ITC reconciliation bucketing, budgeting variance auto-derive, cash register denomination handling (paisa server-side, rupees UI), vendor credit-terms validation (>= 0 days), GST REGISTERED vendor GSTIN format validation, F9 delivery-challan mode for no-PO receipts. All screens map to accounts.html design file except GRN/GRC (inventory.html)._


### Finance Dashboard (P&L Overview)  ·  `/finance/dashboard`
- **Design file:** accounts.html  ·  **Status:** Done
- **Frontend:** `FinanceDashboard.tsx`, `FinanceFilters.tsx`, `FinanceSummary.tsx`, `GSTPanel.tsx`, `OutstandingPanel.tsx`, `CashFlowPanel.tsx`, `BudgetPanel.tsx`, `VendorPayments.tsx`, `PeriodManagement.tsx`
- **Backend:** `finance.py`
- **Notes:** Maps to accounts.html; real API only (financeApi.*); excludes CANCELLED/DRAFT orders from all aggregations
- **MUST-PRESERVE features (11):**
  - Tab navigation (Dashboard | Cash Flow | GST | Outstanding | Budgets | Vendors | Period Lock)
  - Date range filter (from_date / to_date)
  - Store selector (multi-store scope for ADMIN/AREA_MANAGER)
  - Revenue card (gross_sales, net_revenue, deductions, gst_collected)
  - P&L section (revenue, COGS, gross_profit, operating_expenses, net_profit, margin %)
  - GST summary card (CGST/SGST/IGST collected, input_credit, gst_payable)
  - Outstanding Receivables table (customer_name, amount, due_date, days_overdue, status pill)
  - Cash Flow cards (opening_balance, inflows, outflows, closing_balance)
  - Budgets panel (category, allocated, spent, remaining, variance %)
  - Vendor Payments list (vendor_name, amount_due, status chip: paid/partial/pending)
  - Period lock controls (lock/unlock button, toggle state)

### Cash Flow & Payables (Owner Dashboard)  ·  `/finance/cash-flow`
- **Design file:** accounts.html  ·  **Status:** Done
- **Frontend:** `CashFlowPage.tsx`, `CashFlowPanel.tsx`
- **Backend:** `finance.py`, `vendors.py`
- **Notes:** Real API only; money shown in rupees, authoritative in paise server-side; ACCOUNTANT/ADMIN role gate
- **MUST-PRESERVE features (8):**
  - Tab navigation (Overview | Forecast | AP Aging)
  - Overview: AR vs AP card grid (receivables_total, payables_total, net_position, due_7d, due_30d)
  - This-month snapshot (revenue, expenses, vendor_payments, net_cash_flow)
  - Alerts panel (warning/info level, dismissible)
  - Receivables/Payables aging bar charts (0-30d / 31-60d / 61-90d / 90+ d buckets)
  - Forecast tab: 30/60/90-day projection with low-point alert, opening_cash slider input
  - AP Aging tab: payables by vendor, buckets (current / 1-30d / 31-60d / 61-90d / 90+ d)
  - Vendor ledger drawer (vendor detail, ledger entries, record bill/payment/debit-note controls)

### Cash Register (EOD Reconciliation)  ·  `/finance/cash-register`
- **Design file:** accounts.html  ·  **Status:** Done
- **Frontend:** `CashRegisterPage.tsx`
- **Backend:** `finance.py`
- **Notes:** Real API only (cashRegisterApi); denomination faces fixed to INR standard (no Rs 2000)
- **MUST-PRESERVE features (7):**
  - Open session form: shift dropdown (AM/PM), denomination grid (notes 500/200/100/50/20/10, coins 20/10/5/2/1), pieces spinner, open button
  - Active session display (session_id, opened_at, opening_amount, shift)
  - Close-session form: denomination grid (count pieces), bank_deposit text input, tolerance slider (default ₹200)
  - Live reconciliation preview (counted_total, expected_cash, bank_deposit, variance, status pill: BALANCED/OVER/SHORT)
  - History table (session_date, shift, opening_amount, closing_amount, variance, status)
  - Refresh button (reload sessions)
  - Color-coded variance: green (balanced), red (over/short)

### Blind EOD Tally & Z-Read  ·  `/finance/blind-eod`
- **Design file:** accounts.html  ·  **Status:** Done
- **Frontend:** `BlindEodTallyPage.tsx`
- **Backend:** `till.py`
- **Notes:** Money in paisa server-side, rupees in UI; blind enforcement at data layer (server redacts for cashier roles); POS CASH sales + refunds + payouts calculated from /api/v1/till/sessions
- **MUST-PRESERVE features (7):**
  - Open phase: shift dropdown, denomination grid (same as Cash Register), open button
  - Blind submit phase: denomination grid for count (no expected figure shown to cashier), payouts input, submit button
  - Manager lock phase (STORE_MANAGER/AREA_MANAGER): reveals expected figure, variance (over/short/balanced), Z-Read
  - Reopen: reason text input, reopen button (soft-lock, reopenable)
  - Session history list (date, shift, opened_by, closed_by, locked_by, status)
  - Data-layer redaction: SALES_CASHIER/CASHIER roles NEVER see expected figure pre-lock
  - Z-Read report (expected_cash, counted_cash, variance, locked_at, locked_by)

### GST Input Credit (ITC) Reconciliation  ·  `/finance/itc`
- **Design file:** accounts.html  ·  **Status:** Done
- **Frontend:** `ItcReconcilePage.tsx`
- **Backend:** `finance.py`
- **Notes:** GSTR-2B reconciliation; period auto-defaults to current FY month (April-March); reconciliation is pure matching, no mutations
- **MUST-PRESERVE features (8):**
  - Period dropdown (filtered from register.periods[])
  - ITC register table: vendor GSTIN, invoice_no, taxable, CGST, SGST, IGST, total_itc, match_status
  - CSV upload/paste: header auto-detect (GSTIN, Invoice, Taxable, Tax columns)
  - Reconciliation runner: parse GSTR-2B rows, match against booked bills
  - Result buckets (matched / mismatched / only_in_books / only_in_2b)
  - Bucket export buttons (CSV download per bucket)
  - Sum invariant strip (Total ITC = Safe + Mismatch + At-risk)
  - Status pills (matched: ok, mismatch: warn, only_in_books: err, only_in_2b: info)

### Budgeting (Planned vs Actual)  ·  `/finance/budgeting`
- **Design file:** accounts.html  ·  **Status:** Done
- **Frontend:** `BudgetingPage.tsx`
- **Backend:** `budgets.py`
- **Notes:** Dual-mode: planned=user-entered, actual=server-derived; never shows fabricated figures; fail-soft on DB outage
- **MUST-PRESERVE features (10):**
  - Store selector (dropdown, scope for ADMIN/AREA_MANAGER; defaults to activeStoreId)
  - Period selector (YYYY-MM format input, defaults to current month)
  - Editable planned amounts table (head name, planned_amount input, variance & % cols)
  - REVENUE head (income target, always first row)
  - Expense category heads (seeded defaults: rent, salaries, utilities, marketing, inventory, maintenance, travel, miscellaneous)
  - Add head form (text input, add button, duplicate/reserved-name validation)
  - Delete head button per row (removes budget document)
  - Actual figures auto-derived (orders for REVENUE, APPROVED expenses by category)
  - Variance display (allocated - spent, % variance, color: green if positive/under, red if over)
  - Save all button (upserts all non-empty planned amounts)

### B2B Tally Export Console  ·  `/finance/b2b-tally-export`
- **Design file:** accounts.html  ·  **Status:** Done
- **Frontend:** `B2BTallyExport.tsx`
- **Backend:** `finance.py`
- **Notes:** ACCOUNTANT/ADMIN role gate; real Tally integration (e-invoice + e-way bill issued in Tally)
- **MUST-PRESERVE features (7):**
  - Invoice list (invoice_number, order_date, customer_name, amount, status: exported/failed/pending)
  - Status filter (dropdown: all/pending/exported/failed)
  - Bulk select checkbox (header + per-row)
  - Export button (converts selected invoices to Tally XML, queues e-invoice + e-way bill)
  - Refresh button
  - Status chips (color-coded: pending=gray, exported=green, failed=red)
  - Re-export failed button (retry 403/500 exports)

### B2B Tally Worklist (Reminders)  ·  `/finance/b2b-tally-worklist`
- **Design file:** accounts.html  ·  **Status:** Done
- **Frontend:** `B2BTallyWorklist.tsx`
- **Backend:** `finance.py`
- **Notes:** ACCOUNTANT/ADMIN role gate; reminders for compliance
- **MUST-PRESERVE features (4):**
  - Pending worklist (invoices not yet exported to Tally)
  - Invoice row (invoice_number, order_date, customer_name, days_pending badge, re-export button)
  - Sort by (invoice_date, days_pending, customer_name)
  - Bulk actions (select all, export selected)

### Purchase Management (Main Module)  ·  `/purchase (tabs: purchase-orders | purchase-invoices | variance | suppliers | vendor-returns | analytics)`
- **Design file:** accounts.html  ·  **Status:** Done
- **Frontend:** `PurchaseManagementPage.tsx`, `PurchaseTable.tsx`, `PurchaseOrderForm.tsx`, `SupplierPanel.tsx`, `SupplierFormModal.tsx`, `PurchaseInvoicesTab.tsx`, `PurchaseVarianceTab.tsx`, `PurchaseAnalytics.tsx`
- **Backend:** `vendors.py`, `supply_chain.py`
- **Notes:** ADMIN/AREA_MANAGER/STORE_MANAGER/ACCOUNTANT; real API (vendorsApi.getPurchaseOrders, getVendors); tab URL param syncing
- **MUST-PRESERVE features (11):**
  - Tab bar (Purchase Orders | Invoices | Variance | Suppliers | Vendor Returns | Analytics)
  - Search box (filters by PO#/vendor/product)
  - Status filter dropdown (ALL / DRAFT / SENT / ACKNOWLEDGED / PARTIAL / RECEIVED / CANCELLED)
  - Create PO button (opens PurchaseOrderForm modal)
  - PO table (po_number, vendor_name, date, expected_date, status, items_count, total, actions)
  - PO detail drawer (expandable, shows items, approvals, received quantities)
  - Supplier list (name, contact_person, phone, email, credit_limit, current_outstanding, performance metrics)
  - Add supplier button (opens SupplierFormModal)
  - Invoice tab: purchase invoices with recon flags
  - Variance tab: received vs invoiced quantity mismatches
  - Analytics tab: vendor performance, purchase trends, cost analysis

### Goods Receipt Note (GRN)  ·  `/purchase/grn`
- **Design file:** inventory.html  ·  **Status:** Done
- **Frontend:** `GoodsReceiptNote.tsx`, `GRNPrint.tsx`
- **Backend:** `vendors.py`, `supply_chain.py`
- **Notes:** Real API (vendorsApi.getGRNs, createGRN); inventory.html design; INSPECTION_CHECKLIST seeded; quality_status derived from accept/reject totals (not stored); F9 delivery-challan mode supports no-PO receipts; mints stock units with location_code + batch/expiry for CL
- **MUST-PRESERVE features (14):**
  - Tab navigation (Create | History | Discrepancies)
  - Create tab: PO dropdown (filters by status SENT/ACKNOWLEDGED/PARTIAL/PARTIALLY_RECEIVED), items table
  - GRN line item fields (product_name, sku, po_qty, received_qty, inspection_status chip: pending/passed/failed)
  - Inspection checklist (8 checks: packaging, expiry, damage, serial, qty, color, certifications)
  - Quality notes textarea
  - Vendor invoice number input
  - F9 Delivery-Challan mode toggle (enables dc_number, dc_date fields; makes vendor invoice optional)
  - Discrepancy tracking (received_qty, accepted_qty, rejected_qty, rejection_reason)
  - Batch code + expiry date inputs (for contact lenses, optional)
  - Quality status chip (passed: green, failed: red, conditional: yellow)
  - Submit button (creates GRN, mints stock units)
  - Print GRN button (opens print dialog)
  - History table (grn_number, po_number, received_at, items_received, quality_status)
  - Discrepancies panel (product, received, accepted, rejected, reason)

### Goods Receipt Cockpit (Vendor-first Receiving)  ·  `/purchase/receive`
- **Design file:** inventory.html  ·  **Status:** Done
- **Frontend:** `GoodsReceiptCockpit.tsx`, `PrintLabelsDialog.tsx`
- **Backend:** `vendors.py`, `supply_chain.py`
- **Notes:** SUPERADMIN/ADMIN/AREA_MANAGER/STORE_MANAGER; vendor-first flow (cockpit payload pre-filters open POs); mandatory file_id validation (forged id -> 400); received_qty capped to ordered_qty; mismatch raises follow-up task
- **MUST-PRESERVE features (12):**
  - Vendor selector (dropdown, filters cockpit payload with 3 worklists: open POs, in-transit, ready-to-receive)
  - Worklist sections (open POs with count badge, in-transit shipments, ready-to-receive list)
  - PO selector (from worklist, expands to show line items)
  - Receive line items form (product_name, ordered_qty, fields: received_qty, accepted_qty, rejected_qty, rejection_reason)
  - Batch code + expiry date inputs (optional, for CL)
  - MANDATORY attachment gate (vendor invoice/challan upload, file_id validation before GRN create)
  - Attachment preview (image/PDF thumbnail)
  - Remove attachment button
  - Create GRN button (disabled until attachment uploaded + valid)
  - Print labels dialog (post-GRN, asks to print barcode labels for received products)
  - Reopen reason input (on soft-lock)
  - SectionHeader component (title + count chip)

### Vendor RMA (Return Merchandise Authorization)  ·  `/purchase/vendor-rma`
- **Design file:** accounts.html  ·  **Status:** Done
- **Frontend:** `VendorRMA.tsx`
- **Backend:** `vendor_rma.py`
- **Notes:** N4 feature; SUPERADMIN/ADMIN/AREA_MANAGER/STORE_MANAGER/ACCOUNTANT; lifecycle state machine (DRAFT -> AUTHORIZED -> DISPATCHED -> CREDIT_RECEIVED -> CLOSED); credit notes reconciliation; no emoji (Windows cp1252)
- **MUST-PRESERVE features (13):**
  - RMA list (rma_id, vendor_name, status chip, date, total_amount, actions)
  - Status chips (DRAFT/AUTHORIZED/DISPATCHED/CREDIT_RECEIVED/CLOSED/REJECTED, color-coded)
  - Create RMA modal: vendor dropdown, line items table (product_name, quantity, reason, unit_cost)
  - Reason dropdown (DEFECTIVE / WRONG / EXCESS / WARRANTY / NON_ADAPT)
  - RMA notes textarea
  - RMA detail drawer (lifecycle: DRAFT -> AUTHORIZED -> DISPATCHED -> CREDIT_RECEIVED -> CLOSED)
  - Authorize button (if DRAFT)
  - Dispatch button (generates tracking stub, if AUTHORIZED)
  - Record credit notes section (CN date, CN amount, file upload)
  - Close button (if all CRs recorded, if CREDIT_RECEIVED)
  - Reject button (terminal, any state except CLOSED)
  - Expected total calculator (sum qty * unit_cost per line)
  - Money display in rupees (authoritative in paise server-side)

### Reconciliation Console (Accountant's 4-flag + Worklists)  ·  `/purchase/recon-console`
- **Design file:** accounts.html  ·  **Status:** Done
- **Frontend:** `ReconConsole.tsx`
- **Backend:** `vendors.py`, `supply_chain.py`
- **Notes:** Purchase S6 feature; ACCOUNTANT/ADMIN/SUPERADMIN only; 4 atomic recon flags per invoice; 4 independent worklists for follow-up; flag toggles call `purchaseReconApi.toggleFlag` (server-side audit-logged)
- **MUST-PRESERVE features (17):**
  - Purchase Invoices section: expandable bill list
  - Per-invoice 4-flag controls (checkbox buttons with titles):
  -   - Reconciled (physically reconciled with vendor statement)
  -   - Entered Tally (entered into Tally)
  -   - Filed GST (included in GSTR-2B / GSTR-3B)
  -   - Paid (payment scheduled or made)
  - Flag state display (by user, at timestamp when checked)
  - Completion progress (done_count / 4 with %, color: green if 100%, amber otherwise)
  - Expand/collapse toggle per bill
  - Bill row shows (invoice_id, vendor_name, invoice_amount, invoice_date)
  - 4 Worklists (collapsible sections):
  -   - Stock yet to receive (open POs with unreceived lines, count)
  -   - Vendor returns (open/in-flight RMAs, count)
  -   - Pending scheme credit notes (VOLUME_REBATE CNs not yet received)
  -   - Pending return credit notes (return CNs not yet issued)
  - Worklist rows (show key fields + action buttons per row, e.g. follow-up)
  - Refresh button

## Tasks/SOP + Hub/Dashboard + Reports/Analytics

_COMPREHENSIVE AUDIT: Tasks/SOP + Hub/Dashboard + Reports/Analytics Module

SCREEN COUNT: 5 major screens (Hub Dashboard, Task Management, Tasks Dashboard, Reports, Activity Log)

DESIGN COVERAGE: 3 of 11 design files referenced (hub.html, tasks.html, reports.html); activity log has no design yet

KEY BUSINESS RULES (embedded in screens):

1. **Task Lifecycle & Escalation**
   - Priority codes: P0 (immediate, ~10m), P1 (<30m, escalates auto), P2 (today, rolls forward), P3 (week, plannable), P4 (backlog, nice-to-have)
   - Statuses: OPEN → IN_PROGRESS → COMPLETED; can also escalate to OVERDUE or be CANCELLED
   - Auto-escalation: when due_at passes SLA threshold, task escalates to next manager tier (STORE_MANAGER → AREA_MANAGER → ADMIN → SUPERADMIN)
   - Max escalation level enforced; manual escalate capped by MAX_ESCALATION_LEVEL
   - Task store-scoped; only owner/assigner/manager-tier can mutate (3-tier object-level guard)

2. **SOP Management**
   - Templates are persistent, re-usable, frequency-driven (DAILY|WEEKLY|MONTHLY|AD_HOC)
   - Daily checklists = DAILY-frequency templates + auto-seeded starters if store has none
   - Checklist items toggled per-step, completion tracked per template per day
   - Steps are ordered, deletable, editable; warnings highlight critical steps
   - Can be archived (soft delete) not destroyed
   - Assigned to roles (who must complete) and users (additional assignees)

3. **Reports & Analytics**
   - Date-range driven: Today, Week, Month, Quarter, Custom
   - All financials exclude CANCELLED & DRAFT orders (Q1-2026 audit fix)
   - Non-moving stock color-coded: ≥180d = red (dead), ≥120d = orange (slow), else yellow (monitor)
   - Non-moving stock surfaces 'never_sold' flag top + product_id can be null (new SKUs)
   - GST reports (GSTR-1, GSTR-3B) include CDNR section (credit/debit notes)
   - Purchase recommendations use 90d velocity, reorder point, confidence tier (HIGH|MEDIUM|LOW)
   - All reports respect role RBAC: financial reports gated to ADMIN|AREA_MANAGER|STORE_MANAGER|ACCOUNTANT

4. **Hub Dashboard**
   - Lazy-load all cards (fire-and-forget pattern: 404/500 → show placeholders)
   - Priority tasks sorted P0-P4 then by due_at (most urgent first, top 5)
   - SOP templates top 5 (DAILY frequency only)
   - Owner digest privacy-stratified: SUPERADMIN/ADMIN see ₹ amounts, others see % only
   - Handoff inbox + clinical handover cards for inter-store file + Rx transfers
   - Jarvis card role-gated to SUPERADMIN

5. **Activity Log (Audit Trail)**
   - SUPERADMIN-only read-only explorer over immutable audit_logs collection
   - Filters: user, organization (entity_id), store (store_code), date range, action (prefix match)
   - Stores filter narrows per organization picked (preventing contradictory filters)
   - Expandable rows show before/after changes, IP, user ID, severity
   - Used by JARVIS to answer 'who did what when' questions

STATUS: All 5 screens DONE; no pending design updates. 100% backend-wired per code audit.

NEXT SESSION: Re-verify against deployed Railway/Vercel stack for visual regression + live E2E flow test (user stories: onboarding → create task → SOP checklist → run report → close day)._


### Hub/Dashboard Home Page  ·  `/dashboard`
- **Design file:** hub.html  ·  **Status:** Done
- **Frontend:** `frontend/src/pages/dashboard/HubPage.tsx`, `frontend/src/components/handoffs/HandoffInboxCard.tsx`, `frontend/src/components/handoffs/ClinicalHandoverCard.tsx`, `frontend/src/components/handoffs/HandoffUploadModal.tsx`, `frontend/src/components/notifications/DashboardNotifications.tsx`, `frontend/src/components/dashboard/OwnerDigestCard.tsx`, `frontend/src/components/hub/TickerCard.tsx`
- **Backend:** `analytics.py (GET /dashboard-summary, /targets)`, `tasks.py (GET /tasks, /task-summary, /sop-templates)`, `clinical.py (GET /queue)`
- **MUST-PRESERVE features (18):**
  - Hero section with greeting + today's date
  - Live sales summary card (₹ formatted, % delta vs 4-wk avg)
  - Open tasks count + overdue/P1 breakdown
  - Clinical queue count + in-exam detail
  - Monthly target ticker (privacy-stratified by role)
  - Owner digest snapshot (SUPERADMIN/ADMIN only) — day-close KPIs
  - Priority tasks list (top 5 open, sorted P0-P4 then by due_at)
  - Task rows: priority pill + title + due countdown (live ticker)
  - Priority task SLA colors: P0=red, P1=orange, P2=yellow, P3=blue, P4=gray
  - Today's checklists section (DAILY frequency SOP templates, top 5)
  - SOP rows: template title + frequency + estimated_time (mins)
  - Dashboard notifications card (inbound message preview)
  - Module grid: 8 cards (POS, Clinical, Inventory, Tasks, Reports, Customers, Jarvis, Store Setup)
  - Module card eyebrow label + title + description + icon + live meta KPIs
  - Jarvis card badge 'Super-admin' + role gate requireRoles=['SUPERADMIN']
  - Send a file button (opens HandoffUploadModal)
  - Handoff inbox card (external lens lab file intake)
  - Clinical handover card (Rx + test transfer to POS)

### Task Management Page — Tabs: My Tasks, Team Tasks, SOP Editor, Analytics  ·  `/tasks`
- **Design file:** tasks.html  ·  **Status:** Done
- **Frontend:** `frontend/src/pages/tasks/TaskManagementPage.tsx`, `frontend/src/components/tasks/NewTaskModal.tsx`, `frontend/src/components/tasks/SopEditorModal.tsx`
- **Backend:** `tasks.py (GET /tasks, POST /tasks, PUT /tasks/{id}, PATCH /tasks/{id}/complete|acknowledge|start|escalate|reassign, POST /sop-templates, PUT /sop-templates/{id}, DELETE /sop-templates/{id}, GET /sop-templates)`
- **MUST-PRESERVE features (18):**
  - TAB: My Tasks — search field (title/description), status filter (PENDING|IN_PROGRESS|COMPLETED|OVERDUE|ALL), priority filter (URGENT|HIGH|MEDIUM|LOW|ALL)
  - Task list with rows: priority badge (P0=red, P1=orange, P2=yellow, P3=blue, P4=gray), title, assignee name, due date, status badge, actions
  - Task actions: view detail modal, complete (requires completion_notes), acknowledge, start, escalate (auto-reassigns to manager tier per SLA)
  - New Task button → NewTaskModal
  - TAB: Team Tasks — for STORE_MANAGER|AREA_MANAGER only, task list with team member filter, same row structure
  - TAB: SOP (Standard Operating Procedures) — list of sop_templates, read-only view for staff
  - SOP rows: title, description, category, frequency (DAILY|WEEKLY|MONTHLY|AD_HOC), estimated_time, assigned_roles, steps list
  - SOP View button → expands inline step list with warnings (yellow alerts)
  - Edit SOP button → SopEditorModal (SUPERADMIN|ADMIN|STORE_MANAGER only)
  - New SOP button → SopEditorModal with null initial
  - TAB: Analytics — team performance grid (employee, tasks assigned, completed, overdue, % completion rate), bar chart
  - MODAL: NewTaskModal — priority selector (5 chips P0-P4, each with SLA hint), title field (min 3 chars), description textarea, due-in presets (15m, 30m, 1h, 2h, end-of-shift, tomorrow-10am, this-week)
  - NewTaskModal: owner/assignee dropdown (store users list, fallback self-assign), watchers multi-select (context-only, backend doesn't persist)
  - NewTaskModal: auto-escalation toggle (context-only), live preview card + escalation ladder on RHS
  - MODAL: SopEditorModal — title field (min 3 chars), description textarea, category dropdown (Operations|Finance|Sales|Clinical|Workshop), frequency dropdown (DAILY|WEEKLY|MONTHLY|AD_HOC), estimated_time number input
  - SopEditorModal: steps list editor — each row: instruction textarea, optional warning textarea, up/down reorder buttons, delete button
  - SopEditorModal: add step button, assigned_roles multi-select (all 11 roles), assigned_users multi-select (store staff)
  - SopEditorModal save button (create or update), delete button (archives, not destroys)

### Tasks Dashboard — My Tasks, Daily Checklists, Team Tasks (Manager-only)  ·  `/tasks/dashboard, /tasks/checklists`
- **Design file:** tasks.html  ·  **Status:** Done
- **Frontend:** `frontend/src/pages/tasks/TasksDashboard.tsx`, `frontend/src/components/tasks/SystemIntegrityPanel.tsx`
- **Backend:** `tasks.py (GET /tasks, /task-summary, /sop-templates, /sop-templates/{id}/checklist, PATCH /sop-templates/{id}/checklist/{step}/toggle, POST /sop-templates/seed-defaults)`
- **MUST-PRESERVE features (12):**
  - TAB: My Tasks — summary cards: Total Tasks (count), Open/In Progress (count, yellow), Overdue (count, red), Completed (count, green)
  - Alert banner when overdue > 0 or escalated > 0 — red box with triangle icon + counts + 'Action required'
  - My Tasks list with priority color bars, title, description, assigned-by, due date, status, completion notes (if COMPLETED)
  - Task row actions: complete (requires notes), acknowledge, start
  - Create Task button (inline, opens inline modal/form on this page)
  - Create Task form: title field, description, priority dropdown (P0-P4), assignee dropdown (self or other), due_date date picker
  - TAB: Daily Checklists — SOP template picker dropdown (DAILY frequency only, auto-selects first)
  - Checklist rendering: template title + description + frequency + estimated_time, steps list
  - Each checklist step: checkbox + instruction text + optional warning (yellow box), step indicator (N/M)
  - Seed Defaults button (creates starter checklists if none exist) — SUPERADMIN|ADMIN|STORE_MANAGER only
  - TAB: Team Tasks (manager-only) — same task list as My Tasks but shows all store tasks, team member selector dropdown, status filter (all|open|completed|escalated)
  - SystemIntegrityPanel (manager-only row below header) — variance/integrity controls for reconciliation

### Reports Page — Tabs: Sales, Inventory, Customers, GST; Cards: 6 deep-dive sections  ·  `/reports`
- **Design file:** reports.html  ·  **Status:** Done
- **Frontend:** `frontend/src/pages/reports/ReportsPage.tsx`, `frontend/src/components/reports/GSTR1Report.tsx`, `frontend/src/components/reports/GSTR3BReport.tsx`, `frontend/src/components/reports/DemandForecast.tsx`, `frontend/src/pages/reports/sections/FootfallAuditCard.tsx`, `frontend/src/pages/reports/sections/TaxCodeAuditCard.tsx`, `frontend/src/pages/reports/sections/PriceBandCard.tsx`, `frontend/src/pages/reports/sections/LensDeepDiveCard.tsx`, `frontend/src/pages/reports/sections/SeasonalityCard.tsx`, `frontend/src/pages/reports/sections/WorkshopProductivityCard.tsx`
- **Backend:** `reports.py (GET /sales/summary, /sales/growth, /sales/by-category, /sales/trend, /inventory/non-moving-stock, /inventory/stock-count, /customers/acquisition, /customers/discount-analysis, /customers/expense-vs-revenue, /staff-ranking, /brand-sellthrough, /purchase-recommendations, /workshop/pending-jobs, GET /gst/gstr1, /gst/gstr3b)`
- **MUST-PRESERVE features (20):**
  - Header with report type tabs: Sales, Inventory, Customers, GST (4 tabs, ADMIN|AREA_MANAGER|STORE_MANAGER|ACCOUNTANT gated)
  - Date range selector: Today, Week, Month, Quarter, Custom (radio/dropdown)
  - Sales Tab: Daily Sales Report card, Monthly Sales Summary card, both with export-to-CSV button
  - Sales tab: Total Sales, Order Count, Avg Order Value, Top Category, Gross Profit, GST Collected (6-stat banner)
  - Category Breakdown chart: category name, sales (₹), units, % share — sortable table with export
  - Daily Trend line chart: date on X, sales (₹) on Y
  - Sales Comparison card: MoM growth % + previous-month sales, YoY growth % + previous-year sales
  - Non-Moving Stock table: SKU, brand, model, MRP, last_sold_at (or 'Never sold'), days_since_sold (color-coded: ≥180d red, ≥120d orange, else yellow), total_sold_all_time
  - Purchase Recommendations card (R2): product name, brand, category, 90d velocity, current stock, reorder point, suggested order qty, avg selling price, estimated margin %, confidence (HIGH|MEDIUM|LOW)
  - Inventory Tab: Stock Report card, Stock Movement card, Non-Moving Stock deep-dive
  - Customers Tab: Customer Report card, Customer Acquisition card, Discount Analysis card, Expense vs Revenue card
  - GST Tab: GSTR-1 Report card (with modal viewer), GSTR-3B Report card (with modal viewer), Tax Code Audit card
  - GSTR-1 modal: outbound invoice table (invoice number, date, GSTIN, invoice amount, tax %, tax amount), CDNR section
  - GSTR-3B modal: ITC reconciliation (input tax, credit eligible, claw-back per category), liability summary
  - Footfall Audit card: manual walk-in count + conversion % (sales/footfall)
  - Tax Code Audit card: invoice count per tax rate (5%, 18%, etc.), aggregate revenue per rate
  - Price Band card: category-wise price segments (bottom 10%, mid 50%, top 10%)
  - Lens Deep Dive card: sphere range, cylinder range, add segment breakdown + sales contribution
  - Seasonality card: month-wise sales trend (bar chart) — identify peak/off-peak
  - Workshop Productivity card: pending jobs aging, completion rate (%), avg turnaround days, QC rework %

### Activity Log (Audit Trail) — SUPERADMIN only  ·  `/admin/activity-log`
- **Design file:** none (no design yet)  ·  **Status:** Done
- **Frontend:** `frontend/src/pages/admin/ActivityLogPage.tsx`
- **Backend:** `settings.py (GET /audit-logs)`, `stores.py (GET /stores, /stores/{id}/users)`, `entities.py (GET /entities)`
- **MUST-PRESERVE features (10):**
  - Header: 'User Activity Log' with eyebrow 'Audit Trail · Superadmin' + 'Ask JARVIS' link (to /jarvis)
  - Filter bar (grid 1-6 cols responsive): User dropdown (all users from roster, label: 'name · role'), Organization dropdown (entity_id filtered), Store dropdown (store_code filtered, narrows per org picked)
  - Date range inputs: From (date), To (date), Action text input (optional, e.g. 'UPDATE', 'LOGIN')
  - Date presets: Today, 7 days, 30 days (buttons)
  - Refresh button (with spinner icon when loading)
  - Summary line: 'N action(s) by [user] at [store] in [org] · start-date → end-date'
  - Results table: When, User, Action (badge with tone color: CREATE=green, UPDATE=blue, DELETE=red, VIEW=gray, LOGIN=green, LOGOUT=gray, EXPORT=purple, APPROVE=green, REJECT=red), On (entity_type: entity_name), Store, expand icon
  - Expandable detail row per log entry: User ID, IP Address, Timestamp, Severity, Changes (field: old_value → new_value in strikethrough/green diff), Description JSON
  - Load more button (pagination, PAGE_SIZE=100)
  - Access control: shows 'Superadmin only' message for non-SUPERADMIN users

## Settings/Org/Users + Print templates + Jarvis/AI + Online Store + Auth/login

_Comprehensive design-parity inventory for IMS 2.0 Settings/Org/Users module scope (9 screens: Login, Organization, Settings, Setup/Onboarding, Go-Live Checklist, Print Templates, Jarvis AI, Online Store + subpages, Activity Log). Every control, field, dropdown, toggle, modal, and key business rule grounded in the real codebase (routes, components, backend routers). Mapped to 3 design files (setup.html for org/users/onboarding, print.html for templates, jarvis.html for AI) + 1 undesigned (Online Store Phase 1 foundation = none yet). All features listed with validation rules, role gates, and audit trails preserved._


### Login  ·  `/login`
- **Design file:** accounts.html  ·  **Status:** Done
- **Backend:** `backend/api/routers/auth.py (POST /login endpoint with rate-limiter, geo-fence validation)`
- **Notes:** Public route (outside ProtectedRoute). Rate-limited: 5 failed IP / 15 min, 10 failed user / 30 min → 15/30 min lockout. Geo-fence check: store staff (roles 4-7) must be within 500m of assigned store. On success, sets JWT token (8h expiry). Force-change-password for seed accounts. Geolocation via navigator.geolocation.getCurrentPosition() with 10s timeout. Backend constant _SEED_DEFAULT_PASSWORD forces must_change_password=True for seed accounts.
- **MUST-PRESERVE features (11):**
  - Username or Email field (text input, required, placeholder 'Enter username or email')
  - Password field (password input, required, placeholder 'Enter password', show/hide toggle with Eye/EyeOff icons)
  - Sign In button (primary action, disabled during loading)
  - Show password toggle (Eye/EyeOff icon, state-aware aria-pressed)
  - Error message display (red alert box with AlertCircle icon, role=alert, aria-live=assertive)
  - Loading state indicator (spinner + 'Signing in...' text when isLoading=true)
  - Brand lockup image (Better Vision logo, hi-res, responsive sizing h-20/h-24)
  - Subtitle: 'Retail Operating System'
  - Geolocation capture (enabled for geo-fenced roles 4-7, location coords sent to backend)
  - Session cache clear button (Clear Cache button with RefreshCw icon, confirms before clearing localStorage/sessionStorage)
  - Focus management and accessibility (aria-required, aria-invalid, autoComplete attributes)

### Organization (Entities + Stores)  ·  `/organization`
- **Design file:** setup.html  ·  **Status:** Done
- **Backend:** `backend/api/routers/entities.py (GET /entities, POST /entities, PUT /entities/{id}, DELETE /entities/{id})`, `backend/api/routers/stores.py (GET /stores, POST /stores, PUT /stores/{id}, DELETE /stores/{id})`
- **Notes:** SUPERADMIN / ADMIN only. Replaces orphaned /settings/entities route (COUNCIL RULING §3). Hierarchical display: entity (PAN) → GSTINs (per state) → stores. Read-only GSTIN (derived server-side from entity by state). Unassigned stores surface loudly in amber alert (fail-loud, no silent data loss). Entity fields: name, entity_type (PROPRIETORSHIP/PARTNERSHIP/LLP/etc), pan, tan, cin, llpin, udyam, incorporation_date, gstins[], bank_accounts[], invoice_identity. Store fields: store_id, store_code, store_name, brand, gstin (read-only), city, state, pincode, address, phone, email, opening_time, closing_time, geo_lat, geo_lng, geo_fence_radius, enabled_categories[], store_type (RETAIL/HQ/WAREHOUSE), is_active. Validation: GSTIN format (15-char), IFSC for bank accounts, pincode (6-digit), phone (10-digit Indian). GSTINs read-only on edit (generated from entity + state).
- **MUST-PRESERVE features (14):**
  - Entity list with hierarchical tree (collapsible entity rows, Landmark + Building2 icons)
  - Add Entity button (Plus icon, 'Add entity' label, btn-primary styling)
  - Entity row: name + entity_type badge + PAN label + GSTIN count + store count + is_active status
  - Expand/collapse toggle (ChevronRight/ChevronDown)
  - Entity edit button (Pencil icon, inline action)
  - Store add button (Plus icon 'Store', per-entity inline action)
  - Unassigned Stores alert box (AlertTriangle, amber border, high visibility)
  - Unassigned stores list (if entity_id is null/absent, grouped into '_unassigned' bucket)
  - Assign to entity dropdown (multi-entity select for assigning orphan stores)
  - GSTIN list per entity (read-only, shows gstin + state_name + is_primary badge)
  - Store list per entity (StoreIcon, store_name + store_code + city + gstin + store_type badge + is_active status)
  - Store edit button (Pencil icon, per-store action)
  - Entity Modal (create/edit, see EntityCreate schema below)
  - Store Modal (create/edit, see StorePayload schema below)

### Settings (Tabbed Container)  ·  `/settings`
- **Design file:** setup.html  ·  **Status:** Done
- **Backend:** `backend/api/routers/settings.py (GET/PUT /settings/tax, /settings/invoice, /settings/printers, etc.)`, `backend/api/routers/admin.py (audit logs, system status)`, `backend/api/routers/approvals.py (approval PIN, workflows)`, `backend/api/routers/promotions.py (loyalty, discounts)`
- **Notes:** SUPERADMIN/ADMIN/STORE_MANAGER/AREA_MANAGER/CATALOG_MANAGER/ACCOUNTANT (role-gated tabs). Tab group membership (COUNCIL RULING §3): typed total map ensures no 'orphan' tab. URL sync via searchParams (tab=profile, etc.). Inline tabs (tax-invoice, printers, audit-logs, system) load data only when active. Audit logs: AuditAction enum (LOGIN/LOGOUT/CREATE/UPDATE/DELETE/EXPORT) with colour badges. Filters: by action, by date range, by user search. Audit summary: today's total_actions, logins, orders_created. Tax settings: gst_enabled, company_gstin, default_gst_rate, hsn_validation, e_invoice_enabled, e_way_bill_enabled, e_way_bill_threshold (₹value). Invoice settings: invoice_prefix, current_invoice_number, financial_year, show_logo_on_invoice, show_qr_code, default_warranty_days. Printer settings: receipt_printer_name, receipt_printer_width, label_printer_name, label_size, auto_print_receipt, auto_print_job_card, copies_per_print, qz_enabled, auto_print_stage_sticker. Policy Matrix: scoped rules (global → entity → store, last-writer-wins). Approval Workflows: per-role approval thresholds, user's approval PIN (numeric). Feature Toggles: store-specific DARK/LIGHT flags for modules.
- **MUST-PRESERVE features (26):**
  - Sidebar tabs (grouped by audience: My Account | Business & Org | Catalog & Pricing | Compliance & Finance | System & Admin)
  - Profile tab content (SettingsProfile component)
  - Business Profile tab content (SettingsProfile component)
  - Stores & Entities tab (redirect card to /organization with ExternalLink icon)
  - User Management tab content (SettingsAuth UserManagementSection component)
  - Category Master tab (CategorySection component)
  - Brand Master tab (BrandSection component)
  - Lens Master tab (LensMasterSection component)
  - Lens Catalog Enums tab (LensCatalogEnumsSection component)
  - Lens Pricing tab (LensRangePricingSection component)
  - Discount Rules tab (DiscountSection component)
  - Loyalty Programme tab (LoyaltySettingsSection component)
  - Tax & Invoice tab (inline: tax settings, invoice settings, GST/e-invoice/e-way bill toggles)
  - HSN & GST Rates tab (HsnRatesSection component)
  - TDS Rates tab (TdsRatesSection component)
  - Policy Matrix tab (PolicySchemaForm component, scoped policies)
  - Refund Policy tab (RefundPolicySection component)
  - Notifications tab (NotificationSettings component, SMS/WhatsApp templates)
  - Reminders tab (RemindersSettings component, rule-based reminders)
  - Integrations tab (IntegrationsHub component, Razorpay/Shopify/Tally/MSG91)
  - Printers tab (inline: receipt_printer_name, label_printer_name, auto_print toggles, QZ receipt printer config)
  - Approval Workflows tab (ApprovalWorkflows component, set approval PIN, configure thresholds)
  - AI Agents tab (AgentControlPanel component, agent toggles, SUPERADMIN-only)
  - Feature Toggles tab (FeatureToggles component, per-store feature ON/OFF, SUPERADMIN-only)
  - Audit Logs tab (inline: action filter dropdown, date range, search, table of AuditLogEntry rows)
  - System tab (inline: database status, API status, version, backup status)

### Employee Onboarding Wizard (Setup Page)  ·  `/setup`
- **Design file:** setup.html  ·  **Status:** Done
- **Backend:** `backend/api/routers/users.py (POST /users endpoint, create_user with all fields)`, `backend/api/routers/stores.py (POST /users/{id}/assign-store endpoint)`
- **Notes:** STORE_MANAGER / AREA_MANAGER / ADMIN / SUPERADMIN only (gated by can_assign_roles). COUNCIL RULING §5: repurposes SetupPage into Employee Onboarding wizard over the mature /users endpoint (no new endpoint). Friendly ROLE_CATALOGUE: 11 roles listed plain-English (Sales Staff, Sales Cashier, Cashier, Optometrist, Workshop Staff, Store Manager, Accountant, Catalog Manager, Area Manager, Admin, Superadmin). Role level (ROLE_HIERARCHY) caps assignable roles to creating admin's level - a STORE_MANAGER can only assign SALES_STAFF/SALES_CASHIER/CASHIER/OPTOMETRIST/WORKSHOP_STAFF (roles below SM). geoFenced flag on roles 4-7 (store staff) auto-applied. Stores read-only (COUNCIL RULING §3). Onboarding creates user with must_change_password=True (no skip toggle). randomTempPassword() generates 12-char random from alphanumeric + no-confusing chars (I/l/O/0, etc). Govt-ID validation: PAN AAAAA9999A regex (fail-soft, doesn't block), Aadhaar 12 digits (strips spaces/hyphens). Document upload: accepts PDF/JPG/PNG/HEIC/WebP, max 25 MB, client-side + server-side MIME check. GridFS storage happens AFTER user is created (uses new employee_id). Form state: NewEmployee interface with all fields (name, email, phone, photoDataUrl, roles[], assignedStores[], primaryStore, username, tempPassword, aadhaarNo, panNo, uanNo, esicNo). Submit flow: POST /users → successful response carries new user_id → POST /users/{id}/assign-store per accessibleStores[]. Role-above-actor attempt surfaces backend 403 reason. Default password never ships in frontend (BUG-132).
- **MUST-PRESERVE features (25):**
  - Step 1: Who (name, Indian mobile, email, photo upload via Camera/Upload icons)
  - Step 2: Role(s) selection (friendly role names, plain-English descriptions, single default + 'Advanced' reveal for multi-role)
  - Step 3: Store assignment (defaults to admin's active store, multiple stores selectable, geo-fence note for roles 4-7)
  - Step 4: Permissions (placeholder, read-only 'Uses standard <Role> permissions' card)
  - Step 5: Credentials (username + temp password, copyable handoff card, must_change_password=True preserved)
  - Step 6: Documents upload (staged docs: AADHAAR, PAN, UAN_PF, ESIC, RESUME, PHOTO slots)
  - Progress indicator (step N/TOTAL_STEPS = 6)
  - Previous/Next buttons (ChevronLeft/ChevronRight, disabled appropriately)
  - Name field (text input, required)
  - Mobile field (Indian mobile, 10-digit, required, validates 6-9 start)
  - Email field (required, EmailStr validation)
  - Photo field (optional, data URL preview, Camera/Upload picker buttons)
  - Role list (ROLE_CATALOGUE: 11 roles, level hierarchy, geoFenced flag)
  - Multi-role 'Advanced' toggle (reveals secondary roles beyond primary)
  - Store selector (dropdown, shows store_name + store_code, auto-defaults to admin's store)
  - Permissions card (read-only, green CheckCircle badge, no editable overrides yet)
  - Username field (auto-generated or admin-set, validated 3-50 chars)
  - Temp password field (generated randomTempPassword() or admin custom, copyable Copy button)
  - Show password toggle (Eye/EyeOff icon on password reveal)
  - Govt-ID fields (aadhaarNo, panNo, uanNo, esicNo, all optional with fail-soft format hints)
  - Document slot pickers (single-file: AADHAAR, PAN, UAN_PF, ESIC, RESUME, PHOTO)
  - Document upload area (drag-drop + file input, status badges: staged/uploading/done/error, error message)
  - Trash icon for document removal (Trash2 icon, removes staged doc from array)
  - Cancel button (exits wizard, discards form state)
  - Create/Submit button (POST /users then POST /users/{id}/assign-store per store)

### Go-Live Readiness Checklist  ·  `/go-live`
- **Design file:** setup.html  ·  **Status:** Pending
- **Backend:** `backend/api/routers/admin.py (GET /go-live/checklist, POST /go-live/sign-off)`
- **Notes:** SUPERADMIN / ADMIN only. Foundation data checks: entities, GSTINs, stores, vendors, products (min counts). Tax/Compliance: GST registration, invoicing setup, PF/ESI if payroll enabled. Integrations: Razorpay (payments), Tally (accounting), Shopify (optional for BVI). Staff: min user count, role coverage. Finalisation: MongoDB/GridFS health, CREDENTIAL_ENCRYPTION_KEY + reset seed passwords, data backup timestamp. Sign-off cascades to a backend flag (store/entity cannot go live until owner confirms). No actual 'live switch' yet (Phase 2 work).
- **MUST-PRESERVE features (9):**
  - Checklist sections (Foundation Data, Tax/Compliance, Integrations, Staff, Finalisation)
  - Checkbox items per section (each with completion status badge)
  - Item status display (unchecked, checked, warning, skipped)
  - Completion percentage (visual progress bar or numeric %)
  - Item detail panel (hover/click to reveal sub-tasks, validation rules, required docs)
  - Sign-off signature field (admin sign-off with timestamp)
  - Go-live date picker (optional, future-guarded)
  - Final submit button (Launch Live, disabled until all critical items checked)
  - Warning banner for incomplete critical sections (AlertTriangle icon, amber background)

### User Management (Settings Auth Tab)  ·  `/settings (tab=users)`
- **Design file:** setup.html  ·  **Status:** Done
- **Backend:** `backend/api/routers/users.py (GET /users, POST /users, PUT /users/{id}, DELETE /users/{id}, POST /users/{id}/assign-store)`
- **Notes:** Role filtering: SUPERADMIN sees all, ADMIN sees all except SUPERADMIN, STORE_MANAGER sees only users from their managed stores with roles below SM level. can_manage_user() check: currentUserRoleLevel > targetRoleLevel. Delete guard: cannot delete self (u.id !== user?.id). Discount cap: per-user override capped at role baseline via effective_discount_cap(). Module access: deny-only per-user override (moduleAccess: {key: bool}). Permissions: two-sided capability override (grant/deny). Phone validation via validatePhone(). Multi-role support: roles[], not single role.
- **MUST-PRESERVE features (11):**
  - User list table (columns: User | Roles | Stores | Discount Cap | Status | Actions)
  - User row: fullName + email (secondary line), role badges (colour-coded), store codes (truncated +N more), discount cap % bold, Active/Inactive badge, Edit/Delete buttons
  - Add User button (Plus icon, btn-primary)
  - Edit user button (Pencil icon, inline per row, role-gated: can_manage_user check)
  - Delete user button (Trash2 icon, inline per row, requires confirmation, role-gated)
  - Disabled actions for higher-level users (Edit/Delete greyed out, title='Cannot edit higher-level users')
  - User Modal (create/edit, see UserModal below)
  - Available Roles Reference (flex-wrap pill badges showing assignable roles per admin level)
  - Assignable Roles caption (STORE_MANAGER shows limited roles, ADMIN/SUPERADMIN show all)
  - Loading spinner (Loader2 icon + text while loading users/stores)
  - Empty state message (role-aware: 'No staff members found' vs 'No users found')

### User Modal (Create/Edit)  ·  `Modal within /settings tab=users`
- **Design file:** setup.html  ·  **Status:** Done
- **Backend:** `backend/api/routers/users.py (POST /users, PUT /users/{id})`
- **Notes:** FormData shape mirrors UserData interface. Phone validation: validatePhone(). Roles capped by ROLE_HIERARCHY[actor] (SM can only assign roles below SM). Module access persisted as moduleAccess field (DENY-ONLY). Permissions persisted as permissions field (grant/deny dicts, escalation-guarded server-side). On create, requires: username (3-50), email (EmailStr), password (8-72 chars, bcrypt limit), full_name. On edit, password optional (preserves existing if blank). Submit via adminUserApi.createUser() or adminUserApi.updateUser().
- **MUST-PRESERVE features (10):**
  - Form fields: username, email, fullName, phone, roles (multi-select), accessibleStores (multi-select), primaryStore, discountCap (%), isActive toggle
  - Password field (for new users, optional for edits)
  - Module Access checkboxes (per module, deny-only toggle)
  - Permissions editor (two-sided grant/deny capability checkboxes)
  - Submit button (Save / Create User)
  - Cancel button (close modal without saving)
  - Title: 'Create User' or 'Edit User'
  - Role multi-select (capped by role hierarchy, shows ASSIGNABLE_ROLES for current user's level)
  - Store multi-select (shows available stores, current user's stores if STORE_MANAGER)
  - Discount cap input (number, 0-100, None = role default)

### Print Templates Index  ·  `/print`
- **Design file:** print.html  ·  **Status:** Done
- **Backend:** `backend/api/routers/print_documents.py (GET /print/templates, GET /print/templates/{key}, POST /print/templates/{key}/preview)`, `backend/api/routers/print_overrides.py (GET/PUT /settings/print-overrides/{key})`
- **Notes:** Phase 1.8 foundation: discovery surface, not a competing renderer. 8 core templates live (invoice, rx, job, token, challan, credit, dayend Z-report, thermal receipt). Each triggers print in its real context (POS → invoice/thermal, Clinical → rx/token, Workshop → job card, Inventory → challan, Returns → credit). Templates have editKey when wired to PrintTemplateContentEditor (tax_invoice, rx_card, job_card, z_report, thermal_receipt are editable; others not yet). Staff header vs customer-facing header flag (staffHeader: bool). Metadata tracks last edit timestamp + 30-day usage count (for adoption metrics). No live template editing (pixel-perfect inspector + editing is Phase 2). Fields map to data sources (POS step 1, Clinical, GST engine, etc.) for discovery.
- **MUST-PRESERVE features (13):**
  - Template cards (grid layout, 3-col on desktop, responsive)
  - Template card: icon + name + sub (size/kind) + fields list + usage metadata + action CTA
  - Template icon (FileText, Eye, Package, Ticket, Receipt, RotateCcw per template)
  - Template name (e.g., 'Tax invoice', 'Prescription card', 'Lens job card')
  - Template sub (A4/A5/80mm + kind description)
  - Fields list (read-only, [key, source] pairs, e.g., ['invoice.number', 'system'], ['cust.*', 'POS step 1'])
  - Usage metadata (lastEdited: '12 Apr 2026', usage30d: 1284, formatted)
  - Trigger action (route link or inline helper label)
  - CTA button (Open / View or route link with ExternalLink icon)
  - Customise Content button (Pencil icon, per-template editKey, SUPERADMIN/ADMIN-only)
  - Template list (8+ templates: invoice, rx, job, token, challan, credit, dayend, thermal, grn, po, dayend-summary, etc.)
  - Live preview pane (right side, shows sample print output in selected template)
  - Inspector pane (inspects template structure, fields, overrides)

### Jarvis AI Control Interface  ·  `/jarvis`
- **Design file:** jarvis.html  ·  **Status:** Done
- **Backend:** `backend/api/routers/agents.py (GET /jarvis/agents, GET /jarvis/agents/{id}/status, PUT /jarvis/agents/{id}/toggle, PUT /jarvis/agents/{id}/config, GET /jarvis/agents/diagnostic, GET /jarvis/models)`, `backend/api/routers/jarvis.py (POST /jarvis/chat, GET /jarvis/insights, GET /jarvis/agents/activity)`
- **Notes:** SUPERADMIN EXCLUSIVE (strict isStrictSuperAdmin check). ADMIN can see Recommended POs section (poProposals). 8 agents: JARVIS (core, not toggleable), CORTEX (core, not toggleable), SENTINEL (60s interval, toggleable), PIXEL (daily 2 AM + on deploy, toggleable), MEGAPHONE (30 min + daily 9 AM, toggleable), ORACLE (hourly + 10 PM, toggleable), TASKMASTER (5 min, toggleable), NEXUS (hourly + webhook, toggleable). Each agent has: agent_id, agent_name, description, version, enabled (toggle state), toggleable (bool, false for core), status (running/sleeping/stopped/error/starting), health (healthy/degraded/unhealthy/unknown), schedule_type (interval/cron/event), schedule_value (seconds/cron-expr/descriptor), last_run (ISO8601 or null), last_status (success/error), last_error (message or null), run_count, error_count, avg_run_time_ms, hero (comic identity), capabilities (list). Chat: message history per session (persisted in React state, not backend). LLM models from /jarvis/models (id, label, tier). Premium model confirm modal surfaces rough cost (~$15/1M input, ~$75/1M output tokens = ₹40-80/query). Activity feed: filter by kind (all/anomalies/sync_runs/task_execution/notifications/ui_audits), severity badge (CRITICAL/HIGH/MEDIUM/LOW), recommended_action field. AI Proposals: from proposalsApi, approve/reject with reason. PIXEL audits: summary scores (performance, accessibility, best_practices, seo), Core Web Vitals (LCP ms, CLS, TBT ms), per-page a11y violations count, regressions vs baseline. SENTINEL health: latest score, checks per domain with response_time_ms. Hook: auto-scroll only when user near bottom (autoFollowRef).
- **MUST-PRESERVE features (21):**
  - Quick Insights card (revenue_today, revenue_growth %, orders_today, pending_orders, low_stock_count, staff_present, top_recommendation)
  - Agent grid (8 live agents: JARVIS, CORTEX, SENTINEL, PIXEL, MEGAPHONE, ORACLE, TASKMASTER, NEXUS)
  - Agent card: agent_id + agent_name + description + version + status badge + health badge + schedule_type + last_run timestamp + toggle switch (toggleable: bool, disabled for core agents JARVIS/CORTEX)
  - Agent toggle switch (enabled/disabled state, onClick toggleAgent with optimistic UI, togglingId loading state)
  - Agent health colour (healthy=green, degraded=yellow, unhealthy=red, unknown=gray)
  - Agent status indicator (running/sleeping/stopped/error/starting, icon + label)
  - Hero identity display (comic-book hero name, cosmetic only)
  - Capabilities list (array of strings, displayed as badges)
  - Agent metrics (run_count, error_count, avg_run_time_ms, displayed as 'Ran 42 times, 3 errors, avg 234ms')
  - Chat interface (message history, user input field, Send button, Mic button for voice input)
  - LLM model selector (dropdown, shows models from /jarvis/models endpoint, tier labels: free/standard/premium, confirm modal for premium model cost)
  - Chat messages (user message align-right, Jarvis reply align-left, timestamp, data payload rendering)
  - Activity feed (Phase 5, unified activity from /jarvis/agents/activity endpoint)
  - Activity filter dropdown (all/anomalies/sync_runs/task_execution/notifications/ui_audits)
  - Activity event display (agent_id, kind, timestamp, summary, severity badge, recommended_action, details expandable)
  - AI Proposals section (SUPERADMIN: change proposals from ai_agents work, approve/reject buttons with proposalBusyId loading)
  - Recommended POs section (ADMIN/SUPERADMIN: pending reorder draft-PO suggestions from #7 predictive purchasing)
  - PIXEL audit history (last N ui_audits, pagespeed_ready flag, audit runs with summary: overall_min_perf, overall_min_a11y, pages_audited, per-page scores)
  - SENTINEL system health (latest health score, results by domain: database/api/frontend/agents/data_integrity, history graph, alerts list)
  - Configuration options (SUPERADMIN-only: schedule_type, schedule_value, config_overrides, save button)
  - Status indicator (status field from agent, colour-coded icon + label)

### Online Store (Module Shell)  ·  `/online-store`
- **Design file:** none (no design yet)  ·  **Status:** Pending
- **Backend:** `backend/api/routers/online_store.py (GET /online-store/summary endpoint, fail-soft counts)`, `backend/api/routers/online_store_collections.py (GET /collections, POST, PUT, DELETE)`, `backend/api/routers/online_store_menus.py (GET /menus, POST, PUT, DELETE)`, `backend/api/routers/online_store_images.py (GET /images, POST design-queue status)`, `backend/api/routers/online_store_orders.py (GET /orders, store-scoped)`
- **Notes:** SUPERADMIN / ADMIN / CATALOG_MANAGER / DESIGN_MANAGER only (router-level gate via require_roles). Phase 1 foundation only: skeleton + stub summary endpoint. Planned features (BVI_MERGE_PLAN §B roadmap): Phase 1 variant identity + Shopify mapping (foundation); Phase 2 collections (editor live at /online-store/collections); Phase 3 menus (editor live at /online-store/menus) + online orders (view live at /online-store/orders); Phase 4 image design queue (live at /online-store/images); Phase 5 stock tally; Phase 6 Shopify push. GET /online-store/summary returns: module status, phase, db_connected, shopify_writes_enabled, planned_features[], counts (products with ecom sub-doc, catalog_variants, ecom_collections, ecom_menus, product_images QUEUED, customers with shopify_customer_id, orders with channel=ONLINE). Counts fail-soft to 0 if collections don't exist. SSO handoff via ecommerceSsoApi.getUrl() (POST /ecommerce/sso-url, server-to-server single-sign-on to BVI app at VITE_ECOMMERCE_URL). No design yet (Phase 1 is discovery surface).
- **MUST-PRESERVE features (9):**
  - Module title + phase info (BVI Phase 1 foundation)
  - Section cards grid (9 planned sections: Products/PIM, Collections, Menus, Images, Customers, Orders, Stock tally, Store health, Shopify sync)
  - Section card: icon + title + blurb + phase label + status pill (Live/Coming soon)
  - Live count display (countKey + countLabel, e.g., '1,284 products', '312 collections')
  - Open button (href link to in-app screen, ExternalLink icon, or 'Coming soon' pill for planned-only sections)
  - Current admin link (SSO handoff to external BVI app, 'View in Shopify Admin', ExternalLink icon, fallback to ecommerceUrl)
  - Sync banner (OnlineStoreSyncBanner component, status + last-run timestamp)
  - Database connection status (db_connected: bool, displayed in summary metadata)
  - Shopify writes enabled flag (shopify_writes_enabled: bool, should default OFF/gated)

### Collections Editor  ·  `/online-store/collections`
- **Design file:** none (no design yet)  ·  **Status:** Pending
- **Backend:** `backend/api/routers/online_store_collections.py`
- **Notes:** Phase 2 shipped: collections editor is live in-app. Manual collections (hand-curated items) + smart collections (rule-based auto-membership). SEO fields: title, description, url slug. Browse endpoint (GET /collections, materialized membership fast-path). Same role gate as module shell.
- **MUST-PRESERVE features (6):**
  - Collection list (table or card grid, name + item count + rule-based status + edit/delete actions)
  - Add Collection button (Plus icon)
  - Collection form (name, description, rules for smart collections, manual items selection)
  - Rule builder (if/then logic for auto-membership, e.g., 'brand=Cartier' OR 'category=SUNGLASS')
  - Collection edit button (inline Pencil icon)
  - Collection delete button (inline Trash2 icon, confirm)

### Menus / Mega-menu Editor  ·  `/online-store/menus`
- **Design file:** none (no design yet)  ·  **Status:** Pending
- **Backend:** `backend/api/routers/online_store_menus.py`
- **Notes:** Phase 3 shipped: mega-menu editor is live in-app. Visual editor for storefront navigation tree. Nested structure: root level has sections (e.g., 'Frames', 'Sunglasses', 'Accessories'), each can have sub-items (e.g., 'Ray-Ban', 'Cartier'). Pin-to-top control. Thumbnails + badges per item for richer UX on the storefront.
- **MUST-PRESERVE features (8):**
  - Mega-menu tree editor (nested items, drag-reorder, collapse/expand)
  - Menu item: label + type (category/collection/custom link) + optional thumbnail + optional badge
  - Add menu item button (Plus icon, per-level)
  - Edit menu item (inline Pencil icon, name/type/thumbnail/badge/url fields)
  - Delete menu item (inline Trash2 icon)
  - Save button (persists menu structure)
  - Pin-to-top toggle (per item, pins section to top of mega-menu)
  - Thumbnail upload (optional image per menu item)

### Image Design Queue  ·  `/online-store/images`
- **Design file:** none (no design yet)  ·  **Status:** Pending
- **Backend:** `backend/api/routers/online_store_images.py (GET /images, PATCH /images/{id}/approve, PATCH /images/{id}/reject)`
- **Notes:** Phase 4 shipped: image design queue is live in-app. Workflow: raw photo → edited hero image, role-gated per-image design status. Design team submits RAW, DESIGN_MANAGER/ADMIN reviews + approves (moves to COMPLETED) or rejects (requeues). Timestamps + audit trail.
- **MUST-PRESERVE features (9):**
  - Image queue table (columns: product name + sku | current_image | design_status + due date | designer | actions)
  - Image status badges (QUEUED=orange, IN_PROGRESS=blue, COMPLETED=green, REJECTED=red)
  - Image preview (thumbnail in table or expand modal)
  - Assign to designer dropdown (role-gated to DESIGN_MANAGER/ADMIN)
  - Approve button (Approve image, ADMIN/DESIGN_MANAGER-only, role-gated in-page)
  - Reject button (Reject + requeue, with reason text)
  - Design status history (timestamps + actor)
  - Filter by status (dropdown: All / Queued / In Progress / Completed / Rejected)
  - Sort by due date / designer / product

### Online Orders  ·  `/online-store/orders`
- **Design file:** none (no design yet)  ·  **Status:** Pending
- **Backend:** `backend/api/routers/online_store_orders.py (GET /orders, store-scoped, POST /orders/{id}/remap)`
- **Notes:** Phase 3b shipped: online orders view is live in-app. Read-only list of storefront orders flowing into IMS books as they happen. Customer upsert on order receipt (matched by phone/email to IMS customer, carrying shopify_customer_id). Stock decrement automatically. Tagged channel='ONLINE'. Remap action handles Shopify variant → IMS product SKU collisions (if same SKU ordered but variant_id different, admin chooses which IMS product to charge). Store-scoped: users see only their store's online orders.
- **MUST-PRESERVE features (7):**
  - Online orders table (columns: order_id | customer | order_date | amount | status | items count | remap action)
  - Order status badges (PENDING/PROCESSING/SHIPPED/DELIVERED/CANCELLED)
  - Remap button (Re-map action, in-page role-gated to SUPERADMIN/ADMIN, maps Shopify → IMS product mapping if collision)
  - Order detail (expand row or modal: items list, customer contact, shipping address, payment method)
  - Customer profile link (navigate to customers/360 for this customer)
  - Filter by status (dropdown)
  - Date range filter (from/to dates)

### Activity Log (Admin)  ·  `/admin/activity-log`
- **Design file:** setup.html  ·  **Status:** Pending
- **Backend:** `backend/api/routers/audit.py (GET /audit/logs, filter by action/user/entity_type/date)`
- **Notes:** SUPERADMIN / ADMIN only. Immutable audit trail (even SUPERADMIN cannot delete). Timestamp: ISO8601 UTC. user_id + user_name. action: LOGIN/LOGOUT/CREATE/UPDATE/DELETE/EXPORT (enum). entity_type (optional): PRODUCT/USER/ENTITY/STORE/ORDER/etc. entity_id (optional): if action is on a specific doc. changes (optional): what changed (for UPDATE). ip_address: request IP. Summary stats: today's total_actions, logins, orders_created. Audit logs live in a separate immutable collection (audit_logs or similar), indexed by timestamp + user_id + action for fast filtering.
- **MUST-PRESERVE features (9):**
  - Activity log table (columns: timestamp | user_name | action | entity_type | details | ip_address)
  - Action badges (LOGIN=purple, LOGOUT=gray, CREATE=green, UPDATE=blue, DELETE=red, EXPORT=amber, with label text)
  - Row background colour (CREATE=green-50/40, DELETE=red-50/40, others=white)
  - Search by user/action/entity (search box + filter state)
  - Date range filter (from/to date inputs, Calendar icon)
  - Action filter dropdown (AuditAction enum: LOGIN/LOGOUT/CREATE/UPDATE/DELETE/EXPORT, '', 'All')
  - Clear filters button (resets all filters)
  - Pagination or infinite scroll (depends on data size)
  - Export button (CSV export of filtered log)

---

## Design-file coverage

- Total screens inventoried: **99**
- Design files mapped to >=1 screen: accounts.html, clinical.html, hub.html, inventory.html, jarvis.html, pos.html, pricing.html, print.html, reports.html, setup.html, tasks.html
- **Design files with NO mapped screen (flag):** none — all 11 used
- Screens with **no design file** are marked `none (no design yet)` in the tables above — reskin those only when a design is produced.
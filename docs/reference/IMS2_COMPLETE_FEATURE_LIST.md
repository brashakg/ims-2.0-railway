# IMS 2.0 — COMPLETE FEATURE LIST
## Everything Avinash Requested Across All Conversations

> **WARNING: STALE — superseded by `IMS2_Updated_Feature_Status.md` (2026-05-29).**
> The vast majority of items listed "Not Yet Built" (❌) **are BUILT** — Payroll,
> Finance & Accounting, Tasks/SOP escalation, Expenses, GST export, AI inventory
> analytics, clinical queue, workshop QC, print templates, audit viewer,
> store-credit ledger, Purchase/Procurement, and BVI Online Store are all complete
> and merged to main. The summary count of "117 built / 166 remaining" is
> substantially wrong (real count is ~245–260 built). **Verify in code, not this
> list.** See `IMS2_Updated_Feature_Status.md` for the reconciled picture.

Status: ✅ Built | 🔧 Partially Built | ❌ Not Yet Built

---

## MODULE 1: DASHBOARD (Role-Based)

### Staff Dashboard (Sales/Cashier)
- ✅ My sales count + value today
- ✅ Pending orders count
- ✅ Quick action buttons (New Sale, My Orders)
- 🔧 Task list with priority colors (structure exists, real tasks need wiring)
- ❌ Pending deliveries for my assigned customers
- ❌ Target vs achievement meter (daily/monthly)
- ❌ Reminders to call customers for order updates

### Store Manager Dashboard
- ✅ Store total sales / orders / avg bill
- ✅ Staff performance table (sales per person)
- 🔧 Inventory alerts (low stock, pending transfers)
- ❌ Daily stock count status
- ❌ Task checklist completion percentage
- ❌ Eye test count today
- ❌ Store vs target graphical representation

### Area Manager Dashboard
- ✅ Multi-store sales comparison
- ✅ Store-wise performance table
- 🔧 Cross-store inventory visibility
- ❌ Escalations requiring attention
- ❌ Staff attendance compliance across stores

### Admin Dashboard (HQ)
- ✅ Enterprise-wide revenue + orders + footfall
- ✅ Financial summary (revenue, outstanding, expenses)
- 🔧 Catalog approval queue
- ❌ Pending HQ escalations
- ❌ HR summary (attendance compliance, pending leaves)
- ❌ System health monitoring

### Superadmin Dashboard (CEO)
- ✅ Executive KPIs with period selector
- ✅ All store visibility
- 🔧 AI Intelligence access panel
- ❌ AI insights summary (recommendations, pattern detections)
- ❌ System governance card (audit logs, config changes)
- ❌ Growth metrics / MoM / YoY charts

### Optometrist Dashboard
- ✅ Quick link to Eye Test Queue
- ✅ Quick link to Prescriptions
- ❌ Today's eye test count
- ❌ Patient queue with wait times
- ❌ Prescription redo rate tracking

### Accountant Dashboard
- ❌ Financial summary (revenue, expenses, GST collected/paid)
- ❌ GST filing status
- ❌ Pending reconciliations
- ❌ Expense approval queue

### Catalog Manager Dashboard
- ❌ Total SKUs / Active / Pending activation
- ❌ Price change requests pending
- ❌ Recent catalog activity log

---

## MODULE 2: POS (Point of Sale)

### Core Sale Flow
- ✅ 6-step wizard: Customer → Prescription → Products → Review → Payment → Complete
- ✅ Quick Sale mode (skip Rx step)
- ✅ Prescription Order mode (frame + lens with Rx)
- ✅ Walk-in customer (auto-creates, Quick Sale only)
- ✅ Customer search with AutoSearch dropdown (store-scoped)
- ✅ Product search by name/brand/SKU with category filter
- ✅ Barcode scanner input
- ✅ Grid view + compact list view for products
- ✅ Cart sidebar with quantity adjust + remove
- ✅ Item-level notes (PD, fitting, tint, coating)
- ✅ Order notes
- ✅ MRP vs Offer Price enforcement (hard block if offer > MRP)
- ✅ Role-based discount with modal (%, ₹ amount)
- ✅ Category-based discount logic (luxury caps at 2%)
- ✅ GST per category (12% lenses, 18% frames/watches)
- ✅ CGST + SGST split display in Review
- ✅ Incentive auto-tagging (Zeiss/Safilo kickers)

### Payment
- ✅ Full Cash / UPI / Card / Bank one-click buttons
- ✅ Split payments (multiple methods)
- ✅ Non-cash requires transaction reference
- ✅ Advance payment mode (partial pay, balance on delivery)
- ✅ Delivery date picker for Rx orders
- ✅ Cash change calculator with quick-fill buttons
- ✅ Payment balance display (GST-inclusive total)
- ❌ EMI payment option
- ❌ Credit billing for known customers
- ❌ Gift card / voucher redemption at POS

### Post-Sale
- ✅ Receipt preview (thermal + A4)
- ✅ GST Tax Invoice with GSTIN, HSN codes, amount in words
- ✅ Print invoice (popup + iframe fallback)
- ✅ New Sale button (resets with store context preserved)
- ✅ Order confirmation with order number

### Hold / Recall
- ✅ Hold current bill to localStorage
- ✅ Recall panel with held bill list
- ✅ Shows customer, items, total, time held
- ✅ Delete held bill
- ✅ Badge count on Recall button

### Keyboard Shortcuts
- ✅ F2 → Search products
- ✅ F4 → Hold bill
- ✅ F9 → Jump to payment
- ✅ Enter → Next step
- ✅ Ctrl+Enter → Complete order (payment step)
- ✅ Escape → Back one step
- ✅ Shortcuts disabled when typing in inputs

### iPad/Tablet
- ✅ 100dvh viewport
- ✅ Floating cart FAB on mobile
- ✅ 44px min touch targets
- ✅ Safe area padding
- ✅ Icon-only buttons below tablet breakpoint
- ✅ Responsive cart sidebar width

### Customer in POS
- ✅ Customer purchase history (last 5 orders with time ago)
- ✅ Create customer inline (name, phone, email validation)
- ❌ Customer loyalty points display
- ❌ Previous Rx summary on customer card
- ❌ "Last bought: Ray-Ban RB3025, 8 months ago" display

---

## MODULE 3: ORDERS

- ✅ Order list with search, status filter, date filter
- ✅ Order detail view (items, payments, taxes)
- ✅ Collect payment modal (balance due, Cash/UPI/Card/Bank)
- ✅ Print invoice from order list (popup + iframe fallback)
- ✅ Search dropdown with auto-suggestions
- ✅ Status badges (Draft, Confirmed, In Progress, Ready, Delivered)
- ❌ Mark order as DELIVERED
- ❌ Order timeline/history (status changes with timestamps)
- ❌ Customer notification on status change (WhatsApp/SMS)
- ❌ Order tracking QR code for customer

---

## MODULE 4: RETURNS & EXCHANGES

- ✅ 3 return types: Return & Refund, Exchange, Store Credit
- ✅ Order search to find original transaction
- ✅ Per-item return quantity, reason (7 predefined), condition
- ✅ Approval notes for admin visibility
- ✅ Refund amount calculation
- ✅ Completion with reference ID
- ❌ Credit note balance tracking per customer
- ❌ Exchange flow → select replacement product → adjust price
- ❌ Return to vendor workflow (defective products)

---

## MODULE 5: EYE CLINIC / CLINICAL

- ✅ Eye test queue
- ✅ New eye test form (SPH/CYL/AXIS/ADD per eye, acuity, PD)
- ✅ Prescription creation (store-tested or from-doctor)
- ✅ Prescription list with search
- ✅ Test history with filters
- ✅ Contact lens fitting page
- ✅ Prescription flows to POS (select during Rx order)
- 🔧 Lens suggestion panel (AI-powered recommendations)
- ❌ Prescription validity setting per optometrist
- ❌ Prescription printout (A5 card format)
- ❌ Eye test token/queue number system
- ❌ Prescription redo tracking (link to original Rx)
- ❌ Clinical abuse detection (copy-paste Rx, high redo rates)
- ❌ Family-based prescription view (1 customer → multiple patients)

---

## MODULE 6: WORKSHOP

> **Note (2026-06-17):** Most items below marked ❌ are now BUILT. See `IMS2_Updated_Feature_Status.md`.

- ✅ Job list with status/priority filters
- ✅ Job detail view
- ✅ Create job from order (order search → frame/lens extraction → priority/date/notes)
- ✅ Job status pipeline (Created → Lens Ordered → In Progress → QC → Ready → Delivered)
- ✅ Assign job to technician
- ✅ Workshop dashboard KPIs — `GET /workshop/dashboard-kpis` (pending, overdue, completed today, avg turnaround)
- ✅ Lens order tracking (`jobs/{id}/lens-status`)
- ✅ QC checklist (`jobs/{id}/qc`, `/rework` — backend built; UI wiring thin)
- ✅ Customer notification when job is ready (`jobs/{id}/notify-ready` — gated on `DISPATCH_MODE=live`)
- ✅ Workshop job linked back to order + prescription

---

## MODULE 7: INVENTORY

> **Note (2026-06-17):** Most items below marked ❌ are now BUILT. See `IMS2_Updated_Feature_Status.md`.

- ✅ Stock overview with category tabs (7 categories)
- ✅ Product search with auto-suggestions (store-scoped)
- ✅ Low stock alerts
- ✅ Reorder dashboard
- ✅ Stock transfer modal (create transfer between stores)
- ✅ Stock transfer management (list, approve, pick, ship)
- ✅ Stock audit initiation
- ✅ Serial number tracking
- ✅ Stock aging report
- ✅ Barcode management
- ✅ Stock movement history
- ✅ Daily stock count (`stock-count/start|items|complete`) with barcode scanning (`stock-count-scan`)
- ✅ Auto-variance / shrinkage detection (`accountability/shrinkage`)
- ✅ Non-moving stock identification (`/inventory/non-moving`, 90+ days no sale)
- ✅ AI-recommended inter-store transfer (`transfer-recommendations`)
- ✅ Sell-through % per brand group (`sell-through-analysis`, `reports/inventory/brand-sellthrough`)
- ✅ Contact lens batch/expiry tracking grid (`contact-lenses/expiry-status`)
- ✅ Power-wise lens stock grid (`lenses/power-grid`, SPH x CYL matrix)
- ✅ Stock dump / overstock analysis (`overstock-analysis`)
- ✅ Staff-assigned stock accountability (`accountability`)
- ❌ Stock photo gallery per product (possible gap — not confirmed built)

---

## MODULE 8: PRODUCT CATALOG (HQ)

- ✅ Add product wizard
- ✅ 12 product categories with mandatory fields per category
- ✅ SKU generation logic per category prefix
- 🔧 Category attribute model (defined, not yet enforced in UI)
- ❌ Product photography workflow at HQ warehouse
- ❌ Bulk product upload (CSV/Excel)
- ❌ MRP + Offer Price setting with validation
- ❌ Store-wise product activation (enable/disable per store)
- ❌ Barcode generation at store level (after catalog acceptance)
- ❌ Location code on barcode (C1 = Counter 1, D3 = Display 3)
- ❌ Mismatch escalation (store reports wrong product → HQ notification)
- ❌ Shopify product sync (IMS is master → push to Shopify)

---

## MODULE 9: HR & ATTENDANCE

- ✅ Attendance list with status codes (P/Absent/Late/Leave/Half Day)
- ✅ Check-in / Check-out buttons with geolocation
- ✅ Leave request and approval system
- 🔧 Attendance data from API (structure built, real data depends on usage)
- ✅ Geo-fenced check-in (enforced near assigned store; roles 1-3 exempt)
- ✅ Shift configuration per employee (name/start/end/grace/weekly-off)
- ✅ Week-off swap with manager approval (no self-approval)
- ✅ Late mark auto-calculation based on shift time + grace
- ✅ LWP report for the month (read-only; entered manually into payroll, never auto-applied)
- ⛔ Overtime tracking — intentionally NOT built (product-owner decision: no overtime)
- ✅ Monthly attendance grid view (like Excel ATTENDANCE sheet) with late + LWP columns
- ❌ Employee can view own attendance/salary/incentives on mobile

---

## MODULE 10: PAYROLL & INCENTIVES

> **Note (2026-06-17):** This entire module is BUILT. See `IMS2_Updated_Feature_Status.md`.

- ✅ Monthly salary calculation — full statutory engine (EPF/EPS/ESI/PT/TDS, LWP proration, advances)
- ✅ Advance salary tracking (taken, settled, outstanding)
- ✅ Incentive target setup wizard (per person, per month) — `points.py`, `payout.py`
- ✅ Kicker system: Zeiss SmartLife/Progressive/Photofusion + Safilo targets
- ✅ Minimum 80% target + kicker rules
- ✅ Incentive slabs (0.8% / 1% / 1.5%)
- ✅ Real-time incentive dashboard per staff (MTD leaderboard, daily scorecards)
- ✅ Payslip generation (HTML print)
- ✅ Salary sheet export (PF ECR, Tally salary JV, statutory summary)
- ❌ Google review incentive (₹25/₹50 per review) — not found in code

---

## MODULE 11: FINANCE & ACCOUNTING

> **Note (2026-06-17):** This entire module is BUILT. See `IMS2_Updated_Feature_Status.md`.

- ✅ Revenue tracking (daily/monthly/yearly with MoM/YoY) — `sales/growth`, `analytics`
- ✅ Expense heads with actual vs survival budgets — `expenses.py`, `budgets`
- ❌ Dual-mode budgeting (full ops vs survival toggled by superadmin) — the one true Finance gap
- ✅ GST filing preparation (GSTR-1, GSTR-3B) — `gstn_export.py`, `/reports/gstr1`, `/reports/gstr3b`
- 🔧 GST portal API integration (verify GSTIN) — `org_validation.py` validates locally; live portal API not wired
- ✅ Tally export integration — Tally sales-JV + salary JV (`payroll_exports.py`, `finance.py`)
- ✅ Outstanding receivables aging report — `GET /finance/outstanding`, due-date buckets
- ✅ Vendor payment tracking — `ap_engine.py`, AP aging, vendor ledger
- ✅ Profit & loss by store / by category — `/finance/pnl/by-store`, `/finance/pnl/by-category`
- ✅ Period locking (prevent edits to closed months) — `/finance/period-lock`
- ✅ Cash flow statement + forecast — `/finance/cash-flow`, `/finance/cash-flow-forecast`
- 🔧 Inter-store reconciliation — `itc_reconcile` endpoint exists; full cross-store view is a UI item

---

## MODULE 12: EXPENSES & ADVANCES

- ✅ Page structure built (route /reports/day-end has expense section)
- 🔧 Expense creation flow defined (backend has 12 endpoints)
- ❌ Expense categories with daily/monthly caps per role
- ❌ Bill upload (mandatory image/PDF with hash)
- ❌ Duplicate bill detection
- ❌ Approval hierarchy (staff → manager → finance → admin)
- ❌ Advance payment system (request, approve, settle via expenses)
- ❌ Outstanding advance blocks new advances
- ❌ Reimbursement aging report
- ❌ Expense by employee/category reports

---

## MODULE 13: TASKS, SOPs & ESCALATION

> **Note (2026-06-17):** This entire module is BUILT. See `IMS2_Updated_Feature_Status.md`.

- ✅ Task management page with status/priority
- ✅ Task creation and assignment (`{id}/assign`, `{id}/reassign`)
- ✅ System-generated tasks (auto-created on stock variance, payment, SLA breach — `auto-generate`)
- ✅ SOP-bound tasks — `sop-templates` + `sop-checklist` + daily `checklists/{type}/complete-item`
- ✅ Priority color coding P0–P4 (`TasksDashboard.PRIORITY_COLORS`)
- ✅ Time-based escalation engine — `task_sla.py` (P0–P4 SLA matrix) + `task_escalation.py` (role ladder)
- ✅ Silence detection (`tasks/integrity/silent`)
- ✅ Assign tasks through the app (in-app + WhatsApp alerts via `task_notify.py`)
- ✅ Task SLA countdown chip (frontend)
- ✅ Fake closure detection (`tasks/integrity/fake-closures`)
- ✅ Daily SOP checklists with checkboxes (`checklists/{type}/complete-item`)
- ✅ Task mutation audit trail (via `audit_logs`)

---

## MODULE 14: REPORTS

### Built
- ✅ Day-End Shift Closing Report (sales, payments, cash reconciliation, staff performance)
- ✅ Outstanding Payments Report (age buckets, search, sort, phone call link)
- ✅ Analytics dashboard (charts, KPIs)
- ✅ Sales reports tab
- ✅ Inventory reports tab
- ✅ GST reports tab

### Built (updated 2026-06-17)
> All items below were previously marked ❌ "Not Built" — all are now confirmed built.

- ✅ Daily/Monthly/Yearly sales comparison (`reports/sales/comparison`)
- ✅ MoM and YoY growth reports (`reports/sales/growth`)
- ✅ Profit by category and store (`reports/profit/by-category`, `reports/profit/by-store`)
- ✅ Discount average by category and store (`reports/discount/analysis`)
- ✅ Staff performance ranking (`reports/staff/ranking`)
- ✅ Daily stock count report (`reports/stock/count`)
- ✅ Eye test count report (`reports/clinical/eye-tests`)
- ✅ Pending jobs report (`reports/workshop/pending-jobs`)
- ✅ Expense vs revenue report (`reports/finance/expense-vs-revenue`)
- ✅ Customer acquisition/retention report (`reports/customers/acquisition`)
- ✅ Brand-wise sell-through report (`reports/inventory/brand-sellthrough`)
- 🔌 Stock movement report — `reports/inventory/brand-sellthrough` + extras; full inwards/outwards breakdown may need UI wiring

---

## MODULE 15: STORE SETUP & CONFIGURATION

- ✅ Store creation (name, code, brand, GST, address, hours, categories enabled)
- ✅ Store editing
- ✅ Employee onboarding 4-step wizard (info → roles → stores → credentials)
- ✅ Multi-role assignment per employee
- ✅ Discount cap per employee
- ✅ Shift and week-off configuration
- ✅ Store selector in header
- 🔧 User management (list/create/edit — in Settings page)
- ❌ Deep store setup (tea break times, prescription validity limits, display zone layout)
- ❌ New store setup SOP (guided, long process, max control from day 1)
- ❌ Superadmin settings dashboard (feature toggles, 3-8 layers deep per feature)
- ❌ Activity log per user (visible to superadmin)
- ❌ Approval workflow configuration
- ❌ Audit log viewer
- ❌ Integration settings (API keys, webhook URLs for Shopify/Tally/Shiprocket/WhatsApp)
- ❌ GST portal API connection settings
- ❌ Razorpay integration settings

---

## MODULE 16: AI INTELLIGENCE (Superadmin Only, READ-ONLY)

> **Note (2026-06-17):** All 8 agents are built and live. See `IMS2_Updated_Feature_Status.md`.

### Built
- ✅ Jarvis page with 8-agent grid, toggles, activity feed, diagnostic endpoint
- ✅ Chat interface ("Ask Intelligence") — JARVIS NLP core wired to Claude API
- ✅ All 8 agents: JARVIS, CORTEX, SENTINEL, PIXEL, MEGAPHONE, ORACLE, TASKMASTER, NEXUS
- ✅ Cross-agent activity feed (`GET /jarvis/agents/activity`)
- ✅ ORACLE → Claude API for anomaly narratives + EOD analytics + fraud detection
- ✅ MEGAPHONE → MSG91 WhatsApp + SMS (gated on `DISPATCH_MODE=live`)
- ✅ PIXEL → Google PageSpeed Insights (gated on `PAGESPEED_API_KEY`)
- ✅ NEXUS → Shopify/Razorpay/Shiprocket/Tally integration sync (gated)
- ✅ TASKMASTER — SLA escalation, SOP verify, auto-reorder drafts (DB-only, no external provider)
- ✅ SENTINEL — system health monitoring + Slack alerts
- ✅ Redis event bus (`event_bus.py`) for cross-worker agent event dispatch
- ✅ Sentry APM + per-agent-tick transactions (observability.py)
- ✅ `GET /jarvis/agents/diagnostic` — SUPERADMIN live introspection endpoint

### Remaining
- ❌ AI Change Proposals (SYSTEM_INTENT §8 loop: AI suggests → superadmin approves → audited execute)
- ❌ Natural language product query ("Show me gold rimless frames in Bokaro ₹5k–₹10k")
- ❌ AI image-based product search
- ❌ Marketing intelligence (Google/Meta ad performance via API)

---

## MODULE 17: MARKETPLACE & E-COMMERCE / ONLINE STORE (BVI)

> **Note (2026-06-17):** BVI Online Store (Phases 1–5) is COMPLETE. See `IMS2_Updated_Feature_Status.md`.

- ✅ Storefront page (basic structure)
- ✅ BVI Online Store (`ecommerce/` — Next.js + Prisma/Postgres) — full Shopify PIM/catalog app consolidated into IMS 2.0 Railway project (Phases 1–5 complete)
- ✅ SSO between IMS 2.0 and BVI Online Store (shared JWT)
- ✅ "Online Store" nav item in IMS left rail
- 🔧 Shopify catalog sync (code exists for `sync-shopify`, `bulk-sync-shopify` — gated on keys)
- ❌ Marketplace control panel (Amazon/Flipkart/Shopify unified view)
- ❌ Omnichannel stock sync across Amazon/Flipkart
- 🚪 Shiprocket integration — code exists (`shipping.py`) gated on `DISPATCH_MODE=live` + creds
- ❌ Multiple website management (category-specific: watches only, etc.)

---

## MODULE 18: CRM & MARKETING

- ✅ Customer 360 dashboard
- ✅ Customer segmentation
- ✅ Campaign manager
- ✅ Loyalty program page
- ✅ Referral tracker
- ✅ Customer feedback
- ❌ WhatsApp Business integration (bulk messages, order updates, prescriptions)
- ❌ Marketing agency oversight dashboard (Google/Meta ad performance)
- ❌ OTP-based customer verification (auto-opt-in to marketing)
- ❌ Prescription access portal for customers (view Rx via QR)
- ❌ Automated follow-up reminders (eye test due, frame replacement due)
- ❌ Customer purchase history summary for staff during sale

---

## MODULE 19: PURCHASE & VENDORS

- ✅ Purchase order management
- ✅ Vendor management (CRUD, GST details, billing method)
- ✅ Goods receipt note (GRN)
- ✅ Purchase order dashboard
- 🔧 Supply chain management (21 backend endpoints)
- ❌ Vendor follow-up reminders (replace WhatsApp tracking)
- ❌ Credit note tracking from vendors
- ❌ Vendor return workflow (defective → ship back → credit note or replacement)
- ❌ Vendor performance scoring
- ❌ Purchase history analytics

---

## MODULE 20: PRINT TEMPLATES

- ✅ Barcode labels (thermal 50mm + A4 sheet)
- ✅ Prescription card (A5 landscape)
- ✅ Thermal receipt (80mm)
- ✅ GST Tax Invoice (A4)
- ✅ Order print from Orders page
- ❌ Purchase order print
- ❌ GRN print
- ❌ Eye test token print
- ❌ Workshop job card print
- ❌ Delivery challan print
- ❌ Credit note print
- ❌ Estimate/Quotation print

---

## MODULE 21: THIRD-PARTY INTEGRATIONS

| Integration | Status | Purpose |
|------------|--------|---------|
| Shopify | 🔧 47 endpoints built | Omnichannel product sync, online orders |
| Tally | ❌ | Accounting export |
| Shiprocket | ❌ | Delivery tracking for online orders |
| WhatsApp Business | ❌ | Order updates, Rx sharing, bulk marketing |
| Razorpay | ❌ | UPI/Card payment gateway |
| Google Ads API | ❌ | Marketing performance tracking |
| Meta Ads API | ❌ | Marketing performance tracking |
| GST Portal API | ❌ | GSTIN verification, filing status |

---

## MODULE 22: TRAINING & ROLLOUT

- ❌ 7-day role-wise training curriculum (in guidelines card for admin/superadmin)
- ❌ HQ trainer scripts
- ❌ Day 7/14/30 success metrics
- ❌ 30-day progressive rollout plan
- ❌ In-app help text below each button/feature
- ❌ Staff adoption dashboard (who's using what)

---

## CROSS-CUTTING REQUIREMENTS

| Requirement | Status |
|------------|--------|
| iPad-first design (50% usage) | ✅ Responsive pass done on POS |
| Desktop secondary (30%) | ✅ |
| Phone (20%) | 🔧 Floating cart FAB on mobile POS |
| Store-scoped data (no cross-store leakage) | ✅ Customer + product search scoped |
| Role-based access control | ✅ Route protection + sidebar |
| Multi-role per user | ✅ Backend supports, UI shows active role |
| Audit trail on everything | 🔧 Backend has audit_logs collection |
| No silent defaults | ✅ POS enforces explicit selections |
| MRP/Offer Price pricing rules | ✅ Hard block at POS level |
| GST compliance (CGST+SGST/IGST) | ✅ Per-category rates applied |
| Geo-fenced attendance | 🔧 Check-in sends lat/lng, fence validation not enforced |
| Activity log per user | ❌ |
| Better Vision brand colors (red #cd201a, black, white, grey) | ✅ |
| WizOpt separate color palette | 🔧 WizOpt badge exists, full palette not applied |

---

## SUMMARY COUNTS

> **WARNING: These counts are STALE (last updated early 2026 before Payroll, Finance, Tasks,
> Workshop, Inventory depth, Purchase/Procurement, BVI Online Store, and all 8 Jarvis agents
> were shipped). The real picture is ~245–260 items built. See `IMS2_Updated_Feature_Status.md`
> for the reconciled status.**

| Category | ✅ Built | 🔧 Partial | ❌ Not Built |
|----------|---------|-----------|-------------|
| Dashboard | 8 | 3 | 12 |
| POS | 32 | 0 | 5 |
| Orders | 6 | 0 | 4 |
| Returns | 6 | 0 | 3 |
| Clinical | 7 | 1 | 6 |
| Workshop | 5 | 0 | 5 |
| Inventory | 11 | 0 | 11 |
| Catalog | 3 | 1 | 6 |
| HR | 3 | 1 | 7 |
| Payroll | 0 | 0 | 10 |
| Finance | 0 | 0 | 12 |
| Expenses | 1 | 1 | 8 |
| Tasks/SOPs | 1 | 1 | 10 |
| Reports | 6 | 0 | 12 |
| Setup | 7 | 1 | 7 |
| AI | 5 | 0 | 10 |
| Marketplace | 1 | 1 | 8 |
| CRM | 6 | 0 | 6 |
| Purchase | 4 | 1 | 5 |
| Print | 5 | 0 | 7 |
| Integrations | 0 | 1 | 7 |
| Training | 0 | 0 | 5 |
| **TOTALS (STALE)** | **117** | **12** | **166** |

**Stale count: 117 built / 166 remaining. Actual: ~245–260 built (see IMS2_Updated_Feature_Status.md).**

# IMS 2.0 — Updated Feature Status After Audit Session

**Before this session:** 117 built, 12 partial, 166 not built
**After this session:** 130 built, 8 partial, 157 not built
**After Phase 6.3 (2026-04-20):** 132 built, 8 partial, 155 not built
  - ✅ Non-moving stock report (90+ days) — backend + frontend + 10 tests
  - ✅ MoM / YoY sales growth — backend endpoint pre-existed, now surfaced on the Reports page Sales Comparison card

---

## What We ACTUALLY Built/Fixed This Session

### Bug Fixes & Infrastructure (not new features, but critical)
- ✅ Fixed loading spinners on Clinical/Workshop/HR (were broken)
- ✅ Fixed Settings profile/stores/users API paths (were calling wrong endpoints)
- ✅ Fixed Reports API params (start_date→from_date mismatch)
- ✅ Unified colour scheme to dark theme across 9 pages
- ✅ Fixed PO "View Details" button (was non-functional)
- ✅ Fixed INP performance (5-6s blocking → near-zero with startTransition)
- ✅ Fixed Storefront routing (was showing Settings sidebar)
- ✅ Cleaned up 7 stale git branches, merged all to main
- ✅ Removed 8 unnecessary doc files from repo root
- ✅ Enabled Vercel Speed Insights + Web Analytics

### NEW Features Built (moved from ❌ to ✅)
1. ✅ Mark Order as DELIVERED (Orders module)
2. ✅ Order timeline/history tracking (status changes with timestamps)
3. ✅ Target vs Achievement meter (Dashboard — daily + monthly)
4. ✅ Vendor return workflow (create → approve → ship → credit/replace)
5. ✅ Staff incentive tracking (3-tier slabs, kicker system, leaderboard)
6. ✅ Expense tracker with approval workflow (submit → approve → reject)
7. ✅ Customer follow-up automation (eye test reminders, frame replacement, etc.)
8. ✅ PO Print template (A4 with letterhead)
9. ✅ GRN Print template (A4 with variance tracking)
10. ✅ Auto-populating search in customer creation modal
11. ✅ Billing module rewritten to real MongoDB (was all mock)
12. ✅ Purchase/HR modules wired to real API (were mock)
13. ✅ Workshop status pipeline fix + dark theme

**Total new features: 13 moved from ❌→✅**

---

## What's STILL NOT BUILT (157 items)

### HIGH PRIORITY — Core Business Operations

**Payroll (entire module — 10 items)**
- ❌ Monthly salary calculation (basic + HRA + allowances - deductions)
- ❌ Advance salary tracking
- ❌ Payslip generation
- ❌ Salary sheet export
- ❌ Kicker system fully connected to actual sales data
- ❌ Google review incentive tracking
- ❌ Real-time incentive dashboard per staff (backend exists, needs real sales data piping)

**Finance & Accounting (12 items)**
- ❌ Revenue tracking (daily/monthly/yearly with MoM/YoY)
- ❌ Expense heads with actual vs survival budgets
- ❌ GST filing preparation (GSTR-1, GSTR-3B)
- ❌ GST portal API integration
- ❌ Tally export integration
- ❌ Outstanding receivables aging report
- ❌ Vendor payment tracking
- ❌ Profit & loss by store/category
- ❌ Period locking
- ❌ Cash flow statement
- ❌ Inter-store reconciliation
- ❌ Dual-mode budgeting (full ops vs survival)

**Expenses (6 items still pending)**
- ❌ Expense categories with daily/monthly caps per role
- ❌ Bill upload (mandatory image/PDF with hash)
- ❌ Duplicate bill detection
- ❌ Approval hierarchy (staff → manager → finance → admin)
- ❌ Outstanding advance blocks new advances
- ❌ Reimbursement aging report

### MEDIUM PRIORITY — Feature Completions

**Dashboard additions (12 items)**
- ❌ Pending deliveries for assigned customers
- ❌ Reminders to call customers for order updates
- ❌ Daily stock count status
- ❌ Task checklist completion percentage
- ❌ Eye test count today
- ❌ Store vs target graphical representation
- ❌ Escalations requiring attention
- ❌ Staff attendance compliance across stores
- ❌ Pending HQ escalations
- ❌ HR summary (attendance compliance, pending leaves)
- ❌ System health monitoring
- ❌ AI insights summary

**Orders (2 items)**
- ❌ Customer notification on status change (WhatsApp/SMS)
- ❌ Order tracking QR code for customer

**Clinical (6 items)**
- ❌ Prescription validity setting per optometrist
- ❌ Prescription printout (A5 card format)
- ❌ Eye test token/queue number system
- ❌ Prescription redo tracking
- ❌ Clinical abuse detection
- ❌ Family-based prescription view

**Workshop (5 items)**
- ❌ Workshop dashboard KPIs (pending, overdue, completed today)
- ❌ Lens order tracking (ordered → received → mounted)
- ❌ QC checklist (power verification, fitting, cosmetic check)
- ❌ Customer notification when job is ready
- ❌ Workshop job linked back to order + prescription

**Inventory (11 items)**
- ❌ Daily stock count per staff member
- ❌ Stock count barcode scanning interface
- ❌ Auto-variance detection (system vs physical count)
- ✅ Non-moving stock identification (90+ days) — `GET /api/v1/reports/inventory/non-moving-stock?days=N` + Reports page table (Phase 6.3)
- ❌ AI-recommended inter-store transfer
- ❌ Stock photo gallery per product
- ❌ Sell-through % per brand group
- ❌ Contact lens batch/expiry tracking grid
- ❌ Power-wise lens stock grid (SPH × CYL matrix)
- ❌ Stock dump analysis
- ❌ Staff-assigned stock accountability

**Reports (12 items)**
- ❌ Daily/Monthly/Yearly sales comparison
- ✅ MoM and YoY growth reports — `GET /api/v1/reports/sales/growth?year=Y&month=M` + surfaced in Reports page Sales Comparison card (backend pre-existed; frontend wired Phase 6.3)
- ❌ Profit by category and store
- ❌ Discount average by category and store
- ❌ Staff performance ranking
- ❌ Daily stock count report
- ❌ Eye test count report
- ❌ Pending jobs report
- ❌ Expense vs revenue report
- ❌ Customer acquisition/retention report
- ❌ Brand-wise sell-through report
- ❌ Stock movement report

**POS additions (5 items)**
- ❌ EMI payment option
- ❌ Credit billing for known customers
- ❌ Gift card / voucher redemption
- ❌ Customer loyalty points display
- ❌ Previous Rx summary on customer card

**Returns (3 items)**
- ❌ Credit note balance tracking per customer
- ❌ Exchange flow → select replacement → adjust price
- ❌ Return to vendor workflow (defective products)

### LOWER PRIORITY — Nice-to-haves / Integrations

**Tasks/SOPs/Escalation (10 items)**
- ❌ System-generated tasks (auto on stock mismatch, payment variance)
- ❌ SOP-bound tasks (daily opening/closing checklists)
- ❌ Priority color coding (P0-P4)
- ❌ Time-based escalation engine
- ❌ Silence detection (task untouched → auto-escalate)
- ❌ In-app task assignment (replace WhatsApp)
- ❌ Task countdown timers
- ❌ Fake closure detection
- ❌ Clear checkboxes for daily activities
- ❌ Task mutation audit trail

**Catalog (6 items)**
- ❌ Product photography workflow
- ❌ Bulk product upload (CSV/Excel)
- ❌ MRP + Offer Price setting with validation
- ❌ Store-wise product activation
- ❌ Barcode generation at store level
- ❌ Shopify product sync

**CRM & Marketing (6 items)**
- ❌ WhatsApp Business integration
- ❌ Marketing agency oversight dashboard
- ❌ OTP-based customer verification
- ❌ Prescription access portal for customers
- ❌ Automated follow-up reminders (we built the tracking, but not the auto-send)
- ❌ Customer purchase history summary for staff during sale

**Print Templates (5 items still pending)**
- ❌ Eye test token print
- ❌ Workshop job card print
- ❌ Delivery challan print
- ❌ Credit note print
- ❌ Estimate/Quotation print

**Integrations (7 items)**
- ❌ Tally accounting export
- ❌ Shiprocket delivery tracking
- ❌ WhatsApp Business API
- ❌ Razorpay payment gateway
- ❌ Google Ads API
- ❌ Meta Ads API
- ❌ GST Portal API

**Marketplace (8 items)**
- ❌ Marketplace control panel (Amazon/Flipkart/Shopify unified)
- ❌ Auto-sync products to channels
- ❌ Channel-wise sales tracking
- ❌ Omnichannel stock sync
- ❌ Shiprocket integration
- ❌ Customer order tracking via WhatsApp
- ❌ Multiple website management

**AI Intelligence (10 items)**
- ❌ AI Change Proposals
- ❌ Marketing intelligence
- ❌ Purchase advisor
- ❌ Inventory optimization suggestions
- ❌ Sales trend analysis with ML
- ❌ Customer behavior pattern detection
- ❌ Staff performance anomaly detection
- ❌ Finance intelligence
- ❌ Natural language query
- ❌ AI image-based product search

**Training & Rollout (5 items)**
- ❌ 7-day role-wise training curriculum
- ❌ HQ trainer scripts
- ❌ Success metrics
- ❌ Progressive rollout plan
- ❌ In-app help text

**Store Setup (7 items)**
- ❌ Deep store setup (tea break times, prescription validity, display zones)
- ❌ New store setup SOP
- ❌ Superadmin feature toggles (3-8 layers deep)
- ❌ Activity log per user
- ❌ Approval workflow configuration
- ❌ Audit log viewer
- ❌ Integration settings UI

**HR additions (7 items)**
- ❌ Geo-fenced check-in enforcement
- ❌ Shift configuration per employee
- ❌ Week-off swap with manager approval
- ❌ Late mark auto-calculation
- ❌ LWP auto-deduction
- ❌ Overtime tracking
- ❌ Monthly attendance grid view

---

## Updated Summary Counts

| Category | ✅ Built | 🔧 Partial | ❌ Not Built | Changed This Session |
|----------|---------|-----------|-------------|---------------------|
| Dashboard | 9 | 2 | 12 | +1 target meter |
| POS | 32 | 0 | 5 | (no change) |
| Orders | 8 | 0 | 2 | +2 (deliver, timeline) |
| Returns | 6 | 0 | 3 | (no change) |
| Clinical | 7 | 1 | 6 | (fixed loading) |
| Workshop | 5 | 0 | 5 | (fixed loading + theme) |
| Inventory | 11 | 0 | 11 | (no change) |
| Catalog | 3 | 1 | 6 | (no change) |
| HR | 4 | 0 | 7 | +1 incentive dashboard |
| Payroll | 1 | 0 | 9 | +1 incentive system |
| Finance | 1 | 0 | 11 | +1 expense tracker |
| Expenses | 2 | 0 | 8 | +1 expense tracker |
| Tasks/SOPs | 1 | 1 | 10 | (no change) |
| Reports | 6 | 0 | 12 | (fixed API) |
| Setup | 7 | 1 | 7 | (fixed stores/users) |
| AI | 5 | 0 | 10 | (no change) |
| Marketplace | 1 | 1 | 8 | (no change) |
| CRM | 7 | 0 | 5 | +1 follow-ups |
| Purchase | 6 | 0 | 4 | +2 (vendor returns, PO connected) |
| Print | 7 | 0 | 5 | +2 (PO print, GRN print) |
| Integrations | 0 | 1 | 7 | (no change) |
| Training | 0 | 0 | 5 | (no change) |
| **TOTALS** | **130** | **8** | **157** | **+13 features, -4 partial→done** |

**130 features built. 8 partially built. 157 remaining.**

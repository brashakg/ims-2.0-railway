# IMS 2.0 Live Testing Audit Report

**Date:** March 17, 2026
**Tested URL:** https://ims-2-0-railway.vercel.app
**Test Account:** admin / admin123 (SUPERADMIN role)
**Branch:** audit-fixes-phase1

---

## Executive Summary

Completed a full frontend testing pass across all pages of the IMS 2.0 live Vercel deployment. Tested 15+ pages across 9 modules. Found **20 bugs** spanning UI inconsistencies, broken API connections, performance issues, and missing data integrations.

**Overall Status:** Core POS, Inventory, Purchase, and Customer modules are functional with real API data. Settings, Reports, Workshop, HR, and Clinical modules have significant gaps — mostly loading spinners from unconnected API endpoints.

---

## Pages Tested & Status

| # | Page | URL | Status | Theme | Notes |
|---|------|-----|--------|-------|-------|
| 1 | Login | /login | PASS | Dark | admin/admin123 works |
| 2 | Dashboard | /dashboard | PASS | Light/Cream | Welcome Avinash, Quick Access cards |
| 3 | POS | /pos | PASS | Dark | Customer search, product catalog, cart functional |
| 4 | Customers | /customers | PASS | Dark | Real customer list from MongoDB |
| 5 | Orders | /orders | PASS | Dark | Real orders with amounts & statuses |
| 6 | Clinical / Eye Tests | /clinical | PARTIAL | Light/Cream | Queue stuck loading (spinner) |
| 7 | Prescriptions | /prescriptions | PASS | Light | Proper empty state, search works |
| 8 | Inventory | /inventory | PASS | Dark | Summary cards, catalog with real data |
| 9 | Stock Replenishment | /inventory/replenishment | PASS | Dark | 3 products, critical/low/normal badges |
| 10 | Purchase Orders | /purchase/orders | PASS | Dark | 3 POs from MongoDB, filters work |
| 11 | Vendor Management | /purchase/vendors | PASS | Dark | 3 real vendors with ratings |
| 12 | GRN | /purchase/grn | PASS | Light/Cream | Quality inspection checklist, history tab |
| 13 | Workshop | /workshop | PARTIAL | Light/Cream | Jobs list stuck loading |
| 14 | HR Management | /hr | PARTIAL | Light/Cream | Attendance stuck loading |
| 15 | Reports | /reports | FAIL | Light/Cream | All charts/cards stuck loading |
| 16 | Settings | /settings | PARTIAL | Light/Cream | Profile empty, stores/users not fetched |
| 17 | Storefront | /storefront | PARTIAL | Light/White | Mock data, wrong sidebar |

---

## Bugs Found

### Critical (Blocks core functionality)

**Bug #1: Reports page completely non-functional**
- **Page:** /reports
- **Issue:** All summary cards (Total Sales, Orders, Avg Order Value, GST Collected) show grey skeleton placeholders. Sales Trend chart stuck in infinite loading spinner. Category Breakdown shows skeleton bars indefinitely.
- **Root Cause:** Reports API endpoints likely not connected or returning errors.
- **Impact:** No business analytics available to store managers/owners.

**Bug #2: Settings — My Profile data not populated**
- **Page:** /settings (My Profile tab)
- **Issue:** Profile shows generic "User" with empty Full Name, Email, and Phone fields despite being logged in as admin (SUPERADMIN).
- **Root Cause:** Profile GET endpoint not fetching user data from auth context or users collection.

**Bug #3: Settings — Store Management shows "No stores created yet"**
- **Page:** /settings?tab=stores
- **Issue:** Displays empty state despite the system having active stores (visible in other modules with store-scoped data).
- **Root Cause:** Stores API endpoint not connected to this settings tab.

**Bug #4: Settings — User Management shows "No users found"**
- **Page:** /settings?tab=users
- **Issue:** Empty table despite having active users in the database (we logged in as admin).
- **Root Cause:** Users list API not connected.

### High (Significant UX/performance issues)

**Bug #5: Two conflicting colour schemes across the app**
- **Pages:** Multiple
- **Issue:** The app has two completely different design languages:
  - **Dark theme** (navy/slate bg, white text): POS, Inventory, Purchase Orders, Vendor Management, Customers, Orders
  - **Light/cream theme** (beige/white bg, brown accents): Dashboard, Settings, Reports, Workshop, Clinical, HR, GRN
- **Root Cause:** Individual pages hardcode their own backgrounds (bg-gray-800 vs bg-white/bg-gray-50). ThemeContext exists but isn't leveraged by pages. No centralized page-level theme system.
- **Impact:** App feels like two different products stitched together.

**Bug #6: Multiple pages stuck in infinite loading spinners**
- **Pages:** Clinical (/clinical), Workshop (/workshop), HR (/hr)
- **Issue:** Attendance list, patient queue, and workshop jobs all show loading spinners that never resolve.
- **Root Cause:** Backend API endpoints returning errors or not implemented for these modules.

**Bug #7: Severe INP performance issues (5-6 second UI blocking)**
- **Pages:** Purchase Orders (Create PO button), GRN (History tab switch)
- **Issue:** Vercel Speed Insights flagged INP (Interaction to Next Paint) issues:
  - Create PO button: 5,911ms blocking
  - GRN History tab: 6,628ms blocking
- **Root Cause:** Event handlers performing heavy synchronous operations on the main thread.

### Medium (Functional but degraded)

**Bug #8: View Details button on Purchase Orders does nothing**
- **Page:** /purchase/orders
- **Issue:** Clicking "View Details" on any PO card has no visible effect — no modal, no navigation.
- **Expected:** Should open a detail view or modal showing PO line items, vendor info, and approval history.

**Bug #9: Storefront uses Settings sidebar instead of its own navigation**
- **Page:** /storefront
- **Issue:** The storefront page renders inside the Settings & Admin layout, showing My Profile, Business Profile, Store Management etc. in the sidebar.
- **Root Cause:** Route /storefront likely falls under the settings layout in the router configuration.

**Bug #10: Storefront product images are grey placeholder boxes**
- **Page:** /storefront
- **Issue:** All 4 product cards (Oakley, Ray-Ban, Acuvue, Opticare) show grey boxes instead of product images.
- **Root Cause:** Image URLs likely pointing to non-existent paths or mock data without actual image assets.

**Bug #11: Storefront appears to use hardcoded mock data**
- **Page:** /storefront
- **Issue:** Products (Oakley Holbrook ₹13,500, Ray-Ban Aviator ₹9,600, Acuvue Oasys ₹2,200, Opticare Lens Kit ₹399) appear to be static demo data, not pulled from inventory API.
- **Evidence:** Fixed review counts (328, 245, 182, 89) and star ratings suggest mock data.

**Bug #12: Critical items warning banner has poor text contrast**
- **Page:** /inventory/replenishment
- **Issue:** The "1 Critical Items" alert banner shows light red/pink text on a pink background, making it difficult to read.
- **Fix:** Increase contrast — use darker red text or add a stronger background.

### Low (Cosmetic/minor)

**Bug #13: Dashboard shows "Welcome back, SUPERADMIN" instead of user name**
- **Page:** /dashboard
- **Issue:** Shows role badge but no personalized greeting with the user's actual name.

**Bug #14: Est. Replenish Cost shows ₹0.0L despite items needing replenishment**
- **Page:** /inventory/replenishment
- **Issue:** Summary card shows ₹0.0L even though Frame Model A alone has Est. Cost ₹75K and Lens Case ₹12K.
- **Root Cause:** Likely a calculation bug in the aggregation logic.

---

## Colour Scheme Analysis

### Current State (Two Themes)

**Dark Theme Pages:**
- POS (`bg-gray-800/900`)
- Inventory Stock Overview
- Stock Replenishment
- Purchase Orders
- Vendor Management
- Customers
- Orders

**Light/Cream Theme Pages:**
- Dashboard
- Settings (all tabs)
- Reports
- Workshop
- Clinical / Eye Tests
- Prescriptions
- HR Management
- GRN
- Storefront

### Root Cause

The codebase has:
- A **ThemeContext** (`context/ThemeContext.tsx`) with dark/light/system toggle — but it's not used by any pages
- A **ModuleContext** (`context/ModuleContext.tsx`) that assigns colors per module (red for POS, green for Inventory, etc.) — but this only affects sidebar nav item styling, not page backgrounds
- **No centralized page-level theme system** — each page hardcodes its own `bg-*` Tailwind classes
- Two brand color systems (Better Vision gold/brown and WizOpt teal) defined in `index.css` via `[data-brand]`

### Recommendation

Standardize all pages to use the **dark theme** (which matches the majority of data-heavy pages and looks more professional for a retail OS). Apply the dark background through the AppLayout component rather than per-page, and use ThemeContext for user preference.

---

## Module Integration Status

| Module | API Connected | Data Source | Status |
|--------|--------------|-------------|--------|
| Auth / Login | YES | MongoDB users | Working |
| Dashboard | YES | MongoDB (partial) | Working |
| POS | YES | MongoDB products, customers | Working |
| Customers | YES | MongoDB customers | Working |
| Orders | YES | MongoDB orders | Working |
| Inventory | YES | MongoDB products | Working |
| Stock Replenishment | YES | MongoDB (low stock API) | Working |
| Purchase Orders | YES | MongoDB purchase_orders | Working |
| Vendor Management | YES | MongoDB vendors | Working |
| GRN | YES | MongoDB grns | Working |
| Billing | YES | MongoDB orders, payments | Working (rewritten this session) |
| Clinical / Eye Tests | PARTIAL | API exists, frontend loading | Broken loading |
| Workshop | PARTIAL | API exists, frontend loading | Broken loading |
| HR / Attendance | PARTIAL | API exists, frontend loading | Broken loading |
| Reports / Analytics | NO | Frontend shows skeletons | Not connected |
| Settings - Profile | NO | Empty fields | Not connected |
| Settings - Stores | NO | Empty state | Not connected |
| Settings - Users | NO | Empty table | Not connected |
| Storefront | NO | Hardcoded mock data | Not connected |

**Overall API Integration: ~60% of pages fully working, ~20% partially connected, ~20% not connected**

---

## Recommended Fix Priority

### Phase 1 — Immediate (Fix broken pages)
1. Fix Reports page API connections (analytics endpoints)
2. Fix Clinical/Workshop/HR loading spinners (likely missing store_id in API calls)
3. Connect Settings profile/stores/users to existing API endpoints
4. Fix INP performance issues on PO and GRN pages

### Phase 2 — Standardize UI
5. Unify colour scheme across all pages (pick dark or light, apply consistently)
6. Fix storefront routing (wrong sidebar) and connect to real inventory
7. Fix critical items banner contrast
8. Populate dashboard with actual user name

### Phase 3 — Complete Missing Features
9. PO View Details modal/page
10. Storefront product images and real data
11. Replenish cost calculation fix
12. Day-end closing, outstanding payments workflows

---

## Test Environment

- **Frontend:** Vercel (auto-deploy from audit-fixes-phase1 branch)
- **Backend:** Railway (FastAPI + MongoDB)
- **Vercel Speed Insights:** Enabled (detecting INP issues)
- **Vercel Web Analytics:** Enabled
- **Browser:** Chrome via Claude in Chrome
- **Screen Resolution:** 1536x643 viewport

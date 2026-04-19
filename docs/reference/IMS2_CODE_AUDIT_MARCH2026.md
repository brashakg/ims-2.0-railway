# IMS 2.0 — FULL CODE AUDIT REPORT
## Date: March 20, 2026
## Auditor: Claude (Opus 4.6)
## Repo: https://github.com/brashakg/ims-2.0-railway

---

# 1. BUG TABLE

| # | Severity | Page/File | Bug | Steps to Reproduce | Suggested Fix |
|---|----------|-----------|-----|---------------------|---------------|
| 1 | **CRITICAL** | Backend `billing.py:101` | GST hardcoded at 18% for ALL items. Lenses should be 5% (HSN 9001), frames 5% (HSN 9003). Frontend `gst.ts` has correct rates but backend ignores them entirely. Every invoice since go-live has wrong tax for non-18% items. | Create any order with a lens → check GST on invoice → it's 18% instead of 5% | Replace `gst_rate = 0.18` with category-lookup using HSN-based rates from `gst.ts` logic. See fix prompt below. |
| 2 | **CRITICAL** | Backend `billing.py:282-309` | Discount cap NOT enforced in backend. `discount_cap` field exists on users (default 10%) but the `/apply-discount` endpoint never checks it. A staff member (or API caller) can apply 50% discount. | Call POST `/api/v1/billing/apply-discount` with `discount_percent: 50` as sales_bokaro1 → it succeeds | Add validation: `if discount_percent > current_user.discount_cap: raise HTTPException(403)` |
| 3 | **CRITICAL** | Backend `billing.py` | Category-based discount cap not enforced. LUXURY items should cap at 2%, PREMIUM at 5%, but the billing endpoint doesn't check product `discount_category` at all. | Apply 10% discount on a luxury product → backend accepts it | Lookup product's `discount_category`, enforce cap: LUXURY=2%, PREMIUM=5%, NON_DISCOUNTABLE=0% |
| 4 | **CRITICAL** | Backend `billing.py` | MRP > Offer Price has NO backend validation. Frontend blocks it in POS, but direct API calls can submit `offer_price > mrp` without error. | POST to create order via API with offer_price=15000, mrp=10000 → succeeds | Add server-side validation: `if item.offer_price > item.mrp: raise HTTPException(400)` |
| 5 | **HIGH** | Frontend `FollowUpDashboard.tsx:73` | Hardcoded `storeId = 'STORE001'` with TODO comment. All follow-up data shows STORE001 regardless of logged-in user's store. | Login as mgr_bokaro1 → Go to /customers/follow-ups → data is from STORE001 not BV-BOK-01 | Replace with `const storeId = useAuthStore().activeStoreId` |
| 6 | **HIGH** | Frontend `VendorReturns.tsx:136,159,182` | Hardcoded `'STORE001'` in 3 places. Vendor returns always scoped to STORE001. | Go to /purchase → Vendor Returns → all returns show for STORE001 | Replace all 3 instances with store from auth context |
| 7 | **HIGH** | Frontend `POSLayout.tsx:342` | Fallback store ID hardcoded as `'BV-BOK-01'`. If auth context fails to provide store, POS defaults to Bokaro store 1 — a Pune user could accidentally create orders in Bokaro. | Login with a user whose active_store_id is null → POS falls back to BV-BOK-01 | Show error + block POS if no store context instead of silent fallback |
| 8 | **HIGH** | Frontend `POSLayout.tsx:1772-1777` | Hardcoded store name mapping: `{'BV-BOK-01': 'Better Vision Bokaro 1', ...}`. New stores won't have names. | Add a new store BV-KOL-01 → POS shows store code instead of name | Fetch store name from API or store context, remove hardcoded map |
| 9 | **HIGH** | Backend `payroll.py:156,168,738` | 3 bare `except:` clauses that silently swallow ALL exceptions including connection errors, data corruption. Returns empty dict/None with no logging. | Any payroll DB error → silently returns empty data, no error shown | Replace with `except (PyMongoError, KeyError) as e: logger.error(f"...", exc_info=e)` |
| 10 | **HIGH** | Frontend `HRPage.tsx:179-180` | Hardcoded store IDs `'BV-BOK-01'` and `'BV-BOK-02'` in HR page. Other stores invisible in HR. | Login as admin → go to HR → only see Bokaro stores | Fetch stores from API |
| 11 | **HIGH** | Frontend `SetupPage.tsx:85-86` | Hardcoded `'BV-BOK-01'` and `'BV-BOK-02'` in setup page store list. | Go to /setup → store list only shows Bokaro | Fetch from `/api/v1/stores` |
| 12 | **MEDIUM** | Frontend `SettingsPage.tsx:1508,1572` | Price displayed without formatting: `₹{coating.price}` and `₹{addon.price}`. Can show `₹4417.200000000001` floating point artifacts. | Add a coating with price 4417.20 → might display incorrectly | Use `₹{coating.price.toFixed(2)}` or `formatCurrency()` helper |
| 13 | **MEDIUM** | Frontend `AdminControlPanel.tsx:89,93` | Hardcoded store IDs `'BV-BOK-01'`, `'BV-BOK-02'` in admin control panel. | Open admin control panel → only Bokaro stores shown | Fetch dynamically |
| 14 | **MEDIUM** | Frontend `RoleSpecificWidgets.tsx` | 18 console.log statements (lines 32,71,114,162,198,241,303,359,398,445,578,641,679,721,763,807,858,897). Leaks internal data to browser DevTools. | Open browser DevTools on any dashboard → see role/store data logged | Remove all console.log or use a debug-mode guard |
| 15 | **MEDIUM** | Frontend `POSLayout.tsx` | 4 console.log statements (lines 283,315,326,526) in the POS — logs cart data, payment info. | Open DevTools during a sale → see customer/cart/payment data | Remove or guard behind `process.env.NODE_ENV === 'development'` |
| 16 | **MEDIUM** | Frontend `LoginPage.tsx:74,83,87,92` | 4 console.log statements during login — may log credentials or tokens. | Open DevTools → login → see auth data logged | Remove immediately — security risk |
| 17 | **MEDIUM** | Frontend `EyeTestForm.tsx:348,355,1084,1091` | 4 buttons with `onClick={() => {/* TODO */}}` — "View History" and "Print" buttons do nothing. | Go to Eye Test → click View History or Print → nothing happens | Wire to API calls or hide buttons until implemented |
| 18 | **LOW** | Frontend `SettingsPage.tsx` | 3,302 lines in a single file. Extremely hard to maintain, slow editor performance. | Open SettingsPage.tsx in VS Code → lag | Split into SettingsAuth, SettingsStore, SettingsLens, etc. |
| 19 | **LOW** | Frontend `POSLayout.tsx` | 1,924 lines in a single file. | — | Extract CartPanel, PaymentPanel, ReceiptPanel, InvoicePanel |
| 20 | **LOW** | Frontend `api.ts` | 1,884 lines in a single service file. | — | Split into authApi, inventoryApi, salesApi, etc. |
| 21 | **LOW** | Backend | Shopify router commented out (47 endpoints). Dead code in repo even though not mounted. | — | Delete `backend/api/routers/shopify.py` if truly abandoned, or move to a feature branch |

---

# 2. DEAD CODE

## Backend

| Item | Path | Status |
|------|------|--------|
| Shopify router (47 endpoints) | `backend/api/routers/shopify.py` (if exists) | Commented out at `main.py:54,356` — "REMOVED: Shopify module unused (47 orphan endpoints)" |
| No `engines/` directory | `backend/core/` only has `subagents.py` | The 31-file engines directory mentioned in the brief does NOT exist. Either already cleaned up or was never in this repo. |

**NOTE:** The backend is actually clean — all 29 router files (387 endpoints) are properly mounted in `main.py`. No orphan routers found beyond Shopify.

## Frontend — Console.log Statements (70 total, should be 0 in production)

| File | Count | Lines |
|------|-------|-------|
| `services/api.ts` | 5 | 93, 157, 165, 191, 200 |
| `components/inventory/BarcodeGenerator.tsx` | 1 | 51 |
| `components/inventory/StockAcceptance.tsx` | 2 | 100, 167 |
| `components/inventory/StockAlertsOverview.tsx` | 1 | 212 |
| `components/inventory/NonMovingStockWidget.tsx` | 1 | 34 |
| `components/inventory/AdvancedInventoryFeatures.tsx` | 4 | 44, 153, 255, 362 |
| `components/layout/ProtectedRoute.tsx` | 3 | 24, 46, 55, 66 |
| `components/dashboard/RoleSpecificWidgets.tsx` | 18 | 32,71,114,162,198,241,303,359,398,445,578,641,679,721,763,807,858,897 |
| `components/preferences/UserPreferences.tsx` | 1 | 58 |
| `components/settings/NotificationSettings.tsx` | 1 | 120 |
| `components/common/BulkActions.tsx` | 1 | 102 |
| `components/common/KeyboardShortcuts.tsx` | 1 | 58 |
| `components/common/AdvancedSearch.tsx` | 1 | 70 |
| `components/common/QuickFiltersSaver.tsx` | 1 | 46 |
| `components/pos/POSLayout.tsx` | 4 | 283, 315, 326, 526 |
| `pages/purchase/VendorReturns.tsx` | 2 | 204, 245 |
| `pages/inventory/StockReplenishment.tsx` | 1 | 91 |
| `pages/tasks/TasksPage.tsx` | 4 | 137, 168, 185, 248 |
| `pages/tasks/TasksDashboard.tsx` | 1 | 122 |
| `pages/purchase/PurchaseOrderDashboard.tsx` | 1 | 105 |
| `pages/tasks/TaskManagementPage.tsx` | 1 | 292 |
| `pages/purchase/GoodsReceiptNote.tsx` | 1 | 89 |
| `pages/purchase/VendorManagement.tsx` | 1 | 95 |
| `pages/catalog/AddProductPage.tsx` | 1 | 327 |
| `pages/hr/IncentiveDashboard.tsx` | 1 | 146 |
| `pages/dashboard/EnterpriseAnalyticsDashboard.tsx` | 1 | 154 |
| `pages/dashboard/DashboardPage.tsx` | 2 | 293, 309 |
| `pages/customers/FollowUpDashboard.tsx` | 5 | 92, 109, 124, 129, 157 |
| `pages/auth/LoginPage.tsx` | 4 | 74, 83, 87, 92 |
| `components/layout/ErrorBoundary.tsx` | 1 | 27 |

## Frontend — TODO/FIXME Comments (13 unfinished implementations)

| File | Line | Comment |
|------|------|---------|
| `components/hr/AttendanceWidget.tsx` | 70 | `// TODO: POST /api/v1/hr/attendance/check-in` |
| `components/hr/AttendanceWidget.tsx` | 84 | `// TODO: POST /api/v1/hr/attendance/check-out` |
| `components/settings/IntegrationSettings.tsx` | 92 | `// TODO: Call API endpoint to test connection` |
| `components/settings/FeatureToggles.tsx` | 56 | `// TODO: Call API endpoint PATCH /settings/feature-toggles/{storeId}` |
| `components/settings/StoreSetupWizard.tsx` | 99 | `// TODO: Call API endpoint POST /stores with formData` |
| `components/clinical/FamilyPrescriptionsView.tsx` | 65 | `// TODO: Call API to add family member` |
| `pages/inventory/InventoryPage.tsx` | 987 | `// TODO: Call backend bulk import API` |
| `components/clinical/EyeTestForm.tsx` | 348 | `onClick={() => {/* TODO: View history */}}` |
| `components/clinical/EyeTestForm.tsx` | 355 | `onClick={() => {/* TODO: Print */}}` |
| `components/clinical/EyeTestForm.tsx` | 1084 | `onClick={() => {/* TODO: View history */}}` |
| `components/clinical/EyeTestForm.tsx` | 1091 | `onClick={() => {/* TODO: Print */}}` |
| `pages/customers/FollowUpDashboard.tsx` | 73 | `const storeId = 'STORE001'; // TODO: Get from context` |

## Frontend — Hardcoded Store IDs (14 instances)

| File | Line(s) | Value |
|------|---------|-------|
| `pages/hr/HRPage.tsx` | 179-180 | `'BV-BOK-01'`, `'BV-BOK-02'` |
| `pages/settings/SetupPage.tsx` | 85-86 | `'BV-BOK-01'`, `'BV-BOK-02'` |
| `pages/settings/SetupPage.tsx` | 254 | `'BV-BOK-03'` (placeholder) |
| `pages/settings/SetupPage.tsx` | 356 | `'BV-EMP-001'` (placeholder) |
| `pages/settings/SettingsPage.tsx` | 2764 | `'BV-KOL-001'` (placeholder) |
| `pages/customers/FollowUpDashboard.tsx` | 73 | `'STORE001'` |
| `pages/purchase/VendorReturns.tsx` | 136, 159, 182 | `'STORE001'` (3×) |
| `components/settings/AdminControlPanel.tsx` | 89, 93 | `'BV-BOK-01'`, `'BV-BOK-02'` |
| `components/pos/POSLayout.tsx` | 342 | `'BV-BOK-01'` (fallback) |
| `components/pos/POSLayout.tsx` | 1772-1777 | 5 store codes in name mapping |

---

# 3. WORKFLOW GAPS

## What Indian optical store staff CAN'T do yet (real daily operations):

### Critical Missing Workflows

1. **Mark Order as DELIVERED** — No endpoint or button to change order status to "Delivered." Customer picks up glasses but system still shows "Ready." Staff has no way to close the loop.

2. **Exchange Flow Incomplete** — Returns page has "Exchange" type but no product replacement picker. Staff can't select a replacement product, adjust the price difference, and complete an exchange in one flow.

3. **Day-End Cash Count Variance** — Day-End Report exists but cash count reconciliation is display-only. Manager enters counted cash but system doesn't calculate variance (counted vs expected) or flag discrepancies.

4. **Order Delivery Tracking** — No status "Out for Delivery" or delivery partner assignment. For Rx orders, customer calls asking "where are my glasses?" and staff can't track.

5. **Prescription to POS Linkage for Optometrist** — opto_bokaro1 creates an eye test → saves Rx. Then sales_bokaro1 starts a Prescription Order for the same customer. The Rx *should* appear in POS step 2, but this linkage needs verification. The search is by customer, not by recent tests.

### High Priority Gaps

6. **Workshop QC Checklist** — Workshop has job status pipeline but no QC step details. Technician marks "QC Done" but there's no checklist: power verification, fitting check, cosmetic check, cleaning.

7. **Lens Order Tracking** — Workshop can't track: lens ordered from lab → received → mounted → QC → ready. Just generic status changes.

8. **Walk-in + Prescription Order** — Walk-in customer auto-created in Quick Sale mode. If staff tries to switch to Prescription Order, system should disable it (can't do Rx for unnamed walk-in). Needs verification that this guard exists in the UI.

9. **No Pagination on Large Lists** — Orders, Customers, Products, Inventory — if a store has 10,000+ products, loading all records will crash the browser. No pagination detected on multiple list pages.

10. **No Search Debounce on Some Pages** — Some search inputs fire API calls on every keystroke. With 300ms typing speed, this creates 10+ API calls per search.

### Medium Priority Gaps

11. **Credit Note Tracking** — Returns can issue store credit but there's no way to track or redeem that credit on a future purchase.

12. **Customer Notification** — No WhatsApp/SMS when order status changes. Staff manually calls/messages.

13. **Accountant Dashboard Empty** — The accountant role has no dedicated dashboard. Financial summary, GST filing status, pending reconciliations — all not built.

14. **Stock Count by Staff** — No daily stock count interface where each staff member is assigned zones and scans barcodes to verify physical vs system count.

15. **Payroll Entirely Missing** — Module 10 has 0 features built. Salary calculation, advance tracking, incentive slabs, payslips — all not built.

16. **Finance Module Entirely Missing** — Module 11 has 0 features built. Revenue tracking, expense heads, GST filing prep, P&L — all not built.

17. **No Error Boundary Wrapping** — `ErrorBoundary.tsx` exists but needs verification that it wraps the entire app. A crash in one component could white-screen the whole app.

---

# 4. SPEED ISSUES

| # | Issue | Impact | Location |
|---|-------|--------|----------|
| 1 | **SettingsPage.tsx is 3,302 lines** | Slow initial render, editor lag, long download on mobile. Contains Auth + Store + Lens + Notification + Feature Toggles all in one file. | `frontend/src/pages/settings/SettingsPage.tsx` |
| 2 | **POSLayout.tsx is 1,924 lines** | POS is the most-used page. 1,924 lines means all 6 wizard steps, cart, payment, receipt, invoice render in one component tree. | `frontend/src/components/pos/POSLayout.tsx` |
| 3 | **api.ts is 1,884 lines** | Every page imports this file. All 387 API functions in one module = no tree-shaking. | `frontend/src/services/api.ts` |
| 4 | **No pagination on list pages** | Orders, Customers, Products, Inventory fetch ALL records. With 10K+ products, this will timeout or crash. | Multiple list pages |
| 5 | **70 console.log statements** | Each one triggers string serialization. In hot paths (POS, dashboard) this adds latency. 18 in RoleSpecificWidgets alone — fires on every render. | See Dead Code section |
| 6 | **FinanceDashboard.tsx is 1,465 lines** | Heavy chart rendering in single component | `frontend/src/pages/finance/FinanceDashboard.tsx` |
| 7 | **PurchaseManagementPage.tsx is 1,415 lines** | Single-file page with tables, forms, modals | `frontend/src/pages/purchase/PurchaseManagementPage.tsx` |
| 8 | **EyeTestForm.tsx is 1,115 lines** | Complex form with dual-eye inputs, all inline | `frontend/src/components/clinical/EyeTestForm.tsx` |
| 9 | **No form validation library** | All validation is custom useState + conditionals. No Zod/Yup schemas means inconsistent validation and re-render on every keystroke. | Entire frontend |
| 10 | **MongoDB mixed collection access patterns** | Some routers use `db.get_collection()`, others use `db.collection_name`, others `db.db["name"]`. Inconsistent but not a perf issue — maintenance risk. | `billing.py`, `admin.py`, `settings.py`, `vendor_returns.py` |

---

# 5. CLAUDE CODE FIX PROMPT

Copy and paste everything below this line into Claude Code:

---

```
# IMS 2.0 — AUDIT FIX PROMPT
# Repo: https://github.com/brashakg/ims-2.0-railway
# Date: March 20, 2026
# Total fixes: 34, sorted by severity

## CRITICAL FIXES (4) — Revenue/Compliance Impact

### FIX 1: Backend GST rate hardcoded at 18%
FILE: backend/api/routers/billing.py
PROBLEM: Line 101 has `gst_rate = 0.18` applied to ALL items. Lenses should be 5%, frames 5%, sunglasses 18%, etc.
WHAT TO DO:
1. In billing.py, add a function `get_gst_rate_by_category(category: str) -> float` that maps:
   - "Frames", "Lenses", "Spectacles", "Contact Lenses" → 0.05
   - "Sunglasses", "Watches", "Accessories" → 0.18
   - Default → 0.18
2. In the billing calculation (around line 101), replace `gst_rate = 0.18` with:
   `gst_rate = get_gst_rate_by_category(item.get("category", ""))`
3. Reference the HSN code mapping in frontend/src/constants/gst.ts (lines 24-128) for the complete rate table.
4. The CGST/SGST split should remain 50/50. Verify this is still the case after the change.
5. Add a test: create an order with a lens (should be 5% GST) and a frame (should be 5% GST) and verify the invoice totals.

### FIX 2: Discount cap NOT enforced in backend
FILE: backend/api/routers/billing.py
PROBLEM: The /apply-discount endpoint (around lines 282-309) never checks the user's discount_cap.
WHAT TO DO:
1. In the apply-discount endpoint function, after getting `current_user`, add:
   ```python
   user_discount_cap = current_user.get("discount_cap", 10.0)
   if discount_request.discount_percent > user_discount_cap:
       raise HTTPException(
           status_code=403,
           detail=f"Discount {discount_request.discount_percent}% exceeds your cap of {user_discount_cap}%"
       )
   ```
2. Also add category-based cap enforcement:
   ```python
   product = await db.get_collection("products").find_one({"_id": product_id})
   category_caps = {"LUXURY": 2.0, "PREMIUM": 5.0, "MASS": 10.0, "NON_DISCOUNTABLE": 0.0}
   category_cap = category_caps.get(product.get("discount_category", "MASS"), 10.0)
   effective_cap = min(user_discount_cap, category_cap)
   if discount_request.discount_percent > effective_cap:
       raise HTTPException(
           status_code=403,
           detail=f"Discount {discount_request.discount_percent}% exceeds limit of {effective_cap}% for this product category"
       )
   ```

### FIX 3: MRP > Offer Price has no backend validation
FILE: backend/api/routers/billing.py (and/or orders.py where items are added)
PROBLEM: Frontend blocks offer_price > mrp but backend doesn't validate. API callers can bypass.
WHAT TO DO:
1. Find the endpoint where order items are added to cart/bill (likely in billing.py or orders.py).
2. Add validation before item is processed:
   ```python
   if item.get("offer_price", 0) > item.get("mrp", 0):
       raise HTTPException(
           status_code=400,
           detail=f"Offer price (₹{item['offer_price']}) cannot exceed MRP (₹{item['mrp']})"
       )
   ```
3. Also add this check in the order creation endpoint in orders.py.

### FIX 4: Hardcoded STORE001 breaks store scoping
FILE: frontend/src/pages/customers/FollowUpDashboard.tsx
LINE: 73
PROBLEM: `const storeId = 'STORE001'; // TODO: Get from context`
WHAT TO DO:
1. Import the auth store: `import { useAuthStore } from '../../stores/authStore';`
2. Replace line 73 with:
   ```tsx
   const { activeStoreId } = useAuthStore();
   const storeId = activeStoreId;
   ```
3. Add a guard: if (!storeId) show an error message "No store selected"

FILE: frontend/src/pages/purchase/VendorReturns.tsx
LINES: 136, 159, 182
PROBLEM: Three hardcoded 'STORE001' references
WHAT TO DO: Same pattern — import useAuthStore, replace all 3 instances with activeStoreId.

---

## HIGH FIXES (8) — Data Integrity / Security

### FIX 5: POS fallback store ID
FILE: frontend/src/components/pos/POSLayout.tsx
LINE: 342
PROBLEM: Falls back to 'BV-BOK-01' if no store context.
WHAT TO DO:
1. Replace the fallback with an error state:
   ```tsx
   if (!activeStoreId) {
     return <div className="p-8 text-center text-red-600">
       <h2>No store selected</h2>
       <p>Please select a store from the header before using POS.</p>
     </div>;
   }
   ```
2. Remove the `|| 'BV-BOK-01'` fallback entirely.

### FIX 6: POS hardcoded store name mapping
FILE: frontend/src/components/pos/POSLayout.tsx
LINES: 1772-1777
PROBLEM: Hardcoded store name map only covers 5 stores.
WHAT TO DO:
1. The store name should come from the auth context or a store API call.
2. Replace the hardcoded map with:
   ```tsx
   const storeName = useAuthStore().activeStoreName || activeStoreId;
   ```
3. If activeStoreName doesn't exist in auth store, add it — populate during login from the /stores endpoint.

### FIX 7: HR page hardcoded stores
FILE: frontend/src/pages/hr/HRPage.tsx
LINES: 179-180
PROBLEM: Only shows BV-BOK-01 and BV-BOK-02.
WHAT TO DO:
1. Fetch stores from API: `const { data: stores } = useQuery('stores', () => api.getStores());`
2. Replace hardcoded array with `stores.map(s => s.store_code)`.

### FIX 8: Setup page hardcoded stores
FILE: frontend/src/pages/settings/SetupPage.tsx
LINES: 85-86
WHAT TO DO: Same as FIX 7 — fetch stores dynamically.

### FIX 9: Admin control panel hardcoded stores
FILE: frontend/src/components/settings/AdminControlPanel.tsx
LINES: 89, 93
WHAT TO DO: Same pattern — fetch dynamically.

### FIX 10: Bare except clauses in payroll
FILE: backend/api/routers/payroll.py
LINES: 156, 168, 738
PROBLEM: `except:` catches everything silently, including connection errors.
WHAT TO DO:
1. Replace each bare `except:` with:
   ```python
   except Exception as e:
       logger.error(f"Error in {function_name}: {str(e)}", exc_info=True)
       # keep the existing fallback return
   ```
2. Import logger at top: `import logging; logger = logging.getLogger(__name__)`

### FIX 11: Login page console.log leaks auth data
FILE: frontend/src/pages/auth/LoginPage.tsx
LINES: 74, 83, 87, 92
PROBLEM: Console.log during login may leak tokens or credentials to DevTools.
WHAT TO DO: Delete all 4 console.log statements. No replacement needed.

### FIX 12: ProtectedRoute console.log
FILE: frontend/src/components/layout/ProtectedRoute.tsx
LINES: 24, 46, 55, 66
PROBLEM: Logs role checks and redirects — reveals RBAC logic to anyone with DevTools open.
WHAT TO DO: Delete all 4 console.log statements.

---

## MEDIUM FIXES (12) — UX / Correctness

### FIX 13: Price formatting missing .toFixed(2)
FILE: frontend/src/pages/settings/SettingsPage.tsx
LINES: 1508, 1572
PROBLEM: `₹{coating.price}` and `₹{addon.price}` display without decimal formatting.
WHAT TO DO:
1. Line 1508: Replace `₹{coating.price}` with `₹{Number(coating.price).toFixed(2)}`
2. Line 1572: Replace `₹{addon.price}` with `₹{Number(addon.price).toFixed(2)}`

### FIX 14: Eye Test Form dead buttons
FILE: frontend/src/components/clinical/EyeTestForm.tsx
LINES: 348, 355, 1084, 1091
PROBLEM: "View History" and "Print" buttons have empty onClick handlers.
WHAT TO DO: Either:
a) Wire them to actual API calls, OR
b) Add `disabled` prop and a tooltip "Coming soon" so users don't think it's broken.

### FIX 15: Attendance check-in/out not wired
FILE: frontend/src/components/hr/AttendanceWidget.tsx
LINES: 70, 84
PROBLEM: Check-in and Check-out buttons have TODO comments, not wired to API.
WHAT TO DO: Wire to `POST /api/v1/hr/attendance/check-in` and `POST /api/v1/hr/attendance/check-out` endpoints.

### FIX 16: Feature toggles not wired
FILE: frontend/src/components/settings/FeatureToggles.tsx
LINE: 56
PROBLEM: Toggle changes don't call API.
WHAT TO DO: Wire to `PATCH /api/v1/settings/feature-toggles/{storeId}`.

### FIX 17: Store setup wizard not wired
FILE: frontend/src/components/settings/StoreSetupWizard.tsx
LINE: 99
PROBLEM: Store creation doesn't call API.
WHAT TO DO: Wire to `POST /api/v1/stores`.

### FIX 18: Integration test connection not wired
FILE: frontend/src/components/settings/IntegrationSettings.tsx
LINE: 92
PROBLEM: "Test Connection" button does nothing.
WHAT TO DO: Wire to appropriate integration test endpoint or disable with tooltip.

### FIX 19: Family prescriptions not wired
FILE: frontend/src/components/clinical/FamilyPrescriptionsView.tsx
LINE: 65
PROBLEM: "Add Family Member" does nothing.
WHAT TO DO: Wire to customer family API or disable.

### FIX 20: Bulk import not wired
FILE: frontend/src/pages/inventory/InventoryPage.tsx
LINE: 987
PROBLEM: Bulk product import doesn't call API.
WHAT TO DO: Wire to backend bulk import endpoint or disable.

### FIX 21: Remove 18 console.logs from dashboard
FILE: frontend/src/components/dashboard/RoleSpecificWidgets.tsx
LINES: 32,71,114,162,198,241,303,359,398,445,578,641,679,721,763,807,858,897
WHAT TO DO: Delete all 18 console.log statements.

### FIX 22: Remove 4 console.logs from POS
FILE: frontend/src/components/pos/POSLayout.tsx
LINES: 283, 315, 326, 526
WHAT TO DO: Delete all 4 console.log statements.

### FIX 23: Remove remaining 44 console.logs
FILES: (see dead code section for complete list)
WHAT TO DO: Run this command from frontend/src/:
```bash
find . -name "*.tsx" -o -name "*.ts" | xargs grep -n "console\.\(log\|warn\|error\)" | grep -v "ErrorBoundary"
```
Delete all except the one in ErrorBoundary.tsx (that one is appropriate).

### FIX 24: Settings page placeholder store IDs
FILE: frontend/src/pages/settings/SettingsPage.tsx
LINE: 2764
PROBLEM: Hardcoded 'BV-KOL-001' as placeholder.
WHAT TO DO: Replace with dynamic example from store context or use generic placeholder like 'BV-XXX-001'.

---

## LOW FIXES (10) — Maintainability / Performance

### FIX 25: Split SettingsPage.tsx (3,302 lines)
FILE: frontend/src/pages/settings/SettingsPage.tsx
WHAT TO DO:
1. Extract into separate files:
   - SettingsAuth.tsx (auth/password section)
   - SettingsStore.tsx (store configuration)
   - SettingsLens.tsx (lens config, coatings, addons)
   - SettingsNotifications.tsx
   - SettingsFeatureToggles.tsx
2. SettingsPage.tsx becomes a tab container importing these.

### FIX 26: Split POSLayout.tsx (1,924 lines)
FILE: frontend/src/components/pos/POSLayout.tsx
WHAT TO DO:
1. Extract:
   - POSCart.tsx (cart sidebar)
   - POSPayment.tsx (payment step)
   - POSReceipt.tsx (receipt preview)
   - POSInvoice.tsx (GST invoice)
2. POSLayout.tsx remains the wizard orchestrator.

### FIX 27: Split api.ts (1,884 lines)
FILE: frontend/src/services/api.ts
WHAT TO DO:
1. Split into domain modules:
   - api/auth.ts
   - api/inventory.ts
   - api/sales.ts
   - api/customers.ts
   - api/reports.ts
   - api/index.ts (re-exports all)
2. This enables tree-shaking — pages only import what they need.

### FIX 28: Split FinanceDashboard.tsx (1,465 lines)
FILE: frontend/src/pages/finance/FinanceDashboard.tsx
WHAT TO DO: Extract chart components, data processing, and filter panels.

### FIX 29: Split PurchaseManagementPage.tsx (1,415 lines)
FILE: frontend/src/pages/purchase/PurchaseManagementPage.tsx
WHAT TO DO: Extract PurchaseTable, OrderForm, SupplierPanel.

### FIX 30: Split EyeTestForm.tsx (1,115 lines)
FILE: frontend/src/components/clinical/EyeTestForm.tsx
WHAT TO DO: Extract EyeTestInput, EyeTestResults, PrescriptionBuilder.

### FIX 31: Add pagination to list pages
FILES: OrdersPage, CustomersPage, InventoryPage, ProductsPage
WHAT TO DO:
1. Backend: Add `skip` and `limit` query params to list endpoints (most already support this).
2. Frontend: Add pagination component at bottom of each list page.
3. Default page size: 50 items.

### FIX 32: Add search debounce
FILES: All search input components
WHAT TO DO:
1. Create a `useDebounce` hook:
   ```tsx
   function useDebounce<T>(value: T, delay: number = 300): T {
     const [debouncedValue, setDebouncedValue] = useState(value);
     useEffect(() => {
       const handler = setTimeout(() => setDebouncedValue(value), delay);
       return () => clearTimeout(handler);
     }, [value, delay]);
     return debouncedValue;
   }
   ```
2. Use it in every search input that triggers API calls.

### FIX 33: Standardize MongoDB collection access
FILES: backend/api/routers/vendor_returns.py, billing.py, admin.py, settings.py
WHAT TO DO: Replace all `db.collection_name`, `db["name"]`, and `db.db["name"]` with the consistent pattern `db.get_collection("name")`.

### FIX 34: Delete dead Shopify code
FILE: backend/api/routers/shopify.py (if exists)
WHAT TO DO: If the file exists, delete it. The router is already commented out in main.py. Either delete permanently or move to a `deprecated/` folder. Clean up any Shopify-related imports in other files.

---

## VERIFICATION AFTER FIXES
After applying all fixes, verify:
1. Run the backend with `uvicorn backend.api.main:app` — should start with 0 errors
2. Run `npm run build` in frontend/ — should compile with 0 errors
3. Test: Create an order with 1 lens + 1 frame → lens GST should be 5%, frame GST should be 5%
4. Test: As sales_bokaro1, try to apply 15% discount → should be blocked (cap is 10%)
5. Test: As sales_bokaro1, try to apply 5% discount on a LUXURY item → should be blocked (cap is 2%)
6. Test: Login → check browser DevTools console → should be clean (no log spam)
7. Test: Go to /customers/follow-ups → should show data for current store, not STORE001
8. Test: Go to HR page → should show all stores, not just Bokaro
```

---

# APPENDIX A: CODEBASE METRICS

| Metric | Value |
|--------|-------|
| Backend router files | 29 |
| Backend API endpoints | 387 |
| Frontend components/pages | 170 files (148 components) |
| Frontend routes | 47 (including redirects + 404) |
| Feature list: Built | 117 |
| Feature list: Partial | 12 |
| Feature list: Not built | 166 |
| Console.log statements | 70 |
| TODO comments | 13 |
| Hardcoded store IDs | 14 instances |
| Files > 1000 lines | 8 |
| Bare except clauses | 3 |
| Broken imports | 0 |
| Dead router files | 1 (Shopify, already disabled) |
| Core/engines dead code | 0 (engines/ dir doesn't exist) |

# APPENDIX B: ROUTE TABLE (47 routes)

| Path | Component | Access |
|------|-----------|--------|
| /login | LoginPage | Public |
| /unauthorized | UnauthorizedPage | Public |
| / | → /dashboard | Protected |
| /dashboard | DashboardPage | All roles |
| /dashboard/executive | ExecutiveDashboard | SUPERADMIN, ADMIN, AREA_MANAGER |
| /dashboard/analytics | EnterpriseAnalyticsDashboard | SUPERADMIN–STORE_MANAGER |
| /pos | POSPage | Sales roles + managers |
| /customers | CustomersPage | Sales roles + managers + opto |
| /customers/:id/360 | Customer360Dashboard | Sales roles + managers + opto |
| /customers/segmentation | CustomerSegmentation | SUPERADMIN–STORE_MANAGER |
| /customers/loyalty | LoyaltyProgram | SUPERADMIN–STORE_MANAGER |
| /customers/campaigns | CampaignManager | SUPERADMIN–STORE_MANAGER |
| /customers/referrals | ReferralTracker | SUPERADMIN–STORE_MANAGER |
| /customers/feedback | CustomerFeedback | SUPERADMIN–STORE_MANAGER |
| /customers/follow-ups | FollowUpDashboard | Sales + managers |
| /inventory | InventoryPage | Inventory roles + managers |
| /orders | OrdersPage | Most roles |
| /returns | ReturnsPage | Sales + managers |
| /clinical | ClinicalPage | Clinical + managers |
| /clinical/test | NewEyeTestPage | Clinical + managers |
| /clinical/history | TestHistoryPage | Clinical + managers |
| /prescriptions | PrescriptionsPage | Clinical + managers |
| /clinical/contact-lens | ContactLensFittingPage | Clinical + managers |
| /workshop | WorkshopPage | Workshop + managers |
| /purchase | PurchaseManagementPage | Purchase roles + managers |
| /purchase/orders | PurchaseOrderDashboard | Purchase roles |
| /purchase/vendors | VendorManagement | Purchase roles |
| /purchase/grn | GoodsReceiptNote | Purchase roles |
| /inventory/replenishment | StockReplenishment | Inventory + managers |
| /inventory/audit | StockAudit | Inventory + managers |
| /tasks | TaskManagementPage | Managers + accountant |
| /tasks/dashboard | TasksDashboard | Managers + sales |
| /tasks/checklists | TasksDashboard | Managers + sales |
| /hr | HRPage | HR roles + managers |
| /hr/payroll | PayrollDashboard | HR roles |
| /hr/incentives | IncentiveDashboard | HR roles |
| /reports | ReportsPage | Reporting roles |
| /reports/day-end | DayEndReport | Sales + managers |
| /reports/outstanding | OutstandingPaymentsReport | Managers + accountant |
| /settings | SettingsPage | SUPERADMIN–STORE_MANAGER |
| /setup | SetupPage | SUPERADMIN, ADMIN only |
| /jarvis | JarvisPage | SUPERADMIN only |
| /catalog/add | AddProductPage | Catalog roles |
| /storefront | StorefrontPage | SUPERADMIN, ADMIN |
| /finance/expenses | ExpenseTracker | Finance roles |
| /finance/dashboard | FinanceDashboard | Finance roles |
| * | NotFoundPage | Catch-all |

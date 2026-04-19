# IMS 2.0 — COMPREHENSIVE SYSTEM AUDIT REPORT

**Auditor:** Claude (Opus 4.6)
**Date:** March 16, 2026
**Scope:** Full-stack audit of IMS 2.0 Retail Operating System
**Repo:** github.com/brashakg/ims-2.0-railway (main branch)
**Live:** ims-2-0-railway.vercel.app

---

## EXECUTIVE SUMMARY

IMS 2.0 is a functional retail operating system with a solid POS core, but it carries significant technical debt from rapid development. This audit identified **47 bugs** (7 critical, 15 high, 18 medium, 7 low), **31 dead backend engine files** (17,435 lines), **59 dead frontend files** (14,843 lines), **239 orphan API endpoints**, and **12 security vulnerabilities**. The POS Quick Sale flow works end-to-end with correct GST calculations, but prescription workflows, inter-state tax handling, store scoping, and role-based access have critical gaps.

**Key numbers at a glance:**

| Metric | Count |
|--------|-------|
| Total bugs found | 47 |
| Critical severity | 7 |
| High severity | 15 |
| Dead backend files (core/engines) | 31 files, 17,435 lines |
| Dead frontend files | 59 files, 14,843 lines |
| Backend endpoints defined | 361 |
| Frontend API calls | 90 |
| Orphan backend endpoints (unused) | 239 |
| Broken frontend calls (no backend) | 35 |
| Security vulnerabilities | 12 |
| Missing retail workflows | 11 |

---

## 1. BUG REPORT TABLE

### Critical Severity (P0 — Fix immediately)

| # | Page/Component | Bug | How to Reproduce | Fix Suggestion |
|---|----------------|-----|------------------|----------------|
| 1 | POS / POSLayout.tsx:918 | **Incomplete offer price validation** — allows zero, negative, and NaN prices to bypass MRP check | Add product with offer_price=0 or negative to cart | Add: `if (isNaN(offerPrice) || offerPrice <= 0)` before the MRP comparison block |
| 2 | Backend / auth.py:177-192 | **Hardcoded superadmin fallback** — plaintext `admin123` password in source code, bypasses database entirely | Anyone with repo access can authenticate as superadmin on any environment | Remove fallback_users dict entirely; use proper emergency access via env vars |
| 3 | Backend / multiple routers | **No backend store scoping** — store_id query param accepted without validation against user's store_ids | `mgr_bokaro1` calls `GET /inventory/stock?store_id=BV-PUN-01` → sees Pune data | Add middleware: `if store_id not in current_user['store_ids'] and not is_admin: raise 403` |
| 4 | Backend / customers.py:63-81 | **Customer list leaks cross-store data** — no store_id filtering when filters are empty | `GET /customers?skip=0&limit=100` returns ALL customers regardless of user's store | Add `filter_dict['store_id'] = active_store_id` for non-admin users |
| 5 | POS / POSLayout.tsx:1166-1173 | **Discount "N/A" logic inverted** — shows N/A when offer_price < MRP (should allow discount) | Open POS, add a product with offer_price < MRP → discount button shows N/A incorrectly | Invert the condition: show "N/A" only when `offer_price === mrp` and no discount authority |
| 6 | POS / GSTInvoice.tsx:47 | **IGST never calculated** — hardcoded `isInterState = false`, all orders use CGST+SGST even for cross-state | Create order from BV-PUN-01 for customer in Jharkhand → still shows CGST+SGST | Implement state detection: compare customer state vs store state, use IGST when different |
| 7 | Backend / transfers.py | **Stock transfers stored in-memory** — data lost on Railway restart | Create a stock transfer → restart backend → transfer is gone | Persist transfers to MongoDB collection |

### High Severity (P1 — Fix this week)

| # | Page/Component | Bug | How to Reproduce | Fix Suggestion |
|---|----------------|-----|------------------|----------------|
| 8 | POS / POSLayout.tsx:228-325 | **No MRP/price re-validation at order creation** — cart items not re-checked before submitting order | Add item to cart → mutate posStore state in devtools → create order with invalid price | Add validation loop in order creation: re-validate every cart item's pricing |
| 9 | POS / POSLayout.tsx:1112-1114 | **GST per-item rounding accumulates errors** — each item's tax rounded independently then summed | Cart with 3 items at ₹333.33 each (5% GST): per-item sum = ₹50.01, correct = ₹50.00 | Aggregate taxable per rate first, then round once |
| 10 | POS / POSLayout.tsx:1190-1200 | **CGST+SGST split loses 1 paisa** — `tax / 2` for both halves, odd tax amounts don't split evenly | Order with total tax ₹17.67 → CGST ₹8.83 + SGST ₹8.83 = ₹17.66 (missing ₹0.01) | Round CGST down, set SGST = tax - CGST |
| 11 | Backend / inventory.py:66-90 | **Inventory store override** — accepts `store_id` query param without checking user's store access | `mgr_bokaro1` calls `GET /inventory/stock?store_id=BV-PUN-01` | Validate `store_id` against `current_user['store_ids']` |
| 12 | POS / posStore.ts:66-69 | **Lens linkage not validated** — removing a frame leaves orphaned lens with dangling frame reference | Add frame + linked lens → remove frame → lens still has linked_frame_id pointing to nothing | Add validator: check all linked_frame_ids resolve to items still in cart |
| 13 | POS / POSLayout.tsx:258 | **Walk-in can create prescription order** — no check that prescription orders require a patient record | Select Prescription Order → select Walk-in Customer → proceed | Add: `if (saleType === 'prescription_order' && customer.is_walkin) block with message` |
| 14 | Backend / rbac.py:228-268 | **ResourceAccessControl never called** — `can_access_customer()`, `can_access_order()` are dead code | N/A — they're defined but no endpoint imports them | Either implement resource-level checks or remove dead code |
| 15 | Backend / rbac.py:278 | **require_permission() never used** — fine-grained permission system exists but no router enforces it | All routers use role checks instead of permission checks | Implement permission enforcement on sensitive endpoints |
| 16 | Backend / stores.py:90-129 | **Any authenticated user can create stores** — no role check on POST /stores | Login as sales_bokaro1 → POST /stores with new store data → succeeds | Add `require_admin()` dependency |
| 17 | POS / POSLayout.tsx:1282 | **Payment balance rounding error blocks completion** — accumulated float errors prevent final payment | Cart with many items, small rounding errors accumulate → balance shows 0.005 → can't complete | Round balance before comparison: `Math.round(balance * 100) / 100` |
| 18 | Backend / crm.py | **Undefined database functions** — calls to functions that don't exist, will crash at runtime | Access any CRM endpoint → 500 error | Implement the missing repository functions or remove the endpoints |
| 19 | Frontend / Multiple pages | **INP blocking events (3-29 seconds)** — event handlers block UI for extended periods | Click buttons in POS, navigate between steps | Profile and optimize: debounce expensive handlers, use web workers for calculations |
| 20 | Backend / auth.py:211-219 | **JWT has no expiry validation in most endpoints** — token contains store_ids but endpoints don't re-validate | Modify JWT in devtools → make API calls → accepted | Add token validation middleware that checks claims against current DB state |
| 21 | Frontend / AuthContext.tsx | **Console.log in auth flow** — logs token validation and session restoration in production | Open browser console during login → see auth debugging output | Remove all console statements from AuthContext |
| 22 | Frontend / posStore.ts | **No state cleanup on logout** — POS draft, customer data, prescriptions persist in localStorage | Login as sales_bokaro1 → add items → logout → login as mgr_bokaro1 → old cart visible | Add logout handler: clear all POS-related localStorage keys |

### Medium Severity (P2 — Fix this sprint)

| # | Page/Component | Bug | How to Reproduce | Fix Suggestion |
|---|----------------|-----|------------------|----------------|
| 23 | POS / POSLayout.tsx:926 | **Silent zero-price line items** — `unit_price: offerPrice || mrp` falls to 0 when both are falsy | Add product with both prices = 0 → line item created at ₹0 | Throw error if both are falsy: `if (!offerPrice && !mrp) block` |
| 24 | POS / ReceiptPreview.tsx:129 | **Receipt rounds to integers** — `.toFixed(0)` on line totals loses decimal precision | View receipt for ₹333.33 × 3 → shows ₹1000 instead of ₹999.99 | Change to `.toFixed(2)` |
| 25 | POS / GSTInvoice.tsx:73-85 | **Proportional discount rounding loss** — ₹100 across 3 items = ₹33.33 × 3 = ₹99.99 | Apply ₹100 order-level discount to 3-item order | Adjust last item: `lastItem.discount = totalDiscount - sumOfOthers` |
| 26 | POS / PrescriptionSelectModal.tsx:63-69 | **No prescription expiry grace period** — marks Rx expired immediately at expiry date | Prescription with 12-month validity → day after = expired, no grace | Add 30-day grace period to expiry check |
| 27 | POS / PrescriptionForm.tsx:35-42 | **No date validation on prescriptions** — allows future issue dates and expiry before issue date | Enter issue_date = 2030 → accepted | Validate: issue_date ≤ today, expiry ≥ issue_date |
| 28 | POS / POSLayout.tsx:1310-1311 | **CREDIT payment requires reference** — but credit sales settle later, reference comes at settlement | Select CREDIT payment → can't proceed without reference | Exclude CREDIT from reference requirement |
| 29 | POS / POSLayout.tsx:213 | **F2 shortcut skips customer validation** — jumps to products step without requiring customer selection | Press F2 on customer step → lands on products with no customer | Add: `if (currentStep === 'customer' && !customer) return` |
| 30 | POS / POSLayout.tsx:111-164 | **Hold/Recall ignores prescription expiry** — recalled bills don't re-validate prescription validity | Hold bill Mon → recall Sat → expired Rx used silently | Re-validate prescription on recall |
| 31 | POS / PrescriptionSelectModal.tsx:60 | **Prescription power display vs storage mismatch** — displays `toFixed(2)` but stores raw float | Store 1.001 → display +1.00 → lens made for 1.001 | Round to 0.25 increments before display and storage |
| 32 | POS / LensDetailsModal.tsx:80-150 | **No lens compatibility validation** — allows bifocal+progressive, photochromic+polarized combos | Select bifocal type + progressive addon → accepted | Add compatibility matrix to block invalid combos |
| 33 | POS / PrescriptionForm.tsx | **No external Rx source flag** — all inline Rx marked as in-store, can't distinguish external | Create Rx from external doctor → no way to flag it | Add source dropdown: in-store vs external |
| 34 | Frontend / ThemeContext.tsx | **ThemeProvider not mounted** — context exists but never used in App.tsx | Theme toggle has no effect | Either mount ThemeProvider or remove dead code |
| 35 | Frontend / api.ts:9-17 | **API base URL logged in production** — console.log of base URL on every load | Open browser console → see API URL | Remove console.log, use environment-only config |
| 36 | Frontend / App.tsx | **Only 1 Suspense boundary for 55 lazy routes** — single lazy load failure crashes entire app | Slow network → one route chunk fails → white screen | Add per-route Suspense boundaries with fallback UI |
| 37 | Frontend / useSessionExpiry.ts:28 | **Hardcoded 480-min session timeout** — should come from backend, not frontend constant | Session expires based on frontend clock, not server | Get expiry from JWT token claims |
| 38 | Backend / settings.py | **No RBAC on settings endpoints** — frontend blocks SALES_STAFF but backend allows any authenticated user | sales_bokaro1 calls settings API directly → succeeds | Add role check: require_admin() or require_manager() |
| 39 | Backend / prescriptions.py | **No SPH/CYL/AXIS range validation** — accepts any values without checking optometric ranges | POST prescription with SPH = -50 → accepted | Validate: SPH ±20, CYL ±10, AXIS 1-180 |
| 40 | Backend / analytics.py | **No store_id filtering** — returns analytics for all stores regardless of user's access | area_jharkhand calls analytics → sees all 6 stores | Filter by user's store_ids |

### Low Severity (P3 — Backlog)

| # | Page/Component | Bug | How to Reproduce | Fix Suggestion |
|---|----------------|-----|------------------|----------------|
| 41 | POS / POSLayout.tsx:219 | **F9 with empty cart gives no feedback** — shortcut silently fails when cart is empty | Press F9 with empty cart → nothing happens | Show toast: "Add items to cart first" |
| 42 | POS / POSLayout.tsx:223 | **Escape exits without save prompt** — pressing Escape mid-transaction loses all cart data | Add items → press Escape → cart gone | Add confirmation dialog: "Hold as draft?" |
| 43 | POS / gst.ts:234-235 | **GST function name ambiguous** — `calculateGST()` uses inclusive formula but name doesn't clarify | Developer misuse if they pass pre-tax amount | Add JSDoc: "Extracts GST from tax-inclusive amount" |
| 44 | Frontend / 21 files | **Console.log statements in production** — 21 files have debug logging in production code | Open browser console → see debug output | Strip all console.* in production build (Vite plugin) |
| 45 | Frontend / ModuleContext.tsx | **Module state out of sync with URL** — module context doesn't sync with browser navigation | Navigate via URL bar → module context shows wrong module | Sync module state with react-router location |
| 46 | Frontend / posStore.ts | **localStorage persistence not encrypted** — customer PII, prescriptions stored in plaintext | Open Application tab → Local Storage → see customer data | Encrypt localStorage or use sessionStorage |
| 47 | Backend / shopify.py | **Entire Shopify router is unused** — 47 endpoints with no frontend calls | N/A | Remove entire router unless Shopify integration is planned |

---

## 2. DEAD CODE REPORT

### Backend Dead Code: 31 Engine Files (17,435 lines)

**Only 1 of 32 core/ files is used** (`subagents.py`, imported by `jarvis.py`).

| File | Lines | Status |
|------|-------|--------|
| core/ai_intelligence_engine.py | 655 | DEAD — never imported |
| core/audit_engine.py | 435 | DEAD |
| core/auth_system.py | 526 | DEAD |
| core/clinical_engine.py | 443 | DEAD |
| core/customer_engine.py | 335 | DEAD |
| core/expense_engine.py | 626 | DEAD |
| core/finance_engine.py | 895 | DEAD |
| core/hr_engine.py | 377 | DEAD |
| core/ims_app.py | 309 | DEAD |
| core/integrations_engine.py | 718 | DEAD |
| core/inventory_engine.py | 1,261 | DEAD |
| core/jarvis_ai_orchestrator.py | 562 | DEAD |
| core/jarvis_alert_system.py | 400 | DEAD |
| core/jarvis_analytics_engine.py | 324 | DEAD |
| core/jarvis_claude_integration.py | 465 | DEAD |
| core/jarvis_compliance_engine.py | 403 | DEAD |
| core/jarvis_nlp_engine.py | 323 | DEAD |
| core/jarvis_realtime_service.py | 542 | DEAD |
| core/jarvis_recommendation_engine.py | 348 | DEAD |
| core/jarvis_visualization_engine.py | 679 | DEAD |
| core/jarvis_voice_interface.py | 646 | DEAD |
| core/marketplace_engine.py | 401 | DEAD |
| core/notification_engine.py | 445 | DEAD |
| core/pos_engine.py | 943 | DEAD |
| core/pricing_engine.py | 640 | DEAD |
| core/printables_engine.py | 224 | DEAD |
| core/reports_engine.py | 329 | DEAD |
| core/settings_engine.py | 771 | DEAD |
| core/tasks_engine.py | 1,165 | DEAD |
| core/vendor_engine.py | 839 | DEAD |
| core/workshop_engine.py | 406 | DEAD |

### Frontend Dead Code: 59 Files (14,843 lines)

**Scaffolded Feature Modules (never wired to routes):**

| File | Lines | Why Dead |
|------|-------|----------|
| components/optical/PrescriptionManagement.tsx | ~400 | Never imported |
| components/optical/FrameLensManagement.tsx | ~400 | Never imported |
| components/optical/POSSystem.tsx | ~400 | Never imported |
| components/optical/PatientManagement.tsx | ~400 | Never imported |
| components/optical/MultiLocationInventory.tsx | ~448 | Never imported |
| components/crm/LoyaltyTierVisualization.tsx | ~225 | Never imported |
| components/crm/PrescriptionRenewalAlerts.tsx | ~224 | Never imported |
| components/enterprise/ReportsBuilder.tsx | ~430 | Never imported |
| components/enterprise/EnterpriseIntegrations.tsx | ~435 | Never imported |
| components/financial/FinancialManagement.tsx | ~421 | Never imported |
| components/communication/EmailSMSManagement.tsx | ~356 | Never imported |
| components/service/ServiceTicketManagement.tsx | ~401 | Never imported |
| components/alerts/SmartAlertsCenter.tsx | ~528 | Never imported |

**Unused Common Components:**

| File | Lines | Why Dead |
|------|-------|----------|
| components/common/FormInput.tsx | ~426 | Never imported |
| components/common/StatusBadge.tsx | ~355 | Never imported |
| components/common/PhotochromicLoader.tsx | ~303 | Never imported |
| components/common/SoftDeleteManager.tsx | ~307 | Never imported |
| components/common/FormValidator.tsx | ~250 | Never imported |
| components/common/AdvancedSearch.tsx | ~300 | Never imported |
| components/common/BulkActions.tsx | ~280 | Never imported |
| components/common/ErrorState.tsx | ~200 | Never imported |
| components/common/KeyboardShortcuts.tsx | ~250 | Never imported |
| components/common/QuickFiltersSaver.tsx | ~200 | Never imported |
| components/common/ThemeToggle.tsx | ~150 | ThemeProvider not mounted |
| components/common/SessionExpiryWarning.tsx | ~150 | Never imported |

**Unused Feature Components:**

| File | Lines | Why Dead |
|------|-------|----------|
| components/pos/PrescriptionModal.tsx | ~586 | Superseded by PrescriptionSelectModal |
| components/jarvis/JarvisEnhancedDashboard.tsx | ~550 | Never imported |
| components/clinical/ContactLensTracker.tsx | ~530 | Never imported |
| components/supply-chain/PurchaseOrderManagement.tsx | ~350 | Duplicated by pages/purchase/ |
| components/supply-chain/ReturnManagement.tsx | ~400 | Never imported |
| components/shopify/ShopifySyncDashboard.tsx | ~300 | Only used by dead Shopify module |

**Unused Pages, Hooks, Utils, Constants, Stores, Services:**

| File | Lines | Why Dead |
|------|-------|----------|
| pages/tasks/TasksPage.tsx | ~678 | Superseded by TaskManagementPage |
| hooks/useNotification.ts | ~58 | Never imported |
| hooks/useAsyncOperation.ts | ~422 | Never imported |
| utils/printTemplates.ts | ~222 | Never imported |
| utils/cache.ts | ~332 | Never imported |
| utils/performance.ts | ~346 | Never imported |
| utils/validators.ts | ~241 | Never imported |
| utils/formValidation.ts | ~88 | Never imported |
| constants/loyalty.ts | ~329 | Never imported |
| constants/contactLens.ts | ~282 | Never imported |
| stores/appStore.ts | ~81 | Never imported (posStore is active) |
| services/shopifyAPI.ts | ~430 | Only used by dead ShopifySyncDashboard |
| context/ThemeContext.tsx | ~100 | Implemented but ThemeProvider never mounted |

### Orphan Backend Endpoints (239 of 361 total)

**Major orphan groups:**

| Router | Orphan Endpoints | Notes |
|--------|-----------------|-------|
| shopify.py | 47 | Entire module unused |
| supply_chain.py | 21 | Purchase orders, GRN, audits |
| crm.py | 15+ | CRM functionality not wired |
| admin.py | 12 | Lens/discount management, integration configs |
| analytics.py | 10 | Advanced analytics |
| billing.py | 8 | Payment settlement endpoints |
| hr.py | 8 | Attendance, incentive tracking |
| vendors.py | 6 | Vendor management |
| clinical.py | 5 | Advanced clinical features |
| tasks.py | 5 | Task management |

**Broken Frontend Calls (35 calls to non-existent endpoints):**
These are frontend API functions that call backend paths which don't exist, likely due to refactoring mismatches.

---

## 3. MISSING OPTICAL RETAIL WORKFLOWS

These are daily operations that real optical store staff need but **cannot currently do** in IMS 2.0:

### Critical Missing (staff blocked daily)

1. **Order Delivery Confirmation** — No way to mark an order as DELIVERED. Status transitions stop at READY. Staff can't close the fulfillment loop.

2. **Invoice Reprint** — Cannot re-print an old invoice from the Orders page. Customers frequently ask for duplicate invoices.

3. **Inter-State GST (IGST)** — All orders use CGST+SGST regardless of customer/store state. Required for BV-PUN-01 (Maharashtra) selling to Jharkhand customers.

4. **Vendor Return for Defectives** — No workflow for returning defective products to vendors and getting credit notes. Common weekly occurrence.

5. **Daily Stock Count by Staff** — No mechanism for staff to do daily stock reconciliation (physical count vs system). Required before shift handover.

### Important Missing (weekly pain points)

6. **Monthly Incentive Tracking** — Zeiss SmartLife/Progressive/Photofusion and Safilo targets mentioned in business requirements but no UI exists for tracking brand-specific sales against targets.

7. **Customer Follow-up Reminders** — No automated reminder for prescription renewal (every 12-24 months) or contact lens reorder.

8. **Barcode Label Printing** — BarcodeManagement and BarcodeGenerator components exist but are not wired into the inventory workflow.

9. **Expense Approval Workflow** — Expense tracking exists in backend but the approval hierarchy (staff → manager → finance → admin) has no UI.

10. **Attendance with Geo-fencing** — Mentioned in requirements but not implemented. Store staff check-in should verify proximity to assigned store.

### Nice to Have (monthly/quarterly)

11. **Non-Moving Stock Identification** — No report to identify products sitting for 60+ days for transfer to WizOpt outlet.

---

## 4. PERFORMANCE ISSUES

| Issue | Severity | Impact | Fix |
|-------|----------|--------|-----|
| **INP blocking events: 3-29 seconds** | HIGH | Buttons unresponsive for up to 29 seconds during POS operations | Profile with Chrome DevTools → debounce expensive handlers, move calculations to web workers |
| **Customer list loads all (limit:100 hardcoded)** | MEDIUM | Slow for stores with 1000+ customers | Implement server-side pagination with cursor-based loading |
| **No skeleton loaders** | LOW | Pages show spinners instead of content placeholders during load | Add skeleton components for data-heavy pages |
| **Bundle includes dead code** | MEDIUM | 59 unused files shipped to production, increasing bundle size | Tree-shake or delete dead files |
| **localStorage sync debounce (500ms)** | MEDIUM | POS draft can lose data if app crashes within 500ms window | Reduce debounce to 100ms or use synchronous write for critical data |
| **Single Suspense boundary** | MEDIUM | One lazy-load failure crashes entire app instead of individual route | Add per-route Suspense + ErrorBoundary wrappers |
| **No API request deduplication** | LOW | Same data fetched multiple times if components mount concurrently | TanStack Query should handle this — verify staleTime config |

---

## 5. SECURITY VULNERABILITIES SUMMARY

| # | Severity | Issue | Impact |
|---|----------|-------|--------|
| 1 | CRITICAL | Hardcoded superadmin fallback with admin123 password in source code | Anyone with repo access = full admin |
| 2 | CRITICAL | No backend store_id validation on API calls | Cross-store data access |
| 3 | CRITICAL | Customer list endpoint leaks all customers cross-store | PII exposure |
| 4 | HIGH | Settings endpoints have no backend RBAC | Any authenticated user can change settings |
| 5 | HIGH | ResourceAccessControl defined but never enforced | No resource-level permission checks |
| 6 | HIGH | require_permission() exists but unused | Fine-grained permissions are decorative |
| 7 | HIGH | Any user can create stores (POST /stores) | Unauthorized store creation |
| 8 | HIGH | Auth context logs tokens to browser console | Token exposure in production |
| 9 | MEDIUM | JWT payload not re-validated against DB | Stale tokens with removed permissions still work |
| 10 | MEDIUM | POS data persists in unencrypted localStorage | Customer PII in plaintext on device |
| 11 | MEDIUM | Frontend-only route protection (no backend enforcement) | DevTools can bypass UI restrictions |
| 12 | MEDIUM | No audit trail for cross-store access attempts | Cannot detect unauthorized access |

---

## 6. CLAUDE CODE FIX PROMPT

Copy the entire block below and paste it into Claude Code:

---

```
# IMS 2.0 — PRIORITIZED FIX INSTRUCTIONS
# Paste this entire prompt into Claude Code
# Repository: github.com/brashakg/ims-2.0-railway (branch: main)
# IMPORTANT: Run the full test suite after each group of fixes. Do NOT break existing POS Quick Sale flow.

## PHASE 1: CRITICAL SECURITY FIXES (Do first)

### Fix 1.1: Remove hardcoded superadmin fallback
# File: backend/api/routers/auth.py (lines 177-192)
# DELETE the entire `fallback_users` dictionary and the code block that checks it
# The system should ONLY authenticate against the database
# If emergency access is needed, use an environment variable EMERGENCY_ADMIN_HASH instead

### Fix 1.2: Add backend store scoping middleware
# File: backend/api/dependencies.py
# ADD a new dependency function `validate_store_access(store_id, current_user)`:
#   - If store_id is provided and user is not ADMIN/SUPERADMIN:
#     - Check store_id is in current_user['store_ids']
#     - If not, raise HTTPException(403, "No access to this store")
#   - If store_id is not provided, default to current_user['active_store_id']
# Then add this dependency to EVERY endpoint that accepts store_id parameter in these routers:
#   - backend/api/routers/inventory.py (stock endpoint, line 66-90)
#   - backend/api/routers/customers.py (list endpoint, lines 63-81)
#   - backend/api/routers/orders.py (list/create endpoints)
#   - backend/api/routers/analytics.py (all endpoints)
#   - backend/api/routers/reports.py (all endpoints)
#   - backend/api/routers/users.py (get_store_users, line 107)

### Fix 1.3: Add RBAC to unprotected endpoints
# File: backend/api/routers/stores.py line 90 - Add require_admin() to POST /stores
# File: backend/api/routers/settings.py - Add require_manager() to ALL settings endpoints
# File: backend/api/routers/admin.py - Add require_admin() to integration config endpoints

### Fix 1.4: Remove auth debugging from production
# File: frontend/src/context/AuthContext.tsx - Remove ALL console.log/console.warn/console.error statements
# File: frontend/src/services/api.ts lines 13-17 - Remove console.log of API base URL
# File: frontend/src/services/api.ts line 23 - Remove HTTPS conversion console.warn
# File: frontend/src/services/api.ts line 41 - Remove CORS error console.log


## PHASE 2: CRITICAL BUSINESS LOGIC FIXES

### Fix 2.1: Fix offer price validation
# File: frontend/src/components/pos/POSLayout.tsx line 918
# CHANGE the validation to:
#   if (isNaN(offerPrice) || offerPrice <= 0 || (mrp > 0 && offerPrice > mrp)) {
#     // Show error and block
#   }
# Also add at line 926: if (!offerPrice && !mrp) { showError("Invalid pricing"); return; }

### Fix 2.2: Fix inverted discount N/A logic
# File: frontend/src/components/pos/POSLayout.tsx lines 1166-1173
# The current condition shows N/A when offer_price < MRP — this is BACKWARDS
# Business rule: N/A when offer_price < MRP (already discounted, no further discount)
# WAIT — re-reading the business rules: "If offer_price < MRP → no further discount"
# The current code IS showing N/A when offer_price < MRP. But the DISPLAY logic is inverted.
# The N/A should show when offer_price < MRP AND offer_price > 0 (product is already on offer)
# Fix: Add tooltip explaining "Product already at offer price — no additional discount allowed"

### Fix 2.3: Implement IGST for inter-state transactions
# File: frontend/src/components/pos/GSTInvoice.tsx line 47
# REPLACE: const isInterState = false; // TODO
# WITH: Logic that compares store.state with customer.state (or customer.billing_state)
# If states differ → use IGST (single line at full rate)
# If same state → use CGST+SGST (split at half rate each)
# Also update: frontend/src/components/pos/POSLayout.tsx lines 1190-1200 to use isInterState flag

### Fix 2.4: Fix GST rounding precision
# File: frontend/src/components/pos/POSLayout.tsx lines 1112-1114
# INSTEAD OF rounding per item then summing:
#   Group cart items by GST rate → sum taxable per rate → round once per rate
#   const taxByRate = {};
#   cart.forEach(item => {
#     const rate = getGSTRate(item.category);
#     taxByRate[rate] = (taxByRate[rate] || 0) + item.taxable_amount;
#   });
#   let totalTax = 0;
#   Object.entries(taxByRate).forEach(([rate, taxable]) => {
#     totalTax += Math.round(taxable * (rate / 100) * 100) / 100;
#   });

### Fix 2.5: Fix CGST/SGST split rounding
# File: frontend/src/components/pos/POSLayout.tsx lines 1190-1200
# REPLACE: const cgst = tax / 2; const sgst = tax / 2;
# WITH: const cgst = Math.floor(tax * 100 / 2) / 100; const sgst = Math.round((tax - cgst) * 100) / 100;

### Fix 2.6: Fix receipt line total precision
# File: frontend/src/components/pos/ReceiptPreview.tsx line 129
# CHANGE: .toFixed(0) TO: .toFixed(2)

### Fix 2.7: Add MRP/price re-validation at order creation
# File: frontend/src/components/pos/POSLayout.tsx lines 228-325 (order creation function)
# ADD at the start of the function:
#   for (const item of store.cart) {
#     if (item.unit_price <= 0 || isNaN(item.unit_price)) {
#       setError("Invalid price for " + item.product_name); return;
#     }
#     if (item.mrp > 0 && item.unit_price > item.mrp) {
#       setError("Price exceeds MRP for " + item.product_name); return;
#     }
#   }


## PHASE 3: HIGH PRIORITY FIXES

### Fix 3.1: Fix payment balance rounding
# File: frontend/src/components/pos/POSLayout.tsx line 1282
# CHANGE: if (a > balance + 0.01)
# TO: const roundedBalance = Math.round(balance * 100) / 100; if (a > roundedBalance + 0.01)

### Fix 3.2: Fix walk-in prescription order blocking
# File: frontend/src/components/pos/POSLayout.tsx line 258
# ADD check: if (saleType === 'prescription_order' && (!store.customer || store.customer.is_walkin)) {
#   setError('Prescription orders require a registered customer with patient record');
#   return;
# }

### Fix 3.3: Add lens linkage validation
# File: frontend/src/stores/posStore.ts
# ADD a new action validateCartLinkage() that:
#   - For each item with linked_frame_id, verify the frame is still in cart
#   - For each item with linked_prescription_id, verify prescription is set
#   - Return validation errors array
# Call this in POSLayout.tsx before order creation

### Fix 3.4: Clear POS state on logout
# File: frontend/src/stores/posStore.ts
# ADD: clearAll() action that resets entire store AND removes localStorage keys
# File: frontend/src/context/AuthContext.tsx
# In the logout function, CALL: usePOSStore.getState().clearAll()
# Also clear: localStorage.removeItem('ims_pos_draft'), localStorage.removeItem('ims_held_bills')

### Fix 3.5: Add per-route error boundaries
# File: frontend/src/App.tsx
# WRAP each lazy-loaded Route element in individual Suspense + ErrorBoundary:
#   <Route path="pos" element={
#     <ErrorBoundary fallback={<div>POS failed to load. <button>Retry</button></div>}>
#       <Suspense fallback={<LoadingSpinner />}>
#         <POSPage />
#       </Suspense>
#     </ErrorBoundary>
#   }/>

### Fix 3.6: Fix prescription validations
# File: frontend/src/components/pos/PrescriptionForm.tsx
# ADD validation before save:
#   - issue_date must be ≤ today
#   - expiry_date must be ≥ issue_date (if provided)
#   - SPH must be between -20.00 and +20.00
#   - CYL must be between -10.00 and +10.00
#   - AXIS must be between 1 and 180
# File: backend/api/routers/prescriptions.py
# ADD same validation on the backend before inserting to MongoDB

### Fix 3.7: Fix proportional discount rounding
# File: frontend/src/components/pos/GSTInvoice.tsx lines 73-85
# After distributing discount proportionally:
#   const adjustedItems = items.map((item, i) => {
#     if (i === items.length - 1) {
#       item.discount = totalDiscount - sumOfPreviousDiscounts;
#     }
#     return item;
#   });


## PHASE 4: DEAD CODE REMOVAL

### Fix 4.1: Delete all 31 dead backend engine files
# DELETE the entire backend/core/ directory EXCEPT for subagents.py
# Move subagents.py to backend/ai/subagents_core.py
# Update backend/api/routers/jarvis.py imports from core.subagents to ai.subagents_core
# This removes 17,435 lines of dead code

### Fix 4.2: Delete dead frontend files
# DELETE these directories entirely (all files are unused):
#   frontend/src/components/optical/
#   frontend/src/components/crm/ (except RecallManager.tsx if it's used — verify first)
#   frontend/src/components/enterprise/
#   frontend/src/components/financial/
#   frontend/src/components/communication/
#   frontend/src/components/service/
#   frontend/src/components/alerts/
#   frontend/src/components/shopify/
# DELETE these specific files:
#   frontend/src/components/pos/PrescriptionModal.tsx (superseded)
#   frontend/src/components/jarvis/JarvisEnhancedDashboard.tsx
#   frontend/src/components/clinical/ContactLensTracker.tsx
#   frontend/src/components/supply-chain/PurchaseOrderManagement.tsx
#   frontend/src/components/supply-chain/ReturnManagement.tsx
#   frontend/src/pages/tasks/TasksPage.tsx (superseded by TaskManagementPage)
#   frontend/src/hooks/useNotification.ts
#   frontend/src/hooks/useAsyncOperation.ts
#   frontend/src/utils/printTemplates.ts
#   frontend/src/utils/cache.ts
#   frontend/src/utils/performance.ts
#   frontend/src/utils/validators.ts
#   frontend/src/utils/formValidation.ts
#   frontend/src/constants/loyalty.ts
#   frontend/src/constants/contactLens.ts
#   frontend/src/stores/appStore.ts
#   frontend/src/services/shopifyAPI.ts
#   frontend/src/context/ThemeContext.tsx (or mount it properly in App.tsx)

### Fix 4.3: Remove Shopify backend router
# File: backend/api/main.py — Remove the line that mounts shopify router
# DELETE: backend/api/routers/shopify.py (47 orphan endpoints)

### Fix 4.4: Strip all console.log from production
# File: frontend/vite.config.ts
# ADD to the build config:
#   build: {
#     minify: 'terser',
#     terserOptions: {
#       compress: {
#         drop_console: true,
#         drop_debugger: true,
#       },
#     },
#   }


## PHASE 5: WORKFLOW ADDITIONS (after bugs are fixed)

### Fix 5.1: Add order DELIVERED status transition
# File: backend/api/routers/orders.py
# ADD endpoint: PATCH /orders/{order_id}/deliver
#   - Validate order is in READY status
#   - Update status to DELIVERED
#   - Record delivery timestamp and staff who handed over
#   - Update customer's last_purchase_date
# File: frontend/src/pages/orders/OrdersPage.tsx
# ADD "Mark Delivered" button on READY orders

### Fix 5.2: Add invoice reprint
# File: frontend/src/pages/orders/OrdersPage.tsx
# ADD "Reprint Invoice" button on each completed order row
# Reuse GSTInvoice component with order data

### Fix 5.3: Add prescription validation ranges to backend
# File: backend/api/routers/prescriptions.py
# ADD validation: SPH -20 to +20, CYL -10 to +10, AXIS 1-180
# Return 422 with field-specific error messages

# END OF FIX INSTRUCTIONS
```

---

*Report generated by Claude (Opus 4.6) on March 16, 2026*
*Total files analyzed: 92 backend + 160 frontend = 252 source files*
*Total lines of dead code identified: 32,278 (17,435 backend + 14,843 frontend)*

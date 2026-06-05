# Per-module deep dive (Wave-1 audit)

> Consolidated per-module state, defects + recommendations from the Total Coverage Wave-1 seam audit (10 lanes, each confirmed against real code). File:line for every defect is in [`../IMS2_WAVE2_BACKLOG.md`](../IMS2_WAVE2_BACKLOG.md). Status reflects what's since been **✅ fixed/merged**.
> Overall: the app is **feature-complete + mature**; the value is FE↔BE seam defects, not greenfield.

## POS / Orders / Returns
**State:** healthy. Core money/GST/discount/idempotency seams are solid (BE recomputes authoritatively; prior #368/#373/#376 hardened it). **Fixed:** ✅ payment-badge enum (UNPAID/CREDIT/REFUNDED). **Open:** EMI tender records only the down-payment; `item_note`/`order_type` dropped on create; cancelOrder reason-as-body; loyalty redeemed before order exists. **Rec:** persist the dropped fields; defer/reverse loyalty redeem to post-create.

## Finance / Accounting / GST
**State:** strong (multi-entity/4-GSTIN, real P&L). **Fixed:** ✅ payout CSV download; ✅ GST-summary CGST/SGST-vs-IGST split. **Open:** fabricated default budget allocations; Tally JV drops IGST; P&L COGS 60% fallback can present a fabricated margin. **Rec:** honest empty-state for budgets; IGST ledger in the Tally voucher; flag estimated-COGS.

## Inventory / Transfers / Stock
**State:** ledger/replenishment/power-grid healthy; the inter-store Transfer UI was the broken cluster. **Fixed:** ✅ transfer envelope/from_location/status-enum/`.filter` seams (via #465). **Open:** category-scoped stock count filters a non-existent field; receive-payload line.id assumption. **Rec:** resolve product_ids by category first; thread the real line id.

## Catalog / Pricing / Lens catalog
**State:** pricing-caps resolver canonical/correct. **Open (money):** `discount_category` (MASS/PREMIUM/LUXURY) dropped on product create → cap falls back to MASS 15% on LUXURY (`products.py:276`). **Minor:** legacy split-brain importer, dead `bulkImportProducts`, lens-pricing camel-vs-snake keys. **Rec:** persist discount_category on create; retire the legacy importer.

## Clinical / Prescriptions / Lens config
**State:** queue/family-Rx/CL-fitting/versions well-wired. **Fixed:** ✅ TestHistory crash (now uses `readEyePower`). **Open:** progression-delta reads wrong keys → blank; Rx-history passes queue/test id as customer_id → 404. **Rec:** align the delta field names; guard the Rx-history button on a real customerId.

## HR / Payroll / Entities
**State:** modern payroll-run/approve/lock + salary config + attendance grid CLEAN. **Open (broken):** PayrollDashboard salary-sheet/payslips + EmployeeSelfService read **orphaned legacy collections** the run page never writes → always empty; legacy `/hr/payroll/*` routes un-gated. **Rec:** point the legacy tabs at the `payroll` collection or retire them; gate the legacy routes.

## CRM / Customers / Loyalty / Marketing
**State:** largely healthy, well-hardened. **Open (money):** referral reward `$inc`s a parallel `loyalty_points` field the real engine never reads (`marketing.py:770`); churn medium/low bands are a stub; NPS-detractor follow-up writes wrong field names. **Rec:** route referral through the loyalty/store-credit ledgers; implement churn bands on real recency.

## Online Store (BVI)
**State:** the most-broken lane pre-fix; now mostly repaired. **Fixed:** ✅ membership list route (#468); ✅ add/remove/reorder SKU-keying + resolved-products (#464). **Open:** menus `addItem`/`moveItem` payload shape (dormant — MenusPage uses saveTree); summary counts. **Rec:** opportunistic. (BVI cutover tracked separately.)

## Tasks / Notifications / Settings / Stores / Admin / RBAC
**State:** Settings/Stores/Notifications healthy + hardened. **Fixed:** ✅ TaskManagement `id:t.task_id` mapping. **Open:** create-task default-today → 422; adminIntegration camelCase setters rejected. **Rec:** send end-of-day/now+1h due; drop/snake-case the unused setters.

## Reports / Analytics / Dashboards / Jarvis
**State:** Reports + Jarvis healthy (18 `/jarvis/*` SUPERADMIN-gated). **Open:** analytics revenue/order counts include CANCELLED+DRAFT; top-customers "Unknown" name; store "store-001"; 500-order cap; **Jarvis chat fabricates fake numbers on backend error** (SYSTEM_INTENT violation); Reports "View" stubs. **Rec:** exclude CANCELLED/DRAFT; join names; replace Jarvis fabricated fallback with an honest "no data".

---
_For each defect's exact file:line + the one-line fix, see [`IMS2_WAVE2_BACKLOG.md`](../IMS2_WAVE2_BACKLOG.md). Research + competitor analysis: [`../research/`](../research/)._

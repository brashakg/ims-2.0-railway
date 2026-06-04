# IMS 2.0 — Wave-2 Backlog (verified FE↔BE seam defects + research)

> Output of the Total Coverage Wave-1 audit (10 lanes) + competitor research, **each confirmed against the actual code**. Severity: BROKEN > PARTIAL > MINOR. Status: ✅ fixed · ⬜ open · 🔶 coordinate (file has concurrent edits).
> Companion to [`IMS2_MASTER_TRACKER.md`](IMS2_MASTER_TRACKER.md).

## Shipped from this program so far
- ✅ `fix(db)` per-index isolation in `ensure_indexes` (one dirty collection no longer aborts all builds) — commit 6f93403
- ✅ `feat(audit)` SUPERADMIN Activity Log completed (name resolution + change diff + today-summary endpoint) + own nav group — commit 442340a
- ✅ `fix(finance)` Payout CSV downloads via the authenticated API (was a relative href → 404/401 on Vercel) — commit fb90394

---

## BROKEN (confirmed) — fix first

### Online Store / BVI — the worst cluster (needs its own PR; bigger than a rename)
Verified deeply 2026-06-05. The backend (`online_store_collections.py`) is coherent; the FE service (`services/api/onlineStore.ts`) + `CollectionsPage.tsx` are the mis-built side, AND the membership model differs:
- ⬜ Rows keyed `collection_id`/`menu_id`/`image_id`; FE reads `.id` → every row action undefined. Normalise `id` in the service mappers.
- ⬜ Single-object responses are wrapped `{collection: …}`; FE `get/create/update` return the envelope un-unwrapped → `.id`/`.smart_rules` all undefined.
- ⬜ `addProduct` sends `{product_id}` but backend `AddProduct` wants `sku` → 422. `reorder` sends `{product_ids}` vs `skus` → 422. `removeProduct` deletes by `product_id` on a `…/products/{sku}` route.
- ⬜ Smart rules: FE nests `smart_rules{field,op,value}`; backend wants top-level `rules[{field,relation,value}]` + `disjunctive` → rules silently dropped on save and never round-trip back.
- ⬜ Preview posts `POST /collections/resolve` which **does not exist** (only `GET /{id}/resolved-products`) → 404.
- ⬜ **`members()` calls `GET /{id}/products` which has no backend route** → manual membership list is always empty. Needs a backend `GET /{collection_id}/products` (or FE reads the embedded `products[]` + catalog-joins detail).
- ⬜ Menus: `addItem` posts a flat body but backend wants `{item:{…}}`; `moveItem` posts `{parent_id}` vs `{new_parent_id}`.
- ⬜ Online-store summary counts shape mismatch → module cards show 0.
- **Note:** push-dark / not live yet, so no live-revenue impact — but it's the most-broken lane. Recommend a single focused PR that reconciles the membership model + adds the `GET /products` route + maps the seam.

### Other BROKEN (smaller, self-contained)
- ⬜ **Inventory Transfers** (`StockTransferManagement.tsx` / `StockTransferModal.tsx`): treats `{transfers:[…]}` envelope as a raw array; `from_location_id` vs `from_store_id` mislabel; status enum case crashes the badge; product search `.filter()` on a `{items}` object. 🔶 `services/api/inventory.ts` has concurrent edits — coordinate.
- ⬜ **Clinical** `TestHistoryPage.tsx:243`: reads `test.rightEye.sphere` but completed-test Rx is at `prescription.rightEye` with `sph` keys → list render crash. Fix: use the existing `readEyePower()` helper. (non-colliding, FE-only — good next quick win)
- ⬜ **HR** `PayrollDashboard.tsx` + `EmployeeSelfService.tsx`: read orphaned legacy payroll collections the run page never writes → salary sheet/payslips always empty. Point at the `payroll` collection or retire the legacy tabs.
- ⬜ **Tasks**: create-task with the default *today* due date is rejected as past-dated (422); `TaskManagementPage` maps `id:t.id` but API emits `task_id` → empty id + Reassign 404. 🔶 `TaskManagementPage.tsx` touched by the a11y commit — coordinate.
- ⬜ **POS** `OrdersPage.tsx`: payment badge blank for UNPAID/CREDIT/REFUNDED (unhandled enum). 🔶 OrdersPage touched by a11y commit — verify if already handled.

## PARTIAL — money/GST correctness (review carefully)
- ⬜ **Finance** `finance.py:717`: GST summary cards split ALL output tax 50/50 CGST+SGST, never IGST → inter-state sales mis-classified on the dashboard. `finance.py:1673`: Budgets tab seeds fabricated allocations when no budget doc. Tally JV drops IGST on inter-state. P&L COGS 60% fallback can present a fabricated margin.
- ⬜ **Catalog** `products.py:276`: `discount_category` dropped on product create → bulk-pricing cap falls back to MASS 15% default on LUXURY items.
- ⬜ **Analytics** `analytics.py:224`: dashboard revenue/order counts include CANCELLED + DRAFT; `:790` top-customers name never joined ("Unknown"); `:525` store name synthetic; `order_repository.py:113` dashboard silently capped at 500 orders.
- ⬜ **CRM/Marketing** `marketing.py:770`: referral reward `$inc`s a parallel `loyalty_points` field the real engine never reads (route through the loyalty/store-credit ledgers); `crm.py:824` churn medium/low bands are a stub; `marketing.py:873` NPS-detractor follow-up writes wrong field names → blank on the dashboard.

## Research-backed features (from the optical-PMS competitor gap)
IMS **leads** the Indian optical-software field on retail-OS breadth. Confirmed gaps, ranked:
- **P1**: GST **e-invoice (IRN + signed QR)** + **e-way bill** via a per-GSTIN GSP/IRP (legally mandatory for B2B at scale); **inter-GSTIN transfer auto-books the mirror purchase** (correctness for the 3-entity/4-GSTIN structure); **offline-first POS** (service worker + idempotent replay — neutralises TechCherry's edge; POS sign-off).
- **P2**: WhatsApp **appointment booking + optometrist diary** feeding the walk-in queue; **per-sale commission ledger + staff leaderboard**; per-customer **credit-limit at POS** (khata); **guided cycle-count**.
- **Quick wins (S)**: **daily owner WhatsApp digest at store close** (all ingredients exist); auto-trigger the **already-built NPS** survey on delivery + show on customer 360; **progressive fitting params** (segment height / pantoscopic tilt / vertex / wrap) on the workshop job.
- Full detail: [`research/COMPETITOR_FEATURE_GAP.md`] (vendors: OpticalCRM, Gofrugal Opticals, TechCherry [legacy], Indian peers, global PMS). **Lenorix could not be verified as a real optical product** (lenorix.com is a generic Spain IT brochure; "Joining Ends Pvt Ltd" not in any registry) — owner to re-confirm the intended name.

_Recommended next: Clinical TestHistory crash (quick, non-colliding) → Finance GST-summary IGST split (money, careful) → Online Store membership reconciliation (own PR). Coordinate on inventory.ts / TaskManagementPage / OrdersPage with the parallel session._

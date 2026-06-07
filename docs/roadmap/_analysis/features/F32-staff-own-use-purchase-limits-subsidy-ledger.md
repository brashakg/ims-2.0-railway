# Feature #32: Staff "Own-Use" Purchase Limits & Subsidy Ledger
META: effort=M days=5 risk=MED roi=3 quickwin=no deps=none phase=3

## Existing overlap
- **POS discount caps** (`backend/api/services/role_caps.py`, `pricing_caps.py`): role-level and category/brand caps already enforced at order creation (`orders.py:1195–1312`). Own-use is a special discount tier on top of this.
- **Orders collection** (`orders`): `discount_approved_by`, `discount_reason` fields exist per line; `cart_discount_approved_by` exists at order level. Own-use purchases would ride these fields without schema change.
- **Expense category caps** (`expense_category_caps` collection, `expenses.py:45–46`): per-role/category daily+monthly spend caps — a partial parallel, but for expenses not purchases.
- **Audit logs** (`audit_logs` collection): before/after state captured on every order; own-use purchases would auto-appear here.
- **RBAC policy** (`rbac_policy.py`): all 12 roles defined; `SALES_STAFF`, `SALES_CASHIER`, `STORE_MANAGER` etc. all participate in POS — own-use policy maps onto existing role identities.
- **Change-proposal system** (`proposals.py`, `ai_proposals` collection): Tier-2 approve-confirm pattern already handles refund/staff-transfer approvals; own-use limit overrides can use the same path.
- **Payroll salary_config** (`salary_config` collection): per-employee record already carries `employee_id`, entity linkage — own-use allowance can be stored here or in a sibling doc.
- **No existing own-use feature**: no `staff_own_use_ledger` collection, no purchase-intent flag on orders, no FY allowance tracking. This is net-new but heavily scaffolded by the above.

## Reuse (extend, don't rebuild)
- **`orders.py` `create_order`** (line 1027): add `is_own_use: bool` + `own_use_approved_by` fields to `OrderCreate` schema; gate own-use discount tier inside the existing per-item discount cap chain (after role cap, before category cap).
- **`pricing_caps.py` / `role_caps.py`**: add `own_use_discount_pct` lookup that applies when `is_own_use=True`; enforced in the same cap resolution block (orders.py:1195–1312) — no new enforcement path.
- **`salary_config` collection** (payroll.py): add `own_use_annual_allowance_inr` field (FY budget per employee) and `own_use_brands_allowed: list[str]` (scope). Migration: default to 0 (disabled) for existing docs.
- **`audit_logs` collection**: own-use orders auto-logged via the existing order-create audit path; no new collection needed for the trail.
- **`posStore.ts`** (`frontend/src/stores/posStore.ts`): add `isOwnUse: boolean` and `ownUseApprovedBy: string` to cart state alongside existing `cartDiscount*` fields. POS step-Review already has a discount reason panel — extend it.
- **HR salary-setup page** (`frontend/src/pages/hr/PayrollDashboard.tsx` or sibling `SalarySetupPage`): extend the per-employee salary config form to include own-use allowance + allowed-brand list.
- **Expense category caps pattern** (`expense_category_caps`): use as the architectural model for the new `staff_own_use_policy` singleton — same upsert pattern, same SUPERADMIN-only write gate.

## Data model
**New collection: `staff_own_use_policy`** (singleton `{"_id": "default"}`)
```
{
  enabled: bool,                          // master kill-switch
  own_use_discount_pct: float,            // e.g. 30 — additional % on offer_price when is_own_use
  annual_allowance_inr: float,            // FY cap per staff (overridable per-employee)
  allowed_categories: [str],              // e.g. ["FRAMES","CONTACT_LENS","OPTICAL_LENS"]
  allowed_brands_blocklist: [str],        // brands explicitly excluded (luxury: Cartier, Chopard…)
  max_own_use_orders_per_month: int,      // frequency cap (e.g. 2 per month)
  requires_manager_approval: bool,        // if true → approval flow; if false → auto-approve
  updated_by: str,
  updated_at: datetime
}
```

**New fields on `salary_config`** (per-employee override layer):
```
own_use_annual_allowance_inr: float | None   // null = use policy default
own_use_brands_allowed: [str] | None         // null = use policy default (all allowed)
own_use_discount_override_pct: float | None  // null = use policy default
```

**New collection: `staff_own_use_ledger`** (one doc per transaction)
```
{
  ledger_id: str,             // uuid
  employee_id: str,
  store_id: str,
  order_id: str,              // links to orders collection
  fy: str,                    // e.g. "2026-27"
  amount_inr: float,          // grand_total at own-use price (what they paid)
  subsidy_inr: float,         // discount_given = normal_price - own_use_price
  items: [{product_id, name, category, brand, qty, mrp, own_use_price, subsidy}],
  approved_by: str | None,
  approved_at: datetime | None,
  status: "PENDING"|"APPROVED"|"REJECTED"|"CANCELLED",
  created_at: datetime
}
```
FY computed as Apr–Mar (existing `ims_fy()` helper already in `finance.py`).

**New field on `orders`** (no migration needed — add only when `is_own_use=True`):
```
is_own_use: bool
own_use_employee_id: str
own_use_ledger_id: str
own_use_approved_by: str
```

## Backend

**`settings.py`** — add two endpoints under `/api/v1/settings/own-use-policy`:
- `GET /own-use-policy` (SUPERADMIN/ADMIN): read policy singleton.
- `PUT /own-use-policy` (SUPERADMIN only): upsert policy; validates discount_pct ≤ 50 (hard ceiling).

**`orders.py` `create_order`** — extend existing discount-cap block:
- If `is_own_use=True`: verify `own_use_employee_id == current_user.id` (can only buy for self); check `policy.enabled`; check category and brand eligibility; check FY ledger balance (query `staff_own_use_ledger` aggregate `subsidy_inr` for this FY vs `annual_allowance_inr`); check monthly frequency cap; apply `own_use_discount_pct` as an additional layer after role/category caps; if `requires_manager_approval=True` → create ledger doc with `status=PENDING` and block order until approved (same pattern as `require_approval` ₹0-discount flag that already exists at orders.py:1237); if no approval required → create ledger doc `status=APPROVED` immediately and proceed.
- All validation failures → HTTP 403 with specific reason code (over_limit, ineligible_category, etc.).

**New router: `backend/api/routers/own_use.py`** (mount at `/api/v1/own-use`):
- `GET /ledger?employee_id=&fy=&store_id=` (SUPERADMIN/ADMIN/STORE_MANAGER/self): per-employee or store-wide ledger; includes FY consumed vs allowance.
- `GET /summary?fy=&store_id=` (SUPERADMIN/ADMIN): store-level aggregation — total subsidy given, top consumers, category breakdown.
- `POST /approve/{ledger_id}` (STORE_MANAGER/ADMIN/SUPERADMIN): approve PENDING own-use order; updates ledger status → APPROVED; releases the held order to CONFIRMED.
- `POST /reject/{ledger_id}` (STORE_MANAGER/ADMIN/SUPERADMIN): reject → ledger REJECTED; cancels or reverts order discount.
- `GET /policy` and `PUT /policy` delegated to settings.py as above.

**`payroll.py`** — extend salary config CRUD to accept `own_use_*` override fields (read/write, SUPERADMIN/ADMIN only).

## Frontend

**POS Review step** (`frontend/src/pages/pos/` — existing Review panel): add an "Own-Use Purchase" toggle (checkbox, hidden unless `current_user.roles` includes a staff role and `policy.enabled=true`). When toggled: show a read-only "Own-Use Price" column in the cart table; show FY allowance consumed / remaining badge; if `requires_manager_approval`, show "Pending manager approval" chip and block the Pay button until approved. Restrained UI: single-accent badge (green = within limit, amber = near limit, red = over limit).

**New page: `frontend/src/pages/hr/OwnUseLedgerPage.tsx`**: Tabbed — "My Purchases" (self-view, all roles) and "Staff Purchases" (STORE_MANAGER+ sees store-wide). Table columns: date, employee, items (collapsed), own-use price paid, subsidy given, FY consumed %, status chip. Filter by FY, month, employee. Export to CSV (reuse existing frontend CSV exporter; apply formula-neutralisation already in place from `NEW-EXPORT-FE-CSV-INJECTION` fix).

**HR / Salary Setup page** (`frontend/src/pages/hr/`): extend existing per-employee salary config form with a collapsible "Own-Use Allowance" section — annual cap (INR), allowed brands override, discount % override. Shows "using policy default" when null.

**Settings → Own-Use Policy** (extend existing `/settings` page): new card under HR/Payroll settings group — toggle enabled, set discount %, annual allowance, category list, brand blocklist, monthly frequency cap, approval-required toggle.

## Business rules
- **Hard ceiling**: `own_use_discount_pct` cannot exceed 50% (hardcoded server-side; not owner-configurable).
- **Luxury brand block**: Cartier, Chopard, Bvlgari, Gucci, Prada, Versace, Burberry are permanently ineligible for own-use discount (mirrors existing luxury-brand cap constants in `pricing_caps.py`; owner can add more via blocklist but cannot remove these).
- **Self-only**: `own_use_employee_id` must equal the purchasing employee (cannot buy "own-use" for a family member or friend via this mechanism).
- **FY cap is hard**: once consumed, no further own-use discount permitted until next FY (Apr 1 reset); no rollover.
- **Existing discount caps still apply first**: own-use discount is layered on top of role/category/brand caps — the final price cannot go below COGS (existing cost-floor guard in `orders.py` already enforces this).
- **Approval trail**: every own-use order writes an immutable ledger doc regardless of approval requirement; `audit_logs` entry stamped with before/after prices.
- **Returns**: if an own-use order is returned, the subsidy is credited BACK to FY allowance (ledger doc `status=CANCELLED`; add reversal logic in `returns.py` alongside existing loyalty reversal at line 1191).
- **Period lock**: own-use orders blocked if accounting period is locked (same `check_period_locked` gate already on `orders.py:1073`).
- **Tally JV**: own-use subsidy amount should appear as a separate `StaffSubsidy` expense line in the Tally sales-JV XML (extend `nexus_providers.py` Tally builder — new debit entry in the existing voucher template).

## RBAC
| Role | Can flag own-use | Sees own ledger | Sees store ledger | Approve/reject | Configure policy |
|---|---|---|---|---|---|
| SUPERADMIN | Yes | Yes | All stores | Yes | Yes |
| ADMIN | Yes | Yes | All stores | Yes | Yes |
| AREA_MANAGER | Yes | Yes | Their stores | Yes | No |
| STORE_MANAGER | Yes | Yes | Their store | Yes | No |
| ACCOUNTANT | No (read-only) | Yes (own) | Their store (read) | No | No |
| CATALOG_MANAGER | No | No | No | No | No |
| SALES_CASHIER / SALES_STAFF | Yes (own purchase only) | Yes (own) | No | No | No |
| CASHIER / WORKSHOP_STAFF / OPTOMETRIST | No | No | No | No | No |

Middleware gate: `own_use_employee_id` must equal JWT `sub` for non-manager roles (enforced in `create_order`).

## Integrations
- **Tally**: extend Tally sales-JV XML builder (`nexus_providers.py`, NEXUS agent nightly export) to emit a `StaffSubsidy` expense debit line for each own-use order — keeps books balanced without manual journal entry.
- **Jarvis / ORACLE**: ORACLE's discount-abuse detection (`oracle.py:249+`) should exclude flagged own-use orders from anomaly scoring (add `is_own_use=True` filter in the aggregation). ORACLE should separately flag if any employee's FY consumption exceeds 80% of allowance with a proposal to SUPERADMIN (advisory tier-3).
- **MSG91 / WhatsApp**: if `requires_manager_approval=True`, send in-app bell notification to STORE_MANAGER on PENDING own-use order (reuse existing `notifications` collection write pattern from `taskmaster.py:193–195`). No WhatsApp needed (internal staff flow).
- **No Shopify / Razorpay / Shiprocket involvement.**

## Risk notes
- **POS revenue-critical path**: `create_order` is the hottest path in the system. The own-use ledger balance check adds a MongoDB aggregation query per flagged order. Must be fast (<50ms) — use a pre-aggregated `fy_consumed_inr` field on the ledger account doc (updated atomically via `$inc` on each approved order) rather than a live aggregation every time. Follow the `redeem_voucher_atomic` guard-in-filter pattern.
- **Approval-held orders**: if `requires_manager_approval=True`, the order sits in PENDING discount state. Need to decide what the POS terminal does while waiting (block cashier or allow a regular-price sale). This is an owner decision (see below).
- **Return-subsidy reversal in `returns.py`**: returns path already has loyalty reversal (line 1191), ITC reversal hooks, and restock. Adding own-use subsidy reversal is low-risk but must be tested — feature-flag the reversal logic independently.
- **Tally JV change**: any change to the Tally XML schema should be smoke-tested against the existing nightly export; wrap in `try/except` (fail-soft, existing pattern in `nexus.py`).
- **Feature flag**: add `OWN_USE_ENABLED` env/settings toggle (defaults `false`); all new code paths gated on it. POS UI only shows the toggle when flag is on. Safe to deploy dark.

## Recommendation
Build later (Phase 3). Not a quick win — it touches the revenue-critical `create_order` path, requires atomic balance tracking, an approval loop, Tally JV extension, and a return-reversal hook. Low abuse risk right now for a 6-store operation; revisit when headcount grows or audit flags discount-abuse patterns from ORACLE. When built, ship behind `OWN_USE_ENABLED=false` feature flag and enable per-store.

## Owner decisions
- Q: What is the annual own-use subsidy cap per staff member (in INR)? | Why: This is the primary hard limit in the ledger — determines how much company money is allocated per FY per head. | Options: flat amount (e.g. ₹5,000/year for all staff) / tiered by role (e.g. ₹3k Sales Staff, ₹8k Store Manager, ₹15k Admin) / zero (tracking only, no subsidy — staff pays own-use price but no extra discount beyond role cap)
- Q: Should managers approve each own-use purchase before it goes through, or should it be post-purchase reporting only? | Why: Approval-required blocks the POS terminal mid-sale; reporting-only is frictionless but reactive. | Options: pre-approval required (manager must confirm in-app before cashier can close sale) / post-purchase report only (sale closes immediately; manager reviews ledger weekly) / amount-threshold triggered (e.g., purchases over ₹2,000 need pre-approval)
- Q: Which product categories are eligible for the own-use discount? | Why: Determines the `allowed_categories` list in policy — a narrow list (e.g., frames + lenses only) limits abuse; a broad list (all categories) is a better staff benefit. | Options: frames + optical lenses + contact lenses only / all categories except luxury brands / all categories with no exclusions
- Q: Should the own-use subsidy appear as a named expense line in Tally (e.g. "Staff Subsidy — FY 2026-27"), or is it acceptable to let it reduce the sale revenue silently? | Why: Affects P&L presentation — explicit expense line gives a clean gross-margin view; silent revenue reduction hides the true subsidy cost from category margins. | Options: explicit Tally expense debit (recommended for clean P&L) / reduce revenue only (simpler Tally, but obscures staff cost) / no Tally integration for this (manual journal by accountant)
- Q: Can staff buy own-use items for immediate family members (spouse, children) under their allowance? | Why: Changes the `is_own_use` self-only enforcement — allowing family doubles effective allowance and complicates audit. | Options: strictly self only (simplest, lowest abuse) / immediate family allowed (wider benefit, requires patient/family member link from `customers.patients[]`) / allowed with manager override only
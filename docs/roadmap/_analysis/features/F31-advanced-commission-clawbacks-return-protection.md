# Feature #31: Advanced Commission Clawbacks (Return Protection)
META: effort=M days=5 risk=MED roi=4 quickwin=no deps=none phase=3

## Existing overlap
The codebase has partial commission infrastructure but no clawback logic:

- `salary_config` collection has `commission_rate_percent` field (backend/api/routers/payroll.py:109-114) — stored but never consumed by `payroll_engine.py`
- `payroll_engine.py` has `_earnings()` which merges incentives into gross after PF/ESI base — this is the exact insertion point for commission and clawback
- `returns.py` already handles `_issue_store_credit`, `_priced_return_lines`, and `reverse_for_return()` (loyalty clawback pattern at loyalty.py:955-1058) — the loyalty reversal is the exact pattern to copy for commission
- `orders.py` captures `items[].sales_person_id` on order create (the associate attribution needed for commission earn)
- `payroll.py` run lifecycle (DRAFT → APPROVED → PAID) and `lock` state machine already exists — clawback deduction slots naturally into DRAFT computation
- Daily points scoring (points.py) already handles visufit gate and RBAC for who can write scores; commission leaderboard is a noted gap in the HR audit

No clawback logic, no commission ledger, no commission calculation in payroll engine — this is largely greenfield wiring of pieces that exist.

## Reuse (extend, don't rebuild)
- `backend/api/routers/payroll.py` — extend `compute_payroll()` to call commission engine; add clawback deduction line in `breakdown.deductions`
- `backend/api/services/payroll_engine.py` — add `compute_commission(earned_rows, clawback_rows)` pure function; follows existing `_earnings()` / `_deductions()` pattern
- `backend/api/routers/returns.py` — extend `create_return()` to write a `commission_clawback_pending` ledger row when the returned order has commission-eligible line items (mirrors `reverse_for_return()` loyalty pattern at loyalty.py:955)
- `backend/api/routers/orders.py` — extend `create_order()` to write a `commission_ledger` EARN row on order confirm (mirrors how loyalty points are earned); `items[].sales_person_id` already populated
- `salary_config` collection — already has `commission_rate_percent`; add `commission_basis` enum field (PERCENTAGE_OF_SALE | FLAT_PER_UNIT | SPIFF_CATALOG)
- `payroll` collection — `breakdown` dict already has `deductions` array; add `commission_earned` and `commission_clawback` line items alongside existing EPF/ESI/PT
- `frontend/src/pages/hr/PayrollDashboard.tsx` — extend payslip breakdown card to show commission line + clawback line
- `backend/api/services/rbac_policy.py` — add new endpoints to POLICY table

## Data model
New collection `commission_ledger` (append-only, never updated):
```
{
  ledger_id: UUID,
  employee_id: str,
  store_id: str,
  type: "EARN" | "CLAWBACK",
  order_id: str,
  return_id: str | null,       # set on CLAWBACK rows
  order_date: datetime,
  return_date: datetime | null,
  item_ids: [str],             # order item IDs this row covers
  gross_sale_amount: Decimal,  # taxable value of covered items
  commission_rate_pct: Decimal,# snapshot of rate at time of sale
  commission_amount: Decimal,  # computed; negative on CLAWBACK
  payroll_month: str | null,   # "2026-06" once absorbed into a payroll run
  payroll_id: str | null,      # FK once absorbed
  status: "PENDING" | "ABSORBED" | "VOIDED",
  created_at: datetime
}
```

New fields on `salary_config`:
- `commission_basis`: `"PERCENTAGE_OF_SALE"` (default) | `"FLAT_PER_UNIT"` | `"SPIFF_CATALOG"`
- `commission_eligible_categories`: list of item_type strings (e.g. `["LUXURY", "PREMIUM"]`); empty = all categories

New field on `orders.items[]`:
- `commission_ledger_id`: str | null — FK back to the EARN row (set at order confirm)

Mongo index: `(employee_id, status, payroll_month)` on `commission_ledger` for payroll absorption query.

## Backend

**`POST /api/v1/payroll/commission/earn`** (internal, called by orders.py on confirm)
- Reads `items[].sales_person_id` + `commission_rate_percent` from `salary_config`
- Filters to `commission_eligible_categories`; skips zero-price and comp items
- Writes EARN rows to `commission_ledger`; stamps `orders.items[].commission_ledger_id`
- Atomic: if any write fails, entire batch is no-op (no partial earn)

**`POST /api/v1/payroll/commission/clawback`** (internal, called by returns.py on return confirm)
- Reads original EARN rows by `order_id` + `item_ids` from `commission_ledger`
- Computes clawback = EARN amount × (returned_qty / original_qty) — proportional to what was returned
- Writes CLAWBACK rows (negative `commission_amount`); never modifies existing EARN rows
- If EARN row already ABSORBED (payroll already ran), still writes clawback — absorbed in next payroll
- Fail-soft: clawback failure never blocks the return; flags `commission_clawback_failed=True` on return doc for reconciliation queue

**`GET /api/v1/payroll/commission/summary?employee_id=&month=`** (STORE_MANAGER / ADMIN / SUPERADMIN)
- Returns PENDING EARN + CLAWBACK rows for month, net commission, absorbed-to-date

**`GET /api/v1/payroll/commission/ledger?employee_id=&from=&to=`** (STORE_MANAGER / ADMIN / SUPERADMIN)
- Full ledger with order links, return links, status; used for disputes

**Extend `payroll_engine.compute_payroll()`**:
- Query `commission_ledger` for `status=PENDING` rows in the payroll month for this employee
- Net = sum of EARN + CLAWBACK (CLAWBACKs are negative, so net can go to zero but never below zero — clamp at 0)
- Add `commission_earned` (gross positive) and `commission_clawback` (gross negative) as separate line items in `breakdown`
- Mark absorbed rows `status=ABSORBED`, stamp `payroll_id` — done inside the payroll run transaction (find_one_and_update pattern from vouchers.redeem_voucher_atomic)
- Clamp: if clawback exceeds this month's earn, carry forward residual to next month (new `commission_ledger` row type `CARRY_FORWARD`)

## Frontend

**`frontend/src/pages/hr/PayrollDashboard.tsx`** — extend payslip breakdown card:
- Add "Commission Earned" row (green) and "Commission Clawback" row (red, shown only if non-zero) in the earnings/deductions table
- Show net commission line with tooltip: "N returns clawed back from M sales"

**New page `frontend/src/pages/hr/CommissionLedgerPage.tsx`** (STORE_MANAGER / ADMIN / SUPERADMIN):
- Per-employee commission history table: date | order# | item | sale amount | commission rate | earned/clawed | status
- Month filter; CSV export
- "Disputed" flag button (opens notes modal) for manager review
- Restrained light-only: neutral table, single accent for positive/negative amounts (green-600 / red-600 text only, no background fill)

**Extend `frontend/src/pages/orders/OrdersPage.tsx`**:
- Add "Commission" chip on order detail sidebar showing which associate earned what (read-only for STORE_MANAGER+)

**Extend `frontend/src/pages/orders/ReturnsPage.tsx`**:
- Add "Commission Clawback" notice in return confirmation modal: "₹X commission will be clawed back from [Name]'s next payroll run"
- Requires no new call — surface data already returned by clawback endpoint

## Business rules
- Commission is computed on **taxable sale value** (not grand_total) — GST-exclusive basis, consistent with how COGS is measured
- Net commission for any payroll run is clamped at ₹0 (cannot reduce net pay below zero via clawbacks alone)
- Residual clawback (exceeds month's earn) carries forward as a CARRY_FORWARD ledger row — absorbed in next month's run
- Once a payroll run is PAID (`status=PAID`), clawback rows for that period are still written but absorbed in the next open DRAFT run — no retroactive amendment to a locked payroll
- Returns processed after the payroll PAID cutoff generate clawback rows with `payroll_month=null` — absorbed at next `compute_payroll()`
- Commission rates are **snapshotted at order time** (stored on `commission_ledger.commission_rate_pct`); rate changes do not retro-affect past EARN rows
- Partial returns: clawback is proportional — `returned_qty / original_qty × earn_amount`; full returns claw back 100%
- If `sales_person_id` is absent on an order item, no EARN row is written (silent skip, no error)
- Audit: every EARN and CLAWBACK row is immutable; disputes are flagged via a `dispute_note` field, never by modifying ledger rows

## RBAC
- **SUPERADMIN / ADMIN**: full ledger read, commission settings write, manual void of PENDING rows
- **AREA_MANAGER / STORE_MANAGER**: read ledger for their store's employees; cannot void
- **ACCOUNTANT**: read-only on commission breakdown in payroll run (needed for payslip sign-off)
- **SALES_STAFF / SALES_CASHIER**: read their own commission ledger only (`GET /commission/ledger?employee_id=self`)
- **OPTOMETRIST / WORKSHOP_STAFF / CASHIER / CATALOG_MANAGER**: no access

## Integrations
- **Tally JV (NEXUS)**: Commission earn and clawback should appear as separate payroll cost lines in the Tally salary JV XML (`backend/api/services/payroll_exports.py` — extend `build_salary_jv_xml()` to include commission ledger lines per employee). No new Tally integration required — extend existing XML builder.
- **Jarvis / ORACLE**: ORACLE's `_detect_discount_abuse()` can be extended to flag employees with high clawback ratios (returns > 20% of commission-generating sales) — advisory only, proposal to SUPERADMIN.
- No MSG91 / Shopify / Razorpay dependency.

## Risk notes
- **Money risk**: Clawback touches net pay — a bug here can underpay staff. The clamping logic (never below zero) and carry-forward must be tested with payroll_engine unit tests before any live run. Ship behind a `COMMISSION_CLAWBACK_ENABLED` env flag (default `false`); Railway var set by owner when ready.
- **Atomicity**: The EARN write (at order confirm) must not block order creation. Use fire-and-forget async with failure flagged on the order (`commission_ledger_failed=True`) — same pattern as `online_stock_writeback.py`.
- **Payroll lock race**: If payroll run is computing while a return is being processed, the clawback row must land in the correct `payroll_month`. Use the payroll run's `created_at` as the cutoff boundary, not wall-clock time.
- **Sales person attribution**: `items[].sales_person_id` is currently optional. Commission only works if POS correctly stamps this field. Confirm with owner whether all stores require associate attribution at POS (currently not enforced).
- **Return abuse vector**: A manager could theoretically process a sham return to claw back a competitor associate's commission. The clawback endpoint must require the same STORE_MANAGER+ role that processes returns, and must write to `audit_logs` with `actor_id`.

## Recommendation
Build later — after confirming `salary_config.commission_rate_percent` is populated for at least one employee in production and that POS reliably stamps `sales_person_id` on items. Those two preconditions are currently unverified gaps (both noted in the HR audit). Ship the EARN ledger first (no clawback) so commission data accumulates; add clawback in the next sprint once the ledger has real data to validate against.

## Owner decisions
- Q: Which categories earn commission (e.g. LUXURY + PREMIUM only, or all categories)? | Why: Sets `commission_eligible_categories` on `salary_config`; determines how many order lines generate EARN rows and how complex the filtering logic is | Options: (a) All categories except SERVICE and NON_DISCOUNTABLE / (b) LUXURY + PREMIUM only / (c) Per-brand SPIFF catalog (highest effort — needs new `spiff_catalog` collection)
- Q: Should commission be paid on the full sale price or only on margin above cost? | Why: Changes the `gross_sale_amount` basis in the ledger — taxable value vs (taxable value − COGS); affects every earn computation | Options: (a) % of taxable sale value (simplest, industry norm) / (b) % of gross margin (requires reliable cost_at_sale, which exists but has estimated-flag risk)
- Q: What is the maximum clawback carry-forward period? | Why: A staff member who leaves after a clawback carry-forward row is open creates a liability; needs a write-off policy | Options: (a) Carry forward indefinitely until absorbed / (b) Write off after 3 months if still unabsorbed / (c) Deduct from final settlement pay only
- Q: Should sales staff be able to see their own commission ledger in real time (before payroll), or only on payslip? | Why: Real-time visibility motivates behaviour but may create disputes mid-month; payslip-only is simpler | Options: (a) Real-time self-service ledger page / (b) Payslip only (no mid-month visibility) / (c) Weekly summary notification via WhatsApp
# Feature #34: Real-Time Global Target "Ticker" (Privacy-Configurable)
META: effort=M days=4 risk=LOW roi=4 quickwin=yes deps=none phase=3

## Existing overlap
- **Revenue aggregation**: `backend/api/routers/finance.py` lines 524-622 already computes monthly revenue with MoM growth %, period bucketing, and excludes DRAFT/CANCELLED orders. The ticker needs the same aggregation scoped to the current calendar month.
- **Budgets (Planned vs Actual)**: `backend/api/routers/budgets.py` lines 135-200 already computes `planned_amount` vs actuals per (store, period, head=REVENUE). The ticker's "target" is exactly the REVENUE head of the current month's budget doc.
- **Daily points leaderboard**: `backend/api/routers/points.py` lines 621-645 is the existing "applause"-style feed for staff. The ticker can piggyback on the same leaderboard UI card.
- **In-app bell notifications**: `backend/api/routers/notifications.py` and `notifications` collection already support per-user delivery of system messages. Milestone "Applause" pushes go through this path.
- **RBAC middleware**: `backend/api/services/rbac_policy.py` + `backend/api/middleware/rbac_enforcement.py` have the exact role-check mechanism needed for the privacy split.
- **ORACLE agent**: `backend/agents/implementations/oracle.py` already does sales anomaly detection and end-of-day sweeps. Milestone detection logic fits naturally in its hourly tick.

## Reuse (extend, don't rebuild)
- `backend/api/routers/finance.py` — extend the existing revenue aggregation to expose a lightweight `/finance/target-ticker` endpoint (MTD actual vs budget target, % complete, day-of-month pace).
- `backend/api/routers/budgets.py` — read `budgets` collection (head=REVENUE, period=current month) as the target source; no new collection needed.
- `backend/api/routers/notifications.py` + `notifications` collection — push milestone "Applause" bell notifications through the existing in-app bell pipeline.
- `backend/agents/implementations/oracle.py` — add milestone check inside the existing hourly tick (`_do_background_work`); reuse `_audit_log()` and `emit_event()` patterns already in the base class.
- `frontend/src/pages/hub/HubPage.tsx` (or equivalent dashboard landing) — extend with a Ticker card component; restrained single-accent progress bar, no multi-color fanfare.
- `frontend/src/context/AuthContext.tsx` — role already on `user.activeRole`; use it client-side to gate raw-number vs % display without a second API call.

## Data model
No new collection required. Two small additions:

**New fields on existing `budgets` collection docs** (head=REVENUE rows only):
- `milestone_pcts: list[int]` — e.g. `[25, 50, 75, 100]` — owner-configured milestone thresholds (stored per store+period; default seeded from global setting).
- `milestones_fired: list[int]` — tracks which milestones have already triggered notifications this month (prevents re-fire on re-query); reset to `[]` on new period.

**New singleton in `business_settings`** (upsert on `_id: "default"`):
- `ticker_milestone_pcts: list[int]` — global default milestone list if a store budget row lacks its own.
- `ticker_refresh_seconds: int` — frontend polling interval (default 60).

## Backend
- `GET /api/v1/finance/target-ticker?store_id=<optional>` — returns:
  - `mtd_revenue` (₹, from existing order aggregation, IST-aware date bounds matching `finance.py` IST temporal logic)
  - `monthly_target` (₹, from `budgets` head=REVENUE for current FY Apr-Mar month)
  - `pct_complete` (float, 0-100)
  - `days_elapsed / days_in_month` (for pace line: are we ahead/behind)
  - `pace_revenue` (₹ — what MTD should be at today's date if on track linearly)
  - `milestones_fired` (list of already-announced thresholds — FE suppresses re-announce)
  - `raw_visible: bool` — server-side flag: True if caller's role is SUPERADMIN/ADMIN/AREA_MANAGER/STORE_MANAGER/ACCOUNTANT; False for junior roles (SALES_CASHIER/SALES_STAFF/CASHIER/WORKSHOP_STAFF/OPTOMETRIST). FE uses this to render raw ₹ vs % bar only.
  - Multi-store: SUPERADMIN/ADMIN/AREA_MANAGER get an `all_stores: []` array with per-store breakdown; store-scoped roles get their store only. Reuse existing `validate_store_access` pattern.
- `POST /api/v1/finance/target-ticker/settings` (SUPERADMIN/ADMIN only) — upsert `ticker_milestone_pcts` and `ticker_refresh_seconds` into `business_settings`.
- ORACLE agent change only (no new endpoint): inside existing hourly `_do_background_work`, after the anomaly scan, check if any store has crossed a new milestone since last tick. If yes, call `emit_event("target.milestone_reached", {store_id, pct, mtd_revenue})` and queue in-app bell notifications for qualifying roles via existing `notifications` insert pattern. Write `milestones_fired` back to the budget doc atomically (`$addToSet`).

## Frontend
- **`TickerCard` component** (`frontend/src/components/hub/TickerCard.tsx`, new):
  - Polls `GET /finance/target-ticker` every `ticker_refresh_seconds` seconds (from response payload; default 60).
  - **Management view** (`raw_visible=true`): shows ₹MTD / ₹Target, numeric %, pace indicator ("₹X ahead / ₹Y behind pace"), per-store rows in an accordion (multi-store roles).
  - **Staff view** (`raw_visible=false`): shows only a labelled progress bar ("Company Goal — 72% reached"), no rupee amounts, no store breakdown. Same accent colour as existing brand token (`--color-accent`).
  - Milestone flash: when `pct_complete` crosses a new threshold not in previous poll's `milestones_fired`, show a one-time 3-second `toast.success("Target milestone reached!")` (reuse existing `useToast` from `context/ToastContext`). No persistent badge.
  - Light-only, neutral palette. Single `--color-accent` progress fill. No multi-colour confetti; restrained executive aesthetic per design preference.
- **Hub page** (`frontend/src/pages/hub/HubPage.tsx` or `/dashboard`): mount `<TickerCard />` in the top summary row, same visual weight as existing KPI cards.
- **Settings panel** (extend existing Settings page, Superadmin tab): two inputs — milestone thresholds (comma-separated %, defaulting to 25/50/75/100) and refresh interval (seconds, min 30, max 300).

## Business rules
- Target source is always the `budgets` collection (head=REVENUE, current IST financial month Apr-Mar). If no budget doc exists for the current month, `monthly_target=null` and ticker shows "No target set" — never fabricates a number.
- Revenue calculation excludes CANCELLED and DRAFT orders (same as `finance.py:524-622` existing guard).
- `milestones_fired` uses `$addToSet` to prevent duplicate notifications; resets to `[]` on the 1st of each month (ORACLE tick checks `period != current_month` and resets).
- Milestone notifications go to in-app bell only (no WhatsApp/SMS for internal targets — quiet-hours complexity not warranted). Bell notification text: "Store [name] reached [X]% of monthly target."
- Raw rupee amounts are NEVER sent to the junior-role API response. The `raw_visible` flag is computed server-side from the decoded JWT role — the client cannot elevate it.
- No POS writes, no financial ledger writes. Read-only aggregation on top of `orders` and `budgets`. Period lock has no effect (ticker reads live data, not posting).

## RBAC
| Role | What they see |
|---|---|
| SUPERADMIN, ADMIN | Raw ₹ + % + all-stores breakdown + settings edit |
| AREA_MANAGER | Raw ₹ + % + their assigned stores breakdown (store_ids from JWT) |
| STORE_MANAGER, ACCOUNTANT | Raw ₹ + % for their store only |
| SALES_CASHIER, SALES_STAFF, CASHIER, OPTOMETRIST, WORKSHOP_STAFF, CATALOG_MANAGER, DESIGN_MANAGER | % progress bar only, no ₹ amounts, no store breakdown |

Endpoint is accessible to all authenticated roles (`require_roles(*ALL_ROLES)` or no role gate beyond JWT auth); the privacy split is data-level in the response, not a 403.

## Integrations
- None for core ticker. ORACLE agent (internal) fires milestone events — no MSG91, no Shopify, no Tally, no Razorpay involvement.

## Risk notes
- **No POS/money risk**: pure read aggregation over existing `orders` + `budgets`; no writes to financial ledger.
- **Performance**: MTD revenue aggregation on `orders` runs on every poll (default 60s × all active sessions). Must add a compound index `{created_at: 1, status: 1, store_id: 1}` on `orders` if not already present (check `backend/database/connection.py` index definitions). Consider a 60-second TTL server-side cache (Redis if available; in-process dict fallback) on the aggregation result to avoid per-user Mongo scans.
- **Budget dependency**: ticker is only meaningful when budgets are set. If no REVENUE budget exists for the current month, the card degrades gracefully ("No target set — configure in Budgets"). No risk from missing data.
- **Milestone double-fire**: `$addToSet` on `milestones_fired` is atomic and prevents re-notification. The only edge case is a month rollover at exactly the ORACLE tick boundary — guarded by the period check.
- **No feature flag needed**: entirely additive read-only card; no existing flow is altered. Safe to ship without a flag.

## Recommendation
Build now (quick win). Four-day effort, zero POS/money risk, high visibility for the owner and management. Revenue aggregation and budget read are already built — this is assembly + UI only.

## Owner decisions
- Q: What is the default monthly revenue target if no budget has been entered for a store? | Why: Determines whether the ticker shows "No target set" or falls back to a rule (e.g., last month's actual, or a fixed seed) | Options: (a) Show "No target set — please configure in Budgets" and leave card greyed out [recommended — avoids fabricated targets] / (b) Default to last month's actual revenue as a soft target / (c) Owner enters a global default target ₹ amount in Settings as a fallback
- Q: Which staff roles should receive in-app "Applause" milestone notifications — all authenticated users, or only store-level staff (SALES_CASHIER/SALES_STAFF)? | Why: Determines the recipient list in the ORACLE milestone notification push; sending to SUPERADMIN/ADMIN too is trivial but may add noise | Options: (a) All roles in the relevant store / (b) Only store-floor roles (SALES_CASHIER, SALES_STAFF, CASHIER) / (c) Only management (STORE_MANAGER and above)
- Q: Should the milestone thresholds be configurable per store, or one global setting for all stores? | Why: Per-store config allows a high-volume store to celebrate at 50%/100% while a small store celebrates at 25%/75%; global is simpler to manage | Options: (a) Global thresholds only (simpler) / (b) Per-store override on top of global default / (c) Per-store only
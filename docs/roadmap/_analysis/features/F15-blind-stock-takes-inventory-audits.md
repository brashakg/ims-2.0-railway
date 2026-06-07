# Feature #15: Blind Stock Takes (Inventory Audits)
META: effort=M days=5 risk=MED roi=4 quickwin=no deps=none phase=3

## Existing overlap
IMS already has a full stock-count/cycle-count subsystem (`backend/api/routers/inventory.py` lines 1263–1609) with:
- `stock_count` sessions (start, add items, complete, reconcile)
- Variance calculation (`variance_percentage`, `shrinkage_percentage`) on `complete_stock_count`
- `reconcile_stock_count` endpoint that writes counted qty back to `stock_units`
- `StockAudit.tsx` frontend (grouped by zone/fixture, variance cards, in-progress/completed chips)
- `StockCountScanningInterface.tsx` component for barcode input

The current system is **sighted** — staff see `system_quantity` when counting. The blind-count feature is a mode change on top of this infrastructure, not a rebuild.

## Reuse (extend, don't rebuild)
- `stock_count` MongoDB collection — add `blind_mode: bool` flag per session
- `POST /stock-count/start` (inventory.py:1290) — accept `blind_mode` param; set flag on doc
- `POST /stock-count/{count_id}/items` (inventory.py:1384) — suppress `system_quantity` in response when `blind_mode=True`
- `POST /stock-count/{count_id}/complete` (inventory.py:1449) — variance calculation already correct; add flag to control who sees results
- `POST /stock-count/{count_id}/reconcile` (inventory.py:1608) — restrict to HQ roles when `blind_mode=True`
- `StockAudit.tsx` — extend with blind-mode indicator + HQ-only variance reveal panel
- `StockCountScanningInterface.tsx` — suppress system qty display conditionally
- `audit_logs` collection — already used; stamp every blind-session start/complete/reconcile with `blind_mode=true`
- RBAC: `rbac_policy.py` + `rbac_enforcement.py` — add route-level restrictions for variance-reveal and reconcile endpoints under blind mode

## Data model
**Extend `stock_count` collection** (no new collection needed):
```
blind_mode: bool (default false)
blind_initiated_by: user_id
blind_initiated_at: datetime
variance_revealed_at: datetime | null
variance_revealed_by: user_id | null
reconcile_approved_by: user_id | null
reconcile_approved_at: datetime | null
```

**Extend `stock_count` items subdoc** — no change to schema; `system_quantity` already stored server-side, just withheld from response when `blind_mode=True` and caller is store-level role.

## Backend
- **`POST /stock-count/start`** — accept `blind_mode: bool`; if true, require ADMIN/AREA_MANAGER/SUPERADMIN as initiator; store `blind_initiated_by`/`at`
- **`POST /stock-count/{count_id}/items`** — when `blind_mode=True` AND caller role is STORE_MANAGER/SALES_STAFF/WORKSHOP_STAFF, strip `system_quantity` and `expected_quantity` from each line in response (server holds them, staff never see them during count)
- **`GET /stock-count/{count_id}`** — when `blind_mode=True`: store-level roles see only `counted_quantity`; HQ roles (ADMIN/AREA_MANAGER/SUPERADMIN/ACCOUNTANT) see full variance detail including `system_quantity`, `variance`, `shrinkage_percentage`
- **`POST /stock-count/{count_id}/reveal-variance`** (new endpoint) — HQ-only; stamps `variance_revealed_at/by`; returns full per-item discrepancy including items with `counted_quantity=0` (missing stock); emits in-app notification to SUPERADMIN
- **`POST /stock-count/{count_id}/reconcile`** — for blind sessions, require ADMIN or higher (block STORE_MANAGER from self-reconciling); require `reconcile_approved_by` to differ from `blind_initiated_by` (four-eyes enforcement); stamp `reconcile_approved_by/at`; write existing variance to `audit_logs` with `blind_mode=true`
- **`GET /stock-count`** (list) — HQ roles see `variance_summary` on blind sessions; store roles see only session status/zone (no counts, no variances)

## Frontend
**`StockAudit.tsx`** — extend:
- "Blind Count" toggle on session-start modal (visible only to HQ roles); show warning banner "Staff will not see expected quantities"
- During an active blind session, scanning interface (`StockCountScanningInterface.tsx`) shows product name + barcode only — no "System Qty" column
- Completed blind session card: store staff see "Awaiting HQ Review" badge; HQ roles see variance chips (red = shrinkage, amber = overage) and a "Reveal & Review" button
- Reveal panel (HQ only): per-item table with `system_qty | counted_qty | variance | value_at_risk` (using `cost_price`); sortable by variance descending; CSV export

**No new page needed** — all within existing `StockAudit.tsx` flow with conditional rendering by role.

## Business rules
- A blind session can only be started by ADMIN, AREA_MANAGER, or SUPERADMIN — store staff cannot initiate their own blind count (prevents gaming)
- During counting, store staff receive zero system-quantity hints — not even after submitting an item; the server accepts the scan/entry silently
- Items not scanned at all are treated as `counted_quantity=0`; these surface as 100% variance in the reveal
- Variance reveal is restricted to HQ roles; store staff never see it, even after session closes
- Self-reconcile is blocked: the person who approved the blind session cannot also be the reconciler (four-eyes on shrinkage write-back)
- Reconciliation adjustments write to `stock_units` exactly as the existing `reconcile_stock_count` path — no new accounting entries needed (shrinkage is a stock-unit status change to SCRAPPED/DAMAGED, not a financial journal; finance module picks it up via COGS fallback)
- Blind session results are immutable in `audit_logs` once revealed — cannot be re-opened or re-counted (prevent repeated revision until a "good" number appears)
- Period lock (`check_period_locked`, finance.py:446) applies to reconcile — cannot write stock adjustments to a closed accounting month

## RBAC
| Action | SUPERADMIN | ADMIN | AREA_MANAGER | STORE_MANAGER | ACCOUNTANT | Others |
|---|---|---|---|---|---|---|
| Start blind session | Y | Y | Y | N | N | N |
| Scan/enter items | Y | Y | Y | Y | N | N |
| See system qty during count | N | N | N | N | N | N |
| Reveal variance | Y | Y | Y | N | Y (read-only) | N |
| Reconcile (write back) | Y | Y | N | N | N | N |
| View shrinkage report | Y | Y | Y | N | Y | N |

All enforcement via existing `require_roles()` pattern + new `blind_mode` check inside handler; RBAC policy registry (`rbac_policy.py`) gets three new rows for `/stock-count/*/reveal-variance`, blind-mode item GET, and blind reconcile.

## Integrations
- **In-app notifications** (`notifications` collection + bell API) — when a blind session reaches `complete` status, NEXUS/TASKMASTER emits an in-app bell to SUPERADMIN and initiating HQ user: "Blind count for [Zone] at [Store] ready for review"
- **Tally** — no new entry; shrinkage reconciliation writes to `stock_units` (status → SCRAPPED); existing NEXUS nightly Tally export sees the COGS impact via cost_at_sale on the affected products — no additional integration needed
- **Jarvis/ORACLE** — ORACLE's existing `_detect_discount_abuse` anomaly scan can be extended to flag stores with high blind-count variance frequency (repeatable shrinkage signal); advisory proposal only, no auto-execute

## Risk notes
- **POS continuity**: blind counts run alongside live trading (no store close needed); the existing `stock_units` status model handles concurrent sales during a count — items sold mid-count will create a variance that is expected; the reconcile step should note "count window" timestamps. Ship behind a feature flag (`BLIND_COUNT_ENABLED=false` default in `integrations` or env) so it can be toggled per store.
- **Reconcile vs accounting**: stock write-down (shrinkage) changes on-hand but does not auto-post an expense or inventory shrinkage journal. Finance will see it only via COGS fallback in P&L. If Avinash wants an explicit shrinkage expense line, a future extension is needed (out of scope here).
- **Staff coaching risk**: If store staff figure out that a "Blind" session means no qty hints, motivated bad actors simply won't scan missing items. Mitigation is in the four-eyes reconcile rule and HQ initiating the session unannounced.
- **No existing test coverage** for blind-mode logic — add pytest cases to `backend/tests/test_stock_count_blind.py` covering: (a) store role receives no system_qty, (b) HQ role receives full variance, (c) self-reconcile blocked, (d) reveal stamps correctly.

## Recommendation
Build later (Phase 3, after returns-integrity and invoice-serial PRs merge). The existing stock-count infrastructure is solid; this is a low-code-change, high-policy-enforcement extension. Most of the work is RBAC gating and response-field suppression — not new data pipelines. Target a 5-day sprint once the team is past current P1/P2 backlog.

## Owner decisions
- Q: Should store staff be told that a session is "blind" (i.e., they see the label "Blind Count in Progress") or should it appear identical to a regular count so they have no behavioural cue? | Why: If staff know it's blind, there is less chance of accidental over-entry; if they don't know, there is less chance of deliberate manipulation. Changes the UX label shown during scanning. | Options: a) Show "Blind Count" label to staff (transparent, less manipulation risk from honest staff) / b) Show "Count Session" with no blind indicator (covert audit, catches deliberate gaming but may confuse honest staff)

- Q: What is your shrinkage tolerance threshold before HQ is automatically alerted — i.e., at what variance % or rupee value should an in-app/WhatsApp notification fire immediately vs. queued for morning review? | Why: Determines the trigger condition on `reveal-variance` — whether TASKMASTER auto-escalates or just logs. | Options: a) Alert immediately if variance > 2% of session value / b) Alert if shrinkage rupee value > ₹5,000 per session / c) Always queue for morning review, no real-time alert

- Q: Should a blind count automatically lock the affected zone/fixture from further stock movements (transfers out, POS sales of affected SKUs) during the count window, or allow trading to continue freely? | Why: Lock prevents mid-count variances caused by concurrent sales but disrupts POS; free trading is operationally easier but means the reconcile must account for sales during the window. | Options: a) Soft lock — flag affected stock_units as "under audit" (warn cashier, don't block) / b) Hard lock — block SOLD status changes for affected SKUs during active blind session / c) No lock — reconcile handler auto-subtracts sales during window from expected qty
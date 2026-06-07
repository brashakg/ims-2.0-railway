# Feature #40: AI-Driven VIP Churn Prediction (Admin Restricted)
META: effort=M days=4 risk=LOW roi=4 quickwin=no deps=none phase=3

## Existing overlap
Substantial foundation already exists:

- **Churn risk signal** (`backend/api/routers/crm.py:1055-1140`, `_identify_churn_risk_customers()`): already buckets customers by recency — High (180+ days), Medium (91-179), Low (31-90). This feature deepens the same concept for VIPs only, replacing the flat-recency rule with a personalised buying-interval baseline.
- **RFM segmentation** (`crm.py:947-1052`, `_perform_rfm_segmentation()`): already identifies "Big Spenders" (LTV ≥ ₹25k or freq ≥ 3) and "Champions". VIP cohort selection reuses this logic.
- **Customer 360** (`crm.py` + `frontend/src/pages/customers/Customer360Dashboard.tsx`): already renders churn risk cards, LTV, loyalty tier, interaction history. The churn-risk card on this page is the primary UI extension point.
- **Lifecycle phase** (`crm.py:830-882`, `_determine_lifecycle_phase()`): already flags `at_risk` (180+ days) and `VIP` (LTV ≥ ₹1L or freq ≥ 20). Threshold for VIP is already codified here.
- **ORACLE agent** (`backend/agents/implementations/oracle.py`): already runs `_identify_churn_risk_customers()` context, emits anomaly narratives via Claude. The per-customer interval scan is a natural addition to ORACLE's hourly/EOD sweep.
- **Proposal system** (`backend/agents/proposals.py`): `is_reversible()` + `ProposalStore` already handles ORACLE-generated proposals for Superadmin review. An "intervention recommendation" proposal maps cleanly here.
- **Notification dispatch** (`backend/agents/implementations/megaphone.py`): already queues winback campaigns via `campaign_segments.winback`. MEGAPHONE is the correct agent to fire the outreach once admin decides to intervene.

No greenfield infrastructure required. This is a deepening of `crm.py` + `oracle.py` + `Customer360Dashboard.tsx`.

## Reuse (extend, don't rebuild)
- `backend/api/routers/crm.py` — extend `_identify_churn_risk_customers()` to add a VIP-only personalised-interval path; add a new endpoint `GET /crm/vip-churn` that returns the ranked watchlist
- `backend/agents/implementations/oracle.py` — add `_scan_vip_churn()` step to the EOD sweep (22:00 IST tick); reuse existing Claude narrative call (`_get_anomaly_narrative()`) for per-customer explanation
- `backend/agents/proposals.py` — reuse proposal type `winback_outreach` (new reversible tier-1) so ORACLE can queue an intervention recommendation for Superadmin one-click approval
- `backend/agents/implementations/megaphone.py` — reuse `_dispatch_scheduled_campaigns()` + `send_notification()` path; winback campaign already exists in `campaign_segments.winback`; extend to accept a `customer_id` list override (targeted, not segment-wide)
- `frontend/src/pages/customers/Customer360Dashboard.tsx` — extend existing churn-risk card to show personalised interval data (usual_interval_days, last_purchase_days_ago, overdue_by_days)
- `frontend/src/pages/customers/CustomerSegmentation.tsx` — add a "VIP Watch List" tab alongside existing RFM buckets
- `orders` collection — no schema change; query by customer_id for purchase timestamps

## Data model
New fields on **existing** `customers` collection (upserted by ORACLE EOD sweep — no migration needed for existing docs):
```
vip_churn_risk: {
  usual_interval_days: int,      # median inter-purchase gap across last N orders
  last_purchase_days_ago: int,   # computed at scan time
  overdue_by_days: int,          # last_purchase_days_ago - usual_interval_days (negative = not yet due)
  risk_score: float,             # overdue_by_days / usual_interval_days, capped 0-1
  risk_label: "NONE"|"WATCH"|"HIGH",
  last_scanned_at: datetime,
  narrative: str                 # Claude-generated one-liner (may be null if Claude unavailable)
}
```

New collection **`vip_churn_snapshots`** (daily, append-only — lightweight audit + trend):
```
{
  snapshot_id: str,
  store_id: str,
  scanned_at: datetime,
  vip_count: int,
  watch_count: int,
  high_risk_count: int,
  top_10: [ { customer_id, name, ltv, overdue_by_days, risk_label } ]
}
```
One doc per store per day. Used by the watchlist page to show trend (is the VIP pool shrinking?).

## Backend

**`GET /api/v1/crm/vip-churn`** (new endpoint in `crm.py`)
- Role gate: SUPERADMIN, ADMIN only
- Query params: `store_id` (required for ADMIN, optional for SUPERADMIN), `risk_label` filter, `sort_by` (overdue_by_days | ltv | last_purchase_days_ago), `limit` (default 50)
- Logic: aggregates `customers` where `vip_churn_risk.risk_label IN (WATCH, HIGH)` + `is_vip=True` (or LTV threshold); joins with latest `vip_churn_snapshots` for trend sparkline data
- Returns: ranked list with customer_id, name, mobile (masked last 4 visible), ltv, usual_interval_days, last_purchase_days_ago, overdue_by_days, risk_label, narrative, loyalty_tier

**`POST /api/v1/crm/vip-churn/{customer_id}/intervene`** (new endpoint in `crm.py`)
- Role gate: SUPERADMIN, ADMIN only
- Body: `{ intervention_type: "PERSONAL_CALL" | "EXCLUSIVE_OFFER" | "LOYALTY_BONUS" | "WINBACK_WHATSAPP", notes: str }`
- Side effects: creates `ai_proposals` doc (type=`winback_outreach`, tier-1 reversible) pre-approved, dispatches MEGAPHONE targeted send if type=WINBACK_WHATSAPP, writes `audit_logs` entry
- Ensures one intervention per customer per 30-day window (idempotency guard on proposals dedup key)

**ORACLE agent extension** (`oracle.py`, add `_scan_vip_churn()` called at EOD 22:00 sweep):
- Queries `customers` where LTV ≥ threshold AND order count ≥ min_orders_threshold
- For each VIP: fetches last N `orders` sorted by `created_at`, computes median inter-purchase interval
- Writes `vip_churn_risk` sub-doc back to customer (upsert)
- Appends daily snapshot to `vip_churn_snapshots`
- Emits `anomaly.detected` event for any new HIGH-risk customers (SENTINEL picks up)
- Calls Claude narrative for top 5 highest-risk customers (bounded API cost)

## Frontend

**`/crm/vip-churn`** — new page (`frontend/src/pages/customers/VipChurnWatchlistPage.tsx`):
- Access: SUPERADMIN / ADMIN only (ProtectedRoute)
- Layout: restrained table — no colour gradients; semantic-only colour (red text for HIGH, amber for WATCH, grey for NONE)
- Columns: Rank | Customer Name | Store | LTV | Usual Interval | Days Since Last Purchase | Overdue By | Risk | Last AI Note | Action
- Action column: "Intervene" button opens a modal with intervention_type picker + notes field; calls `/intervene` endpoint
- Filter bar: store picker (SUPERADMIN sees all; ADMIN sees their stores), risk_label chips, sort toggle
- Snapshot trend card at top: "VIP pool: 42 customers | Watch: 8 | High risk: 3 | vs last week: +2 HIGH"
- Empty state: "No VIP customers are overdue for a visit. Good health." (plain text, no illustration)

**Extend `Customer360Dashboard.tsx`** (existing page):
- In the existing churn-risk card: if `vip_churn_risk` sub-doc present, replace flat recency badge with "Usual visit every 18 mo — last visit 24 mo ago — 6 mo overdue" + risk_label chip
- Add "Mark Intervened" shortcut button (same `/intervene` endpoint) for inline action from the customer profile

**Nav**: add "VIP Watch List" as a child item under the existing "CRM" sidebar group — visible only to SUPERADMIN/ADMIN

## Business rules
- **VIP qualification**: customer is VIP if LTV ≥ threshold AND total_order_count ≥ min_orders. Both thresholds are owner decisions (see below). Computed at scan time from `orders` aggregation; not stored permanently so threshold changes take effect on next nightly scan.
- **Interval baseline**: median of inter-purchase gaps across last N completed orders (DELIVERED/CONFIRMED status only; CANCELLED/DRAFT excluded). Minimum 2 gaps required to compute a baseline — customers with fewer orders are excluded from this signal (they stay in flat-recency churn model instead).
- **Risk labelling**: NONE (overdue_by_days ≤ 0), WATCH (overdue_by_days 1–90), HIGH (overdue_by_days > 90). Boundaries are owner decisions.
- **Intervention idempotency**: only one intervention proposal per customer per 30-day rolling window. Second call within window returns existing proposal_id with HTTP 200 (no duplicate). Owner can force-override with `force=true` query param (SUPERADMIN only).
- **Narrative cost cap**: Claude called for maximum 10 customers per EOD sweep (highest risk_score first). Remaining customers get `narrative=null` (UI shows "—"). Prevents runaway API spend.
- **Audit**: every `/intervene` call writes to `audit_logs` regardless of outcome (action=`VIP_CHURN_INTERVENTION`, entity_type=`customer`). Immutable.
- **No auto-send without approval**: WINBACK_WHATSAPP intervention fires MEGAPHONE only after the endpoint call — it is an explicit admin action, not an agent auto-send. DISPATCH_MODE gating applies as usual.
- **No POS / pricing mutation**: this feature is read + notification only. It never touches orders, prices, or balances. No feature flag needed.

## RBAC
| Role | Can view `/crm/vip-churn` | Can call `/intervene` | Can see VIP card on Customer 360 |
|---|---|---|---|
| SUPERADMIN | Yes — all stores | Yes | Yes |
| ADMIN | Yes — their stores only | Yes | Yes |
| AREA_MANAGER | No | No | No |
| STORE_MANAGER and below | No | No | No |

Enforcement: `require_roles(SUPERADMIN, ADMIN)` dependency on both new endpoints. Store scoping for ADMIN enforced by checking `user.store_ids` against the requested `store_id` parameter (same pattern as existing store-scoped endpoints in `crm.py`).

## Integrations
- **ORACLE agent** — runs the nightly scan (EOD 22:00 tick); writes `vip_churn_risk` sub-docs and snapshots
- **Claude API** (via `backend/agents/claude_client.py`) — narrative generation for top-10 HIGH-risk customers per sweep; fail-soft (narrative=null if ANTHROPIC_API_KEY absent)
- **MEGAPHONE agent** — dispatches WhatsApp outreach when intervention_type=WINBACK_WHATSAPP; reuses existing `winback` campaign template; DISPATCH_MODE gated
- **No Shopify / Razorpay / Tally involvement**

## Risk notes
- **Data sparsity**: customers with fewer than 3 orders have no reliable interval baseline. The feature silently excludes them (they stay in the existing flat-recency model). No regression risk to existing churn signal.
- **Scan cost at scale**: nightly aggregation over `orders` per VIP customer. At current scale (hundreds of VIPs across 6 stores) this is a sub-second Mongo aggregation. Index on `(customer_id, created_at, status)` is already present via the orders router. No new index needed.
- **Claude API cost**: hard-capped at 10 narratives per sweep (~10 short prompts, negligible cost). Owner controls whether `ANTHROPIC_API_KEY` is set.
- **No POS risk**: read-only analysis + optional WhatsApp send. No order, price, or balance touched. No feature flag required.
- **Intervention abuse**: the 30-day idempotency guard prevents spamming a VIP customer. SUPERADMIN force-override is audited.

## Recommendation
Build now — it reuses ~80% of existing infrastructure (ORACLE sweep, proposal system, MEGAPHONE dispatch, Customer 360 card, RFM segmentation), adds no new collections of consequence, and directly addresses senior-leadership retention of the highest-LTV customers. ROI is high relative to effort (4 days). The only genuine new code is the interval-baseline computation in `oracle.py`, the two new endpoints in `crm.py`, and the watchlist page.

## Owner decisions
- Q: What LTV threshold qualifies a customer as "VIP" for this analysis? | Why: Sets the size of the cohort ORACLE scans and admins monitor — too low floods the list, too high misses important customers | Options: a) ₹50,000 lifetime spend / b) ₹1,00,000 (current `_determine_lifecycle_phase` threshold) / c) ₹25,000 (current "Big Spender" RFM threshold) / d) owner sets a custom number
- Q: What minimum number of past purchases is required before the interval baseline is computed for a customer? | Why: Fewer purchases = unreliable median; excluded customers fall back to the flat-recency churn model | Options: a) 2 orders (widest net) / b) 3 orders (recommended) / c) 5 orders (stricter, smaller cohort)
- Q: How many days overdue should trigger the HIGH risk label (vs WATCH)? | Why: Determines how urgently the watchlist surfaces a customer to leadership | Options: a) 30 days overdue = HIGH / b) 60 days / c) 90 days (current default in build spec) / d) 50% of usual interval (e.g., if usual = 12mo, HIGH at 6mo overdue)
- Q: When admin clicks "Intervene — Personal Call", should the system create a follow-up task assigned to the store manager, or just log the action? | Why: If a task is created, the store manager gets an in-app bell notification and it enters the SLA escalation engine; if log-only, leadership tracks it themselves | Options: a) Create a P1 follow-up task assigned to store manager / b) Log only in audit trail / c) Both — task + audit
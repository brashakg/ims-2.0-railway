# Feature #18: Gamified Vendor Rebate Tracker
META: effort=L days=8 risk=MED roi=4 quickwin=no deps=none phase=3

## Existing overlap
- **Vendor master** (`backend/api/routers/vendors.py:69-99`) has `vendor_id`, `credit_days`, `legal_name`, `gstin` — but no rebate tier fields.
- **Purchase Invoices** (`backend/api/routers/purchase_invoices.py`) record per-vendor billed amounts with `taxable_amount`, `total_amount`, `invoice_date` — the spend volume needed to evaluate rebate targets is derivable via aggregation over `vendor_bills`.
- **3-Way Match engine** (`backend/api/services/purchase_match.py`) has `moving_average_cost()` and `valuation_trueup_for_invoice()` — same aggregation patterns apply to rebate accrual math.
- **Finance/AP ledger** (`backend/api/routers/finance.py:1051-1106`, `backend/api/services/ap_engine.py`) tracks vendor outstanding balance via `build_ledger()`; rebate credit posts as a debit-note reducing AP balance — same ledger entry path.
- **Vendor Returns / Debit Notes** (`backend/api/routers/vendor_returns.py`) already generates `credit_note_number` and posts `credit_note_amount` to vendor balance — a rebate posting is structurally identical.
- **AI Proposals system** (`backend/agents/proposals.py`) supports reversible tier-1 auto-execution (e.g., `draft_po`); rebate ledger posting is a new reversible action type ORACLE/TASKMASTER can propose at month-end.
- **Period lock** (`backend/api/routers/finance.py:446-481`) guards posting to closed months — rebate credit must check this before posting.
- **Tally nightly export** (`backend/agents/implementations/nexus.py`, `tally_exports` collection) already runs at 23:00; rebate credit vouchers must flow into the same Tally JV pipeline.
- **Audit logs** (`audit_logs` collection) and `agent_audit_log` are fully operational — rebate posting must write both.

## Reuse (extend, don't rebuild)
- **`vendors` collection** — add `rebate_agreements[]` sub-array (new fields only, no schema breaking change)
- **`vendor_bills` collection** — add `rebate_eligible: bool` flag per invoice (default true, accountant can exclude)
- **`vendor_returns.py` router** — extend `credit_note_number` generation pattern for rebate credit notes (same numbering sequence, new `doc_type: REBATE_CREDIT`)
- **`ap_engine.py` `build_ledger()`** — extend to include REBATE_CREDIT entry type so the AP ledger balance reduces correctly
- **`finance.py` AP aging endpoint** — rebate credits already reduce outstanding naturally once ledger entry exists
- **`proposals.py`** — register `rebate_accrual_post` as a new reversible tier-1 type so ORACLE/TASKMASTER can auto-propose month-end posting
- **`nexus.py` Tally export pipeline** — add `REBATE_CREDIT` voucher type to the nightly XML builder (`_build_tally_export`)
- **`frontend/src/pages/purchase/`** — extend the existing Purchase page tab structure (POs, GRNs, Invoices, Vendor Returns tabs already exist) with a new "Rebates" tab
- **`audit_logs` + `agent_audit_log`** — no change needed; rebate posting writes to these using existing helpers

## Data model

**New collection: `vendor_rebate_agreements`**
```
{
  agreement_id: uuid,
  vendor_id: ref → vendors,
  name: str,                          // e.g. "FY26 Q1 Volume Rebate"
  financial_year: str,               // "2026-27" (Apr–Mar)
  period: "MONTHLY"|"QUARTERLY"|"ANNUAL",
  scope: {                            // what purchases count
    category_filter: [str]|null,      // null = all categories
    product_ids: [str]|null           // null = all products from vendor
  },
  tiers: [
    {
      tier_name: str,                 // e.g. "Silver", "Gold"
      min_spend_inr: int,             // ₹ paisa-exact
      rebate_pct: float,             // 0.0–100.0
      rebate_flat_inr: int|null      // alternative flat ₹ amount (mutually exclusive with pct)
    }
  ],
  cap_inr: int|null,                 // max rebate claimable this period (null = uncapped)
  auto_post: bool,                   // true = TASKMASTER auto-proposes at period-end; false = manual only
  is_active: bool,
  created_by: str,
  created_at: datetime,
  updated_at: datetime
}
```

**New collection: `vendor_rebate_ledger`**
```
{
  ledger_id: uuid,
  agreement_id: ref → vendor_rebate_agreements,
  vendor_id: ref → vendors,
  period_start: date,
  period_end: date,
  cumulative_spend_inr: int,         // sum of eligible invoice taxable_amount for period
  achieved_tier: str|null,           // tier_name of highest tier cleared; null if no tier hit
  rebate_earned_inr: int,            // computed (paisa-exact)
  status: "ACCRUING"|"PENDING_POST"|"POSTED"|"POSTED_OVERRIDE",
  posted_at: datetime|null,
  posted_by: str|null,               // user_id or "TASKMASTER"
  credit_note_number: str|null,      // generated on POST, same sequence as vendor_returns
  tally_voucher_id: str|null,        // stamped by Tally export pipeline
  proposal_id: str|null,            // links to ai_proposals if auto-proposed
  notes: str|null,
  created_at: datetime,
  updated_at: datetime
}
```

**Extended fields on `vendor_bills`** (existing collection, additive only):
```
rebate_eligible: bool   // default true; accountant can set false per invoice (e.g. credit notes, adjustments)
```

## Backend

**`backend/api/routers/vendors.py`** (extend):
- `POST /vendors/{vendor_id}/rebate-agreements` — create rebate agreement (ADMIN/SUPERADMIN); validates tier monotonicity (min_spend strictly increasing across tiers), enforces paisa-exact amounts
- `GET /vendors/{vendor_id}/rebate-agreements` — list agreements with current-period progress snapshot
- `PUT /vendors/{vendor_id}/rebate-agreements/{agreement_id}` — update (only if no POSTED ledger row for current period; else 409)
- `DELETE /vendors/{vendor_id}/rebate-agreements/{agreement_id}` — soft-delete (set is_active=false); cannot delete if POSTED rows exist

**New router: `backend/api/routers/vendor_rebates.py`**:
- `GET /vendor-rebates/dashboard` — ADMIN/ACCOUNTANT/SUPERADMIN: all active agreements, current-period spend vs tiers, earned rebate, status; sortable by vendor, period, achievement %; the "gamified" view
- `GET /vendor-rebates/{agreement_id}/ledger` — full posting history for one agreement
- `POST /vendor-rebates/{ledger_id}/post` — ACCOUNTANT/ADMIN: manually post a PENDING_POST ledger row; checks period lock (raises 423 if locked), generates credit_note_number (reuses vendor_returns numbering), writes to `vendor_rebate_ledger`, calls `ap_engine.add_credit_entry()` to reduce vendor balance, writes `audit_logs`
- `POST /vendor-rebates/{ledger_id}/mark-ineligible` — ACCOUNTANT: override a specific invoice's `rebate_eligible` flag (patched on `vendor_bills`); re-triggers spend recomputation for that period row
- `GET /vendor-rebates/summary` — SUPERADMIN/ADMIN: org-wide rebate pipeline (accruing, pending, posted, earned YTD); feeds Jarvis ORACLE narrative

**New service: `backend/api/services/rebate_engine.py`** (pure, stateless — same pattern as `purchase_match.py`):
- `compute_period_spend(vendor_id, agreement, period_start, period_end, db)` — aggregates `vendor_bills` where `vendor_id` matches, `bill_date` in period, `rebate_eligible=true`, `status != CANCELLED`; returns paisa-exact sum
- `resolve_tier(spend_inr, tiers)` — returns highest tier cleared (or None); pure function, unit-testable
- `compute_rebate(spend_inr, achieved_tier)` — applies pct or flat; caps at `cap_inr`; paisa-exact; returns `(rebate_earned_inr, tier_name)`
- `refresh_ledger_row(agreement_id, period_start, db)` — called on any invoice add/update in the period: recomputes spend + tier + earned, upserts `vendor_rebate_ledger` row (ACCRUING if below top tier, PENDING_POST when any tier cleared and period ended)

**Extend `backend/agents/implementations/taskmaster.py`**:
- New method `_check_rebate_period_ends()` in 5-min tick: for each active agreement where period ends today (or yesterday), calls `refresh_ledger_row()`, transitions ACCRUING → PENDING_POST, creates `ai_proposals` doc of type `rebate_accrual_post` (tier-1 reversible) if `auto_post=true`

**Extend `backend/agents/proposals.py`**:
- Register `rebate_accrual_post` in `REVERSIBLE_TYPES`; executor calls the same logic as manual `/post` endpoint

**Extend `backend/agents/implementations/nexus.py` `_build_tally_export()`**:
- Include `POSTED` rebate ledger rows for the export date as debit-note vouchers in the Tally XML (credit vendor AP account, debit "Rebate Received" ledger head — exact Tally ledger name is an owner decision)

**Extend `backend/api/services/ap_engine.py`**:
- `add_credit_entry(vendor_id, amount_inr, ref_id, doc_type)` — inserts into the vendor ledger as REBATE_CREDIT entry type; reduces outstanding; called by rebate post endpoint

## Frontend

**Extend `frontend/src/pages/purchase/` — new "Rebates" tab** (consistent with existing POs / GRNs / Invoices / Vendor Returns tabs):

- **Rebate Dashboard card grid** (one card per active agreement):
  - Vendor name + agreement name
  - Progress bar: current spend ÷ next-tier threshold (colour: gray below first tier, amber near next tier, green on tier achieved)
  - Badge: "NO TIER YET" / "SILVER ✓" / "GOLD ✓" etc. (semantic colour only — green = achieved, amber = close, neutral = far)
  - Earned this period (₹ formatted with Indian lakh/crore grouping)
  - Days remaining in period
  - Status chip: ACCRUING / PENDING POST / POSTED
  - CTA: "Post Credit Note" button (ACCOUNTANT/ADMIN only, visible when PENDING_POST)

- **Agreement setup drawer** (ADMIN/SUPERADMIN): vendor picker, period selector, tier table (add/remove rows), scope filters (category multiselect or all), cap field, auto_post toggle; all existing form patterns from PurchaseOrderForm

- **Ledger history modal**: table of posted credit notes with amount, tier achieved, credit_note_number, posted_by, posted_at; links to Tally export status

- **Jarvis ORACLE narrative panel** (existing JarvisPage activity feed): ORACLE surfaces "Vendor X rebate at 87% of Gold tier — ₹23,000 gap in 12 days. Projected: GOLD. Expected credit: ₹14,500." — reuses existing agent activity card component

All UI follows restrained executive style: no celebratory animations, single accent colour for tier badge, neutral progress bars, no emoji.

## Business rules

- **Paisa-exact arithmetic**: all spend sums, rebate calculations, and credit note amounts stored as integer paise; no float rounding errors
- **Period lock respected**: posting a rebate credit to a period-locked month returns HTTP 423 (reuses `check_period_locked` from `finance.py:446`)
- **Tier monotonicity guard**: agreement creation/update rejects tiers where `min_spend[i] >= min_spend[i+1]`; enforced in `rebate_engine.resolve_tier()` and at the API layer
- **No retroactive recomputation beyond 2 prior financial years**: `compute_period_spend` raises 400 if `period_start` is more than 2 FYs ago (prevents inadvertent bulk ledger mutation)
- **Invoice eligibility changes recompute immediately**: toggling `rebate_eligible` on a `vendor_bill` triggers `refresh_ledger_row()` for that agreement's current period; if status was POSTED, flags `status=POSTED_OVERRIDE` and writes audit entry — does NOT reverse the posted credit (accountant must issue a manual adjustment)
- **Double-post guard**: `vendor_rebate_ledger` has a unique index on `(agreement_id, period_start)` with `status IN (POSTED, POSTED_OVERRIDE)`; second post attempt returns 409
- **Cap enforcement**: `compute_rebate()` applies `cap_inr` before returning; cap is displayed on the progress card so accountants can see ceiling
- **Audit everything**: every post, eligibility override, and proposal execution writes to `audit_logs` (action=REBATE_POSTED / REBATE_ELIGIBILITY_OVERRIDE) and `agent_audit_log` (for TASKMASTER-initiated)
- **Tally ledger mapping**: rebate credit posts to a configurable "Rebate Received" ledger head stored in `tax_settings` (new field `rebate_ledger_head`); default "Rebate Received A/c" — accountant must confirm this maps to their Chart of Accounts

## RBAC

| Role | Permission |
|---|---|
| SUPERADMIN, ADMIN | Full: create/edit/delete agreements; view dashboard; approve proposals; post credit notes |
| ACCOUNTANT | View dashboard + ledger; post credit notes; mark invoices ineligible; cannot create/edit agreements |
| AREA_MANAGER | View dashboard (own-area vendors only, store-scoped); no posting |
| STORE_MANAGER | View only: agreements and progress for vendors who supply their store |
| CATALOG_MANAGER, all others | No access |
| JARVIS agents (ORACLE, TASKMASTER) | Backend-only: read agreements, write proposals, post via approved proposal executor |

All endpoints registered in `rbac_policy.py` POLICY list; middleware enforces at request time.

## Integrations
- **Tally (NEXUS nightly 23:00)**: POSTED rebate rows included as debit-note vouchers in nightly Tally XML export; `tally_voucher_id` stamped back on `vendor_rebate_ledger` row
- **ORACLE agent**: reads `/vendor-rebates/summary` on hourly tick; surfaces narrative in activity feed when any agreement is within 15% of a tier threshold or has PENDING_POST rows older than 3 days
- **TASKMASTER agent**: period-end detection (5-min tick) triggers PENDING_POST transition and auto-proposal when `auto_post=true`
- **MSG91 / WhatsApp**: no direct integration; MEGAPHONE is not triggered (rebate is an internal accounting action, not a customer-facing notification)
- **Shopify / Razorpay / Shiprocket**: none

## Risk notes
- **Accounting correctness is the primary risk**: rebate credit reduces vendor AP balance. If the Tally ledger head is misconfigured, the Tally trial balance will be unbalanced. Mitigation: same balanced-XML validation already in `_build_tally_export()`; flag `tally_exports.balanced=false` if debit ≠ credit, same as today.
- **Period-end race condition**: if TASKMASTER ticks at midnight exactly when period ends and an invoice is being booked simultaneously, `compute_period_spend` may see a partial view. Mitigation: `refresh_ledger_row` uses a MongoDB `$sum` aggregation (single atomic read) not a read-modify-write; the worst case is a 5-minute lag before the next tick corrects it. This is acceptable for a month-end accrual.
- **No POS touch**: rebates are purely a purchase/finance flow. Zero POS risk. No feature flag needed for POS.
- **Feature flag for auto-post**: the `auto_post` boolean on each agreement is the feature flag — owner can leave it false on all agreements initially (manual-only mode) while auditing accuracy, then flip to true vendor-by-vendor. No code flag needed.
- **POSTED_OVERRIDE complexity**: if an invoice is retroactively marked ineligible after posting, the system flags the ledger row but does not auto-reverse. This is intentional (accountant must issue an explicit adjustment debit note). Communicate this clearly in the UI tooltip.

## Recommendation
Build later (phase 3 backlog). The AP ledger, vendor master, and Tally pipeline it depends on are solid, but the feature requires a new pure-service module (`rebate_engine.py`), a new router, two new collections, and a TASKMASTER tick extension — meaningful scope. It is not a quick win. Prioritise after the live-QA blockers (P0/P1 security fixes, store-IDOR sweep) and the returns/money-integrity branch are merged. Start with manual-only mode (`auto_post=false` on all agreements) so the Accountant can validate calculation accuracy before enabling TASKMASTER automation.

## Owner decisions
- Q: Which vendors and categories should be covered at launch? | Why: Determines how many agreements need to be configured and whether the category_filter scope feature is needed in v1 or can be deferred (all-vendor-spend is simpler to build and validate first) | Options: a) All purchases from a vendor count (simplest), b) Filter by category (frames only, lenses only), c) Filter by specific SKUs
- Q: Should the rebate credit reduce the vendor's next payable (AP offset) or post as income to a separate P&L line? | Why: Changes the Tally ledger mapping and how Finance sees it — AP offset is cleaner for cash flow; income line is cleaner for margin reporting | Options: a) Reduce AP outstanding (debit vendor payable, credit rebate received), b) Post as other income (debit rebate receivable, credit other income), c) Both — offset AP first, remainder as income
- Q: What Tally ledger head name should rebate credits post to? | Why: Must exactly match the ledger name in your Tally company file, or the XML import will create a phantom ledger | Options: Whatever name currently exists in your Tally chart of accounts (ask your accountant)
- Q: Should TASKMASTER auto-post the credit note at period-end (fully automated) or only propose it for Accountant review? | Why: Auto-post is faster but carries accounting risk if spend data has errors; proposal-only is safer for the first 2-3 cycles | Options: a) Manual only (accountant clicks "Post" after reviewing), b) Auto-propose via TASKMASTER (Superadmin approves in Jarvis), c) Fully auto-post with no approval step
- Q: What is the rebate period — monthly, quarterly, or annual per vendor? | Why: Affects how often the ledger row resets and when period-end detection triggers; some vendors may use different periods | Options: a) All agreements use the same period (monthly/quarterly/annual — pick one), b) Per-agreement period (flexible but more complex UI and testing)
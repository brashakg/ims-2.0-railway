# Feature #49: Family/Household Loyalty Points Wallet
META: effort=M days=5 risk=MED roi=4 quickwin=no deps=none phase=3

## Existing overlap
IMS already has substantial groundwork:
- **`loyalty_accounts` collection** (`backend/api/routers/loyalty.py`) — per-customer balance, tier, lifetime_earned, lifetime_redeemed. Atomic `try_debit()` uses guarded `find_one_and_update` (loyalty_repository.py:101-163). No family pooling.
- **`patients[]` embedded array on `customers`** (`backend/api/routers/customers.py:178-204`) — family members with patient_id, name, mobile, relation. Relation types already tracked (mother/child/spouse etc.).
- **Loyalty tier engine** (`backend/api/services/loyalty_engine.py:26-34`) — `compute_tier()` from lifetime_earned thresholds (BRONZE/SILVER/GOLD/PLATINUM). Currently per-customer only.
- **Atomic debit guard** (`loyalty_repository.py:101-163`) — `try_debit()` filter-in-guard prevents double-spend. This exact pattern must be reused for household-level debit.
- **Earn/redeem routes** (`loyalty.py` — `/earn`, `/redeem`, `/balance`) — currently customer-scoped.
- **POS `posStore.ts`** — `pendingLoyaltyRedeem` intent, LOYALTY tender path. Already threads `customer_id` through POS flow.
- **Customer 360** (`frontend/src/pages/customers/Customer360Dashboard.tsx`) — shows loyalty tier, balance. Natural home for household wallet panel.
- **DPDP consent ledger** (`customers.py:1364-1525`) — each patient/customer has separate consent. Household wallet must not merge clinical data — already by design since patients[] holds clinical data separately.
- **Audit trail** (`audit_logs` collection) — immutable before/after; must be used for all wallet mutations.

**Gap**: No `household_wallets` grouping, no cross-customer point pooling, no household-tier computation, no "add member to wallet" UX.

## Reuse (extend, don't rebuild)
- **`loyalty_repository.py`** — extend with `HouseholdWalletRepository` using same `find_one_and_update` atomic pattern for household-level debit; reuse `adjust_balance()` as model
- **`loyalty.py` router** — add household wallet endpoints here (keep loyalty domain unified); do NOT create a new router
- **`loyalty_engine.py`** — extend `compute_tier()` to accept `lifetime_earned` from household aggregate; no new logic needed
- **`customers.py` router** — extend customer-create/update to accept optional `household_id` linkage; dedup check against existing household members
- **`loyalty_accounts` collection** — add `household_id` foreign key field (nullable); existing rows default null = standalone
- **`customer_repository.py`** — extend `find_by_mobile()` to also surface household_id for merge lookups
- **`Customer360Dashboard.tsx`** — add "Household Wallet" panel/tab showing pooled balance, member list, tier; reuse existing loyalty card layout
- **`posStore.ts`** — extend loyalty redeem intent to carry `household_id` when customer is wallet-linked; backend resolves which account to debit

## Data model
**New collection: `household_wallets`**
```
{
  household_id:       string (uuid, generated at creation),
  name:               string  (e.g., "Sharma Family"),
  anchor_customer_id: string  (primary account holder; owns tier + decides wallet settings),
  member_customer_ids: [string]  (list of IMS customer_ids in the household; max owner-decided),
  balance_points:     int     (POOLED balance — atomic, single source of truth),
  lifetime_earned:    int     (sum across all members, used for household tier),
  lifetime_redeemed:  int,
  tier:               enum BRONZE|SILVER|GOLD|PLATINUM  (computed from lifetime_earned),
  earn_attribution:   enum ANCHOR|PROPORTIONAL  (owner decision — see Q1),
  created_at:         datetime,
  updated_at:         datetime,
  is_active:          bool
}
```

**New collection: `household_wallet_txns`** (immutable ledger)
```
{
  txn_id:           string,
  household_id:     string,
  earning_customer_id: string  (which member earned/spent),
  type:             enum EARN|REDEEM|ADJUST|MEMBER_ADDED|MEMBER_REMOVED|MERGE,
  points:           int  (positive=earn, negative=redeem),
  rupee_value:      float,
  order_id:         string (nullable),
  store_id:         string,
  before_balance:   int,
  after_balance:    int,
  created_by:       string  (user_id),
  created_at:       datetime,
  expires_at:       datetime (nullable — per-earn lot expiry, same rule as standalone)
}
```

**New field on `loyalty_accounts`**
- `household_id: string | null` — set when customer joins a household wallet; null = standalone account

**New field on `customers`**
- `household_id: string | null` — denormalized for fast POS lookup without joining loyalty_accounts

## Backend

**POST `/api/v1/loyalty/households`** — Create a new household wallet. Anchor customer must exist. Returns `household_id`. Roles: STORE_MANAGER, ADMIN, SUPERADMIN.

**POST `/api/v1/loyalty/households/{household_id}/members`** — Add a customer to an existing wallet. Validates: customer not already in another household, active IMS customer. Migrates existing standalone `loyalty_account.balance_points` into household balance (owner decision on migration policy — see Q2). Roles: STORE_MANAGER, ADMIN, SUPERADMIN.

**DELETE `/api/v1/loyalty/households/{household_id}/members/{customer_id}`** — Remove a member. Splits their proportional share back to a standalone `loyalty_account` OR forfeits (owner decision — see Q3). Roles: ADMIN, SUPERADMIN.

**GET `/api/v1/loyalty/households/{household_id}`** — Returns household balance, tier, member list with per-member earned lifetime total (for attribution display). Roles: all store roles (read-only).

**GET `/api/v1/loyalty/households/{household_id}/transactions`** — Paginated ledger of `household_wallet_txns`. Roles: STORE_MANAGER+.

**PATCH `/api/v1/loyalty/earn`** (extend existing) — When `customer_id` has a `household_id`, earn goes to the household `balance_points` (atomic `$inc` on `household_wallets`) AND logs member attribution in `household_wallet_txns.earning_customer_id`. Standalone customers unchanged.

**PATCH `/api/v1/loyalty/redeem`** (extend existing) — When `customer_id` has a `household_id`, debit from household wallet using guarded `find_one_and_update` (`balance_points >= requested_amount`). Any household member can redeem from the shared pool. Logs `earning_customer_id = redeeming_customer_id` in txn. Standalone flow unchanged.

**GET `/api/v1/loyalty/balance/{customer_id}`** (extend existing) — When customer has `household_id`, return household balance + tier instead of individual. Also return `is_household_member: true`, `household_id`, `member_count` so POS and Customer360 can show the right UI.

**POST `/api/v1/loyalty/households/{household_id}/merge`** (SUPERADMIN only) — Merge two households. Combines balances, deduplicates members. Fully audited; irreversible (creates MERGE txn row as record). Addresses future edge case when a couple both had standalone wallets before marriage.

## Frontend

**`CustomerHouseholdWallet.tsx`** (new component, used inside `Customer360Dashboard.tsx`)
- Shows pooled balance (large numeral), current tier badge, member roster (name, relation, points earned by them lifetime, last activity date)
- "Add family member" button — opens modal to search IMS customers by name/mobile and link
- "Remove member" action (ADMIN only) with confirmation dialog showing split-back balance
- Matches restrained light-only design: white card, single accent color for tier badge, neutral table rows, no color except tier chip

**Extend `Customer360Dashboard.tsx`**
- Replace individual loyalty card with `CustomerHouseholdWallet` when `is_household_member=true`
- Show "Standalone account" state with "Create household wallet" CTA when `is_household_member=false`

**Extend POS loyalty step (`posStore.ts` + POS review step)**
- On loyalty balance fetch (`GET /loyalty/balance/{customer_id}`), if `is_household_member=true`, display "Family Wallet — [N] members" label next to balance so cashier knows the pool belongs to a household
- No change to redeem UX; backend resolves source transparently

**`HouseholdWalletPage.tsx`** (new page under `/crm/households` or `/loyalty/households`)
- Admin/SUPERADMIN-only list of all household wallets with search by anchor customer name
- Click-through to wallet detail (members, balance, txn ledger)
- Merge wallet action (SUPERADMIN only)

## Business rules
- A customer can belong to exactly ONE household wallet at a time. Attempting to add a customer already in another household returns 409.
- Clinical data (prescriptions, eye tests) remains per-patient and is never merged or cross-visible — household wallet touches only loyalty points and (optionally) corporate discount allowance.
- Any household member can EARN into and REDEEM from the pooled balance. The anchor customer does not have veto power over redeeming — the pool is shared equally.
- Per-lot point expiry (already implemented in standalone `loyalty_transactions`) applies at the household level via `household_wallet_txns.expires_at`. Expiry sweep (`loyalty.py expire_sweep`) must be extended to also sweep `household_wallet_txns`.
- Tier computed from household `lifetime_earned` using the same `compute_tier()` thresholds as standalone. Household tier applies to ALL members (any member shopping gets household tier benefits).
- Point earn is subject to the same `min_order_for_earn` and category multiplier rules as standalone — just credited to household pool.
- `max_redeem_pct` (percent-of-order cap on redemption) applies per transaction, sourced from `loyalty_settings` unchanged.
- Household creation requires the anchor customer's explicit consent (DPDP — "household data sharing" purpose must be GRANTED; extend `dpdp_consent_ledger` with purpose `HOUSEHOLD_LOYALTY`).
- All mutations (create wallet, add/remove member, earn, redeem, merge) write an immutable `audit_logs` row with before/after state.
- Removal of a member from a household is irreversible within 24 hours (cooling-off) — the split-back transfer is queued with a 24h hold flag to allow cancellation by ADMIN.

## RBAC
- **SUPERADMIN, ADMIN**: Full access — create, view, add/remove members, merge wallets, adjust balance
- **AREA_MANAGER, STORE_MANAGER**: Create wallet, add members, view balance/ledger, cannot merge or adjust
- **SALES_CASHIER, SALES_STAFF, CASHIER**: Read-only balance display in POS (via extended `/loyalty/balance/` response); cannot manage membership
- **OPTOMETRIST, WORKSHOP_STAFF**: No access to wallet management; loyalty is not part of their workflow
- **ACCOUNTANT**: View-only on household wallet list (for reconciliation); cannot manage membership
- Household wallet management page (`/crm/households`) gated to STORE_MANAGER+

## Integrations
- **Jarvis / ORACLE agent**: After household wallet reaches GOLD tier, ORACLE can propose a targeted "family bundle" campaign (advisory tier-3 proposal); no auto-execution. Extend ORACLE's `_propose_reorders()` pattern as a new `_flag_high_value_households()` method.
- **MEGAPHONE agent**: Birthday campaigns (already implemented) should fire for the anchor customer when ANY household member has a birthday — extend `campaign_segments.py` birthday segment to include household member DOBs when `household_id` is set.
- **MSG91**: No new integration needed. Notification goes to anchor customer's mobile (household communication point).
- **Shopify / Razorpay / Tally**: No direct integration. Online orders ingested via `online_order_mapper.py` still resolve to a `customer_id`; if that customer has a `household_id`, the earn step credits household pool automatically (transparent).
- **Tally**: Loyalty liability is a balance-sheet item. Household pooling does not change the accounting entry — point liability is still per-point-issued, regardless of which family member uses it.

## Risk notes
- **Atomic concurrency**: Two family members checking out simultaneously at two POS terminals must not double-spend the household balance. The existing `try_debit()` guarded `find_one_and_update` on `household_wallets.balance_points` handles this correctly — this is the highest-risk line and must be the first thing tested.
- **POS revenue-critical path**: Changes to `posStore.ts` and the loyalty redeem backend are in the POS hot path. Ship behind a feature flag (`HOUSEHOLD_WALLET_ENABLED=false` by default); enable per-store via `integrations` or `business_settings` before rollout.
- **Migration of existing balances**: When a customer with an existing standalone `loyalty_account` joins a household, their existing points must be migrated atomically (zero-out standalone, add to household pool). A failed migration mid-flight would create phantom points — use a two-phase write: (1) stamp `loyalty_accounts.household_id`, (2) transfer balance to `household_wallets`, (3) write MERGE txn. If step 2 fails, rollback step 1 via compensating update.
- **Tier downgrade on member removal**: If the anchor customer's personal lifetime_earned is lower than the household threshold, removing members could downgrade the anchor's tier mid-session. Implement a 30-day tier-protection grace period (same as standalone tier protection if already in codebase, or add it).
- **DPDP consent**: Household data sharing must be GDPR/DPDP-compliant. A new consent purpose (`HOUSEHOLD_LOYALTY`) must be granted by ALL members before they are linked. This adds a consent-grant step to the "add member" flow and must be in the audit trail.
- **Family breakup edge case**: Divorce/estrangement — ADMIN must be able to dissolve a household wallet and split balances. The 24h cooling-off window on member removal is essential but the dissolution endpoint needs careful design (pro-rata split vs anchor takes all).

## Recommendation
Build later (Phase 3, after core loyalty is proven stable in production). The standalone loyalty engine is complete and solid. Household pooling adds meaningful ROI for family-oriented optical retail (a primary use case), but it touches the POS hot path and requires DPDP consent extension — both warrant careful staging. Recommended: deploy standalone loyalty for one full business cycle (3 months), measure adoption, then ship household wallet with the feature flag off until validated in one pilot store.

## Owner decisions
- Q: When a customer joins a household wallet, what happens to their existing standalone loyalty points? | Why: Determines the migration strategy — pooling vs forfeit changes the accounting liability and customer expectation significantly | Options: (a) Merge into household pool (customer keeps all points, now shared with family); (b) Keep standalone balance separate (member earns/redeems from household going forward, old balance stays personal until spent); (c) Forfeit old balance on joining (simple but customer-hostile)
- Q: What is the maximum number of customers allowed in one household wallet? | Why: Sets a hard cap to prevent abuse (e.g., a shopkeeper linking all customers to one "family") | Options: (a) 4 members (nuclear family); (b) 6 members (joint family); (c) 10 members (extended); (d) Unlimited with admin approval
- Q: If a member is removed from a household wallet, do they get a proportional share of the pool back as a standalone account, or do their points stay in the household? | Why: Drives the removal flow, balance-sheet treatment, and customer communication | Options: (a) Pro-rata share returned (their lifetime_earned / household.lifetime_earned × current balance); (b) Points stay with household, removed member starts fresh at zero; (c) Fixed amount returned (e.g., last 6 months' earned by that member)
- Q: Should household tier apply at checkout even if the purchasing member is shopping alone (without the anchor present)? | Why: If yes, every family member gets the household discount benefit at any store independently — this directly affects margin | Options: (a) Yes — tier follows the household_id, applies to any member at checkout regardless of who is present; (b) No — tier applies only when the anchor customer is the purchaser; (c) Configurable per store
- Q: Should corporate discount allowances (B2B credit limits / staff discounts) also pool at the household level, or is this strictly a loyalty-points-only wallet? | Why: Pooling credit limits is a significantly larger accounting and fraud risk and would change the scope from M to XL | Options: (a) Loyalty points only (recommended — keeps scope M); (b) Loyalty points + store credit pooling; (c) Loyalty + store credit + shared credit limit (khata)
# Feature #44: Duplicate Profile "Merge" Utility (Access Controlled)
META: effort=L days=6 risk=HIGH roi=4 quickwin=no deps=none phase=3

## Existing overlap
- `backend/api/routers/customers.py` — full customer CRUD, `find_by_mobile()` dedup by phone, `patients[]` embedded family members, `store_credit` atomic ledger, `loyalty_points` field, consent ledger
- `backend/api/routers/loyalty.py` — `loyalty_accounts` + `loyalty_transactions` ledger; `reverse_for_return()` shows idempotency pattern; `try_debit()` atomic guard
- `backend/api/routers/prescriptions.py` — `find_by_patient_id()`, family grouping via `patient_id`; `prescriptions` collection carries `customer_id`
- `backend/api/routers/orders.py` — orders carry `customer_id`; returns/credit notes carry `customer_id`
- `backend/api/routers/follow_ups.py` — `follow_ups.customer_id` FK
- `backend/api/routers/walkouts.py` — `walkouts.customer_id` FK
- `backend/database/repositories/customer_repository.py` — `search_customers()` queries name/mobile/email/patients; dedup logic only on mobile today
- `backend/api/services/rbac_policy.py` + `backend/api/middleware/rbac_enforcement.py` — 12-role RBAC, store-scoped gates
- `backend/agents/proposals.py` — reversible change-proposal workflow (before/after state capture, approval chain)
- `audit_logs` collection — immutable before/after writes; used throughout
- `credit_note_ledger` — append-only; `try_debit_store_credit()` atomic guard (customers.py:110-152)
- `dpdp_consent_ledger` — consent audit trail per customer

No existing merge endpoint or merge-candidate detection exists. Greenfield on the merge engine itself.

## Reuse (extend, don't rebuild)
- `backend/api/routers/customers.py` — add `/customers/merge-candidates` (search) and `/customers/merge` (execute) endpoints here; reuse `search_customers()` for candidate discovery
- `backend/database/repositories/customer_repository.py` — add `find_merge_candidates(query)` (returns pairs ranked by name/phone similarity score) and `apply_merge(winner_id, loser_id, field_choices)` atomic writer
- `backend/agents/proposals.py` — merge is a high-stakes, non-reversible Tier-2 action: log a proposal doc (type=`customer_merge`) with before_state snapshot of both profiles; approval stamps `approved_by`/`approved_at` before execution proceeds
- `audit_logs` collection — write one immutable merge audit row capturing winner_id, loser_id, every FK collection touched, approver, timestamp
- `credit_note_ledger` — issue a single ADJUST entry on winner transferring loser balance (reuse existing ADJUST entry_type)
- `loyalty_accounts` + `loyalty_transactions` — reuse `adjust_balance()` (loyalty_repository.py) to transfer points; mint one ADJUST transaction on winner and one ADJUST (negative) on loser before tombstoning loser account
- `notifications.py` bell — notify approver and requester on merge completion
- `frontend/src/pages/customers/Customer360Dashboard.tsx` — surface "Possible Duplicate" badge and merge-trigger button from here for authorised roles

## Data model
New fields on `customers` collection:
```
merged_into: str | null          # set on the LOSER doc; points to winner customer_id
merged_at: datetime | null
merged_by: str | null            # user_id who executed
merge_absorbed: list[str]        # on WINNER: list of loser customer_ids absorbed (audit trail)
is_tombstoned: bool              # True on loser after merge; filtered from all normal queries
```

New collection `customer_merge_log` (append-only, one doc per merge event):
```
merge_id: str
winner_id: str
loser_id: str
winner_snapshot: dict            # full customer doc before merge
loser_snapshot: dict             # full customer doc before merge
field_choices: dict              # which fields came from which source (e.g. email: "winner")
fk_collections_updated: list[str]  # ["orders","prescriptions","follow_ups",...]
loyalty_points_transferred: int
store_credit_transferred: float
proposed_by: str
approved_by: str
approved_at: datetime
executed_at: datetime
```

No new FK collections needed — all relational tables (orders, prescriptions, returns, follow_ups, walkouts, notification_logs, loyalty_transactions, credit_note_ledger, dpdp_consent_ledger) already carry `customer_id` and support a bulk `updateMany` repoint.

## Backend

`GET /api/v1/customers/merge-candidates?q=<name|phone|email>&limit=20`
- Roles: SUPERADMIN, ADMIN, STORE_MANAGER, ACCOUNTANT
- Runs `search_customers()` then groups results by fuzzy-phone or fuzzy-name similarity; returns ranked pairs with a `similarity_score` (Levenshtein on name + exact-match on normalised mobile)
- Never returns tombstoned profiles

`POST /api/v1/customers/merge/preview`
- Body: `{ winner_id, loser_id }`
- Returns diff: conflicting fields (name, email, DOB, GSTIN, address), open orders on loser, prescription count, loyalty balance to transfer, store credit to transfer, consent purposes union
- No writes; read-only pre-flight

`POST /api/v1/customers/merge/propose`
- Body: `{ winner_id, loser_id, field_choices: { name: "winner"|"loser", email: "winner"|"loser", ... } }`
- Creates `ai_proposals` doc (type=`customer_merge`, status=PENDING) with full before-state snapshot of both profiles
- Returns `proposal_id`; triggers in-app bell notification to ADMIN/SUPERADMIN approvers

`POST /api/v1/customers/merge/approve/{proposal_id}`
- Roles: SUPERADMIN, ADMIN only (Store Manager can propose, cannot self-approve)
- Validates proposal still PENDING; stamps approved_by/approved_at
- Executes merge atomically (see execution sequence below)
- Returns merge_id

`POST /api/v1/customers/merge/reject/{proposal_id}`
- Roles: SUPERADMIN, ADMIN
- Stamps status=REJECTED; notifies proposer

`GET /api/v1/customers/{id}/merge-history`
- Returns `customer_merge_log` docs where winner_id or loser_id = id
- Roles: SUPERADMIN, ADMIN, STORE_MANAGER, ACCOUNTANT

**Execution sequence inside `approve` (all inside a logical transaction using ordered writes with rollback-intent logging):**
1. Snapshot both customer docs → `customer_merge_log.winner_snapshot` / `loser_snapshot`
2. Apply `field_choices` to winner doc; union `patients[]` (deduplicate by name+DOB)
3. Union consent purposes from `dpdp_consent_ledger` (grant winner any purpose loser had)
4. Bulk `updateMany` on: `orders`, `returns`, `credit_note_ledger`, `prescriptions`, `follow_ups`, `walkouts`, `notification_logs`, `loyalty_transactions`, `shipments`, `workshop_jobs` — repoint `customer_id` from loser → winner
5. Transfer loyalty points: `adjust_balance(winner, +loser_balance, reason="merge:{merge_id}")` then tombstone loser loyalty_account
6. Transfer store credit: ADJUST entry on winner credit_note_ledger; zero loser field
7. Tombstone loser: set `merged_into=winner_id`, `merged_at`, `merged_by`, `is_tombstoned=True`
8. Stamp winner `merge_absorbed += [loser_id]`
9. Write `customer_merge_log` doc
10. Write `audit_logs` immutable row (action=`customer.merge`, entity_id=winner_id, before/after)
11. Notify requester via in-app bell

All FK repoints use `updateMany` (not find-and-update-one-by-one) to minimise window. If any step after step 4 fails, the `customer_merge_log` doc records `fk_collections_updated` as partial, enabling a manual-recovery query. No automatic rollback (Mongo has no cross-collection ACID); recovery procedure documented in the log doc.

## Frontend

`frontend/src/pages/customers/MergeCandidatesPage.tsx` (new, linked from Customers nav)
- Search bar → calls `/merge-candidates`; shows ranked pair cards (similarity score, shared phone/email indicator)
- Each pair has "Review & Propose Merge" button

`frontend/src/components/customers/MergeProposalModal.tsx` (new)
- Side-by-side diff view of both profiles (conflicting fields highlighted)
- Radio selectors for each conflicting field (keep winner / keep loser)
- Summary panel: "X orders, Y Rx, Z loyalty pts, ₹N store credit will move to master"
- "Open balance / layaway" warning banner if loser has unpaid orders
- Submit → calls `/merge/propose`; shows "Sent for approval" state

`frontend/src/pages/customers/MergeApprovalsPage.tsx` (new, ADMIN/SUPERADMIN only)
- List of PENDING merge proposals (proposer, stores, timestamp)
- Expand → shows same side-by-side diff + field choices the proposer selected
- Approve / Reject buttons with optional rejection note
- Post-approval: shows merge_id and link to merged profile

`frontend/src/pages/customers/Customer360Dashboard.tsx` (extend existing)
- If `customer.merge_absorbed` is non-empty: show "This profile absorbed N duplicate(s)" chip with link to merge history
- If `customer.merged_into` is set (tombstoned): redirect automatically to winner profile with a banner "This profile was merged into [winner name] on [date]"

`frontend/src/pages/customers/CustomerList.tsx` (extend existing)
- Filter out `is_tombstoned=True` in default query
- Add "Show merged/tombstoned" toggle for ADMIN/SUPERADMIN

All UI follows restrained/executive design: neutral background, single accent colour for the "Merge" action CTA, red only for the irreversibility warning banner.

## Business rules
- **Irreversible hard-lock**: once executed, merge cannot be undone via the UI. The loser doc is tombstoned (not deleted), so data is recoverable by engineering only. UI must show an explicit "This action cannot be undone" confirmation modal with the word "MERGE" typed to confirm.
- **Self-approval blocked**: the user who proposed cannot approve the same proposal (enforced by comparing `proposed_by` vs `approved_by` JWT sub).
- **Open-order warning**: if loser has orders with `payment_status` IN (UNPAID, PARTIAL) or `status` IN (PENDING, IN_PROGRESS, PROCESSING), the preview flags these. Merge is not blocked but the approver must explicitly acknowledge by checking a "I confirm open balances are accounted for" checkbox.
- **B2B GSTIN guard**: if both profiles have a non-null GSTIN and they differ, merge is blocked hard (two different legal entities cannot collapse into one GST ledger). UI shows "Cannot merge: conflicting GSTIN" and surfaces both GSTINs for manual review.
- **Loyalty cap**: transferred points cannot cause winner balance to exceed the store's `loyalty_settings.max_balance` (if configured). Points beyond cap are zeroed and noted in merge log.
- **Consent union**: winner inherits the most permissive consent (if loser had MARKETING consent but winner withdrew it, the merge does NOT re-grant; always favour the more restrictive consent to respect DPDP). Exception: SERVICE_DELIVERY always retained.
- **Tombstoned profiles invisible**: all customer list/search endpoints must filter `is_tombstoned != True` by default; only ADMIN/SUPERADMIN can see them with explicit flag.
- **No merge of already-merged loser**: if `loser.merged_into` is already set, block with 409.
- **Audit immutability**: `customer_merge_log` and `audit_logs` entries are insert-only, never updated.

## RBAC
| Role | Can search candidates | Can propose | Can approve | Can view merge history |
|---|---|---|---|---|
| SUPERADMIN | Yes | Yes | Yes | Yes |
| ADMIN | Yes | Yes | Yes | Yes |
| AREA_MANAGER | Yes | No | No | Yes (own stores) |
| STORE_MANAGER | Yes | Yes | No | Yes (own store) |
| ACCOUNTANT | Yes | No | No | Yes (own store) |
| All others | No | No | No | No |

Store Managers can propose but not self-approve — proposal goes to ADMIN/SUPERADMIN queue.

## Integrations
- **Jarvis / ORACLE agent**: ORACLE's `_detect_discount_abuse()` and churn segmentation use `customer_id`; tombstoned profiles are filtered from those queries automatically once `is_tombstoned=True` filter is added to `search_customers()`. No agent code changes needed.
- **Shopify**: if loser has `shopify_customer_id` and winner does not, copy field to winner doc. If both have different Shopify customer IDs, flag in merge log (two Shopify accounts; Shopify merge is out of scope and must be done manually in Shopify admin — surface this as a post-merge action item in the UI).
- **Tally**: orders re-pointed to winner_id will appear under winner in any future Tally export. Historical Tally exports already generated are not retroactively changed (Tally JV XML is immutable once exported). No Tally API call needed.
- **MSG91 / WhatsApp**: `notification_logs` repointed to winner_id; future MEGAPHONE sends use winner's phone. No MSG91 action needed.
- **Loyalty engine**: reuse `adjust_balance()` directly — no new provider integration.

## Risk notes
- **No cross-collection Mongo ACID**: the FK repoint across 10+ collections is sequential `updateMany` calls. If the process crashes mid-way, partial repoints exist. Mitigated by: (a) writing `customer_merge_log` with `fk_collections_updated` list incrementally so partial state is visible; (b) loser doc is only tombstoned as the final step — until then, both profiles are "live" and the partial state is recoverable.
- **POS / revenue risk**: if loser has an open POS session or in-flight order being processed concurrently, repointing `customer_id` mid-transaction could cause order to save with old customer_id. Mitigate with the open-order warning in preview and a merge-time check that loser has no orders in status DRAFT or PROCESSING in the last 30 minutes.
- **Loyalty double-spend window**: between reading loser's loyalty balance and executing `adjust_balance`, a concurrent POS redemption could drain the loser account. Use `try_debit(loser, loser_balance)` to atomically claim the full balance before transferring; if it fails (balance already changed), re-read and retry once.
- **Feature flag**: wrap entire merge execution path behind `FEATURE_CUSTOMER_MERGE=1` env var (default off). The search/preview/propose endpoints can be live immediately; approve/execute is gated by the flag. Allows QA without risk.
- **Data volume**: stores with 50k+ customers doing a broad search could be slow. Index `customers` on `(mobile, is_tombstoned)` and `(name, is_tombstoned)` before launch.

## Recommendation
Build later — not a quick win. Core retail operations (POS, Rx, orders) are unaffected by duplicate profiles today. Duplicates cause loyalty fragmentation and reporting noise but not revenue loss. Build after CRM segmentation and loyalty expiry (features that directly depend on clean customer data) are in production and the duplicate problem becomes quantifiably painful. Estimated 6 days of careful backend work plus regression testing across 10 FK collections.

## Owner decisions
- Q: Should Store Managers be able to propose merges, or only Admin/Superadmin? | Why: If Store Managers can propose, every store will independently queue merges that Admins must approve — could create approval backlog. If restricted to Admin+, fewer proposals but slower resolution. | Options: a) Store Manager can propose (current design) / b) Only Admin/Superadmin can propose and approve
- Q: What happens to a tombstoned customer's open credit/layaway if it is significant (e.g., ₹50,000 store credit on loser)? Should there be a minimum-balance threshold below which merge is auto-cleared vs above which it requires a second approval from Accountant? | Why: Determines whether to add a second-level financial sign-off step to the approval workflow | Options: a) All merges require single Admin approval regardless of balance / b) Merges where transferred store_credit + loyalty_rupee_value > ₹X require Accountant co-approval / c) Show warning only, single approval always sufficient
- Q: When both profiles have a Shopify customer ID (two different Shopify accounts), should IMS automatically trigger a Shopify customer merge via Admin API, or leave it as a manual action for the owner to do in Shopify? | Why: Shopify Admin API supports customer merge but it is a destructive API call — if done automatically it could affect Shopify order history. If manual, the IMS merge completes but Shopify stays split until owner acts. | Options: a) Never touch Shopify automatically — show a post-merge checklist item / b) Auto-trigger Shopify merge behind DISPATCH_MODE=live gate / c) Offer a one-click "Sync to Shopify" button on the post-merge confirmation screen
- Q: How long should tombstoned (merged-loser) customer records be retained before hard deletion? | Why: Determines storage cost and whether a "permanently delete loser" cleanup job is needed. DPDP right-to-erasure may require deletion on customer request. | Options: a) Retain indefinitely (safest for audit) / b) Hard-delete loser after 2 years / c) Delete only on explicit DPDP erasure request
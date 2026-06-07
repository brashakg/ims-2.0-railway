# Feature #17: Store Petty Cash Controls & Expense Ledger
META: effort=M days=4 risk=MED roi=4 quickwin=no deps=none phase=3

## Existing overlap
IMS already has a **complete expense workflow** in `backend/api/routers/expenses.py` (1,397 lines) covering PENDING → APPROVED → SENT_TO_ACCOUNTANT → ENTERED lifecycle, per-role/category spend caps (`expense_category_caps` collection), SHA-256 duplicate-bill detection, advance linkage, and an aging report. The `frontend/src/pages/finance/ExpenseTracker.tsx` renders the full approval queue and accountant ledger-entry queue.

The feature as described is **not greenfield** — it is a targeted extension of the existing expense module with three additions:
1. A **petty-cash float** per store (opening balance, running balance, top-up workflow)
2. **Receipt photo capture** wired into the expense create flow (photo → bill_sha256 already computed, storage hook needed)
3. A **store-level float dashboard** surfacing float balance alongside expense history

The approval-routing logic (expenses over a threshold → Admin/Superadmin) already exists via `expense_category_caps` and the APPROVED gating in `expenses.py:614-730`. Period-lock guard (`check_period_locked`) is already called on expense approval (finance.py:446-481).

## Reuse (extend, don't rebuild)
- `backend/api/routers/expenses.py` — extend `ExpenseCreate` schema with `receipt_image_id` (GridFS file ref); extend `create_expense` to debit petty-cash float atomically; extend `approve_expense` to enforce the new per-store float threshold
- `expenses` collection — add `receipt_image_id` field; no schema migration needed (additive)
- `expense_category_caps` collection — already stores role/category daily/monthly caps; extend the doc shape with a `float_threshold` field (expenses above this amount auto-route to Admin approval regardless of category)
- `backend/api/services/ap_engine.py` — reuse `compute_due_date` pattern; no changes needed
- `backend/api/routers/finance.py` — reuse `check_period_locked` (already imported in expenses.py); add float balance to the cash-flow endpoint response
- `frontend/src/pages/finance/ExpenseTracker.tsx` — extend with a "Store Float" header card (current balance, low-float warning) and a camera/file-upload button on the new-expense form
- `audit_logs` collection — already written on expense state changes; no new collection needed for the audit trail

## Data model
**New collection: `petty_cash_floats`** (one doc per store)
```
{
  store_id: str,            // unique index
  opening_balance: Decimal, // set at store setup or top-up
  current_balance: Decimal, // atomically debited on each approved expense
  last_topup_at: datetime,
  last_topup_by: str,       // user_id
  low_balance_threshold: Decimal,  // owner-set; triggers in-app bell to Store Manager + Admin
  updated_at: datetime
}
```

**New collection: `petty_cash_topups`** (append-only ledger)
```
{
  topup_id: str,
  store_id: str,
  amount: Decimal,
  note: str,
  approved_by: str,         // Admin/Superadmin user_id
  created_by: str,
  created_at: datetime
}
```

**Extension to `expenses` collection** (additive fields, no migration):
```
receipt_image_id: Optional[str]   // GridFS file_id for the photographed receipt
float_debited: bool               // True when approved expense was charged to the float
```

## Backend

- `POST /api/v1/expenses/{expense_id}/receipt` — Upload receipt image (≤10 MB JPEG/PNG/PDF); stores to GridFS; stamps `receipt_image_id` on the expense doc. Roles: STORE_MANAGER, SALES_CASHIER, SALES_STAFF (own store only).
- `GET /api/v1/expenses/{expense_id}/receipt` — Stream receipt image from GridFS. Roles: STORE_MANAGER, AREA_MANAGER, ADMIN, SUPERADMIN, ACCOUNTANT.
- `GET /api/v1/petty-cash/float/{store_id}` — Returns current balance, opening_balance, last_topup_at, low_balance_threshold, and last 30 expense rows that debited the float. Roles: STORE_MANAGER (own store), AREA_MANAGER, ADMIN, SUPERADMIN, ACCOUNTANT.
- `POST /api/v1/petty-cash/float/{store_id}/topup` — Admin/Superadmin adds cash to the float (atomic `$inc` on current_balance, appends to `petty_cash_topups`). Writes `audit_logs` entry with before/after balance. Roles: ADMIN, SUPERADMIN.
- `PUT /api/v1/petty-cash/float/{store_id}/settings` — Set `low_balance_threshold` and `opening_balance` (one-time or reset). Roles: ADMIN, SUPERADMIN.
- **Extend `POST /api/v1/expenses` (existing)** — After expense is created with `receipt_image_id`, if `amount > float_threshold` (from `expense_category_caps.float_threshold`), force status to PENDING and set `requires_admin_approval=True`; otherwise standard STORE_MANAGER approval path is unchanged.
- **Extend `POST /api/v1/expenses/{expense_id}/approve` (existing)** — On approval, if expense `float_debited=False` and store has a float, perform atomic `find_one_and_update` on `petty_cash_floats` with `$inc: {current_balance: -amount}` guarded by `current_balance >= amount`; stamp `float_debited=True`. If float would go negative, return 422 with "Insufficient float balance — top up required." If float balance after debit ≤ `low_balance_threshold`, dispatch in-app notification to STORE_MANAGER + ADMIN.

## Frontend

- **`ExpenseTracker.tsx` — extend** with a "Store Float" summary card at the top of the store manager view: shows current balance as a large figure, a coloured low-balance badge (amber when ≤ threshold, red when ≤ 20% of threshold), date of last top-up, and a "Request Top-up" button (sends an in-app notification to Admin; does not auto-top-up).
- **New expense form — receipt capture**: Add a camera icon / file picker below the "Amount" field. On mobile/tablet it opens the camera; on desktop it opens a file picker. Preview thumbnail shown inline. Upload fires `POST /expenses/{id}/receipt` immediately after expense is saved (two-step: create → upload). No receipt required for amounts below ₹200 (owner-configurable threshold — see Owner decisions).
- **`PettyCashPage.tsx` (new, under `/finance/petty-cash`)** — ADMIN/SUPERADMIN/ACCOUNTANT view only. Shows per-store float status table (store name, current balance, low-balance indicator, last top-up), a top-up form (store selector, amount, note), and a top-up history ledger. Restrained neutral table layout matching the Finance dashboard style — no colour beyond semantic amber/red for low-balance.
- **Receipt viewer modal** — clicking the thumbnail on any expense row opens a full-screen modal with the image or PDF embed. Accessible (keyboard-dismissible, focus-trapped).

## Business rules
- Float debit is **atomic** (`find_one_and_update` with balance guard) — no read-modify-write; concurrent approvals cannot overdraft.
- Expenses above the `float_threshold` **must** go to Admin/Superadmin approval regardless of category cap — this overrides the standard STORE_MANAGER approval path.
- A store float balance **cannot go negative** — approval is blocked with a 422 until the float is topped up.
- Receipt images are **required** for expenses above ₹500 (owner-configurable; hard-enforced at the backend create endpoint).
- Duplicate-bill detection (SHA-256 on receipt image bytes) is already implemented in `expenses.py:75-103` — it fires on image upload and soft-flags the expense (not a hard block, consistent with existing behaviour).
- Period-lock guard already in `expenses.py` — no change needed; float debit respects the same lock.
- Top-ups are append-only (`petty_cash_topups`) and write an `audit_logs` entry with before/after balance — immutable audit trail.
- Low-balance alert fires in-app (bell notification) at approval time when post-debit balance ≤ `low_balance_threshold`; does not block the approval.
- All expense and float operations are **store-scoped** via `validate_store_access` — a Store Manager sees only their own store's float.

## RBAC

| Role | Float view | Expense submit | Expense approve | Top-up | Settings (threshold/opening) |
|---|---|---|---|---|---|
| SUPERADMIN | All stores | — | All | Yes | Yes |
| ADMIN | All stores | — | All | Yes | Yes |
| AREA_MANAGER | Managed stores | — | Managed stores | No | No |
| STORE_MANAGER | Own store | Yes | Own store (below threshold) | No (can request) | No |
| ACCOUNTANT | All stores (read) | — | No | No | No |
| SALES_CASHIER / SALES_STAFF | No | Yes (own store) | No | No | No |
| All others | No | No | No | No | No |

## Integrations
- **GridFS (MongoDB)** — receipt image storage; already used by `handoffs.py` for file uploads; reuse the same GridFS bucket (`fs`) and `gridfs_upload` helper.
- **In-app notifications** (`notifications` collection) — low-balance alert and top-up request use the existing bell notification path (`notifications.py`).
- **Tally JV** — top-ups should appear as a debit to "Petty Cash A/c" and credit to "Cash/Bank A/c" in the nightly Tally export. Extend `nexus_providers.py` `_build_tally_export` to include `petty_cash_topups` as a separate voucher type (RECEIPT). This is a Tally config question for the accountant, not a build-blocking requirement for Phase 1.
- **MSG91 / Shopify / Razorpay** — not involved.
- **MEGAPHONE** — not needed; low-balance alert is in-app only (not WhatsApp, to avoid marketing-DND collision).

## Risk notes
- **Money integrity**: The atomic float debit (guarded `find_one_and_update`) mirrors the `redeem_voucher_atomic` pattern — the highest-risk part is already solved by the pattern. The main risk is ensuring the guard field (`current_balance >= amount`) uses Decimal128 consistently (not float) to avoid paisa rounding errors. Use `Decimal128` on the MongoDB side and Python `Decimal` on the service side — same pattern as the voucher engine.
- **Receipt storage size**: GridFS is fine for receipts at ≤ 10 MB each. If the store generates 50 receipts/month across 6 stores, storage growth is negligible. No CDN needed.
- **No POS touch**: This feature touches `expenses.py` and a new `petty_cash_floats` collection — zero overlap with `orders.py` or `posStore.ts`. No feature flag needed for the expense changes (they are additive). The float-debit at approval time is new logic but isolated.
- **Accountant workflow**: The ENTERED state (accountant ledger sign-off) already exists. The float balance should be reconciled by the accountant at month-end — this is a process question, not a system gap. The `petty_cash_topups` ledger gives the accountant the full trail.

## Recommendation
**Build now (Phase 3)** — the expense workflow is fully built; this is a targeted extension (new float collection + receipt upload + one atomic debit call + one new page). The "shoebox of receipts" and float-leakage pain is real and high-frequency for store managers. Estimated 4 days: Day 1 data model + backend endpoints, Day 2 receipt upload + float debit logic, Day 3 frontend float card + PettyCashPage, Day 4 Tally voucher extension + testing.

## Owner decisions
- Q: What is the per-expense amount above which Admin/Superadmin approval is required (the `float_threshold`)? | Why: This is the single most important business rule — it determines how much autonomy Store Managers have vs. how many approvals land in your inbox daily. | Options: ₹500 (tight control, more approvals) / ₹1,000 (balanced) / ₹2,000 (trust managers more, fewer interruptions)
- Q: What is the minimum receipt-required amount (below which a photo is optional)? | Why: Requiring a photo for every ₹20 tea purchase creates friction; setting it too high defeats the audit goal. | Options: ₹100 / ₹200 / ₹500
- Q: What should the opening petty-cash float be per store, and how often do you expect to top it up? | Why: This sets the `opening_balance` and determines the `low_balance_threshold` — if the float is ₹5,000 and you top up weekly, a ₹1,000 low-balance alert makes sense; if monthly, you'd want ₹2,000. | Options: Owner to specify per store (Better Vision vs. WizOpt may differ)
- Q: Should top-ups appear in the Tally export as a separate RECEIPT voucher (Petty Cash A/c Dr, Cash/Bank A/c Cr), or should they be handled manually by your accountant outside Tally? | Why: Automated Tally vouchers for top-ups require knowing which Tally ledger name maps to "Petty Cash" for each entity — that mapping must come from you or your CA. | Options: Auto-export to Tally (need ledger names) / manual (accountant posts in Tally separately)
- Q: Should Store Managers be able to see each other's float balances within the same brand (e.g., all Better Vision stores visible to Better Vision's area manager)? | Why: This determines whether `validate_store_access` for Area Managers is scoped to their assigned stores or brand-wide. | Options: Scoped to assigned stores only (default, safer) / brand-wide visibility for area managers
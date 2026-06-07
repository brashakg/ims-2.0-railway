# Feature #27: Configurable Refund Approval Matrix & Original Tender Enforcement
META: effort=M days=5 risk=HIGH roi=4 quickwin=no deps=none phase=2

## Existing overlap
IMS already has substantial relevant infrastructure:

- **Returns router** (`backend/api/routers/returns.py:928+`): Full return lifecycle with `create_return()`, monetary cap guard (lines 1099-1122), original tender enforcement via `_order_payment_method()` (lines 552-566), qty cap + atomic claim (lines 302-348), loyalty reversal (lines 1191-1220). The tender enforcement already defaults to the original payment method — this feature hardens and configures it.
- **Vouchers atomic redeem** (`backend/api/routers/vouchers.py:170`): Pattern for atomic `find_one_and_update` — already used for loyalty/store-credit; extend same guard for refund approval tokens.
- **Orders approval pattern** (`backend/api/routers/orders.py:1195-1312`): Discount approval with `discount_approved_by` + `discount_reason` fields — same pattern, same DB approach for refund approvals.
- **Discount cap enforcement** (`backend/api/services/role_caps.py`, `pricing_caps.py`): Role-aware cap resolution already in use; refund cap matrix mirrors the same design.
- **RBAC policy** (`backend/api/services/rbac_policy.py`): All 12 roles catalogued; `check_access()` and `require_roles()` helpers reusable.
- **Settings singletons** (`backend/api/routers/settings.py`): `business_settings`, `tax_settings`, `invoice_settings` pattern — add `refund_settings` singleton with same upsert pattern.
- **Audit logs** (`audit_logs` collection): before/after state, user_id, timestamp already captured on returns (returns.py writes on completion); extend with approval chain.
- **AI Proposals tier-2** (`backend/agents/proposals.py`): `refund_issue` is already in `requires_confirmation` (non-reversible tier-2); this feature formalises that path.
- **In-app notifications** (`backend/api/routers/notifications.py`): Bell + WhatsApp dispatch infrastructure ready for approval-request alerts.

## Reuse (extend, don't rebuild)
- `backend/api/routers/returns.py` — extend `ReturnCreate` schema + `create_return()` to check the refund matrix before processing; inject approval gate into existing flow
- `backend/api/routers/settings.py` — add `refund_settings` GET/PUT endpoints (same singleton upsert pattern as `invoice_settings`)
- `backend/api/services/role_caps.py` — add `effective_refund_cap(user_role, store_id)` alongside existing `effective_discount_cap()`
- `backend/agents/proposals.py` — wire `refund_issue` tier-2 to actually create an approval-request doc (currently just records status, no executor)
- `audit_logs` collection — already captures returns; extend with `approval_chain` array field on the return doc itself
- `backend/api/routers/notifications.py` — reuse `create_notification()` to ping approver in-app + MSG91 WhatsApp for high-value refund approval requests
- `frontend/src/pages/orders/ReturnsPage.tsx` — extend with approval status badge, PIN entry modal, blocked-refund warning
- `frontend/src/pages/settings/` — add Refund Policy tab (same pattern as existing Tax Settings / Invoice Settings tabs)

## Data model
New fields on existing `returns` collection docs (no new collection needed for the return itself):
- `approval_required: bool` — computed at create time based on matrix
- `approval_status: "PENDING" | "APPROVED" | "REJECTED" | "AUTO_APPROVED"` — default AUTO_APPROVED when below threshold
- `approval_requested_from: user_id` — the approver role targeted
- `approval_requested_at: datetime`
- `approved_by: user_id`
- `approved_by_name: str`
- `approval_pin_hash: str` — bcrypt hash of the PIN used (never store plaintext)
- `approval_granted_at: datetime`
- `approval_channel: "PIN" | "IN_APP" | "WHATSAPP_OTP"`
- `tender_override_attempted: bool` — flag if cashier tried to change refund method
- `tender_override_blocked: bool` — flag confirming it was blocked

New `refund_settings` singleton collection (one doc, `_id: "default"`):
```
{
  "_id": "default",
  "auto_approve_below_inr": 500,          // owner sets; below this → no approval needed
  "require_admin_above_inr": 2000,        // owner sets; above this → ADMIN/AREA_MANAGER approval
  "require_superadmin_above_inr": 10000,  // owner sets; above this → only SUPERADMIN can approve
  "pin_required_above_inr": 2000,         // owner sets; approval via PIN (not just in-app click)
  "original_tender_enforce": true,        // hardlock: card → card, UPI → UPI, etc.
  "cash_refund_allowed_for_cash_sales": true,  // allow cash refund only when paid in cash
  "card_refund_max_days": 30,            // owner sets: card reversal window
  "store_overrides": {                   // optional per-store policy (future)
    "<store_id>": { "auto_approve_below_inr": 300 }
  },
  "updated_by": "user_id",
  "updated_at": "datetime"
}
```

New `refund_approval_requests` collection (lightweight, for pending approvals):
```
{
  "request_id": "uuid",
  "return_id": "uuid",
  "store_id": "str",
  "refund_amount": "Decimal",
  "original_tender": "CASH|CARD|UPI|BANK_TRANSFER|...",
  "requested_by": "user_id",
  "requested_by_name": "str",
  "required_approver_roles": ["ADMIN", "AREA_MANAGER"],
  "status": "PENDING|APPROVED|REJECTED|EXPIRED",
  "expires_at": "datetime",     // TTL: 4 hours; auto-expires stale requests
  "approved_by": "user_id",
  "approval_pin_hash": "str",
  "created_at": "datetime"
}
```
TTL index on `expires_at` so stale approval requests auto-clear.

## Backend

**New/changed endpoints:**

- `GET /api/v1/settings/refund` — fetch current refund policy matrix (ADMIN/SUPERADMIN only)
- `PUT /api/v1/settings/refund` — update policy matrix (SUPERADMIN only); validates thresholds are ordered (`auto < admin < superadmin`); writes audit log
- `POST /api/v1/returns` — **extend existing** `create_return()`: before processing, call `_check_refund_gate(return_request, settings)` which:
  1. Enforces original tender: if `original_tender_enforce=true` and requested `refund_method ≠ original payment method` → 422 with `TENDER_MISMATCH` error code (cashier cannot override)
  2. Computes required approver role from amount vs thresholds
  3. If amount below `auto_approve_below_inr` → proceeds immediately, stamps `approval_status=AUTO_APPROVED`
  4. If amount above threshold → creates `refund_approval_requests` doc, returns 202 with `{"approval_required": true, "request_id": "..."}` — return is NOT processed yet, status is `PENDING_APPROVAL`
- `POST /api/v1/returns/approval-requests/{request_id}/approve` — approver calls this; validates:
  1. Caller has the required role (ADMIN or SUPERADMIN per matrix)
  2. If `pin_required_above_inr` threshold met: validates PIN against `approval_pin_hash` (bcrypt verify); PIN set in `refund_settings.approver_pin_hash` by SUPERADMIN
  3. Updates `refund_approval_requests.status = APPROVED`, stamps approver fields
  4. Triggers the actual return processing (calls existing `_execute_return()` internal helper)
  5. Writes `audit_logs` entry with before/after + approval chain
  6. Sends in-app notification to cashier that refund is approved and processed
- `POST /api/v1/returns/approval-requests/{request_id}/reject` — approver rejects; stamps reason; cashier notified in-app; return stays unprocessed
- `GET /api/v1/returns/approval-requests/pending` — ADMIN/AREA_MANAGER/SUPERADMIN sees their queue of pending high-value refund requests (store-scoped)
- `PUT /api/v1/settings/refund/pin` — SUPERADMIN sets/changes approver PIN (bcrypt hashed at rest, never returned in GET, masked in logs)

**Internal helper additions to `returns.py`:**
- `_check_refund_gate(return_data, refund_settings) → GateResult` — pure function, no DB side effects, returns `{auto_approved, required_roles, block_reason}`
- `_execute_return(return_id)` — extract the actual DB-writing logic from `create_return()` so it can be called from both the immediate path and the approval callback

## Frontend

- **`frontend/src/pages/settings/RefundPolicyPage.tsx`** (new, extend Settings nav): Restrained table UI showing the three threshold tiers (auto / admin / superadmin) as editable INR fields; toggle for original-tender enforcement; card-reversal window days; PIN set/change modal (SUPERADMIN only, shows only bullet-masked confirmation). No colour except semantic red for "blocked" state.
- **`frontend/src/pages/orders/ReturnsPage.tsx`** (extend): When API returns 202 + `approval_required=true`, show a neutral status chip "Awaiting Approval" with the request ID. Cashier sees a non-dismissable info banner: "This refund requires manager approval. The manager has been notified." — no further action for cashier.
- **`frontend/src/components/returns/RefundApprovalModal.tsx`** (new): Manager/Admin opens from their notification bell or from the pending-approvals queue. Shows: customer name, order number, refund amount, original tender, items being returned. If PIN required: PIN entry field (masked, 4-6 digits). Approve / Reject buttons. On approve: POST to approval endpoint; on success, modal closes and return status updates live.
- **`frontend/src/pages/returns/PendingApprovalsPage.tsx`** (new, ADMIN+ only): List of pending refund approval requests across the store. Columns: time pending, cashier name, customer, amount, original tender, action (Approve/Reject). Sortable by amount descending. No pagination — approval queue should be short; show all open items. Badge count on nav item.
- **`frontend/src/pages/orders/ReturnsPage.tsx`** (extend further): If cashier changes refund method to differ from original tender and `original_tender_enforce=true`, show inline red validation text immediately (client-side mirror of server rule): "Refunds must go back to the original [CARD/UPI/CASH] — this cannot be changed." Disable the submit button. Do NOT make this a dismissable warning — it must be a hard block.

## Business rules

**Hardlocks (non-negotiable, enforced server-side — no role can bypass):**
1. `refund_method` must equal `original_payment_method` when `original_tender_enforce=true`; cashier role cannot override; even ADMIN cannot override without explicitly disabling the policy in settings (audit-logged)
2. A return doc with `approval_status=PENDING_APPROVAL` cannot transition to `COMPLETED` or `REFUNDED` without a corresponding `APPROVED` record in `refund_approval_requests`
3. Approver cannot be the same user who initiated the return request (self-approval block — same pattern as expenses router)
4. Approval requests expire after 4 hours; expired requests auto-reject and cashier must re-initiate
5. PIN is bcrypt-hashed at rest; never appears in API responses or logs; wrong PIN = 401 after 3 attempts (lockout for 15 min on the request, not the user account)
6. Refund amount cannot exceed `order.amount_paid` (already enforced in returns.py:1099 — preserve this)
7. All refund events (auto-approved, approval-requested, approved, rejected, expired, tender-mismatch-blocked) write to `audit_logs` with before/after state

**Configurable policies (owner sets in settings, SUPERADMIN only):**
- Three threshold tiers (auto / require-admin / require-superadmin INR values)
- PIN requirement threshold (can be same as admin threshold or higher)
- Card reversal max days window
- Per-store overrides (optional, default: global policy applies to all stores)

## RBAC

| Role | Can initiate return | Can approve (if below admin threshold) | Can approve (admin tier) | Can approve (superadmin tier) | Can view pending queue | Can configure policy | Can set/change PIN |
|---|---|---|---|---|---|---|---|
| SUPERADMIN | Yes | Yes (auto-approved) | Yes | Yes | Yes | Yes | Yes |
| ADMIN | Yes | Yes (auto-approved) | Yes | No | Yes | No | No |
| AREA_MANAGER | Yes | Yes (auto-approved) | Yes | No | Yes (own stores) | No | No |
| STORE_MANAGER | Yes | Yes (auto-approved) | No | No | Yes (own store only) | No | No |
| ACCOUNTANT | View only | No | No | No | Yes (own store only) | No | No |
| SALES_CASHIER | Yes (initiates) | No | No | No | No | No | No |
| SALES_STAFF | Yes (initiates) | No | No | No | No | No | No |
| All others | No | No | No | No | No | No | No |

RBAC policy entries to add in `rbac_policy.py`:
- `GET /settings/refund` → `[ADMIN, SUPERADMIN]`
- `PUT /settings/refund` → `[SUPERADMIN]`
- `PUT /settings/refund/pin` → `[SUPERADMIN]`
- `GET /returns/approval-requests/pending` → `[STORE_MANAGER, AREA_MANAGER, ADMIN, SUPERADMIN]`
- `POST /returns/approval-requests/{id}/approve` → `[AREA_MANAGER, ADMIN, SUPERADMIN]`
- `POST /returns/approval-requests/{id}/reject` → `[AREA_MANAGER, ADMIN, SUPERADMIN]`

## Integrations
- **MSG91 WhatsApp** (MEGAPHONE / `agents/providers.py`): When a high-value refund approval request is created, send WhatsApp alert to the on-duty approver role (ADMIN or AREA_MANAGER for that store) via existing `send_whatsapp()` — message: "Refund approval needed: ₹{amount} for order {order_number} at {store_name}. Open IMS to approve." Gated by `DISPATCH_MODE`. Quiet hours (21:00–09:00 IST) respected — if outside hours, in-app bell only.
- **In-app bell** (`notifications.py`): Always fires regardless of quiet hours — in-app notification to approver with deep-link to approval modal.
- **Tally export** (NEXUS nightly): Approved refunds already flow into Tally JV via existing `build_tally_export()`; no change needed — the return doc completion triggers the same downstream path.
- **Jarvis TASKMASTER**: `refund_issue` is already in `requires_confirmation` (tier-2) in `proposals.py`. Wire TASKMASTER to enqueue an approval-request notification (not auto-execute) when ORACLE flags a suspicious high-value refund pattern (anomaly detection hook, optional phase-2 enhancement).

## Risk notes
- **POS revenue risk (HIGH)**: This sits directly in the return/refund critical path. Any bug that blocks legitimate refunds, or worse, allows unapproved refunds through, is a P0. Build behind a feature flag: `REFUND_GATE_ENABLED=true` env var (default `false` for current Railway deploy); when `false`, existing returns.py flow is unchanged. Flip to `true` only after manual QA of all refund paths on staging.
- **Atomic approval race**: Two managers could simultaneously approve the same request. Guard with `find_one_and_update` on `refund_approval_requests` filtered by `status=PENDING` — same pattern as `vouchers.redeem_voucher_atomic`. Only one approve call wins; second gets 409.
- **PIN brute-force**: 3-attempt lockout per `request_id` (not per user — avoids locking out the manager account). After 3 wrong PINs, the request enters `LOCKED` status; only SUPERADMIN can unlock or the cashier must re-initiate.
- **Card reversal timing**: Some card networks (Visa/MC) have a 30-day reversal window. If `card_refund_max_days` is set and the original sale is older than that window, block card refund and surface a human-readable message: "Original card cannot be refunded after 30 days. Contact your bank or issue store credit." Do not silently redirect to cash.
- **Tender mismatch for split-tender orders**: If original order had split tender (e.g., ₹800 UPI + ₹200 cash), the refund logic must split proportionally back to each tender. The existing `_order_payment_method()` in returns.py reads only `payments[0].method` — this needs extension for multi-tender original orders. Flag this as a known complexity; start with single-tender orders for phase 1.
- **No POS feature flag bypass**: Even SUPERADMIN POS sessions must go through the gate — never exempt any role from the tender enforcement check.

## Recommendation
Build later (phase 2, after POS stabilizes). The returns.py flow is already correct on original-tender defaulting; the missing piece is the approval matrix configuration and PIN enforcement. This is medium effort but high risk due to touching the refund critical path. Run behind `REFUND_GATE_ENABLED` feature flag and validate on staging with real return scenarios before enabling on production.

## Owner decisions
- Q: What INR thresholds should trigger each approval tier? | Why: These are the exact values stored in `refund_settings.auto_approve_below_inr`, `require_admin_above_inr`, `require_superadmin_above_inr` — they directly control when a cashier can refund freely vs. needs a manager. | Options: Example defaults: auto below ₹500 / admin above ₹2,000 / superadmin above ₹10,000 — adjust to match your actual refund patterns and store cash-handling norms.
- Q: Should the approval PIN be a 4-digit or 6-digit number, and should there be one global PIN or a per-approver PIN? | Why: One global PIN means any ADMIN-level manager uses the same code (simpler, but a known PIN can be shared); per-approver PIN ties the approval to a specific person (stronger audit trail, harder to share). | Options: a) One global SUPERADMIN-set PIN for all approvers / b) Each ADMIN/AREA_MANAGER sets their own PIN in their user profile.
- Q: Should a rejected refund close the return permanently, or should the cashier be allowed to re-initiate a new refund request (possibly for a smaller amount)? | Why: If rejection is final, a mistaken rejection by a manager blocks a legitimate customer refund; if re-initiation is allowed, a dishonest cashier could keep retrying. | Options: a) Rejection is final — manager must initiate a new return themselves / b) Cashier can re-initiate once with a note / c) Cashier can re-initiate unlimited times (not recommended).
- Q: For split-tender orders (e.g., part UPI, part cash), should refunds split proportionally back to each original tender, or should the manager choose which tender to refund? | Why: Proportional split is automatic and tamper-proof; manager choice is flexible but opens a bypass path for the very fraud this feature prevents. | Options: a) Always proportional split back to original tenders / b) Manager can choose but it requires SUPERADMIN approval and logs a `tender_override` flag.
- Q: What should happen when a card refund is requested on a sale older than the card-reversal window (e.g., 30 days)? | Why: After 30 days, the card network may reject the reversal; IMS needs to know your fallback policy. | Options: a) Block the refund entirely and instruct staff to contact the bank / b) Automatically convert to store credit with customer consent / c) Allow cash refund with SUPERADMIN approval only.
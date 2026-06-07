# Feature #26: Remote Approval Matrix (Discounts & Paid Leave)
META: effort=M days=6 risk=MED roi=4 quickwin=no deps=none phase=3

## Existing overlap
IMS already has substantial infrastructure this feature builds on directly:

**Discount enforcement (partially built):**
- `backend/api/routers/orders.py:1195-1312` — server-side discount cap enforcement per role + category + luxury brand. When a discount exceeds the cap today the order is rejected with 403. The feature replaces that hard-reject with a "halt and request approval" flow.
- `backend/api/services/role_caps.py` — `effective_discount_cap()` returns the cap per role. The threshold where approval is required will sit just below this.
- `frontend/src/stores/posStore.ts:146-149` — cart-level discount fields (`cart_discount_approved_by`, `cart_discount_reason`) are already in the Zustand store and persisted to the order doc. The approval PIN just needs to populate `cart_discount_approved_by`.

**Approval proposals (partially built):**
- `backend/agents/proposals.py` — `ai_proposals` collection with PENDING → APPROVED → EXECUTED lifecycle and before/after audit. The discount approval request is a new proposal type that slots into this framework rather than needing a new collection.
- `backend/agents/implementations/taskmaster.py:49` — `refund_issue` is already in the tier-2 (ask-confirm) list. Discount overrides follow the same pattern.

**In-app notifications (built):**
- `backend/api/routers/notifications.py` + `notifications` collection — bell notifications for ADMIN/SUPERADMIN already implemented including unread badge.

**Leave management (partially built):**
- `backend/api/routers/hr.py:1237-1304` — leave apply (PENDING → APPROVED/REJECTED), manager approval gate, overlap detection. The gap is mobile push and a fast-path approval surface; the state machine itself is done.

**MSG91 dispatch (built):**
- `backend/agents/providers.py` — `send_whatsapp`, `send_sms` with DISPATCH_MODE gating. MEGAPHONE already uses this for Rx reminders. The feature adds discount/leave as new trigger types.

**Quiet hours (built):**
- `backend/agents/quiet_hours.py` — shared 21:00–09:00 IST DND window. Leave approval requests respect this (discount overrides bypass DND as they are operational).

## Reuse (extend, don't rebuild)
- `backend/agents/proposals.py` — add `discount_override` and `leave_approval` to proposal types; the existing PENDING → APPROVED → REJECTED lifecycle, before/after state capture, and audit_logs write are reused verbatim
- `ai_proposals` collection — no schema change; `type` field accepts new values; `payload` carries the request details
- `backend/api/routers/orders.py:1195-1312` — change the 403 hard-reject into a 202 "pending approval" response that creates a proposal and halts the POS cart
- `frontend/src/stores/posStore.ts` — add `pendingApprovalId`, `approvalStatus`, and `approvalPin` fields; the existing `cart_discount_approved_by` field receives the approver identity on resolution
- `backend/api/routers/hr.py:1237-1304` — leave apply already creates the PENDING leave doc; extend to fire a MSG91 WhatsApp to the approving manager immediately on submit
- `backend/api/routers/notifications.py` — reuse bell notification creation for in-app alert alongside the WhatsApp push
- `backend/api/services/role_caps.py` — read the margin-threshold config from `approval_matrix_settings` (new doc in `business_settings`) rather than hardcoding

## Data model
**New doc inside existing `business_settings` collection** (keyed `approval_matrix`):
```
{
  "_id": "approval_matrix",
  "discount_approval": {
    "trigger_below_margin_pct": <owner sets>,   // e.g. 30 — gross margin % floor
    "trigger_above_discount_pct": <owner sets>, // e.g. 15 — discount % that triggers halt
    "approver_roles": ["ADMIN", "SUPERADMIN"],
    "pin_ttl_seconds": 300,                     // PIN expires after N seconds
    "auto_reject_seconds": 600                  // auto-reject if no response in N seconds
  },
  "leave_approval": {
    "same_day_route_to": ["STORE_MANAGER", "AREA_MANAGER"],
    "advance_notice_days_threshold": 2,         // requests < 2 days notice use fast-path
    "fast_path_ttl_seconds": 900
  }
}
```

**New fields on `ai_proposals`** (no migration; new proposals carry these):
- `proposal_subtype`: `"discount_override"` | `"leave_approval"`
- `requester_id`, `requester_name`, `store_id` (already in payload; make top-level for query speed)
- `pin_hash`: bcrypt hash of the one-time PIN (never stored in plain text)
- `pin_expires_at`: datetime
- `resolved_at`: datetime, set on APPROVED or REJECTED

**New fields on `leaves`** (existing collection):
- `fast_path_proposal_id`: links to the `ai_proposals` doc when fast-path was used
- `approved_via`: `"standard"` | `"fast_path"` | `"auto_rejected"`

## Backend
- `POST /api/v1/approvals/discount-request` — called by POS when discount exceeds threshold. Creates `ai_proposals` doc (type=`discount_override`), generates a 6-digit PIN, stores bcrypt hash + TTL, sends WhatsApp + in-app bell to every online ADMIN/SUPERADMIN. Returns `{proposal_id, expires_at}`. POS cart status → `AWAITING_APPROVAL`. Extend `orders.py` discount validation path to call this instead of raising 403.
- `POST /api/v1/approvals/{proposal_id}/resolve` — called by approver (ADMIN/SUPERADMIN only). Body: `{decision: "approve"|"reject", pin?: str}`. On approve: validates PIN hash + TTL, flips proposal to APPROVED, returns PIN to caller. On reject: flips to REJECTED, POS receives rejection event. Writes immutable `audit_logs` row with before/after discount, approver identity, timestamp.
- `GET /api/v1/approvals/pending` — ADMIN/SUPERADMIN only. Lists open `discount_override` and `leave_approval` proposals for their stores. Sorted by `created_at` ASC (oldest first). Used by the approver mobile surface.
- `POST /api/v1/approvals/leave-fast-path` — thin wrapper over existing `hr.py` leave creation. When `days_until_start < threshold`, fires MSG91 WhatsApp to the approving manager and stores `fast_path_proposal_id` on the leave doc. Reuses existing `hr.py:1237` create-leave logic; just adds the notification side-effect.
- `POST /api/v1/approvals/{proposal_id}/auto-reject` — internal endpoint called by a background check in TASKMASTER's 5-minute tick. If `pin_expires_at` is past and proposal still PENDING, flips to auto-REJECTED and notifies POS cashier. No new scheduler needed; piggybacks on TASKMASTER's existing `_do_background_work()`.
- Extend `backend/api/middleware/rbac_enforcement.py` — no new logic; the new routes register in `rbac_policy.py` POLICY list with `approver_roles` from settings.

## Frontend
**POS approval-halt overlay** (extend `frontend/src/pages/pos/POSPage.tsx` and `posStore.ts`):
- When backend returns 202 from discount-request, POS renders a full-screen modal (light, neutral) showing: item, proposed discount %, margin impact, approver-notified message, countdown timer. Two actions: Cancel (restore original price) and Enter PIN (6-digit input). On PIN entry calls `/resolve` endpoint; on APPROVED the cart continues normally with `cart_discount_approved_by` populated.

**Approver quick-action surface** (new lightweight page `frontend/src/pages/approvals/PendingApprovalsPage.tsx`):
- Accessible from the bell icon badge and from the sidebar "Approvals" item (visible to ADMIN/SUPERADMIN only, gated by role). Lists pending requests in two tabs: Discounts and Leave. Each card shows: requester name, store, item/discount or leave dates, time elapsed. Approve button generates and displays the PIN; Reject button with optional note. Restrained card-list layout, no colour beyond semantic green/red on the action buttons.

**Leave request flow** (extend `frontend/src/pages/attendance/AttendancePage.tsx`):
- After submitting a leave request, show a "Sent for approval" status chip. If fast-path triggered (short notice), show "Urgent — manager notified via WhatsApp" subtext. No new page needed.

**Settings panel** (extend `frontend/src/pages/settings/` — whichever tab holds business rules):
- Single card "Approval Matrix" under Operations Settings (SUPERADMIN only). Shows the two threshold fields for discounts and the fast-path window for leave. Calls `GET/PUT /api/v1/settings/approval-matrix`. No free-form JSON — use labelled number inputs.

## Business rules
- PIN is one-time: once used to approve one request it is invalid even if TTL has not elapsed.
- PIN hash is bcrypt (never reversible); the plain PIN is shown once to the approver in the UI and never stored.
- Auto-reject fires at `pin_expires_at`; the cashier is notified and the cart discount is reset to the cap without manual action needed.
- Discount override approval does NOT raise the user's role cap permanently — it is a per-transaction exception, captured in `cart_discount_approved_by` and `audit_logs`.
- Leave fast-path is still subject to the existing overlap-detection logic in `hr.py:1257` — a fast-path approval cannot override an already-approved leave for the same date range.
- Rejected discount requests are logged with the original proposed discount % so ORACLE can detect abuse patterns (cashiers repeatedly requesting overrides).
- All resolution events (approve/reject/auto-reject) write to `audit_logs` with `entity_type="discount_approval"` or `"leave_approval"`, approver identity, and before/after state. Immutable.
- Quiet hours (21:00–09:00 IST) bypass for discount WhatsApp alerts because they are operational, not marketing. Leave fast-path alerts respect quiet hours for non-same-day requests; same-day urgent requests bypass.

## RBAC
| Role | Discount request | Discount approve/reject | Leave apply | Leave approve | View pending |
|---|---|---|---|---|---|
| SUPERADMIN | implicit (no cap) | YES | YES | YES | YES |
| ADMIN | implicit (no cap) | YES | YES | YES | YES |
| AREA_MANAGER | if discount > own cap | NO | YES | YES for own stores | own stores |
| STORE_MANAGER | if discount > own cap | NO | YES | YES for own store | own store (leave only) |
| SALES_CASHIER / SALES_STAFF | triggers halt | NO | YES | NO | NO |
| All others | N/A | NO | where applicable | NO | NO |

## Integrations
- **MSG91 WhatsApp** — approval request notification to ADMIN/SUPERADMIN. Template: `DISCOUNT_OVERRIDE_REQUEST` and `LEAVE_FAST_PATH_REQUEST` (two new entries in `notification_templates` collection). Reuses `send_whatsapp()` from `backend/agents/providers.py`. DISPATCH_MODE-gated.
- **TASKMASTER agent** — extend `_do_background_work()` in `taskmaster.py` to sweep for expired pending proposals and auto-reject them. No new scheduler.
- **ORACLE agent** — subscribe to `approval.rejected` event to accumulate per-cashier override-request rate. If rate exceeds threshold (> N requests/day), enqueue an advisory proposal to SUPERADMIN. Reuses existing anomaly-detection pattern in `oracle.py`.

## Risk notes
- **POS revenue risk** — The approval-halt path sits in the order-creation hot path (`orders.py:1195`). A bug here can block sales. This must ship behind a feature flag (`APPROVAL_MATRIX_ENABLED=false` default in env; `business_settings.approval_matrix.enabled` bool toggled by SUPERADMIN from Settings UI). Off by default; owner enables per store.
- **PIN timing attack** — bcrypt comparison must use `hmac.compare_digest` to prevent timing leaks. Use `passlib.hash.bcrypt.verify()` which is constant-time.
- **Connectivity dependency** — If MSG91 is down, the PIN is generated and stored but the approver gets no WhatsApp. Mitigation: in-app bell notification is sent independently (different code path, no external dependency). The approver can poll `/approvals/pending` from any browser even without the push.
- **Auto-reject race** — TASKMASTER tick is every 5 minutes; a PIN with 300s TTL could sit expired for up to 5 minutes before auto-reject fires. Acceptable for retail; document this as "up to 5 minutes after PIN expiry."
- **Leave fast-path vs payroll lock** — if a period is locked (`period_locks`), leave approvals that retroactively change LWP for a locked month should be blocked. Add a check in the fast-path handler that mirrors the existing `check_period_locked` guard in `hr.py`.

## Recommendation
Build later (not a quick win). The POS halt path is revenue-critical and needs the feature flag scaffolding before any code touches `orders.py`. Recommended sequencing: (1) build the `approval_matrix_settings` doc and Settings UI card first, (2) build `/approvals` endpoints and the approver surface, (3) wire the POS halt as the last step behind the flag, (4) owner enables flag store-by-store after live testing.

## Owner decisions
- Q: What gross-margin % floor should trigger a discount approval request? | Why: Sets the `trigger_below_margin_pct` threshold. Too low = constant interruptions; too high = no protection. | Options: a) 30% (conservative, catches deep discounts only) / b) 20% (standard retail floor) / c) custom per category (MASS/PREMIUM/LUXURY)
- Q: Should AREA MANAGERs be able to approve discount overrides, or only ADMIN/SUPERADMIN? | Why: Determines who gets WhatsApp alerts and who can issue the PIN. Affects response time vs. security. | Options: a) ADMIN + SUPERADMIN only / b) AREA_MANAGER + ADMIN + SUPERADMIN / c) STORE_MANAGER (own store only) + above
- Q: How long should the PIN stay valid before auto-reject? | Why: Too short = sales blocked if approver is unavailable; too long = PIN abuse window. | Options: a) 5 minutes / b) 10 minutes / c) 15 minutes
- Q: Should leave fast-path apply to all leave types or only Casual/Sick? | Why: Earned/Privilege leave typically requires advance notice by HR policy; fast-tracking those could conflict with accrual rules. | Options: a) Casual + Sick only / b) All leave types / c) Store manager decides per request
- Q: Should a rejected discount override be permanently visible to the cashier's line manager in a report, or only to ADMIN/SUPERADMIN? | Why: Determines who can see the abuse-detection signal from ORACLE. | Options: a) STORE_MANAGER + above / b) ADMIN + SUPERADMIN only
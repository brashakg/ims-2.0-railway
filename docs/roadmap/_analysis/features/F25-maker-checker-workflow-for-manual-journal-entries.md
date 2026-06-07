# Feature #25: Maker-Checker Workflow for Manual Journal Entries
META: effort=M days=5 risk=MED roi=4 quickwin=no deps=none phase=3

## Existing overlap
IMS has several relevant primitives but no GL journal layer:

- **Proposal system** (`backend/agents/proposals.py`, `ai_proposals` collection): already models PENDING→APPROVED→EXECUTED with before/after state capture and immutable audit trail. The tier-2 "ask-confirm" flow is the conceptual twin of maker-checker.
- **Period lock** (`backend/api/routers/finance.py:446-481`, `period_locks` collection): blocks posting to closed months — re-usable as a hard gate on JE posting.
- **`audit_logs` collection** (`backend/api/routers/admin.py`, `finance.py`): immutable action log with entity_id/before_state/after_state. JE lifecycle events write here.
- **Expense separation-of-duties** (`backend/api/routers/expenses.py:614-730`): PENDING→APPROVED→SENT_TO_ACCOUNTANT→ENTERED with role-gated approval. The ENTERED state + `ledger_reference` field is the closest existing analogue.
- **ACCOUNTANT and ADMIN roles** are already defined in `backend/api/services/rbac_policy.py` and have finance-gating precedents throughout `finance.py`.
- **P&L aggregation** (`finance.py:628-724`): reads from `orders`, `expenses`, `payroll`. A new `journal_entries` collection would add a fourth source to this aggregation — the integration point is clean and additive.

No existing GL journal collection, no double-entry posting, no chart of accounts. Greenfield on the data model; extension on everything else.

## Reuse (extend, don't rebuild)
- **`backend/api/routers/finance.py`** — add new JE endpoints here (same router prefix `/api/v1/finance/journal-entries`); reuse `check_period_locked()` (line 459) as-is
- **`backend/agents/proposals.py` pattern** — copy the PENDING→APPROVED→EXECUTED state machine and before/after capture; do not reuse the `ai_proposals` collection (JEs are human-initiated, not AI proposals)
- **`audit_logs` collection** — write JE lifecycle events (DRAFT, SUBMITTED, APPROVED, REJECTED, POSTED, REVERSED) here using the existing schema; no new audit collection needed
- **`backend/api/routers/expenses.py` ENTERED-state pattern** — the "accountant marks ledger-entered" step mirrors posting; reuse the mental model and role gate
- **`frontend/src/pages/finance/FinanceDashboard.tsx`** — add a "Journal Entries" tab rather than a new top-level page; keeps finance work in one place
- **`period_locks` collection** — zero changes; `check_period_locked(month, year, db)` is called unchanged before any JE posts

## Data model
New collection **`journal_entries`**:
```
{
  je_id: string (UUID, PK),
  je_number: string (JE/FY/serial — same atomic counter pattern as invoice_number, counters collection),
  store_id: string,
  entity_id: string,          # legal entity (for multi-GSTIN P&L split)
  entry_date: datetime (IST), # the accounting date — period-lock checked against this
  description: string (max 500 chars),
  reference: string (optional — PO number, expense_id, vendor_bill_id, etc.),
  lines: [
    {
      line_id: string,
      account_code: string,   # from chart_of_accounts (see below)
      account_name: string,   # denormalised for display speed
      debit: Decimal128,      # paisa-exact; exactly one of debit/credit non-zero
      credit: Decimal128,
      narration: string (optional, max 200 chars)
    }
  ],
  total_debit: Decimal128,    # sum(lines.debit) — validated == total_credit
  total_credit: Decimal128,
  status: enum DRAFT | SUBMITTED | APPROVED | REJECTED | POSTED | REVERSED,
  maker_id: string,           # user_id of drafter
  maker_name: string,
  checker_id: string (nullable),
  checker_name: string (nullable),
  checker_note: string (nullable, max 500 chars),
  reversal_of: string (nullable, je_id of the JE being reversed),
  reversed_by: string (nullable, je_id of the reversal JE),
  created_at: datetime,
  submitted_at: datetime (nullable),
  checked_at: datetime (nullable),
  posted_at: datetime (nullable)
}
```

New collection **`chart_of_accounts`** (lightweight, seeded):
```
{
  account_code: string (PK, e.g. "5001"),
  account_name: string (e.g. "Depreciation — Furniture"),
  account_type: enum ASSET | LIABILITY | EQUITY | REVENUE | EXPENSE,
  is_active: boolean,
  allow_manual_je: boolean,  # some accounts (e.g. GST Output Tax) blocked from manual JE
  created_at: datetime
}
```

Indexes: `journal_entries` — `(store_id, entry_date)`, `(status)`, `(je_number)` unique; `chart_of_accounts` — `(account_code)` unique.

## Backend
All endpoints under `/api/v1/finance/journal-entries` in `backend/api/routers/finance.py`:

- **POST `/journal-entries`** — Maker creates DRAFT JE. Validates: lines balance (total_debit == total_credit, paisa-exact); entry_date not in locked period; all account_codes exist + `allow_manual_je=True`; minimum 2 lines. Stamps `status=DRAFT`, `maker_id`.
- **POST `/journal-entries/{je_id}/submit`** — Maker promotes DRAFT→SUBMITTED. Maker cannot be checker (enforced). Notifies checker roles via in-app bell (`notifications` collection).
- **POST `/journal-entries/{je_id}/approve`** — Checker (ACCOUNTANT/ADMIN/SUPERADMIN, not the maker) moves SUBMITTED→APPROVED. Stamps `checker_id`, `checker_note`, `checked_at`.
- **POST `/journal-entries/{je_id}/post`** — Same checker or SUPERADMIN moves APPROVED→POSTED. This is the single moment the JE affects P&L reads. Runs `check_period_locked()` again (double gate). Stamps `posted_at`. Writes to `audit_logs`.
- **POST `/journal-entries/{je_id}/reject`** — Checker rejects SUBMITTED→REJECTED with mandatory `checker_note`. Notifies maker. JE can be edited and resubmitted (creates new DRAFT from rejected, stamping `reference` back to the rejected je_id).
- **POST `/journal-entries/{je_id}/reverse`** — ADMIN/SUPERADMIN only; only on POSTED JEs. Auto-creates a mirror JE (debits↔credits swapped, description prefixed "REVERSAL OF {je_number}"), sets both to POSTED, links `reversal_of` / `reversed_by`. Period lock checked on reversal date.
- **GET `/journal-entries`** — list with filters: status, date range, maker_id, store_id. Store-scoped for STORE_MANAGER/ACCOUNTANT; org-wide for ADMIN/SUPERADMIN.
- **GET `/journal-entries/{je_id}`** — full detail with lines + audit trail (filtered from `audit_logs` by entity_id=je_id).
- **GET `/chart-of-accounts`** — list all accounts (filter `is_active=True`). Readable by all finance roles.
- **POST `/chart-of-accounts`** — SUPERADMIN only; seed or add accounts.

**P&L integration**: `finance.py` `get_pnl()` (line 628) already sums orders + expenses + payroll. Add a fourth aggregation: `sum(lines.credit - lines.debit)` grouped by `account_type` for POSTED JEs in the date range. REVENUE accounts increase revenue; EXPENSE accounts increase cost. This is an additive `$lookup + $group` on `journal_entries` — no existing query is changed.

## Frontend
Extend **`frontend/src/pages/finance/FinanceDashboard.tsx`** with a "Journal Entries" tab (do not create a new page):

- **JE List view** (default tab content): table — je_number / entry_date / description / total_debit / status chip (colour-coded: DRAFT=gray, SUBMITTED=amber, APPROVED=blue, POSTED=green, REJECTED=red, REVERSED=slate) / maker_name. Filter bar: status multi-select, date range, store picker.
- **New JE drawer** (slide-in, not full page): entry_date picker (calendar), description field, reference field, line-items table with account code search (autocomplete from `/chart-of-accounts`), debit/credit amount inputs (paisa-exact, numeric keyboard). Running debit/credit totals shown; "Balance" badge turns green when equal. Submit button disabled until balanced + ≥2 lines.
- **JE Detail drawer** (click any row): shows all fields + status timeline (created → submitted → checked → posted). Checker sees "Approve" / "Reject" action buttons with mandatory note field. ADMIN/SUPERADMIN sees "Reverse" button on POSTED JEs.
- **Notification integration**: maker receives in-app bell toast on reject; checker receives bell toast on submit — reuses existing `useToast()` from `context/ToastContext` and `/api/v1/notifications` write.
- Design: neutral/monochrome table; status chips use existing Tailwind semantic colours (amber-100/amber-700 for pending, green-100/green-700 for posted). No new design tokens needed.

## Business rules
- **Balance invariant (hardlock)**: `sum(debit lines) == sum(credit lines)` to the paisa; backend rejects if not equal (HTTP 422). No exceptions.
- **Maker ≠ Checker (hardlock)**: the user who created the JE cannot approve or post it. Enforced in both backend and greyed-out in UI.
- **Period lock gate (hardlock)**: `entry_date` must not fall in a locked period. Checked at DRAFT creation and again at POST (double gate in case period was locked between submission and posting).
- **account_code must exist + allow_manual_je=True (hardlock)**: accounts like GST Output Tax, Creditors Control are seeded with `allow_manual_je=False` to prevent accidental corruption of system-managed balances.
- **Reversal date must be in open period**: a reversal JE uses `today` as entry_date; if today's period is locked, reversal is blocked with a clear error message.
- **Immutable POSTED JEs**: once POSTED, a JE cannot be edited or deleted. The only mutation path is a formal reversal (which itself creates a new traceable JE).
- **Audit trail mandatory**: every status transition writes an `audit_logs` entry with before/after status, user_id, timestamp, ip_address.
- **Minimum 2 lines**: a single-line JE is a data error; blocked at creation.
- **No backdating beyond financial year start**: entry_date must be ≥ current FY start (April 1) unless SUPERADMIN override — reduces year-crossing errors.

## RBAC

| Role | Can Draft | Can Submit | Can Approve/Post | Can Reject | Can Reverse | Can View |
|---|---|---|---|---|---|---|
| SUPERADMIN | Yes | Yes | Yes | Yes | Yes | All stores |
| ADMIN | Yes | Yes | Yes | Yes | Yes | All stores |
| ACCOUNTANT | Yes | Yes | Approve only (not own) | Yes (not own) | No | Own store |
| AREA_MANAGER | No | No | No | No | No | Own stores (read) |
| STORE_MANAGER | No | No | No | No | No | Own store (read) |
| All other roles | No | No | No | No | No | No |

Enforced via `require_roles(...)` on each endpoint; the "not own" maker≠checker rule is checked inside the handler (not at the RBAC layer) because it depends on runtime state.

## Integrations
- **Tally**: NEXUS nightly export (`backend/agents/implementations/nexus.py`, `tally_exports` collection) currently exports sales vouchers. Extend `_build_tally_export()` to also export POSTED JEs as Tally Journal Vouchers (XML `<JOURNALVOUCHER>` type). Only POSTED JEs are eligible; DRAFT/SUBMITTED/APPROVED are invisible to Tally.
- **In-app notifications** (`notifications` collection): Maker→checker "JE submitted for review" bell on submit; Checker→maker "JE rejected — note: …" bell on reject. Reuses existing `POST /api/v1/notifications` internal write pattern.
- No MSG91/WhatsApp/Shopify/Razorpay/Shiprocket involvement — this is a pure internal accounting workflow.
- Jarvis/ORACLE: out of scope for this feature; ORACLE could flag unusual JE patterns (large round-number debits to non-standard accounts) in a future iteration.

## Risk notes
- **P&L integrity risk**: POSTED JEs directly affect the P&L aggregation. The double period-lock gate and balance invariant are the primary guards. Ship the feature behind an `ENABLE_MANUAL_JE=1` env flag (Railway variable); default off in production until owner has seeded the chart of accounts and tested the flow.
- **Chart of accounts bootstrap**: the feature is useless without a seeded COA. A seed script must run before go-live. Accounts that IMS auto-generates (GST Output, stock, payroll) must have `allow_manual_je=False` to prevent double-counting.
- **No double-entry enforcement at the sub-ledger level**: this implementation enforces debit=credit but does not enforce that the account types net to zero (e.g., it won't prevent a nonsensical Revenue DR / Revenue CR JE). That level of validation requires a full chart-of-accounts type-awareness engine — out of scope.
- **Tally XML extension** touches the nightly export path; test in staging with `DISPATCH_MODE=test` before enabling in live Tally.
- POS and inventory are completely unaffected — JEs are a pure finance-layer feature.

## Recommendation
**Build now (Phase 3, after returns/credit-note hardening).** The audit and compliance value is high for a multi-entity retail business generating depreciation, accruals, and adjustment entries. The P&L integration is purely additive (new aggregation source, no existing queries changed). The maker-checker model maps cleanly onto existing patterns (proposals, expense separation-of-duties). Estimated 5 days: 2 days backend (data model + endpoints + P&L integration), 1 day COA seed script + Tally export extension, 2 days frontend (JE tab + new/detail drawers).

## Owner decisions
- **Q:** Which accounts should be in the initial chart of accounts seed — specifically, which expense categories do you track manually today (depreciation, rent, bank charges, miscellaneous adjustments) and which are already captured by the expense-claim workflow? | **Why:** Accounts already covered by the expenses module should have `allow_manual_je=False` to avoid double-counting in P&L. | **Options:** (a) Owner provides a list of manual-JE accounts; (b) Start with a minimal seed (Depreciation, Bank Charges, Prior-Period Adjustments, Miscellaneous) and expand over time.
- **Q:** Should the Junior Accountant (ACCOUNTANT role) be the only maker, or should Store Managers also be allowed to draft JEs for store-level adjustments? | **Why:** Granting STORE_MANAGER draft access increases coverage but reduces control — a store manager could draft entries that affect multi-store P&L. | **Options:** (a) ACCOUNTANT only drafts; (b) STORE_MANAGER can draft for own store only, ACCOUNTANT/ADMIN approves.
- **Q:** Should POSTED JEs be visible to Store Managers and Area Managers in read-only mode, or kept restricted to finance roles only? | **Why:** Visibility helps store managers understand their P&L adjustments, but exposes accounting decisions that may not be appropriate at that level. | **Options:** (a) Finance-only visibility (ACCOUNTANT/ADMIN/SUPERADMIN); (b) STORE_MANAGER/AREA_MANAGER see read-only JEs for their store.
- **Q:** Should the Tally Journal Voucher export happen in the existing nightly 23:00 run, or as a separate on-demand export triggered manually by the accountant? | **Why:** Nightly auto-export is simpler but irreversible if a JE is posted with an error; on-demand gives the accountant control over when entries hit Tally. | **Options:** (a) Add to nightly NEXUS run automatically; (b) Accountant-triggered export button per JE or per date range.
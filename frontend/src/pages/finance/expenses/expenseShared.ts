// ============================================================================
// IMS 2.0 - Expenses: shared constants, role lists and the section context
// ============================================================================
// Wave 6 split: the old ExpenseTracker page held nine role-gated tabs behind
// one URL in useState. Each tab is now its own page under ExpensesLayout
// (/finance/expenses/<section>). What two or more of those pages read lives
// here, moved byte-identical from the old page.

import { useOutletContext } from 'react-router-dom';
import type { UserRole } from '../../../types';
import type { AgingReport, ExpenseRecord } from '../../../services/api/expenses';

interface ApiError {
  // FastAPI returns a string detail for our own HTTPExceptions and a list of
  // field errors for a 422 (schema validation, e.g. an unknown advance type).
  response?: { status?: number; data?: { detail?: string | { msg?: string }[] } };
}

// Expense categories carry NO status meaning, so they all share one neutral
// chip (was a decorative rainbow — off the muted house theme).
//
// OWNER RULING 2026-08-14: this is a CLOSED list, and since 2026-08-15 the
// server enforces it too — POST /expenses rejects any other category with a 422
// naming these, so pay can no longer be recorded as a shop expense. Keep this
// array identical to EXPENSE_CATEGORIES in backend/api/routers/expenses.py; a
// backend test (test_expense_category_fixed_list.py) reads THIS file and fails
// if they drift, because the symptom of drift is a form that 422s after the
// user has typed everything.
//
// The mixed casing (eight lowercase, PETTY_CASH uppercase) is pre-existing and
// deliberate: stored expenses, the petty-cash float rule and the spend caps all
// already key off these exact strings. Do not tidy it.
//
// "Miscellaneous" stays EXACTLY as it is by the owner's explicit decision — no
// cap, no warning, no nudge.
const CATEGORIES: { value: string; label: string; color: string }[] = [
  { value: 'utilities', label: 'Utilities', color: 'bg-gray-100 text-gray-700' },
  { value: 'rent', label: 'Rent / Lease', color: 'bg-gray-100 text-gray-700' },
  { value: 'maintenance', label: 'Maintenance', color: 'bg-gray-100 text-gray-700' },
  { value: 'supplies', label: 'Supplies', color: 'bg-gray-100 text-gray-700' },
  { value: 'travel', label: 'Travel', color: 'bg-gray-100 text-gray-700' },
  { value: 'food', label: 'Food & Beverage', color: 'bg-gray-100 text-gray-700' },
  { value: 'marketing', label: 'Marketing', color: 'bg-gray-100 text-gray-700' },
  { value: 'miscellaneous', label: 'Miscellaneous', color: 'bg-gray-100 text-gray-700' },
  // F17: a petty-cash payout draws down the store float on approval. Neutral
  // badge -- the category carries no status meaning (no colour-flag).
  { value: 'PETTY_CASH', label: 'Petty Cash Payout', color: 'bg-gray-100 text-gray-700' },
];

const PAYMENT_MODES: { value: string; label: string }[] = [
  { value: 'CASH', label: 'Cash' },
  { value: 'UPI', label: 'UPI' },
  { value: 'CARD', label: 'Card' },
  { value: 'BANK_TRANSFER', label: 'Bank transfer' },
  { value: 'CHEQUE', label: 'Cheque' },
];

const STATUS_META: Record<string, { label: string; badge: string }> = {
  DRAFT: { label: 'Draft', badge: 'bg-gray-100 text-gray-600' },
  PENDING: { label: 'Pending', badge: 'bg-amber-50 text-amber-700' },
  APPROVED: { label: 'Approved', badge: 'bg-green-50 text-green-700' },
  REJECTED: { label: 'Rejected', badge: 'bg-red-50 text-red-700' },
  SENT_TO_ACCOUNTANT: { label: 'With accountant', badge: 'bg-blue-50 text-blue-700' },
  ENTERED: { label: 'Entered', badge: 'bg-green-50 text-green-700' },
};

const catLabel = (v: string) => CATEGORIES.find((c) => c.value === v)?.label || v;
const catColor = (v: string) => CATEGORIES.find((c) => c.value === v)?.color || 'bg-gray-100 text-gray-700';
const payLabel = (v?: string | null) => PAYMENT_MODES.find((p) => p.value === v)?.label || v || '—';
const fc = (n: number) => `₹${Math.round(n || 0).toLocaleString('en-IN')}`;

// WHO SEES WHICH SECTION. Each list is the old page's JSX gate, verbatim
// (isApprover / isAccountant / canViewFloat / canManageFloat). The router
// gates in routes/financeRoutes.tsx and the layout's nav + loaders read these
// same four lists, so a section can never be offered to a role its route
// refuses.
const EXPENSE_APPROVER_ROLES: UserRole[] = ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'];
const EXPENSE_ACCOUNTANT_ROLES: UserRole[] = ['SUPERADMIN', 'ADMIN', 'ACCOUNTANT'];
const FLOAT_VIEW_ROLES: UserRole[] = ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'];
const FLOAT_MANAGE_ROLES: UserRole[] = ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER'];

/** What ExpensesLayout hands its section pages through <Outlet context>.
 *  The layout owns the one expenses load (its header cards and nav counts
 *  need it too); sections read it here instead of loading a second copy. */
interface ExpensesOutletContext {
  isApprover: boolean;
  canManageFloat: boolean;
  mine: ExpenseRecord[];
  approvals: ExpenseRecord[];
  toEnter: ExpenseRecord[];
  aging: AgingReport | null;
  duplicates: ExpenseRecord[];
  doAction: (fn: () => Promise<unknown>, ok: string) => Promise<void>;
  totalAmt: number;
  pendingCount: number;
  approvedCount: number;
}

const useExpensesContext = () => useOutletContext<ExpensesOutletContext>();

export {
  CATEGORIES,
  PAYMENT_MODES,
  STATUS_META,
  catLabel,
  catColor,
  payLabel,
  fc,
  EXPENSE_APPROVER_ROLES,
  EXPENSE_ACCOUNTANT_ROLES,
  FLOAT_VIEW_ROLES,
  FLOAT_MANAGE_ROLES,
  useExpensesContext,
};
export type { ApiError, ExpensesOutletContext };

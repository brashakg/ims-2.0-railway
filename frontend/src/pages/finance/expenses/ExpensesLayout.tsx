// ============================================================================
// IMS 2.0 - Expense Tracking & Approval System (layout)
// ============================================================================
// Submit -> approve/reject -> send to accountant -> entered in books.
// Visibility: a user sees only their own expenses; ADMIN/SUPERADMIN see all.
// Approvers (ADMIN/AREA_MANAGER/STORE_MANAGER/ACCOUNTANT) get an approval queue;
// ACCOUNTANT/ADMIN get a ledger-entry queue.
//
// Wave 6 split (the W1 recipe PurchaseLayout set): the nine tabs that lived in
// useState behind one URL are real pages now, one URL each:
//   /finance/expenses (My Expenses) · /approvals · /entry · /aging ·
//   /duplicates · /advances · /float · /settle · /summary
// The role gate that used to sit on each tab's JSX is the route's
// allowedRoles (routes/financeRoutes.tsx). This layout keeps the header, the
// summary cards, the section nav, the Add-expense modal and the one expenses
// load; it hands that data to the section pages through <Outlet context>.

import { useState, useEffect, useCallback } from 'react';
import { NavLink, Outlet } from 'react-router-dom';
import { Plus, X as XIcon, Loader2 } from 'lucide-react';
import { useAuth } from '../../../context/AuthContext';
import { useToast } from '../../../context/ToastContext';
import {
  expensesApi,
  type ExpenseRecord,
  type AgingReport,
} from '../../../services/api/expenses';
import clsx from 'clsx';
import {
  CATEGORIES,
  PAYMENT_MODES,
  fc,
  EXPENSE_APPROVER_ROLES,
  EXPENSE_ACCOUNTANT_ROLES,
  FLOAT_VIEW_ROLES,
  FLOAT_MANAGE_ROLES,
  type ApiError,
  type ExpensesOutletContext,
} from './expenseShared';
import { Card, Labeled } from './expenseWidgets';

type TabType =
  | 'my' | 'approvals' | 'entry' | 'aging' | 'duplicates' | 'summary'
  | 'float' | 'settle' | 'advances';

export function ExpensesLayout() {
  const { user } = useAuth();
  const toast = useToast();
  const roles = user?.roles || [];
  const isApprover = roles.some((r) => EXPENSE_APPROVER_ROLES.includes(r));
  const isAccountant = roles.some((r) => EXPENSE_ACCOUNTANT_ROLES.includes(r));
  // F17: who can SEE the float tab (manager / area-manager / accountant / admin)
  // and who can MANAGE it (open / topup -- the accountant is view-only).
  const canViewFloat = roles.some((r) => FLOAT_VIEW_ROLES.includes(r));
  const canManageFloat = roles.some((r) => FLOAT_MANAGE_ROLES.includes(r));

  const [mine, setMine] = useState<ExpenseRecord[]>([]);
  const [approvals, setApprovals] = useState<ExpenseRecord[]>([]);
  const [toEnter, setToEnter] = useState<ExpenseRecord[]>([]);
  const [aging, setAging] = useState<AgingReport | null>(null);
  const [duplicates, setDuplicates] = useState<ExpenseRecord[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  // Modals
  const [showSubmitModal, setShowSubmitModal] = useState(false);

  // Form
  const [formCategory, setFormCategory] = useState('utilities');
  const [formAmount, setFormAmount] = useState('');
  const [formDescription, setFormDescription] = useState('');
  const [formDate, setFormDate] = useState(new Date().toISOString().split('T')[0]);
  const [formPaymentMode, setFormPaymentMode] = useState('CASH');
  const [formBill, setFormBill] = useState<File | null>(null);
  const [saving, setSaving] = useState(false);

  // isLoading is the FIRST load only. A reload (every toast re-runs this: the
  // ToastProvider's value changes identity) must never swap the tree for the
  // spinner, or <Outlet/> unmounts and the open section loses its state.
  const load = useCallback(async () => {
    try {
      const pick = (r: any): ExpenseRecord[] => (r?.expenses || r || []) as ExpenseRecord[];
      const [mineR, apprR, entR, agingR, dupR] = await Promise.all([
        expensesApi.getExpenses({}),
        isApprover ? expensesApi.getPendingApproval(user?.activeStoreId) : Promise.resolve(null),
        isAccountant ? expensesApi.getToEnter(user?.activeStoreId) : Promise.resolve(null),
        isAccountant ? expensesApi.getAging(user?.activeStoreId).catch(() => null) : Promise.resolve(null),
        isApprover ? expensesApi.getDuplicateBills(user?.activeStoreId).catch(() => null) : Promise.resolve(null),
      ]);
      setMine(pick(mineR));
      setApprovals(apprR ? pick(apprR) : []);
      setToEnter(entR ? pick(entR) : []);
      setAging((agingR as AgingReport | null) || null);
      setDuplicates(dupR ? pick(dupR) : []);
    } catch {
      toast.error('Failed to load expenses');
    } finally {
      setIsLoading(false);
    }
  }, [isApprover, isAccountant, user?.activeStoreId, toast]);

  useEffect(() => { load(); }, [load]);

  // Warm the sibling section chunks once the browser is idle, so the FIRST
  // click on any section renders without the lazy-chunk download spinner
  // (same owner feedback PurchaseLayout answers). Vite dedupes these against
  // the route-level lazy() imports -- no double download.
  useEffect(() => {
    const idle: (cb: () => void) => void =
      'requestIdleCallback' in window
        ? (cb) => (window as Window & { requestIdleCallback: (cb: () => void) => void }).requestIdleCallback(cb)
        : (cb) => { setTimeout(cb, 1500); };
    idle(() => {
      void import('./MyExpensesSection');
      void import('./ExpenseApprovalsSection');
      void import('./ExpenseEntrySection');
      void import('./ExpenseAgingSection');
      void import('./ExpenseDuplicatesSection');
      void import('./ExpenseAdvancesSection');
      void import('./PettyCashFloatSection');
      void import('./DaySettlementSection');
      void import('./ExpenseSummarySection');
    });
  }, []);

  const resetForm = () => {
    setFormCategory('utilities'); setFormAmount(''); setFormDescription('');
    setFormDate(new Date().toISOString().split('T')[0]); setFormPaymentMode('CASH'); setFormBill(null);
  };

  const handleSubmit = async () => {
    if (!formAmount || Number(formAmount) <= 0) { toast.error('Enter a valid amount'); return; }
    if (!formDescription.trim()) { toast.error('Enter a description'); return; }
    setSaving(true);
    try {
      const res = await expensesApi.createExpense({
        category: formCategory,
        amount: parseFloat(formAmount),
        description: formDescription.trim(),
        expense_date: formDate,
        payment_mode: formPaymentMode,
        store_id: user?.activeStoreId,
      });
      const newId = res?.expense_id;
      let dupWarned = false;
      if (formBill && newId) {
        try {
          const up = await expensesApi.uploadBill(newId, formBill);
          if (up?.duplicate_bill) {
            // Anti-fraud: same receipt was already attached to another expense
            // in this store. Soft warning -- the claim still goes through for an
            // approver to scrutinise.
            toast.warning('This bill matches a receipt already submitted in this store. Flagged for approver review.');
            dupWarned = true;
          }
        }
        catch { toast.warning('Expense saved, but bill upload failed'); }
      }
      if (!dupWarned) toast.success('Expense submitted for approval');
      setShowSubmitModal(false);
      resetForm();
      await load();
    } catch (err) {
      // Surface governance rejections (cap exceeded / unsettled advance) which
      // the backend returns as a 400 with a clear detail message, AND schema
      // rejections (422), which arrive as a LIST of field errors rather than a
      // string -- e.g. a category the server no longer accepts. The dropdown
      // only offers accepted categories, so a 422 here means something is out
      // of step; say what the server said instead of a useless generic.
      const e = err as ApiError;
      const detail = e?.response?.data?.detail;
      if (e?.response?.status === 400 && typeof detail === 'string') {
        toast.error(detail);
      } else if (Array.isArray(detail) && detail[0]?.msg) {
        toast.error(detail[0].msg);
      } else {
        toast.error('Failed to submit expense');
      }
    } finally {
      setSaving(false);
    }
  };

  const doAction = async (fn: () => Promise<unknown>, ok: string) => {
    try { await fn(); toast.success(ok); await load(); }
    catch { toast.error('Action failed'); }
  };

  // Summary derived from the user's own expenses.
  const totalAmt = mine.reduce((s, e) => s + (e.amount || 0), 0);
  const pendingCount = mine.filter((e) => (e.status || '').toUpperCase() === 'PENDING').length;
  const approvedCount = mine.filter((e) => ['APPROVED', 'SENT_TO_ACCOUNTANT', 'ENTERED'].includes((e.status || '').toUpperCase())).length;

  if (isLoading) {
    return <div className="flex items-center justify-center h-96"><Loader2 className="w-8 h-8 text-bv-red-600 animate-spin" /></div>;
  }

  const sectionContext: ExpensesOutletContext = {
    isApprover, canManageFloat, mine, approvals, toEnter, aging, duplicates,
    doAction, totalAmt, pendingCount, approvedCount,
  };

  return (
    <div className="inv-body">
      <div className="inv-head">
        <div>
          <div className="eyebrow" style={{ marginBottom: 6 }}>Expenses</div>
          <h1>What went out.</h1>
          <div className="hint">Submit · approve · send to accountant · enter in books. You see your own expenses; admins see all.</div>
        </div>
        <button onClick={() => setShowSubmitModal(true)} className="btn sm primary">
          <Plus className="w-4 h-4" /> Add expense
        </button>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
        <Card label="My total" value={fc(totalAmt)} />
        <Card label="Pending" value={String(pendingCount)} color="text-amber-600" />
        <Card label="Approved+" value={String(approvedCount)} color="text-green-600" />
        <Card label={isAccountant ? 'For my entry' : 'For approval'} value={String(isAccountant ? toEnter.length : approvals.length)} color="text-blue-600" />
      </div>

      {/* Section nav -- real links, one URL per section. A section is only
          offered to the roles its route lets in (same lists, expenseShared). */}
      <div className="flex gap-4 mb-6 border-b border-gray-200 overflow-x-auto">
        {([
          ['my', 'My Expenses'],
          ...(isApprover ? [['approvals', `Pending Approval${approvals.length ? ` (${approvals.length})` : ''}`]] : []),
          ...(isAccountant ? [['entry', `For Entry${toEnter.length ? ` (${toEnter.length})` : ''}`]] : []),
          ...(isAccountant ? [['aging', `Aging${aging?.total_count ? ` (${aging.total_count})` : ''}`]] : []),
          ...(isApprover ? [['duplicates', `Duplicates${duplicates.length ? ` (${duplicates.length})` : ''}`]] : []),
          ['advances', 'Advances'],
          ...(canViewFloat ? [['float', 'Petty Cash Float']] : []),
          ...(canViewFloat ? [['settle', 'Day Settlement']] : []),
          ['summary', 'Category Summary'],
        ] as [TabType, string][]).map(([tab, label]) => (
          <NavLink key={tab} to={tab === 'my' ? '/finance/expenses' : `/finance/expenses/${tab}`} end
            className={({ isActive }) => clsx('px-4 py-3 font-medium whitespace-nowrap transition-colors border-b-2',
              isActive ? 'text-bv-red-600 border-bv-red-500' : 'text-gray-500 border-transparent hover:text-gray-700')}>
            {label}
          </NavLink>
        ))}
      </div>

      <Outlet context={sectionContext} />

      {/* Add expense modal */}
      {showSubmitModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg border border-gray-200 max-w-md w-full max-h-[90dvh] overflow-y-auto">
            <div className="border-b border-gray-200 px-6 py-4 flex items-center justify-between">
              <h2 className="text-lg font-semibold text-gray-900">Add expense</h2>
              <button onClick={() => setShowSubmitModal(false)} className="text-gray-500 hover:text-gray-700" aria-label="Close modal"><XIcon className="w-5 h-5" /></button>
            </div>
            <div className="p-6 space-y-4">
              <Labeled label="Type of expense">
                <select value={formCategory} onChange={(e) => setFormCategory(e.target.value)} className="input-field" title="Category">
                  {CATEGORIES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
                </select>
              </Labeled>
              <Labeled label="Amount (₹)">
                <input type="number" value={formAmount} onChange={(e) => setFormAmount(e.target.value)} placeholder="0" className="input-field" title="Amount" />
              </Labeled>
              <Labeled label="Mode of payment">
                <select value={formPaymentMode} onChange={(e) => setFormPaymentMode(e.target.value)} className="input-field" title="Payment mode">
                  {PAYMENT_MODES.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
                </select>
              </Labeled>
              <Labeled label="Description">
                <textarea value={formDescription} onChange={(e) => setFormDescription(e.target.value)} rows={3} placeholder="What was this for?" className="input-field" title="Description" />
              </Labeled>
              <Labeled label="Date">
                <input type="date" value={formDate} onChange={(e) => setFormDate(e.target.value)} className="input-field" title="Date" />
              </Labeled>
              <Labeled label="Bill / receipt (optional)">
                <input type="file" accept="image/*,application/pdf" onChange={(e) => setFormBill(e.target.files?.[0] || null)}
                  className="block w-full text-sm text-gray-600 file:mr-3 file:py-1.5 file:px-3 file:rounded-md file:border-0 file:bg-gray-100 file:text-gray-700" title="Bill or receipt" />
              </Labeled>
            </div>
            <div className="border-t border-gray-200 px-6 py-4 flex gap-3 justify-end">
              <button onClick={() => setShowSubmitModal(false)} className="px-4 py-2 rounded-lg text-gray-600 hover:bg-gray-100">Cancel</button>
              <button onClick={handleSubmit} disabled={saving} className="px-4 py-2 bg-bv-red-600 text-white rounded-lg hover:bg-bv-red-700 disabled:opacity-60">
                {saving ? 'Submitting…' : 'Submit'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

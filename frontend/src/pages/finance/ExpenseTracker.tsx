// ============================================================================
// IMS 2.0 - Expense Tracking & Approval System
// ============================================================================
// Submit -> approve/reject -> send to accountant -> entered in books.
// Visibility: a user sees only their own expenses; ADMIN/SUPERADMIN see all.
// Approvers (ADMIN/AREA_MANAGER/STORE_MANAGER/ACCOUNTANT) get an approval queue;
// ACCOUNTANT/ADMIN get a ledger-entry queue.

import { useState, useEffect, useCallback } from 'react';
import {
  Plus, Search, Check, X as XIcon,
  Loader2, BarChart3, Send, BookCheck, Banknote, Clock,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { expensesApi, type ExpenseRecord, type AgingReport } from '../../services/api/expenses';
import { formatDateIST } from '../../utils/datetime';
import clsx from 'clsx';

type TabType = 'my' | 'approvals' | 'entry' | 'aging' | 'summary';

interface ApiError {
  response?: { status?: number; data?: { detail?: string } };
}

const CATEGORIES: { value: string; label: string; color: string }[] = [
  { value: 'utilities', label: 'Utilities', color: 'bg-blue-100 text-blue-700' },
  { value: 'rent', label: 'Rent / Lease', color: 'bg-indigo-100 text-indigo-700' },
  { value: 'maintenance', label: 'Maintenance', color: 'bg-orange-100 text-orange-700' },
  { value: 'supplies', label: 'Supplies', color: 'bg-green-100 text-green-700' },
  { value: 'travel', label: 'Travel', color: 'bg-purple-100 text-purple-700' },
  { value: 'food', label: 'Food & Beverage', color: 'bg-red-100 text-red-700' },
  { value: 'marketing', label: 'Marketing', color: 'bg-pink-100 text-pink-700' },
  { value: 'miscellaneous', label: 'Miscellaneous', color: 'bg-gray-100 text-gray-700' },
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
  PENDING: { label: 'Pending', badge: 'bg-yellow-100 text-yellow-700' },
  APPROVED: { label: 'Approved', badge: 'bg-green-100 text-green-700' },
  REJECTED: { label: 'Rejected', badge: 'bg-red-100 text-red-700' },
  SENT_TO_ACCOUNTANT: { label: 'With accountant', badge: 'bg-blue-100 text-blue-700' },
  ENTERED: { label: 'Entered', badge: 'bg-emerald-100 text-emerald-700' },
};

const catLabel = (v: string) => CATEGORIES.find((c) => c.value === v)?.label || v;
const catColor = (v: string) => CATEGORIES.find((c) => c.value === v)?.color || 'bg-gray-100 text-gray-700';
const payLabel = (v?: string | null) => PAYMENT_MODES.find((p) => p.value === v)?.label || v || '—';
const fc = (n: number) => `₹${Math.round(n || 0).toLocaleString('en-IN')}`;

export default function ExpenseTracker() {
  const { user } = useAuth();
  const toast = useToast();
  const roles = user?.roles || [];
  const isApprover = roles.some((r) => ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'].includes(r));
  const isAccountant = roles.some((r) => ['SUPERADMIN', 'ADMIN', 'ACCOUNTANT'].includes(r));

  const [activeTab, setActiveTab] = useState<TabType>('my');
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [categoryFilter, setCategoryFilter] = useState<string>('all');

  const [mine, setMine] = useState<ExpenseRecord[]>([]);
  const [approvals, setApprovals] = useState<ExpenseRecord[]>([]);
  const [toEnter, setToEnter] = useState<ExpenseRecord[]>([]);
  const [aging, setAging] = useState<AgingReport | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  // Modals
  const [showSubmitModal, setShowSubmitModal] = useState(false);
  const [showRejectModal, setShowRejectModal] = useState(false);
  const [selected, setSelected] = useState<ExpenseRecord | null>(null);
  const [rejectionReason, setRejectionReason] = useState('');

  // Form
  const [formCategory, setFormCategory] = useState('utilities');
  const [formAmount, setFormAmount] = useState('');
  const [formDescription, setFormDescription] = useState('');
  const [formDate, setFormDate] = useState(new Date().toISOString().split('T')[0]);
  const [formPaymentMode, setFormPaymentMode] = useState('CASH');
  const [formBill, setFormBill] = useState<File | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setIsLoading(true);
    try {
      const pick = (r: any): ExpenseRecord[] => (r?.expenses || r || []) as ExpenseRecord[];
      const [mineR, apprR, entR, agingR] = await Promise.all([
        expensesApi.getExpenses({}),
        isApprover ? expensesApi.getPendingApproval(user?.activeStoreId) : Promise.resolve(null),
        isAccountant ? expensesApi.getToEnter(user?.activeStoreId) : Promise.resolve(null),
        isAccountant ? expensesApi.getAging(user?.activeStoreId).catch(() => null) : Promise.resolve(null),
      ]);
      setMine(pick(mineR));
      setApprovals(apprR ? pick(apprR) : []);
      setToEnter(entR ? pick(entR) : []);
      setAging((agingR as AgingReport | null) || null);
    } catch {
      toast.error('Failed to load expenses');
    } finally {
      setIsLoading(false);
    }
  }, [isApprover, isAccountant, user?.activeStoreId, toast]);

  useEffect(() => { load(); }, [load]);

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
      if (formBill && newId) {
        try { await expensesApi.uploadBill(newId, formBill); }
        catch { toast.warning('Expense saved, but bill upload failed'); }
      }
      toast.success('Expense submitted for approval');
      setShowSubmitModal(false);
      resetForm();
      await load();
    } catch (err) {
      // Surface governance rejections (cap exceeded / unsettled advance) which
      // the backend returns as a 400 with a clear detail message.
      const e = err as ApiError;
      const detail = e?.response?.data?.detail;
      if (e?.response?.status === 400 && detail) {
        toast.error(detail);
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

  const handleReject = async () => {
    if (!rejectionReason.trim()) { toast.error('Provide a rejection reason'); return; }
    if (!selected) return;
    await doAction(() => expensesApi.rejectExpense(selected.expense_id, rejectionReason.trim()), 'Expense rejected');
    setShowRejectModal(false); setRejectionReason(''); setSelected(null);
  };

  const filteredMine = mine.filter((e) => {
    const q = searchQuery.toLowerCase();
    const matchesSearch = !q || e.description?.toLowerCase().includes(q) || e.expense_id?.toLowerCase().includes(q);
    const matchesStatus = statusFilter === 'all' || (e.status || '').toUpperCase() === statusFilter;
    const matchesCategory = categoryFilter === 'all' || e.category === categoryFilter;
    return matchesSearch && matchesStatus && matchesCategory;
  });

  // Summary derived from the user's own expenses.
  const totalAmt = mine.reduce((s, e) => s + (e.amount || 0), 0);
  const pendingCount = mine.filter((e) => (e.status || '').toUpperCase() === 'PENDING').length;
  const approvedCount = mine.filter((e) => ['APPROVED', 'SENT_TO_ACCOUNTANT', 'ENTERED'].includes((e.status || '').toUpperCase())).length;
  const byCategory = CATEGORIES.map((c) => ({
    ...c, amount: mine.filter((e) => e.category === c.value).reduce((s, e) => s + (e.amount || 0), 0),
  }));

  const StatusPill = ({ status }: { status: string }) => {
    const m = STATUS_META[(status || '').toUpperCase()] || STATUS_META.PENDING;
    return <span className={clsx('inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium', m.badge)}>{m.label}</span>;
  };

  if (isLoading && mine.length === 0) {
    return <div className="flex items-center justify-center h-96"><Loader2 className="w-8 h-8 text-bv-red-600 animate-spin" /></div>;
  }

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
        <Card label="Pending" value={String(pendingCount)} color="text-yellow-600" />
        <Card label="Approved+" value={String(approvedCount)} color="text-green-600" />
        <Card label={isAccountant ? 'For my entry' : 'For approval'} value={String(isAccountant ? toEnter.length : approvals.length)} color="text-blue-600" />
      </div>

      {/* Tabs */}
      <div className="flex gap-4 mb-6 border-b border-gray-200 overflow-x-auto">
        {([
          ['my', 'My Expenses'],
          ...(isApprover ? [['approvals', `Pending Approval${approvals.length ? ` (${approvals.length})` : ''}`]] : []),
          ...(isAccountant ? [['entry', `For Entry${toEnter.length ? ` (${toEnter.length})` : ''}`]] : []),
          ...(isAccountant ? [['aging', `Aging${aging?.total_count ? ` (${aging.total_count})` : ''}`]] : []),
          ['summary', 'Category Summary'],
        ] as [TabType, string][]).map(([tab, label]) => (
          <button key={tab} onClick={() => setActiveTab(tab)}
            className={clsx('px-4 py-3 font-medium whitespace-nowrap transition-colors border-b-2',
              activeTab === tab ? 'text-bv-red-600 border-bv-red-500' : 'text-gray-500 border-transparent hover:text-gray-700')}>
            {label}
          </button>
        ))}
      </div>

      {/* My Expenses */}
      {activeTab === 'my' && (
        <div className="bg-white rounded-lg border border-gray-200">
          <div className="p-4 border-b border-gray-200 grid grid-cols-1 md:grid-cols-3 gap-3">
            <div className="relative">
              <Search className="absolute left-3 top-2.5 w-4 h-4 text-gray-400" />
              <input placeholder="Search…" value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full pl-9 pr-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:border-bv-red-500" />
            </div>
            <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}
              className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm bg-white focus:outline-none focus:border-bv-red-500">
              <option value="all">All status</option>
              {Object.entries(STATUS_META).filter(([k]) => k !== 'DRAFT').map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
            </select>
            <select value={categoryFilter} onChange={(e) => setCategoryFilter(e.target.value)}
              className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm bg-white focus:outline-none focus:border-bv-red-500">
              <option value="all">All categories</option>
              {CATEGORIES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
            </select>
          </div>
          <ExpenseTable rows={filteredMine} showOwner={false}
            renderActions={(e) => (
              (e.status || '').toUpperCase() === 'APPROVED' && isApprover ? (
                <button className="text-blue-600 hover:underline text-xs inline-flex items-center gap-1"
                  onClick={() => doAction(() => expensesApi.sendToAccountant(e.expense_id), 'Sent to accountant')}>
                  <Send className="w-3.5 h-3.5" /> To accountant
                </button>
              ) : null
            )} />
        </div>
      )}

      {/* Pending Approval (approvers) */}
      {activeTab === 'approvals' && isApprover && (
        <div className="bg-white rounded-lg border border-gray-200">
          <ExpenseTable rows={approvals} showOwner
            empty="No expenses pending approval"
            renderActions={(e) => (
              <div className="flex items-center gap-2 justify-end">
                <button className="px-3 py-1 rounded-md bg-green-600 text-white text-xs inline-flex items-center gap-1 hover:bg-green-700"
                  onClick={() => doAction(() => expensesApi.approveExpense(e.expense_id), 'Approved')}>
                  <Check className="w-3.5 h-3.5" /> Approve
                </button>
                <button className="px-3 py-1 rounded-md bg-red-600 text-white text-xs inline-flex items-center gap-1 hover:bg-red-700"
                  onClick={() => { setSelected(e); setShowRejectModal(true); }}>
                  <XIcon className="w-3.5 h-3.5" /> Reject
                </button>
              </div>
            )} />
        </div>
      )}

      {/* For Entry (accountant) */}
      {activeTab === 'entry' && isAccountant && (
        <div className="bg-white rounded-lg border border-gray-200">
          <ExpenseTable rows={toEnter} showOwner
            empty="Nothing awaiting ledger entry"
            renderActions={(e) => (
              <button className="px-3 py-1 rounded-md bg-emerald-600 text-white text-xs inline-flex items-center gap-1 hover:bg-emerald-700"
                onClick={() => doAction(() => expensesApi.markEntered(e.expense_id), 'Marked as entered')}>
                <BookCheck className="w-3.5 h-3.5" /> Mark entered
              </button>
            )} />
        </div>
      )}

      {/* Reimbursement aging (accountant/admin) */}
      {activeTab === 'aging' && isAccountant && (
        <div className="space-y-6">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {(['0-7', '8-15', '15+'] as const).map((b) => {
              const bk = aging?.buckets?.[b];
              const tone = b === '15+' ? 'text-red-600' : b === '8-15' ? 'text-orange-600' : 'text-green-600';
              const label = b === '0-7' ? '0-7 days' : b === '8-15' ? '8-15 days' : 'Over 15 days';
              return (
                <div key={b} className="bg-white border border-gray-200 rounded-lg p-5">
                  <p className="text-gray-500 text-sm mb-1 flex items-center gap-1.5"><Clock className="w-4 h-4" /> {label}</p>
                  <p className={clsx('text-2xl font-bold', tone)}>{bk?.count ?? 0}</p>
                  <p className="text-xs text-gray-500 mt-1">{fc(bk?.amount ?? 0)} outstanding</p>
                </div>
              );
            })}
          </div>

          <div className="bg-white rounded-lg border border-gray-200">
            <div className="px-4 py-3 border-b border-gray-200 flex items-center justify-between">
              <h3 className="text-sm font-semibold text-gray-700">Pending reimbursements (approved, not yet entered)</h3>
              <span className="text-xs text-gray-500">{aging?.total_count ?? 0} item(s) · {fc(aging?.total_amount ?? 0)}</span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-gray-200 text-left text-xs font-semibold text-gray-500 uppercase">
                    <th className="px-4 py-3">By</th>
                    <th className="px-4 py-3">Category</th>
                    <th className="px-4 py-3 text-right">Amount</th>
                    <th className="px-4 py-3">Status</th>
                    <th className="px-4 py-3">Waiting since</th>
                    <th className="px-4 py-3 text-right">Days</th>
                    <th className="px-4 py-3">Bucket</th>
                  </tr>
                </thead>
                <tbody>
                  {(aging?.rows || []).map((r) => (
                    <tr key={r.expense_id} className="border-b border-gray-100 hover:bg-gray-50">
                      <td className="px-4 py-3 text-sm text-gray-700">{r.employee_name || r.employee_id || '—'}</td>
                      <td className="px-4 py-3 text-sm"><span className={clsx('inline-block px-2 py-0.5 rounded text-xs font-medium', catColor(r.category || ''))}>{catLabel(r.category || '')}</span></td>
                      <td className="px-4 py-3 text-sm font-semibold text-gray-900 text-right">{fc(r.amount)}</td>
                      <td className="px-4 py-3"><StatusPill status={r.status} /></td>
                      <td className="px-4 py-3 text-sm text-gray-500 whitespace-nowrap">{formatDateIST(r.since)}</td>
                      <td className="px-4 py-3 text-sm text-gray-700 text-right">{r.days_pending}</td>
                      <td className="px-4 py-3">
                        <span className={clsx('inline-block px-2 py-0.5 rounded-full text-xs font-medium',
                          r.bucket === '15+' ? 'bg-red-100 text-red-700' : r.bucket === '8-15' ? 'bg-orange-100 text-orange-700' : 'bg-green-100 text-green-700')}>
                          {r.bucket} days
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {(aging?.rows?.length ?? 0) === 0 && <div className="p-10 text-center text-gray-500 text-sm">No pending reimbursements</div>}
            </div>
          </div>
        </div>
      )}

      {/* Summary */}
      {activeTab === 'summary' && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <div className="bg-white rounded-lg border border-gray-200 p-6">
            <h3 className="text-lg font-semibold text-gray-900 mb-6 flex items-center gap-2"><BarChart3 className="w-5 h-5" /> Spending by category</h3>
            <div className="space-y-4">
              {byCategory.map((c) => {
                const pct = totalAmt > 0 ? (c.amount / totalAmt) * 100 : 0;
                return (
                  <div key={c.value}>
                    <div className="flex justify-between items-center mb-1">
                      <span className="text-sm font-medium text-gray-600">{c.label}</span>
                      <span className="text-sm font-semibold text-gray-900">{fc(c.amount)}</span>
                    </div>
                    <div className="w-full bg-gray-200 rounded-full h-2"><div className="bg-bv-red-500 h-2 rounded-full" style={{ width: `${pct}%` }} /></div>
                  </div>
                );
              })}
            </div>
          </div>
          <div className="bg-white rounded-lg border border-gray-200 p-6">
            <h3 className="text-lg font-semibold text-gray-900 mb-6 flex items-center gap-2"><Banknote className="w-5 h-5" /> Totals</h3>
            <div className="space-y-4">
              <Row label="My total expenses" value={fc(totalAmt)} />
              <Row label="Pending approval" value={String(pendingCount)} color="text-yellow-600" />
              <Row label="Approved or beyond" value={String(approvedCount)} color="text-green-600" />
            </div>
          </div>
        </div>
      )}

      {/* Add expense modal */}
      {showSubmitModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg border border-gray-200 max-w-md w-full max-h-[90vh] overflow-y-auto">
            <div className="border-b border-gray-200 px-6 py-4 flex items-center justify-between">
              <h2 className="text-lg font-semibold text-gray-900">Add expense</h2>
              <button onClick={() => setShowSubmitModal(false)} className="text-gray-500 hover:text-gray-700"><XIcon className="w-5 h-5" /></button>
            </div>
            <div className="p-6 space-y-4">
              <Labeled label="Type of expense">
                <select value={formCategory} onChange={(e) => setFormCategory(e.target.value)} className="input-field">
                  {CATEGORIES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
                </select>
              </Labeled>
              <Labeled label="Amount (₹)">
                <input type="number" value={formAmount} onChange={(e) => setFormAmount(e.target.value)} placeholder="0" className="input-field" />
              </Labeled>
              <Labeled label="Mode of payment">
                <select value={formPaymentMode} onChange={(e) => setFormPaymentMode(e.target.value)} className="input-field">
                  {PAYMENT_MODES.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
                </select>
              </Labeled>
              <Labeled label="Description">
                <textarea value={formDescription} onChange={(e) => setFormDescription(e.target.value)} rows={3} placeholder="What was this for?" className="input-field" />
              </Labeled>
              <Labeled label="Date">
                <input type="date" value={formDate} onChange={(e) => setFormDate(e.target.value)} className="input-field" />
              </Labeled>
              <Labeled label="Bill / receipt (optional)">
                <input type="file" accept="image/*,application/pdf" onChange={(e) => setFormBill(e.target.files?.[0] || null)}
                  className="block w-full text-sm text-gray-600 file:mr-3 file:py-1.5 file:px-3 file:rounded-md file:border-0 file:bg-gray-100 file:text-gray-700" />
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

      {/* Reject modal */}
      {showRejectModal && selected && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg border border-gray-200 max-w-md w-full">
            <div className="border-b border-gray-200 px-6 py-4 flex items-center justify-between">
              <h2 className="text-lg font-semibold text-gray-900">Reject expense</h2>
              <button onClick={() => { setShowRejectModal(false); setRejectionReason(''); }} className="text-gray-500 hover:text-gray-700"><XIcon className="w-5 h-5" /></button>
            </div>
            <div className="p-6 space-y-4">
              <p className="text-gray-600 text-sm"><strong>Expense:</strong> {selected.description} · {fc(selected.amount)}</p>
              <Labeled label="Reason for rejection">
                <textarea value={rejectionReason} onChange={(e) => setRejectionReason(e.target.value)} rows={3} placeholder="Why is this being rejected?" className="input-field" />
              </Labeled>
            </div>
            <div className="border-t border-gray-200 px-6 py-4 flex gap-3 justify-end">
              <button onClick={() => { setShowRejectModal(false); setRejectionReason(''); }} className="px-4 py-2 rounded-lg text-gray-600 hover:bg-gray-100">Cancel</button>
              <button onClick={handleReject} className="px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700">Reject</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );

  function ExpenseTable({ rows, showOwner, empty, renderActions }: {
    rows: ExpenseRecord[]; showOwner: boolean; empty?: string; renderActions?: (e: ExpenseRecord) => React.ReactNode;
  }) {
    return (
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="border-b border-gray-200 text-left text-xs font-semibold text-gray-500 uppercase">
              <th className="px-4 py-3">Date</th>
              {showOwner && <th className="px-4 py-3">By</th>}
              <th className="px-4 py-3">Category</th>
              <th className="px-4 py-3">Payment</th>
              <th className="px-4 py-3 text-right">Amount</th>
              <th className="px-4 py-3">Description</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3 text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((e) => (
              <tr key={e.expense_id} className="border-b border-gray-100 hover:bg-gray-50">
                <td className="px-4 py-3 text-sm text-gray-500 whitespace-nowrap">{formatDateIST(e.expense_date || e.created_at)}</td>
                {showOwner && <td className="px-4 py-3 text-sm text-gray-700">{e.employee_name || e.employee_id || '—'}</td>}
                <td className="px-4 py-3 text-sm"><span className={clsx('inline-block px-2 py-0.5 rounded text-xs font-medium', catColor(e.category))}>{catLabel(e.category)}</span></td>
                <td className="px-4 py-3 text-sm text-gray-600">{payLabel(e.payment_mode)}</td>
                <td className="px-4 py-3 text-sm font-semibold text-gray-900 text-right">{fc(e.amount)}</td>
                <td className="px-4 py-3 text-sm text-gray-600 max-w-xs truncate" title={e.description}>{e.description}</td>
                <td className="px-4 py-3"><StatusPill status={e.status} /></td>
                <td className="px-4 py-3 text-right">{renderActions?.(e)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 && <div className="p-10 text-center text-gray-500 text-sm">{empty || 'No expenses found'}</div>}
      </div>
    );
  }
}

function Card({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="bg-white border border-gray-200 rounded-lg p-5">
      <p className="text-gray-500 text-sm mb-1">{label}</p>
      <p className={clsx('text-2xl font-bold', color || 'text-gray-900')}>{value}</p>
    </div>
  );
}

function Row({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex justify-between items-center pb-3 border-b border-gray-100 last:border-0">
      <span className="text-gray-500">{label}</span>
      <span className={clsx('text-xl font-bold', color || 'text-gray-900')}>{value}</span>
    </div>
  );
}

function Labeled({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="block text-sm font-medium text-gray-600 mb-1">{label}</span>
      {children}
    </label>
  );
}

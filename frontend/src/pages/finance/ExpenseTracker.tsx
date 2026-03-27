// ============================================================================
// IMS 2.0 - Expense Tracking & Approval System
// ============================================================================
// Track store expenses with approval hierarchy and category breakdown

import { useState, useEffect } from 'react';
import {
  DollarSign,
  Plus,
  Search,
  CheckCircle,
  Clock,
  XCircle,
  TrendingUp,
  Eye,
  Check,
  X as XIcon,
  Loader2,
  BarChart3,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { expensesApi } from '../../services/api/expenses';
import { ExpenseBillUpload } from '../../components/finance/ExpenseBillUpload';
import clsx from 'clsx';

type TabType = 'my-expenses' | 'pending-approval' | 'summary';
type ExpenseStatus = 'pending' | 'approved' | 'rejected';
type ExpenseCategory = 'utilities' | 'maintenance' | 'supplies' | 'travel' | 'food' | 'marketing' | 'miscellaneous';

interface Expense {
  id: string;
  expense_id: string;
  category: ExpenseCategory;
  amount: number;
  description: string;
  submitted_at: string;
  status: ExpenseStatus;
  submitted_by: string;
  approved_by?: string;
  approved_at?: string;
  rejection_reason?: string;
  receipt_attached: boolean;
}

interface ExpenseSummary {
  total_this_month: number;
  pending_count: number;
  approved_count: number;
  average_daily: number;
  by_category: Record<ExpenseCategory, number>;
}

const CATEGORIES: { value: ExpenseCategory; label: string; color: string }[] = [
  { value: 'utilities', label: 'Utilities', color: 'bg-blue-100 text-blue-700' },
  { value: 'maintenance', label: 'Maintenance', color: 'bg-orange-100 text-orange-700' },
  { value: 'supplies', label: 'Supplies', color: 'bg-green-100 text-green-700' },
  { value: 'travel', label: 'Travel', color: 'bg-purple-100 text-purple-700' },
  { value: 'food', label: 'Food & Beverage', color: 'bg-red-100 text-red-700' },
  { value: 'marketing', label: 'Marketing', color: 'bg-pink-100 text-pink-700' },
  { value: 'miscellaneous', label: 'Miscellaneous', color: 'bg-gray-100 text-gray-700' },
];

export default function ExpenseTracker() {
  const { user } = useAuth();
  const toast = useToast();

  const [activeTab, setActiveTab] = useState<TabType>('my-expenses');
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<ExpenseStatus | 'all'>('all');
  const [categoryFilter, setCategoryFilter] = useState<ExpenseCategory | 'all'>('all');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');

  const [expenses, setExpenses] = useState<Expense[]>([]);
  const [summary, setSummary] = useState<ExpenseSummary | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  // Modal states
  const [showSubmitModal, setShowSubmitModal] = useState(false);
  const [showRejectModal, setShowRejectModal] = useState(false);
  const [selectedExpense, setSelectedExpense] = useState<Expense | null>(null);
  const [rejectionReason, setRejectionReason] = useState('');

  // Form states
  const [formCategory, setFormCategory] = useState<ExpenseCategory>('utilities');
  const [formAmount, setFormAmount] = useState('');
  const [formDescription, setFormDescription] = useState('');
  const [formDate, setFormDate] = useState(new Date().toISOString().split('T')[0]);

  useEffect(() => {
    loadExpenses();
    loadSummary();
  }, [activeTab, statusFilter, dateFrom, dateTo]);

  const loadExpenses = async () => {
    setIsLoading(true);
    try {
      const response = await expensesApi.getExpenses({
        store_id: user?.activeStoreId,
        status: statusFilter !== 'all' ? statusFilter : undefined,
        from_date: dateFrom || undefined,
        to_date: dateTo || undefined,
      });
      const list = response?.expenses || response || [];
      setExpenses(Array.isArray(list) ? list : []);
    } catch (error) {
      toast.error('Failed to load expenses');
      setExpenses([]);
    } finally {
      setIsLoading(false);
    }
  };

  const loadSummary = async () => {
    try {
      const response = await expensesApi.getExpenses({ store_id: user?.activeStoreId });
      const list = response?.expenses || response || [];
      const expenseList: Expense[] = Array.isArray(list) ? list : [];
      const byCategory: Record<ExpenseCategory, number> = {
        utilities: 0, maintenance: 0, supplies: 0, travel: 0, food: 0, marketing: 0, miscellaneous: 0,
      };
      let total = 0;
      let pendingCount = 0;
      let approvedCount = 0;
      expenseList.forEach(e => {
        total += e.amount || 0;
        if (e.status === 'pending') pendingCount++;
        if (e.status === 'approved') approvedCount++;
        if (byCategory[e.category as ExpenseCategory] !== undefined) {
          byCategory[e.category as ExpenseCategory] += e.amount || 0;
        }
      });
      setSummary({
        total_this_month: total,
        pending_count: pendingCount,
        approved_count: approvedCount,
        average_daily: expenseList.length > 0 ? Math.round(total / 30) : 0,
        by_category: byCategory,
      });
    } catch (error) {
      toast.error('Failed to load summary');
    }
  };

  const handleSubmitExpense = async () => {
    if (!formAmount || !formDescription) {
      toast.error('Please fill in all fields');
      return;
    }

    try {
      await expensesApi.createExpense({
        category: formCategory,
        amount: parseFloat(formAmount),
        description: formDescription,
        expense_date: formDate,
        store_id: user?.activeStoreId,
      });
      toast.success('Expense submitted successfully');
      setShowSubmitModal(false);
      setFormAmount('');
      setFormDescription('');
      setFormCategory('utilities');
      await loadExpenses();
      await loadSummary();
    } catch (error) {
      toast.error('Failed to submit expense');
    }
  };

  const handleApproveExpense = async (expenseId: string) => {
    try {
      await expensesApi.approveExpense(expenseId);
      toast.success('Expense approved');
      await loadExpenses();
      await loadSummary();
    } catch (error) {
      toast.error('Failed to approve expense');
    }
  };

  const handleRejectExpense = async () => {
    if (!rejectionReason) {
      toast.error('Please provide a rejection reason');
      return;
    }

    try {
      if (selectedExpense) {
        await expensesApi.rejectExpense(selectedExpense.id, rejectionReason);
      }
      toast.success('Expense rejected');
      setShowRejectModal(false);
      setRejectionReason('');
      setSelectedExpense(null);
      await loadExpenses();
      await loadSummary();
    } catch (error) {
      toast.error('Failed to reject expense');
    }
  };

  const filteredExpenses = expenses.filter((expense) => {
    const matchesSearch =
      expense.description.toLowerCase().includes(searchQuery.toLowerCase()) ||
      expense.expense_id.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesStatus = statusFilter === 'all' || expense.status === statusFilter;
    const matchesCategory = categoryFilter === 'all' || expense.category === categoryFilter;

    return matchesSearch && matchesStatus && matchesCategory;
  });

  const getStatusBadge = (status: ExpenseStatus) => {
    const badges = {
      pending: 'bg-yellow-100 text-yellow-700',
      approved: 'bg-green-100 text-green-700',
      rejected: 'bg-red-100 text-red-700',
    };
    return badges[status];
  };

  const getStatusIcon = (status: ExpenseStatus) => {
    const icons = {
      pending: <Clock className="w-4 h-4" />,
      approved: <CheckCircle className="w-4 h-4" />,
      rejected: <XCircle className="w-4 h-4" />,
    };
    return icons[status];
  };

  if (isLoading && !summary) {
    return (
      <div className="flex items-center justify-center h-96">
        <Loader2 className="w-8 h-8 text-blue-500 animate-spin" />
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50 p-6">
      {/* Header */}
      <div className="mb-8">
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-3">
            <div className="w-12 h-12 bg-blue-600 rounded-lg flex items-center justify-center">
              <DollarSign className="w-6 h-6 text-white" />
            </div>
            <div>
              <h1 className="text-3xl font-bold text-gray-900">Expense Tracking</h1>
              <p className="text-gray-500">Manage and approve store expenses</p>
            </div>
          </div>
          <button
            onClick={() => setShowSubmitModal(true)}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
          >
            <Plus className="w-5 h-5" />
            Submit Expense
          </button>
        </div>

        {/* Summary Cards */}
        {summary && (
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            <div className="bg-white border border-gray-200 rounded-lg p-6">
              <p className="text-gray-500 text-sm mb-1">Total (This Month)</p>
              <p className="text-3xl font-bold text-gray-900">₹{summary.total_this_month.toLocaleString()}</p>
            </div>
            <div className="bg-white border border-gray-200 rounded-lg p-6">
              <p className="text-gray-500 text-sm mb-1">Pending Approval</p>
              <p className="text-3xl font-bold text-yellow-400">{summary.pending_count}</p>
            </div>
            <div className="bg-white border border-gray-200 rounded-lg p-6">
              <p className="text-gray-500 text-sm mb-1">Approved</p>
              <p className="text-3xl font-bold text-green-400">{summary.approved_count}</p>
            </div>
            <div className="bg-white border border-gray-200 rounded-lg p-6">
              <p className="text-gray-500 text-sm mb-1">Average Daily</p>
              <p className="text-3xl font-bold text-blue-400">₹{summary.average_daily.toLocaleString()}</p>
            </div>
          </div>
        )}
      </div>

      {/* Tabs */}
      <div className="flex gap-4 mb-6 border-b border-gray-200">
        {(['my-expenses', 'pending-approval', 'summary'] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={clsx(
              'px-4 py-3 font-medium transition-colors border-b-2',
              activeTab === tab
                ? 'text-blue-400 border-blue-400'
                : 'text-gray-500 border-transparent hover:text-gray-700'
            )}
          >
            {tab === 'my-expenses' && 'My Expenses'}
            {tab === 'pending-approval' && 'Pending Approval'}
            {tab === 'summary' && 'Category Summary'}
          </button>
        ))}
      </div>

      {/* Content */}
      {activeTab === 'my-expenses' && (
        <div className="bg-white rounded-lg border border-gray-200">
          {/* Filters */}
          <div className="p-6 border-b border-gray-200">
            <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-600 mb-2">Search</label>
                <div className="relative">
                  <Search className="absolute left-3 top-3 w-5 h-5 text-gray-500" />
                  <input
                    type="text"
                    placeholder="Search expenses..."
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    className="w-full pl-10 pr-4 py-2 bg-white border border-gray-200 rounded-lg text-gray-900 placeholder-gray-500 focus:outline-none focus:border-blue-500"
                  />
                </div>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-600 mb-2">Status</label>
                <select
                  value={statusFilter}
                  onChange={(e) => setStatusFilter(e.target.value as any)}
                  className="w-full px-4 py-2 bg-white border border-gray-200 rounded-lg text-gray-900 focus:outline-none focus:border-blue-500"
                >
                  <option value="all">All Status</option>
                  <option value="pending">Pending</option>
                  <option value="approved">Approved</option>
                  <option value="rejected">Rejected</option>
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-600 mb-2">Category</label>
                <select
                  value={categoryFilter}
                  onChange={(e) => setCategoryFilter(e.target.value as any)}
                  className="w-full px-4 py-2 bg-white border border-gray-200 rounded-lg text-gray-900 focus:outline-none focus:border-blue-500"
                >
                  <option value="all">All Categories</option>
                  {CATEGORIES.map((cat) => (
                    <option key={cat.value} value={cat.value}>
                      {cat.label}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-600 mb-2">From</label>
                <input
                  type="date"
                  value={dateFrom}
                  onChange={(e) => setDateFrom(e.target.value)}
                  className="w-full px-4 py-2 bg-white border border-gray-200 rounded-lg text-gray-900 focus:outline-none focus:border-blue-500"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-600 mb-2">To</label>
                <input
                  type="date"
                  value={dateTo}
                  onChange={(e) => setDateTo(e.target.value)}
                  className="w-full px-4 py-2 bg-white border border-gray-200 rounded-lg text-gray-900 focus:outline-none focus:border-blue-500"
                />
              </div>
            </div>
          </div>

          {/* Expenses List */}
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-gray-200">
                  <th className="px-6 py-3 text-left text-sm font-semibold text-gray-600">ID</th>
                  <th className="px-6 py-3 text-left text-sm font-semibold text-gray-600">Category</th>
                  <th className="px-6 py-3 text-left text-sm font-semibold text-gray-600">Amount</th>
                  <th className="px-6 py-3 text-left text-sm font-semibold text-gray-600">Description</th>
                  <th className="px-6 py-3 text-left text-sm font-semibold text-gray-600">Status</th>
                  <th className="px-6 py-3 text-left text-sm font-semibold text-gray-600">Date</th>
                  <th className="px-6 py-3 text-left text-sm font-semibold text-gray-600">Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredExpenses.map((expense) => (
                  <tr key={expense.id} className="border-b border-gray-200 hover:bg-gray-50 transition-colors">
                    <td className="px-6 py-4 text-sm text-gray-600">{expense.expense_id}</td>
                    <td className="px-6 py-4 text-sm">
                      {CATEGORIES.find((c) => c.value === expense.category) && (
                        <span
                          className={clsx(
                            'inline-block px-2 py-1 rounded text-xs font-medium',
                            CATEGORIES.find((c) => c.value === expense.category)?.color
                          )}
                        >
                          {CATEGORIES.find((c) => c.value === expense.category)?.label}
                        </span>
                      )}
                    </td>
                    <td className="px-6 py-4 text-sm font-semibold text-gray-900">₹{expense.amount.toLocaleString()}</td>
                    <td className="px-6 py-4 text-sm text-gray-600 max-w-xs truncate">{expense.description}</td>
                    <td className="px-6 py-4 text-sm">
                      <span className={clsx('inline-flex items-center gap-2 px-2 py-1 rounded-full text-xs font-medium', getStatusBadge(expense.status))}>
                        {getStatusIcon(expense.status)}
                        {expense.status.charAt(0).toUpperCase() + expense.status.slice(1)}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-500">
                      {new Date(expense.submitted_at).toLocaleDateString()}
                    </td>
                    <td className="px-6 py-4 text-sm">
                      <button
                        onClick={() => setSelectedExpense(expense)}
                        className="text-blue-400 hover:text-blue-300 transition-colors"
                        title="View details"
                      >
                        <Eye className="w-4 h-4" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {filteredExpenses.length === 0 && (
            <div className="p-12 text-center">
              <p className="text-gray-500">No expenses found</p>
            </div>
          )}
        </div>
      )}

      {activeTab === 'pending-approval' && (
        <div className="bg-white rounded-lg border border-gray-200 p-6">
          <div className="space-y-4">
            {expenses.filter((e) => e.status === 'pending').map((expense) => (
              <div key={expense.id} className="bg-gray-50 rounded-lg p-4 border border-gray-200">
                <div className="flex justify-between items-start mb-4">
                  <div>
                    <p className="font-semibold text-gray-900">{expense.description}</p>
                    <p className="text-sm text-gray-500">{expense.expense_id}</p>
                  </div>
                  <span className="text-2xl font-bold text-blue-400">₹{expense.amount.toLocaleString()}</span>
                </div>
                <div className="flex gap-4">
                  <button
                    onClick={() => handleApproveExpense(expense.id)}
                    className="flex-1 flex items-center justify-center gap-2 px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 transition-colors"
                  >
                    <Check className="w-4 h-4" />
                    Approve
                  </button>
                  <button
                    onClick={() => {
                      setSelectedExpense(expense);
                      setShowRejectModal(true);
                    }}
                    className="flex-1 flex items-center justify-center gap-2 px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 transition-colors"
                  >
                    <XIcon className="w-4 h-4" />
                    Reject
                  </button>
                </div>
              </div>
            ))}
            {expenses.filter((e) => e.status === 'pending').length === 0 && (
              <p className="text-gray-500 text-center py-8">No pending expenses for approval</p>
            )}
          </div>
        </div>
      )}

      {activeTab === 'summary' && summary && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {/* Category breakdown chart */}
          <div className="bg-white rounded-lg border border-gray-200 p-6">
            <h3 className="text-lg font-semibold text-gray-900 mb-6 flex items-center gap-2">
              <BarChart3 className="w-5 h-5" />
              Spending by Category
            </h3>
            <div className="space-y-4">
              {CATEGORIES.map((cat) => {
                const amount = summary.by_category[cat.value] || 0;
                const percentage = summary.total_this_month > 0 ? (amount / summary.total_this_month) * 100 : 0;
                return (
                  <div key={cat.value}>
                    <div className="flex justify-between items-center mb-2">
                      <span className="text-sm font-medium text-gray-600">{cat.label}</span>
                      <span className="text-sm font-semibold text-gray-900">₹{amount.toLocaleString()}</span>
                    </div>
                    <div className="w-full bg-gray-200 rounded-full h-2">
                      <div
                        className="bg-blue-500 h-2 rounded-full transition-all"
                        style={{ width: `${percentage}%` }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Summary stats */}
          <div className="bg-white rounded-lg border border-gray-200 p-6">
            <h3 className="text-lg font-semibold text-gray-900 mb-6 flex items-center gap-2">
              <TrendingUp className="w-5 h-5" />
              Summary Statistics
            </h3>
            <div className="space-y-4">
              <div className="flex justify-between items-center pb-4 border-b border-gray-200">
                <span className="text-gray-500">Total Expenses</span>
                <span className="text-2xl font-bold text-gray-900">₹{summary.total_this_month.toLocaleString()}</span>
              </div>
              <div className="flex justify-between items-center pb-4 border-b border-gray-200">
                <span className="text-gray-500">Pending Approval</span>
                <span className="text-2xl font-bold text-yellow-400">{summary.pending_count}</span>
              </div>
              <div className="flex justify-between items-center pb-4 border-b border-gray-200">
                <span className="text-gray-500">Approved</span>
                <span className="text-2xl font-bold text-green-400">{summary.approved_count}</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-gray-500">Daily Average</span>
                <span className="text-2xl font-bold text-blue-400">₹{summary.average_daily.toLocaleString()}</span>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Submit Expense Modal */}
      {showSubmitModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg border border-gray-200 max-w-md w-full">
            <div className="border-b border-gray-200 px-6 py-4 flex items-center justify-between">
              <h2 className="text-lg font-semibold text-gray-900">Submit Expense</h2>
              <button
                onClick={() => setShowSubmitModal(false)}
                className="text-gray-500 hover:text-gray-700 transition-colors"
              >
                <XIcon className="w-5 h-5" />
              </button>
            </div>
            <div className="p-6 space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-600 mb-2">Category</label>
                <select
                  value={formCategory}
                  onChange={(e) => setFormCategory(e.target.value as ExpenseCategory)}
                  className="w-full px-4 py-2 bg-white border border-gray-200 rounded-lg text-gray-900 focus:outline-none focus:border-blue-500"
                >
                  {CATEGORIES.map((cat) => (
                    <option key={cat.value} value={cat.value}>
                      {cat.label}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-600 mb-2">Amount (₹)</label>
                <input
                  type="number"
                  value={formAmount}
                  onChange={(e) => setFormAmount(e.target.value)}
                  placeholder="0.00"
                  className="w-full px-4 py-2 bg-white border border-gray-200 rounded-lg text-gray-900 placeholder-gray-500 focus:outline-none focus:border-blue-500"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-600 mb-2">Description</label>
                <textarea
                  value={formDescription}
                  onChange={(e) => setFormDescription(e.target.value)}
                  placeholder="Enter expense details..."
                  rows={3}
                  className="w-full px-4 py-2 bg-white border border-gray-200 rounded-lg text-gray-900 placeholder-gray-500 focus:outline-none focus:border-blue-500"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-600 mb-2">Date</label>
                <input
                  type="date"
                  value={formDate}
                  onChange={(e) => setFormDate(e.target.value)}
                  className="w-full px-4 py-2 bg-white border border-gray-200 rounded-lg text-gray-900 focus:outline-none focus:border-blue-500"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-600 mb-2">Attach Bill / Receipt</label>
                <ExpenseBillUpload
                  onBillUpload={(_file, _hash) => {
                    toast.success('Bill attached successfully');
                  }}
                />
              </div>
            </div>
            <div className="border-t border-gray-200 px-6 py-4 flex gap-3 justify-end">
              <button
                onClick={() => setShowSubmitModal(false)}
                className="px-4 py-2 rounded-lg text-gray-600 hover:bg-gray-100 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleSubmitExpense}
                className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
              >
                Submit
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Reject Expense Modal */}
      {showRejectModal && selectedExpense && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg border border-gray-200 max-w-md w-full">
            <div className="border-b border-gray-200 px-6 py-4 flex items-center justify-between">
              <h2 className="text-lg font-semibold text-gray-900">Reject Expense</h2>
              <button
                onClick={() => {
                  setShowRejectModal(false);
                  setRejectionReason('');
                }}
                className="text-gray-500 hover:text-gray-700 transition-colors"
              >
                <XIcon className="w-5 h-5" />
              </button>
            </div>
            <div className="p-6 space-y-4">
              <p className="text-gray-600">
                <strong>Expense:</strong> {selectedExpense.description}
              </p>
              <div>
                <label className="block text-sm font-medium text-gray-600 mb-2">Reason for Rejection</label>
                <textarea
                  value={rejectionReason}
                  onChange={(e) => setRejectionReason(e.target.value)}
                  placeholder="Explain why this expense is being rejected..."
                  rows={3}
                  className="w-full px-4 py-2 bg-white border border-gray-200 rounded-lg text-gray-900 placeholder-gray-500 focus:outline-none focus:border-blue-500"
                />
              </div>
            </div>
            <div className="border-t border-gray-200 px-6 py-4 flex gap-3 justify-end">
              <button
                onClick={() => {
                  setShowRejectModal(false);
                  setRejectionReason('');
                }}
                className="px-4 py-2 rounded-lg text-gray-600 hover:bg-gray-100 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleRejectExpense}
                className="px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 transition-colors"
              >
                Reject
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ============================================================================
// IMS 2.0 - Financial Management
// ============================================================================
// Manage invoices, payments, expenses, and financial reporting

import { useState } from 'react';
import { Plus, Search, Edit2, Trash2, DollarSign, TrendingUp, FileText } from 'lucide-react';
import clsx from 'clsx';

export interface Invoice {
  id: string;
  invoiceNumber: string;
  customerId: string;
  customerName: string;
  issueDate: string;
  dueDate: string;
  items: InvoiceItem[];
  subtotal: number;
  tax: number;
  total: number;
  amountPaid: number;
  status: 'draft' | 'sent' | 'partially-paid' | 'paid' | 'overdue' | 'cancelled';
  notes?: string;
  createdAt: string;
}

export interface InvoiceItem {
  id: string;
  description: string;
  quantity: number;
  unitPrice: number;
  total: number;
}

export interface Expense {
  id: string;
  category: string;
  description: string;
  amount: number;
  date: string;
  vendor?: string;
  paymentMethod: 'cash' | 'card' | 'check' | 'bank-transfer';
  status: 'pending' | 'approved' | 'paid';
  approvedBy?: string;
  createdAt: string;
}

interface FinancialManagementProps {
  invoices: Invoice[];
  expenses: Expense[];
  onCreateInvoice: (invoice: Omit<Invoice, 'id' | 'createdAt'>) => Promise<void>;
  onUpdateInvoice: (invoice: Invoice) => Promise<void>;
  onDeleteInvoice: (id: string) => Promise<void>;
  onRecordPayment: (invoiceId: string, amount: number) => Promise<void>;
  onCreateExpense: (expense: Omit<Expense, 'id' | 'createdAt'>) => Promise<void>;
  onApproveExpense: (id: string, approvedBy: string) => Promise<void>;
  loading?: boolean;
}

type TabType = 'invoices' | 'expenses';

export function FinancialManagement({
  invoices,
  expenses,
  onCreateInvoice,
  onUpdateInvoice,
  onDeleteInvoice,
  onRecordPayment,
  onCreateExpense,
  onApproveExpense,
  loading = false,
}: FinancialManagementProps) {
  const [activeTab, setActiveTab] = useState<TabType>('invoices');
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [formData, setFormData] = useState<any>({});

  const filteredInvoices = invoices.filter(i =>
    i.invoiceNumber.toLowerCase().includes(searchTerm.toLowerCase()) ||
    i.customerName.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const filteredExpenses = expenses.filter(e =>
    e.description.toLowerCase().includes(searchTerm.toLowerCase()) ||
    e.category.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const totalRevenue = invoices.filter(i => i.status === 'paid').reduce((sum, i) => sum + i.total, 0);
  const totalExpenses = expenses.filter(e => e.status === 'paid').reduce((sum, e) => sum + e.amount, 0);
  const totalOutstanding = invoices.filter(i => ['partially-paid', 'overdue', 'sent'].includes(i.status)).reduce((sum, i) => sum + (i.total - i.amountPaid), 0);

  const getInvoiceStatusColor = (status: string) => {
    switch (status) {
      case 'draft':
        return 'bg-gray-100 text-gray-700';
      case 'sent':
        return 'bg-blue-100 text-blue-700';
      case 'partially-paid':
        return 'bg-yellow-100 text-yellow-700';
      case 'paid':
        return 'bg-green-100 text-green-700';
      case 'overdue':
        return 'bg-red-100 text-red-700';
      case 'cancelled':
        return 'bg-gray-100 text-gray-700';
      default:
        return 'bg-gray-100 text-gray-700';
    }
  };

  const getExpenseStatusColor = (status: string) => {
    switch (status) {
      case 'pending':
        return 'bg-yellow-100 text-yellow-700';
      case 'approved':
        return 'bg-blue-100 text-blue-700';
      case 'paid':
        return 'bg-green-100 text-green-700';
      default:
        return 'bg-gray-100 text-gray-700';
    }
  };

  return (
    <div className="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-800">
      {/* Financial Summary */}
      <div className="p-6 border-b border-gray-200 dark:border-gray-800 grid grid-cols-3 gap-4">
        <div className="bg-green-50 dark:bg-green-900/20 p-4 rounded-lg border border-green-200 dark:border-green-800">
          <p className="text-sm font-medium text-gray-600 dark:text-gray-400 mb-1">Total Revenue</p>
          <p className="text-2xl font-bold text-green-600 dark:text-green-400">${totalRevenue.toFixed(2)}</p>
        </div>
        <div className="bg-red-50 dark:bg-red-900/20 p-4 rounded-lg border border-red-200 dark:border-red-800">
          <p className="text-sm font-medium text-gray-600 dark:text-gray-400 mb-1">Total Expenses</p>
          <p className="text-2xl font-bold text-red-600 dark:text-red-400">${totalExpenses.toFixed(2)}</p>
        </div>
        <div className="bg-blue-50 dark:bg-blue-900/20 p-4 rounded-lg border border-blue-200 dark:border-blue-800">
          <p className="text-sm font-medium text-gray-600 dark:text-gray-400 mb-1">Outstanding</p>
          <p className="text-2xl font-bold text-blue-600 dark:text-blue-400">${totalOutstanding.toFixed(2)}</p>
        </div>
      </div>

      {/* Header & Tabs */}
      <div className="p-6 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between mb-4">
        <h2 className="text-lg font-bold text-gray-900 dark:text-white flex items-center gap-2">
          <DollarSign className="w-5 h-5" />
          Financial Management
        </h2>
        <button
          onClick={() => {
            setEditingId(null);
            setFormData(activeTab === 'invoices' ? { items: [], status: 'draft' } : { status: 'pending' });
            setShowCreateModal(true);
          }}
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 font-medium"
        >
          <Plus className="w-4 h-4" />
          {activeTab === 'invoices' ? 'New Invoice' : 'New Expense'}
        </button>
      </div>

      {/* Tabs */}
      <div className="border-b border-gray-200 dark:border-gray-800 flex px-6">
        <button
          onClick={() => setActiveTab('invoices')}
          className={clsx(
            'px-6 py-3 font-medium border-b-2 transition-colors',
            activeTab === 'invoices'
              ? 'border-blue-600 text-blue-600 dark:text-blue-400'
              : 'border-transparent text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white'
          )}
        >
          Invoices ({invoices.length})
        </button>
        <button
          onClick={() => setActiveTab('expenses')}
          className={clsx(
            'px-6 py-3 font-medium border-b-2 transition-colors',
            activeTab === 'expenses'
              ? 'border-blue-600 text-blue-600 dark:text-blue-400'
              : 'border-transparent text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white'
          )}
        >
          Expenses ({expenses.length})
        </button>
      </div>

      {/* Search */}
      <div className="p-4 border-b border-gray-200 dark:border-gray-800">
        <div className="relative">
          <Search className="absolute left-3 top-3 w-4 h-4 text-gray-400" />
          <input
            type="text"
            placeholder={activeTab === 'invoices' ? 'Search invoices...' : 'Search expenses...'}
            value={searchTerm}
            onChange={e => setSearchTerm(e.target.value)}
            className="w-full pl-10 pr-4 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
          />
        </div>
      </div>

      {/* Content */}
      <div className="divide-y divide-gray-200 dark:divide-gray-800">
        {loading ? (
          <div className="p-8 text-center text-gray-500 dark:text-gray-400">
            <p>Loading...</p>
          </div>
        ) : activeTab === 'invoices' ? (
          filteredInvoices.length === 0 ? (
            <div className="p-8 text-center text-gray-500 dark:text-gray-400">
              <FileText className="w-12 h-12 mx-auto mb-3 opacity-50" />
              <p>No invoices found</p>
            </div>
          ) : (
            filteredInvoices.map(invoice => (
              <div key={invoice.id} className="p-4 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors">
                <div className="flex items-start justify-between gap-4 mb-2">
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-1">
                      <h3 className="font-semibold text-gray-900 dark:text-white">{invoice.invoiceNumber}</h3>
                      <span className={clsx('px-2 py-1 rounded text-xs font-medium', getInvoiceStatusColor(invoice.status))}>
                        {invoice.status}
                      </span>
                    </div>
                    <p className="text-sm text-gray-600 dark:text-gray-400">{invoice.customerName}</p>
                  </div>
                  <div className="text-right">
                    <p className="font-semibold text-gray-900 dark:text-white">${invoice.total.toFixed(2)}</p>
                    <p className="text-xs text-gray-500 dark:text-gray-400">Due: {new Date(invoice.dueDate).toLocaleDateString()}</p>
                  </div>
                </div>

                <div className="flex items-center gap-2 mt-3 flex-wrap">
                  {invoice.status !== 'paid' && invoice.status !== 'cancelled' && (
                    <button
                      onClick={() => onRecordPayment(invoice.id, invoice.total - invoice.amountPaid)}
                      className="px-3 py-1 bg-green-600 text-white rounded text-sm hover:bg-green-700 font-medium"
                    >
                      Record Payment
                    </button>
                  )}
                  <button
                    onClick={() => {
                      setFormData(invoice);
                      setEditingId(invoice.id);
                      setShowCreateModal(true);
                    }}
                    className="p-2 hover:bg-amber-100 dark:hover:bg-amber-900/20 rounded-lg text-amber-600 dark:text-amber-400"
                    title="Edit"
                  >
                    <Edit2 className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => {
                      if (confirm(`Delete invoice ${invoice.invoiceNumber}?`)) {
                        onDeleteInvoice(invoice.id);
                      }
                    }}
                    className="p-2 hover:bg-red-100 dark:hover:bg-red-900/20 rounded-lg text-red-600 dark:text-red-400"
                    title="Delete"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
            ))
          )
        ) : (
          filteredExpenses.length === 0 ? (
            <div className="p-8 text-center text-gray-500 dark:text-gray-400">
              <TrendingUp className="w-12 h-12 mx-auto mb-3 opacity-50" />
              <p>No expenses found</p>
            </div>
          ) : (
            filteredExpenses.map(expense => (
              <div key={expense.id} className="p-4 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors">
                <div className="flex items-start justify-between gap-4 mb-2">
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-1">
                      <h3 className="font-semibold text-gray-900 dark:text-white">{expense.description}</h3>
                      <span className={clsx('px-2 py-1 rounded text-xs font-medium', getExpenseStatusColor(expense.status))}>
                        {expense.status}
                      </span>
                    </div>
                    <p className="text-sm text-gray-600 dark:text-gray-400">{expense.category}{expense.vendor ? ` • ${expense.vendor}` : ''}</p>
                  </div>
                  <div className="text-right">
                    <p className="font-semibold text-gray-900 dark:text-white">${expense.amount.toFixed(2)}</p>
                    <p className="text-xs text-gray-500 dark:text-gray-400">{new Date(expense.date).toLocaleDateString()}</p>
                  </div>
                </div>

                <div className="flex items-center gap-2 mt-3 flex-wrap">
                  {expense.status === 'pending' && (
                    <button
                      onClick={() => onApproveExpense(expense.id, 'current_user')}
                      className="px-3 py-1 bg-blue-600 text-white rounded text-sm hover:bg-blue-700 font-medium"
                    >
                      Approve
                    </button>
                  )}
                  <button
                    onClick={() => {
                      if (confirm(`Delete expense?`)) {
                        // Would call onDeleteExpense if it existed
                      }
                    }}
                    className="p-2 hover:bg-red-100 dark:hover:bg-red-900/20 rounded-lg text-red-600 dark:text-red-400"
                    title="Delete"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
            ))
          )
        )}
      </div>

      {/* Create/Edit Modal */}
      {showCreateModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50" onClick={() => setShowCreateModal(false)}>
          <div className="bg-white dark:bg-gray-900 rounded-lg shadow-lg p-6 max-w-2xl w-full max-h-96 overflow-y-auto" onClick={e => e.stopPropagation()}>
            <h2 className="text-lg font-bold text-gray-900 dark:text-white mb-4">
              {editingId ? 'Edit' : 'Create New'} {activeTab === 'invoices' ? 'Invoice' : 'Expense'}
            </h2>

            {activeTab === 'invoices' ? (
              <div className="space-y-4">
                <input
                  type="text"
                  placeholder="Invoice Number *"
                  value={formData.invoiceNumber || ''}
                  onChange={e => setFormData({ ...formData, invoiceNumber: e.target.value })}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                />
                <input
                  type="text"
                  placeholder="Customer Name *"
                  value={formData.customerName || ''}
                  onChange={e => setFormData({ ...formData, customerName: e.target.value })}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                />
                <div className="grid grid-cols-2 gap-2">
                  <input
                    type="date"
                    value={formData.issueDate || ''}
                    onChange={e => setFormData({ ...formData, issueDate: e.target.value })}
                    className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                  />
                  <input
                    type="date"
                    value={formData.dueDate || ''}
                    onChange={e => setFormData({ ...formData, dueDate: e.target.value })}
                    className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                  />
                </div>
              </div>
            ) : (
              <div className="space-y-4">
                <input
                  type="text"
                  placeholder="Description *"
                  value={formData.description || ''}
                  onChange={e => setFormData({ ...formData, description: e.target.value })}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                />
                <input
                  type="text"
                  placeholder="Category *"
                  value={formData.category || ''}
                  onChange={e => setFormData({ ...formData, category: e.target.value })}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                />
                <div className="grid grid-cols-2 gap-2">
                  <input
                    type="number"
                    step="0.01"
                    placeholder="Amount *"
                    value={formData.amount || ''}
                    onChange={e => setFormData({ ...formData, amount: parseFloat(e.target.value) })}
                    className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                  />
                  <input
                    type="date"
                    value={formData.date || ''}
                    onChange={e => setFormData({ ...formData, date: e.target.value })}
                    className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                  />
                </div>
              </div>
            )}

            <div className="flex gap-2 mt-6">
              <button
                onClick={() => setShowCreateModal(false)}
                className="flex-1 px-4 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800"
              >
                Cancel
              </button>
              <button
                onClick={() => {
                  if (activeTab === 'invoices') {
                    if (editingId) onUpdateInvoice({ ...formData, id: editingId, createdAt: formData.createdAt } as Invoice);
                    else onCreateInvoice({ ...formData, createdAt: new Date().toISOString() } as any);
                  } else {
                    onCreateExpense({ ...formData, createdAt: new Date().toISOString() } as any);
                  }
                  setShowCreateModal(false);
                }}
                className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
              >
                Save
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

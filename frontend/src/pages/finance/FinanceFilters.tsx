// ============================================================================
// IMS 2.0 - Finance Dashboard Header & Filters
// ============================================================================

import {
  BarChart3,
  Download,
  TrendingUp,
  TrendingDown,
  Calendar,
  Percent,
  CreditCard,
  Target,
  Building2,
  Scale,
} from 'lucide-react';
import clsx from 'clsx';
import type { TabType } from './financeTypes';

interface FinanceFiltersProps {
  selectedYear: string;
  onYearChange: (year: string) => void;
  dateFrom: string;
  dateTo: string;
  onDateFromChange: (date: string) => void;
  onDateToChange: (date: string) => void;
  activeTab: TabType;
  onTabChange: (tab: TabType) => void;
}

const TABS: { id: TabType; label: string; icon: typeof TrendingUp }[] = [
  { id: 'revenue-pl', label: 'Revenue & P&L', icon: TrendingUp },
  { id: 'gst', label: 'GST Management', icon: Percent },
  { id: 'outstanding', label: 'Outstanding & Collections', icon: CreditCard },
  { id: 'cash-flow', label: 'Cash Flow', icon: TrendingDown },
  { id: 'period', label: 'Period Management', icon: Calendar },
  { id: 'budgets', label: 'Budgets', icon: Target },
  { id: 'vendor-payments', label: 'Vendor Payments', icon: Building2 },
  { id: 'reconciliation', label: 'Reconciliation', icon: Scale },
];

export default function FinanceFilters({
  selectedYear,
  onYearChange,
  dateFrom,
  dateTo,
  onDateFromChange,
  onDateToChange,
  activeTab,
  onTabChange,
}: FinanceFiltersProps) {
  return (
    <>
      {/* Header */}
      <div className="mb-8">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-3xl font-bold text-gray-900 flex items-center gap-3">
              <BarChart3 className="w-8 h-8 text-blue-600" />
              Finance & Accounting
            </h1>
            <p className="text-slate-600 mt-2">Financial year 2025-26 | Indian Accounting Standards</p>
          </div>
          <div className="flex gap-3">
            <select
              value={selectedYear}
              onChange={(e) => onYearChange(e.target.value)}
              className="bg-slate-50 border border-slate-700 text-gray-900 rounded px-4 py-2 text-sm focus:outline-none focus:border-blue-500"
            >
              <option>2025-2026</option>
              <option>2024-2025</option>
              <option>2023-2024</option>
            </select>
            <button className="flex items-center gap-2 px-4 py-2 bg-blue-50 hover:bg-blue-800 text-blue-100 rounded border border-blue-700 transition-colors font-medium text-sm">
              <Download className="w-4 h-4" />
              Export
            </button>
          </div>
        </div>

        {/* Date Filter */}
        <div className="flex flex-wrap gap-3">
          <div className="flex gap-2">
            <input
              type="date"
              value={dateFrom}
              onChange={(e) => onDateFromChange(e.target.value)}
              className="bg-slate-50 border border-slate-700 text-gray-900 rounded px-3 py-2 text-sm focus:outline-none focus:border-blue-500"
            />
            <input
              type="date"
              value={dateTo}
              onChange={(e) => onDateToChange(e.target.value)}
              className="bg-slate-50 border border-slate-700 text-gray-900 rounded px-3 py-2 text-sm focus:outline-none focus:border-blue-500"
            />
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex flex-wrap gap-2 mb-6 border-b border-slate-700">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => onTabChange(id)}
            className={clsx(
              'flex items-center gap-2 px-4 py-3 font-medium text-sm border-b-2 transition-colors',
              activeTab === id
                ? 'text-blue-600 border-blue-400'
                : 'text-slate-600 border-transparent hover:text-slate-700'
            )}
          >
            <Icon className="w-4 h-4" />
            {label}
          </button>
        ))}
      </div>
    </>
  );
}

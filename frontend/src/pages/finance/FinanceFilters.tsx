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
  BookOpen,
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

// ---- Indian financial year (Apr–Mar), computed from the current IST date ----
// OS-054: the FY dropdown used to be a hardcoded list that went stale (current
// FY missing) and the subtitle was frozen at "2025-26". Everything below is
// derived from IST "today" so it never needs a yearly code change.

/** FY start year for the current IST date: Apr..Dec -> this year, Jan..Mar -> last year. */
export function currentFyStartYearIST(): number {
  // en-CA yields YYYY-MM-DD; Asia/Kolkata pins the calendar date to IST.
  const [y, m] = new Date()
    .toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' })
    .split('-')
    .map(Number);
  return m >= 4 ? y : y - 1;
}

/** Current FY in the selector's "YYYY-YYYY" format, e.g. "2026-2027". */
export function currentFyLabelIST(): string {
  const start = currentFyStartYearIST();
  return `${start}-${start + 1}`;
}

// Earliest FY the selector offered before this fix — kept so no previously
// selectable year disappears; the list grows by one option each April.
const EARLIEST_FY_START = 2023;

function fyOptions(): string[] {
  const options: string[] = [];
  for (let y = currentFyStartYearIST(); y >= EARLIEST_FY_START; y--) {
    options.push(`${y}-${y + 1}`);
  }
  return options;
}

/** "2026-2027" -> "2026-27" for the compact subtitle. */
function fyShort(label: string): string {
  const [a, b] = label.split('-');
  return a && b && b.length === 4 ? `${a}-${b.slice(2)}` : label;
}

const TABS: { id: TabType; label: string; icon: typeof TrendingUp }[] = [
  { id: 'revenue-pl', label: 'Revenue & P&L', icon: TrendingUp },
  { id: 'gst', label: 'GST Management', icon: Percent },
  { id: 'outstanding', label: 'Outstanding & Collections', icon: CreditCard },
  { id: 'cash-flow', label: 'Cash Flow', icon: TrendingDown },
  { id: 'period', label: 'Period Management', icon: Calendar },
  { id: 'budgets', label: 'Budgets', icon: Target },
  { id: 'vendor-payments', label: 'Vendor Payments', icon: Building2 },
  { id: 'journal-entries', label: 'Journal Entries', icon: BookOpen },
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
        <div className="flex flex-wrap items-center justify-between gap-4 mb-6">
          <div>
            <h1 className="text-3xl font-bold text-gray-900 flex items-center gap-3">
              <BarChart3 className="w-8 h-8 text-blue-600" />
              Finance & Accounting
            </h1>
            <p className="text-slate-600 mt-2">Financial year {fyShort(selectedYear)} | Indian Accounting Standards</p>
          </div>
          <div className="flex gap-3">
            <select
              value={selectedYear}
              onChange={(e) => onYearChange(e.target.value)}
              className="bg-slate-50 border border-gray-200 text-gray-900 rounded px-4 py-2 text-sm focus:outline-none focus:border-blue-500"
            >
              {fyOptions().map((fy) => (
                <option key={fy} value={fy}>{fy}</option>
              ))}
            </select>
            <button className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded border border-blue-700 transition-colors font-medium text-sm">
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
              className="bg-slate-50 border border-gray-200 text-gray-900 rounded px-3 py-2 text-sm focus:outline-none focus:border-blue-500"
            />
            <input
              type="date"
              value={dateTo}
              onChange={(e) => onDateToChange(e.target.value)}
              className="bg-slate-50 border border-gray-200 text-gray-900 rounded px-3 py-2 text-sm focus:outline-none focus:border-blue-500"
            />
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex flex-wrap gap-2 mb-6 border-b border-gray-200">
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

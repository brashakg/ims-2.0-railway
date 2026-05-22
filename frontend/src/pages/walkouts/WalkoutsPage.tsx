// ============================================================================
// IMS 2.0 — Walkouts Page (Pune Incentive Module i, Phases 1-2)
// ============================================================================
// List + filter sidebar + intake modal trigger. Per-walkout follow-up
// tracking, the conversion-feed dashboard, and the won-back panel ship
// in Phases 3-5. See docs/PUNE_INCENTIVE_BUILD_PLAN.md.

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { Plus, UserX, Filter, Loader2, RefreshCw, X, BarChart3 } from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { walkoutsApi } from '../../services/api';
import {
  WALKOUT_REASONS,
  type ListWalkoutsParams,
  type Walkout,
  type WalkoutReason,
} from '../../types';
import { WalkoutIntakeModal } from './WalkoutIntakeModal';
import { WalkoutResultBadge } from './ResultPanel';

const RESULT_OPTIONS = ['DUE', 'NEGATIVE', 'CONVERTED', 'none'] as const;
type ResultOption = typeof RESULT_OPTIONS[number];
const PAGE_SIZE = 50;

export function WalkoutsPage() {
  const toast = useToast();
  const [isModalOpen, setIsModalOpen] = useState(false);

  const [items, setItems] = useState<Walkout[]>([]);
  const [total, setTotal] = useState(0);
  const [skip, setSkip] = useState(0);
  const [isLoading, setIsLoading] = useState(false);

  // Filters
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [salesPersonId, setSalesPersonId] = useState('');
  const [reason, setReason] = useState<WalkoutReason | ''>('');
  const [resultFilter, setResultFilter] = useState<ResultOption | ''>('');

  const params: ListWalkoutsParams = useMemo(() => {
    const p: ListWalkoutsParams = { skip, limit: PAGE_SIZE };
    if (dateFrom) p.date_from = dateFrom;
    if (dateTo) p.date_to = dateTo;
    if (salesPersonId.trim()) p.sales_person_id = salesPersonId.trim();
    if (reason) p.primary_walkout_reason = reason;
    if (resultFilter) p.result = resultFilter;
    return p;
  }, [skip, dateFrom, dateTo, salesPersonId, reason, resultFilter]);

  const loadList = useCallback(async () => {
    setIsLoading(true);
    try {
      const resp = await walkoutsApi.listWalkouts(params);
      setItems(resp.items || []);
      setTotal(resp.total || 0);
    } catch (e: any) {
      const msg = e?.response?.data?.detail || e?.message || 'Could not load walkouts';
      toast.error(typeof msg === 'string' ? msg : 'Could not load walkouts');
      setItems([]);
      setTotal(0);
    } finally {
      setIsLoading(false);
    }
  }, [params, toast]);

  useEffect(() => {
    loadList();
  }, [loadList]);

  const handleSaved = () => {
    setIsModalOpen(false);
    setSkip(0);
    loadList();
  };

  const clearFilters = () => {
    setDateFrom('');
    setDateTo('');
    setSalesPersonId('');
    setReason('');
    setResultFilter('');
    setSkip(0);
  };

  const hasActiveFilters = !!(
    dateFrom || dateTo || salesPersonId || reason || resultFilter
  );

  const pageStart = items.length === 0 ? 0 : skip + 1;
  const pageEnd = skip + items.length;

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
            <UserX className="w-6 h-6 text-bv-red-500" />
            Walkouts
          </h1>
          <p className="text-gray-500 mt-1 text-sm">
            Customers who left without buying. {total} total in the current view.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Link
            to="/walkouts/dashboard"
            className="btn-secondary inline-flex items-center gap-2"
          >
            <BarChart3 className="w-4 h-4" />
            Dashboard
          </Link>
          <button
            type="button"
            onClick={loadList}
            className="btn-secondary inline-flex items-center gap-2"
            disabled={isLoading}
            title="Refresh"
          >
            <RefreshCw className={`w-4 h-4 ${isLoading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
          <button
            type="button"
            onClick={() => setIsModalOpen(true)}
            className="btn-primary inline-flex items-center gap-2"
          >
            <Plus className="w-4 h-4" />
            Log Walkout
          </button>
        </div>
      </div>

      <div className="grid grid-cols-12 gap-4">
        {/* Filter sidebar */}
        <aside className="col-span-12 lg:col-span-3">
          <div className="card p-4 space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-sm font-semibold text-gray-700">
                <Filter className="w-4 h-4" />
                Filters
              </div>
              {hasActiveFilters && (
                <button
                  type="button"
                  onClick={clearFilters}
                  className="text-xs text-bv-red-500 hover:text-bv-red-600 inline-flex items-center gap-1"
                >
                  <X className="w-3 h-3" />
                  Clear
                </button>
              )}
            </div>

            <FilterField label="Date from">
              <input
                type="date"
                value={dateFrom}
                onChange={e => { setDateFrom(e.target.value); setSkip(0); }}
                className="filter-input"
              />
            </FilterField>
            <FilterField label="Date to">
              <input
                type="date"
                value={dateTo}
                onChange={e => { setDateTo(e.target.value); setSkip(0); }}
                className="filter-input"
              />
            </FilterField>
            <FilterField label="Sales person ID">
              <input
                type="text"
                value={salesPersonId}
                onChange={e => { setSalesPersonId(e.target.value); setSkip(0); }}
                placeholder="user-akshay"
                className="filter-input"
              />
            </FilterField>
            <FilterField label="Reason">
              <select
                value={reason}
                onChange={e => { setReason(e.target.value as WalkoutReason | ''); setSkip(0); }}
                className="filter-input"
              >
                <option value="">All reasons</option>
                {WALKOUT_REASONS.map(r => (
                  <option key={r} value={r}>{r}</option>
                ))}
              </select>
            </FilterField>
            <FilterField label="Result">
              <select
                value={resultFilter}
                onChange={e => { setResultFilter(e.target.value as any); setSkip(0); }}
                className="filter-input"
              >
                <option value="">All</option>
                {RESULT_OPTIONS.map(r => (
                  <option key={r} value={r}>{r === 'none' ? 'Not yet set' : r}</option>
                ))}
              </select>
            </FilterField>
          </div>
        </aside>

        {/* List */}
        <main className="col-span-12 lg:col-span-9">
          <div className="card overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200 text-xs uppercase tracking-wider text-gray-600">
                <tr>
                  <th className="px-4 py-3 text-left font-medium">Date</th>
                  <th className="px-4 py-3 text-left font-medium">Customer</th>
                  <th className="px-4 py-3 text-left font-medium">Mobile</th>
                  <th className="px-4 py-3 text-left font-medium">Product</th>
                  <th className="px-4 py-3 text-left font-medium">Reason</th>
                  <th className="px-4 py-3 text-left font-medium">Sales person</th>
                  <th className="px-4 py-3 text-left font-medium">Result</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {isLoading && items.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="px-4 py-12 text-center text-gray-500">
                      <Loader2 className="w-5 h-5 animate-spin inline-block mr-2" />
                      Loading…
                    </td>
                  </tr>
                ) : items.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="px-4 py-12 text-center text-gray-500">
                      No walkouts match the current filter.
                    </td>
                  </tr>
                ) : items.map(w => (
                  <tr key={w.walkout_id} className="hover:bg-gray-50">
                    <td className="px-4 py-3 whitespace-nowrap text-gray-700">{w.date_str}</td>
                    <td className="px-4 py-3">
                      <Link
                        to={`/walkouts/${w.walkout_id}`}
                        className="text-bv-red-600 hover:underline font-medium"
                      >
                        {w.customer_name}
                      </Link>
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-gray-700">{w.mobile}</td>
                    <td className="px-4 py-3 text-gray-700">{w.product_interested}</td>
                    <td className="px-4 py-3">
                      <span className="text-xs px-2 py-0.5 rounded-full bg-amber-50 text-amber-800 border border-amber-200">
                        {w.primary_walkout_reason}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-gray-700">{w.sales_person_name || w.sales_person_id}</td>
                    <td className="px-4 py-3">
                      <WalkoutResultBadge value={w.result} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          {total > 0 && (
            <div className="flex items-center justify-between mt-3 text-sm text-gray-600">
              <div>
                Showing <span className="font-medium">{pageStart}</span>–
                <span className="font-medium">{pageEnd}</span> of{' '}
                <span className="font-medium">{total}</span>
              </div>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => setSkip(Math.max(0, skip - PAGE_SIZE))}
                  disabled={skip === 0 || isLoading}
                  className="btn-secondary px-3 py-1 text-xs disabled:opacity-50"
                >
                  Previous
                </button>
                <button
                  type="button"
                  onClick={() => setSkip(skip + PAGE_SIZE)}
                  disabled={pageEnd >= total || isLoading}
                  className="btn-secondary px-3 py-1 text-xs disabled:opacity-50"
                >
                  Next
                </button>
              </div>
            </div>
          )}
        </main>
      </div>

      <WalkoutIntakeModal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        onSaved={handleSaved}
      />

      <style>{`
        .filter-input {
          width: 100%;
          padding: 6px 10px;
          border: 1px solid #e5e7eb;
          border-radius: 4px;
          font-size: 13px;
          color: #111827;
          background: #fff;
        }
        .filter-input:focus { outline: none; border-color: #fca5a5; }
      `}</style>
    </div>
  );
}

function FilterField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="text-xs text-gray-500 mb-1 block">{label}</span>
      {children}
    </label>
  );
}

export default WalkoutsPage;

// ============================================================================
// IMS 2.0 - F24 Optometrist -> retail conversion tab
// ============================================================================
// Of all patients an optometrist examined in a period, what % placed a retail
// order within 7 days? Read-only analytics.
//
// Revenue is ROLE-GATED (DECISIONS sec 3, locked): OPTOMETRIST sees only tests +
// converted count + conversion rate (revenue columns absent). STORE_MANAGER and
// above see revenue + avg order value + a store summary bar. The server already
// strips revenue (returns null) for optometrists; the FE simply does not render
// those columns when the caller can't see revenue.
//
// Restrained light UI: neutral cards, single accent, colour only for semantic
// meaning (rate badge). No chart library - a CSS width bar shows the rate.

import { useCallback, useEffect, useState } from 'react';
import { Loader2, ChevronRight, TrendingUp } from 'lucide-react';
import { format, startOfMonth } from 'date-fns';
import { clinicalApi } from '../../services/api';
import type { ConversionDashboard, ConversionRow } from '../../services/api/clinical';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import clsx from 'clsx';

const INR = (n: number) =>
  new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 }).format(Math.round(n));

// Semantic-only colour: a single meaning (good / okay / poor conversion).
function rateBadgeClass(pct: number): string {
  if (pct >= 60) return 'bg-green-100 text-green-800';
  if (pct >= 40) return 'bg-yellow-100 text-yellow-800';
  return 'bg-red-100 text-red-800';
}

type SortKey = 'optometrist_name' | 'tests_completed' | 'converted_count' | 'conversion_rate_pct';

export function ConversionTab() {
  const { user, hasRole } = useAuth();
  const toast = useToast();

  // Managers (and above) see revenue + the summary bar. A pure optometrist does not.
  const canSeeRevenue = hasRole(['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER']);
  // HQ roles may pick a store; STORE_MANAGER + OPTOMETRIST are bound to their own.
  const canPickStore = hasRole(['SUPERADMIN', 'ADMIN', 'AREA_MANAGER']);

  const [fromDate, setFromDate] = useState(() => format(startOfMonth(new Date()), 'yyyy-MM-dd'));
  const [toDate, setToDate] = useState(() => format(new Date(), 'yyyy-MM-dd'));
  const [data, setData] = useState<ConversionDashboard | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [sortKey, setSortKey] = useState<SortKey>('converted_count');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [expanded, setExpanded] = useState<string | null>(null);

  const load = useCallback(async () => {
    setIsLoading(true);
    try {
      const res = await clinicalApi.getConversionDashboard({
        fromDate,
        toDate,
        storeId: canPickStore ? undefined : user?.activeStoreId,
      });
      setData(res);
    } catch {
      toast.error('Failed to load conversion dashboard');
      setData({ store_summary: {} as ConversionDashboard['store_summary'], rows: [] });
    } finally {
      setIsLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fromDate, toDate, canPickStore, user?.activeStoreId]);

  useEffect(() => {
    load();
  }, [load]);

  const rows = [...(data?.rows ?? [])].sort((a, b) => {
    const av = a[sortKey];
    const bv = b[sortKey];
    let cmp: number;
    if (typeof av === 'number' && typeof bv === 'number') cmp = av - bv;
    else cmp = String(av).localeCompare(String(bv));
    return sortDir === 'asc' ? cmp : -cmp;
  });

  const toggleSort = (key: SortKey) => {
    if (key === sortKey) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    else {
      setSortKey(key);
      setSortDir('desc');
    }
  };

  const summary = data?.store_summary;
  const totalUnattributed = rows.reduce((s, r) => s + (r.unattributed_tests || 0), 0);

  return (
    <div className="space-y-4">
      {/* Controls */}
      <div className="card flex flex-wrap items-end gap-3">
        <label className="text-sm text-gray-600">
          <span className="block mb-1">From</span>
          <input
            type="date"
            className="input-field"
            value={fromDate}
            max={toDate}
            onChange={(e) => setFromDate(e.target.value)}
          />
        </label>
        <label className="text-sm text-gray-600">
          <span className="block mb-1">To</span>
          <input
            type="date"
            className="input-field"
            value={toDate}
            min={fromDate}
            onChange={(e) => setToDate(e.target.value)}
          />
        </label>
        <div className="ml-auto text-xs text-gray-500 self-center">
          7-day conversion window
        </div>
      </div>

      {/* Store summary bar - managers + above only */}
      {canSeeRevenue && summary && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <SummaryCard label="Tests completed" value={String(summary.tests_completed ?? 0)} />
          <SummaryCard label="Converted" value={String(summary.converted ?? 0)} />
          <SummaryCard
            label="Overall rate"
            value={`${summary.conversion_rate_pct ?? 0}%`}
            accent
          />
          <SummaryCard
            label="Revenue attributed"
            value={`Rs ${INR(summary.revenue_attributed ?? 0)}`}
          />
        </div>
      )}

      {/* Table */}
      <div className="card overflow-x-auto">
        {isLoading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="w-8 h-8 animate-spin text-bv-red-600" />
          </div>
        ) : rows.length === 0 ? (
          <div className="text-center py-12 text-gray-500">
            <TrendingUp className="w-10 h-10 mx-auto mb-2 opacity-40" />
            <p>No completed tests in this period.</p>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-gray-500 border-b border-gray-200">
                <Th onClick={() => toggleSort('optometrist_name')} active={sortKey === 'optometrist_name'} dir={sortDir}>
                  Optometrist
                </Th>
                <Th onClick={() => toggleSort('tests_completed')} active={sortKey === 'tests_completed'} dir={sortDir} right>
                  Tests
                </Th>
                <Th onClick={() => toggleSort('converted_count')} active={sortKey === 'converted_count'} dir={sortDir} right>
                  Converted
                </Th>
                <Th onClick={() => toggleSort('conversion_rate_pct')} active={sortKey === 'conversion_rate_pct'} dir={sortDir} right>
                  Rate
                </Th>
                {canSeeRevenue && (
                  <>
                    <th className="py-2 px-3 text-right">Revenue</th>
                    <th className="py-2 px-3 text-right">Avg order</th>
                    <th className="py-2 px-3 text-right">Avg days</th>
                  </>
                )}
                <th className="py-2 px-3" />
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <ConversionRowView
                  key={r.optometrist_id}
                  row={r}
                  canSeeRevenue={canSeeRevenue}
                  expanded={expanded === r.optometrist_id}
                  onToggle={() =>
                    setExpanded((cur) => (cur === r.optometrist_id ? null : r.optometrist_id))
                  }
                />
              ))}
            </tbody>
          </table>
        )}
      </div>

      {totalUnattributed > 0 && (
        <p className="text-xs text-gray-500">
          {totalUnattributed} test{totalUnattributed === 1 ? '' : 's'} had no linked customer and
          could not be attributed to an order.
        </p>
      )}
    </div>
  );
}

function SummaryCard({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div
      className={clsx(
        'bg-gray-50 border border-gray-200 rounded-lg px-4 py-3',
        accent && 'border-l-4 border-l-bv-red-500',
      )}
    >
      <div className="text-sm text-gray-500">{label}</div>
      <div className="text-lg font-semibold text-gray-900">{value}</div>
    </div>
  );
}

function Th({
  children,
  onClick,
  active,
  dir,
  right,
}: {
  children: React.ReactNode;
  onClick: () => void;
  active: boolean;
  dir: 'asc' | 'desc';
  right?: boolean;
}) {
  return (
    <th
      className={clsx('py-2 px-3 cursor-pointer select-none', right && 'text-right')}
      onClick={onClick}
    >
      <span className={clsx(active && 'text-gray-900 font-semibold')}>
        {children}
        {active ? (dir === 'asc' ? ' ^' : ' v') : ''}
      </span>
    </th>
  );
}

function ConversionRowView({
  row,
  canSeeRevenue,
  expanded,
  onToggle,
}: {
  row: ConversionRow;
  canSeeRevenue: boolean;
  expanded: boolean;
  onToggle: () => void;
}) {
  const colSpan = canSeeRevenue ? 8 : 5;
  return (
    <>
      <tr className="border-b border-gray-100 hover:bg-gray-50">
        <td className="py-2 px-3 text-gray-900">{row.optometrist_name}</td>
        <td className="py-2 px-3 text-right">{row.tests_completed}</td>
        <td className="py-2 px-3 text-right">{row.converted_count}</td>
        <td className="py-2 px-3 text-right">
          <span className={clsx('inline-block px-2 py-0.5 rounded-full text-xs font-medium', rateBadgeClass(row.conversion_rate_pct))}>
            {row.conversion_rate_pct}%
          </span>
        </td>
        {canSeeRevenue && (
          <>
            <td className="py-2 px-3 text-right">
              {row.revenue_attributed != null ? `Rs ${INR(row.revenue_attributed)}` : '-'}
            </td>
            <td className="py-2 px-3 text-right">
              {row.avg_order_value != null ? `Rs ${INR(row.avg_order_value)}` : '-'}
            </td>
            <td className="py-2 px-3 text-right">
              {row.avg_days_to_order != null ? row.avg_days_to_order : '-'}
            </td>
          </>
        )}
        <td className="py-2 px-3 text-right">
          {row.orders.length > 0 && (
            <button
              type="button"
              onClick={onToggle}
              className="text-gray-400 hover:text-gray-700"
              aria-label="Show converted orders"
            >
              <ChevronRight className={clsx('w-4 h-4 transition-transform', expanded && 'rotate-90')} />
            </button>
          )}
        </td>
      </tr>
      {expanded && row.orders.length > 0 && (
        <tr className="bg-gray-50">
          <td colSpan={colSpan} className="py-2 px-6">
            {/* CSS rate bar - no chart library. */}
            <div className="mb-3">
              <div className="h-2 w-full max-w-md bg-gray-200 rounded">
                <div
                  className="h-2 bg-bv-red-500 rounded"
                  style={{ width: `${Math.min(100, row.conversion_rate_pct)}%` }}
                />
              </div>
            </div>
            <ul className="space-y-1 text-xs text-gray-600">
              {row.orders.slice(0, 10).map((o, i) => (
                <li key={`${o.order_number}-${i}`} className="flex gap-4">
                  <span className="font-medium text-gray-800">{o.order_number ?? 'Order'}</span>
                  {canSeeRevenue && o.amount != null && <span>Rs {INR(o.amount)}</span>}
                  <span className="text-gray-400">
                    {o.days_after_test} day{o.days_after_test === 1 ? '' : 's'} after test
                  </span>
                </li>
              ))}
              {row.orders.length > 10 && (
                <li className="text-gray-400">and {row.orders.length - 10} more</li>
              )}
            </ul>
          </td>
        </tr>
      )}
    </>
  );
}

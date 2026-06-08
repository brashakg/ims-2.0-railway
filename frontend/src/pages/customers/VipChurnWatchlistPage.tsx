// ============================================================================
// IMS 2.0 - VIP Churn Watchlist (F40)
// ============================================================================
// SUPERADMIN/ADMIN-only watchlist of VIP customers (LTV >= 1,00,000 AND >= 3
// completed orders) who are overdue relative to their PERSONAL buying rhythm —
// not the flat-recency churn model on the Segmentation page. ORACLE's nightly
// scan writes the vip_churn_risk subdoc; this page is a pure read of
// GET /crm/vip-churn plus an inline "Intervene" action.
//
// Restrained/executive UI: a one-line trend summary (no stat tiles), plain
// table, risk shown as coloured TEXT only (text-red-600 HIGH / text-amber-600
// WATCH) with no background fills, and a plain-text empty state.

import { useState, useEffect, useCallback } from 'react';
import { Loader2 } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
// Imported directly from the module (not the services/api barrel) — the
// new-service barrel re-exports fail to resolve under this tsconfig (TS2614),
// same as CustomerSegmentation.tsx.
import { crmApi } from '../../services/api/crm';
import type {
  VipChurnRiskCustomer,
  VipChurnTrend,
  VipChurnSortBy,
} from '../../services/api/crm';
import { VipInterveneModal } from '../../components/customers/VipInterveneModal';

type RiskFilter = '' | 'HIGH' | 'WATCH';

// Risk label cell — colour on the TEXT only, no chip / background fill.
function RiskLabel({ label }: { label: string }) {
  if (label === 'HIGH') return <span className="font-medium text-red-600">HIGH</span>;
  if (label === 'WATCH') return <span className="font-medium text-amber-600">WATCH</span>;
  return <span className="text-gray-500">{label}</span>;
}

function rupees(n: number): string {
  return `₹${(n ?? 0).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
}

function formatScanDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleDateString('en-IN');
}

export function VipChurnWatchlistPage() {
  const { user } = useAuth();
  const isSuperadmin = (user?.roles || []).includes('SUPERADMIN');

  const [customers, setCustomers] = useState<VipChurnRiskCustomer[]>([]);
  const [trend, setTrend] = useState<VipChurnTrend | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  // Filters. SUPERADMIN can pick any store (free-text store_id); ADMIN is
  // store-scoped server-side and uses their active store implicitly.
  const [storeId, setStoreId] = useState<string>(isSuperadmin ? '' : user?.activeStoreId || '');
  const [riskFilter, setRiskFilter] = useState<RiskFilter>('');
  const [sortBy, setSortBy] = useState<VipChurnSortBy>('overdue_by_days');

  // Intervene modal state — keyed on the selected customer.
  const [interveneTarget, setInterveneTarget] = useState<VipChurnRiskCustomer | null>(null);

  const load = useCallback(async () => {
    setIsLoading(true);
    try {
      const res = await crmApi.getVipChurn({
        store_id: storeId || undefined,
        risk_label: riskFilter || undefined,
        sort_by: sortBy,
        limit: 100,
      });
      setCustomers(Array.isArray(res?.customers) ? res.customers : []);
      setTrend(res?.trend ?? null);
    } catch {
      setCustomers([]);
      setTrend(null);
    } finally {
      setIsLoading(false);
    }
  }, [storeId, riskFilter, sortBy]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="inv-body">
      {/* Editorial header */}
      <div className="inv-head">
        <div>
          <div className="eyebrow" style={{ marginBottom: 6 }}>CRM · VIP Watch List</div>
          <h1>The customers worth a phone call.</h1>
          <div className="hint">
            VIPs (lifetime value over ₹1,00,000) who are overdue against their own buying rhythm — not a flat
            recency rule. Updated nightly.
          </div>
        </div>
      </div>

      {/* Trend summary — one plain line, no stat tiles. */}
      <p className="text-sm text-gray-500">
        {trend
          ? `${trend.vip_count} VIPs tracked | ${trend.watch_count} WATCH | ${trend.high_risk_count} HIGH | Previous scan: ${formatScanDate(trend.scanned_at)}`
          : 'No scan has run yet.'}
      </p>

      {/* Filter bar */}
      <div className="flex flex-wrap items-end gap-3">
        {isSuperadmin && (
          <div>
            <label htmlFor="vip-store" className="block text-xs text-gray-500 mb-1">Store</label>
            <input
              id="vip-store"
              type="text"
              value={storeId}
              onChange={(e) => setStoreId(e.target.value)}
              placeholder="All stores"
              className="border border-gray-200 rounded px-2 py-1 text-sm"
            />
          </div>
        )}
        <div>
          <label htmlFor="vip-risk" className="block text-xs text-gray-500 mb-1">Risk</label>
          <select
            id="vip-risk"
            value={riskFilter}
            onChange={(e) => setRiskFilter(e.target.value as RiskFilter)}
            className="border border-gray-200 rounded px-2 py-1 text-sm"
          >
            <option value="">All</option>
            <option value="HIGH">High</option>
            <option value="WATCH">Watch</option>
          </select>
        </div>
        <div>
          <label htmlFor="vip-sort" className="block text-xs text-gray-500 mb-1">Sort by</label>
          <select
            id="vip-sort"
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value as VipChurnSortBy)}
            className="border border-gray-200 rounded px-2 py-1 text-sm"
          >
            <option value="overdue_by_days">Overdue by</option>
            <option value="ltv">Lifetime value</option>
            <option value="last_purchase_days_ago">Days since last</option>
          </select>
        </div>
      </div>

      {/* Table */}
      <div className="bg-white border border-gray-200 rounded-lg p-6 mt-2">
        {isLoading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="w-6 h-6 text-blue-500 animate-spin" />
          </div>
        ) : customers.length === 0 ? (
          <p className="text-gray-500 text-center py-8">No VIP customers are overdue. Good health.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-gray-600 border-b border-gray-200">
                <tr>
                  <th className="px-3 py-2 text-left">Rank</th>
                  <th className="px-3 py-2 text-left">Customer</th>
                  <th className="px-3 py-2 text-left">Store</th>
                  <th className="px-3 py-2 text-right">LTV</th>
                  <th className="px-3 py-2 text-right">Usual Interval</th>
                  <th className="px-3 py-2 text-right">Days Since Last</th>
                  <th className="px-3 py-2 text-right">Overdue By</th>
                  <th className="px-3 py-2 text-left">Risk</th>
                  <th className="px-3 py-2 text-left">Last AI Note</th>
                  <th className="px-3 py-2 text-right">Action</th>
                </tr>
              </thead>
              <tbody>
                {customers.map((c, i) => {
                  const r = c.vip_churn_risk;
                  return (
                    <tr
                      key={c.customer_id || i}
                      className={i % 2 === 1 ? 'bg-gray-50' : undefined}
                    >
                      <td className="px-3 py-2 text-gray-500">{i + 1}</td>
                      <td className="px-3 py-2 font-medium text-gray-900">{c.name || 'Unknown'}</td>
                      <td className="px-3 py-2 text-gray-600">{c.store_id || '—'}</td>
                      <td className="px-3 py-2 text-right text-gray-700">{rupees(c.ltv)}</td>
                      <td className="px-3 py-2 text-right text-gray-600">{r?.usual_interval_days ?? '—'} days</td>
                      <td className="px-3 py-2 text-right text-gray-600">{r?.last_purchase_days_ago ?? '—'} days</td>
                      <td className="px-3 py-2 text-right text-gray-700">{r?.overdue_by_days ?? '—'} days</td>
                      <td className="px-3 py-2"><RiskLabel label={r?.risk_label ?? '—'} /></td>
                      <td className="px-3 py-2 text-gray-500 max-w-xs">
                        {r?.narrative || <span className="text-gray-400">—</span>}
                      </td>
                      <td className="px-3 py-2 text-right">
                        <button
                          type="button"
                          onClick={() => setInterveneTarget(c)}
                          className="text-sm text-blue-600 underline hover:text-blue-800"
                        >
                          Intervene
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {interveneTarget && (
        <VipInterveneModal
          customerId={interveneTarget.customer_id}
          customerName={interveneTarget.name}
          isOpen={!!interveneTarget}
          onClose={() => setInterveneTarget(null)}
          onSuccess={load}
        />
      )}
    </div>
  );
}

export default VipChurnWatchlistPage;

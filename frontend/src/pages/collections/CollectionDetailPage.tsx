// ============================================================================
// IMS 2.0 - Collection detail (Phase 1) — the KPI landing page
// ============================================================================
// One collection's commercial picture: identity (type + Online/Internal pill +
// read-only rule chips) and the KPI strip — products, units on hand, stock
// value (+ basis + MRP subtext), sold 7/30/90, revenue, margin (or "needs cost
// data"), sell-through, days of cover (display-capped at 180+) — plus a
// per-store split table. The 7/30/90 selector re-fetches; non-store-scoped
// roles also get a store filter (STORE_MANAGER numbers arrive store-forced
// from the backend, so no filter is rendered for them). Fail-soft while the
// Track 2 insights backend isn't deployed.

import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  ArrowLeft,
  Layers,
  Loader2,
  Sparkles,
  ListChecks,
  AlertTriangle,
  ExternalLink,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { storeApi } from '../../services/api/stores';
import {
  collectionsInsightsApi,
  type CollectionInsights,
  type CollectionStoreInsight,
  type CollectionMeta,
} from '../../services/api/collectionsInsights';
import { rupee, fmtInt, basisLabel, pct, daysOfCover } from './collectionsShared';

interface StoreOpt {
  store_id: string;
  store_name?: string;
  store_code?: string;
}

// Roles that get the store filter (a pure STORE_MANAGER is already store-forced
// by the backend, so the select would be a lie for them).
const STORE_FILTER_ROLES = ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'CATALOG_MANAGER'];
const PUBLISH_LINK_ROLES = ['SUPERADMIN', 'ADMIN'];

// NOTE: the insights API's KPI windows are FIXED (sold d7/d30/d90; revenue/
// margin/sell-through are 30d) -- a window selector would relabel numbers it
// cannot re-window, so the strip shows all three sold windows and labels the
// 30d KPIs honestly. (The API's `days` param is reserved for a later phase.)

function KpiCard({
  label,
  value,
  sub,
}: {
  label: string;
  value: string;
  sub?: string | null;
}) {
  return (
    <div className="card p-4">
      <div className="text-[11px] uppercase tracking-wide text-gray-400 mb-1">{label}</div>
      <div className="text-lg font-semibold text-gray-900 leading-tight">{value}</div>
      {sub && <div className="text-[11px] text-gray-400 mt-0.5">{sub}</div>}
    </div>
  );
}

/** Render one read-only rule chip ("brand: Ray-Ban + Vogue"). */
function ruleText(field: string, relation: string, value: string | number | string[]): string {
  const v = Array.isArray(value) ? value.join(' + ') : String(value);
  const rel = relation.toUpperCase();
  if (rel === 'GREATER_THAN') return `${field} > ${v}`;
  if (rel === 'LESS_THAN') return `${field} < ${v}`;
  if (rel === 'NOT_EQUALS') return `${field} ≠ ${v}`;
  if (rel === 'CONTAINS') return `${field} contains ${v}`;
  return `${field}: ${v}`;
}

export default function CollectionDetailPage() {
  const { id = '' } = useParams<{ id: string }>();
  const { user } = useAuth();
  const roles: string[] = (user?.roles as string[]) || [];
  const showStoreFilter = roles.some((r) => STORE_FILTER_ROLES.includes(r));
  const canManagePublishing = roles.some((r) => PUBLISH_LINK_ROLES.includes(r));

  const [meta, setMeta] = useState<CollectionMeta | null>(null);
  const [insights, setInsights] = useState<CollectionInsights | null>(null);
  const [storeRows, setStoreRows] = useState<CollectionStoreInsight[]>([]);
  const [stores, setStores] = useState<StoreOpt[]>([]);
  const [storeId, setStoreId] = useState('');
  const [loading, setLoading] = useState(true);

  // Identity + per-store split + store options: once per collection.
  useEffect(() => {
    let alive = true;
    collectionsInsightsApi.collectionMeta(id).then((m) => {
      if (alive) setMeta(m);
    });
    collectionsInsightsApi.storeInsights(id).then((rows) => {
      if (alive) setStoreRows(rows);
    });
    return () => {
      alive = false;
    };
  }, [id]);

  useEffect(() => {
    if (!showStoreFilter) return;
    let alive = true;
    storeApi
      .getStores()
      .then((r: { stores?: StoreOpt[] } | undefined) => {
        if (alive) setStores(r?.stores || []);
      })
      .catch(() => {
        if (alive) setStores([]); // fail-soft: chain-wide only
      });
    return () => {
      alive = false;
    };
  }, [showStoreFilter]);

  // KPI rollup: re-fetch on store change.
  useEffect(() => {
    let alive = true;
    setLoading(true);
    collectionsInsightsApi
      .insights(id, { store_id: storeId || undefined })
      .then((res) => {
        if (!alive) return;
        setInsights(res);
        setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [id, storeId]);

  const title = meta?.title || insights?.title || '(untitled collection)';
  const collectionType = meta?.collection_type || 'SMART';
  const published = meta?.published ?? false;
  const mrpSub = useMemo(() => {
    if (!insights) return null;
    return insights.stock_value_mrp ? `MRP ${rupee(insights.stock_value_mrp)}` : null;
  }, [insights]);
  const basis = basisLabel(insights?.value_basis);

  const materializedAt = insights?.materialized_at
    ? new Date(insights.materialized_at).toLocaleString('en-IN', {
        dateStyle: 'medium',
        timeStyle: 'short',
      })
    : null;

  return (
    <div className="p-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-3 mb-4">
        <div className="min-w-0">
          <Link
            to="/collections"
            className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700 mb-1"
          >
            <ArrowLeft size={14} /> Collections
          </Link>
          <h1 className="text-xl font-semibold text-gray-900 flex items-center gap-2 flex-wrap">
            <Layers size={20} className="shrink-0" />
            <span className="truncate">{title}</span>
            <span className="inline-flex items-center gap-1 text-[11px] font-medium text-gray-500 border border-gray-200 rounded-full px-2 py-0.5">
              {collectionType === 'SMART' ? <Sparkles size={11} /> : <ListChecks size={11} />}
              {collectionType === 'SMART' ? 'Smart' : 'Manual'}
            </span>
            <span
              className={`px-2 py-0.5 rounded-full text-[11px] font-medium ${
                published ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-600'
              }`}
            >
              {published ? 'Online' : 'Internal'}
            </span>
          </h1>
        </div>
        <div className="flex items-center gap-3">
          {/* store filter — hidden for store-forced roles */}
          {showStoreFilter && stores.length > 0 && (
            <select
              value={storeId}
              onChange={(e) => setStoreId(e.target.value)}
              className="input-field text-sm"
              aria-label="Store filter"
            >
              <option value="">All stores</option>
              {stores.map((s) => (
                <option key={s.store_id} value={s.store_id}>
                  {s.store_name || s.store_code || s.store_id}
                </option>
              ))}
            </select>
          )}
          {canManagePublishing && (
            <Link
              to="/online-store/collections"
              className="btn-secondary inline-flex items-center gap-1.5 text-sm"
            >
              <ExternalLink size={14} /> Manage online publishing
            </Link>
          )}
        </div>
      </div>

      {/* Rule chips (read-only v1) */}
      {meta && meta.rules.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 mb-4">
          {meta.rules.map((r, i) => (
            <span
              key={`${r.field}-${i}`}
              className="inline-flex items-center px-2.5 py-1 rounded-full bg-gray-100 text-gray-700 text-xs"
            >
              {ruleText(r.field, r.relation, r.value)}
            </span>
          ))}
          <span className="text-[11px] text-gray-400">
            {meta.disjunctive ? 'any rule matches' : 'all rules must match'}
          </span>
        </div>
      )}

      {/* Membership cap warning */}
      {insights?.membership_capped && (
        <div className="mb-4 flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          <AlertTriangle size={15} className="mt-0.5 shrink-0" />
          <span>
            This collection is very large — numbers are computed over a capped member set, so
            totals are an approximation.
          </span>
        </div>
      )}

      {/* KPI strip */}
      {loading ? (
        <div className="card p-10 text-center text-gray-400 mb-6">
          <Loader2 size={20} className="animate-spin mx-auto mb-2" />
          Loading insights…
        </div>
      ) : !insights ? (
        <div className="card p-8 text-center text-sm text-gray-500 mb-6">
          Insights aren't available for this collection yet (the insights service may not be
          deployed). The collection itself exists — numbers appear once the service is live.
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
            <KpiCard label="Products" value={fmtInt(insights.members)} />
            <KpiCard label="Units on hand" value={fmtInt(insights.units_on_hand)} />
            <KpiCard
              label="Stock value"
              value={rupee(insights.stock_value)}
              sub={[basis, mrpSub].filter(Boolean).join(' · ') || null}
            />
            <KpiCard
              label="Sold 7 / 30 / 90"
              value={`${fmtInt(insights.sold?.d7)} / ${fmtInt(insights.sold?.d30)} / ${fmtInt(insights.sold?.d90)}`}
              sub="units"
            />
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
            <KpiCard label="Revenue (30d)" value={rupee(insights.revenue_30d)} />
            <KpiCard
              label="Margin (30d)"
              value={insights.margin_30d === null ? 'needs cost data' : rupee(insights.margin_30d)}
            />
            <KpiCard label="Sell-through (30d)" value={pct(insights.sell_through_30d)} />
            <KpiCard label="Days of cover" value={daysOfCover(insights.days_of_cover)} />
          </div>
        </>
      )}

      {/* Per-store split */}
      <div className="card p-0 overflow-hidden">
        <div className="px-4 py-3 border-b border-gray-100 text-sm font-medium text-gray-700">
          By store
        </div>
        {storeRows.length === 0 ? (
          <div className="p-6 text-sm text-gray-400 text-center">
            No per-store data yet.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-[11px] uppercase tracking-wide text-gray-400 border-b border-gray-100">
                  <th className="px-4 py-2 font-medium">Store</th>
                  <th className="px-4 py-2 font-medium text-right">On hand</th>
                  <th className="px-4 py-2 font-medium text-right">Stock value</th>
                  <th className="px-4 py-2 font-medium text-right">Sold 30d</th>
                  <th className="px-4 py-2 font-medium text-right">Sell-through</th>
                  <th className="px-4 py-2 font-medium text-right">Days of cover</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {storeRows.map((s) => {
                  const b = basisLabel(s.value_basis);
                  return (
                    <tr key={s.store_id} className="text-gray-800">
                      <td className="px-4 py-2.5">{s.store_name || s.store_id}</td>
                      <td className="px-4 py-2.5 text-right">{fmtInt(s.on_hand)}</td>
                      <td className="px-4 py-2.5 text-right">
                        {rupee(s.stock_value)}
                        {b && <span className="ml-1 text-[10px] text-amber-600">{b}</span>}
                      </td>
                      <td className="px-4 py-2.5 text-right">{fmtInt(s.sold_30d)}</td>
                      <td className="px-4 py-2.5 text-right">{pct(s.sell_through)}</td>
                      <td className="px-4 py-2.5 text-right">{daysOfCover(s.days_of_cover)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {materializedAt && (
        <p className="text-[11px] text-gray-400 mt-3">Figures computed {materializedAt}.</p>
      )}
    </div>
  );
}

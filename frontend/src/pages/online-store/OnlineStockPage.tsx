// ============================================================================
// IMS 2.0 - Online Store - Stock tally  (BVI Phase 5)
// ============================================================================
// A READ-ONLY reconciliation dashboard: per online-listed SKU it shows what the
// storefront lists vs the real physical on-hand vs what is already reserved,
// derives the sellable count (on_hand - reserved), flags oversell-risk
// (listed > sellable), and suggests a conservative buffer to keep off the
// listing. It answers "am I about to sell the same unit twice online + in-store".
//
// STRICTLY READ-ONLY. This screen NEVER reserves/allocates a unit and NEVER
// changes on-hand math — it only reports. The write-path allocation (marking
// units RESERVED on online-order ingest, excluding them from on-hand) is a
// deliberate, separately-reviewed follow-up: it is concurrency-sensitive
// (needs an atomic find_one_and_update claim + idempotency, mirroring the POS
// oversell claim) and changes revenue/availability behaviour, and online orders
// aren't live yet. So this ships the safe reporting view now, nothing more.
//
// Data: GET /api/v1/online-store/stock-tally (onlineStoreApi.getStockTally),
// reusing the on-hand / reserved aggregations behind the SUPERADMIN sync-health
// tile. FAIL-SOFT: any error (404 stale deploy / 403 outside the ecom gate)
// degrades to a friendly "coming online" note, never a white screen.
//
// Gated at the route (App.tsx) to the ecom role set: SUPERADMIN / ADMIN /
// CATALOG_MANAGER / DESIGN_MANAGER. Light theme only.

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Boxes,
  ArrowLeft,
  RefreshCw,
  Loader2,
  Search,
  Info,
  AlertTriangle,
  CheckCircle2,
  ShieldCheck,
} from 'lucide-react';
import {
  onlineStoreApi,
  type StockTallyRow,
  type StockTallySummary,
} from '../../services/api/onlineStore';

type RiskFilter = 'ALL' | 'AT_RISK';

function fmtInt(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—';
  try {
    return n.toLocaleString('en-IN');
  } catch {
    return String(n);
  }
}

// ===========================================================================
// Page
// ===========================================================================
export default function OnlineStockPage() {
  const [items, setItems] = useState<StockTallyRow[]>([]);
  const [summary, setSummary] = useState<StockTallySummary | null>(null);
  const [available, setAvailable] = useState(true);
  const [onlineConfigured, setOnlineConfigured] = useState(true);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<RiskFilter>('ALL');
  const [search, setSearch] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await onlineStoreApi.getStockTally();
      setItems(res.items);
      setSummary(res.summary);
      setAvailable(res.available);
      setOnlineConfigured(res.summary.online_configured);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const searchFiltered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return items;
    return items.filter((r) =>
      [r.sku, r.name].filter(Boolean).some((v) => String(v).toLowerCase().includes(q)),
    );
  }, [items, search]);

  const counts = useMemo(() => {
    const atRisk = searchFiltered.filter((r) => r.oversell_risk).length;
    return { ALL: searchFiltered.length, AT_RISK: atRisk };
  }, [searchFiltered]);

  const visible = useMemo(
    () => (filter === 'AT_RISK' ? searchFiltered.filter((r) => r.oversell_risk) : searchFiltered),
    [searchFiltered, filter],
  );

  const atRiskCount = summary?.at_risk_count ?? 0;

  return (
    <div className="p-6 max-w-6xl mx-auto">
      {/* Header + breadcrumb */}
      <div className="flex flex-wrap items-start justify-between gap-3 mb-1">
        <div>
          <div className="flex items-center gap-2 text-xs text-gray-500 mb-1">
            <Link to="/online-store" className="inline-flex items-center gap-1 hover:text-gray-700">
              <ArrowLeft className="w-3.5 h-3.5" /> Online Store
            </Link>
            <span>/</span>
            <span className="text-gray-700">Stock tally</span>
          </div>
          <h1 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
            <Boxes className="w-5 h-5" /> Stock tally
          </h1>
        </div>
        <button
          type="button"
          onClick={load}
          className="btn-outline inline-flex items-center gap-1.5 text-sm"
          title="Reload"
        >
          <RefreshCw className={'w-4 h-4 ' + (loading ? 'animate-spin' : '')} /> Refresh
        </button>
      </div>
      <p className="text-sm text-gray-500 mb-4 max-w-3xl">
        Reconciles the quantity each SKU lists online against the real physical on-hand and what is
        already reserved, so you never sell the same unit twice. This view is{' '}
        <span className="font-medium text-gray-700">read-only</span> — it reports the numbers and a
        suggested safety buffer; it does not change stock or reserve anything.
      </p>

      {/* Summary strip */}
      {!loading && available && summary && (
        <div className="mb-4 grid gap-3 grid-cols-2 sm:grid-cols-3 lg:grid-cols-5">
          <SummaryStat label="SKUs online" value={summary.skus_checked} />
          <SummaryStat
            label="Oversell-risk"
            value={summary.at_risk_count}
            tone={summary.at_risk_count > 0 ? 'danger' : 'ok'}
          />
          <SummaryStat label="Listed online" value={summary.total_online_listed} />
          <SummaryStat label="On-hand" value={summary.total_on_hand} />
          <SummaryStat label="Sellable" value={summary.total_sellable} />
        </div>
      )}

      {/* Oversell-risk banner — only when something is at risk. */}
      {!loading && available && atRiskCount > 0 && (
        <div className="mb-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="inline-flex items-center gap-1.5 rounded-full bg-red-100 text-red-700 border border-red-200 px-2.5 py-1 text-xs font-semibold">
            <AlertTriangle className="w-3.5 h-3.5" /> {atRiskCount} SKU{atRiskCount !== 1 ? 's' : ''} at
            oversell risk
          </span>
          <span className="text-sm text-red-900">
            These list more online than is free to sell — reduce the online quantity (or restock)
            before they can oversell.
          </span>
        </div>
      )}

      {/* No Shopify-mapped products yet: nothing to tally. */}
      {!loading && available && !onlineConfigured && (
        <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 flex items-start gap-2">
          <Info className="w-4 h-4 text-amber-600 mt-0.5 shrink-0" />
          <span className="text-sm text-amber-900">
            No products in the catalog are mapped to Shopify yet, so there is nothing to tally.
            Push products to the online store and they appear here.
          </span>
        </div>
      )}

      {/* Live Shopify read unavailable or PARTIAL: uncovered rows show "—". */}
      {!loading && available && onlineConfigured && summary?.listed_qty_live === false && (
        <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 flex items-start gap-2">
          <Info className="w-4 h-4 text-amber-600 mt-0.5 shrink-0" />
          <span className="text-sm text-amber-900">
            {(summary?.listed_live_rows ?? 0) > 0
              ? `Live Shopify quantities cover ${summary?.listed_live_rows} of ${summary?.listed_mapped_rows} mapped SKUs. Rows showing — were not covered by this read and are unverified (no oversell flag can fire for them).`
              : 'Live Shopify quantities are unavailable right now, so "Listed online" shows — (unknown) and oversell flags can\'t fire. On-hand, reserved and sellable counts are live.'}
          </span>
        </div>
      )}

      {/* Filter chips + search toolbar */}
      {!loading && available && items.length > 0 && (
        <div className="mb-4 flex flex-wrap items-center gap-2">
          {(['ALL', 'AT_RISK'] as RiskFilter[]).map((f) => {
            const active = filter === f;
            const label = f === 'ALL' ? 'All' : 'Oversell-risk';
            return (
              <button
                key={f}
                type="button"
                onClick={() => setFilter(f)}
                className={active ? 'ims-chip ims-chip--on' : 'ims-chip'}
              >
                {label}
                <span className="inline-flex items-center justify-center min-w-[1.25rem] rounded-full px-1 text-[11px] bg-gray-100 text-gray-600">
                  {counts[f]}
                </span>
              </button>
            );
          })}
          <div className="relative flex-1 min-w-[220px] max-w-md">
            <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by SKU or name…"
              className="input-field w-full pl-9"
            />
          </div>
        </div>
      )}

      {/* Body */}
      {loading ? (
        <div className="rounded-xl border border-gray-200 bg-white p-6 flex items-center gap-2 text-sm text-gray-500">
          <Loader2 className="w-4 h-4 animate-spin" /> Loading stock tally…
        </div>
      ) : !available ? (
        <div className="rounded-xl border border-blue-200 bg-blue-50 p-6 text-center">
          <Boxes className="w-10 h-10 mx-auto mb-2 text-blue-400" />
          <p className="text-sm font-medium text-blue-900">Stock tally is coming online</p>
          <p className="text-xs text-blue-700 mt-1 max-w-md mx-auto">
            The reconciliation view appears here once the module backend is deployed. Nothing changes
            for the storefront in the meantime.
          </p>
        </div>
      ) : visible.length === 0 ? (
        <div className="rounded-xl border border-gray-200 bg-white p-10 text-center text-gray-500">
          <Boxes className="w-10 h-10 mx-auto mb-2 opacity-50" />
          <p className="text-sm">
            {search || filter !== 'ALL'
              ? 'No SKUs match this view.'
              : 'No SKUs are listed online yet. Once products go live online, they show up here.'}
          </p>
        </div>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-gray-200 bg-white">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-100 text-left text-xs text-gray-500">
                <th className="px-4 py-2.5 font-medium">SKU / Product</th>
                <th className="px-4 py-2.5 font-medium text-right">Listed online</th>
                <th className="px-4 py-2.5 font-medium text-right">On-hand</th>
                <th className="px-4 py-2.5 font-medium text-right">Reserved</th>
                <th className="px-4 py-2.5 font-medium text-right">Sellable</th>
                <th className="px-4 py-2.5 font-medium text-right" title="Suggested units to keep off the listing (not enforced)">
                  Buffer
                </th>
                <th className="px-4 py-2.5 font-medium text-right">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {visible.map((r, idx) => (
                <tr
                  key={r.sku || idx}
                  className={r.oversell_risk ? 'bg-red-50/60' : 'hover:bg-gray-50/60'}
                >
                  <td className="px-4 py-2.5">
                    <div className="font-medium text-gray-900">{r.sku || '—'}</div>
                    {r.name && <div className="text-xs text-gray-500 truncate max-w-xs">{r.name}</div>}
                  </td>
                  <td className="px-4 py-2.5 text-right tabular-nums text-gray-900">
                    {fmtInt(r.online_listed_qty)}
                  </td>
                  <td className="px-4 py-2.5 text-right tabular-nums text-gray-700">
                    {fmtInt(r.on_hand)}
                  </td>
                  <td className="px-4 py-2.5 text-right tabular-nums text-gray-700">
                    {fmtInt(r.reserved)}
                  </td>
                  <td className="px-4 py-2.5 text-right tabular-nums font-semibold text-gray-900">
                    {fmtInt(r.sellable)}
                  </td>
                  <td className="px-4 py-2.5 text-right tabular-nums text-gray-500">
                    {fmtInt(r.recommended_buffer)}
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    {r.oversell_risk ? (
                      <span className="inline-flex items-center gap-1 rounded-full bg-red-100 text-red-700 border border-red-200 px-2 py-0.5 text-[11px] font-medium">
                        <AlertTriangle className="w-3 h-3" /> Oversell risk
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 rounded-full bg-green-100 text-green-800 border border-green-200 px-2 py-0.5 text-[11px] font-medium">
                        <CheckCircle2 className="w-3 h-3" /> OK
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Read-only + buffer note. */}
      <div className="mt-4 flex items-start gap-2 text-xs text-gray-500 max-w-3xl">
        <ShieldCheck className="w-3.5 h-3.5 mt-0.5 shrink-0 text-gray-400" />
        <p>
          <span className="font-medium text-gray-600">Read-only:</span> nothing here reserves or moves
          stock. <span className="font-medium text-gray-600">Buffer</span> is a suggestion — keep about
          max(1, 5%) of on-hand off the online listing so a walk-in sale can't strand an online order.
          Automatic reservation of units on online-order ingest is a separate, upcoming step.
        </p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Small summary stat card
// ---------------------------------------------------------------------------
function SummaryStat({
  label,
  value,
  tone = 'neutral',
}: {
  label: string;
  value: number;
  tone?: 'neutral' | 'ok' | 'danger';
}) {
  const valueClass =
    tone === 'danger' && value > 0
      ? 'text-red-700'
      : tone === 'ok'
        ? 'text-green-700'
        : 'text-gray-900';
  return (
    <div className="rounded-xl border border-gray-200 bg-white px-3 py-2.5">
      <div className={'text-lg font-semibold tabular-nums ' + valueClass}>{fmtInt(value)}</div>
      <div className="text-[11px] text-gray-500">{label}</div>
    </div>
  );
}

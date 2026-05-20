// ============================================================================
// IMS 2.0 — TechCherry R1.3 — Lens Deep Dive
// ============================================================================
// Brand / type / coating / refractive-index breakdown of lens sales. Joins
// order items against the products catalog. Spec: docs/TECHCHERRY_PORT_SCOPE.md §5.3.

import { useEffect, useState } from 'react';
import { AlertTriangle, Eye, Info, Loader2 } from 'lucide-react';
import { reportsApi } from '../../../services/api';

type Data = Awaited<ReturnType<typeof reportsApi.getLensDeepDive>>;

function inr(n: number): string {
  if (n >= 10000000) return '₹' + (n / 10000000).toFixed(2) + 'Cr';
  if (n >= 100000) return '₹' + (n / 100000).toFixed(2) + 'L';
  if (n >= 1000) return '₹' + (n / 1000).toFixed(1) + 'K';
  return '₹' + Math.round(n).toLocaleString('en-IN');
}

function pct(n: number, places = 1): string {
  return (n * 100).toFixed(places) + '%';
}

function MiniList({ title, rows, keyField, emptyHint }: {
  title: string;
  rows: Array<Record<string, any>>;
  keyField: string;
  emptyHint: string;
}) {
  return (
    <div className="bg-white rounded p-3 border border-gray-100">
      <p className="text-xs font-medium text-gray-700 mb-2">{title}</p>
      {rows.length === 0 ? (
        <p className="text-[11px] text-gray-400">{emptyHint}</p>
      ) : (
        <table className="w-full text-xs">
          <tbody>
            {rows.slice(0, 6).map(r => (
              <tr key={r[keyField]} className="border-b last:border-b-0">
                <td className="py-1 pr-2 text-gray-700">{r[keyField]}</td>
                <td className="py-1 px-2 text-right tabular-nums text-gray-500">{r.units}</td>
                <td className="py-1 pl-2 text-right tabular-nums">{inr(r.revenue)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export function LensDeepDiveCard({ storeId }: { storeId?: string }) {
  const [data, setData] = useState<Data | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    reportsApi.getLensDeepDive(storeId, 12)
      .then(setData)
      .catch(e => setError(e?.response?.data?.detail || e?.message || 'Failed to load'))
      .finally(() => setLoading(false));
  }, [storeId]);

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold text-gray-900 inline-flex items-center gap-2">
          <Eye className="w-4 h-4 text-bv-red-600" />
          Lens Deep Dive
        </h3>
        <span className="text-xs text-gray-400">last 12 months</span>
      </div>
      <p className="text-xs text-gray-500 mb-4 max-w-3xl">
        Brand / type / coating / refractive-index breakdown of lens and contact-lens sales.
        Parse rate shows how much catalog metadata is populated.
      </p>

      {loading && (
        <div className="h-32 flex items-center justify-center">
          <Loader2 className="w-6 h-6 animate-spin text-bv-red-600" />
        </div>
      )}
      {error && (
        <div className="flex items-start gap-2 text-sm text-red-700 bg-red-50 border border-red-200 rounded p-3">
          <AlertTriangle className="w-4 h-4 mt-0.5" /> {error}
        </div>
      )}

      {data && !loading && (
        <>
          <div className="grid grid-cols-2 tablet:grid-cols-5 gap-3 mb-4">
            <div className="bg-white rounded p-3 border border-gray-100">
              <p className="text-xs text-gray-500">Lens units</p>
              <p className="text-lg font-bold text-gray-900 mt-1 tabular-nums">{data.totals.lens_units}</p>
            </div>
            <div className="bg-white rounded p-3 border border-gray-100">
              <p className="text-xs text-gray-500">Lens revenue</p>
              <p className="text-lg font-bold text-gray-900 mt-1 tabular-nums">{inr(data.totals.lens_revenue)}</p>
            </div>
            <div className="bg-white rounded p-3 border border-gray-100">
              <p className="text-xs text-gray-500">ATV / lens</p>
              <p className="text-lg font-bold text-gray-900 mt-1 tabular-nums">{inr(data.totals.atv)}</p>
            </div>
            <div className="bg-white rounded p-3 border border-gray-100">
              <p className="text-xs text-gray-500">Contact lens</p>
              <p className="text-lg font-bold text-gray-900 mt-1 tabular-nums">{data.totals.contact_lens_units}</p>
            </div>
            <div className="bg-white rounded p-3 border border-gray-100">
              <p className="text-xs text-gray-500">Parse rate</p>
              <p className={`text-lg font-bold mt-1 tabular-nums ${
                data.parse_rate >= 0.8 ? 'text-emerald-600' :
                data.parse_rate >= 0.5 ? 'text-amber-600' : 'text-red-600'
              }`}>
                {pct(data.parse_rate, 0)}
              </p>
            </div>
          </div>

          {data.metadata_pending && (
            <div className="flex items-start gap-2 text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded p-2 mb-3">
              <Info className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
              <span>
                No lens metadata in catalog yet (no <code>lens_type</code> / <code>coating</code>{' '}
                / <code>refractive_index</code> on product.attributes). Type / coating / index
                breakdowns will populate once Catalog products are enriched.
              </span>
            </div>
          )}

          <div className="grid grid-cols-1 tablet:grid-cols-2 laptop:grid-cols-4 gap-3">
            <MiniList
              title="By Brand"
              rows={data.by_brand.map(r => ({ ...r, name: r.brand }))}
              keyField="brand"
              emptyHint="No lens sales yet."
            />
            <MiniList
              title="By Type"
              rows={data.by_type}
              keyField="type"
              emptyHint="Add product.attributes.lens_type"
            />
            <MiniList
              title="By Coating"
              rows={data.by_coating}
              keyField="coating"
              emptyHint="Add product.attributes.coating"
            />
            <MiniList
              title="By Refractive Index"
              rows={data.by_refractive_index}
              keyField="index"
              emptyHint="Add product.attributes.refractive_index"
            />
          </div>
        </>
      )}
    </div>
  );
}

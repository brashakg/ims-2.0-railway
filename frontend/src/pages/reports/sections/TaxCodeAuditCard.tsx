// ============================================================================
// IMS 2.0 — Go-live readiness: Tax-Code Audit
// ============================================================================
// Read-only worklist of products whose stored HSN code / GST rate disagrees
// with the canonical table for their category. POS bills whatever is on the
// product, so a wrong code here = wrong GST on every sale of it. Fix the
// flagged rows in Catalog before the first live invoice. Finance-role gated.

import { useEffect, useState } from 'react';
import { AlertTriangle, Loader2, ShieldCheck, Download } from 'lucide-react';
import { reportsApi } from '../../../services/api';

type Data = Awaited<ReturnType<typeof reportsApi.getTaxCodeAudit>>;

function downloadCsv(rows: Data['data']) {
  const header = [
    'product_id', 'sku', 'name', 'category',
    'stored_hsn', 'stored_gst', 'expected_hsn', 'expected_gst',
    'severity', 'issues',
  ];
  const esc = (v: unknown) => {
    const s = v === null || v === undefined ? '' : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const body = rows.map(r => [
    r.product_id, r.sku, r.name, r.category ?? '',
    r.stored_hsn ?? '', r.stored_gst ?? '', r.expected_hsn ?? '', r.expected_gst ?? '',
    r.severity, r.issues.join(' | '),
  ].map(esc).join(','));
  const blob = new Blob([[header.join(','), ...body].join('\n')], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `tax-code-audit-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

export function TaxCodeAuditCard({ storeId, canExport = true }: { storeId?: string; canExport?: boolean }) {
  const [data, setData] = useState<Data | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    reportsApi.getTaxCodeAudit(storeId)
      .then(setData)
      .catch(e => setError(e?.response?.data?.detail || e?.message || 'Failed to load'))
      .finally(() => setLoading(false));
  }, [storeId]);

  const s = data?.summary;
  const allClear = s && s.flagged === 0 && s.total_products > 0;

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold text-gray-900 inline-flex items-center gap-2">
          <ShieldCheck className="w-4 h-4 text-bv-red-600" />
          Tax-Code Audit
        </h3>
        {canExport && data && data.data.length > 0 && (
          <button
            onClick={() => downloadCsv(data.data)}
            className="text-xs inline-flex items-center gap-1 text-bv-red-600 hover:text-bv-red-700"
          >
            <Download className="w-3.5 h-3.5" /> Export CSV
          </button>
        )}
      </div>
      <p className="text-xs text-gray-500 mb-4 max-w-3xl">
        Go-live check. Flags any product whose <strong>HSN code</strong> or <strong>GST %</strong> doesn't
        match its category (e.g. a sunglass at 5% instead of 18%, or a blank category that
        defaults to 5%). POS bills whatever is on the product — fix the flagged rows in
        Catalog before your first live invoice. This report never edits anything.
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

      {s && !loading && (
        <>
          <div className="grid grid-cols-2 tablet:grid-cols-4 gap-3 mb-4">
            <div className="bg-white rounded p-3 border border-gray-100">
              <p className="text-xs text-gray-500">Products checked</p>
              <p className="text-lg font-bold text-gray-900 mt-1 tabular-nums">{s.total_products}</p>
            </div>
            <div className="bg-white rounded p-3 border border-gray-100">
              <p className="text-xs text-gray-500">Flagged</p>
              <p className={`text-lg font-bold mt-1 tabular-nums ${s.flagged > 0 ? 'text-red-600' : 'text-emerald-600'}`}>
                {s.flagged}
              </p>
            </div>
            <div className="bg-white rounded p-3 border border-gray-100">
              <p className="text-xs text-gray-500">Wrong GST %</p>
              <p className={`text-lg font-bold mt-1 tabular-nums ${s.gst_mismatch > 0 ? 'text-red-600' : 'text-gray-900'}`}>
                {s.gst_mismatch}
              </p>
            </div>
            <div className="bg-white rounded p-3 border border-gray-100">
              <p className="text-xs text-gray-500">HSN / no category</p>
              <p className={`text-lg font-bold mt-1 tabular-nums ${(s.hsn_mismatch + s.uncategorized) > 0 ? 'text-amber-600' : 'text-gray-900'}`}>
                {s.hsn_mismatch + s.uncategorized}
              </p>
            </div>
          </div>

          {allClear && (
            <div className="flex items-center gap-2 text-sm text-emerald-700 bg-emerald-50 border border-emerald-200 rounded p-3">
              <ShieldCheck className="w-4 h-4" /> All {s.total_products} products have correct tax codes. Ready to bill.
            </div>
          )}
          {s.total_products === 0 && (
            <div className="text-sm text-gray-400 text-center py-4">
              No products loaded yet. Load your catalog, then re-run this check.
            </div>
          )}

          {data && data.data.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-xs text-gray-500 border-b">
                  <tr>
                    <th className="text-left py-2 px-2 font-medium">Product</th>
                    <th className="text-left py-2 px-2 font-medium">Category</th>
                    <th className="text-right py-2 px-2 font-medium">Stored GST</th>
                    <th className="text-right py-2 px-2 font-medium">Should be</th>
                    <th className="text-left py-2 px-2 font-medium">HSN (stored → expected)</th>
                    <th className="text-left py-2 px-2 font-medium">Issue</th>
                  </tr>
                </thead>
                <tbody>
                  {data.data.slice(0, 100).map((r) => (
                    <tr key={r.product_id} className="border-b last:border-b-0 align-top">
                      <td className="py-1.5 px-2">
                        <span className="font-medium text-gray-900">{r.name || r.sku || r.product_id}</span>
                        {r.sku && r.name && <span className="block text-[10px] text-gray-400 font-mono">{r.sku}</span>}
                      </td>
                      <td className="py-1.5 px-2 text-gray-600">{r.category || <span className="text-red-500">— none —</span>}</td>
                      <td className="py-1.5 px-2 text-right tabular-nums">{r.stored_gst === null ? '—' : `${r.stored_gst}%`}</td>
                      <td className="py-1.5 px-2 text-right tabular-nums font-medium text-gray-900">{r.expected_gst === null ? '?' : `${r.expected_gst}%`}</td>
                      <td className="py-1.5 px-2 font-mono text-xs text-gray-600">
                        {(r.stored_hsn || '—')} <span className="text-gray-400">→</span> {(r.expected_hsn || '?')}
                      </td>
                      <td className="py-1.5 px-2">
                        <span className={`inline-block text-[10px] font-semibold px-1.5 py-0.5 rounded mr-1 ${
                          r.severity === 'CRITICAL' ? 'bg-red-100 text-red-700' : 'bg-amber-100 text-amber-700'
                        }`}>
                          {r.severity}
                        </span>
                        <span className="text-xs text-gray-600">{r.issues[0]}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {data.data.length > 100 && (
                <p className="text-xs text-gray-400 mt-2 text-center">
                  Showing first 100 of {data.data.length}. Export CSV for the full list.
                </p>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

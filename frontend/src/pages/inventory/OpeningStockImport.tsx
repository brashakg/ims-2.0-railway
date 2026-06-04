// ============================================================================
// IMS 2.0 — Opening-Stock Importer (go-live)
// ============================================================================
// Bulk-seed shelf quantities at go-live. Paste/upload a CSV of
// product_id-or-sku + quantity, PREVIEW (dry-run, never writes), then COMMIT.
// Preview-first by design: the owner sees exactly what will happen — including
// products that already hold stock — before any write. skip_if_existing (on by
// default) means a double-submit can't double inventory.

import { useState } from 'react';
import {
  UploadCloud, Eye, CheckCircle2, AlertTriangle, Loader2, ArrowRight, ShieldCheck,
} from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { inventoryApi } from '../../services/api';
import type {
  OpeningStockInputRow, OpeningStockResultRow, OpeningStockResponse,
} from '../../services/api/inventory';

const SAMPLE = `sku,quantity,location_code
RB3025-001,12,Counter-A
ZEISS-15-HMC,8,Shelf-2
CL-ACUVUE-OASYS,20,`;

/** Minimal CSV parser (no quoted-comma support) -> import rows. First line is
 *  the header; recognized columns: product_id, sku, quantity, location_code,
 *  batch_code, expiry_date. A row needs product_id OR sku, and quantity > 0. */
function parseCsv(text: string): { rows: OpeningStockInputRow[]; skipped: number } {
  const lines = text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  if (lines.length < 2) return { rows: [], skipped: 0 };
  const headers = lines[0].split(',').map((h) => h.trim().toLowerCase());
  const rows: OpeningStockInputRow[] = [];
  let skipped = 0;
  for (let i = 1; i < lines.length; i++) {
    const cells = lines[i].split(',').map((c) => c.trim());
    const get = (k: string) => {
      const idx = headers.indexOf(k);
      return idx >= 0 ? (cells[idx] ?? '') : '';
    };
    const qty = Number(get('quantity') || 0);
    const product_id = get('product_id');
    const sku = get('sku');
    if ((!product_id && !sku) || !qty || qty <= 0) {
      skipped += 1;
      continue;
    }
    rows.push({
      ...(product_id ? { product_id } : {}),
      ...(sku ? { sku } : {}),
      quantity: Math.floor(qty),
      ...(get('location_code') ? { location_code: get('location_code') } : {}),
      ...(get('batch_code') ? { batch_code: get('batch_code') } : {}),
      ...(get('expiry_date') ? { expiry_date: get('expiry_date') } : {}),
    });
  }
  return { rows, skipped };
}

const STATUS_STYLE: Record<OpeningStockResultRow['status'], string> = {
  WILL_ADD: 'bg-emerald-100 text-emerald-700',
  WILL_ADD_ON_TOP: 'bg-amber-100 text-amber-700',
  SKIP_EXISTING: 'bg-gray-100 text-gray-600',
  ADDED: 'bg-emerald-100 text-emerald-700',
  SKIPPED: 'bg-gray-100 text-gray-600',
  ERROR: 'bg-red-100 text-red-700',
};

export function OpeningStockImport() {
  const toast = useToast();
  const [csvText, setCsvText] = useState('');
  const [skipIfExisting, setSkipIfExisting] = useState(true);
  const [preview, setPreview] = useState<OpeningStockResponse | null>(null);
  const [committed, setCommitted] = useState<OpeningStockResponse | null>(null);
  const [busy, setBusy] = useState(false);

  const onFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      setCsvText(String(reader.result || ''));
      setPreview(null);
      setCommitted(null);
    };
    reader.readAsText(file);
  };

  const runPreview = async () => {
    const { rows, skipped } = parseCsv(csvText);
    if (rows.length === 0) {
      toast.error('No valid rows. Need a header line plus rows with sku/product_id and quantity.');
      return;
    }
    if (skipped > 0) toast.info(`${skipped} row(s) skipped (missing product or quantity).`);
    setBusy(true);
    setCommitted(null);
    try {
      const res = await inventoryApi.previewOpeningStock(rows, skipIfExisting);
      setPreview(res);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || 'Preview failed');
    } finally {
      setBusy(false);
    }
  };

  const runCommit = async () => {
    const { rows } = parseCsv(csvText);
    if (rows.length === 0) return;
    setBusy(true);
    try {
      const res = await inventoryApi.commitOpeningStock(rows, skipIfExisting);
      setCommitted(res);
      setPreview(null);
      toast.success(`Added ${res.summary.units_added ?? 0} unit(s).`);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || 'Import failed');
    } finally {
      setBusy(false);
    }
  };

  const result = committed || preview;
  const isCommitted = !!committed;

  return (
    <div className="p-6 space-y-4 max-w-5xl mx-auto">
      <div>
        <h1 className="text-xl font-bold text-gray-900 inline-flex items-center gap-2">
          <UploadCloud className="w-5 h-5 text-bv-red-600" /> Opening-Stock Import
        </h1>
        <p className="text-sm text-gray-500 mt-1">
          Seed your shelf quantities for go-live. Upload or paste a CSV, <strong>preview</strong>{' '}
          to see exactly what will happen, then <strong>commit</strong>. Products that already
          have stock are skipped by default, so running it twice won't double your inventory.
        </p>
      </div>

      <div className="card space-y-3">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <label className="btn-outline text-sm inline-flex items-center gap-2 cursor-pointer">
            <UploadCloud className="w-4 h-4" /> Choose CSV file
            <input type="file" accept=".csv,text/csv" onChange={onFile} className="hidden" />
          </label>
          <button
            type="button"
            onClick={() => { setCsvText(SAMPLE); setPreview(null); setCommitted(null); }}
            className="text-xs text-bv-red-600 hover:text-bv-red-700"
          >
            Load sample
          </button>
        </div>

        <textarea
          value={csvText}
          onChange={(e) => { setCsvText(e.target.value); setPreview(null); setCommitted(null); }}
          rows={8}
          placeholder={SAMPLE}
          className="input-field w-full font-mono text-xs"
        />
        <p className="text-xs text-gray-500">
          First line is the header. Columns: <code>product_id</code> or <code>sku</code> (one
          required), <code>quantity</code>, and optional <code>location_code</code>,{' '}
          <code>batch_code</code>, <code>expiry_date</code> (YYYY-MM-DD).
        </p>

        <label className="flex items-center gap-2 text-sm text-gray-700">
          <input
            type="checkbox"
            checked={skipIfExisting}
            onChange={(e) => setSkipIfExisting(e.target.checked)}
          />
          Skip products that already have stock (recommended — prevents double-counting)
        </label>

        <div className="flex items-center gap-2">
          <button onClick={runPreview} disabled={busy} className="btn-outline inline-flex items-center gap-2">
            {busy && !isCommitted ? <Loader2 className="w-4 h-4 animate-spin" /> : <Eye className="w-4 h-4" />}
            Preview
          </button>
          <button
            onClick={runCommit}
            disabled={busy || !preview || (preview.summary.units_to_add ?? 0) === 0}
            className="btn-primary inline-flex items-center gap-2"
            title={!preview ? 'Preview first' : ''}
          >
            <ArrowRight className="w-4 h-4" /> Commit Import
          </button>
        </div>
      </div>

      {result && (
        <div className="card space-y-3">
          <div className="flex items-center gap-2">
            {isCommitted
              ? <CheckCircle2 className="w-5 h-5 text-emerald-600" />
              : <Eye className="w-5 h-5 text-bv-red-600" />}
            <h3 className="font-semibold text-gray-900">
              {isCommitted ? 'Import complete' : 'Preview — nothing written yet'}
            </h3>
          </div>

          <div className="grid grid-cols-2 tablet:grid-cols-4 gap-3">
            <div className="bg-white rounded p-3 border border-gray-100">
              <p className="text-xs text-gray-500">Rows</p>
              <p className="text-lg font-bold text-gray-900 tabular-nums">{result.summary.total_rows}</p>
            </div>
            <div className="bg-white rounded p-3 border border-gray-100">
              <p className="text-xs text-gray-500">{isCommitted ? 'Units added' : 'Units to add'}</p>
              <p className="text-lg font-bold text-emerald-600 tabular-nums">
                {isCommitted ? (result.summary.units_added ?? 0) : (result.summary.units_to_add ?? 0)}
              </p>
            </div>
            <div className="bg-white rounded p-3 border border-gray-100">
              <p className="text-xs text-gray-500">Skipped</p>
              <p className="text-lg font-bold text-gray-600 tabular-nums">
                {isCommitted ? (result.summary.rows_skipped ?? 0) : (result.summary.rows_to_skip ?? 0)}
              </p>
            </div>
            <div className="bg-white rounded p-3 border border-gray-100">
              <p className="text-xs text-gray-500">Errors</p>
              <p className={`text-lg font-bold tabular-nums ${result.summary.rows_with_errors > 0 ? 'text-red-600' : 'text-gray-900'}`}>
                {result.summary.rows_with_errors}
              </p>
            </div>
          </div>

          {!isCommitted && (result.summary.units_to_add ?? 0) > 0 && (
            <div className="flex items-center gap-2 text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded p-3">
              <ShieldCheck className="w-4 h-4" />
              Looks good. Click <strong>Commit Import</strong> to write {result.summary.units_to_add} unit(s).
            </div>
          )}

          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-xs text-gray-500 border-b">
                <tr>
                  <th className="text-left py-2 px-2 font-medium">#</th>
                  <th className="text-left py-2 px-2 font-medium">Product</th>
                  <th className="text-right py-2 px-2 font-medium">Existing</th>
                  <th className="text-right py-2 px-2 font-medium">Qty</th>
                  <th className="text-left py-2 px-2 font-medium">Result</th>
                </tr>
              </thead>
              <tbody>
                {result.rows.map((r) => (
                  <tr key={r.index} className="border-b last:border-b-0">
                    <td className="py-1.5 px-2 text-gray-400 tabular-nums">{r.index + 1}</td>
                    <td className="py-1.5 px-2">
                      <span className="text-gray-900">{r.name || r.sku || r.product_id || r.identifier || '—'}</span>
                      {r.sku && r.name && <span className="block text-[10px] text-gray-400 font-mono">{r.sku}</span>}
                    </td>
                    <td className="py-1.5 px-2 text-right tabular-nums text-gray-500">{r.existing ?? '—'}</td>
                    <td className="py-1.5 px-2 text-right tabular-nums">{r.added ?? r.quantity ?? '—'}</td>
                    <td className="py-1.5 px-2">
                      <span className={`inline-block text-[10px] font-semibold px-1.5 py-0.5 rounded mr-1 ${STATUS_STYLE[r.status]}`}>
                        {r.status.replace(/_/g, ' ')}
                      </span>
                      <span className="text-xs text-gray-600">{r.message}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {result.summary.rows_with_errors > 0 && (
            <div className="flex items-start gap-2 text-xs text-red-700 bg-red-50 border border-red-200 rounded p-3">
              <AlertTriangle className="w-4 h-4 mt-0.5" />
              Some rows couldn't be matched to a product. Fix the product_id / sku in your CSV and re-preview.
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default OpeningStockImport;

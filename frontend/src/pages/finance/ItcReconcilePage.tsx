// ============================================================================
// IMS 2.0 - GST Input Credit (ITC) + GSTR-2B reconciliation
// ============================================================================
// Shows the input tax credit available from booked vendor bills, and lets you
// reconcile it against a GSTR-2B export (paste CSV or upload a .csv): what's
// matched (safe to claim), mismatched, in your books but not in 2B (ITC at
// risk -> chase the vendor), or in 2B but not booked (book it then claim).

import { useCallback, useEffect, useState } from 'react';
import { Loader2, Upload, AlertTriangle, CheckCircle2, FileText } from 'lucide-react';
import { itcApi, type ItcRegister, type ReconcileResult, type Gstr2bRow } from '../../services/api/itc';
import { useToast } from '../../context/ToastContext';

const inr = (n?: number) => `₹${Math.round(Number(n) || 0).toLocaleString('en-IN')}`;

function parseCsv(text: string): Gstr2bRow[] {
  const lines = text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  if (!lines.length) return [];
  let start = 0;
  let idx = { gstin: 0, invoice_no: 1, taxable: 2, tax: 3 };
  if (/gstin|invoice|inv|tax/i.test(lines[0])) {
    const cols = lines[0].split(',').map((c) => c.trim().toLowerCase());
    const find = (names: string[], fallback: number) => {
      const i = cols.findIndex((c) => names.some((n) => c.includes(n)));
      return i >= 0 ? i : fallback;
    };
    idx = {
      gstin: find(['gstin', 'gst'], 0),
      invoice_no: find(['invoice', 'inv', 'bill', 'document', 'doc'], 1),
      taxable: find(['taxable', 'value', 'base'], 2),
      tax: find(['tax', 'igst', 'amount'], 3),
    };
    start = 1;
  }
  const rows: Gstr2bRow[] = [];
  for (let i = start; i < lines.length; i++) {
    const c = lines[i].split(',').map((x) => x.trim());
    if (!c[idx.gstin] && !c[idx.invoice_no]) continue;
    rows.push({
      gstin: c[idx.gstin],
      invoice_no: c[idx.invoice_no],
      taxable: parseFloat(c[idx.taxable]) || 0,
      tax: parseFloat(c[idx.tax]) || 0,
    });
  }
  return rows;
}

export default function ItcReconcilePage() {
  const toast = useToast();
  const [reg, setReg] = useState<ItcRegister | null>(null);
  const [loading, setLoading] = useState(true);
  const [csv, setCsv] = useState('');
  const [result, setResult] = useState<ReconcileResult | null>(null);
  const [running, setRunning] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try { setReg(await itcApi.register()); }
    catch (e) { toast.error(e instanceof Error ? e.message : 'Failed to load ITC register'); }
    finally { setLoading(false); }
  }, [toast]);
  useEffect(() => { load(); }, [load]);

  const runReconcile = async () => {
    const rows = parseCsv(csv);
    if (!rows.length) { toast.error('No rows found. Paste GSTR-2B CSV with columns: GSTIN, Invoice, Taxable, Tax.'); return; }
    setRunning(true);
    try {
      setResult(await itcApi.reconcile(rows));
      toast.success(`Reconciled ${rows.length} GSTR-2B rows`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Reconciliation failed');
    } finally { setRunning(false); }
  };

  const onFile = (f?: File) => {
    if (!f) return;
    const r = new FileReader();
    r.onload = () => setCsv(String(r.result || ''));
    r.readAsText(f);
  };

  return (
    <div className="p-6 max-w-5xl mx-auto">
      <h1 className="text-xl font-semibold text-gray-900 flex items-center gap-2 mb-1">
        <FileText className="w-5 h-5" /> GST Input Credit (ITC)
      </h1>
      <p className="text-sm text-gray-500 mb-5">Input tax credit from your vendor bills, and a GSTR-2B reconciliation to see what's safe to claim.</p>

      {/* ITC register */}
      {loading ? (
        <div className="flex items-center gap-2 text-gray-500"><Loader2 className="w-4 h-4 animate-spin" /> Loading...</div>
      ) : reg && (
        <div className="bg-white border border-gray-200 rounded-lg overflow-hidden mb-6">
          <div className="px-4 py-2 bg-gray-50 text-sm font-medium text-gray-700">
            ITC available (from vendor bills) &middot; total {inr(reg.total_itc)}
          </div>
          <table className="w-full text-sm">
            <thead className="text-xs text-gray-500">
              <tr><th className="text-left px-4 py-2">Period</th><th className="text-right px-4 py-2">Taxable</th><th className="text-right px-4 py-2">CGST</th><th className="text-right px-4 py-2">SGST</th><th className="text-right px-4 py-2">Total ITC</th><th className="text-right px-4 py-2">Bills</th></tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {reg.periods.length === 0 ? (
                <tr><td colSpan={6} className="px-4 py-6 text-center text-gray-400">No vendor bills booked yet.</td></tr>
              ) : reg.periods.map((p) => (
                <tr key={p.period}>
                  <td className="px-4 py-2 text-gray-700">{p.period}</td>
                  <td className="px-4 py-2 text-right">{inr(p.taxable)}</td>
                  <td className="px-4 py-2 text-right text-gray-500">{inr(p.cgst)}</td>
                  <td className="px-4 py-2 text-right text-gray-500">{inr(p.sgst)}</td>
                  <td className="px-4 py-2 text-right font-medium">{inr(p.tax)}</td>
                  <td className="px-4 py-2 text-right text-gray-500">{p.bills}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* GSTR-2B reconcile */}
      <h2 className="text-sm font-semibold text-gray-700 mb-2">Reconcile against GSTR-2B</h2>
      <p className="text-xs text-gray-500 mb-2">Download GSTR-2B from the GST portal, export the B2B rows as CSV (GSTIN, Invoice no, Taxable value, Tax), then paste or upload below.</p>
      <textarea value={csv} onChange={(e) => setCsv(e.target.value)} rows={5}
        placeholder="GSTIN,Invoice,Taxable,Tax&#10;27AAPFU0939F1ZV,INV-001,1000,50"
        className="w-full border border-gray-300 rounded-lg px-3 py-2 text-xs font-mono mb-2" />
      <div className="flex items-center gap-3 mb-5">
        <label className="inline-flex items-center gap-1.5 text-sm text-gray-600 hover:bg-gray-100 rounded-lg px-3 py-1.5 cursor-pointer border border-gray-200">
          <Upload className="w-4 h-4" /> Upload .csv
          <input type="file" accept=".csv,text/csv" className="hidden" onChange={(e) => onFile(e.target.files?.[0])} />
        </label>
        <button type="button" onClick={runReconcile} disabled={running}
          className="inline-flex items-center gap-1.5 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg px-4 py-1.5 disabled:opacity-60">
          {running && <Loader2 className="w-4 h-4 animate-spin" />} Reconcile
        </button>
      </div>

      {result && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
            <Card label="Matched (claim)" value={String(result.summary.matched)} sub={inr(result.summary.itc_safe_to_claim)} tone="good" />
            <Card label="Mismatch" value={String(result.summary.mismatch)} sub="tax differs" tone="warn" />
            <Card label="In books, not in 2B" value={String(result.summary.only_in_books)} sub={`${inr(result.summary.itc_at_risk)} ITC at risk`} tone="bad" />
            <Card label="In 2B, not booked" value={String(result.summary.only_in_2b)} sub="book then claim" />
          </div>

          <Bucket title="ITC at risk — in your books but NOT in GSTR-2B (chase the vendor)" rows={result.only_in_books}
            cols={['vendor_name', 'invoice_no', 'gstin', 'book_tax', 'days_old']} tone="bad" />
          <Bucket title="Tax mismatch (same invoice, different tax)" rows={result.mismatch}
            cols={['vendor_name', 'invoice_no', 'book_tax', 'portal_tax', 'diff']} tone="warn" />
          <Bucket title="In GSTR-2B but not booked (missing purchase entry)" rows={result.only_in_2b}
            cols={['gstin', 'invoice_no', 'taxable', 'tax']} />
        </>
      )}
    </div>
  );
}

function Card({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: 'good' | 'bad' | 'warn' }) {
  const c = tone === 'good' ? 'text-green-700' : tone === 'bad' ? 'text-red-700' : tone === 'warn' ? 'text-amber-700' : 'text-gray-900';
  return (
    <div className="bg-white border border-gray-200 rounded-lg p-3">
      <p className="text-xs text-gray-500 mb-1">{label}</p>
      <p className={`text-xl font-semibold ${c}`}>{value}</p>
      {sub && <p className="text-xs text-gray-400 mt-1">{sub}</p>}
    </div>
  );
}

function Bucket({ title, rows, cols, tone }: { title: string; rows: Array<Record<string, unknown>>; cols: string[]; tone?: 'bad' | 'warn' }) {
  if (!rows || rows.length === 0) return null;
  const head = tone === 'bad' ? 'text-red-700' : tone === 'warn' ? 'text-amber-700' : 'text-gray-700';
  const isMoney = (k: string) => /tax|taxable|diff/.test(k);
  return (
    <div className="mb-5">
      <p className={`text-sm font-medium mb-1 flex items-center gap-1.5 ${head}`}>
        {tone && <AlertTriangle className="w-4 h-4" />}{!tone && <CheckCircle2 className="w-4 h-4 text-gray-400" />}{title} ({rows.length})
      </p>
      <div className="bg-white border border-gray-200 rounded-lg overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="text-gray-400"><tr>{cols.map((c) => <th key={c} className={`px-3 py-1.5 ${isMoney(c) ? 'text-right' : 'text-left'}`}>{c.replace(/_/g, ' ')}</th>)}</tr></thead>
          <tbody className="divide-y divide-gray-100">
            {rows.slice(0, 200).map((r, i) => (
              <tr key={i}>
                {cols.map((c) => (
                  <td key={c} className={`px-3 py-1.5 ${isMoney(c) ? 'text-right' : 'text-left'} text-gray-700`}>
                    {isMoney(c) ? inr(r[c] as number) : String(r[c] ?? '')}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

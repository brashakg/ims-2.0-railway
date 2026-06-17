// ============================================================================
// IMS 2.0 - B2B invoices -> Tally Export console (Screen 1)
// ============================================================================
// Owner decision: GST e-invoice (IRN) + e-way bill are generated IN TALLY, not
// in IMS. This screen lets the accountant pull B2B sales invoices for a period
// + scope, download a Tally-importable XML (per-invoice or bulk for the
// selection), and mark which invoices have already been handed to Tally.
//
// Exporting (or marking exported) advances PENDING invoices to IN_TALLY so the
// companion Worklist screen reflects the same hand-off state.

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Download, Loader2, Receipt, CheckCircle2, AlertTriangle, FileDown,
} from 'lucide-react';
import { financeApi, type B2BInvoice, type B2BInvoiceSummary } from '../../services/api/finance';
import { storeApi } from '../../services/api/stores';
import { useToast } from '../../context/ToastContext';
import {
  inr, downloadBlob, currentMonthRange, TALLY_STATUS_STYLE, TALLY_STATUS_LABEL,
} from './b2bTallyShared';

interface StoreOpt { store_id: string; store_name?: string; store_code?: string }

export default function B2BTallyExport() {
  const toast = useToast();
  const def = useMemo(() => currentMonthRange(), []);
  const [fromDate, setFromDate] = useState(def.from);
  const [toDate, setToDate] = useState(def.to);
  const [storeId, setStoreId] = useState('');
  const [stores, setStores] = useState<StoreOpt[]>([]);
  const [invoices, setInvoices] = useState<B2BInvoice[]>([]);
  const [summary, setSummary] = useState<B2BInvoiceSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [rowBusy, setRowBusy] = useState<string | null>(null);

  useEffect(() => {
    storeApi
      .getStores()
      .then((r) => setStores((r?.stores as StoreOpt[]) || []))
      .catch(() => setStores([]));
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await financeApi.listB2BInvoices({
        from_date: fromDate || undefined,
        to_date: toDate || undefined,
        store_id: storeId || undefined,
      });
      setInvoices(r.invoices);
      setSummary(r.summary);
      setSelected(new Set());
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to load B2B invoices');
    } finally {
      setLoading(false);
    }
  }, [fromDate, toDate, storeId, toast]);

  useEffect(() => {
    load();
  }, [load]);

  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const allSelected = invoices.length > 0 && selected.size === invoices.length;
  const toggleAll = () =>
    setSelected(allSelected ? new Set() : new Set(invoices.map((i) => i.order_id)));

  const downloadOne = async (inv: B2BInvoice) => {
    setRowBusy(inv.order_id);
    try {
      const blob = await financeApi.downloadB2BInvoiceXml(inv.order_id);
      downloadBlob(blob, `b2b_tally_${inv.invoice_number.replace(/[\\/]/g, '-')}.xml`);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'XML download failed');
    } finally {
      setRowBusy(null);
    }
  };

  const exportSelected = async () => {
    const ids = [...selected];
    if (!ids.length) {
      toast.error('Select at least one invoice to export');
      return;
    }
    setBusy(true);
    try {
      const blob = await financeApi.exportB2BInvoicesToTally(ids, true);
      downloadBlob(blob, `b2b_tally_export_${toDate || 'all'}.xml`);
      toast.success(`Exported ${ids.length} invoice(s) to Tally XML`);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Export failed');
    } finally {
      setBusy(false);
    }
  };

  const markExported = async () => {
    const ids = [...selected];
    if (!ids.length) {
      toast.error('Select at least one invoice to mark exported');
      return;
    }
    setBusy(true);
    try {
      const res = await financeApi.markB2BInvoicesExported(ids);
      toast.success(`Marked ${res.marked} invoice(s) as exported to Tally`);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Mark-exported failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <h1 className="text-xl font-semibold text-gray-900 flex items-center gap-2 mb-1">
        <Receipt className="w-5 h-5" /> B2B Invoices &rarr; Tally Export
      </h1>
      <p className="text-sm text-gray-500 mb-5">
        Pull B2B sales invoices and download Tally-importable XML. Tally issues
        the GST e-invoice and e-way bill on import.
      </p>

      {/* Filters */}
      <div className="flex flex-wrap items-end gap-3 mb-5">
        <Field label="From">
          <input type="date" value={fromDate} onChange={(e) => setFromDate(e.target.value)}
            className="border border-gray-300 rounded-lg px-2 py-1.5 text-sm" />
        </Field>
        <Field label="To">
          <input type="date" value={toDate} onChange={(e) => setToDate(e.target.value)}
            className="border border-gray-300 rounded-lg px-2 py-1.5 text-sm" />
        </Field>
        <Field label="Store">
          <select value={storeId} onChange={(e) => setStoreId(e.target.value)}
            className="border border-gray-300 rounded-lg px-2 py-1.5 text-sm bg-white">
            <option value="">All stores</option>
            {stores.map((s) => (
              <option key={s.store_id} value={s.store_id}>
                {s.store_name || s.store_code || s.store_id}
              </option>
            ))}
          </select>
        </Field>
        <button type="button" onClick={load} disabled={loading}
          className="inline-flex items-center gap-1.5 text-sm font-medium text-white bg-bv hover:bg-bv-600 rounded-lg px-4 py-2 disabled:opacity-60">
          {loading && <Loader2 className="w-4 h-4 animate-spin" />} Refresh
        </button>
      </div>

      {/* Summary cards */}
      {summary && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-4">
          <Card label="B2B invoices" value={String(summary.count)} />
          <Card label="Total value" value={inr(summary.total_value)} />
          <Card label="Pending" value={String(summary.pending)} tone="warn" />
          <Card label="Exported" value={String(summary.exported)} tone="good" />
          <Card label="Need e-way bill" value={String(summary.needs_eway)} tone="warn" />
        </div>
      )}

      {/* Bulk action bar */}
      <div className="flex flex-wrap items-center gap-3 mb-2">
        <span className="text-sm text-gray-500">{selected.size} selected</span>
        <button type="button" onClick={exportSelected} disabled={busy || selected.size === 0}
          className="inline-flex items-center gap-1.5 text-sm font-medium text-white bg-bv hover:bg-bv-600 rounded-lg px-3 py-1.5 disabled:opacity-50">
          {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <FileDown className="w-4 h-4" />}
          Export to Tally (XML)
        </button>
        <button type="button" onClick={markExported} disabled={busy || selected.size === 0}
          className="inline-flex items-center gap-1.5 text-sm font-medium text-gray-700 border border-gray-300 hover:bg-gray-100 rounded-lg px-3 py-1.5 disabled:opacity-50">
          <CheckCircle2 className="w-4 h-4" /> Mark exported
        </button>
      </div>

      {/* Table */}
      <div className="bg-white border border-gray-200 rounded-lg overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-xs text-gray-500 bg-gray-50">
            <tr>
              <th className="px-3 py-2 text-left">
                <input type="checkbox" checked={allSelected} onChange={toggleAll}
                  aria-label="Select all invoices" />
              </th>
              <th className="px-3 py-2 text-left">Invoice</th>
              <th className="px-3 py-2 text-left">Date</th>
              <th className="px-3 py-2 text-left">Customer / GSTIN</th>
              <th className="px-3 py-2 text-right">Taxable</th>
              <th className="px-3 py-2 text-right">CGST</th>
              <th className="px-3 py-2 text-right">SGST</th>
              <th className="px-3 py-2 text-right">IGST</th>
              <th className="px-3 py-2 text-right">Total</th>
              <th className="px-3 py-2 text-center">e-Way</th>
              <th className="px-3 py-2 text-center">Status</th>
              <th className="px-3 py-2 text-center">XML</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {loading ? (
              <tr><td colSpan={12} className="px-4 py-6 text-center text-gray-400">
                <Loader2 className="w-4 h-4 animate-spin inline mr-1" /> Loading...
              </td></tr>
            ) : invoices.length === 0 ? (
              <tr><td colSpan={12} className="px-4 py-6 text-center text-gray-400">
                No B2B invoices for this period.
              </td></tr>
            ) : invoices.map((inv) => (
              <tr key={inv.order_id} className={selected.has(inv.order_id) ? 'bg-bv-50' : ''}>
                <td className="px-3 py-2">
                  <input type="checkbox" checked={selected.has(inv.order_id)}
                    onChange={() => toggle(inv.order_id)}
                    aria-label={`Select ${inv.invoice_number}`} />
                </td>
                <td className="px-3 py-2 font-medium text-gray-900">{inv.invoice_number}</td>
                <td className="px-3 py-2 text-gray-600">{inv.date}</td>
                <td className="px-3 py-2">
                  <div className="text-gray-900">{inv.customer_name}</div>
                  <div className="text-xs text-gray-400 font-mono">{inv.customer_gstin}</div>
                </td>
                <td className="px-3 py-2 text-right">{inr(inv.taxable)}</td>
                <td className="px-3 py-2 text-right text-gray-500">{inr(inv.cgst)}</td>
                <td className="px-3 py-2 text-right text-gray-500">{inr(inv.sgst)}</td>
                <td className="px-3 py-2 text-right text-gray-500">{inr(inv.igst)}</td>
                <td className="px-3 py-2 text-right font-medium">{inr(inv.total)}</td>
                <td className="px-3 py-2 text-center">
                  {inv.needs_eway ? (
                    <span title={inv.interstate ? 'Inter-state' : 'Value ≥ ₹50,000'}
                      className="inline-flex items-center gap-1 text-xs text-amber-700">
                      <AlertTriangle className="w-3.5 h-3.5" /> Yes
                    </span>
                  ) : <span className="text-xs text-gray-400">No</span>}
                </td>
                <td className="px-3 py-2 text-center">
                  <span className={`text-xs px-2 py-0.5 rounded-full ${TALLY_STATUS_STYLE[inv.tally_status]}`}>
                    {TALLY_STATUS_LABEL[inv.tally_status]}
                  </span>
                </td>
                <td className="px-3 py-2 text-center">
                  <button type="button" onClick={() => downloadOne(inv)} disabled={rowBusy === inv.order_id}
                    className="inline-flex items-center gap-1 text-xs text-gray-600 hover:text-gray-900 border border-gray-200 hover:border-gray-300 rounded px-2 py-1 disabled:opacity-60"
                    aria-label={`Download Tally XML for ${inv.invoice_number}`}>
                    {rowBusy === inv.order_id ? <Loader2 className="w-3 h-3 animate-spin" /> : <Download className="w-3 h-3" />}
                    XML
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-xs text-gray-500">{label}</span>
      {children}
    </label>
  );
}

function Card({ label, value, tone }: { label: string; value: string; tone?: 'good' | 'warn' | 'bad' }) {
  const c = tone === 'good' ? 'text-green-700' : tone === 'warn' ? 'text-amber-700' : tone === 'bad' ? 'text-red-700' : 'text-gray-900';
  return (
    <div className="bg-white border border-gray-200 rounded-lg p-3">
      <p className="text-xs text-gray-500 mb-1">{label}</p>
      <p className={`text-xl font-semibold ${c}`}>{value}</p>
    </div>
  );
}

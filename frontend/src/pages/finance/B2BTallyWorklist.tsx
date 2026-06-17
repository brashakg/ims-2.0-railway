// ============================================================================
// IMS 2.0 - B2B Tally Worklist / reminder (Screen 2)
// ============================================================================
// A LOG + REMINDER of which B2B invoices still need to be created in Tally and
// given special attention -- because the GST e-invoice + e-way bill are issued
// in Tally, not in IMS. Defaults to the PENDING backlog, oldest-first, so the
// accountant clears the queue. Per row: e-way flag, an overdue/age reminder, a
// free-text attention note, and a "Mark done in Tally" action.

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Loader2, ClipboardList, CheckCircle2, AlertTriangle, Clock, StickyNote,
} from 'lucide-react';
import { financeApi, type B2BInvoice, type B2BInvoiceSummary } from '../../services/api/finance';
import { useToast } from '../../context/ToastContext';
import {
  inr, currentMonthRange, TALLY_STATUS_STYLE, TALLY_STATUS_LABEL,
} from './b2bTallyShared';

type ViewFilter = 'PENDING' | 'ALL';

export default function B2BTallyWorklist() {
  const toast = useToast();
  const def = useMemo(() => currentMonthRange(), []);
  const [fromDate, setFromDate] = useState(def.from);
  const [toDate, setToDate] = useState(def.to);
  const [view, setView] = useState<ViewFilter>('PENDING'); // default = reminder backlog
  const [invoices, setInvoices] = useState<B2BInvoice[]>([]);
  const [summary, setSummary] = useState<B2BInvoiceSummary | null>(null);
  const [reminderDays, setReminderDays] = useState(3);
  const [loading, setLoading] = useState(false);
  const [rowBusy, setRowBusy] = useState<string | null>(null);
  const [noteDraft, setNoteDraft] = useState<Record<string, string>>({});
  const [editingNote, setEditingNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await financeApi.listB2BInvoices({
        from_date: fromDate || undefined,
        to_date: toDate || undefined,
        tally_status: view === 'PENDING' ? 'PENDING' : undefined,
      });
      // Oldest-first so the backlog is cleared from the top (PENDING reminder).
      const rows = [...r.invoices].sort((a, b) => (b.age_days ?? 0) - (a.age_days ?? 0));
      setInvoices(rows);
      setSummary(r.summary);
      setReminderDays(r.pending_reminder_days);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to load worklist');
    } finally {
      setLoading(false);
    }
  }, [fromDate, toDate, view, toast]);

  useEffect(() => {
    load();
  }, [load]);

  const markDone = async (inv: B2BInvoice) => {
    setRowBusy(inv.order_id);
    try {
      await financeApi.markB2BInvoiceDone(inv.order_id);
      toast.success(`${inv.invoice_number} marked done in Tally`);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Mark-done failed');
    } finally {
      setRowBusy(null);
    }
  };

  const saveNote = async (inv: B2BInvoice) => {
    const note = noteDraft[inv.order_id] ?? inv.attention_note;
    setRowBusy(inv.order_id);
    try {
      await financeApi.setB2BAttentionNote(inv.order_id, note);
      toast.success('Attention note saved');
      setEditingNote(null);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Note save failed');
    } finally {
      setRowBusy(null);
    }
  };

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <h1 className="text-xl font-semibold text-gray-900 flex items-center gap-2 mb-1">
        <ClipboardList className="w-5 h-5" /> B2B Tally Worklist
      </h1>
      <p className="text-sm text-gray-500 mb-5">
        Invoices that still need to be created in Tally (e-invoice + e-way bill
        are issued there). Clear the PENDING backlog oldest-first.
      </p>

      {/* Filters + view toggle */}
      <div className="flex flex-wrap items-end gap-3 mb-5">
        <Field label="From">
          <input type="date" value={fromDate} onChange={(e) => setFromDate(e.target.value)}
            className="border border-gray-300 rounded-lg px-2 py-1.5 text-sm" />
        </Field>
        <Field label="To">
          <input type="date" value={toDate} onChange={(e) => setToDate(e.target.value)}
            className="border border-gray-300 rounded-lg px-2 py-1.5 text-sm" />
        </Field>
        <div className="flex rounded-lg border border-gray-300 overflow-hidden">
          {(['PENDING', 'ALL'] as ViewFilter[]).map((v) => (
            <button key={v} type="button" onClick={() => setView(v)}
              className={`px-3 py-1.5 text-sm ${view === v ? 'bg-bv text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}>
              {v === 'PENDING' ? 'Pending (reminder)' : 'All'}
            </button>
          ))}
        </div>
        <button type="button" onClick={load} disabled={loading}
          className="inline-flex items-center gap-1.5 text-sm font-medium text-gray-700 border border-gray-300 hover:bg-gray-100 rounded-lg px-3 py-2 disabled:opacity-60">
          {loading && <Loader2 className="w-4 h-4 animate-spin" />} Refresh
        </button>
      </div>

      {/* Summary */}
      {summary && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-4">
          <Card label="Pending" value={String(summary.pending)} tone="warn" />
          <Card label="Overdue" value={String(summary.overdue)} tone="bad"
            sub={`> ${reminderDays} days`} />
          <Card label="In Tally" value={String(summary.in_tally)} />
          <Card label="Done" value={String(summary.done)} tone="good" />
          <Card label="Need e-way bill" value={String(summary.needs_eway)} tone="warn" />
        </div>
      )}

      {/* Table */}
      <div className="bg-white border border-gray-200 rounded-lg overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-xs text-gray-500 bg-gray-50">
            <tr>
              <th className="px-3 py-2 text-left">Invoice</th>
              <th className="px-3 py-2 text-left">Date</th>
              <th className="px-3 py-2 text-left">Customer / GSTIN</th>
              <th className="px-3 py-2 text-right">Total</th>
              <th className="px-3 py-2 text-center">e-Way</th>
              <th className="px-3 py-2 text-center">Age</th>
              <th className="px-3 py-2 text-center">Status</th>
              <th className="px-3 py-2 text-left">Attention note</th>
              <th className="px-3 py-2 text-center">Action</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {loading ? (
              <tr><td colSpan={9} className="px-4 py-6 text-center text-gray-400">
                <Loader2 className="w-4 h-4 animate-spin inline mr-1" /> Loading...
              </td></tr>
            ) : invoices.length === 0 ? (
              <tr><td colSpan={9} className="px-4 py-6 text-center text-gray-400">
                {view === 'PENDING' ? 'No pending B2B invoices. Backlog is clear.' : 'No B2B invoices for this period.'}
              </td></tr>
            ) : invoices.map((inv) => (
              <tr key={inv.order_id} className={inv.overdue ? 'bg-red-50/40' : ''}>
                <td className="px-3 py-2 font-medium text-gray-900">{inv.invoice_number}</td>
                <td className="px-3 py-2 text-gray-600">{inv.date}</td>
                <td className="px-3 py-2">
                  <div className="text-gray-900">{inv.customer_name}</div>
                  <div className="text-xs text-gray-400 font-mono">{inv.customer_gstin}</div>
                </td>
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
                  {inv.age_days == null ? (
                    <span className="text-xs text-gray-400">-</span>
                  ) : (
                    <span className={`inline-flex items-center gap-1 text-xs ${inv.overdue ? 'text-red-700 font-medium' : 'text-gray-500'}`}>
                      {inv.overdue && <Clock className="w-3.5 h-3.5" />}{inv.age_days}d
                    </span>
                  )}
                </td>
                <td className="px-3 py-2 text-center">
                  <span className={`text-xs px-2 py-0.5 rounded-full ${TALLY_STATUS_STYLE[inv.tally_status]}`}>
                    {TALLY_STATUS_LABEL[inv.tally_status]}
                  </span>
                </td>
                <td className="px-3 py-2">
                  {editingNote === inv.order_id ? (
                    <div className="flex items-center gap-1">
                      <input
                        autoFocus
                        value={noteDraft[inv.order_id] ?? inv.attention_note}
                        onChange={(e) => setNoteDraft((d) => ({ ...d, [inv.order_id]: e.target.value }))}
                        placeholder="Special attention..."
                        className="border border-gray-300 rounded px-2 py-1 text-xs w-44" />
                      <button type="button" onClick={() => saveNote(inv)} disabled={rowBusy === inv.order_id}
                        className="text-xs text-bv hover:underline disabled:opacity-60">Save</button>
                      <button type="button" onClick={() => setEditingNote(null)}
                        className="text-xs text-gray-400 hover:underline">Cancel</button>
                    </div>
                  ) : (
                    <button type="button"
                      onClick={() => { setNoteDraft((d) => ({ ...d, [inv.order_id]: inv.attention_note })); setEditingNote(inv.order_id); }}
                      className="inline-flex items-center gap-1 text-xs text-left text-gray-600 hover:text-gray-900">
                      <StickyNote className="w-3.5 h-3.5 text-gray-400" />
                      {inv.attention_note || <span className="text-gray-400 italic">add note</span>}
                    </button>
                  )}
                </td>
                <td className="px-3 py-2 text-center">
                  {inv.tally_status === 'DONE' ? (
                    <span className="inline-flex items-center gap-1 text-xs text-green-700">
                      <CheckCircle2 className="w-3.5 h-3.5" /> Done
                    </span>
                  ) : (
                    <button type="button" onClick={() => markDone(inv)} disabled={rowBusy === inv.order_id}
                      className="inline-flex items-center gap-1 text-xs font-medium text-white bg-bv hover:bg-bv-600 rounded px-2 py-1 disabled:opacity-60">
                      {rowBusy === inv.order_id ? <Loader2 className="w-3 h-3 animate-spin" /> : <CheckCircle2 className="w-3 h-3" />}
                      Mark done
                    </button>
                  )}
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

function Card({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: 'good' | 'warn' | 'bad' }) {
  const c = tone === 'good' ? 'text-green-700' : tone === 'warn' ? 'text-amber-700' : tone === 'bad' ? 'text-red-700' : 'text-gray-900';
  return (
    <div className="bg-white border border-gray-200 rounded-lg p-3">
      <p className="text-xs text-gray-500 mb-1">{label}</p>
      <p className={`text-xl font-semibold ${c}`}>{value}</p>
      {sub && <p className="text-xs text-gray-400 mt-1">{sub}</p>}
    </div>
  );
}

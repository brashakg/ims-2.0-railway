// ============================================================================
// IMS 2.0 - Shared helpers for the two B2B -> Tally screens
// ============================================================================
// Both screens (Export console + Worklist) read the same B2B-invoice list and
// share money/date formatting + a blob-download helper. Kept in one module so
// the two pages stay consistent.

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  financeApi, type B2BInvoice, type B2BInvoiceSummary, type TallyStatus,
} from '../../services/api/finance';
import { useToast } from '../../context/ToastContext';

export const inr = (n?: number) =>
  `₹${Math.round(Number(n) || 0).toLocaleString('en-IN')}`;

/** Trigger a browser download for a Blob with the given filename. */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/** First + last day of the current month as YYYY-MM-DD (the default range). */
export function currentMonthRange(): { from: string; to: string } {
  const now = new Date();
  const first = new Date(now.getFullYear(), now.getMonth(), 1);
  const last = new Date(now.getFullYear(), now.getMonth() + 1, 0);
  const iso = (d: Date) =>
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(
      d.getDate(),
    ).padStart(2, '0')}`;
  return { from: iso(first), to: iso(last) };
}

export const TALLY_STATUS_STYLE: Record<TallyStatus, string> = {
  PENDING: 'bg-amber-100 text-amber-800',
  IN_TALLY: 'bg-blue-100 text-blue-800',
  DONE: 'bg-green-100 text-green-800',
};

export const TALLY_STATUS_LABEL: Record<TallyStatus, string> = {
  PENDING: 'Pending',
  IN_TALLY: 'In Tally',
  DONE: 'Done',
};

/** What both screens say, with Retry, in place of the table when the invoice
 *  list could not be loaded (decision D115). */
export const B2B_LOAD_FAILED = 'Could not load B2B invoices';

interface B2BInvoiceList {
  invoices: B2BInvoice[];
  summary: B2BInvoiceSummary | null;
  reminderDays: number;
  /** The last load failed: the rows are empty because nothing was read. */
  failed: boolean;
}

const NOT_LOADED: B2BInvoiceList = { invoices: [], summary: null, reminderDays: 3, failed: false };

/**
 * THE read of the B2B invoice list behind both screens (decision D115).
 *
 * A load that fails clears the rows and the summary and sets `failed`, so the
 * screen can say it could not load. Keeping what was there showed "No B2B
 * invoices" on first open, or last period's rows under the new dates. Only
 * the answer to the filter asked for LAST is shown: an earlier request that
 * finishes late, or fails late, changes nothing.
 */
export function useB2BInvoiceList(filter: {
  from_date?: string;
  to_date?: string;
  store_id?: string;
  tally_status?: TallyStatus;
}) {
  const toast = useToast();
  const { from_date, to_date, store_id, tally_status } = filter;
  const [list, setList] = useState<B2BInvoiceList>(NOT_LOADED);
  const [loading, setLoading] = useState(false);
  const latest = useRef(0);

  const load = useCallback(async () => {
    const mine = ++latest.current;
    setLoading(true);
    try {
      const r = await financeApi.listB2BInvoices({ from_date, to_date, store_id, tally_status });
      if (mine !== latest.current) return;
      setList({
        invoices: r.invoices, summary: r.summary, reminderDays: r.pending_reminder_days, failed: false,
      });
    } catch (e) {
      if (mine !== latest.current) return;
      setList({ ...NOT_LOADED, failed: true });
      toast.error(e instanceof Error ? e.message : B2B_LOAD_FAILED);
    } finally {
      if (mine === latest.current) setLoading(false);
    }
  }, [from_date, to_date, store_id, tally_status, toast]);

  useEffect(() => {
    load();
  }, [load]);

  return { ...list, loading, load };
}

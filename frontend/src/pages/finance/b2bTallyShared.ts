// ============================================================================
// IMS 2.0 - Shared helpers for the two B2B -> Tally screens
// ============================================================================
// Both screens (Export console + Worklist) read the same B2B-invoice list and
// share money/date formatting + a blob-download helper. Kept in one module so
// the two pages stay consistent.

import type { TallyStatus } from '../../services/api/finance';

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

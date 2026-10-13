// ============================================================================
// IMS 2.0 - GSTR-2B reconcile: the return period and each row's invoice date
// ============================================================================
// Round 5, problem 3: the reconciler keyed bills on (GSTIN, number) only and
// compared every bill ever booked, so a supplier's INV-10 of last year and of
// this year both matched this month's single 2B row. The server now keys by
// financial year and compares within the 2B's return period; this screen
// sends both -- the period picked (last month by default, IST) and each row's
// invoice date read off the portal CSV (DD-MM-YYYY there).

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const toastMock = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toastMock }));
const itc = vi.hoisted(() => ({ register: vi.fn(), reconcile: vi.fn(), exportBucketCsv: vi.fn() }));
vi.mock('../../../services/api/itc', () => ({ itcApi: itc }));

import ItcReconcilePage from '../ItcReconcilePage';

const EMPTY = {
  as_of: '2026-10-01',
  summary: {
    matched: 0, mismatch: 0, only_in_books: 0, only_in_2b: 0, booked_no_credit: 0,
    itc_safe_to_claim: 0, itc_in_mismatch: 0, itc_at_risk: 0, total_book_itc: 0,
  },
  matched: [], mismatch: [], only_in_books: [], only_in_2b: [], booked_no_credit: [],
};

beforeEach(() => {
  vi.clearAllMocks();
  itc.register.mockResolvedValue({ periods: [], total_taxable: 0, total_itc: 0 });
  itc.reconcile.mockResolvedValue(EMPTY);
});

describe('GSTR-2B reconcile sends the return period and the invoice dates', () => {
  it('defaults to last month in IST, and sends what is picked with each row dated', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-09-30T20:00:00Z')); // 1 October, 01:30 IST
    try {
      render(<ItcReconcilePage />);
      const period = (await screen.findByLabelText(/2B return period/i)) as HTMLInputElement;
      expect(period.value).toBe('2026-09');
      fireEvent.change(screen.getByPlaceholderText(/GSTIN,Invoice,Taxable,Tax/), {
        target: {
          value:
            'GSTIN of supplier,Invoice number,Invoice Date,Taxable Value,Integrated Tax\n' +
            '20AACCD5678E1ZX,INV-10,01-09-2026,6200,310\n' +
            '20AACCD5678E1ZX,INV-9,2025-09-14,6200,310\n' +
            '20AACCD5678E1ZX,INV-8,,6200,310',
        },
      });
      fireEvent.change(period, { target: { value: '2026-08' } });
      fireEvent.click(screen.getByRole('button', { name: /Reconcile/ }));
      await waitFor(() => expect(itc.reconcile).toHaveBeenCalledTimes(1));
      const [rows, sent] = itc.reconcile.mock.calls[0];
      expect(sent).toBe('2026-08');
      expect(rows).toEqual([
        { gstin: '20AACCD5678E1ZX', invoice_no: 'INV-10', taxable: 6200, tax: 310, invoice_date: '2026-09-01' },
        { gstin: '20AACCD5678E1ZX', invoice_no: 'INV-9', taxable: 6200, tax: 310, invoice_date: '2025-09-14' },
        { gstin: '20AACCD5678E1ZX', invoice_no: 'INV-8', taxable: 6200, tax: 310, invoice_date: undefined },
      ]);
    } finally {
      vi.useRealTimers();
    }
  });
});

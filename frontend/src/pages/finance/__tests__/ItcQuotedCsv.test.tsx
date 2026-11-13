// ============================================================================
// IMS 2.0 - GSTR-2B paste: real CSV, and a bad money cell is never a zero
// ============================================================================
// Audit L3-26: the paste was split on every comma, so a standard quoted cell
// ("AUDIT,CSV-QUOTED") cut the invoice number in two and shifted the money
// columns (taxable read 0, tax read the taxable value). An unreadable amount
// also became 0 silently. Both reach the reconciler as wrong figures.

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

async function paste(text: string) {
  render(<ItcReconcilePage />);
  await screen.findByLabelText(/2B return period/i);
  fireEvent.change(screen.getByPlaceholderText(/GSTIN,Invoice,Taxable,Tax/), { target: { value: text } });
  fireEvent.click(screen.getByRole('button', { name: /Reconcile/ }));
}

describe('GSTR-2B paste is read as real CSV', () => {
  it('keeps a quoted invoice number and quoted Indian-format amounts whole', async () => {
    await paste(
      'GSTIN,Invoice,Taxable,Tax,Invoice date\n' +
        '27AABCA1234A1ZJ,"AUDIT,CSV-QUOTED",100,18,2026-09-15\n' +
        '27AABCA1234A1ZJ,"INV ""7""","1,250.50","225.09",15-09-2026\n',
    );
    await waitFor(() => expect(itc.reconcile).toHaveBeenCalledTimes(1));
    expect(itc.reconcile.mock.calls[0][0]).toEqual([
      { gstin: '27AABCA1234A1ZJ', invoice_no: 'AUDIT,CSV-QUOTED', taxable: 100, tax: 18, invoice_date: '2026-09-15' },
      { gstin: '27AABCA1234A1ZJ', invoice_no: 'INV "7"', taxable: 1250.5, tax: 225.09, invoice_date: '2026-09-15' },
    ]);
  });

  it('refuses the paste, naming the row, when a money cell is not a number', async () => {
    await paste(
      'GSTIN,Invoice,Taxable,Tax\n' +
        '27AABCA1234A1ZJ,INV-1,100,18\n' +
        '27AABCA1234A1ZJ,INV-2,abc,18\n',
    );
    await waitFor(() => expect(toastMock.error).toHaveBeenCalled());
    expect(String(toastMock.error.mock.calls[0][0])).toMatch(/row 3/i);
    expect(itc.reconcile).not.toHaveBeenCalled();
  });
});

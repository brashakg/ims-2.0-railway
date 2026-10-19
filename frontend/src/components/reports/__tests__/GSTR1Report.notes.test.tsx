// ============================================================================
// IMS 2.0 - GSTR-1 shows every document under the number it was issued with
// ============================================================================
// Owner / CA 2026-10-18: a stock move between two GSTINs is a tax invoice from
// the sending shop's T series (a B2B row), and credit / debit notes carry
// their shop's C / D number (Rule 53). The screen lists both in full -- the
// notes on their own tab, which the report never showed before.

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

vi.mock('../../../services/api', () => ({
  reportsApi: { getGSTR1Report: vi.fn(), getGSTR1GstnJson: vi.fn() },
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u-1', roles: ['ACCOUNTANT'], activeStoreId: 'BV-DHN-02' } }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

import { GSTR1Report } from '../GSTR1Report';
import { reportsApi } from '../../../services/api';

const row = { cgst: 0, sgst: 0, igst: 185, taxableValue: 3700 };

(reportsApi.getGSTR1Report as ReturnType<typeof vi.fn>).mockResolvedValue({
  period: '2026-10',
  gstin: '20AAACB1234C1Z5',
  legalName: 'Dhanbad',
  totalInvoices: 1,
  totalTaxableValue: 3700,
  totalTax: 185,
  b2b: [{
    ...row, invoiceNumber: 'DHN02/T2627/0001', invoiceDate: '2026-10-05',
    customerName: 'Pune', customerGSTIN: '27AAACB1234C1Z3', customerState: '27',
    placeOfSupply: '27', invoiceValue: 3885, totalTax: 185, hsnCode: '900311', gstRate: 5,
  }],
  b2cl: [],
  b2cs: [],
  cdnr: [{
    ...row, refReference: 'DHN02/C2627/0001', creditNoteDate: '2026-10-06',
    customerName: 'Opti Traders', customerGSTIN: '20AAACY2222B1ZA', taxValue: 185,
  }],
  validation: { ok: true, issueCount: 0, issues: [] },
});

describe('GSTR-1 report', () => {
  it('lists the transfer invoice and the credit note under their full numbers', async () => {
    render(<GSTR1Report />);
    expect(await screen.findByText('DHN02/T2627/0001')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Credit \/ Debit Notes/ }));
    expect(screen.getByText('DHN02/C2627/0001')).toBeInTheDocument();
  });
});

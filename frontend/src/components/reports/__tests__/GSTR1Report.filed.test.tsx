// ============================================================================
// IMS 2.0 - GSTR-1 screen and CSV show what the uploaded JSON files
// ============================================================================
// GST batch 2 (owner Option B): the portal JSON files B2CS NET of consumer
// notes, so the B2CS tab and its CSV show those filed rows (b2csFiled), not
// the gross sales; the notes table and CSV say each note's type (a debit note
// ADDS) and where it is filed (CDNR / CDNUR / B2CS).

import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

vi.mock('../../../services/api', () => ({
  reportsApi: { getGSTR1Report: vi.fn(), getGSTR1GstnJson: vi.fn() },
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u-1', roles: ['ACCOUNTANT'], activeStoreId: 'BV-BOK-01' } }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

import { GSTR1Report } from '../GSTR1Report';
import { reportsApi } from '../../../services/api';

const b2c = (taxableValue: number, tax: number) => ({
  placeOfSupply: 'Jharkhand', gstRate: 5, taxableValue, cgst: tax / 2, sgst: tax / 2, igst: 0, totalTax: tax,
});

(reportsApi.getGSTR1Report as ReturnType<typeof vi.fn>).mockResolvedValue({
  period: '2026-09', gstin: '20AAACB1234C1Z5', legalName: 'Bokaro',
  totalInvoices: 1, totalTaxableValue: 2000, totalTax: 100,
  b2b: [], b2cl: [],
  b2cs: [b2c(2000, 100)],
  b2csFiled: [b2c(1900, 95)],
  cdnr: [
    { refReference: 'BOK/C2627/0001', noteType: 'C', noteSection: 'B2CS', creditNoteDate: '2026-09-10',
      customerName: 'Walk-in', taxableValue: 200, cgst: 5, sgst: 5, igst: 0, taxValue: 10 },
    { refReference: 'BOK/D2627/0001', noteType: 'D', noteSection: 'B2CS', creditNoteDate: '2026-09-11',
      customerName: 'Walk-in', taxableValue: 100, cgst: 2.5, sgst: 2.5, igst: 0, taxValue: 5 },
  ],
  validation: { ok: true, issueCount: 0, issues: [] },
});

let csvText = '';
class CaptureBlob {
  constructor(parts: string[]) { csvText = parts.join(''); }
}

afterEach(() => vi.unstubAllGlobals());

async function downloadCsvOf(tab: RegExp) {
  vi.stubGlobal('Blob', CaptureBlob);
  URL.createObjectURL = vi.fn(() => 'blob:x');
  URL.revokeObjectURL = vi.fn();
  fireEvent.click(screen.getByRole('button', { name: tab }));
  fireEvent.click(screen.getByRole('button', { name: /Download CSV/ }));
  return csvText;
}

describe('GSTR-1 report: filed rows', () => {
  it('shows and exports B2CS as filed (net of the notes), never the gross sales', async () => {
    render(<GSTR1Report />);
    await screen.findByText('Download CSV');
    const csv = await downloadCsvOf(/B2C Small|B2CS/);
    expect(csv).toContain('Jharkhand,5%,1900,47.5,47.5,0,95');
    expect(csv).not.toContain(',2000,');
    expect(screen.getAllByText(/1,900/).length).toBeGreaterThan(0);
  });

  it('says each note type and where it is filed, on screen and in the CSV', async () => {
    render(<GSTR1Report />);
    await screen.findByText('Download CSV');
    const csv = await downloadCsvOf(/Credit \/ Debit Notes/);
    expect(csv.split('\n')[0]).toContain('Note Number,Type,Filed In,');
    expect(csv).toContain('BOK/C2627/0001,Credit,B2CS,');
    expect(csv).toContain('BOK/D2627/0001,Debit,B2CS,');
    expect(screen.getByText('Debit')).toBeInTheDocument();
    expect(screen.getAllByText('B2CS').length).toBe(2);
  });
});

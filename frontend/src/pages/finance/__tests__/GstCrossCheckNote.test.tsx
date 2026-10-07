// ============================================================================
// IMS 2.0 - GST Cross-Check: a row's note is visible text, not only a tooltip
// ============================================================================
// Round 15 #4: the "Transfers with no input credit" row keeps its whole
// explanation (shop, bill, tax, "check with your CA") in row.note. A title=
// tooltip on a non-focusable icon cannot be read by touch or keyboard, so the
// note is printed under the metric name.

import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';

const apis = vi.hoisted(() => ({
  gstCrossCheckApi: { get: vi.fn(), signoff: vi.fn() },
  entitiesApi: { list: vi.fn() },
}));
vi.mock('../../../services/api/gstCrossCheck', () => apis);
vi.mock('../../../services/api/entities', () => apis);
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));

import GstCrossCheckPage from '../GstCrossCheckPage';

const NOTE =
  'Transfer from Pune shop - sender has no valid GSTIN: no input credit (bill TRF/T-1, tax 50.00); check whether outward tax applies with your CA.';

function crossCheck() {
  return {
    month: 5, year: 2026, period: '2026-05', entity_id: 'E1', entity_name: 'BVOPL',
    store_count: 1, stores_computed: 1, failed_store_ids: [], partial: false, tolerance: 1,
    comparisons: [
      { metric: 'Transfers with no input credit', sources: { 'Credit denied': 50 }, variance: 0, status: 'INFO', note: NOTE },
      { metric: 'Plain row', sources: { A: 1 }, variance: 0, status: 'MATCH' },
    ],
    rate_breakup: [], cdnr: { count: 0, taxableValue: 0, tax: 0, rows: [] },
    deemed_supply: { count: 0, taxableValue: 0, tax: 0, rows: [] },
    validation: { ok: true, issueCount: 0, issues: [] },
    summary: { mismatch_count: 0, mismatch_metrics: [], all_matched: true, gst_payable: 0 },
    gstr1: { totalTaxableValue: 0, totalTax: 0, cgst: 0, sgst: 0, igst: 0 },
    gstr3b: {
      outwardTaxableValue: 0, outwardTax: 0,
      itc: { cgst: 0, sgst: 0, igst: 0, total: 0 },
      netCash: { cgst: 0, sgst: 0, igst: 0, total: 0 },
      rcm: { taxableValue: 0, cgst: 0, sgst: 0, igst: 0, total: 0 },
    },
    books: { sales_grand_total: 0, sales_tax: 0, sales_taxable: 0, payments_collected: 0, input_credit: null },
    tally: { taxable: 0, tax: 0, cgst: 0, sgst: 0, igst: 0 },
    signoff: null,
  };
}

describe('GST Cross-Check row note', () => {
  it('prints the note as visible text under its own row, and only that row', async () => {
    apis.entitiesApi.list.mockResolvedValue({ entities: [] });
    apis.gstCrossCheckApi.get.mockResolvedValue(crossCheck());
    render(<GstCrossCheckPage />);
    const note = await screen.findByText(NOTE);
    const row = note.closest('tr') as HTMLElement;
    expect(within(row).getByText('Transfers with no input credit')).toBeTruthy();
    expect(note.getAttribute('title')).toBeNull();
    // A row with no note prints none.
    const plain = screen.getByText('Plain row').closest('tr') as HTMLElement;
    expect(within(plain).queryByTestId('crosscheck-note')).toBeNull();
  });
});

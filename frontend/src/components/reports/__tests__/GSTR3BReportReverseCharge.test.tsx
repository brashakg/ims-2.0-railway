// ============================================================================
// IMS 2.0 - GSTR-3B screen: the net is the server's cash figure, and 3.1(d) shows
// ============================================================================
// The owner's case: a reverse-charge freight bill of Rs 1000 @ 18% in May,
// credit on, no sales. The server says tax payable 0, credit 180 (none used),
// reverse charge 180, cash 180. The screen used to work out its own net
// (tax payable - credit = -180) and never showed the reverse-charge tax.

import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

const heads = (i: number) => ({ integratedTax: i, centralTax: 0, stateTax: 0, cess: 0 });

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn() }),
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { activeStoreId: 'S1' } }),
}));
vi.mock('../../../services/api', () => ({
  reportsApi: {
    getGSTR3BReport: vi.fn().mockImplementation(async () => ({
      period: '2026-05',
      gstin: '20ZZZZZ9999Z1Z9',
      legalName: 'Better Vision',
      outwardTaxableValue: 0,
      outwardTaxableSupplies: heads(0),
      inwardSuppliesReverseChargeValue: 1000,
      inwardSuppliesReverseCharge: heads(180),
      zeroRatedValue: 0,
      zeroRatedSupplies: heads(0),
      itcAvailable: heads(180),
      exemptSupplies: 0,
      taxPayable: heads(0),
      itcUtilized: heads(0),
      taxPaidCash: heads(180),
      interest: heads(0),
      lateFee: 0,
    })),
    getGSTR3BGstnJson: vi.fn(),
  },
}));

import { GSTR3BReport } from '../GSTR3BReport';

// The first element carrying the label (the summary card comes before the tables).
const card = (label: string) => screen.getAllByText(label)[0].closest('.card')?.textContent ?? '';

describe('GSTR-3B screen on a reverse-charge month', () => {
  it('reads the net from the server cash figure, never tax payable less credit', async () => {
    render(<GSTR3BReport />);
    await screen.findByText('Net Tax Liability');
    expect(card('Net Tax Liability')).toContain('₹180');
    expect(card('Net Tax Liability')).not.toContain('₹-');
  });

  it('shows the 3.1(d) reverse-charge tax, in the liability and in the payment table', async () => {
    render(<GSTR3BReport />);
    await screen.findByText('3.1(d) Inward Supplies Liable to Reverse Charge');
    expect(card('3.1(d) Inward Supplies Liable to Reverse Charge')).toContain('₹1,000.00');
    expect(card('3.1(d) Inward Supplies Liable to Reverse Charge')).toContain('₹180.00');
    expect(card('Tax Liability')).toContain('₹180');
    expect(screen.getByText('Tax Payable (reverse charge, cash only)')).toBeTruthy();
  });
});

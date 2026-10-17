// ============================================================================
// IMS 2.0 - Finance > Vendor Payments: THE ONE 'WE OWE' RULE (review r3 #1)
// ============================================================================
// One supplier Rs 10,000 ahead and another owed Rs 5,000: 'Total Payable'
// summed the signed balances to -Rs 5,000 -- one supplier's advance netted
// against another's debt. Now Total Payable is what we owe (the positive
// balances), the advances are their own figure, and each row keeps its own
// signed balance.

import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u-1', roles: ['ADMIN'], activeStoreId: '' } }),
}));
vi.mock('../../../services/api/stores', () => ({
  storeApi: { getStores: vi.fn().mockResolvedValue([]) },
}));

import VendorPayments from '../VendorPayments';
import { formatCurrency } from '../financeUtils';
import type { VendorPaymentData } from '../financeTypes';

const row = (id: string, vendor_name: string, amount_due: number): VendorPaymentData => ({
  id, vendor_name, amount_due, due_date: '', days_overdue: 0,
  status: amount_due <= 0 ? 'paid' : 'pending',
});

describe('Vendor Payments totals', () => {
  it('Total Payable is Rs 5,000 owed and Advances Rs 10,000 apart, never -Rs 5,000', () => {
    render(<VendorPayments vendorPayments={[row('V-ADV', 'Advance Frames', -10000), row('V-OWE', 'Owed Lens Co', 5000)]} />);
    const total = screen.getByTestId('vp-total-payable');
    expect(within(total).getByText(formatCurrency(5000))).toBeInTheDocument();
    const adv = screen.getByTestId('vp-advances');
    expect(within(adv).getByText(formatCurrency(10000))).toBeInTheDocument();
    expect(adv.textContent).toContain('not taken off the Total Payable');
    expect(total.textContent).not.toContain(formatCurrency(-5000));
  });

  it('each supplier row keeps its own signed balance', () => {
    render(<VendorPayments vendorPayments={[row('V-ADV', 'Advance Frames', -10000), row('V-OWE', 'Owed Lens Co', 5000)]} />);
    const advRow = screen.getByText('Advance Frames').closest('tr') as HTMLElement;
    expect(advRow.textContent).toContain(formatCurrency(-10000));
    expect(advRow.textContent).toContain('Advance');
    const owedRow = screen.getByText('Owed Lens Co').closest('tr') as HTMLElement;
    expect(owedRow.textContent).toContain(formatCurrency(5000));
  });

  it('with no advance, Advances reads zero and Total Payable the sum of what we owe', () => {
    render(<VendorPayments vendorPayments={[row('A', 'A Co', 1200), row('B', 'B Co', 800), row('C', 'C Co', 0)]} />);
    expect(within(screen.getByTestId('vp-total-payable')).getByText(formatCurrency(2000))).toBeInTheDocument();
    expect(within(screen.getByTestId('vp-advances')).getByText(formatCurrency(0))).toBeInTheDocument();
  });
});

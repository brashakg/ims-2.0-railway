// ============================================================================
// IMS 2.0 - Finance GST panel: the breakdown adds up to Net GST Payable
// ============================================================================
// /finance/gst/summary owes reverse-charge GST in cash (credit never pays it)
// and never lets credit take the payable below 0 (the rest carries forward).
// The panel prints both, so Collected - credit + carried + reverse charge is
// the Net GST Payable it shows. Fed through mapGst, the dashboard's mapper.

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import GSTPanel from '../GSTPanel';
import { mapGst, formatCurrency } from '../financeUtils';

const summary = (over: Record<string, number>) =>
  mapGst({ month: 5, year: 2026, cgst: 0, sgst: 0, igst: 0, gst_collected: 0, gst_input_credit: 0,
    gst_input_credit_carried_forward: 0, reverse_charge_tax: 0, net_gst_payable: 0, ...over });

const row = (label: string) => screen.getByText(label).parentElement?.textContent;

describe('GST panel - reverse charge and carried credit', () => {
  it('shows the reverse charge GST that the payable adds on top of collected less credit', () => {
    render(<GSTPanel gstSummary={summary({ igst: 1000, gst_collected: 1000, gst_input_credit: 680,
      reverse_charge_tax: 180, net_gst_payable: 500 })} />);
    expect(row('Plus: reverse charge GST (paid in cash)')).toContain(formatCurrency(180));
    expect(row('Less: Input Tax Credit')).toContain(formatCurrency(680));
    expect(row('Net GST Payable')).toContain(formatCurrency(500));
    expect(screen.queryByText('Plus: credit carried to next month')).toBeNull();
  });

  it('no sales and one RCM bill: credit carries forward, the 180 is still payable', () => {
    render(<GSTPanel gstSummary={summary({ gst_input_credit: 180, gst_input_credit_carried_forward: 180,
      reverse_charge_tax: 180, net_gst_payable: 180 })} />);
    expect(row('Plus: credit carried to next month')).toContain(formatCurrency(180));
    expect(row('Plus: reverse charge GST (paid in cash)')).toContain(formatCurrency(180));
    expect(row('Net GST Payable')).toContain(formatCurrency(180));
  });

  it('a month with no reverse charge and no spare credit shows neither line', () => {
    render(<GSTPanel gstSummary={summary({ gst_collected: 500, gst_input_credit: 200, net_gst_payable: 300 })} />);
    expect(screen.queryByText(/reverse charge GST \(paid/)).toBeNull();
    expect(screen.queryByText(/carried to next month/)).toBeNull();
  });
});

// ============================================================================
// IMS 2.0 - GSTR-3B: input tax is for the accounts roles only (R5)
// ============================================================================
// All GST report routes are now Accounts/Admin only. The routed access tests
// cover that rule; these direct component tests retain the additional ITC
// display guard if a component is rendered with a non-Accounts user.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('../../../services/api', () => ({
  reportsApi: {
    getGSTR3BReport: vi.fn(),
    getGSTR3BGstnJson: vi.fn(),
  },
}));

let roles: string[] = ['STORE_MANAGER'];
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u-1', roles, activeStoreId: 'S1' } }),
}));

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

import { GSTR3BReport } from '../GSTR3BReport';
import { reportsApi } from '../../../services/api';

const api = reportsApi as unknown as Record<string, ReturnType<typeof vi.fn>>;

const heads = (i: number, c: number, s: number) => ({
  integratedTax: i,
  centralTax: c,
  stateTax: s,
  cess: 0,
});

// The sales side every finance role reads.
const OUTPUT_SIDE = {
  period: '2026-05',
  gstin: '20AAACB1234C1Z5',
  legalName: 'Bokaro',
  storeState: 'Jharkhand',
  outwardTaxableValue: 1000,
  outwardTaxableSupplies: heads(0, 90, 90),
  creditNotes: { integratedTax: 0, centralTax: 0, stateTax: 0, taxableValue: 0 },
  creditNoteCarryForward: { integratedTax: 0, centralTax: 0, stateTax: 0, taxableValue: 0 },
  zeroRatedValue: 0,
  zeroRatedSupplies: heads(0, 0, 0),
  exemptSupplies: 0,
  taxPayable: heads(0, 90, 90),
  inwardSuppliesReverseChargeValue: 0,
  inwardSuppliesReverseCharge: heads(0, 0, 0),
  interest: heads(0, 0, 0),
  lateFee: 0,
};

// What the server adds for an accounts role (distinctive figures).
const INPUT_SIDE = {
  itcAvailable: heads(0, 61.5, 61.5),
  itcAvailableRegular: heads(0, 61.5, 61.5),
  itcAvailableGstin: heads(0, 0, 0),
  itcUtilized: heads(0, 61.5, 61.5),
  inwardSuppliesReverseChargeValue: 400,
  inwardSuppliesReverseCharge: heads(0, 10, 10),
  inwardSuppliesReverseChargeGstin: { integratedTax: 0, centralTax: 0, stateTax: 0, taxableValue: 0 },
  taxPaidCash: heads(0, 38.5, 38.5),
};

async function renderAs(role: string, body: unknown) {
  roles = [role];
  api.getGSTR3BReport.mockResolvedValue(body);
  render(<GSTR3BReport />);
  // The sales side is drawn for everyone.
  expect(await screen.findByText('Outward Supplies')).toBeInTheDocument();
  expect(screen.getAllByText('Tax Liability').length).toBeGreaterThan(0);
  expect(screen.getByText(/3\.1 Outward Taxable Supplies/)).toBeInTheDocument();
}

function expectNoInputTax() {
  expect(screen.queryByText('ITC Available')).toBeNull();
  expect(screen.queryByText(/Eligible ITC/)).toBeNull();
  expect(screen.queryByText('ITC Utilized')).toBeNull();
  expect(screen.queryByText('Tax Paid in Cash')).toBeNull();
  expect(screen.queryByText('Cash Paid')).toBeNull();
  expect(screen.queryByText('Net Tax Liability')).toBeNull();
  expect(screen.queryByRole('button', { name: /Download GSTN JSON/i })).toBeNull();
  // No input figure under any label: 2 x 61.50 = 123, 2 x 38.50 = 77.
  expect(screen.queryByText(/61\.50|38\.50/)).toBeNull();
  expect(screen.queryByText(/₹123\b|₹77\b/)).toBeNull();
}

describe('GSTR-3B screen - input tax follows the accounts rule (R5)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it.each([['STORE_MANAGER'], ['AREA_MANAGER']])(
    '%s: the server-stripped report renders the sales side only',
    async (role) => {
      await renderAs(role, OUTPUT_SIDE);
      expectNoInputTax();
    },
  );

  it.each([['STORE_MANAGER'], ['AREA_MANAGER']])(
    '%s: even a report that still carries input tax is not drawn',
    async (role) => {
      await renderAs(role, { ...OUTPUT_SIDE, ...INPUT_SIDE });
      expectNoInputTax();
    },
  );

  it.each([['ACCOUNTANT'], ['ADMIN'], ['SUPERADMIN']])(
    '%s: Table 4, the ITC / cash rows, the net and the portal export are drawn',
    async (role) => {
      await renderAs(role, { ...OUTPUT_SIDE, ...INPUT_SIDE });
      expect(screen.getAllByText('ITC Available').length).toBeGreaterThan(0);
      expect(screen.getByText(/4 Eligible ITC/)).toBeInTheDocument();
      expect(screen.getAllByText('ITC Utilized').length).toBeGreaterThan(0);
      expect(screen.getAllByText('Tax Paid in Cash').length).toBeGreaterThan(0);
      expect(screen.getAllByText('Cash Paid').length).toBeGreaterThan(0);
      expect(screen.getAllByText('Net Tax Liability').length).toBeGreaterThan(0);
      expect(screen.getByRole('button', { name: /Download GSTN JSON/i })).toBeInTheDocument();
      expect(screen.getAllByText(/61\.50/).length).toBeGreaterThan(0);
      // No "shown to Admin and Accountant only" note: no one else reaches
      // the page (R1, reportRoutes).
      expect(screen.queryByText(/Admin and Accountant only/)).toBeNull();
    },
  );

  it('ACCOUNTANT: section 17(5) blocked credit is its own Table 4 row, outside the ITC', async () => {
    await renderAs('ACCOUNTANT', { ...OUTPUT_SIDE, ...INPUT_SIDE, itcBlocked: heads(0, 9.25, 9.25) });
    expect(screen.getByText(/Not claimable, section 17\(5\) - Table 4\(B\)\(1\)/)).toBeInTheDocument();
    expect(screen.getAllByText(/18\.50/).length).toBeGreaterThan(0);
  });

  it('ACCOUNTANT: credit reversed on written-off stock is its own Table 4(B)(1) row', async () => {
    await renderAs('ACCOUNTANT', { ...OUTPUT_SIDE, ...INPUT_SIDE, itcWriteOffReversed: heads(0, 77.5, 77.5) });
    expect(screen.getByText(/Reversed on stock written off, section 17\(5\)\(h\)/)).toBeInTheDocument();
    expect(screen.getAllByText(/155\.00/).length).toBeGreaterThan(0);
  });

  it('ACCOUNTANT: a write-off reversal with no GSTIN is flagged, never counted', async () => {
    await renderAs('ACCOUNTANT', { ...OUTPUT_SIDE, ...INPUT_SIDE,
      unallocatedWriteOffs: { count: 1, tax: 155, bill_numbers: ['MB-TRF-11'] } });
    expect(screen.getByText(/Written-off stock needs its bill's GSTIN/)).toBeInTheDocument();
    expect(screen.getByText('MB-TRF-11')).toBeInTheDocument();
  });

  it('STORE_MANAGER: the written-off reversal is input tax too, never drawn', async () => {
    await renderAs('STORE_MANAGER', { ...OUTPUT_SIDE, ...INPUT_SIDE, itcWriteOffReversed: heads(0, 77.5, 77.5) });
    expect(screen.queryByText(/17\(5\)\(h\)/)).toBeNull();
  });

  it('STORE_MANAGER: the blocked-credit row is input tax too, never drawn', async () => {
    await renderAs('STORE_MANAGER', { ...OUTPUT_SIDE, ...INPUT_SIDE, itcBlocked: heads(0, 9.25, 9.25) });
    expect(screen.queryByText(/section 17\(5\)/)).toBeNull();
  });
});

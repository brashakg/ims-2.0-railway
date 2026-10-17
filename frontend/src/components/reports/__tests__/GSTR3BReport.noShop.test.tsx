// ============================================================================
// IMS 2.0 - GSTR-3B: a login with no shop sees no shop (R4)
// ============================================================================
// Owner ruling 2026-10-12: "A non-admin login with NO shop assigned sees NO
// shop until an admin assigns one ... a clear 'No shop is assigned to this
// login' message; never every shop." A GSTR-3B is one shop's return; the
// server refuses such a login (403, NO_SHOP_DETAIL) where it used to hand back
// store-001's. So the screen must not ask at all: it shows the owner's words
// (NoShopNotice) and no report, no download. A login that holds a shop -- or
// an admin -- loads the report as before.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

vi.mock('../../../services/api', () => ({
  reportsApi: {
    getGSTR3BReport: vi.fn(),
    getGSTR3BGstnJson: vi.fn(),
  },
}));

type FakeUser = { id: string; roles: string[]; activeStoreId?: string; storeIds?: string[] };
let user: FakeUser = { id: 'u-1', roles: ['STORE_MANAGER'] };
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user }),
}));

const toastError = vi.fn();
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: toastError, info: vi.fn(), warning: vi.fn() }),
}));

import { GSTR3BReport } from '../GSTR3BReport';
import { NO_SHOP_MESSAGE } from '../../common/NoShopNotice';
import { reportsApi } from '../../../services/api';

const api = reportsApi as unknown as Record<string, ReturnType<typeof vi.fn>>;

const heads = (i: number, c: number, s: number) => ({
  integratedTax: i,
  centralTax: c,
  stateTax: s,
  cess: 0,
});

const REPORT = {
  period: '2026-05',
  gstin: '20AAACB1234C1Z5',
  legalName: 'Bokaro',
  outwardTaxableValue: 1000,
  outwardTaxableSupplies: heads(0, 90, 90),
  zeroRatedValue: 0,
  zeroRatedSupplies: heads(0, 0, 0),
  exemptSupplies: 0,
  taxPayable: heads(0, 90, 90),
  inwardSuppliesReverseChargeValue: 0,
  inwardSuppliesReverseCharge: heads(0, 0, 0),
  interest: heads(0, 0, 0),
  lateFee: 0,
};

beforeEach(() => {
  api.getGSTR3BReport.mockReset();
  api.getGSTR3BGstnJson.mockReset();
  api.getGSTR3BReport.mockResolvedValue(REPORT);
  toastError.mockReset();
});

describe('GSTR-3B for a login with no shop (R4)', () => {
  it.each(['STORE_MANAGER', 'AREA_MANAGER', 'ACCOUNTANT'])(
    '%s with no shop sees the notice and asks for no return',
    async (role) => {
      user = { id: 'u-1', roles: [role], storeIds: [] };
      render(<GSTR3BReport />);
      expect(await screen.findByTestId('no-shop-notice')).toHaveTextContent(NO_SHOP_MESSAGE);
      expect(screen.getByText('GSTR-3B Summary')).toBeInTheDocument();
      // No request: nothing for the server to refuse, no error toast.
      expect(api.getGSTR3BReport).not.toHaveBeenCalled();
      expect(api.getGSTR3BGstnJson).not.toHaveBeenCalled();
      expect(toastError).not.toHaveBeenCalled();
      // No report and no download on screen.
      expect(screen.queryByText('Outward Supplies')).not.toBeInTheDocument();
      expect(screen.queryByText(/Download/)).not.toBeInTheDocument();
      expect(screen.queryByText(/No data available/)).not.toBeInTheDocument();
    },
  );

  it('junk shop ids are no shop either', async () => {
    user = { id: 'u-1', roles: ['STORE_MANAGER'], activeStoreId: '', storeIds: [''] };
    render(<GSTR3BReport />);
    expect(await screen.findByTestId('no-shop-notice')).toBeInTheDocument();
    expect(api.getGSTR3BReport).not.toHaveBeenCalled();
  });

  it('a login with a shop loads its return as before (no notice)', async () => {
    user = { id: 'u-1', roles: ['ACCOUNTANT'], activeStoreId: 'S1', storeIds: ['S1'] };
    render(<GSTR3BReport />);
    expect(await screen.findByText('Outward Supplies')).toBeInTheDocument();
    expect(api.getGSTR3BReport).toHaveBeenCalledWith(expect.any(String), 'S1');
    expect(screen.queryByTestId('no-shop-notice')).not.toBeInTheDocument();
  });

  it('a multi-store login with no active shop still loads (the server reads its own shop)', async () => {
    user = { id: 'u-1', roles: ['ACCOUNTANT'], storeIds: ['S1', 'S2'] };
    render(<GSTR3BReport />);
    expect(await screen.findByText('Outward Supplies')).toBeInTheDocument();
    expect(screen.queryByTestId('no-shop-notice')).not.toBeInTheDocument();
  });

  it.each(['ADMIN', 'SUPERADMIN'])('%s with no shop of its own still loads', async (role) => {
    user = { id: 'u-1', roles: [role] };
    render(<GSTR3BReport />);
    await waitFor(() => expect(api.getGSTR3BReport).toHaveBeenCalled());
    expect(await screen.findByText('Outward Supplies')).toBeInTheDocument();
    expect(screen.queryByTestId('no-shop-notice')).not.toBeInTheDocument();
  });
});

// ============================================================================
// IMS 2.0 - Finance > GST: input tax credit is for the accounts roles (R5, R1)
// ============================================================================
// Owner ruling 2026-10-12: GST input tax -- the credit from supplier bills --
// is shown only to ADMIN / SUPERADMIN / ACCOUNTANT (COST_ROLES). GET
// /finance/gst/summary leaves gst_input_credit and net_gst_payable (collected
// minus that credit) out for a store / area manager, and FinanceDashboard's
// mapGst turns a missing figure into 0. So the panel must not draw the
// "Input Tax Credit" row, the "Net GST Payable" row or the "Total GST
// Payable / Less Input Tax Credit" tile for a manager at all -- not as 0, and
// not as a "net" that is really the gross. The sales-side figures stay.

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { GSTSummaryData } from '../financeTypes';

let roles: string[] = ['STORE_MANAGER'];
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u-1', roles } }),
}));

import GSTPanel from '../GSTPanel';

// What mapGst hands a manager: the input figures arrive as 0 (the server sent
// none); an accounts role gets the real ones (distinctive values).
const MANAGER_VIEW: GSTSummaryData = {
  period: '',
  cgst_collected: 9000,
  sgst_collected: 9000,
  igst_collected: 0,
  total_gst: 18000,
  gst_payable: 0,
  input_tax_credit: 0,
  gst_type: 'CGST_SGST',
};
const ACCOUNTS_VIEW: GSTSummaryData = {
  ...MANAGER_VIEW,
  gst_payable: 5655,
  input_tax_credit: 12345,
};

describe('GSTPanel - input tax credit follows the accounts rule (R5)', () => {
  it.each([['STORE_MANAGER'], ['AREA_MANAGER']])(
    '%s: output figures only, no input credit, no net payable',
    (role) => {
      roles = [role];
      // Even handed the accounts figures, the panel draws none of them.
      for (const view of [MANAGER_VIEW, ACCOUNTS_VIEW]) {
        const { unmount } = render(<GSTPanel gstSummary={view} />);
        expect(screen.queryByText(/Input Tax Credit/i)).toBeNull();
        expect(screen.queryByText('Net GST Payable')).toBeNull();
        expect(screen.queryByText('Total GST Payable')).toBeNull();
        expect(screen.queryByText('Less Input Tax Credit')).toBeNull();
        expect(screen.queryByText(/12,345|5,655/)).toBeNull();
        // The sales side stays: CGST / SGST tiles and the collected total.
        expect(screen.getAllByText('Total GST Collected').length).toBeGreaterThan(0);
        expect(screen.getAllByText(/18,000/).length).toBeGreaterThan(0);
        unmount();
      }
    },
  );

  it.each([['ACCOUNTANT'], ['ADMIN'], ['SUPERADMIN']])(
    '%s: input credit and net payable are drawn',
    (role) => {
      roles = [role];
      render(<GSTPanel gstSummary={ACCOUNTS_VIEW} />);
      expect(screen.getByText('Less: Input Tax Credit')).toBeInTheDocument();
      expect(screen.getByText('Net GST Payable')).toBeInTheDocument();
      expect(screen.getByText('Total GST Payable')).toBeInTheDocument();
      expect(screen.getAllByText(/12,345/).length).toBeGreaterThan(0);
      expect(screen.getAllByText(/5,655/).length).toBeGreaterThan(0);
      // No "shown to Admin and Accountant only" note: the GST tab is theirs
      // alone (R1, FinanceFilters).
      expect(screen.queryByText(/Admin and Accountant only/)).toBeNull();
    },
  );
});

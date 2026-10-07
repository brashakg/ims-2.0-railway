// ============================================================================
// Reports > GST: the filing card offers only the reports its reader has (R1)
// ============================================================================
// Owner ruling 2026-10-07: GSTR-3B (input tax from supplier bills) is the
// accounts roles'. The card hid a manager's 'View GSTR-3B' link but still told
// him to download the GSTR-3B report.

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

let roles: string[] = ['STORE_MANAGER'];
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ hasRole: (want: string[]) => want.some((r) => roles.includes(r)) }),
}));
vi.mock('../ReportsLayout', () => ({ useReportsContext: () => ({ storeId: 'BV-DHN-01', startDate: '', endDate: '' }) }));
vi.mock('../reportsQueries', () => ({ useDiscountAnalysis: () => ({ isPending: false, data: null }) }));
vi.mock('../ReportCardsGrid', () => ({ ReportCardsGrid: () => null }));

import { ReportsGstPage } from '../ReportsGstPage';

function open(role: string) {
  roles = [role];
  render(
    <MemoryRouter>
      <ReportsGstPage />
    </MemoryRouter>,
  );
}

describe('Reports > GST filing card', () => {
  it.each([['STORE_MANAGER'], ['AREA_MANAGER']])('%s: GSTR-1 only, in the words and the links', (role) => {
    open(role);
    expect(screen.getByText(/compiled/)).toHaveTextContent('Download the report for GSTR-1 filing.');
    expect(screen.queryByText(/GSTR-3B/)).toBeNull();
    expect(screen.getByRole('link', { name: 'View GSTR-1' })).toBeInTheDocument();
  });

  it.each([['ACCOUNTANT'], ['ADMIN']])('%s: both reports, in the words and the links', (role) => {
    open(role);
    expect(screen.getByText(/compiled/)).toHaveTextContent('Download the reports for GSTR-1 and GSTR-3B filing.');
    expect(screen.getByRole('link', { name: 'View GSTR-3B' })).toBeInTheDocument();
  });
});

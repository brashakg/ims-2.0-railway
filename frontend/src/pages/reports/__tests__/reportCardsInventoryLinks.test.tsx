// ============================================================================
// Reports -> the two inventory cards open REAL inventory addresses
// ============================================================================
// "Stock Report" and "Stock Movement" used to link `/inventory?tab=stock` and
// `/inventory?tab=transfers` — mega-page tabs the Wave 2 split turned into a
// redirect. They link the split addresses directly now. Asserted on the
// rendered button, so it fails if anyone puts the `?tab=` form back.

import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

const navigate = vi.fn();
vi.mock('react-router-dom', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-router-dom')>()),
  useNavigate: () => navigate,
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));
vi.mock('../ReportsLayout', () => ({
  useReportsContext: () => ({
    storeId: 'BV-RAN-01',
    dateRange: 'today',
    startDate: '2026-09-01',
    endDate: '2026-09-06',
    canExport: true,
  }),
}));
vi.mock('../reportsQueries', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../reportsQueries')>()),
  useSalesSummary: () => ({ data: undefined }),
}));

import { ReportCardsGrid } from '../ReportCardsGrid';

/** The "View" button inside the named report card. */
function viewButtonOf(title: string) {
  const card = screen.getByText(title).closest('.card') as HTMLElement;
  return within(card).getByRole('button', { name: 'View' });
}

describe('ReportCardsGrid inventory links', () => {
  beforeEach(() => navigate.mockClear());

  it.each([
    ['Stock Report', '/inventory/stock'],
    ['Stock Movement', '/inventory/transfers'],
  ])('%s View opens %s', (title, path) => {
    render(
      <MemoryRouter>
        <ReportCardsGrid category="inventory" />
      </MemoryRouter>
    );
    fireEvent.click(viewButtonOf(title));
    expect(navigate).toHaveBeenCalledWith(path);
    expect(navigate.mock.calls[0][0]).not.toContain('tab=');
  });
});

// ============================================================================
// L2-POS-F02: a newly assigned employee with no walk-in / walkout activity
// yet is named on the Footfall capture sheet (from the server's staff
// directory names), never shown as a raw id. Display only: no count, price,
// payment or sale-flow change.
//
// Discriminating power: the old page built names from activity feeds only,
// so the zero-activity row rendered the raw id and this test fails.
// ============================================================================
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const { NEW_ID } = vi.hoisted(() => ({ NEW_ID: '5b0f7f0a-3c1e-4d2a-9a51-6f2f6a9b1c11' }));
vi.mock('../../../services/api', () => ({
  walkoutsApi: {
    walkinsStatus: vi.fn().mockResolvedValue({
      store_id: 'AUDTA', date_str: '2026-11-13', status: 'PENDING',
      staff_with_data: [], staff_missing: [NEW_ID],
      staff_names: { [NEW_ID]: 'AUDIT ROLE SALES_STAFF' },
      total_walk_ins: 0, store_conversion_pct: null,
    }),
    conversionFeed: vi.fn().mockResolvedValue([]),
    dashboardPerStaff: vi.fn().mockResolvedValue({ items: [] }),
  },
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'sm', roles: ['STORE_MANAGER'], activeRole: 'STORE_MANAGER', activeStoreId: 'AUDTA' } }),
}));
vi.mock('../../../hooks/useIsOnlineStore', () => ({ useIsOnlineStore: () => false }));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));

import { FootfallPage } from '../FootfallPage';

describe('Footfall capture sheet names (L2-POS-F02)', () => {
  it('names a zero-activity employee from the staff directory', async () => {
    render(<MemoryRouter><FootfallPage /></MemoryRouter>);
    expect((await screen.findAllByText('AUDIT ROLE SALES_STAFF')).length).toBeGreaterThan(0);
    expect(screen.queryByText(NEW_ID)).toBeNull();
  });
});

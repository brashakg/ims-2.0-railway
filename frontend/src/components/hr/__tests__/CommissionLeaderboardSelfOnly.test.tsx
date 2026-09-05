// ============================================================================
// Owner ruling 2026-09-03: only ADMIN/SUPERADMIN see the full sales
// leaderboard; everyone else gets their OWN row + "you are Nth of M".
//
// The ENFORCEMENT lives on the server (backend/api/routers/payroll.py via
// points.self_only_rows, covered by backend/tests/
// test_commission_leaderboard_self_only.py -- including "the colleague's name
// is not in the raw JSON"). These tests cover the component's half: when the
// server sends visibility='self', render the standing line from rank +
// total_participants and never the "No sales recorded" empty-table look; when
// it sends visibility='all', render the full board as before.
//
// Discriminating power (measured): with the component's visibility handling
// reverted, "renders the self standing" fails (no "of 3 this month" anywhere)
// and "no-sales self viewer" fails on the empty-table copy -- the fixtures
// never contain those strings, only the new code path composes them.
// ============================================================================
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

const getCommissionLeaderboard = vi.fn();
const getCommissionSummary = vi.fn();

vi.mock('../../../services/api/payroll', () => ({
  payrollApi: {
    getCommissionLeaderboard: (...args: unknown[]) => getCommissionLeaderboard(...args),
    getCommissionSummary: (...args: unknown[]) => getCommissionSummary(...args),
  },
}));

const mockUseAuth = vi.fn();
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => mockUseAuth(),
}));

import { CommissionLeaderboard } from '../CommissionLeaderboard';

const OWN_ROW = {
  staff_id: 'me-1',
  name: 'Reader Themselves',
  sales_count: 2,
  revenue: 60000,
  rank: 2,
  badge: 'Star Performer',
  is_self: true,
};

describe('CommissionLeaderboard - self-only visibility (owner ruling 2026-09-03)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getCommissionSummary.mockResolvedValue({
      month: 6, year: 2026, items: [], total_commission: 0, visibility: 'self',
    });
  });

  it("renders the self standing ('#2 of 3 this month') from the server shape", async () => {
    mockUseAuth.mockReturnValue({ user: { roles: ['STORE_MANAGER'], activeStoreId: 'S1' } });
    getCommissionLeaderboard.mockResolvedValue({
      leaderboard: [OWN_ROW], period: 'month', visibility: 'self', total_participants: 3,
    });
    render(<CommissionLeaderboard storeId="S1" />);
    await waitFor(() => expect(screen.getByText('#2')).toBeInTheDocument());
    expect(screen.getByText(/of 3 this month/)).toBeInTheDocument();
    expect(screen.getByText(/The full board is visible to administrators/)).toBeInTheDocument();
    // The own row still renders, named, with its rank cell.
    expect(screen.getByText('Reader Themselves')).toBeInTheDocument();
    expect(screen.queryByText(/No sales recorded/)).not.toBeInTheDocument();
  });

  it('a self viewer with no sales sees the field size, never the empty-table copy', async () => {
    mockUseAuth.mockReturnValue({ user: { roles: ['AREA_MANAGER'], activeStoreId: 'S1' } });
    getCommissionLeaderboard.mockResolvedValue({
      leaderboard: [], period: 'month', visibility: 'self', total_participants: 3,
    });
    render(<CommissionLeaderboard storeId="S1" />);
    await waitFor(() =>
      expect(screen.getByText(/You have no sales this month \(3 on the board\)/)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/No sales recorded/)).not.toBeInTheDocument();
  });

  it('renders the full board for an admin (visibility=all), no standing line', async () => {
    mockUseAuth.mockReturnValue({ user: { roles: ['ADMIN'], activeStoreId: 'S1' } });
    getCommissionLeaderboard.mockResolvedValue({
      leaderboard: [
        { ...OWN_ROW, staff_id: 'u-a', name: 'Rekha Colleague', rank: 1, is_self: false },
        { ...OWN_ROW, rank: 2 },
      ],
      period: 'month',
      visibility: 'all',
      total_participants: 2,
    });
    render(<CommissionLeaderboard storeId="S1" />);
    await waitFor(() => expect(screen.getByText('Rekha Colleague')).toBeInTheDocument());
    expect(screen.getByText('Reader Themselves')).toBeInTheDocument();
    expect(screen.queryByText(/this month$/)).not.toBeInTheDocument();
    expect(screen.queryByText(/visible to administrators/)).not.toBeInTheDocument();
  });
});

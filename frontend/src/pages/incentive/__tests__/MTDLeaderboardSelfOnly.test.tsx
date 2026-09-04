// ============================================================================
// Owner ruling 2026-09-03: only ADMIN/SUPERADMIN see the full leaderboard;
// everyone else gets their OWN row + "you are Nth of M".
//
// The ENFORCEMENT lives on the server (backend/api/routers/points.py,
// covered by backend/tests/test_incentive_self_only.py — including "the
// colleague's name is not in the raw JSON"). These tests cover the page's
// half of the contract: when the server sends visibility='self', render the
// standing banner from rank + total_participants and skip the podium band;
// when it sends visibility='all', render the full board as before.
//
// Discriminating power (measured): with the page's visibility handling
// reverted, "renders the self standing banner" fails (no "#4" / "of 11"
// anywhere) — the fixture never contains those strings, only the new code
// path composes them.
// ============================================================================
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import type { MTDStaffEntry } from '../../../types';

const getLeaderboard = vi.fn();

vi.mock('../../../services/api', () => ({
  incentiveApi: {
    getLeaderboard: (...args: unknown[]) => getLeaderboard(...args),
    getSettings: vi.fn().mockResolvedValue({ eligibility_bands: [] }),
    listDaily: vi.fn().mockResolvedValue({ items: [] }),
  },
  walkoutsApi: { walkinsStatus: vi.fn().mockResolvedValue(null) },
}));
vi.mock('../../../services/api/stores', () => ({
  adminUserApi: { getUsers: vi.fn().mockResolvedValue([]) },
}));

const mockUseAuth = vi.fn();
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => mockUseAuth(),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({
    success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn(),
  }),
}));

import { MTDLeaderboardPage } from '../MTDLeaderboardPage';

function row(overrides: Partial<MTDStaffEntry>): MTDStaffEntry {
  return {
    staff_id: 'u-x',
    staff_name: 'Someone',
    days_logged: 12,
    avg: {
      attendance: 9, conversion: 16, task: 9, visufit: 8, punctuality: 9,
      behaviour: 9, kicker_1: 5, kicker_2: 5, reviews: 8, total: 78,
    },
    eligibility_avg: 0.8,
    rank: 1,
    tier_label: 'PODIUM',
    title_earned: null,
    badge_keys: [],
    rank_delta: null,
    ...overrides,
  };
}

function renderPage() {
  return render(
    <MemoryRouter>
      <MTDLeaderboardPage />
    </MemoryRouter>,
  );
}

describe('MTDLeaderboardPage — self-only visibility (owner ruling 2026-09-03)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the self standing banner ('#4' of 11) and no podium band", async () => {
    mockUseAuth.mockReturnValue({ user: { roles: ['STORE_MANAGER'] } });
    getLeaderboard.mockResolvedValue({
      store_id: 'BV-TEST-01',
      scope: 'store',
      days: 30,
      date_from: '2026-06-01',
      date_to: '2026-06-30',
      visibility: 'self',
      total_participants: 11,
      items: [row({ staff_id: 'me-1', staff_name: 'Reader Themselves', rank: 4, tier_label: 'CONTENDER' })],
    });
    renderPage();
    // "#4" appears in the standing banner AND the (own-row) table rank cell.
    await waitFor(() =>
      expect(screen.getAllByText('#4').length).toBeGreaterThan(0),
    );
    expect(screen.getByText(/of 11 in this window/)).toBeInTheDocument();
    expect(
      screen.getByText(/The full board is visible to\s+administrators/),
    ).toBeInTheDocument();
    // No podium cards for a self-only viewer: the podium band renders each
    // card's average as "<avg> avg / <days>d" — that composition must be
    // absent, while the table row (which shows the same person) still exists.
    expect(screen.queryByText(/avg \/ 12d/)).not.toBeInTheDocument();
  });

  it('renders the full board for an admin (visibility=all), no standing banner', async () => {
    mockUseAuth.mockReturnValue({ user: { roles: ['ADMIN'] } });
    getLeaderboard.mockResolvedValue({
      store_id: 'BV-TEST-01',
      scope: 'store',
      days: 30,
      date_from: '2026-06-01',
      date_to: '2026-06-30',
      visibility: 'all',
      total_participants: 2,
      items: [
        row({ staff_id: 'u-a', staff_name: 'Rekha Colleague', rank: 1 }),
        row({ staff_id: 'u-b', staff_name: 'Tarun Third', rank: 2 }),
      ],
    });
    renderPage();
    // Both names visible (podium card + table row each render the name).
    await waitFor(() =>
      expect(screen.getAllByText('Rekha Colleague').length).toBeGreaterThan(0),
    );
    expect(screen.getAllByText('Tarun Third').length).toBeGreaterThan(0);
    expect(screen.queryByText(/in this window/)).not.toBeInTheDocument();
  });
});

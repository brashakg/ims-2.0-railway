// ============================================================================
// Wave 6 B12 Loyalty split - one smoke test per section, the route gate, and
// the legacy ?tab= net
// ============================================================================
// The old LoyaltyProgram held four tabs behind one URL and had no test at all.
// Overview / Tiers / Rewards are each their own URL now under LoyaltyLayout,
// fed by the layout's one stats + settings load through <Outlet context>; the
// 'promotions' placeholder is gone. Every test drives the REAL customerRoutes
// table, so the URL-to-section mapping under test is the one the app ships (a
// section that lost its data wiring, or a URL wired to the wrong section,
// renders the wrong text and fails here), then the same table's gate (every
// section must keep exactly the roles /customers/loyalty had).
//
// The same file also pins the other half of B12: /customers/feedback is the
// NPS dashboard only, and a link to one of its three deleted tabs lands there.

import { Suspense } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom';

// jsdom has no requestIdleCallback, so the layout's chunk-warming would fall
// back to a 1.5s setTimeout that can fire after teardown. Not under test.
vi.stubGlobal('requestIdleCallback', () => 0);

let currentRoles: string[] = [];

vi.mock('../../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u-1', roles: currentRoles, activeStoreId: 'ZZ-STORE' },
    isAuthenticated: true,
    isLoading: false,
    // Real ProtectedRoute semantics: SUPERADMIN/ADMIN pass every gate.
    hasRole: (roles: string[]) =>
      currentRoles.includes('SUPERADMIN') || currentRoles.includes('ADMIN')
      || roles.some((r) => currentRoles.includes(r)),
    hasPermission: () => true,
    hasModuleAccess: () => true,
  }),
}));

vi.mock('../../../../services/api/loyalty', () => ({
  loyaltyApi: {
    getProgramStats: vi.fn(),
    getSettings: vi.fn(),
    listRewards: vi.fn(),
  },
}));

// The feedback page's only fetch.
vi.mock('../../../../services/api/marketing', () => ({
  marketingApi: { getNpsDashboard: vi.fn() },
}));

import { loyaltyApi } from '../../../../services/api/loyalty';
import { marketingApi } from '../../../../services/api/marketing';
import { ToastProvider } from '../../../../context/ToastContext';
import { customerRoutes } from '../../../../routes/customerRoutes';

const api = loyaltyApi as unknown as Record<string, ReturnType<typeof vi.fn>>;
const nps = marketingApi as unknown as Record<string, ReturnType<typeof vi.fn>>;

// customerRoutes lazy-loads the layout and every section, and those chunks
// compile on demand under vitest - slow on a loaded machine. Same allowance as
// the other real-route tests (expensesSections, customersRecallsRoute).
const FIND = { timeout: 20000 };
vi.setConfig({ testTimeout: 30000 });

beforeEach(() => {
  api.getProgramStats.mockResolvedValue({
    total_members: 1234, points_issued: 56000, points_redeemed: 12000, redemption_rate: 21,
    active_points_balance: 44000, avg_points_per_member: 35,
    by_tier: { BRONZE: 1000, SILVER: 200, GOLD: 30, PLATINUM: 4 },
  });
  api.getSettings.mockResolvedValue({
    enabled: true,
    tier_thresholds: { BRONZE: 0, SILVER: 5000, GOLD: 20000, PLATINUM: 50000 },
    tier_multipliers: { BRONZE: 1, SILVER: 1.5, GOLD: 2, PLATINUM: 3 },
  });
  api.listRewards.mockResolvedValue({
    rewards: [{
      reward_id: 'RW-1', name: 'ZZ free lens cloth', type: 'FREE_ITEM', point_cost: 250,
      active: true, redemption_count: 0,
    }],
    total: 1,
  });
  nps.getNpsDashboard.mockResolvedValue({
    avg_score: 8.2, promoters: 7, passives: 2, detractors: 1, response_rate: 40,
    total_surveys: 25, total_responses: 10, nps_score: 60, responses: [],
  });
});

/** Where the router actually ended up — pathname + search, as one string. */
function LocationProbe() {
  const { pathname, search } = useLocation();
  return <div data-testid="location">{pathname + search}</div>;
}

// The REAL route table - the same URL-to-section mapping and gate the app
// ships (customerRoutes.tsx), never a hand-copied one.
function renderRoute(path: string, roles: string[]) {
  currentRoles = roles;
  return render(
    <ToastProvider>
      <MemoryRouter initialEntries={[path]}>
        <LocationProbe />
        <Suspense fallback={null}>
          <Routes>
            {customerRoutes}
            <Route path="/unauthorized" element={<div>ZZ-DENIED</div>} />
          </Routes>
        </Suspense>
      </MemoryRouter>
    </ToastProvider>,
  );
}

const renderSection = (path: string) => renderRoute(path, ['STORE_MANAGER']);

/** The section's own nav link is the active one - one URL per section. */
async function expectActiveLink(name: RegExp) {
  expect(await screen.findByRole('link', { name }, FIND)).toHaveAttribute('aria-current', 'page');
}

describe('each loyalty section renders at its own URL', () => {
  it('overview (index): member distribution + points, under the shared header', async () => {
    renderSection('/customers/loyalty');
    expect(await screen.findByText('Member Distribution', undefined, FIND)).toBeInTheDocument();
    expect(screen.getByText('Points Overview')).toBeInTheDocument();
    // The layout's summary card, fed by the one stats load.
    expect(await screen.findByText('1,234', undefined, FIND)).toBeInTheDocument();
    await expectActiveLink(/^Overview$/);
    expect(screen.queryByRole('link', { name: /Promotions/ })).not.toBeInTheDocument();
  });

  it('tiers: the four tier cards with the engine threshold + multiplier', async () => {
    renderSection('/customers/loyalty/tiers');
    expect(await screen.findByText('5,000+ lifetime points', undefined, FIND)).toBeInTheDocument();
    expect(screen.getByText('3x')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Platinum' })).toBeInTheDocument();
    await expectActiveLink(/^Tiers$/);
  });

  it('rewards: loads the catalog on arrival', async () => {
    renderSection('/customers/loyalty/rewards');
    expect(await screen.findByText('ZZ free lens cloth', undefined, FIND)).toBeInTheDocument();
    expect(api.listRewards).toHaveBeenCalledWith({ active_only: false });
    expect(screen.getByRole('button', { name: /Add Reward/ })).toBeInTheDocument();
    await expectActiveLink(/^Rewards$/);
  });
});

// The /customers/loyalty gate wraps the layout, so every section keeps exactly
// the roles the one page had: SUPERADMIN / ADMIN / STORE_MANAGER.
describe('every section keeps the /customers/loyalty gate', () => {
  it.each(['', '/tiers', '/rewards'])('SALES_STAFF is refused /customers/loyalty%s', async (section) => {
    renderRoute(`/customers/loyalty${section}`, ['SALES_STAFF']);
    expect(await screen.findByText('ZZ-DENIED', undefined, FIND)).toBeInTheDocument();
  });

  it('STORE_MANAGER opens a section, as the one page let it', async () => {
    renderRoute('/customers/loyalty/rewards', ['STORE_MANAGER']);
    expect(await screen.findByText('ZZ free lens cloth', undefined, FIND)).toBeInTheDocument();
  });
});

describe('legacy /customers/loyalty?tab= links still land', () => {
  it('?tab=rewards forwards to /customers/loyalty/rewards and paints it', async () => {
    renderSection('/customers/loyalty?tab=rewards');
    expect(await screen.findByText('ZZ free lens cloth', undefined, FIND)).toBeInTheDocument();
    expect(screen.getByTestId('location').textContent).toBe('/customers/loyalty/rewards');
  });

  it('?tab=promotions (the deleted tab) lands on the Overview', async () => {
    renderSection('/customers/loyalty?tab=promotions');
    expect(await screen.findByText('Member Distribution', undefined, FIND)).toBeInTheDocument();
    expect(screen.getByTestId('location').textContent).toBe('/customers/loyalty');
  });
});

describe('/customers/feedback is the NPS dashboard only', () => {
  it('a link to a deleted tab lands on the NPS dashboard, with no tab bar', async () => {
    renderSection('/customers/feedback?tab=sentiment');
    expect(await screen.findByText('Respondent Segments', undefined, FIND)).toBeInTheDocument();
    expect(screen.getByText('Score Distribution')).toBeInTheDocument();
    for (const gone of [/Sentiment/, /Complaints/, /Comparison/, /^NPS$/]) {
      expect(screen.queryByRole('button', { name: gone })).not.toBeInTheDocument();
    }
  });
});

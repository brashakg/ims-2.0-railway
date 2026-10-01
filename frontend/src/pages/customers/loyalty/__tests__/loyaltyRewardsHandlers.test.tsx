// ============================================================================
// Loyalty Rewards - add / toggle / delete handlers and their error paths
// ============================================================================
// The handlers moved from the old LoyaltyProgram page into
// LoyaltyRewardsSection (Wave 6 B12). Driven through the real customerRoutes
// table with loyaltyApi mocked: success changes the list and toasts; a
// failing call toasts the error and leaves the list exactly as it was.

import { Suspense } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes } from 'react-router-dom';

vi.stubGlobal('requestIdleCallback', () => 0);

vi.mock('../../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u-1', roles: ['STORE_MANAGER'], activeStoreId: 'ZZ-STORE' },
    isAuthenticated: true,
    isLoading: false,
    hasRole: () => true,
    hasPermission: () => true,
    hasModuleAccess: () => true,
  }),
}));

vi.mock('../../../../services/api/loyalty', () => ({
  loyaltyApi: {
    getProgramStats: vi.fn(),
    getSettings: vi.fn(),
    listRewards: vi.fn(),
    createReward: vi.fn(),
    updateReward: vi.fn(),
    deleteReward: vi.fn(),
  },
}));

// Stable spies so the test can read what the handlers told the user.
const toast = vi.hoisted(() => ({
  success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn(),
}));
vi.mock('../../../../context/ToastContext', () => ({
  useToast: () => toast,
  ToastProvider: ({ children }: { children: React.ReactNode }) => children,
}));

import { loyaltyApi } from '../../../../services/api/loyalty';
import { customerRoutes } from '../../../../routes/customerRoutes';

const api = loyaltyApi as unknown as Record<string, ReturnType<typeof vi.fn>>;

const FIND = { timeout: 20000 };
vi.setConfig({ testTimeout: 30000 });

const CLOTH = {
  reward_id: 'RW-1', name: 'ZZ free lens cloth', type: 'FREE_ITEM', point_cost: 250,
  active: true, redemption_count: 0,
};

beforeEach(() => {
  vi.clearAllMocks();
  api.getProgramStats.mockResolvedValue({
    total_members: 1, points_issued: 0, points_redeemed: 0, redemption_rate: 0,
    active_points_balance: 0, avg_points_per_member: 0, by_tier: {},
  });
  api.getSettings.mockResolvedValue({ enabled: true, tier_thresholds: {}, tier_multipliers: {} });
  api.listRewards.mockResolvedValue({ rewards: [CLOTH], total: 1 });
});

async function openRewards() {
  render(
    <MemoryRouter initialEntries={['/customers/loyalty/rewards']}>
      <Suspense fallback={null}>
        <Routes>{customerRoutes}</Routes>
      </Suspense>
    </MemoryRouter>,
  );
  await screen.findByText('ZZ free lens cloth', undefined, FIND);
}

async function fillAndSave(name: string) {
  fireEvent.click(screen.getByRole('button', { name: /Add Reward/ }));
  fireEvent.change(screen.getByPlaceholderText('e.g. Free glasses-cloth'), { target: { value: name } });
  fireEvent.click(screen.getByRole('button', { name: 'Save Reward' }));
}

describe('add reward', () => {
  it('creates it, puts it first in the list, closes the form and toasts', async () => {
    api.createReward.mockResolvedValue({
      reward: { reward_id: 'RW-2', name: 'ZZ new voucher', type: 'DISCOUNT', point_cost: 100, active: true, redemption_count: 0 },
    });
    await openRewards();
    await fillAndSave('ZZ new voucher');
    expect(await screen.findByText('ZZ new voucher')).toBeInTheDocument();
    expect(api.createReward).toHaveBeenCalledWith(expect.objectContaining({ name: 'ZZ new voucher', point_cost: 100 }));
    expect(screen.queryByText('New reward')).not.toBeInTheDocument();
    expect(toast.success).toHaveBeenCalledWith('Reward added');
    // Newest first, existing row kept.
    const names = screen.getAllByText(/^ZZ (new voucher|free lens cloth)$/).map(e => e.textContent);
    expect(names).toEqual(['ZZ new voucher', 'ZZ free lens cloth']);
  });

  it('a failing create toasts the error, keeps the list and keeps the form', async () => {
    api.createReward.mockRejectedValue(new Error('boom'));
    await openRewards();
    await fillAndSave('ZZ will fail');
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Failed to create reward'));
    expect(toast.success).not.toHaveBeenCalled();
    expect(screen.getAllByText(/ZZ/).map(e => e.textContent)).not.toContain('ZZ will fail');
    // The draft is still on screen so nothing typed is lost.
    expect(screen.getByPlaceholderText('e.g. Free glasses-cloth')).toHaveValue('ZZ will fail');
    expect(screen.getByText('ZZ free lens cloth')).toBeInTheDocument();
  });
});

describe('toggle active', () => {
  it('deactivates and shows the server row', async () => {
    api.updateReward.mockResolvedValue({ reward: { ...CLOTH, active: false } });
    await openRewards();
    fireEvent.click(screen.getByRole('button', { name: 'Deactivate' }));
    expect(await screen.findByRole('button', { name: 'Activate' })).toBeInTheDocument();
    expect(api.updateReward).toHaveBeenCalledWith('RW-1', { active: false });
    expect(screen.getByText('Inactive')).toBeInTheDocument();
    expect(toast.success).toHaveBeenCalledWith('Reward deactivated');
  });

  it('a failing update toasts the error and the row stays active', async () => {
    api.updateReward.mockRejectedValue(new Error('boom'));
    await openRewards();
    fireEvent.click(screen.getByRole('button', { name: 'Deactivate' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Failed to update reward'));
    expect(toast.success).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Deactivate' })).toBeInTheDocument();
    expect(screen.queryByText('Inactive')).not.toBeInTheDocument();
  });
});

describe('delete reward', () => {
  it('removes the row and toasts', async () => {
    api.deleteReward.mockResolvedValue({});
    await openRewards();
    fireEvent.click(screen.getByRole('button', { name: 'Delete reward' }));
    await waitFor(() => expect(screen.queryByText('ZZ free lens cloth')).not.toBeInTheDocument());
    expect(api.deleteReward).toHaveBeenCalledWith('RW-1');
    expect(toast.success).toHaveBeenCalledWith('Reward deleted');
  });

  it('a failing delete toasts the error and the row stays', async () => {
    api.deleteReward.mockRejectedValue(new Error('boom'));
    await openRewards();
    fireEvent.click(screen.getByRole('button', { name: 'Delete reward' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Failed to delete reward'));
    expect(toast.success).not.toHaveBeenCalled();
    expect(screen.getByText('ZZ free lens cloth')).toBeInTheDocument();
  });
});

// ============================================================================
// Owner ruling 2026-09-04: per-person commission terms (staff_weightages /
// supervisor_bonuses) are ADMIN/SUPERADMIN only. The server strips both keys
// from GET /incentive/points/settings/eligibility for everyone else and sends
// visibility='self' (backend/tests/test_incentive_pay_terms_admin_only.py
// proves the JSON). This is the page's half of the contract: a STORE_MANAGER
// must still get a full ENTRY grid -- one editable row per staff member, a
// numeric total and an eligibility chip -- with those keys absent, and
// nothing on the page may name a weighting or a bonus.
//
// Discriminating power (measured): with a `settings.staff_weightages` read
// planted in the page's render path, "renders the entry grid" fails (the
// page throws on the absent key). The fixture deliberately omits both keys.
// ============================================================================
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const getSettings = vi.fn();
const listDaily = vi.fn();
const getUsers = vi.fn();

vi.mock('../../../services/api', () => ({
  incentiveApi: {
    getSettings: (...args: unknown[]) => getSettings(...args),
    listDaily: (...args: unknown[]) => listDaily(...args),
    createBulk: vi.fn(),
    deleteDaily: vi.fn(),
  },
  walkoutsApi: { walkinsStatus: vi.fn().mockResolvedValue(null) },
}));
vi.mock('../../../services/api/stores', () => ({
  adminUserApi: { getUsers: (...args: unknown[]) => getUsers(...args) },
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

import { DailyScorecardPage } from '../DailyScorecardPage';

// Exactly what a non-admin receives: bands + gate, NO pay-term keys.
const SELF_SETTINGS = {
  store_id: 'BV-TEST-01',
  eligibility_bands: [
    { min: 0, max: 70, value: 0.0 },
    { min: 70, max: 80, value: 0.6 },
    { min: 80, max: 95, value: 0.8 },
    { min: 95, max: 1000, value: 1.0 },
  ],
  growth_targets: { L1: 0.2 },
  base_rates: { L1: 0.01 },
  discount_kill_threshold: 0.15,
  discount_multipliers: [],
  visufit_gate_threshold: 0.9,
  visufit_gate_enabled: true,
  updated_at: null,
  updated_by: null,
  visibility: 'self' as const,
};

function renderPage() {
  return render(
    <MemoryRouter>
      <DailyScorecardPage />
    </MemoryRouter>,
  );
}

describe('DailyScorecardPage -- no per-person pay terms for a manager (owner ruling 2026-09-04)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUseAuth.mockReturnValue({
      user: { user_id: 'mgr-1', roles: ['STORE_MANAGER'], activeStoreId: 'BV-TEST-01' },
    });
    getUsers.mockResolvedValue([
      { user_id: 'u-a', name: 'Rekha Colleague' },
      { user_id: 'u-b', name: 'Tarun Third' },
    ]);
    listDaily.mockResolvedValue({ items: [], visibility: 'self' });
    getSettings.mockResolvedValue(SELF_SETTINGS);
  });

  it('renders the entry grid with editable inputs when the pay-term keys are absent', async () => {
    const { container } = renderPage();

    await waitFor(() => expect(screen.getByText('Rekha Colleague')).toBeInTheDocument());
    expect(screen.getByText('Tarun Third')).toBeInTheDocument();

    // 2 staff x (9 categories, conversion shown as AUTO today) = 16 numeric
    // entry cells, every one editable -- the manager can still ENTER scores.
    const inputs = container.querySelectorAll('input[type="number"]');
    expect(inputs.length).toBe(16);
    inputs.forEach(i => expect(i).not.toBeDisabled());

    // Total + eligibility computed from bands alone: no NaN anywhere.
    expect(screen.getAllByText('0.00').length).toBe(2);
    expect(container.textContent).not.toMatch(/NaN/);

    // Nothing on the page names a weighting or a bonus.
    expect(container.textContent).not.toMatch(/weight/i);
    expect(container.textContent).not.toMatch(/bonus/i);

    // The visufit gate note still renders from the keys that DID arrive.
    expect(screen.getByText(/Visufit gate active/)).toBeInTheDocument();
  });

  it('keeps the grid identical for an admin who DOES receive the keys', async () => {
    mockUseAuth.mockReturnValue({
      user: { user_id: 'adm-1', roles: ['ADMIN'], activeStoreId: 'BV-TEST-01' },
    });
    getSettings.mockResolvedValue({
      ...SELF_SETTINGS,
      visibility: 'all',
      staff_weightages: { 'u-a': 0.2731 },
      supervisor_bonuses: [{ user_id: 'u-b', role: 'STORE_MANAGER', bonus_pct: { L1: 0.1937 } }],
    });
    listDaily.mockResolvedValue({ items: [], visibility: 'all' });

    const { container } = renderPage();
    await waitFor(() => expect(screen.getByText('Rekha Colleague')).toBeInTheDocument());
    expect(container.querySelectorAll('input[type="number"]').length).toBe(16);
    // The scorecard is an ENTRY surface: even an admin's grid shows no pay terms.
    expect(container.textContent).not.toMatch(/0\.2731|0\.1937/);
  });
});

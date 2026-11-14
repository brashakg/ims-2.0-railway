// ============================================================================
// Codex PR-1428 r1: a refused (409) or unsaved (503) call outcome on the NBA
// call list / reactivation work-list is SHOWN in the modal, which stays open
// for the retry. It used to be swallowed: the modal sat there saying nothing.
// ============================================================================
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u-1', roles: ['STORE_MANAGER'], activeStoreId: 'S1' } }),
}));

const crmApi = vi.hoisted(() => ({
  getNbaCallList: vi.fn(),
  completeNbaCard: vi.fn(),
  dismissNbaCard: vi.fn(),
  getReactivationWorklist: vi.fn(),
  getReactivationAnalytics: vi.fn(),
  logReactivationOutcome: vi.fn(),
}));
vi.mock('../../../services/api/crm', () => ({ crmApi }));

import { NBADashboardPage } from '../NBADashboardPage';
import { LapsedReactivationPage } from '../LapsedReactivationPage';
import { buildApiError } from '../../../services/api/client';

const NOT_SAVED =
  'The call outcome could not be saved. Try again; if it did save, the retry says so and records nothing twice.';
const refused503 = () =>
  Promise.reject(buildApiError({
    isAxiosError: true, message: 'x', config: { method: 'post', url: '/crm/nba/S1/complete' },
    response: { status: 503, data: { detail: NOT_SAVED } },
  } as never));

const CARD = {
  rank: 1, is_vip_slot: false, customer_id: 'C1', customer_name: 'Asha', customer_mobile: '9',
  signals: [], headline: 'Refill due', sub_headlines: [], suggested_action: 'CALL',
  loyalty_tier: null, lifetime_value: 0, last_purchase_date: null, tags: [], is_vip: false,
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe('NBA call list', () => {
  it('shows an unsaved call and keeps the modal for the retry', async () => {
    crmApi.getNbaCallList.mockResolvedValue({ date: '2026-11-14', cards: [CARD] });
    crmApi.completeNbaCard.mockImplementationOnce(refused503);
    render(<NBADashboardPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Done' }));
    fireEvent.change(screen.getByPlaceholderText(/What did you discuss/), {
      target: { value: 'Called, will visit Saturday.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(NOT_SAVED);
    expect(screen.getByText(/Call done/)).toBeInTheDocument();
  });
});

describe('Reactivation work-list', () => {
  it('shows a refused outcome', async () => {
    crmApi.getReactivationWorklist.mockResolvedValue({
      date: '2026-11-14',
      entries: [{ customer_id: 'C1', customer_name: 'Asha', customer_mobile: '9', months_lapsed: 30,
        is_vip: false, headline: 'Lapsed', lifetime_value: 0, last_touch_date: null, tags: [] }],
    });
    crmApi.getReactivationAnalytics.mockResolvedValue(null);
    crmApi.logReactivationOutcome.mockImplementationOnce(() =>
      Promise.reject(buildApiError({
        isAxiosError: true, message: 'x', config: { method: 'post', url: '/crm/reactivation/S1/log' },
        response: { status: 409, data: { detail: 'This call was already recorded; its outcome is kept. Reload the list.' } },
      } as never)),
    );
    render(<LapsedReactivationPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Log outcome' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Save' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/already recorded/);
  });
});

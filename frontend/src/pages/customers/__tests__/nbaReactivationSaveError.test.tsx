// ============================================================================
// Codex PR-1428 r1: a refused (409) or unsaved (503) call outcome on the NBA
// call list / reactivation work-list is SHOWN in the modal, which stays open
// for the retry. It used to be swallowed: the modal sat there saying nothing.
// ============================================================================
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
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

// D69 (Codex PR-1428 r2): the call is saved but the next reminder is not. The
// screen says so, never closes as done, locks what was entered and offers a
// Retry that sends the SAME request (the server sets the reminder once).
const NEXT_NOT_SET =
  'Call saved. The next reminder was NOT set. Press Retry; the call is not recorded twice.';
const nextNotSet = (url: string) => () =>
  Promise.reject(buildApiError({
    isAxiosError: true, message: 'x', config: { method: 'post', url },
    response: { status: 503, data: { detail: NEXT_NOT_SET } },
  } as never));

describe('D69 call saved, next reminder not set', () => {
  it('NBA: says so, locks the entries and retries the same request', async () => {
    crmApi.getNbaCallList.mockResolvedValue({ date: '2026-11-14', cards: [CARD] });
    crmApi.completeNbaCard.mockImplementationOnce(nextNotSet('/crm/nba/S1/complete'))
      .mockResolvedValueOnce({ ok: true, next_follow_up_id: 'FU-N' });
    render(<NBADashboardPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Done' }));
    fireEvent.change(screen.getByPlaceholderText(/What did you discuss/), {
      target: { value: 'Called, will visit Saturday.' },
    });
    fireEvent.change(screen.getByLabelText(/Schedule next follow-up/), { target: { value: '2026-11-20' } });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(NEXT_NOT_SET);
    expect(screen.getByText(/Call done/)).toBeInTheDocument(); // not shown as done
    expect(screen.getByPlaceholderText(/What did you discuss/)).toBeDisabled();
    expect(screen.getByLabelText(/Schedule next follow-up/)).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(screen.queryByText(/Call done/)).not.toBeInTheDocument());
    expect(crmApi.completeNbaCard).toHaveBeenCalledTimes(2);
    expect(crmApi.completeNbaCard.mock.calls[1]).toEqual(crmApi.completeNbaCard.mock.calls[0]);
  });

  it('Reactivation: says so, locks the entries and retries the same request', async () => {
    crmApi.getReactivationWorklist.mockResolvedValue({
      date: '2026-11-14',
      entries: [{ customer_id: 'C1', customer_name: 'Asha', customer_mobile: '9', months_lapsed: 30,
        is_vip: false, headline: 'Lapsed', lifetime_value: 0, last_touch_date: null, tags: [] }],
    });
    crmApi.getReactivationAnalytics.mockResolvedValue(null);
    crmApi.logReactivationOutcome.mockImplementationOnce(nextNotSet('/crm/reactivation/S1/log'))
      .mockResolvedValueOnce({ ok: true, follow_up_id: 'FU-1', next_follow_up_id: 'FU-N' });
    render(<LapsedReactivationPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Log outcome' }));
    fireEvent.change(screen.getByLabelText(/Schedule next touch/), { target: { value: '2026-11-20' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(NEXT_NOT_SET);
    expect(screen.getByText(/Reactivation outcome/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Schedule next touch/)).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(screen.queryByText(/Reactivation outcome/)).not.toBeInTheDocument());
    expect(crmApi.logReactivationOutcome).toHaveBeenCalledTimes(2);
    expect(crmApi.logReactivationOutcome.mock.calls[1]).toEqual(crmApi.logReactivationOutcome.mock.calls[0]);
  });
});

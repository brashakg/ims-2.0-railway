// ============================================================================
// IMS 2.0 - Follow-up dashboard: failures are failures, Rescheduled asks a date
// ============================================================================
// L1-F49: a failed list / summary read shows a failure with Retry (never "No
//   follow-ups found" with zero counts); a failed Complete keeps the chosen
//   outcome and says it was not saved.
// L1-F50 (owner ruling 2026-11-04): Rescheduled shows a date input, cannot be
//   saved without a date, and sends next_date with the outcome.
// L1-F48: the dashboard never sends anything that could clear context notes
//   (notes: '' is now the attempt's note; the server keeps the context).

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u-1', roles: ['STORE_MANAGER'], activeStoreId: 'S1' } }),
}));
const toastError = vi.hoisted(() => vi.fn());
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: toastError, info: vi.fn(), warning: vi.fn() }),
}));
const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), patch: vi.fn(), put: vi.fn(), delete: vi.fn() }));
vi.mock('../../../services/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../services/api/client')>();
  return { ...actual, default: api, api };
});

import { FollowUpDashboard } from '../FollowUpDashboard';

const FU = {
  follow_up_id: 'FU-1', customer_id: 'C1', customer_name: 'Asha', customer_phone: '9',
  store_id: 'S1', type: 'prescription_expiry', scheduled_date: '2026-11-05', status: 'pending',
  outcome: null, notes: 'call after 6pm', created_at: '2026-11-01',
};
const SUMMARY = { due_today: 1, this_week: 1, overdue: 0, completed_this_month: 0, pending_total: 1 };
const fail = () => Promise.reject(Object.assign(new Error('Service Unavailable'), { response: { status: 503 } }));

function okReads() {
  api.get.mockImplementation((url: string) =>
    Promise.resolve({ data: url.includes('summary') ? SUMMARY : [FU] }),
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('FollowUpDashboard', () => {
  it('a failed load is shown as a failure with Retry, not an empty queue', async () => {
    api.get.mockImplementation(fail);
    render(<FollowUpDashboard />);
    expect(await screen.findByText(/Could not load follow-ups/)).toBeInTheDocument();
    expect(screen.queryByText('No follow-ups found')).toBeNull();
    expect(screen.getByText(/The counts could not be loaded|Could not load/)).toBeInTheDocument();
    expect(screen.queryAllByText('0')).toHaveLength(0);
    okReads();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('Asha')).toBeInTheDocument();
  });

  it('a failed Complete keeps the outcome and says it was not saved', async () => {
    okReads();
    api.patch.mockImplementationOnce(fail);
    render(<FollowUpDashboard />);
    const select = await screen.findByLabelText('Outcome for Asha');
    fireEvent.change(select, { target: { value: 'no_answer' } });
    fireEvent.click(screen.getByRole('button', { name: 'Complete' }));
    expect(await screen.findByText(/Not saved:/)).toBeInTheDocument();
    expect((screen.getByLabelText('Outcome for Asha') as HTMLSelectElement).value).toBe('no_answer');
    expect(api.patch).toHaveBeenCalledWith('/follow-ups/FU-1/complete', { outcome: 'no_answer', notes: '' });
  });

  it('Rescheduled asks for the new date and sends it', async () => {
    okReads();
    api.patch.mockResolvedValue({ status: 200, data: {} });
    render(<FollowUpDashboard />);
    const select = await screen.findByLabelText('Outcome for Asha');
    fireEvent.change(select, { target: { value: 'rescheduled' } });
    const save = screen.getByRole('button', { name: 'Save new date' });
    expect(save).toBeDisabled();
    fireEvent.change(screen.getByLabelText('New date for Asha'), { target: { value: '2099-01-15' } });
    fireEvent.click(save);
    await waitFor(() =>
      expect(api.patch).toHaveBeenCalledWith('/follow-ups/FU-1/complete', {
        outcome: 'rescheduled', notes: '', next_date: '2099-01-15',
      }),
    );
  });

  it('a failed Auto-generate says so', async () => {
    okReads();
    api.post.mockImplementation(fail);
    render(<FollowUpDashboard />);
    await screen.findByText('Asha');
    fireEvent.click(screen.getByRole('button', { name: /Auto-generate/ }));
    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(String(toastError.mock.calls[0][0])).toMatch(/Auto-generate failed/);
  });
});

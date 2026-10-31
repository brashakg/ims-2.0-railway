// Money review of #1264: the refund approval rule (above Rs 2,000 needs a
// manager) is ON by default. A Save from a form that never loaded the server's
// values must not write OFF (or any guessed value) over it.
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const toast = { success: vi.fn(), error: vi.fn() };
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));
vi.mock('../../../services/api', () => ({
  policiesApi: { getAll: vi.fn(), set: vi.fn() },
}));
vi.mock('../../../components/settings/ApprovalPinSection', () => ({ ApprovalPinSection: () => null }));

import { policiesApi } from '../../../services/api';
import { RefundPolicySection } from '../RefundPolicyPage';

const getAll = vi.mocked(policiesApi.getAll);
const set = vi.mocked(policiesApi.set);

describe('Refund policy save', () => {
  beforeEach(() => vi.clearAllMocks());

  it('refuses to save when the current values could not be loaded', async () => {
    getAll.mockRejectedValue(new Error('network'));
    render(<RefundPolicySection />);
    const save = await screen.findByRole('button', { name: /save/i });
    fireEvent.click(save);
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(set).not.toHaveBeenCalled();
  });

  it('keeps the rule ON when the server holds no saved value', async () => {
    getAll.mockResolvedValue({ policies: {} } as any);
    set.mockResolvedValue({} as any);
    render(<RefundPolicySection />);
    const save = await screen.findByRole('button', { name: /save/i });
    await waitFor(() => expect((save as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(save);
    await waitFor(() => expect(set).toHaveBeenCalled());
    // Round 9: the switch is the owner's (Superadmin-only) -- an unchanged
    // switch is never written, so an Admin's save can never touch it.
    expect(set.mock.calls.find((c) => c[0] === 'refund.matrix_enabled')).toBeUndefined();
  });

  it('shows the real rule: Rs 2,000 per bill, ON, no dead amount bands', async () => {
    getAll.mockResolvedValue({ policies: {} } as any);
    render(<RefundPolicySection />);
    await screen.findByText(/Refund Approval Rule/);
    expect(screen.getByText(/2,000 in total/)).toBeTruthy();
    expect(screen.queryByText(/Needs SUPERADMIN above/)).toBeNull();
    expect(screen.queryByText(/When OFF \(default\)/)).toBeNull();
  });
});

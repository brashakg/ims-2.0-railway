// ============================================================================
// Approval PIN: a failed status read is not "No PIN set" (audit R43)
// ============================================================================
// An ADMIN with a working PIN opened Approvals while the status read failed:
// the card said "No PIN set" and dropped the Current PIN box. Whether a PIN
// exists is only known from a successful answer; until then the card says it
// could not check, offers Retry, and offers no form to set or change a PIN.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { unlabelledControls } from '../../../test/unlabelledControls';

const toast = { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() };
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));
vi.mock('../../../context/AuthContext', () => ({ useAuth: () => ({ user: { id: 'u-admin' } }) }));

const getPinStatus = vi.fn();
const setPin = vi.fn();
vi.mock('../../../services/api/approvals', () => ({
  approvalsApi: {
    getPinStatus: (...a: unknown[]) => getPinStatus(...a),
    setPin: (...a: unknown[]) => setPin(...a),
  },
}));

import { ApprovalPinSection } from '../ApprovalPinSection';

beforeEach(() => {
  vi.clearAllMocks();
  setPin.mockResolvedValue({ ok: true });
});

describe('Approval PIN status (R43)', () => {
  it('a failed read says it could not check - never "No PIN set" - and offers no PIN form', async () => {
    getPinStatus.mockRejectedValue(new Error('503'));
    render(<ApprovalPinSection />);

    expect(await screen.findByRole('alert')).toHaveTextContent(/could not check whether you have an approval pin/i);
    expect(screen.queryByText(/no pin set/i)).toBeNull();
    expect(screen.queryByText(/pin is set/i)).toBeNull();
    expect(screen.queryByLabelText('PIN')).toBeNull();
    expect(screen.queryByLabelText('Confirm PIN')).toBeNull();
    expect(screen.queryByRole('button', { name: /set pin|update pin/i })).toBeNull();
    expect(setPin).not.toHaveBeenCalled();
  });

  it('an answer without a yes/no is also "could not check"', async () => {
    getPinStatus.mockResolvedValue({});
    render(<ApprovalPinSection />);
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(screen.queryByText(/no pin set/i)).toBeNull();
  });

  it('Retry shows the real state: PIN is set, and changing it needs the current PIN', async () => {
    getPinStatus.mockRejectedValueOnce(new Error('503'));
    getPinStatus.mockResolvedValue({ has_pin: true, pin_set_at: null });
    render(<ApprovalPinSection />);
    fireEvent.click(await screen.findByRole('button', { name: 'Retry' }));

    expect(await screen.findByText(/pin is set/i)).toBeInTheDocument();
    const update = screen.getByRole('button', { name: 'Update PIN' });
    fireEvent.change(screen.getByLabelText('New PIN'), { target: { value: '4321' } });
    fireEvent.change(screen.getByLabelText('Confirm PIN'), { target: { value: '4321' } });
    expect(update).toBeDisabled(); // no current PIN typed yet

    fireEvent.change(screen.getByLabelText('Current PIN'), { target: { value: '1234' } });
    expect(update).toBeEnabled();
    fireEvent.click(update);
    await waitFor(() => expect(setPin).toHaveBeenCalledWith('u-admin', '4321', '1234'));
    expect(unlabelledControls()).toEqual([]);
  });

  it('a successful "no PIN" answer still offers first-time setup', async () => {
    getPinStatus.mockResolvedValue({ has_pin: false });
    render(<ApprovalPinSection />);
    expect(await screen.findByText(/no pin set/i)).toBeInTheDocument();
    expect(screen.queryByLabelText('Current PIN')).toBeNull();
    fireEvent.change(screen.getByLabelText('PIN'), { target: { value: '2468' } });
    fireEvent.change(screen.getByLabelText('Confirm PIN'), { target: { value: '2468' } });
    fireEvent.click(screen.getByRole('button', { name: 'Set PIN' }));
    await waitFor(() => expect(setPin).toHaveBeenCalledWith('u-admin', '2468', undefined));
  });
});

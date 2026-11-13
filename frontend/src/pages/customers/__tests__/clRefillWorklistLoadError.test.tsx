// ============================================================================
// IMS 2.0 - CL refill worklist: an unreadable order history is a failure (L1-F19)
// ============================================================================
// The server answers 503 "Order history unavailable; try again" when the
// orders cannot be read. The page must say so with Retry, never "0 due" /
// "No contact-lens refills due", and never keep rows from an earlier load.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u-1', roles: ['STORE_MANAGER'], activeStoreId: 'S1' } }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));
const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), patch: vi.fn(), put: vi.fn(), delete: vi.fn() }));
vi.mock('../../../services/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../services/api/client')>();
  return { ...actual, default: api, api };
});

import { CLRefillWorklistPage } from '../CLRefillWorklistPage';

const ROW = {
  customer_id: 'C1', customer_name: 'Asha', refill_due_date: '2026-11-20', days_remaining: 7,
  overdue: false, sku: 'ACUVUE', modality: 'monthly', last_cl_order_date: '2026-10-01',
};
const fail503 = () =>
  Promise.reject(
    Object.assign(new Error('Request failed with status code 503'), {
      isAxiosError: true,
      response: { status: 503, data: { detail: 'Order history unavailable; try again' } },
    }),
  );

beforeEach(() => vi.clearAllMocks());

describe('CLRefillWorklistPage', () => {
  it('a 503 shows the failure with Retry, not an empty worklist', async () => {
    api.get.mockImplementation(fail503);
    render(<CLRefillWorklistPage />);
    expect(await screen.findByRole('alert')).toHaveTextContent(/Order history unavailable; try again/);
    expect(screen.queryByText(/No contact-lens refills due/)).toBeNull();
    expect(screen.queryByText(/ due$/)).toBeNull();
    api.get.mockResolvedValue({ data: { items: [ROW], overdue_count: 0 } });
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('Asha')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('a failed refresh drops the rows of the earlier load', async () => {
    api.get.mockResolvedValueOnce({ data: { items: [ROW], overdue_count: 0 } });
    render(<CLRefillWorklistPage />);
    expect(await screen.findByText('Asha')).toBeInTheDocument();
    api.get.mockImplementation(fail503);
    fireEvent.click(screen.getByRole('button', { name: /Refresh/ }));
    await waitFor(() => expect(screen.queryByText('Asha')).toBeNull());
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });
});

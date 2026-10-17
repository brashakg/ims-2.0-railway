import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ summary: vi.fn(), add: vi.fn(), update: vi.fn() }));
let customerId: string | undefined;
let total = 100;
let payments: { method: string; amount: number }[] = [];
let roles = ['CASHIER'];
vi.mock('../../../stores/posStore', () => ({
  usePOSStore: () => ({ customer: customerId ? { id: customerId } : null, payments,
    getGrandTotal: () => total, addPayment: mocks.add, removePayment: vi.fn() }),
}));
vi.mock('../../../services/api/customers', () => ({
  customersApi: { getCreditSummary: mocks.summary },
  customerApi: { updateCustomer: mocks.update },
}));
vi.mock('../../../context/AuthContext', () => ({ useAuth: () => ({ user: { roles } }) }));

import { CreditBillingOption } from '../CreditBillingOption';
import { EditCustomerModal } from '../../customers/EditCustomerModal';

beforeEach(() => {
  customerId = undefined;
  total = 100;
  payments = [];
  roles = ['CASHIER'];
  vi.clearAllMocks();
  mocks.summary.mockResolvedValue({ credit_limit: 100, ar_outstanding: 0, ar_available: 100, limit_exceeded: false });
  mocks.update.mockResolvedValue({});
});

describe('credit at the till', () => {
  it('checks and shows the chain default for a walk-in', async () => {
    render(<CreditBillingOption />);
    await waitFor(() => expect(mocks.summary).toHaveBeenCalledWith('walk-in'));
    const button = await screen.findByRole('button', { name: 'Mark Unpaid Amount as Credit' });
    fireEvent.click(button);
    expect(mocks.add).toHaveBeenCalledWith(expect.objectContaining({ method: 'CREDIT', amount: 100 }));
    expect(screen.getByText('Credit limit')).toBeTruthy();
  });

  it('blocks an over-limit walk-in', async () => {
    customerId = 'walkin-123';
    total = 101;
    render(<CreditBillingOption />);
    const button = await screen.findByRole('button', { name: 'Credit Limit Exceeded' });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(button);
    expect(mocks.add).not.toHaveBeenCalled();
  });

  it('uses the unpaid amount after a cash tender', async () => {
    customerId = 'C1';
    total = 120;
    payments = [{ method: 'CASH', amount: 20 }];
    render(<CreditBillingOption />);
    fireEvent.click(await screen.findByRole('button', { name: 'Mark Unpaid Amount as Credit' }));
    expect(mocks.add).toHaveBeenCalledWith(expect.objectContaining({ amount: 100 }));
  });

  it('keeps credit disabled while loading and when the limit cannot be read', async () => {
    mocks.summary.mockRejectedValue(new Error('offline'));
    render(<CreditBillingOption />);
    expect((screen.getByRole('button') as HTMLButtonElement).disabled).toBe(true);
    expect(await screen.findByRole('alert')).toHaveTextContent('Credit limit could not be checked');
    expect((screen.getByRole('button') as HTMLButtonElement).disabled).toBe(true);
  });

  it('does not reuse the previous customer limit after changing customer', async () => {
    customerId = 'C1';
    const view = render(<CreditBillingOption />);
    await screen.findByRole('button', { name: 'Mark Unpaid Amount as Credit' });
    customerId = 'C2';
    mocks.summary.mockReturnValue(new Promise(() => {}));
    view.rerender(<CreditBillingOption />);
    expect((screen.getByRole('button') as HTMLButtonElement).disabled).toBe(true);
  });
});

describe('individual credit limit editing', () => {
  const renderEditor = () => render(<EditCustomerModal customer={{ id: 'C1', name: 'Asha', phone: '9876543210', credit_limit: 2500 }} onClose={vi.fn()} onSaved={vi.fn()} />);

  it.each(['ADMIN', 'SUPERADMIN'])('lets %s edit the limit and reset it to the default', async role => {
    roles = [role];
    renderEditor();
    const field = screen.getByLabelText('Credit limit (Rs)');
    expect((field as HTMLInputElement).value).toBe('2500');
    fireEvent.change(field, { target: { value: '0' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save Changes' }));
    await waitFor(() => expect(mocks.update).toHaveBeenCalledWith('C1', expect.objectContaining({ credit_limit: 0 })));
  });

  it.each(['STORE_MANAGER', 'CASHIER', 'SALES_STAFF'])('does not send or expose the limit for %s', async role => {
    roles = [role];
    renderEditor();
    expect(screen.queryByLabelText('Credit limit (Rs)')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Save Changes' }));
    await waitFor(() => expect(mocks.update).toHaveBeenCalled());
    expect(mocks.update.mock.calls[0][1]).not.toHaveProperty('credit_limit');
  });
});

// ============================================================================
// IMS 2.0 - Workshop staff log a vendor return without a price
// ============================================================================
// Owner ruling 2026-10-12: WORKSHOP_STAFF may create a vendor return (item,
// quantity, reason) but never see or type what was paid. A return's price is
// the accounts roles' alone (rulings 2026-10-12 R2 + R3): a manager approves
// the goods unpriced and accounts set the prices ("Set prices").

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), patch: vi.fn() }));
const dnMock = vi.hoisted(() => ({ list: vi.fn() }));
const toastMock = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }));
const auth = vi.hoisted(() => ({ role: 'WORKSHOP_STAFF' }));

vi.mock('../../../services/api/client', () => ({ default: apiMock }));
vi.mock('../../../services/api/rtvDebitNotes', () => ({ rtvDebitNotesApi: dnMock }));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toastMock }));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { activeStoreId: 'BV-TEST-01', roles: [auth.role] } }),
}));

import { VendorReturns } from '../VendorReturns';

const unpriced = {
  return_id: 'VR1',
  vendor_id: 'V1',
  vendor_name: 'Acme',
  store_id: 'BV-TEST-01',
  return_type: 'credit_note',
  status: 'created',
  total_value: null,
  credit_note_number: null,
  created_at: '2026-10-12T10:00:00',
  created_by: 'u1',
  notes: '',
  items: [{ product_id: 'p', product_name: 'RB Frame', quantity: 2, reason: 'defective', unit_price: null }],
};

function load(returns: unknown[] = []) {
  apiMock.get.mockImplementation((url: string) =>
    Promise.resolve({
      data: url.startsWith('/vendor-returns')
        ? { returns }
        : { vendors: [{ vendor_id: 'V1', legal_name: 'Acme', trade_name: 'Acme', mobile: '' }] },
    }),
  );
  dnMock.list.mockResolvedValue({ debit_notes: [], total: 0 });
  apiMock.post.mockResolvedValue({ data: { return_id: 'VR2' } });
}

async function fillAndCreate() {
  render(
    <MemoryRouter>
      <VendorReturns />
    </MemoryRouter>,
  );
  fireEvent.click(await screen.findByRole('button', { name: /create return/i }));
  fireEvent.change(screen.getAllByRole('combobox')[0], { target: { value: 'V1' } });
  fireEvent.change(screen.getByPlaceholderText('Enter product name'), { target: { value: 'RB Frame' } });
  const buttons = screen.getAllByRole('button', { name: /create return/i });
  fireEvent.click(buttons[buttons.length - 1]);
  await waitFor(() => expect(apiMock.post).toHaveBeenCalled());
  return apiMock.post.mock.calls[0][1];
}

describe('VendorReturns create - who prices the return', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    auth.role = 'WORKSHOP_STAFF';
  });

  // Owner ruling 2026-10-12: a store manager logs a return like the workshop.
  it.each(['WORKSHOP_STAFF', 'STORE_MANAGER'])(
    '%s log item, quantity and reason - no price field, no price sent', async (role) => {
    auth.role = role;
    load();
    const body = await fillAndCreate();
    expect(screen.queryByText('Unit Price')).toBeNull();
    expect(screen.queryByText('Total Return Value')).toBeNull();
    expect(body.items[0]).not.toHaveProperty('unit_price');
    expect(body.items[0]).toMatchObject({ product_name: 'RB Frame', quantity: 1, reason: 'defective' });
  });

  it('a store manager logs the goods too - no price field, no total', async () => {
    auth.role = 'STORE_MANAGER';
    load();
    const body = await fillAndCreate();
    expect(screen.queryByText('Unit Price')).toBeNull();
    expect(screen.queryByText('Total Return Value')).toBeNull();
    expect(body.items[0]).not.toHaveProperty('unit_price');
  });

  it('workshop staff get no approve / ship / credit buttons and no pricing', async () => {
    load([unpriced]);
    render(
      <MemoryRouter>
        <VendorReturns />
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByText('Acme'));
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull();
    expect(screen.queryByRole('button', { name: /set prices/i })).toBeNull();
  });

  it('the manager approves an unpriced return without a price; accounts price it', async () => {
    auth.role = 'STORE_MANAGER';
    load([unpriced]);
    apiMock.patch.mockResolvedValue({ data: { return: { ...unpriced, status: 'approved' } } });
    render(
      <MemoryRouter>
        <VendorReturns />
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByText('Acme'));
    expect(screen.queryByRole('button', { name: /set prices/i })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }));
    await waitFor(() => expect(apiMock.patch).toHaveBeenCalled());
    expect(apiMock.patch.mock.calls[0][1]).toEqual({ status: 'approved' });
  });
});

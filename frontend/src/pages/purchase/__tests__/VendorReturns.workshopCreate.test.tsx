// ============================================================================
// IMS 2.0 - Workshop staff log a vendor return without a price
// ============================================================================
// Owner ruling 2026-10-12: WORKSHOP_STAFF may create a vendor return (item,
// quantity, reason) but never see or type what was paid. The manager who
// approves it enters the price paid for each unpriced line.

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

  it('workshop staff log item, quantity and reason - no price field, no price sent', async () => {
    load();
    const body = await fillAndCreate();
    expect(screen.queryByText('Unit Price')).toBeNull();
    expect(screen.queryByText('Total Return Value')).toBeNull();
    expect(body.items[0]).not.toHaveProperty('unit_price');
    expect(body.items[0]).toMatchObject({ product_name: 'RB Frame', quantity: 1, reason: 'defective' });
  });

  it('a store manager still types the unit price and it is sent', async () => {
    auth.role = 'STORE_MANAGER';
    load();
    render(
      <MemoryRouter>
        <VendorReturns />
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByRole('button', { name: /create return/i }));
    expect(screen.getByText('Unit Price')).toBeTruthy();
    expect(screen.getByText('Total Return Value')).toBeTruthy();
  });

  it('workshop staff get no approve / ship / credit buttons', async () => {
    load([unpriced]);
    render(
      <MemoryRouter>
        <VendorReturns />
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByText('Acme'));
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull();
    expect(screen.queryByLabelText(/price paid for/i)).toBeNull();
  });

  it('the approving manager enters the price paid for an unpriced line', async () => {
    auth.role = 'STORE_MANAGER';
    load([unpriced]);
    apiMock.patch.mockResolvedValue({ data: { return: { ...unpriced, status: 'approved' } } });
    render(
      <MemoryRouter>
        <VendorReturns />
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByText('Acme'));
    // No price typed: refused on the screen, nothing sent.
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }));
    expect(apiMock.patch).not.toHaveBeenCalled();
    expect(toastMock.error).toHaveBeenCalledWith(
      'Enter the price paid for every item before approving this return.',
    );
    fireEvent.change(screen.getByLabelText('Price paid for RB Frame'), { target: { value: '1500' } });
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }));
    await waitFor(() => expect(apiMock.patch).toHaveBeenCalled());
    expect(apiMock.patch.mock.calls[0][1]).toEqual({ status: 'approved', unit_prices: [1500] });
  });
});

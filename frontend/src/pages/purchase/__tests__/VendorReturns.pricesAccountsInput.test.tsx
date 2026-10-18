// ============================================================================
// IMS 2.0 - Vendor Returns: a return's price is the accounts roles' to ENTER
// ============================================================================
// Owner rulings 2026-10-12: R2 recording supplier money is ADMIN / SUPERADMIN /
// ACCOUNTANT only; R3 managers see items, quantities and reasons only on a
// return -- no unit price, no line value. The Create Return form used to ask
// a manager for a "Unit Price" and show him a "Total Return Value", and the
// credit was recorded at his figure. Now a manager logs the goods only; the
// accounts roles price the lines ("Set prices") and the credit and the debit
// note wait for that price.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { ReactNode } from 'react';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), patch: vi.fn(), put: vi.fn() }));
const dnMock = vi.hoisted(() => ({
  list: vi.fn(),
  issue: vi.fn(),
  fetchPrintHtml: vi.fn(),
  fetchTallyXml: vi.fn(),
}));
let roles: string[] = ['STORE_MANAGER'];

vi.mock('../../../services/api/client', () => ({ default: apiMock }));
vi.mock('../../../services/api/rtvDebitNotes', () => ({ rtvDebitNotesApi: dnMock }));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { activeStoreId: 'BV-TEST-01', roles } }),
}));

import { VendorReturns } from '../VendorReturns';

const withCache = (node: ReactNode) => (
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    {node}
  </QueryClientProvider>
);

const VENDOR = { vendor_id: 'V1', legal_name: 'Acme Optics', trade_name: 'Acme', mobile: '' };
// A manager raised it: no price on the line, no value on the return.
const UNPRICED = {
  return_id: 'VR1',
  vendor_id: 'V1',
  vendor_name: 'Acme Optics',
  store_id: 'BV-TEST-01',
  items: [{ product_id: 'P1', product_name: 'RB Frame', quantity: 3, reason: 'defective', unit_price: null }],
  return_type: 'credit_note',
  status: 'received_by_vendor',
  total_value: null,
  credit_note_number: null,
  credit_note_amount: null,
  created_at: '2026-10-12T10:00:00',
  created_by: 'u-manager',
  notes: '',
};

function load(returns: unknown[]) {
  apiMock.get.mockImplementation((url: string) =>
    Promise.resolve({
      data: url.startsWith('/vendor-returns') ? { returns } : { vendors: [VENDOR] },
    }),
  );
  dnMock.list.mockResolvedValue({ debit_notes: [], total: 0 });
}

function renderPage() {
  render(
    withCache(
      <MemoryRouter>
        <VendorReturns />
      </MemoryRouter>,
    ),
  );
}

async function openCreateForm() {
  renderPage();
  await screen.findByText('Vendor Returns');
  fireEvent.click(screen.getAllByRole('button', { name: /Create Return/ })[0]);
  await screen.findByText('Create Vendor Return');
  await screen.findByRole('option', { name: 'Acme Optics' });
}

async function submitOneLine(price?: string) {
  const vendorSelect = screen.getByRole('option', { name: 'Select Vendor...' }).closest('select')!;
  fireEvent.change(vendorSelect, { target: { value: 'V1' } });
  fireEvent.change(screen.getByPlaceholderText('Enter product name'), {
    target: { value: 'RB Frame' },
  });
  fireEvent.change(screen.getByPlaceholderText('Add any additional notes...'), { target: { value: 'Made-to-order lens, never in stock' } });
  if (price !== undefined) {
    const priceInput = screen.getByText('Unit Price').parentElement!.querySelector('input')!;
    fireEvent.change(priceInput, { target: { value: price } });
  }
  const buttons = screen.getAllByRole('button', { name: 'Create Return' });
  fireEvent.click(buttons[buttons.length - 1]);
  await waitFor(() => expect(apiMock.post).toHaveBeenCalled());
  return apiMock.post.mock.calls[0][1];
}

describe('Create Return: the price is the accounts roles\' to enter', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    load([]);
    apiMock.post.mockResolvedValue({ data: { return_id: 'VR9' } });
  });

  it.each([['STORE_MANAGER'], ['AREA_MANAGER']])(
    '%s: no Unit Price, no Total Return Value, and the return goes up unpriced',
    async (role) => {
      roles = [role];
      await openCreateForm();
      expect(screen.queryByText('Unit Price')).not.toBeInTheDocument();
      expect(screen.queryByText('Total Return Value')).not.toBeInTheDocument();
      expect(
        screen.getByText("Accounts set the price per piece and record the supplier's credit."),
      ).toBeInTheDocument();
      const body = await submitOneLine();
      expect(body.items).toHaveLength(1);
      expect(body.items[0]).not.toHaveProperty('unit_price');
      expect(body.items[0]).toMatchObject({ product_name: 'RB Frame', quantity: 1, reason: 'defective' });
    },
  );

  it('ACCOUNTANT: may price the line as the return is raised', async () => {
    roles = ['ACCOUNTANT'];
    await openCreateForm();
    expect(screen.getByText('Total Return Value')).toBeInTheDocument();
    const body = await submitOneLine('500');
    expect(body.items[0].unit_price).toBe(500);
  });
});

describe('An unpriced return: the accounts roles set its prices before the credit', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    load([UNPRICED]);
  });

  async function openReturn() {
    renderPage();
    fireEvent.click(await screen.findByText('Acme Optics'));
    await screen.findByText('RB Frame');
  }

  it('STORE_MANAGER: no Set prices', async () => {
    roles = ['STORE_MANAGER'];
    await openReturn();
    expect(screen.queryByRole('button', { name: /prices/ })).not.toBeInTheDocument();
  });

  it('ACCOUNTANT: supplier evidence may be recorded while unpriced; internal issue waits for prices', async () => {
    roles = ['ACCOUNTANT'];
    apiMock.put.mockResolvedValue({
      data: {
        return: { ...UNPRICED, items: [{ ...UNPRICED.items[0], unit_price: 100 }], total_value: 300 },
      },
    });
    await openReturn();
    expect(screen.getByText('Not priced')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Issue Credit' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Issue Debit Note' })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: 'Set prices' }));
    const dialog = await screen.findByRole('dialog', { name: 'Set prices' });
    fireEvent.change(within(dialog).getByLabelText('Price per piece for RB Frame'), {
      target: { value: '100' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save prices' }));
    await waitFor(() =>
      expect(apiMock.put).toHaveBeenCalledWith('/vendor-returns/VR1/prices', { unit_prices: [100] }),
    );
    await waitFor(() => expect(screen.getByRole('button', { name: 'Issue Credit' })).toBeEnabled());
    expect(screen.getByRole('button', { name: 'Issue Debit Note' })).toBeEnabled();
    expect(document.body.textContent).toContain('₹300');
  });
});

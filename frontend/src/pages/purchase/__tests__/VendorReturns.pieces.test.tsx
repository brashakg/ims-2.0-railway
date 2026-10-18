// ============================================================================
// IMS 2.0 - A supplier return names the exact pieces (owner 2026-10-18)
// ============================================================================
// A frame goes back piece by piece: scan each piece's own barcode, or type a
// SKU and tick the shop's pieces. The form sends each line's stock_ids; the
// server (vendor_returns._pieces_for_lines) checks every piece again.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), patch: vi.fn() }));
const dnMock = vi.hoisted(() => ({ list: vi.fn() }));
const toastMock = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }));

vi.mock('../../../services/api/client', () => ({ default: apiMock }));
vi.mock('../../../services/api/rtvDebitNotes', () => ({ rtvDebitNotesApi: dnMock }));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toastMock }));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { activeStoreId: 'BV-TEST-01', roles: ['STORE_MANAGER'] } }),
}));

import { VendorReturns } from '../VendorReturns';

const UNITS: Record<string, unknown> = {
  BC1: { stock_id: 'U1', barcode: 'BC1', status: 'AVAILABLE', product_id: 'P1', product: { name: 'RB Aviator' } },
  BC9: { stock_id: 'U9', barcode: 'BC9', status: 'SOLD', product_id: 'P1', product: { name: 'RB Aviator' } },
};

function load() {
  apiMock.get.mockImplementation((url: string) => {
    if (url.startsWith('/inventory/barcode/')) {
      const unit = UNITS[decodeURIComponent(url.split('/').pop() as string)];
      return unit ? Promise.resolve({ data: unit }) : Promise.reject({ response: { status: 404 } });
    }
    if (url === '/products') return Promise.resolve({ data: { products: [{ product_id: 'P1', name: 'RB Aviator' }] } });
    if (url === '/inventory/units') {
      return Promise.resolve({ data: { units: [
        { stock_id: 'U1', barcode: 'BC1', status: 'AVAILABLE' },
        { stock_id: 'U2', barcode: 'BC2', status: 'SOLD' },
      ] } });
    }
    if (url.startsWith('/vendor-returns')) return Promise.resolve({ data: { returns: [] } });
    return Promise.resolve({ data: { vendors: [{ vendor_id: 'V1', legal_name: 'Acme', trade_name: 'Acme', mobile: '' }] } });
  });
  dnMock.list.mockResolvedValue({ debit_notes: [], total: 0 });
  apiMock.post.mockResolvedValue({ data: { return_id: 'VR2' } });
}

async function openForm(path = '/purchase/vendor-returns') {
  render(
    <MemoryRouter initialEntries={[path]}>
      <VendorReturns />
    </MemoryRouter>,
  );
  fireEvent.click(await screen.findByRole('button', { name: /create return/i }));
  fireEvent.change(screen.getAllByRole('combobox')[0], { target: { value: 'V1' } });
}

function scan(code: string) {
  const box = screen.getByPlaceholderText('Piece barcode or SKU');
  fireEvent.change(box, { target: { value: code } });
  fireEvent.keyDown(box, { key: 'Enter' });
}

async function submit() {
  const buttons = screen.getAllByRole('button', { name: /create return/i });
  fireEvent.click(buttons[buttons.length - 1]);
  await waitFor(() => expect(apiMock.post).toHaveBeenCalled());
  return apiMock.post.mock.calls[0][1];
}

describe('VendorReturns - the add-item step names the pieces', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    load();
  });

  it('a scanned piece fills the line; its quantity is the pieces named', async () => {
    await openForm();
    scan('BC1');
    expect(await screen.findByText('BC1')).toBeTruthy();
    expect((screen.getByPlaceholderText('Enter product name') as HTMLInputElement).value).toBe('RB Aviator');
    const qty = screen.getByDisplayValue('1') as HTMLInputElement;
    expect(qty.readOnly).toBe(true);
    const body = await submit();
    expect(body.items[0]).toMatchObject({ product_id: 'P1', quantity: 1, stock_ids: ['U1'] });
    expect(body).not.toHaveProperty('stock_ids');
  });

  it('a sold piece is refused on the form, never added', async () => {
    await openForm();
    scan('BC9');
    await waitFor(() => expect(toastMock.error).toHaveBeenCalled());
    expect(toastMock.error.mock.calls[0][0]).toMatch(/BC9 is sold/);
    expect(screen.queryByText('BC9')).toBeNull();
  });

  it('a SKU lists the shop\'s pieces that can go back, to pick', async () => {
    await openForm();
    scan('RB-SKU');
    const pick = await screen.findByRole('checkbox');
    expect(screen.queryByText('BC2')).toBeNull(); // sold: not offered
    fireEvent.click(pick);
    const body = await submit();
    expect(body.items[0]).toMatchObject({ product_id: 'P1', quantity: 1, stock_ids: ['U1'] });
  });

  it('the Quarantine Queue link starts the form with its piece', async () => {
    await openForm('/purchase/vendor-returns?stock_id=U1&barcode=BC1');
    expect(await screen.findByText('BC1')).toBeTruthy();
    const body = await submit();
    expect(body.items[0].stock_ids).toEqual(['U1']);
    expect(body).not.toHaveProperty('stock_ids');
  });
});

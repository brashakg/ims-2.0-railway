// ============================================================================
// IMS 2.0 - A supplier return names the exact pieces (owner 2026-10-18)
// ============================================================================
// Every piece goes back by its own barcode: scan it, or type a SKU and tick
// the shop's pieces (dated stock pre-ticked: set aside first, then earliest
// expiry). The form sends each line's stock_ids; the server
// (vendor_returns._pieces_for_lines) checks every piece again.

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
  OLD7: { stock_id: 'U7', barcode: 'OLD7', status: 'in_stock', product_id: 'P1', product: { name: 'RB Aviator' } },
};
let shelf: unknown[] = [];

function load() {
  shelf = [
    { stock_id: 'U1', barcode: 'BC1', status: 'AVAILABLE' },
    { stock_id: 'U2', barcode: 'BC2', status: 'SOLD' },
  ];
  apiMock.get.mockImplementation((url: string) => {
    if (url.startsWith('/inventory/barcode/')) {
      const unit = UNITS[decodeURIComponent(url.split('/').pop() as string)];
      return unit ? Promise.resolve({ data: unit }) : Promise.reject({ response: { status: 404 } });
    }
    if (url === '/products') return Promise.resolve({ data: { products: [{ product_id: 'P1', name: 'RB Aviator' }] } });
    if (url === '/inventory/units') return Promise.resolve({ data: { units: shelf } });
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

  it('a scanned piece fills the line; its quantity is the pieces named; one request id per form', async () => {
    await openForm();
    scan('BC1');
    expect(await screen.findByText('BC1')).toBeTruthy();
    expect((screen.getByPlaceholderText('Enter product name') as HTMLInputElement).value).toBe('RB Aviator');
    const qty = screen.getByDisplayValue('1') as HTMLInputElement;
    expect(qty.readOnly).toBe(true);
    const body = await submit();
    expect(body.items[0]).toMatchObject({ product_id: 'P1', quantity: 1, stock_ids: ['U1'] });
    expect(body).not.toHaveProperty('stock_ids');
    expect(typeof body.client_request_id).toBe('string');
    expect(body.client_request_id.length).toBeGreaterThan(0);
  });

  it('a sold piece is refused on the form, never added', async () => {
    await openForm();
    scan('BC9');
    await waitFor(() => expect(toastMock.error).toHaveBeenCalled());
    expect(toastMock.error.mock.calls[0][0]).toMatch(/BC9 is sold/);
    expect(screen.queryByText('BC9')).toBeNull();
  });

  it('a legacy "in_stock" piece reads as on the shelf, as the server reads it', async () => {
    await openForm();
    scan('OLD7');
    expect(await screen.findByText('OLD7')).toBeTruthy();
    expect(toastMock.error).not.toHaveBeenCalled();
  });

  it("a SKU lists the shop's pieces that can go back, to pick", async () => {
    await openForm();
    scan('RB-SKU');
    const pick = await screen.findByRole('checkbox');
    expect(screen.queryByText('BC2')).toBeNull(); // sold: not offered
    fireEvent.click(pick);
    const body = await submit();
    expect(body.items[0]).toMatchObject({ product_id: 'P1', quantity: 1, stock_ids: ['U1'] });
  });

  it('dated stock is pre-ticked set-aside first, then earliest expiry; a piece on another open return or rejected at receiving is never offered', async () => {
    shelf = [
      { stock_id: 'L3', barcode: 'CL-2028', status: 'AVAILABLE', expiry_date: '2028-01-31' },
      { stock_id: 'L1', barcode: 'CL-2026', status: 'AVAILABLE', expiry_date: '2026-11-30' },
      { stock_id: 'L2', barcode: 'CL-ASIDE', status: 'QUARANTINED', expiry_date: '2027-06-30' },
      { stock_id: 'LR', barcode: 'CL-ON-VR', status: 'QUARANTINED', expiry_date: '2025-01-31', on_supplier_return: 'VR-1' },
      { stock_id: 'LX', barcode: 'CL-REJ', status: 'QUARANTINED', expiry_date: '2024-01-31', rejected: true },
    ];
    await openForm();
    fireEvent.change(screen.getByDisplayValue('1'), { target: { value: '2' } });
    scan('CL-SKU');
    await screen.findAllByRole('checkbox');
    const boxes = screen.getAllByRole('checkbox') as HTMLInputElement[];
    const labels = boxes.map((b) => b.parentElement!.textContent);
    expect(labels.map((t) => t!.split(/on shelf|set aside/)[0])).toEqual(['CL-ASIDE', 'CL-2026', 'CL-2028']);
    expect(boxes.map((b) => b.checked)).toEqual([true, true, false]);
    const body = await submit();
    expect(body.items[0]).toMatchObject({ quantity: 2, stock_ids: ['L2', 'L1'] });
  });

  it('Create waits while a save is in flight: a second click sends nothing', async () => {
    let finish: (v: unknown) => void = () => {};
    apiMock.post.mockImplementation(() => new Promise((r) => { finish = r; }));
    await openForm();
    scan('BC1');
    await screen.findByText('BC1');
    const buttons = screen.getAllByRole('button', { name: /create return/i });
    const create = buttons[buttons.length - 1] as HTMLButtonElement;
    fireEvent.click(create);
    await waitFor(() => expect(create.disabled).toBe(true));
    fireEvent.click(create);
    expect(apiMock.post).toHaveBeenCalledTimes(1);
    finish({ data: { return_id: 'VR2' } });
    await waitFor(() => expect(toastMock.success).toHaveBeenCalled());
  });

  it('an item with no piece scanned needs a note before it is sent', async () => {
    await openForm();
    fireEvent.change(screen.getByPlaceholderText('Enter product name'), { target: { value: 'Made-to-order lens' } });
    const buttons = screen.getAllByRole('button', { name: /create return/i });
    fireEvent.click(buttons[buttons.length - 1]);
    await waitFor(() => expect(toastMock.error).toHaveBeenCalled());
    expect(toastMock.error.mock.calls[0][0]).toMatch(/needs a note/);
    expect(apiMock.post).not.toHaveBeenCalled();
  });

  it('the Quarantine Queue link starts the form with its piece', async () => {
    await openForm('/purchase/vendor-returns?stock_id=U1&barcode=BC1');
    expect(await screen.findByText('BC1')).toBeTruthy();
    const body = await submit();
    expect(body.items[0].stock_ids).toEqual(['U1']);
    expect(body).not.toHaveProperty('stock_ids');
  });
});

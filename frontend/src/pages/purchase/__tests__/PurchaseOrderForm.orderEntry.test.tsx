// ============================================================================
// IMS 2.0 - Create Purchase Order: what a store manager actually does
// ============================================================================
// Procurement audit, reproduced on the REAL form (only the network is faked):
//   F22  The catalogue cost (3,200) filled the unit-cost box first, so the
//        lookup of the price last paid to THIS vendor (3,100 on PO 0002)
//        never ran and its caption never appeared.
//   F67  Tapping a prefilled number box left the caret after the value:
//        Qty 1 + typing 4 = 14 frames. Every number box selects on focus.
//   F87  Cancel / X threw a half-typed order away without asking.
//   F21  The product box asks for the WIDE search (model number anywhere,
//        brand spelt any way, colour words); the till keeps its own rule.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u1', name: 'Manager', roles: ['STORE_MANAGER'], activeStoreId: 'BV-DHN-02' },
    hasRole: () => true,
    hasPermission: () => true,
  }),
}));

const getProducts = vi.hoisted(() => vi.fn());
vi.mock('../../../services/api', () => ({
  vendorsApi: { createPurchaseOrder: vi.fn() },
  productApi: { getProducts },
}));

const getLastCost = vi.hoisted(() => vi.fn());
vi.mock('../../../services/api/inventory', () => ({
  vendorsApi: {
    getLastCost,
    getPoGstHeads: vi.fn().mockResolvedValue({ shop_gstin: '', heads: {} }),
  },
}));

vi.mock('../../../services/api/stores', () => ({
  storeApi: {
    getStore: vi.fn().mockResolvedValue({ gstin: '20AABCU9603R1Z1', state: 'Jharkhand' }),
  },
}));

vi.mock('../../../services/api/entities', () => ({
  entitiesApi: { meta: vi.fn().mockResolvedValue({ state_codes: [], entity_types: [] }) },
}));

import { PurchaseOrderForm } from '../PurchaseOrderForm';
import type { Supplier } from '../purchaseTypes';

const supplier = (id: string, name: string): Supplier => ({
  id,
  name,
  code: id.toUpperCase(),
  contactPerson: '',
  phone: '',
  email: '',
  address: '',
  city: '',
  state: 'Jharkhand',
  stateCode: '20',
  gstNumber: '',
  paymentTerms: 30,
  creditLimit: 0,
  currentOutstanding: 0,
  rating: 0,
  totalPurchases: 0,
  lastPurchaseDate: '',
  performance: { onTimeDelivery: 0, qualityScore: 0, priceCompetitiveness: 0 },
});

const MUMBAI = supplier('v-mum', 'Mumbai Lens House');
const RANCHI = supplier('v-rnc', 'Ranchi Optics');
const KOLKATA = supplier('v-kol', 'Kolkata Frames');

const CARRERA = {
  product_id: 'P-CAR',
  sku: 'FR-CAR-CA8895-C1',
  brand: 'Carrera',
  model: 'CA 8895',
  category: 'FRAME',
  mrp: 8990,
  cost_price: 3200,
  gst_rate: 5,
  hsn_code: '900311',
};

async function settle() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

async function renderForm(onClose = vi.fn()) {
  render(
    <PurchaseOrderForm
      suppliers={[MUMBAI, RANCHI, KOLKATA]}
      existingPOCount={0}
      onClose={onClose}
      onCreated={() => {}}
    />,
  );
  await settle();
  return onClose;
}

async function pickCarrera() {
  fireEvent.change(screen.getByPlaceholderText('Search catalogued product...'), {
    target: { value: '8895' },
  });
  fireEvent.click(await screen.findByRole('button', { name: /Carrera CA 8895/ }, { timeout: 3000 }));
}

beforeEach(() => {
  vi.clearAllMocks();
  getProducts.mockResolvedValue({ products: [CARRERA] });
  getLastCost.mockImplementation(async (vendorId: string) =>
    vendorId === 'v-mum'
      ? { costs: { 'P-CAR': { unit_price: 3100, date: '2026-09-17T10:00:00', po_number: 'PO/BV-DHN-02/26-27/0002' } } }
      : vendorId === 'v-kol'
        ? { costs: { 'P-CAR': { unit_price: 3000, date: '2026-08-02T10:00:00', po_number: 'PO/BV-DHN-02/26-27/0001' } } }
        : { costs: {} },
  );
});

describe('F21 - the product box asks for the wide search', () => {
  it('sends match=anywhere, so "8895" finds Carrera CA 8895', async () => {
    await renderForm();
    await pickCarrera();
    expect(getProducts).toHaveBeenCalledWith({ search: '8895', match: 'anywhere' });
  });
});

describe('F22 - the price last paid to THIS vendor wins over the catalogue cost', () => {
  it('fills 3,100 with its caption, not the catalogue 3,200', async () => {
    await renderForm();
    fireEvent.change(screen.getByLabelText('Vendor'), { target: { value: 'v-mum' } });
    await pickCarrera();

    const cost = screen.getByLabelText('Unit cost for line 1') as HTMLInputElement;
    await waitFor(() => expect(cost.value).toBe('3100'));
    expect(getLastCost).toHaveBeenCalledWith('v-mum', ['P-CAR']);
    expect(screen.getByText(/last paid ₹3,100 on 17 Sept? 2026/)).toBeInTheDocument();
  });

  it('a vendor never paid for this frame puts the catalogue cost back', async () => {
    await renderForm();
    fireEvent.change(screen.getByLabelText('Vendor'), { target: { value: 'v-mum' } });
    await pickCarrera();
    const cost = screen.getByLabelText('Unit cost for line 1') as HTMLInputElement;
    await waitFor(() => expect(cost.value).toBe('3100'));

    fireEvent.change(screen.getByLabelText('Vendor'), { target: { value: 'v-rnc' } });
    await waitFor(() => expect(getLastCost).toHaveBeenCalledWith('v-rnc', ['P-CAR']));
    await settle();
    expect(cost.value).toBe('3200');
    expect(screen.queryByText(/last paid/)).not.toBeInTheDocument();
  });

  it('a cost the manager typed is never replaced (the caption still tells what this vendor was paid)', async () => {
    await renderForm();
    await pickCarrera();
    const cost = screen.getByLabelText('Unit cost for line 1') as HTMLInputElement;
    fireEvent.change(cost, { target: { value: '2950' } });
    fireEvent.change(screen.getByLabelText('Vendor'), { target: { value: 'v-mum' } });
    await screen.findByText(/last paid ₹3,100 on 17 Sept? 2026/, undefined, { timeout: 3000 });
    expect(cost.value).toBe('2950');
  });

  it("switching vendor after typing a cost shows THAT vendor's last price, never the previous one's", async () => {
    await renderForm();
    fireEvent.change(screen.getByLabelText('Vendor'), { target: { value: 'v-mum' } });
    await pickCarrera();
    const cost = screen.getByLabelText('Unit cost for line 1') as HTMLInputElement;
    await waitFor(() => expect(cost.value).toBe('3100'));
    fireEvent.change(cost, { target: { value: '2950' } });

    fireEvent.change(screen.getByLabelText('Vendor'), { target: { value: 'v-kol' } });
    await screen.findByText(/last paid ₹3,000 on 2 Aug 2026/, undefined, { timeout: 3000 });
    expect(screen.queryByText(/3,100/)).not.toBeInTheDocument();
    expect(cost.value).toBe('2950');

    // A vendor never paid for it: no caption at all, not a stale one.
    fireEvent.change(screen.getByLabelText('Vendor'), { target: { value: 'v-rnc' } });
    await waitFor(() => expect(getLastCost).toHaveBeenCalledWith('v-rnc', ['P-CAR']));
    await settle();
    expect(screen.queryByText(/last paid/)).not.toBeInTheDocument();
    expect(cost.value).toBe('2950');
  });

  it('the caption still shows when the last order has no date', async () => {
    getLastCost.mockResolvedValue({ costs: { 'P-CAR': { unit_price: 3100, date: null, po_number: 'PO-2' } } });
    await renderForm();
    fireEvent.change(screen.getByLabelText('Vendor'), { target: { value: 'v-mum' } });
    await pickCarrera();
    const caption = await screen.findByText(/last paid ₹3,100/, undefined, { timeout: 3000 });
    expect(caption.textContent).not.toMatch(/ on /);
  });
});

describe('F22 - changing the product drops the cost the form filled for the old one', () => {
  it('"Change product" then "Not in the catalogue?" does not carry 3,100 onto an unpriced item', async () => {
    await renderForm();
    fireEvent.change(screen.getByLabelText('Vendor'), { target: { value: 'v-mum' } });
    await pickCarrera();
    const cost = screen.getByLabelText('Unit cost for line 1') as HTMLInputElement;
    await waitFor(() => expect(cost.value).toBe('3100'));

    fireEvent.click(screen.getByTitle('Change product'));
    expect(cost.value).toBe('0');
    expect(screen.queryByText(/last paid/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Not in the catalogue\?/ }));
    expect((screen.getByLabelText('Unit cost for line 1') as HTMLInputElement).value).toBe('0');
  });

  it('a cost the manager typed survives changing the product', async () => {
    await renderForm();
    await pickCarrera();
    const cost = screen.getByLabelText('Unit cost for line 1') as HTMLInputElement;
    fireEvent.change(cost, { target: { value: '2950' } });
    fireEvent.click(screen.getByTitle('Change product'));
    expect(cost.value).toBe('2950');
  });
});

describe('F67 - tapping a number box selects what is in it', () => {
  let selected: HTMLInputElement[];
  beforeEach(() => {
    selected = [];
    vi.spyOn(HTMLInputElement.prototype, 'select').mockImplementation(function (this: HTMLInputElement) {
      selected.push(this);
    });
  });
  afterEach(() => vi.restoreAllMocks());

  it('Qty and Unit cost select on focus, so typing replaces 1 / 0', async () => {
    await renderForm();
    const qty = screen.getByLabelText('Quantity for line 1');
    const cost = screen.getByLabelText('Unit cost for line 1');
    fireEvent.focus(qty);
    fireEvent.focus(cost);
    expect(selected).toEqual([qty, cost]);
  });

  it('the MRP box of an item not yet catalogued selects on focus too', async () => {
    await renderForm();
    fireEvent.click(screen.getByRole('button', { name: /Not in the catalogue\?/ }));
    const mrp = screen.getByLabelText('New item MRP');
    fireEvent.focus(mrp);
    expect(selected).toEqual([mrp]);
  });
});

describe('F87 - a half-typed order is not thrown away without asking', () => {
  let confirmSpy: ReturnType<typeof vi.spyOn>;
  beforeEach(() => {
    confirmSpy = vi.spyOn(window, 'confirm');
  });
  afterEach(() => vi.restoreAllMocks());

  it('an untouched form closes straight away (X and Cancel)', async () => {
    const onClose = await renderForm();
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onClose).toHaveBeenCalledTimes(2);
    expect(confirmSpy).not.toHaveBeenCalled();
  });

  it('asks first once something is entered; "no" keeps the order open', async () => {
    const onClose = await renderForm();
    fireEvent.change(screen.getByLabelText('Vendor'), { target: { value: 'v-mum' } });
    await pickCarrera();

    confirmSpy.mockReturnValue(false);
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(confirmSpy).toHaveBeenCalledTimes(2);
    expect(confirmSpy.mock.calls[0][0]).toMatch(/discard this order/i);
    expect(onClose).not.toHaveBeenCalled();

    confirmSpy.mockReturnValue(true);
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('typing only a note counts as entered', async () => {
    const onClose = await renderForm();
    fireEvent.change(screen.getByLabelText('Notes'), { target: { value: 'Quote MLH/Q/118' } });
    confirmSpy.mockReturnValue(false);
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(onClose).not.toHaveBeenCalled();
  });
});

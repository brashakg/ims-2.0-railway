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
vi.mock('../../../services/api/inventory', () => ({ vendorsApi: { getLastCost } }));

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
      suppliers={[MUMBAI, RANCHI]}
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
      : { costs: {} },
  );
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

  it('a cost the manager typed is never replaced', async () => {
    await renderForm();
    await pickCarrera();
    const cost = screen.getByLabelText('Unit cost for line 1') as HTMLInputElement;
    fireEvent.change(cost, { target: { value: '2950' } });
    fireEvent.change(screen.getByLabelText('Vendor'), { target: { value: 'v-mum' } });
    await new Promise((r) => setTimeout(r, 400));
    expect(cost.value).toBe('2950');
    expect(screen.queryByText(/last paid/)).not.toBeInTheDocument();
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

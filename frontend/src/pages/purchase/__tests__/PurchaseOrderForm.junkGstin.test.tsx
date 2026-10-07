// ============================================================================
// IMS 2.0 - Create PO screen: the tax head is the SERVER's, not the browser's
// ============================================================================
// The composer used to decide the head itself from the vendor's GSTIN and the
// shop's raw `store.gstin` -- a second GSTIN rule beside the server's
// shop_gstin (a blank own GSTIN read "cannot tell", a stale own GSTIN a stale
// head, a junk "88..." prefix a confident IGST). Round 12 #3: the form asks
// GET /vendors/po-gst-heads (shop_gstin + classify_supply) and shows exactly
// that: true -> IGST, false -> CGST + SGST, null/missing -> no verdict.
//
// This renders the real PurchaseOrderForm, picks the vendor, and steers the
// server's answer. It also pins that the form no longer reads the shop record.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u1', name: 'Admin', roles: ['ADMIN'], activeStoreId: 'BV-BOK-01' },
    hasRole: () => true,
    hasPermission: () => true,
  }),
}));

vi.mock('../../../services/api', () => ({
  vendorsApi: { createPurchaseOrder: vi.fn() },
  productApi: { getProducts: vi.fn().mockResolvedValue({ products: [] }) },
}));

// The composer's cost-prefill api -- never fires here (no line has a product).
vi.mock('../../../services/api/inventory', () => ({
  vendorsApi: {
    getLastCost: vi.fn().mockResolvedValue({ costs: {} }),
    getPoGstHeads: getHeads,
  },
}));

// The server's verdict per vendor, steered per test.
const heads = vi.hoisted(() => ({ current: {} as Record<string, boolean | null> }));
const getHeads = vi.hoisted(() => vi.fn());
const getStore = vi.hoisted(() => vi.fn());
vi.mock('../../../services/api/stores', () => ({ storeApi: { getStore } }));

import { PurchaseOrderForm } from '../PurchaseOrderForm';
import type { Supplier } from '../purchaseTypes';

const junkVendor: Supplier = {
  id: 'v1',
  name: 'Universal Optics',
  code: 'SUP004',
  contactPerson: 'Rakesh Sinha',
  phone: '9000000000',
  email: 'r@universal.in',
  address: '12 Main Road',
  city: 'Ranchi',
  state: '',
  stateCode: undefined,
  gstNumber: '88AABCU9603R1ZF', // 88 is not an Indian state
  paymentTerms: 30,
  creditLimit: 250000,
  currentOutstanding: 0,
  rating: 4,
  totalPurchases: 100000,
  lastPurchaseDate: '',
  performance: { onTimeDelivery: 90, qualityScore: 90, priceCompetitiveness: 90 },
};

/** Let the store fetch + state-list load settle and React flush. */
async function settle() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

async function renderFormAndPickJunkVendor() {
  render(
    <PurchaseOrderForm
      suppliers={[junkVendor]}
      existingPOCount={0}
      onClose={() => {}}
      onCreated={() => {}}
    />,
  );
  await settle();
  fireEvent.change(screen.getByLabelText('Vendor'), { target: { value: 'v1' } });
  await settle();
}

function expectNoVerdict() {
  expect(
    screen.getByText(/add the GST number to this vendor and to this shop/i),
  ).toBeInTheDocument();
  expect(screen.queryByText(/\bIGST\b/)).not.toBeInTheDocument();
  expect(screen.queryByText(/Different states/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/Same state/i)).not.toBeInTheDocument();
}

beforeEach(() => {
  vi.clearAllMocks();
  heads.current = {};
  getHeads.mockImplementation(async () => ({ shop_gstin: '20AABCU9603R1Z1', heads: heads.current }));
});

describe('Create PO screen shows the tax head the server resolved', () => {
  it('asks for the head of the ACTIVE shop and never reads the shop record', async () => {
    await renderFormAndPickJunkVendor();
    expect(getHeads).toHaveBeenCalledWith('BV-BOK-01');
    expect(getStore).not.toHaveBeenCalled();
  });

  it('server null (junk "88..." vendor GSTIN, or a shop with no number): no verdict', async () => {
    heads.current = { v1: null };
    await renderFormAndPickJunkVendor();
    expectNoVerdict();
  });

  it('server endpoint down: no verdict, the form still works', async () => {
    getHeads.mockRejectedValue(new Error('503'));
    await renderFormAndPickJunkVendor();
    expectNoVerdict();
  });

  it('server true: IGST', async () => {
    heads.current = { v1: true };
    await renderFormAndPickJunkVendor();
    expect(await screen.findByText(/Different states/i)).toBeInTheDocument();
    expect(screen.getAllByText('IGST').length).toBeGreaterThan(0);
  });

  it('server false: CGST + SGST, whatever the vendor GSTIN prefix says', async () => {
    // The vendor row carries an 88 prefix; the browser would call that IGST
    // (88 != 20). The server's answer is the one shown.
    heads.current = { v1: false };
    await renderFormAndPickJunkVendor();
    expect(await screen.findByText(/Same state/i)).toBeInTheDocument();
    expect(screen.queryByText(/\bIGST\b/)).not.toBeInTheDocument();
  });
});

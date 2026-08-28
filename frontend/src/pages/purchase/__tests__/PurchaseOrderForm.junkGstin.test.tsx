// ============================================================================
// IMS 2.0 - Create PO screen: a junk "88..." vendor GSTIN gets NO tax verdict
// ============================================================================
// The last wire nothing pinned. gstStateCode is pinned pure, the composer is
// pinned given the `interstate` prop, and the supplier CARD is pinned in both
// worlds -- but no test proved the Create-PO FORM actually routes its verdict
// through isInterStateSupply's state-list gate. Rewiring the form to the raw
// prefix comparison (`gstStateCode(vendor.gstNumber) !==
// gstStateCode(store.gstin)`, no gate) passed tsc AND the full 1,068-test
// suite, while a junk "88..." GSTIN printed "Different states -- the vendor
// charges IGST." on a real purchase order (88 != 20 as raw digits, over a
// state that does not exist -- the engine reads NO state off that GSTIN).
//
// So this file renders the real PurchaseOrderForm, picks a junk-88 vendor,
// and asserts no verdict appears -- with the state list DOWN and UP.
//
// ORDER MATTERS: useGstStateCodes caches module-level. A rejected load leaves
// the cache EMPTY (retried next mount), a successful one fills it for every
// later test in the file -- so the DOWN test must run first.

import { describe, it, expect, vi } from 'vitest';
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
  vendorsApi: { getLastCost: vi.fn().mockResolvedValue({ costs: {} }) },
}));

// The receiving shop: a real Jharkhand (20) registration.
vi.mock('../../../services/api/stores', () => ({
  storeApi: {
    getStore: vi.fn().mockResolvedValue({ gstin: '20AABCU9603R1Z1', state: 'Jharkhand' }),
  },
}));

// The state-list endpoint, steered per test: rejected first (DOWN), then a
// served list that -- like the real org_validation list -- has no state "88".
const metaMock = vi.hoisted(() => vi.fn());
vi.mock('../../../services/api/entities', () => ({ entitiesApi: { meta: metaMock } }));

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
  // The honest fallback is on screen (proves the totals box rendered) ...
  expect(
    screen.getByText(/add the GST number to this vendor and to this shop/i),
  ).toBeInTheDocument();
  // ... and neither verdict is. Under the raw-prefix rewiring both of these
  // light up as "IGST" / "Different states" (88 != 20); under a falsy-unknown
  // regression the "Same state" sentence appears instead.
  expect(screen.queryByText(/\bIGST\b/)).not.toBeInTheDocument();
  expect(screen.queryByText(/Different states/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/Same state/i)).not.toBeInTheDocument();
}

describe('Create PO screen with a junk "88..." vendor GSTIN', () => {
  it('state list DOWN: no verdict for the whole session', async () => {
    metaMock.mockRejectedValue(new Error('503'));
    await renderFormAndPickJunkVendor();
    expectNoVerdict();
  });

  it('state list UP (no state 88 in it): still no verdict', async () => {
    metaMock.mockResolvedValue({
      state_codes: [
        { code: '20', name: 'Jharkhand' },
        { code: '27', name: 'Maharashtra' },
      ],
      entity_types: [],
    });
    await renderFormAndPickJunkVendor();
    expectNoVerdict();
  });
});

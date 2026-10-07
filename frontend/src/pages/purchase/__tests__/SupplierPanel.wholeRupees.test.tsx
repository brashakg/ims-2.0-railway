// ============================================================================
// Review r1 #6/#30: the Suppliers card reads the ledger in whole rupees
// ============================================================================
// The card printed the supplier ledger's balance as lakh to one decimal, so
// Rs 4,999 owed read "Rs 0.0L" (the F56 "Rs 0 on one screen") and a Rs 1,500.50
// advance read "Rs -0.0L", while the Purchases report said "Rs 4,999" and
// "Rs 1,501 advance" for the same suppliers. The all-time billed figure sat
// under "Total Purchases", the ledger's "Billed" under another name. The card
// now uses the report's own formatter.
//
// Review r2 #4/#23: it then took the report's bare "Billed" too -- but the
// report's Billed is ONE month and the card's is every bill to date, so two
// neighbouring Purchase tabs used one word for two figures. The card's label
// says its period: "Billed to date".

import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';

vi.mock('../../../context/AuthContext', () => ({
  // The GST-head chips follow the active shop (usePoGstHeads(user.activeStoreId)).
  useAuth: () => ({ user: { id: 'U1', roles: ['ACCOUNTANT'], activeStoreId: 'BV-DHN-01', storeIds: ['BV-DHN-01'] } }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));
// The tax chip is the server's verdict for the active shop (#1167); not under test here.
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', roles: ['ADMIN'], activeStoreId: 'S1' } }),
}));
vi.mock('../../../hooks/usePoGstHeads', () => ({ usePoGstHeads: () => ({}) }));
vi.mock('../../../services/api', () => ({ vendorsApi: { generatePortalToken: vi.fn() } }));
vi.mock('../../../services/api/entities', () => ({
  entitiesApi: { meta: vi.fn().mockResolvedValue({ state_codes: [], entity_types: [] }) },
}));

import { SupplierPanel, type SupplierBalance } from '../SupplierPanel';
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
  state: '',
  stateCode: '',
  gstNumber: '',
  paymentTerms: 30,
  creditLimit: 0,
  currentOutstanding: 0,
  rating: 4,
  totalPurchases: 0,
  lastPurchaseDate: '',
  performance: { onTimeDelivery: 0, qualityScore: 0, priceCompetitiveness: 0 },
});

/** The figure printed under `label` on the named supplier's card. */
function figure(name: string, label: string): string | null {
  const card = screen.getByText(name).closest('.card') as HTMLElement;
  const [el] = within(card).queryAllByText(label);
  return el ? (el.nextElementSibling?.textContent ?? '').trim() : null;
}

function show(balances: Record<string, SupplierBalance>) {
  render(
    <SupplierPanel
      suppliers={[supplier('v1', 'Essilor'), supplier('v2', 'Pune Frames'), supplier('v3', 'Small Co')]}
      balances={balances}
    />,
  );
}

describe('the Suppliers card says what the Purchases report says', () => {
  it('Rs 4,999 owed reads Rs 4,999 under Outstanding and Billed to date -- never Rs 0.0L', () => {
    show({ v1: { balance: 4999, total_billed: 4999 } });
    expect(figure('Essilor', 'Outstanding')).toBe('₹4,999');
    expect(figure('Essilor', 'Billed to date')).toBe('₹4,999');
    expect(screen.queryByText(/0\.0L/)).toBeNull();
  });

  it('an advance reads as the report reads it: Rs 1,501 advance, never Rs -0.0L', () => {
    show({ v2: { balance: -1500.5, total_billed: 0 } });
    expect(figure('Pune Frames', 'Outstanding')).toBe('₹1,501 advance');
    expect(figure('Pune Frames', 'Billed to date')).toBe('₹0');
  });

  it("the all-time billed figure is labelled Billed to date -- not Total Purchases, not the report's one-month Billed", () => {
    show({ v3: { balance: 0.4, total_billed: 1234567.8 } });
    expect(screen.queryByText('Total Purchases')).toBeNull();
    expect(screen.queryAllByText('Billed')).toHaveLength(0);
    expect(figure('Small Co', 'Billed to date')).toBe('₹12,34,568');
    expect(figure('Small Co', 'Outstanding')).toBe('₹0');
  });

  it('a supplier the ledger has no figure for shows a dash, not a made-up Rs 0', () => {
    show({});
    expect(figure('Essilor', 'Outstanding')).toBe('—');
    expect(figure('Essilor', 'Billed to date')).toBe('—');
  });
});

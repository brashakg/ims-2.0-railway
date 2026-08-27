// ============================================================================
// IMS 2.0 - Suppliers tab: the GST chip when the state list NEVER arrives
// ============================================================================
// Pin for the fail-closed rule. The card's verdict is gated through the
// server-fed state list (useGstStateCodes -> GET /entities/meta/options).
// This file mocks that endpoint DOWN, so the hook's session cache never
// fills — which is why these tests live in their own file: the cache is
// module-level, and any sibling test that loads the list successfully would
// leave it filled for everyone after it.
//
// The clause this replaces was fail-open: "until the list arrives the raw
// read stands". Under it, a meta-endpoint failure meant a junk "88..." GSTIN
// printed "Other state - IGST" for the whole session (88 != 20 as raw
// digits), a tax verdict over a state that does not exist — and no test
// noticed when the clause was deleted. Fail-closed: no list, no verdict.

import { describe, it, expect, vi } from 'vitest';
import { render, screen, act } from '@testing-library/react';

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));

vi.mock('../../../hooks/useStorePrintInfo', () => ({
  // Buying store registered in Jharkhand (20) — a real, known state.
  useStorePrintInfo: () => ({
    storeName: 'BV Bokaro', address: '', city: '', state: 'Jharkhand',
    pincode: '', stateCode: '20', gstin: '20AABCU9603R1Z1',
  }),
}));

vi.mock('../../../services/api', () => ({
  vendorsApi: { generatePortalToken: vi.fn(), updateVendor: vi.fn(), createVendor: vi.fn() },
}));

// The state list endpoint is DOWN for the whole session.
vi.mock('../../../services/api/entities', () => ({
  entitiesApi: { meta: vi.fn().mockRejectedValue(new Error('503')) },
}));

import { SupplierPanel } from '../SupplierPanel';
import type { Supplier } from '../purchaseTypes';

const base: Supplier = {
  id: 'v1',
  name: 'Universal Optics',
  code: 'SUP004',
  contactPerson: 'Rakesh Sinha',
  phone: '9000000000',
  email: 'r@universal.in',
  address: '12 Main Road',
  city: 'Ranchi',
  state: 'Jharkhand',
  stateCode: '20',
  gstNumber: '20AABCU9603R1Z1',
  paymentTerms: 30,
  creditLimit: 250000,
  currentOutstanding: 0,
  rating: 4,
  totalPurchases: 100000,
  lastPurchaseDate: '',
  performance: { onTimeDelivery: 90, qualityScore: 90, priceCompetitiveness: 90 },
};

/** Let the rejected meta promise settle and React flush. */
async function settle() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

describe('SupplierPanel GST chip with the state list unavailable', () => {
  it('a junk "88..." GSTIN never prints IGST — unknown, for the whole session', async () => {
    const junk: Supplier = {
      ...base, stateCode: undefined, state: '', gstNumber: '88AABCU9603R1ZF',
    };
    render(<SupplierPanel suppliers={[junk]} />);
    await settle();
    // The engine (org_validation, behind determine_place_of_supply) reads NO
    // state off "88...", so "Other state - IGST" here would contradict the
    // bill. With no list to check against, no verdict is the only honest chip.
    expect(screen.getByText(/tax split unknown/i)).toBeInTheDocument();
    expect(screen.queryByText(/\bIGST\b/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/CGST \+ SGST/i)).not.toBeInTheDocument();
  });

  it('even a genuine same-state pair gets no verdict without the list', async () => {
    // Fail-closed cuts both ways: two valid Jharkhand GSTINs cannot be
    // CONFIRMED valid without the server's list, so the chip stays unknown
    // rather than trusting raw prefixes it cannot check.
    render(<SupplierPanel suppliers={[base]} />);
    await settle();
    expect(screen.getByText(/tax split unknown/i)).toBeInTheDocument();
    expect(screen.queryByText(/CGST \+ SGST/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/\bIGST\b/i)).not.toBeInTheDocument();
  });
});

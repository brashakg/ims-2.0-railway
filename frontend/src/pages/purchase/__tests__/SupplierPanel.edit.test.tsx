// ============================================================================
// IMS 2.0 - Suppliers tab: the Edit button, and the GST state chip
// ============================================================================
// Owner report (2026-08-26): "edit vendor button is not working". The pencil
// on each supplier card was a <button> with no onClick at all -- it rendered,
// it depressed, and nothing happened.
//
// Also pinned here: the card must say which state the vendor is in and whether
// buying from them is intra-state (CGST+SGST) or inter-state (IGST), because
// that is the thing the owner cannot check by eye today.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

const toastMock = vi.hoisted(() => ({
  success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn(),
}));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toastMock }));

// The buying store's identity (its GST state) comes from the shared print-info
// hook; stub the fetch, not the component under test.
const storeInfo = vi.hoisted(() => ({ current: null as null | Record<string, unknown> }));
vi.mock('../../../hooks/useStorePrintInfo', () => ({
  useStorePrintInfo: () => storeInfo.current,
}));

vi.mock('../../../services/api', () => ({
  vendorsApi: { generatePortalToken: vi.fn(), updateVendor: vi.fn(), createVendor: vi.fn() },
}));

// The 2-digit -> state-name list lives on the server (org_validation), served
// by GET /entities/meta/options. Stub the transport, not the lookup.
vi.mock('../../../services/api/entities', () => ({
  entitiesApi: {
    meta: vi.fn().mockResolvedValue({
      state_codes: [
        { code: '20', name: 'Jharkhand' },
        { code: '27', name: 'Maharashtra' },
      ],
      entity_types: [],
    }),
  },
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

beforeEach(() => {
  vi.clearAllMocks();
  // Buying store is REGISTERED in Jharkhand (20) unless a test says otherwise.
  // Its gstin is what taxes the purchase; stores.py stamps it from the entity's
  // registrations, while state_code below comes from the store's address.
  storeInfo.current = {
    storeName: 'BV Bokaro', address: '', city: '', state: 'Jharkhand', pincode: '',
    stateCode: '20', gstin: '20AABCU9603R1Z1',
  };
});

describe('SupplierPanel edit button', () => {
  it('calls onEdit with the supplier when the Edit button is pressed', () => {
    const onEdit = vi.fn();
    render(<SupplierPanel suppliers={[base]} onEdit={onEdit} />);

    fireEvent.click(screen.getByRole('button', { name: /edit universal optics/i }));

    expect(onEdit).toHaveBeenCalledTimes(1);
    expect(onEdit).toHaveBeenCalledWith(base);
  });

  it('still renders when no onEdit handler is supplied', () => {
    expect(() => render(<SupplierPanel suppliers={[base]} />)).not.toThrow();
  });
});

describe('SupplierPanel GST treatment chip', () => {
  it('shows CGST+SGST for a vendor in the same state as the buying store', () => {
    render(<SupplierPanel suppliers={[base]} />);
    expect(screen.getByText(/CGST \+ SGST/i)).toBeInTheDocument();
    expect(screen.queryByText(/\bIGST\b/i)).not.toBeInTheDocument();
  });

  it('shows IGST for a vendor in another state', () => {
    const mh: Supplier = { ...base, state: 'Maharashtra', stateCode: '27', gstNumber: '27AAPFU0939F1ZV' };
    render(<SupplierPanel suppliers={[mh]} />);
    expect(screen.getByText(/\bIGST\b/i)).toBeInTheDocument();
  });

  it('derives the state code from the GSTIN when the vendor row has none stored', async () => {
    // Legacy vendors created before state_code was persisted.
    const legacy: Supplier = { ...base, stateCode: undefined, gstNumber: '27AAPFU0939F1ZV', state: '' };
    render(<SupplierPanel suppliers={[legacy]} />);
    expect(screen.getByText(/\bIGST\b/i)).toBeInTheDocument();
    // The name is looked up from the server's state-code list, not a second
    // hardcoded copy in the browser.
    expect(await screen.findByText(/Maharashtra/)).toBeInTheDocument();
  });

  it('reads as UNKNOWN, never as "same state", when the buying store has no GST state', () => {
    // The dangerous shape: a helper that answers "is this inter-state?" with a
    // boolean has no way to say "I do not know", so false doubles as "same
    // state" and the card prints "Same state - CGST + SGST" over a vendor whose
    // tax split nobody has established. That is a wrong-tax statement shown to
    // staff. Unknown must read as unknown.
    storeInfo.current = { storeName: 'X', address: '', city: '', state: '', pincode: '' };
    render(<SupplierPanel suppliers={[base]} />);
    expect(screen.getByText(/tax split unknown/i)).toBeInTheDocument();
    expect(screen.queryByText(/CGST \+ SGST/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/\bIGST\b/i)).not.toBeInTheDocument();
  });

  it("uses the store's REGISTRATION, never its address, to decide the split", () => {
    // The configuration this business actually has: a store whose address is in
    // one state while it bills under an entity registered in another (WizOpt's
    // online store bills under BV Opticals Pvt Ltd). stores.py derives
    // state_code from the ADDRESS and gstin from the ENTITY, so the two
    // legitimately disagree.
    //
    // determine_place_of_supply reads the recipient GSTIN and nothing else, so
    // an address-first card prints the opposite verdict to the bill it is
    // sitting next to. Registration wins.
    storeInfo.current = {
      storeName: 'WizOpt Online', address: '', city: '', state: 'Maharashtra',
      pincode: '', stateCode: '27', gstin: '20AABCU9603R1Z1',
    };
    render(<SupplierPanel suppliers={[base]} />);
    expect(screen.getByText(/CGST \+ SGST/i)).toBeInTheDocument();
    expect(screen.queryByText(/\bIGST\b/i)).not.toBeInTheDocument();
  });

  it("uses the vendor's GSTIN, never a stale stored state code, for the split", () => {
    // Same rule on the other side. A vendor row can carry a state_code written
    // before its GSTIN was corrected; the bill will be taxed off the GSTIN, so
    // the card must be too.
    const moved: Supplier = { ...base, stateCode: '20', state: 'Jharkhand', gstNumber: '27AAPFU0939F1ZV' };
    render(<SupplierPanel suppliers={[moved]} />);
    expect(screen.getByText(/\bIGST\b/i)).toBeInTheDocument();
    expect(screen.queryByText(/CGST \+ SGST/i)).not.toBeInTheDocument();
  });

  it('renders NO tax verdict for a vendor GSTIN whose state code the server does not know', async () => {
    // "88" parses as two digits but is not an Indian GST state. The engine's
    // parser (org_validation) reads NO state off such a GSTIN, so the card
    // must not print "Other state - IGST" (88 != 20) over a state that does
    // not exist. Honest fallback: the unknown chip.
    const junk: Supplier = { ...base, stateCode: undefined, state: '', gstNumber: '88AABCU9603R1ZF' };
    render(<SupplierPanel suppliers={[junk]} />);
    expect(await screen.findByText(/tax split unknown/i)).toBeInTheDocument();
    expect(screen.queryByText(/\bIGST\b/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/CGST \+ SGST/i)).not.toBeInTheDocument();
  });

  it("renders NO tax verdict when the buying store's own GSTIN carries an unknown state code", async () => {
    // Same gate on the buyer side: a store GSTIN with a junk prefix must not
    // anchor an IGST/CGST claim against a real vendor state.
    storeInfo.current = {
      storeName: 'X', address: '', city: '', state: '', pincode: '',
      gstin: '99AABCU9603R1ZF',
    };
    render(<SupplierPanel suppliers={[base]} />);
    expect(await screen.findByText(/tax split unknown/i)).toBeInTheDocument();
    expect(screen.queryByText(/\bIGST\b/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/CGST \+ SGST/i)).not.toBeInTheDocument();
  });

  it('shows no tax chip at all for an unregistered vendor', () => {
    // No GSTIN means no GST on the purchase -- neither split applies, and the
    // GSTIN line already says "Unregistered".
    const unregistered: Supplier = { ...base, gstNumber: '', stateCode: undefined, state: '' };
    render(<SupplierPanel suppliers={[unregistered]} />);
    expect(screen.getByText(/unregistered \(no gstin\)/i)).toBeInTheDocument();
    expect(screen.queryByText(/CGST \+ SGST/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/tax split unknown/i)).not.toBeInTheDocument();
  });
});

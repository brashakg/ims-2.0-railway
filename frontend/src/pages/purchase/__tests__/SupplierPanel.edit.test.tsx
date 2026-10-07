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

// The ACTIVE shop is the buyer; steer it per test.
const auth = vi.hoisted(() => ({ store: 'S1' as string | undefined }));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', roles: ['ADMIN'], activeStoreId: auth.store } }),
}));

// The tax head of each vendor is the SERVER's verdict (GET
// /vendors/po-gst-heads: shop_gstin + classify_supply). Stub the transport and
// steer the answer per test; the card only maps it onto a chip.
const poHeads = vi.hoisted(() => ({
  current: {} as Record<string, boolean | null>,
  down: false,
}));
vi.mock('../../../services/api/inventory', () => ({
  vendorsApi: {
    getPoGstHeads: vi.fn(async () =>
      poHeads.down
        ? { shop_gstin: '', heads: {} }
        : { shop_gstin: '20AABCU9603R1Z1', heads: poHeads.current },
    ),
  },
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
import { vendorsApi as poApi } from '../../../services/api/inventory';
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
  poHeads.current = {};
  poHeads.down = false;
  auth.store = 'S1';
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

describe('SupplierPanel GST treatment chip (the server decides)', () => {
  it('shows CGST+SGST when the server says false', async () => {
    poHeads.current = { v1: false };
    render(<SupplierPanel suppliers={[base]} />);
    expect(await screen.findByText(/CGST \+ SGST/i)).toBeInTheDocument();
    expect(screen.queryByText(/\bIGST\b/i)).not.toBeInTheDocument();
  });

  it('shows IGST when the server says true', async () => {
    poHeads.current = { v1: true };
    const mh: Supplier = { ...base, state: 'Maharashtra', stateCode: '27', gstNumber: '27AAPFU0939F1ZV' };
    render(<SupplierPanel suppliers={[mh]} />);
    expect(await screen.findByText(/\bIGST\b/i)).toBeInTheDocument();
  });

  it('derives the state NAME from the GSTIN when the vendor row has none stored', async () => {
    poHeads.current = { v1: true };
    const legacy: Supplier = { ...base, stateCode: undefined, gstNumber: '27AAPFU0939F1ZV', state: '' };
    render(<SupplierPanel suppliers={[legacy]} />);
    expect(await screen.findByText(/Maharashtra/)).toBeInTheDocument();
  });

  it('reads UNKNOWN, never a split, when the server says null', async () => {
    poHeads.current = { v1: null };
    render(<SupplierPanel suppliers={[base]} />);
    expect(await screen.findByText(/tax split unknown/i)).toBeInTheDocument();
    expect(screen.queryByText(/CGST \+ SGST/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/\bIGST\b/i)).not.toBeInTheDocument();
  });

  it('reads UNKNOWN for a vendor the server did not answer for, and while it is down', async () => {
    poHeads.down = true;
    render(<SupplierPanel suppliers={[base]} />);
    expect(await screen.findByText(/tax split unknown/i)).toBeInTheDocument();
    expect(screen.queryByText(/CGST \+ SGST/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/\bIGST\b/i)).not.toBeInTheDocument();
  });

  it('never works the head out of the vendor GSTIN itself (a stale stored state is ignored)', async () => {
    // Vendor GSTIN says Maharashtra and the stored state says Jharkhand; the
    // chip follows the SERVER (false -> CGST + SGST), not either field.
    poHeads.current = { v1: false };
    const moved: Supplier = { ...base, stateCode: '20', state: 'Jharkhand', gstNumber: '27AAPFU0939F1ZV' };
    render(<SupplierPanel suppliers={[moved]} />);
    expect(await screen.findByText(/CGST \+ SGST/i)).toBeInTheDocument();
    expect(screen.queryByText(/\bIGST\b/i)).not.toBeInTheDocument();
  });

  it('shows no tax chip at all for an unregistered vendor', () => {
    const unregistered: Supplier = { ...base, gstNumber: '', stateCode: undefined, state: '' };
    render(<SupplierPanel suppliers={[unregistered]} />);
    expect(screen.getByText(/unregistered \(no gstin\)/i)).toBeInTheDocument();
    expect(screen.queryByText(/CGST \+ SGST/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/tax split unknown/i)).not.toBeInTheDocument();
  });
});

describe('SupplierPanel follows the active shop', () => {
  it('asks the server for the ACTIVE shop, and again when the shop is switched', async () => {
    poHeads.current = { v1: true };
    const { rerender } = render(<SupplierPanel suppliers={[base]} />);
    await screen.findByText(/\bIGST\b/i);
    expect(poApi.getPoGstHeads).toHaveBeenLastCalledWith('S1');

    // Top-bar shop switch: the mounted tab re-asks for the new shop.
    auth.store = 'PUNE';
    poHeads.current = { v1: false };
    rerender(<SupplierPanel suppliers={[base]} />);
    expect(await screen.findByText(/CGST \+ SGST/i)).toBeInTheDocument();
    expect(poApi.getPoGstHeads).toHaveBeenLastCalledWith('PUNE');
    expect(screen.queryByText(/\bIGST\b/i)).not.toBeInTheDocument();
  });
});

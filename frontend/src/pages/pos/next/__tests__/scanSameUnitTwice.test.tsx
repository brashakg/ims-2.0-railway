// ============================================================================
// Scanning the SAME serialized piece twice adds it ONCE (audit R34)
// ============================================================================
// A scanner double-trigger (or a second scan while the first lookup was still
// in flight) left two or three cart lines for one physical unit - the bill
// asked Rs 2000 / Rs 3000 for one piece on the shelf. The cart now refuses a
// second line for a stock_id already on it ("already on this bill"), on both
// counter doors. Distinct units of one SKU, and typed (non-serialized) adds,
// still add as before.

(() => {
  const m = new Map<string, string>();
  const ls = {
    getItem: (k: string) => (m.has(k) ? m.get(k)! : null),
    setItem: (k: string, v: string) => { m.set(k, String(v)); },
    removeItem: (k: string) => { m.delete(k); },
    clear: () => { m.clear(); },
    key: (i: number) => Array.from(m.keys())[i] ?? null,
    get length() { return m.size; },
  };
  Object.defineProperty(globalThis, 'localStorage', { value: ls, configurable: true, writable: true });
})();

import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u-1', name: 'Meena', activeStoreId: 'BV-BOK-01', roles: ['STORE_MANAGER'] },
    hasRole: () => true,
  }),
}));
vi.mock('../../../../components/pos/submitOrder', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../../components/pos/submitOrder')>()),
  submitPosOrder: vi.fn(),
}));
vi.mock('../../../../hooks/usePOSQueries', () => ({
  useProducts: () => ({ data: [], isLoading: false }),
  useSellableStock: () => ({ data: undefined }),
}));
vi.mock('../../../../hooks/useIsOnlineStore', () => ({ useIsOnlineStore: () => false }));
vi.mock('../../../../components/pos/WalkoutComplianceBanner', () => ({ default: () => null }));
vi.mock('../../../../components/pos/WalkinWalkoutControls', () => ({ WalkinWalkoutControls: () => null }));
vi.mock('../../../../components/pos/HeldBillsControls', () => ({ HeldBillsControls: () => null }));
vi.mock('../../../../components/pos/LensDetailsModal', () => ({
  addManualLensToCart: vi.fn(),
  LensDetailsModal: () => null,
}));
vi.mock('../../../../components/pos/NewPrescriptionAtTill', () => ({ NewPrescriptionAtTill: () => null }));
vi.mock('../../../../components/pos/CustomerCardWithLoyalty', () => ({ CustomerCardWithLoyalty: () => null }));
vi.mock('../../../../components/pos/POSCart', () => ({ CartSidebar: () => <div>cart</div> }));
vi.mock('../../../../components/pos/DiscountModal', () => ({
  DiscountModal: () => null,
  toDiscountItem: (x: unknown) => x,
}));
vi.mock('../../../../components/pos/BillDiscountCard', () => ({ BillDiscountCard: () => null }));
vi.mock('../../../../components/pos/POSPayment', () => ({ StepPayment: () => <div>payment</div> }));
vi.mock('../../../../components/pos/POSInvoice', () => ({ StepComplete: () => null }));
vi.mock('../../../../components/pos/PrescriptionSelectModal', () => ({ PrescriptionSelectModal: () => null }));
vi.mock('../../../../components/customers/AddCustomerModal', () => ({ AddCustomerModal: () => null }));
vi.mock('../../../../components/pos/SalespersonPicker', () => ({ SalespersonPicker: () => null }));
vi.mock('../../../../components/pos/CustomerSearchBar', () => ({
  CustomerSearchBar: () => null,
  createAndSelectCustomer: vi.fn(),
  selectCustomerHit: vi.fn(),
}));
vi.mock('../PosWidgets', () => ({ PosWidgets: () => null }));
vi.mock('../SaleCompleteScreen', () => ({ default: () => null, CounterCompleteScreen: () => null }));
vi.mock('../ProductResultsStrip', () => ({ default: () => null, MAX_PRODUCT_RESULTS: 12 }));
vi.mock('../DeliveryOptionsRow', () => ({ default: () => null }));
// A scanner the test drives: whatever is typed + Enter is a scan.
vi.mock('../../../../components/pos/BarcodeScanner', () => ({
  BarcodeScanner: ({ onScan }: { onScan: (v: string) => void }) => (
    <input
      aria-label="scan"
      onKeyDown={(e) => {
        if (e.key === 'Enter') onScan((e.target as HTMLInputElement).value);
      }}
    />
  ),
}));
// The real intake brain, with only the network lookup stubbed: a barcode is
// one physical unit (its own stock_id) of SKU-1.
const resolveBarcode = vi.fn();
vi.mock('../../../../components/pos/productIntake', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../../components/pos/productIntake')>()),
  resolveBarcode: (...a: unknown[]) => resolveBarcode(...a),
}));

import { MemoryRouter } from 'react-router-dom';
import { GeneralCounterSurface } from '../GeneralCounterSurface';
import { BillingSurface } from '../BillingSurface';
import { usePOSStore } from '../../../../stores/posStore';

const unit = (code: string) => ({
  ok: true,
  product: {
    product_id: 'p-1', name: 'Titan watch', sku: 'SKU-1', barcode: code,
    stock_id: `stk-${code}`, category: 'WATCH', mrp: 1000, offer_price: 1000,
  },
});

const scan = (code: string) => {
  const box = screen.getByLabelText('scan') as HTMLInputElement;
  fireEvent.change(box, { target: { value: code } });
  fireEvent.keyDown(box, { key: 'Enter' });
};
const cart = () => usePOSStore.getState().cart;

beforeEach(() => {
  usePOSStore.getState().resetTransaction('discard');
  resolveBarcode.mockReset().mockImplementation(async (_s: string, code: string) => unit(code));
});

describe.each([
  ['general counter', GeneralCounterSurface],
  ['billing counter', BillingSurface],
])('R34 - %s', (_name, Surface) => {
  const mount = () => render(<MemoryRouter><Surface /></MemoryRouter>);

  it('a second scan of the same unit adds nothing and says it is already on the bill', async () => {
    mount();
    scan('AU07');
    await waitFor(() => expect(cart()).toHaveLength(1));
    scan('AU07');
    await waitFor(() => expect(screen.getByText(/already on this bill/i)).toBeTruthy());
    expect(cart()).toHaveLength(1);
  });

  it('three rapid scans whose lookups overlap still leave ONE line', async () => {
    const releases: (() => void)[] = [];
    resolveBarcode.mockImplementation(
      (_s: string, code: string) => new Promise((r) => releases.push(() => r(unit(code)))),
    );
    mount();
    scan('AU07');
    scan('AU07');
    scan('AU07');
    await waitFor(() => expect(releases).toHaveLength(3));
    await act(async () => { releases.forEach((r) => r()); });
    expect(cart()).toHaveLength(1);
  });

  it('two DIFFERENT units of the same SKU both add (positive control)', async () => {
    mount();
    scan('AU07');
    await waitFor(() => expect(cart()).toHaveLength(1));
    scan('AU08');
    await waitFor(() => expect(cart()).toHaveLength(2));
    expect(screen.queryByText(/already on this bill/i)).toBeNull();
  });
});

describe('R34 - the cart itself', () => {
  it('a typed (non-serialized) product can still be added twice', () => {
    const add = () => usePOSStore.getState().addToCart({
      product_id: 'p-2', name: 'Lens cloth', sku: 'SKU-2', unit_price: 50, mrp: 50, quantity: 1,
    } as never);
    add();
    add();
    expect(cart()).toHaveLength(2);
  });
});

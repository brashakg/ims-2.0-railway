// ============================================================================
// The three things the new billing surface could not do, which kept the old
// /pos alive
// ============================================================================
// The legacy-till retirement inventory found the blockers all here:
//
//   G1  an Rx sale created NO workshop job. submitOrder builds the job only
//       for sale_type === 'prescription_order', and only the classic till ever
//       set that. This is how the shop makes most of its money, and the lab
//       never heard about it.
//   G2  a DEPOSIT sale was impossible: submitOrder refuses a partly-paid bill
//       unless is_advance_payment, which only the classic review panel set --
//       so the delivery counter had nothing to collect on.
//   G3  a wrong customer pick could only be undone by reloading the page.
//
// sale_type is DERIVED, never asked: Rx on the bill + something the lab makes
// or fits (frame/sunglass or a lens line -- the job creator's own predicate,
// workshopItemsOf) => prescription order; anything else => quick sale. Each
// test below reads the state the surface actually handed to the submit brain.

import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u-1', name: 'Meena', activeStoreId: 'BV-BOK-01', roles: ['CASHIER'] },
    hasRole: () => true,
  }),
}));

// Keep the REAL workshopItemsOf (the predicate under test) and stub only the
// submit itself.
const submitPosOrder = vi.fn();
vi.mock('../../../../components/pos/submitOrder', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../../components/pos/submitOrder')>()),
  submitPosOrder: (...a: unknown[]) => submitPosOrder(...a),
}));

vi.mock('../../../../hooks/usePOSQueries', () => ({
  useProducts: () => ({ data: [], isLoading: false }),
}));
vi.mock('../../../../hooks/useIsOnlineStore', () => ({ useIsOnlineStore: () => false }));

// Leaf components: the smallest thing that still lets the surface render.
vi.mock('../../../../components/pos/WalkoutComplianceBanner', () => ({ default: () => null }));
vi.mock('../../../../components/pos/WalkinWalkoutControls', () => ({ WalkinWalkoutControls: () => null }));
vi.mock('../../../../components/pos/HeldBillsControls', () => ({ HeldBillsControls: () => null }));
vi.mock('../../../../components/pos/LensDetailsModal', () => ({
  addManualLensToCart: vi.fn(),
  LensDetailsModal: () => null,
}));
vi.mock('../../../../components/pos/NewPrescriptionAtTill', () => ({
  NewPrescriptionAtTill: () => null,
}));
// The card echoes the ONE prop this file cares about: the till's clear-pick door.
vi.mock('../../../../components/pos/CustomerCardWithLoyalty', () => ({
  CustomerCardWithLoyalty: ({ onChange }: { onChange?: () => void }) => (
    <button type="button" onClick={onChange}>change-customer</button>
  ),
}));
vi.mock('../../../../components/pos/POSCart', () => ({ CartSidebar: () => <div>cart</div> }));
vi.mock('../../../../components/pos/DiscountModal', () => ({
  DiscountModal: () => null,
  toDiscountItem: (x: unknown) => x,
}));
vi.mock('../../../../components/pos/BillDiscountCard', () => ({ BillDiscountCard: () => null }));
vi.mock('../../../../components/pos/POSPayment', () => ({ StepPayment: () => <div>payment</div> }));
vi.mock('../../../../components/pos/POSInvoice', () => ({ StepComplete: () => null }));
vi.mock('../../../../components/pos/BarcodeScanner', () => ({ BarcodeScanner: () => null }));
vi.mock('../../../../components/pos/PrescriptionSelectModal', () => ({
  PrescriptionSelectModal: () => null,
}));
vi.mock('../../../../components/customers/AddCustomerModal', () => ({ AddCustomerModal: () => null }));
vi.mock('../../../../components/pos/SalespersonPicker', () => ({ SalespersonPicker: () => null }));
vi.mock('../../../../components/pos/CustomerSearchBar', () => ({
  CustomerSearchBar: () => null,
  createAndSelectCustomer: vi.fn(),
  selectCustomerHit: vi.fn(),
}));
vi.mock('../PosWidgets', () => ({ PosWidgets: () => null }));
vi.mock('../SaleCompleteScreen', () => ({
  default: (p: { orderId: string }) => <div>sale-complete:{p.orderId}</div>,
}));
vi.mock('../ProductResultsStrip', () => ({ default: () => null }));
vi.mock('../DeliveryOptionsRow', () => ({ default: () => null }));

import { BillingSurface } from '../BillingSurface';
import { usePOSStore } from '../../../../stores/posStore';

type Line = 'FRAME' | 'OPTICAL_LENS' | 'ACCESSORIES' | 'CONTACT_LENS';

/** A bill on the till: customer, optional Rx, and the given lines. */
function seed(opts: { rx?: boolean; lines: Line[] }) {
  const s = usePOSStore.getState();
  s.setCustomer({ id: 'c-1', name: 'Asha Verma', phone: '9876543210' } as never);
  if (opts.rx) s.setPrescription({ id: 'rx-1', rightEye: {}, leftEye: {} } as never);
  opts.lines.forEach((category, i) =>
    s.addToCart({
      id: `p-${i}`,
      product_id: `p-${i}`,
      name: category,
      category,
      quantity: 1,
      unit_price: 1000,
      price: 1000,
      mrp: 1000,
      tax_rate: 5,
    } as never),
  );
}

const completeSale = async () => {
  fireEvent.click(screen.getByRole('button', { name: /complete sale|saving/i }));
  await waitFor(() => expect(submitPosOrder).toHaveBeenCalledTimes(1));
  return submitPosOrder.mock.calls[0][0] as { sale_type: string; is_advance_payment: boolean };
};

beforeEach(() => {
  usePOSStore.getState().resetTransaction();
  submitPosOrder.mockReset().mockResolvedValue({ ok: true, orderId: 'o-1' });
});

describe('G1: the sale type the lab depends on is derived from the bill', () => {
  it('Rx + frame => prescription order (a workshop job will be created)', async () => {
    seed({ rx: true, lines: ['FRAME'] });
    render(<BillingSurface />);
    expect((await completeSale()).sale_type).toBe('prescription_order');
  });

  it('Rx + lens line => prescription order', async () => {
    seed({ rx: true, lines: ['OPTICAL_LENS'] });
    render(<BillingSurface />);
    expect((await completeSale()).sale_type).toBe('prescription_order');
  });

  it('frame with no Rx => quick sale', async () => {
    seed({ lines: ['FRAME'] });
    render(<BillingSurface />);
    expect((await completeSale()).sale_type).toBe('quick_sale');
  });

  it('Rx with only accessories or contact lenses => quick sale, never a dead end', async () => {
    // A prescription order without a lens/frame is REFUSED by the submit
    // brain ("requires at least one lens item") and this surface has no
    // switch to flip back -- so it must never be typed that way.
    seed({ rx: true, lines: ['ACCESSORIES', 'CONTACT_LENS'] });
    render(<BillingSurface />);
    expect((await completeSale()).sale_type).toBe('quick_sale');
  });
});

describe('G2: a deposit sale', () => {
  it('flags the order advance-only so a part payment is accepted and the balance waits for delivery', async () => {
    seed({ lines: ['FRAME'] });
    render(<BillingSurface />);
    const toggle = screen.getByRole('button', { name: /advance only/i });
    expect(toggle.getAttribute('aria-pressed')).toBe('false');

    fireEvent.click(toggle);
    expect(toggle.getAttribute('aria-pressed')).toBe('true');
    expect((await completeSale()).is_advance_payment).toBe(true);
  });

  it('is off by default -- a full-payment bill is unchanged', async () => {
    seed({ lines: ['FRAME'] });
    render(<BillingSurface />);
    expect((await completeSale()).is_advance_payment).toBe(false);
  });
});

describe('G3: a wrong customer pick can be cleared', () => {
  it('Change on the card clears the customer (and with it the member and Rx)', () => {
    seed({ rx: true, lines: [] });
    render(<BillingSurface />);
    fireEvent.click(screen.getByText('change-customer'));
    expect(usePOSStore.getState().customer).toBeNull();
    expect(usePOSStore.getState().prescription).toBeNull();
  });
});

// ============================================================================
// The till records the EXACT scanned unit on each sale line
// ============================================================================
// Owner ruling 2026-09-30 00:10 (POS ask 1, answered YES): a line built from a
// unit scan carries THAT unit's stock_id to the order, so the server marks the
// scanned frame SOLD -- never the first AVAILABLE unit of the product. A line
// added without a scan (typed / tapped product) keeps today's first-available
// behaviour. A scanned unit that is no longer AVAILABLE is refused clearly.
// Billing, prices, GST and the till layout do not change.
//
// The unit's stock_id used to be dropped at every hop (resolveBarcode, the cart
// line, submitPosOrder's payload list), so the server's explicit-unit path
// (orders/stock.py _mark_units_sold path 1) was never reached and every scanned
// frame was sold first-available.
//
// Finding ids, each fixed and pinned below (the server half, TSU-4..6 / TSC-*,
// is in backend/tests/test_till_records_scanned_unit.py):
//   TSU-1  productIntake: the scanned unit's stock_id never reached the cart line.
//   TSU-2  submitOrder: the order payload never sent a line's stock_id.
//   TSU-3  productIntake: a scanned unit that is SOLD / quarantined / transferred
//          was added to the cart like any other.

import { describe, it, expect, beforeEach, vi } from 'vitest';

vi.mock('../../../services/api', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    inventoryApi: { ...(actual.inventoryApi as object), searchByBarcode: vi.fn() },
    orderApi: {
      ...(actual.orderApi as object),
      createOrder: vi.fn(),
      addPayment: vi.fn(),
    },
    loyaltyApi: { ...(actual.loyaltyApi as object), redeem: vi.fn() },
    workshopApi: { ...(actual.workshopApi as object), createJob: vi.fn() },
  };
});

import { inventoryApi, orderApi } from '../../../services/api';
import { usePOSStore } from '../../../stores/posStore';
import { cartItemFromProduct, posPriceGuard, resolveBarcode } from '../productIntake';
import { submitPosOrder } from '../submitOrder';

const STORE = 'BV-BOK-01';
const PRODUCT = {
  product_id: 'FR-1',
  name: 'Carrera CA 8895 Havana 54',
  sku: 'FR-CARRERA-CA8895-807-54',
  brand: 'Carrera',
  category: 'FRAME',
  hsn_code: '900311',
  mrp: 10000,
  offer_price: 9000,
};
// GET /inventory/barcode/{code}: the stock_units row with the product joined.
const scanReply = (over: Record<string, unknown> = {}) => ({
  stock_id: 'SU-B',
  barcode: 'BVB00000002',
  product_id: 'FR-1',
  store_id: STORE,
  status: 'AVAILABLE',
  cross_store: false,
  product: { ...PRODUCT },
  ...over,
});

const mockedScan = vi.mocked(inventoryApi.searchByBarcode);
const mockedCreate = vi.mocked(orderApi.createOrder);

/** The surfaces' scan handler (BillingSurface / GeneralCounterSurface). */
async function scanIntoCart(reply: any) {
  mockedScan.mockResolvedValue(reply);
  const res = await resolveBarcode(STORE, reply.barcode);
  if (res.ok && res.product) {
    usePOSStore.getState().addToCart(cartItemFromProduct(res.product, posPriceGuard(res.product)));
  }
  return res;
}

/** The product strip's tap (ProductResultsStrip): no scan, no unit. */
function typeIntoCart(product: any) {
  usePOSStore.getState().addToCart(cartItemFromProduct(product, posPriceGuard(product)));
}

/** Pay in full and press Complete sale; returns the order payload's lines. */
async function completeSale(): Promise<any[]> {
  const total = usePOSStore.getState().getGrandTotal();
  usePOSStore.setState({ payments: [{ method: 'CASH', amount: total, timestamp: '' }] as any });
  const res = await submitPosOrder(usePOSStore.getState() as any, 'idem-till-unit');
  expect(res.ok).toBe(true);
  return (mockedCreate.mock.calls[0][0] as any).items;
}

describe('the till records the exact scanned unit', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedCreate.mockResolvedValue({ order_id: 'ord-1', order_number: 'BV-001' } as any);
    vi.mocked(orderApi.addPayment).mockResolvedValue({ payment_id: 'pmt-1' } as any);
    usePOSStore.setState({
      cart: [],
      customer: { id: 'cust-1', name: 'Asha' } as any,
      sale_type: 'quick_sale' as any,
      store_id: STORE,
      cart_discount_percent: 0,
      cart_discount_amount: 0,
      cash_tender: null,
      payments: [],
      pendingLoyaltyRedeem: null,
    } as any);
  });

  it('TSU-1: a unit scan puts THAT unit on the cart line', async () => {
    const res = await scanIntoCart(scanReply());
    expect(res.ok).toBe(true);
    const [line] = usePOSStore.getState().cart as any[];
    expect(line.stock_id).toBe('SU-B');
    expect(line.barcode).toBe('BVB00000002');
  });

  it('TSU-2: the order submit sends each line its stock_id', async () => {
    typeIntoCart(PRODUCT);
    const [typed] = usePOSStore.getState().cart;
    usePOSStore.setState({ cart: [{ ...typed, id: 'scanned', stock_id: 'SU-B' } as any] });
    const [sent] = await completeSale();
    expect(sent.stock_id).toBe('SU-B');
  });

  it('TSU-1+2: scan -> Complete sale sends the scanned unit; billing is the typed line', async () => {
    await scanIntoCart(scanReply());
    typeIntoCart(PRODUCT);
    const [scanned, typed] = await completeSale();
    expect(scanned.stock_id).toBe('SU-B');
    // A line added without a scan keeps first-available: no unit named.
    expect(typed.stock_id).toBeUndefined();
    // Billing, price and GST inputs are exactly the typed line's.
    const billing = (l: any) => {
      const b = { ...l };
      delete b.stock_id;
      delete b.barcode;
      return b;
    };
    expect(billing(scanned)).toEqual(billing(typed));
  });

  it('a typed line names no unit (first-available stays the server rule)', async () => {
    typeIntoCart(PRODUCT);
    const [sent] = await completeSale();
    expect(sent.stock_id).toBeUndefined();
    expect(sent).toMatchObject({ product_id: 'FR-1', unit_price: 9000, quantity: 1, item_type: 'FRAME' });
  });

  it('TSU-3: a scanned unit that is no longer AVAILABLE is refused at the scan', async () => {
    for (const status of ['SOLD', 'QUARANTINED', 'TRANSFERRED']) {
      usePOSStore.setState({ cart: [] });
      const res = await scanIntoCart(scanReply({ status }));
      expect(res.ok).toBe(false);
      expect(res.message).toMatch(/BVB00000002/);
      expect(res.message).toMatch(/not available/i);
      expect(usePOSStore.getState().cart).toHaveLength(0);
    }
  });

  it('an AVAILABLE unit in any letter case scans in (the server accepts all three)', async () => {
    for (const status of ['AVAILABLE', 'available', 'Available']) {
      usePOSStore.setState({ cart: [] });
      const res = await scanIntoCart(scanReply({ status }));
      expect(res.ok).toBe(true);
      expect(usePOSStore.getState().cart).toHaveLength(1);
    }
  });
});

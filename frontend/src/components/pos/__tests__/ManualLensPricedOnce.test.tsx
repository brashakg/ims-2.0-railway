// POS 3 (Fable review 2026-10-23): a made-to-order lens was billed at DOUBLE
// the price the cashier was shown. The Add Lens window showed a "Total (Pair)"
// and then put that PAIR figure on the bill as the price of ONE lens with
// quantity 2. It also added the base price once but coatings and add-ons twice,
// so even the shown total was wrong.
//
// Driven through the real till path: the real Add Lens window, the real
// addManualLensToCart builder, the real POS store and orderLinesOf (the exact
// lines order create and POST /orders/quote receive). The server bills
// unit_price x quantity, so the payload line must be per lens x 2.
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('../../../services/api', () => ({
  adminLensApi: {
    getLensBrands: vi.fn(async () => ({ brands: [{ id: 'b1', name: 'ZEISS', code: 'ZEI' }] })),
    getLensIndices: vi.fn(async () => ({
      indices: [{ id: 'i167', value: '1.67', name: 'High index', multiplier: 1.5 }],
    })),
    getLensCoatings: vi.fn(async () => ({
      coatings: [{ id: 'c1', name: 'DuraVision', code: 'DV', price: 500 }],
    })),
    getLensAddons: vi.fn(async () => ({
      addons: [{ id: 'a1', name: 'Blue cut', code: 'BC', price: 200 }],
    })),
  },
  orderApi: {},
}));

import { LensDetailsModal, addManualLensToCart } from '../LensDetailsModal';
import { usePOSStore } from '../../../stores/posStore';
import { orderLinesOf } from '../billQuote';

async function ringUpLens(category: 'Single Vision' | 'Progressive' | 'Bifocal', base = 1000) {
  render(
    <LensDetailsModal
      onClose={() => undefined}
      onSave={(d) => addManualLensToCart(usePOSStore.getState(), d)}
    />,
  );
  fireEvent.click(await screen.findByText('ZEISS'));
  fireEvent.click(screen.getByText(category));
  fireEvent.change(screen.getByPlaceholderText('0'), { target: { value: String(base) } });
  fireEvent.click(screen.getByText('Next'));
  fireEvent.click(screen.getByText('1.67'));
  fireEvent.click(screen.getByText('Next'));
  fireEvent.click(screen.getByText('DuraVision'));
  fireEvent.click(screen.getByText('Next'));
  fireEvent.click(screen.getByText('Blue cut'));
  fireEvent.click(screen.getByText('Next'));
  const shownPair = screen.getByText('Total (Pair)').nextElementSibling?.textContent;
  fireEvent.click(screen.getByText('Add to Order'));
  await waitFor(() => expect(usePOSStore.getState().cart.length).toBe(1));
  const line = usePOSStore.getState().cart[0];
  const [payload] = orderLinesOf(usePOSStore.getState().cart);
  return { shownPair, line, payload };
}

describe('a made-to-order lens is priced once at the till', () => {
  beforeEach(() => {
    usePOSStore.setState({ cart: [], cart_discount_percent: 0 });
  });

  it('progressive 1.67 + coating + add-on: per lens 3,700, pair 7,400 shown and billed', async () => {
    // Per lens: 1000 base x 1.5 index x 2 progressive = 3000, + 500 coating
    // + 200 add-on = 3700. A pair is two lenses: 7400.
    const { shownPair, line, payload } = await ringUpLens('Progressive');
    expect(shownPair).toBe('₹7,400');
    expect(line.unit_price).toBe(3700);
    expect(line.quantity).toBe(2);
    expect(line.line_total).toBe(7400);
    // What the server is sent (create + quote): per lens x 2 = the shown pair.
    expect(payload.unit_price * payload.quantity).toBe(7400);
    expect(payload.item_type).toBe('LENS');
  });

  it('single vision: the bill equals the pair total the cashier saw', async () => {
    // Per lens 1000 x 1.5 = 1500 + 500 + 200 = 2200; pair 4400.
    const { shownPair, line } = await ringUpLens('Single Vision');
    expect(shownPair).toBe('₹4,400');
    expect(line.unit_price).toBe(2200);
    expect(line.unit_price * line.quantity).toBe(4400);
  });

  it('bifocal 1.67 + coating + add-on: per lens 2,950, pair 5,900 shown and billed', async () => {
    // Per lens: 1000 x 1.5 index x 1.5 bifocal = 2250, + 500 + 200 = 2950.
    const { shownPair, line, payload } = await ringUpLens('Bifocal');
    expect(shownPair).toBe('₹5,900');
    expect(line.unit_price).toBe(2950);
    expect(line.quantity).toBe(2);
    expect(payload.unit_price * payload.quantity).toBe(5900);
  });

  it('bifocal with an odd base: shown pair = per-lens x 2 = what the server is sent', async () => {
    // 999 x 1.5 x 1.5 = 2247.75 + 700 = 2947.75 -> 2948 per lens, pair 5896.
    const { shownPair, line, payload } = await ringUpLens('Bifocal', 999);
    expect(line.unit_price).toBe(2948);
    expect(shownPair).toBe('₹5,896');
    expect(payload.unit_price * payload.quantity).toBe(line.unit_price * 2);
  });

  it('one eye only: quantity 1 bills one per-lens price, not half a pair', async () => {
    const { line } = await ringUpLens('Progressive');
    usePOSStore.getState().updateQuantity(line.id, 1);
    const [payload] = orderLinesOf(usePOSStore.getState().cart);
    expect(payload.unit_price).toBe(3700);
    expect(payload.quantity).toBe(1);
    expect(usePOSStore.getState().cart[0].line_total).toBe(3700);
  });

  it('a line discount comes off per-lens x 2, not off the pair billed twice', async () => {
    const { line } = await ringUpLens('Progressive');
    usePOSStore.getState().applyDiscount(line.id, 10, 'test');
    const after = usePOSStore.getState().cart[0];
    // 10% of 3700 x 2 = 740; line 6660.
    expect(after.discount_amount).toBe(740);
    expect(after.line_total).toBe(6660);
    const [payload] = orderLinesOf(usePOSStore.getState().cart);
    expect(payload.unit_price).toBe(3700);
    expect(payload.quantity).toBe(2);
    expect(payload.discount_percent).toBe(10);
  });
});

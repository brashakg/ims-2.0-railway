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

async function ringUpLens(category: 'Single Vision' | 'Progressive') {
  render(
    <LensDetailsModal
      onClose={() => undefined}
      onSave={(d) => addManualLensToCart(usePOSStore.getState(), d)}
    />,
  );
  fireEvent.click(await screen.findByText('ZEISS'));
  fireEvent.click(screen.getByText(category));
  fireEvent.change(screen.getByPlaceholderText('0'), { target: { value: '1000' } });
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
});

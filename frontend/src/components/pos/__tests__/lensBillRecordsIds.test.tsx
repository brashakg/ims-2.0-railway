// D44 (owner ruling 2026-11-13, PR #1429): every add-on ticked at the till is
// charged at its listed price AND the bill line records the lens, coating and
// add-on ids. Codex round 1 (2026-11-15): the pair was priced right (4400) but
// addManualLensToCart dropped brand / coating / add-on ids before the bill.
//
// Real path: server-shaped list rows (brand_id / index_id / coating_id /
// addon_id, an index label in `description`) through the real adminLensApi
// adapter, the real Add Lens window, builder, POS store and orderLinesOf (the
// lines order create and POST /orders/quote receive).
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const { lists } = vi.hoisted(() => ({
  lists: {
    '/admin/lens/brands': { brands: [{ brand_id: 'LB-1', name: 'D44 Lens', code: 'D44' }] },
    '/admin/lens/indices': { indices: [{ index_id: 'LI-1', value: '1.67', description: 'Thin', multiplier: 1.2 }] },
    '/admin/lens/coatings': { coatings: [{ coating_id: 'LC-1', name: 'D44 Coat', code: 'DC', price: 500 }] },
    '/admin/lens/addons': {
      addons: [
        { addon_id: 'LA-1', name: 'D44 Tint', code: 'T', price: 300, type: 'TINT' },
        { addon_id: 'LA-2', name: 'D44 Protect', code: 'P', price: 200, type: 'PROTECT' },
      ],
    },
  } as Record<string, unknown>,
}));
vi.mock('../../../services/api/client', async (original) => ({
  ...(await original<object>()),
  default: { get: vi.fn(async (url: string) => ({ data: lists[url] })) },
}));

import { LensDetailsModal, addManualLensToCart } from '../LensDetailsModal';
import { usePOSStore } from '../../../stores/posStore';
import { orderLinesOf } from '../billQuote';

describe('a till lens line records what was sold (D44)', () => {
  it('two add-ons: each charged once, and brand / index / coating / add-on ids reach the bill', async () => {
    usePOSStore.setState({ cart: [], cart_discount_percent: 0 });
    render(<LensDetailsModal onClose={() => undefined} onSave={(d) => addManualLensToCart(usePOSStore.getState(), d)} />);
    fireEvent.click(await screen.findByText('D44 Lens'));
    fireEvent.change(screen.getByPlaceholderText('0'), { target: { value: '1000' } });
    fireEvent.click(screen.getByText('Next'));
    fireEvent.click(screen.getByText('1.67'));
    fireEvent.click(screen.getByText('Next'));
    fireEvent.click(screen.getByText('D44 Coat'));
    fireEvent.click(screen.getByText('Next'));
    fireEvent.click(screen.getByText('D44 Tint'));
    fireEvent.click(screen.getByText('D44 Protect'));
    fireEvent.click(screen.getByText('Next'));
    expect(screen.getByText('Total (Pair)').nextElementSibling?.textContent).toBe('₹4,400');
    fireEvent.click(screen.getByText('Add to Order'));
    await waitFor(() => expect(usePOSStore.getState().cart.length).toBe(1));

    const [line] = orderLinesOf(usePOSStore.getState().cart);
    // Per lens 1000 x 1.2 + 500 + 300 + 200 = 2200; pair 4400, priced once.
    expect(line.unit_price).toBe(2200);
    expect(line.quantity).toBe(2);
    expect(line.lens_details).toMatchObject({
      index: 'LI-1',
      brand_id: 'LB-1',
      brand: 'D44 Lens',
      coating_id: 'LC-1',
      coatings: ['D44 Coat'],
      add_on_ids: ['LA-1', 'LA-2'],
      add_ons: ['D44 Tint', 'D44 Protect'],
    });

    // Recovery: a held bill is stored as JSON and recalled through
    // restoreHeldSale; the recalled line carries the same ids and price.
    const snapshot = JSON.parse(JSON.stringify({ cart: usePOSStore.getState().cart }));
    usePOSStore.setState({ cart: [] });
    expect(usePOSStore.getState().restoreHeldSale(snapshot, null, null, 'parked')).toBe(true);
    const [recalled] = orderLinesOf(usePOSStore.getState().cart);
    expect(recalled.unit_price * recalled.quantity).toBe(4400);
    expect(recalled.lens_details).toEqual(line.lens_details);
  });
});

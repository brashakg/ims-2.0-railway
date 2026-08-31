// Pair linking (owner spec 4): one bill can carry several Rx+frame PAIRS for
// the same customer, each with its own Rx, so a 4-pair family order stays
// untangled and the workshop gets one job per pair.
// Discriminating: each assert fails if setLinePair or the label allocator is
// reverted.
import { describe, it, expect, beforeEach } from 'vitest';
import { usePOSStore } from '../../../stores/posStore';
import { nextPairId } from '../POSCart';

const line = (id: string, pair?: string) => ({
  id,
  product_id: 'p-' + id,
  name: 'Frame ' + id,
  sku: 'SKU' + id,
  category: 'FRAME',
  unit_price: 1000,
  mrp: 1000,
  quantity: 1,
  is_optical: true,
  discount_percent: 0,
  discount_amount: 0,
  line_total: 1000,
  pair_id: pair,
}) as any;

describe('POS pair linking', () => {
  beforeEach(() => {
    usePOSStore.setState({ cart: [line('a'), line('b')] });
  });

  it('assigns a line to a pair and unlinks it again', () => {
    usePOSStore.getState().setLinePair('a', 'Pair 1');
    expect(usePOSStore.getState().cart.find((i: any) => i.id === 'a')?.pair_id).toBe('Pair 1');
    // Other lines are untouched.
    expect(usePOSStore.getState().cart.find((i: any) => i.id === 'b')?.pair_id).toBeUndefined();

    usePOSStore.getState().setLinePair('a', null);
    expect(usePOSStore.getState().cart.find((i: any) => i.id === 'a')?.pair_id).toBeUndefined();
  });

  it('allocates the next free label and reuses gaps', () => {
    expect(nextPairId([])).toBe('Pair 1');
    expect(nextPairId([line('x', 'Pair 1')])).toBe('Pair 2');
    // Pair 1 removed -> its label is free again (labels stay small).
    expect(nextPairId([line('x', 'Pair 2')])).toBe('Pair 1');
  });
});

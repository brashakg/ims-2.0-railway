// usePoGstHeads: the verdict belongs to the shop it was decided for.
// A late answer for shop A must never show for shop B, and the render right
// after a shop switch has no verdict at all (not A's, for a frame).

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';

const getPoGstHeads = vi.hoisted(() => vi.fn());
vi.mock('../../services/api/inventory', () => ({ vendorsApi: { getPoGstHeads } }));

import { usePoGstHeads } from '../usePoGstHeads';

type Deferred = { resolve: (v: { shop_gstin: string; heads: Record<string, boolean | null> }) => void };
const pending: Record<string, Deferred> = {};

beforeEach(() => {
  vi.clearAllMocks();
  for (const k of Object.keys(pending)) delete pending[k];
  getPoGstHeads.mockImplementation(
    (store?: string) =>
      new Promise((resolve) => {
        pending[store ?? ''] = { resolve };
      }),
  );
});

describe('usePoGstHeads', () => {
  it('asks the server for the shop, and again whenever the shop changes', async () => {
    const { rerender } = renderHook(({ s }) => usePoGstHeads(s), { initialProps: { s: 'A' } });
    expect(getPoGstHeads).toHaveBeenCalledTimes(1);
    expect(getPoGstHeads).toHaveBeenLastCalledWith('A');
    rerender({ s: 'B' });
    expect(getPoGstHeads).toHaveBeenCalledTimes(2);
    expect(getPoGstHeads).toHaveBeenLastCalledWith('B');
    rerender({ s: 'B' });
    expect(getPoGstHeads).toHaveBeenCalledTimes(2);
  });

  it('a late answer for the old shop never replaces the new shop\'s verdict', async () => {
    const { result, rerender } = renderHook(({ s }) => usePoGstHeads(s), { initialProps: { s: 'A' } });
    // A is still in flight when the shop is switched to B.
    rerender({ s: 'B' });
    await act(async () => pending.B.resolve({ shop_gstin: 'b', heads: { v1: false } }));
    await waitFor(() => expect(result.current).toEqual({ v1: false }));

    // A's request answers LAST (it was slow): it must be ignored.
    await act(async () => pending.A.resolve({ shop_gstin: 'a', heads: { v1: true } }));
    expect(result.current).toEqual({ v1: false });
  });

  it('never renders the previous shop\'s verdict after a switch, not even for one render', async () => {
    const seen: Array<[string, Record<string, boolean | null>]> = [];
    const { rerender } = renderHook(
      ({ s }) => {
        const h = usePoGstHeads(s);
        seen.push([s, h]);
        return h;
      },
      { initialProps: { s: 'A' } },
    );
    await act(async () => pending.A.resolve({ shop_gstin: 'a', heads: { v1: true } }));
    rerender({ s: 'B' });
    // Every render made for B, before B answers, carries no verdict.
    const forB = seen.filter(([s]) => s === 'B');
    expect(forB.length).toBeGreaterThan(0);
    for (const [, h] of forB) expect(h).toEqual({});
  });

  it('a failed request leaves the shop with no verdict', async () => {
    getPoGstHeads.mockImplementation(() => Promise.reject(new Error('down')));
    const { result } = renderHook(() => usePoGstHeads('A'));
    await waitFor(() => expect(getPoGstHeads).toHaveBeenCalled());
    expect(result.current).toEqual({});
  });
});

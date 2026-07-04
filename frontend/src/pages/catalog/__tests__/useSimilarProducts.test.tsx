// ============================================================================
// IMS 2.0 - useSimilarProducts tests (dup-detect Phase 2 trigger logic)
// ============================================================================
// Locks the council-exact trigger contract:
//   * ARM only when brand AND model (>= 2 chars) are both set (+ a category).
//   * Fire 400ms after the operator stops typing; every keystroke RESTARTS
//     the timer and ABORTS the in-flight request.
//   * data is null while idle/debouncing/fetching/errored/un-armed — the
//     strip renders nothing in all of those states.

import { renderHook, act } from '@testing-library/react';
import { vi, type Mock } from 'vitest';
import {
  useSimilarProducts,
  isSimilarQueryArmed,
  SIMILAR_DEBOUNCE_MS,
  type SimilarQueryInput,
} from '../useSimilarProducts';
import { productApi, type SimilarProductsResponse } from '../../../services/api/products';

vi.mock('../../../services/api/products', () => ({
  productApi: { getSimilarProducts: vi.fn() },
}));

const getSimilar = productApi.getSimilarProducts as unknown as Mock;

const RESPONSE: SimilarProductsResponse = {
  exact_match: null,
  siblings: [{ product_id: 'P-1', sku: 'S-1', colour_code: 'BLK' }],
  model_colour_count: 1,
};

const QUERY: SimilarQueryInput = {
  category: 'FR',
  brand: 'Ray-Ban',
  model: 'RB-2140',
  colour: '',
  size: '',
};

function renderSimilar(initial: SimilarQueryInput = QUERY) {
  return renderHook(({ q }: { q: SimilarQueryInput }) => useSimilarProducts(q), {
    initialProps: { q: initial },
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  getSimilar.mockResolvedValue(RESPONSE);
});

afterEach(() => {
  vi.useRealTimers();
});

describe('isSimilarQueryArmed (council arm rule)', () => {
  it('arms only when brand AND model (>= 2 chars) are both set', () => {
    expect(isSimilarQueryArmed({ category: 'FR', brand: 'Ray-Ban', model: 'RB' })).toBe(true);
    expect(isSimilarQueryArmed({ category: 'FR', brand: '', model: 'RB-2140' })).toBe(false);
    expect(isSimilarQueryArmed({ category: 'FR', brand: '  ', model: 'RB-2140' })).toBe(false);
    expect(isSimilarQueryArmed({ category: 'FR', brand: 'Ray-Ban', model: 'R' })).toBe(false);
    expect(isSimilarQueryArmed({ category: 'FR', brand: 'Ray-Ban', model: ' R ' })).toBe(false);
    expect(isSimilarQueryArmed({ category: '', brand: 'Ray-Ban', model: 'RB-2140' })).toBe(false);
  });
});

describe('useSimilarProducts', () => {
  it('does not fire before the 400ms debounce, fires once after it', async () => {
    const { result } = renderSimilar();
    expect(result.current.armed).toBe(true);
    expect(result.current.data).toBeNull();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(SIMILAR_DEBOUNCE_MS - 1);
    });
    expect(getSimilar).not.toHaveBeenCalled();
    expect(result.current.data).toBeNull(); // still "loading" -> render nothing

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(getSimilar).toHaveBeenCalledTimes(1);
    expect(getSimilar).toHaveBeenCalledWith(
      { category: 'FR', brand: 'Ray-Ban', model_no: 'RB-2140', colour_code: undefined, size: undefined },
      expect.any(AbortSignal)
    );
    expect(result.current.data).toEqual(RESPONSE);
  });

  it('never calls the API while un-armed', async () => {
    const { result } = renderSimilar({ ...QUERY, model: 'R' });
    expect(result.current.armed).toBe(false);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SIMILAR_DEBOUNCE_MS * 3);
    });
    expect(getSimilar).not.toHaveBeenCalled();
    expect(result.current.data).toBeNull();
  });

  it('every keystroke restarts the timer (single call with the LATEST values)', async () => {
    const { rerender } = renderSimilar();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SIMILAR_DEBOUNCE_MS - 100);
    });
    rerender({ q: { ...QUERY, colour: 'B' } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SIMILAR_DEBOUNCE_MS - 100);
    });
    rerender({ q: { ...QUERY, colour: 'BL' } });
    expect(getSimilar).not.toHaveBeenCalled(); // two restarts, never fired

    await act(async () => {
      await vi.advanceTimersByTimeAsync(SIMILAR_DEBOUNCE_MS);
    });
    expect(getSimilar).toHaveBeenCalledTimes(1);
    expect(getSimilar.mock.calls[0][0]).toMatchObject({ colour_code: 'BL' });
  });

  it('aborts the in-flight request when an input changes', async () => {
    let capturedSignal: AbortSignal | undefined;
    getSimilar.mockImplementation(
      (_params: unknown, signal?: AbortSignal) =>
        new Promise(() => {
          capturedSignal = signal; // never resolves — stays in flight
        })
    );
    const { result, rerender } = renderSimilar();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SIMILAR_DEBOUNCE_MS);
    });
    expect(getSimilar).toHaveBeenCalledTimes(1);
    expect(capturedSignal?.aborted).toBe(false);

    rerender({ q: { ...QUERY, colour: 'X' } }); // keystroke while in flight
    expect(capturedSignal?.aborted).toBe(true);
    expect(result.current.data).toBeNull();
  });

  it('clears data immediately on the next keystroke (no stale strip)', async () => {
    const { result, rerender } = renderSimilar();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SIMILAR_DEBOUNCE_MS);
    });
    expect(result.current.data).toEqual(RESPONSE);

    rerender({ q: { ...QUERY, colour: 'R' } });
    expect(result.current.data).toBeNull(); // cleared before the refetch lands
  });

  it('disarming (model shortened below 2 chars) clears data and stops firing', async () => {
    const { result, rerender } = renderSimilar();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SIMILAR_DEBOUNCE_MS);
    });
    expect(result.current.data).toEqual(RESPONSE);

    rerender({ q: { ...QUERY, model: 'R' } });
    expect(result.current.armed).toBe(false);
    expect(result.current.data).toBeNull();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SIMILAR_DEBOUNCE_MS * 2);
    });
    expect(getSimilar).toHaveBeenCalledTimes(1); // no second call
  });

  it('an API error yields null data (render nothing, never a crash)', async () => {
    getSimilar.mockRejectedValue(new Error('network down'));
    const { result } = renderSimilar();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SIMILAR_DEBOUNCE_MS);
    });
    expect(getSimilar).toHaveBeenCalledTimes(1);
    expect(result.current.data).toBeNull();
  });

  it('aborts the in-flight request on unmount', async () => {
    let capturedSignal: AbortSignal | undefined;
    getSimilar.mockImplementation(
      (_params: unknown, signal?: AbortSignal) =>
        new Promise(() => {
          capturedSignal = signal;
        })
    );
    const { unmount } = renderSimilar();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SIMILAR_DEBOUNCE_MS);
    });
    unmount();
    expect(capturedSignal?.aborted).toBe(true);
  });
});

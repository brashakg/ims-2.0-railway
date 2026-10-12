// ============================================================================
// IMS 2.0 - useSimilarProducts tests (dup-detect Phase 2 trigger logic)
// ============================================================================
// Locks the trigger contract:
//   * SEND the category and the form's attributes as typed; the server's one
//     identity rule decides what matters (no client list of identity keys).
//   * ARM once a category and a brand are set.
//   * Fire 400ms after the operator stops typing; every keystroke RESTARTS
//     the timer and ABORTS the in-flight request.
//   * The last answer stays until the fresh one lands (no form jump); null
//     while idle/errored/un-armed — the strip renders nothing then.

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
  attributes: { brand_name: 'Ray-Ban', model_no: 'RB-2140', colour_code: '' },
};
const withAttrs = (extra: Record<string, string>): SimilarQueryInput => ({
  ...QUERY,
  attributes: { ...QUERY.attributes, ...extra },
});

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

describe('isSimilarQueryArmed (arm rule)', () => {
  it('arms once a category and a brand are set', () => {
    expect(isSimilarQueryArmed(QUERY)).toBe(true);
    // An Optical Lens has no model: the server keys it on sub-brand, coating, index.
    expect(isSimilarQueryArmed({ category: 'LS', attributes: { brand_name: 'Essilor' } })).toBe(true);
    expect(isSimilarQueryArmed(withAttrs({ brand_name: '' }))).toBe(false);
    expect(isSimilarQueryArmed(withAttrs({ brand_name: '  ' }))).toBe(false);
    expect(isSimilarQueryArmed({ ...QUERY, category: '' })).toBe(false);
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
      { category: 'FR', brand_name: 'Ray-Ban', model_no: 'RB-2140' },
      expect.any(AbortSignal)
    );
    expect(result.current.data).toEqual(RESPONSE);
  });

  it('never calls the API while un-armed', async () => {
    const { result } = renderSimilar(withAttrs({ brand_name: '' }));
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
    rerender({ q: withAttrs({ colour_code: 'B' }) });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SIMILAR_DEBOUNCE_MS - 100);
    });
    rerender({ q: withAttrs({ colour_code: 'BL' }) });
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

    rerender({ q: withAttrs({ colour_code: 'X' }) }); // keystroke while in flight
    expect(capturedSignal?.aborted).toBe(true);
    expect(result.current.data).toBeNull();
  });

  it('keeps the last answer until the fresh one lands (the form never jumps)', async () => {
    const { result, rerender } = renderSimilar();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SIMILAR_DEBOUNCE_MS);
    });
    expect(result.current.data).toEqual(RESPONSE);

    const FRESH: SimilarProductsResponse = { exact_match: null, siblings: [], model_colour_count: 0 };
    getSimilar.mockResolvedValueOnce(FRESH);
    rerender({ q: withAttrs({ usp_1: 'Light' }) }); // typing in a field below the strip
    expect(result.current.data).toEqual(RESPONSE);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SIMILAR_DEBOUNCE_MS);
    });
    expect(result.current.data).toEqual(FRESH);
  });

  it('a new category clears the answer at once', async () => {
    const { result, rerender } = renderSimilar();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SIMILAR_DEBOUNCE_MS);
    });
    expect(result.current.data).toEqual(RESPONSE);
    rerender({ q: { ...QUERY, category: 'SG' } });
    expect(result.current.data).toBeNull();
  });

  it('disarming (brand cleared) clears data and stops firing', async () => {
    const { result, rerender } = renderSimilar();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SIMILAR_DEBOUNCE_MS);
    });
    expect(result.current.data).toEqual(RESPONSE);

    rerender({ q: withAttrs({ brand_name: '' }) });
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

  it("sends every typed attribute and keeps no list of the key's inputs", async () => {
    // The server keys the form with the create door's own identity_parts; a
    // client list of what it reads drifted (colour name, sub-brand, coating,
    // index, contact-lens fit). Blanks are dropped, values trimmed.
    const attributes = {
      brand_name: 'Acuvue', model_name: 'Oasys', colour_name: ' Hazel ', power: '-1.25D',
      base_curve: '8.6', diameter: '14.2', pack: '6', cl_axis: '', subbrand: 'Max',
    };
    const { rerender } = renderSimilar({ category: 'CCL', attributes });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SIMILAR_DEBOUNCE_MS);
    });
    expect(getSimilar).toHaveBeenCalledWith(
      { category: 'CCL', brand_name: 'Acuvue', model_name: 'Oasys', colour_name: 'Hazel',
        power: '-1.25D', base_curve: '8.6', diameter: '14.2', pack: '6', subbrand: 'Max' },
      expect.any(AbortSignal)
    );
    rerender({ q: { category: 'CCL', attributes: { ...attributes } } }); // same values, new object
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SIMILAR_DEBOUNCE_MS);
    });
    expect(getSimilar).toHaveBeenCalledTimes(1); // never refires
    rerender({ q: { category: 'CCL', attributes: { ...attributes, pack: '30' } } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SIMILAR_DEBOUNCE_MS);
    });
    expect(getSimilar).toHaveBeenCalledTimes(2);
    expect(getSimilar.mock.calls[1][0]).toMatchObject({ pack: '30' });
  });
});

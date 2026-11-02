// ============================================================================
// IMS 2.0 - productApi.createProduct maps the server's 409 code
// ============================================================================
// NEAR_DUPLICATE (the same model written differently) must reach the form as
// that code: it opens "Is this the same product?" with a "different product"
// answer. Anything else is the hard "This product already exists".

import { vi, beforeEach, describe, it, expect } from 'vitest';

vi.mock('../client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../client')>()),
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

import api from '../client';
import { productApi, DuplicateProductError } from '../products';

const mockPost = api.post as unknown as ReturnType<typeof vi.fn>;
const TWIN = { product_id: 'P-OLD', sku: 'SG-RAYBAN-RB4340-601/M3', model: 'RB4340' };

beforeEach(() => {
  vi.clearAllMocks();
});

async function rejected(detail: unknown): Promise<DuplicateProductError> {
  mockPost.mockResolvedValue({ status: 409, data: { detail } });
  const err = await productApi
    .createProduct({ category: 'SUNGLASS' } as Parameters<typeof productApi.createProduct>[0])
    .catch((e: unknown) => e);
  expect(err).toBeInstanceOf(DuplicateProductError);
  return err as DuplicateProductError;
}

describe('productApi.createProduct 409', () => {
  it('keeps code NEAR_DUPLICATE and the existing product', async () => {
    const err = await rejected({ code: 'NEAR_DUPLICATE', message: 'same?', existing: TWIN });
    expect(err.code).toBe('NEAR_DUPLICATE');
    expect(err.existing).toEqual(TWIN);
  });

  it('anything else is DUPLICATE_PRODUCT', async () => {
    expect((await rejected({ code: 'DUPLICATE_PRODUCT', existing: TWIN })).code).toBe('DUPLICATE_PRODUCT');
    expect((await rejected('A product with this identity already exists.')).code).toBe('DUPLICATE_PRODUCT');
  });
});

// purchaseRecon: the id rule on the recon read (getRecon) and write (upsertRecon).

import { vi, beforeEach, describe, it, expect } from 'vitest';

vi.mock('../client', () => ({ default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() } }));

import api from '../client';
import { purchaseReconApi } from '../purchaseRecon';

const mockGet = api.get as unknown as ReturnType<typeof vi.fn>;
const mockPost = api.post as unknown as ReturnType<typeof vi.fn>;

beforeEach(() => vi.clearAllMocks());

describe('purchaseReconApi.getRecon', () => {
  it('returns null and sends nothing without a usable id', async () => {
    for (const bad of ['', '  ', 'undefined', 'null', undefined, null]) {
      expect(await purchaseReconApi.getRecon(bad as unknown as string)).toBeNull();
    }
    expect(mockGet).not.toHaveBeenCalled();
  });

  it('trims and encodes a real id', async () => {
    mockGet.mockResolvedValue({ data: { ok: 1 } });
    await purchaseReconApi.getRecon(' a/b ');
    expect(mockGet).toHaveBeenCalledWith('/vendors/purchase-invoices/a%2Fb/recon');
  });
});

describe('purchaseReconApi.upsertRecon', () => {
  it('throws and sends nothing without a usable id', async () => {
    for (const bad of ['', '  ', 'undefined', 'null', undefined, null]) {
      await expect(purchaseReconApi.upsertRecon(bad as unknown as string, {})).rejects.toThrow(/no id/);
    }
    expect(mockPost).not.toHaveBeenCalled();
  });

  it('posts to the encoded id', async () => {
    mockPost.mockResolvedValue({ data: { ok: 1 } });
    await purchaseReconApi.upsertRecon('pi_1', {});
    expect(mockPost).toHaveBeenCalledWith('/vendors/purchase-invoices/pi_1/recon', {});
    await purchaseReconApi.upsertRecon('x?y', {});
    expect(mockPost).toHaveBeenLastCalledWith('/vendors/purchase-invoices/x%3Fy/recon', {});
  });
});

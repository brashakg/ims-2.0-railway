// PR #1424 Codex round 1: Settings > Users read ONE /users page (50 rows) and
// staff beyond it could not be found. getUsers now reads every page; a failed
// page rejects (never a silently short list).
import { vi, beforeEach, describe, it, expect } from 'vitest';

vi.mock('../client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

import api from '../client';
import { adminUserApi } from '../stores';

const mockGet = api.get as unknown as ReturnType<typeof vi.fn>;
const rows = (from: number, n: number) =>
  Array.from({ length: n }, (_, i) => ({ user_id: `u${from + i}` }));

beforeEach(() => vi.clearAllMocks());

describe('adminUserApi.getUsers', () => {
  it('reads every page until a short one', async () => {
    mockGet
      .mockResolvedValueOnce({ data: rows(0, 100) })
      .mockResolvedValueOnce({ data: rows(100, 30) });
    const all = await adminUserApi.getUsers({ activeOnly: false, storeId: 'S1' });
    expect(all).toHaveLength(130);
    expect(mockGet).toHaveBeenNthCalledWith(1, '/users', { params: { store_id: 'S1', active_only: false, skip: 0, limit: 100 } });
    expect(mockGet).toHaveBeenNthCalledWith(2, '/users', { params: { store_id: 'S1', active_only: false, skip: 100, limit: 100 } });
  });

  it('a failed later page rejects instead of returning the first page only', async () => {
    mockGet
      .mockResolvedValueOnce({ data: rows(0, 100) })
      .mockRejectedValueOnce(new Error('Network Error'));
    await expect(adminUserApi.getUsers()).rejects.toThrow('Network Error');
  });

  it('a row repeated across pages is listed once', async () => {
    mockGet
      .mockResolvedValueOnce({ data: rows(0, 100) })
      .mockResolvedValueOnce({ data: [{ user_id: 'u99' }, { user_id: 'u100' }] });
    const all = await adminUserApi.getUsers();
    expect(all.map((u) => u.user_id)).toHaveLength(101);
  });
});

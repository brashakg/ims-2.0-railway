// Owner report 2026-11-13: editing a user's username and email said "saved"
// but nothing changed, because adminUserApi.updateUser never sent either field.
import { vi, beforeEach, describe, it, expect } from 'vitest';

vi.mock('../client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

import api from '../client';
import { adminUserApi } from '../stores';

const mockPut = api.put as unknown as ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.clearAllMocks();
  mockPut.mockResolvedValue({ data: { user_id: 'u1' } });
});

describe('adminUserApi.updateUser login identity', () => {
  it('sends the edited username and email', async () => {
    await adminUserApi.updateUser('u1', { name: 'Ravi', username: ' ravi.k ', email: 'ravi.k@example.com' });
    expect(mockPut).toHaveBeenCalledWith('/users/u1', expect.objectContaining({
      username: 'ravi.k',
      email: 'ravi.k@example.com',
    }));
  });

  it('leaves a blank username or email out instead of sending an empty value', async () => {
    await adminUserApi.updateUser('u1', { name: 'Ravi', username: '', email: '  ' });
    const payload = mockPut.mock.calls[0][1];
    expect(payload).not.toHaveProperty('username');
    expect(payload).not.toHaveProperty('email');
  });
});

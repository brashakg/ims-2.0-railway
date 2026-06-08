// ============================================================================
// IMS 2.0 - approvals.ts client tests
// ============================================================================
// The load-bearing logic in approvals.ts is the error-shaping for approve/
// reject/consume: those calls use validateStatus:()=>true so the raw HTTP
// status + structured `detail` survive the global interceptor, and the client
// re-shapes them into a typed { ok, status, error, remaining, retry_after_min }
// result the PIN modal branches on. These tests lock that mapping.

import { vi, beforeEach, describe, it, expect } from 'vitest';

vi.mock('../client', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

import api from '../client';
import { approvalsApi } from '../approvals';

const mockPost = api.post as unknown as ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.clearAllMocks();
});

describe('approvalsApi.approve — success', () => {
  it('returns ok + token on a 200', async () => {
    mockPost.mockResolvedValue({
      status: 200,
      data: { ok: true, approval_token: 'APT-abc', reviewed_at: '2026-06-08T10:00:00Z' },
    });
    const res = await approvalsApi.approve('REQ-1', '1234');
    expect(res.ok).toBe(true);
    expect(res.approval_token).toBe('APT-abc');
    // approve must NOT throw on a non-2xx — it reads validateStatus responses.
    expect(mockPost).toHaveBeenCalledWith(
      '/approvals/requests/REQ-1/approve',
      { pin: '1234' },
      { validateStatus: expect.any(Function) },
    );
  });
});

describe('approvalsApi.approve — structured failures (object detail)', () => {
  it('maps 403 wrong_pin + remaining attempts', async () => {
    mockPost.mockResolvedValue({
      status: 403,
      data: { detail: { error: 'wrong_pin', remaining: 2 } },
    });
    const res = await approvalsApi.approve('REQ-1', '0000');
    expect(res.ok).toBe(false);
    expect(res.status).toBe(403);
    expect(res.error).toBe('wrong_pin');
    expect(res.remaining).toBe(2);
  });

  it('maps 423 pin_locked + retry_after_min', async () => {
    mockPost.mockResolvedValue({
      status: 423,
      data: { detail: { error: 'pin_locked', retry_after_min: 12 } },
    });
    const res = await approvalsApi.approve('REQ-1', '0000');
    expect(res.ok).toBe(false);
    expect(res.status).toBe(423);
    expect(res.error).toBe('pin_locked');
    expect(res.retry_after_min).toBe(12);
  });

  it('maps 409 already_reviewed with request status', async () => {
    mockPost.mockResolvedValue({
      status: 409,
      data: { detail: { error: 'already_reviewed', status: 'APPROVED' } },
    });
    const res = await approvalsApi.approve('REQ-1', '1234');
    expect(res.ok).toBe(false);
    expect(res.status).toBe(409);
    expect(res.error).toBe('already_reviewed');
    expect(res.request_status).toBe('APPROVED');
  });

  it('maps 410 expired', async () => {
    mockPost.mockResolvedValue({
      status: 410,
      data: { detail: { error: 'expired' } },
    });
    const res = await approvalsApi.approve('REQ-1', '1234');
    expect(res.ok).toBe(false);
    expect(res.status).toBe(410);
    expect(res.error).toBe('expired');
  });
});

describe('approvalsApi.reject', () => {
  it('sends pin + reason and returns ok on success', async () => {
    mockPost.mockResolvedValue({ status: 200, data: { ok: true } });
    const res = await approvalsApi.reject('REQ-9', '4321', 'duplicate');
    expect(res.ok).toBe(true);
    expect(mockPost).toHaveBeenCalledWith(
      '/approvals/requests/REQ-9/reject',
      { pin: '4321', reason: 'duplicate' },
      { validateStatus: expect.any(Function) },
    );
  });
});

describe('approvalsApi.consume', () => {
  it('maps a 409 already_consumed string detail into a typed error', async () => {
    mockPost.mockResolvedValue({
      status: 409,
      data: { detail: 'already_consumed' },
    });
    const res = await approvalsApi.consume('REQ-3', { action_type: 'refund' });
    expect(res.ok).toBe(false);
    expect(res.status).toBe(409);
    expect(res.error).toBe('already_consumed');
  });
});

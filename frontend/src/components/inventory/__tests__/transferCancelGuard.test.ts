// ============================================================================
// IMS 2.0 - fresh-state cancel guard tests (stale-tab race, PR #959 round 2)
// ============================================================================
// The P0 scenario these lock: a tab loads the list while a transfer is
// 'packed' (Cancel legitimately visible), another session ships + receives it,
// then the stale tab clicks Cancel. The guard must re-fetch the transfer and
// re-gate on the FRESH doc — and the cancel POST must NEVER fire when the
// server-side status is post-ship. Fail-closed: if the fresh state cannot be
// fetched at all, the POST is also never sent.

import { vi, describe, it, expect } from 'vitest';
import { cancelWithFreshCheck } from '../transferCancelGuard';
import type { TransferActor } from '../transferPermissions';

const admin: TransferActor = { roles: ['ADMIN'], storeIds: [], activeStoreId: '' };

const freshDoc = (status: string) => ({
  transfer: {
    id: 'trf_1',
    status,
    from_location_id: 'STORE-A',
    to_location_id: 'STORE-B',
  },
});

describe('cancelWithFreshCheck — stale-tab race (P0)', () => {
  it('client shows packed, server says received -> cancel POST never fires', async () => {
    // The stale client believed the transfer was 'packed'; the server has
    // moved on to 'received' (shipped + landed at destination).
    const getTransfer = vi.fn().mockResolvedValue(freshDoc('received'));
    const cancelTransfer = vi.fn();

    const result = await cancelWithFreshCheck({
      actor: admin,
      transferId: 'trf_1',
      reason: 'stale click',
      getTransfer,
      cancelTransfer,
    });

    expect(getTransfer).toHaveBeenCalledWith('trf_1');
    // The load-bearing assertion: the cancel POST was never sent.
    expect(cancelTransfer).not.toHaveBeenCalled();
    expect(result.ok).toBe(false);
    if (result.ok === false && result.reason === 'stale_not_cancellable') {
      expect(result.freshStatus).toBe('received');
    } else {
      throw new Error('expected stale_not_cancellable');
    }
  });

  it('never POSTs for any post-ship fresh status', async () => {
    for (const status of ['in_transit', 'partially_received', 'received', 'completed', 'cancelled']) {
      const cancelTransfer = vi.fn();
      const result = await cancelWithFreshCheck({
        actor: admin,
        transferId: 'trf_1',
        reason: 'stale click',
        getTransfer: vi.fn().mockResolvedValue(freshDoc(status)),
        cancelTransfer,
      });
      expect(cancelTransfer).not.toHaveBeenCalled();
      expect(result.ok).toBe(false);
    }
  });

  it('fail-closed: fetch failure means the POST is never sent', async () => {
    const cancelTransfer = vi.fn();
    const result = await cancelWithFreshCheck({
      actor: admin,
      transferId: 'trf_1',
      reason: 'network down',
      getTransfer: vi.fn().mockRejectedValue(new Error('network')),
      cancelTransfer,
    });
    expect(cancelTransfer).not.toHaveBeenCalled();
    expect(result).toEqual({ ok: false, reason: 'verify_failed' });
  });

  it('fail-closed: a malformed fresh doc (no status) blocks the POST', async () => {
    const cancelTransfer = vi.fn();
    const result = await cancelWithFreshCheck({
      actor: admin,
      transferId: 'trf_1',
      reason: 'weird payload',
      getTransfer: vi.fn().mockResolvedValue({ transfer: {} }),
      cancelTransfer,
    });
    expect(cancelTransfer).not.toHaveBeenCalled();
    expect(result).toEqual({ ok: false, reason: 'verify_failed' });
  });
});

describe('cancelWithFreshCheck — fresh pre-ship doc proceeds', () => {
  it('POSTs the cancel when the fresh status is still pre-ship', async () => {
    const cancelTransfer = vi.fn().mockResolvedValue({ transfer: { status: 'cancelled' } });
    const result = await cancelWithFreshCheck({
      actor: admin,
      transferId: 'trf_1',
      reason: 'no longer needed',
      getTransfer: vi.fn().mockResolvedValue(freshDoc('packed')),
      cancelTransfer,
    });
    expect(cancelTransfer).toHaveBeenCalledTimes(1);
    expect(cancelTransfer).toHaveBeenCalledWith('trf_1', 'no longer needed');
    expect(result.ok).toBe(true);
  });

  it('tolerates a bare doc (no {transfer} envelope)', async () => {
    const cancelTransfer = vi.fn().mockResolvedValue({});
    const result = await cancelWithFreshCheck({
      actor: admin,
      transferId: 'trf_1',
      reason: 'ok',
      getTransfer: vi.fn().mockResolvedValue({
        id: 'trf_1',
        status: 'approved',
        from_location_id: 'STORE-A',
        to_location_id: 'STORE-B',
      }),
      cancelTransfer,
    });
    expect(cancelTransfer).toHaveBeenCalledTimes(1);
    expect(result.ok).toBe(true);
  });
});

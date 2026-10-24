import { describe, expect, it } from 'vitest';
import { pushToastLevel } from '../OnlineStoreSyncBanner';
import type { PushResult } from '../../../services/api/onlineStore';

const pushed: PushResult = { mode: 'LIVE', entity: 'product', action: 'update', ok: true };

describe('photo push severity', () => {
  it('keeps the first incomplete pass amber', () => {
    expect(pushToastLevel({ ...pushed, code: 'PHOTOS_NOT_SYNCED', photos: { sync_state: 'pending' } })).toBe('warning');
  });

  it.each(['PHOTOS_SYNC_FAILED', 'PRICE_NOT_SYNCED'])('makes repeated photo failure red alongside %s', (code) => {
    expect(pushToastLevel({ ...pushed, code, photos: { sync_state: 'failed', consecutive_incomplete: 2 } })).toBe('error');
  });

  it('resets to success when the photo pass is complete', () => {
    expect(pushToastLevel({ ...pushed, photos: { sync_state: 'synced', consecutive_incomplete: 0 } })).toBe('success');
  });
});

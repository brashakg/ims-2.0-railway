// APP-01: the AppLayout "pick a store first" guard. An empty install has no
// store to pick, so SUPERADMIN / ADMIN must reach /organization (where the
// first store is created) without one; every other screen and every other
// role stays guarded. Revert mustPickStoreFirst to the old rule
// (multi-store-capable && no active store) and the exemption case goes red.
import { describe, it, expect } from 'vitest';
import type { User } from '../../types';
import { mustPickStoreFirst } from '../storeAccess';

const u = (over: Partial<User>): User =>
  ({ id: 'u1', name: 'X', roles: ['SUPERADMIN'], activeRole: 'SUPERADMIN', storeIds: [], activeStoreId: '', ...over }) as User;

describe('mustPickStoreFirst', () => {
  it('lets a storeless SUPERADMIN / ADMIN open Organization', () => {
    expect(mustPickStoreFirst(u({}), '/organization')).toBe(false);
    expect(mustPickStoreFirst(u({}), '/organization/')).toBe(false);
    expect(mustPickStoreFirst(u({ roles: ['ADMIN'], activeRole: 'ADMIN' }), '/organization')).toBe(false);
  });

  it('still sends a storeless SUPERADMIN to the picker from every store-scoped screen', () => {
    for (const p of ['/dashboard', '/pos/new', '/inventory', '/settings', '/organization-x']) {
      expect(mustPickStoreFirst(u({}), p)).toBe(true);
    }
  });

  it('an AREA_MANAGER with two shops and none chosen is guarded even on Organization', () => {
    const am = u({ roles: ['AREA_MANAGER'], activeRole: 'AREA_MANAGER', storeIds: ['A', 'B'] });
    expect(mustPickStoreFirst(am, '/organization')).toBe(true);
  });

  it('never touches a user with an active store or a single-store user', () => {
    expect(mustPickStoreFirst(u({ activeStoreId: 'BV-BOK-02' }), '/dashboard')).toBe(false);
    const staff = u({ roles: ['SALES_STAFF'], activeRole: 'SALES_STAFF', storeIds: ['A'] });
    expect(mustPickStoreFirst(staff, '/dashboard')).toBe(false);
    expect(mustPickStoreFirst(null, '/dashboard')).toBe(false);
  });
});

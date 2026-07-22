import { describe, it, expect } from 'vitest';
import { isOnlineStore, isOnlineStoreId, ONLINE_STORE_IDS } from '../storeMode';

describe('inventory storeMode', () => {
  it('classifies an ONLINE store_type as online (any casing)', () => {
    expect(isOnlineStore({ store_type: 'ONLINE' })).toBe(true);
    expect(isOnlineStore({ store_type: 'online' })).toBe(true);
    expect(isOnlineStore({ store_type: ' Online ' })).toBe(true);
  });

  it('classifies physical store types as NOT online', () => {
    for (const t of ['RETAIL', 'HQ', 'WAREHOUSE', '', undefined, null]) {
      expect(isOnlineStore({ store_type: t as string, id: 'BV-GANGA-01' })).toBe(false);
    }
  });

  it('falls back to the known online store ids when store_type is missing', () => {
    expect(isOnlineStore({ id: 'BV-ONLINE-01' })).toBe(true);
    expect(isOnlineStore({ store_id: 'WO-ONLINE-01' })).toBe(true);
    expect(isOnlineStore({ id: 'BV-RANCHI-01' })).toBe(false);
  });

  it('returns false for null / undefined / empty', () => {
    expect(isOnlineStore(null)).toBe(false);
    expect(isOnlineStore(undefined)).toBe(false);
    expect(isOnlineStore({})).toBe(false);
  });

  it('isOnlineStoreId matches only the known online ids', () => {
    expect(isOnlineStoreId('BV-ONLINE-01')).toBe(true);
    expect(isOnlineStoreId('WO-ONLINE-01')).toBe(true);
    expect(isOnlineStoreId('BV-GANGA-01')).toBe(false);
    expect(isOnlineStoreId('')).toBe(false);
    expect(isOnlineStoreId(null)).toBe(false);
  });

  it('exposes exactly the two current online store ids', () => {
    expect([...ONLINE_STORE_IDS].sort()).toEqual(['BV-ONLINE-01', 'WO-ONLINE-01']);
  });
});

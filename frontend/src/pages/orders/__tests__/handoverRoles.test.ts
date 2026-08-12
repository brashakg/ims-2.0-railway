/**
 * The Orders "Mark Delivered" button must never be offered to a role the
 * backend's HANDOVER_ROLES will refuse. It previously rendered fully enabled for
 * OPTOMETRIST and 403'd in front of the customer.
 */
import { describe, it, expect } from 'vitest';

import { HANDOVER_ROLES, canCloseHandover } from '../handoverRoles';

describe('canCloseHandover', () => {
  it('allows every counter and scan role', () => {
    for (const role of [
      'SUPERADMIN',
      'ADMIN',
      'AREA_MANAGER',
      'STORE_MANAGER',
      'SALES_CASHIER',
      'SALES_STAFF',
      'CASHIER',
      'WORKSHOP_STAFF',
    ]) {
      expect(canCloseHandover({ roles: [role] })).toBe(true);
    }
  });

  it('refuses OPTOMETRIST, which the backend 403s', () => {
    expect(canCloseHandover({ roles: ['OPTOMETRIST'] })).toBe(false);
    expect(canCloseHandover({ activeRole: 'OPTOMETRIST' })).toBe(false);
  });

  it('refuses other out-of-scope roles', () => {
    for (const role of ['ACCOUNTANT', 'CATALOG_MANAGER', 'INVESTOR']) {
      expect(canCloseHandover({ roles: [role] })).toBe(false);
    }
  });

  it('reads activeRole as well as the full role set', () => {
    expect(canCloseHandover({ activeRole: 'CASHIER' })).toBe(true);
    // A dual-role user must not be wrongly blocked by activeRole alone.
    expect(
      canCloseHandover({ roles: ['OPTOMETRIST', 'STORE_MANAGER'], activeRole: 'OPTOMETRIST' }),
    ).toBe(true);
  });

  it('refuses an absent or empty user', () => {
    expect(canCloseHandover(null)).toBe(false);
    expect(canCloseHandover(undefined)).toBe(false);
    expect(canCloseHandover({ roles: [] })).toBe(false);
  });

  it('mirrors the backend tuple exactly (update BOTH or neither)', () => {
    // backend/api/routers/orders.py HANDOVER_ROLES
    expect([...HANDOVER_ROLES].sort()).toEqual(
      [
        'ADMIN',
        'AREA_MANAGER',
        'CASHIER',
        'SALES_CASHIER',
        'SALES_STAFF',
        'STORE_MANAGER',
        'SUPERADMIN',
        'WORKSHOP_STAFF',
      ].sort(),
    );
  });
});

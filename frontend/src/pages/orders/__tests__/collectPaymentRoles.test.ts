// Round-2 review of #1264: the server's payment door refuses WORKSHOP_STAFF
// (and catalogue / design); the Orders screen must not offer Collect Payment.
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, it, expect } from 'vitest';
import { canTakePayment } from '../handoverRoles';

const ORDERS_PAGE = join(dirname(fileURLToPath(import.meta.url)), '..', 'OrdersPage.tsx');

describe('canTakePayment mirrors the server payment roles', () => {
  it('offers it to the till roles and accounts', () => {
    for (const r of ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'SALES_CASHIER',
      'SALES_STAFF', 'CASHIER', 'OPTOMETRIST', 'ACCOUNTANT']) {
      expect(canTakePayment({ roles: [r] })).toBe(true);
    }
  });
  it('hides it from workshop, catalogue and design staff', () => {
    for (const r of ['WORKSHOP_STAFF', 'CATALOG_MANAGER', 'DESIGN_MANAGER']) {
      expect(canTakePayment({ roles: [r] })).toBe(false);
    }
  });
});

describe('the Orders screen', () => {
  it('gates both Collect Payment buttons on canTakePayment', () => {
    const src = readFileSync(ORDERS_PAGE, 'utf8');
    expect((src.match(/canTakePayment\(user\)/g) || []).length).toBe(2);
  });
});

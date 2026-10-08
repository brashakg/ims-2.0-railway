// Owner ruling 2026-09-28: RECEIVING IS MANAGERS ONLY. The backend half is
// one tuple (services/cost_mask.py RECEIVE_ROLES, which vendors/_shared.py
// _RECEIVE_ROLES is, pinned by
// test_c7_receiving_is_the_managers); this pins the frontend half to it, so
// re-adding ACCOUNTANT to RECEIVING_MANAGER_ROLES -- which would hand the
// accountant the Receive Goods menu item, the /purchase/grn and
// /purchase/receive routes and every Receive button, each answering 403 --
// fails here by name. (The drawer's Receive step is pinned for ACCOUNTANT in
// POLifecycleDrawer.test.tsx.)
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { RECEIVING_MANAGER_ROLES } from '../purchaseTypes';
import { NAV_GROUPS } from '../../../components/shell/navConfig';

const here = path.dirname(fileURLToPath(import.meta.url));
const src = (rel: string) => readFileSync(path.resolve(here, rel), 'utf8');

describe('receiving is the managers only, on both sides', () => {
  it('RECEIVING_MANAGER_ROLES is the backend RECEIVE_ROLES plus SUPERADMIN', () => {
    const backend = src('../../../../../backend/api/services/cost_mask.py');
    const m = backend.match(/^RECEIVE_ROLES = \(([^)]*)\)/m);
    expect(m, 'the RECEIVE_ROLES tuple is no longer where this test reads it').not.toBeNull();
    const roles = [...m![1].matchAll(/"([A-Z_]+)"/g)].map((r) => r[1]);
    expect(roles.length).toBeGreaterThan(0);
    expect([...RECEIVING_MANAGER_ROLES].sort()).toEqual(['SUPERADMIN', ...roles].sort());
    expect(RECEIVING_MANAGER_ROLES).not.toContain('ACCOUNTANT');
  });

  it('the Receive Goods menu item opens to exactly those roles', () => {
    const item = NAV_GROUPS.flatMap((g) => g.items).find((i) => i.to === '/purchase/receive');
    expect(item?.requireRoles).toEqual([...RECEIVING_MANAGER_ROLES]);
  });

  it('both receiving routes and the PO table Receive button gate on that one list', () => {
    const routes = src('../../../routes/purchaseRoutes.tsx');
    for (const route of ['purchase/grn', 'purchase/receive']) {
      const block = routes.match(new RegExp(`path="${route}"[\\s\\S]*?allowedRoles=\\{([^}]*)\\}`));
      expect(block?.[1], route).toBe('[...RECEIVING_MANAGER_ROLES]');
    }
    expect(src('../PurchaseTable.tsx')).toMatch(/canReceive = hasRole\(\[\.\.\.RECEIVING_MANAGER_ROLES\]\)/);
  });
});

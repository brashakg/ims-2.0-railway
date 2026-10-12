// ============================================================================
// A parent breadcrumb is a link only when this user can open it
// ============================================================================
// Round 10 on #1180: counter staff on /inventory/lookup got an 'Inventory'
// crumb button that landed on /unauthorized (the /inventory module gate
// leaves them out); OPTOMETRIST on /inventory/power-grid likewise. The crumb
// now asks the router's own gates (the real route tree + ProtectedRoute's
// gateAllows), so these fail if it goes back to linking every parent, or if
// it stops asking the module gate.

import { describe, it, expect } from 'vitest';
import { canOpenPath, pathToCrumbs } from '../AppLayout';
import type { UserRole } from '../../../types';

function crumbsFor(role: UserRole, path: string, deniedModules: string[] = []) {
  const auth = {
    hasRole: (r: UserRole | UserRole[]) => [r].flat().includes(role),
    hasPermission: () => true,
    hasModuleAccess: (m: string) => !deniedModules.includes(m),
  };
  return pathToCrumbs(path, (p) => canOpenPath(auth, p));
}

describe('parent breadcrumbs follow the route gates', () => {
  it.each(['SALES_STAFF', 'CASHIER', 'OPTOMETRIST'] as UserRole[])(
    '%s on /inventory/lookup sees Inventory as text',
    (role) => {
      expect(crumbsFor(role, '/inventory/lookup')).toEqual([
        { label: 'Inventory', to: undefined },
        { label: 'Lookup', to: undefined },
      ]);
    },
  );

  it('ADMIN on /inventory/lookup gets Inventory as a link', () => {
    expect(crumbsFor('ADMIN', '/inventory/lookup')[0]).toEqual({ label: 'Inventory', to: '/inventory' });
  });

  it('OPTOMETRIST on /inventory/power-grid sees Inventory as text, ADMIN a link', () => {
    expect(crumbsFor('OPTOMETRIST', '/inventory/power-grid')[0].to).toBeUndefined();
    expect(crumbsFor('ADMIN', '/inventory/power-grid')[0].to).toBe('/inventory');
  });

  it('a parent the role CAN open stays a link (SALES_STAFF -> Customers)', () => {
    expect(crumbsFor('SALES_STAFF', '/customers/360')[0]).toEqual({ label: 'Customers', to: '/customers' });
  });

  it('a module denied to this user is text even for ADMIN', () => {
    expect(crumbsFor('ADMIN', '/inventory/power-grid', ['inventory'])[0].to).toBeUndefined();
  });

  it('a parent with no screen is text', () => {
    expect(crumbsFor('ADMIN', '/no-such-screen/child')[0].to).toBeUndefined();
  });
});

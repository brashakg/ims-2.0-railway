// ============================================================================
// IMS 2.0 - A layout whose sections share its list still names it
// ============================================================================
// Review round 7 (second pass): the first fix stopped EVERY layout gate naming
// its roles, so ~40 pages under /inventory, /reports ... lost a "Who can" list
// that was exactly right. Only layouts whose sections are narrower opt out
// (namesWhoCan={false}); /inventory is not one of them.

import { Suspense } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    isAuthenticated: true,
    isLoading: false,
    hasRole: (roles?: string[]) => !roles || roles.includes('SALES_STAFF'),
    hasPermission: () => true,
    hasModuleAccess: () => true,
  }),
}));

import { UnauthorizedPage } from '../UnauthorizedPage';
import { inventoryRoutes } from '../../../routes/inventoryRoutes';
import { INVENTORY_MODULE_ROLES } from '../../../pages/inventory/inventoryRoles';

function label(role: string): string {
  const words = role.toLowerCase().replace(/_/g, ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
}

describe('403 under a layout whose sections share its roles', () => {
  it.each(['/inventory/stock', '/inventory'])('%s names the inventory roles', async (path) => {
    render(
      <MemoryRouter initialEntries={[path]}>
        <Suspense fallback={<div>loading</div>}>
          <Routes>
            {inventoryRoutes}
            <Route path="/unauthorized" element={<UnauthorizedPage />} />
          </Routes>
        </Suspense>
      </MemoryRouter>,
    );
    const want = INVENTORY_MODULE_ROLES.filter((r) => r !== 'SUPERADMIN').map(label).join(', ');
    expect(await screen.findByText(/^Who can:/)).toHaveTextContent(`Who can: ${want}.`);
  });
});

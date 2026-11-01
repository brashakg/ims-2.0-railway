// ============================================================================
// Ctrl-K still finds Online Stock and Power Grid after they left the menu
// ============================================================================
// The palette derives pages from the menu, so the two rows removed on
// 2026-11-01 come back through EXTRA_JUMPS, gated like their routes.

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeAll } from 'vitest';

let roles: string[] = ['SUPERADMIN'];

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u1', name: 'Tester', roles, activeRole: roles[0], activeStoreId: 'BV-DHN-01', storeIds: ['BV-DHN-01'] },
    hasModuleAccess: () => true,
  }),
}));
vi.mock('../../../services/api/customers', () => ({
  customerApi: { searchByPhone: vi.fn().mockResolvedValue([]), getCustomers: vi.fn().mockResolvedValue([]) },
}));
vi.mock('../../../services/api/sales', () => ({ orderApi: { getOrders: vi.fn().mockResolvedValue([]) } }));
vi.mock('../../../services/api/products', () => ({
  productApi: { getProducts: vi.fn().mockResolvedValue([]), searchProducts: vi.fn().mockResolvedValue([]) },
}));

import { CommandPalette } from '../CommandPalette';

beforeAll(() => {
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
  Element.prototype.scrollIntoView ??= () => {};
});

async function searchPages(role: string, query: string) {
  roles = [role];
  render(
    <MemoryRouter>
      <CommandPalette open onOpenChange={vi.fn()} />
    </MemoryRouter>,
  );
  await userEvent.setup().type(await screen.findByRole('combobox'), query);
}

describe('CommandPalette - pages moved off the menu', () => {
  it.each(['SUPERADMIN', 'STORE_MANAGER', 'CATALOG_MANAGER'])('%s finds Online Stock', async (role) => {
    await searchPages(role, 'online stock');
    expect(await screen.findByText('/inventory/online-sync', { exact: false })).toBeInTheDocument();
  });

  it.each(['WORKSHOP_STAFF', 'CASHIER', 'OPTOMETRIST'])('%s does not find Online Stock', async (role) => {
    await searchPages(role, 'online stock');
    await screen.findByText('Jump to page');
    expect(screen.queryByText('/inventory/online-sync', { exact: false })).not.toBeInTheDocument();
  });

  it.each(['SUPERADMIN', 'AREA_MANAGER', 'OPTOMETRIST'])('%s finds Power Grid', async (role) => {
    await searchPages(role, 'power grid');
    expect(await screen.findByText('/inventory/power-grid', { exact: false })).toBeInTheDocument();
  });

  it.each(['CASHIER', 'WORKSHOP_STAFF', 'ACCOUNTANT'])('%s does not find Power Grid', async (role) => {
    await searchPages(role, 'power grid');
    await screen.findByText('Jump to page');
    expect(screen.queryByText('/inventory/power-grid', { exact: false })).not.toBeInTheDocument();
  });
});

// ============================================================================
// Stock & supply menu in two sections (owner Option A, MCQ 2026-11-01)
// ============================================================================
// Power Grid and Online Stock left the menu; Opening Stock stays (owner) and
// also sits in Inventory > Operations. These pin (1) each role's rows, (2) a
// door to every moved screen for every role its route admits, and (3) the
// section headings: shown on a long list only, never a menuitem.

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi } from 'vitest';
import type { UserRole } from '../../../types';

let roles: UserRole[] = ['SUPERADMIN'];
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u1', name: 'Tester', roles, activeRole: roles[0], activeStoreId: 'BV-BOK-01', storeIds: ['BV-BOK-01'] },
    hasModuleAccess: () => true,
    logout: vi.fn(),
  }),
}));

import { TopNav } from '../TopNav';
import { filterVisibleGroups } from '../navConfig';
import { INVENTORY_MANAGE_ROLES, POWER_GRID_ROLES, canManageInventory } from '../../../pages/inventory/inventoryRoles';

const stockRows = (role: UserRole) =>
  filterVisibleGroups([role], role, () => true).find((g) => g.title === 'Stock & supply')?.items.map((i) => i.id) ?? [];

const STOCK = ['inventory', 'stock-lookup', 'stock-count', 'reorder', 'opening-stock', 'purchase', 'grn-cockpit'];
const PRODUCTS_ALL = ['catalog', 'catalog-review', 'catalog-missing-photos', 'catalog-scorecard', 'pricing', 'collections', 'quick-share'];

// Before -> after (Power Grid / Online Stock rows gone): SA/AD 17 -> 15,
// SM 14 -> 12, CM 13 -> 11, AM 12 -> 10, OPTOMETRIST 3 -> 2, others unchanged.
const EXPECTED: Record<string, string[]> = {
  SUPERADMIN: [...STOCK, 'workshop', ...PRODUCTS_ALL],
  ADMIN: [...STOCK, 'workshop', ...PRODUCTS_ALL],
  STORE_MANAGER: [...STOCK, 'workshop', 'catalog', 'catalog-scorecard', 'collections', 'quick-share'],
  CATALOG_MANAGER: ['inventory', 'stock-count', 'reorder', 'opening-stock', ...PRODUCTS_ALL],
  AREA_MANAGER: [...STOCK, 'catalog-scorecard', 'collections', 'quick-share'],
  WORKSHOP_STAFF: ['inventory', 'vendor-returns', 'workshop'],
  OPTOMETRIST: ['stock-lookup', 'quick-share'],
  CASHIER: ['stock-lookup', 'quick-share'],
  SALES_STAFF: ['stock-lookup', 'quick-share'],
  ACCOUNTANT: ['reorder', 'purchase'],
};

describe('Stock & supply rows per role', () => {
  it.each(Object.entries(EXPECTED))('%s keeps exactly its rows', (role, ids) => {
    expect(stockRows(role as UserRole)).toEqual(ids);
  });

  it('no role sees a Power Grid or Online Stock row', () => {
    for (const role of Object.keys(EXPECTED)) {
      expect(stockRows(role as UserRole)).not.toContain('power-grid');
      expect(stockRows(role as UserRole)).not.toContain('online-stock');
    }
  });
});

describe('every role its route admits still has a door to the moved screens', () => {
  it.each(INVENTORY_MANAGE_ROLES)('%s: Online stock via Inventory > Operations', (role) => {
    expect(stockRows(role)).toContain('inventory');
    expect(canManageInventory([role])).toBe(true);
  });

  it.each(POWER_GRID_ROLES)('%s: Power Grid via Inventory > Optical or the Stock Lookup header', (role) => {
    const rows = stockRows(role);
    const viaInventory = rows.includes('inventory') && canManageInventory([role]);
    const viaLookup = rows.includes('stock-lookup'); // header link shows to POWER_GRID_ROLES
    expect(viaInventory || viaLookup).toBe(true);
  });
});

function openStockMenu(role: UserRole) {
  roles = [role];
  render(
    <MemoryRouter>
      <TopNav brand="bv" />
    </MemoryRouter>,
  );
  return userEvent.setup().click(screen.getByRole('button', { name: /Stock & supply/i }));
}

describe('TopNav section headings', () => {
  it('SUPERADMIN sees both headings, and they are not menuitems', async () => {
    await openStockMenu('SUPERADMIN');
    const menu = screen.getByRole('menu', { name: /Stock & supply/i });
    const headings = menu.querySelectorAll('.top-nav-dd-heading');
    expect([...headings].map((h) => h.textContent)).toEqual(['Stock and buying', 'Products']);
    headings.forEach((h) => {
      expect(h).toHaveAttribute('aria-hidden', 'true');
      expect(h.getAttribute('role')).toBeNull();
    });
    expect(screen.queryByRole('menuitem', { name: /Stock and buying|^Products$/ })).not.toBeInTheDocument();
    expect(screen.getAllByRole('menuitem')).toHaveLength(15);
  });

  it('CASHIER (2 rows) sees no headings', async () => {
    await openStockMenu('CASHIER');
    const menu = screen.getByRole('menu', { name: /Stock & supply/i });
    expect(menu.querySelectorAll('.top-nav-dd-heading')).toHaveLength(0);
    expect(screen.getAllByRole('menuitem')).toHaveLength(2);
  });
});

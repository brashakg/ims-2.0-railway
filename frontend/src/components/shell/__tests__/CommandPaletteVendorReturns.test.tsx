// ============================================================================
// Ctrl-K: the accountant can jump to Vendor Returns
// ============================================================================
// Owner ruling 2026-10-01: supplier money -- including the supplier credit and
// the GST debit notes on vendor returns -- is for ADMIN / SUPERADMIN /
// ACCOUNTANT. /purchase/vendor-returns now admits ACCOUNTANT, and the command
// palette's EXTRA_JUMPS row MIRRORS that route gate (see CommandPalette.tsx),
// so the accountant is offered the jump too. Counter staff still are not.

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeAll } from 'vitest';

let roles: string[] = ['ACCOUNTANT'];

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u1', name: 'Tester', roles, activeRole: roles[0], activeStoreId: 'BV-DHN-01', storeIds: ['BV-DHN-01'] },
    hasModuleAccess: () => true,
  }),
}));
// The palette also searches customers / orders / products on every query.
vi.mock('../../../services/api/customers', () => ({
  customerApi: { searchByPhone: vi.fn().mockResolvedValue([]), getCustomers: vi.fn().mockResolvedValue([]) },
}));
vi.mock('../../../services/api/sales', () => ({ orderApi: { getOrders: vi.fn().mockResolvedValue([]) } }));
vi.mock('../../../services/api/products', () => ({
  productApi: { getProducts: vi.fn().mockResolvedValue([]), searchProducts: vi.fn().mockResolvedValue([]) },
}));

import { CommandPalette } from '../CommandPalette';

beforeAll(() => {
  // cmdk measures and scrolls its list; jsdom has neither.
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

describe('CommandPalette - Vendor Returns jump', () => {
  it('ACCOUNTANT is offered Vendor Returns', async () => {
    await searchPages('ACCOUNTANT', 'vendor ret');
    expect(await screen.findByText('/purchase/vendor-returns', { exact: false })).toBeInTheDocument();
  });

  it('SALES_STAFF is not', async () => {
    await searchPages('SALES_STAFF', 'vendor ret');
    await screen.findByText('Jump to page');
    expect(screen.queryByText('/purchase/vendor-returns', { exact: false })).not.toBeInTheDocument();
  });
});

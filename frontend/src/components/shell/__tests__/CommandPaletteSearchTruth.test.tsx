// ============================================================================
// Global search tells a failed search from an empty one (R16) and finds the
// child pages a person can open (R39).
// ============================================================================
// R16 (audit): with the customer, order and product searches all answering
// 503, the palette settled on "No results" / "No customer matches" -- an outage
// read as "nobody by that name". R39: a store manager searching "transfers" or
// "stock transfer" got "No page matches" although /inventory/transfers opens
// for them; the search knew only the top menu plus four extras.

import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest';

let roles: string[] = ['STORE_MANAGER'];
const api = vi.hoisted(() => ({
  searchByPhone: vi.fn(),
  getCustomers: vi.fn(),
  getOrders: vi.fn(),
  searchProducts: vi.fn(),
}));

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u1', name: 'Tester', roles, activeRole: roles[0], activeStoreId: 'BV-DHN-01', storeIds: ['BV-DHN-01'] },
    hasModuleAccess: () => true,
  }),
}));
vi.mock('../../../services/api/customers', () => ({
  customerApi: { searchByPhone: api.searchByPhone, getCustomers: api.getCustomers },
}));
vi.mock('../../../services/api/sales', () => ({ orderApi: { getOrders: api.getOrders } }));
vi.mock('../../../services/api/products', () => ({
  productApi: { getProducts: vi.fn().mockResolvedValue([]), searchProducts: api.searchProducts },
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

const outage = () => Promise.reject(Object.assign(new Error('Server error. Please try again in a moment.'), { status: 503 }));

beforeEach(() => {
  roles = ['STORE_MANAGER'];
  for (const fn of Object.values(api)) fn.mockReset();
  api.searchByPhone.mockResolvedValue([]);
  api.getCustomers.mockResolvedValue({ customers: [] });
  api.getOrders.mockResolvedValue({ orders: [] });
  api.searchProducts.mockResolvedValue({ products: [] });
});

async function search(query: string, role = 'STORE_MANAGER') {
  roles = [role];
  render(
    <MemoryRouter>
      <CommandPalette open onOpenChange={vi.fn()} />
    </MemoryRouter>,
  );
  const user = userEvent.setup();
  await user.type(await screen.findByRole('combobox'), query);
  return user;
}

describe('a failed search is never shown as "no match" (R16)', () => {
  it('all three searches down: each says it could not search, no "No results"', async () => {
    api.getCustomers.mockImplementation(outage);
    api.getOrders.mockImplementation(outage);
    api.searchProducts.mockImplementation(outage);
    await search('ramesh');
    const alerts = await screen.findAllByRole('alert');
    expect(alerts.map((a) => a.textContent)).toEqual([
      expect.stringContaining('Could not search customers'),
      expect.stringContaining('Could not search orders'),
      expect.stringContaining('Could not search products'),
    ]);
    expect(screen.queryByText(/No results for/)).not.toBeInTheDocument();
    expect(screen.queryByText('No customer matches')).not.toBeInTheDocument();
    expect(screen.queryByText('No order matches')).not.toBeInTheDocument();
    expect(screen.queryByText('No product matches')).not.toBeInTheDocument();
  });

  it('Retry searches again and a recovered search shows its real answer', async () => {
    api.getCustomers.mockImplementationOnce(outage).mockResolvedValue({
      customers: [{ customer_id: 'C1', name: 'Ramesh Kumar', phone: '9800000001' }],
    });
    const user = await search('ramesh');
    const alert = await screen.findByRole('alert');
    await user.click(within(alert).getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('Ramesh Kumar')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('positive control: searches that answered with nothing still say "No results"', async () => {
    await search('zzzz');
    expect(await screen.findByText(/No results for/)).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('one search down, the others empty: no "No results" headline, only that one explains', async () => {
    api.searchProducts.mockImplementation(outage);
    await search('zzzz');
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Could not search products');
    expect(screen.queryByText(/No results for/)).not.toBeInTheDocument();
    expect(screen.getByText('No customer matches')).toBeInTheDocument();
  });
});

describe('global search finds the child pages a person can open (R39)', () => {
  it.each(['transfers', 'transfer', 'stock transfer'])('a store manager searching "%s" finds Inventory Transfers', async (q) => {
    await search(q);
    expect(await screen.findByText('/inventory/transfers', { exact: false })).toBeInTheDocument();
  });

  it.each([
    ['quarantine', '/inventory/quarantine'],
    ['movements', '/inventory/movements'],
    ['user management', '/settings/users'],
  ])('a store manager finds "%s"', async (q, route) => {
    await search(q);
    expect(await screen.findByText(route, { exact: false })).toBeInTheDocument();
  });

  it('roles keep their limits: a cashier (no Inventory, no Settings) finds neither', async () => {
    await search('transfer', 'CASHIER');
    await screen.findByText('Jump to page');
    expect(screen.queryByText('/inventory/transfers', { exact: false })).not.toBeInTheDocument();
  });

  it('workshop staff (module members) find Transfers', async () => {
    await search('transfers', 'WORKSHOP_STAFF');
    expect(await screen.findByText('/inventory/transfers', { exact: false })).toBeInTheDocument();
  });

  it('workshop staff are not offered the manager-only Opening stock', async () => {
    await search('opening stock', 'WORKSHOP_STAFF');
    await screen.findByText('Jump to page');
    expect(screen.queryByText('/inventory/opening-stock', { exact: false })).not.toBeInTheDocument();
  });

  it('an accountant (Settings, no User Management) is not offered User Management', async () => {
    await search('user management', 'ACCOUNTANT');
    await screen.findByText('Jump to page');
    expect(screen.queryByText('/settings/users', { exact: false })).not.toBeInTheDocument();
  });
});

// ============================================================================
// Review r1 #35: a non-admin is told which shop the Purchase tabs cover
// ============================================================================
// Admins get a Shop picker in the Purchase header (audit F63). Everyone else
// keeps their own shop -- the server's rule -- but the header said nothing, so
// an accountant read the report, the supplier balances and the order list with
// no shop named anywhere. Where the picker would be, a non-admin now reads a
// plain "Shop: <name>" (no control: there is nothing to choose).

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

vi.stubGlobal('requestIdleCallback', () => 0);

let roles: string[] = ['ACCOUNTANT'];
let activeStoreId: string | undefined = 'WO-PUN-01';
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u1', name: 'Staff', roles, activeStoreId, storeIds: activeStoreId ? [activeStoreId] : [] },
    hasRole: (want: string[]) => want.some((r) => roles.includes(r)),
    hasPermission: () => true,
  }),
}));
vi.mock('../../../hooks/useIsOnlineStore', () => ({ useIsOnlineStore: () => false }));

const get = vi.hoisted(() => vi.fn());
vi.mock('../../../services/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../services/api/client')>();
  const fake = { get, post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() };
  return { ...actual, default: fake, api: fake };
});

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { PurchaseLayout } from '../PurchaseLayout';

beforeEach(() => {
  get.mockReset();
  get.mockImplementation((url: string) =>
    Promise.resolve({
      data: url === '/stores'
        ? { stores: [{ store_id: 'WO-PUN-01', store_name: 'WizOpt Pune' }, { store_id: 'BV-DHN-01', store_name: 'Dhanbad' }] }
        : {},
    }),
  );
});

function open(path = '/purchase/this-month') {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/purchase" element={<PurchaseLayout />}>
            <Route path="this-month" element={<div>report</div>} />
            <Route path="orders" element={<div>report</div>} />
            <Route path="suppliers" element={<div>report</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const label = () => screen.queryByText((_, el) => el?.tagName === 'SPAN' && /^Shop: /.test(el.textContent ?? ''));

describe('the Purchase header names the shop a non-admin reads', () => {
  it('an accountant reads "Shop: WizOpt Pune", with no picker', async () => {
    roles = ['ACCOUNTANT'];
    activeStoreId = 'WO-PUN-01';
    open();
    await waitFor(() => expect(label()?.textContent).toBe('Shop: WizOpt Pune'));
    expect(screen.queryByLabelText('Purchase shop')).toBeNull();
  });

  it('a store manager reads his own shop too', async () => {
    roles = ['STORE_MANAGER'];
    activeStoreId = 'BV-DHN-01';
    open();
    await waitFor(() => expect(label()?.textContent).toBe('Shop: Dhanbad'));
  });

  it('an admin gets the picker instead of a label', async () => {
    roles = ['ADMIN'];
    activeStoreId = 'BV-DHN-01';
    open();
    expect(await screen.findByLabelText('Purchase shop')).toBeInTheDocument();
    expect(label()).toBeNull();
  });

  // Owner ruling 2026-10-07 (R3): it used to open the tab -- and the server
  // handed it every shop. Now: the plain message, no tab, no list requested.
  it.each([['ACCOUNTANT'], ['STORE_MANAGER']])('%s with no shop reads the message, not a tab', async (role) => {
    roles = [role];
    activeStoreId = undefined;
    open();
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Your login has no shop assigned - ask an admin to assign one.',
    );
    expect(screen.queryByText('report')).toBeNull();
    expect(label()).toBeNull();
  });

  // The header's create buttons sit outside the gate: they used to stay up
  // next to the message and open nothing (no section is mounted).
  it.each([
    ['/purchase/orders', 'New PO'],
    ['/purchase/suppliers', 'New supplier'],
  ])('%s: no shop, no %s button', async (path, button) => {
    roles = ['STORE_MANAGER'];
    activeStoreId = undefined;
    open(path);
    await screen.findByRole('alert');
    expect(screen.queryByRole('button', { name: button })).toBeNull();
  });

  it.each([
    ['/purchase/orders', 'New PO'],
    ['/purchase/suppliers', 'New supplier'],
  ])('%s: with a shop the %s button is there', async (path, button) => {
    roles = ['STORE_MANAGER'];
    activeStoreId = 'BV-DHN-01';
    open(path);
    expect(await screen.findByRole('button', { name: button })).toBeInTheDocument();
  });

  it('an admin with no shop still opens the tab on all stores', async () => {
    roles = ['ADMIN'];
    activeStoreId = undefined;
    open();
    expect(await screen.findByText('report')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull();
  });
});

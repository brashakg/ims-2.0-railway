// ============================================================================
// Ctrl-K "Recent" belongs to the person who made it (Codex R09)
// ============================================================================
// On a shared counter tablet an admin opened a customer through the palette,
// signed out, and the next person (an INVESTOR) opened the palette and was
// shown that customer's name and phone under Recent: the list lived under one
// key for the whole browser. It is now kept per person (and per role set and
// shop), so a different login never reads it. The same person still gets
// their own recent jumps back.

import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

type U = { id: string; name: string; roles: string[]; activeRole: string; activeStoreId: string; storeIds: string[] };
const ADMIN: U = { id: 'u-admin', name: 'Admin', roles: ['ADMIN'], activeRole: 'ADMIN', activeStoreId: 'BV-S1', storeIds: ['BV-S1'] };
const INVESTOR: U = { id: 'u-inv', name: 'Investor', roles: ['INVESTOR'], activeRole: 'INVESTOR', activeStoreId: 'BV-S1', storeIds: ['BV-S1'] };
let user: U = ADMIN;

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user, hasModuleAccess: () => true }),
}));
vi.mock('../../../services/api/customers', () => ({
  customerApi: {
    searchByPhone: vi.fn().mockResolvedValue([]),
    getCustomers: vi.fn().mockResolvedValue({
      customers: [{ customer_id: 'C-1', name: 'AUDIT ONLY Customer', phone: '9000000002' }],
    }),
  },
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

// A real-shaped localStorage (the runner's shim lacks methods).
beforeEach(() => {
  const mem = new Map<string, string>();
  vi.stubGlobal('localStorage', {
    getItem: (k: string) => mem.get(k) ?? null,
    setItem: (k: string, v: string) => void mem.set(k, String(v)),
    removeItem: (k: string) => void mem.delete(k),
    clear: () => mem.clear(),
    key: (i: number) => [...mem.keys()][i] ?? null,
    get length() { return mem.size; },
  });
});
afterEach(cleanup);

function openPalette() {
  return render(
    <MemoryRouter>
      <CommandPalette open onOpenChange={vi.fn()} />
    </MemoryRouter>,
  );
}

async function adminOpensTheCustomer() {
  user = ADMIN;
  const view = openPalette();
  const ue = userEvent.setup();
  await ue.type(await screen.findByRole('combobox'), 'AUDIT');
  await ue.click(await screen.findByText('AUDIT ONLY Customer'));
  view.unmount();
}

describe('CommandPalette - Recent is per person', () => {
  it("the next person never sees the last person's recent customer", async () => {
    await adminOpensTheCustomer();
    user = INVESTOR;
    openPalette();
    await screen.findByRole('combobox');
    expect(screen.queryByText('AUDIT ONLY Customer')).not.toBeInTheDocument();
    expect(screen.queryByText('9000000002')).not.toBeInTheDocument();
  });

  it('the same person still gets their recent jump back', async () => {
    await adminOpensTheCustomer();
    user = ADMIN;
    openPalette();
    expect(await screen.findByText('AUDIT ONLY Customer')).toBeInTheDocument();
    expect(screen.getByText('9000000002')).toBeInTheDocument();
  });
});

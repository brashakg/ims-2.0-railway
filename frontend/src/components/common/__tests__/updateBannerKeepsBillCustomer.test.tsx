// ============================================================================
// The "new version - Refresh" bar keeps the open bill's customer (audit R35)
// ============================================================================
// The draft is saved WITHOUT the customer (SEC-4: a shared terminal never
// leaves a customer's details in Local Storage), so pressing the bar's Refresh
// on a half-rung bill brought the cart back with nobody on it. The bar now
// hands the bill's customer across THAT ONE reload in this tab only, and the
// store takes it back only for the same bill and the same signed-in user.
// Every other reload keeps the SEC-4 rule: no customer.

import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

function memoryStorage(): Storage {
  const m = new Map<string, string>();
  return {
    getItem: (k: string) => (m.has(k) ? m.get(k)! : null),
    setItem: (k: string, v: string) => { m.set(k, String(v)); },
    removeItem: (k: string) => { m.delete(k); },
    clear: () => { m.clear(); },
    key: (i: number) => Array.from(m.keys())[i] ?? null,
    get length() { return m.size; },
  };
}

vi.mock('../../../hooks/useUpdateAvailable', () => ({
  useUpdateAvailable: () => ({ updateAvailable: true }),
}));

const CUSTOMER = { id: 'c-5c76', name: 'Asha Verma', phone: '9876543210' };
const signIn = (id: string) =>
  localStorage.setItem('ims_user', JSON.stringify({ id, activeStoreId: 'BV-BOK-01' }));

/** Ring a bill up the way the till does - a customer and one line - and let
 *  the draft's debounced save land (the audit waited for persistence too). */
async function openBill() {
  const { usePOSStore } = await import('../../../stores/posStore');
  const s = usePOSStore.getState();
  s.setCustomer(CUSTOMER as never);
  s.addToCart({ product_id: 'p-1', name: 'Frame', sku: 'F-1', unit_price: 1000, mrp: 1000, quantity: 1 } as never);
  await new Promise((r) => setTimeout(r, 600));
}

/** A page reload: the store module is loaded afresh and hydrates from storage. */
async function reloadStore() {
  vi.resetModules();
  return (await import('../../../stores/posStore')).usePOSStore.getState();
}

async function pressRefresh() {
  const { UpdateAvailableBanner } = await import('../UpdateAvailableBanner');
  render(<UpdateAvailableBanner />);
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
}

beforeEach(() => {
  vi.resetModules();
  Object.defineProperty(globalThis, 'localStorage', { value: memoryStorage(), configurable: true, writable: true });
  Object.defineProperty(globalThis, 'sessionStorage', { value: memoryStorage(), configurable: true, writable: true });
  Object.defineProperty(window, 'location', {
    value: { ...window.location, reload: vi.fn() },
    configurable: true,
    writable: true,
  });
  signIn('u-staff-1');
});

describe('R35 - Refresh on the update bar', () => {
  it('brings the bill back WITH its customer for the same signed-in user', async () => {
    await openBill();
    await pressRefresh();
    expect(window.location.reload).toHaveBeenCalledTimes(1);

    const after = await reloadStore();
    expect(after.cart).toHaveLength(1);
    expect(after.customer?.id).toBe('c-5c76');
  });

  it('hands the customer across ONE reload only', async () => {
    await openBill();
    await pressRefresh();
    await reloadStore();
    const second = await reloadStore();
    expect(second.cart).toHaveLength(1);
    expect(second.customer).toBeNull();
  });

  it('never hands a customer to a DIFFERENT signed-in user', async () => {
    await openBill();
    await pressRefresh();
    signIn('u-someone-else');
    const after = await reloadStore();
    expect(after.customer).toBeNull();
  });

  it('a plain reload (not the bar) still drops the customer - SEC-4 unchanged', async () => {
    await openBill();
    const after = await reloadStore();
    expect(after.cart).toHaveLength(1);
    expect(after.customer).toBeNull();
    expect(sessionStorage.length).toBe(0);
  });
});

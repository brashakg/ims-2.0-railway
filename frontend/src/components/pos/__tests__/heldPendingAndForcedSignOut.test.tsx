// Money review 23 of #1264: a parked bill whose press was never confirmed is
// marked "may already be paid" and Discard asks first; a forced sign-out (a
// 401 the client cannot refresh) parks a pending bill through THE one
// sign-out step.
(() => {
  const m = new Map<string, string>();
  const ls = {
    getItem: (k: string) => (m.has(k) ? m.get(k)! : null),
    setItem: (k: string, v: string) => { m.set(k, String(v)); },
    removeItem: (k: string) => { m.delete(k); },
    clear: () => { m.clear(); },
    key: (i: number) => Array.from(m.keys())[i] ?? null,
    get length() { return m.size; },
  };
  Object.defineProperty(globalThis, 'localStorage', { value: ls, configurable: true, writable: true });
})();

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', activeStoreId: 'S1' } }),
}));

import { HeldBillsControls } from '../HeldBillsControls';
import api from '../../../services/api/client';
import { usePOSStore } from '../../../stores/posStore';
import { billKeyEntry, billKeyFor } from '../posBillKey';

const heldBills = () => JSON.parse(localStorage.getItem('ims-held-bills') || '[]');
const seedHeld = (pending: boolean) => localStorage.setItem('ims-held-bills', JSON.stringify([{
  id: 'h1', customer: 'Asha', items: 1, total: 1000, heldAt: '', held_by: 'u1', store_id: 'S1', state: {},
  bill_key: { key: 'k1', at: Date.now(), ...(pending ? { pending: true } : {}) },
}]));

describe('a pending parked bill', () => {
  beforeEach(() => localStorage.clear());

  it('is marked "may already be paid" and Discard asks first', () => {
    seedHeld(true);
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    render(<HeldBillsControls />);
    fireEvent.click(screen.getByRole('button', { name: /Held/ }));
    expect(screen.getByText('May already be paid - check Orders')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Discard' }));
    expect(confirm).toHaveBeenCalled();
    expect(heldBills()).toHaveLength(1); // not discarded without a yes
    confirm.mockRestore();
  });

  it('a settled parked bill has no mark and discards at once', () => {
    seedHeld(false);
    const confirm = vi.spyOn(window, 'confirm');
    render(<HeldBillsControls />);
    fireEvent.click(screen.getByRole('button', { name: /Held/ }));
    expect(screen.queryByText('May already be paid - check Orders')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Discard' }));
    expect(confirm).not.toHaveBeenCalled();
    expect(heldBills()).toHaveLength(0);
    confirm.mockRestore();
  });
});

describe('a forced sign-out on a 401', () => {
  it('parks the pending bill with its key before the draft is cleared', async () => {
    localStorage.clear();
    localStorage.setItem('ims_user', JSON.stringify({ id: 'u1', activeStoreId: 'S1' }));
    usePOSStore.getState().resetTransaction();
    usePOSStore.getState().addToCart({
      id: 'p-1', product_id: 'p-1', name: 'Frame', category: 'FRAME', quantity: 1,
      unit_price: 1000, price: 1000, mrp: 1000, discount_percent: 0,
    } as any);
    const key = billKeyFor(usePOSStore.getState().ensureBillId());
    await api.get('/anything', {
      adapter: async (config) => Promise.reject(Object.assign(new Error('401'), {
        isAxiosError: true, config, response: { status: 401, data: {}, headers: {}, config, statusText: '' },
      })),
    } as any).catch(() => undefined);
    expect(heldBills().map((b: any) => [b.bill_key?.key, b.held_by, b.store_id])).toEqual([[key, 'u1', 'S1']]);
    expect(usePOSStore.getState().cart).toEqual([]);
  });
});

describe('money review 24: discarding the LIVE bill', () => {
  it('asks with the may-already-be-paid words when its press is pending', () => {
    localStorage.clear();
    usePOSStore.getState().resetTransaction();
    usePOSStore.getState().addToCart({
      id: 'p-1', product_id: 'p-1', name: 'Frame', category: 'FRAME', quantity: 1,
      unit_price: 1000, price: 1000, mrp: 1000, discount_percent: 0,
    } as any);
    billKeyFor(usePOSStore.getState().ensureBillId());
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    render(<HeldBillsControls />);
    fireEvent.click(screen.getByRole('button', { name: /Discard bill/ }));
    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/may already be paid/));
    expect(usePOSStore.getState().cart).toHaveLength(1);
    confirm.mockRestore();
  });
});

describe('money review 28: Discard is what lets a pending bill go', () => {
  it('a confirmed Discard of the live pending bill clears its key', () => {
    localStorage.clear();
    usePOSStore.getState().resetTransaction();
    usePOSStore.getState().addToCart({
      id: 'p-1', product_id: 'p-1', name: 'Frame', category: 'FRAME', quantity: 1,
      unit_price: 1000, price: 1000, mrp: 1000, discount_percent: 0,
    } as any);
    const id = usePOSStore.getState().ensureBillId();
    billKeyFor(id);
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<HeldBillsControls />);
    fireEvent.click(screen.getByRole('button', { name: /Discard bill/ }));
    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/may already be paid/));
    expect(billKeyEntry(id)).toBeNull();
    expect(usePOSStore.getState().cart).toEqual([]);
    confirm.mockRestore();
  });
});

describe('money review 29: an emptied pending bill in the Held list', () => {
  it('shows 0 items with the may-already-be-paid mark, and Discard asks first', () => {
    localStorage.clear();
    localStorage.setItem('ims-held-bills', JSON.stringify([{
      id: 'h0', customer: 'Walk-in', items: 0, total: 0, heldAt: '', held_by: 'u1', store_id: 'S1', state: {},
      bill_key: { key: 'k0', at: Date.now(), pending: true },
    }]));
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    render(<HeldBillsControls />);
    fireEvent.click(screen.getByRole('button', { name: /Held/ }));
    expect(screen.getByText(/0 items/)).toBeTruthy();
    expect(screen.getByText('May already be paid - check Orders')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Discard' }));
    expect(confirm).toHaveBeenCalled();
    confirm.mockRestore();
  });
});

// #1264 follow-ups: (P3) another tab's settle never un-pends this tab's
// press; (P4) the draft is saved with its bill id the moment a press marks
// the key pending; (orphan) a pending key with no bill on this till warns once.
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

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', activeStoreId: 'S1' } }),
}));

type BillKeyModule = typeof import('../posBillKey');
/** Two tabs: two copies of the module, one shared localStorage. */
async function twoTabs(): Promise<[BillKeyModule, BillKeyModule]> {
  vi.resetModules();
  const a = await import('../posBillKey');
  vi.resetModules();
  const b = await import('../posBillKey');
  return [a, b];
}
const REFUSED = { definite: true, sent: true };
const WARNING = 'A sale may already be paid but is no longer on this till - check Orders';

beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());

describe('P3: a settle in another tab', () => {
  it('never turns this tab\'s pending press back to not-pending', async () => {
    const [tabA, tabB] = await twoTabs();
    expect(tabA).not.toBe(tabB);
    tabB.billKeyFor('bill-X'); // tab B presses first
    tabA.billKeyFor('bill-X'); // tab A presses the same bill: its press is pending
    tabB.settleAfterPress('bill-X', false, REFUSED); // B's refusal arrives
    expect(tabA.billKeyEntry('bill-X')?.pending).toBe(true);
    // This tab's own refusal still settles its own press.
    tabA.settleAfterPress('bill-X', false, REFUSED);
    expect(tabA.billKeyEntry('bill-X')?.pending).toBe(false);
  });

  it('a recall in another tab never un-pends the entry either', async () => {
    const [tabA, tabB] = await twoTabs();
    const key = tabA.billKeyFor('bill-X');
    tabB.putBillKey('bill-X', key, Date.now(), false);
    expect(tabA.billKeyEntry('bill-X')?.pending).toBe(true);
  });

  it('writes touch only their own entry', async () => {
    const [tabA, tabB] = await twoTabs();
    const kA = tabA.billKeyFor('bill-A');
    const kB = tabB.billKeyFor('bill-B');
    tabB.settleAfterPress('bill-B', false, REFUSED);
    expect(tabA.billKeyEntry('bill-A')).toMatchObject({ key: kA, pending: true });
    expect(tabA.billKeyEntry('bill-B')).toMatchObject({ key: kB, pending: false });
  });
});

describe('P4: the draft at the moment of the press', () => {
  it('is saved with its bill id (and the customer) synchronously', async () => {
    vi.resetModules();
    const { usePOSStore } = await import('../../../stores/posStore');
    const { billKeyFor } = await import('../posBillKey');
    usePOSStore.setState({ customer: { id: 'c1', name: 'Asha' } } as any);
    const fp = usePOSStore.getState().ensureBillId();
    billKeyFor(fp);
    const draft = JSON.parse(localStorage.getItem('ims-pos-draft') || 'null');
    expect(draft?.state?.bill_id).toBe(fp);
    expect(draft?.state?.customer?.id).toBe('c1');
  });
});

describe('orphan warning on till load', () => {
  const seed = () => {
    const at = Date.now();
    localStorage.setItem('ims_pos_bill_keys', JSON.stringify({
      'bill-live': { key: 'k1', at, pending: true },
      'bill-held': { key: 'k2', at, pending: true },
      'bill-gone': { key: 'k3', at, pending: true },
      'bill-done': { key: 'k4', at, pending: false },
    }));
    localStorage.setItem('ims-held-bills', JSON.stringify([
      { id: 'h1', bill_key: { key: 'k2', at, pending: true }, held_by: 'u1', store_id: 'S1', state: { bill_id: 'bill-held', cart: [] } },
    ]));
    localStorage.setItem('ims-pos-draft', JSON.stringify({ state: { bill_id: 'bill-live', cart: [], payments: [] }, version: 0 }));
  };
  const load = async () => {
    vi.resetModules();
    const { usePOSStore } = await import('../../../stores/posStore');
    const { HeldBillsControls } = await import('../HeldBillsControls');
    const { billKeyEntry } = await import('../posBillKey');
    return { usePOSStore, HeldBillsControls, billKeyEntry };
  };

  it('lists only the pending key with no bill here, and shows ONE warning', async () => {
    seed();
    const { usePOSStore, HeldBillsControls } = await load();
    expect(usePOSStore.getState().orphan_bill_ids).toEqual(['bill-gone']);
    render(<HeldBillsControls />);
    expect(screen.getAllByText(WARNING)).toHaveLength(1);
  });

  it('dismiss is a confirm: cancelled keeps it; confirmed clears only the orphan key', async () => {
    seed();
    const { usePOSStore, HeldBillsControls, billKeyEntry } = await load();
    render(<HeldBillsControls />);
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(screen.queryByText(WARNING)).not.toBeNull();
    expect(billKeyEntry('bill-gone')?.pending).toBe(true);
    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(screen.queryByText(WARNING)).toBeNull();
    expect(billKeyEntry('bill-gone')).toBeNull();
    expect(billKeyEntry('bill-live')?.pending).toBe(true);
    expect(billKeyEntry('bill-held')?.pending).toBe(true);
    expect(usePOSStore.getState().orphan_bill_ids).toEqual([]);
  });

  it('no warning when every pending key has its bill here', async () => {
    seed();
    const keys = JSON.parse(localStorage.getItem('ims_pos_bill_keys')!);
    delete keys['bill-gone'];
    localStorage.setItem('ims_pos_bill_keys', JSON.stringify(keys));
    const { HeldBillsControls } = await load();
    render(<HeldBillsControls />);
    expect(screen.queryByText(WARNING)).toBeNull();
  });
});

// L1-F47: after previewing scope A, changing the scope left "Apply 1 valid
// row" enabled, and clicking it committed scope B (11 rows) unseen. Apply must
// be allowed only for the exact inputs the shown preview was made for, and a
// late answer for older inputs must never become the shown preview.
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// Stable objects: the page's effects depend on them.
const { toast, auth } = vi.hoisted(() => ({
  toast: { success: () => {}, error: () => {}, warning: () => {} },
  auth: { user: { name: 'Audit' } },
}));
vi.mock('../../../context/AuthContext', () => ({ useAuth: () => auth }));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));
const bulkPrice = vi.fn();
const bulkOffer = vi.fn();
vi.mock('../../../services/api/products', () => ({
  pricingApi: { bulkPrice: (...a: unknown[]) => bulkPrice(...a), bulkOffer: (...a: unknown[]) => bulkOffer(...a) },
  productApi: {
    getCategories: async () => ({ categories: [] }),
    getBrands: async () => ({ brands: ['BRAND A', 'BRAND B'] }),
  },
}));
vi.mock('../../../services/api/stores', () => ({ storeApi: { getStores: async () => ({ stores: [] }) } }));

import PricingOffersPage from '../PricingOffersPage';

const resp = (valid: number) => ({
  rows: [],
  summary: { counts: { valid, violations: 0, total: valid }, committed: 0 },
});

type Call = { brand?: string; apply: boolean };
const pending: Array<{ args: Call; resolve: (v: unknown) => void; reject: (e: unknown) => void }> = [];

beforeEach(() => {
  vi.clearAllMocks();
  pending.length = 0;
  const impl = (args: Call) => new Promise((resolve, reject) => pending.push({ args, resolve, reject }));
  bulkPrice.mockImplementation(impl);
  bulkOffer.mockImplementation(impl);
});

// Resolve every pending PREVIEW made for this brand (auto or manual).
const answer = async (brand: string, valid: number) => {
  await act(async () => {
    pending.filter((p) => p.args.brand === brand && !p.args.apply).forEach((p) => p.resolve(resp(valid)));
  });
};
const asked = (brand: string) => pending.some((p) => p.args.brand === brand && !p.args.apply);
const brandSelect = () => screen.getAllByRole('combobox').find((s) => s.textContent?.includes('All brands'))!;

describe.each([
  ['price', 'Bulk price update', bulkPrice],
  ['offer', 'Bulk offer update', bulkOffer],
])('%s tab', (_k, tabLabel, fn) => {
  it('a scope change disables Apply until a fresh preview of the new scope', async () => {
    render(<PricingOffersPage />);
    fireEvent.click(await screen.findByRole('button', { name: tabLabel }));
    fireEvent.change(brandSelect(), { target: { value: 'BRAND A' } });
    await waitFor(() => expect(asked('BRAND A')).toBe(true), { timeout: 2000 });
    await answer('BRAND A', 1);
    const apply = await screen.findByRole('button', { name: 'Apply 1 valid row' });
    expect(apply).toBeEnabled();

    fireEvent.change(brandSelect(), { target: { value: 'BRAND B' } });
    const stale = screen.getByRole('button', { name: /^Apply/ });
    expect(stale).toBeDisabled();
    fireEvent.click(stale);
    expect(fn.mock.calls.some((c) => (c[0] as Call).apply)).toBe(false);
  });

  it('a late answer for the old scope never becomes the shown preview', async () => {
    render(<PricingOffersPage />);
    fireEvent.click(await screen.findByRole('button', { name: tabLabel }));
    fireEvent.change(brandSelect(), { target: { value: 'BRAND A' } });
    await waitFor(() => expect(asked('BRAND A')).toBe(true), { timeout: 2000 });
    fireEvent.change(brandSelect(), { target: { value: 'BRAND B' } });
    await waitFor(() => expect(asked('BRAND B')).toBe(true), { timeout: 2000 });
    await answer('BRAND B', 11);
    await answer('BRAND A', 1); // the old scope answers last
    const apply = screen.getByRole('button', { name: /^Apply/ });
    expect(apply).toHaveTextContent('Apply 11 valid rows');
    expect(apply).toBeEnabled();
    fireEvent.click(apply);
    await waitFor(() => expect(fn.mock.calls.some((c) => (c[0] as Call).apply)).toBe(true));
    const applied = fn.mock.calls.find((c) => (c[0] as Call).apply)![0] as Call;
    expect(applied.brand).toBe('BRAND B');
  });
});

// Codex PR-1429 round 1: the Apply landed but its reply was lost; the same
// "Apply 1 valid row" stayed enabled and a second click moved 1010 -> 1020.
// The Apply now names the reviewed rows (the server refuses or reconciles a
// replay), and a failed Apply holds its preview until a fresh Preview.
describe.each([
  ['price', 'Bulk price update', bulkPrice, { old_mrp: 1000 }],
  ['offer', 'Bulk offer update', bulkOffer, { mrp: 1000 }],
])('%s tab: lost acknowledgement', (_k, tabLabel, fn, mrpField) => {
  const row = {
    product_id: 'P-1', sku: 'S-1', old_offer_price: 1000, new_offer_price: 1010, ok: true, changed: true,
    version: '2026-11-16T10:00:00.123000', ...mrpField,
  };
  const withRow = { rows: [row], summary: { counts: { valid: 1, violations: 0, total: 1, unchanged: 0 }, committed: 0 } };

  it('sends the reviewed rows, and a failed Apply cannot be clicked again until a fresh preview', async () => {
    render(<PricingOffersPage />);
    fireEvent.click(await screen.findByRole('button', { name: tabLabel }));
    fireEvent.change(brandSelect(), { target: { value: 'BRAND A' } });
    await waitFor(() => expect(asked('BRAND A')).toBe(true), { timeout: 2000 });
    await act(async () => {
      pending.filter((p) => !p.args.apply).forEach((p) => p.resolve(withRow));
    });
    const apply = await screen.findByRole('button', { name: 'Apply 1 valid row' });
    fireEvent.click(apply);
    await waitFor(() => expect(pending.some((p) => p.args.apply)).toBe(true));
    const sent = pending.find((p) => p.args.apply)!;
    expect((sent.args as unknown as { expected: unknown }).expected).toEqual([
      { product_id: 'P-1', mrp: 1000, offer_price: 1000, version: '2026-11-16T10:00:00.123000' },
    ]);
    // The write landed, the reply did not.
    await act(async () => {
      sent.reject(new Error('Network Error'));
    });
    await screen.findByRole('alert');
    const held = screen.getByRole('button', { name: /^Apply/ });
    expect(held).toBeDisabled();
    fireEvent.click(held);
    expect(pending.filter((p) => p.args.apply)).toHaveLength(1);

    // A fresh preview shows today's prices and is a new review.
    fireEvent.click(screen.getByRole('button', { name: /Preview/ }));
    await waitFor(() => expect(pending.filter((p) => !p.args.apply).length).toBeGreaterThan(1));
    await act(async () => {
      pending.filter((p) => !p.args.apply).forEach((p) => p.resolve(withRow));
    });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Apply 1 valid row' })).toBeEnabled());
    expect(screen.queryByRole('alert')).toBeNull();
    expect(fn).toHaveBeenCalled();
  });

  // Reviewer round 2: the server's safe replay (a saved row is never written
  // again; its website copy is carried again) was reachable only by API. The
  // held preview now offers it, sending exactly the reviewed rows again.
  it('"Retry the same Apply" re-sends the reviewed rows once, and never the new-change Apply', async () => {
    render(<PricingOffersPage />);
    fireEvent.click(await screen.findByRole('button', { name: tabLabel }));
    fireEvent.change(brandSelect(), { target: { value: 'BRAND A' } });
    await waitFor(() => expect(asked('BRAND A')).toBe(true), { timeout: 2000 });
    await act(async () => {
      pending.filter((p) => !p.args.apply).forEach((p) => p.resolve(withRow));
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Apply 1 valid row' }));
    await waitFor(() => expect(pending.some((p) => p.args.apply)).toBe(true));
    await act(async () => {
      pending.find((p) => p.args.apply)!.reject(new Error('Network Error'));
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Retry the same Apply' }));
    await waitFor(() => expect(pending.filter((p) => p.args.apply)).toHaveLength(2));
    const [first, retry] = pending.filter((p) => p.args.apply);
    expect(retry.args).toEqual(first.args);
    expect((retry.args as unknown as { expected: unknown }).expected).toEqual([
      { product_id: 'P-1', mrp: 1000, offer_price: 1000, version: '2026-11-16T10:00:00.123000' },
    ]);
    // While the retry runs it cannot be sent a third time.
    expect(screen.getByRole('button', { name: 'Retry the same Apply' })).toBeDisabled();
    await act(async () => {
      retry.resolve({ ...withRow, summary: { ...withRow.summary, committed: 0 } });
    });
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
    expect(screen.getByRole('button', { name: /^Apply/ })).toBeDisabled();
    expect(fn.mock.calls.filter((c) => (c[0] as Call).apply)).toHaveLength(2);
  });

  // Reviewer round 3: the hold lives in this tab only. A fresh preview (after
  // a reload, or a scope change and back) names the rows a bulk Apply moved
  // in the last hour, from the server's own record.
  it('a fresh preview names rows a bulk Apply changed in the last hour', async () => {
    render(<PricingOffersPage />);
    fireEvent.click(await screen.findByRole('button', { name: tabLabel }));
    fireEvent.change(brandSelect(), { target: { value: 'BRAND A' } });
    await waitFor(() => expect(asked('BRAND A')).toBe(true), { timeout: 2000 });
    await act(async () => {
      pending.filter((p) => !p.args.apply).forEach((p) => p.resolve(withRow));
    });
    await screen.findByRole('button', { name: 'Apply 1 valid row' });
    expect(screen.queryByText(/changed by a bulk Apply in the last hour/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /Preview/ }));
    await waitFor(() => expect(pending.filter((p) => !p.args.apply).length).toBeGreaterThan(1));
    const recent = { ...row, recent_bulk_change_at: '2026-11-16T05:00:00Z' };
    await act(async () => {
      pending.filter((p) => !p.args.apply).forEach((p) => p.resolve({ ...withRow, rows: [recent] }));
    });
    expect(await screen.findByText(/1 row was changed by a bulk Apply in the last hour/)).toBeTruthy();
  });

  // D94: the Apply sends each row's preview version; a row someone changed
  // since is left alone by the server and must not read as a green 'OK'.
  it('a row the Apply left alone shows "Changed since preview: not saved"', async () => {
    render(<PricingOffersPage />);
    fireEvent.click(await screen.findByRole('button', { name: tabLabel }));
    fireEvent.change(brandSelect(), { target: { value: 'BRAND A' } });
    await waitFor(() => expect(asked('BRAND A')).toBe(true), { timeout: 2000 });
    await act(async () => {
      pending.filter((p) => !p.args.apply).forEach((p) => p.resolve(withRow));
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Apply 1 valid row' }));
    await waitFor(() => expect(pending.some((p) => p.args.apply)).toBe(true));
    const left = { ...row, applied: false, reason: 'CHANGED_SINCE_PREVIEW', message: 'Changed since preview' };
    await act(async () => {
      pending.find((p) => p.args.apply)!.resolve({
        rows: [left],
        summary: { ...withRow.summary, applied: true, counts: { ...withRow.summary.counts, changed_since_preview: 1 } },
      });
    });
    expect(await screen.findByText('Changed since preview: not saved')).toBeTruthy();
    expect(screen.queryByText(/^OK/)).toBeNull();
  });
});

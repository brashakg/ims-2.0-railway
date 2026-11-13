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
const pending: Array<{ args: Call; resolve: (v: unknown) => void }> = [];

beforeEach(() => {
  vi.clearAllMocks();
  pending.length = 0;
  const impl = (args: Call) => new Promise((resolve) => pending.push({ args, resolve }));
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

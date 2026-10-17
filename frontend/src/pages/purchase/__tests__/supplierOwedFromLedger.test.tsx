// ============================================================================
// Audit F56 (with F57): the supplier card says what the LEDGER says we owe
// ============================================================================
// Owner audit 2026-09-29: the same "we owe" figure reads Rs 0, Rs 1,77,896 and
// Rs 1,44,456 on different screens. The Rs 0 is this card: Outstanding and
// Total purchases map v.current_outstanding / v.total_purchases
// (purchaseMappers.ts:32-34), fields NOTHING in the backend ever writes, so
// every card shows a confident Rs 0.0L while Rs 1.44L is owed.
//
// Ruling 2026-09-28: one payable rule -- the supplier ledger. Every
// ledger-shaped read below answers the SAME Rs 1,44,456 (the vendor ledger,
// /finance/vendor-payments, AP aging, the new report), so the card may read any
// of them; what it may not do is invent a zero. A role that may not read
// supplier balances (a store manager -- owner ruling 2026-09-29: ADMIN +
// ACCOUNTANT only) never ASKS for them: the mocked server below answers him
// 200, as this branch's /finance/vendor-payments still does, so only the
// screen's own gate keeps the figure off his card.
//
// The HTTP client itself is mocked (not vendorsApi), so the fix is free to pick
// its ledger reader. These were `it.fails` pins while the finding was open;
// the card now reads GET /finance/vendor-payments (the ledger).

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

let roles: string[] = ['ADMIN'];

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u1', name: 'Owner', roles, activeStoreId: 'BV-DHN-01', storeIds: ['BV-DHN-01'] },
    hasRole: (want: string[]) => want.some((r) => roles.includes(r) || roles.includes('SUPERADMIN')),
    hasPermission: () => true,
  }),
}));
vi.mock('../../../hooks/useIsOnlineStore', () => ({ useIsOnlineStore: () => false }));
vi.mock('../../../services/api/entities', () => ({
  entitiesApi: { meta: vi.fn().mockResolvedValue({ state_codes: [], entity_types: [] }) },
}));

const OWED = 144456;
const BILLED = 480000;
const VENDOR = {
  vendor_id: 'v1',
  legal_name: 'Jharkhand Optical',
  trade_name: 'Jharkhand Optical',
  vendor_code: 'SUP001',
  state_code: '20',
  gstin: '20AABCU9603R1Z1',
  credit_days: 30,
  credit_limit: 250000,
};

const get = vi.hoisted(() => vi.fn());
vi.mock('../../../services/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../services/api/client')>();
  const fake = { get, post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() };
  return { ...actual, default: fake, api: fake };
});

function forbidden() {
  return Promise.reject(Object.assign(new Error('Forbidden'), { response: { status: 403, data: {} } }));
}

/** Every ledger-shaped read answers the one figure; the vendor ledger reads
 *  refuse a role outside the supplier-balance readers. /finance/vendor-payments
 *  does NOT (the manager gate there lands with #1161): the screen must not ask. */
function route(url: string) {
  const readsBalances = roles.some((r) => ['ADMIN', 'ACCOUNTANT', 'SUPERADMIN'].includes(r));
  if (url === '/vendors' || url === '/vendors/') return Promise.resolve({ data: { vendors: [VENDOR], total: 1 } });
  const balanceRead =
    url === '/vendors/ap-aging' ||
    url.startsWith('/vendors/purchases-this-month') ||
    /^\/vendors\/v1\/(ledger|bills|payments)/.test(url);
  if (balanceRead && !readsBalances) return forbidden();
  if (url === '/finance/vendor-payments') {
    return Promise.resolve({ data: [{
      vendor_id: 'v1', vendor_name: 'Jharkhand Optical', po_total: 0, total_orders: 0,
      total_billed: BILLED, total_paid: BILLED - OWED, total_tds: 0, total_debit_notes: 0, balance: OWED,
    }] });
  }
  if (url === '/vendors/ap-aging') {
    return Promise.resolve({ data: {
      as_of: '2026-09-29',
      totals: { buckets: {}, total_outstanding: OWED, unallocated_credits: 0, net_payable: OWED },
      vendors: [{ vendor_id: 'v1', vendor_name: 'Jharkhand Optical', buckets: {}, total_outstanding: OWED, unallocated_credits: 0, net_payable: OWED }],
    } });
  }
  if (url === '/vendors/v1/ledger') {
    return Promise.resolve({ data: { vendor_id: 'v1', vendor: VENDOR, ledger: { entries: [], closing_balance: OWED, total_billed: BILLED, total_paid: BILLED - OWED, total_tds: 0, total_debit_notes: 0 }, aging: {} } });
  }
  if (url.startsWith('/vendors/purchases-this-month')) {
    return Promise.resolve({ data: { month: '2026-09', vendors: [{ vendor_id: 'v1', vendor_name: 'Jharkhand Optical', ordered: 0, received: 0, billed: 0, paid: 0, owed: OWED, next_due_date: null }], totals: { ordered: 0, received: 0, billed: 0, paid: 0, owed: OWED } } });
  }
  return Promise.resolve({ data: {} });
}

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { SuppliersSection } from '../SuppliersSection';

beforeEach(() => {
  get.mockReset();
  get.mockImplementation((url: string) => route(url));
});

async function openAs(role: string) {
  roles = [role];
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={['/purchase/suppliers']}>
        <SuppliersSection />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await screen.findByText('Jharkhand Optical');
}

/** The value printed under a card label, or null when the card has no such figure. */
function figure(label: string): string | null {
  const [el] = screen.queryAllByText(label);
  if (!el) return null;
  return (el.nextElementSibling?.textContent ?? '').trim();
}

describe('F56: the supplier card reads what we owe from the supplier ledger', () => {
  it('renders the supplier card from GET /vendors (harness check)', async () => {
    await openAs('ADMIN');
    expect(get).toHaveBeenCalledWith('/vendors', expect.anything());
    expect(screen.getByText('Jharkhand Optical')).toBeInTheDocument();
  });

  it('F56: an admin sees Outstanding Rs 1,44,456 -- the ledger, in whole rupees -- not Rs 0', async () => {
    await openAs('ADMIN');
    await vi.waitFor(() => expect(figure('Outstanding')).toBe('₹1,44,456'));
  });

  it('F56/F57: a store manager, who cannot read supplier balances, is never shown a confident Rs 0', async () => {
    await openAs('STORE_MANAGER');
    expect(screen.queryAllByText('₹0')).toHaveLength(0);
    expect(screen.queryAllByText('₹0.0L')).toHaveLength(0);
  });

  it('owner ruling 2026-09-29: a store manager never asks for what we owe -- no figure even when the server would answer', async () => {
    await openAs('STORE_MANAGER');
    await new Promise((r) => setTimeout(r, 50));
    expect(get.mock.calls.filter(([url]) => url === '/finance/vendor-payments')).toHaveLength(0);
    expect(figure('Outstanding')).toBe('—');
    expect(screen.queryByText('₹1,44,456')).toBeNull();
  });

  it('F63: the card reads the shop the Purchase filter shows', async () => {
    await openAs('ACCOUNTANT');
    await vi.waitFor(() => expect(figure('Outstanding')).toBe('₹1,44,456'));
    const params = get.mock.calls
      .filter(([url]) => url === '/finance/vendor-payments')
      .map(([, cfg]) => (cfg as { params?: Record<string, string> } | undefined)?.params?.store_id);
    expect(params).toEqual(['BV-DHN-01']);
  });
});

// ============================================================================
// IMS 2.0 - the PO drawer keeps supplier money to the accounts roles
// ============================================================================
// Owner ruling 2026-10-01: "supplier balances should not be shown to anyone
// apart from admin superadmin and accountant". A bill booked against a PO says
// what we owe the supplier (its total) and whether we have paid it (its
// OUTSTANDING / PARTIAL / PAID status, and the "Bill settled" event whose
// detail carries that status). The drawer opens for store and area managers
// too (the /purchase/orders gate), so for them it shows the bill's number and
// date and nothing else -- whatever the server sends, and in the shape the
// server now sends them (no total, a neutral BOOKED status).
// Both directions: an accounts reader still sees all of it.

import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

let currentRoles: string[] = ['STORE_MANAGER'];

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    // Same contract as AuthContext.hasRole: ADMIN / SUPERADMIN pass any check.
    hasRole: (role: string | string[]) => {
      if (currentRoles.includes('ADMIN') || currentRoles.includes('SUPERADMIN')) return true;
      const wanted = Array.isArray(role) ? role : [role];
      return wanted.some((r) => currentRoles.includes(r));
    },
  }),
}));

vi.mock('react-router-dom', () => ({ useNavigate: () => vi.fn() }));

vi.mock('../../../services/api/inventory', () => ({
  vendorsApi: { getPOTimeline: vi.fn() },
}));

import { vendorsApi } from '../../../services/api/inventory';
import { POLifecycleDrawer, type POTimeline } from '../POLifecycleDrawer';

const getPOTimeline = vi.mocked(vendorsApi.getPOTimeline);

const BASE_EVENTS = [
  { kind: 'ordered', label: 'Ordered', at: '2026-09-01T10:00:00Z', ref: 'PO-2026-0042' },
  { kind: 'on_shelf', label: 'On shelf', at: '2026-09-05T10:00:00Z', ref: 'GRN-77' },
];

/** What a leaky (or stale) server would send: the bill's total, its paid
 *  state and the status inside the "Bill settled" event detail. */
function leakyTimeline(): POTimeline {
  return {
    po_id: 'PO1',
    po_number: 'PO-2026-0042',
    status: 'RECEIVED',
    vendor_id: 'V1',
    vendor_name: 'Essilor India',
    events: [
      ...BASE_EVENTS,
      {
        kind: 'bill_settled',
        label: 'Bill settled',
        at: '2026-09-06T12:00:00Z',
        ref: 'INV-9',
        detail: 'Purchase invoice booked (PARTIAL)',
      },
    ],
    grns: [{ grn_id: 'g1', grn_number: 'GRN-77', status: 'ACCEPTED' }],
    invoices: [
      { bill_id: 'b1', invoice_number: 'INV-9', status: 'PARTIAL', total: 48250, created_at: '2026-09-06T12:00:00Z' },
    ],
  };
}

/** What the server sends a manager now (services/payables_mask): no total, a
 *  neutral BOOKED status, a plain event detail. */
function maskedTimeline(): POTimeline {
  const tl = leakyTimeline();
  return {
    ...tl,
    events: tl.events.map((e) => (e.kind === 'bill_settled' ? { ...e, detail: 'Purchase invoice booked' } : e)),
    invoices: [{ bill_id: 'b1', invoice_number: 'INV-9', status: 'BOOKED', created_at: '2026-09-06T12:00:00Z' }],
  };
}

async function open(tl: POTimeline) {
  getPOTimeline.mockResolvedValue(tl);
  render(<POLifecycleDrawer poId="PO1" poNumber="PO-2026-0042" onClose={vi.fn()} />);
  // Anchor on something only the fetched timeline supplies.
  await screen.findByText('Essilor India');
  await screen.findByText('INV-9', { selector: 'p' });
}

beforeEach(() => {
  currentRoles = ['STORE_MANAGER'];
  getPOTimeline.mockReset();
});

describe('POLifecycleDrawer - a manager reads no supplier money', () => {
  it.each([['STORE_MANAGER'], ['AREA_MANAGER']])(
    '%s: the bill is listed by number, with no amount, no paid state and no "Bill settled"',
    async (role) => {
      currentRoles = [role];
      await open(leakyTimeline());
      const body = document.body.textContent || '';
      expect(body).not.toContain('48,250');
      expect(body).not.toContain('₹');
      expect(body).not.toMatch(/PARTIAL|OUTSTANDING|\bPAID\b/);
      expect(screen.queryByText('Bill settled')).not.toBeInTheDocument();
      // The rest of the timeline is untouched.
      const labels = screen.getAllByTestId('po-timeline-event').map((el) => el.querySelector('p')?.textContent);
      expect(labels).toEqual(['Ordered', 'On shelf']);
    },
  );

  it('STORE_MANAGER: the masked server answer (BOOKED, no total) reads cleanly too', async () => {
    await open(maskedTimeline());
    expect(screen.getByText('Purchase invoices (1)')).toBeInTheDocument();
    expect(screen.queryByText('Bill settled')).not.toBeInTheDocument();
    expect(screen.queryByText('BOOKED')).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain('₹');
  });
});

describe('POLifecycleDrawer - the accounts roles still read it', () => {
  it.each([['ACCOUNTANT'], ['ADMIN'], ['SUPERADMIN']])(
    '%s: the bill total, its paid state and the "Bill settled" event',
    async (role) => {
      currentRoles = [role];
      await open(leakyTimeline());
      expect(document.body.textContent).toContain('₹48,250');
      // PARTIAL is not in the owner vocabulary, so the chip shows it raw.
      expect(screen.getByText('PARTIAL')).toBeInTheDocument();
      expect(screen.getByText('Bill settled')).toBeInTheDocument();
      expect(screen.getByText(/Purchase invoice booked \(PARTIAL\)/)).toBeInTheDocument();
    },
  );
});

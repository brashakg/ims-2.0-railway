// ============================================================================
// IMS 2.0 - POLifecycleDrawer tests
// ============================================================================
// Pins the drawer contract:
//   * events render in the server's chronological order (5-word vocabulary)
//   * ONE derived next-step button per state --
//       DRAFT                     -> "Send to vendor" (parent callback)
//       receivable (SENT/...)     -> "Receive" deep-link (receiving roles)
//       ACCEPTED GRN, no invoice  -> "Book invoice" (AP roles only)
//   * fail-soft on fetch error (inline error line, never a blank panel)
//   * Esc + scrim click both close

import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';

// ---- mocks (factories only close over these lazily) -----------------------

let currentRoles: string[] = ['SUPERADMIN'];
const mockNavigate = vi.fn();

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    hasRole: (role: string | string[]) => {
      const wanted = Array.isArray(role) ? role : [role];
      return wanted.some((r) => currentRoles.includes(r));
    },
  }),
}));

vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
}));

vi.mock('../../../services/api/inventory', () => ({
  vendorsApi: { getPOTimeline: vi.fn() },
}));

import { vendorsApi } from '../../../services/api/inventory';
import { POLifecycleDrawer, type POTimeline } from '../POLifecycleDrawer';

const getPOTimeline = vi.mocked(vendorsApi.getPOTimeline);

function makeTimeline(overrides: Partial<POTimeline> = {}): POTimeline {
  return {
    po_id: 'PO1',
    po_number: 'PO-2026-0042',
    status: 'SENT',
    vendor_id: 'V1',
    vendor_name: 'Essilor India',
    delivery_store_id: 'BV-BOK-01',
    events: [
      { kind: 'ordered', label: 'Ordered', at: '2026-06-01T10:00:00Z', ref: 'PO-2026-0042', detail: 'Created by Admin' },
      { kind: 'sent', label: 'Sent', at: '2026-06-02T11:00:00Z', ref: 'PO-2026-0042', detail: 'Emailed to vendor' },
    ],
    grns: [],
    invoices: [],
    ...overrides,
  };
}

function renderDrawer(props: Partial<Parameters<typeof POLifecycleDrawer>[0]> = {}) {
  const onClose = vi.fn();
  const utils = render(
    <POLifecycleDrawer poId="PO1" poNumber="PO-2026-0042" onClose={onClose} {...props} />,
  );
  return { onClose, ...utils };
}

beforeEach(() => {
  currentRoles = ['SUPERADMIN'];
  mockNavigate.mockReset();
});

// ---------------------------------------------------------------------------

describe('POLifecycleDrawer — timeline rendering', () => {
  it('renders events in the order the server sent (chronological)', async () => {
    getPOTimeline.mockResolvedValue(
      makeTimeline({
        status: 'RECEIVED',
        events: [
          { kind: 'ordered', label: 'Ordered', at: '2026-06-01T10:00:00Z' },
          { kind: 'sent', label: 'Sent', at: '2026-06-02T11:00:00Z' },
          { kind: 'box_received', label: 'Box received', at: '2026-06-05T09:00:00Z', ref: 'GRN-77' },
          { kind: 'on_shelf', label: 'On shelf', at: '2026-06-05T10:00:00Z', ref: 'GRN-77' },
          { kind: 'bill_settled', label: 'Bill settled', at: '2026-06-06T12:00:00Z', ref: 'INV-9' },
        ],
        invoices: [{ bill_id: 'b1', invoice_number: 'INV-9', status: 'BOOKED', total: 1200, created_at: '2026-06-06T12:00:00Z' }],
      }),
    );
    renderDrawer();

    const items = await screen.findAllByTestId('po-timeline-event');
    const labels = items.map((el) => el.querySelector('p')?.textContent);
    expect(labels).toEqual(['Ordered', 'Sent', 'Box received', 'On shelf', 'Bill settled']);
  });

  it('shows the PO number, vendor and ref/detail line', async () => {
    getPOTimeline.mockResolvedValue(makeTimeline());
    renderDrawer();

    expect(await screen.findByText('PO-2026-0042')).toBeInTheDocument();
    expect(screen.getByText('Essilor India')).toBeInTheDocument();
    // ref + detail joined on one muted line
    expect(screen.getByText(/Emailed to vendor/)).toBeInTheDocument();
  });
});

describe('POLifecycleDrawer — next-step derivation', () => {
  it('DRAFT -> "Send to vendor" fires the parent callback', async () => {
    getPOTimeline.mockResolvedValue(makeTimeline({ status: 'DRAFT', events: [{ kind: 'ordered', label: 'Ordered', at: '2026-06-01T10:00:00Z' }] }));
    const onSendToVendor = vi.fn();
    renderDrawer({ onSendToVendor });

    const btn = await screen.findByRole('button', { name: /Send to vendor/i });
    await userEvent.setup().click(btn);
    expect(onSendToVendor).toHaveBeenCalledTimes(1);
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it('DRAFT without an onSendToVendor callback offers no next-step', async () => {
    getPOTimeline.mockResolvedValue(makeTimeline({ status: 'DRAFT' }));
    renderDrawer();

    await screen.findAllByTestId('po-timeline-event');
    expect(screen.queryByRole('button', { name: /Send to vendor/i })).not.toBeInTheDocument();
  });

  it('receivable status -> "Receive" deep-links with vendor_id + po_id (receiving role)', async () => {
    currentRoles = ['STORE_MANAGER'];
    getPOTimeline.mockResolvedValue(makeTimeline({ status: 'SENT' }));
    const { onClose } = renderDrawer();

    const btn = await screen.findByRole('button', { name: /Receive/i });
    await userEvent.setup().click(btn);
    expect(mockNavigate).toHaveBeenCalledWith('/purchase/receive?vendor_id=V1&po_id=PO1');
    expect(onClose).toHaveBeenCalled();
  });

  it('receivable status shows NO button for a non-receiving role', async () => {
    currentRoles = ['SALES_STAFF'];
    getPOTimeline.mockResolvedValue(makeTimeline({ status: 'SENT' }));
    renderDrawer();

    await screen.findAllByTestId('po-timeline-event');
    expect(screen.queryByRole('button', { name: /Receive/i })).not.toBeInTheDocument();
  });

  it('ACCEPTED GRN with no invoice -> "Book invoice" for an AP role', async () => {
    currentRoles = ['ACCOUNTANT'];
    getPOTimeline.mockResolvedValue(
      makeTimeline({
        status: 'RECEIVED',
        grns: [
          { grn_id: 'grn-1', grn_number: 'GRN-77', status: 'ACCEPTED', created_at: '2026-06-05T09:00:00Z', accepted_at: '2026-06-05T10:00:00Z', total_accepted: 10 },
        ],
        invoices: [],
      }),
    );
    renderDrawer();

    const btn = await screen.findByRole('button', { name: /Book invoice/i });
    await userEvent.setup().click(btn);
    expect(mockNavigate).toHaveBeenCalledWith('/purchase/invoices/book?grn_id=grn-1');
  });

  it('"Book invoice" is hidden for a non-AP role', async () => {
    currentRoles = ['STORE_MANAGER'];
    getPOTimeline.mockResolvedValue(
      makeTimeline({
        status: 'RECEIVED',
        grns: [{ grn_id: 'grn-1', grn_number: 'GRN-77', status: 'ACCEPTED' }],
        invoices: [],
      }),
    );
    renderDrawer();

    await screen.findAllByTestId('po-timeline-event');
    expect(screen.queryByRole('button', { name: /Book invoice/i })).not.toBeInTheDocument();
  });

  it('"Book invoice" is hidden once a live invoice exists', async () => {
    currentRoles = ['ACCOUNTANT'];
    getPOTimeline.mockResolvedValue(
      makeTimeline({
        status: 'RECEIVED',
        grns: [{ grn_id: 'grn-1', grn_number: 'GRN-77', status: 'ACCEPTED' }],
        invoices: [{ bill_id: 'b1', invoice_number: 'INV-9', status: 'BOOKED', total: 500 }],
      }),
    );
    renderDrawer();

    await screen.findAllByTestId('po-timeline-event');
    expect(screen.queryByRole('button', { name: /Book invoice/i })).not.toBeInTheDocument();
  });

  it('CANCELLED PO gets no next-step at all', async () => {
    getPOTimeline.mockResolvedValue(
      makeTimeline({
        status: 'CANCELLED',
        events: [{ kind: 'cancelled', label: 'Cancelled', at: '2026-06-03T10:00:00Z' }],
      }),
    );
    renderDrawer({ onSendToVendor: vi.fn() });

    await screen.findAllByTestId('po-timeline-event');
    expect(screen.queryByRole('button', { name: /Send to vendor|Receive|Book invoice/i })).not.toBeInTheDocument();
  });
});

describe('POLifecycleDrawer — fail-soft + close behaviour', () => {
  it('renders an inline error line (with Retry) when the fetch fails — never blank', async () => {
    getPOTimeline.mockRejectedValue(new Error('boom'));
    renderDrawer();

    expect(await screen.findByText(/Couldn't load this PO's timeline\./)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Retry/i })).toBeInTheDocument();
    // Header still shows the PO number handed down as a prop.
    expect(screen.getByText('PO-2026-0042')).toBeInTheDocument();
  });

  it('Retry refetches and recovers', async () => {
    getPOTimeline.mockRejectedValueOnce(new Error('boom')).mockResolvedValueOnce(makeTimeline());
    renderDrawer();

    const retry = await screen.findByRole('button', { name: /Retry/i });
    await userEvent.setup().click(retry);
    expect(await screen.findAllByTestId('po-timeline-event')).toHaveLength(2);
  });

  it('Esc closes the drawer', async () => {
    getPOTimeline.mockResolvedValue(makeTimeline());
    const { onClose } = renderDrawer();
    await screen.findAllByTestId('po-timeline-event');

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('clicking the scrim closes the drawer', async () => {
    getPOTimeline.mockResolvedValue(makeTimeline());
    const { onClose } = renderDrawer();
    await screen.findAllByTestId('po-timeline-event');

    // The scrim is the button whose accessible name is exactly "Close"
    // (the header X is "Close drawer").
    await userEvent.setup().click(screen.getByRole('button', { name: 'Close' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

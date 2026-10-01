// ============================================================================
// IMS 2.0 - Purchase orders list: status filter + cancel with a reason
// ============================================================================
// Procurement audit F23: the "All Status" filter offered Pending / Approved /
// Ordered (statuses the server never produces) and had no Sent or Partly
// received, so orders with the vendor could not be filtered. The filter words
// now match the badges: Draft, Sent, Partly received, Received, Cancelled.
// F4: cancelling calls the server WITH the typed reason and the list shows the
// server's answer (never a local status flip).

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';

const toastMock = vi.hoisted(() => ({
  success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn(),
}));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toastMock }));

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u1', name: 'Mgr', roles: ['STORE_MANAGER'], activeStoreId: 'BV-DHN-02' },
    hasRole: () => true,
    hasPermission: () => true,
  }),
}));
vi.mock('../../../hooks/useIsOnlineStore', () => ({ useIsOnlineStore: () => false }));
vi.mock('../../../components/print/storeIdentity', () => ({
  resolveStoreIdentity: vi.fn().mockResolvedValue(null),
}));

const getPurchaseOrders = vi.fn();
const cancelPurchaseOrder = vi.fn();
const cancelPurchaseOrderLine = vi.fn();

vi.mock('../../../services/api', () => ({
  vendorsApi: {
    getVendors: vi.fn().mockResolvedValue({ vendors: [] }),
    getPurchaseOrders: (...a: unknown[]) => getPurchaseOrders(...a),
    sendPurchaseOrder: vi.fn(),
    cancelPurchaseOrder: (...a: unknown[]) => cancelPurchaseOrder(...a),
    cancelPurchaseOrderLine: (...a: unknown[]) => cancelPurchaseOrderLine(...a),
    updatePurchaseOrder: vi.fn(),
    createPurchaseOrder: vi.fn(),
  },
  productApi: { getProducts: vi.fn() },
}));

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { PurchaseOrdersSection } from '../PurchaseOrdersSection';

const raw = (n: string, status: string, over: Record<string, unknown> = {}) => ({
  po_id: `po-${n}`,
  po_number: `PO-${n}`,
  vendor_id: 'v1',
  vendor_name: 'Jharkhand Optical Traders',
  status,
  created_at: '2026-09-20T10:00:00',
  expected_date: '2026-09-30',
  items: [
    { product_id: 'p1', product_name: 'Carrera CA8895', sku: 'CA', quantity: 2, unit_price: 1000, tax_rate: 5 },
  ],
  subtotal: 2000,
  tax_amount: 100,
  total_amount: 2100,
  ...over,
});

const ALL = [
  raw('D1', 'DRAFT'),
  raw('S1', 'SENT'),
  raw('S2', 'ACKNOWLEDGED'),
  raw('P1', 'PARTIALLY_RECEIVED'),
  raw('R1', 'RECEIVED'),
  raw('C1', 'CANCELLED'),
];

beforeEach(() => {
  vi.clearAllMocks();
  getPurchaseOrders.mockResolvedValue({ purchase_orders: ALL });
});

function renderSection() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <PurchaseOrdersSection />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function statusFilter(): HTMLSelectElement {
  return screen.getByRole('combobox', { name: /status/i }) as HTMLSelectElement;
}

describe('PO list status filter (F23)', () => {
  it('offers exactly the words the badges use', async () => {
    renderSection();
    await screen.findByText('PO-D1');
    const labels = Array.from(statusFilter().options).map((o) => o.textContent);
    expect(labels).toEqual(['All Status', 'Draft', 'Sent', 'Partly received', 'Received', 'Cancelled']);
  });

  it('Sent finds the orders with the vendor (sent + acknowledged)', async () => {
    renderSection();
    await screen.findByText('PO-D1');
    fireEvent.change(statusFilter(), { target: { value: 'SENT' } });
    expect(screen.getByText('PO-S1')).toBeInTheDocument();
    expect(screen.getByText('PO-S2')).toBeInTheDocument();
    expect(screen.queryByText('PO-D1')).not.toBeInTheDocument();
    expect(screen.queryByText('PO-P1')).not.toBeInTheDocument();
  });

  it('Partly received finds the part-delivered orders', async () => {
    renderSection();
    await screen.findByText('PO-D1');
    fireEvent.change(statusFilter(), { target: { value: 'PARTIALLY_RECEIVED' } });
    expect(screen.getByText('PO-P1')).toBeInTheDocument();
    expect(screen.queryByText('PO-S1')).not.toBeInTheDocument();
  });
});

describe('Cancel an order with a reason (F4)', () => {
  it('sends the typed reason and shows the order as the server returns it', async () => {
    cancelPurchaseOrder.mockResolvedValue({
      po_id: 'po-S1',
      po: raw('S1', 'CANCELLED', { cancellation_reason: 'qty typo', cancelled_by: 'mgr_dhn2' }),
    });
    renderSection();
    const card = (await screen.findByText('PO-S1')).closest('.card') as HTMLElement;
    fireEvent.click(within(card).getByRole('button', { name: /view order/i }));
    fireEvent.click(await screen.findByRole('button', { name: /^cancel order$/i }));
    fireEvent.change(screen.getByLabelText(/why/i), { target: { value: 'qty typo' } });
    fireEvent.click(screen.getByRole('button', { name: /confirm cancel/i }));
    await waitFor(() => expect(cancelPurchaseOrder).toHaveBeenCalledWith('po-S1', 'qty typo'));
    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith('PO-S1 cancelled'));
    // The modal now shows the order as cancelled, with the reason.
    expect(screen.getAllByText(/qty typo/).length).toBeGreaterThan(0);
  });

  it('a refused cancel is toasted and nothing flips', async () => {
    cancelPurchaseOrder.mockRejectedValue(new Error('A delivery against this order is logged but not accepted yet (RCPT/0007).'));
    renderSection();
    const card = (await screen.findByText('PO-S1')).closest('.card') as HTMLElement;
    fireEvent.click(within(card).getByRole('button', { name: /view order/i }));
    fireEvent.click(await screen.findByRole('button', { name: /^cancel order$/i }));
    fireEvent.change(screen.getByLabelText(/why/i), { target: { value: 'changed mind' } });
    fireEvent.click(screen.getByRole('button', { name: /confirm cancel/i }));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledWith(
      'A delivery against this order is logged but not accepted yet (RCPT/0007).',
    ));
    expect(toastMock.success).not.toHaveBeenCalled();
  });

  it('cancelling one line sends the line position, product, quantity and reason', async () => {
    cancelPurchaseOrderLine.mockResolvedValue(
      raw('S1', 'CANCELLED', {
        items: [{ product_id: 'p1', product_name: 'Carrera CA8895', quantity: 0, ordered_qty: 0, cancelled_qty: 2, line_status: 'CANCELLED', unit_price: 1000, tax_rate: 5 }],
      }),
    );
    renderSection();
    const card = (await screen.findByText('PO-S1')).closest('.card') as HTMLElement;
    fireEvent.click(within(card).getByRole('button', { name: /view order/i }));
    const row = (await screen.findAllByText('Carrera CA8895')).map((el) => el.closest('tr')).find(Boolean)!;
    fireEvent.click(within(row).getByRole('button', { name: /cancel line/i }));
    fireEvent.change(screen.getByLabelText(/why/i), { target: { value: 'vendor discontinued' } });
    fireEvent.click(screen.getByRole('button', { name: /confirm cancel/i }));
    await waitFor(() =>
      expect(cancelPurchaseOrderLine).toHaveBeenCalledWith('po-S1', 0, 'vendor discontinued', 'p1', 2),
    );
  });
});

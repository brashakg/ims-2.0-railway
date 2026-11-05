// ============================================================================
// The delivery counter commits to ONE order at a time (audit SP02 / SP03)
// ============================================================================
// SP02: the collector name, handover note and the two advisory checks typed
// for order A rode into order B's handover when the counter switched orders.
// SP03: a slow lookup for A, answered after B's, replaced B on screen - the
// order handed over was not the one scanned last.

import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../PosWidgets', () => ({ PosWidgets: () => null }));
vi.mock('../../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u-staff-1', name: 'Meena', activeStoreId: 'BV-BOK-01', roles: ['STORE_MANAGER'] },
  }),
}));

const getOrder = vi.fn();
const getPendingDelivery = vi.fn();
const deliverWithPayment = vi.fn();
vi.mock('../../../../services/api/sales', () => ({
  orderApi: {
    getOrder: (...a: unknown[]) => getOrder(...a),
    getOrders: vi.fn().mockResolvedValue({ orders: [] }),
    getPendingDelivery: (...a: unknown[]) => getPendingDelivery(...a),
    addPayment: vi.fn().mockResolvedValue({}),
    deliverWithPayment: (...a: unknown[]) => deliverWithPayment(...a),
  },
}));
vi.mock('../../../../services/api', () => ({
  storeApi: { getStore: vi.fn().mockResolvedValue({ emi_annual_rate_percent: 12 }) },
  adminStoreApi: { getStoreUsers: vi.fn().mockResolvedValue({ users: [] }) },
}));
vi.mock('../DeliveryCompleteScreen', () => ({ default: () => <div>handed-over</div> }));
vi.mock('../../../../components/pos/BarcodeScanner', () => ({
  BarcodeScanner: ({ onScan }: { onScan: (v: string) => void }) => (
    <input
      aria-label="find"
      onKeyDown={(e) => {
        if (e.key === 'Enter') onScan((e.target as HTMLInputElement).value);
      }}
    />
  ),
}));

import { DeliverySurface } from '../DeliverySurface';
import { usePOSStore } from '../../../../stores/posStore';

const row = (id: string, over: Record<string, unknown> = {}) => ({
  id,
  orderNumber: `ORD-${id}`,
  customerName: `Customer ${id}`,
  customerPhone: '9876543210',
  orderStatus: 'READY',
  storeId: 'BV-BOK-01',
  grandTotal: 1000,
  amountPaid: 1000,
  balanceDue: 0,
  items: [{ productName: 'Frame', quantity: 1 }],
  ...over,
});

const scan = (code: string) => {
  const box = screen.getByLabelText('find') as HTMLInputElement;
  fireEvent.change(box, { target: { value: code } });
  fireEvent.keyDown(box, { key: 'Enter' });
};
const collector = () => screen.getByPlaceholderText(/Collected by/) as HTMLInputElement;
const note = () => screen.getByPlaceholderText(/Handover note/) as HTMLInputElement;
const checks = () => screen.getAllByRole('checkbox') as HTMLInputElement[];

const fillHandover = () => {
  fireEvent.change(collector(), { target: { value: 'PICKUP PERSON A' } });
  fireEvent.change(note(), { target: { value: 'ONLY FOR ORDER A' } });
  checks().forEach((c) => fireEvent.click(c));
};

beforeEach(() => {
  getOrder.mockReset().mockImplementation(async (id: string) => row(id));
  getPendingDelivery.mockReset().mockResolvedValue({ orders: [] });
  deliverWithPayment.mockReset().mockResolvedValue({});
  usePOSStore.getState().resetTransaction('discard');
});

describe('SP02 - handover details belong to one order', () => {
  it('clears collector, note and checks when a different order is loaded', async () => {
    render(<DeliverySurface />);
    scan('A');
    await waitFor(() => expect(screen.getByText('Customer A')).toBeTruthy());
    fillHandover();
    expect(collector().value).toBe('PICKUP PERSON A');

    scan('B');
    await waitFor(() => expect(screen.getByText('Customer B')).toBeTruthy());
    expect(collector().value).toBe('');
    expect(note().value).toBe('');
    expect(checks().map((c) => c.checked)).toEqual([false, false]);
  });

  it('keeps them on a retry of the SAME order after a refused handover', async () => {
    deliverWithPayment.mockRejectedValueOnce(new Error('QC gate'));
    render(<DeliverySurface />);
    scan('A');
    await waitFor(() => expect(screen.getByText('Customer A')).toBeTruthy());
    fillHandover();
    fireEvent.click(screen.getByRole('button', { name: /mark delivered/i }));
    await waitFor(() => expect(deliverWithPayment).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByText(/QC gate|Could not complete/)).toBeTruthy());

    expect(collector().value).toBe('PICKUP PERSON A');
    expect(note().value).toBe('ONLY FOR ORDER A');
    expect(checks().map((c) => c.checked)).toEqual([true, true]);
  });
});

describe('SP03 - only the latest lookup selects an order', () => {
  it('a slow answer for an earlier scan never replaces the later one', async () => {
    let releaseA: (v: unknown) => void = () => {};
    getOrder.mockImplementation((id: string) =>
      id === 'A' ? new Promise((r) => { releaseA = () => r(row('A')); }) : Promise.resolve(row('B')),
    );
    render(<DeliverySurface />);
    scan('A');
    scan('B');
    await waitFor(() => expect(screen.getByText('Customer B')).toBeTruthy());

    await act(async () => { releaseA(undefined); });
    expect(screen.getByText('Customer B')).toBeTruthy();
    expect(screen.queryByText('Customer A')).toBeNull();
  });

  it('a slow FAILURE for an earlier scan does not clear or error the later one', async () => {
    let failA: () => void = () => {};
    getOrder.mockImplementation((id: string) =>
      id === 'A'
        ? new Promise((_r, rej) => { failA = () => rej(new Error('404')); })
        : Promise.resolve(row('B')),
    );
    getPendingDelivery.mockResolvedValue({ orders: [] });
    render(<DeliverySurface />);
    scan('A');
    scan('B');
    await waitFor(() => expect(screen.getByText('Customer B')).toBeTruthy());

    await act(async () => { failA(); });
    await act(async () => {});
    expect(screen.getByText('Customer B')).toBeTruthy();
    expect(screen.queryByText(/Nothing awaiting collection/)).toBeNull();
  });

  it('a single scan still loads its order (positive control)', async () => {
    render(<DeliverySurface />);
    scan('B');
    await waitFor(() => expect(screen.getByText('Customer B')).toBeTruthy());
  });
});

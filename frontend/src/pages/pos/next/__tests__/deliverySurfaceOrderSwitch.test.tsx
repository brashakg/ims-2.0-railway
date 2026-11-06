// ============================================================================
// The delivery counter commits to ONE order at a time (audit SP02 / SP03)
// ============================================================================
// SP02: the collector name, handover note and the two advisory checks typed
// for order A rode into order B's handover when the counter switched orders.
// SP03: a slow lookup for A, answered after B's, replaced B on screen - the
// order handed over was not the one scanned last. The same defect had two more
// doors: the previous order stayed deliverable while the next lookup was still
// pending, and an old handover answer (or its resync) wrote over the newer
// selection - order A back on screen wearing order B details.

import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../PosWidgets', () => ({ PosWidgets: () => null }));
vi.mock('../../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u-staff-1', name: 'Meena', activeStoreId: 'BV-BOK-01', roles: ['STORE_MANAGER'] },
  }),
}));

const getOrder = vi.fn();
const addPayment = vi.fn();
const getPendingDelivery = vi.fn();
const deliverWithPayment = vi.fn();
vi.mock('../../../../services/api/sales', () => ({
  orderApi: {
    getOrder: (...a: unknown[]) => getOrder(...a),
    getOrders: vi.fn().mockResolvedValue({ orders: [] }),
    getPendingDelivery: (...a: unknown[]) => getPendingDelivery(...a),
    addPayment: (...a: unknown[]) => addPayment(...a),
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
  addPayment.mockReset().mockResolvedValue({});
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

/** A promise the test settles by hand: the response "held at the boundary". */
function held<T = unknown>() {
  let resolve: (v: T) => void = () => {};
  let reject: (e: unknown) => void = () => {};
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}
const deliverButton = () =>
  screen.getByRole('button', { name: /mark delivered|collect|working/i }) as HTMLButtonElement;
const load = async (id: string) => {
  scan(id);
  await waitFor(() => expect(screen.getByText(`Customer ${id}`)).toBeTruthy());
};
const addLeg = (method: string, amount: number) => {
  fireEvent.click(screen.getByRole('button', { name: method }));
  const inputs = Array.from(document.querySelectorAll('input[type="number"]')) as HTMLInputElement[];
  fireEvent.change(inputs[0], { target: { value: String(amount) } });
  fireEvent.click(screen.getByRole('button', { name: 'Add' }));
};

describe('SP03 - a pending lookup suspends the handover of the order still on screen', () => {
  it('Codex C/D: no delivery of C while the scan of D is unanswered', async () => {
    render(<DeliverySurface />);
    await load('C');
    fillHandover();

    const lookupD = held();
    getOrder.mockImplementation(() => lookupD.promise);
    scan('D');
    await waitFor(() => expect(deliverButton().disabled).toBe(true));
    expect(screen.getByText('Customer C')).toBeTruthy(); // still shown, no longer actionable
    fireEvent.click(deliverButton());
    expect(deliverWithPayment).not.toHaveBeenCalled();

    await act(async () => { lookupD.resolve(row('D')); });
    await waitFor(() => expect(screen.getByText('Customer D')).toBeTruthy());
    expect(collector().value).toBe('');
    expect(deliverButton().disabled).toBe(false);
    fireEvent.click(deliverButton());
    await waitFor(() => expect(deliverWithPayment).toHaveBeenCalledTimes(1));
    expect(deliverWithPayment.mock.calls[0][0]).toBe('D');
  });

  it('a latest lookup that FAILS leaves nothing actionable', async () => {
    render(<DeliverySurface />);
    await load('C');
    getOrder.mockRejectedValue(new Error('not an id'));
    getPendingDelivery.mockRejectedValue(new Error('network'));
    scan('D');
    await waitFor(() => expect(screen.getByText(/Could not load that order/)).toBeTruthy());
    expect(screen.queryByText('Customer C')).toBeNull();
    expect(deliverButton().disabled).toBe(true);
  });

  it('a latest lookup that finds NOTHING leaves nothing actionable', async () => {
    render(<DeliverySurface />);
    await load('C');
    getOrder.mockRejectedValue(new Error('not an id'));
    scan('NOPE');
    await waitFor(() => expect(screen.getByText(/Nothing awaiting collection/)).toBeTruthy());
    expect(deliverButton().disabled).toBe(true);
  });

  it('a deliberate pick from the match list is actionable straight away', async () => {
    getOrder.mockRejectedValue(new Error('not an id'));
    getPendingDelivery.mockResolvedValue({ orders: [row('M1'), row('M2')] });
    render(<DeliverySurface />);
    scan('sharma');
    await waitFor(() => expect(screen.getByText(/2 jobs waiting/)).toBeTruthy());
    expect(deliverButton().disabled).toBe(true);
    fireEvent.click(screen.getByText('Customer M2'));
    await waitFor(() => expect(deliverButton().disabled).toBe(false));
    fireEvent.click(deliverButton());
    await waitFor(() => expect(deliverWithPayment).toHaveBeenCalledTimes(1));
    expect(deliverWithPayment.mock.calls[0][0]).toBe('M2');
  });
});

describe('SP03 - an old handover answer never writes over a newer selection', () => {
  /** A's handover held in flight, then B selected and typed into. */
  const overlap = async (legAmount?: number) => {
    const deliverA = held();
    deliverWithPayment.mockImplementationOnce(() => deliverA.promise);
    render(<DeliverySurface />);
    await load('A');
    fillHandover();
    if (legAmount) addLeg('Cash', legAmount);
    fireEvent.click(deliverButton());
    await waitFor(() => expect(deliverWithPayment).toHaveBeenCalledTimes(1));
    await load('B');
    fireEvent.change(collector(), { target: { value: 'SYNTHETIC COLLECTOR B' } });
    fireEvent.change(note(), { target: { value: 'ONLY ORDER B' } });
    return deliverA;
  };
  const expectBIntact = () => {
    expect(screen.getByText('Customer B')).toBeTruthy();
    expect(screen.queryByText('Customer A')).toBeNull();
    expect(collector().value).toBe('SYNTHETIC COLLECTOR B');
    expect(note().value).toBe('ONLY ORDER B');
    expect(screen.queryByText('handed-over')).toBeNull();
  };

  it('Codex A/B: a delayed refusal of A keeps B, its details, and says it was A', async () => {
    const deliverA = await overlap();
    getOrder.mockClear();
    await act(async () => { deliverA.reject(new Error('QC gate')); });
    await waitFor(() => expect(getOrder).toHaveBeenCalledWith('A')); // the money check still runs
    await waitFor(() => expect(screen.getByText(/QC gate/)).toBeTruthy());
    expectBIntact();
    expect(screen.getByText(/ORD-A: QC gate/)).toBeTruthy();
  });

  it('a delayed refusal still reports money banked against A', async () => {
    getOrder.mockImplementation(async (id: string) =>
      row(id, id === 'A' ? { amountPaid: 0, balanceDue: 1000 } : {}),
    );
    const deliverA = await overlap(400);
    getOrder.mockImplementation(async (id: string) =>
      row(id, id === 'A' ? { amountPaid: 400, balanceDue: 600 } : {}),
    );
    await act(async () => { deliverA.reject(new Error('QC gate')); });
    await waitFor(() => expect(screen.getByText(/ORD-A: QC gate ₹400 was recorded/)).toBeTruthy());
    expectBIntact();
  });

  it('a held resync of A answered after B was selected is dropped too', async () => {
    const deliverA = held();
    deliverWithPayment.mockImplementationOnce(() => deliverA.promise);
    render(<DeliverySurface />);
    await load('A');
    fireEvent.click(deliverButton());
    await waitFor(() => expect(deliverWithPayment).toHaveBeenCalledTimes(1));

    const resyncA = held();
    getOrder.mockImplementationOnce(() => resyncA.promise);
    await act(async () => { deliverA.reject(new Error('QC gate')); });
    await load('B');
    fireEvent.change(collector(), { target: { value: 'SYNTHETIC COLLECTOR B' } });
    fireEvent.change(note(), { target: { value: 'ONLY ORDER B' } });
    await act(async () => { resyncA.resolve(row('A')); });
    expectBIntact();
  });

  it('a delayed refusal whose resync also fails still leaves B alone', async () => {
    const deliverA = await overlap();
    getOrder.mockRejectedValue(new Error('network'));
    await act(async () => { deliverA.reject(new Error('QC gate')); });
    await waitFor(() => expect(screen.getByText(/QC gate/)).toBeTruthy());
    expectBIntact();
    expect(screen.getByText(/ORD-A: QC gate/)).toBeTruthy();
  });

  it('a delayed SUCCESS of A reports A and leaves B on the counter', async () => {
    const deliverA = await overlap();
    await act(async () => { deliverA.resolve({}); });
    await waitFor(() => expect(screen.getByText(/Delivered — ORD-A/)).toBeTruthy());
    expectBIntact();
    // B is a normal handover from here, with B's own details.
    fireEvent.click(deliverButton());
    await waitFor(() => expect(deliverWithPayment).toHaveBeenCalledTimes(2));
    expect(deliverWithPayment.mock.calls[1][0]).toBe('B');
    expect(deliverWithPayment.mock.calls[1][1].handover.picked_up_by_name).toBe('SYNTHETIC COLLECTOR B');
  });

  it('a split stops at the next write once another job card is scanned', async () => {
    getOrder.mockImplementation(async (id: string) =>
      row(id, id === 'A' ? { amountPaid: 0, balanceDue: 1000 } : {}),
    );
    const firstLeg = held();
    addPayment.mockImplementationOnce(() => firstLeg.promise);
    render(<DeliverySurface />);
    await load('A');
    addLeg('Cash', 400);
    addLeg('Cash', 600);
    fireEvent.click(deliverButton());
    await waitFor(() => expect(addPayment).toHaveBeenCalledTimes(1));
    await load('B');

    getOrder.mockImplementation(async (id: string) =>
      row(id, id === 'A' ? { amountPaid: 400, balanceDue: 600 } : {}),
    );
    await act(async () => { firstLeg.resolve({}); });
    await waitFor(() => expect(screen.getByText(/ORD-A: .*₹400 was recorded/)).toBeTruthy());
    expect(deliverWithPayment).not.toHaveBeenCalled(); // A is not delivered behind B's back
    expect(screen.getByText('Customer B')).toBeTruthy();
  });

  it('a rescan of the SAME order still gets the refusal resync (money stays honest)', async () => {
    getOrder.mockImplementation(async (id: string) => row(id, { amountPaid: 0, balanceDue: 1000 }));
    const deliverA = held();
    deliverWithPayment.mockImplementationOnce(() => deliverA.promise);
    render(<DeliverySurface />);
    await load('A');
    addLeg('Cash', 400);
    fireEvent.click(deliverButton());
    await waitFor(() => expect(deliverWithPayment).toHaveBeenCalledTimes(1));

    scan('A'); // same job card again, read before the money lands
    await act(async () => {});
    getOrder.mockImplementation(async (id: string) => row(id, { amountPaid: 400, balanceDue: 600 }));
    await act(async () => { deliverA.reject(new Error('QC gate')); });
    await waitFor(() => expect(screen.getByText('₹600', { selector: '.text-3xl' })).toBeTruthy());
    expect(screen.getByText('Customer A')).toBeTruthy();
  });

  it('a refusal of the on-screen order while a lookup is pending resyncs it, then the lookup wins', async () => {
    const deliverA = held();
    deliverWithPayment.mockImplementationOnce(() => deliverA.promise);
    render(<DeliverySurface />);
    await load('A');
    fillHandover();
    fireEvent.click(deliverButton());
    await waitFor(() => expect(deliverWithPayment).toHaveBeenCalledTimes(1));

    const lookupB = held();
    getOrder.mockImplementationOnce(() => lookupB.promise);
    scan('B');
    await act(async () => { deliverA.reject(new Error('QC gate')); });
    await waitFor(() => expect(screen.getByText(/ORD-A: QC gate/)).toBeTruthy());
    expect(screen.getByText('Customer A')).toBeTruthy();
    expect(deliverButton().disabled).toBe(true); // busy is over; the pending scan still holds it

    await act(async () => { lookupB.resolve(row('B')); });
    await waitFor(() => expect(screen.getByText('Customer B')).toBeTruthy());
    expect(collector().value).toBe('');
    expect(deliverButton().disabled).toBe(false);
  });

  it('positive control: an ordinary handover still reaches the completion screen', async () => {
    render(<DeliverySurface />);
    await load('A');
    fireEvent.click(deliverButton());
    await waitFor(() => expect(screen.getByText('handed-over')).toBeTruthy());
  });
});

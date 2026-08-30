// ============================================================================
// THE CASH DRAWER: closing without a count must not record an emptied till
// ============================================================================
// This is the screen the shops actually close their day on. It used to send
// `closing_cash: closingCash ? parseFloat(closingCash) : 0` -- so a cashier who
// hit "Confirm Day Closing" without typing a count persisted "Rs 0.00 in the
// drawer" and a variance of minus the entire day's cash, stamped with their
// name and audit-logged as a WARNING. On the record it is indistinguishable
// from a till someone had emptied.
//
// Asserted here against the REAL page, on the payload that leaves the browser
// and on what the operator is shown while deciding:
//   * nothing counted  -> no closing_cash at all, no count block, "Not counted"
//   * a real count     -> the amount AND the notes behind it, per face
//   * either way the close goes through -- no cash entry may block a close.

import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

// vi.mock is hoisted, so the spy has to be created inside the factory and
// pulled back out afterwards.
vi.mock('../../../services/api', () => ({
  orderApi: {
    getOrders: vi.fn(async () => ({
      orders: [
        {
          orderId: 'ORD-1',
          orderStatus: 'COMPLETED',
          grandTotal: 1600,
          items: [{ productName: 'Frame', quantity: 1, finalPrice: 1600 }],
          payments: [{ method: 'CASH', amount: 1600 }],
        },
      ],
    })),
  },
  reportsApi: {
    getDayEndClose: vi.fn(async () => ({ closed: false, close: null })),
    createDayEndClose: vi.fn(),
  },
}));

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { activeStoreId: 'BV-PUN-01', storeIds: ['BV-PUN-01'] } }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));
vi.mock('../../../hooks/useStorePrintInfo', () => ({
  useStorePrintInfo: () => ({ name: 'Better Vision', address: '', phone: '', gstin: '' }),
}));
vi.mock('../../../hooks/useIsOnlineStore', () => ({ useIsOnlineStore: () => false }));
vi.mock('../../../utils/datetime', () => ({ istDayString: () => null }));

import { orderApi, reportsApi } from '../../../services/api';
import DayEndReport from '../DayEndReport';

const createDayEndClose = reportsApi.createDayEndClose as unknown as ReturnType<typeof vi.fn>;
const getDayEndClose = reportsApi.getDayEndClose as unknown as ReturnType<typeof vi.fn>;
const getOrders = orderApi.getOrders as unknown as ReturnType<typeof vi.fn>;

// vitest.config sets `restoreMocks`, which strips a vi.fn implementation before
// EVERY test -- so the doubles are re-installed here rather than once at import.
beforeEach(() => {
  getOrders.mockImplementation(async () => ({
    orders: [
      {
        orderId: 'ORD-1',
        orderStatus: 'COMPLETED',
        grandTotal: 1600,
        items: [{ productName: 'Frame', quantity: 1, finalPrice: 1600 }],
        payments: [{ method: 'CASH', amount: 1600 }],
      },
    ],
  }));
  getDayEndClose.mockImplementation(async () => ({ closed: false, close: null }));
  createDayEndClose.mockImplementation(async (body: Record<string, unknown>) => ({
    closed: true,
    store_id: 'BV-PUN-01',
    date: body.date,
    close: {
      store_id: 'BV-PUN-01',
      date: body.date,
      closing_cash: (body.closing_cash as number | undefined) ?? null,
      system_cash: (body.system_cash as number | undefined) ?? null,
      variance: body.closing_cash == null ? null : 0,
      notes: null,
      closed_by: 'u-1',
      closed_at: '2026-08-24T15:00:00',
    },
  }));
});

async function renderReport() {
  const view = render(<DayEndReport />);
  await waitFor(() => expect(screen.getByText('Close Day')).toBeInTheDocument());
  return view;
}

function closeDay() {
  fireEvent.click(screen.getByRole('button', { name: /Confirm Day Closing/i }));
}

describe('closing without counting the drawer', () => {
  it('sends NO closing cash and NO count -- never a zero', async () => {
    await renderReport();
    closeDay();

    await waitFor(() => expect(createDayEndClose).toHaveBeenCalledTimes(1));
    const body = createDayEndClose.mock.calls[0][0];
    // The defect: `closing_cash: 0`. Absence must travel as absence.
    expect(body.closing_cash).toBeUndefined();
    expect(body.closing_cash).not.toBe(0);
    expect(body.closing_count).toBeUndefined();
    // The day still closed. No cash entry may block a close.
    expect(body.date).toBeTruthy();
  });

  it('shows the operator a blind variance box, never a fabricated shortfall', async () => {
    await renderReport();
    // Rs 1,600 of cash sales with nothing counted used to read as "-Rs 1,600
    // cash short" before the button was even pressed. Since the 2026-08-25
    // ruling (blind is THE day-end) the pending-day variance box is BLIND:
    // it says the variance reveals at close, and the system-cash figure is
    // hidden so the count cannot be anchored to it.
    expect(screen.getByText(/Revealed when the day closes/i)).toBeInTheDocument();
    expect(screen.queryByText('Cash short')).not.toBeInTheDocument();
    // The system-cash target itself is masked while counting: the CASH tile
    // and the "System Cash (from POS)" figure both read Hidden (the non-cash
    // tiles, which cannot anchor a drawer count, stay visible).
    expect(screen.getAllByText('Hidden')).toHaveLength(2);
  });
});

describe('counting the drawer note by note', () => {
  it('sends the amount AND the faces behind it', async () => {
    await renderReport();
    fireEvent.click(screen.getByRole('button', { name: /Count the notes and coins/i }));
    // Three Rs 500 notes and one Rs 100 note = Rs 1,600.
    fireEvent.change(screen.getByLabelText('note of 500 rupees, pieces'), {
      target: { value: '3' },
    });
    fireEvent.change(screen.getByLabelText('note of 100 rupees, pieces'), {
      target: { value: '1' },
    });
    closeDay();

    await waitFor(() => expect(createDayEndClose).toHaveBeenCalledTimes(1));
    const body = createDayEndClose.mock.calls[0][0];
    // The grid IS the count when no figure was typed -- it is what is in the
    // drawer -- and it is a real count, not a machine guess.
    expect(body.closing_cash).toBe(1600);
    expect(body.closing_count.state).toBe('COUNTED');
    // ASSERT THE SET AND THE COUNT: only the faces that were actually there.
    expect(body.closing_count.rows).toHaveLength(2);
    expect(
      new Set(
        body.closing_count.rows.map(
          (r: { kind: string; face: number; pieces: number }) =>
            `${r.kind}-${r.face}-${r.pieces}`,
        ),
      ),
    ).toEqual(new Set(['note-500-3', 'note-100-1']));
  });

  it('a typed figure that disagrees with the notes is still the amount, and the notes still travel', async () => {
    await renderReport();
    fireEvent.click(screen.getByRole('button', { name: /Count the notes and coins/i }));
    fireEvent.change(screen.getByLabelText('note of 500 rupees, pieces'), {
      target: { value: '3' },
    });
    // The manager overrides with what they believe is really there.
    fireEvent.change(screen.getByPlaceholderText(/₹1,500|Enter actual cash/), {
      target: { value: '1400' },
    });
    closeDay();

    await waitFor(() => expect(createDayEndClose).toHaveBeenCalledTimes(1));
    const body = createDayEndClose.mock.calls[0][0];
    // THE AMOUNT IS THE MONEY. The breakdown rides alongside and is not
    // silently corrected to match, nor does it correct the amount.
    expect(body.closing_cash).toBe(1400);
    expect(body.closing_count.rows).toEqual([
      expect.objectContaining({ face: 500, kind: 'note', pieces: 3 }),
    ]);
  });
});

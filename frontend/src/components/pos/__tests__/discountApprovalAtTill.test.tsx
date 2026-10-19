// A discount over the seller's own limit, at the till (owner 2026-10-18).
//
// WHO approved a discount is the server's decision: the till never sends a
// typed approver, and never writes the approval request either. Over the
// limit, order create refuses with DISCOUNT_NEEDS_APPROVAL; "Ask a manager to
// approve" re-sends THIS bill with request_approval and the SERVER raises the
// request from its own pricing. Once a manager approves, the same sale is
// resubmitted with the request id -- unless the bill changed meanwhile.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u-staff', name: 'Priya Staff', discountCap: 10, roles: ['SALES_STAFF'] } }),
}));
vi.mock('../../../services/api', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    orderApi: {
      ...(actual.orderApi as object),
      quoteBill: vi.fn(),
      createOrder: vi.fn(),
      addPayment: vi.fn(),
    },
  };
});
vi.mock('../../../services/api/approvals', () => ({
  approvalsApi: { createRequest: vi.fn(), getRequest: vi.fn() },
}));

import type { AxiosError } from 'axios';
import { orderApi } from '../../../services/api';
import { buildApiError } from '../../../services/api/client';
import { approvalsApi } from '../../../services/api/approvals';
import { usePOSStore } from '../../../stores/posStore';
import { submitPosOrder, askManagerForDiscount } from '../submitOrder';
import {
  DiscountApprovalPanel, APPROVAL_POLL_MS, BILL_CHANGED_MESSAGE, type DiscountApprovalNeed,
} from '../DiscountApprovalPanel';

const NEED: DiscountApprovalNeed = {
  code: 'DISCOUNT_NEEDS_APPROVAL',
  message: 'The discount on Frame comes to 18.00% (item and bill discounts together), which exceeds your limit of 10%. A manager must approve it.',
  requested_percent: 18,
  max_percent: 10,
  zero_total: false,
};

/** A refusal exactly as the API client delivers it (the real transform). */
function refusal(status: number, code: string, detail: Record<string, unknown> = {}) {
  return buildApiError({
    message: `Request failed with status code ${status}`,
    response: { status, data: { detail: { message: `refused: ${code}`, ...detail, code } } },
  } as unknown as AxiosError<{ detail?: string }>);
}

function cartAt18() {
  const s = usePOSStore.getState();
  s.resetTransaction();
  s.addToCart({
    product_id: 'p-frame', name: 'Frame', sku: 'S', category: 'FRAME',
    unit_price: 1000, mrp: 1000, quantity: 1, is_optical: false,
  } as never);
  const st = usePOSStore.getState();
  st.applyDiscount(st.cart[0].id, 18, 'price match');
  usePOSStore.getState().addPayment({ method: 'CASH', amount: 820 } as never);
}

const createOrder = () => vi.mocked(orderApi.createOrder);

describe('submitPosOrder and the discount approver', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(orderApi.quoteBill).mockResolvedValue({
      subtotal: 1000, taxable: 780.95, tax: 39.05, total_discount: 180,
      grand_total: 820, round_off: 0,
    } as never);
    vi.mocked(orderApi.addPayment).mockResolvedValue({} as never);
  });

  it('never sends a typed approver, and sends the approval id on the retry', async () => {
    cartAt18();
    createOrder().mockResolvedValue({ order_id: 'o1', order_number: 'BV-1' } as never);
    await submitPosOrder(usePOSStore.getState(), 'idem-1', 'REQ-abc');
    const body = createOrder().mock.calls[0][0] as Record<string, unknown>;
    expect(body).not.toHaveProperty('cart_discount_approved_by');
    expect(body.discount_approval_id).toBe('REQ-abc');
    for (const line of body.items as Record<string, unknown>[]) {
      expect(line).not.toHaveProperty('discount_approved_by');
    }
  });

  it('hands DISCOUNT_NEEDS_APPROVAL back as an actionable refusal', async () => {
    cartAt18();
    createOrder().mockRejectedValue(refusal(403, 'DISCOUNT_NEEDS_APPROVAL', { ...NEED }));
    const res = await submitPosOrder(usePOSStore.getState(), 'idem-2');
    expect(res.ok).toBe(false);
    expect(res.discountApproval?.requested_percent).toBe(18);
    expect(res.error).toMatch(/exceeds your limit of 10%/);
  });

  it.each([
    [409, 'DISCOUNT_APPROVAL_USED'],
    [410, 'DISCOUNT_APPROVAL_EXPIRED'],
    [404, 'DISCOUNT_APPROVAL_NOT_FOUND'],
    [403, 'DISCOUNT_APPROVAL_INVALID'],
  ])('drops an approval the server refuses with %s %s', async (status, code) => {
    cartAt18();
    createOrder().mockRejectedValue(refusal(status, code));
    const res = await submitPosOrder(usePOSStore.getState(), 'idem-3', 'REQ-dead');
    expect(res.ok).toBe(false);
    expect(res.discountApprovalRejected).toBe(true);
  });

  it('keeps the approval when the approvals store is only unavailable', async () => {
    cartAt18();
    createOrder().mockRejectedValue(refusal(503, 'DISCOUNT_APPROVAL_UNAVAILABLE'));
    const res = await submitPosOrder(usePOSStore.getState(), 'idem-4', 'REQ-live');
    expect(res.ok).toBe(false);
    expect(res.discountApprovalRejected).toBeUndefined();
  });

  it('asks by re-sending THIS bill; the till writes no request of its own', async () => {
    cartAt18();
    createOrder().mockResolvedValue({ code: 'DISCOUNT_APPROVAL_REQUESTED', request_id: 'REQ-7', status: 'REQUESTED' } as never);
    const res = await askManagerForDiscount(usePOSStore.getState(), ' regular customer ');
    expect(res).toEqual({ requestId: 'REQ-7', status: 'REQUESTED' });
    const body = createOrder().mock.calls[0][0] as Record<string, unknown>;
    expect(body.request_approval).toBe(true);
    expect(body.approval_note).toBe('regular customer');
    expect((body.items as unknown[]).length).toBe(1);
    expect(body).not.toHaveProperty('action_type');
    expect(body).not.toHaveProperty('context');
    expect(createOrder().mock.calls[0][1]).toBeUndefined(); // no idempotency key: never a sale
    expect(approvalsApi.createRequest).not.toHaveBeenCalled();
  });
});

describe('DiscountApprovalPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
    cartAt18();
  });
  afterEach(() => vi.useRealTimers());

  async function ask(note?: string) {
    if (note) fireEvent.change(screen.getByLabelText('Note for the manager'), { target: { value: note } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Ask a manager to approve' }));
    });
  }
  const tick = () => act(async () => { await vi.advanceTimersByTimeAsync(APPROVAL_POLL_MS); });

  it('asks through the server, waits for the manager, then hands back the request id', async () => {
    createOrder().mockResolvedValue({ request_id: 'REQ-1', status: 'REQUESTED' } as never);
    vi.mocked(approvalsApi.getRequest)
      .mockResolvedValueOnce({ request_id: 'REQ-1', status: 'REQUESTED' } as never)
      .mockResolvedValueOnce({ request_id: 'REQ-1', status: 'APPROVED' } as never);
    const onApproved = vi.fn();
    render(<DiscountApprovalPanel need={NEED} onApproved={onApproved} onClose={vi.fn()} />);

    await ask('regular customer');
    expect((createOrder().mock.calls[0][0] as Record<string, unknown>).approval_note).toBe('regular customer');
    expect(screen.getByText(/Waiting for a manager/)).toBeTruthy();
    await tick();
    expect(onApproved).not.toHaveBeenCalled();
    await tick();
    expect(onApproved).toHaveBeenCalledWith('REQ-1');
  });

  it('does NOT complete a bill that changed after asking -- it says ask again', async () => {
    createOrder().mockResolvedValue({ request_id: 'REQ-2', status: 'REQUESTED' } as never);
    vi.mocked(approvalsApi.getRequest).mockResolvedValue({ request_id: 'REQ-2', status: 'APPROVED' } as never);
    const onApproved = vi.fn();
    render(<DiscountApprovalPanel need={NEED} onApproved={onApproved} onClose={vi.fn()} />);
    await ask();
    // The cashier adds a second frame while the manager is deciding.
    act(() => {
      usePOSStore.getState().updateQuantity(usePOSStore.getState().cart[0].id, 2);
    });
    await tick();
    expect(onApproved).not.toHaveBeenCalled();
    expect(screen.getByText(BILL_CHANGED_MESSAGE)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Ask a manager to approve' })).toBeTruthy();
  });

  it('uses an already-approved request for the same bill straight away', async () => {
    createOrder().mockResolvedValue({ request_id: 'REQ-3', status: 'APPROVED' } as never);
    const onApproved = vi.fn();
    render(<DiscountApprovalPanel need={NEED} onApproved={onApproved} onClose={vi.fn()} />);
    await ask();
    expect(onApproved).toHaveBeenCalledWith('REQ-3');
  });

  it('says so when the manager rejects, and lets the cashier ask again', async () => {
    createOrder().mockResolvedValue({ request_id: 'REQ-4', status: 'REQUESTED' } as never);
    vi.mocked(approvalsApi.getRequest).mockResolvedValue({
      request_id: 'REQ-4', status: 'REJECTED', reject_reason: 'too much',
    } as never);
    const onApproved = vi.fn();
    render(<DiscountApprovalPanel need={NEED} onApproved={onApproved} onClose={vi.fn()} />);
    await ask();
    await tick();
    expect(onApproved).not.toHaveBeenCalled();
    expect(screen.getByText(/did not approve it: too much/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Ask a manager to approve' })).toBeTruthy();
  });
});

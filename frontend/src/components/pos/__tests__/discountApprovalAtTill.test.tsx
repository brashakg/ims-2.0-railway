// A discount over the seller's own limit, at the till (owner 2026-10-18).
//
// WHO approved a discount is the server's decision: the till never sends a
// typed approver. Over the limit, order create refuses with
// DISCOUNT_NEEDS_APPROVAL; the till asks a manager through the E4 approvals
// flow and resubmits the same sale with the approved request's id.
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
import { submitPosOrder } from '../submitOrder';
import { DiscountApprovalPanel, APPROVAL_POLL_MS, type DiscountApprovalNeed } from '../DiscountApprovalPanel';

const NEED: DiscountApprovalNeed = {
  code: 'DISCOUNT_NEEDS_APPROVAL',
  message: 'The discount on Frame comes to 18.00% ..., which exceeds your limit of 10%. A manager must approve it.',
  requested_percent: 18,
  max_percent: 10,
  zero_total: false,
  approval_request: {
    action_type: 'discount_override',
    store_id: 'BV-TEST-01',
    required_tier: 'auto',
    context: { discount_percent: 18, zero_total: false, product_ids: 'p-frame' },
  },
};

/** The refusal exactly as the API client delivers it (the real transform). */
function refusal(code: string, detail: Record<string, unknown>) {
  return buildApiError({
    message: 'Request failed with status code 403',
    response: { status: 403, data: { detail: { ...detail, code } } },
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
    vi.mocked(orderApi.createOrder).mockResolvedValue({ order_id: 'o1', order_number: 'BV-1' } as never);
    await submitPosOrder(usePOSStore.getState(), 'idem-1', 'REQ-abc');
    const body = vi.mocked(orderApi.createOrder).mock.calls[0][0] as Record<string, unknown>;
    expect(body).not.toHaveProperty('cart_discount_approved_by');
    expect(body.discount_approval_id).toBe('REQ-abc');
    for (const line of body.items as Record<string, unknown>[]) {
      expect(line).not.toHaveProperty('discount_approved_by');
    }
  });

  it('hands DISCOUNT_NEEDS_APPROVAL back as an actionable refusal', async () => {
    cartAt18();
    vi.mocked(orderApi.createOrder).mockRejectedValue(
      refusal('DISCOUNT_NEEDS_APPROVAL', { ...NEED }),
    );
    const res = await submitPosOrder(usePOSStore.getState(), 'idem-2');
    expect(res.ok).toBe(false);
    expect(res.discountApproval?.approval_request.context.discount_percent).toBe(18);
    expect(res.error).toMatch(/exceeds your limit of 10%/);
  });

  it('marks an approval the server would not take, so the till drops it', async () => {
    cartAt18();
    vi.mocked(orderApi.createOrder).mockRejectedValue(
      refusal('DISCOUNT_APPROVAL_INVALID', {
        message: 'That manager approval cannot be used: it has already been used on a sale. Ask a manager again.',
      }),
    );
    const res = await submitPosOrder(usePOSStore.getState(), 'idem-3', 'REQ-used');
    expect(res.ok).toBe(false);
    expect(res.discountApprovalRejected).toBe(true);
    expect(res.discountApproval).toBeUndefined();
  });
});

describe('DiscountApprovalPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
    cartAt18();
  });
  afterEach(() => vi.useRealTimers());

  it('asks through E4, waits for the manager, then hands back the request id', async () => {
    vi.mocked(approvalsApi.createRequest).mockResolvedValue({
      ok: true, request_id: 'REQ-1', status: 'REQUESTED',
    } as never);
    vi.mocked(approvalsApi.getRequest)
      .mockResolvedValueOnce({ request_id: 'REQ-1', status: 'REQUESTED' } as never)
      .mockResolvedValueOnce({ request_id: 'REQ-1', status: 'APPROVED' } as never);
    const onApproved = vi.fn();
    render(<DiscountApprovalPanel need={NEED} onApproved={onApproved} onClose={vi.fn()} />);

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Ask a manager to approve' }));
    });
    const sent = vi.mocked(approvalsApi.createRequest).mock.calls[0][0];
    // Exactly the request the server said would cover this bill.
    expect(sent.action_type).toBe('discount_override');
    expect(sent.store_id).toBe('BV-TEST-01');
    expect(sent.context).toEqual({ ...NEED.approval_request.context, requested_by_name: 'Priya Staff' });
    expect(sent.reason).toBe('price match');
    expect(screen.getByText(/Waiting for a manager/)).toBeTruthy();

    await act(async () => { await vi.advanceTimersByTimeAsync(APPROVAL_POLL_MS); });
    expect(onApproved).not.toHaveBeenCalled();
    await act(async () => { await vi.advanceTimersByTimeAsync(APPROVAL_POLL_MS); });
    expect(onApproved).toHaveBeenCalledWith('REQ-1');
  });

  it('says so when the manager rejects, and lets the cashier ask again', async () => {
    vi.mocked(approvalsApi.createRequest).mockResolvedValue({ ok: true, request_id: 'REQ-2', status: 'REQUESTED' } as never);
    vi.mocked(approvalsApi.getRequest).mockResolvedValue({
      request_id: 'REQ-2', status: 'REJECTED', reject_reason: 'too much',
    } as never);
    const onApproved = vi.fn();
    render(<DiscountApprovalPanel need={NEED} onApproved={onApproved} onClose={vi.fn()} />);
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Ask a manager to approve' }));
    });
    await act(async () => { await vi.advanceTimersByTimeAsync(APPROVAL_POLL_MS); });
    expect(onApproved).not.toHaveBeenCalled();
    expect(screen.getByText(/did not approve it: too much/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Ask a manager to approve' })).toBeTruthy();
  });
});

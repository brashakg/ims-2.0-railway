// ============================================================================
// IMS 2.0 - PO detail modal: the actions a person can actually take
// ============================================================================
// Owner rulings 2026-09-28 (procurement audit F3 / F4 / F20):
//   * there is NO approval step -- a draft is SENT to the vendor, so the button
//     says "Send to vendor" (never "Submit for Approval"),
//   * a DRAFT can be edited,
//   * a Draft or Sent order -- or one line of it -- can be cancelled, and only
//     WITH A REASON; a part-received order cancels only what is still due,
//   * nothing left to cancel on a received or cancelled order.
// P0-4 still holds: the old Approve / Mark-as-Ordered / Mark-as-Received
// theater buttons stay gone.

import { useState } from 'react';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi } from 'vitest';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { activeStoreId: 'BV-BOK-01' } }),
}));

vi.mock('../../../components/print/storeIdentity', () => ({
  resolveStoreIdentity: vi.fn().mockResolvedValue(null),
}));

import { PurchaseOrderDetail } from '../PurchaseOrderDetail';
import type { PurchaseOrder } from '../purchaseTypes';
import { mapPOtoPurchaseOrder } from '../purchaseMappers';

const po = (status: string, over: Partial<PurchaseOrder> = {}) =>
  ({
    id: 'PO1',
    poNumber: 'PO-2026-0042',
    supplierId: 'V1',
    supplierName: 'Essilor India',
    date: '2026-08-01',
    expectedDelivery: '2026-08-10',
    status,
    items: [
      { productId: 'p1', productName: 'Ray-Ban RB2140', sku: 'RB-2140', quantity: 2, unitCost: 1000, taxRate: 18, total: 2000, receivedQty: 0 },
      { productId: 'p2', productName: 'Carrera CA8895', sku: 'CA-8895', quantity: 3, unitCost: 500, taxRate: 5, total: 1500, receivedQty: 0 },
    ],
    subtotal: 3500,
    taxAmount: 435,
    total: 3935,
    ...over,
  }) as unknown as PurchaseOrder;

function show(p: PurchaseOrder, onAction = vi.fn()) {
  render(<PurchaseOrderDetail po={p} onClose={vi.fn()} onAction={onAction} />);
  return onAction;
}

describe('PO detail modal - one honest word: Send to vendor (F20)', () => {
  it('DRAFT offers "Send to vendor", never an approval step', async () => {
    const onAction = show(po('DRAFT'));
    expect(screen.queryByRole('button', { name: /submit for approval/i })).not.toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole('button', { name: /send to vendor/i }));
    expect(onAction).toHaveBeenCalledWith(expect.objectContaining({ id: 'PO1' }), 'send');
  });

  it('once sent, the send step offers Print PO right there (owner 2026-09-29)', async () => {
    function Sending() {
      const [cur, setCur] = useState(po('DRAFT'));
      return (
        <PurchaseOrderDetail
          po={cur}
          onClose={vi.fn()}
          onAction={async () => setCur(po('SENT'))}
        />
      );
    }
    render(<Sending />);
    expect(screen.queryByRole('button', { name: /print po now/i })).not.toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole('button', { name: /send to vendor/i }));
    expect(await screen.findByRole('status')).toHaveTextContent(/sent to essilor india/i);
    expect(screen.getByRole('button', { name: /print po now/i })).toBeInTheDocument();
  });

  it('a refused send offers no print prompt (it is still a draft)', async () => {
    show(po('DRAFT'), vi.fn().mockResolvedValue(undefined));
    await userEvent.setup().click(screen.getByRole('button', { name: /send to vendor/i }));
    expect(screen.queryByRole('button', { name: /print po now/i })).not.toBeInTheDocument();
  });

  it('the theater buttons stay gone (P0-4)', () => {
    show(po('SENT'));
    expect(screen.queryByRole('button', { name: /^approve$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /mark as ordered/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /mark as received/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /reject/i })).not.toBeInTheDocument();
  });
});

describe('PO detail modal - edit a draft (F3)', () => {
  it('DRAFT has Edit', async () => {
    const onAction = show(po('DRAFT'));
    await userEvent.setup().click(screen.getByRole('button', { name: /^edit/i }));
    expect(onAction).toHaveBeenCalledWith(expect.objectContaining({ id: 'PO1' }), 'edit');
  });

  it.each(['SENT', 'PARTIALLY_RECEIVED', 'RECEIVED', 'CANCELLED'])(
    '%s is not editable', (status) => {
      show(po(status));
      expect(screen.queryByRole('button', { name: /^edit/i })).not.toBeInTheDocument();
    },
  );
});

describe('PO detail modal - cancel with a reason (F4)', () => {
  it.each(['DRAFT', 'SENT'])('%s: Cancel order needs a reason, then fires', async (status) => {
    const onAction = show(po(status));
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /^cancel order$/i }));
    const confirm = screen.getByRole('button', { name: /confirm cancel/i });
    expect(confirm).toBeDisabled();
    await user.type(screen.getByLabelText(/why/i), 'qty typo');
    expect(confirm).toBeEnabled();
    await user.click(confirm);
    expect(onAction).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'PO1' }),
      'cancel',
      { reason: 'qty typo' },
    );
  });

  it('a part-received order cancels only what is still due', () => {
    show(po('PARTIALLY_RECEIVED'));
    expect(screen.getByRole('button', { name: /cancel what is still due/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^cancel order$/i })).not.toBeInTheDocument();
  });

  it.each(['RECEIVED', 'CANCELLED'])('%s has nothing left to cancel', (status) => {
    show(po(status));
    expect(screen.queryByRole('button', { name: /cancel order|still due|cancel line/i })).not.toBeInTheDocument();
  });

  it('SENT: one line can be cancelled, with a reason', async () => {
    const onAction = show(po('SENT'));
    const user = userEvent.setup();
    const row = screen.getByText('Carrera CA8895').closest('tr')!;
    await user.click(within(row).getByRole('button', { name: /cancel line/i }));
    await user.type(screen.getByLabelText(/why/i), 'vendor discontinued');
    await user.click(screen.getByRole('button', { name: /confirm cancel/i }));
    expect(onAction).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'PO1' }),
      'cancel-line',
      { reason: 'vendor discontinued', lineIndex: 1 },
    );
  });

  it('a fully received line offers no line cancel', () => {
    show(
      po('PARTIALLY_RECEIVED', {
        items: [
          { productId: 'p1', productName: 'Ray-Ban RB2140', sku: 'RB', quantity: 2, unitCost: 1000, taxRate: 18, total: 2000, receivedQty: 2 },
          { productId: 'p2', productName: 'Carrera CA8895', sku: 'CA', quantity: 3, unitCost: 500, taxRate: 5, total: 1500, receivedQty: 1 },
        ],
      } as Partial<PurchaseOrder>),
    );
    const done = screen.getByText('Ray-Ban RB2140').closest('tr')!;
    expect(within(done).queryByRole('button', { name: /cancel line/i })).not.toBeInTheDocument();
    const due = screen.getByText('Carrera CA8895').closest('tr')!;
    expect(within(due).getByRole('button', { name: /cancel line/i })).toBeInTheDocument();
  });

  it('a cancelled order says why', () => {
    show(po('CANCELLED', { cancellationReason: 'qty typo' } as Partial<PurchaseOrder>));
    expect(screen.getByText(/qty typo/)).toBeInTheDocument();
  });
});

describe('PO detail modal - no approval wording anywhere (owner 2026-09-28)', () => {
  it('an older order that still carries approved_by shows no "Approved By"', () => {
    show(
      mapPOtoPurchaseOrder({
        po_id: 'PO9',
        po_number: 'PO-2026-0009',
        vendor_name: 'Essilor India',
        status: 'SENT',
        created_at: '2026-08-01T10:00:00',
        expected_date: '2026-08-10',
        approved_by: 'u-approver',
        items: [],
      }),
    );
    expect(screen.queryByText(/approved/i)).not.toBeInTheDocument();
    expect(screen.queryByText('u-approver')).not.toBeInTheDocument();
  });
});

describe('PO detail modal - a lens order names each power (review round 7)', () => {
  it('shows each line by its own description, and its Cancel line button says which', () => {
    const lens = mapPOtoPurchaseOrder({
      po_id: 'PO7', po_number: 'PO-LENS-1', status: 'SENT', source: 'cl_po_generator',
      items: ['-1.00', '-2.00'].map((sph) => ({
        product_id: 'CL1', product_name: 'Acuvue Oasys', description: `Acuvue Oasys SPH ${sph}`,
        quantity: 2, unit_price: 900, tax_rate: 5,
      })),
    });
    show(lens);
    expect(screen.getByText('Acuvue Oasys SPH -1.00')).toBeInTheDocument();
    expect(screen.getByText('Acuvue Oasys SPH -2.00')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Cancel line Acuvue Oasys SPH -2.00' })).toBeInTheDocument();
  });
});

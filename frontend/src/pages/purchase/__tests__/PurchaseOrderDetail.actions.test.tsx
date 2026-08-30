// ============================================================================
// IMS 2.0 - P0-4 (launch gate): the status-theater buttons are GONE
// ============================================================================
// Approve / Mark as Ordered / Mark as Received on the PO detail modal called
// NO API — pure local state flips that evaporated on reload, making a manager
// believe lifecycle steps happened that never reached the server. They are
// removed for launch (wiring them is a feature, not a P0). The two REAL
// actions stay: Submit for Approval (DRAFT -> send) and Reject (cancel).

import { render, screen } from '@testing-library/react';
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

const po = (status: string) =>
  ({
    id: 'PO1',
    poNumber: 'PO-2026-0042',
    supplierId: 'V1',
    supplierName: 'Essilor India',
    date: '2026-08-01',
    expectedDelivery: '2026-08-10',
    status,
    items: [
      { productId: 'p1', productName: 'Ray-Ban RB2140', sku: 'RB-2140', quantity: 2, unitCost: 1000, taxRate: 18, total: 2000 },
    ],
    subtotal: 2000,
    taxAmount: 360,
    total: 2360,
  }) as unknown as PurchaseOrder;

describe('PO detail modal — only REAL actions render (P0-4)', () => {
  it('PENDING: Approve (theater) is gone, Reject (real cancel) stays', () => {
    render(<PurchaseOrderDetail po={po('PENDING')} onClose={vi.fn()} onAction={vi.fn()} />);
    expect(screen.queryByRole('button', { name: /^approve$/i })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /reject/i })).toBeInTheDocument();
  });

  it('APPROVED: Mark as Ordered (theater) is gone', () => {
    render(<PurchaseOrderDetail po={po('APPROVED')} onClose={vi.fn()} onAction={vi.fn()} />);
    expect(
      screen.queryByRole('button', { name: /mark as ordered/i }),
    ).not.toBeInTheDocument();
  });

  it('ORDERED: Mark as Received (theater) is gone', () => {
    render(<PurchaseOrderDetail po={po('ORDERED')} onClose={vi.fn()} onAction={vi.fn()} />);
    expect(
      screen.queryByRole('button', { name: /mark as received/i }),
    ).not.toBeInTheDocument();
  });

  it('DRAFT: Submit for Approval (the real send) still fires onAction', async () => {
    const onAction = vi.fn();
    render(<PurchaseOrderDetail po={po('DRAFT')} onClose={vi.fn()} onAction={onAction} />);
    await userEvent.setup().click(
      screen.getByRole('button', { name: /submit for approval/i }),
    );
    expect(onAction).toHaveBeenCalledWith(expect.objectContaining({ id: 'PO1' }), 'submit');
  });
});

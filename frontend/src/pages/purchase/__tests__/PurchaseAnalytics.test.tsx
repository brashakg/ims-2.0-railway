// ============================================================================
// IMS 2.0 - Purchase Analytics: no approval step anywhere
// ============================================================================
// Owner ruling 2026-09-28: a purchase order has no approval step. The fourth
// card counts drafts nobody has sent yet; it used to say "Pending Approval",
// and reverting it passed every purchase suite (verifier, hollow test).

import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { PurchaseAnalytics } from '../PurchaseAnalytics';
import type { PurchaseOrder } from '../purchaseTypes';

const po = (id: string, status: PurchaseOrder['status']) =>
  ({ id, poNumber: id, status, total: 1000, items: [] }) as unknown as PurchaseOrder;

describe('PurchaseAnalytics', () => {
  it('counts drafts not sent and never mentions approval', () => {
    const { container } = render(
      <PurchaseAnalytics
        purchaseOrders={[po('a', 'DRAFT'), po('b', 'DRAFT'), po('c', 'SENT')]}
        suppliers={[]}
      />,
    );
    const label = screen.getByText('Drafts not sent');
    expect(label.nextElementSibling?.textContent).toBe('2');
    expect(container.textContent).not.toMatch(/approv/i);
  });
});

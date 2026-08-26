// ============================================================================
// IMS 2.0 - "Print PO" from the purchase-order viewing popup
// ============================================================================
// Owner report: "if i click on print po, the preview comes behind the section
// and is not useable and clicking cross to close closes it all".
//
// Both overlays were `z-50` siblings and the print one was rendered FIRST, so
// at equal z-index the PO modal painted over it. The only cross he could reach
// was the PO modal's, which threw away the purchase order he was reading.
//
// These pin BOTH halves:
//   * the print preview sits ABOVE the purchase-order popup, and
//   * dismissing the preview leaves that popup open on the same PO.

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

const PO = {
  id: 'PO1',
  poNumber: 'PO-2026-0042',
  supplierId: 'V1',
  supplierName: 'Essilor India',
  date: '2026-08-01',
  expectedDelivery: '2026-08-10',
  status: 'DRAFT',
  items: [
    { productId: 'p1', productName: 'Ray-Ban RB2140', sku: 'RB-2140', quantity: 2, unitCost: 1000, taxRate: 18, total: 2000 },
  ],
  subtotal: 2000,
  taxAmount: 360,
  total: 2360,
} as unknown as PurchaseOrder;

/** The numeric z-index carried by a Tailwind class: `z-50` or `z-[70]`. */
function zLayer(el: HTMLElement): number {
  const hit = /(?:^|\s)z-(?:\[(\d+)\]|(\d+))(?:\s|$)/.exec(el.className);
  expect(hit, `no z-index class on: ${el.className}`).not.toBeNull();
  return Number(hit![1] ?? hit![2]);
}

function overlays(): HTMLElement[] {
  return Array.from(document.querySelectorAll<HTMLElement>('div.fixed.inset-0'));
}

function poPopup(): HTMLElement {
  // The popup is the overlay carrying the PO's own action bar.
  const el = overlays().find(
    (o) => within(o).queryAllByRole('button', { name: /Submit for Approval/i }).length > 0,
  );
  expect(el, 'purchase-order popup not on screen').toBeTruthy();
  return el!;
}

function printPreview(): HTMLElement | undefined {
  return overlays().find((o) => within(o).queryAllByText('Print Purchase Order').length > 0);
}

describe('Print PO from the purchase-order popup', () => {
  it('opens the preview ABOVE the popup, not behind it', async () => {
    render(<PurchaseOrderDetail po={PO} onClose={vi.fn()} onAction={vi.fn()} />);

    await userEvent.setup().click(screen.getByRole('button', { name: /Print PO/i }));

    const preview = printPreview();
    expect(preview, 'print preview did not open').toBeTruthy();
    expect(zLayer(preview!)).toBeGreaterThan(zLayer(poPopup()));
  });

  it('closing the preview returns him to the same PO instead of dumping him out', async () => {
    const onClose = vi.fn();
    render(<PurchaseOrderDetail po={PO} onClose={onClose} onAction={vi.fn()} />);
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: /Print PO/i }));
    const preview = printPreview()!;

    // The cross INSIDE the preview -- the one now on top and reachable.
    const cross = within(preview).getAllByRole('button').at(-1)!;
    await user.click(cross);

    expect(printPreview()).toBeUndefined();
    expect(poPopup()).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();
  });
});

// ============================================================================
// Owner ruling 2026-09-28: RECEIVING IS MANAGERS ONLY. The accountant keeps
// bills and payments but is never handed a way to receive goods: no Receive
// button on the PO list, no "Receive Goods" menu item. Both read
// RECEIVING_MANAGER_ROLES; widening that list (or either call site) to include
// ACCOUNTANT must fail here.
// ============================================================================

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const roles = vi.hoisted(() => ({ current: ['ACCOUNTANT'] as string[] }));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u1', roles: roles.current, activeRole: roles.current[0] },
    hasRole: (wanted: string[]) => wanted.some((r) => roles.current.includes(r)),
    hasPermission: () => true,
  }),
}));

import { PurchaseTable } from '../../../pages/purchase/PurchaseTable';
import { filterVisibleGroups } from '../../shell/navConfig';
import type { PurchaseOrder } from '../../../pages/purchase/purchaseTypes';
import type { UserRole } from '../../../types';

const sentPO = {
  id: 'PO1', poNumber: 'PO/1', supplierId: 'v1', supplierName: 'Universal Optics',
  date: '2026-09-20', expectedDelivery: '', status: 'SENT', items: [], subtotal: 0,
  taxAmount: 0, total: 0,
} as unknown as PurchaseOrder;

function receiveButton(as: string[]) {
  roles.current = as;
  const { unmount } = render(
    <MemoryRouter>
      <PurchaseTable purchaseOrders={[sentPO]} onViewPO={() => {}} />
    </MemoryRouter>,
  );
  const found = screen.queryByRole('button', { name: /^receive$/i });
  unmount();
  return found;
}

function menuIds(as: UserRole[]) {
  return filterVisibleGroups(as, as[0], () => true).flatMap((g) => g.items.map((i) => i.id));
}

describe('an accountant cannot receive goods', () => {
  it('sees no Receive button on a sent purchase order; a store manager does', () => {
    expect(receiveButton(['ACCOUNTANT'])).toBeNull();
    expect(receiveButton(['STORE_MANAGER'])).not.toBeNull();
  });

  it('has no "Receive Goods" item in the menu; a store manager does', () => {
    expect(menuIds(['ACCOUNTANT'])).not.toContain('grn-cockpit');
    expect(menuIds(['STORE_MANAGER'])).toContain('grn-cockpit');
  });
});

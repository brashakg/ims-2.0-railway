// ============================================================================
// IMS 2.0 - Create PO: a typed-in item we already have (audit C2)
// ============================================================================
// The manager's catalogue search missed, so he typed the item in through
// "Not in the catalogue?". The server (create_po) created NOTHING and answered
// 409 ALREADY_IN_CATALOGUE naming the product. The form asks "use it?" and, on
// yes, resends that line naming the catalogued product -- never a hidden twin.
// Backend half: backend/tests/test_off_catalogue_items_release.py (C2).

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react';

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u1', name: 'Manager', roles: ['STORE_MANAGER'], activeStoreId: 'BV-DHN-02' },
    hasRole: () => true,
    hasPermission: () => true,
  }),
}));

const createPO = vi.hoisted(() => vi.fn());
vi.mock('../../../services/api', () => ({
  vendorsApi: { createPurchaseOrder: createPO },
  productApi: { getProducts: vi.fn().mockResolvedValue({ products: [] }) },
}));
vi.mock('../../../services/api/inventory', () => ({
  vendorsApi: { getLastCost: vi.fn().mockResolvedValue({ costs: {} }) },
}));
vi.mock('../../../services/api/stores', () => ({
  storeApi: { getStore: vi.fn().mockResolvedValue({ gstin: '20AABCU9603R1Z1', state: 'Jharkhand' }) },
}));
vi.mock('../../../services/api/entities', () => ({
  entitiesApi: { meta: vi.fn().mockResolvedValue({ states: [] }) },
}));

import { PurchaseOrderForm } from '../PurchaseOrderForm';
import { ApiError } from '../../../services/api/client';
import type { Supplier } from '../purchaseTypes';

const vendor = {
  id: 'v1',
  name: 'Jot Optics',
  code: 'SUP001',
  gstNumber: '20AABCU9603R1Z1',
  paymentTerms: 30,
} as unknown as Supplier;

const alreadyInCatalogue = () =>
  new ApiError('Already in the catalogue', {
    status: 409,
    code: 'ALREADY_IN_CATALOGUE',
    detail: {
      code: 'ALREADY_IN_CATALOGUE',
      matches: [
        {
          line: 0,
          existing: { product_id: 'p-carrera', sku: 'FRCARRERACA8895807', name: 'Carrera Ca 8895 - 807', size: '54' },
        },
      ],
    },
  });

async function typeCarreraAndSubmit(onCreated: () => void) {
  render(<PurchaseOrderForm suppliers={[vendor]} existingPOCount={0} onClose={() => {}} onCreated={onCreated} />);
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
  fireEvent.change(screen.getByLabelText('Vendor'), { target: { value: 'v1' } });
  fireEvent.click(screen.getByRole('button', { name: /not in the catalogue\?/i }));
  fireEvent.change(screen.getByLabelText('New item brand'), { target: { value: 'Carrera' } });
  fireEvent.change(screen.getByLabelText('New item model number'), { target: { value: 'CA 8895' } });
  fireEvent.change(screen.getByLabelText('New item colour code'), { target: { value: '807' } });
  fireEvent.change(screen.getByLabelText('New item size'), { target: { value: '54' } });
  fireEvent.change(screen.getByLabelText('New item MRP'), { target: { value: '6990' } });
  fireEvent.change(screen.getByLabelText('Unit cost for line 1'), { target: { value: '3200' } });
  fireEvent.click(screen.getByRole('button', { name: /create as draft/i }));
}

describe('Create PO - a typed-in item that is already in the catalogue (audit C2)', () => {
  beforeEach(() => {
    createPO.mockReset();
    vi.restoreAllMocks();
  });

  it('asks "use it?" naming the size, then orders the catalogued product', async () => {
    createPO
      .mockRejectedValueOnce(alreadyInCatalogue())
      .mockResolvedValueOnce({ po_id: 'po-1', po_number: 'PO-BV-DHN-02-0001' });
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    const onCreated = vi.fn();

    await typeCarreraAndSubmit(onCreated);

    await waitFor(() => expect(createPO).toHaveBeenCalledTimes(2));
    expect(createPO.mock.calls[0][0].items[0].new_product).toMatchObject({ brand: 'Carrera', size: '54' });
    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/already in the catalogue.*size 54/i));
    const resent = createPO.mock.calls[1][0].items[0];
    expect(resent.product_id).toBe('p-carrera');
    expect(resent.new_product).toBeUndefined();
    await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1));
  });

  it('on "no" it creates nothing', async () => {
    createPO.mockRejectedValueOnce(alreadyInCatalogue());
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    const onCreated = vi.fn();

    await typeCarreraAndSubmit(onCreated);

    await waitFor(() => expect(createPO).toHaveBeenCalledTimes(1));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(createPO).toHaveBeenCalledTimes(1);
    expect(onCreated).not.toHaveBeenCalled();
  });
});

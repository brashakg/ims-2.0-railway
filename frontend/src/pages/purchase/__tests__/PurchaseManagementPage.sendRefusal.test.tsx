// ============================================================================
// IMS 2.0 - P0-4 (launch gate): a refused "Send PO" must never report success
// ============================================================================
// Launch-gate repro: DRAFT PO -> "Submit for Approval" with the server
// REFUSING the send. The old handler swallowed the refusal in an empty catch,
// then flipped the row to PENDING and fired toast.success('') anyway — an
// empty green toast — so a manager believed the PO reached the vendor and the
// goods were simply never ordered. Pinned here:
//   * the refusal TEXT is toasted as an error,
//   * no success toast fires,
//   * state does NOT flip — the PO stays DRAFT with its Submit button.
// The rejection is built by the REAL api-client transform (buildApiError),
// never a hand-made axios shape the client would not deliver.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import type { AxiosError } from 'axios';

const toastMock = vi.hoisted(() => ({
  success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn(),
}));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toastMock }));

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u1', name: 'Admin', roles: ['ADMIN'], activeStoreId: 'BV-BOK-01' },
    hasRole: () => true,
    hasPermission: () => true,
  }),
}));

vi.mock('../../../hooks/useIsOnlineStore', () => ({ useIsOnlineStore: () => false }));

vi.mock('../../../hooks/useStorePrintInfo', () => ({
  useStorePrintInfo: () => ({
    storeName: 'BV Bokaro', address: '', city: 'Bokaro', state: 'Jharkhand',
    pincode: '', stateCode: '20',
  }),
}));

vi.mock('../../../components/print/storeIdentity', () => ({
  resolveStoreIdentity: vi.fn().mockResolvedValue(null),
}));

vi.mock('../../../services/api/entities', () => ({
  entitiesApi: { meta: vi.fn().mockResolvedValue({ state_codes: [], entity_types: [] }) },
}));

const getVendors = vi.fn();
const getPurchaseOrders = vi.fn();
const sendPurchaseOrder = vi.fn();

vi.mock('../../../services/api', () => ({
  vendorsApi: {
    getVendors: (...a: unknown[]) => getVendors(...a),
    getPurchaseOrders: (...a: unknown[]) => getPurchaseOrders(...a),
    sendPurchaseOrder: (...a: unknown[]) => sendPurchaseOrder(...a),
    createVendor: vi.fn(),
    updateVendor: vi.fn(),
    generatePortalToken: vi.fn(),
    cancelPurchaseOrder: vi.fn(),
    getGRNs: vi.fn(),
    getVarianceReport: vi.fn(),
    dismissVariance: vi.fn(),
    getLastCost: vi.fn(),
    createPurchaseOrder: vi.fn(),
  },
  productApi: { getProducts: vi.fn() },
}));

import { MemoryRouter } from 'react-router-dom';
import { PurchaseManagementPage } from '../PurchaseManagementPage';
import { buildApiError } from '../../../services/api/client';

const RAW_PO = {
  po_id: 'po-1',
  po_number: 'PO-2026-0042',
  vendor_id: 'v1',
  vendor_name: 'Essilor India',
  status: 'DRAFT',
  created_at: '2026-08-01T10:00:00',
  expected_date: '2026-08-10',
  items: [
    { product_id: 'p1', product_name: 'Crizal Rock', sku: 'CRZ', quantity: 2, unit_price: 1000, tax_rate: 18 },
  ],
  subtotal: 2000,
  tax_amount: 360,
  total_amount: 2360,
};

beforeEach(() => {
  vi.clearAllMocks();
  getVendors.mockResolvedValue({ vendors: [] });
  getPurchaseOrders.mockResolvedValue({ purchase_orders: [RAW_PO] });
});

async function openDraftPO() {
  const { container } = render(
    <MemoryRouter>
      <PurchaseManagementPage />
    </MemoryRouter>,
  );
  await screen.findByText('PO-2026-0042');
  // The eye (view) button on the PO row has no accessible name — find the
  // button wrapping the lucide eye icon.
  const eyeBtn = Array.from(container.querySelectorAll('button')).find((b) =>
    b.querySelector('svg.lucide-eye'),
  );
  expect(eyeBtn, 'view (eye) button not found on the PO row').toBeTruthy();
  fireEvent.click(eyeBtn!);
  return screen.findByRole('button', { name: /submit for approval/i });
}

describe('Purchase page — refused Send PO (P0-4)', () => {
  it('toasts the refusal text, fires no success, and the PO stays DRAFT', async () => {
    sendPurchaseOrder.mockRejectedValue(
      buildApiError({
        message: 'Request failed with status code 400',
        response: {
          status: 400,
          data: { detail: 'PO_LINES_INCOMPLETE: line 2 has no rate — fix the draft before sending.' },
        },
      } as unknown as AxiosError<{ detail?: string }>),
    );

    const submitBtn = await openDraftPO();
    fireEvent.click(submitBtn);

    await waitFor(() =>
      expect(toastMock.error).toHaveBeenCalledWith(
        'PO_LINES_INCOMPLETE: line 2 has no rate — fix the draft before sending.',
      ),
    );
    // The lie is dead: no green toast of any kind, empty-string included.
    expect(toastMock.success).not.toHaveBeenCalled();
    // State did not flip: the PO is still DRAFT, so its Submit button remains.
    expect(
      screen.getByRole('button', { name: /submit for approval/i }),
    ).toBeInTheDocument();
  });

  it('a successful send still flips the row and toasts success', async () => {
    sendPurchaseOrder.mockResolvedValue({ ok: true });

    const submitBtn = await openDraftPO();
    fireEvent.click(submitBtn);

    await waitFor(() =>
      expect(toastMock.success).toHaveBeenCalledWith(
        'PO-2026-0042 submitted for approval',
      ),
    );
    expect(toastMock.error).not.toHaveBeenCalled();
    // DRAFT -> PENDING: the Submit button is gone.
    expect(
      screen.queryByRole('button', { name: /submit for approval/i }),
    ).not.toBeInTheDocument();
  });
});

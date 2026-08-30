// ============================================================================
// IMS 2.0 - Manual purchase invoice: the goods/services declaration
// ============================================================================
// Free-text lines carry no product_id, so the server's goods trigger cannot
// see them -- the manual form booked "20 pcs assorted frames" (Rs 72,450) as
// prose with no receipt. The form now requires the declaration:
//   - booking without the choice never reaches the server
//   - SERVICES books and carries bill_kind on the wire
//   - GOODS disables Book and shows the receipt-first guidance (naming the
//     no-PO Delivery-Challan route)

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const toastMock = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}));

const apis = vi.hoisted(() => ({
  purchaseInvoicesApi: {
    list: vi.fn(),
    get: vi.fn(),
    create: vi.fn(),
    createFromGrn: vi.fn(),
    createFromDcs: vi.fn(),
    getOpenDcs: vi.fn(),
    getMatch: vi.fn(),
    approveException: vi.fn(),
    getConfig: vi.fn(),
    requestCataloguing: vi.fn(),
  },
}));

vi.mock('../../../services/api/vendorAp', async (importOriginal) => {
  const real = await importOriginal<Record<string, unknown>>();
  return { ...real, ...apis };
});
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toastMock }));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { activeStoreId: 'S1', roles: ['ACCOUNTANT'] },
    hasRole: () => true,
  }),
}));

import { PurchaseInvoicesTab } from '../PurchaseInvoicesTab';

const SUPPLIER = {
  id: 'V-77',
  name: 'Frames Wala',
  code: 'FW',
  gstNumber: '27ABCDE1234F1Z5',
  state: 'Maharashtra',
} as never;

async function openManualForm() {
  render(
    <MemoryRouter>
      <PurchaseInvoicesTab suppliers={[SUPPLIER] as never} />
    </MemoryRouter>,
  );
  await waitFor(() => expect(apis.purchaseInvoicesApi.list).toHaveBeenCalled());
  fireEvent.click(screen.getByRole('button', { name: /Manual invoice/i }));
  await screen.findByText('This bill is for');
}

function fillManualHeaderAndLine() {
  fireEvent.change(screen.getByDisplayValue('Select supplier...'), {
    target: { value: 'V-77' },
  });
  fireEvent.change(
    screen.getByPlaceholderText(/As printed on the supplier's bill/),
    { target: { value: 'FW-3301' } },
  );
  fireEvent.change(screen.getByPlaceholderText('Item description'), {
    target: { value: '20 pcs assorted frames' },
  });
  const qty = document.querySelector(
    'input[type="number"].w-16',
  ) as HTMLInputElement;
  fireEvent.change(qty, { target: { value: '21' } });
}

beforeEach(() => {
  vi.clearAllMocks();
  apis.purchaseInvoicesApi.list.mockResolvedValue({ purchase_invoices: [], total: 0 });
  apis.purchaseInvoicesApi.getConfig.mockResolvedValue(null);
  apis.purchaseInvoicesApi.create.mockResolvedValue({});
});

describe('manual invoice bill-kind declaration', () => {
  it('booking without the declaration never reaches the server', async () => {
    await openManualForm();
    fillManualHeaderAndLine();
    fireEvent.click(screen.getByRole('button', { name: /Book invoice/i }));
    await waitFor(() =>
      expect(toastMock.error).toHaveBeenCalledWith(
        expect.stringContaining('what this bill is for'),
      ),
    );
    expect(apis.purchaseInvoicesApi.create).not.toHaveBeenCalled();
  });

  it('a declared SERVICES bill books and carries bill_kind on the wire', async () => {
    await openManualForm();
    fillManualHeaderAndLine();
    fireEvent.change(screen.getByDisplayValue(/Choose: goods, or services/), {
      target: { value: 'SERVICES' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Book invoice/i }));
    await waitFor(() =>
      expect(apis.purchaseInvoicesApi.create).toHaveBeenCalledTimes(1),
    );
    const payload = apis.purchaseInvoicesApi.create.mock.calls[0][0];
    expect(payload.bill_kind).toBe('SERVICES');
    expect(payload.vendor_id).toBe('V-77');
  });

  it('GOODS walls off Book and names the Delivery-Challan route', async () => {
    await openManualForm();
    fillManualHeaderAndLine();
    fireEvent.change(screen.getByDisplayValue(/Choose: goods, or services/), {
      target: { value: 'GOODS' },
    });
    await screen.findByText(/Delivery Challan/);
    expect(screen.getByRole('button', { name: /Book invoice/i })).toBeDisabled();
    expect(apis.purchaseInvoicesApi.create).not.toHaveBeenCalled();
  });
});

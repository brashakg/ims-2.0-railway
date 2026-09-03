// ============================================================================
// The general counter's completion screen issues the TAX INVOICE, always
// ============================================================================
// Owner ruling 2026-09-04: at the counter the goods leave with the customer,
// so the sale IS the handover and every counter sale ends on the tax invoice
// -- minted by the ONE statutory door (GET /orders/{id}/invoice.pdf). There is
// no per-sale receipt choice and no "order receipt" label on this stage: that
// label belongs to the optical till, whose document at sale time really is a
// receipt and whose invoice is printed at delivery.
//
// Reverting the counter to the SALE stage (or re-adding a chooser) fails the
// first test; pointing the print anywhere but the invoice door fails it too.

import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u-1', activeStoreId: 'BV-BOK-01', roles: ['CASHIER'] } }),
}));

const apiGet = vi.fn();
vi.mock('../../../../services/api/client', () => ({
  default: { get: (...a: unknown[]) => apiGet(...a) },
}));

const getOrder = vi.fn();
vi.mock('../../../../services/api/sales', () => ({
  orderApi: { getOrder: (...a: unknown[]) => getOrder(...a) },
  workshopApi: { getJob: vi.fn() },
}));

const sendNotification = vi.fn();
vi.mock('../../../../services/api/marketing', () => ({
  marketingApi: {
    sendNotification: (...a: unknown[]) => sendNotification(...a),
    sendReviewRequest: vi.fn(),
  },
}));
vi.mock('../../../../services/api/incentive', () => ({
  incentiveApi: {
    listDaily: () => Promise.resolve({ items: [] }),
    getMtd: () => Promise.resolve({ items: [] }),
  },
}));
vi.mock('../../../../services/api/walkouts', () => ({
  walkoutsApi: { dashboardPerStaff: () => Promise.resolve({ items: [] }) },
}));
vi.mock('../../../../components/print/storeIdentity', () => ({
  resolveStoreIdentity: () =>
    Promise.resolve({
      store: { storeName: 'Better Vision Bokaro', storeCode: 'BV-BOK-01' },
      entity: {},
    }),
}));
vi.mock('../../../../components/print/WorkshopJobCardPrint', () => ({
  WorkshopJobCardPrint: () => null,
}));

import { CounterCompleteScreen, SaleCompleteScreen } from '../SaleCompleteScreen';

/** The wire shape of GET /orders/{id} (camelCase, via order_to_frontend). */
const ORDER = {
  id: 'o-1',
  orderNumber: 'ORD-1',
  storeId: 'BV-BOK-01',
  customerId: 'c-1',
  customerName: 'Asha Verma',
  customerPhone: '9876543210',
  grandTotal: 5000,
  amountPaid: 5000,
  balanceDue: 0,
  items: [{ productName: 'Titan watch', quantity: 1, finalPrice: 5000 }],
};

const mount = (ui: React.ReactElement) => render(<MemoryRouter>{ui}</MemoryRouter>);

beforeEach(() => {
  getOrder.mockReset().mockResolvedValue(ORDER);
  apiGet.mockReset().mockResolvedValue({ data: new Blob(['%PDF']) });
  sendNotification.mockReset().mockResolvedValue({});
  // jsdom has neither; the screen opens the PDF in a new tab.
  (URL as unknown as { createObjectURL: unknown }).createObjectURL = vi.fn(() => 'blob:x');
  (URL as unknown as { revokeObjectURL: unknown }).revokeObjectURL = vi.fn();
  window.open = vi.fn(() => ({}) as Window);
});

describe('the general counter completion screen', () => {
  it('prints the TAX INVOICE through the statutory door, with no receipt label anywhere', async () => {
    mount(<CounterCompleteScreen orderId="o-1" />);
    await screen.findByText(/Asha Verma/);

    fireEvent.click(screen.getByRole('button', { name: 'Tax invoice (A4)' }));
    await waitFor(() =>
      expect(apiGet).toHaveBeenCalledWith('/orders/o-1/invoice.pdf', { responseType: 'blob' }),
    );
    expect(screen.queryByText(/order receipt/i)).toBeNull();
    expect(screen.queryByRole('button', { name: /receipt/i })).toBeNull();
  });

  it('never claims the invoice message already went out -- only the deliver door auto-queues', async () => {
    mount(<CounterCompleteScreen orderId="o-1" />);
    await screen.findByText(/Asha Verma/);
    expect(screen.queryByText(/sent automatically/i)).toBeNull();
  });

  it('sends the document under the registered ORDER_DELIVERED template', async () => {
    // ORDER_DELIVERED is the registered document template that says what
    // happened here (the customer has the goods); an unmapped flow key fails
    // in every dispatch mode.
    mount(<CounterCompleteScreen orderId="o-1" />);
    await screen.findByText(/Asha Verma/);

    fireEvent.click(screen.getByRole('button', { name: /WhatsApp tax invoice/i }));
    await waitFor(() => expect(sendNotification).toHaveBeenCalledTimes(1));
    expect(sendNotification.mock.calls[0][0]).toMatchObject({
      template_id: 'ORDER_DELIVERED',
      channel: 'WHATSAPP',
      customer_phone: '9876543210',
    });
  });

  it('leaves the optical till on its order receipt -- there the invoice prints at delivery', async () => {
    mount(<SaleCompleteScreen orderId="o-1" />);
    await screen.findByText(/Asha Verma/);
    expect(screen.getByRole('button', { name: 'Order receipt (A4)' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /tax invoice/i })).toBeNull();
  });
});

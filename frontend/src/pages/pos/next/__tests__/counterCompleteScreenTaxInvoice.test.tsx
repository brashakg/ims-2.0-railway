// ============================================================================
// The general counter's completion screen issues the TAX INVOICE, always
// ============================================================================
// Owner ruling 2026-09-04: at the counter the goods leave with the customer,
// so the sale IS the handover and every counter sale ends on the tax invoice
// -- minted by the ONE statutory door (GET /orders/{id}/invoice.pdf). There is
// no per-sale receipt choice and no "receipt" label anywhere. The optical
// till's SALE stage opens the SAME door (the serial is minted at the sale --
// owner ruling 2026-09-04, "mint at the sale"), so it says "tax invoice" too:
// nobody is told they are printing a receipt while a serial is being minted.
//
// Re-adding a chooser or a receipt label fails the first/last tests; pointing
// the print anywhere but the invoice door fails them too.

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

  it('never claims the invoice message already went out when the hand-over step did not run', async () => {
    // Only the deliver door auto-queues ORDER_DELIVERED. A home-delivery bill,
    // or a take-away whose hand-over failed, never went through it.
    mount(<CounterCompleteScreen orderId="o-1" />);
    await screen.findByText(/Asha Verma/);
    expect(screen.queryByText(/sent automatically/i)).toBeNull();
  });

  it('admits the message already went when the sale was handed over at the till', async () => {
    // The counter's take-away sale goes through /deliver, which queues the
    // ORDER_DELIVERED text itself -- the manual button must offer a RESEND,
    // not a first send (owner rule: never pretend a message was not sent).
    mount(<CounterCompleteScreen orderId="o-1" delivered />);
    await screen.findByText(/Asha Verma/);
    expect(screen.getByText(/sent automatically/i)).toBeTruthy();
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

  it('the optical till mints the serial at the sale, so its SALE stage says "tax invoice" and never "receipt"', async () => {
    const { container } = mount(<SaleCompleteScreen orderId="o-1" />);
    await screen.findByText(/Asha Verma/);

    fireEvent.click(screen.getByRole('button', { name: 'Tax invoice (A4)' }));
    await waitFor(() =>
      expect(apiGet).toHaveBeenCalledWith('/orders/o-1/invoice.pdf', { responseType: 'blob' }),
    );
    // Rendered text AND attributes (titles, labels): the word must not appear.
    expect(container.innerHTML).not.toMatch(/receipt/i);
    // The sale stage still sends under the sale-time template, not the
    // delivered one -- the goods have not been handed over on the optical till.
    fireEvent.click(screen.getByRole('button', { name: /WhatsApp tax invoice/i }));
    await waitFor(() => expect(sendNotification).toHaveBeenCalledTimes(1));
    expect(sendNotification.mock.calls[0][0]).toMatchObject({ template_id: 'ORDER_CONFIRMED' });
  });
});

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { SuperadminOrderEditModal } from '../SuperadminOrderEditModal';
import { orderApi } from '../../../services/api/sales';

vi.mock('../../../services/api/sales', () => ({ orderApi: {
  quoteBill: vi.fn(), superadminEditOrder: vi.fn(), superadminInvoiceChange: vi.fn(),
} }));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => ({ success: vi.fn(), error: vi.fn() }) }));
const quote = vi.mocked(orderApi.quoteBill);
const change = vi.mocked(orderApi.superadminInvoiceChange);
const reply = (total: number) => ({ grand_total: total, round_off: 0, subtotal: total, taxable: total, tax: 0, total_discount: 0 });
const order = {
  id: 'OLD-1', orderNumber: 'BV-1', invoiceNumber: 'INV-1', grandTotal: 1000,
  items: [{ item_id: 'L1', item_type: 'FRAME', product_id: 'P1', product_name: 'Frame', quantity: 1, unit_price: 1000.49, discount_percent: 0 }],
};
const open = () => render(<SuperadminOrderEditModal order={order as never} onClose={vi.fn()} onSaved={vi.fn()} />);

beforeEach(() => {
  vi.clearAllMocks();
  quote.mockResolvedValue(reply(1000));
  change.mockResolvedValue({ note_type: 'CREDIT_NOTE', note_number: 'CN1', amount: 100 } as never);
});

describe('Order edits use the saved-order server quote', () => {
  it('waits for the quote, preserves order identity, and promises the same credit as the server', async () => {
    open();
    const save = screen.getByRole('button', { name: 'Issue credit / debit note' });
    expect(save).toBeDisabled();
    await waitFor(() => expect(save).toBeEnabled());
    expect(quote.mock.calls[0][0]).toMatchObject({ order_id: 'OLD-1', items: [{ unit_price: 1000.49 }] });
    expect(screen.getByText('No change')).toBeInTheDocument();

    quote.mockResolvedValue(reply(900));
    fireEvent.change(screen.getByDisplayValue('1000.49'), { target: { value: '900.30' } });
    expect(save).toBeDisabled();
    await waitFor(() => expect(screen.getByText(/A credit note for/)).toHaveTextContent('100.00'));
    expect(screen.queryByText(/99.70/)).toBeNull();
    fireEvent.change(screen.getByPlaceholderText(/Why is this order/), { target: { value: 'Agreed revised price' } });
    fireEvent.click(save);
    await waitFor(() => expect(change).toHaveBeenCalledWith('OLD-1', expect.objectContaining({ items: [expect.objectContaining({ unit_price: 900.3 })] })));
  });

  it('keeps saving unavailable when pricing fails', async () => {
    quote.mockRejectedValue(new Error('offline'));
    open();
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not price this edit');
    expect(screen.getByRole('button', { name: 'Issue credit / debit note' })).toBeDisabled();
    expect(change).not.toHaveBeenCalled();
  });

  it('ignores an older quote that finishes after the next edit', async () => {
    let old!: (result: ReturnType<typeof reply>) => void;
    quote.mockImplementationOnce(() => new Promise((resolve) => { old = resolve; }));
    open();
    await waitFor(() => expect(quote).toHaveBeenCalledTimes(1));
    quote.mockResolvedValue(reply(900));
    fireEvent.change(screen.getByDisplayValue('1000.49'), { target: { value: '900.30' } });
    await waitFor(() => expect(screen.getByText(/A credit note for/)).toHaveTextContent('100.00'));
    old(reply(1000));
    await waitFor(() => expect(screen.getByText(/A credit note for/)).toHaveTextContent('100.00'));
  });
});

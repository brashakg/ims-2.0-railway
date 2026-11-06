// PR #1393: the server refuses typed goods (no product), so '+ Add line' can only
// ever save a service line. The screen must send the type its Category says, and
// say so before the press instead of promising a total a save cannot reach.
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { SuperadminOrderEditModal } from '../SuperadminOrderEditModal';
import { orderApi } from '../../../services/api/sales';

vi.mock('../../../services/api/sales', () => ({ orderApi: {
  quoteBill: vi.fn(), superadminEditOrder: vi.fn(), superadminInvoiceChange: vi.fn(),
} }));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => ({ success: vi.fn(), error: vi.fn() }) }));
const quote = vi.mocked(orderApi.quoteBill);
const edit = vi.mocked(orderApi.superadminEditOrder);
const change = vi.mocked(orderApi.superadminInvoiceChange);
const reply = (total: number) => ({ grand_total: total, round_off: 0, subtotal: total, taxable: total, tax: 0, total_discount: 0 });
const stored = { item_id: 'L1', item_type: 'FRAME', product_id: 'P1', product_name: 'Frame', category: 'FRAME', quantity: 1, unit_price: 1000, discount_percent: 0 };
const order = { id: 'ORD-1', orderNumber: 'BV-1', grandTotal: 1000, items: [stored] };
const SAY = /Only a service line can be typed here/;
const open = (o: object = order) =>
  render(<SuperadminOrderEditModal order={o as never} onClose={vi.fn()} onSaved={vi.fn()} />);

const addLine = async (category: string, name: string) => {
  await waitFor(() => expect(quote).toHaveBeenCalled());
  fireEvent.click(screen.getByRole('button', { name: '+ Add line' }));
  const [, typedName] = screen.getAllByPlaceholderText('Item name');
  fireEvent.change(typedName, { target: { value: name } });
  fireEvent.change(screen.getAllByPlaceholderText('FRAME')[1], { target: { value: category } });
  fireEvent.change(screen.getAllByDisplayValue('0').filter((el) => el.getAttribute('min') === '0' && !el.getAttribute('max'))[0], { target: { value: '100' } });
};
const lastQuoted = () => quote.mock.calls.at(-1)?.[0] as { items: Array<Record<string, unknown>> };

beforeEach(() => {
  vi.clearAllMocks();
  quote.mockResolvedValue(reply(1100));
  edit.mockResolvedValue({ grand_total: 1100 } as never);
  change.mockResolvedValue({ note_type: 'DEBIT_NOTE', note_number: 'DN1', amount: 100 } as never);
});

describe('A line typed on the edit screen', () => {
  it('saves a typed SERVICE line as a service line, on the body it priced', async () => {
    open();
    await addLine('SERVICE', 'Courier charge');
    const save = screen.getByRole('button', { name: 'Save order edit' });
    await waitFor(() => expect(save).toBeEnabled());
    expect(screen.queryByText(SAY)).toBeNull();
    fireEvent.change(screen.getByPlaceholderText(/Why is this order/), { target: { value: 'Courier added' } });
    fireEvent.click(save);
    await waitFor(() => expect(edit).toHaveBeenCalledTimes(1));
    const sent = (edit.mock.calls[0][1] as { items: Array<Record<string, unknown>> }).items;
    expect(sent).toHaveLength(2);
    expect(sent[0]).toMatchObject({ item_id: 'L1', item_type: 'FRAME', product_id: 'P1' });
    expect(sent[1]).toEqual({
      item_type: 'SERVICE', category: 'SERVICE', product_name: 'Courier charge',
      quantity: 1, unit_price: 100, discount_percent: 0,
    });
    expect(lastQuoted().items).toEqual(sent);
  });

  it('reads the category the way the till does, in any spelling', async () => {
    open();
    await addLine('services', 'Fitting');
    await waitFor(() => expect(lastQuoted().items[1]).toMatchObject({ item_type: 'SERVICE' }));
    expect(screen.queryByText(SAY)).toBeNull();
  });

  it('also sends the service type on an invoiced bill', async () => {
    open({ ...order, invoiceNumber: 'INV-1' });
    await addLine('SERVICE', 'Courier charge');
    const save = screen.getByRole('button', { name: 'Issue credit / debit note' });
    await waitFor(() => expect(save).toBeEnabled());
    fireEvent.change(screen.getByPlaceholderText(/Why is this order/), { target: { value: 'Courier added' } });
    fireEvent.click(save);
    await waitFor(() => expect(change).toHaveBeenCalledTimes(1));
    const sent = change.mock.calls[0][1] as { items: Array<Record<string, unknown>> };
    expect(sent.items[1]).toMatchObject({ item_type: 'SERVICE', category: 'SERVICE' });
  });

  it.each([['FRAME', 'FRAME'], ['SUNGLASS', 'SUNGLASS'], ['Courier', 'COURIER']])(
    'says why typed goods (%s) cannot be saved, and promises no total',
    async (category, itemType) => {
      open();
      await addLine(category, 'Ray-Ban 3025');
      expect(await screen.findByText(SAY)).toHaveTextContent('new bill');
      await waitFor(() => expect(lastQuoted().items[1]).toMatchObject({ item_type: itemType, category }));
      expect(lastQuoted().items[1].product_id).toBeUndefined();
      await waitFor(() => expect(quote.mock.results.length).toBeGreaterThan(1));
      expect(screen.queryByText('Additional amount owed')).toBeNull();
      expect(screen.queryByText(/1,100/)).toBeNull();
      const save = screen.getByRole('button', { name: 'Save order edit' });
      expect(save).toBeDisabled();
      fireEvent.change(screen.getByPlaceholderText(/Why is this order/), { target: { value: 'Typed goods' } });
      fireEvent.click(save);
      expect(edit).not.toHaveBeenCalled();
    },
  );

  it('warns as soon as the line is added, then clears when it becomes a service line', async () => {
    open();
    await waitFor(() => expect(quote).toHaveBeenCalled());
    fireEvent.click(screen.getByRole('button', { name: '+ Add line' }));
    expect(await screen.findByText(SAY)).toBeInTheDocument();
    fireEvent.change(screen.getAllByPlaceholderText('FRAME')[1], { target: { value: 'SERVICE' } });
    await waitFor(() => expect(screen.queryByText(SAY)).toBeNull());
  });

  it('leaves a stored line with no item id alone: it names its product', async () => {
    open({ ...order, items: [{ ...stored, item_id: undefined, item_type: 'SUNGLASS' }] });
    await waitFor(() => expect(lastQuoted().items[0]).toMatchObject({ item_type: 'SUNGLASS', product_id: 'P1' }));
    expect(screen.queryByText(SAY)).toBeNull();
  });
});

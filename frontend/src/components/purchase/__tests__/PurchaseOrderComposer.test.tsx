// ============================================================================
// IMS 2.0 - PurchaseOrderComposer tests (procurement Phase 2C)
// ============================================================================
// Pins the shared PO body used by BOTH the manual form and the Buy Desk draft:
//   - validation gate: a line with zero cost blocks submit (no createPO call)
//   - cost prefill: a blank line fills from getLastCost + shows the caption
//   - prefill NEVER overwrites a cost the operator already typed
//   - fail-soft: getLastCost returns empty -> blank cost, no caption, form works

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, render, screen, waitFor, fireEvent } from '@testing-library/react';

const toastMock = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}));

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => toastMock,
}));

// The composer calls vendorsApi.getLastCost off THIS module (direct import).
vi.mock('../../../services/api/inventory', () => ({
  vendorsApi: {
    getLastCost: vi.fn(),
  },
}));

// Only for the real getLastCost (vi.importActual) in the failed-lookup tests.
vi.mock('../../../services/api/client', () => ({ default: { get: vi.fn() } }));

import { PurchaseOrderComposer, applyPickedProduct } from '../PurchaseOrderComposer';
import type {
  ComposerLine,
  ComposerVendorOption,
  PurchaseOrderComposerProps,
} from '../PurchaseOrderComposer';
import { vendorsApi } from '../../../services/api/inventory';

const getLastCostMock = vendorsApi.getLastCost as unknown as ReturnType<typeof vi.fn>;

const VENDORS: ComposerVendorOption[] = [
  { id: 'v-1', name: 'Luxottica India', code: 'LUX' },
  { id: 'v-2', name: 'Essilor', code: 'ESS' },
];

const LINE = (over: Partial<ComposerLine> = {}): ComposerLine => ({
  productId: 'prod-a',
  productName: 'Ray-Ban RX5154',
  sku: 'RB5154',
  quantity: 2,
  unitCost: 0,
  taxRate: 18,
  costTouched: false,
  lastPaid: null,
  ...over,
});

// A read-only product cell (Buy-Desk style) keeps the tests focused on the
// composer's own behaviour (validation + prefill), not the manual picker.
function renderComposer(over: Partial<PurchaseOrderComposerProps> = {}) {
  const onSubmit = vi.fn().mockResolvedValue(undefined);
  const props: PurchaseOrderComposerProps = {
    mode: 'modal',
    vendors: VENDORS,
    initialVendorId: 'v-1',
    initialLines: [LINE()],
    renderProductCell: ({ line }) => (
      <div data-testid="product-cell">{line.productName}</div>
    ),
    onSubmit,
    ...over,
  };
  const utils = render(<PurchaseOrderComposer {...props} />);
  return { ...utils, onSubmit };
}

describe('PurchaseOrderComposer — validation gate', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getLastCostMock.mockResolvedValue({ costs: {} });
  });

  it('blocks submit when a line has zero cost and never calls onSubmit', async () => {
    const { onSubmit } = renderComposer({ initialLines: [LINE({ unitCost: 0 })] });

    // Wait for the (empty) prefill to settle so nothing races the click.
    await waitFor(() => expect(getLastCostMock).toHaveBeenCalled());

    fireEvent.click(screen.getByRole('button', { name: /create as draft/i }));

    await waitFor(() =>
      expect(toastMock.error).toHaveBeenCalledWith(
        expect.stringContaining('unit cost above 0'),
      ),
    );
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('submits when every line has a product, qty >= 1 and cost > 0', async () => {
    const { onSubmit } = renderComposer({
      initialLines: [LINE({ unitCost: 1500, costTouched: true })],
    });

    fireEvent.click(screen.getByRole('button', { name: /create as draft/i }));

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    const payload = onSubmit.mock.calls[0][0];
    expect(payload.vendorId).toBe('v-1');
    expect(payload.items).toHaveLength(1);
    expect(payload.items[0]).toEqual(
      expect.objectContaining({ product_id: 'prod-a', quantity: 2, unit_price: 1500 }),
    );
    expect(toastMock.error).not.toHaveBeenCalled();
  });
});

// Owner ruling 13: a line may name an item that is not in the catalogue yet.
// The composer must let it through and hand the typed identity to the server.
describe('PurchaseOrderComposer — an item that is not catalogued yet', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getLastCostMock.mockResolvedValue({ costs: {} });
  });

  const NEW_ITEM = {
    category: 'FR',
    brand: 'Ray-Ban',
    model: 'RB3025',
    colour: 'G-15',
    size: '58',
    mrp: 7990,
  };

  it('submits the typed identity instead of a product id', async () => {
    const { onSubmit } = renderComposer({
      initialLines: [
        LINE({
          productId: '',
          sku: '',
          productName: 'Ray-Ban RB3025',
          newProduct: NEW_ITEM,
          unitCost: 3200,
          costTouched: true,
        }),
      ],
    });

    fireEvent.click(screen.getByRole('button', { name: /create as draft/i }));

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    const payload = onSubmit.mock.calls[0][0];
    expect(payload.items).toHaveLength(1);
    expect(payload.items[0].new_product).toEqual(NEW_ITEM);
    expect(payload.items[0].product_id).toBeUndefined();
    expect(payload.items[0].quantity).toBe(2);
    expect(payload.items[0].unit_price).toBe(3200);
    expect(toastMock.error).not.toHaveBeenCalled();
  });

  it('still refuses a line that names nothing at all', async () => {
    const { onSubmit } = renderComposer({
      initialLines: [
        LINE({ productId: '', sku: '', productName: '', newProduct: null, unitCost: 3200 }),
      ],
    });

    fireEvent.click(screen.getByRole('button', { name: /create as draft/i }));

    await waitFor(() =>
      expect(toastMock.error).toHaveBeenCalledWith(
        expect.stringContaining('Not in the catalogue?'),
      ),
    );
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('still requires a cost on a typed-in line — it is the provisional cost price', async () => {
    const { onSubmit } = renderComposer({
      initialLines: [
        LINE({ productId: '', sku: '', newProduct: NEW_ITEM, unitCost: 0 }),
      ],
    });

    fireEvent.click(screen.getByRole('button', { name: /create as draft/i }));

    await waitFor(() =>
      expect(toastMock.error).toHaveBeenCalledWith(
        expect.stringContaining('unit cost above 0'),
      ),
    );
    expect(onSubmit).not.toHaveBeenCalled();
  });
});

describe('PurchaseOrderComposer — cost prefill', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('fills a blank line from getLastCost and shows the "last paid" caption', async () => {
    getLastCostMock.mockResolvedValue({
      costs: {
        'prod-a': { unit_price: 3200, po_number: 'PO-BV-26-0007', po_id: 'po-7', date: '2026-06-30T10:00:00' },
      },
    });

    renderComposer({ initialLines: [LINE({ unitCost: 0 })] });

    // getLastCost is called with the vendor + the blank line's product id.
    await waitFor(() => expect(getLastCostMock).toHaveBeenCalledWith('v-1', ['prod-a']));

    // The unit-cost input is filled with the last agreed price...
    const costInput = screen.getByLabelText(/unit cost for line 1/i) as HTMLInputElement;
    await waitFor(() => expect(costInput.value).toBe('3200'));

    // ...and the muted caption renders the amount + a human-friendly date.
    await waitFor(() =>
      expect(screen.getByText(/last paid ₹3,200 on 30 Jun 2026/i)).toBeInTheDocument(),
    );
  });

  it('does NOT overwrite a cost the operator already typed (but still says what this vendor was paid)', async () => {
    getLastCostMock.mockResolvedValue({
      costs: { 'prod-a': { unit_price: 3200, po_number: 'PO-1', po_id: 'po-1', date: '2026-06-30T10:00:00' } },
    });

    // Line already carries an operator-entered cost (costTouched).
    renderComposer({ initialLines: [LINE({ unitCost: 999, costTouched: true })] });

    const costInput = screen.getByLabelText(/unit cost for line 1/i) as HTMLInputElement;
    expect(costInput.value).toBe('999');

    // The lookup runs for the caption; the typed 999 stays.
    await waitFor(() =>
      expect(screen.getByText(/last paid ₹3,200 on 30 Jun 2026/i)).toBeInTheDocument(),
    );
    expect(costInput.value).toBe('999');
  });
});

// Audit F22: picking a product seeds its catalogue cost; that seed is the
// form's own guess, not the buyer's, so the vendor's last price replaces it.
describe('PurchaseOrderComposer — last paid beats the catalogue seed (F22)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  function renderWithPicker() {
    return renderComposer({
      initialLines: undefined,
      renderProductCell: ({ pickProduct }) => (
        <button
          type="button"
          onClick={() =>
            pickProduct({ productId: 'prod-a', productName: 'Carrera CA 8895', sku: 'CA8895', costPrice: 3200, gstRate: 5, hsn: '900311' })
          }
        >
          pick
        </button>
      ),
    });
  }

  it('replaces the catalogue cost with the last price paid to this vendor', async () => {
    getLastCostMock.mockResolvedValue({
      costs: { 'prod-a': { unit_price: 3100, po_number: 'PO-2', po_id: 'po-2', date: '2026-09-17T10:00:00' } },
    });
    renderWithPicker();
    fireEvent.click(screen.getByRole('button', { name: 'pick' }));

    const costInput = screen.getByLabelText(/unit cost for line 1/i) as HTMLInputElement;
    await waitFor(() => expect(costInput.value).toBe('3100'));
    expect(getLastCostMock).toHaveBeenCalledWith('v-1', ['prod-a']);
    expect(screen.getByText(/last paid ₹3,100 on 17 Sept? 2026/i)).toBeInTheDocument();
  });

  it('re-picking a product does not inherit the previous one\'s auto-filled cost', () => {
    const autoFilled = LINE({ unitCost: 3100, costTouched: false, lastPaid: { unitPrice: 3100 } });
    const next = applyPickedProduct(autoFilled, { productId: 'prod-b', productName: 'B', sku: 'B', costPrice: 5000 });
    expect(next.unitCost).toBe(5000);
    const typed = applyPickedProduct(LINE({ unitCost: 2950, costTouched: true }), {
      productId: 'prod-b', productName: 'B', sku: 'B', costPrice: 5000,
    });
    expect(typed.unitCost).toBe(2950);
  });

  it('typing in an uncatalogued item drops the cost the form filled for the picked one', async () => {
    getLastCostMock.mockResolvedValue({
      costs: { 'prod-a': { unit_price: 3100, po_number: 'PO-2', po_id: 'po-2', date: '2026-09-17T10:00:00' } },
    });
    renderComposer({
      initialLines: undefined,
      renderProductCell: ({ pickProduct, setNewProduct }) => (
        <>
          <button
            type="button"
            onClick={() =>
              pickProduct({ productId: 'prod-a', productName: 'Carrera CA 8895', sku: 'CA8895', costPrice: 3200, gstRate: 5, hsn: '900311' })
            }
          >
            pick
          </button>
          <button
            type="button"
            onClick={() =>
              setNewProduct({ category: 'FR', brand: 'Vogue', model: 'VO5051', colour: 'W44', size: '52', mrp: 5990 })
            }
          >
            new
          </button>
        </>
      ),
    });
    fireEvent.click(screen.getByRole('button', { name: 'pick' }));
    const costInput = screen.getByLabelText(/unit cost for line 1/i) as HTMLInputElement;
    await waitFor(() => expect(costInput.value).toBe('3100'));

    fireEvent.click(screen.getByRole('button', { name: 'new' }));
    expect(costInput.value).toBe('0');
    expect(screen.queryByText(/last paid/i)).not.toBeInTheDocument();
  });

  it('keeps the catalogue cost when this vendor was never paid for it', async () => {
    getLastCostMock.mockResolvedValue({ costs: {} });
    renderWithPicker();
    fireEvent.click(screen.getByRole('button', { name: 'pick' }));

    await waitFor(() => expect(getLastCostMock).toHaveBeenCalledWith('v-1', ['prod-a']));
    const costInput = screen.getByLabelText(/unit cost for line 1/i) as HTMLInputElement;
    expect(costInput.value).toBe('3200');
    expect(screen.queryByText(/last paid/i)).not.toBeInTheDocument();
  });
});

// Audit F67 x F22: the last-cost answer can land AFTER the manager has tapped
// the cost box (its text selected, ready to be typed over). Writing the price
// into it then drops the selection, so '2950' typed next read '31002950'.
describe('PurchaseOrderComposer — a late last-cost answer never rewrites a tapped box', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  const paid = (price: number) => ({
    costs: { 'prod-a': { unit_price: price, po_number: 'PO-3', po_id: 'po-3', date: '2026-09-17T10:00:00' } },
  });

  // Owner direction: the last price paid to this vendor wins over the
  // catalogue cost. While the manager is in the box the answer only captions
  // the line; leaving it WITHOUT typing puts that price in.
  it('an answer that lands while the manager is in the box captions it, and leaving untyped takes the price', async () => {
    let answer!: (v: unknown) => void;
    getLastCostMock.mockReturnValue(new Promise((r) => { answer = r; }));
    renderComposer({ initialLines: [LINE({ unitCost: 2800, catalogCost: 2800 })] });
    await waitFor(() => expect(getLastCostMock).toHaveBeenCalledWith('v-1', ['prod-a']));

    const cost = screen.getByLabelText(/unit cost for line 1/i) as HTMLInputElement;
    fireEvent.focus(cost);
    await act(async () => answer(paid(3100)));

    await waitFor(() => expect(screen.getByText(/last paid ₹3,100/i)).toBeInTheDocument());
    expect(cost.value).toBe('2800');
    fireEvent.blur(cost);
    expect(cost.value).toBe('3100');
  });

  it('Buy Desk (seed 0): leaving the tapped box untyped takes the price, and Create goes through', async () => {
    let answer!: (v: unknown) => void;
    getLastCostMock.mockReturnValue(new Promise((r) => { answer = r; }));
    const { onSubmit } = renderComposer({ initialLines: [LINE({ unitCost: 0 })] });
    await waitFor(() => expect(getLastCostMock).toHaveBeenCalledWith('v-1', ['prod-a']));

    const cost = screen.getByLabelText(/unit cost for line 1/i) as HTMLInputElement;
    fireEvent.focus(cost);
    await act(async () => answer(paid(3100)));
    await waitFor(() => expect(screen.getByText(/last paid ₹3,100/i)).toBeInTheDocument());
    expect(cost.value).toBe('0');
    fireEvent.blur(cost);
    expect(cost.value).toBe('3100');

    fireEvent.click(screen.getByRole('button', { name: /create as draft/i }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][0].items[0].unit_price).toBe(3100);
    expect(toastMock.error).not.toHaveBeenCalled();
  });

  it('a cost typed in the box always wins, whenever the answer lands', async () => {
    let answer!: (v: unknown) => void;
    getLastCostMock.mockReturnValue(new Promise((r) => { answer = r; }));
    renderComposer({ initialLines: [LINE({ unitCost: 2800, catalogCost: 2800 })] });
    await waitFor(() => expect(getLastCostMock).toHaveBeenCalled());

    const cost = screen.getByLabelText(/unit cost for line 1/i) as HTMLInputElement;
    fireEvent.focus(cost);
    fireEvent.change(cost, { target: { value: '2950' } });
    await act(async () => answer(paid(3100)));
    await waitFor(() => expect(screen.getByText(/last paid ₹3,100/i)).toBeInTheDocument());
    fireEvent.blur(cost);
    expect(cost.value).toBe('2950');
  });

  // The same taps give the same price whatever another line does later: a
  // lookup started by line 2 must not flip line 1 from one price to another.
  it('line 1 ends on the same price whether or not line 2 starts a lookup afterwards', async () => {
    let answer!: (v: unknown) => void;
    getLastCostMock
      .mockReturnValueOnce(new Promise((r) => { answer = r; }))
      .mockResolvedValue({
        costs: {
          'prod-a': { unit_price: 3100, po_number: 'PO-3', po_id: 'po-3', date: '2026-09-17T10:00:00' },
          'prod-b': { unit_price: 1500, po_number: 'PO-4', po_id: 'po-4', date: '2026-09-18T10:00:00' },
        },
      });
    renderComposer({
      initialLines: [
        LINE({ unitCost: 2800, catalogCost: 2800 }),
        LINE({ productId: '', productName: '', sku: '' }),
      ],
      renderProductCell: ({ index, pickProduct }) =>
        index === 1 ? (
          <button
            type="button"
            onClick={() => pickProduct({ productId: 'prod-b', productName: 'B', sku: 'B', costPrice: 1000 })}
          >
            pick line 2
          </button>
        ) : (
          <div />
        ),
    });
    await waitFor(() => expect(getLastCostMock).toHaveBeenCalledWith('v-1', ['prod-a']));

    const cost1 = screen.getByLabelText(/unit cost for line 1/i) as HTMLInputElement;
    fireEvent.focus(cost1);
    await act(async () => answer(paid(3100)));
    await waitFor(() => expect(screen.getByText(/last paid ₹3,100/i)).toBeInTheDocument());
    fireEvent.blur(cost1);
    expect(cost1.value).toBe('3100');

    fireEvent.click(screen.getByRole('button', { name: 'pick line 2' }));
    const cost2 = screen.getByLabelText(/unit cost for line 2/i) as HTMLInputElement;
    await waitFor(() => expect(cost2.value).toBe('1500'));
    expect(getLastCostMock).toHaveBeenLastCalledWith('v-1', ['prod-a', 'prod-b']);
    expect(cost1.value).toBe('3100');
  });

  it('a Qty keystroke does not hold the lookup back until the cost box is tapped', async () => {
    vi.useFakeTimers();
    try {
      getLastCostMock.mockResolvedValue({ costs: {} });
      renderComposer({ initialLines: [LINE({ unitCost: 2800 })] });
      act(() => { vi.advanceTimersByTime(200); });
      fireEvent.change(screen.getByLabelText(/quantity for line 1/i), { target: { value: '4' } });
      await act(async () => { vi.advanceTimersByTime(60); });
      expect(getLastCostMock).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("after a vendor switch the new vendor's price fills a box tapped before it", async () => {
    getLastCostMock.mockImplementation(async (vendorId: string) => paid(vendorId === 'v-1' ? 3100 : 2900));
    renderComposer({ initialLines: [LINE({ unitCost: 2800, catalogCost: 2800 })] });
    const cost = screen.getByLabelText(/unit cost for line 1/i) as HTMLInputElement;
    await waitFor(() => expect(cost.value).toBe('3100'));

    fireEvent.focus(cost);
    fireEvent.blur(cost);
    fireEvent.change(screen.getByLabelText('Vendor'), { target: { value: 'v-2' } });
    await waitFor(() => expect(cost.value).toBe('2900'));
    expect(screen.getByText(/last paid ₹2,900/i)).toBeInTheDocument();
  });

  // The guard lasts only while the box has focus: a tap the manager has left
  // behind, typing nothing, must not keep a vendor's last price out.
  it('a box tapped and left before the answer lands still takes the price', async () => {
    let answer!: (v: unknown) => void;
    getLastCostMock.mockReturnValue(new Promise((r) => { answer = r; }));
    renderComposer({ initialLines: [LINE({ unitCost: 2800, catalogCost: 2800 })] });
    await waitFor(() => expect(getLastCostMock).toHaveBeenCalled());

    const cost = screen.getByLabelText(/unit cost for line 1/i) as HTMLInputElement;
    fireEvent.focus(cost);
    fireEvent.blur(cost);
    await act(async () => answer(paid(3100)));
    await waitFor(() => expect(cost.value).toBe('3100'));
  });

  it('a tapped box takes the price of a vendor with history after one without', async () => {
    getLastCostMock.mockImplementation(async (vendorId: string) =>
      vendorId === 'v-2' ? paid(2900) : { costs: {} });
    renderComposer({ initialLines: [LINE({ unitCost: 2800, catalogCost: 2800 })] });
    await waitFor(() => expect(getLastCostMock).toHaveBeenCalledWith('v-1', ['prod-a']));

    const cost = screen.getByLabelText(/unit cost for line 1/i) as HTMLInputElement;
    fireEvent.focus(cost);
    fireEvent.blur(cost);
    fireEvent.change(screen.getByLabelText('Vendor'), { target: { value: 'v-2' } });
    await waitFor(() => expect(cost.value).toBe('2900'));
  });

  it('a Buy Desk box tapped before the vendor is set takes that vendor’s price', async () => {
    getLastCostMock.mockResolvedValue(paid(3100));
    const { rerender, onSubmit } = renderComposer({ initialVendorId: '', initialLines: [LINE({ unitCost: 0 })] });
    const cost = screen.getByLabelText(/unit cost for line 1/i) as HTMLInputElement;
    fireEvent.focus(cost);
    fireEvent.blur(cost);

    rerender(
      <PurchaseOrderComposer
        mode="modal"
        vendors={VENDORS}
        initialVendorId="v-1"
        initialLines={[LINE({ unitCost: 0 })]}
        renderProductCell={({ line }) => <div data-testid="product-cell">{line.productName}</div>}
        onSubmit={onSubmit}
      />,
    );
    await waitFor(() => expect(cost.value).toBe('3100'));
  });
});

// A failed last-paid lookup is handled in ONE place, the composer. The API
// client lets the failure through: it used to swallow it, so a network error
// looked like "this vendor was never paid" and the form never asked again.
describe('PurchaseOrderComposer — a failed last-paid lookup', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('the API client lets the failure through to the form', async () => {
    const { default: client } = await import('../../../services/api/client');
    (client.get as unknown as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new Error('Network Error'));
    const { vendorsApi: real } = await vi.importActual<typeof import('../../../services/api/inventory')>(
      '../../../services/api/inventory',
    );
    await expect(real.getLastCost('v-1', ['prod-a'])).rejects.toThrow('Network Error');
  });

  it('keeps the catalogue cost with no caption, and asks again on the next product change', async () => {
    getLastCostMock.mockRejectedValueOnce(new Error('Network Error')).mockResolvedValue({
      costs: { 'prod-a': { unit_price: 3100, po_number: 'PO-2', po_id: 'po-2', date: '2026-09-17T10:00:00' } },
    });
    renderComposer({
      initialLines: undefined,
      renderProductCell: ({ pickProduct }) => (
        <>
          <button type="button" onClick={() => pickProduct({ productId: 'prod-a', productName: 'A', sku: 'A', costPrice: 2800 })}>
            pick a
          </button>
          <button type="button" onClick={() => pickProduct({ productId: 'prod-b', productName: 'B', sku: 'B', costPrice: 5000 })}>
            pick b
          </button>
        </>
      ),
    });
    fireEvent.click(screen.getByRole('button', { name: 'pick a' }));
    await waitFor(() => expect(getLastCostMock).toHaveBeenCalledTimes(1));
    await act(async () => {});
    const cost = screen.getByLabelText(/unit cost for line 1/i) as HTMLInputElement;
    expect(cost.value).toBe('2800');
    expect(screen.queryByText(/last paid/i)).not.toBeInTheDocument();

    // Changed and changed back inside the lookup's short wait: the same
    // product set as the ask that failed -- it is asked again, not skipped.
    fireEvent.click(screen.getByRole('button', { name: 'pick b' }));
    fireEvent.click(screen.getByRole('button', { name: 'pick a' }));
    await waitFor(() => expect(cost.value).toBe('3100'));
    expect(getLastCostMock).toHaveBeenCalledTimes(2);
    expect(getLastCostMock).toHaveBeenLastCalledWith('v-1', ['prod-a']);
    expect(screen.getByText(/last paid ₹3,100/i)).toBeInTheDocument();
  });
});

describe('PurchaseOrderComposer — fail-soft when history is empty', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getLastCostMock.mockResolvedValue({ costs: {} });
  });

  it('leaves the cost blank, shows no caption, and still submits once filled', async () => {
    const { onSubmit } = renderComposer({ initialLines: [LINE({ unitCost: 0 })] });

    await waitFor(() => expect(getLastCostMock).toHaveBeenCalledWith('v-1', ['prod-a']));

    const costInput = screen.getByLabelText(/unit cost for line 1/i) as HTMLInputElement;
    // No history -> still blank, no caption.
    expect(costInput.value).toBe('0');
    expect(screen.queryByText(/last paid/i)).not.toBeInTheDocument();

    // The form still works: type a cost and submit.
    fireEvent.change(costInput, { target: { value: '1200' } });
    fireEvent.click(screen.getByRole('button', { name: /create as draft/i }));

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][0].items[0].unit_price).toBe(1200);
  });
});

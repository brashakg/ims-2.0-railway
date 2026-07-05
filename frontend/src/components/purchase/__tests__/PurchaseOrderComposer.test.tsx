// ============================================================================
// IMS 2.0 - PurchaseOrderComposer tests (procurement Phase 2C)
// ============================================================================
// Pins the shared PO body used by BOTH the manual form and the Buy Desk draft:
//   - validation gate: a line with zero cost blocks submit (no createPO call)
//   - cost prefill: a blank line fills from getLastCost + shows the caption
//   - prefill NEVER overwrites a cost the operator already typed
//   - fail-soft: getLastCost returns empty -> blank cost, no caption, form works

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';

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

import { PurchaseOrderComposer } from '../PurchaseOrderComposer';
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

  it('does NOT overwrite a cost the operator already typed', async () => {
    getLastCostMock.mockResolvedValue({
      costs: { 'prod-a': { unit_price: 3200, po_number: 'PO-1', po_id: 'po-1', date: '2026-06-30T10:00:00' } },
    });

    // Line already carries an operator-entered cost (costTouched).
    renderComposer({ initialLines: [LINE({ unitCost: 999, costTouched: true })] });

    const costInput = screen.getByLabelText(/unit cost for line 1/i) as HTMLInputElement;
    expect(costInput.value).toBe('999');

    // getLastCost should not even be asked about a line that already has a cost;
    // give any pending effect a tick, then assert the value is untouched and no
    // caption appeared.
    await new Promise((r) => setTimeout(r, 50));
    expect(costInput.value).toBe('999');
    expect(screen.queryByText(/last paid/i)).not.toBeInTheDocument();
    // No product needed a price -> the endpoint was never hit for it.
    expect(getLastCostMock).not.toHaveBeenCalledWith('v-1', ['prod-a']);
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

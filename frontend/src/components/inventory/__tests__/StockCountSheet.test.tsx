// ============================================================================
// The count sheet must be able to answer for a style that has walked entirely
// ============================================================================
// The only wired door was the barcode scanner, so a shortage was findable only
// while at least one unit of that style survived on the shelf. If the last one
// has gone, so has its label -- and that is exactly what a count is for. The
// sheet gives every expected line a box, and a typed ZERO is a real answer,
// never "not counted yet".

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const getStockCount = vi.fn();
const recordCountItem = vi.fn();

vi.mock('../../../services/api', () => ({
  default: { post: vi.fn() },
  inventoryApi: {
    getStockCount: (...a: unknown[]) => getStockCount(...a),
    recordCountItem: (...a: unknown[]) => recordCountItem(...a),
  },
}));

import { StockCountScanningInterface } from '../StockCountScanningInterface';

const SHEET = {
  expected_lines: [
    {
      product_id: 'PRD-GONE',
      product_name: 'Ray-Ban RB3025',
      sku: 'SKU-GONE',
      system_quantity: 2,
      counted_quantity: null,
    },
    {
      product_id: 'PRD-THERE',
      product_name: 'Vogue VO5001',
      sku: 'SKU-THERE',
      system_quantity: 3,
      counted_quantity: 3,
    },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  getStockCount.mockResolvedValue(SHEET);
  recordCountItem.mockResolvedValue({ items_counted: 2 });
});

describe('the count sheet', () => {
  it('lists every line the session expects, not just the ones with a label left', async () => {
    render(<StockCountScanningInterface countId="C-1" />);
    expect(await screen.findByText('Ray-Ban RB3025')).toBeInTheDocument();
    expect(screen.getByText('Vogue VO5001')).toBeInTheDocument();
    expect(screen.getByText('1 of 2 lines answered')).toBeInTheDocument();
  });

  it('records a typed ZERO for a style with nothing left on the shelf', async () => {
    render(<StockCountScanningInterface countId="C-1" />);
    const box = await screen.findByLabelText('Counted quantity for Ray-Ban RB3025');

    fireEvent.change(box, { target: { value: '0' } });
    fireEvent.blur(box);

    await waitFor(() =>
      expect(recordCountItem).toHaveBeenCalledWith('C-1', {
        product_id: 'PRD-GONE',
        product_name: 'Ray-Ban RB3025',
        sku: 'SKU-GONE',
        counted_quantity: 0,
      })
    );
    // and a counted zero must read as counted, not as "not counted yet"
    expect(await screen.findByText('counted 0')).toBeInTheDocument();
    expect(screen.queryByText('not counted')).not.toBeInTheDocument();
  });

  it('never posts an empty box as a count', async () => {
    render(<StockCountScanningInterface countId="C-1" />);
    const box = await screen.findByLabelText('Counted quantity for Ray-Ban RB3025');
    fireEvent.blur(box);
    await waitFor(() => expect(getStockCount).toHaveBeenCalled());
    expect(recordCountItem).not.toHaveBeenCalled();
  });
});

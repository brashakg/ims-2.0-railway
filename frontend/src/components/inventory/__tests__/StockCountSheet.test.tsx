// ============================================================================
// A style whose last piece has no label can still be answered -- by typing
// ============================================================================
// If the last piece of a style has gone (or its label tore off), there is
// nothing to scan. The counter finds the style in the catalogue and types a
// quantity; 0 is a real answer ("none of this style here"). The typed number
// sits BESIDE the scanned pieces, never in place of them (owner ruling S1).

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const getStockCount = vi.fn();
const recordCountItem = vi.fn();
const searchProducts = vi.fn();

vi.mock('../../../services/api', () => ({
  inventoryApi: {
    getStockCount: (...a: unknown[]) => getStockCount(...a),
    recordCountItem: (...a: unknown[]) => recordCountItem(...a),
    scanCountPiece: vi.fn(),
  },
  productApi: { searchProducts: (...a: unknown[]) => searchProducts(...a) },
}));

import { StockCountScanningInterface, countedStyles } from '../StockCountScanningInterface';

beforeEach(() => {
  vi.clearAllMocks();
  getStockCount.mockResolvedValue({ status: 'in_progress', scans: [], items: [], pieces_scanned: 0 });
  recordCountItem.mockResolvedValue({ items_counted: 1 });
  searchProducts.mockResolvedValue({
    products: [{ product_id: 'PRD-GONE', brand: 'Vogue', model: 'VO2787', sku: 'SKU-GONE' }],
  });
});

describe('typing a quantity for a style with no label', () => {
  it('finds the style and records a typed ZERO', async () => {
    render(<StockCountScanningInterface countId="C-1" />);
    fireEvent.change(await screen.findByLabelText('Find a style'), { target: { value: 'Vogue' } });
    fireEvent.click(await screen.findByText(/Vogue VO2787/));

    const box = await screen.findByLabelText('Typed quantity for Vogue VO2787');
    fireEvent.change(box, { target: { value: '0' } });
    fireEvent.keyDown(box, { key: 'Enter' });

    await waitFor(() =>
      expect(recordCountItem).toHaveBeenCalledWith('C-1', {
        product_id: 'PRD-GONE',
        product_name: 'Vogue VO2787',
        sku: 'SKU-GONE',
        counted_quantity: 0,
      })
    );
  });
});

describe('countedStyles', () => {
  it('keeps scanned pieces and the typed number apart', () => {
    const rows = countedStyles({
      items: [{ product_id: 'P1', product_name: 'A', sku: 'S1', typed_quantity: 1 }],
      scans: [
        { product_id: 'P1', product_name: 'A', sku: 'S1' },
        { product_id: 'P1', product_name: 'A', sku: 'S1' },
        { product_id: 'P2', product_name: 'B', sku: 'S2' },
      ],
    });
    expect(rows).toEqual([
      { product_id: 'P1', product_name: 'A', sku: 'S1', scanned: 2, typed: 1 },
      { product_id: 'P2', product_name: 'B', sku: 'S2', scanned: 1, typed: null },
    ]);
  });
});

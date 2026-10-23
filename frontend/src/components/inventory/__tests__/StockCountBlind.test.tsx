// ============================================================================
// THE COUNT IS BLIND (owner ruling 2026-08-25) -- and a scan is ONE piece (S1)
// ============================================================================
// While a session is open the counter must not see what the books expect.
// The server withholds the figures at the source (see
// backend/tests/test_stock_count_blind.py); this suite guards the second line
// of defence: even if a stale cache or an old server ships the numbers (or the
// old expected-styles sheet) anyway, the counting screen must not render
// them. Every fixture deliberately INCLUDES the withheld fields with
// unmistakable sentinel values, and the assertions are that those values
// never reach the DOM.

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const getStockCount = vi.fn();
const recordCountItem = vi.fn();
const scanCountPiece = vi.fn();

vi.mock('../../../services/api', () => ({
  inventoryApi: {
    getStockCount: (...a: unknown[]) => getStockCount(...a),
    recordCountItem: (...a: unknown[]) => recordCountItem(...a),
    scanCountPiece: (...a: unknown[]) => scanCountPiece(...a),
  },
  productApi: { searchProducts: vi.fn(async () => ({ products: [] })) },
}));

import { StockCountScanningInterface } from '../StockCountScanningInterface';

// An OPEN session as an OLD server would send it: the expected-styles sheet
// with system quantities. 777 and "Hidden Style" appear nowhere else, so
// finding either in the DOM = a leak.
const HOSTILE_DOC = {
  status: 'in_progress',
  system_quantities: { 'PRD-9': 777 },
  expected_lines: [
    { product_id: 'PRD-9', product_name: 'Hidden Style', sku: 'SKU-9', system_quantity: 777, counted_quantity: null },
  ],
  scans: [],
  items: [],
  pieces_scanned: 0,
};

beforeEach(() => {
  vi.clearAllMocks();
  getStockCount.mockResolvedValue(HOSTILE_DOC);
});

describe('the blind count screen', () => {
  it('never lists the styles the books expect, nor how many', async () => {
    render(<StockCountScanningInterface countId="C-1" />);
    await waitFor(() => expect(getStockCount).toHaveBeenCalled());
    expect(await screen.findByTestId('pieces-scanned')).toHaveTextContent('0');
    expect(screen.queryByText('Hidden Style')).toBeNull();
    expect(screen.queryByText(/777/)).toBeNull();
  });

  it('a scan sends ONE piece (no quantity) and shows only "scanned"', async () => {
    scanCountPiece.mockResolvedValue({
      recorded: true, added: true, product_name: 'Ray-Ban RB3025', sku: 'SKU-1',
      pieces_scanned: 1, items_counted: 1,
      // what the OLD endpoint used to echo -- must never reach the DOM
      system_count: 888, variance: -885,
    });
    // after the scan the session re-reads with the piece on it
    getStockCount.mockResolvedValueOnce(HOSTILE_DOC).mockResolvedValue({ ...HOSTILE_DOC, pieces_scanned: 1 });
    render(<StockCountScanningInterface countId="C-1" />);
    const box = await screen.findByLabelText("Scan a piece's barcode");
    fireEvent.change(box, { target: { value: 'BV91FA3858A2' } });
    fireEvent.keyDown(box, { key: 'Enter' });

    expect(await screen.findByText('Last: Ray-Ban RB3025 · scanned')).toBeInTheDocument();
    expect(scanCountPiece).toHaveBeenCalledWith('C-1', 'BV91FA3858A2');
    expect(screen.getByTestId('pieces-scanned')).toHaveTextContent('1');
    expect(screen.queryByText(/888/)).toBeNull();
    expect(screen.queryByText(/-885/)).toBeNull();
    expect(screen.queryByText(/variance/i)).toBeNull();
  });

  it('the same label scanned twice is said to count once', async () => {
    scanCountPiece.mockResolvedValue({
      recorded: true, added: false, product_name: 'Ray-Ban RB3025', sku: 'SKU-1',
      pieces_scanned: 1, items_counted: 1,
    });
    // after the scan the session re-reads with the piece on it
    getStockCount.mockResolvedValueOnce(HOSTILE_DOC).mockResolvedValue({ ...HOSTILE_DOC, pieces_scanned: 1 });
    render(<StockCountScanningInterface countId="C-1" />);
    const box = await screen.findByLabelText("Scan a piece's barcode");
    fireEvent.change(box, { target: { value: 'BV91FA3858A2' } });
    fireEvent.keyDown(box, { key: 'Enter' });
    expect(
      await screen.findByText('Last: Ray-Ban RB3025 · already scanned, counted once')
    ).toBeInTheDocument();
    expect(screen.getByTestId('pieces-scanned')).toHaveTextContent('1');
  });
});

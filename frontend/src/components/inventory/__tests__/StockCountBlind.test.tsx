// ============================================================================
// THE COUNT IS BLIND (owner ruling 2026-08-25)
// ============================================================================
// While a session is open the counter must not see what the books expect.
// The server withholds the figures at the source (see
// backend/tests/test_stock_count_blind.py -- THE key tests assert on the
// response body); this suite guards the second line of defence: even if a
// stale cache or a hostile/old server ships the numbers anyway, the counting
// screen must not render them. Every fixture here deliberately INCLUDES the
// withheld fields with unmistakable sentinel values, and the assertions are
// that those values never reach the DOM.

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const getStockCount = vi.fn();
const recordCountItem = vi.fn();
const apiPost = vi.fn();

vi.mock('../../../services/api', () => ({
  default: { post: (...a: unknown[]) => apiPost(...a) },
  inventoryApi: {
    getStockCount: (...a: unknown[]) => getStockCount(...a),
    recordCountItem: (...a: unknown[]) => recordCountItem(...a),
  },
}));

import { StockCountScanningInterface } from '../StockCountScanningInterface';

// A sheet as an OLD server would send it: system_quantity present. 777 is the
// sentinel -- it appears nowhere else, so finding it in the DOM = a leak.
const HOSTILE_SHEET = {
  status: 'in_progress',
  expected_lines: [
    {
      product_id: 'PRD-1',
      product_name: 'Ray-Ban RB3025',
      sku: 'SKU-1',
      system_quantity: 777,
      counted_quantity: null,
    },
  ],
};

// A scan response as the OLD endpoint sent it: live variance readout.
const HOSTILE_SCAN = {
  data: {
    barcode: 'BC-1',
    product_id: 'PRD-1',
    product_name: 'Ray-Ban RB3025',
    sku: 'SKU-1',
    system_count: 888,
    physical_count: 3,
    variance: -885,
    variance_percent: -99.66,
    count_id: 'C-1',
    recorded: true,
    items_counted: 1,
  },
};

beforeEach(() => {
  vi.clearAllMocks();
  getStockCount.mockResolvedValue(HOSTILE_SHEET);
  recordCountItem.mockResolvedValue({ items_counted: 1 });
  apiPost.mockResolvedValue(HOSTILE_SCAN);
});

describe('the blind count sheet', () => {
  it('never shows a Books-say column, even when the payload carries the figure', async () => {
    render(<StockCountScanningInterface countId="C-1" />);
    expect(await screen.findByText('Ray-Ban RB3025')).toBeInTheDocument();

    expect(screen.queryByText('Books say')).toBeNull();
    // the sentinel expected quantity must not surface anywhere
    expect(screen.queryByText(/777/)).toBeNull();
    // the sheet stays usable blind: a box to answer into
    expect(
      screen.getByLabelText('Counted quantity for Ray-Ban RB3025')
    ).toBeInTheDocument();
  });

  it('acknowledges a scan without echoing system count or variance', async () => {
    render(<StockCountScanningInterface countId="C-1" />);
    await screen.findByText('Ray-Ban RB3025');

    fireEvent.change(screen.getByPlaceholderText('Scan or enter barcode...'), {
      target: { value: 'BC-1' },
    });
    fireEvent.change(screen.getByPlaceholderText('0'), {
      target: { value: '3' },
    });
    fireEvent.click(screen.getByText('Record counted quantity'));

    expect(await screen.findByText('Recorded')).toBeInTheDocument();
    // the hostile response's figures must never reach the DOM
    expect(screen.queryByText(/888/)).toBeNull();
    expect(screen.queryByText(/-885/)).toBeNull();
    expect(screen.queryByText(/variance/i)).toBeNull();
    expect(screen.queryByText(/system count/i)).toBeNull();
    // ...while the counter's own answer is confirmed saved
    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith('/inventory/stock-count-scan', {
        barcode: 'BC-1',
        physical_count: 3,
        notes: undefined,
        count_id: 'C-1',
      })
    );
  });
});

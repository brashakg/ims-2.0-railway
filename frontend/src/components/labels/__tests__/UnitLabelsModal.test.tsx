// ============================================================================
// F26 / F27 - the units view and its three print doors
// ============================================================================
// F27: receiving serialises every piece but no screen listed them.
// F26: "Print labels" after receiving toasted success and printed nothing.
// Pinned here: the list shows each unit (barcode, receipt number, status);
// the print doors (selected units / all units just received / reprint one)
// open ONE print dialog with exactly those units' labels, and barcode_printed
// is recorded on exactly the units sent -- never when the dialog did not open.

// This Node/jsdom runner ships a partial localStorage: the repo's Map stand-in.
(() => {
  const m = new Map<string, string>();
  const ls = {
    getItem: (k: string) => (m.has(k) ? m.get(k)! : null),
    setItem: (k: string, v: string) => { m.set(k, String(v)); },
    removeItem: (k: string) => { m.delete(k); },
    clear: () => { m.clear(); },
    key: (i: number) => Array.from(m.keys())[i] ?? null,
    get length() { return m.size; },
  };
  Object.defineProperty(globalThis, 'localStorage', { value: ls, configurable: true, writable: true });
})();

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render as rtlRender, screen, fireEvent, waitFor, within } from '@testing-library/react';
import type { ReactElement } from 'react';
import { MemoryRouter } from 'react-router-dom';
import type { StockUnit } from '../../../services/api/inventory';

const toastMock = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }));
const apiMock = vi.hoisted(() => ({ getUnits: vi.fn(), markBarcodePrinted: vi.fn() }));
const printMock = vi.hoisted(() => vi.fn());
const roles = vi.hoisted(() => ({ current: ['STORE_MANAGER'] as string[] }));

vi.mock('../../../services/api/inventory', () => ({ inventoryApi: apiMock }));
vi.mock('../../../services/printWindow', () => ({ printHtmlFallback: printMock }));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toastMock }));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { activeStoreId: 'BV-DHN-02', roles: roles.current },
    hasRole: (r: string[]) => r.some((x) => roles.current.includes(x)),
  }),
}));

import { UnitLabelsModal } from '../UnitLabelsModal';

const render = (ui: ReactElement) => rtlRender(<MemoryRouter>{ui}</MemoryRouter>);

function unit(n: number, over: Partial<StockUnit> = {}): StockUnit {
  return {
    stock_id: `STK-${n}`,
    product_id: 'P1',
    barcode: `BV--0000000${n}`,
    status: 'AVAILABLE',
    grn_number: 'RCPT/BV-DHN-02/26-27/0009',
    source: 'GRN',
    received_on: '2026-09-27',
    barcode_printed: false,
    location_code: 'DEFAULT',
    name: 'Carrera CA 8895',
    brand: 'Carrera',
    model: 'CA 8895',
    colour: '807',
    size: '54',
    mrp: 8990,
    ...over,
  };
}

/** The HTML handed to the print dialog, and which unit barcodes it carries. */
function printedBarcodes(): string[] {
  expect(printMock).toHaveBeenCalledTimes(1);
  const html = printMock.mock.calls[0][0] as string;
  return Array.from(html.matchAll(/<div class="txt">([^<]*)<\/div>/g)).map((m) => m[1]);
}

beforeEach(() => {
  roles.current = ['STORE_MANAGER'];
  printMock.mockReturnValue({ method: 'html', message: 'Opened label in a print window.' });
  apiMock.markBarcodePrinted.mockImplementation(async (ids: string[]) => ({ updated: ids.length, stock_ids: ids }));
});

describe('the units view (from the stock ledger)', () => {
  it('lists every unit with its barcode, receipt number and status', async () => {
    apiMock.getUnits.mockResolvedValue({ units: [unit(1), unit(2, { status: 'SOLD' })], total: 2 });
    render(<UnitLabelsModal productId="P1" title="Carrera CA 8895" onClose={() => {}} />);
    expect(await screen.findByText('BV--00000001')).toBeInTheDocument();
    expect(apiMock.getUnits).toHaveBeenCalledWith({ store_id: 'BV-DHN-02', product_id: 'P1' });
    const sold = screen.getByText('BV--00000002').closest('tr')!;
    expect(within(sold).getByText('Sold')).toBeInTheDocument();
    expect(within(sold).getByText('RCPT/BV-DHN-02/26-27/0009')).toBeInTheDocument();
    // A sold unit is gone from the shop: nothing to label.
    expect(within(sold).getByRole('checkbox')).toBeDisabled();
    // No cost for a role the server withheld it from.
    expect(screen.queryByText(/^Cost$/)).not.toBeInTheDocument();
  });

  it('words every status, and a unit being counted can still get its label', async () => {
    // A count is exactly when a missing label turns up; the counted unit is on
    // the shelf. A written-off unit is not, and no status shows as a raw token.
    apiMock.getUnits.mockResolvedValue({
      units: [unit(1, { status: 'BLIND_COUNT' }), unit(2, { status: 'UNDER_AUDIT' }), unit(3, { status: 'VOID' })],
      total: 3,
    });
    render(<UnitLabelsModal productId="P1" title="x" onClose={() => {}} />);
    const row = async (n: number) => (await screen.findByText(`BV--0000000${n}`)).closest('tr')!;
    for (const [n, text] of [[1, 'In a stock count'], [2, 'Under audit']] as const) {
      const r = await row(n);
      expect(within(r).getByText(text)).toBeInTheDocument();
      expect(within(r).getByRole('checkbox')).toBeEnabled();
      expect(within(r).getByRole('button', { name: /reprint label/i })).toBeInTheDocument();
    }
    const voided = await row(3);
    expect(within(voided).getByText('Written off')).toBeInTheDocument();
    expect(within(voided).getByRole('checkbox')).toBeDisabled();
    expect(within(voided).queryByRole('button', { name: /reprint label/i })).not.toBeInTheDocument();
    fireEvent.click(within(await row(1)).getByRole('button', { name: /reprint label/i }));
    expect(printedBarcodes()).toEqual(['BV--00000001']);
  });

  it('shows cost only when the server sent it', async () => {
    apiMock.getUnits.mockResolvedValue({ units: [unit(1, { cost_price: 4200 })], total: 1 });
    render(<UnitLabelsModal productId="P1" title="x" onClose={() => {}} />);
    expect(await screen.findByText('Cost')).toBeInTheDocument();
    expect(screen.getByText('₹4,200')).toBeInTheDocument();
  });

  it('prints the SELECTED units and records exactly those as printed', async () => {
    apiMock.getUnits.mockResolvedValue({ units: [unit(1), unit(2), unit(3)], total: 3 });
    render(<UnitLabelsModal productId="P1" title="x" onClose={() => {}} />);
    await screen.findByText('BV--00000001');
    fireEvent.click(within(screen.getByText('BV--00000001').closest('tr')!).getByRole('checkbox'));
    fireEvent.click(within(screen.getByText('BV--00000003').closest('tr')!).getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: /print 2 labels/i }));
    expect(printedBarcodes()).toEqual(['BV--00000001', 'BV--00000003']);
    await waitFor(() => expect(apiMock.markBarcodePrinted).toHaveBeenCalledWith(['STK-1', 'STK-3']));
  });

  it('reprints ONE unit', async () => {
    apiMock.getUnits.mockResolvedValue({ units: [unit(1), unit(2, { barcode_printed: true })], total: 2 });
    render(<UnitLabelsModal productId="P1" title="x" onClose={() => {}} />);
    await screen.findByText('BV--00000002');
    fireEvent.click(
      within(screen.getByText('BV--00000002').closest('tr')!).getByRole('button', { name: /reprint/i }),
    );
    expect(printedBarcodes()).toEqual(['BV--00000002']);
    await waitFor(() => expect(apiMock.markBarcodePrinted).toHaveBeenCalledWith(['STK-2']));
  });

  it('records nothing and says so when the print dialog could not open', async () => {
    printMock.mockReturnValue({ method: 'failed', message: 'Could not open a print window (popup blocked?).' });
    apiMock.getUnits.mockResolvedValue({ units: [unit(1)], total: 1 });
    render(<UnitLabelsModal productId="P1" title="x" onClose={() => {}} />);
    await screen.findByText('BV--00000001');
    fireEvent.click(screen.getByRole('button', { name: /reprint/i }));
    expect(toastMock.error).toHaveBeenCalled();
    expect(toastMock.success).not.toHaveBeenCalled();
    expect(apiMock.markBarcodePrinted).not.toHaveBeenCalled();
  });

  it('offers no label for a shelved unit with no barcode, and never records one', async () => {
    // A return re-shelves a piece as a fresh unit; a historical one has no
    // barcode. Its "label" had no bars and IMS still recorded it as printed.
    apiMock.getUnits.mockResolvedValue({ units: [unit(1, { barcode: '' }), unit(2)], total: 2 });
    render(<UnitLabelsModal productId="P1" title="x" onClose={() => {}} />);
    const bare = (await screen.findByText('No barcode')).closest('tr')!;
    expect(within(bare).getByRole('checkbox')).toBeDisabled();
    expect(within(bare).queryByRole('button', { name: /reprint/i })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox', { name: /select every unit/i }));
    fireEvent.click(screen.getByRole('button', { name: /print 1 label$/i }));
    expect(printedBarcodes()).toEqual(['BV--00000002']);
    await waitFor(() => expect(apiMock.markBarcodePrinted).toHaveBeenCalledWith(['STK-2']));
  });

  it('says "Sent to print", never "Printed" (a cancelled print dialog cannot be seen)', async () => {
    apiMock.getUnits.mockResolvedValue({ units: [unit(1), unit(2, { barcode_printed: true })], total: 2 });
    render(<UnitLabelsModal productId="P1" title="x" onClose={() => {}} />);
    const sent = (await screen.findByText('BV--00000002')).closest('tr')!;
    expect(within(sent).getByText('Sent to print')).toBeInTheDocument();
    fireEvent.click(
      within(screen.getByText('BV--00000001').closest('tr')!).getByRole('button', { name: /reprint/i }),
    );
    const first = screen.getByText('BV--00000001').closest('tr')!;
    expect(await within(first).findByText('Sent to print')).toBeInTheDocument();
    expect(screen.queryByText(/^Printed$/)).not.toBeInTheDocument();
  });

  it('shows a transferred-in unit by its transfer, not the source shop receipt', async () => {
    apiMock.getUnits.mockResolvedValue({
      units: [
        unit(1, {
          source: 'TRANSFER',
          grn_number: 'RCPT/1',
          transfer_number: 'TRF/BV-DHN-02/0001',
          from_store_id: 'BV-DHN-02',
          received_on: '2026-09-29',
        }),
      ],
      total: 1,
    });
    render(<UnitLabelsModal productId="P1" storeId="BV-BOK-01" title="x" onClose={() => {}} />);
    const row = (await screen.findByText('BV--00000001')).closest('tr')!;
    expect(within(row).getByText('Transfer TRF/BV-DHN-02/0001 from BV-DHN-02')).toBeInTheDocument();
    expect(within(row).queryByText('RCPT/1')).not.toBeInTheDocument();
    expect(within(row).getByText('2026-09-29')).toBeInTheDocument();
  });

  it('offers no print door to a role that cannot record labels', async () => {
    roles.current = ['SALES_STAFF'];
    apiMock.getUnits.mockResolvedValue({ units: [unit(1)], total: 1 });
    render(<UnitLabelsModal productId="P1" title="x" onClose={() => {}} />);
    await screen.findByText('BV--00000001');
    expect(screen.queryByRole('button', { name: /print|reprint/i })).not.toBeInTheDocument();
  });
});

describe('the dialog after receiving (grn door)', () => {
  it('prints ALL units the receipt put on the shelf', async () => {
    apiMock.getUnits.mockResolvedValue({ units: [unit(1), unit(2), unit(3)], total: 3 });
    render(<UnitLabelsModal grnId="GRN-9" title="Print stock labels?" onClose={() => {}} />);
    // A receipt is read by its own id: its units are at the shop it was
    // received into, which need not be the active one (the server scopes it).
    expect(apiMock.getUnits).toHaveBeenCalledWith({ grn_id: 'GRN-9' });
    fireEvent.click(await screen.findByRole('button', { name: /print 3 labels/i }));
    expect(printedBarcodes()).toEqual(['BV--00000001', 'BV--00000002', 'BV--00000003']);
    await waitFor(() => expect(apiMock.markBarcodePrinted).toHaveBeenCalledWith(['STK-1', 'STK-2', 'STK-3']));
    expect(toastMock.success).toHaveBeenCalled();
  });

  it('pre-selects only the units whose label was never sent (a re-accepted receipt)', async () => {
    // First accept shelved 3 units and printed their labels; the held line was
    // catalogued and "Add to stock" shelved 2 more. Re-labelling the first 3
    // puts duplicate barcodes in the shop.
    apiMock.getUnits.mockResolvedValue({
      units: [
        unit(1, { barcode_printed: true }),
        unit(2, { barcode_printed: true }),
        unit(3, { barcode_printed: true }),
        unit(4),
        unit(5),
      ],
      total: 5,
    });
    render(<UnitLabelsModal grnId="GRN-9" title="Print stock labels?" onClose={() => {}} />);
    fireEvent.click(await screen.findByRole('button', { name: /print 2 labels/i }));
    expect(printedBarcodes()).toEqual(['BV--00000004', 'BV--00000005']);
    await waitFor(() => expect(apiMock.markBarcodePrinted).toHaveBeenCalledWith(['STK-4', 'STK-5']));
  });

  it('says there is nothing to print instead of a fake success', async () => {
    apiMock.getUnits.mockResolvedValue({ units: [], total: 0 });
    render(<UnitLabelsModal grnId="GRN-9" title="Print stock labels?" onClose={() => {}} />);
    expect(await screen.findByText(/put no units on the shelf/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /print \d+ label/i })).not.toBeInTheDocument();
    expect(printMock).not.toHaveBeenCalled();
    expect(toastMock.success).not.toHaveBeenCalled();
  });
});

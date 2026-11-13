// ============================================================================
// Reports cards: Export CSV and Print carry the card's OWN report
// ============================================================================
// Audit L3-08: Stock Report / Stock Movement / Customer Report / GST Report
// CSVs all held the same generic sales-summary row; daily/monthly sales were
// one aggregate row under blank Date/Order/Customer/Items headers.
// Audit L3-13: Print did nothing on the split pages (it gated on a
// `.space-y-4` element those pages no longer have).

import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

const { exportToCSV, toastError } = vi.hoisted(() => ({ exportToCSV: vi.fn(), toastError: vi.fn() }));

vi.mock('../../../utils/exportUtils', () => ({ exportToCSV }));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: toastError, info: vi.fn(), warning: vi.fn() }),
}));
vi.mock('../ReportsLayout', () => ({
  useReportsContext: () => ({
    storeId: 'BV-RAN-01',
    dateRange: 'month',
    startDate: '2026-10-13',
    endDate: '2026-11-13',
    canExport: true,
  }),
}));
vi.mock('../reportsQueries', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../reportsQueries')>()),
  useSalesSummary: () => ({
    data: {
      summary: { totalSales: 9999, orderCount: 9, averageOrderValue: 1111, topCategory: '-', grossProfit: 0, gstCollected: 99 },
      dailyTrend: [
        { date: '2026-11-12', sales: 1500, orders: 2 },
        { date: '2026-11-13', sales: 2500.5, orders: 3 },
      ],
      categoryBreakdown: [{ category: 'FRAME', sales: 4000.5, units: 4, percentage: 100 }],
    },
  }),
  useStockCount: () => ({
    data: {
      data: [
        { category: 'FRAME', item_count: 12, total_quantity: 40, total_value: 52000 },
        { category: 'SUNGLASS', item_count: 3, total_quantity: 7, total_value: 9100 },
      ],
      summary: { total_items: 15, total_quantity: 47, total_value: 61100 },
    },
  }),
  useCustomerAcquisition: () => ({
    data: { new_customers: 21, returning_customers: 8, total_customers: 29, retention_percent: 27.6 },
  }),
}));

import { ReportCardsGrid } from '../ReportCardsGrid';

function card(title: string) {
  return within(screen.getByText(title).closest('.card') as HTMLElement);
}

function renderSection(category: 'sales' | 'inventory' | 'customers' | 'gst') {
  render(
    <MemoryRouter>
      <ReportCardsGrid category={category} />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  exportToCSV.mockClear();
  toastError.mockClear();
});

describe('Report card exports are the named report', () => {
  it('Stock Report exports stock by category, not a sales summary', () => {
    renderSection('inventory');
    fireEvent.click(card('Stock Report').getByRole('button', { name: 'Export CSV' }));
    const [rows, , columns] = exportToCSV.mock.calls[0];
    expect(rows).toEqual([
      { category: 'FRAME', item_count: 12, total_quantity: 40, total_value: '52000.00' },
      { category: 'SUNGLASS', item_count: 3, total_quantity: 7, total_value: '9100.00' },
    ]);
    expect(columns.map((c: { label: string }) => c.label)).not.toContain('Total Sales (₹)');
    expect(JSON.stringify(rows)).not.toContain('9999');
  });

  it('Customer Report exports the acquisition figures', () => {
    renderSection('customers');
    fireEvent.click(card('Customer Report').getByRole('button', { name: 'Export CSV' }));
    expect(exportToCSV.mock.calls[0][0]).toEqual([
      expect.objectContaining({ new_customers: 21, returning_customers: 8, total_customers: 29, retention_percent: 27.6 }),
    ]);
  });

  it('Daily Sales exports one row per day with no blank order/customer columns', () => {
    renderSection('sales');
    fireEvent.click(card('Daily Sales Report').getByRole('button', { name: 'Export CSV' }));
    const [rows, , columns] = exportToCSV.mock.calls[0];
    expect(rows).toEqual([
      { date: '2026-11-12', orders: 2, sales: '1500.00' },
      { date: '2026-11-13', orders: 3, sales: '2500.50' },
    ]);
    expect(columns.map((c: { key: string }) => c.key)).toEqual(['date', 'orders', 'sales']);
  });

  it('Stock Movement and GST offer no fake export or print (View opens the real screen)', () => {
    renderSection('inventory');
    expect(card('Stock Movement').queryByRole('button', { name: 'Export CSV' })).toBeNull();
    expect(card('Stock Movement').queryByRole('button', { name: 'Print' })).toBeNull();
    expect(card('Stock Movement').getByRole('button', { name: 'View' })).toBeInTheDocument();
  });

  it('GST card has no fake export or print', () => {
    renderSection('gst');
    expect(card('GST Report').queryByRole('button', { name: 'Export CSV' })).toBeNull();
    expect(card('GST Report').queryByRole('button', { name: 'Print' })).toBeNull();
  });
});

describe('Report card Print opens and prints the report', () => {
  it.each([
    ['inventory', 'Stock Report', 'SUNGLASS'],
    ['customers', 'Customer Report', '21'],
  ] as const)('%s / %s', (section, title, expected) => {
    const write = vi.fn();
    const print = vi.fn();
    const win = { document: { write, close: vi.fn() }, focus: vi.fn(), print, close: vi.fn() };
    const open = vi.spyOn(window, 'open').mockReturnValue(win as unknown as Window);
    renderSection(section);
    fireEvent.click(card(title).getByRole('button', { name: 'Print' }));
    expect(open).toHaveBeenCalledTimes(1);
    expect(write.mock.calls[0][0]).toContain(title);
    expect(write.mock.calls[0][0]).toContain(expected);
    expect(print).toHaveBeenCalledTimes(1);
    open.mockRestore();
  });

  it('a blocked pop-up is said, not silent', () => {
    const open = vi.spyOn(window, 'open').mockReturnValue(null);
    renderSection('inventory');
    fireEvent.click(card('Stock Report').getByRole('button', { name: 'Print' }));
    expect(toastError).toHaveBeenCalled();
    open.mockRestore();
  });
});

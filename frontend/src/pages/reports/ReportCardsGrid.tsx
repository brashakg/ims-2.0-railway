// ============================================================================
// IMS 2.0 - Reports "Available Reports" card grid
// ============================================================================
// Lifted verbatim out of the old ReportsPage. It rendered on every tab,
// filtered to the active tab's category, so it now renders on every section
// page with the category fixed by that page. Export CSV and Print read the
// card's own dataset (audit L3-08 / L3-13: they used to emit a sales summary
// for every card, and Print returned early on a selector the split pages lack).
//
// Two links changed target because the thing they opened moved: the GST card
// used to open the GSTR-1 MODAL; it now navigates to the GSTR-1 PAGE. The
// daily/monthly-sales "View" used to flip a useState tab; it now navigates to
// the Sales URL and scrolls to the same element.
//
// The two inventory cards linked `/inventory?tab=transfers` / `?tab=stock`
// until the Wave 2 split gave those sections real addresses; they now link
// /inventory/transfers and /inventory/stock directly (?tab=stock was never a
// valid tab and always fell through to the stock ledger, which is where the
// legacy redirect still sends it).

import { useNavigate } from 'react-router-dom';
import {
  BarChart3,
  TrendingUp,
  Download,
  Package,
  Users,
  FileText,
  Eye,
  Printer,
} from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { exportToCSV } from '../../utils/exportUtils';
import { useReportsContext } from './ReportsLayout';
import {
  useCustomerAcquisition,
  useSalesSummary,
  useStockCount,
  type ReportSection,
} from './reportsQueries';

// Report cards. Only a card with a real dataset (`data: true`) offers Export
// CSV / Print, and both read the SAME rows (audit L3-08 / L3-13). Stock
// Movement and GST have no dataset of their own here: their View opens the
// screen that has (the transfers ledger, the GSTR-1 page with its downloads).
const REPORT_CARDS = [
  {
    id: 'daily-sales',
    title: 'Daily Sales Report',
    description: 'Day-wise sales and order count for the period',
    icon: BarChart3,
    category: 'sales' as ReportSection,
    data: true,
  },
  {
    id: 'monthly-sales',
    title: 'Monthly Sales Summary',
    description: 'Sales by category for the period',
    icon: TrendingUp,
    category: 'sales' as ReportSection,
    data: true,
  },
  {
    id: 'stock-report',
    title: 'Stock Report',
    description: 'Current stock levels by category',
    icon: Package,
    category: 'inventory' as ReportSection,
    data: true,
  },
  {
    id: 'stock-movement',
    title: 'Stock Movement',
    description: 'Stock in/out movements and transfers',
    icon: Package,
    category: 'inventory' as ReportSection,
    data: false,
  },
  {
    id: 'customer-report',
    title: 'Customer Report',
    description: 'New and returning customers for the period',
    icon: Users,
    category: 'customers' as ReportSection,
    data: true,
  },
  {
    id: 'gst-report',
    title: 'GST Report',
    // The card's one link is GSTR-1. GSTR-3B (input tax from supplier bills)
    // is the accounts roles' (R1), offered on the filing card below to them.
    description: 'GSTR-1 data for filing',
    icon: FileText,
    category: 'gst' as ReportSection,
    data: false,
  },
];

type Column = { key: string; label: string };
type Table = { columns: Column[]; rows: Record<string, string | number>[] };

const esc = (v: unknown) =>
  String(v ?? '').replace(/[&<>"']/g, ch => `&#${ch.charCodeAt(0)};`);

export function ReportCardsGrid({ category }: { category: ReportSection }) {
  const navigate = useNavigate();
  const toast = useToast();
  const { storeId, startDate, endDate, dateRange, canExport } = useReportsContext();
  // Each section loads only its own cards' data (same query keys as the
  // section's widgets, so nothing is fetched twice).
  const only = (c: ReportSection) => ({ storeId: category === c ? storeId : undefined, startDate, endDate });
  const salesQ = useSalesSummary(only('sales'));
  const stockQ = useStockCount(only('inventory'));
  const acqQ = useCustomerAcquisition(only('customers'));

  const filteredReports = REPORT_CARDS.filter(r => r.category === category);

  /** The report's rows, or null while its data has not loaded. */
  const reportTable = (reportId: string): Table | null => {
    switch (reportId) {
      case 'daily-sales': {
        const trend = salesQ.data?.dailyTrend as Array<{ date: string; sales: number; orders?: number }> | undefined;
        if (!trend) return null;
        return {
          columns: [
            { key: 'date', label: 'Date' },
            { key: 'orders', label: 'Orders' },
            { key: 'sales', label: 'Sales (₹)' },
          ],
          rows: trend.map(d => ({ date: d.date, orders: d.orders ?? '', sales: d.sales.toFixed(2) })),
        };
      }
      case 'monthly-sales': {
        const cats = salesQ.data?.categoryBreakdown;
        if (!cats) return null;
        return {
          columns: [
            { key: 'category', label: 'Category' },
            { key: 'units', label: 'Units' },
            { key: 'sales', label: 'Sales (₹)' },
            { key: 'percentage', label: 'Share (%)' },
          ],
          rows: cats.map(c => ({ category: c.category, units: c.units, sales: c.sales.toFixed(2), percentage: c.percentage })),
        };
      }
      case 'stock-report': {
        const sc = stockQ.data;
        if (!sc) return null;
        // Stock at cost is sent to admin + accounts only (server cost_mask):
        // no value column for anyone else.
        const withValue = sc.summary?.total_value != null;
        return {
          columns: [
            { key: 'category', label: 'Category' },
            { key: 'item_count', label: 'Items' },
            { key: 'total_quantity', label: 'Units' },
            ...(withValue ? [{ key: 'total_value', label: 'Value at cost (₹)' }] : []),
          ],
          rows: (sc.data ?? []).map(r => ({
            category: r.category,
            item_count: r.item_count,
            total_quantity: r.total_quantity,
            ...(withValue ? { total_value: (r.total_value ?? 0).toFixed(2) } : {}),
          })),
        };
      }
      case 'customer-report': {
        const a = acqQ.data;
        if (!a) return null;
        return {
          columns: [
            { key: 'period', label: 'Period' },
            { key: 'new_customers', label: 'New customers' },
            { key: 'returning_customers', label: 'Returning customers' },
            { key: 'total_customers', label: 'Total customers' },
            { key: 'retention_percent', label: 'Retention (%)' },
          ],
          rows: [{
            period: `${startDate} to ${endDate}`,
            new_customers: a.new_customers,
            returning_customers: a.returning_customers,
            total_customers: a.total_customers,
            retention_percent: a.retention_percent,
          }],
        };
      }
      default:
        return null;
    }
  };

  /** The table, or null after telling the user why there is nothing. */
  const readyTable = (reportId: string): Table | null => {
    const t = reportTable(reportId);
    if (!t) {
      toast.error('Report data is still loading. Try again in a moment.');
      return null;
    }
    if (t.rows.length === 0) {
      toast.error('Nothing to report for this period.');
      return null;
    }
    return t;
  };

  const handleExportReport = (reportId: string) => {
    const t = readyTable(reportId);
    if (!t) return;
    exportToCSV(t.rows, `${reportId}_${startDate}_${endDate}`, t.columns);
    toast.success(`${reportId.replace(/-/g, ' ')} exported`);
  };

  const handlePrintReport = (reportId: string, reportTitle: string) => {
    const t = readyTable(reportId);
    if (!t) return;
    const printWindow = window.open('', '_blank');
    if (!printWindow) {
      toast.error('The print window was blocked. Allow pop-ups for this site and try again.');
      return;
    }
    const head = t.columns.map(c => `<th>${esc(c.label)}</th>`).join('');
    const body = t.rows
      .map(r => `<tr>${t.columns.map(c => `<td>${esc(r[c.key])}</td>`).join('')}</tr>`)
      .join('');
    printWindow.document.write(`<!DOCTYPE html><html><head><title>${esc(reportTitle)}</title>
      <style>
        body { font-family: sans-serif; padding: 20px; }
        table { width: 100%; border-collapse: collapse; }
        th, td { border: 1px solid #ddd; padding: 8px; text-align: left; font-size: 12px; }
        th { background: #f5f5f5; }
      </style></head><body>
      <h1>${esc(reportTitle)}</h1>
      <p>Period: ${esc(startDate)} to ${esc(endDate)} (${esc(dateRange)})</p>
      <table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>
      </body></html>`);
    printWindow.document.close();
    printWindow.focus();
    printWindow.print();
  };

  return (
    <div>
      <h3 className="font-semibold text-gray-900 mb-3">Available Reports</h3>
      <div className="grid grid-cols-1 tablet:grid-cols-2 laptop:grid-cols-3 gap-4">
        {filteredReports.map(report => (
          <div key={report.id} className="card hover:border-bv-red-300 transition-colors">
            <div className="flex items-start gap-4">
              <div className="w-10 h-10 bg-bv-red-100 rounded-lg flex items-center justify-center flex-shrink-0">
                <report.icon className="w-5 h-5 text-bv-red-600" />
              </div>
              <div className="flex-1 min-w-0">
                <h4 className="font-medium text-gray-900">{report.title}</h4>
                <p className="text-sm text-gray-500 mt-1">{report.description}</p>
                <div className="flex items-center gap-2 mt-3">
                  {report.id === 'gst-report' ? (
                    <button
                      onClick={() => navigate('/reports/gstr1')}
                      className="text-sm text-bv-red-600 hover:text-bv-red-700 flex items-center gap-1"
                    >
                      <Eye className="w-4 h-4" />
                      View GSTR-1
                    </button>
                  ) : report.id === 'stock-report' || report.id === 'stock-movement' ? (
                    <button
                      onClick={() => navigate(report.id === 'stock-movement' ? '/inventory/transfers' : '/inventory/stock')}
                      className="text-sm text-bv-red-600 hover:text-bv-red-700 flex items-center gap-1"
                    >
                      <Eye className="w-4 h-4" />
                      View
                    </button>
                  ) : report.id === 'customer-report' ? (
                    <button
                      onClick={() => navigate('/customers')}
                      className="text-sm text-bv-red-600 hover:text-bv-red-700 flex items-center gap-1"
                    >
                      <Eye className="w-4 h-4" />
                      View
                    </button>
                  ) : (
                    // daily-sales / monthly-sales: scroll to the trend chart
                    // already rendered on this page in the Sales section.
                    <button
                      onClick={() => {
                        navigate('/reports/sales');
                        setTimeout(() => {
                          const el = document.querySelector('.inv-tabs');
                          if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
                        }, 50);
                      }}
                      className="text-sm text-bv-red-600 hover:text-bv-red-700 flex items-center gap-1"
                    >
                      <Eye className="w-4 h-4" />
                      View
                    </button>
                  )}
                  {report.data && canExport && (
                    <button
                      onClick={() => handleExportReport(report.id)}
                      className="text-sm text-gray-500 hover:text-gray-700 flex items-center gap-1"
                    >
                      <Download className="w-4 h-4" />
                      Export CSV
                    </button>
                  )}
                  {report.data && (
                    <button
                      onClick={() => handlePrintReport(report.id, report.title)}
                      className="text-sm text-gray-500 hover:text-gray-700 flex items-center gap-1"
                    >
                      <Printer className="w-4 h-4" />
                      Print
                    </button>
                  )}
                </div>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default ReportCardsGrid;

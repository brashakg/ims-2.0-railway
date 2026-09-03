// ============================================================================
// IMS 2.0 - Reports "Available Reports" card grid
// ============================================================================
// Lifted verbatim out of the old ReportsPage. It rendered on every tab,
// filtered to the active tab's category, so it now renders on every section
// page with the category fixed by that page. The export + print handlers are
// unchanged, including their quirks.
//
// Two links changed target because the thing they opened moved: the GST card
// used to open the GSTR-1 MODAL; it now navigates to the GSTR-1 PAGE. The
// daily/monthly-sales "View" used to flip a useState tab; it now navigates to
// the Sales URL and scrolls to the same element.

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
import {
  exportToCSV,
  SALES_REPORT_COLUMNS,
  INVENTORY_REPORT_COLUMNS,
  CUSTOMER_REPORT_COLUMNS,
  GST_REPORT_COLUMNS,
} from '../../utils/exportUtils';
import { useReportsContext } from './ReportsLayout';
import { useSalesSummary, type ReportSection, type SalesSummary } from './reportsQueries';

const EMPTY_SUMMARY: SalesSummary = {
  totalSales: 0,
  orderCount: 0,
  averageOrderValue: 0,
  topCategory: '-',
  grossProfit: 0,
  gstCollected: 0,
};

// Report cards
const REPORT_CARDS = [
  {
    id: 'daily-sales',
    title: 'Daily Sales Report',
    description: 'Day-wise sales breakdown with payment modes',
    icon: BarChart3,
    category: 'sales' as ReportSection,
  },
  {
    id: 'monthly-sales',
    title: 'Monthly Sales Summary',
    description: 'Monthly sales with category and brand analysis',
    icon: TrendingUp,
    category: 'sales' as ReportSection,
  },
  {
    id: 'stock-report',
    title: 'Stock Report',
    description: 'Current stock levels by category and brand',
    icon: Package,
    category: 'inventory' as ReportSection,
  },
  {
    id: 'stock-movement',
    title: 'Stock Movement',
    description: 'Stock in/out movements and transfers',
    icon: Package,
    category: 'inventory' as ReportSection,
  },
  {
    id: 'customer-report',
    title: 'Customer Report',
    description: 'Customer acquisition and purchase patterns',
    icon: Users,
    category: 'customers' as ReportSection,
  },
  {
    id: 'gst-report',
    title: 'GST Report',
    description: 'GSTR-1 and GSTR-3B data for filing',
    icon: FileText,
    category: 'gst' as ReportSection,
  },
];

export function ReportCardsGrid({ category }: { category: ReportSection }) {
  const navigate = useNavigate();
  const toast = useToast();
  const { storeId, startDate, endDate, dateRange, canExport } = useReportsContext();
  const salesSummary = useSalesSummary({ storeId, startDate, endDate }).data?.summary ?? EMPTY_SUMMARY;

  const filteredReports = REPORT_CARDS.filter(r => r.category === category);

  const handleExportReport = (reportId: string) => {
    // Export current summary data based on report type
    const reportData: Record<string, any>[] = [];
    let columns: { key: string; label: string }[] = [];

    switch (reportId) {
      case 'daily-sales':
      case 'monthly-sales':
        reportData.push({
          period: dateRange,
          totalSales: salesSummary.totalSales.toFixed(2),
          orderCount: salesSummary.orderCount,
          averageOrderValue: salesSummary.averageOrderValue.toFixed(2),
          grossProfit: salesSummary.grossProfit.toFixed(2),
          gstCollected: salesSummary.gstCollected.toFixed(2),
          topCategory: salesSummary.topCategory,
        });
        columns = SALES_REPORT_COLUMNS.slice(0, 4).concat([
          { key: 'totalSales', label: 'Total Sales (₹)' },
          { key: 'orderCount', label: 'Orders' },
          { key: 'averageOrderValue', label: 'Avg Order Value (₹)' },
          { key: 'grossProfit', label: 'Gross Profit (₹)' },
          { key: 'gstCollected', label: 'GST Collected (₹)' },
        ]);
        break;
      case 'stock-report':
      case 'stock-movement':
        columns = INVENTORY_REPORT_COLUMNS;
        break;
      case 'customer-report':
        columns = CUSTOMER_REPORT_COLUMNS;
        break;
      case 'gst-report':
        columns = GST_REPORT_COLUMNS;
        break;
    }

    if (reportData.length > 0) {
      exportToCSV(reportData, reportId, columns);
      toast.success(`${reportId.replace('-', ' ')} exported`);
    } else {
      // For reports without local data, export what we have
      const summaryData = [{
        period: dateRange,
        totalSales: salesSummary.totalSales.toFixed(2),
        orderCount: salesSummary.orderCount,
        averageOrderValue: salesSummary.averageOrderValue.toFixed(2),
        gstCollected: salesSummary.gstCollected.toFixed(2),
      }];
      exportToCSV(summaryData, reportId);
      toast.success(`${reportId.replace(/-/g, ' ')} summary exported`);
    }
  };

  const handlePrintReport = (reportTitle: string) => {
    // Print current page content
    const printContent = document.querySelector('.space-y-4');
    if (printContent) {
      const printWindow = window.open('', '_blank');
      if (printWindow) {
        printWindow.document.write(`
          <!DOCTYPE html><html><head><title>${reportTitle}</title>
          <style>
            body { font-family: sans-serif; padding: 20px; }
            .card { border: 1px solid #ddd; padding: 16px; margin: 8px 0; border-radius: 8px; }
            table { width: 100%; border-collapse: collapse; }
            th, td { border: 1px solid #ddd; padding: 8px; text-align: left; font-size: 12px; }
            th { background: #f5f5f5; }
          </style></head><body>
          <h1>${reportTitle}</h1>
          <p>Date: ${new Date().toLocaleDateString('en-IN')}</p>
          <table><tr><th>Metric</th><th>Value</th></tr>
          <tr><td>Total Sales</td><td>₹${salesSummary.totalSales.toLocaleString('en-IN')}</td></tr>
          <tr><td>Orders</td><td>${salesSummary.orderCount}</td></tr>
          <tr><td>Avg Order Value</td><td>₹${salesSummary.averageOrderValue.toLocaleString('en-IN')}</td></tr>
          <tr><td>GST Collected</td><td>₹${salesSummary.gstCollected.toLocaleString('en-IN')}</td></tr>
          </table>
          </body></html>
        `);
        printWindow.document.close();
        printWindow.focus();
        printWindow.print();
        printWindow.close();
      }
    }
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
                      onClick={() => navigate(report.id === 'stock-movement' ? '/inventory?tab=transfers' : '/inventory?tab=stock')}
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
                  {canExport && (
                    <button
                      onClick={() => handleExportReport(report.id)}
                      className="text-sm text-gray-500 hover:text-gray-700 flex items-center gap-1"
                    >
                      <Download className="w-4 h-4" />
                      Export CSV
                    </button>
                  )}
                  <button
                    onClick={() => handlePrintReport(report.title)}
                    className="text-sm text-gray-500 hover:text-gray-700 flex items-center gap-1"
                  >
                    <Printer className="w-4 h-4" />
                    Print
                  </button>
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

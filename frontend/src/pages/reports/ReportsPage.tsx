// ============================================================================
// IMS 2.0 - Reports Page
// ============================================================================
// NO MOCK DATA - All data from API

import { useState, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  BarChart3,
  TrendingUp,
  Download,
  IndianRupee,
  Package,
  Users,
  ShoppingCart,
  FileText,
  Eye,
  Printer,
  Loader2,
  RefreshCw,
  AlertTriangle,
  X,
} from 'lucide-react';
import { reportsApi } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { GSTR1Report } from '../../components/reports/GSTR1Report';
import { GSTR3BReport } from '../../components/reports/GSTR3BReport';
import { DemandForecast } from '../../components/reports/DemandForecast';
import clsx from 'clsx';
import { exportToCSV, SALES_REPORT_COLUMNS, INVENTORY_REPORT_COLUMNS, CUSTOMER_REPORT_COLUMNS, GST_REPORT_COLUMNS } from '../../utils/exportUtils';

type ReportType = 'sales' | 'inventory' | 'customers' | 'gst' | 'forecast';
type DateRange = 'today' | 'week' | 'month' | 'quarter' | 'custom';

// Types
interface SalesSummary {
  totalSales: number;
  orderCount: number;
  averageOrderValue: number;
  topCategory: string;
  grossProfit: number;
  gstCollected: number;
}

interface CategoryBreakdown {
  category: string;
  sales: number;
  units: number;
  percentage: number;
}

interface DailyTrend {
  date: string;
  sales: number;
}

// Report cards
const REPORT_CARDS = [
  {
    id: 'daily-sales',
    title: 'Daily Sales Report',
    description: 'Day-wise sales breakdown with payment modes',
    icon: BarChart3,
    category: 'sales' as ReportType,
  },
  {
    id: 'monthly-sales',
    title: 'Monthly Sales Summary',
    description: 'Monthly sales with category and brand analysis',
    icon: TrendingUp,
    category: 'sales' as ReportType,
  },
  {
    id: 'stock-report',
    title: 'Stock Report',
    description: 'Current stock levels by category and brand',
    icon: Package,
    category: 'inventory' as ReportType,
  },
  {
    id: 'stock-movement',
    title: 'Stock Movement',
    description: 'Stock in/out movements and transfers',
    icon: Package,
    category: 'inventory' as ReportType,
  },
  {
    id: 'customer-report',
    title: 'Customer Report',
    description: 'Customer acquisition and purchase patterns',
    icon: Users,
    category: 'customers' as ReportType,
  },
  {
    id: 'gst-report',
    title: 'GST Report',
    description: 'GSTR-1 and GSTR-3B data for filing',
    icon: FileText,
    category: 'gst' as ReportType,
  },
];

export function ReportsPage() {
  const { user, hasRole } = useAuth();
  const toast = useToast();
  const [searchParams] = useSearchParams();

  // Data state
  const [salesSummary, setSalesSummary] = useState<SalesSummary>({
    totalSales: 0,
    orderCount: 0,
    averageOrderValue: 0,
    topCategory: '-',
    grossProfit: 0,
    gstCollected: 0,
  });
  const [categoryBreakdown, setCategoryBreakdown] = useState<CategoryBreakdown[]>([]);
  const [dailyTrend, setDailyTrend] = useState<DailyTrend[]>([]);

  // UI state
  const [activeTab, setActiveTab] = useState<ReportType>('sales');
  const [dateRange, setDateRange] = useState<DateRange>('month');

  // Sync active tab from URL query params (e.g. /reports?tab=gst)
  useEffect(() => {
    const tabParam = searchParams.get('tab');
    if (tabParam && tabParam !== activeTab) {
      const validTabs: ReportType[] = ['sales', 'inventory', 'customers', 'gst'];
      if (validTabs.includes(tabParam as ReportType)) {
        setActiveTab(tabParam as ReportType);
      }
    }
  }, [searchParams]);

  // Loading state
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // GST Reports Modal state
  const [showGSTR1, setShowGSTR1] = useState(false);
  const [showGSTR3B, setShowGSTR3B] = useState(false);

  // Role-based permissions
  const canExport = hasRole(['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']);

  // Load data on mount and when date range changes
  useEffect(() => {
    loadReportData();
  }, [user?.activeStoreId, dateRange]);

  const getDateRange = (): { startDate: string; endDate: string } => {
    const now = new Date();
    const endDate = now.toISOString().split('T')[0];
    let startDate: string;

    switch (dateRange) {
      case 'today':
        startDate = endDate;
        break;
      case 'week':
        const weekAgo = new Date(now);
        weekAgo.setDate(weekAgo.getDate() - 7);
        startDate = weekAgo.toISOString().split('T')[0];
        break;
      case 'month':
        const monthAgo = new Date(now);
        monthAgo.setMonth(monthAgo.getMonth() - 1);
        startDate = monthAgo.toISOString().split('T')[0];
        break;
      case 'quarter':
        const quarterAgo = new Date(now);
        quarterAgo.setMonth(quarterAgo.getMonth() - 3);
        startDate = quarterAgo.toISOString().split('T')[0];
        break;
      default:
        startDate = endDate;
    }

    return { startDate, endDate };
  };

  const loadReportData = async () => {
    if (!user?.activeStoreId) return;

    setIsLoading(true);
    setError(null);

    try {
      const { startDate, endDate } = getDateRange();
      const response = await reportsApi.getSalesSummary(user.activeStoreId, startDate, endDate);

      if (response) {
        setSalesSummary({
          totalSales: response.totalSales || 0,
          orderCount: response.orderCount || 0,
          averageOrderValue: response.averageOrderValue || 0,
          topCategory: response.topCategory || '-',
          grossProfit: response.grossProfit || 0,
          gstCollected: response.gstCollected || 0,
        });

        if (response.categoryBreakdown) {
          setCategoryBreakdown(response.categoryBreakdown);
        }

        if (response.dailyTrend) {
          setDailyTrend(response.dailyTrend);
        }
      }
    } catch {
      setError('Failed to load report data. Please try again.');
    } finally {
      setIsLoading(false);
    }
  };

  const filteredReports = REPORT_CARDS.filter(r => r.category === activeTab);

  // Export handlers
  const handleExportSalesTrend = () => {
    if (dailyTrend.length === 0) {
      toast.warning('No data to export');
      return;
    }
    exportToCSV(
      dailyTrend.map(d => ({ date: d.date, sales: d.sales.toFixed(2) })),
      'sales_trend',
      [{ key: 'date', label: 'Date' }, { key: 'sales', label: 'Sales (₹)' }]
    );
    toast.success('Sales trend exported');
  };

  const handleExportCategoryBreakdown = () => {
    if (categoryBreakdown.length === 0) {
      toast.warning('No data to export');
      return;
    }
    exportToCSV(
      categoryBreakdown.map(c => ({
        category: c.category,
        sales: c.sales.toFixed(2),
        units: c.units,
        percentage: c.percentage.toFixed(1),
      })),
      'category_breakdown',
      [
        { key: 'category', label: 'Category' },
        { key: 'sales', label: 'Sales (₹)' },
        { key: 'units', label: 'Units Sold' },
        { key: 'percentage', label: 'Percentage (%)' },
      ]
    );
    toast.success('Category breakdown exported');
  };

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

  const formatCurrency = (amount: number) => {
    if (amount >= 100000) {
      return `₹${(amount / 100000).toFixed(2)}L`;
    }
    return `₹${amount.toLocaleString('en-IN')}`;
  };

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Reports</h1>
          <p className="text-gray-500">Analytics and business reports</p>
        </div>
        <div className="flex gap-2">
          <select
            value={dateRange}
            onChange={e => setDateRange(e.target.value as DateRange)}
            className="input-field w-auto"
          >
            <option value="today">Today</option>
            <option value="week">This Week</option>
            <option value="month">This Month</option>
            <option value="quarter">This Quarter</option>
          </select>
          <button
            onClick={loadReportData}
            disabled={isLoading}
            className="btn-outline flex items-center gap-2"
          >
            {isLoading ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <RefreshCw className="w-4 h-4" />
            )}
            Refresh
          </button>
        </div>
      </div>

      {/* Error State */}
      {error && (
        <div className="card bg-red-50 border-red-200">
          <div className="flex items-center gap-3 text-red-600">
            <AlertTriangle className="w-5 h-5" />
            <p>{error}</p>
            <button onClick={loadReportData} className="ml-auto text-sm underline">
              Retry
            </button>
          </div>
        </div>
      )}

      {/* Summary Cards */}
      <div className="grid grid-cols-2 tablet:grid-cols-4 gap-4">
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-green-100 rounded-lg flex items-center justify-center">
              <IndianRupee className="w-5 h-5 text-green-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">Total Sales</p>
              {isLoading ? (
                <div className="h-7 w-20 bg-gray-200 animate-pulse rounded mt-1" />
              ) : (
                <p className="text-xl font-bold text-gray-900">{formatCurrency(salesSummary.totalSales)}</p>
              )}
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-blue-100 rounded-lg flex items-center justify-center">
              <ShoppingCart className="w-5 h-5 text-blue-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">Orders</p>
              {isLoading ? (
                <div className="h-7 w-12 bg-gray-200 animate-pulse rounded mt-1" />
              ) : (
                <p className="text-xl font-bold text-gray-900">{salesSummary.orderCount}</p>
              )}
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-purple-100 rounded-lg flex items-center justify-center">
              <TrendingUp className="w-5 h-5 text-purple-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">Avg Order Value</p>
              {isLoading ? (
                <div className="h-7 w-16 bg-gray-200 animate-pulse rounded mt-1" />
              ) : (
                <p className="text-xl font-bold text-gray-900">{formatCurrency(salesSummary.averageOrderValue)}</p>
              )}
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-orange-100 rounded-lg flex items-center justify-center">
              <FileText className="w-5 h-5 text-orange-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">GST Collected</p>
              {isLoading ? (
                <div className="h-7 w-16 bg-gray-200 animate-pulse rounded mt-1" />
              ) : (
                <p className="text-xl font-bold text-gray-900">{formatCurrency(salesSummary.gstCollected)}</p>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-gray-200 overflow-x-auto">
        {[
          { id: 'sales' as ReportType, label: 'Sales', icon: BarChart3 },
          { id: 'inventory' as ReportType, label: 'Inventory', icon: Package },
          { id: 'customers' as ReportType, label: 'Customers', icon: Users },
          { id: 'gst' as ReportType, label: 'GST', icon: FileText },
          { id: 'forecast' as ReportType, label: 'Forecast', icon: TrendingUp },
        ].map(tab => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={clsx(
              'flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors whitespace-nowrap',
              activeTab === tab.id
                ? 'border-bv-red-600 text-bv-red-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            )}
          >
            <tab.icon className="w-4 h-4" />
            {tab.label}
          </button>
        ))}
      </div>

      {/* Report Content */}
      {activeTab === 'sales' && (
        <div className="grid grid-cols-1 laptop:grid-cols-2 gap-4">
          {/* Sales Trend */}
          <div className="card">
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-semibold text-gray-900">Sales Trend</h3>
              {canExport && (
                <button
                  onClick={handleExportSalesTrend}
                  className="text-sm text-bv-red-600 hover:text-bv-red-700 flex items-center gap-1"
                >
                  <Download className="w-4 h-4" />
                  Export CSV
                </button>
              )}
            </div>
            {isLoading ? (
              <div className="h-48 flex items-center justify-center">
                <Loader2 className="w-8 h-8 animate-spin text-bv-red-600" />
              </div>
            ) : dailyTrend.length === 0 ? (
              <div className="h-48 flex items-center justify-center text-gray-500">
                <p>No sales data available for this period</p>
              </div>
            ) : (
              <div className="h-48 flex items-end gap-2">
                {dailyTrend.map((day, index) => {
                  const maxSales = Math.max(...dailyTrend.map(d => d.sales));
                  const height = maxSales > 0 ? (day.sales / maxSales) * 100 : 0;
                  return (
                    <div key={index} className="flex-1 flex flex-col items-center gap-1">
                      <div
                        className="w-full bg-bv-red-600 rounded-t transition-all hover:bg-bv-red-700"
                        style={{ height: `${height}%` }}
                        title={formatCurrency(day.sales)}
                      />
                      <span className="text-xs text-gray-500">{day.date}</span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Category Breakdown */}
          <div className="card">
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-semibold text-gray-900">Category Breakdown</h3>
              {canExport && categoryBreakdown.length > 0 && (
                <button
                  onClick={handleExportCategoryBreakdown}
                  className="text-sm text-bv-red-600 hover:text-bv-red-700 flex items-center gap-1"
                >
                  <Download className="w-4 h-4" />
                  Export CSV
                </button>
              )}
            </div>
            {isLoading ? (
              <div className="space-y-3">
                {[1, 2, 3, 4, 5].map((i) => (
                  <div key={i}>
                    <div className="flex items-center justify-between mb-1">
                      <div className="h-4 w-20 bg-gray-200 animate-pulse rounded" />
                      <div className="h-4 w-16 bg-gray-200 animate-pulse rounded" />
                    </div>
                    <div className="h-2 bg-gray-200 rounded-full" />
                  </div>
                ))}
              </div>
            ) : categoryBreakdown.length === 0 ? (
              <div className="py-8 text-center text-gray-500">
                <p>No category data available</p>
              </div>
            ) : (
              <div className="space-y-3">
                {categoryBreakdown.map((cat, index) => (
                  <div key={index}>
                    <div className="flex items-center justify-between text-sm mb-1">
                      <span className="text-gray-600">{cat.category}</span>
                      <span className="font-medium">{formatCurrency(cat.sales)}</span>
                    </div>
                    <div className="h-2 bg-gray-200 rounded-full overflow-hidden">
                      <div
                        className="h-full bg-bv-red-600 rounded-full"
                        style={{ width: `${cat.percentage}%` }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Report Cards */}
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
                        onClick={() => setShowGSTR1(true)}
                        className="text-sm text-bv-red-600 hover:text-bv-red-700 flex items-center gap-1"
                      >
                        <Eye className="w-4 h-4" />
                        View GSTR-1
                      </button>
                    ) : (
                      <button
                        onClick={() => toast.info(`Detailed ${report.title} view requires store data`)}
                        className="text-sm text-bv-red-600 hover:text-bv-red-700 flex items-center gap-1"
                      >
                        <Eye className="w-4 h-4" />
                        View
                      </button>
                    )}
                    {canExport && (
                      <button
                        onClick={() => handleExportReport(report.id)}
                        className="text-sm text-gray-600 hover:text-gray-700 flex items-center gap-1"
                      >
                        <Download className="w-4 h-4" />
                        Export CSV
                      </button>
                    )}
                    <button
                      onClick={() => handlePrintReport(report.title)}
                      className="text-sm text-gray-600 hover:text-gray-700 flex items-center gap-1"
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

      {/* GST Reports Section (when GST tab selected) */}
      {activeTab === 'gst' && (
        <div className="card bg-yellow-50 border-yellow-200">
          <div className="flex items-start gap-4">
            <div className="w-10 h-10 bg-yellow-100 rounded-lg flex items-center justify-center flex-shrink-0">
              <FileText className="w-5 h-5 text-yellow-600" />
            </div>
            <div>
              <h4 className="font-medium text-gray-900">GST Filing Data Ready</h4>
              <p className="text-sm text-gray-600 mt-1">
                GST data for the period has been compiled. Download the reports for GSTR-1 and GSTR-3B filing.
              </p>
              <div className="flex gap-3 mt-3">
                <button
                  onClick={() => setShowGSTR1(true)}
                  className="btn-primary text-sm"
                >
                  View GSTR-1
                </button>
                <button
                  onClick={() => setShowGSTR3B(true)}
                  className="btn-outline text-sm"
                >
                  View GSTR-3B
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Demand Forecast Section */}
      {activeTab === 'forecast' && (
        <DemandForecast />
      )}

      {/* GSTR-1 Modal */}
      {showGSTR1 && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl w-full max-w-7xl max-h-[90vh] overflow-y-auto">
            <div className="sticky top-0 bg-white border-b border-gray-200 px-6 py-4 flex items-center justify-between">
              <h2 className="text-xl font-bold text-gray-900">GSTR-1 Report</h2>
              <button
                onClick={() => setShowGSTR1(false)}
                className="p-2 hover:bg-gray-100 rounded-lg"
              >
                <X className="w-5 h-5 text-gray-500" />
              </button>
            </div>
            <div className="p-6">
              <GSTR1Report />
            </div>
          </div>
        </div>
      )}

      {/* GSTR-3B Modal */}
      {showGSTR3B && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl w-full max-w-7xl max-h-[90vh] overflow-y-auto">
            <div className="sticky top-0 bg-white border-b border-gray-200 px-6 py-4 flex items-center justify-between">
              <h2 className="text-xl font-bold text-gray-900">GSTR-3B Report</h2>
              <button
                onClick={() => setShowGSTR3B(false)}
                className="p-2 hover:bg-gray-100 rounded-lg"
              >
                <X className="w-5 h-5 text-gray-500" />
              </button>
            </div>
            <div className="p-6">
              <GSTR3BReport />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default ReportsPage;

// ============================================================================
// IMS 2.0 - Reports > Forecast  (/reports/forecast)
// ============================================================================
// The old ReportsPage `activeTab === 'forecast'` blocks on their own URL.
// This tab was UNREACHABLE by link: the ?tab= allow-list omitted 'forecast',
// so /reports?tab=forecast silently landed on Sales. It now has an address.

import { Loader2 } from 'lucide-react';
import { DemandForecast } from '../../components/reports/DemandForecast';
import { ReportCardsGrid } from './ReportCardsGrid';
import { useReportsContext } from './ReportsLayout';
import { useExpenseVsRevenue } from './reportsQueries';

export function ReportsForecastPage() {
  const { storeId, startDate, endDate } = useReportsContext();
  const evrQ = useExpenseVsRevenue({ storeId, startDate, endDate });
  const expenseVsRevenue = evrQ.data;

  return (
    <>
      {/* No report cards carry the 'forecast' category — the heading renders
          over an empty grid, exactly as it did on the old tab. */}
      <ReportCardsGrid category="forecast" />

      {/* Expense vs Revenue */}
      <div className="chart-card" style={{ marginBottom: 14 }}>
        <div className="chart-head">
          <h3>Expense vs Revenue</h3>
        </div>
        <div className="chart-body">
        {evrQ.isPending ? (
          <div className="h-40 flex items-center justify-center">
            <Loader2 className="w-6 h-6 animate-spin text-bv-red-600" />
          </div>
        ) : expenseVsRevenue ? (
          <div className="space-y-4">
            <div className="grid grid-cols-2 tablet:grid-cols-4 gap-3">
              <div className="bg-white rounded p-3">
                <p className="text-xs text-gray-500">Revenue</p>
                <p className="text-lg font-bold text-green-600">₹{((expenseVsRevenue.revenue || 0) / 100000).toFixed(2)}L</p>
              </div>
              <div className="bg-white rounded p-3">
                <p className="text-xs text-gray-500">Cost</p>
                <p className="text-lg font-bold text-red-600">₹{((expenseVsRevenue.cost || 0) / 100000).toFixed(2)}L</p>
              </div>
              <div className="bg-white rounded p-3">
                <p className="text-xs text-gray-500">Profit</p>
                <p className="text-lg font-bold text-blue-600">₹{((expenseVsRevenue.profit || 0) / 100000).toFixed(2)}L</p>
              </div>
              <div className="bg-white rounded p-3">
                <p className="text-xs text-gray-500">Margin</p>
                <p className="text-lg font-bold text-bv-red-600">{expenseVsRevenue.margin_percent || 0}%</p>
              </div>
            </div>
          </div>
        ) : (
          <p className="text-gray-500">No data available</p>
        )}
        </div>
      </div>

      {/* Demand Forecast Section */}
      <DemandForecast />
    </>
  );
}

export default ReportsForecastPage;

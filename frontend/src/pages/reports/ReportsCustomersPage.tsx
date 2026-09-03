// ============================================================================
// IMS 2.0 - Reports > Customers  (/reports/customers)
// ============================================================================
// The old ReportsPage `activeTab === 'customers'` blocks on their own URL.

import { Loader2 } from 'lucide-react';
import { FootfallAuditCard } from './sections/FootfallAuditCard';
import { ReportCardsGrid } from './ReportCardsGrid';
import { useReportsContext } from './ReportsLayout';
import { useCustomerAcquisition } from './reportsQueries';

export function ReportsCustomersPage() {
  const { storeId, startDate, endDate } = useReportsContext();
  const acqQ = useCustomerAcquisition({ storeId, startDate, endDate });
  const customerAcquisition = acqQ.data;

  return (
    <>
      <ReportCardsGrid category="customers" />

      <FootfallAuditCard storeId={storeId} />

      <div className="chart-card" style={{ marginTop: 14 }}>
        <div className="chart-head">
          <h3>Customer Acquisition &amp; Retention</h3>
        </div>
        <div className="chart-body">
        {acqQ.isPending ? (
          <div className="h-40 flex items-center justify-center">
            <Loader2 className="w-6 h-6 animate-spin text-bv-red-600" />
          </div>
        ) : customerAcquisition ? (
          <div className="grid grid-cols-2 tablet:grid-cols-4 gap-3">
            <div className="bg-white rounded p-3">
              <p className="text-xs text-gray-500">New Customers</p>
              <p className="text-xl font-bold text-green-600 mt-1">{customerAcquisition.new_customers}</p>
            </div>
            <div className="bg-white rounded p-3">
              <p className="text-xs text-gray-500">Returning</p>
              <p className="text-xl font-bold text-blue-600 mt-1">{customerAcquisition.returning_customers}</p>
            </div>
            <div className="bg-white rounded p-3">
              <p className="text-xs text-gray-500">Retention Rate</p>
              <p className="text-xl font-bold text-bv-red-600 mt-1">{customerAcquisition.retention_percent}%</p>
            </div>
            <div className="bg-white rounded p-3">
              <p className="text-xs text-gray-500">Total Customers</p>
              <p className="text-xl font-bold text-orange-600 mt-1">{customerAcquisition.total_customers}</p>
            </div>
          </div>
        ) : (
          <p className="text-gray-500">No data available</p>
        )}
        </div>
      </div>
    </>
  );
}

export default ReportsCustomersPage;

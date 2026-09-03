// ============================================================================
// IMS 2.0 - Reports > GST  (/reports/gst)
// ============================================================================
// The old ReportsPage `activeTab === 'gst'` blocks on their own URL.
//
// The two buttons used to open GSTR-1 / GSTR-3B in a MODAL inside this
// scrolling page — two nested scrollbars over the figures the accountant
// types into the GST portal by hand. They now go to full pages the
// accountant can bookmark: /reports/gstr1 and /reports/gstr3b.

import { Link } from 'react-router-dom';
import { FileText, Loader2 } from 'lucide-react';
import { ReportCardsGrid } from './ReportCardsGrid';
import { useReportsContext } from './ReportsLayout';
import { useDiscountAnalysis } from './reportsQueries';

export function ReportsGstPage() {
  const { storeId, startDate, endDate } = useReportsContext();
  const discQ = useDiscountAnalysis({ storeId, startDate, endDate });
  const discountAnalysis = discQ.data;

  return (
    <>
      <ReportCardsGrid category="gst" />

      <div className="card bg-yellow-50 border-yellow-200">
        <div className="flex items-start gap-4">
          <div className="w-10 h-10 bg-yellow-100 rounded-lg flex items-center justify-center flex-shrink-0">
            <FileText className="w-5 h-5 text-yellow-600" />
          </div>
          <div>
            <h4 className="font-medium text-gray-900">GST Filing Data Ready</h4>
            <p className="text-sm text-gray-500 mt-1">
              GST data for the period has been compiled. Download the reports for GSTR-1 and GSTR-3B filing.
            </p>
            <div className="flex gap-3 mt-3">
              <Link to="/reports/gstr1" className="btn-primary text-sm">
                View GSTR-1
              </Link>
              <Link to="/reports/gstr3b" className="btn-outline text-sm">
                View GSTR-3B
              </Link>
            </div>
          </div>
        </div>
      </div>

      {/* Discount Analysis Section */}
      <div className="chart-card" style={{ marginTop: 14 }}>
        <div className="chart-head">
          <h3>Discount Analysis</h3>
        </div>
        <div className="chart-body">
        {discQ.isPending ? (
          <div className="h-32 flex items-center justify-center">
            <Loader2 className="w-6 h-6 animate-spin text-bv-red-600" />
          </div>
        ) : discountAnalysis ? (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="bg-white rounded p-3">
                <p className="text-xs text-gray-500">Total Discount</p>
                <p className="text-lg font-bold text-gray-900">₹{((discountAnalysis.summary?.total_discount || 0) / 1000).toFixed(1)}K</p>
              </div>
              <div className="bg-white rounded p-3">
                <p className="text-xs text-gray-500">Discount %</p>
                <p className="text-lg font-bold text-orange-600">{discountAnalysis.summary?.discount_percent || 0}%</p>
              </div>
            </div>
            <div className="space-y-2">
              <p className="text-sm font-medium text-gray-500">By Category:</p>
              {discountAnalysis.by_category?.slice(0, 3).map((cat, idx: number) => (
                <div key={idx} className="flex items-center justify-between text-sm">
                  <span className="text-gray-600">{cat.category}</span>
                  <span className="text-bv-red-600">₹{(cat.total_discount / 1000).toFixed(1)}K</span>
                </div>
              ))}
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

export default ReportsGstPage;

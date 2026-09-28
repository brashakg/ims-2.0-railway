// ============================================================================
// IMS 2.0 - Finance dashboard: GST
// ============================================================================
// One section of the old FinanceDashboard tab container, moved verbatim. The
// data and handlers come from FinanceLayout (useFinanceContext).

import { useFinanceContext } from './FinanceLayout';
import GSTPanel from './GSTPanel';

export function FinanceGstPage() {
  const { gstSummary, gstRecon } = useFinanceContext();
  return (
    <>
      <GSTPanel gstSummary={gstSummary} />
      {gstRecon.length > 0 && (
        <div className="card mt-4 overflow-x-auto">
          <div className="px-4 py-2 text-sm font-medium text-gray-700 border-b border-gray-100">GST reconciliation by entity (file via Tally)</div>
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50 text-gray-600"><tr>
              <th className="px-3 py-2 text-left">Entity</th>
              <th className="px-3 py-2 text-right">GST collected</th>
              <th className="px-3 py-2 text-right">Input credit</th>
              <th className="px-3 py-2 text-right">Net payable</th>
            </tr></thead>
            <tbody>
              {gstRecon.map((e, i) => (
                <tr key={i} className="border-t border-gray-100">
                  <td className="px-3 py-2">{e.entity_name}</td>
                  <td className="px-3 py-2 text-right">₹{Math.round(e.gst_collected || 0).toLocaleString('en-IN')}</td>
                  <td className="px-3 py-2 text-right">₹{Math.round(e.input_credit || 0).toLocaleString('en-IN')}</td>
                  <td className="px-3 py-2 text-right font-semibold">₹{Math.round(e.net_payable || 0).toLocaleString('en-IN')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

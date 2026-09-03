// ============================================================================
// IMS 2.0 - GSTR-3B  (/reports/gstr3b)
// ============================================================================
// Was a modal on the Reports page; now its own bookmarkable page. Same
// component, no behaviour change beyond the container.

import { Link } from 'react-router-dom';
import { ChevronLeft } from 'lucide-react';
import { GSTR3BReport } from '../../components/reports/GSTR3BReport';

export function GSTR3BPage() {
  return (
    <div className="p-4 tablet:p-6">
      <Link to="/reports/gst" className="text-sm text-gray-500 hover:text-gray-700 inline-flex items-center gap-1 mb-3">
        <ChevronLeft className="w-4 h-4" />
        Back to GST reports
      </Link>
      <GSTR3BReport />
    </div>
  );
}

export default GSTR3BPage;

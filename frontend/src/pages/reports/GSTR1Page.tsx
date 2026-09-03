// ============================================================================
// IMS 2.0 - GSTR-1  (/reports/gstr1)
// ============================================================================
// The GSTR-1 report used to render inside a modal on the Reports page, which
// put two nested scrollbars between the accountant and the figures he types
// into the GST portal by hand. Same component, now a bookmarkable page.

import { Link } from 'react-router-dom';
import { ChevronLeft } from 'lucide-react';
import { GSTR1Report } from '../../components/reports/GSTR1Report';

export function GSTR1Page() {
  return (
    <div className="p-4 tablet:p-6">
      <Link to="/reports/gst" className="text-sm text-gray-500 hover:text-gray-700 inline-flex items-center gap-1 mb-3">
        <ChevronLeft className="w-4 h-4" />
        Back to GST reports
      </Link>
      <GSTR1Report />
    </div>
  );
}

export default GSTR1Page;

import { ProtectedRoute } from '../../components/layout/ProtectedRoute';
import POSLayout from '../../components/pos/POSLayout';
import WalkoutComplianceBanner from '../../components/pos/WalkoutComplianceBanner';

/**
 * Enterprise Point of Sale (POS) System
 * Complete system for optical retail transactions with:
 * - Smart product catalog with barcode scanning
 * - Shopping cart with real-time pricing
 * - GST-compliant billing engine
 * - Multiple payment methods
 * - Optical prescription entry
 * - Receipt generation (thermal 80mm + A4 tax invoice)
 * - Digital receipt sharing (WhatsApp, Email)
 */
export function POSPage() {
  return (
    <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'CASHIER', 'SALES_CASHIER', 'SALES_STAFF', 'OPTOMETRIST']}>
      {/* F45 D8 -- soft-block walkout nudge. Renders null + no fetch when the
          VITE_ENABLE_POS_WALKOUT_COMPLIANCE_BANNER flag is off (default), so
          POSLayout is unchanged. Never blocks a sale. */}
      <WalkoutComplianceBanner />
      <POSLayout />
    </ProtectedRoute>
  );
}

export default POSPage;

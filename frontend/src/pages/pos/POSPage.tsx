import { ProtectedRoute } from '../../components/layout/ProtectedRoute';
import POSLayout from '../../components/pos/POSLayout';

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
    <ProtectedRoute allowedRoles={['ADMIN', 'STORE_MANAGER', 'CASHIER', 'SALES_CASHIER']}>
      <POSLayout />
    </ProtectedRoute>
  );
}

export default POSPage;

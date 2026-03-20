// ============================================================================
// IMS 2.0 - POS Receipt Preview Wrapper
// ============================================================================
// Extracted from POSLayout.tsx — builds receipt data from POS store state
// and renders the ReceiptPreview modal.

import { usePOSStore } from '../../stores/posStore';
import { ReceiptPreview } from './ReceiptPreview';

interface POSReceiptProps {
  onClose: () => void;
}

export function POSReceipt({ onClose }: POSReceiptProps) {
  const store = usePOSStore();

  const sub = store.getSubtotal();
  const disc = store.getTotalDiscount();
  const taxable = sub - disc;
  const gst = store.getGrandTotal() - taxable;

  // Detect inter-state: compare customer billing state with store state
  const custState = ((store.customer as any)?.billing_address?.state || (store.customer as any)?.state || '').toLowerCase().trim();
  const stState = ((store as any).store_state || '').toLowerCase().trim();
  const isInterState = custState && stState && custState !== stState;
  const halfGst = Math.round(gst / 2 * 100) / 100;

  return (
    <ReceiptPreview
      billData={{
        bill_number: store.order_number || 'N/A',
        subtotal: Math.round(sub),
        item_discount: Math.round(disc),
        order_discount_amount: 0,
        taxable_amount: Math.round(taxable),
        cgst_amount: isInterState ? 0 : halfGst,
        sgst_amount: isInterState ? 0 : halfGst,
        igst_amount: isInterState ? Math.round(gst) : 0,
        total_gst: Math.round(gst),
        roundoff_amount: 0,
        total_amount: Math.round(store.getGrandTotal()),
        payment_method: (store.payments || []).map(p => p.method).join(' + ') || 'N/A',
      }}
      selectedCustomer={store.customer || { name: 'Walk-in', phone: '' }}
      cartItems={(store.cart || []).map(item => ({
        ...item,
        unitPrice: item.unit_price,
        discountPercent: item.discount_percent,
        discountAmount: item.discount_amount,
        finalPrice: item.line_total,
        productName: item.name,
      })) as any}
      onClose={onClose}
    />
  );
}

export default POSReceipt;

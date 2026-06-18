// ============================================================================
// IMS 2.0 - POS Receipt Preview Wrapper
// ============================================================================
// Extracted from POSLayout.tsx — builds receipt data from POS store state
// and renders the ReceiptPreview modal.
//
// STORE-SPECIFIC: the thermal / A4 receipt MUST carry the ISSUING store's
// identity (legal/trade name, address, GSTIN, phone, brand logo) — resolved
// from THIS order's store_id + its legal entity, not a hardcoded brand. We use
// the shared print-identity resolver (store + entity, snake->camel normalised).

import { useEffect, useState } from 'react';
import { usePOSStore } from '../../stores/posStore';
import { ReceiptPreview } from './ReceiptPreview';
import { resolveStoreIdentity, type StoreIdentity } from '../print/storeIdentity';

interface POSReceiptProps {
  onClose: () => void;
}

export function POSReceipt({ onClose }: POSReceiptProps) {
  const store = usePOSStore();

  // Resolve the ORDER's store + legal entity for the printed receipt header.
  const [identity, setIdentity] = useState<StoreIdentity | null>(null);
  useEffect(() => {
    if (!store.store_id) return;
    let active = true;
    resolveStoreIdentity(store.store_id)
      .then((id) => { if (active) setIdentity(id); })
      .catch(() => { if (active) setIdentity(null); });
    return () => { active = false; };
  }, [store.store_id]);

  const sub = store.getSubtotal();
  const disc = store.getTotalDiscount();
  // GST-inclusive: the taxable base + GST are extracted from WITHIN the
  // inclusive total (taxable + gst === grand), not added on top.
  const taxable = store.getTaxableValue();
  const gst = store.getTax();

  const sv = identity?.store;
  const ent = identity?.entity;

  // Detect inter-state: compare customer billing state with the RESOLVED store
  // state (posStore has no store_state, so use the fetched document store).
  const custState = ((store.customer as any)?.billing_address?.state || (store.customer as any)?.state || '').toLowerCase().trim();
  const stState = (sv?.state || '').toLowerCase().trim();
  const isInterState = custState && stState && custState !== stState;
  const halfGst = Math.round(gst / 2 * 100) / 100;

  // Statutory identity for the receipt header (GSTIN-for-state, legal/trade
  // name, address, brand logo). SAFE NEUTRAL fallback only (store name / code /
  // empty) -- NEVER a fixed brand name.
  const storeStateCode = sv?.stateCode || '';
  const entGstins = ent?.gstins || [];
  const gstinForState =
    entGstins.find((g) => g.state_code && g.state_code === storeStateCode)?.gstin
    || entGstins.find((g) => g.is_primary)?.gstin
    || sv?.gstin
    || '';
  const legalName = ent?.legal_name || ent?.name || '';
  const tradeName = ent?.name || sv?.storeName || '';
  const logoUrl = ent?.invoice?.logo_url || (ent as any)?.logo_url || '';
  const addressParts = sv
    ? [sv.address, sv.city, sv.state, sv.pincode].filter(Boolean).join(', ')
    : '';

  const storeData = sv
    ? {
        name: tradeName || sv.storeName || sv.storeCode || '',
        legalName,
        address: addressParts,
        phone: (sv as any).phone || '',
        gst: gstinForState,
        stateCode: storeStateCode,
        logo: logoUrl,
        storeCode: sv.storeCode || '',
      }
    : undefined;

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
      storeData={storeData}
      entityTradeName={tradeName || undefined}
      onClose={onClose}
    />
  );
}

export default POSReceipt;

// ============================================================================
// IMS 2.0 - POS Receipt Preview Wrapper
// ============================================================================
// Extracted from POSLayout.tsx -- builds receipt data from POS store state
// and renders the ReceiptPreview modal.
//
// STORE-SPECIFIC IDENTITY: the thermal receipt is a statutory document (Sec. 31
// CGST) and MUST carry the ISSUING store's identity -- its own name, address,
// phone, GSTIN and (where configured) logo -- NOT a hardcoded brand. We resolve
// the order's own store via storeApi.getStore(store_id) and best-effort its
// legal entity (for the trade name + logo). If the fetch fails we pass whatever
// the POS store knows and let ReceiptPreview fall back to the store code -- we
// never substitute another store's brand name.

import { useEffect, useState } from 'react';
import { usePOSStore } from '../../stores/posStore';
import { storeApi, entitiesApi } from '../../services/api';
import { ReceiptPreview } from './ReceiptPreview';

interface POSReceiptProps {
  onClose: () => void;
}

interface ResolvedStoreData {
  name?: string;
  address?: string;
  phone?: string;
  gst?: string;
  stateCode?: string;
  logo?: string;
}

export function POSReceipt({ onClose }: POSReceiptProps) {
  const store = usePOSStore();

  // Resolved identity of the ISSUING store (this order's store), fetched from
  // the API -- distinct from any "active store" notion. Best-effort + fail-soft.
  const [storeData, setStoreData] = useState<ResolvedStoreData | undefined>(undefined);
  const [entityTradeName, setEntityTradeName] = useState<string | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    const storeId = store.store_id;
    if (!storeId) return;

    (async () => {
      try {
        const s: any = await storeApi.getStore(storeId);
        if (cancelled || !s) return;
        // The raw store doc is snake_case; the API client also adds camelCase
        // aliases, so read defensively from either shape.
        const addr = [
          s.address || s.address_line_1 || s.street,
          s.city,
          s.state || s.state_name,
          s.pincode,
        ]
          .filter(Boolean)
          .join(', ');
        setStoreData({
          name: s.store_name || s.storeName || s.name || s.trade_name || '',
          address: addr,
          phone: s.phone || '',
          gst: s.gstin || '',
          stateCode: s.state_code || s.stateCode || '',
          logo: s.logo_url || s.logoUrl || s.logo || '',
        });

        // Best-effort: enrich with the legal entity (trade name + logo). The
        // entity GET is authenticated-only, so a cashier can read it. Never
        // block the receipt if it fails.
        const eid = s.entity_id || s.entityId;
        if (eid) {
          try {
            const res: any = await entitiesApi.get(eid);
            const ent = res?.entity || res;
            if (!cancelled && ent) {
              const trade = ent.name || ent.legal_name || ent.legalName;
              if (trade) setEntityTradeName(String(trade));
              // Prefer an entity/brand logo when the store has none.
              const elogo = ent.logo_url || ent.logoUrl;
              if (elogo) {
                setStoreData((prev) => (prev && !prev.logo ? { ...prev, logo: elogo } : prev));
              }
            }
          } catch {
            /* entity enrichment is optional */
          }
        }
      } catch {
        /* store fetch failed -- ReceiptPreview falls back to the store code */
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [store.store_id]);

  const sub = store.getSubtotal();
  const disc = store.getTotalDiscount();
  // GST-inclusive: the taxable base + GST are extracted from WITHIN the
  // inclusive total (taxable + gst === grand), not added on top.
  const taxable = store.getTaxableValue();
  const gst = store.getTax();

  // Detect inter-state: compare customer billing state with store state
  const custState = ((store.customer as any)?.billing_address?.state || (store.customer as any)?.state || '').toLowerCase().trim();
  const stState = ((store as any).store_state || '').toLowerCase().trim();
  const isInterState = custState && stState && custState !== stState;
  const halfGst = Math.round(gst / 2 * 100) / 100;

  return (
    <ReceiptPreview
      billData={{
        bill_number: store.order_number || 'N/A',
        store_code: store.store_id || '',
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
      entityTradeName={entityTradeName}
      onClose={onClose}
    />
  );
}

export default POSReceipt;

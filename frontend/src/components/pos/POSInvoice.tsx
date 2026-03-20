// ============================================================================
// IMS 2.0 - POS Invoice / Order Complete Step
// ============================================================================
// Extracted from POSLayout.tsx — displays order confirmation, receipt/invoice
// buttons, incentive-qualifying items, and GST invoice modal.

import { useState, useEffect, useMemo } from 'react';
import {
  CheckCircle, Plus, Printer, FileText, X, Sparkles, AlertTriangle,
} from 'lucide-react';
import { usePOSStore } from '../../stores/posStore';
import { storeApi } from '../../services/api';
import type { Store } from '../../types';
import { GSTInvoice } from './GSTInvoice';

/** Safe currency format */
function fc(amount: number | undefined | null): string {
  const val = Math.round((amount || 0) * 100) / 100;
  return `\u20B9${val.toLocaleString('en-IN', { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`;
}

interface StepCompleteProps {
  onPrint: () => void;
  onReset: () => void;
}

export function StepComplete({ onPrint, onReset }: StepCompleteProps) {
  const store = usePOSStore();
  const [showGSTInvoice, setShowGSTInvoice] = useState(false);

  // Fetch real store details from the API instead of using a hardcoded map
  const [fetchedStore, setFetchedStore] = useState<Store | null>(null);
  const [storeWarning, setStoreWarning] = useState<string | null>(null);

  useEffect(() => {
    if (!store.store_id) return;
    storeApi.getStore(store.store_id)
      .then((data: Store) => {
        setFetchedStore(data);
        if (!data.gstin) {
          setStoreWarning('Store GSTIN is not configured. Tax invoice may be invalid.');
        } else {
          setStoreWarning(null);
        }
      })
      .catch(() => {
        setFetchedStore(null);
        setStoreWarning('Could not load store details. Tax invoice data may be incomplete.');
      });
  }, [store.store_id]);

  // Build Order-shaped object from POS store for GSTInvoice
  const orderForInvoice = useMemo(() => ({
    id: store.order_id || '',
    orderNumber: store.order_number || '',
    storeId: store.store_id,
    customerId: store.customer?.id || '',
    customerName: store.customer?.name || 'Walk-in',
    customerPhone: store.customer?.phone || '',
    patientName: store.patient?.name,
    items: (store.cart || []).map(item => ({
      id: item.id,
      itemType: item.category || 'FRAMES',
      productId: item.product_id,
      productName: item.name,
      sku: item.sku || '',
      quantity: item.quantity,
      unitPrice: item.unit_price,
      discountPercent: item.discount_percent || 0,
      discountAmount: item.discount_amount || 0,
      finalPrice: item.line_total || item.unit_price * item.quantity,
    })),
    payments: (store.payments || []).map((p, i) => ({
      id: `pay-${i}`,
      mode: p.method,
      amount: p.amount,
      reference: p.reference,
      paidAt: new Date().toISOString(),
    })),
    subtotal: store.getSubtotal(),
    totalDiscount: store.getTotalDiscount(),
    taxAmount: store.getGrandTotal() - store.getSubtotal(),
    grandTotal: store.getGrandTotal(),
    amountPaid: store.getTotalPaid(),
    balanceDue: store.getBalance(),
    orderStatus: 'CONFIRMED',
    createdAt: new Date().toISOString(),
  }), [store.order_id]);

  // Build store object for invoice from fetched API data
  const storeForInvoice = useMemo(() => {
    if (fetchedStore) return fetchedStore;
    // Fallback: minimal stub so GSTInvoice can render — warning is shown to user
    return {
      id: store.store_id,
      storeCode: store.store_id,
      storeName: '',
      brand: 'BETTER_VISION' as any,
      gstin: '',
      address: '',
      city: '',
      state: '',
      stateCode: '',
      pincode: '',
      latitude: 0, longitude: 0, geoFenceRadius: 0,
      isActive: true, isHQ: false,
      enabledCategories: [],
      openingTime: '10:00', closingTime: '21:00',
    };
  }, [fetchedStore, store.store_id]);

  return (
    <div className="max-w-md mx-auto text-center py-8 space-y-6">
      <div className="w-20 h-20 bg-green-100 rounded-full flex items-center justify-center mx-auto"><CheckCircle className="w-10 h-10 text-green-500" /></div>
      <div><h2 className="text-2xl font-bold text-white">Order Created!</h2><p className="text-gray-500 mt-1">Order #{store.order_number}</p></div>
      <div className="bg-gray-800 border border-gray-700 rounded-xl p-4 text-left space-y-2 text-sm">
        <div className="flex justify-between"><span className="text-gray-500">Customer</span><span className="font-medium">{store.customer?.name}</span></div>
        <div className="flex justify-between"><span className="text-gray-500">Items</span><span className="font-medium">{(store.cart || []).length}</span></div>
        <div className="flex justify-between"><span className="text-gray-500">Total</span><span className="font-bold text-lg">{fc(store.getGrandTotal())}</span></div>
        <div className="flex justify-between"><span className="text-gray-500">Paid</span><span className="font-medium text-green-600">{fc(store.getTotalPaid())}</span></div>
        {store.getBalance() > 0 && <div className="flex justify-between"><span className="text-gray-500">Balance due</span><span className="font-medium text-red-600">{fc(store.getBalance())}</span></div>}
        {store.sale_type === 'prescription_order' && <div className="flex justify-between"><span className="text-gray-500">Type</span><span className="px-2 py-0.5 bg-purple-900/30 text-purple-600 rounded text-xs font-medium">Rx Order {'\u2192'} Workshop</span></div>}
      </div>

      {/* Incentive qualifying items — auto-tagged for kicker tracking */}
      {(() => {
        const INCENTIVE_KEYS = ['ZEISS', 'SAFILO', 'CARRERA', 'POLAROID', 'MARC JACOB', 'HUGO', 'SEVENTH STREET', 'BOSS', 'TOMMY HILFIGER', 'PIERRE CARDIN', 'UNDER ARMOUR'];
        const qualifying = (store.cart || []).filter(i => {
          const b = (i.brand || '').toUpperCase();
          const sb = (i.subbrand || '').toUpperCase();
          const n = (i.name || '').toUpperCase();
          return INCENTIVE_KEYS.some(k => b.includes(k) || sb.includes(k) || n.includes(k));
        });
        if (qualifying.length === 0) return null;
        return (
          <div className="bg-amber-900/30 border border-amber-700 rounded-xl p-4 text-left text-xs">
            <div className="flex items-center gap-2 mb-2">
              <Sparkles className="w-4 h-4 text-amber-600" />
              <span className="font-semibold text-amber-800">Incentive-qualifying items ({qualifying.length})</span>
              <span className="text-amber-400 ml-auto">Auto-tagged at POS</span>
            </div>
            <div className="space-y-1.5">
              {qualifying.map(item => {
                const brandLabel = item.brand || 'Unknown';
                const subLabel = item.subbrand ? ` \u00B7 ${item.subbrand}` : '';
                return (
                  <div key={item.id} className="flex items-center justify-between gap-2 bg-gray-800/60 rounded-lg px-2.5 py-1.5">
                    <div className="flex-1 min-w-0">
                      <span className="font-medium text-amber-900 truncate block">{brandLabel}{subLabel}</span>
                      <span className="text-amber-500 truncate block">{item.name}</span>
                    </div>
                    <div className="text-right flex-shrink-0">
                      <span className="font-semibold text-amber-800">{fc(item.line_total)}</span>
                      {item.discount_percent > 0 && (
                        <span className="ml-1.5 text-red-500">-{item.discount_percent}%</span>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })()}

      {storeWarning && (
        <div className="flex items-start gap-2 p-3 bg-amber-950 border border-amber-700 rounded-lg text-left text-xs text-amber-300">
          <AlertTriangle className="w-4 h-4 text-amber-400 flex-shrink-0 mt-0.5" />
          <span>{storeWarning}</span>
        </div>
      )}

      <div className="flex gap-3 justify-center flex-wrap">
        <button onClick={onPrint} className="flex items-center gap-2 px-4 py-2.5 border border-gray-600 rounded-lg text-sm font-medium hover:bg-gray-700"><Printer className="w-4 h-4" /> Receipt</button>
        <button onClick={() => setShowGSTInvoice(true)} className="flex items-center gap-2 px-4 py-2.5 border border-blue-300 bg-blue-900/30 text-blue-700 rounded-lg text-sm font-medium hover:bg-blue-100"><FileText className="w-4 h-4" /> Tax Invoice</button>
        <button onClick={onReset} className="flex items-center gap-2 px-6 py-2.5 bg-bv-gold-500 text-white rounded-lg text-sm font-semibold hover:bg-bv-gold-600"><Plus className="w-4 h-4" /> New Sale</button>
      </div>

      {showGSTInvoice && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-gray-800 rounded-xl shadow-2xl w-full max-w-4xl max-h-[90vh] overflow-y-auto">
            <div className="p-4 border-b border-gray-700 flex items-center justify-between no-print">
              <h3 className="font-semibold text-white">GST Tax Invoice</h3>
              <button onClick={() => setShowGSTInvoice(false)} className="p-1 hover:bg-gray-700 rounded"><X className="w-5 h-5" /></button>
            </div>
            <div className="p-4">
              <GSTInvoice order={orderForInvoice as any} store={storeForInvoice as any} onPrint={() => setShowGSTInvoice(false)} />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default StepComplete;

// ============================================================================
// IMS 2.0 - POS Invoice / Order Complete Step
// ============================================================================
// Displays order confirmation, receipt/invoice buttons and the
// incentive-qualifying items panel. Callers: POSLayout (legacy till),
// BillingSurface (fallback branch) and GeneralCounterSurface.
//
// THE TAX INVOICE IS THE SERVER'S DOCUMENT. "Tax Invoice" opens
// GET /orders/{id}/invoice.pdf (backend invoice_pdf.py) -- the same door
// SaleCompleteScreen.tsx uses -- so the serial on the paper is the one minted
// and recorded server-side. The old client-side GSTInvoice modal INVENTED an
// invoice number in the browser (a BV/FY/store/order-slice pattern that
// existed nowhere in the books) and re-computed GST locally; it was retired
// 2026-09-03 (owner decision: one invoice document, one number).

import { useState } from 'react';
import {
  CheckCircle, Plus, Printer, FileText, Sparkles, AlertTriangle,
} from 'lucide-react';
import { usePOSStore } from '../../stores/posStore';
import api from '../../services/api/client';

/** Safe currency format */
function fc(amount: number | undefined | null): string {
  const val = Math.round((amount || 0) * 100) / 100;
  return `₹${val.toLocaleString('en-IN', { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`;
}

interface StepCompleteProps {
  onPrint: () => void;
  onReset: () => void;
}

export function StepComplete({ onPrint, onReset }: StepCompleteProps) {
  const store = usePOSStore();
  const [invoiceError, setInvoiceError] = useState<string | null>(null);
  const [invoiceBusy, setInvoiceBusy] = useState(false);

  // Server-rendered A4 tax invoice. Nothing is numbered, laid out or totalled
  // in the browser -- fetch the PDF and hand it to a new tab for printing.
  // (Same snippet as SaleCompleteScreen.openDocumentPdf; that file lives in
  // pages/pos/next and is owned elsewhere, so the ten lines are duplicated
  // rather than half-extracted.)
  const openTaxInvoicePdf = async () => {
    if (!store.order_id || invoiceBusy) return;
    setInvoiceError(null);
    setInvoiceBusy(true);
    try {
      const res = await api.get(`/orders/${store.order_id}/invoice.pdf`, {
        responseType: 'blob',
      });
      const url = URL.createObjectURL(
        new Blob([res.data as BlobPart], { type: 'application/pdf' }),
      );
      const win = window.open(url, '_blank');
      if (!win) setInvoiceError('Allow pop-ups for this site to open the tax invoice.');
      // Keep the blob alive long enough for the new tab to load it.
      window.setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch {
      // An error body on a blob request is itself a Blob -- no readable server
      // detail here, so the message stays generic.
      setInvoiceError('Could not build the tax invoice. Check the store GSTIN in settings.');
    } finally {
      setInvoiceBusy(false);
    }
  };

  return (
    <div className="max-w-md mx-auto text-center py-8 space-y-6">
      <div className="w-20 h-20 bg-green-100 rounded-full flex items-center justify-center mx-auto"><CheckCircle className="w-10 h-10 text-green-500" /></div>
      <div><h2 className="text-2xl font-bold text-gray-900">Order Created!</h2><p className="text-gray-500 mt-1">Order #{store.order_number}</p></div>
      <div className="bg-white border border-gray-200 rounded-xl p-4 text-left space-y-2 text-sm">
        <div className="flex justify-between"><span className="text-gray-500">Customer</span><span className="font-medium">{store.customer?.name}</span></div>
        <div className="flex justify-between"><span className="text-gray-500">Items</span><span className="font-medium">{(store.cart || []).length}</span></div>
        <div className="flex justify-between"><span className="text-gray-500">Total</span><span className="font-bold text-lg">{fc(store.getGrandTotal())}</span></div>
        <div className="flex justify-between"><span className="text-gray-500">Paid</span><span className="font-medium text-green-600">{fc(store.getTotalPaid())}</span></div>
        {store.getBalance() > 0 && <div className="flex justify-between"><span className="text-gray-500">Balance due</span><span className="font-medium text-red-600">{fc(store.getBalance())}</span></div>}
        {store.sale_type === 'prescription_order' && <div className="flex justify-between"><span className="text-gray-500">Type</span><span className="px-2 py-0.5 bg-purple-50 text-purple-700 rounded text-xs font-medium">Rx Order {'→'} Workshop</span></div>}
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
          <div className="bg-amber-50 border border-amber-300 rounded-xl p-4 text-left text-xs">
            <div className="flex items-center gap-2 mb-2">
              <Sparkles className="w-4 h-4 text-amber-600" />
              <span className="font-semibold text-amber-800">Incentive-qualifying items ({qualifying.length})</span>
              <span className="text-amber-400 ml-auto">Auto-tagged at POS</span>
            </div>
            <div className="space-y-1.5">
              {qualifying.map(item => {
                const brandLabel = item.brand || 'Unknown';
                const subLabel = item.subbrand ? ` · ${item.subbrand}` : '';
                return (
                  <div key={item.id} className="flex items-center justify-between gap-2 bg-white/60 rounded-lg px-2.5 py-1.5">
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

      {invoiceError && (
        <div className="flex items-start gap-2 p-3 bg-red-50 border border-red-200 rounded-lg text-left text-xs text-red-700">
          <AlertTriangle className="w-4 h-4 text-red-400 flex-shrink-0 mt-0.5" />
          <span>{invoiceError}</span>
        </div>
      )}

      <div className="flex gap-3 justify-center flex-wrap">
        <button onClick={onPrint} className="flex items-center gap-2 px-4 py-2.5 border border-gray-300 rounded-lg text-sm font-medium hover:bg-gray-100"><Printer className="w-4 h-4" /> Receipt</button>
        <button
          onClick={() => void openTaxInvoicePdf()}
          disabled={!store.order_id || invoiceBusy}
          title={store.order_id ? 'Open the server-issued GST tax invoice (PDF)' : 'Order id missing — reprint this invoice from the Orders screen'}
          className="flex items-center gap-2 px-4 py-2.5 border border-blue-300 bg-blue-50 text-blue-700 rounded-lg text-sm font-medium hover:bg-blue-100 disabled:opacity-50 disabled:cursor-not-allowed"
        ><FileText className="w-4 h-4" /> {invoiceBusy ? 'Opening…' : 'Tax Invoice'}</button>
        <button onClick={onReset} className="flex items-center gap-2 px-6 py-2.5 bg-bv-red-600 text-white rounded-lg text-sm font-semibold hover:bg-bv-red-700"><Plus className="w-4 h-4" /> New Sale</button>
      </div>
    </div>
  );
}

export default StepComplete;

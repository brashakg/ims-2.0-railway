// ============================================================================
// IMS 2.0 - Purchase Order Print Component
// ============================================================================
// Professional A4 purchase order template for optical retail supply chain

import { useRef } from 'react';
import { Printer, X } from 'lucide-react';

interface POItem {
  product_id: string;
  product_name: string;
  quantity: number;
  unit_price: number;
  total: number;
}

interface POPrintData {
  po_id: string;
  po_number: string;
  po_date: string;
  expected_delivery: string;
  vendor_id: string;
  vendor_name: string;
  vendor_address: string;
  vendor_gstin: string;
  items: POItem[];
  subtotal: number;
  tax_amount: number;
  grand_total: number;
  terms_conditions?: string;
}

interface StoreInfo {
  storeName: string;
  address: string;
  city: string;
  state: string;
  pincode: string;
  phone?: string;
  gstin?: string;
}

interface POPrintProps {
  po: POPrintData;
  store: StoreInfo;
  onClose: () => void;
}

function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleDateString('en-IN', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  });
}

function formatCurrency(value: number): string {
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
  }).format(value);
}

export function POPrint({ po, store, onClose }: POPrintProps) {
  const printRef = useRef<HTMLDivElement>(null);

  const handlePrint = () => {
    window.print();
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl max-w-[900px] w-full max-h-[95vh] overflow-y-auto">
        {/* Action Bar - hidden during print */}
        <div className="flex items-center justify-between p-4 border-b border-gray-200 no-print">
          <h2 className="text-lg font-bold text-gray-900">Print Purchase Order</h2>
          <div className="flex items-center gap-2">
            <button
              onClick={handlePrint}
              className="flex items-center gap-2 bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-lg transition"
            >
              <Printer className="w-4 h-4" />
              Print
            </button>
            <button
              onClick={onClose}
              className="p-2 hover:bg-gray-100 rounded-lg"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Printable Document */}
        <div
          ref={printRef}
          className="po-print-area bg-white p-8"
          style={{ maxWidth: '210mm', margin: '0 auto' }}
        >
          {/* Company Header */}
          <div className="text-center mb-8 pb-4 border-b-4 border-gray-800">
            <h1 className="text-2xl font-bold text-gray-900 uppercase tracking-wide">
              {store.storeName}
            </h1>
            <p className="text-gray-600 text-sm mt-1">{store.address}</p>
            <p className="text-gray-600 text-sm">
              {store.city}, {store.state} - {store.pincode}
            </p>
            {store.phone && <p className="text-gray-600 text-sm">Phone: {store.phone}</p>}
            {store.gstin && <p className="text-gray-600 text-sm">GSTIN: {store.gstin}</p>}
          </div>

          {/* Document Title */}
          <div className="text-center mb-6">
            <h2 className="text-2xl font-bold text-gray-900 uppercase tracking-widest">
              PURCHASE ORDER
            </h2>
          </div>

          {/* PO Details */}
          <div className="grid grid-cols-2 gap-6 mb-6 text-sm">
            <div>
              <p className="text-gray-500 font-semibold">PO Number</p>
              <p className="text-gray-900 font-mono text-lg">{po.po_number}</p>
            </div>
            <div className="text-right">
              <p className="text-gray-500 font-semibold">PO Date</p>
              <p className="text-gray-900 text-lg">{formatDate(po.po_date)}</p>
            </div>
            <div>
              <p className="text-gray-500 font-semibold">Expected Delivery</p>
              <p className="text-gray-900 text-lg">{formatDate(po.expected_delivery)}</p>
            </div>
          </div>

          {/* Vendor Details */}
          <div className="mb-6 p-4 bg-gray-50 border border-gray-300 rounded">
            <h3 className="font-bold text-gray-900 mb-2 uppercase">Vendor Details</h3>
            <p className="text-gray-900 font-semibold">{po.vendor_name}</p>
            <p className="text-gray-700 text-sm">{po.vendor_address}</p>
            {po.vendor_gstin && (
              <p className="text-gray-700 text-sm mt-1">GSTIN: {po.vendor_gstin}</p>
            )}
          </div>

          {/* Items Table */}
          <div className="mb-6">
            <h3 className="font-bold text-gray-900 mb-2 uppercase">Items Ordered</h3>
            <table className="w-full border-collapse">
              <thead>
                <tr className="bg-gray-800 text-white">
                  <th className="border border-gray-600 px-3 py-2 text-left font-semibold">Sr.</th>
                  <th className="border border-gray-600 px-3 py-2 text-left font-semibold">Product Name</th>
                  <th className="border border-gray-600 px-3 py-2 text-center font-semibold">Quantity</th>
                  <th className="border border-gray-600 px-3 py-2 text-right font-semibold">Unit Price</th>
                  <th className="border border-gray-600 px-3 py-2 text-right font-semibold">Total</th>
                </tr>
              </thead>
              <tbody>
                {po.items.map((item, index) => (
                  <tr key={item.product_id} className="hover:bg-gray-50">
                    <td className="border border-gray-300 px-3 py-2 text-gray-900">{index + 1}</td>
                    <td className="border border-gray-300 px-3 py-2 text-gray-900">{item.product_name}</td>
                    <td className="border border-gray-300 px-3 py-2 text-center text-gray-900">
                      {item.quantity}
                    </td>
                    <td className="border border-gray-300 px-3 py-2 text-right text-gray-900 font-mono">
                      {formatCurrency(item.unit_price)}
                    </td>
                    <td className="border border-gray-300 px-3 py-2 text-right text-gray-900 font-mono font-semibold">
                      {formatCurrency(item.total)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Totals */}
          <div className="mb-6 flex justify-end">
            <table className="w-80 text-sm">
              <tbody>
                <tr>
                  <td className="text-gray-700 px-4 py-2 font-semibold">Subtotal</td>
                  <td className="text-gray-900 px-4 py-2 font-mono text-right">
                    {formatCurrency(po.subtotal)}
                  </td>
                </tr>
                <tr className="bg-gray-100">
                  <td className="text-gray-700 px-4 py-2 font-semibold">Tax (SGST+CGST)</td>
                  <td className="text-gray-900 px-4 py-2 font-mono text-right">
                    {formatCurrency(po.tax_amount)}
                  </td>
                </tr>
                <tr className="border-t-2 border-gray-800 bg-gray-800 text-white">
                  <td className="px-4 py-2 font-bold uppercase">Grand Total</td>
                  <td className="px-4 py-2 font-mono text-right font-bold text-lg">
                    {formatCurrency(po.grand_total)}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          {/* Terms & Conditions */}
          {po.terms_conditions && (
            <div className="mb-6 p-4 bg-gray-50 border border-gray-300 rounded">
              <h3 className="font-bold text-gray-900 mb-2 uppercase">Terms & Conditions</h3>
              <p className="text-gray-700 text-sm whitespace-pre-wrap">{po.terms_conditions}</p>
            </div>
          )}

          {/* Signature Line */}
          <div className="mt-8 pt-6 border-t border-gray-300 flex justify-between items-end">
            <div className="text-center">
              <div className="w-40 border-b border-gray-400 mb-2" />
              <p className="text-xs text-gray-600">Authorized By</p>
            </div>
            <div className="text-center">
              <div className="w-40 border-b border-gray-400 mb-2" />
              <p className="text-xs text-gray-600">Vendor Signature</p>
            </div>
          </div>

          {/* Footer */}
          <div className="mt-4 pt-2 border-t border-gray-200 text-center">
            <p className="text-[10px] text-gray-400">
              {store.storeName} &middot; {store.city}, {store.state} &middot; {po.po_number}
            </p>
          </div>
        </div>
      </div>

      {/* Print-specific CSS for A4 page */}
      <style>{`
        @media print {
          body * {
            visibility: hidden;
          }
          .po-print-area,
          .po-print-area * {
            visibility: visible;
          }
          .po-print-area {
            position: absolute;
            left: 0;
            top: 0;
            width: 100%;
            padding: 12mm;
            margin: 0;
            max-width: none;
          }

          .no-print {
            display: none !important;
          }

          @page {
            size: A4;
            margin: 12mm;
          }

          table {
            page-break-inside: avoid;
          }
          .po-print-area {
            font-size: 11pt;
            color: #000;
            background: #fff;
          }
          .po-print-area h1 {
            font-size: 18pt;
          }
          .po-print-area h2 {
            font-size: 16pt;
          }
          .po-print-area h3 {
            font-size: 10pt;
          }
        }
      `}</style>
    </div>
  );
}

export default POPrint;

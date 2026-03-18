// ============================================================================
// IMS 2.0 - Delivery Challan Print Component
// ============================================================================
// A4 format challan for inter-store transfers and deliveries

import { useRef } from 'react';
import { Printer, X } from 'lucide-react';

interface ChallanItem {
  productName: string;
  quantity: number;
  serialNumbers?: string;
  remarks?: string;
}

interface ChallanPrintData {
  challanNumber: string;
  date: string;
  fromStore: string;
  toStore: string;
  items: ChallanItem[];
  dispatchedBy?: string;
  receivedBy?: string;
  notes?: string;
}

interface StoreInfo {
  storeName: string;
  address: string;
  city: string;
  state: string;
  pincode: string;
  phone?: string;
}

interface DeliveryChallanPrintProps {
  challan: ChallanPrintData;
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

export function DeliveryChallanPrint({
  challan,
  store,
  onClose,
}: DeliveryChallanPrintProps) {
  const printRef = useRef<HTMLDivElement>(null);

  const handlePrint = () => {
    window.print();
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl max-w-[900px] w-full max-h-[95vh] overflow-y-auto">
        {/* Action Bar - hidden during print */}
        <div className="flex items-center justify-between p-4 border-b border-gray-200 no-print">
          <h2 className="text-lg font-bold text-gray-900">Print Delivery Challan</h2>
          <div className="flex items-center gap-2">
            <button
              onClick={handlePrint}
              className="flex items-center gap-2 bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-lg transition"
            >
              <Printer className="w-4 h-4" />
              Print
            </button>
            <button onClick={onClose} className="p-2 hover:bg-gray-100 rounded-lg">
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Printable Document */}
        <div
          ref={printRef}
          className="challan-print-area bg-white p-8"
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
          </div>

          {/* Document Title */}
          <div className="text-center mb-6">
            <h2 className="text-2xl font-bold text-gray-900 uppercase tracking-widest">
              DELIVERY CHALLAN
            </h2>
          </div>

          {/* Challan Details */}
          <div className="grid grid-cols-2 gap-6 mb-6 text-sm">
            <div>
              <p className="text-gray-500 font-semibold">Challan Number</p>
              <p className="text-gray-900 font-mono text-lg">{challan.challanNumber}</p>
            </div>
            <div className="text-right">
              <p className="text-gray-500 font-semibold">Date</p>
              <p className="text-gray-900 text-lg">{formatDate(challan.date)}</p>
            </div>
          </div>

          {/* Store Details */}
          <div className="grid grid-cols-2 gap-6 mb-6">
            <div className="p-4 bg-gray-50 border border-gray-300 rounded">
              <h3 className="font-bold text-gray-900 mb-2 uppercase text-sm">From Store</h3>
              <p className="text-gray-900 font-semibold">{challan.fromStore}</p>
            </div>
            <div className="p-4 bg-gray-50 border border-gray-300 rounded">
              <h3 className="font-bold text-gray-900 mb-2 uppercase text-sm">To Store</h3>
              <p className="text-gray-900 font-semibold">{challan.toStore}</p>
            </div>
          </div>

          {/* Items Table */}
          <div className="mb-6">
            <h3 className="font-bold text-gray-900 mb-2 uppercase text-sm">Items</h3>
            <table className="w-full border-collapse">
              <thead>
                <tr className="bg-gray-800 text-white">
                  <th className="border border-gray-600 px-3 py-2 text-left font-semibold">Sr.</th>
                  <th className="border border-gray-600 px-3 py-2 text-left font-semibold">
                    Product Name
                  </th>
                  <th className="border border-gray-600 px-3 py-2 text-center font-semibold">
                    Qty
                  </th>
                  <th className="border border-gray-600 px-3 py-2 text-left font-semibold">
                    Serial Numbers / Remarks
                  </th>
                </tr>
              </thead>
              <tbody>
                {challan.items.map((item, index) => (
                  <tr key={index} className="hover:bg-gray-50">
                    <td className="border border-gray-300 px-3 py-2 text-gray-900">{index + 1}</td>
                    <td className="border border-gray-300 px-3 py-2 text-gray-900">
                      {item.productName}
                    </td>
                    <td className="border border-gray-300 px-3 py-2 text-center text-gray-900">
                      {item.quantity}
                    </td>
                    <td className="border border-gray-300 px-3 py-2 text-gray-900 text-sm">
                      {item.serialNumbers || item.remarks || '-'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Notes */}
          {challan.notes && (
            <div className="mb-6 p-4 bg-gray-50 border border-gray-300 rounded">
              <h3 className="font-bold text-gray-900 mb-2 uppercase text-sm">Notes</h3>
              <p className="text-gray-700 text-sm whitespace-pre-wrap">{challan.notes}</p>
            </div>
          )}

          {/* Signature Lines */}
          <div className="mt-8 pt-6 border-t border-gray-300 flex justify-between items-end">
            <div className="text-center">
              <div className="w-40 border-b border-gray-400 mb-2" />
              <p className="text-xs text-gray-600">Dispatched By</p>
              {challan.dispatchedBy && (
                <p className="text-xs text-gray-700 mt-1 font-medium">{challan.dispatchedBy}</p>
              )}
            </div>
            <div className="text-center">
              <div className="w-40 border-b border-gray-400 mb-2" />
              <p className="text-xs text-gray-600">Received By</p>
              {challan.receivedBy && (
                <p className="text-xs text-gray-700 mt-1 font-medium">{challan.receivedBy}</p>
              )}
            </div>
            <div className="text-center">
              <div className="w-40 border-b border-gray-400 mb-2" />
              <p className="text-xs text-gray-600">Verified By</p>
            </div>
          </div>

          {/* Footer */}
          <div className="mt-4 pt-2 border-t border-gray-200 text-center">
            <p className="text-[10px] text-gray-400">
              {store.storeName} • {challan.challanNumber} • {formatDate(challan.date)}
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
          .challan-print-area,
          .challan-print-area * {
            visibility: visible;
          }
          .challan-print-area {
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
          .challan-print-area {
            font-size: 11pt;
            color: #000;
            background: #fff;
          }
          .challan-print-area h1 {
            font-size: 18pt;
          }
          .challan-print-area h2 {
            font-size: 16pt;
          }
          .challan-print-area h3 {
            font-size: 10pt;
          }
        }
      `}</style>
    </div>
  );
}

export default DeliveryChallanPrint;

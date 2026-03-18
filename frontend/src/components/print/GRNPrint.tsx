// ============================================================================
// IMS 2.0 - Goods Receipt Note Print Component
// ============================================================================
// Professional A4 GRN template for optical retail supply chain

import { useRef } from 'react';
import { Printer, X } from 'lucide-react';

interface GRNItem {
  product_id: string;
  product_name: string;
  ordered_qty: number;
  received_qty: number;
  variance: number;
  remarks?: string;
}

interface GRNPrintData {
  grn_id: string;
  grn_number: string;
  grn_date: string;
  po_number: string;
  vendor_id: string;
  vendor_name: string;
  vendor_address: string;
  vendor_gstin: string;
  items: GRNItem[];
  quality_inspection: 'accepted' | 'rejected' | 'partially_accepted';
  inspection_remarks?: string;
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

interface GRNPrintProps {
  grn: GRNPrintData;
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

function getInspectionStatusColor(status: string): string {
  switch (status) {
    case 'accepted':
      return 'bg-green-100 text-green-800 border-green-300';
    case 'rejected':
      return 'bg-red-100 text-red-800 border-red-300';
    case 'partially_accepted':
      return 'bg-yellow-100 text-yellow-800 border-yellow-300';
    default:
      return 'bg-gray-100 text-gray-800 border-gray-300';
  }
}

function getVarianceColor(variance: number): string {
  if (variance > 0) return 'text-red-600'; // Over-received
  if (variance < 0) return 'text-orange-600'; // Short-received
  return 'text-green-600'; // Perfect match
}

export function GRNPrint({ grn, store, onClose }: GRNPrintProps) {
  const printRef = useRef<HTMLDivElement>(null);

  const handlePrint = () => {
    window.print();
  };

  const totalOrdered = grn.items.reduce((sum, item) => sum + item.ordered_qty, 0);
  const totalReceived = grn.items.reduce((sum, item) => sum + item.received_qty, 0);
  const totalVariance = totalReceived - totalOrdered;

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl max-w-[900px] w-full max-h-[95vh] overflow-y-auto">
        {/* Action Bar - hidden during print */}
        <div className="flex items-center justify-between p-4 border-b border-gray-200 no-print">
          <h2 className="text-lg font-bold text-gray-900">Print Goods Receipt Note</h2>
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
          className="grn-print-area bg-white p-8"
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
              GOODS RECEIPT NOTE
            </h2>
          </div>

          {/* GRN Details */}
          <div className="grid grid-cols-2 gap-6 mb-6 text-sm">
            <div>
              <p className="text-gray-500 font-semibold">GRN Number</p>
              <p className="text-gray-900 font-mono text-lg">{grn.grn_number}</p>
            </div>
            <div className="text-right">
              <p className="text-gray-500 font-semibold">GRN Date</p>
              <p className="text-gray-900 text-lg">{formatDate(grn.grn_date)}</p>
            </div>
            <div>
              <p className="text-gray-500 font-semibold">Against PO</p>
              <p className="text-gray-900 font-mono text-lg">{grn.po_number}</p>
            </div>
          </div>

          {/* Vendor Details */}
          <div className="mb-6 p-4 bg-gray-50 border border-gray-300 rounded">
            <h3 className="font-bold text-gray-900 mb-2 uppercase">Vendor Details</h3>
            <p className="text-gray-900 font-semibold">{grn.vendor_name}</p>
            <p className="text-gray-700 text-sm">{grn.vendor_address}</p>
            {grn.vendor_gstin && (
              <p className="text-gray-700 text-sm mt-1">GSTIN: {grn.vendor_gstin}</p>
            )}
          </div>

          {/* Received Items Table */}
          <div className="mb-6">
            <h3 className="font-bold text-gray-900 mb-2 uppercase">Items Received</h3>
            <table className="w-full border-collapse">
              <thead>
                <tr className="bg-gray-800 text-white">
                  <th className="border border-gray-600 px-3 py-2 text-left font-semibold">Sr.</th>
                  <th className="border border-gray-600 px-3 py-2 text-left font-semibold">Product Name</th>
                  <th className="border border-gray-600 px-3 py-2 text-center font-semibold">Ordered</th>
                  <th className="border border-gray-600 px-3 py-2 text-center font-semibold">Received</th>
                  <th className="border border-gray-600 px-3 py-2 text-center font-semibold">Variance</th>
                  <th className="border border-gray-600 px-3 py-2 text-left font-semibold">Remarks</th>
                </tr>
              </thead>
              <tbody>
                {grn.items.map((item, index) => (
                  <tr key={item.product_id} className="hover:bg-gray-50">
                    <td className="border border-gray-300 px-3 py-2 text-gray-900">{index + 1}</td>
                    <td className="border border-gray-300 px-3 py-2 text-gray-900">{item.product_name}</td>
                    <td className="border border-gray-300 px-3 py-2 text-center text-gray-900 font-semibold">
                      {item.ordered_qty}
                    </td>
                    <td className="border border-gray-300 px-3 py-2 text-center text-gray-900 font-semibold">
                      {item.received_qty}
                    </td>
                    <td className={`border border-gray-300 px-3 py-2 text-center font-semibold ${getVarianceColor(item.variance)}`}>
                      {item.variance > 0 ? '+' : ''}{item.variance}
                    </td>
                    <td className="border border-gray-300 px-3 py-2 text-gray-700 text-sm">
                      {item.remarks || '-'}
                    </td>
                  </tr>
                ))}
                <tr className="bg-gray-100 font-semibold">
                  <td colSpan={2} className="border border-gray-300 px-3 py-2 text-right">TOTAL</td>
                  <td className="border border-gray-300 px-3 py-2 text-center">{totalOrdered}</td>
                  <td className="border border-gray-300 px-3 py-2 text-center">{totalReceived}</td>
                  <td className={`border border-gray-300 px-3 py-2 text-center ${getVarianceColor(totalVariance)}`}>
                    {totalVariance > 0 ? '+' : ''}{totalVariance}
                  </td>
                  <td className="border border-gray-300 px-3 py-2"></td>
                </tr>
              </tbody>
            </table>
          </div>

          {/* Quality Inspection */}
          <div className="mb-6">
            <h3 className="font-bold text-gray-900 mb-3 uppercase">Quality Inspection</h3>
            <div className={`p-4 border rounded ${getInspectionStatusColor(grn.quality_inspection)}`}>
              <p className="font-semibold uppercase">{grn.quality_inspection.replace(/_/g, ' ')}</p>
              {grn.inspection_remarks && (
                <p className="text-sm mt-2">{grn.inspection_remarks}</p>
              )}
            </div>
          </div>

          {/* Signature Lines */}
          <div className="mt-8 pt-6 border-t border-gray-300">
            <div className="grid grid-cols-3 gap-4">
              <div className="text-center">
                <div className="w-full border-b border-gray-400 mb-2" style={{ minHeight: '50px' }} />
                <p className="text-xs text-gray-600 font-semibold">Received By</p>
              </div>
              <div className="text-center">
                <div className="w-full border-b border-gray-400 mb-2" style={{ minHeight: '50px' }} />
                <p className="text-xs text-gray-600 font-semibold">Verified By</p>
              </div>
              <div className="text-center">
                <div className="w-full border-b border-gray-400 mb-2" style={{ minHeight: '50px' }} />
                <p className="text-xs text-gray-600 font-semibold">Authorized By</p>
              </div>
            </div>
          </div>

          {/* Footer */}
          <div className="mt-4 pt-2 border-t border-gray-200 text-center">
            <p className="text-[10px] text-gray-400">
              {store.storeName} &middot; {store.city}, {store.state} &middot; {grn.grn_number}
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
          .grn-print-area,
          .grn-print-area * {
            visibility: visible;
          }
          .grn-print-area {
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
          .grn-print-area {
            font-size: 11pt;
            color: #000;
            background: #fff;
          }
          .grn-print-area h1 {
            font-size: 18pt;
          }
          .grn-print-area h2 {
            font-size: 16pt;
          }
          .grn-print-area h3 {
            font-size: 10pt;
          }
        }
      `}</style>
    </div>
  );
}

export default GRNPrint;

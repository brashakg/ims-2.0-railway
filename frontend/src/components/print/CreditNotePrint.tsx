// ============================================================================
// IMS 2.0 - Credit Note Print Component
// ============================================================================
// A4 format credit note for returns and adjustments

import { useRef } from 'react';
import { Printer, X } from 'lucide-react';

interface CreditNoteItem {
  description: string;
  quantity: number;
  unitPrice: number;
  amount: number;
}

interface CreditNotePrintData {
  creditNoteNumber: string;
  date: string;
  customerName: string;
  customerAddress?: string;
  originalInvoiceNumber: string;
  originalInvoiceDate: string;
  items: CreditNoteItem[];
  creditAmount: number;
  reason: string;
  termsOfUse?: string;
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

interface CreditNotePrintProps {
  creditNote: CreditNotePrintData;
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

export function CreditNotePrint({
  creditNote,
  store,
  onClose,
}: CreditNotePrintProps) {
  const printRef = useRef<HTMLDivElement>(null);

  const handlePrint = () => {
    window.print();
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl max-w-[900px] w-full max-h-[95vh] overflow-y-auto">
        {/* Action Bar - hidden during print */}
        <div className="flex items-center justify-between p-4 border-b border-gray-200 no-print">
          <h2 className="text-lg font-bold text-gray-900">Print Credit Note</h2>
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
          className="credit-note-print-area bg-white p-8"
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
              CREDIT NOTE
            </h2>
            <p className="text-red-600 text-sm font-semibold mt-1">
              Issued for Return / Adjustment
            </p>
          </div>

          {/* CN Details */}
          <div className="grid grid-cols-3 gap-4 mb-6 text-sm">
            <div>
              <p className="text-gray-500 font-semibold">Credit Note Number</p>
              <p className="text-gray-900 font-mono text-lg">{creditNote.creditNoteNumber}</p>
            </div>
            <div className="text-center">
              <p className="text-gray-500 font-semibold">CN Date</p>
              <p className="text-gray-900 text-lg">{formatDate(creditNote.date)}</p>
            </div>
            <div className="text-right">
              <p className="text-gray-500 font-semibold">Reason</p>
              <p className="text-gray-900 text-sm">{creditNote.reason}</p>
            </div>
          </div>

          {/* Customer Details */}
          <div className="mb-6 p-4 bg-gray-50 border border-gray-300 rounded">
            <h3 className="font-bold text-gray-900 mb-2 uppercase text-sm">Customer Details</h3>
            <p className="text-gray-900 font-semibold">{creditNote.customerName}</p>
            {creditNote.customerAddress && (
              <p className="text-gray-700 text-sm">{creditNote.customerAddress}</p>
            )}
          </div>

          {/* Original Invoice Reference */}
          <div className="mb-6 p-3 bg-blue-50 border border-blue-300 rounded">
            <h3 className="font-bold text-gray-900 mb-2 uppercase text-sm">
              Original Invoice Reference
            </h3>
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <p className="text-gray-500 font-semibold text-xs">Invoice Number</p>
                <p className="text-gray-900 font-mono">{creditNote.originalInvoiceNumber}</p>
              </div>
              <div>
                <p className="text-gray-500 font-semibold text-xs">Invoice Date</p>
                <p className="text-gray-900 font-mono">{formatDate(creditNote.originalInvoiceDate)}</p>
              </div>
            </div>
          </div>

          {/* Items Table */}
          <div className="mb-6">
            <h3 className="font-bold text-gray-900 mb-2 uppercase text-sm">Items Returned</h3>
            <table className="w-full border-collapse">
              <thead>
                <tr className="bg-gray-800 text-white">
                  <th className="border border-gray-600 px-3 py-2 text-left font-semibold">Sr.</th>
                  <th className="border border-gray-600 px-3 py-2 text-left font-semibold">
                    Description
                  </th>
                  <th className="border border-gray-600 px-3 py-2 text-center font-semibold">Qty</th>
                  <th className="border border-gray-600 px-3 py-2 text-right font-semibold">
                    Unit Price
                  </th>
                  <th className="border border-gray-600 px-3 py-2 text-right font-semibold">Amount</th>
                </tr>
              </thead>
              <tbody>
                {creditNote.items.map((item, index) => (
                  <tr key={index} className="hover:bg-gray-50">
                    <td className="border border-gray-300 px-3 py-2 text-gray-900">{index + 1}</td>
                    <td className="border border-gray-300 px-3 py-2 text-gray-900">
                      {item.description}
                    </td>
                    <td className="border border-gray-300 px-3 py-2 text-center text-gray-900">
                      {item.quantity}
                    </td>
                    <td className="border border-gray-300 px-3 py-2 text-right text-gray-900 font-mono">
                      {formatCurrency(item.unitPrice)}
                    </td>
                    <td className="border border-gray-300 px-3 py-2 text-right text-gray-900 font-mono font-semibold">
                      {formatCurrency(item.amount)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Credit Amount */}
          <div className="mb-6 flex justify-end">
            <div className="w-64 bg-gray-100 border-2 border-gray-800 rounded p-4">
              <div className="flex justify-between items-center">
                <p className="text-gray-900 font-bold text-lg">Total Credit Amount</p>
                <p className="text-gray-900 font-mono text-lg font-bold">
                  {formatCurrency(creditNote.creditAmount)}
                </p>
              </div>
            </div>
          </div>

          {/* Terms of Use */}
          {creditNote.termsOfUse && (
            <div className="mb-6 p-4 bg-gray-50 border border-gray-300 rounded">
              <h3 className="font-bold text-gray-900 mb-2 uppercase text-sm">Terms & Conditions</h3>
              <p className="text-gray-700 text-sm whitespace-pre-wrap">{creditNote.termsOfUse}</p>
            </div>
          )}

          {/* Standard Terms */}
          <div className="mb-6 p-4 bg-yellow-50 border border-yellow-300 rounded">
            <p className="text-xs text-gray-700 leading-relaxed">
              This credit note is valid for 12 months from the date of issue. Credit can be
              applied against future purchases or refunded as per store policy. No interest will
              be paid on refunds.
            </p>
          </div>

          {/* Signature Lines */}
          <div className="mt-8 pt-6 border-t border-gray-300 flex justify-between items-end">
            <div className="text-center">
              <div className="w-40 border-b border-gray-400 mb-2" />
              <p className="text-xs text-gray-600">Authorized By</p>
            </div>
            <div className="text-center">
              <div className="w-40 border-b border-gray-400 mb-2" />
              <p className="text-xs text-gray-600">Received By</p>
            </div>
          </div>

          {/* Footer */}
          <div className="mt-4 pt-2 border-t border-gray-200 text-center">
            <p className="text-[10px] text-gray-400">
              {store.storeName} • {creditNote.creditNoteNumber} • {formatDate(creditNote.date)}
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
          .credit-note-print-area,
          .credit-note-print-area * {
            visibility: visible;
          }
          .credit-note-print-area {
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
          .credit-note-print-area {
            font-size: 11pt;
            color: #000;
            background: #fff;
          }
          .credit-note-print-area h1 {
            font-size: 18pt;
          }
          .credit-note-print-area h2 {
            font-size: 16pt;
          }
          .credit-note-print-area h3 {
            font-size: 10pt;
          }
        }
      `}</style>
    </div>
  );
}

export default CreditNotePrint;

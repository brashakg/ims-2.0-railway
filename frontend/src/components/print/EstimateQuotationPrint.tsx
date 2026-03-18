// ============================================================================
// IMS 2.0 - Estimate/Quotation Print Component
// ============================================================================
// A4 format quotation for product pricing and GST breakdown

import { useRef } from 'react';
import { Printer, X } from 'lucide-react';

interface QuotationItem {
  description: string;
  quantity: number;
  mrp: number;
  offerPrice: number;
  total: number;
}

interface EstimateQuotationPrintData {
  quoteNumber: string;
  date: string;
  validUntil: string;
  customerName: string;
  customerPhone?: string;
  customerAddress?: string;
  items: QuotationItem[];
  subtotal: number;
  cgst: number;
  sgst: number;
  totalGst: number;
  grandTotal: number;
  termsAndConditions?: string;
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

interface EstimateQuotationPrintProps {
  quotation: EstimateQuotationPrintData;
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

export function EstimateQuotationPrint({
  quotation,
  store,
  onClose,
}: EstimateQuotationPrintProps) {
  const printRef = useRef<HTMLDivElement>(null);

  const handlePrint = () => {
    window.print();
  };

  const daysDiff = Math.ceil(
    (new Date(quotation.validUntil).getTime() - new Date(quotation.date).getTime()) /
      (1000 * 60 * 60 * 24)
  );

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl max-w-[900px] w-full max-h-[95vh] overflow-y-auto">
        {/* Action Bar - hidden during print */}
        <div className="flex items-center justify-between p-4 border-b border-gray-200 no-print">
          <h2 className="text-lg font-bold text-gray-900">Print Quotation</h2>
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
          className="quote-print-area bg-white p-8"
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
              ESTIMATE / QUOTATION
            </h2>
          </div>

          {/* Quote Details */}
          <div className="grid grid-cols-3 gap-4 mb-6 text-sm">
            <div>
              <p className="text-gray-500 font-semibold">Quote Number</p>
              <p className="text-gray-900 font-mono text-lg">{quotation.quoteNumber}</p>
            </div>
            <div className="text-center">
              <p className="text-gray-500 font-semibold">Date</p>
              <p className="text-gray-900 text-lg">{formatDate(quotation.date)}</p>
            </div>
            <div className="text-right">
              <p className="text-gray-500 font-semibold">Valid Until</p>
              <p className="text-gray-900 text-lg">{formatDate(quotation.validUntil)}</p>
              <p className="text-gray-600 text-xs mt-1">({daysDiff} days from issue)</p>
            </div>
          </div>

          {/* Customer Details */}
          <div className="mb-6 p-4 bg-gray-50 border border-gray-300 rounded">
            <h3 className="font-bold text-gray-900 mb-2 uppercase text-sm">Customer Details</h3>
            <p className="text-gray-900 font-semibold">{quotation.customerName}</p>
            {quotation.customerPhone && (
              <p className="text-gray-700 text-sm">Phone: {quotation.customerPhone}</p>
            )}
            {quotation.customerAddress && (
              <p className="text-gray-700 text-sm">{quotation.customerAddress}</p>
            )}
          </div>

          {/* Items Table */}
          <div className="mb-6">
            <h3 className="font-bold text-gray-900 mb-2 uppercase text-sm">Items & Pricing</h3>
            <table className="w-full border-collapse">
              <thead>
                <tr className="bg-gray-800 text-white">
                  <th className="border border-gray-600 px-3 py-2 text-left font-semibold">Sr.</th>
                  <th className="border border-gray-600 px-3 py-2 text-left font-semibold">
                    Description
                  </th>
                  <th className="border border-gray-600 px-3 py-2 text-right font-semibold">MRP</th>
                  <th className="border border-gray-600 px-3 py-2 text-right font-semibold">
                    Offer Price
                  </th>
                  <th className="border border-gray-600 px-3 py-2 text-center font-semibold">Qty</th>
                  <th className="border border-gray-600 px-3 py-2 text-right font-semibold">Total</th>
                </tr>
              </thead>
              <tbody>
                {quotation.items.map((item, index) => (
                  <tr key={index} className="hover:bg-gray-50">
                    <td className="border border-gray-300 px-3 py-2 text-gray-900">{index + 1}</td>
                    <td className="border border-gray-300 px-3 py-2 text-gray-900">
                      {item.description}
                    </td>
                    <td className="border border-gray-300 px-3 py-2 text-right text-gray-900 font-mono">
                      {formatCurrency(item.mrp)}
                    </td>
                    <td className="border border-gray-300 px-3 py-2 text-right text-gray-900 font-mono font-semibold text-blue-600">
                      {formatCurrency(item.offerPrice)}
                    </td>
                    <td className="border border-gray-300 px-3 py-2 text-center text-gray-900">
                      {item.quantity}
                    </td>
                    <td className="border border-gray-300 px-3 py-2 text-right text-gray-900 font-mono font-semibold">
                      {formatCurrency(item.total)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Totals with GST */}
          <div className="mb-6 flex justify-end">
            <table className="w-96 text-sm">
              <tbody>
                <tr>
                  <td className="text-gray-700 px-4 py-2 font-semibold">Subtotal</td>
                  <td className="text-gray-900 px-4 py-2 font-mono text-right">
                    {formatCurrency(quotation.subtotal)}
                  </td>
                </tr>
                <tr className="bg-gray-100">
                  <td className="text-gray-700 px-4 py-2 font-semibold">CGST (9%)</td>
                  <td className="text-gray-900 px-4 py-2 font-mono text-right">
                    {formatCurrency(quotation.cgst)}
                  </td>
                </tr>
                <tr className="bg-gray-100">
                  <td className="text-gray-700 px-4 py-2 font-semibold">SGST (9%)</td>
                  <td className="text-gray-900 px-4 py-2 font-mono text-right">
                    {formatCurrency(quotation.sgst)}
                  </td>
                </tr>
                <tr>
                  <td className="text-gray-700 px-4 py-2 font-semibold">Total GST</td>
                  <td className="text-gray-900 px-4 py-2 font-mono text-right">
                    {formatCurrency(quotation.totalGst)}
                  </td>
                </tr>
                <tr className="border-t-2 border-gray-800 bg-gray-800 text-white">
                  <td className="px-4 py-2 font-bold uppercase">Grand Total</td>
                  <td className="px-4 py-2 font-mono text-right font-bold text-lg">
                    {formatCurrency(quotation.grandTotal)}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          {/* Terms & Conditions */}
          {quotation.termsAndConditions && (
            <div className="mb-6 p-4 bg-gray-50 border border-gray-300 rounded">
              <h3 className="font-bold text-gray-900 mb-2 uppercase text-sm">
                Terms & Conditions
              </h3>
              <p className="text-gray-700 text-sm whitespace-pre-wrap">
                {quotation.termsAndConditions}
              </p>
            </div>
          )}

          {/* Disclaimer */}
          <div className="mb-6 p-4 bg-yellow-50 border-2 border-yellow-300 rounded">
            <p className="text-center text-gray-900 font-bold text-sm">
              THIS IS NOT A TAX INVOICE
            </p>
            <p className="text-gray-700 text-xs mt-2 leading-relaxed text-center">
              This is an estimate/quotation document and not a tax invoice. Prices are valid for{' '}
              {daysDiff} days from the date of issue. An official tax invoice will be issued upon
              confirmation of order and payment.
            </p>
          </div>

          {/* Signature Line */}
          <div className="mt-8 pt-6 border-t border-gray-300 text-right">
            <div className="w-48 ml-auto text-center">
              <div className="border-b border-gray-400 mb-2" style={{ height: '60px' }} />
              <p className="text-xs text-gray-600">Authorized Signature</p>
              <p className="text-xs text-gray-600 mt-1">Date: {formatDate(quotation.date)}</p>
            </div>
          </div>

          {/* Footer */}
          <div className="mt-4 pt-2 border-t border-gray-200 text-center">
            <p className="text-[10px] text-gray-400">
              {store.storeName} • {quotation.quoteNumber}
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
          .quote-print-area,
          .quote-print-area * {
            visibility: visible;
          }
          .quote-print-area {
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
          .quote-print-area {
            font-size: 11pt;
            color: #000;
            background: #fff;
          }
          .quote-print-area h1 {
            font-size: 18pt;
          }
          .quote-print-area h2 {
            font-size: 16pt;
          }
          .quote-print-area h3 {
            font-size: 10pt;
          }
        }
      `}</style>
    </div>
  );
}

export default EstimateQuotationPrint;

// ============================================================================
// IMS 2.0 - GST Invoice Template
// ============================================================================
// GST-compliant invoice with HSN codes and tax breakup

import { useRef } from 'react';
import { Printer, Download } from 'lucide-react';
import { calculateGST, calculateIGST, validateGSTNumber } from '../../constants/gst';
import type { Order, Store } from '../../types';

interface GSTInvoiceProps {
  order: Order;
  store: Store;
  onPrint?: () => void;
}

interface InvoiceLineItem {
  productName: string;
  hsnCode: string;
  quantity: number;
  unitPrice: number;
  discount: number;
  taxableValue: number;
  gstRate: number;
  cgst: number;
  sgst: number;
  igst: number;
  totalAmount: number;
}

export function GSTInvoice({ order, store, onPrint }: GSTInvoiceProps) {
  const invoiceRef = useRef<HTMLDivElement>(null);

  // Check if transaction is inter-state (IGST) or intra-state (CGST + SGST)
  const isInterState = false; // TODO: Implement based on customer state vs store state

  // Convert order items to invoice line items with GST calculation
  const lineItems: InvoiceLineItem[] = order.items.map(item => {
    const taxableValue = item.finalPrice;
    const gstRate = 12; // Default rate, should come from product
    const hsnCode = '9004'; // Default HSN, should come from product

    let cgst = 0, sgst = 0, igst = 0;

    if (isInterState) {
      const gstCalc = calculateIGST(taxableValue, gstRate);
      igst = gstCalc.igst;
    } else {
      const gstCalc = calculateGST(taxableValue, gstRate);
      cgst = gstCalc.cgst;
      sgst = gstCalc.sgst;
    }

    return {
      productName: item.productName,
      hsnCode: hsnCode,
      quantity: item.quantity,
      unitPrice: item.unitPrice,
      discount: item.discountAmount,
      taxableValue: taxableValue,
      gstRate: gstRate,
      cgst: cgst,
      sgst: sgst,
      igst: igst,
      totalAmount: taxableValue,
    };
  });

  // Calculate totals
  const subtotal = lineItems.reduce((sum, item) => sum + item.taxableValue, 0);
  const totalCGST = lineItems.reduce((sum, item) => sum + item.cgst, 0);
  const totalSGST = lineItems.reduce((sum, item) => sum + item.sgst, 0);
  const totalIGST = lineItems.reduce((sum, item) => sum + item.igst, 0);
  const totalTax = totalCGST + totalSGST + totalIGST;
  const grandTotal = subtotal + totalTax;

  const handlePrint = () => {
    window.print();
    if (onPrint) onPrint();
  };

  const handleDownloadPDF = () => {
    // In production, use a library like jsPDF or call backend API to generate PDF
    alert('PDF download feature will be implemented with backend integration');
  };

  // Convert number to words (for amount in words)
  const numberToWords = (num: number): string => {
    // Simplified implementation - in production use a proper library
    const ones = ['', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine'];
    const tens = ['', '', 'Twenty', 'Thirty', 'Forty', 'Fifty', 'Sixty', 'Seventy', 'Eighty', 'Ninety'];
    const teens = ['Ten', 'Eleven', 'Twelve', 'Thirteen', 'Fourteen', 'Fifteen', 'Sixteen', 'Seventeen', 'Eighteen', 'Nineteen'];

    if (num === 0) return 'Zero';
    if (num < 10) return ones[num];
    if (num < 20) return teens[num - 10];
    if (num < 100) return tens[Math.floor(num / 10)] + (num % 10 ? ' ' + ones[num % 10] : '');

    const thousands = Math.floor(num / 1000);
    const hundreds = Math.floor((num % 1000) / 100);
    const remainder = num % 100;

    let result = '';
    if (thousands > 0) result += numberToWords(thousands) + ' Thousand ';
    if (hundreds > 0) result += ones[hundreds] + ' Hundred ';
    if (remainder > 0) result += numberToWords(remainder);

    return result.trim() + ' Rupees Only';
  };

  return (
    <div className="space-y-4">
      {/* Action Buttons */}
      <div className="flex gap-2 justify-end no-print">
        <button onClick={handlePrint} className="btn-outline flex items-center gap-2">
          <Printer className="w-4 h-4" />
          Print Invoice
        </button>
        <button onClick={handleDownloadPDF} className="btn-primary flex items-center gap-2">
          <Download className="w-4 h-4" />
          Download PDF
        </button>
      </div>

      {/* Invoice Document */}
      <div
        ref={invoiceRef}
        className="bg-white p-8 border border-gray-300 print:border-none"
        style={{ maxWidth: '210mm', margin: '0 auto' }}
      >
        {/* Header */}
        <div className="text-center mb-6 pb-4 border-b-2 border-gray-300">
          <h1 className="text-2xl font-bold text-gray-900">{store.storeName}</h1>
          <p className="text-sm text-gray-600 mt-1">{store.address}</p>
          <p className="text-sm text-gray-600">
            {store.city}, {store.state} - {store.pincode}
          </p>
          <div className="mt-2 text-sm">
            <span className="font-medium">GSTIN:</span> {store.gstin}
          </div>
        </div>

        {/* Invoice Info */}
        <div className="grid grid-cols-2 gap-4 mb-6">
          <div>
            <h2 className="text-lg font-semibold text-gray-900 mb-2">Tax Invoice</h2>
            <div className="text-sm space-y-1">
              <div>
                <span className="font-medium">Invoice No:</span> {order.orderNumber}
              </div>
              <div>
                <span className="font-medium">Date:</span>{' '}
                {new Date(order.createdAt).toLocaleDateString('en-IN', {
                  day: '2-digit',
                  month: 'short',
                  year: 'numeric',
                })}
              </div>
              <div>
                <span className="font-medium">Place of Supply:</span> {store.state}
              </div>
            </div>
          </div>

          <div>
            <h3 className="font-semibold text-gray-900 mb-2">Bill To:</h3>
            <div className="text-sm space-y-1">
              <div className="font-medium">{order.customerName}</div>
              <div>{order.customerPhone}</div>
              {order.patientName && (
                <div className="text-gray-600">Patient: {order.patientName}</div>
              )}
            </div>
          </div>
        </div>

        {/* Items Table */}
        <div className="mb-6">
          <table className="w-full border-collapse border border-gray-300 text-sm">
            <thead>
              <tr className="bg-gray-100">
                <th className="border border-gray-300 px-2 py-2 text-left">#</th>
                <th className="border border-gray-300 px-2 py-2 text-left">Product Description</th>
                <th className="border border-gray-300 px-2 py-2 text-center">HSN</th>
                <th className="border border-gray-300 px-2 py-2 text-center">Qty</th>
                <th className="border border-gray-300 px-2 py-2 text-right">Rate</th>
                <th className="border border-gray-300 px-2 py-2 text-right">Discount</th>
                <th className="border border-gray-300 px-2 py-2 text-right">Taxable Value</th>
                <th className="border border-gray-300 px-2 py-2 text-center">GST%</th>
                {!isInterState ? (
                  <>
                    <th className="border border-gray-300 px-2 py-2 text-right">CGST</th>
                    <th className="border border-gray-300 px-2 py-2 text-right">SGST</th>
                  </>
                ) : (
                  <th className="border border-gray-300 px-2 py-2 text-right">IGST</th>
                )}
                <th className="border border-gray-300 px-2 py-2 text-right">Total</th>
              </tr>
            </thead>
            <tbody>
              {lineItems.map((item, index) => (
                <tr key={index}>
                  <td className="border border-gray-300 px-2 py-2">{index + 1}</td>
                  <td className="border border-gray-300 px-2 py-2">{item.productName}</td>
                  <td className="border border-gray-300 px-2 py-2 text-center">{item.hsnCode}</td>
                  <td className="border border-gray-300 px-2 py-2 text-center">{item.quantity}</td>
                  <td className="border border-gray-300 px-2 py-2 text-right">
                    ₹{item.unitPrice.toFixed(2)}
                  </td>
                  <td className="border border-gray-300 px-2 py-2 text-right">
                    ₹{item.discount.toFixed(2)}
                  </td>
                  <td className="border border-gray-300 px-2 py-2 text-right">
                    ₹{item.taxableValue.toFixed(2)}
                  </td>
                  <td className="border border-gray-300 px-2 py-2 text-center">{item.gstRate}%</td>
                  {!isInterState ? (
                    <>
                      <td className="border border-gray-300 px-2 py-2 text-right">
                        ₹{item.cgst.toFixed(2)}
                      </td>
                      <td className="border border-gray-300 px-2 py-2 text-right">
                        ₹{item.sgst.toFixed(2)}
                      </td>
                    </>
                  ) : (
                    <td className="border border-gray-300 px-2 py-2 text-right">
                      ₹{item.igst.toFixed(2)}
                    </td>
                  )}
                  <td className="border border-gray-300 px-2 py-2 text-right font-medium">
                    ₹{item.totalAmount.toFixed(2)}
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="bg-gray-50 font-medium">
                <td colSpan={6} className="border border-gray-300 px-2 py-2 text-right">
                  Sub Total:
                </td>
                <td className="border border-gray-300 px-2 py-2 text-right">
                  ₹{subtotal.toFixed(2)}
                </td>
                <td className="border border-gray-300 px-2 py-2"></td>
                {!isInterState ? (
                  <>
                    <td className="border border-gray-300 px-2 py-2 text-right">
                      ₹{totalCGST.toFixed(2)}
                    </td>
                    <td className="border border-gray-300 px-2 py-2 text-right">
                      ₹{totalSGST.toFixed(2)}
                    </td>
                  </>
                ) : (
                  <td className="border border-gray-300 px-2 py-2 text-right">
                    ₹{totalIGST.toFixed(2)}
                  </td>
                )}
                <td className="border border-gray-300 px-2 py-2 text-right"></td>
              </tr>
              <tr className="bg-gray-100 font-bold">
                <td colSpan={isInterState ? 9 : 10} className="border border-gray-300 px-2 py-2 text-right">
                  Grand Total:
                </td>
                <td className="border border-gray-300 px-2 py-2 text-right">
                  ₹{grandTotal.toFixed(2)}
                </td>
              </tr>
            </tfoot>
          </table>
        </div>

        {/* Tax Summary */}
        <div className="mb-6 p-3 bg-gray-50 border border-gray-300">
          <h3 className="font-semibold text-sm mb-2">Tax Summary</h3>
          <div className="grid grid-cols-2 gap-4 text-sm">
            <div>
              <div className="flex justify-between">
                <span>Taxable Amount:</span>
                <span className="font-medium">₹{subtotal.toFixed(2)}</span>
              </div>
              {!isInterState ? (
                <>
                  <div className="flex justify-between">
                    <span>CGST:</span>
                    <span className="font-medium">₹{totalCGST.toFixed(2)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span>SGST:</span>
                    <span className="font-medium">₹{totalSGST.toFixed(2)}</span>
                  </div>
                </>
              ) : (
                <div className="flex justify-between">
                  <span>IGST:</span>
                  <span className="font-medium">₹{totalIGST.toFixed(2)}</span>
                </div>
              )}
            </div>
            <div>
              <div className="flex justify-between font-bold">
                <span>Total Tax:</span>
                <span>₹{totalTax.toFixed(2)}</span>
              </div>
              <div className="flex justify-between font-bold text-base mt-2">
                <span>Invoice Total:</span>
                <span>₹{grandTotal.toFixed(2)}</span>
              </div>
            </div>
          </div>
        </div>

        {/* Amount in Words */}
        <div className="mb-6 p-3 bg-gray-50 border border-gray-300">
          <div className="text-sm">
            <span className="font-semibold">Amount in Words:</span>{' '}
            {numberToWords(Math.round(grandTotal))}
          </div>
        </div>

        {/* Payment Details */}
        {order.payments && order.payments.length > 0 && (
          <div className="mb-6">
            <h3 className="font-semibold text-sm mb-2">Payment Details</h3>
            <div className="text-sm space-y-1">
              {order.payments.map((payment, idx) => (
                <div key={idx} className="flex justify-between">
                  <span>{payment.mode}:</span>
                  <span className="font-medium">₹{payment.amount.toFixed(2)}</span>
                </div>
              ))}
              {order.balanceDue > 0 && (
                <div className="flex justify-between text-red-600 font-medium pt-2 border-t">
                  <span>Balance Due:</span>
                  <span>₹{order.balanceDue.toFixed(2)}</span>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Terms & Conditions */}
        <div className="mb-6 text-xs text-gray-600">
          <h3 className="font-semibold text-sm mb-2 text-gray-900">Terms & Conditions:</h3>
          <ul className="list-disc list-inside space-y-1">
            <li>Goods once sold cannot be returned or exchanged</li>
            <li>Subject to {store.city} jurisdiction</li>
            <li>All disputes are subject to {store.city} jurisdiction only</li>
            <li>Warranty as per manufacturer's terms and conditions</li>
          </ul>
        </div>

        {/* Footer */}
        <div className="text-right pt-4 border-t border-gray-300">
          <div className="text-sm font-semibold text-gray-900">For {store.storeName}</div>
          <div className="mt-12 text-sm text-gray-600">Authorized Signatory</div>
        </div>

        {/* Footer Note */}
        <div className="mt-6 text-center text-xs text-gray-500">
          <p>This is a computer-generated invoice and does not require a physical signature</p>
        </div>
      </div>

      {/* Print Styles */}
      <style jsx>{`
        @media print {
          .no-print {
            display: none !important;
          }
          body {
            margin: 0;
            padding: 0;
          }
          @page {
            size: A4;
            margin: 10mm;
          }
        }
      `}</style>
    </div>
  );
}

export default GSTInvoice;

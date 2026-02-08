import { useState } from 'react';
import { Printer, Mail, Share2, Download, FileText, Receipt } from 'lucide-react';

interface ReceiptPreviewProps {
  billData: any;
  selectedCustomer: any;
  cartItems: any[];
  onClose: () => void;
}

type ReceiptFormat = 'thermal' | 'a4';

export function ReceiptPreview({
  billData,
  selectedCustomer,
  cartItems,
  onClose,
}: ReceiptPreviewProps) {
  const [format, setFormat] = useState<ReceiptFormat>('thermal');
  const storeInfo = {
    name: 'Better Vision Opticals',
    address: '123 Main Street, City, 560001',
    phone: '+91 98765 43210',
    gst: '29ABCDE1234F1ZA',
  };

  const handlePrint = () => {
    window.print();
  };

  const handleWhatsAppShare = () => {
    const message = `Bill #${billData.bill_number}\nTotal: ₹${billData.total_amount}\n\nThank you for your purchase!`;
    const whatsappUrl = `https://wa.me/${selectedCustomer?.phone}?text=${encodeURIComponent(message)}`;
    window.open(whatsappUrl, '_blank');
  };

  const handleEmailShare = () => {
    const subject = `Receipt - Bill #${billData.bill_number}`;
    const body = `Dear ${selectedCustomer?.name || 'Customer'},\n\nPlease find your bill details below:\n\nBill #: ${billData.bill_number}\nTotal Amount: ₹${billData.total_amount}\n\nThank you for your business!`;
    const mailtoUrl = `mailto:${selectedCustomer?.email}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
    window.open(mailtoUrl);
  };

  const handleDownloadPDF = () => {
    // Placeholder for PDF download functionality
    alert('PDF download feature coming soon');
  };

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
      <div className="bg-gray-800 rounded-lg max-w-2xl w-full max-h-screen overflow-y-auto">
        {/* Header */}
        <div className="bg-gray-900 border-b border-gray-700 px-6 py-4 sticky top-0">
          <h2 className="text-xl font-bold text-white mb-4">Receipt Preview</h2>

          {/* Format Selector */}
          <div className="flex gap-4">
            <button
              onClick={() => setFormat('thermal')}
              className={`flex items-center gap-2 px-4 py-2 rounded-lg transition-colors ${
                format === 'thermal'
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
              }`}
            >
              <Receipt className="w-4 h-4" />
              Thermal (80mm)
            </button>
            <button
              onClick={() => setFormat('a4')}
              className={`flex items-center gap-2 px-4 py-2 rounded-lg transition-colors ${
                format === 'a4'
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
              }`}
            >
              <FileText className="w-4 h-4" />
              A4 Tax Invoice
            </button>
          </div>
        </div>

        {/* Receipt Content */}
        <div className="p-6">
          {format === 'thermal' ? (
            // Thermal Receipt (80mm width - ~300px in screen)
            <div className="mx-auto bg-white text-black p-4 font-mono text-xs rounded border border-gray-300" style={{ width: '300px' }}>
              {/* Store Header */}
              <div className="text-center border-b border-black pb-2 mb-2">
                <p className="font-bold text-sm">{storeInfo.name}</p>
                <p className="text-xs">{storeInfo.address}</p>
                <p className="text-xs">{storeInfo.phone}</p>
              </div>

              {/* Bill Details */}
              <div className="space-y-1 text-xs mb-2">
                <div className="flex justify-between">
                  <span>Bill #:</span>
                  <span className="font-bold">{billData.bill_number}</span>
                </div>
                <div className="flex justify-between">
                  <span>Date:</span>
                  <span>{new Date().toLocaleDateString('en-IN')}</span>
                </div>
                <div className="flex justify-between">
                  <span>Time:</span>
                  <span>{new Date().toLocaleTimeString('en-IN')}</span>
                </div>
                {selectedCustomer && (
                  <div className="flex justify-between">
                    <span>Customer:</span>
                    <span className="font-semibold">{selectedCustomer.name}</span>
                  </div>
                )}
              </div>

              {/* Items */}
              <div className="border-t border-b border-black py-2 mb-2">
                <div className="flex justify-between font-bold mb-1 border-b border-black pb-1">
                  <span className="flex-1">Item</span>
                  <span className="w-12 text-right">Qty</span>
                  <span className="w-16 text-right">Amt</span>
                </div>
                {cartItems.map((item, idx) => (
                  <div key={idx} className="text-xs space-y-1">
                    <div className="flex justify-between">
                      <span className="flex-1">{item.name}</span>
                      <span className="w-12 text-right">{item.quantity}</span>
                      <span className="w-16 text-right">₹{(item.unit_price * item.quantity).toFixed(0)}</span>
                    </div>
                    {item.discount_percent && (
                      <div className="text-right text-gray-600">
                        -{item.discount_percent}% = -₹{(item.unit_price * item.quantity * item.discount_percent / 100).toFixed(0)}
                      </div>
                    )}
                  </div>
                ))}
              </div>

              {/* Totals */}
              <div className="space-y-1 text-xs mb-2">
                <div className="flex justify-between">
                  <span>Subtotal:</span>
                  <span>₹{billData.subtotal.toFixed(0)}</span>
                </div>
                {(billData.item_discount + billData.order_discount_amount) > 0 && (
                  <div className="flex justify-between text-gray-600">
                    <span>Discount:</span>
                    <span>-₹{(billData.item_discount + billData.order_discount_amount).toFixed(0)}</span>
                  </div>
                )}
                <div className="flex justify-between text-gray-600">
                  <span>{billData.igst_amount > 0 ? 'IGST' : 'CGST+SGST'} (18%):</span>
                  <span>₹{billData.total_gst.toFixed(0)}</span>
                </div>
                {Math.abs(billData.roundoff_amount) > 0.01 && (
                  <div className="flex justify-between text-gray-600">
                    <span>Round-off:</span>
                    <span>₹{billData.roundoff_amount.toFixed(2)}</span>
                  </div>
                )}
                <div className="border-t border-black pt-1 flex justify-between font-bold">
                  <span>TOTAL:</span>
                  <span>₹{billData.total_amount}</span>
                </div>
              </div>

              {/* Footer */}
              <div className="text-center border-t border-black pt-2 text-xs">
                <p>Thank you for shopping!</p>
                <p>GST: {storeInfo.gst}</p>
                <p className="mt-1 text-gray-600">Returns within 7 days with receipt</p>
              </div>
            </div>
          ) : (
            // A4 Tax Invoice
            <div className="bg-white text-black p-8 rounded border-2 border-gray-300 space-y-4">
              {/* Header */}
              <div className="grid grid-cols-2 gap-8 pb-4 border-b-2 border-black">
                <div>
                  <h1 className="text-2xl font-bold">{storeInfo.name}</h1>
                  <p className="text-sm text-gray-600">{storeInfo.address}</p>
                  <p className="text-sm text-gray-600">Phone: {storeInfo.phone}</p>
                  <p className="text-sm text-gray-600">GST: {storeInfo.gst}</p>
                </div>
                <div className="text-right">
                  <h2 className="text-xl font-bold mb-2">TAX INVOICE</h2>
                  <p className="text-sm"><strong>Bill #:</strong> {billData.bill_number}</p>
                  <p className="text-sm"><strong>Date:</strong> {new Date().toLocaleDateString('en-IN')}</p>
                </div>
              </div>

              {/* Bill To */}
              {selectedCustomer && (
                <div className="grid grid-cols-2 gap-8 py-4 border-b border-gray-300">
                  <div>
                    <p className="font-bold mb-2">BILL TO:</p>
                    <p className="text-sm"><strong>{selectedCustomer.name}</strong></p>
                    <p className="text-sm">{selectedCustomer.phone}</p>
                    {selectedCustomer.email && <p className="text-sm">{selectedCustomer.email}</p>}
                  </div>
                  <div className="text-right text-sm">
                    {/* Space for additional info */}
                  </div>
                </div>
              )}

              {/* Items Table */}
              <div className="mb-4">
                <table className="w-full text-sm border-collapse">
                  <thead>
                    <tr className="border-b-2 border-black">
                      <th className="text-left py-2">Description</th>
                      <th className="text-center py-2">Qty</th>
                      <th className="text-right py-2">Unit Price</th>
                      <th className="text-right py-2">Amount</th>
                    </tr>
                  </thead>
                  <tbody>
                    {cartItems.map((item, idx) => (
                      <tr key={idx} className="border-b border-gray-300">
                        <td className="py-2">
                          <p className="font-semibold">{item.name}</p>
                          <p className="text-xs text-gray-600">{item.sku} • {item.brand}</p>
                        </td>
                        <td className="text-center py-2">{item.quantity}</td>
                        <td className="text-right py-2">₹{item.unit_price.toLocaleString('en-IN')}</td>
                        <td className="text-right py-2">₹{(item.unit_price * item.quantity).toLocaleString('en-IN')}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Totals */}
              <div className="flex justify-end mb-4">
                <div className="w-64 space-y-1 text-sm border-t-2 border-black pt-2">
                  <div className="flex justify-between">
                    <span>Subtotal:</span>
                    <span>₹{billData.subtotal.toLocaleString('en-IN')}</span>
                  </div>
                  {(billData.item_discount + billData.order_discount_amount) > 0 && (
                    <div className="flex justify-between text-gray-600">
                      <span>Discounts:</span>
                      <span>-₹{(billData.item_discount + billData.order_discount_amount).toLocaleString('en-IN')}</span>
                    </div>
                  )}
                  <div className="flex justify-between">
                    <span>Taxable Amount:</span>
                    <span>₹{billData.taxable_amount.toLocaleString('en-IN')}</span>
                  </div>
                  {billData.igst_amount > 0 ? (
                    <div className="flex justify-between">
                      <span>IGST (18%):</span>
                      <span>₹{billData.igst_amount.toFixed(2)}</span>
                    </div>
                  ) : (
                    <>
                      <div className="flex justify-between">
                        <span>CGST (9%):</span>
                        <span>₹{billData.cgst_amount.toFixed(2)}</span>
                      </div>
                      <div className="flex justify-between">
                        <span>SGST (9%):</span>
                        <span>₹{billData.sgst_amount.toFixed(2)}</span>
                      </div>
                    </>
                  )}
                  {Math.abs(billData.roundoff_amount) > 0.01 && (
                    <div className="flex justify-between text-gray-600">
                      <span>Round-off:</span>
                      <span>₹{billData.roundoff_amount.toFixed(2)}</span>
                    </div>
                  )}
                  <div className="border-t border-black pt-1 flex justify-between font-bold text-lg">
                    <span>TOTAL:</span>
                    <span>₹{billData.total_amount}</span>
                  </div>
                </div>
              </div>

              {/* Footer */}
              <div className="text-center text-xs text-gray-600 pt-4 border-t border-gray-300">
                <p>Thank you for your purchase!</p>
                <p>Returns accepted within 7 days with original receipt</p>
              </div>
            </div>
          )}
        </div>

        {/* Action Buttons */}
        <div className="bg-gray-900 border-t border-gray-700 px-6 py-4 flex gap-4 flex-wrap">
          <button
            onClick={handlePrint}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg transition-colors"
          >
            <Printer className="w-5 h-5" />
            Print
          </button>
          {selectedCustomer?.phone && (
            <button
              onClick={handleWhatsAppShare}
              className="flex items-center gap-2 px-4 py-2 bg-green-600 hover:bg-green-700 text-white rounded-lg transition-colors"
            >
              <Share2 className="w-5 h-5" />
              WhatsApp
            </button>
          )}
          {selectedCustomer?.email && (
            <button
              onClick={handleEmailShare}
              className="flex items-center gap-2 px-4 py-2 bg-purple-600 hover:bg-purple-700 text-white rounded-lg transition-colors"
            >
              <Mail className="w-5 h-5" />
              Email
            </button>
          )}
          <button
            onClick={handleDownloadPDF}
            className="flex items-center gap-2 px-4 py-2 bg-amber-600 hover:bg-amber-700 text-white rounded-lg transition-colors"
          >
            <Download className="w-5 h-5" />
            Download PDF
          </button>
          <button
            onClick={onClose}
            className="flex-1 px-4 py-2 bg-gray-700 hover:bg-gray-600 text-white rounded-lg transition-colors"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

export default ReceiptPreview;

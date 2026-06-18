// ============================================================================
// IMS 2.0 - POS Receipt Preview (v2-3: statutory polish)
// ============================================================================
// Thermal (80mm) + A4 fallback. Thermal carries a compact statutory header
// (trade name, GSTIN, doc no, copy-marker pseudo strip, amount-in-words,
// retention-line footer) since per Sec. 31 CGST any tax-bearing receipt is a
// statutory document -- just rendered in a 58/80mm friendly layout.

import { useState } from 'react';
import { Printer, Mail, Share2, Download, FileText, Receipt } from 'lucide-react';
import { describeForReceipt } from '../../utils/receiptFormat';
import {
  amountInWords,
  declarations,
  formatDateTimeIST,
  inr,
  statutoryFooter,
  type OverrideFields,
} from '../print/legalPrimitives';

interface ReceiptPreviewProps {
  billData: any;
  selectedCustomer: any;
  cartItems: any[];
  onClose: () => void;
  storeData?: { name?: string; legalName?: string; address?: string; phone?: string; gst?: string; stateCode?: string; logo?: string; storeCode?: string };
  /** Per-entity content overrides surfaced from the editor. */
  overrides?: OverrideFields | null;
  /** Entity trade name override (e.g. "Better Vision") -- thermal can only
   *  fit a short name; defaults to storeData.name. */
  entityTradeName?: string;
}

type ReceiptFormat = 'thermal' | 'a4';

export function ReceiptPreview({
  billData,
  selectedCustomer,
  cartItems,
  onClose,
  storeData,
  overrides,
  entityTradeName,
}: ReceiptPreviewProps) {
  const [format, setFormat] = useState<ReceiptFormat>('thermal');
  // STORE-SPECIFIC: render the ISSUING store's identity. NEVER fall back to a
  // fixed brand name -- that would mislabel a sale made at a different store /
  // entity. When nothing is available we leave the name blank (the caller
  // passes a neutral store code as a last resort).
  const storeInfo = {
    name: storeData?.name || storeData?.storeCode || '',
    legalName: storeData?.legalName || '',
    address: storeData?.address || '',
    phone: storeData?.phone || '',
    gst: storeData?.gst || '',
    logo: storeData?.logo || '',
  };
  const tradeName = entityTradeName || overrides?.header_subtitle || storeInfo.name || '';
  const subtitle = overrides?.header_subtitle || '';
  // A tax-bearing receipt is statutory (Sec. 31): label it "TAX INVOICE" only
  // when a GSTIN is actually present; otherwise it is a plain sales receipt.
  const hasGstin = !!storeInfo.gst;
  const declarationText = overrides?.declaration_text || declarations('thermal_receipt');
  const footerLine = statutoryFooter('thermal_receipt', overrides?.retention_years || 7);
  const grandTotalRupees = typeof billData?.total_amount === 'number'
    ? billData.total_amount
    : Number(String(billData?.total_amount ?? '0').replace(/[^\d.-]/g, '')) || 0;
  const totalWords = amountInWords(grandTotalRupees);
  const docNumber = billData?.bill_number || '';

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
    window.print();
  };

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-lg max-w-2xl w-full max-h-screen overflow-y-auto">
        {/* Header */}
        <div className="no-print bg-white border-b border-gray-200 px-6 py-4 sticky top-0">
          <h2 className="text-xl font-bold text-gray-900 mb-4">Receipt Preview</h2>

          {/* Format Selector */}
          <div className="flex gap-4">
            <button
              onClick={() => setFormat('thermal')}
              className={`flex items-center gap-2 px-4 py-2 rounded-lg transition-colors ${
                format === 'thermal'
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
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
                  : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
              }`}
            >
              <FileText className="w-4 h-4" />
              A4 Tax Invoice
            </button>
          </div>
        </div>

        {/* Receipt Content */}
        <div className={`receipt-print-area p-6 ${format === 'thermal' ? 'receipt-thermal' : 'receipt-a4'}`}>
          {format === 'thermal' ? (
            // Thermal Receipt — compact statutory layout (80mm)
            <div
              className="mx-auto"
              style={{
                width: '300px',
                background: '#fff',
                color: '#1a1a19',
                padding: '12px 12px',
                fontFamily: 'Inter, system-ui, sans-serif',
                fontSize: 10.5,
                lineHeight: 1.35,
                border: '1px solid #aaa9a3',
              }}
            >
              {/* Compact statutory header */}
              <div style={{ textAlign: 'center', borderBottom: '1px solid #1a1a19', paddingBottom: 6 }}>
                {storeInfo.logo && (
                  <img
                    src={storeInfo.logo}
                    alt=""
                    style={{ maxHeight: 36, maxWidth: '70%', objectFit: 'contain', margin: '0 auto 4px', display: 'block' }}
                  />
                )}
                <div style={{ fontSize: 12, fontWeight: 700, letterSpacing: '.01em' }}>{(tradeName || '').toUpperCase()}</div>
                {storeInfo.legalName && storeInfo.legalName !== tradeName && (
                  <div style={{ fontSize: 8.5, color: '#4a4a45' }}>{storeInfo.legalName}</div>
                )}
                {subtitle && <div style={{ fontSize: 9, color: '#4a4a45', letterSpacing: '.08em', textTransform: 'uppercase' }}>{subtitle}</div>}
                <div style={{ fontSize: 9, color: '#4a4a45', marginTop: 2 }}>{storeInfo.address}</div>
                {storeInfo.phone && <div style={{ fontSize: 9, color: '#4a4a45' }}>{storeInfo.phone}</div>}
                {storeInfo.gst ? (
                  <div style={{ fontSize: 9, color: '#4a4a45', marginTop: 2, fontFamily: 'JetBrains Mono, Menlo, monospace' }}>
                    GSTIN: {storeInfo.gst}
                  </div>
                ) : (
                  <div style={{ fontSize: 8.5, color: '#a01c1c', marginTop: 2, fontWeight: 600 }}>
                    GSTIN NOT CONFIGURED
                  </div>
                )}
              </div>

              {/* Doc strip — label TAX INVOICE only when a GSTIN is present (a
                  GSTIN-less receipt is not a statutory tax invoice). */}
              <div
                style={{
                  background: '#1a1a19',
                  color: '#fff',
                  textAlign: 'center',
                  fontSize: 8.5,
                  fontWeight: 600,
                  letterSpacing: '.16em',
                  padding: '3px 0',
                  marginTop: 6,
                }}
              >
                {hasGstin ? 'TAX INVOICE · ORIGINAL FOR RECIPIENT' : 'SALES RECEIPT'}
              </div>

              {/* Bill meta */}
              <div style={{ marginTop: 6, fontSize: 9.5 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ color: '#4a4a45', textTransform: 'uppercase', letterSpacing: '.06em', fontSize: 8.5 }}>Bill No.</span>
                  <span style={{ fontFamily: 'JetBrains Mono, Menlo, monospace', fontWeight: 600 }}>{docNumber}</span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ color: '#4a4a45', textTransform: 'uppercase', letterSpacing: '.06em', fontSize: 8.5 }}>Date · Time</span>
                  <span style={{ fontFamily: 'JetBrains Mono, Menlo, monospace' }}>{formatDateTimeIST(new Date())}</span>
                </div>
                {selectedCustomer && (
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 1 }}>
                    <span style={{ color: '#4a4a45', textTransform: 'uppercase', letterSpacing: '.06em', fontSize: 8.5 }}>Customer</span>
                    <span style={{ fontWeight: 600 }}>{selectedCustomer.name}</span>
                  </div>
                )}
              </div>

              {/* Items */}
              <div style={{ marginTop: 6, borderTop: '1px solid #1a1a19', borderBottom: '1px solid #1a1a19', padding: '4px 0' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 8.5, color: '#4a4a45', textTransform: 'uppercase', letterSpacing: '.06em', fontWeight: 600, borderBottom: '1px solid #aaa9a3', paddingBottom: 2, marginBottom: 2 }}>
                  <span style={{ flex: 1 }}>Item · HSN</span>
                  <span style={{ width: 30, textAlign: 'right' }}>Qty</span>
                  <span style={{ width: 56, textAlign: 'right' }}>Amount</span>
                </div>
                {cartItems.map((item, idx) => (
                  <div key={idx} style={{ fontSize: 9.5, marginBottom: 2 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                      <span style={{ flex: 1 }}>
                        {describeForReceipt(item)}
                        {item.hsn_code ? <span style={{ color: '#7a7a72', fontFamily: 'JetBrains Mono, Menlo, monospace', marginLeft: 4 }}>·{item.hsn_code}</span> : null}
                      </span>
                      <span style={{ width: 30, textAlign: 'right' }}>{item.quantity}</span>
                      <span style={{ width: 56, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                        ₹{(item.unit_price * item.quantity).toFixed(2)}
                      </span>
                    </div>
                    {item.discount_percent && (
                      <div style={{ textAlign: 'right', color: '#4a4a45', fontSize: 9 }}>
                        -{item.discount_percent}% = -₹{(item.unit_price * item.quantity * item.discount_percent / 100).toFixed(2)}
                      </div>
                    )}
                  </div>
                ))}
              </div>

              {/* Totals */}
              <div style={{ marginTop: 4, fontSize: 9.5 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ color: '#4a4a45' }}>Subtotal</span>
                  <span style={{ fontVariantNumeric: 'tabular-nums' }}>₹{billData.subtotal.toFixed(2)}</span>
                </div>
                {(billData.item_discount + billData.order_discount_amount) > 0 && (
                  <div style={{ display: 'flex', justifyContent: 'space-between', color: '#4a4a45' }}>
                    <span>Discount</span>
                    <span style={{ fontVariantNumeric: 'tabular-nums' }}>-₹{(billData.item_discount + billData.order_discount_amount).toFixed(2)}</span>
                  </div>
                )}
                <div style={{ display: 'flex', justifyContent: 'space-between', color: '#4a4a45' }}>
                  <span>{billData.igst_amount > 0 ? 'IGST' : 'CGST+SGST'}</span>
                  <span style={{ fontVariantNumeric: 'tabular-nums' }}>₹{billData.total_gst.toFixed(2)}</span>
                </div>
                {Math.abs(billData.roundoff_amount) > 0.01 && (
                  <div style={{ display: 'flex', justifyContent: 'space-between', color: '#4a4a45' }}>
                    <span>Round-off</span>
                    <span style={{ fontVariantNumeric: 'tabular-nums' }}>₹{billData.roundoff_amount.toFixed(2)}</span>
                  </div>
                )}
                <div style={{ display: 'flex', justifyContent: 'space-between', borderTop: '1px solid #1a1a19', marginTop: 3, paddingTop: 3, fontWeight: 700, fontSize: 11 }}>
                  <span>TOTAL</span>
                  <span style={{ fontVariantNumeric: 'tabular-nums' }}>{inr(grandTotalRupees)}</span>
                </div>
              </div>

              {/* Amount in words */}
              <div style={{ marginTop: 6, fontSize: 9, color: '#4a4a45', borderTop: '1px solid #aaa9a3', paddingTop: 4 }}>
                <span style={{ textTransform: 'uppercase', letterSpacing: '.06em', fontWeight: 600, fontSize: 8.5 }}>In words:</span>{' '}
                <span style={{ color: '#1a1a19' }}>{totalWords}</span>
              </div>

              {/* Declaration */}
              <div style={{ marginTop: 4, fontSize: 8.5, color: '#4a4a45', textAlign: 'center', lineHeight: 1.4 }}>
                {declarationText}
              </div>
              {/* Statutory footer */}
              <div style={{ marginTop: 4, fontSize: 8, color: '#7a7a72', textAlign: 'center', textTransform: 'uppercase', letterSpacing: '.08em' }}>
                {footerLine}
              </div>
              <div style={{ marginTop: 2, fontSize: 8, color: '#aaa9a3', textAlign: 'center' }}>
                Thank you for shopping with us
              </div>
            </div>
          ) : (
            // A4 Tax Invoice (simplified fallback — full statutory render is GSTInvoice.tsx)
            <div className="bg-white text-black p-8 rounded border-2 border-gray-300 space-y-4">
              <div className="grid grid-cols-2 gap-8 pb-4 border-b-2 border-black">
                <div>
                  {storeInfo.logo && (
                    <img src={storeInfo.logo} alt="" className="h-12 mb-2 object-contain" />
                  )}
                  <h1 className="text-2xl font-bold">{storeInfo.legalName || storeInfo.name}</h1>
                  {storeInfo.name && storeInfo.legalName && storeInfo.name !== storeInfo.legalName && (
                    <p className="text-sm text-gray-600">{storeInfo.name}</p>
                  )}
                  <p className="text-sm text-gray-600">{storeInfo.address}</p>
                  {storeInfo.phone && <p className="text-sm text-gray-600">Phone: {storeInfo.phone}</p>}
                  {storeInfo.gst
                    ? <p className="text-sm text-gray-600">GSTIN: {storeInfo.gst}</p>
                    : <p className="text-sm text-red-700 font-semibold">GSTIN NOT CONFIGURED</p>}
                </div>
                <div className="text-right">
                  <h2 className="text-xl font-bold mb-2">{hasGstin ? 'TAX INVOICE' : 'SALES RECEIPT'}</h2>
                  <p className="text-sm"><strong>Bill #:</strong> {billData.bill_number}</p>
                  <p className="text-sm"><strong>Date:</strong> {new Date().toLocaleDateString('en-IN')}</p>
                </div>
              </div>
              {selectedCustomer && (
                <div className="grid grid-cols-2 gap-8 py-4 border-b border-gray-300">
                  <div>
                    <p className="font-bold mb-2">BILL TO:</p>
                    <p className="text-sm"><strong>{selectedCustomer.name}</strong></p>
                    <p className="text-sm">{selectedCustomer.phone}</p>
                    {selectedCustomer.email && <p className="text-sm">{selectedCustomer.email}</p>}
                  </div>
                </div>
              )}
              <div className="mb-4">
                <table className="w-full text-sm border-collapse">
                  <thead>
                    <tr className="border-b-2 border-black">
                      <th className="text-left py-2">Description</th>
                      <th className="text-center py-2">HSN</th>
                      <th className="text-center py-2">Qty</th>
                      <th className="text-right py-2">Unit Price</th>
                      <th className="text-right py-2">Amount</th>
                    </tr>
                  </thead>
                  <tbody>
                    {cartItems.map((item, idx) => (
                      <tr key={idx} className="border-b border-gray-300">
                        <td className="py-2 font-semibold">{describeForReceipt(item)}</td>
                        <td className="text-center py-2 font-mono text-xs">{item.hsn_code || '—'}</td>
                        <td className="text-center py-2">{item.quantity}</td>
                        <td className="text-right py-2 tabular-nums">₹{item.unit_price.toLocaleString('en-IN')}</td>
                        <td className="text-right py-2 tabular-nums">₹{(item.unit_price * item.quantity).toLocaleString('en-IN')}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
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
                    <span>{inr(grandTotalRupees)}</span>
                  </div>
                </div>
              </div>
              {/* Amount in words + declaration + statutory line on A4 fallback */}
              <div className="border-t border-gray-300 pt-3 text-xs">
                <p><strong>In words:</strong> {totalWords}</p>
                <p className="text-gray-600 mt-2">{declarationText}</p>
                <p className="text-gray-500 mt-2 uppercase tracking-wider text-[10px]">{footerLine}</p>
              </div>
            </div>
          )}
        </div>

        {/* Action Buttons */}
        <div className="no-print bg-white border-t border-gray-200 px-6 py-4 flex gap-4 flex-wrap">
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
            className="flex-1 px-4 py-2 bg-gray-100 hover:bg-gray-200 text-gray-900 rounded-lg transition-colors"
          >
            Close
          </button>
        </div>
      </div>

      {/* Print isolation — print ONLY the receipt body, not the modal shell /
          backdrop / action buttons / app chrome. Mirrors the print/* templates
          (POPrint etc.). @page size switches with the chosen format. */}
      <style>{`
        @media print {
          body * { visibility: hidden; }
          .receipt-print-area, .receipt-print-area * { visibility: visible; }
          .receipt-print-area {
            position: absolute; left: 0; top: 0;
            width: 100%; padding: 0 !important; margin: 0;
            max-width: none;
            background: #fff;
          }
          .no-print { display: none !important; }
          .receipt-print-area.receipt-thermal > div { margin: 0 auto; border: none !important; }
          table { page-break-inside: avoid; }
        }
        @media print {
          .receipt-print-area.receipt-thermal { page: thermal; }
          .receipt-print-area.receipt-a4 { page: a4doc; }
        }
        @page thermal { size: 80mm auto; margin: 0; }
        @page a4doc { size: A4; margin: 10mm; }
      `}</style>
    </div>
  );
}

export default ReceiptPreview;

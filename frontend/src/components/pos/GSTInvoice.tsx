// ============================================================================
// IMS 2.0 - GST Tax Invoice Template (v2-3: statutory polish)
// ============================================================================
// GST-compliant tax invoice (Rule 46 CGST). Refactored to the statutory
// aesthetic: bordered, ALL-CAPS, sans-only, copy markers, HSN-wise summary,
// amount-in-words, declaration, signatory block, retention footer.
//
// Real data is sourced from the entity + store + order props. Per-entity
// overrides (signatory_name, declaration_text, footer_terms) are merged
// on top of CGST-compliant defaults at render time (see legalPrimitives.tsx).

import { useRef } from 'react';
import { Printer, Download } from 'lucide-react';
import { calculateGST, calculateIGST, getHSNByCategory } from '../../constants/gst';
import { resolveGstRate, isInclusivePricing } from '../../constants/gstRuntime';
import { describeForReceipt } from '../../utils/receiptFormat';
import {
  buildLegalHeader,
  LegalHeaderView,
  LegalFooterBlock,
  HsnSummaryTable,
  hsnTaxSummary,
  amountInWords,
  declarations,
  inr,
  formatDate,
  tblHead,
  tblCell,
  tblNum,
  type EntityLike,
  type OverrideFields,
  type StoreLike,
} from '../print/legalPrimitives';
import type { Order, Store } from '../../types';

// Generate GST-compliant invoice serial number from order number
// Format: BV/FY25-26/BOK01/0001 (Brand/FinancialYear/Store/Sequence)
function generateInvoiceNumber(orderNumber: string, storeCode: string): string {
  const now = new Date();
  const fy = now.getMonth() >= 3 ? `${now.getFullYear()}-${(now.getFullYear() + 1) % 100}` : `${now.getFullYear() - 1}-${now.getFullYear() % 100}`;
  const seq = orderNumber?.replace(/[^A-Z0-9]/gi, '').slice(-6) || String(Date.now()).slice(-6);
  const brand = storeCode?.includes('WIZ') ? 'WO' : 'BV';
  const store = storeCode?.replace(/^BV-|^WO-/g, '').slice(0, 5) || 'HQ';
  return `${brand}/${fy}/${store}/${seq}`;
}

interface GSTInvoiceProps {
  order: Order;
  store: Store;
  /** Optional entity for full statutory identity. If absent, the store name
   *  alone is shown (legacy callers that don't have the entity in scope). */
  entity?: EntityLike | null;
  /** Per-entity content overrides (declaration_text, signatory_name, etc.). */
  overrides?: OverrideFields | null;
  /** Copy marker preset (ORIGINAL / DUPLICATE / TRIPLICATE). */
  copyMarker?: 'ORIGINAL' | 'DUPLICATE' | 'TRIPLICATE';
  /** Optional override for the printed declaration text. */
  declarationOverride?: string;
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

export function GSTInvoice({
  order,
  store,
  entity,
  overrides,
  copyMarker = 'ORIGINAL',
  declarationOverride,
  onPrint,
}: GSTInvoiceProps) {
  const invoiceRef = useRef<HTMLDivElement>(null);

  // Inter-state (IGST) vs intra-state (CGST + SGST) routing.
  const storeState = store?.state?.toLowerCase?.()?.trim() || '';
  const customerState = (order as any)?.customer_state?.toLowerCase?.()?.trim()
    || (order as any)?.billing_address?.state?.toLowerCase?.()?.trim() || '';
  const isInterState = !!(storeState && customerState && storeState !== customerState);

  // Order-level discount distribution (proportional across line items).
  const safeItems = order.items ?? [];
  const itemDiscountTotal = safeItems.reduce((sum, item) => sum + (item.discountAmount ?? 0), 0);
  const orderLevelDiscount = Math.max(0, (order.totalDiscount ?? 0) - itemDiscountTotal);
  const itemSubtotal = safeItems.reduce((sum, item) => sum + item.finalPrice, 0);
  const discountRatio = itemSubtotal > 0 && orderLevelDiscount > 0
    ? (itemSubtotal - orderLevelDiscount) / itemSubtotal
    : 1;

  // Line items -> invoice rows with GST calculation.
  // GST-INCLUSIVE (owner decision 2026-05-29 / QA F3): the line price the
  // customer is billed IS the all-in amount. We EXTRACT the taxable base +
  // GST from within it (calculateGST/calculateIGST already extract), so the
  // row total equals the price paid — NOT price + GST on top (which made the
  // invoice total Rs 1,046.57 on a Rs 999 sale).
  const lineItems: InvoiceLineItem[] = safeItems.map(item => {
    const grossLine = Math.round(item.finalPrice * discountRatio * 100) / 100;
    const category = (item as any).category || (item as any).itemType || '';
    const hsnInfo = getHSNByCategory(category, true);
    const gstRate = (item as any).gstRate || resolveGstRate(category, (item as any).hsnCode || hsnInfo?.code);
    const hsnCode = (item as any).hsnCode || hsnInfo?.code || '9004';

    // GST_PRICING_MODE (runtime, /health): inclusive (default) extracts GST
    // from WITHIN the line price (row total = price paid); exclusive (legacy)
    // adds GST ON TOP (row total = price + GST). The flag lets prod roll back
    // without a redeploy; the row stays self-consistent either way.
    const inclusive = isInclusivePricing();
    let cgst = 0, sgst = 0, igst = 0;
    let taxableValue: number;
    let totalAmount: number;
    if (inclusive) {
      if (isInterState) {
        const gstCalc = calculateIGST(grossLine, gstRate);
        igst = gstCalc.igst;
        taxableValue = gstCalc.baseAmount;
      } else {
        const gstCalc = calculateGST(grossLine, gstRate);
        cgst = gstCalc.cgst;
        sgst = gstCalc.sgst;
        taxableValue = gstCalc.baseAmount;
      }
      totalAmount = grossLine;
    } else {
      taxableValue = grossLine;
      const tax = Math.round(grossLine * (gstRate / 100) * 100) / 100;
      if (isInterState) {
        igst = tax;
      } else {
        cgst = Math.floor((tax * 100) / 2) / 100;
        sgst = Math.round((tax - cgst) * 100) / 100;
      }
      totalAmount = Math.round((grossLine + tax) * 100) / 100;
    }

    return {
      productName: describeForReceipt({
        brand: (item as any).brand,
        subbrand: (item as any).subbrand,
        category: (item as any).category || (item as any).itemType,
        name: item.productName,
      }),
      hsnCode,
      quantity: item.quantity,
      unitPrice: item.unitPrice,
      discount: item.discountAmount + (item.finalPrice - grossLine),
      taxableValue,
      gstRate,
      cgst,
      sgst,
      igst,
      totalAmount,
    };
  });

  const subtotal = lineItems.reduce((sum, item) => sum + item.taxableValue, 0);
  const totalCGST = lineItems.reduce((sum, item) => sum + item.cgst, 0);
  const totalSGST = lineItems.reduce((sum, item) => sum + item.sgst, 0);
  const totalIGST = lineItems.reduce((sum, item) => sum + item.igst, 0);
  const totalTax = totalCGST + totalSGST + totalIGST;
  const grandTotal = subtotal + totalTax;

  // HSN-wise consolidated summary for Rule 46 + GSTR-1 staging.
  const hsnSummary = hsnTaxSummary(
    lineItems.map(li => ({
      hsn_code: li.hsnCode,
      taxableValue: li.taxableValue,
      taxable_value: li.taxableValue,
      gst_rate: li.gstRate,
      rate: li.gstRate,
      qty: li.quantity,
      description: li.productName,
    })),
    isInterState ? customerState : storeState,
    storeState
  );

  const docNumber = generateInvoiceNumber(order.orderNumber, store.storeCode);

  // Build header from real entity + store (no mock identities).
  // If the caller didn't pass an entity, synthesize a thin one from `store`
  // so existing call sites keep working (legacy contract preserved).
  const effectiveEntity: EntityLike = entity || {
    legal_name: store.storeName,
    name: store.storeName,
    pan: '',
    cin: '',
    registered_address: store.address,
    registered_phone: '',
    registered_email: '',
    website: '',
    gstins: store.gstin ? [{
      gstin: store.gstin,
      state_code: store.stateCode || '',
      state_name: store.state || '',
      is_primary: true,
    }] : [],
  };
  const effectiveStore: StoreLike = {
    name: store.storeName,
    store_code: store.storeCode,
    address: store.address,
    city: store.city,
    state: store.state,
    state_code: store.stateCode,
    pincode: store.pincode,
  };
  const header = buildLegalHeader(effectiveEntity, effectiveStore, 'tax_invoice', {
    docNumber,
    docDate: new Date(order.createdAt),
    placeOfSupply: customerState || store.state,
    reverseCharge: false,
    copyMarker,
    overrides,
    copyMarkerMode: 'rule_48',
  });

  const handlePrint = () => {
    window.print();
    if (onPrint) onPrint();
  };

  const handleDownloadPDF = () => {
    window.print();
  };

  const amountWords = amountInWords(grandTotal);
  const declarationText = declarationOverride
    || overrides?.declaration_text
    || declarations('tax_invoice');

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

      {/* Tax Invoice — statutory aesthetic */}
      <div
        ref={invoiceRef}
        className="bg-white text-black tax-invoice-print"
        style={{ maxWidth: '210mm', margin: '0 auto', fontFamily: 'Inter, system-ui, sans-serif', color: '#1a1a19', border: '1px solid #1a1a19' }}
      >
        {/* Statutory header */}
        <LegalHeaderView header={header} docTypeLabel="TAX INVOICE · RULE 46 CGST" />

        {/* Bill To */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', borderBottom: '1.5px solid #1a1a19' }}>
          <div style={{ padding: '10px 16px', borderRight: '1px solid #7a7a72' }}>
            <div style={{ fontSize: 9, color: '#4a4a45', textTransform: 'uppercase', letterSpacing: '.1em', fontWeight: 500 }}>Bill To</div>
            <div style={{ fontSize: 12.5, fontWeight: 600, marginTop: 4, color: '#1a1a19' }}>{order.customerName || '—'}</div>
            <div style={{ fontSize: 10.5, color: '#4a4a45', marginTop: 2 }}>{order.customerPhone || ''}</div>
            {order.patientName && (
              <div style={{ fontSize: 10, color: '#4a4a45', marginTop: 2 }}>Patient: {order.patientName}</div>
            )}
          </div>
          <div style={{ padding: '10px 16px' }}>
            <div style={{ fontSize: 9, color: '#4a4a45', textTransform: 'uppercase', letterSpacing: '.1em', fontWeight: 500 }}>Ship To / Outlet</div>
            <div style={{ fontSize: 11.5, color: '#1a1a19', marginTop: 4, lineHeight: 1.4 }}>{header.store_name}</div>
            <div style={{ fontSize: 10.5, color: '#4a4a45', marginTop: 2, lineHeight: 1.4 }}>{header.store_address}</div>
          </div>
        </div>

        {/* Line items table */}
        <div style={{ padding: '12px 16px 0' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                <th style={{ ...tblHead, width: '4%' }}>#</th>
                <th style={{ ...tblHead, textAlign: 'left' }}>Description</th>
                <th style={{ ...tblHead, width: '8%' }}>HSN/SAC</th>
                <th style={{ ...tblHead, width: '6%' }}>Qty</th>
                <th style={{ ...tblHead, width: '9%' }}>Rate</th>
                <th style={{ ...tblHead, width: '8%' }}>Discount</th>
                <th style={{ ...tblHead, width: '10%' }}>Taxable</th>
                <th style={{ ...tblHead, width: '5%' }}>GST%</th>
                {!isInterState ? (
                  <>
                    <th style={{ ...tblHead, width: '8%' }}>CGST</th>
                    <th style={{ ...tblHead, width: '8%' }}>SGST</th>
                  </>
                ) : (
                  <th style={{ ...tblHead, width: '10%' }}>IGST</th>
                )}
                <th style={{ ...tblHead, width: '10%' }}>Line Total</th>
              </tr>
            </thead>
            <tbody>
              {lineItems.map((item, index) => (
                <tr key={index}>
                  <td style={tblCell}>{index + 1}</td>
                  <td style={{ ...tblCell, textAlign: 'left' }}>{item.productName}</td>
                  <td style={{ ...tblCell, fontFamily: 'JetBrains Mono, Menlo, monospace' }}>{item.hsnCode}</td>
                  <td style={tblCell}>{item.quantity}</td>
                  <td style={tblNum}>{inr(item.unitPrice, { withPaise: true })}</td>
                  <td style={tblNum}>{inr(item.discount, { withPaise: true })}</td>
                  <td style={tblNum}>{inr(item.taxableValue, { withPaise: true })}</td>
                  <td style={tblCell}>{item.gstRate}%</td>
                  {!isInterState ? (
                    <>
                      <td style={tblNum}>{inr(item.cgst, { withPaise: true })}</td>
                      <td style={tblNum}>{inr(item.sgst, { withPaise: true })}</td>
                    </>
                  ) : (
                    <td style={tblNum}>{inr(item.igst, { withPaise: true })}</td>
                  )}
                  <td style={{ ...tblNum, fontWeight: 600 }}>{inr(item.totalAmount, { withPaise: true })}</td>
                </tr>
              ))}
              {orderLevelDiscount > 0 && (
                <tr>
                  <td colSpan={isInterState ? 9 : 10} style={{ ...tblCell, textAlign: 'right', fontWeight: 500 }}>
                    Order discount
                  </td>
                  <td style={{ ...tblNum }}>-{inr(orderLevelDiscount, { withPaise: true })}</td>
                </tr>
              )}
              <tr>
                <td colSpan={6} style={{ ...tblCell, textAlign: 'right', fontWeight: 700 }}>Taxable Amount</td>
                <td style={{ ...tblNum, fontWeight: 700 }}>{inr(subtotal, { withPaise: true })}</td>
                <td style={tblCell}></td>
                {!isInterState ? (
                  <>
                    <td style={{ ...tblNum, fontWeight: 700 }}>{inr(totalCGST, { withPaise: true })}</td>
                    <td style={{ ...tblNum, fontWeight: 700 }}>{inr(totalSGST, { withPaise: true })}</td>
                  </>
                ) : (
                  <td style={{ ...tblNum, fontWeight: 700 }}>{inr(totalIGST, { withPaise: true })}</td>
                )}
                <td style={tblCell}></td>
              </tr>
              <tr>
                <td colSpan={isInterState ? 9 : 10} style={{ ...tblCell, textAlign: 'right', fontWeight: 700, fontSize: 12.5 }}>
                  Grand Total
                </td>
                <td style={{ ...tblNum, fontWeight: 700, fontSize: 12.5 }}>{inr(grandTotal, { withPaise: true })}</td>
              </tr>
            </tbody>
          </table>
        </div>

        {/* HSN-wise consolidated tax summary (Rule 46 / GSTR-1) */}
        <div style={{ padding: '12px 16px' }}>
          <div style={{ fontSize: 9, color: '#4a4a45', textTransform: 'uppercase', letterSpacing: '.1em', fontWeight: 500, marginBottom: 4 }}>
            HSN-wise tax summary
          </div>
          <HsnSummaryTable summary={hsnSummary} />
        </div>

        {/* Payment Details (kept) */}
        {order.payments && order.payments.length > 0 && (
          <div style={{ padding: '0 16px 12px' }}>
            <div style={{ fontSize: 9, color: '#4a4a45', textTransform: 'uppercase', letterSpacing: '.1em', fontWeight: 500, marginBottom: 4 }}>
              Payment details
            </div>
            <table style={{ width: '100%', fontSize: 10.5, borderCollapse: 'collapse' }}>
              <tbody>
                {order.payments.map((payment, idx) => (
                  <tr key={idx}>
                    <td style={{ padding: '2px 0', color: '#4a4a45' }}>{payment.mode}</td>
                    <td style={{ padding: '2px 0', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{inr(payment.amount, { withPaise: true })}</td>
                  </tr>
                ))}
                {order.balanceDue > 0 && (
                  <tr>
                    <td style={{ padding: '4px 0', borderTop: '1px solid #aaa9a3', color: '#1a1a19', fontWeight: 600 }}>Balance Due</td>
                    <td style={{ padding: '4px 0', borderTop: '1px solid #aaa9a3', textAlign: 'right', fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>
                      {inr(order.balanceDue, { withPaise: true })}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}

        {/* Amount-in-words + Declaration + Signatory + Statutory footer */}
        <LegalFooterBlock
          header={header}
          amountInWordsText={amountWords}
          declarationText={declarationText}
        />
      </div>

      {/* Print Styles */}
      <style>{`
        @media print {
          .no-print { display: none !important; }
          body { margin: 0; padding: 0; }
          @page { size: A4; margin: 10mm; }
          .tax-invoice-print { border: none !important; }
        }
      `}</style>
    </div>
  );
}

export default GSTInvoice;
// Re-export the format helper for callers that need the same date formatting.
export { formatDate as formatInvoiceDate };

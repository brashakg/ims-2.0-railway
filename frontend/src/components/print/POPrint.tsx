// ============================================================================
// IMS 2.0 - Purchase Order Print Component (statutory aesthetic)
// ============================================================================
// A4 purchase order. Migrated to the shared statutory primitives so the issuing
// (buyer) entity legal name + GSTIN + logo print from the PO's OWN store + its
// legal entity -- never a hardcoded "Better Vision Opticals" name.

import { useRef } from 'react';
import { Printer, X } from 'lucide-react';
import {
  buildLegalHeader,
  LegalHeaderView,
  LegalFooterBlock,
  formatDate,
  inr,
  tblHead,
  tblCell,
  tblNum,
  type EntityLike,
  type OverrideFields,
  type StoreLike,
} from './legalPrimitives';

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
  stateCode?: string;
  brand?: string;
  storeCode?: string;
}

interface POPrintProps {
  po: POPrintData;
  store: StoreInfo;
  /** Issuing legal entity (legal_name, pan, cin, gstins[], invoice.logo_url). */
  entity?: EntityLike | null;
  overrides?: OverrideFields | null;
  onClose: () => void;
}

export function POPrint({ po, store, entity, overrides, onClose }: POPrintProps) {
  const printRef = useRef<HTMLDivElement>(null);

  const handlePrint = () => {
    window.print();
  };

  // Fall back to a thin entity synthesized from the store ONLY so legacy callers
  // that don't pass an entity still show THIS store's name + GSTIN (never a
  // fixed brand). Real callers should pass the resolved parent entity.
  const effectiveEntity: EntityLike = entity || {
    legal_name: store.storeName,
    name: store.storeName,
    registered_address: store.address,
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
    brand: store.brand,
    address: store.address,
    city: store.city,
    state: store.state,
    state_code: store.stateCode,
    pincode: store.pincode,
    phone: store.phone,
    gstin: store.gstin,
  };
  const header = buildLegalHeader(effectiveEntity, effectiveStore, 'tax_invoice', {
    docNumber: po.po_number,
    docDate: po.po_date,
    placeOfSupply: store.state,
    copyMarker: 'ORIGINAL',
    overrides,
    copyMarkerMode: 'none',
    extraMeta: [['Expected Delivery', formatDate(po.expected_delivery)]],
  });

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
            <button onClick={onClose} className="p-2 hover:bg-gray-100 rounded-lg">
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Printable Document - statutory aesthetic */}
        <div
          ref={printRef}
          className="po-print-area bg-white text-black"
          style={{
            maxWidth: '210mm',
            margin: '0 auto',
            fontFamily: 'Inter, system-ui, sans-serif',
            color: '#1a1a19',
            border: '1px solid #1a1a19',
          }}
        >
          {/* Statutory header (entity legal name + GSTIN + logo from the PO store) */}
          <LegalHeaderView header={header} docTypeLabel="PURCHASE ORDER" />

          {/* Vendor block */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', borderBottom: '1.5px solid #1a1a19' }}>
            <div style={{ padding: '10px 16px', borderRight: '1px solid #7a7a72' }}>
              <div style={{ fontSize: 9, color: '#4a4a45', textTransform: 'uppercase', letterSpacing: '.1em', fontWeight: 500 }}>To Vendor</div>
              <div style={{ fontSize: 12.5, fontWeight: 600, marginTop: 4 }}>{po.vendor_name}</div>
              <div style={{ fontSize: 10.5, color: '#4a4a45', marginTop: 2 }}>{po.vendor_address}</div>
              {po.vendor_gstin && (
                <div style={{ fontSize: 10.5, color: '#4a4a45', marginTop: 2 }}>
                  GSTIN: <span style={{ fontFamily: 'JetBrains Mono, Menlo, monospace' }}>{po.vendor_gstin}</span>
                </div>
              )}
            </div>
            <div style={{ padding: '10px 16px' }}>
              <div style={{ fontSize: 9, color: '#4a4a45', textTransform: 'uppercase', letterSpacing: '.1em', fontWeight: 500 }}>Deliver To</div>
              <div style={{ fontSize: 12.5, fontWeight: 600, marginTop: 4 }}>{header.store_name}</div>
              <div style={{ fontSize: 10.5, color: '#4a4a45', marginTop: 2, lineHeight: 1.4 }}>{header.store_address}</div>
              <div style={{ fontSize: 10.5, color: '#4a4a45', marginTop: 4 }}>
                <span style={{ color: '#7a7a72', textTransform: 'uppercase', letterSpacing: '.08em', fontSize: 9 }}>Expected:</span>{' '}
                <span style={{ fontFamily: 'JetBrains Mono, Menlo, monospace' }}>{formatDate(po.expected_delivery)}</span>
              </div>
            </div>
          </div>

          {/* Items table */}
          <div style={{ padding: '12px 16px' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th style={{ ...tblHead, width: '6%' }}>#</th>
                  <th style={{ ...tblHead, textAlign: 'left' }}>Product</th>
                  <th style={{ ...tblHead, width: '12%' }}>Qty</th>
                  <th style={{ ...tblHead, width: '18%' }}>Unit Price</th>
                  <th style={{ ...tblHead, width: '18%' }}>Total</th>
                </tr>
              </thead>
              <tbody>
                {po.items.map((item, index) => (
                  <tr key={item.product_id}>
                    <td style={tblCell}>{index + 1}</td>
                    <td style={{ ...tblCell, textAlign: 'left' }}>{item.product_name}</td>
                    <td style={tblCell}>{item.quantity}</td>
                    <td style={tblNum}>{inr(item.unit_price, { withPaise: true })}</td>
                    <td style={{ ...tblNum, fontWeight: 600 }}>{inr(item.total, { withPaise: true })}</td>
                  </tr>
                ))}
                <tr>
                  <td colSpan={4} style={{ ...tblCell, textAlign: 'right', fontWeight: 700 }}>Subtotal</td>
                  <td style={{ ...tblNum, fontWeight: 700 }}>{inr(po.subtotal, { withPaise: true })}</td>
                </tr>
                <tr>
                  <td colSpan={4} style={{ ...tblCell, textAlign: 'right', fontWeight: 700 }}>Tax</td>
                  <td style={{ ...tblNum, fontWeight: 700 }}>{inr(po.tax_amount, { withPaise: true })}</td>
                </tr>
                <tr>
                  <td colSpan={4} style={{ ...tblCell, textAlign: 'right', fontWeight: 700, fontSize: 12.5 }}>Grand Total</td>
                  <td style={{ ...tblNum, fontWeight: 700, fontSize: 12.5 }}>{inr(po.grand_total, { withPaise: true })}</td>
                </tr>
              </tbody>
            </table>
          </div>

          {/* Terms */}
          {po.terms_conditions && (
            <div style={{ padding: '0 16px 12px' }}>
              <div style={{ fontSize: 9, color: '#4a4a45', textTransform: 'uppercase', letterSpacing: '.1em', fontWeight: 500, marginBottom: 4 }}>
                Terms &amp; Conditions
              </div>
              <div style={{ fontSize: 10.5, color: '#4a4a45', whiteSpace: 'pre-wrap' }}>{po.terms_conditions}</div>
            </div>
          )}

          {/* Signatory + statutory footer */}
          <LegalFooterBlock
            header={header}
            showAmountInWords={false}
            signLabel={`For ${header.legal_name || header.trade_name}`}
          />
        </div>
      </div>

      <style>{`
        @media print {
          body * { visibility: hidden; }
          .po-print-area, .po-print-area * { visibility: visible; }
          .po-print-area {
            position: absolute; left: 0; top: 0;
            width: 100%; padding: 0; margin: 0; max-width: none;
            border: none !important;
          }
          .no-print { display: none !important; }
          @page { size: A4; margin: 12mm; }
          table { page-break-inside: avoid; }
        }
      `}</style>
    </div>
  );
}

export default POPrint;

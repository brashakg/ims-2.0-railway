// ============================================================================
// IMS 2.0 - Goods Receipt Note Print (v2-3: statutory polish)
// ============================================================================
// A4 vendor-facing GRN. Not a statutory GST document per se but required
// under industry SOPs and audit standards (retain 7 years per Rule 56).
// Refactored to the statutory aesthetic: bordered tables, ALL-CAPS labels,
// copy markers, statutory rule reference + retention footer, signatory block.

import { useRef } from 'react';
import { Printer, X } from 'lucide-react';
import {
  buildLegalHeader,
  LegalHeaderView,
  LegalFooterBlock,
  declarations,
  formatDate,
  type EntityLike,
  type OverrideFields,
  type StoreLike,
  tblHead,
  tblCell,
  tblNum,
} from './legalPrimitives';

interface GRNItem {
  product_id: string;
  product_name: string;
  hsn_code?: string;
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
  stateCode?: string;
  brand?: string;
  storeCode?: string;
}

interface GRNPrintProps {
  grn: GRNPrintData;
  store: StoreInfo;
  entity?: EntityLike | null;
  overrides?: OverrideFields | null;
  copyMarker?: 'ORIGINAL' | 'DUPLICATE' | 'TRIPLICATE';
  onClose: () => void;
}

function getInspectionStatusColor(status: string): { bg: string; fg: string } {
  switch (status) {
    case 'accepted':
      return { bg: '#e8f5e8', fg: '#1f7a1f' };
    case 'rejected':
      return { bg: '#fde8e8', fg: '#a01c1c' };
    case 'partially_accepted':
      return { bg: '#fff7e0', fg: '#7a5b00' };
    default:
      return { bg: '#f0f0eb', fg: '#4a4a45' };
  }
}

function getVarianceColor(variance: number): string {
  if (variance > 0) return '#a01c1c';
  if (variance < 0) return '#a06d00';
  return '#1f7a1f';
}

export function GRNPrint({
  grn,
  store,
  entity,
  overrides,
  copyMarker = 'ORIGINAL',
  onClose,
}: GRNPrintProps) {
  const printRef = useRef<HTMLDivElement>(null);

  const handlePrint = () => {
    window.print();
  };

  const totalOrdered = grn.items.reduce((sum, item) => sum + item.ordered_qty, 0);
  const totalReceived = grn.items.reduce((sum, item) => sum + item.received_qty, 0);
  const totalVariance = totalReceived - totalOrdered;
  const status = getInspectionStatusColor(grn.quality_inspection);

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
  const header = buildLegalHeader(effectiveEntity, effectiveStore, 'grn', {
    docNumber: grn.grn_number,
    docDate: grn.grn_date,
    placeOfSupply: store.state,
    copyMarker,
    overrides,
    copyMarkerMode: 'rule_48',
    extraMeta: [['Against PO', grn.po_number]],
  });

  const declarationText = overrides?.declaration_text || declarations('grn');

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
            <button onClick={onClose} className="p-2 hover:bg-gray-100 rounded-lg">
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Printable Document - statutory aesthetic */}
        <div
          ref={printRef}
          className="grn-print-area bg-white text-black"
          style={{
            maxWidth: '210mm',
            margin: '0 auto',
            fontFamily: 'Inter, system-ui, sans-serif',
            color: '#1a1a19',
            border: '1px solid #1a1a19',
          }}
        >
          {/* Statutory header */}
          <LegalHeaderView header={header} docTypeLabel="GOODS RECEIPT NOTE" />

          {/* Vendor block */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', borderBottom: '1.5px solid #1a1a19' }}>
            <div style={{ padding: '10px 16px', borderRight: '1px solid #7a7a72' }}>
              <div style={{ fontSize: 9, color: '#4a4a45', textTransform: 'uppercase', letterSpacing: '.1em', fontWeight: 500 }}>From Vendor</div>
              <div style={{ fontSize: 12.5, fontWeight: 600, marginTop: 4 }}>{grn.vendor_name}</div>
              <div style={{ fontSize: 10.5, color: '#4a4a45', marginTop: 2 }}>{grn.vendor_address}</div>
              {grn.vendor_gstin && (
                <div style={{ fontSize: 10.5, color: '#4a4a45', marginTop: 2 }}>
                  GSTIN: <span style={{ fontFamily: 'JetBrains Mono, Menlo, monospace' }}>{grn.vendor_gstin}</span>
                </div>
              )}
            </div>
            <div style={{ padding: '10px 16px' }}>
              <div style={{ fontSize: 9, color: '#4a4a45', textTransform: 'uppercase', letterSpacing: '.1em', fontWeight: 500 }}>Received At</div>
              <div style={{ fontSize: 12.5, fontWeight: 600, marginTop: 4 }}>{header.store_name}</div>
              <div style={{ fontSize: 10.5, color: '#4a4a45', marginTop: 2, lineHeight: 1.4 }}>{header.store_address}</div>
              <div style={{ fontSize: 10.5, color: '#4a4a45', marginTop: 4 }}>
                <span style={{ color: '#7a7a72', textTransform: 'uppercase', letterSpacing: '.08em', fontSize: 9 }}>Against PO:</span>{' '}
                <span style={{ fontFamily: 'JetBrains Mono, Menlo, monospace', fontWeight: 600 }}>{grn.po_number}</span>
              </div>
              <div style={{ fontSize: 10.5, color: '#4a4a45', marginTop: 2 }}>
                <span style={{ color: '#7a7a72', textTransform: 'uppercase', letterSpacing: '.08em', fontSize: 9 }}>Date:</span>{' '}
                <span style={{ fontFamily: 'JetBrains Mono, Menlo, monospace' }}>{formatDate(grn.grn_date)}</span>
              </div>
            </div>
          </div>

          {/* Items table */}
          <div style={{ padding: '12px 16px' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th style={{ ...tblHead, width: '5%' }}>#</th>
                  <th style={{ ...tblHead, textAlign: 'left' }}>Product</th>
                  <th style={{ ...tblHead, width: '10%' }}>HSN/SAC</th>
                  <th style={{ ...tblHead, width: '10%' }}>Ordered</th>
                  <th style={{ ...tblHead, width: '10%' }}>Received</th>
                  <th style={{ ...tblHead, width: '10%' }}>Variance</th>
                  <th style={{ ...tblHead, width: '20%', textAlign: 'left' }}>Remarks</th>
                </tr>
              </thead>
              <tbody>
                {grn.items.map((item, index) => (
                  <tr key={item.product_id}>
                    <td style={tblCell}>{index + 1}</td>
                    <td style={{ ...tblCell, textAlign: 'left' }}>{item.product_name}</td>
                    <td style={{ ...tblCell, fontFamily: 'JetBrains Mono, Menlo, monospace' }}>{item.hsn_code || '—'}</td>
                    <td style={tblNum}>{item.ordered_qty}</td>
                    <td style={tblNum}>{item.received_qty}</td>
                    <td style={{ ...tblNum, color: getVarianceColor(item.variance), fontWeight: 600 }}>
                      {item.variance > 0 ? '+' : ''}{item.variance}
                    </td>
                    <td style={{ ...tblCell, textAlign: 'left' }}>{item.remarks || '—'}</td>
                  </tr>
                ))}
                <tr>
                  <td colSpan={3} style={{ ...tblCell, textAlign: 'right', fontWeight: 700 }}>TOTAL</td>
                  <td style={{ ...tblNum, fontWeight: 700 }}>{totalOrdered}</td>
                  <td style={{ ...tblNum, fontWeight: 700 }}>{totalReceived}</td>
                  <td style={{ ...tblNum, color: getVarianceColor(totalVariance), fontWeight: 700 }}>
                    {totalVariance > 0 ? '+' : ''}{totalVariance}
                  </td>
                  <td style={tblCell}></td>
                </tr>
              </tbody>
            </table>
          </div>

          {/* Quality Inspection */}
          <div style={{ padding: '0 16px 12px' }}>
            <div style={{ fontSize: 9, color: '#4a4a45', textTransform: 'uppercase', letterSpacing: '.1em', fontWeight: 500, marginBottom: 4 }}>
              Quality Inspection
            </div>
            <div style={{ padding: '8px 12px', background: status.bg, color: status.fg, border: `1px solid ${status.fg}`, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '.08em', fontSize: 11 }}>
              {grn.quality_inspection.replace(/_/g, ' ')}
            </div>
            {grn.inspection_remarks && (
              <div style={{ fontSize: 10.5, color: '#4a4a45', marginTop: 4 }}>{grn.inspection_remarks}</div>
            )}
          </div>

          {/* Declaration + signatory + statutory footer */}
          <LegalFooterBlock
            header={header}
            declarationText={declarationText}
            showAmountInWords={false}
            signLabel={`Received for ${header.legal_name || header.trade_name}`}
          />
        </div>
      </div>

      <style>{`
        @media print {
          body * { visibility: hidden; }
          .grn-print-area, .grn-print-area * { visibility: visible; }
          .grn-print-area {
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

export default GRNPrint;

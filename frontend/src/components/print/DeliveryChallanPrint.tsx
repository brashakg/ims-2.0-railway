// ============================================================================
// IMS 2.0 - Delivery Challan Print Component
// ============================================================================
// A4 format challan for inter-store transfers and deliveries

import { useRef } from 'react';
import { Printer, X } from 'lucide-react';
import {
  buildLegalHeader,
  LegalHeaderView,
  LegalFooterBlock,
  type EntityLike,
  type OverrideFields,
  type StoreLike,
} from './legalPrimitives';

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
  gstin?: string;
  stateCode?: string;
  brand?: string;
  storeCode?: string;
}

interface DeliveryChallanPrintProps {
  challan: ChallanPrintData;
  store: StoreInfo;
  /** Issuing (consignor) legal entity -- supplies legal name + GSTIN + logo. */
  entity?: EntityLike | null;
  overrides?: OverrideFields | null;
  copyMarker?: 'ORIGINAL' | 'DUPLICATE' | 'TRIPLICATE';
  onClose: () => void;
}

export function DeliveryChallanPrint({
  challan,
  store,
  entity,
  overrides,
  copyMarker = 'ORIGINAL',
  onClose,
}: DeliveryChallanPrintProps) {
  const printRef = useRef<HTMLDivElement>(null);

  const handlePrint = () => {
    window.print();
  };

  // Statutory consignor identity. Goods moved under Rule 55 must carry the
  // consignor GSTIN -- the header below renders it from the issuing entity.
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
  const header = buildLegalHeader(effectiveEntity, effectiveStore, 'delivery_challan', {
    docNumber: challan.challanNumber,
    docDate: challan.date,
    placeOfSupply: store.state,
    copyMarker,
    overrides,
    copyMarkerMode: 'rule_55',
    extraMeta: [['Reason', 'Inter-store / outward movement']],
  });
  const declarationText = overrides?.declaration_text
    || 'This challan accompanies goods being moved and is NOT a tax invoice (CGST Rule 55).';

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
          className="challan-print-area bg-white"
          style={{ maxWidth: '210mm', margin: '0 auto', fontFamily: 'Inter, system-ui, sans-serif', color: '#1a1a19', border: '1px solid #1a1a19' }}
        >
          {/* Statutory consignor header (legal name + GSTIN + Rule-55 markers) */}
          <LegalHeaderView header={header} docTypeLabel="DELIVERY CHALLAN · RULE 55 CGST" />

          <div className="p-8 pt-4">
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
                <tr className="bg-white text-gray-900">
                  <th className="border border-gray-300 px-3 py-2 text-left font-semibold">Sr.</th>
                  <th className="border border-gray-300 px-3 py-2 text-left font-semibold">
                    Product Name
                  </th>
                  <th className="border border-gray-300 px-3 py-2 text-center font-semibold">
                    Qty
                  </th>
                  <th className="border border-gray-300 px-3 py-2 text-left font-semibold">
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
          </div>

          {/* Declaration + signatory + Rule-55 statutory footer */}
          <LegalFooterBlock
            header={header}
            declarationText={declarationText}
            showAmountInWords={false}
            signLabel={`For ${header.legal_name || header.trade_name} (Consignor)`}
          />
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

// ============================================================================
// IMS 2.0 - Workshop Job Card Print (v2-3: statutory polish)
// ============================================================================
// A5 staff-facing job card. NOT a customer document -- uses the minimal
// internal `StaffHeader` (no GSTIN / CIN / supplier identity block) per the
// council decision to keep statutory ID off internal docs.

import { useEffect, useRef } from 'react';
import JsBarcode from 'jsbarcode';
import { Printer, X } from 'lucide-react';
import {
  buildStaffHeader,
  StaffHeaderView,
  declarations,
  formatDate,
  statutoryFooter,
  type EntityLike,
  type OverrideFields,
  type StoreLike,
} from './legalPrimitives';

interface JobCardPrintData {
  jobNumber: string;
  orderNumber: string;
  customerName: string;
  customerPhone: string;
  frameBrand: string;
  frameModel: string;
  frameColor: string;
  lensType: string;
  lensPower?: string;
  lensCoating?: string;
  lensTint?: string;
  priority: string;
  dueDate: string;
  assignedTechnician?: string;
  status: string;
  createdDate: string;
}

interface StoreInfo {
  storeName: string;
  address: string;
  city: string;
  state: string;
  pincode: string;
  stateCode?: string;
}

interface WorkshopJobCardPrintProps {
  job: JobCardPrintData;
  store: StoreInfo;
  entity?: EntityLike | null;
  overrides?: OverrideFields | null;
  onClose: () => void;
}

const getPriorityColor = (priority: string): string => {
  switch (priority.toUpperCase()) {
    case 'URGENT':
      return '#a01c1c';
    case 'HIGH':
      return '#a06d00';
    case 'NORMAL':
      return '#1a4a7a';
    case 'LOW':
      return '#4a4a45';
    default:
      return '#1a4a7a';
  }
};

export function WorkshopJobCardPrint({
  job,
  store,
  entity,
  overrides,
  onClose,
}: WorkshopJobCardPrintProps) {
  const printRef = useRef<HTMLDivElement>(null);
  // F2 -- disposable job card carries a scannable Code128 barcode of the job
  // number; a USB/Bluetooth wedge scanner at each lab bench reads it to route
  // the job (POST /workshop/scan). Rendered as inline SVG so it prints cleanly.
  const barcodeRef = useRef<SVGSVGElement>(null);

  useEffect(() => {
    if (barcodeRef.current && job.jobNumber) {
      try {
        JsBarcode(barcodeRef.current, job.jobNumber, {
          format: 'CODE128',
          width: 1.6,
          height: 42,
          displayValue: false,
          margin: 0,
        });
      } catch {
        // Fail-soft: if encoding fails the card still prints, just without a
        // barcode (the job number text below remains scannable by hand entry).
      }
    }
  }, [job.jobNumber]);

  const handlePrint = () => {
    window.print();
  };

  const effectiveEntity: EntityLike = entity || { name: store.storeName };
  const effectiveStore: StoreLike = {
    name: store.storeName,
    address: store.address,
    city: store.city,
    state: store.state,
    state_code: store.stateCode,
    pincode: store.pincode,
  };
  const header = buildStaffHeader(effectiveEntity, effectiveStore, 'job_card', {
    docNumber: job.jobNumber,
    docDate: job.createdDate,
    overrides,
    extraMeta: [
      ['Order', job.orderNumber],
      ['Priority', job.priority.toUpperCase()],
      ['Due', formatDate(job.dueDate)],
    ],
  });

  const declarationText = overrides?.declaration_text || declarations('job_card');
  const footerLine = statutoryFooter('job_card');

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg max-h-[95vh] overflow-y-auto">
        {/* Action Bar - hidden during print */}
        <div className="flex items-center justify-between p-4 border-b border-gray-200 no-print">
          <h2 className="text-lg font-bold text-gray-900">Print Job Card</h2>
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

        {/* Printable Document - A5 staff aesthetic */}
        <div
          ref={printRef}
          className="job-card-print-area bg-white text-black"
          style={{
            maxWidth: '148mm',
            margin: '0 auto',
            fontFamily: 'Inter, system-ui, sans-serif',
            color: '#1a1a19',
            border: '1px solid #1a1a19',
          }}
        >
          {/* Staff header */}
          <StaffHeaderView header={header} docTypeLabel="WORKSHOP JOB CARD" />

          {/* Customer + frame + lens block */}
          <div style={{ padding: '10px 14px' }}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <div>
                <div style={{ fontSize: 9, color: '#4a4a45', textTransform: 'uppercase', letterSpacing: '.1em', fontWeight: 500 }}>Customer</div>
                <div style={{ fontSize: 11.5, fontWeight: 600, marginTop: 2 }}>{job.customerName}</div>
                <div style={{ fontSize: 10, color: '#4a4a45', marginTop: 1 }}>{job.customerPhone}</div>
              </div>
              <div>
                <div style={{ fontSize: 9, color: '#4a4a45', textTransform: 'uppercase', letterSpacing: '.1em', fontWeight: 500 }}>Priority</div>
                <div style={{ fontSize: 11, fontWeight: 700, color: getPriorityColor(job.priority), marginTop: 2, textTransform: 'uppercase' }}>
                  {job.priority.toUpperCase()}
                </div>
                <div style={{ fontSize: 10, color: '#4a4a45', marginTop: 1 }}>Status: {job.status}</div>
              </div>
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', borderTop: '1px solid #aaa9a3' }}>
            <div style={{ padding: '10px 14px', borderRight: '1px solid #aaa9a3' }}>
              <div style={{ fontSize: 9, color: '#4a4a45', textTransform: 'uppercase', letterSpacing: '.1em', fontWeight: 500 }}>Frame</div>
              <div style={{ fontSize: 11, marginTop: 2 }}>
                <span style={{ fontWeight: 600 }}>{job.frameBrand}</span> {job.frameModel}
              </div>
              <div style={{ fontSize: 10, color: '#4a4a45', marginTop: 1 }}>Colour: {job.frameColor}</div>
            </div>
            <div style={{ padding: '10px 14px' }}>
              <div style={{ fontSize: 9, color: '#4a4a45', textTransform: 'uppercase', letterSpacing: '.1em', fontWeight: 500 }}>Lens</div>
              <div style={{ fontSize: 11, marginTop: 2 }}>{job.lensType}</div>
              {job.lensPower && <div style={{ fontSize: 10, color: '#4a4a45', marginTop: 1 }}>Power: {job.lensPower}</div>}
              {job.lensCoating && <div style={{ fontSize: 10, color: '#4a4a45', marginTop: 1 }}>Coating: {job.lensCoating}</div>}
              {job.lensTint && <div style={{ fontSize: 10, color: '#4a4a45', marginTop: 1 }}>Tint: {job.lensTint}</div>}
            </div>
          </div>

          {job.assignedTechnician && (
            <div style={{ padding: '8px 14px', borderTop: '1px solid #aaa9a3', fontSize: 10.5 }}>
              <span style={{ color: '#4a4a45', textTransform: 'uppercase', letterSpacing: '.08em', fontSize: 9 }}>Assigned To:</span>{' '}
              <span style={{ color: '#1a1a19', fontWeight: 600 }}>{job.assignedTechnician}</span>
            </div>
          )}

          {/* QC Checklist (bordered, ALL-CAPS labels) */}
          <div style={{ padding: '10px 14px', borderTop: '1px solid #aaa9a3' }}>
            <div style={{ fontSize: 9, color: '#4a4a45', textTransform: 'uppercase', letterSpacing: '.1em', fontWeight: 500, marginBottom: 6 }}>
              QC Checklist
            </div>
            {[
              'Power Verification',
              'Frame Fitting',
              'Cosmetic Check',
              'Alignment Check',
              'Lens Centration',
            ].map((label) => (
              <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 10.5, padding: '3px 0' }}>
                <span
                  style={{
                    display: 'inline-block',
                    width: 12,
                    height: 12,
                    border: '1px solid #4a4a45',
                  }}
                ></span>
                <span style={{ color: '#1a1a19' }}>{label}</span>
              </div>
            ))}
          </div>

          {/* Signatures */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', borderTop: '1px solid #1a1a19', padding: '10px 14px', gap: 24 }}>
            <div>
              <div style={{ height: 36, borderBottom: '0.5px solid #4a4a45' }}></div>
              <div style={{ fontSize: 9, color: '#4a4a45', textTransform: 'uppercase', letterSpacing: '.08em', marginTop: 2 }}>
                Technician Sign
              </div>
            </div>
            <div>
              <div style={{ height: 36, borderBottom: '0.5px solid #4a4a45' }}></div>
              <div style={{ fontSize: 9, color: '#4a4a45', textTransform: 'uppercase', letterSpacing: '.08em', marginTop: 2 }}>
                QC Sign
              </div>
            </div>
          </div>

          {declarationText && (
            <div style={{ padding: '8px 14px', fontSize: 9.5, color: '#4a4a45', borderTop: '1px solid #aaa9a3' }}>
              {declarationText}
            </div>
          )}
          {header.footer_terms && (
            <div style={{ padding: '8px 14px', fontSize: 9.5, color: '#4a4a45', borderTop: '1px solid #aaa9a3' }}>
              {header.footer_terms}
            </div>
          )}

          {/* F2 -- scannable Code128 job-card barcode (full width). The lab
              benches scan THIS to route the job through the stations. */}
          <div
            style={{
              padding: '8px 14px 4px',
              borderTop: '1px solid #aaa9a3',
              textAlign: 'center',
            }}
          >
            <svg
              ref={barcodeRef}
              data-testid="job-card-barcode"
              data-value={job.jobNumber}
              style={{ width: '100%', height: 42 }}
            />
            <div style={{ fontSize: 9, fontFamily: 'ui-monospace, Menlo, monospace', color: '#1a1a19', marginTop: 2 }}>
              {job.jobNumber}
            </div>
          </div>

          <div
            style={{
              padding: '7px 14px',
              fontSize: 9,
              color: '#7a7a72',
              textTransform: 'uppercase',
              letterSpacing: '.08em',
              textAlign: 'center',
            }}
          >
            {footerLine} · {job.jobNumber}
          </div>
        </div>
      </div>

      <style>{`
        @media print {
          body * { visibility: hidden; }
          .job-card-print-area, .job-card-print-area * { visibility: visible; }
          .job-card-print-area {
            position: absolute; left: 0; top: 0;
            width: 100%; padding: 0; margin: 0; max-width: none;
            border: none !important;
          }
          .no-print { display: none !important; }
          @page { size: A5; margin: 6mm; }
          table { page-break-inside: avoid; }
        }
      `}</style>
    </div>
  );
}

export default WorkshopJobCardPrint;

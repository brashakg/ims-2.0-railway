// ============================================================================
// IMS 2.0 - Prescription Print Component
// ============================================================================
// Professional optical prescription (Rx) print template for Indian optical retail
// Follows Indian optical prescription standards with A5 print size

import { useRef } from 'react';
import { Printer, X } from 'lucide-react';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface EyeData {
  sphere: number | null;
  cylinder: number | null;
  axis: number | null;
  add: number | null;
  pd?: number | null;
  va?: string | null;
}

export interface PrescriptionPrintData {
  id: string;
  patientName: string;
  patientAge?: number | string | null;
  customerPhone: string;
  prescribedAt: string;
  rightEye: EyeData;
  leftEye: EyeData;
  pd?: number | null;
  nearVisionRight?: EyeData | null;
  nearVisionLeft?: EyeData | null;
  lensRecommendation?: string | null;
  notes?: string | null;
  optometristName?: string | null;
  nextVisitMonths?: number | null;
  validityMonths?: number | null;
}

export interface StoreInfo {
  storeName: string;
  address: string;
  city: string;
  state: string;
  pincode: string;
  phone?: string;
  gstin?: string;
}

interface PrescriptionPrintProps {
  prescription: PrescriptionPrintData;
  store: StoreInfo;
  onClose: () => void;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatPower(value: number | null | undefined): string {
  if (value === null || value === undefined) return '-';
  return value >= 0 ? `+${value.toFixed(2)}` : value.toFixed(2);
}

function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleDateString('en-IN', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  });
}

function generateRxNumber(id: string, dateStr: string): string {
  const d = new Date(dateStr);
  const yy = d.getFullYear().toString().slice(-2);
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const short = id.slice(-6).toUpperCase();
  return `RX-${yy}${mm}-${short}`;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function PrescriptionPrint({ prescription, store, onClose }: PrescriptionPrintProps) {
  const printRef = useRef<HTMLDivElement>(null);

  const rxNumber = generateRxNumber(prescription.id, prescription.prescribedAt);

  // Determine if near-vision section should show
  const hasNearVision =
    prescription.nearVisionRight || prescription.nearVisionLeft ||
    prescription.rightEye.add !== null || prescription.leftEye.add !== null;

  // Determine the PD to display (per-eye if available, else global)
  const rightPD = prescription.rightEye.pd ?? prescription.pd ?? null;
  const leftPD = prescription.leftEye.pd ?? prescription.pd ?? null;

  // Determine VA
  const rightVA = prescription.rightEye.va ?? null;
  const leftVA = prescription.leftEye.va ?? null;

  const handlePrint = () => {
    window.print();
  };

  // Calculate next visit date if applicable
  const nextVisitDate = (() => {
    const months = prescription.nextVisitMonths ?? prescription.validityMonths;
    if (!months) return null;
    const d = new Date(prescription.prescribedAt);
    d.setMonth(d.getMonth() + months);
    return formatDate(d.toISOString());
  })();

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl max-w-[600px] w-full max-h-[95vh] overflow-y-auto">
        {/* Action Bar - hidden during print */}
        <div className="flex items-center justify-between p-4 border-b border-gray-200 no-print">
          <h2 className="text-lg font-bold text-gray-900">Print Prescription</h2>
          <div className="flex items-center gap-2">
            <button
              onClick={handlePrint}
              className="btn-primary flex items-center gap-2"
            >
              <Printer className="w-4 h-4" />
              Print
            </button>
            <button
              onClick={onClose}
              className="p-2 hover:bg-gray-100 rounded-lg"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Printable Prescription Document */}
        <div
          ref={printRef}
          className="rx-print-area bg-white p-6"
          style={{ maxWidth: '148mm', margin: '0 auto' }}
        >
          {/* Store Header */}
          <div className="text-center mb-4 pb-3 border-b-2 border-gray-800">
            <h1 className="text-xl font-bold text-gray-900 uppercase tracking-wide">
              {store.storeName}
            </h1>
            <p className="text-xs text-gray-600 mt-1">{store.address}</p>
            <p className="text-xs text-gray-600">
              {store.city}, {store.state} - {store.pincode}
            </p>
            {store.phone && (
              <p className="text-xs text-gray-600">Phone: {store.phone}</p>
            )}
            {store.gstin && (
              <p className="text-xs text-gray-500 mt-1">GSTIN: {store.gstin}</p>
            )}
          </div>

          {/* Title */}
          <div className="text-center mb-4">
            <h2 className="text-lg font-bold text-gray-900 tracking-widest uppercase">
              Optical Prescription
            </h2>
            <div className="text-2xl font-serif font-bold text-gray-700 mt-1">
              Rx
            </div>
          </div>

          {/* Patient Info & Rx Details */}
          <div className="grid grid-cols-2 gap-x-4 gap-y-1 mb-4 text-sm">
            <div>
              <span className="text-gray-500">Patient Name:</span>{' '}
              <span className="font-medium">{prescription.patientName}</span>
            </div>
            <div className="text-right">
              <span className="text-gray-500">Rx No:</span>{' '}
              <span className="font-medium">{rxNumber}</span>
            </div>
            <div>
              <span className="text-gray-500">Phone:</span>{' '}
              <span className="font-medium">{prescription.customerPhone}</span>
            </div>
            <div className="text-right">
              <span className="text-gray-500">Date:</span>{' '}
              <span className="font-medium">{formatDate(prescription.prescribedAt)}</span>
            </div>
            {prescription.patientAge && (
              <div>
                <span className="text-gray-500">Age:</span>{' '}
                <span className="font-medium">{prescription.patientAge} yrs</span>
              </div>
            )}
          </div>

          {/* Distance Vision Prescription Table */}
          <div className="mb-4">
            <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
              Distance Vision (DV)
            </h3>
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr className="bg-gray-100">
                  <th className="border border-gray-300 px-2 py-1.5 text-left font-semibold w-[70px]">
                    Eye
                  </th>
                  <th className="border border-gray-300 px-2 py-1.5 text-center font-semibold">
                    SPH
                  </th>
                  <th className="border border-gray-300 px-2 py-1.5 text-center font-semibold">
                    CYL
                  </th>
                  <th className="border border-gray-300 px-2 py-1.5 text-center font-semibold">
                    AXIS
                  </th>
                  <th className="border border-gray-300 px-2 py-1.5 text-center font-semibold">
                    ADD
                  </th>
                  <th className="border border-gray-300 px-2 py-1.5 text-center font-semibold">
                    PD
                  </th>
                  <th className="border border-gray-300 px-2 py-1.5 text-center font-semibold">
                    V/A
                  </th>
                </tr>
              </thead>
              <tbody>
                {/* Right Eye */}
                <tr>
                  <td className="border border-gray-300 px-2 py-1.5 font-semibold text-gray-700">
                    OD (R)
                  </td>
                  <td className="border border-gray-300 px-2 py-1.5 text-center font-mono">
                    {formatPower(prescription.rightEye.sphere)}
                  </td>
                  <td className="border border-gray-300 px-2 py-1.5 text-center font-mono">
                    {formatPower(prescription.rightEye.cylinder)}
                  </td>
                  <td className="border border-gray-300 px-2 py-1.5 text-center font-mono">
                    {prescription.rightEye.axis ?? '-'}
                    {prescription.rightEye.axis !== null && prescription.rightEye.axis !== undefined
                      ? '\u00B0'
                      : ''}
                  </td>
                  <td className="border border-gray-300 px-2 py-1.5 text-center font-mono">
                    {formatPower(prescription.rightEye.add)}
                  </td>
                  <td className="border border-gray-300 px-2 py-1.5 text-center font-mono">
                    {rightPD ?? '-'}
                  </td>
                  <td className="border border-gray-300 px-2 py-1.5 text-center">
                    {rightVA ?? '-'}
                  </td>
                </tr>
                {/* Left Eye */}
                <tr>
                  <td className="border border-gray-300 px-2 py-1.5 font-semibold text-gray-700">
                    OS (L)
                  </td>
                  <td className="border border-gray-300 px-2 py-1.5 text-center font-mono">
                    {formatPower(prescription.leftEye.sphere)}
                  </td>
                  <td className="border border-gray-300 px-2 py-1.5 text-center font-mono">
                    {formatPower(prescription.leftEye.cylinder)}
                  </td>
                  <td className="border border-gray-300 px-2 py-1.5 text-center font-mono">
                    {prescription.leftEye.axis ?? '-'}
                    {prescription.leftEye.axis !== null && prescription.leftEye.axis !== undefined
                      ? '\u00B0'
                      : ''}
                  </td>
                  <td className="border border-gray-300 px-2 py-1.5 text-center font-mono">
                    {formatPower(prescription.leftEye.add)}
                  </td>
                  <td className="border border-gray-300 px-2 py-1.5 text-center font-mono">
                    {leftPD ?? '-'}
                  </td>
                  <td className="border border-gray-300 px-2 py-1.5 text-center">
                    {leftVA ?? '-'}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          {/* Near Vision Table (conditional) */}
          {hasNearVision && (
            <div className="mb-4">
              <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
                Near Vision (NV)
              </h3>
              <table className="w-full border-collapse text-sm">
                <thead>
                  <tr className="bg-gray-100">
                    <th className="border border-gray-300 px-2 py-1.5 text-left font-semibold w-[70px]">
                      Eye
                    </th>
                    <th className="border border-gray-300 px-2 py-1.5 text-center font-semibold">
                      SPH
                    </th>
                    <th className="border border-gray-300 px-2 py-1.5 text-center font-semibold">
                      CYL
                    </th>
                    <th className="border border-gray-300 px-2 py-1.5 text-center font-semibold">
                      AXIS
                    </th>
                    <th className="border border-gray-300 px-2 py-1.5 text-center font-semibold">
                      ADD
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td className="border border-gray-300 px-2 py-1.5 font-semibold text-gray-700">
                      OD (R)
                    </td>
                    <td className="border border-gray-300 px-2 py-1.5 text-center font-mono">
                      {formatPower(prescription.nearVisionRight?.sphere ?? prescription.rightEye.sphere)}
                    </td>
                    <td className="border border-gray-300 px-2 py-1.5 text-center font-mono">
                      {formatPower(prescription.nearVisionRight?.cylinder ?? prescription.rightEye.cylinder)}
                    </td>
                    <td className="border border-gray-300 px-2 py-1.5 text-center font-mono">
                      {(prescription.nearVisionRight?.axis ?? prescription.rightEye.axis) ?? '-'}
                    </td>
                    <td className="border border-gray-300 px-2 py-1.5 text-center font-mono">
                      {formatPower(prescription.nearVisionRight?.add ?? prescription.rightEye.add)}
                    </td>
                  </tr>
                  <tr>
                    <td className="border border-gray-300 px-2 py-1.5 font-semibold text-gray-700">
                      OS (L)
                    </td>
                    <td className="border border-gray-300 px-2 py-1.5 text-center font-mono">
                      {formatPower(prescription.nearVisionLeft?.sphere ?? prescription.leftEye.sphere)}
                    </td>
                    <td className="border border-gray-300 px-2 py-1.5 text-center font-mono">
                      {formatPower(prescription.nearVisionLeft?.cylinder ?? prescription.leftEye.cylinder)}
                    </td>
                    <td className="border border-gray-300 px-2 py-1.5 text-center font-mono">
                      {(prescription.nearVisionLeft?.axis ?? prescription.leftEye.axis) ?? '-'}
                    </td>
                    <td className="border border-gray-300 px-2 py-1.5 text-center font-mono">
                      {formatPower(prescription.nearVisionLeft?.add ?? prescription.leftEye.add)}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          )}

          {/* Lens Recommendation */}
          {prescription.lensRecommendation && (
            <div className="mb-3 text-sm">
              <span className="font-semibold text-gray-700">Lens Type: </span>
              <span className="text-gray-900">{prescription.lensRecommendation}</span>
            </div>
          )}

          {/* Notes / Remarks */}
          {prescription.notes && (
            <div className="mb-3 p-2 bg-gray-50 border border-gray-200 rounded text-sm">
              <span className="font-semibold text-gray-700">Remarks: </span>
              <span className="text-gray-800">{prescription.notes}</span>
            </div>
          )}

          {/* Next Visit */}
          {nextVisitDate && (
            <div className="mb-3 text-sm">
              <span className="font-semibold text-gray-700">Next Visit: </span>
              <span className="text-gray-900">{nextVisitDate}</span>
            </div>
          )}

          {/* Optometrist / Doctor Signature */}
          <div className="mt-8 pt-4 border-t border-gray-300 flex justify-between items-end">
            <div className="text-xs text-gray-500">
              <p>This is a computer-generated prescription.</p>
              <p>Valid for {prescription.validityMonths ?? 6} months from date of issue.</p>
            </div>
            <div className="text-right">
              {prescription.optometristName && (
                <p className="text-sm font-medium text-gray-900 mb-1">
                  {prescription.optometristName}
                </p>
              )}
              <div className="w-40 border-b border-gray-400 mb-1" />
              <p className="text-xs text-gray-600">Optometrist / Doctor</p>
            </div>
          </div>

          {/* Footer */}
          <div className="mt-4 pt-2 border-t border-gray-200 text-center">
            <p className="text-[10px] text-gray-400">
              {store.storeName} &middot; {store.city}, {store.state} &middot; {rxNumber}
            </p>
          </div>
        </div>
      </div>

      {/* Print-specific CSS for A5 optical prescription */}
      <style>{`
        @media print {
          /* Hide everything except the prescription */
          body * {
            visibility: hidden;
          }
          .rx-print-area,
          .rx-print-area * {
            visibility: visible;
          }
          .rx-print-area {
            position: absolute;
            left: 0;
            top: 0;
            width: 100%;
            padding: 8mm;
            margin: 0;
            max-width: none;
          }

          /* Hide non-print elements */
          .no-print {
            display: none !important;
          }

          /* A5 page size - standard for optical Rx in India */
          @page {
            size: A5;
            margin: 8mm;
          }

          /* Ensure clean print styling */
          table {
            page-break-inside: avoid;
          }
          .rx-print-area {
            font-size: 11pt;
            color: #000;
            background: #fff;
          }
          .rx-print-area h1 {
            font-size: 16pt;
          }
          .rx-print-area h2 {
            font-size: 14pt;
          }
          .rx-print-area h3 {
            font-size: 9pt;
          }
        }
      `}</style>
    </div>
  );
}

export default PrescriptionPrint;

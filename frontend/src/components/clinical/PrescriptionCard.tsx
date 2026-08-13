// ============================================================================
// IMS 2.0 - Prescription Card (A5 Print)
// ============================================================================

import { useRef } from 'react';
import { Printer } from 'lucide-react';
import { formatPowerOrDash } from '../../utils/rxPowerValue';

// PATIENT SAFETY: every power here is NULLABLE, and that is the point.
// These fields used to be plain `number`, which forced the one caller
// (ClinicalPage's "Print Rx Card" button) to spell `readEyePower(...) || 0` --
// so an eye test that recorded NO sphere printed "0.00" on the card handed to
// the patient, a positive claim that no correction is needed. A power that was
// never recorded must print as a dash. A power recorded AS 0 must print 0.00.
//
// EXPORTED so the caller's state can be typed with it. While ClinicalPage held
// the card data as `useState<any>`, this whole interface was documentation
// rather than a rule: making the powers nullable changed nothing the compiler
// checked, and a caller could quietly go back to passing `readEyePower(...) || 0`.
export interface PrescriptionData {
  id: string;
  patientName: string;
  patientAge?: number;
  date: string;
  optometristName: string;
  rightEye: {
    sphere: number | null;
    cylinder: number | null;
    axis: number | null;
    add: number | null;
  };
  leftEye: {
    sphere: number | null;
    cylinder: number | null;
    axis: number | null;
    add: number | null;
  };
  pd: number | null;
  visualAcuity: string;
  notes: string;
  // Issuing store identity. storeName must be the RESOLVED store (never a fixed
  // brand). legalName/address/gstin/logoUrl come from the store's legal entity.
  storeName: string;
  storePhone: string;
  storeLegalName?: string;
  storeAddress?: string;
  storeGstin?: string;
  storeLogoUrl?: string;
  /** Optional: the clinical queue's print button has no expiry date to give. */
  validUntil?: string;
}

interface PrescriptionCardProps {
  prescription: PrescriptionData;
}

/**
 * A power for the card: "+0.00" / "-1.25" when recorded, "-" when not. Shared
 * with POS so the counter and the patient's card cannot drift apart -- a
 * recorded 0 renders "+0.00" because a plano IS a prescription.
 */
const formatPower = formatPowerOrDash;

/**
 * An AXIS or a PD that was never recorded prints as a dash, not as 0.
 *
 * Deliberately NOT formatPower: these are not dioptric powers. An axis is a
 * meridian notated 1-180 and a PD of 0mm is anatomically impossible, so neither
 * carries a meaningful zero and neither wants a "+" or two decimal places.
 */
const formatPlain = (value: number | null | undefined): string =>
  value === null || value === undefined || !Number.isFinite(value) ? '-' : String(value);

/** A PD with its unit -- but a dash carries no "mm" ("-mm" is not a reading). */
const formatPd = (value: number | null | undefined): string => {
  const text = formatPlain(value);
  return text === '-' ? '-' : `${text}mm`;
};

/**
 * A date for the card, or "-" when there isn't one.
 *
 * The absence check is NOT cosmetic. `Intl.DateTimeFormat.format()` THROWS a
 * RangeError on an invalid Date, so the previous unguarded version took the
 * whole card down rather than rendering a blank -- and the clinical queue's
 * "Print Rx Card" button has never supplied `validUntil` at all. Typing the
 * caller's state with these props (it was `useState<any>`) is what surfaced it.
 */
const formatDate = (dateString: string | null | undefined) => {
  if (!dateString) return '-';
  const date = new Date(dateString);
  if (Number.isNaN(date.getTime())) return '-';
  return new Intl.DateTimeFormat('en-IN', {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
  }).format(date);
};

/** Expired only when a real expiry date is on file and it has passed. An Rx
 *  with NO recorded validity is not "expired" -- it is unknown, and stamping a
 *  patient's card EXPIRED on missing data is the same class of mistake as
 *  printing 0.00 for a power nobody measured. */
const isExpired = (dateString: string | null | undefined) => {
  if (!dateString) return false;
  const date = new Date(dateString);
  if (Number.isNaN(date.getTime())) return false;
  return date < new Date();
};

export function PrescriptionCard({ prescription }: PrescriptionCardProps) {
  const printRef = useRef<HTMLDivElement>(null);

  const handlePrint = () => {
    const printWindow = window.open('', '_blank');
    if (printWindow && printRef.current) {
      printWindow.document.write(printRef.current.innerHTML);
      printWindow.document.close();
      printWindow.print();
    }
  };

  const expired = isExpired(prescription.validUntil);

  return (
    <div className="space-y-4">
      <button
        onClick={handlePrint}
        className="btn primary flex items-center gap-2"
      >
        <Printer className="w-4 h-4" />
        Print A5 Card
      </button>

      {/* Printable Content (Hidden) */}
      <div
        ref={printRef}
        className="hidden"
        style={{
          width: '148mm',
          height: '210mm',
          padding: '12mm',
          fontFamily: 'Arial, sans-serif',
          fontSize: '12px',
          backgroundColor: '#fff',
          color: '#000',
        }}
      >
        {/* A5 Landscape: 210mm x 148mm */}
        <div style={{ textAlign: 'center', marginBottom: '8mm' }}>
          {prescription.storeLogoUrl && (
            <img src={prescription.storeLogoUrl} alt="logo" style={{ maxHeight: '48px', maxWidth: '160px', objectFit: 'contain', marginBottom: '4px' }} />
          )}
          <div style={{ fontSize: '14px', fontWeight: 'bold' }}>{prescription.storeLegalName || prescription.storeName}</div>
          {prescription.storeName && prescription.storeLegalName && prescription.storeName !== prescription.storeLegalName && (
            <div style={{ fontSize: '11px' }}>{prescription.storeName}</div>
          )}
          {prescription.storeAddress && <div style={{ fontSize: '10px' }}>{prescription.storeAddress}</div>}
          <div style={{ fontSize: '11px' }}>{prescription.storePhone}</div>
          {prescription.storeGstin && <div style={{ fontSize: '10px' }}>GSTIN: {prescription.storeGstin}</div>}
        </div>

        <div style={{ borderBottom: '1px solid #000', paddingBottom: '4mm', marginBottom: '4mm' }}>
          <div style={{ textAlign: 'center', fontSize: '13px', fontWeight: 'bold' }}>PRESCRIPTION</div>
        </div>

        {/* Patient Info */}
        <div style={{ marginBottom: '6mm', display: 'flex', gap: '20mm' }}>
          <div>
            <div style={{ fontSize: '10px', color: '#666' }}>Patient Name:</div>
            <div style={{ fontWeight: 'bold' }}>{prescription.patientName}</div>
          </div>
          <div>
            <div style={{ fontSize: '10px', color: '#666' }}>Age:</div>
            <div style={{ fontWeight: 'bold' }}>{prescription.patientAge || '-'}</div>
          </div>
          <div>
            <div style={{ fontSize: '10px', color: '#666' }}>Date:</div>
            <div style={{ fontWeight: 'bold' }}>{formatDate(prescription.date)}</div>
          </div>
        </div>

        {/* Optometrist */}
        <div style={{ marginBottom: '6mm' }}>
          <div style={{ fontSize: '10px', color: '#666' }}>Optometrist:</div>
          <div>{prescription.optometristName}</div>
        </div>

        {/* Prescription Table */}
        <table style={{ width: '100%', marginBottom: '6mm', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid #000' }}>
              <th style={{ padding: '4px', textAlign: 'left', fontSize: '10px', fontWeight: 'bold' }}>Eye</th>
              <th style={{ padding: '4px', textAlign: 'center', fontSize: '10px', fontWeight: 'bold' }}>SPH</th>
              <th style={{ padding: '4px', textAlign: 'center', fontSize: '10px', fontWeight: 'bold' }}>CYL</th>
              <th style={{ padding: '4px', textAlign: 'center', fontSize: '10px', fontWeight: 'bold' }}>AXIS</th>
              <th style={{ padding: '4px', textAlign: 'center', fontSize: '10px', fontWeight: 'bold' }}>ADD</th>
            </tr>
          </thead>
          <tbody>
            <tr style={{ borderBottom: '1px solid #ccc' }}>
              <td style={{ padding: '4px', fontWeight: 'bold' }}>Right (OD)</td>
              <td style={{ padding: '4px', textAlign: 'center' }}>{formatPower(prescription.rightEye.sphere)}</td>
              <td style={{ padding: '4px', textAlign: 'center' }}>{formatPower(prescription.rightEye.cylinder)}</td>
              <td style={{ padding: '4px', textAlign: 'center' }}>{formatPlain(prescription.rightEye.axis)}</td>
              <td style={{ padding: '4px', textAlign: 'center' }}>{formatPower(prescription.rightEye.add)}</td>
            </tr>
            <tr>
              <td style={{ padding: '4px', fontWeight: 'bold' }}>Left (OS)</td>
              <td style={{ padding: '4px', textAlign: 'center' }}>{formatPower(prescription.leftEye.sphere)}</td>
              <td style={{ padding: '4px', textAlign: 'center' }}>{formatPower(prescription.leftEye.cylinder)}</td>
              <td style={{ padding: '4px', textAlign: 'center' }}>{formatPlain(prescription.leftEye.axis)}</td>
              <td style={{ padding: '4px', textAlign: 'center' }}>{formatPower(prescription.leftEye.add)}</td>
            </tr>
          </tbody>
        </table>

        {/* Additional Info */}
        <div style={{ marginBottom: '4mm', display: 'flex', gap: '15mm' }}>
          <div>
            <div style={{ fontSize: '10px', color: '#666' }}>PD:</div>
            <div>{formatPd(prescription.pd)}</div>
          </div>
          <div>
            <div style={{ fontSize: '10px', color: '#666' }}>Visual Acuity:</div>
            <div>{prescription.visualAcuity}</div>
          </div>
          <div>
            <div style={{ fontSize: '10px', color: '#666' }}>Valid Until:</div>
            <div>{formatDate(prescription.validUntil)}</div>
          </div>
        </div>

        {/* Notes */}
        {prescription.notes && (
          <div style={{ marginBottom: '4mm' }}>
            <div style={{ fontSize: '10px', color: '#666' }}>Notes:</div>
            <div style={{ fontSize: '11px' }}>{prescription.notes}</div>
          </div>
        )}

        {/* Footer */}
        <div style={{ marginTop: '8mm', textAlign: 'center', fontSize: '9px', color: '#999' }}>
          Prescription ID: {prescription.id}
        </div>
      </div>

      {/* Preview in UI */}
      <div className={`p-6 rounded-lg border-2 ${expired ? 'bg-red-50 border-red-300' : 'bg-white border-gray-200'}`}>
        {expired && (
          <div className="mb-4 p-3 bg-red-100 border border-red-300 rounded text-red-700 text-sm">
            ⚠️ This prescription has expired on {formatDate(prescription.validUntil)}
          </div>
        )}

        <div className="grid grid-cols-2 gap-6">
          <div>
            <h3 className="text-gray-900 font-semibold mb-2">{prescription.patientName}</h3>
            <p className="text-gray-500 text-sm">Age: {prescription.patientAge || 'N/A'}</p>
            <p className="text-gray-500 text-sm">Optometrist: {prescription.optometristName}</p>
          </div>
          <div>
            <p className="text-gray-500 text-sm">Date: {formatDate(prescription.date)}</p>
            <p className="text-gray-500 text-sm">Valid Until: {formatDate(prescription.validUntil)}</p>
            <p className="text-gray-500 text-sm">PD: {formatPd(prescription.pd)}</p>
          </div>
        </div>

        <table className="w-full mt-4 text-sm">
          <thead>
            <tr className="border-b border-gray-200">
              <th className="text-left text-gray-600 py-2">Eye</th>
              <th className="text-center text-gray-600 py-2">SPH</th>
              <th className="text-center text-gray-600 py-2">CYL</th>
              <th className="text-center text-gray-600 py-2">AXIS</th>
              <th className="text-center text-gray-600 py-2">ADD</th>
            </tr>
          </thead>
          <tbody>
            <tr className="border-b border-gray-200">
              <td className="text-gray-900 py-2">Right (OD)</td>
              <td className="text-center text-gray-600">{formatPower(prescription.rightEye.sphere)}</td>
              <td className="text-center text-gray-600">{formatPower(prescription.rightEye.cylinder)}</td>
              <td className="text-center text-gray-600">{formatPlain(prescription.rightEye.axis)}</td>
              <td className="text-center text-gray-600">{formatPower(prescription.rightEye.add)}</td>
            </tr>
            <tr>
              <td className="text-gray-900 py-2">Left (OS)</td>
              <td className="text-center text-gray-600">{formatPower(prescription.leftEye.sphere)}</td>
              <td className="text-center text-gray-600">{formatPower(prescription.leftEye.cylinder)}</td>
              <td className="text-center text-gray-600">{formatPlain(prescription.leftEye.axis)}</td>
              <td className="text-center text-gray-600">{formatPower(prescription.leftEye.add)}</td>
            </tr>
          </tbody>
        </table>

        {prescription.notes && (
          <p className="mt-4 text-gray-500 text-sm">Notes: {prescription.notes}</p>
        )}
      </div>
    </div>
  );
}

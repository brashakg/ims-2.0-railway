// ============================================================================
// IMS 2.0 - Prescription (Rx) Card Print (v2-3: statutory polish)
// ============================================================================
// A5 customer-facing Rx card. Driven by NCAHP Act 2021 and the State Medical
// Council registration (e.g. Delhi Medical Council). Refactored to the
// statutory aesthetic: bordered tables, ALL-CAPS labels, NCAHP UID + DMC
// registration in the supplier-identity block (mandatory since 2024).

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
} from '../print/legalPrimitives';

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
  stateCode?: string;
  brand?: string;
  storeCode?: string;
}

interface PrescriptionPrintProps {
  prescription: PrescriptionPrintData;
  store: StoreInfo;
  entity?: EntityLike | null;
  overrides?: OverrideFields | null;
  onClose: () => void;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// Junk tokens that must never reach a patient's Rx card. These are the LITERAL
// strings you get from stringifying an empty value in Python ("None" -- exactly
// what the pre-#969 writer stored for a blank per-eye PD), in JavaScript
// ("null" / "undefined"), or from a NaN. The prop TYPES below say
// `number | null`, but types do not survive the wire: legacy rows, CSV imports,
// integrations and device feeds all deliver these as strings, so this
// patient-facing component must defend at RUNTIME.
const ABSENT_RX_TOKENS = new Set(['', 'none', 'null', 'undefined', 'nan']);

/**
 * True when an Rx cell carries nothing the patient should be shown.
 *
 * CLINICALLY LOAD-BEARING: a genuine 0 / "0" is a REAL prescription value -- a
 * cylinder of 0, an axis of 0 and a prism of 0 each mean something specific --
 * so this is an explicit emptiness test and NEVER a truthiness test. `!value`
 * here would silently erase real clinical data from a patient's card, which is
 * worse than printing junk.
 */
function isAbsentRxValue(value: unknown): boolean {
  if (value === null || value === undefined) return true;
  if (typeof value === 'number') return Number.isNaN(value);
  if (typeof value === 'string') return ABSENT_RX_TOKENS.has(value.trim().toLowerCase());
  return false;
}

/**
 * First value that is actually PRESENT, else null.
 *
 * This is what makes the binocular-PD fallback work: a per-eye PD stored as the
 * string "None" is non-null, so plain `??` keeps it AND suppresses the fallback
 * -- the card then reads "PD: None" while the correct binocular number sits
 * unused one field away. `firstPresent` falls through junk to the real value.
 */
function firstPresent<T>(...values: T[]): T | null {
  for (const value of values) {
    if (!isAbsentRxValue(value)) return value;
  }
  return null;
}

function formatPower(value: number | string | null | undefined): string {
  if (isAbsentRxValue(value)) return '-';
  // Stored powers are a mix of numbers and numeric strings. Coerce, and bail to
  // '-' rather than crashing on `.toFixed` when the value is not numeric at all
  // (a raw "None" reaching the old code threw and blanked the whole card).
  const n = typeof value === 'number' ? value : Number(String(value).trim());
  if (!Number.isFinite(n)) return '-';
  // 0 lands here deliberately and renders "+0.00" -- it is a real power.
  return n >= 0 ? `+${n.toFixed(2)}` : n.toFixed(2);
}

/** Axis with its degree sign, '-' when absent. An axis of 0 renders "0deg". */
function formatAxis(value: number | string | null | undefined): string {
  if (isAbsentRxValue(value)) return '-';
  return `${value}°`;
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

export function PrescriptionPrint({
  prescription,
  store,
  entity,
  overrides,
  onClose,
}: PrescriptionPrintProps) {
  const printRef = useRef<HTMLDivElement>(null);

  const rxNumber = generateRxNumber(prescription.id, prescription.prescribedAt);

  // `add !== null` was true for BOTH undefined and the junk string "None", so a
  // patient with no near Rx could be handed a bogus Near Vision table.
  const hasNearVision =
    !!prescription.nearVisionRight || !!prescription.nearVisionLeft ||
    !isAbsentRxValue(prescription.rightEye.add) ||
    !isAbsentRxValue(prescription.leftEye.add);
  // firstPresent, not `??`: a per-eye PD of "None" must fall THROUGH to the
  // binocular PD instead of masking it.
  const rightPD = firstPresent(prescription.rightEye.pd, prescription.pd);
  const leftPD = firstPresent(prescription.leftEye.pd, prescription.pd);
  const rightVA = firstPresent(prescription.rightEye.va);
  const leftVA = firstPresent(prescription.leftEye.va);

  const handlePrint = () => {
    window.print();
  };

  const nextVisitDate = (() => {
    const months = prescription.nextVisitMonths ?? prescription.validityMonths;
    if (!months) return null;
    const d = new Date(prescription.prescribedAt);
    d.setMonth(d.getMonth() + months);
    return formatDate(d);
  })();

  // Build statutory header from real data; NCAHP UID + DMC reg come from
  // per-entity overrides at render time.
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
  const header = buildLegalHeader(effectiveEntity, effectiveStore, 'rx_card', {
    docNumber: rxNumber,
    docDate: prescription.prescribedAt,
    placeOfSupply: store.state,
    overrides,
    copyMarkerMode: 'none',
  });

  const declarationText = overrides?.declaration_text || declarations('rx_card');

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl max-w-[600px] w-full max-h-[95vh] overflow-y-auto">
        {/* Action Bar - hidden during print */}
        <div className="flex items-center justify-between p-4 border-b border-gray-200 no-print">
          <h2 className="text-lg font-bold text-gray-900">Print Prescription</h2>
          <div className="flex items-center gap-2">
            <button onClick={handlePrint} className="btn-primary flex items-center gap-2">
              <Printer className="w-4 h-4" />
              Print
            </button>
            <button onClick={onClose} className="p-2 hover:bg-gray-100 rounded-lg">
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Printable Document - A5 statutory aesthetic */}
        <div
          ref={printRef}
          className="rx-print-area bg-white text-black"
          style={{
            maxWidth: '148mm',
            margin: '0 auto',
            fontFamily: 'Inter, system-ui, sans-serif',
            color: '#1a1a19',
            border: '1px solid #1a1a19',
          }}
        >
          {/* Statutory header (LegalHeader with NCAHP UID + DMC reg) */}
          <LegalHeaderView header={header} docTypeLabel="OPTICAL PRESCRIPTION (Rx)" />

          {/* Patient block */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', borderBottom: '1.5px solid #1a1a19' }}>
            <div style={{ padding: '8px 14px', borderRight: '1px solid #7a7a72' }}>
              <div style={{ fontSize: 9, color: '#4a4a45', textTransform: 'uppercase', letterSpacing: '.1em', fontWeight: 500 }}>Patient</div>
              <div style={{ fontSize: 12, fontWeight: 600, marginTop: 3 }}>{prescription.patientName}</div>
              <div style={{ fontSize: 10, color: '#4a4a45', marginTop: 1 }}>{prescription.customerPhone}</div>
              {prescription.patientAge && (
                <div style={{ fontSize: 10, color: '#4a4a45', marginTop: 1 }}>Age: {prescription.patientAge} yrs</div>
              )}
            </div>
            <div style={{ padding: '8px 14px' }}>
              <div style={{ fontSize: 9, color: '#4a4a45', textTransform: 'uppercase', letterSpacing: '.1em', fontWeight: 500 }}>Rx Details</div>
              <div style={{ fontSize: 10.5, marginTop: 3 }}>
                <span style={{ color: '#4a4a45' }}>Rx No.:</span>{' '}
                <span style={{ fontFamily: 'JetBrains Mono, Menlo, monospace', fontWeight: 600 }}>{rxNumber}</span>
              </div>
              <div style={{ fontSize: 10.5, marginTop: 2 }}>
                <span style={{ color: '#4a4a45' }}>Examined:</span>{' '}
                <span style={{ fontFamily: 'JetBrains Mono, Menlo, monospace' }}>{formatDate(prescription.prescribedAt)}</span>
              </div>
              {nextVisitDate && (
                <div style={{ fontSize: 10.5, marginTop: 2 }}>
                  <span style={{ color: '#4a4a45' }}>Next visit:</span>{' '}
                  <span style={{ fontFamily: 'JetBrains Mono, Menlo, monospace' }}>{nextVisitDate}</span>
                </div>
              )}
            </div>
          </div>

          {/* Distance Vision Table — bordered statutory */}
          <div style={{ padding: '10px 14px' }}>
            <div style={{ fontSize: 9, color: '#4a4a45', textTransform: 'uppercase', letterSpacing: '.1em', fontWeight: 500, marginBottom: 4 }}>
              Distance Vision (DV)
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th style={{ ...tblHead, width: '12%' }}>Eye</th>
                  <th style={tblHead}>SPH</th>
                  <th style={tblHead}>CYL</th>
                  <th style={tblHead}>AXIS</th>
                  <th style={tblHead}>ADD</th>
                  <th style={tblHead}>PD</th>
                  <th style={tblHead}>V/A</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td style={{ ...tblCell, fontWeight: 700, textAlign: 'left', paddingLeft: 12 }}>OD (R)</td>
                  <td style={{ ...tblCell, fontFamily: 'JetBrains Mono, Menlo, monospace' }}>{formatPower(prescription.rightEye.sphere)}</td>
                  <td style={{ ...tblCell, fontFamily: 'JetBrains Mono, Menlo, monospace' }}>{formatPower(prescription.rightEye.cylinder)}</td>
                  <td style={{ ...tblCell, fontFamily: 'JetBrains Mono, Menlo, monospace' }}>
                    {formatAxis(prescription.rightEye.axis)}
                  </td>
                  <td style={{ ...tblCell, fontFamily: 'JetBrains Mono, Menlo, monospace' }}>{formatPower(prescription.rightEye.add)}</td>
                  <td style={{ ...tblCell, fontFamily: 'JetBrains Mono, Menlo, monospace' }}>{rightPD ?? '-'}</td>
                  <td style={tblCell}>{rightVA ?? '-'}</td>
                </tr>
                <tr>
                  <td style={{ ...tblCell, fontWeight: 700, textAlign: 'left', paddingLeft: 12 }}>OS (L)</td>
                  <td style={{ ...tblCell, fontFamily: 'JetBrains Mono, Menlo, monospace' }}>{formatPower(prescription.leftEye.sphere)}</td>
                  <td style={{ ...tblCell, fontFamily: 'JetBrains Mono, Menlo, monospace' }}>{formatPower(prescription.leftEye.cylinder)}</td>
                  <td style={{ ...tblCell, fontFamily: 'JetBrains Mono, Menlo, monospace' }}>
                    {formatAxis(prescription.leftEye.axis)}
                  </td>
                  <td style={{ ...tblCell, fontFamily: 'JetBrains Mono, Menlo, monospace' }}>{formatPower(prescription.leftEye.add)}</td>
                  <td style={{ ...tblCell, fontFamily: 'JetBrains Mono, Menlo, monospace' }}>{leftPD ?? '-'}</td>
                  <td style={tblCell}>{leftVA ?? '-'}</td>
                </tr>
              </tbody>
            </table>
          </div>

          {hasNearVision && (
            <div style={{ padding: '0 14px 10px' }}>
              <div style={{ fontSize: 9, color: '#4a4a45', textTransform: 'uppercase', letterSpacing: '.1em', fontWeight: 500, marginBottom: 4 }}>
                Near Vision (NV)
              </div>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    <th style={{ ...tblHead, width: '12%' }}>Eye</th>
                    <th style={tblHead}>SPH</th>
                    <th style={tblHead}>CYL</th>
                    <th style={tblHead}>AXIS</th>
                    <th style={tblHead}>ADD</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td style={{ ...tblCell, fontWeight: 700, textAlign: 'left', paddingLeft: 12 }}>OD (R)</td>
                    <td style={{ ...tblCell, fontFamily: 'JetBrains Mono, Menlo, monospace' }}>
                      {formatPower(firstPresent(prescription.nearVisionRight?.sphere, prescription.rightEye.sphere))}
                    </td>
                    <td style={{ ...tblCell, fontFamily: 'JetBrains Mono, Menlo, monospace' }}>
                      {formatPower(firstPresent(prescription.nearVisionRight?.cylinder, prescription.rightEye.cylinder))}
                    </td>
                    <td style={{ ...tblCell, fontFamily: 'JetBrains Mono, Menlo, monospace' }}>
                      {firstPresent(prescription.nearVisionRight?.axis, prescription.rightEye.axis) ?? '-'}
                    </td>
                    <td style={{ ...tblCell, fontFamily: 'JetBrains Mono, Menlo, monospace' }}>
                      {formatPower(firstPresent(prescription.nearVisionRight?.add, prescription.rightEye.add))}
                    </td>
                  </tr>
                  <tr>
                    <td style={{ ...tblCell, fontWeight: 700, textAlign: 'left', paddingLeft: 12 }}>OS (L)</td>
                    <td style={{ ...tblCell, fontFamily: 'JetBrains Mono, Menlo, monospace' }}>
                      {formatPower(firstPresent(prescription.nearVisionLeft?.sphere, prescription.leftEye.sphere))}
                    </td>
                    <td style={{ ...tblCell, fontFamily: 'JetBrains Mono, Menlo, monospace' }}>
                      {formatPower(firstPresent(prescription.nearVisionLeft?.cylinder, prescription.leftEye.cylinder))}
                    </td>
                    <td style={{ ...tblCell, fontFamily: 'JetBrains Mono, Menlo, monospace' }}>
                      {firstPresent(prescription.nearVisionLeft?.axis, prescription.leftEye.axis) ?? '-'}
                    </td>
                    <td style={{ ...tblCell, fontFamily: 'JetBrains Mono, Menlo, monospace' }}>
                      {formatPower(firstPresent(prescription.nearVisionLeft?.add, prescription.leftEye.add))}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          )}

          {/* Lens recommendation + notes. Truthiness-gated `&&` let the junk
              string "None" through -- the backend guards exactly these fields
              via `_text`, so the same rule has to hold on this side too. */}
          {(!isAbsentRxValue(prescription.lensRecommendation) ||
            !isAbsentRxValue(prescription.notes)) && (
            <div style={{ padding: '0 14px 10px' }}>
              {!isAbsentRxValue(prescription.lensRecommendation) && (
                <div style={{ fontSize: 10.5, marginTop: 4 }}>
                  <span style={{ color: '#4a4a45', textTransform: 'uppercase', letterSpacing: '.08em', fontSize: 9 }}>Lens type:</span>{' '}
                  <span style={{ color: '#1a1a19' }}>{prescription.lensRecommendation}</span>
                </div>
              )}
              {!isAbsentRxValue(prescription.notes) && (
                <div style={{ fontSize: 10, color: '#4a4a45', marginTop: 4, padding: '6px 8px', border: '1px solid #aaa9a3', background: '#f6f5f0' }}>
                  <span style={{ fontWeight: 600, color: '#1a1a19' }}>Remarks: </span>
                  {prescription.notes}
                </div>
              )}
            </div>
          )}

          {/* Practitioner signature + declaration + retention footer */}
          <LegalFooterBlock
            header={header}
            declarationText={declarationText}
            showAmountInWords={false}
            signLabel={prescription.optometristName
              ? `${prescription.optometristName} · Optometrist`
              : 'Examining Optometrist'}
          />
        </div>
      </div>

      <style>{`
        @media print {
          body * { visibility: hidden; }
          .rx-print-area, .rx-print-area * { visibility: visible; }
          .rx-print-area {
            position: absolute; left: 0; top: 0;
            width: 100%; padding: 0; margin: 0; max-width: none;
            border: none !important;
          }
          .no-print { display: none !important; }
          @page { size: A5; margin: 8mm; }
          table { page-break-inside: avoid; }
        }
      `}</style>
    </div>
  );
}

export default PrescriptionPrint;

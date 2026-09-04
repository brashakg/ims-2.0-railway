// ============================================================================
// IMS 2.0 - Final Rx (Prescription) Tab Component
// ============================================================================

import { FileText, Stethoscope } from 'lucide-react';
import clsx from 'clsx';
import type { FinalRxData, SubjectiveRxData, ClinicalFindingsData } from './eyeTestTypes';
import { LENS_TYPES, COLOUR_VISION_OPTIONS } from './eyeTestTypes';
import { RxPowerInput } from './RxPowerInput';
import { VA_OPTIONS } from '../../constants/rxLimits';
import { rxDriftWarning, type PreviousRx } from './rxDrift';
import type { PowerWarnings } from './EyeTestInput';

interface FinalRxTabProps {
  data: FinalRxData;
  onChange: (data: FinalRxData) => void;
  subjectiveRxData: SubjectiveRxData;
  // C6-B internal-only findings — rendered here but never printed on the Rx card.
  findings: ClinicalFindingsData;
  onFindingsChange: (data: ClinicalFindingsData) => void;
  /** The drift guardrail's inputs: the patient's previous Rx and their age. */
  drift?: { previousRx?: PreviousRx | null; age?: number };
}

const pw = (v: string) => clsx('pw', v.trim() !== '' && 'filled');

const BASE_OPTIONS = ['IN', 'OUT', 'UP', 'DOWN'];

function DistanceVisionRow({
  label,
  eye,
  data,
  onFieldChange,
  warnings,
}: {
  label: string;
  eye: 'rightEye' | 'leftEye';
  data: FinalRxData['rightEye'];
  onFieldChange: (eye: 'rightEye' | 'leftEye', field: string, value: string) => void;
  warnings?: PowerWarnings;
}) {
  return (
    <tr>
      <td className="eye">{eye === 'rightEye' ? 'R' : 'L'}</td>
      <td>
        <RxPowerInput kind="SPH" value={data.sphere} onChange={(v) => onFieldChange(eye, 'sphere', v)}
          placeholder="+0.00" className={pw(data.sphere)} aria-label={`${label} sphere`} />
        {/* The step's own guardrail, in place, not as a toast */}
        {warnings?.sphere && <div className="exam-drift" role="note">{warnings.sphere}</div>}
      </td>
      <td>
        <RxPowerInput kind="CYL" value={data.cylinder} onChange={(v) => onFieldChange(eye, 'cylinder', v)}
          placeholder="-0.00" className={pw(data.cylinder)} aria-label={`${label} cylinder`} />
        {warnings?.cylinder && <div className="exam-drift" role="note">{warnings.cylinder}</div>}
      </td>
      <td>
        <RxPowerInput kind="AXIS" value={data.axis} onChange={(v) => onFieldChange(eye, 'axis', v)}
          placeholder="1-180" className={pw(data.axis)} aria-label={`${label} axis`} />
      </td>
      <td>
        <input type="text" value={data.prism} onChange={(e) => onFieldChange(eye, 'prism', e.target.value)}
          placeholder="Prism" className={pw(data.prism)} aria-label={`${label} prism`} />
      </td>
      <td>
        <select value={data.base} onChange={(e) => onFieldChange(eye, 'base', e.target.value)}
          className={pw(data.base)} aria-label={`${label} prism base`}>
          <option value="">-</option>
          {BASE_OPTIONS.map(opt => (
            <option key={opt} value={opt}>{opt}</option>
          ))}
        </select>
      </td>
      <td>
        <select value={data.va} onChange={(e) => onFieldChange(eye, 'va', e.target.value)}
          className={pw(data.va)} aria-label={`${label} VA`}>
          {VA_OPTIONS.map(opt => (
            <option key={opt} value={opt}>{opt || '-'}</option>
          ))}
        </select>
      </td>
    </tr>
  );
}

export function FinalRxTab({ data, onChange, subjectiveRxData, findings, onFindingsChange, drift }: FinalRxTabProps) {
  const warn = (eye: 'Right' | 'Left', field: 'SPH' | 'CYL', next: string) =>
    rxDriftWarning({ eye, field, previous: drift?.previousRx, next, age: drift?.age });
  const setFinding = (field: keyof ClinicalFindingsData, value: string) =>
    onFindingsChange({ ...findings, [field]: value });
  const iopHigh = (v: string) => {
    const n = parseFloat(v);
    return !isNaN(n) && n > 21; // >21 mmHg is the common 'refer' threshold
  };
  const handleFieldChange = (eye: 'rightEye' | 'leftEye', field: string, value: string) => {
    onChange({
      ...data,
      [eye]: { ...data[eye], [field]: value },
    });
  };

  const handleCopyFromSubjective = () => {
    // The Subjective tab stores ADD nested inside each eye
    // (`subjectiveRxData.rightEye.add`), but the Final tab's Near Vision
    // section renders from flat root fields (`data.rightAdd` /
    // `data.leftAdd`). The earlier spread-only copy populated the eye
    // objects but never the flat fields the inputs were bound to, so
    // ADD looked blank after the copy. Mirror the per-eye ADD onto the
    // flat fields here. Same fix for IPD (Subjective captures PD per
    // eye; Final has a single IPD value — prefer the right-eye PD,
    // fall back to left, only when Final's IPD isn't already set).
    onChange({
      ...data,
      rightEye: { ...data.rightEye, ...subjectiveRxData.rightEye },
      leftEye: { ...data.leftEye, ...subjectiveRxData.leftEye },
      rightAdd: subjectiveRxData.rightEye.add || data.rightAdd,
      leftAdd: subjectiveRxData.leftEye.add || data.leftAdd,
      ipd:
        data.ipd ||
        subjectiveRxData.rightEye.pd ||
        subjectiveRxData.leftEye.pd ||
        '',
    });
  };

  const handleCopyRightToLeft = () => {
    onChange({
      ...data,
      leftEye: { ...data.rightEye },
      leftAdd: data.rightAdd,
    });
  };

  return (
    <div className="space-y-4">
      {/* Copy actions -- 44px targets */}
      <div className="flex items-center gap-2 flex-wrap">
        <button type="button" onClick={handleCopyFromSubjective} className="btn lg">
          <FileText className="w-4 h-4" />
          Copy from subjective
        </button>
        <button type="button" onClick={handleCopyRightToLeft} className="btn lg">
          Copy R to L
        </button>
      </div>

      {/* Distance vision */}
      <div className="card">
        <h4 className="font-medium text-ink mb-3">Distance vision</h4>
        <div className="pw-wrap">
          <table className="pw-grid">
            <thead>
              <tr>
                <th aria-label="Eye" />
                <th>SPH</th>
                <th>CYL</th>
                <th>AXIS</th>
                <th>PRISM</th>
                <th>BASE</th>
                <th>VA</th>
              </tr>
            </thead>
            <tbody>
              <DistanceVisionRow
                label="Right (OD)"
                eye="rightEye"
                data={data.rightEye}
                onFieldChange={handleFieldChange}
                warnings={{
                  sphere: warn('Right', 'SPH', data.rightEye.sphere),
                  cylinder: warn('Right', 'CYL', data.rightEye.cylinder),
                }}
              />
              <DistanceVisionRow
                label="Left (OS)"
                eye="leftEye"
                data={data.leftEye}
                onFieldChange={handleFieldChange}
                warnings={{
                  sphere: warn('Left', 'SPH', data.leftEye.sphere),
                  cylinder: warn('Left', 'CYL', data.leftEye.cylinder),
                }}
              />
            </tbody>
          </table>
        </div>
      </div>

      {/* Near vision (ADD) + binocular */}
      <div className="card">
        <h4 className="font-medium text-ink mb-3">Near vision and binocular</h4>
        <div className="flex items-end gap-4 flex-wrap">
          <div>
            <label className="text-xs text-ink-4 mb-1 block">Right ADD</label>
            <RxPowerInput
              kind="ADD"
              value={data.rightAdd}
              onChange={(v) => onChange({ ...data, rightAdd: v })}
              placeholder="+0.00"
              className={pw(data.rightAdd)}
              aria-label="Right eye add"
            />
          </div>
          <div>
            <label className="text-xs text-ink-4 mb-1 block">Left ADD</label>
            <RxPowerInput
              kind="ADD"
              value={data.leftAdd}
              onChange={(v) => onChange({ ...data, leftAdd: v })}
              placeholder="+0.00"
              className={pw(data.leftAdd)}
              aria-label="Left eye add"
            />
          </div>
          <div>
            <label className="text-xs text-ink-4 mb-1 block">IPD (mm)</label>
            <RxPowerInput
              kind="PD"
              value={data.ipd}
              onChange={(v) => onChange({ ...data, ipd: v })}
              placeholder="e.g., 62"
              className={clsx(pw(data.ipd), 'w-24')}
              aria-label="Interpupillary distance"
            />
          </div>
          <div>
            <label className="text-xs text-ink-4 mb-1 block">Dominant eye</label>
            <select
              value={findings.dominantEye}
              onChange={(e) => setFinding('dominantEye', e.target.value)}
              className={clsx(pw(findings.dominantEye), 'w-24')}
              aria-label="Dominant eye"
            >
              <option value="">-</option>
              <option value="RIGHT">Right</option>
              <option value="LEFT">Left</option>
            </select>
          </div>
        </div>
      </div>

      {/* Recommendations */}
      <div className="card">
        <h4 className="font-medium text-gray-800 mb-4">Recommendations</h4>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="text-sm text-gray-600 mb-1 block">Lens Type</label>
            <select
              value={data.lensType}
              onChange={(e) => onChange({ ...data, lensType: e.target.value })}
              className="input-field"
            >
              <option value="">Select Lens Type</option>
              {LENS_TYPES.map(type => (
                <option key={type} value={type}>{type}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-sm text-gray-600 mb-1 block">Next Checkup</label>
            <input
              type="date"
              value={data.nextCheckup}
              min={new Date().toISOString().slice(0, 10)}
              onChange={(e) => onChange({ ...data, nextCheckup: e.target.value })}
              className="input-field"
            />
          </div>
        </div>
      </div>

      {/* Clinical Findings (INTERNAL — not printed on the Rx card) */}
      <div className="card border-teal-200">
        <div className="flex items-center gap-2 mb-1">
          <Stethoscope className="w-4 h-4 text-teal-600" />
          <h4 className="font-medium text-gray-800">Clinical Findings</h4>
          <span className="text-xs text-gray-400">(internal — not printed for the customer)</span>
        </div>
        <p className="text-xs text-gray-500 mb-4">All optional. Leave blank for a quick refraction-only test.</p>
        <div className="grid grid-cols-2 tablet:grid-cols-3 gap-4">
          <div>
            <label className="text-sm text-gray-600 mb-1 block">Eye Pressure — Right (mmHg)</label>
            <input
              type="number" step="0.5" min="0" max="80"
              value={findings.iopRight}
              onChange={(e) => setFinding('iopRight', e.target.value)}
              placeholder="e.g., 14"
              className={`input-field ${iopHigh(findings.iopRight) ? 'border-red-400 text-red-700' : ''}`}
            />
            {iopHigh(findings.iopRight) && <p className="text-xs text-red-600 mt-0.5">High (&gt;21) — consider referral</p>}
          </div>
          <div>
            <label className="text-sm text-gray-600 mb-1 block">Eye Pressure — Left (mmHg)</label>
            <input
              type="number" step="0.5" min="0" max="80"
              value={findings.iopLeft}
              onChange={(e) => setFinding('iopLeft', e.target.value)}
              placeholder="e.g., 15"
              className={`input-field ${iopHigh(findings.iopLeft) ? 'border-red-400 text-red-700' : ''}`}
            />
            {iopHigh(findings.iopLeft) && <p className="text-xs text-red-600 mt-0.5">High (&gt;21) — consider referral</p>}
          </div>
          <div>
            <label className="text-sm text-gray-600 mb-1 block">Colour Vision</label>
            <input
              type="text" list="colour-vision-options"
              value={findings.colourVision}
              onChange={(e) => setFinding('colourVision', e.target.value)}
              placeholder="Normal"
              className="input-field"
            />
            <datalist id="colour-vision-options">
              {COLOUR_VISION_OPTIONS.map(o => <option key={o} value={o} />)}
            </datalist>
          </div>
          <div>
            <label className="text-sm text-gray-600 mb-1 block">Cover Test</label>
            <input
              type="text"
              value={findings.coverTest}
              onChange={(e) => setFinding('coverTest', e.target.value)}
              placeholder="e.g., Orthophoria"
              className="input-field"
            />
          </div>
          <div className="col-span-2 tablet:col-span-3">
            <label className="text-sm text-gray-600 mb-1 block">Diagnosis</label>
            <input
              type="text"
              value={findings.diagnosis}
              onChange={(e) => setFinding('diagnosis', e.target.value)}
              placeholder="e.g., Myopia, Astigmatism"
              className="input-field"
            />
          </div>
        </div>
      </div>

      {/* Remarks. THIS is the note that PRINTS on the patient's Rx card; the
          staff-only internal note is a different field and never does. The
          label is associated, and named for its step, so neither a screen
          reader nor a test can confuse it with the subjective remarks. */}
      <div className="card">
        <label htmlFor="final-rx-remarks" className="text-sm text-gray-600 mb-1 block">Remarks</label>
        <textarea
          id="final-rx-remarks"
          aria-label="Final Rx remarks"
          value={data.remarks}
          onChange={(e) => onChange({ ...data, remarks: e.target.value })}
          placeholder="Clinical notes, recommendations..."
          className="input-field w-full h-24 resize-none"
        />
      </div>
    </div>
  );
}

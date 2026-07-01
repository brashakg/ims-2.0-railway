// ============================================================================
// IMS 2.0 - Final Rx (Prescription) Tab Component
// ============================================================================

import { FileText, Stethoscope } from 'lucide-react';
import type { FinalRxData, SubjectiveRxData, ClinicalFindingsData } from './eyeTestTypes';
import { LENS_TYPES, COLOUR_VISION_OPTIONS } from './eyeTestTypes';
import { RxPowerInput } from './RxPowerInput';

interface FinalRxTabProps {
  data: FinalRxData;
  onChange: (data: FinalRxData) => void;
  subjectiveRxData: SubjectiveRxData;
  // C6-B internal-only findings — rendered here but never printed on the Rx card.
  findings: ClinicalFindingsData;
  onFindingsChange: (data: ClinicalFindingsData) => void;
}

const VA_OPTIONS = ['6/6', '6/9', '6/12', '6/18', '6/24', '6/36', '6/60'];
const BASE_OPTIONS = ['IN', 'OUT', 'UP', 'DOWN'];

function DistanceVisionRow({
  label,
  eye,
  data,
  onFieldChange,
}: {
  label: string;
  eye: 'rightEye' | 'leftEye';
  data: FinalRxData['rightEye'];
  onFieldChange: (eye: 'rightEye' | 'leftEye', field: string, value: string) => void;
}) {
  return (
    <tr className={eye === 'rightEye' ? 'border-b border-gray-100' : ''}>
      <td className="py-3 px-3 font-medium text-gray-900">{label}</td>
      <td className="py-2 px-2">
        <RxPowerInput kind="SPH" value={data.sphere} onChange={(v) => onFieldChange(eye, 'sphere', v)}
          placeholder="SPH" className="input-field text-center text-sm w-full" aria-label={`${label} sphere`} />
      </td>
      <td className="py-2 px-2">
        <RxPowerInput kind="CYL" value={data.cylinder} onChange={(v) => onFieldChange(eye, 'cylinder', v)}
          placeholder="CYL" className="input-field text-center text-sm w-full" aria-label={`${label} cylinder`} />
      </td>
      <td className="py-2 px-2">
        <RxPowerInput kind="AXIS" value={data.axis} onChange={(v) => onFieldChange(eye, 'axis', v)}
          placeholder="1-180" className="input-field text-center text-sm w-full" aria-label={`${label} axis`} />
      </td>
      <td className="py-2 px-2">
        <input type="text" value={data.prism} onChange={(e) => onFieldChange(eye, 'prism', e.target.value)}
          placeholder="Prism" className="input-field text-center text-sm w-full" />
      </td>
      <td className="py-2 px-2">
        <select value={data.base} onChange={(e) => onFieldChange(eye, 'base', e.target.value)}
          className="input-field text-center text-sm w-full">
          <option value="">-</option>
          {BASE_OPTIONS.map(opt => (
            <option key={opt} value={opt}>{opt}</option>
          ))}
        </select>
      </td>
      <td className="py-2 px-2">
        <select value={data.va} onChange={(e) => onFieldChange(eye, 'va', e.target.value)}
          className="input-field text-center text-sm w-full">
          {VA_OPTIONS.map(opt => (
            <option key={opt} value={opt}>{opt}</option>
          ))}
        </select>
      </td>
    </tr>
  );
}

export function FinalRxTab({ data, onChange, subjectiveRxData, findings, onFindingsChange }: FinalRxTabProps) {
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
      {/* Header with Copy buttons */}
      <div className="flex items-center justify-between">
        <h3 className="font-semibold text-gray-900">Final Prescription</h3>
        <div className="flex items-center gap-2">
          <button
            onClick={handleCopyFromSubjective}
            className="btn-outline text-sm flex items-center gap-1"
          >
            <FileText className="w-4 h-4" />
            Copy from Subjective
          </button>
          <button
            onClick={handleCopyRightToLeft}
            className="btn-outline text-sm flex items-center gap-1"
          >
            Copy R → L
          </button>
        </div>
      </div>

      {/* Distance Vision Table */}
      <div className="card">
        <h4 className="font-medium text-gray-800 mb-4">Distance Vision</h4>
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b border-gray-200">
                <th className="text-left py-2 px-3 text-sm font-medium text-gray-600 w-28">Eye</th>
                <th className="text-center py-2 px-3 text-sm font-medium text-gray-600">SPH</th>
                <th className="text-center py-2 px-3 text-sm font-medium text-gray-600">CYL</th>
                <th className="text-center py-2 px-3 text-sm font-medium text-gray-600">AXIS</th>
                <th className="text-center py-2 px-3 text-sm font-medium text-gray-600">PRISM</th>
                <th className="text-center py-2 px-3 text-sm font-medium text-gray-600">BASE</th>
                <th className="text-center py-2 px-3 text-sm font-medium text-gray-600">VA</th>
              </tr>
            </thead>
            <tbody>
              <DistanceVisionRow label="Right (OD)" eye="rightEye" data={data.rightEye} onFieldChange={handleFieldChange} />
              <DistanceVisionRow label="Left (OS)" eye="leftEye" data={data.leftEye} onFieldChange={handleFieldChange} />
            </tbody>
          </table>
        </div>
      </div>

      {/* Near Vision (ADD) */}
      <div className="card">
        <h4 className="font-medium text-gray-800 mb-4">Near Vision (ADD)</h4>
        <div className="grid grid-cols-3 gap-4">
          <div>
            <label className="text-sm text-gray-600 mb-1 block">Right ADD</label>
            <RxPowerInput
              kind="ADD"
              value={data.rightAdd}
              onChange={(v) => onChange({ ...data, rightAdd: v })}
              placeholder="+0.00"
              className="input-field"
              aria-label="Right eye add"
            />
          </div>
          <div>
            <label className="text-sm text-gray-600 mb-1 block">Left ADD</label>
            <RxPowerInput
              kind="ADD"
              value={data.leftAdd}
              onChange={(v) => onChange({ ...data, leftAdd: v })}
              placeholder="+0.00"
              className="input-field"
              aria-label="Left eye add"
            />
          </div>
          <div>
            <label className="text-sm text-gray-600 mb-1 block">IPD (mm)</label>
            <RxPowerInput
              kind="PD"
              value={data.ipd}
              onChange={(v) => onChange({ ...data, ipd: v })}
              placeholder="e.g., 62"
              className="input-field"
              aria-label="Interpupillary distance"
            />
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
            <label className="text-sm text-gray-600 mb-1 block">Dominant Eye</label>
            <select
              value={findings.dominantEye}
              onChange={(e) => setFinding('dominantEye', e.target.value)}
              className="input-field"
            >
              <option value="">—</option>
              <option value="RIGHT">Right</option>
              <option value="LEFT">Left</option>
            </select>
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

      {/* Remarks */}
      <div className="card">
        <label className="text-sm text-gray-600 mb-1 block">Remarks</label>
        <textarea
          value={data.remarks}
          onChange={(e) => onChange({ ...data, remarks: e.target.value })}
          placeholder="Clinical notes, recommendations..."
          className="input-field w-full h-24 resize-none"
        />
      </div>
    </div>
  );
}

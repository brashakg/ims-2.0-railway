// ============================================================================
// IMS 2.0 - SOAP Exam Note Form (CLI-11)
// ============================================================================
// Structured SOAP (Subjective / Objective / Assessment / Plan) form for
// optometric EHR charting. Sits alongside the existing refraction tabs.
// All fields are optional -- a refraction-only test can leave this blank.
// Dx codes follow ICD-10 / free-text for ophthalmic diagnoses.

import { useState } from 'react';
import { Plus, Trash2, AlertCircle, Stethoscope, Eye, FileText, ClipboardList } from 'lucide-react';
import type { SoapNoteData, SoapDxCodeData } from './eyeTestTypes';
import { COLOUR_VISION_OPTIONS } from './eyeTestTypes';

interface SoapNoteFormProps {
  data: SoapNoteData;
  onChange: (data: SoapNoteData) => void;
}

// Common ICD-10 codes relevant to a primary eye-care setting.
const COMMON_DX_CODES: { code: string; description: string }[] = [
  { code: 'H52.1', description: 'Myopia' },
  { code: 'H52.2', description: 'Astigmatism' },
  { code: 'H52.0', description: 'Hypermetropia' },
  { code: 'H52.4', description: 'Presbyopia' },
  { code: 'H52.3', description: 'Anisometropia' },
  { code: 'H10.1', description: 'Acute atopic conjunctivitis' },
  { code: 'H10.9', description: 'Conjunctivitis, unspecified' },
  { code: 'H11.0', description: 'Pterygium' },
  { code: 'H18.9', description: 'Disorder of cornea, unspecified' },
  { code: 'H26.9', description: 'Cataract, unspecified' },
  { code: 'H40.9', description: 'Glaucoma, unspecified' },
  { code: 'H35.3', description: 'Degeneration of macula and posterior pole' },
  { code: 'H53.0', description: 'Amblyopia ex anopsia' },
  { code: 'H50.0', description: 'Esotropia' },
  { code: 'H50.1', description: 'Exotropia' },
];

const VA_OPTIONS = ['6/6', '6/9', '6/12', '6/18', '6/24', '6/36', '6/60', 'CF', 'HM', 'PL', 'NPL'];

function SectionHeader({ icon, label, note }: { icon: React.ReactNode; label: string; note?: string }) {
  return (
    <div className="flex items-center gap-2 mb-3">
      <span className="text-gray-500">{icon}</span>
      <h4 className="font-semibold text-gray-900">{label}</h4>
      {note && <span className="text-xs text-gray-400 ml-1">({note})</span>}
    </div>
  );
}

export function SoapNoteForm({ data, onChange }: SoapNoteFormProps) {
  const [dxSearch, setDxSearch] = useState('');
  const [showDxPicker, setShowDxPicker] = useState(false);

  const set = <K extends keyof SoapNoteData>(field: K, value: SoapNoteData[K]) =>
    onChange({ ...data, [field]: value });

  // ── Dx code helpers ──
  const addDxCode = (entry: SoapDxCodeData) => {
    const existing = data.dxCodes || [];
    if (existing.some(d => d.code === entry.code)) return; // no duplicates
    onChange({ ...data, dxCodes: [...existing, entry] });
    setDxSearch('');
    setShowDxPicker(false);
  };

  const removeDxCode = (code: string) =>
    onChange({ ...data, dxCodes: (data.dxCodes || []).filter(d => d.code !== code) });

  const updateDxCode = (idx: number, field: keyof SoapDxCodeData, value: string) => {
    const updated = (data.dxCodes || []).map((d, i) =>
      i === idx ? { ...d, [field]: value } : d
    );
    onChange({ ...data, dxCodes: updated });
  };

  const filteredDx = COMMON_DX_CODES.filter(
    d =>
      d.code.toLowerCase().includes(dxSearch.toLowerCase()) ||
      d.description.toLowerCase().includes(dxSearch.toLowerCase())
  );

  const iopHigh = (v: string) => { const n = parseFloat(v); return !isNaN(n) && n > 21; };

  return (
    <div className="space-y-5">
      {/* ── S: Subjective ── */}
      <div className="card">
        <SectionHeader icon={<ClipboardList className="w-4 h-4" />} label="S — Subjective" note="patient history" />
        <div className="space-y-3">
          <div>
            <label className="text-sm text-gray-600 mb-1 block">Chief Complaint</label>
            <input
              type="text"
              value={data.chiefComplaint}
              onChange={e => set('chiefComplaint', e.target.value)}
              placeholder="e.g., blurry distance vision for 3 months"
              className="input-field"
            />
          </div>
          <div>
            <label className="text-sm text-gray-600 mb-1 block">History of Present Illness</label>
            <textarea
              value={data.historyPresentIllness}
              onChange={e => set('historyPresentIllness', e.target.value)}
              placeholder="Onset, duration, progression, associated symptoms..."
              className="input-field w-full h-20 resize-none"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-sm text-gray-600 mb-1 block">Ocular History</label>
              <textarea
                value={data.ocularHistory}
                onChange={e => set('ocularHistory', e.target.value)}
                placeholder="Previous spectacles / surgery / injuries..."
                className="input-field w-full h-16 resize-none"
              />
            </div>
            <div>
              <label className="text-sm text-gray-600 mb-1 block">Systemic History</label>
              <textarea
                value={data.systemicHistory}
                onChange={e => set('systemicHistory', e.target.value)}
                placeholder="Diabetes, hypertension, thyroid..."
                className="input-field w-full h-16 resize-none"
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-sm text-gray-600 mb-1 block">Family History</label>
              <input
                type="text"
                value={data.familyHistory}
                onChange={e => set('familyHistory', e.target.value)}
                placeholder="e.g., Glaucoma in father"
                className="input-field"
              />
            </div>
            <div>
              <label className="text-sm text-gray-600 mb-1 block">Medications / Allergies</label>
              <input
                type="text"
                value={data.medications}
                onChange={e => set('medications', e.target.value)}
                placeholder="Current medications or known allergies"
                className="input-field"
              />
            </div>
          </div>
          <div>
            <label className="text-sm text-gray-600 mb-1 block">Screen / VDU Usage</label>
            <input
              type="text"
              list="soap-vdu-options"
              value={data.vduUsage}
              onChange={e => set('vduUsage', e.target.value)}
              placeholder="e.g., 6-8 hours daily"
              className="input-field"
            />
            <datalist id="soap-vdu-options">
              {['None', '< 2 hours', '2-4 hours', '4-6 hours', '6-8 hours', '> 8 hours'].map(o => (
                <option key={o} value={o} />
              ))}
            </datalist>
          </div>
        </div>
      </div>

      {/* ── O: Objective ── */}
      <div className="card">
        <SectionHeader icon={<Eye className="w-4 h-4" />} label="O — Objective" note="clinician findings" />
        <div className="space-y-4">
          {/* VA grid */}
          <div>
            <h5 className="text-sm font-medium text-gray-700 mb-2">Visual Acuity</h5>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-200">
                    <th className="text-left py-1.5 px-2 font-medium text-gray-600 w-20">Eye</th>
                    <th className="text-center py-1.5 px-2 font-medium text-gray-600">Unaided</th>
                    <th className="text-center py-1.5 px-2 font-medium text-gray-600">Aided</th>
                  </tr>
                </thead>
                <tbody>
                  <tr className="border-b border-gray-100">
                    <td className="py-2 px-2 font-medium text-gray-700">Right (OD)</td>
                    <td className="py-1 px-2">
                      <input type="text" list="soap-va-options"
                        value={data.vaRightUnaided}
                        onChange={e => set('vaRightUnaided', e.target.value)}
                        placeholder="6/?" className="input-field text-center text-sm" />
                    </td>
                    <td className="py-1 px-2">
                      <input type="text" list="soap-va-options"
                        value={data.vaRightAided}
                        onChange={e => set('vaRightAided', e.target.value)}
                        placeholder="6/?" className="input-field text-center text-sm" />
                    </td>
                  </tr>
                  <tr>
                    <td className="py-2 px-2 font-medium text-gray-700">Left (OS)</td>
                    <td className="py-1 px-2">
                      <input type="text" list="soap-va-options"
                        value={data.vaLeftUnaided}
                        onChange={e => set('vaLeftUnaided', e.target.value)}
                        placeholder="6/?" className="input-field text-center text-sm" />
                    </td>
                    <td className="py-1 px-2">
                      <input type="text" list="soap-va-options"
                        value={data.vaLeftAided}
                        onChange={e => set('vaLeftAided', e.target.value)}
                        placeholder="6/?" className="input-field text-center text-sm" />
                    </td>
                  </tr>
                </tbody>
              </table>
              <datalist id="soap-va-options">
                {VA_OPTIONS.map(o => <option key={o} value={o} />)}
              </datalist>
            </div>
          </div>

          {/* IOP, colour vision, cover test, dominant eye */}
          <div className="grid grid-cols-2 tablet:grid-cols-4 gap-3">
            <div>
              <label className="text-sm text-gray-600 mb-1 block">IOP Right (mmHg)</label>
              <input
                type="number" step="0.5" min="0" max="80"
                value={data.iopRight}
                onChange={e => set('iopRight', e.target.value)}
                placeholder="e.g., 14"
                className={`input-field ${iopHigh(data.iopRight) ? 'border-red-400 text-red-700' : ''}`}
              />
              {iopHigh(data.iopRight) && (
                <p className="text-xs text-red-600 mt-0.5 flex items-center gap-1">
                  <AlertCircle className="w-3 h-3" /> High (&gt;21) — consider referral
                </p>
              )}
            </div>
            <div>
              <label className="text-sm text-gray-600 mb-1 block">IOP Left (mmHg)</label>
              <input
                type="number" step="0.5" min="0" max="80"
                value={data.iopLeft}
                onChange={e => set('iopLeft', e.target.value)}
                placeholder="e.g., 15"
                className={`input-field ${iopHigh(data.iopLeft) ? 'border-red-400 text-red-700' : ''}`}
              />
              {iopHigh(data.iopLeft) && (
                <p className="text-xs text-red-600 mt-0.5 flex items-center gap-1">
                  <AlertCircle className="w-3 h-3" /> High (&gt;21)
                </p>
              )}
            </div>
            <div>
              <label className="text-sm text-gray-600 mb-1 block">Colour Vision</label>
              <input
                type="text" list="soap-colour-vision-options"
                value={data.colourVision}
                onChange={e => set('colourVision', e.target.value)}
                placeholder="Normal"
                className="input-field"
              />
              <datalist id="soap-colour-vision-options">
                {COLOUR_VISION_OPTIONS.map(o => <option key={o} value={o} />)}
              </datalist>
            </div>
            <div>
              <label className="text-sm text-gray-600 mb-1 block">Dominant Eye</label>
              <select
                value={data.dominantEye}
                onChange={e => set('dominantEye', e.target.value as '' | 'RIGHT' | 'LEFT')}
                className="input-field"
              >
                <option value="">—</option>
                <option value="RIGHT">Right</option>
                <option value="LEFT">Left</option>
              </select>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-sm text-gray-600 mb-1 block">Cover Test</label>
              <input
                type="text"
                value={data.coverTest}
                onChange={e => set('coverTest', e.target.value)}
                placeholder="e.g., Orthophoria"
                className="input-field"
              />
            </div>
            <div>
              <label className="text-sm text-gray-600 mb-1 block">Pupils</label>
              <input
                type="text"
                value={data.pupils}
                onChange={e => set('pupils', e.target.value)}
                placeholder="e.g., PERRL, no RAPD"
                className="input-field"
              />
            </div>
            <div>
              <label className="text-sm text-gray-600 mb-1 block">Ocular Motility</label>
              <input
                type="text"
                value={data.ocularMotility}
                onChange={e => set('ocularMotility', e.target.value)}
                placeholder="e.g., Full in all directions"
                className="input-field"
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-sm text-gray-600 mb-1 block">Slit Lamp Summary</label>
              <textarea
                value={data.slitLampSummary}
                onChange={e => set('slitLampSummary', e.target.value)}
                placeholder="Ant. segment: clear cornea, formed AC, ..."
                className="input-field w-full h-16 resize-none"
              />
            </div>
            <div>
              <label className="text-sm text-gray-600 mb-1 block">Fundus Summary</label>
              <textarea
                value={data.fundusSummary}
                onChange={e => set('fundusSummary', e.target.value)}
                placeholder="Disc: 0.4 C/D, healthy NRR, ..."
                className="input-field w-full h-16 resize-none"
              />
            </div>
          </div>
        </div>
      </div>

      {/* ── A: Assessment ── */}
      <div className="card">
        <SectionHeader icon={<Stethoscope className="w-4 h-4" />} label="A — Assessment" note="diagnosis" />
        <div className="space-y-3">
          <div>
            <label className="text-sm text-gray-600 mb-1 block">Clinical Impression</label>
            <textarea
              value={data.assessment}
              onChange={e => set('assessment', e.target.value)}
              placeholder="Summary of diagnosis / impression..."
              className="input-field w-full h-20 resize-none"
            />
          </div>

          {/* Dx codes */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <label className="text-sm text-gray-600">Diagnosis Codes (ICD-10)</label>
              <button
                type="button"
                onClick={() => setShowDxPicker(v => !v)}
                className="btn-outline text-xs flex items-center gap-1"
              >
                <Plus className="w-3.5 h-3.5" /> Add code
              </button>
            </div>

            {/* Quick-pick from common codes */}
            {showDxPicker && (
              <div className="border border-gray-200 rounded-lg p-3 mb-3 bg-gray-50 space-y-2">
                <input
                  type="text"
                  value={dxSearch}
                  onChange={e => setDxSearch(e.target.value)}
                  placeholder="Search code or diagnosis..."
                  className="input-field text-sm"
                  autoFocus
                />
                <div className="max-h-40 overflow-y-auto space-y-1">
                  {filteredDx.map(d => (
                    <button
                      key={d.code}
                      type="button"
                      onClick={() => addDxCode({ code: d.code, description: d.description, system: 'ICD-10' })}
                      className="w-full text-left px-3 py-1.5 text-sm rounded hover:bg-bv-red-50 flex items-center gap-2"
                    >
                      <span className="font-mono text-bv-red-700 text-xs w-14 shrink-0">{d.code}</span>
                      <span className="text-gray-700">{d.description}</span>
                    </button>
                  ))}
                  {filteredDx.length === 0 && (
                    <p className="text-xs text-gray-400 px-3 py-1">No match — enter code manually below</p>
                  )}
                </div>
                {/* Manual entry row */}
                <div className="flex gap-2 pt-1 border-t border-gray-200">
                  <input
                    type="text"
                    placeholder="ICD-10 code"
                    className="input-field text-sm font-mono w-28"
                    id="dx-manual-code"
                  />
                  <input
                    type="text"
                    placeholder="Description"
                    className="input-field text-sm flex-1"
                    id="dx-manual-desc"
                  />
                  <button
                    type="button"
                    onClick={() => {
                      const codeEl = document.getElementById('dx-manual-code') as HTMLInputElement;
                      const descEl = document.getElementById('dx-manual-desc') as HTMLInputElement;
                      const code = codeEl?.value?.trim();
                      const desc = descEl?.value?.trim();
                      if (code) {
                        addDxCode({ code, description: desc || '', system: 'ICD-10' });
                        if (codeEl) codeEl.value = '';
                        if (descEl) descEl.value = '';
                      }
                    }}
                    className="btn-primary text-xs px-3"
                  >
                    Add
                  </button>
                </div>
              </div>
            )}

            {/* Saved Dx code list */}
            {(data.dxCodes || []).length > 0 ? (
              <div className="space-y-1.5">
                {(data.dxCodes || []).map((dx, idx) => (
                  <div key={idx} className="flex items-center gap-2 p-2 rounded border border-gray-200 bg-white">
                    <input
                      type="text"
                      value={dx.code}
                      onChange={e => updateDxCode(idx, 'code', e.target.value)}
                      className="input-field font-mono text-sm w-24 shrink-0"
                    />
                    <input
                      type="text"
                      value={dx.description}
                      onChange={e => updateDxCode(idx, 'description', e.target.value)}
                      className="input-field text-sm flex-1"
                      placeholder="Description"
                    />
                    <button
                      type="button"
                      onClick={() => removeDxCode(dx.code)}
                      className="text-gray-400 hover:text-red-600 transition-colors shrink-0"
                      title="Remove"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-xs text-gray-400 italic">No diagnosis codes added yet.</p>
            )}
          </div>
        </div>
      </div>

      {/* ── P: Plan ── */}
      <div className="card">
        <SectionHeader icon={<FileText className="w-4 h-4" />} label="P — Plan" note="management" />
        <div className="space-y-3">
          <div>
            <label className="text-sm text-gray-600 mb-1 block">Management Plan</label>
            <textarea
              value={data.plan}
              onChange={e => set('plan', e.target.value)}
              placeholder="Prescribed spectacles / treatment / monitoring plan..."
              className="input-field w-full h-20 resize-none"
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="flex items-start gap-3 p-3 border border-gray-200 rounded-lg">
              <input
                type="checkbox"
                id="plan-referral"
                checked={data.planReferral}
                onChange={e => set('planReferral', e.target.checked)}
                className="mt-0.5"
              />
              <div className="flex-1">
                <label htmlFor="plan-referral" className="text-sm font-medium text-gray-700 cursor-pointer">
                  Refer to Specialist
                </label>
                {data.planReferral && (
                  <input
                    type="text"
                    value={data.planReferralTo}
                    onChange={e => set('planReferralTo', e.target.value)}
                    placeholder="e.g., Ophthalmologist, Retina specialist"
                    className="input-field text-sm mt-1.5"
                  />
                )}
              </div>
            </div>

            <div className="flex items-start gap-3 p-3 border border-gray-200 rounded-lg">
              <input
                type="checkbox"
                id="plan-followup"
                checked={data.planFollowUp}
                onChange={e => set('planFollowUp', e.target.checked)}
                className="mt-0.5"
              />
              <div className="flex-1">
                <label htmlFor="plan-followup" className="text-sm font-medium text-gray-700 cursor-pointer">
                  Follow-up Required
                </label>
                {data.planFollowUp && (
                  <div className="flex items-center gap-2 mt-1.5">
                    <input
                      type="number"
                      min={1} max={104}
                      value={data.planFollowUpWeeks ?? ''}
                      onChange={e => set('planFollowUpWeeks', e.target.value ? parseInt(e.target.value, 10) : undefined)}
                      placeholder="weeks"
                      className="input-field text-sm w-20"
                    />
                    <span className="text-xs text-gray-500">weeks</span>
                  </div>
                )}
              </div>
            </div>
          </div>

          <div>
            <label className="text-sm text-gray-600 mb-1 block">Patient Instructions</label>
            <textarea
              value={data.patientInstructions}
              onChange={e => set('patientInstructions', e.target.value)}
              placeholder="Instructions to give the patient in simple language..."
              className="input-field w-full h-16 resize-none"
            />
          </div>
        </div>
      </div>
    </div>
  );
}

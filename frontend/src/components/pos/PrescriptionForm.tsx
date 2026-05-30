import { useState } from 'react';
import { Eye, Plus, X, Glasses, Contact } from 'lucide-react';

// Allowed contact-lens replacement modalities -- kept in sync with the
// backend (prescriptions.CL_MODALITIES / products.CL_MODALITIES).
const CL_MODALITIES = ['DAILY', 'FORTNIGHTLY', 'MONTHLY', 'QUARTERLY', 'YEARLY', 'COLOR'] as const;

// Spectacle Final-Rx option lists -- kept in sync with the clinical Final-Rx
// tab (components/clinical/FinalRxTab + eyeTestTypes) so a POS-captured Rx and
// a clinic-captured Rx offer identical choices.
const VA_OPTIONS = ['', '6/6', '6/9', '6/12', '6/18', '6/24', '6/36', '6/60'] as const;
const BASE_OPTIONS = ['', 'IN', 'OUT', 'UP', 'DOWN'] as const;
const LENS_TYPES = ['Single Vision', 'Bifocal', 'Progressive', 'Office Lens', 'Anti-Fatigue'] as const;

type RxKind = 'SPECTACLE' | 'CONTACT_LENS';

// Per-eye contact-lens parameters (fit by base-curve + diameter, not PD).
interface CLEyeData {
  cl_power?: number;
  cl_cyl?: number;   // toric
  cl_axis?: number;  // toric (0-180)
  cl_add?: number;   // multifocal
  base_curve?: number;
  diameter?: number;
}

interface PrescriptionData {
  // Discriminator -- SPECTACLE (default) keeps the existing spectacle fields.
  rx_kind?: RxKind;
  // ---- Spectacle fields (unchanged) ----
  sph_od?: number;
  cyl_od?: number;
  axis_od?: number;
  add_od?: number;
  pd_od?: number;
  sph_os?: number;
  cyl_os?: number;
  axis_os?: number;
  add_os?: number;
  pd_os?: number;
  // ---- Spectacle parity fields (match the clinical Final Rx) ----
  va_od?: string;
  prism_od?: string;
  base_od?: string;
  va_os?: string;
  prism_os?: string;
  base_os?: string;
  ipd?: string;
  lens_type?: string;
  next_checkup?: string;
  issue_date?: string;
  expiry_date?: string;
  doctor_name?: string;
  // ---- Contact-lens fields (only sent when rx_kind === CONTACT_LENS) ----
  cl_brand?: string;
  cl_series?: string;
  modality?: string;
  color?: string;
  cl_right?: CLEyeData;
  cl_left?: CLEyeData;
}

interface PrescriptionFormProps {
  onSubmit: (prescription: PrescriptionData) => void;
  onCancel: () => void;
  initialData?: PrescriptionData;
  // Show the SPECTACLE | CONTACT LENS toggle. Defaults to true (clinical Rx
  // capture). POS passes false: the POS create path maps spectacle fields
  // only, so the toggle is hidden there to avoid silently dropping CL data.
  allowContactLens?: boolean;
  // Label for the primary action. Defaults to "Add to Order" (POS context).
  // Clinic create/edit passes "Save prescription" / "Save changes".
  submitLabel?: string;
}

export function PrescriptionForm({
  onSubmit,
  onCancel,
  initialData,
  allowContactLens = true,
  submitLabel = 'Add to Order',
}: PrescriptionFormProps) {
  const [prescription, setPrescription] = useState<PrescriptionData>(
    initialData || {}
  );
  const rxKind: RxKind = allowContactLens
    ? prescription.rx_kind || 'SPECTACLE'
    : 'SPECTACLE';

  const handleInputChange = (
    field: keyof PrescriptionData,
    value: string | number
  ) => {
    setPrescription(prev => ({
      ...prev,
      [field]: typeof value === 'string' ? (value ? parseFloat(value) : undefined) : value,
    }));
  };

  const handleStringInputChange = (
    field: keyof PrescriptionData,
    value: string
  ) => {
    setPrescription(prev => ({
      ...prev,
      [field]: value || undefined,
    }));
  };

  // Update a single field on one contact-lens eye (cl_right / cl_left).
  const handleCLEyeChange = (
    eye: 'cl_right' | 'cl_left',
    field: keyof CLEyeData,
    raw: string
  ) => {
    const value = raw === '' ? undefined : parseFloat(raw);
    setPrescription(prev => {
      const current = (prev[eye] as CLEyeData) || {};
      const nextEye = { ...current, [field]: value };
      // Drop empty values so we don't persist a wall of undefineds.
      if (value === undefined) delete (nextEye as Record<string, unknown>)[field];
      return { ...prev, [eye]: nextEye };
    });
  };

  // On submit, send only the fields relevant to the chosen rx_kind so a CL Rx
  // never carries stray spectacle powers and vice-versa.
  const handleSubmit = () => {
    if (rxKind === 'CONTACT_LENS') {
      const {
        // Strip spectacle-only flat keys.
        sph_od: _a, cyl_od: _b, axis_od: _c, add_od: _d, pd_od: _e,
        sph_os: _f, cyl_os: _g, axis_os: _h, add_os: _i, pd_os: _j,
        va_od: _k, prism_od: _l, base_od: _m,
        va_os: _n, prism_os: _o, base_os: _p,
        ipd: _q, lens_type: _r, next_checkup: _s,
        ...clRest
      } = prescription;
      onSubmit({ ...clRest, rx_kind: 'CONTACT_LENS' });
    } else {
      const {
        // Strip CL-only fields.
        cl_brand: _a, cl_series: _b, modality: _c, color: _d,
        cl_right: _e, cl_left: _f,
        ...specRest
      } = prescription;
      onSubmit({ ...specRest, rx_kind: 'SPECTACLE' });
    }
  };

  const clEyeValue = (eye: 'cl_right' | 'cl_left', field: keyof CLEyeData): string => {
    const v = (prescription[eye] as CLEyeData | undefined)?.[field];
    return v === undefined || v === null ? '' : String(v);
  };

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl shadow-2xl max-w-4xl w-full mx-4 max-h-screen overflow-y-auto">
        {/* Header */}
        <div className="bg-white border-b border-gray-200 px-6 py-4 flex items-center justify-between sticky top-0 z-10">
          <div className="flex items-center gap-3">
            <div className="w-11 h-11 bg-teal-100 rounded-full flex items-center justify-center">
              <Eye className="w-6 h-6 text-teal-600" />
            </div>
            <div>
              <h2 className="text-xl font-bold text-gray-900">Lens Prescription</h2>
              <p className="text-sm text-gray-500">Spectacle Rx — same fields as the clinic exam</p>
            </div>
          </div>
          <button
            onClick={onCancel}
            className="p-2 hover:bg-gray-100 rounded-lg text-gray-500 hover:text-gray-900 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-6 space-y-6">
          {/* Rx kind toggle: SPECTACLE | CONTACT LENS. SPECTACLE keeps the
              existing fields; CONTACT LENS swaps in BC/DIA/power fields.
              Hidden when allowContactLens is false (e.g. POS). */}
          {allowContactLens && (
          <div className="flex rounded-lg border border-gray-300 overflow-hidden">
            <button
              type="button"
              onClick={() => setPrescription(prev => ({ ...prev, rx_kind: 'SPECTACLE' }))}
              className={`flex-1 flex items-center justify-center gap-2 py-2.5 text-sm font-semibold transition-colors ${
                rxKind === 'SPECTACLE'
                  ? 'bg-teal-600 text-white'
                  : 'bg-white text-gray-700 hover:bg-gray-100'
              }`}
            >
              <Glasses className="w-4 h-4" />
              Spectacle
            </button>
            <button
              type="button"
              onClick={() => setPrescription(prev => ({ ...prev, rx_kind: 'CONTACT_LENS' }))}
              className={`flex-1 flex items-center justify-center gap-2 py-2.5 text-sm font-semibold transition-colors ${
                rxKind === 'CONTACT_LENS'
                  ? 'bg-teal-600 text-white'
                  : 'bg-white text-gray-700 hover:bg-gray-100'
              }`}
            >
              <Contact className="w-4 h-4" />
              Contact Lens
            </button>
          </div>
          )}

          {/* Prescription Dates */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Prescription Issue Date
              </label>
              <input
                type="date"
                value={prescription.issue_date || ''}
                onChange={(e) => handleStringInputChange('issue_date', e.target.value)}
                className="input-field"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Expiry Date
              </label>
              <input
                type="date"
                value={prescription.expiry_date || ''}
                onChange={(e) => handleStringInputChange('expiry_date', e.target.value)}
                className="input-field"
              />
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Prescribing Doctor Name
            </label>
            <input
              type="text"
              placeholder="Dr. Name"
              value={prescription.doctor_name || ''}
              onChange={(e) => handleStringInputChange('doctor_name', e.target.value)}
              className="input-field"
            />
          </div>

          {rxKind === 'SPECTACLE' && (
          <>
          {/* Right Eye (OD) */}
          <div className="card">
            <h3 className="text-lg font-semibold text-gray-900 mb-4 flex items-center gap-2">
              <Eye className="w-5 h-5 text-teal-600" />
              Right Eye (OD)
            </h3>
            <div className="grid grid-cols-3 gap-4">
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  SPH (Sphere)
                </label>
                <input
                  type="number"
                  step="0.25"
                  placeholder="+1.00"
                  value={prescription.sph_od || ''}
                  onChange={(e) => handleInputChange('sph_od', e.target.value)}
                  className="input-field text-center text-sm"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  CYL (Cylinder)
                </label>
                <input
                  type="number"
                  step="0.25"
                  placeholder="-0.50"
                  value={prescription.cyl_od || ''}
                  onChange={(e) => handleInputChange('cyl_od', e.target.value)}
                  className="input-field text-center text-sm"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  AXIS
                </label>
                <input
                  type="number"
                  min="0"
                  max="180"
                  placeholder="90"
                  value={prescription.axis_od || ''}
                  onChange={(e) => handleInputChange('axis_od', e.target.value)}
                  className="input-field text-center text-sm"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  ADD (Addition)
                </label>
                <input
                  type="number"
                  step="0.25"
                  placeholder="+2.00"
                  value={prescription.add_od || ''}
                  onChange={(e) => handleInputChange('add_od', e.target.value)}
                  className="input-field text-center text-sm"
                />
              </div>
              <div className="col-span-2">
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  PD (Pupillary Distance) - mm
                </label>
                <input
                  type="number"
                  step="0.5"
                  placeholder="32.5"
                  value={prescription.pd_od || ''}
                  onChange={(e) => handleInputChange('pd_od', e.target.value)}
                  className="input-field text-center text-sm"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  VA (Acuity)
                </label>
                <select
                  value={prescription.va_od || ''}
                  onChange={(e) => handleStringInputChange('va_od', e.target.value)}
                  className="input-field text-center text-sm"
                >
                  {VA_OPTIONS.map((v) => (
                    <option key={v} value={v}>{v || '—'}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  Prism
                </label>
                <input
                  type="text"
                  placeholder="e.g. 2"
                  value={prescription.prism_od || ''}
                  onChange={(e) => handleStringInputChange('prism_od', e.target.value)}
                  className="input-field text-center text-sm"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  Base
                </label>
                <select
                  value={prescription.base_od || ''}
                  onChange={(e) => handleStringInputChange('base_od', e.target.value)}
                  className="input-field text-center text-sm"
                >
                  {BASE_OPTIONS.map((b) => (
                    <option key={b} value={b}>{b || '—'}</option>
                  ))}
                </select>
              </div>
            </div>
          </div>

          {/* Left Eye (OS) */}
          <div className="card">
            <h3 className="text-lg font-semibold text-gray-900 mb-4 flex items-center gap-2">
              <Eye className="w-5 h-5 text-teal-600" />
              Left Eye (OS)
            </h3>
            <div className="grid grid-cols-3 gap-4">
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  SPH (Sphere)
                </label>
                <input
                  type="number"
                  step="0.25"
                  placeholder="+1.00"
                  value={prescription.sph_os || ''}
                  onChange={(e) => handleInputChange('sph_os', e.target.value)}
                  className="input-field text-center text-sm"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  CYL (Cylinder)
                </label>
                <input
                  type="number"
                  step="0.25"
                  placeholder="-0.50"
                  value={prescription.cyl_os || ''}
                  onChange={(e) => handleInputChange('cyl_os', e.target.value)}
                  className="input-field text-center text-sm"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  AXIS
                </label>
                <input
                  type="number"
                  min="0"
                  max="180"
                  placeholder="90"
                  value={prescription.axis_os || ''}
                  onChange={(e) => handleInputChange('axis_os', e.target.value)}
                  className="input-field text-center text-sm"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  ADD (Addition)
                </label>
                <input
                  type="number"
                  step="0.25"
                  placeholder="+2.00"
                  value={prescription.add_os || ''}
                  onChange={(e) => handleInputChange('add_os', e.target.value)}
                  className="input-field text-center text-sm"
                />
              </div>
              <div className="col-span-2">
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  PD (Pupillary Distance) - mm
                </label>
                <input
                  type="number"
                  step="0.5"
                  placeholder="32.5"
                  value={prescription.pd_os || ''}
                  onChange={(e) => handleInputChange('pd_os', e.target.value)}
                  className="input-field text-center text-sm"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  VA (Acuity)
                </label>
                <select
                  value={prescription.va_os || ''}
                  onChange={(e) => handleStringInputChange('va_os', e.target.value)}
                  className="input-field text-center text-sm"
                >
                  {VA_OPTIONS.map((v) => (
                    <option key={v} value={v}>{v || '—'}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  Prism
                </label>
                <input
                  type="text"
                  placeholder="e.g. 2"
                  value={prescription.prism_os || ''}
                  onChange={(e) => handleStringInputChange('prism_os', e.target.value)}
                  className="input-field text-center text-sm"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  Base
                </label>
                <select
                  value={prescription.base_os || ''}
                  onChange={(e) => handleStringInputChange('base_os', e.target.value)}
                  className="input-field text-center text-sm"
                >
                  {BASE_OPTIONS.map((b) => (
                    <option key={b} value={b}>{b || '—'}</option>
                  ))}
                </select>
              </div>
            </div>
          </div>

          {/* Recommendations — parity with the clinical Final Rx */}
          <div className="card">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">Recommendations</h3>
            <div className="grid grid-cols-3 gap-4">
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  IPD (mm)
                </label>
                <input
                  type="text"
                  placeholder="e.g. 62"
                  value={prescription.ipd || ''}
                  onChange={(e) => handleStringInputChange('ipd', e.target.value)}
                  className="input-field text-center text-sm"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  Lens Type
                </label>
                <select
                  value={prescription.lens_type || ''}
                  onChange={(e) => handleStringInputChange('lens_type', e.target.value)}
                  className="input-field text-center text-sm"
                >
                  <option value="">Select lens type</option>
                  {LENS_TYPES.map((t) => (
                    <option key={t} value={t}>{t}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  Next Checkup
                </label>
                <input
                  type="date"
                  value={prescription.next_checkup || ''}
                  min={new Date().toISOString().slice(0, 10)}
                  onChange={(e) => handleStringInputChange('next_checkup', e.target.value)}
                  className="input-field text-center text-sm"
                />
              </div>
            </div>
          </div>

          {/* Info Box */}
          <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 text-blue-800 text-sm">
            <p>
              📋 <strong>Note:</strong> All prescription values are optional. Enter values as needed for the customer's lens requirements.
            </p>
          </div>
          </>
          )}

          {rxKind === 'CONTACT_LENS' && (
          <>
          {/* Contact-lens identity: brand, series, modality, colour. */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Brand
              </label>
              <input
                type="text"
                placeholder="e.g. Acuvue"
                value={prescription.cl_brand || ''}
                onChange={(e) => handleStringInputChange('cl_brand', e.target.value)}
                className="input-field"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Series
              </label>
              <input
                type="text"
                placeholder="e.g. Oasys"
                value={prescription.cl_series || ''}
                onChange={(e) => handleStringInputChange('cl_series', e.target.value)}
                className="input-field"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Modality
              </label>
              <select
                value={prescription.modality || ''}
                onChange={(e) => handleStringInputChange('modality', e.target.value)}
                className="input-field"
              >
                <option value="">Select modality</option>
                {CL_MODALITIES.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Color (cosmetic)
              </label>
              <input
                type="text"
                placeholder="e.g. Hazel"
                value={prescription.color || ''}
                onChange={(e) => handleStringInputChange('color', e.target.value)}
                className="input-field"
              />
            </div>
          </div>

          {/* CL Right Eye (OD) */}
          <div className="card">
            <h3 className="text-lg font-semibold text-gray-900 mb-4 flex items-center gap-2">
              <Contact className="w-5 h-5 text-teal-600" />
              Right Eye (OD)
            </h3>
            <div className="grid grid-cols-3 gap-4">
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  Power
                </label>
                <input
                  type="number"
                  step="0.25"
                  placeholder="-2.25"
                  value={clEyeValue('cl_right', 'cl_power')}
                  onChange={(e) => handleCLEyeChange('cl_right', 'cl_power', e.target.value)}
                  className="input-field text-center text-sm"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  Base Curve (BC)
                </label>
                <input
                  type="number"
                  step="0.1"
                  placeholder="8.6"
                  value={clEyeValue('cl_right', 'base_curve')}
                  onChange={(e) => handleCLEyeChange('cl_right', 'base_curve', e.target.value)}
                  className="input-field text-center text-sm"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  Diameter (DIA)
                </label>
                <input
                  type="number"
                  step="0.1"
                  placeholder="14.2"
                  value={clEyeValue('cl_right', 'diameter')}
                  onChange={(e) => handleCLEyeChange('cl_right', 'diameter', e.target.value)}
                  className="input-field text-center text-sm"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  CYL (toric)
                </label>
                <input
                  type="number"
                  step="0.25"
                  placeholder="-0.75"
                  value={clEyeValue('cl_right', 'cl_cyl')}
                  onChange={(e) => handleCLEyeChange('cl_right', 'cl_cyl', e.target.value)}
                  className="input-field text-center text-sm"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  AXIS (toric)
                </label>
                <input
                  type="number"
                  min="0"
                  max="180"
                  placeholder="180"
                  value={clEyeValue('cl_right', 'cl_axis')}
                  onChange={(e) => handleCLEyeChange('cl_right', 'cl_axis', e.target.value)}
                  className="input-field text-center text-sm"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  ADD (multifocal)
                </label>
                <input
                  type="number"
                  step="0.25"
                  placeholder="+2.00"
                  value={clEyeValue('cl_right', 'cl_add')}
                  onChange={(e) => handleCLEyeChange('cl_right', 'cl_add', e.target.value)}
                  className="input-field text-center text-sm"
                />
              </div>
            </div>
          </div>

          {/* CL Left Eye (OS) */}
          <div className="card">
            <h3 className="text-lg font-semibold text-gray-900 mb-4 flex items-center gap-2">
              <Contact className="w-5 h-5 text-teal-600" />
              Left Eye (OS)
            </h3>
            <div className="grid grid-cols-3 gap-4">
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  Power
                </label>
                <input
                  type="number"
                  step="0.25"
                  placeholder="-2.00"
                  value={clEyeValue('cl_left', 'cl_power')}
                  onChange={(e) => handleCLEyeChange('cl_left', 'cl_power', e.target.value)}
                  className="input-field text-center text-sm"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  Base Curve (BC)
                </label>
                <input
                  type="number"
                  step="0.1"
                  placeholder="8.6"
                  value={clEyeValue('cl_left', 'base_curve')}
                  onChange={(e) => handleCLEyeChange('cl_left', 'base_curve', e.target.value)}
                  className="input-field text-center text-sm"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  Diameter (DIA)
                </label>
                <input
                  type="number"
                  step="0.1"
                  placeholder="14.2"
                  value={clEyeValue('cl_left', 'diameter')}
                  onChange={(e) => handleCLEyeChange('cl_left', 'diameter', e.target.value)}
                  className="input-field text-center text-sm"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  CYL (toric)
                </label>
                <input
                  type="number"
                  step="0.25"
                  placeholder="-0.75"
                  value={clEyeValue('cl_left', 'cl_cyl')}
                  onChange={(e) => handleCLEyeChange('cl_left', 'cl_cyl', e.target.value)}
                  className="input-field text-center text-sm"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  AXIS (toric)
                </label>
                <input
                  type="number"
                  min="0"
                  max="180"
                  placeholder="180"
                  value={clEyeValue('cl_left', 'cl_axis')}
                  onChange={(e) => handleCLEyeChange('cl_left', 'cl_axis', e.target.value)}
                  className="input-field text-center text-sm"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  ADD (multifocal)
                </label>
                <input
                  type="number"
                  step="0.25"
                  placeholder="+2.00"
                  value={clEyeValue('cl_left', 'cl_add')}
                  onChange={(e) => handleCLEyeChange('cl_left', 'cl_add', e.target.value)}
                  className="input-field text-center text-sm"
                />
              </div>
            </div>
          </div>

          {/* CL Info Box */}
          <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 text-blue-800 text-sm">
            <p>
              <strong>Note:</strong> Contact lenses are fit by base-curve (BC) and
              diameter (DIA), not pupillary distance. Enter CYL/AXIS only for toric
              lenses and ADD only for multifocal lenses.
            </p>
          </div>
          </>
          )}

          {/* Action Buttons */}
          <div className="flex gap-4 pt-4 border-t border-gray-200">
            <button
              onClick={onCancel}
              className="btn-outline flex-1 justify-center"
            >
              Cancel
            </button>
            <button
              onClick={handleSubmit}
              className="btn-primary flex-1 justify-center"
            >
              <Plus className="w-5 h-5" />
              {submitLabel}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default PrescriptionForm;

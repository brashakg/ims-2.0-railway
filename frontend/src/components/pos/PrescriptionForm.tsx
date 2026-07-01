import { useState } from 'react';
import { Eye, Plus, X, Glasses, Contact } from 'lucide-react';
import { RxPowerInput } from '../clinical/RxPowerInput';
import { validateEyePair } from '../../constants/rxLimits';
import { useToast } from '../../context/ToastContext';

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
  const toast = useToast();
  const [prescription, setPrescription] = useState<PrescriptionData>(
    initialData || {}
  );
  const rxKind: RxKind = allowContactLens
    ? prescription.rx_kind || 'SPECTACLE'
    : 'SPECTACLE';

  // Read a numeric spectacle field back as a STRING for RxPowerInput. A stored
  // number renders through formatRxPower (so 5 -> "+5.00") inside the input.
  const numStr = (field: keyof PrescriptionData): string => {
    const v = prescription[field];
    return v === undefined || v === null ? '' : String(v);
  };

  // RxPowerInput emits a normalized STRING (e.g. "+5.00", "-0.75", "0.00"); the
  // signed string parses cleanly (Number("+5.00") === 5) so the sign survives.
  const handleRxChange = (field: keyof PrescriptionData, value: string) => {
    setPrescription(prev => ({
      ...prev,
      [field]: value.trim() === '' ? undefined : parseFloat(value),
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

  // Validate the entered powers against the canonical realistic limits before
  // submitting (the backend is the ultimate gate; this gives a fast message).
  const validateBeforeSubmit = (): string | null => {
    if (rxKind === 'SPECTACLE') {
      const od = validateEyePair(
        { sph: prescription.sph_od, cyl: prescription.cyl_od, axis: prescription.axis_od,
          add: prescription.add_od, pd: prescription.pd_od, va: prescription.va_od },
        'Right eye (OD)',
      );
      if (od) return od;
      const os = validateEyePair(
        { sph: prescription.sph_os, cyl: prescription.cyl_os, axis: prescription.axis_os,
          add: prescription.add_os, pd: prescription.pd_os, va: prescription.va_os },
        'Left eye (OS)',
      );
      if (os) return os;
    } else {
      // Contact lens: power/cyl/axis/add + base curve + diameter per eye.
      const check = (eye: CLEyeData | undefined, label: string): string | null =>
        validateEyePair(
          { sph: eye?.cl_power, cyl: eye?.cl_cyl, axis: eye?.cl_axis, add: eye?.cl_add,
            base_curve: eye?.base_curve, diameter: eye?.diameter },
          label,
        );
      const od = check(prescription.cl_right, 'Right eye (OD)');
      if (od) return od;
      const os = check(prescription.cl_left, 'Left eye (OS)');
      if (os) return os;
    }
    return null;
  };

  // On submit, send only the fields relevant to the chosen rx_kind so a CL Rx
  // never carries stray spectacle powers and vice-versa.
  const handleSubmit = () => {
    const err = validateBeforeSubmit();
    if (err) {
      toast.error(err);
      return;
    }
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
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  SPH (Sphere)
                </label>
                <RxPowerInput
                  kind="SPH"
                  placeholder="+1.00"
                  value={numStr('sph_od')}
                  onChange={(v) => handleRxChange('sph_od', v)}
                  className="input-field text-center text-sm"
                  aria-label="Right eye sphere"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  CYL (Cylinder)
                </label>
                <RxPowerInput
                  kind="CYL"
                  placeholder="-0.50"
                  value={numStr('cyl_od')}
                  onChange={(v) => handleRxChange('cyl_od', v)}
                  className="input-field text-center text-sm"
                  aria-label="Right eye cylinder"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  AXIS
                </label>
                <RxPowerInput
                  kind="AXIS"
                  placeholder="90"
                  value={numStr('axis_od')}
                  onChange={(v) => handleRxChange('axis_od', v)}
                  className="input-field text-center text-sm"
                  aria-label="Right eye axis"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  ADD (Addition)
                </label>
                <RxPowerInput
                  kind="ADD"
                  placeholder="+2.00"
                  value={numStr('add_od')}
                  onChange={(v) => handleRxChange('add_od', v)}
                  className="input-field text-center text-sm"
                  aria-label="Right eye add"
                />
              </div>
              <div className="col-span-2">
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  PD (Pupillary Distance) - mm
                </label>
                <RxPowerInput
                  kind="PD"
                  placeholder="32.5"
                  value={numStr('pd_od')}
                  onChange={(v) => handleRxChange('pd_od', v)}
                  className="input-field text-center text-sm"
                  aria-label="Right eye pupillary distance"
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
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  SPH (Sphere)
                </label>
                <RxPowerInput
                  kind="SPH"
                  placeholder="+1.00"
                  value={numStr('sph_os')}
                  onChange={(v) => handleRxChange('sph_os', v)}
                  className="input-field text-center text-sm"
                  aria-label="Left eye sphere"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  CYL (Cylinder)
                </label>
                <RxPowerInput
                  kind="CYL"
                  placeholder="-0.50"
                  value={numStr('cyl_os')}
                  onChange={(v) => handleRxChange('cyl_os', v)}
                  className="input-field text-center text-sm"
                  aria-label="Left eye cylinder"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  AXIS
                </label>
                <RxPowerInput
                  kind="AXIS"
                  placeholder="90"
                  value={numStr('axis_os')}
                  onChange={(v) => handleRxChange('axis_os', v)}
                  className="input-field text-center text-sm"
                  aria-label="Left eye axis"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  ADD (Addition)
                </label>
                <RxPowerInput
                  kind="ADD"
                  placeholder="+2.00"
                  value={numStr('add_os')}
                  onChange={(v) => handleRxChange('add_os', v)}
                  className="input-field text-center text-sm"
                  aria-label="Left eye add"
                />
              </div>
              <div className="col-span-2">
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  PD (Pupillary Distance) - mm
                </label>
                <RxPowerInput
                  kind="PD"
                  placeholder="32.5"
                  value={numStr('pd_os')}
                  onChange={(v) => handleRxChange('pd_os', v)}
                  className="input-field text-center text-sm"
                  aria-label="Left eye pupillary distance"
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
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
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
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  Power
                </label>
                <RxPowerInput
                  kind="SPH"
                  placeholder="-2.25"
                  value={clEyeValue('cl_right', 'cl_power')}
                  onChange={(v) => handleCLEyeChange('cl_right', 'cl_power', v)}
                  className="input-field text-center text-sm"
                  aria-label="Right contact lens power"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  Base Curve (BC)
                </label>
                <RxPowerInput
                  kind="BC"
                  placeholder="8.6"
                  value={clEyeValue('cl_right', 'base_curve')}
                  onChange={(v) => handleCLEyeChange('cl_right', 'base_curve', v)}
                  className="input-field text-center text-sm"
                  aria-label="Right contact lens base curve"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  Diameter (DIA)
                </label>
                <RxPowerInput
                  kind="DIA"
                  placeholder="14.2"
                  value={clEyeValue('cl_right', 'diameter')}
                  onChange={(v) => handleCLEyeChange('cl_right', 'diameter', v)}
                  className="input-field text-center text-sm"
                  aria-label="Right contact lens diameter"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  CYL (toric)
                </label>
                <RxPowerInput
                  kind="CYL"
                  placeholder="-0.75"
                  value={clEyeValue('cl_right', 'cl_cyl')}
                  onChange={(v) => handleCLEyeChange('cl_right', 'cl_cyl', v)}
                  className="input-field text-center text-sm"
                  aria-label="Right contact lens cylinder"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  AXIS (toric)
                </label>
                <RxPowerInput
                  kind="AXIS"
                  placeholder="180"
                  value={clEyeValue('cl_right', 'cl_axis')}
                  onChange={(v) => handleCLEyeChange('cl_right', 'cl_axis', v)}
                  className="input-field text-center text-sm"
                  aria-label="Right contact lens axis"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  ADD (multifocal)
                </label>
                <RxPowerInput
                  kind="ADD"
                  placeholder="+2.00"
                  value={clEyeValue('cl_right', 'cl_add')}
                  onChange={(v) => handleCLEyeChange('cl_right', 'cl_add', v)}
                  className="input-field text-center text-sm"
                  aria-label="Right contact lens add"
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
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  Power
                </label>
                <RxPowerInput
                  kind="SPH"
                  placeholder="-2.00"
                  value={clEyeValue('cl_left', 'cl_power')}
                  onChange={(v) => handleCLEyeChange('cl_left', 'cl_power', v)}
                  className="input-field text-center text-sm"
                  aria-label="Left contact lens power"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  Base Curve (BC)
                </label>
                <RxPowerInput
                  kind="BC"
                  placeholder="8.6"
                  value={clEyeValue('cl_left', 'base_curve')}
                  onChange={(v) => handleCLEyeChange('cl_left', 'base_curve', v)}
                  className="input-field text-center text-sm"
                  aria-label="Left contact lens base curve"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  Diameter (DIA)
                </label>
                <RxPowerInput
                  kind="DIA"
                  placeholder="14.2"
                  value={clEyeValue('cl_left', 'diameter')}
                  onChange={(v) => handleCLEyeChange('cl_left', 'diameter', v)}
                  className="input-field text-center text-sm"
                  aria-label="Left contact lens diameter"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  CYL (toric)
                </label>
                <RxPowerInput
                  kind="CYL"
                  placeholder="-0.75"
                  value={clEyeValue('cl_left', 'cl_cyl')}
                  onChange={(v) => handleCLEyeChange('cl_left', 'cl_cyl', v)}
                  className="input-field text-center text-sm"
                  aria-label="Left contact lens cylinder"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  AXIS (toric)
                </label>
                <RxPowerInput
                  kind="AXIS"
                  placeholder="180"
                  value={clEyeValue('cl_left', 'cl_axis')}
                  onChange={(v) => handleCLEyeChange('cl_left', 'cl_axis', v)}
                  className="input-field text-center text-sm"
                  aria-label="Left contact lens axis"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  ADD (multifocal)
                </label>
                <RxPowerInput
                  kind="ADD"
                  placeholder="+2.00"
                  value={clEyeValue('cl_left', 'cl_add')}
                  onChange={(v) => handleCLEyeChange('cl_left', 'cl_add', v)}
                  className="input-field text-center text-sm"
                  aria-label="Left contact lens add"
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

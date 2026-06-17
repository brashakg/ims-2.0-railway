// ============================================================================
// IMS 2.0 - Clinical Patient Intake Modal
// ============================================================================
// Token-first PATIENT intake for the clinical flow. Unlike the generic CRM
// "Add Customer" modal (components/customers/AddCustomerModal), this captures
// the minimal patient identity an optometrist needs to start an exam PLUS an
// inline refraction grid (OD/OS x SPH/CYL/AXIS/ADD/PD, with optional VA) so
// the Rx is captured at the same moment the patient is registered.
//
// It REUSES the existing customer + prescription create APIs:
//   - customerApi.searchByPhone / createCustomer  (patient identity record)
//   - prescriptionApi.createPrescription          (optional Rx, flat keys)
//   - clinicalApi.addToQueue                       (token / queue entry)
//
// Patient (clinical) and Customer (POS) are distinct concepts in the design:
// this modal is the clinical door. The POS "Add Customer" flow is untouched.
// ============================================================================

import { useEffect, useMemo, useState } from 'react';
import { Eye, X, Loader2, UserPlus } from 'lucide-react';
import { customerApi, clinicalApi, prescriptionApi } from '../../services/api';
import { useToast } from '../../context/ToastContext';

const RELATIONS = [
  'Self', 'Spouse', 'Father', 'Mother', 'Son', 'Daughter',
  'Brother', 'Sister', 'Grandfather', 'Grandmother', 'Other',
];

// Visual-acuity options — kept in sync with the clinic Final-Rx / POS form.
const VA_OPTIONS = ['', '6/6', '6/9', '6/12', '6/18', '6/24', '6/36', '6/60'] as const;

// Per-eye refraction values (strings while editing; parsed on submit).
interface EyeRx {
  sph: string;
  cyl: string;
  axis: string;
  add: string;
  pd: string;
  va: string;
}

const emptyEye = (): EyeRx => ({ sph: '', cyl: '', axis: '', add: '', pd: '', va: '' });

export interface PatientIntakeResult {
  customerId?: string;
  patientName: string;
  prescriptionId?: string;
}

interface PatientIntakeModalProps {
  isOpen: boolean;
  onClose: () => void;
  storeId?: string;
  /** Pre-fill the name (or phone) field from a failed "queue existing" search. */
  initialName?: string;
  /** Called after a successful intake so the page can refresh the queue. */
  onComplete?: (result: PatientIntakeResult) => void;
}

// --- Rx range validation (mirrors the business rules + backend gate) --------
// SPH -20..+20 (0.25) · CYL -6..+6 (0.25) · AXIS 1-180 whole · ADD +0.75..+3.50 (0.25)
function inStep(n: number, step: number): boolean {
  // Avoid float drift: compare on an integer grid.
  return Math.abs(Math.round(n / step) - n / step) < 1e-6;
}

function validateEye(eye: EyeRx, label: string): string | null {
  const num = (v: string) => (v.trim() === '' ? null : Number(v));

  const sph = num(eye.sph);
  if (sph !== null) {
    if (!Number.isFinite(sph) || sph < -20 || sph > 20) return `${label} SPH must be between -20.00 and +20.00`;
    if (!inStep(sph, 0.25)) return `${label} SPH must be in 0.25 steps`;
  }
  const cyl = num(eye.cyl);
  if (cyl !== null) {
    if (!Number.isFinite(cyl) || cyl < -6 || cyl > 6) return `${label} CYL must be between -6.00 and +6.00`;
    if (!inStep(cyl, 0.25)) return `${label} CYL must be in 0.25 steps`;
  }
  const axis = num(eye.axis);
  if (axis !== null) {
    if (!Number.isFinite(axis) || axis < 1 || axis > 180 || !Number.isInteger(axis))
      return `${label} AXIS must be a whole number 1-180`;
  }
  const add = num(eye.add);
  if (add !== null) {
    if (!Number.isFinite(add) || add < 0.75 || add > 3.5) return `${label} ADD must be between +0.75 and +3.50`;
    if (!inStep(add, 0.25)) return `${label} ADD must be in 0.25 steps`;
  }
  const pd = num(eye.pd);
  if (pd !== null && (!Number.isFinite(pd) || pd < 20 || pd > 80)) return `${label} PD looks out of range`;
  return null;
}

function eyeHasValue(eye: EyeRx): boolean {
  return (['sph', 'cyl', 'axis', 'add', 'pd', 'va'] as const).some((k) => eye[k].trim() !== '');
}

export function PatientIntakeModal({
  isOpen,
  onClose,
  storeId,
  initialName,
  onComplete,
}: PatientIntakeModalProps) {
  const toast = useToast();

  const [name, setName] = useState('');
  const [mobile, setMobile] = useState('');
  const [dob, setDob] = useState('');
  const [relation, setRelation] = useState('Self');
  const [reason, setReason] = useState('Eye examination');
  const [captureRx, setCaptureRx] = useState(false);
  const [od, setOd] = useState<EyeRx>(emptyEye());
  const [os, setOs] = useState<EyeRx>(emptyEye());
  const [saving, setSaving] = useState(false);

  // Seed the name/phone field from the "couldn't find them" handoff.
  useEffect(() => {
    if (!isOpen) return;
    const seed = (initialName || '').trim();
    if (/^\d{6,}$/.test(seed.replace(/\D/g, ''))) {
      setMobile(seed.replace(/\D/g, '').slice(-10));
    } else if (seed) {
      setName(seed);
    }
  }, [isOpen, initialName]);

  const reset = () => {
    setName(''); setMobile(''); setDob(''); setRelation('Self');
    setReason('Eye examination'); setCaptureRx(false);
    setOd(emptyEye()); setOs(emptyEye());
  };

  const handleClose = () => {
    if (saving) return;
    reset();
    onClose();
  };

  const setEyeField = (eye: 'od' | 'os', field: keyof EyeRx, value: string) => {
    const setter = eye === 'od' ? setOd : setOs;
    setter((prev) => ({ ...prev, [field]: value }));
  };

  const calcAge = (d: string): number | undefined => {
    if (!d) return undefined;
    const birth = new Date(d);
    if (Number.isNaN(birth.getTime())) return undefined;
    const now = new Date();
    let age = now.getFullYear() - birth.getFullYear();
    const m = now.getMonth() - birth.getMonth();
    if (m < 0 || (m === 0 && now.getDate() < birth.getDate())) age--;
    return age >= 0 ? age : undefined;
  };

  const sanitizedMobile = useMemo(() => mobile.replace(/\D/g, '').slice(-10), [mobile]);
  const hasRx = captureRx && (eyeHasValue(od) || eyeHasValue(os));

  const handleSubmit = async () => {
    if (!storeId) {
      toast.error('No active store selected');
      return;
    }
    if (!name.trim()) {
      toast.error('Patient name is required');
      return;
    }
    if (sanitizedMobile.length !== 10) {
      toast.error('Mobile number must contain 10 digits');
      return;
    }

    // Validate Rx ranges up-front (the backend is the final gate, but a clear
    // client message avoids a round-trip on an obvious typo).
    if (captureRx) {
      const odErr = validateEye(od, 'Right eye (OD)');
      if (odErr) { toast.error(odErr); return; }
      const osErr = validateEye(os, 'Left eye (OS)');
      if (osErr) { toast.error(osErr); return; }
    }

    setSaving(true);
    try {
      // 1) Find-or-create the customer record (the patient's billing account).
      const lookup = async (): Promise<any | null> => {
        try {
          const r = await customerApi.searchByPhone(sanitizedMobile);
          if (!r) return null;
          if (Array.isArray(r)) return r[0] || null;
          if ((r as any).customer) return (r as any).customer;
          if (Array.isArray((r as any).customers)) return (r as any).customers[0] || null;
          if ((r as any).customer_id || (r as any)._id || (r as any).id) return r;
          return null;
        } catch {
          return null;
        }
      };

      let existing = await lookup();
      let customerId: string | undefined;
      let isExisting = !!existing;

      if (existing) {
        customerId = existing.customer_id || existing._id || existing.id;
      } else {
        const payload = {
          name: name.trim(),
          mobile: sanitizedMobile,
          dob: dob || undefined,
          customer_type: 'B2C',
          // Register the named person as the first patient on the account so
          // the Rx and queue entry group under them in Family Rx.
          patients: [{ name: name.trim(), mobile: sanitizedMobile, dob: dob || undefined, relation }],
        };
        try {
          const created = await customerApi.createCustomer(payload as any);
          customerId = created?.customer_id || created?.id;
        } catch (err: any) {
          const detail: string = err?.response?.data?.detail ?? err?.message ?? '';
          if (/already exists/i.test(detail)) {
            existing = await lookup();
            if (existing) {
              customerId = existing.customer_id || existing._id || existing.id;
              isExisting = true;
            } else {
              throw err;
            }
          } else {
            throw err;
          }
        }
      }

      // 2) Optionally create the prescription (flat keys -> API normalises to
      //    nested right_eye/left_eye and the backend validates the ranges).
      let prescriptionId: string | undefined;
      if (hasRx) {
        const trimVal = (v: string) => (v.trim() === '' ? undefined : v.trim());
        try {
          const rx = await prescriptionApi.createPrescription({
            customer_id: customerId,
            store_id: storeId,
            source: 'TESTED_AT_STORE',
            rx_kind: 'SPECTACLE',
            sph_od: trimVal(od.sph), cyl_od: trimVal(od.cyl), axis_od: trimVal(od.axis),
            add_od: trimVal(od.add), pd_od: trimVal(od.pd), va_od: trimVal(od.va),
            sph_os: trimVal(os.sph), cyl_os: trimVal(os.cyl), axis_os: trimVal(os.axis),
            add_os: trimVal(os.add), pd_os: trimVal(os.pd), va_os: trimVal(os.va),
          });
          prescriptionId = rx?.prescription_id || rx?.id || rx?._id;
        } catch (err: any) {
          // Surface the backend's range message, but don't lose the patient —
          // they're still created and queued; the Rx can be added in the exam.
          const detail = err?.response?.data?.detail || err?.message || 'Could not save prescription';
          toast.warning(`Patient saved, but Rx not stored: ${detail}`);
        }
      }

      // 3) Add the patient to the clinical queue (issues the token).
      await clinicalApi.addToQueue({
        storeId,
        patientName: name.trim(),
        customerPhone: sanitizedMobile,
        customerId,
        age: calcAge(dob),
        reason: reason || 'Eye examination',
      });

      toast.success(
        isExisting
          ? `${name.trim()} added to queue${prescriptionId ? ' with new Rx' : ''}`
          : `Patient created and added to queue${prescriptionId ? ' with Rx' : ''}`,
      );

      onComplete?.({ customerId, patientName: name.trim(), prescriptionId });
      reset();
      onClose();
    } catch (err: any) {
      const detail = err?.response?.data?.detail || err?.message || 'Failed to add patient';
      toast.error(detail);
    } finally {
      setSaving(false);
    }
  };

  if (!isOpen) return null;

  const eyeRow = (label: string, eye: EyeRx, key: 'od' | 'os') => (
    <div className="grid grid-cols-[44px_repeat(6,minmax(0,1fr))] gap-1.5 items-center">
      <span className="text-xs font-mono font-semibold text-gray-500">{label}</span>
      <input
        type="number" step="0.25" placeholder="SPH"
        value={eye.sph} onChange={(e) => setEyeField(key, 'sph', e.target.value)}
        className="input-field text-center text-sm py-1.5"
        aria-label={`${label} sphere`}
      />
      <input
        type="number" step="0.25" placeholder="CYL"
        value={eye.cyl} onChange={(e) => setEyeField(key, 'cyl', e.target.value)}
        className="input-field text-center text-sm py-1.5"
        aria-label={`${label} cylinder`}
      />
      <input
        type="number" min="1" max="180" placeholder="AXIS"
        value={eye.axis} onChange={(e) => setEyeField(key, 'axis', e.target.value)}
        className="input-field text-center text-sm py-1.5"
        aria-label={`${label} axis`}
      />
      <input
        type="number" step="0.25" placeholder="ADD"
        value={eye.add} onChange={(e) => setEyeField(key, 'add', e.target.value)}
        className="input-field text-center text-sm py-1.5"
        aria-label={`${label} add`}
      />
      <input
        type="number" step="0.5" placeholder="PD"
        value={eye.pd} onChange={(e) => setEyeField(key, 'pd', e.target.value)}
        className="input-field text-center text-sm py-1.5"
        aria-label={`${label} pupillary distance`}
      />
      <select
        value={eye.va} onChange={(e) => setEyeField(key, 'va', e.target.value)}
        className="input-field text-center text-sm py-1.5"
        aria-label={`${label} visual acuity`}
      >
        {VA_OPTIONS.map((v) => <option key={v} value={v}>{v || 'VA'}</option>)}
      </select>
    </div>
  );

  return (
    <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-2xl max-h-[92vh] overflow-y-auto">
        {/* Header */}
        <div className="bg-white border-b border-gray-200 px-6 py-4 flex items-center justify-between sticky top-0 z-10">
          <div className="flex items-center gap-3">
            <div className="w-11 h-11 bg-teal-100 rounded-full flex items-center justify-center">
              <UserPlus className="w-5 h-5 text-teal-600" />
            </div>
            <div>
              <h2 className="text-xl font-bold text-gray-900">New Patient Intake</h2>
              <p className="text-sm text-gray-500">Register the patient and capture their refraction</p>
            </div>
          </div>
          <button
            onClick={handleClose}
            disabled={saving}
            className="p-2 hover:bg-gray-100 rounded-lg text-gray-500 hover:text-gray-900 transition-colors disabled:opacity-50"
            aria-label="Close"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-6 space-y-5">
          {/* Identity — token-first: who is being seen */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div className="sm:col-span-2">
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Patient name <span className="text-red-500">*</span>
              </label>
              <input
                type="text" autoFocus placeholder="Full name"
                value={name} onChange={(e) => setName(e.target.value)}
                className="input-field"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Mobile <span className="text-red-500">*</span>
              </label>
              <input
                type="tel" inputMode="numeric" placeholder="10-digit mobile"
                value={mobile} onChange={(e) => setMobile(e.target.value)}
                className="input-field"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Date of birth</label>
              <input
                type="date" value={dob} onChange={(e) => setDob(e.target.value)}
                max={new Date().toISOString().slice(0, 10)}
                className="input-field"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Relation</label>
              <select
                value={relation} onChange={(e) => setRelation(e.target.value)}
                className="input-field"
              >
                {RELATIONS.map((r) => <option key={r} value={r}>{r}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Reason for visit</label>
              <input
                type="text" value={reason} onChange={(e) => setReason(e.target.value)}
                className="input-field"
              />
            </div>
          </div>

          {/* Rx capture toggle + inline grid */}
          <div className="border-t border-gray-100 pt-4">
            <label className="flex items-center gap-2 cursor-pointer select-none">
              <input
                type="checkbox" checked={captureRx}
                onChange={(e) => setCaptureRx(e.target.checked)}
                className="w-4 h-4 accent-teal-600"
              />
              <span className="text-sm font-medium text-gray-900 flex items-center gap-1.5">
                <Eye className="w-4 h-4 text-teal-600" />
                Capture prescription now
              </span>
              <span className="text-xs text-gray-400">(optional — can also be done in the exam)</span>
            </label>

            {captureRx && (
              <div className="mt-4 space-y-2 bg-gray-50 rounded-lg p-3 border border-gray-100">
                {/* Column headers */}
                <div className="grid grid-cols-[44px_repeat(6,minmax(0,1fr))] gap-1.5 text-[10px] font-mono uppercase tracking-wide text-gray-400 px-0.5">
                  <span>Eye</span>
                  <span className="text-center">Sph</span>
                  <span className="text-center">Cyl</span>
                  <span className="text-center">Axis</span>
                  <span className="text-center">Add</span>
                  <span className="text-center">PD</span>
                  <span className="text-center">VA</span>
                </div>
                {eyeRow('OD', od, 'od')}
                {eyeRow('OS', os, 'os')}
                <p className="text-[11px] text-gray-400 pt-1">
                  SPH -20 to +20 · CYL -6 to +6 (0.25 step) · AXIS 1-180 · ADD +0.75 to +3.50
                </p>
              </div>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="bg-gray-50 border-t border-gray-200 px-6 py-4 flex items-center justify-end gap-3 sticky bottom-0">
          <button onClick={handleClose} disabled={saving} className="btn">
            Cancel
          </button>
          <button onClick={handleSubmit} disabled={saving} className="btn primary">
            {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <UserPlus className="w-4 h-4" />}
            {hasRx ? 'Add patient + Rx' : 'Add patient'}
          </button>
        </div>
      </div>
    </div>
  );
}

export default PatientIntakeModal;

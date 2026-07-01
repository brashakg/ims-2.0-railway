// ============================================================================
// IMS 2.0 - Clinical Patient Intake Modal
// ============================================================================
// The clinical door for registering the person being examined AND capturing
// their refraction in one step. It now captures the SAME customer field set as
// the POS "Add Customer" flow — via the shared <CustomerIdentityFields> body and
// the shared buildCustomerCreatePayload — so both doors produce the IDENTICAL
// customer record (full parity, owner decision). On top of that shared identity
// section this modal keeps its clinical extras:
//   - "Reason for visit"
//   - "Capture prescription now" inline OD/OS refraction grid
//   - find-or-create-by-phone (searchByPhone then createCustomer; on "already
//     exists" it falls back to the found account)
//   - prescriptionApi.createPrescription (optional Rx)
//   - clinicalApi.addToQueue (issues the token)
//
// The person being examined is the account holder (the customer's Full Name) /
// their Primary patient. The backend seeds a Primary member from the account
// name+mobile when no patient rows are added, so the Rx + queue reliably attach
// to that account holder.
// ============================================================================

import { useEffect, useMemo, useState } from 'react';
import { Eye, X, Loader2, UserPlus } from 'lucide-react';
import { customerApi, clinicalApi, prescriptionApi } from '../../services/api';
import { useToast } from '../../context/ToastContext';
import { CustomerIdentityFields } from '../customers/CustomerIdentityFields';
import {
  buildCustomerCreatePayload,
  emptyCustomerFormData,
  type CustomerFormData,
} from '../../utils/customerPayload';
import { validateEyePair } from '../../constants/rxLimits';
import { RxPowerInput } from './RxPowerInput';

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

// --- Rx range validation (single source: constants/rxLimits.ts) -------------
// SPH -25..+25 (0.25) - CYL -6..+6 (0.25) - AXIS 1-180 whole - ADD +0.75..+4.00
// (0.25) - PD 40..80 (0.5). CYL<->AXIS paired. The backend rx_validation.py is
// the ultimate gate; this gives a fast client message on an obvious typo.
function validateEye(eye: EyeRx, label: string): string | null {
  return validateEyePair(
    {
      sph: eye.sph,
      cyl: eye.cyl,
      axis: eye.axis,
      add: eye.add,
      pd: eye.pd,
      va: eye.va,
    },
    label,
  );
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

  // Full customer identity (same shape + field set as the POS Add Customer door).
  const [form, setForm] = useState<CustomerFormData>(emptyCustomerFormData());
  const [mobileError, setMobileError] = useState<string | null>(null);
  const [gstVerified, setGstVerified] = useState<boolean | null>(null);
  // DPDP consent wording (fetched on open, stamped onto the created record).
  const [consentText, setConsentText] = useState('');
  const [consentVersion, setConsentVersion] = useState<string | undefined>();

  // Clinical extras.
  const [reason, setReason] = useState('Eye examination');
  const [captureRx, setCaptureRx] = useState(false);
  const [od, setOd] = useState<EyeRx>(emptyEye());
  const [os, setOs] = useState<EyeRx>(emptyEye());
  const [saving, setSaving] = useState(false);

  // Seed the name/phone field from the "couldn't find them" handoff + reset the
  // rest of the form each time the modal opens. Pull the current consent wording.
  useEffect(() => {
    if (!isOpen) return;
    const fresh = emptyCustomerFormData();
    const seed = (initialName || '').trim();
    if (/^\d{6,}$/.test(seed.replace(/\D/g, ''))) {
      fresh.mobileNumber = seed.replace(/\D/g, '').slice(-10);
    } else if (seed) {
      fresh.fullName = seed;
    }
    setForm(fresh);
    setMobileError(null);
    setGstVerified(null);
    setReason('Eye examination');
    setCaptureRx(false);
    setOd(emptyEye());
    setOs(emptyEye());
    customerApi.getConsentText?.()
      .then((r) => {
        if (r?.text) setConsentText(r.text);
        if (r?.version) setConsentVersion(r.version);
      })
      .catch(() => { /* keep default */ });
  }, [isOpen, initialName]);

  const handleClose = () => {
    if (saving) return;
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

  // The person being examined = the account holder (customer Full Name / mobile).
  const patientName = form.fullName.trim();
  const sanitizedMobile = useMemo(
    () => form.mobileNumber.replace(/\D/g, '').slice(-10),
    [form.mobileNumber],
  );
  const hasRx = captureRx && (eyeHasValue(od) || eyeHasValue(os));

  const handleSubmit = async () => {
    if (!storeId) {
      toast.error('No active store selected');
      return;
    }
    if (!patientName) {
      toast.error('Patient name is required');
      return;
    }
    if (sanitizedMobile.length !== 10) {
      setMobileError('Mobile number must contain 10 digits');
      toast.error('Mobile number must contain 10 digits');
      return;
    }
    setMobileError(null);
    if (form.customerType === 'B2B' && !gstVerified) {
      toast.error('Please verify the GST number first');
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
        // Same shared builder as POS + the Customers page -> identical record.
        const payload = buildCustomerCreatePayload({
          ...form,
          mobileNumber: sanitizedMobile,
          dataConsentTextVersion: consentVersion,
        });
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
        patientName,
        customerPhone: sanitizedMobile,
        customerId,
        age: calcAge(form.dateOfBirth),
        reason: reason || 'Eye examination',
      });

      toast.success(
        isExisting
          ? `${patientName} added to queue${prescriptionId ? ' with new Rx' : ''}`
          : `Patient created and added to queue${prescriptionId ? ' with Rx' : ''}`,
      );

      onComplete?.({ customerId, patientName, prescriptionId });
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
      <RxPowerInput
        kind="SPH" placeholder="SPH"
        value={eye.sph} onChange={(v) => setEyeField(key, 'sph', v)}
        className="input-field text-center text-sm py-1.5"
        aria-label={`${label} sphere`}
      />
      <RxPowerInput
        kind="CYL" placeholder="CYL"
        value={eye.cyl} onChange={(v) => setEyeField(key, 'cyl', v)}
        className="input-field text-center text-sm py-1.5"
        aria-label={`${label} cylinder`}
      />
      <RxPowerInput
        kind="AXIS" placeholder="AXIS"
        value={eye.axis} onChange={(v) => setEyeField(key, 'axis', v)}
        className="input-field text-center text-sm py-1.5"
        aria-label={`${label} axis`}
      />
      <RxPowerInput
        kind="ADD" placeholder="ADD"
        value={eye.add} onChange={(v) => setEyeField(key, 'add', v)}
        className="input-field text-center text-sm py-1.5"
        aria-label={`${label} add`}
      />
      <RxPowerInput
        kind="PD" placeholder="PD"
        value={eye.pd} onChange={(v) => setEyeField(key, 'pd', v)}
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
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-3xl max-h-[92vh] overflow-y-auto">
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
          {/* Shared customer identity — full parity with the POS Add Customer door */}
          <CustomerIdentityFields
            value={form}
            onChange={setForm}
            consentText={consentText}
            mobileError={mobileError}
            onMobileErrorClear={() => setMobileError(null)}
            onGstVerifiedChange={setGstVerified}
          />

          {/* Reason for visit */}
          <div className="border-t border-gray-100 pt-4">
            <label className="block text-sm font-medium text-gray-700 mb-1">Reason for visit</label>
            <input
              type="text" value={reason} onChange={(e) => setReason(e.target.value)}
              className="input-field"
            />
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
                  SPH -25 to +25 · CYL -6 to +6 (0.25 step) · AXIS 1-180 · ADD +0.75 to +4.00
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

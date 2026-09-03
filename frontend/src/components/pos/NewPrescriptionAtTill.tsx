// ============================================================================
// IMS 2.0 - Capture a paper prescription AT THE TILL (shared by both surfaces)
// ============================================================================
// A customer walks in holding a prescription from an outside doctor. Without
// this door the assistant has to abandon the bill, go to the Clinical screen,
// enter the Rx there, and come back - so it lived on the classic till and the
// new one only showed a message telling staff to go elsewhere.
//
// It is ONE component rather than a copy per surface because of what is inside
// it, not because of its size:
//
//   PATIENT SAFETY - a cylinder recorded with no axis CANNOT be ground. The
//   counter is ASKED for the axis behind an un-dismissable prompt; the POS
//   never invents one. An axis of 0 is a real reading and never lands there.
//
//   A BLANK IS NOT A ZERO - `powerOrNull`, never `String(x || 0)`. An empty
//   SPH/CYL/ADD box used to leave the counter as the string "0", asserting
//   that the patient needs no correction. A blank now travels as null and a
//   recorded 0 is preserved exactly.
//
//   PROVENANCE is stamped per eye as `axis_source`, never into `remarks` -
//   remarks is published to the OTP-gated customer portal and printed on the
//   patient's Rx card.
//
// Reasoning like that must not exist in two places where the copies can drift.

import { useEffect, useState } from 'react';
import { AlertTriangle, X } from 'lucide-react';
import { PrescriptionForm } from './PrescriptionForm';
import { prescriptionApi } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { powerOrNull, powerNumberOrNull } from '../../utils/rxPowerValue';
import {
  axisOrNull,
  axisPromptReason,
  axisSourceFor,
  eyesNeedingCounterAxis,
  validateCounterAxis,
  EYE_LABEL,
  type EyeKey,
} from '../../utils/rxAxisEntry';
import type { Prescription } from '../../types';

/** Who may save a prescription at the counter. It lives HERE, with the screen
 *  that enforces it, and POSLayout re-exports it so existing importers (and the
 *  axis-prompt test suite) are unaffected. Defining it in POSLayout while
 *  POSLayout imports this component would be a circular import. */
export const RX_SAVE_ROLES = ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST'] as const;

interface AxisPromptState {
  rxData: any;
  eyes: EyeKey[];
  values: Record<EyeKey, string>;
  errors: Record<EyeKey, string | null>;
}

export interface NewPrescriptionAtTillProps {
  /** Mounted only while true - the parent owns the open/closed decision. */
  isOpen: boolean;
  onClose: () => void;
  /** The POS store instance (both surfaces already hold one). */
  store: any;
  /** Fires whenever the AXIS prompt opens or closes.
   *
   *  The classic till switches its global keyboard map OFF while that prompt is
   *  up: with focus on the dialog's own button an Escape reached goBack() and
   *  walked the sale back a step mid-prompt, and a barcode scanner's trailing
   *  Enter reached goNext(). Neither may happen while a blocking clinical
   *  prompt is open. Moving the prompt into this component would have silently
   *  dropped that guard, so the state is reported upward instead. */
  onAxisPromptChange?: (open: boolean) => void;
}

export function NewPrescriptionAtTill({
  isOpen,
  onClose,
  store,
  onAxisPromptChange,
}: NewPrescriptionAtTillProps) {
  const { user } = useAuth();
  const toast = useToast();
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [axisPrompt, setAxisPrompt] = useState<AxisPromptState | null>(null);

  // Report the prompt's state to the parent from an EFFECT, never from inside
  // the state updater. React runs an updater during RENDER, so calling the
  // parent's setter there is a setState-in-render - React warns
  // "Cannot update a component while rendering a different component", and the
  // resulting double-render made the axis-prompt tests flaky (different tests
  // failing on consecutive runs). The effect fires after commit, which is when
  // the parent actually needs to know.
  useEffect(() => {
    onAxisPromptChange?.(Boolean(axisPrompt));
  }, [axisPrompt, onAxisPromptChange]);

  const setShowNewPrescription = (open: boolean) => {
    if (!open) onClose();
  };

  async function saveNewPrescription(rxData: any, counterAxisEyes: EyeKey[]) {
    setErrorMsg(null);
    try {
      const isOptometrist = user?.roles?.includes('OPTOMETRIST');
      const source = isOptometrist ? 'TESTED_AT_STORE' : 'FROM_DOCTOR';
      // Provenance is stamped PER EYE as `axis_source`, never into `remarks`:
      // remarks is published to the OTP-gated customer portal and printed on the
      // patient's Rx card, and no staff screen renders it. See rxAxisEntry.
      const result = await prescriptionApi.createPrescription({
        patient_id: store.patient?.id || store.customer?.id,
        customer_id: store.customer?.id,
        source,
        optometrist_id: isOptometrist ? user?.id : (user?.id || 'admin-override'),
        validity_months: 12,
        // `powerOrNull`, never `String(x || 0)`: an empty SPH / CYL / ADD box
        // used to leave the counter as the string "0", asserting that this
        // patient needs no correction / has no astigmatism / needs no reading
        // add. A blank now travels as null (EyeData declares these Optional),
        // and a recorded 0 is preserved exactly. `pd` keeps its own shape --
        // a PD of 0mm is anatomically impossible, so truthiness is safe there.
        right_eye: { sph: powerOrNull(rxData.sph_od), cyl: powerOrNull(rxData.cyl_od), axis: axisOrNull(rxData.axis_od), add: powerOrNull(rxData.add_od), pd: String(rxData.pd_od || ''), prism: rxData.prism_od || undefined, base: rxData.base_od || undefined, acuity: rxData.va_od || undefined, axis_source: axisSourceFor('od', counterAxisEyes) },
        left_eye: { sph: powerOrNull(rxData.sph_os), cyl: powerOrNull(rxData.cyl_os), axis: axisOrNull(rxData.axis_os), add: powerOrNull(rxData.add_os), pd: String(rxData.pd_os || ''), prism: rxData.prism_os || undefined, base: rxData.base_os || undefined, acuity: rxData.va_os || undefined, axis_source: axisSourceFor('os', counterAxisEyes) },
        ipd: rxData.ipd || undefined,
        lens_recommendation: rxData.lens_type || undefined,
        next_checkup: rxData.next_checkup || undefined,
        // First-class field (backend persists it); the remarks copy stays so
        // the printed Rx card keeps showing the doctor until the card reads
        // doctor_name directly.
        doctor_name: rxData.doctor_name || undefined,
        remarks: rxData.doctor_name ? `Dr. ${rxData.doctor_name}` : undefined,
      } as any);

      if (result?.prescription_id && rxData.photo_file) {
        // Fail-soft: the Rx photo is evidence, not a gate — a failed upload
        // must never lose the sale.
        try {
          await prescriptionApi.uploadPrescriptionPhoto(
            result.prescription_id,
            rxData.photo_file
          );
        } catch {
          toast.warning('Prescription saved, but the photo upload failed — retry from the Rx card.');
        }
      }

      if (result?.prescription_id) {
        store.setPrescription({
          id: result.prescription_id,
          patientId: store.patient?.id || '',
          customerId: store.customer?.id || '',
          storeId: store.store_id,
          testDate: new Date().toISOString(),
          // Same rule on the local echo the Rx panel reads back: this line
          // carried BOTH halves of the bug at once -- `sph || 0` invented a
          // plano for a blank, while `cyl || null` and `add || null` deleted a
          // recorded one. `pd` keeps truthiness: a PD of 0mm is impossible.
          rightEye: { sphere: powerNumberOrNull(rxData.sph_od), cylinder: powerNumberOrNull(rxData.cyl_od), axis: axisOrNull(rxData.axis_od), add: powerNumberOrNull(rxData.add_od), pd: rxData.pd_od || 0 },
          leftEye: { sphere: powerNumberOrNull(rxData.sph_os), cylinder: powerNumberOrNull(rxData.cyl_os), axis: axisOrNull(rxData.axis_os), add: powerNumberOrNull(rxData.add_os), pd: rxData.pd_os || 0 },
          status: 'COMPLETED',
          createdAt: new Date().toISOString(),
          updatedAt: new Date().toISOString(),
        } as Prescription);
        setErrorMsg(null);
        setShowNewPrescription(false);
      } else {
        setErrorMsg('Prescription saved but no ID returned. Try selecting from existing prescriptions.');
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setErrorMsg(msg || 'Network error -- check your connection and try again');
    }
  }

  // Confirm the counter-entered axis. Every prompted eye must hold a valid whole
  // degree (1-180) before anything is saved; a bad entry re-renders the prompt
  // with the problem and saves nothing.
  function confirmAxisPrompt() {
    if (!axisPrompt) return;
    const errors: Record<EyeKey, string | null> = { od: null, os: null };
    const accepted: Partial<Record<EyeKey, number>> = {};
    let ok = true;
    for (const eye of axisPrompt.eyes) {
      const { value, error } = validateCounterAxis(axisPrompt.values[eye], eye);
      if (error !== null || value === null) {
        errors[eye] = error;
        ok = false;
      } else {
        accepted[eye] = value;
      }
    }
    if (!ok) {
      setAxisPrompt({ ...axisPrompt, errors });
      return;
    }
    const merged = { ...axisPrompt.rxData };
    if (accepted.od !== undefined) merged.axis_od = accepted.od;
    if (accepted.os !== undefined) merged.axis_os = accepted.os;
    const eyes = axisPrompt.eyes;
    setAxisPrompt(null);
    void saveNewPrescription(merged, eyes);
  }

  if (!isOpen && !axisPrompt) return null;

  return (
    <>
      {isOpen && (

        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-2xl w-full max-w-2xl max-h-[90dvh] overflow-y-auto">
            <div className="p-4 border-b border-gray-200 flex items-center justify-between">
              <h3 className="font-semibold text-gray-900">New Prescription</h3>
              <button onClick={() => { setShowNewPrescription(false); setAxisPrompt(null); setErrorMsg(null); }} className="p-1 hover:bg-gray-100 rounded" aria-label="Close" title="Close"><X className="w-5 h-5" /></button>
            </div>
            {errorMsg && (
              <div className="mx-4 mt-4 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700 flex items-start gap-2">
                <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
                <div><p className="font-medium">Failed to save prescription</p><p className="text-xs mt-0.5">{errorMsg}</p></div>
                <button onClick={() => setErrorMsg(null)} className="ml-auto text-red-400 hover:text-red-600" aria-label="Dismiss error" title="Dismiss error"><X className="w-4 h-4" /></button>
              </div>
            )}
            <div className="p-4">
              <PrescriptionForm
                allowContactLens={false}
                // PATIENT SAFETY: hand the "cylinder but no axis" case to the
                // blocking prompt below instead of rejecting it with a transient
                // toast. Without this the modal is unreachable -- the form's own
                // validateEyePair returns before onSubmit ever fires -- and the
                // owner chose a prompt over a hard block deliberately.
                deferAxisPrompt
                onSubmit={async (rxData) => {
                  setErrorMsg(null);
                  // PATIENT SAFETY: a cylinder with no axis cannot be ground.
                  // Park the Rx and ask the counter for the axis -- POS never
                  // invents one. An axis of 0 is a real reading: it counts as
                  // present and never lands here.
                  const needsAxis = eyesNeedingCounterAxis(rxData);
                  if (needsAxis.length > 0) {
                    setAxisPrompt({
                      rxData,
                      eyes: needsAxis,
                      values: { od: '', os: '' },
                      errors: { od: null, os: null },
                    });
                    return;
                  }
                  await saveNewPrescription(rxData, []);
                }}
                onCancel={() => { setShowNewPrescription(false); setAxisPrompt(null); setErrorMsg(null); }}
              />
            </div>
          </div>
        </div>
      )}
      {axisPrompt && (
        <div
          className="fixed inset-0 bg-black/60 flex items-center justify-center z-[60] p-4"
          role="alertdialog"
          aria-modal="true"
          aria-labelledby="axis-prompt-title"
          onKeyDown={(e) => {
            if (e.key === 'Enter') e.preventDefault();
            // Escape is swallowed here as well as at the window handler, so the
            // dialog is inert even if it is ever rendered outside POSLayout.
            if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); }
          }}
        >
          <div className="bg-white rounded-xl shadow-2xl w-full max-w-md max-h-[90dvh] overflow-y-auto border border-gray-200">
            <div className="p-4 border-b border-gray-200 flex items-start gap-2">
              <AlertTriangle className="w-5 h-5 text-amber-600 flex-shrink-0 mt-0.5" />
              <div>
                <h3 id="axis-prompt-title" className="font-semibold text-gray-900">Axis needed before this prescription can be saved</h3>
                <p className="text-xs text-gray-500 mt-0.5">
                  Anyone at the counter can type it in. Check the prescription the customer brought, or ask the optometrist.
                </p>
              </div>
            </div>
            {/* Truthful about who can actually SAVE. create_prescription
                (routers/prescriptions.py) 403s every role outside
                SUPERADMIN / ADMIN / STORE_MANAGER / OPTOMETRIST, and the New
                Prescription entry has no role gate -- so a cashier could fill
                this in and hit a 403 with the sale stranded, the exact stall the
                prompt exists to avoid. We tell them BEFORE they type rather than
                widening a clinical write door. The server stays the only gate;
                this list mirrors it and must be updated with it. */}
            {!RX_SAVE_ROLES.some((r: string) => user?.roles?.includes(r as any)) && (
              <div className="mx-4 mt-4 p-3 bg-amber-50 border border-amber-300 rounded-lg text-xs text-amber-800">
                You can enter the axis here, but saving a new prescription needs a manager or optometrist. Ask one to finish it, or attach an existing prescription instead.
              </div>
            )}
            <div className="p-4 space-y-4">
              {axisPrompt.eyes.map((eye) => (
                <div key={eye}>
                  <p className="text-sm text-gray-700 mb-2">
                    {axisPromptReason(eye, eye === 'od' ? axisPrompt.rxData?.cyl_od : axisPrompt.rxData?.cyl_os)}
                  </p>
                  <label className="block text-xs font-medium text-gray-700 mb-1" htmlFor={`axis-prompt-${eye}`}>
                    {EYE_LABEL[eye]} axis (whole degrees, 1 to 180)
                  </label>
                  <input
                    id={`axis-prompt-${eye}`}
                    type="text"
                    inputMode="numeric"
                    autoComplete="off"
                    // Focus lands in the FIRST axis box, not on the barcode
                    // field behind the scrim -- otherwise a scanner burst is
                    // typed invisibly into the product search while a clinical
                    // prompt is up.
                    autoFocus={eye === axisPrompt.eyes[0]}
                    placeholder="e.g. 90"
                    aria-label={`${EYE_LABEL[eye]} axis`}
                    aria-invalid={axisPrompt.errors[eye] ? true : undefined}
                    value={axisPrompt.values[eye]}
                    onChange={(e) => setAxisPrompt((prev) => (prev ? {
                      ...prev,
                      values: { ...prev.values, [eye]: e.target.value },
                      errors: { ...prev.errors, [eye]: null },
                    } : prev))}
                    className="input-field text-center text-sm"
                  />
                  {axisPrompt.errors[eye] && (
                    <p className="mt-1 text-xs text-red-600">{axisPrompt.errors[eye]}</p>
                  )}
                </div>
              ))}
              <div className="bg-amber-50 border border-amber-300 rounded-lg p-3 text-xs text-amber-800">
                This axis will be recorded as entered at the counter, not measured by an optometrist. If you are not sure of it, go back and send the customer for an eye test instead of guessing.
              </div>
              <div className="flex gap-2 pt-1">
                <button
                  onClick={() => setAxisPrompt(null)}
                  className="flex-1 px-4 py-2 border border-gray-300 text-gray-700 rounded-lg text-sm hover:bg-gray-100"
                >
                  Back to the prescription
                </button>
                <button
                  onClick={confirmAxisPrompt}
                  className="flex-1 px-4 py-2 bg-bv-red-600 text-white rounded-lg text-sm font-semibold hover:bg-bv-red-700"
                >
                  Save axis and continue
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

export default NewPrescriptionAtTill;

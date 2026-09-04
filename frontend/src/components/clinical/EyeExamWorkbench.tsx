// ============================================================================
// IMS 2.0 - The eye examination, as a screen
// ============================================================================
// Owner (2026-09-04): "why is this screen still a pop up". This is the
// examination the 2026-08-30 design specified, built at last:
//
//   * a LEFT RAIL of the seven steps in clinical order, each done / current /
//     todo, so the whole test is visible at a glance;
//   * the step you are on in the CENTRE, with 44px monospace power boxes,
//     "Copy from auto-ref" / "Copy old Rx", and the drift guardrail UNDER the
//     field it is about;
//   * the prescription being built on the RIGHT, from EVERY step, against the
//     patient's last Rx, plus the staff-only internal note;
//   * a footer per step: Back, Save & pause (the test stays in the queue),
//     Continue -- and Complete test once the final Rx is reached.
//
// The brain (state, the ONE range validator, the write-body assembly) is
// useEyeExamDraft; the step bodies are the existing tab components. This file
// is layout and wiring only, and it is mounted by EyeExamPage -- never as a
// modal.

import { useEffect, useMemo, useState } from 'react';
import clsx from 'clsx';
import {
  AlertCircle,
  ArrowLeft,
  Calendar,
  Check,
  Lock,
  Monitor,
  Pause,
  Printer,
  User,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { resolveStoreIdentity, type StoreIdentity } from '../print/storeIdentity';
import { PrescriptionPrint, type PrescriptionPrintData } from './PrescriptionPrint';
// PATIENT SAFETY: a recorded 0 power is a finding, not a blank.
import { formatPowerOrDash, powerNumberOrNull } from '../../utils/rxPowerValue';
import { formatRxPower } from './RxPowerInput';
import type { EyeTestData, ExamStepId, PatientInfo, PowerReading, UploadedFile } from './eyeTestTypes';
import { EXAM_STEPS, VDU_OPTIONS } from './eyeTestTypes';
import type { StoredEyeTest } from './eyeTestHydrate';
import { useEyeExamDraft, type ExamDraft } from './useEyeExamDraft';
import { monthYear, powerDelta, DRIFT_THRESHOLD_D, type PreviousRx } from './rxDrift';
import { LensometerTab } from './LensometerTab';
import { SlitLampTab } from './SlitLampTab';
import { AutoRefTab } from './AutoRefTab';
import { SubjectiveRxTab } from './SubjectiveRxTab';
import { FinalRxTab } from './FinalRxTab';
import { UploadsTab } from './UploadsTab';
import { SoapNoteForm } from './SoapNoteForm';

export type ExamMode = 'exam' | 'amend';

export interface EyeExamWorkbenchProps {
  patient: PatientInfo;
  /** Queue token, when the exam was opened from the queue. */
  token?: string | null;
  optometristName: string;
  /** The stored test: a fresh header, a paused draft, or a completed exam. */
  initialTest?: StoredEyeTest | null;
  /** The patient's most recent earlier Rx (guardrail + "Against <month>"). */
  previousRx?: PreviousRx | null;
  /** ISO date of the patient's earliest Rx on file ("Wearing since"). */
  wearingSince?: string | null;
  /** 'exam': a test in progress (pause + complete). 'amend': correcting a completed one. */
  mode: ExamMode;
  /** Complete test (exam) / Save changes (amend). */
  onFinish: (data: EyeTestData) => Promise<void>;
  /** Save & pause: the test stays in the queue. Exam mode only. */
  onPause?: (data: EyeTestData) => Promise<void>;
  /** Leave without saving (back to the queue / prescriptions). */
  onBack: () => void;
}

const FINAL_IDX = EXAM_STEPS.findIndex((s) => s.id === 'final');

function hasPower(eye: Partial<PowerReading> | undefined): boolean {
  return (['sphere', 'cylinder', 'axis', 'add'] as const).some((k) => (eye?.[k] ?? '').trim() !== '');
}

/** The Rx being built toward: the final Rx once it has a power, else the
 *  subjective, else the auto-ref. The lensometer is the OLD Rx, never this. */
function rxSoFar(d: ExamDraft): { source: string | null; right: Partial<PowerReading>; left: Partial<PowerReading> } {
  const fin = {
    right: { ...d.finalRx.rightEye, add: d.finalRx.rightAdd || d.finalRx.rightEye.add },
    left: { ...d.finalRx.leftEye, add: d.finalRx.leftAdd || d.finalRx.leftEye.add },
  };
  if (hasPower(fin.right) || hasPower(fin.left)) return { source: 'final Rx', ...fin };
  if (hasPower(d.subjectiveRx.rightEye) || hasPower(d.subjectiveRx.leftEye)) {
    return { source: 'subjective refraction', right: d.subjectiveRx.rightEye, left: d.subjectiveRx.leftEye };
  }
  if (hasPower(d.autoRef.rightEye) || hasPower(d.autoRef.leftEye)) {
    return { source: 'auto-refraction', right: d.autoRef.rightEye, left: d.autoRef.leftEye };
  }
  return { source: null, right: {}, left: {} };
}

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ''
    : d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
}

function axisOrDash(v: string | undefined): string {
  return v && v.trim() !== '' ? v : '-';
}

export function EyeExamWorkbench({
  patient,
  token,
  optometristName,
  initialTest = null,
  previousRx = null,
  wearingSince = null,
  mode,
  onFinish,
  onPause,
  onBack,
}: EyeExamWorkbenchProps) {
  const { user } = useAuth();
  const { draft, patch, validate, toEyeTestData } = useEyeExamDraft(initialTest, optometristName);

  const currentIdx = Math.max(0, EXAM_STEPS.findIndex((s) => s.id === draft.step));
  // The furthest step reached: everything up to it is "done", beyond it "todo".
  // An amendment reopens a recorded exam, so every step has been done.
  const [reachedIdx, setReachedIdx] = useState(() => (mode === 'amend' ? EXAM_STEPS.length - 1 : currentIdx));
  const step = EXAM_STEPS[currentIdx];
  const prev = currentIdx > 0 ? EXAM_STEPS[currentIdx - 1] : null;
  const next = currentIdx < EXAM_STEPS.length - 1 ? EXAM_STEPS[currentIdx + 1] : null;
  const showFinish = mode === 'amend' || currentIdx >= FINAL_IDX;

  const [busy, setBusy] = useState(false);
  const [showPrint, setShowPrint] = useState(false);
  // The first out-of-range / un-grindable value found on ANY step. A persistent
  // banner rather than a toast: the clinician has to walk back to another step
  // to fix it, and a toast is gone by the time they get there.
  const [validationError, setValidationError] = useState<string | null>(null);

  // Resolve the issuing store + legal entity for the inline Rx print.
  const [storeIdentity, setStoreIdentity] = useState<StoreIdentity | null>(null);
  useEffect(() => {
    if (!user?.activeStoreId) return;
    let cancelled = false;
    resolveStoreIdentity(user.activeStoreId)
      .then((id) => { if (!cancelled) setStoreIdentity(id); })
      .catch(() => { if (!cancelled) setStoreIdentity(null); });
    return () => { cancelled = true; };
  }, [user?.activeStoreId]);

  const goTo = (id: ExamStepId) => {
    const idx = EXAM_STEPS.findIndex((s) => s.id === id);
    patch({ step: id });
    setReachedIdx((r) => Math.max(r, idx));
  };

  const submit = async (fn: (data: EyeTestData) => Promise<void>) => {
    const problem = validate();
    if (problem) {
      setValidationError(problem);
      return;
    }
    setValidationError(null);
    setBusy(true);
    try {
      await fn(toEyeTestData(patient.id));
    } finally {
      setBusy(false);
    }
  };

  const handleFileUpload = (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = event.target.files;
    if (!files) return;
    const added: UploadedFile[] = Array.from(files).map((file) => ({
      id: crypto.randomUUID(),
      name: file.name,
      type: file.type,
      size: file.size,
    }));
    patch({ uploads: [...draft.uploads, ...added] });
    event.target.value = '';
  };

  // ---- The printed card (unchanged rule: a recorded 0 prints as 0.00) -------
  const buildPrintData = (): PrescriptionPrintData => ({
    id: `eyetest-${Date.now()}`,
    patientName: patient.name,
    patientAge: patient.age ?? null,
    customerPhone: patient.phone,
    prescribedAt: draft.examDate,
    // PATIENT SAFETY -- the POWERS go through powerNumberOrNull, not
    // `parseFloat(x) || null`: `parseFloat("0") || null` is NULL, and a plano
    // sphere then printed as a DASH. AXIS and PD keep `parseFloat(x) || null`
    // DELIBERATELY -- 0 is not a meridian and 0mm is not a PD.
    rightEye: {
      sphere: powerNumberOrNull(draft.finalRx.rightEye.sphere),
      cylinder: powerNumberOrNull(draft.finalRx.rightEye.cylinder),
      axis: parseFloat(draft.finalRx.rightEye.axis) || null,
      add: powerNumberOrNull(draft.finalRx.rightAdd),
      pd: parseFloat(draft.finalRx.rightEye.pd) || null,
      va: draft.finalRx.rightEye.va || null,
    },
    leftEye: {
      sphere: powerNumberOrNull(draft.finalRx.leftEye.sphere),
      cylinder: powerNumberOrNull(draft.finalRx.leftEye.cylinder),
      axis: parseFloat(draft.finalRx.leftEye.axis) || null,
      add: powerNumberOrNull(draft.finalRx.leftAdd),
      pd: parseFloat(draft.finalRx.leftEye.pd) || null,
      va: draft.finalRx.leftEye.va || null,
    },
    pd: parseFloat(draft.finalRx.ipd) || null,
    lensRecommendation: draft.finalRx.lensType || null,
    // The Final-Rx REMARKS print; the internal note never does.
    notes: draft.finalRx.remarks || null,
    optometristName: draft.optometristName || null,
    validityMonths: 12,
  });
  const sv = storeIdentity?.store;
  const printStore = {
    storeName: sv?.storeName || sv?.storeCode || '',
    storeCode: sv?.storeCode || '',
    brand: sv?.brand || '',
    address: sv?.address || '',
    city: sv?.city || '',
    state: sv?.state || '',
    stateCode: sv?.stateCode || '',
    pincode: sv?.pincode || '',
    phone: (sv as { phone?: string } | undefined)?.phone,
    gstin: sv?.gstin as string | undefined,
  };

  // ---- Right panel data -----------------------------------------------------
  const soFar = useMemo(() => rxSoFar(draft), [draft]);
  const against = useMemo(() => {
    if (!previousRx) return null;
    const row = (side: 'Right' | 'Left') => {
      const p = side === 'Right' ? previousRx.rightEye : previousRx.leftEye;
      const n = side === 'Right' ? soFar.right : soFar.left;
      const delta = powerDelta(p.sph, n.sphere);
      return { side, from: formatPowerOrDash(p.sph), to: formatPowerOrDash(n.sphere), delta };
    };
    return [row('Right'), row('Left')];
  }, [previousRx, soFar]);
  const recall = draft.finalRx.nextCheckup
    ? monthYear(draft.finalRx.nextCheckup)
    : (() => {
        const d = new Date(draft.examDate || Date.now());
        d.setMonth(d.getMonth() + 12);
        return monthYear(d.toISOString());
      })();
  const autoRefCarried = draft.step === 'subjective' && (hasPower(draft.autoRef.rightEye) || hasPower(draft.autoRef.leftEye));

  const backLabel = prev
    ? `Back to ${prev.short}`
    : mode === 'amend' ? 'Back to prescriptions' : 'Back to queue';

  return (
    <div className="exam">
      {showPrint && (
        <PrescriptionPrint
          prescription={buildPrintData()}
          store={printStore}
          entity={storeIdentity?.entity ?? null}
          onClose={() => setShowPrint(false)}
        />
      )}

      {/* WHO is in the chair, always */}
      <div className="exam-top">
        <button type="button" className="btn ghost lg" onClick={onBack} aria-label={mode === 'amend' ? 'Back to prescriptions' : 'Back to queue'}>
          <ArrowLeft className="w-4 h-4" />
          {mode === 'amend' ? 'Prescriptions' : 'Queue'}
        </button>
        <span className="font-semibold">{mode === 'amend' ? 'Amend eye test' : 'Eye test'}</span>
        {token && <span className="chip">Token {token}</span>}
        <span className="grow" />
        <span className="font-semibold">{patient.name}</span>
        {patient.age != null && <span className="chip">{patient.age}y</span>}
        {previousRx ? (
          <span className="chip info">Last Rx {fmtDate(previousRx.date)}</span>
        ) : (
          <span className="chip">No earlier Rx</span>
        )}
        {wearingSince && <span className="chip warn">Wearing since {new Date(wearingSince).getFullYear()}</span>}
        <span className="grow" />
        <span className="chip" title="Examining optometrist">{draft.optometristName || optometristName || 'Optometrist'}</span>
        <button type="button" className="btn lg" onClick={() => setShowPrint(true)}>
          <Printer className="w-4 h-4" /> Print
        </button>
      </div>

      {/* Visit header: the four fields the exam record stores beside the steps */}
      <div className="card exam-visit">
        <div>
          <label htmlFor="exam-date" className="text-xs text-ink-4 flex items-center gap-1 mb-1">
            <Calendar className="w-3 h-3" /> Exam date
          </label>
          <input id="exam-date" type="date" value={draft.examDate} onChange={(e) => patch({ examDate: e.target.value })} className="input-field text-sm" />
        </div>
        <div>
          <label htmlFor="exam-optometrist" className="text-xs text-ink-4 flex items-center gap-1 mb-1">
            <User className="w-3 h-3" /> Optometrist
          </label>
          <input id="exam-optometrist" type="text" value={draft.optometristName} onChange={(e) => patch({ optometristName: e.target.value })} placeholder="Enter optometrist name" className="input-field text-sm" />
        </div>
        <div>
          <label htmlFor="exam-complaint" className="text-xs text-ink-4 flex items-center gap-1 mb-1">
            <AlertCircle className="w-3 h-3" /> Chief complaint
          </label>
          <input id="exam-complaint" type="text" value={draft.chiefComplaint} onChange={(e) => patch({ chiefComplaint: e.target.value })} placeholder="e.g., Blurred vision, headache" className="input-field text-sm" />
        </div>
        <div>
          <label htmlFor="vdu-usage" className="text-xs text-ink-4 flex items-center gap-1 mb-1">
            <Monitor className="w-3 h-3" /> VDU usage
          </label>
          <select id="vdu-usage" value={draft.vduUsage} onChange={(e) => patch({ vduUsage: e.target.value })} className="input-field text-sm">
            {VDU_OPTIONS.map((opt) => <option key={opt} value={opt}>{opt}</option>)}
          </select>
        </div>
      </div>

      <div className="exam-grid">
        {/* LEFT: the examination sequence */}
        <aside className="exam-rail" aria-label="Examination steps">
          <span className="eyebrow" style={{ padding: '0 12px 6px' }}>Examination</span>
          {EXAM_STEPS.map((s, i) => {
            const state = i === currentIdx ? 'on' : i <= reachedIdx ? 'done' : 'todo';
            return (
              <button
                key={s.id}
                type="button"
                className={clsx('exam-step', state)}
                aria-current={state === 'on' ? 'step' : undefined}
                onClick={() => goTo(s.id)}
              >
                <span className="dot" aria-hidden="true">
                  {state === 'done' ? <Check className="w-3 h-3" /> : i + 1}
                </span>
                {s.label}
                {s.hint && <span className="hint" aria-hidden="true">{s.hint}</span>}
              </button>
            );
          })}
          <div className="card exam-aside" style={{ marginTop: 8 }}>
            <span className="eyebrow">Why the order stays</span>
            <div className="text-xs text-ink-3 mt-1.5">
              Lensometer, slit lamp, auto-ref, subjective, final: this is the
              examination itself, not a set of tabs. The rail shows the whole
              test at a glance.
            </div>
          </div>
        </aside>

        {/* CENTRE: the step you are on */}
        <section className="card exam-body" aria-labelledby="exam-step-title">
          <div className="flex items-baseline gap-2.5 flex-wrap">
            <h2 id="exam-step-title" className="text-[17px] font-semibold m-0">{step.title}</h2>
            {step.id === 'lensometer' && <span className="chip">the patient&apos;s current glasses</span>}
            {autoRefCarried && <span className="chip info">Auto-ref carried in: adjust from there</span>}
            <span className="chip" style={{ marginLeft: 'auto' }}>step {currentIdx + 1} of {EXAM_STEPS.length}</span>
          </div>

          {step.id === 'lensometer' && (
            <LensometerTab data={draft.lensometer} onChange={(v) => patch({ lensometer: v })} />
          )}
          {step.id === 'slitlamp' && (
            <SlitLampTab data={draft.slitLamp} onChange={(v) => patch({ slitLamp: v })} />
          )}
          {step.id === 'autoref' && (
            <AutoRefTab data={draft.autoRef} onChange={(v) => patch({ autoRef: v })} />
          )}
          {step.id === 'subjective' && (
            <SubjectiveRxTab
              data={draft.subjectiveRx}
              onChange={(v) => patch({ subjectiveRx: v })}
              copyFrom={{ autoRef: draft.autoRef, lensometer: draft.lensometer }}
              drift={{ previousRx, age: patient.age }}
            />
          )}
          {step.id === 'final' && (
            <FinalRxTab
              data={draft.finalRx}
              onChange={(v) => patch({ finalRx: v })}
              subjectiveRxData={draft.subjectiveRx}
              findings={draft.clinicalFindings}
              onFindingsChange={(v) => patch({ clinicalFindings: v })}
              drift={{ previousRx, age: patient.age }}
            />
          )}
          {step.id === 'uploads' && (
            <UploadsTab
              uploads={draft.uploads}
              onUpload={handleFileUpload}
              onRemove={(id) => patch({ uploads: draft.uploads.filter((f) => f.id !== id) })}
            />
          )}
          {step.id === 'soap' && (
            <SoapNoteForm data={draft.soapNote} onChange={(v) => patch({ soapNote: v })} />
          )}

          {validationError && (
            <div role="alert" className="p-3 bg-err-50 border border-err/30 rounded-lg text-sm text-err flex items-start gap-2">
              <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
              <span>{validationError}</span>
            </div>
          )}

          <div className="exam-foot">
            <button type="button" className="btn lg" onClick={() => (prev ? goTo(prev.id) : onBack())} disabled={busy}>
              <ArrowLeft className="w-4 h-4" /> {backLabel}
            </button>
            <span className="grow" />
            {mode === 'exam' && onPause && (
              <button type="button" className="btn lg" onClick={() => submit(onPause)} disabled={busy}>
                <Pause className="w-4 h-4" /> Save &amp; pause
              </button>
            )}
            {next && (
              <button type="button" className={clsx('btn lg', !showFinish && 'primary')} onClick={() => goTo(next.id)} disabled={busy}>
                Continue to {next.short}
              </button>
            )}
            {showFinish && (
              <button type="button" className="btn lg primary" onClick={() => submit(onFinish)} disabled={busy}>
                {busy ? (
                  <span className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                ) : (
                  <Check className="w-4 h-4" />
                )}
                {mode === 'amend' ? 'Save changes' : 'Complete test'}
              </button>
            )}
          </div>
        </section>

        {/* RIGHT: the number being built toward, visible from EVERY step */}
        <aside className="exam-right">
          <div className="card">
            <div className="flex items-baseline">
              <span className="eyebrow">Prescription so far</span>
              <span className="grow" />
              <span className="chip">step {currentIdx + 1} of {EXAM_STEPS.length}</span>
            </div>
            <table className="exam-rx mt-2.5 w-full" aria-label="Prescription so far">
              <thead>
                <tr><th /><th>SPH</th><th>CYL</th><th>AXIS</th><th>ADD</th></tr>
              </thead>
              <tbody>
                {(['right', 'left'] as const).map((side) => {
                  const e = soFar[side];
                  return (
                    <tr key={side}>
                      <td className="text-ink-4">{side === 'right' ? 'R' : 'L'}</td>
                      <td>{formatPowerOrDash(e.sphere)}</td>
                      <td>{formatPowerOrDash(e.cylinder)}</td>
                      <td>{axisOrDash(e.axis)}</td>
                      <td>{formatPowerOrDash(e.add)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <div className="text-xs text-ink-4 mt-2.5">
              {soFar.source
                ? `From the ${soFar.source}. This is what will be billed and dispensed.`
                : 'Fills in as the refraction is recorded. This is what will be billed and dispensed.'}
            </div>
          </div>

          {against && previousRx && (
            <div className="card">
              <span className="eyebrow">Against {monthYear(previousRx.date)}</span>
              <div className="mt-2 flex flex-col gap-1.5 mono">
                {against.map((r) => (
                  <div key={r.side} className="flex items-center gap-2">
                    <span className="text-ink-4 w-6">{r.side === 'Right' ? 'R' : 'L'}</span>
                    <span>{r.from} &rarr; {r.to}</span>
                    {r.delta !== null && (
                      <span className={clsx('chip ml-auto', Math.abs(r.delta) > DRIFT_THRESHOLD_D && 'warn')}>
                        {formatRxPower(String(r.delta), 'SPH')}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="card">
            <span className="eyebrow">{mode === 'amend' ? 'When you save' : 'When the test closes'}</span>
            <ul className="mt-2 flex flex-col gap-1.5 text-[12.5px] text-ink-2 list-disc pl-4 m-0">
              {mode === 'amend' ? (
                <>
                  <li>The recorded exam is corrected; the previous values stay on the record</li>
                  <li>The prescription the lab and the patient read is updated to match</li>
                </>
              ) : (
                <>
                  <li>Prescription saved to {patient.name}</li>
                  <li>Recall set for <strong>{recall}</strong></li>
                  <li>Sent to the floor with your recommendation</li>
                </>
              )}
            </ul>
          </div>

          {/* STAFF-ONLY. Never printed, never sent, never in the customer portal. */}
          <div className="card exam-internal">
            <div className="flex items-center gap-1.5">
              <Lock className="w-3.5 h-3.5 text-ink-4" aria-hidden="true" />
              <span className="eyebrow">Internal note: staff only</span>
            </div>
            <textarea
              aria-label="Internal note (staff only)"
              className="input-field mt-2 h-20 resize-none"
              value={draft.internalNote}
              onChange={(e) => patch({ internalNote: e.target.value })}
              placeholder="What the floor and the workshop should know before they sell or grind."
            />
            <div className="mt-2 flex items-center gap-1.5 flex-wrap">
              <span className="chip">Seen by: optometrist</span>
              <span className="chip">sales floor</span>
              <span className="chip">workshop</span>
            </div>
            <div className="mt-2 text-[11.5px] text-ink-4 leading-relaxed">
              Never printed on the Rx card, never on the invoice, never in the
              customer portal or a WhatsApp send.
            </div>
          </div>

          {mode === 'exam' && (
            <div className="card exam-aside">
              <span className="eyebrow">Not lost if interrupted</span>
              <div className="text-xs text-ink-3 mt-1.5">
                Save &amp; pause keeps this test in the queue. Walking away to
                answer the phone does not cost the exam.
              </div>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}

export default EyeExamWorkbench;

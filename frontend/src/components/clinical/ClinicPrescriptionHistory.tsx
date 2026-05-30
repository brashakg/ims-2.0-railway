// ============================================================================
// IMS 2.0 - Clinic Prescription History + Edit (per customer)
// ============================================================================
// One place in the Clinic module to:
//   * VIEW a customer's past prescriptions, grouped by family member (patient),
//     each annotated with validity / expiry  (bug #3 — old-Rx viewing)
//   * EDIT an existing prescription via PUT /prescriptions/{id}  (bug #1)
//   * Add a clearly-labelled NEW prescription that starts BLANK, so "new" is
//     never confused with "editing the last record"  (bug #2)
//   * PRINT the A5 Rx card
//
// Reuses GET /prescriptions/family/{customer_id} (already grouped by patient)
// and the shared PrescriptionForm for both create and edit.

import { useState, useEffect, useCallback } from 'react';
import {
  X, Users, User, Eye, Calendar, Plus, Pencil, Printer,
  Loader2, AlertTriangle, CheckCircle, Clock,
} from 'lucide-react';
import clsx from 'clsx';
import { prescriptionApi, clinicalApi } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { PrescriptionForm } from '../pos/PrescriptionForm';

interface FamilyMember {
  patient_id: string | null;
  name: string | null;
  relation: string | null;
  prescription_count: number;
  valid_count: number;
  prescriptions: any[];
}

interface ClinicPrescriptionHistoryProps {
  isOpen: boolean;
  onClose: () => void;
  customerId: string;
  customerName?: string;
  /** The patient queued for this visit, pre-selected for a "New prescription". */
  defaultPatientId?: string;
}

// Backend Rx doc (nested right_eye/left_eye, snake_case) -> the flat field
// shape PrescriptionForm consumes as initialData (sph_od/cyl_od/... + dates).
function rxToFormInitial(rx: any): Record<string, any> {
  const re = rx?.right_eye || rx?.rightEye || {};
  const le = rx?.left_eye || rx?.leftEye || {};
  const num = (v: any) => {
    if (v === undefined || v === null || v === '') return undefined;
    const n = Number(v);
    return Number.isFinite(n) ? n : undefined;
  };
  const str = (v: any) => (v === undefined || v === null || v === '' ? undefined : String(v));
  return {
    sph_od: num(re.sph ?? re.sphere), cyl_od: num(re.cyl ?? re.cylinder), axis_od: num(re.axis),
    add_od: num(re.add ?? re.addition), pd_od: num(re.pd),
    va_od: str(re.acuity ?? re.va), prism_od: str(re.prism), base_od: str(re.base),
    sph_os: num(le.sph ?? le.sphere), cyl_os: num(le.cyl ?? le.cylinder), axis_os: num(le.axis),
    add_os: num(le.add ?? le.addition), pd_os: num(le.pd),
    va_os: str(le.acuity ?? le.va), prism_os: str(le.prism), base_os: str(le.base),
    ipd: str(rx?.ipd),
    lens_type: str(rx?.lens_recommendation),
    next_checkup: str(rx?.next_checkup),
  };
}

function rxValidity(rx: any): { expired: boolean; daysLeft: number | null; label: string } {
  // Server already annotates is_valid + expiry_date on the family payload.
  const expiryRaw = rx?.expiry_date || rx?.expiryDate;
  if (rx?.is_valid === false) return { expired: true, daysLeft: null, label: 'Expired' };
  if (!expiryRaw) return { expired: false, daysLeft: null, label: 'Valid' };
  const expiry = new Date(expiryRaw);
  if (isNaN(expiry.getTime())) return { expired: false, daysLeft: null, label: 'Valid' };
  const days = Math.ceil((expiry.getTime() - Date.now()) / (1000 * 60 * 60 * 24));
  if (days < 0) return { expired: true, daysLeft: days, label: 'Expired' };
  if (days <= 30) return { expired: false, daysLeft: days, label: `Expires in ${days}d` };
  return { expired: false, daysLeft: days, label: 'Valid' };
}

function fmtDate(d: any): string {
  const raw = d?.test_date || d?.testDate || d?.prescription_date || d?.created_at;
  if (!raw) return '—';
  const dt = new Date(raw);
  return isNaN(dt.getTime()) ? '—' : dt.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
}

function fmtPower(v: any): string {
  if (v === undefined || v === null || v === '') return '-';
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  return n >= 0 ? `+${n.toFixed(2)}` : n.toFixed(2);
}

export function ClinicPrescriptionHistory({
  isOpen,
  onClose,
  customerId,
  customerName,
  defaultPatientId,
}: ClinicPrescriptionHistoryProps) {
  const { user } = useAuth();
  const toast = useToast();

  const [members, setMembers] = useState<FamilyMember[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Form modal state: either creating (editingRx === null) or editing an Rx.
  const [formOpen, setFormOpen] = useState(false);
  const [formPatientId, setFormPatientId] = useState<string | null>(null);
  const [editingRx, setEditingRx] = useState<any | null>(null);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!customerId) return;
    setIsLoading(true);
    setError(null);
    try {
      const res = await prescriptionApi.getFamilyRx(customerId);
      setMembers((res.members || []) as FamilyMember[]);
    } catch {
      setError('Failed to load prescriptions. Please try again.');
      setMembers([]);
    } finally {
      setIsLoading(false);
    }
  }, [customerId]);

  useEffect(() => {
    if (isOpen) load();
  }, [isOpen, load]);

  if (!isOpen) return null;

  const openNew = (patientId: string | null) => {
    setEditingRx(null);
    setFormPatientId(patientId);
    setFormError(null);
    setFormOpen(true);
  };

  const openEdit = (patientId: string | null, rx: any) => {
    setEditingRx(rx);
    setFormPatientId(patientId);
    setFormError(null);
    setFormOpen(true);
  };

  const printA5 = async (rx: any) => {
    const rxId = rx?.prescription_id || rx?.id;
    if (!rxId) {
      toast.error('Cannot print: prescription id missing.');
      return;
    }
    try {
      const html = await clinicalApi.getPrescriptionPrintHtml(rxId);
      const w = window.open('', '_blank');
      if (w) {
        w.document.write(html);
        w.document.close();
        w.focus();
      } else {
        toast.error('Pop-up blocked. Please allow pop-ups to print.');
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to print prescription.');
    }
  };

  // Submit handler for PrescriptionForm — branches on create vs edit.
  const handleFormSubmit = async (rxData: any) => {
    setSaving(true);
    setFormError(null);
    try {
      const isOptometrist = user?.roles?.includes('OPTOMETRIST');
      if (editingRx) {
        // EDIT — PUT only the mutable fields (identity is immutable server-side).
        const rxId = editingRx.prescription_id || editingRx.id;
        await prescriptionApi.updatePrescription(rxId, {
          ...rxData,
          ipd: rxData.ipd || undefined,
          lens_recommendation: rxData.lens_type || undefined,
          next_checkup: rxData.next_checkup || undefined,
        });
        toast.success('Prescription updated');
      } else {
        // NEW — fresh record, never touches an existing one.
        const source = isOptometrist ? 'TESTED_AT_STORE' : 'FROM_DOCTOR';
        await prescriptionApi.createPrescription({
          ...rxData,
          patient_id: formPatientId || customerId,
          customer_id: customerId,
          source,
          optometrist_id: isOptometrist ? user?.id : (user?.id || 'admin-override'),
          validity_months: 12,
          ipd: rxData.ipd || undefined,
          lens_recommendation: rxData.lens_type || undefined,
          next_checkup: rxData.next_checkup || undefined,
          remarks: rxData.doctor_name ? `Dr. ${rxData.doctor_name}` : undefined,
        });
        toast.success('New prescription created');
      }
      setFormOpen(false);
      setEditingRx(null);
      await load();
    } catch (err: any) {
      const detail = err?.response?.data?.detail || err?.message || 'Failed to save prescription';
      setFormError(typeof detail === 'string' ? detail : 'Failed to save prescription');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-3xl max-h-[92vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-gray-200">
          <div className="flex items-center gap-3">
            <div className="w-11 h-11 bg-teal-100 rounded-full flex items-center justify-center">
              <Eye className="w-6 h-6 text-teal-600" />
            </div>
            <div>
              <h2 className="text-lg font-bold text-gray-900">Prescriptions &amp; History</h2>
              <p className="text-sm text-gray-500">{customerName || 'Customer'} · grouped by family member</p>
            </div>
          </div>
          <button onClick={onClose} className="p-2 hover:bg-gray-100 rounded-lg transition-colors">
            <X className="w-5 h-5 text-gray-500" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-4 space-y-5">
          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="w-8 h-8 animate-spin text-teal-600" />
            </div>
          ) : error ? (
            <div className="flex flex-col items-center justify-center py-12 text-red-500">
              <AlertTriangle className="w-10 h-10 mb-2 opacity-60" />
              <p>{error}</p>
              <button onClick={load} className="mt-3 btn-outline">Retry</button>
            </div>
          ) : members.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 text-gray-500">
              <Users className="w-10 h-10 mb-2 opacity-50" />
              <p>No family members on this account.</p>
              <button onClick={() => openNew(defaultPatientId || customerId)} className="mt-3 btn-primary flex items-center gap-2">
                <Plus className="w-4 h-4" /> New prescription
              </button>
            </div>
          ) : (
            members.map((member) => (
              <div key={member.patient_id || 'unlinked'} className="border border-gray-200 rounded-lg overflow-hidden">
                {/* Member header */}
                <div className="flex items-center justify-between px-4 py-3 bg-gray-50 border-b border-gray-200">
                  <div className="flex items-center gap-3">
                    <div className="w-9 h-9 rounded-full bg-blue-100 flex items-center justify-center">
                      <User className="w-5 h-5 text-blue-600" />
                    </div>
                    <div>
                      <p className="font-medium text-gray-900">{member.name || 'Unlinked patient'}</p>
                      <p className="text-xs text-gray-500">
                        {member.relation || 'Patient'} · {member.prescription_count} Rx
                        {member.valid_count > 0 && <span className="text-green-600"> · {member.valid_count} valid</span>}
                      </p>
                    </div>
                  </div>
                  <button
                    onClick={() => openNew(member.patient_id)}
                    className="btn sm primary flex items-center gap-1"
                    title="Start a fresh, blank prescription for this patient"
                  >
                    <Plus className="w-4 h-4" /> New prescription
                  </button>
                </div>

                {/* Rx list (read-only history; Edit/Print are explicit actions) */}
                {member.prescriptions.length === 0 ? (
                  <div className="px-4 py-6 text-center text-sm text-gray-500">
                    No prescriptions yet for this patient.
                  </div>
                ) : (
                  <div className="divide-y divide-gray-100">
                    {member.prescriptions.map((rx) => {
                      const v = rxValidity(rx);
                      const re = rx.right_eye || rx.rightEye || {};
                      const le = rx.left_eye || rx.leftEye || {};
                      return (
                        <div key={rx.prescription_id || rx.id} className="px-4 py-3">
                          <div className="flex items-start justify-between gap-3 mb-2">
                            <div className="flex items-center gap-2 text-sm">
                              <Calendar className="w-4 h-4 text-gray-400" />
                              <span className="font-medium text-gray-900">{fmtDate(rx)}</span>
                              {rx.rx_kind === 'CONTACT_LENS' && (
                                <span className="px-2 py-0.5 text-xs bg-purple-100 text-purple-700 rounded">Contact Lens</span>
                              )}
                            </div>
                            <span
                              className={clsx(
                                'flex items-center gap-1 text-xs font-medium',
                                v.expired ? 'text-red-600' : v.daysLeft !== null && v.daysLeft <= 30 ? 'text-amber-600' : 'text-green-600',
                              )}
                            >
                              {v.expired ? <AlertTriangle className="w-3 h-3" /> : v.daysLeft !== null && v.daysLeft <= 30 ? <Clock className="w-3 h-3" /> : <CheckCircle className="w-3 h-3" />}
                              {v.label}
                            </span>
                          </div>

                          {/* Powers (read-only) */}
                          <div className="grid grid-cols-2 gap-3 text-xs mb-2">
                            <div className="bg-gray-50 rounded p-2">
                              <span className="text-gray-500">OD: </span>
                              <span className="font-medium">{fmtPower(re.sph ?? re.sphere)} / {fmtPower(re.cyl ?? re.cylinder)} / {re.axis ?? '-'}</span>
                            </div>
                            <div className="bg-gray-50 rounded p-2">
                              <span className="text-gray-500">OS: </span>
                              <span className="font-medium">{fmtPower(le.sph ?? le.sphere)} / {fmtPower(le.cyl ?? le.cylinder)} / {le.axis ?? '-'}</span>
                            </div>
                          </div>

                          {/* Per-Rx actions */}
                          <div className="flex items-center gap-3">
                            <button
                              onClick={() => openEdit(member.patient_id, rx)}
                              className="text-sm text-teal-600 hover:text-teal-700 flex items-center gap-1"
                            >
                              <Pencil className="w-4 h-4" /> Edit
                            </button>
                            <button
                              onClick={() => printA5(rx)}
                              className="text-sm text-gray-500 hover:text-teal-600 flex items-center gap-1"
                            >
                              <Printer className="w-4 h-4" /> Print / View
                            </button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            ))
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 p-4 border-t border-gray-200 bg-gray-50">
          <button onClick={onClose} className="btn-outline">Close</button>
        </div>
      </div>

      {/* Create / Edit form (shared PrescriptionForm). For a NEW Rx, initialData
          is undefined so the form opens BLANK — "new" is never "edit-last". */}
      {formOpen && (
        <div className="fixed inset-0 z-[60]">
          {formError && (
            <div className="fixed top-4 left-1/2 -translate-x-1/2 z-[70] max-w-md w-full px-3">
              <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700 flex items-start gap-2 shadow">
                <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
                <div>
                  <p className="font-medium">{editingRx ? 'Failed to update prescription' : 'Failed to create prescription'}</p>
                  <p className="text-xs mt-0.5">{formError}</p>
                </div>
                <button onClick={() => setFormError(null)} className="ml-auto text-red-400 hover:text-red-600"><X className="w-4 h-4" /></button>
              </div>
            </div>
          )}
          <PrescriptionForm
            allowContactLens={false}
            initialData={editingRx ? rxToFormInitial(editingRx) : undefined}
            submitLabel={editingRx ? 'Save changes' : 'Save prescription'}
            onSubmit={handleFormSubmit}
            onCancel={() => { if (!saving) { setFormOpen(false); setEditingRx(null); setFormError(null); } }}
          />
        </div>
      )}
    </div>
  );
}

export default ClinicPrescriptionHistory;

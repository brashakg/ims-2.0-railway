// ============================================================================
// IMS 2.0 - Prescription Select Modal for POS
// ============================================================================
// Two-step attach flow:
//   STEP 1 - pick the PATIENT (family member) on the customer account
//   STEP 2 - pick the specific PRESCRIPTION for that patient
// Reuses GET /prescriptions/family/{customer_id} (grouped by patient) so the
// cashier always attaches the right person's Rx, not just "the latest on the
// account". If the account has a single patient it's auto-selected, but the Rx
// list is still shown so the operator confirms which prescription to attach.

import { useState, useEffect } from 'react';
import { X, Eye, AlertTriangle, Check, Calendar, User, Users, Clock, FileText, Plus, ChevronLeft, Stethoscope } from 'lucide-react';
import type { Prescription, Patient } from '../../types';
import { prescriptionApi } from '../../services/api';
import { mapRx } from '../../services/api/sales';
import { handoffsApi, type ClinicalHandover } from '../../services/api/handoffs';

interface FamilyMember {
  patient_id: string | null;
  name: string | null;
  relation: string | null;
  prescription_count: number;
  valid_count: number;
  prescriptions: Prescription[];
}

interface PrescriptionSelectModalProps {
  onClose: () => void;
  onSelect: (prescription: Prescription) => void;
  onCreateNew: () => void;
  patient: Patient | null;
  customerId: string;
  currentPrescriptionId?: string;
}

export function PrescriptionSelectModal({
  onClose,
  onSelect,
  onCreateNew,
  patient,
  customerId,
  currentPrescriptionId,
}: PrescriptionSelectModalProps) {
  const [members, setMembers] = useState<FamilyMember[]>([]);
  const [selectedPatientId, setSelectedPatientId] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  // F50: active clinical->retail handovers for this customer. Read-only advisory
  // surface ("From doctor today") — does NOT change selection / order / payment.
  const [handovers, setHandovers] = useState<ClinicalHandover[]>([]);

  useEffect(() => {
    if (customerId) {
      loadFamily();
      loadHandovers();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [customerId]);

  const loadHandovers = async () => {
    if (!customerId) return;
    try {
      const res = await handoffsApi.listClinicalInbox();
      // Scope to THIS customer client-side (the endpoint is already store + recipient scoped).
      setHandovers((res.handoffs || []).filter((h) => h.customer_id === customerId && !h.mark_served));
    } catch {
      // Fail-soft: a missing handover surface must never break the POS Rx picker.
      setHandovers([]);
    }
  };

  const loadFamily = async () => {
    if (!customerId) return;
    setIsLoading(true);
    setErrorMsg(null);
    try {
      const res = await prescriptionApi.getFamilyRx(customerId);
      const mapped: FamilyMember[] = (res.members || []).map((m) => ({
        patient_id: m.patient_id,
        name: m.name,
        relation: m.relation,
        prescription_count: m.prescription_count,
        valid_count: m.valid_count,
        // Normalise each Rx to the camelCase Prescription shape onSelect expects.
        prescriptions: (m.prescriptions || []).map(mapRx) as Prescription[],
      }));
      setMembers(mapped);

      // Pre-select: the patient passed in from POS (if it matches a member),
      // otherwise the lone member when there's only one. Always still show the
      // Rx list so the operator confirms which prescription to attach.
      const preferred =
        (patient?.id && mapped.find((m) => m.patient_id === patient.id)?.patient_id) ||
        (mapped.length === 1 ? mapped[0].patient_id : null);
      setSelectedPatientId(preferred ?? null);
    } catch {
      setErrorMsg('Failed to load prescriptions. Please try again.');
      setMembers([]);
    } finally {
      setIsLoading(false);
    }
  };

  const formatPower = (value: number | null | undefined) => {
    if (value === null || value === undefined) return '-';
    return value >= 0 ? `+${value.toFixed(2)}` : value.toFixed(2);
  };

  // The /family payload already annotates each Rx with expiry_date + is_valid
  // (mapRx preserves them as snake_case). Prefer those; fall back to the
  // testDate + validity recompute for any row that lacks them.
  const expiryDateOf = (prescription: any): Date => {
    const serverExpiry = prescription.expiry_date || prescription.expiryDate;
    if (serverExpiry) {
      const d = new Date(serverExpiry);
      if (!isNaN(d.getTime())) return d;
    }
    const testDate = new Date(prescription.testDate);
    const validityMonths = prescription.validityMonths || 12;
    const expiryDate = new Date(testDate);
    expiryDate.setMonth(expiryDate.getMonth() + validityMonths);
    return expiryDate;
  };

  const isExpired = (prescription: any) => {
    if (prescription.is_valid === false) return true;
    return new Date() > expiryDateOf(prescription);
  };

  const getDaysUntilExpiry = (prescription: any) => {
    const diff = expiryDateOf(prescription).getTime() - new Date().getTime();
    return Math.ceil(diff / (1000 * 60 * 60 * 24));
  };

  const formatDate = (dateString: string) => {
    if (!dateString) return '—';
    const d = new Date(dateString);
    if (isNaN(d.getTime())) return '—';
    return d.toLocaleDateString('en-IN', {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
    });
  };

  const selectedMember = members.find((m) => m.patient_id === selectedPatientId) || null;

  // F50: the active handover for the patient currently in view (if any). Matches
  // on patient_id; falls back to a customer-level match for unlinked patients.
  const activeHandover =
    handovers.find((h) => selectedPatientId && h.patient_id === selectedPatientId) ||
    (selectedPatientId ? null : handovers[0] || null);

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl w-full max-w-2xl max-h-[90vh] overflow-hidden flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-gray-200 bg-gray-50">
          <div className="flex items-center gap-3">
            {/* Back to patient picker from the Rx list (only when more than one
                patient exists — a single-patient account has nothing to go back to). */}
            {selectedPatientId && members.length > 1 && (
              <button
                onClick={() => setSelectedPatientId(null)}
                className="p-2 hover:bg-gray-200 rounded-lg transition-colors"
                title="Back to patient list"
              >
                <ChevronLeft className="w-5 h-5 text-gray-500" />
              </button>
            )}
            <div className="p-2 bg-blue-100 rounded-lg">
              {selectedPatientId ? <Eye className="w-5 h-5 text-blue-600" /> : <Users className="w-5 h-5 text-blue-600" />}
            </div>
            <div>
              <h2 className="text-lg font-semibold text-gray-900">
                {selectedPatientId ? 'Select Prescription' : 'Select Patient'}
              </h2>
              <p className="text-xs text-gray-500">
                {selectedPatientId
                  ? `for ${selectedMember?.name || 'patient'}${selectedMember?.relation ? ` · ${selectedMember.relation}` : ''}`
                  : 'Which family member is this prescription for?'}
              </p>
            </div>
          </div>
          <button onClick={onClose} className="p-2 hover:bg-gray-200 rounded-lg transition-colors">
            <X className="w-5 h-5 text-gray-500" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-4">
          {!customerId ? (
            <div className="flex flex-col items-center justify-center h-48 text-gray-500">
              <User className="w-12 h-12 mb-3 opacity-50" />
              <p>No customer selected</p>
              <p className="text-sm">Pick a customer in step 1 first</p>
            </div>
          ) : isLoading ? (
            <div className="flex items-center justify-center h-48">
              <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-bv-red-600"></div>
            </div>
          ) : errorMsg ? (
            <div className="flex flex-col items-center justify-center h-48 text-red-500">
              <AlertTriangle className="w-12 h-12 mb-3 opacity-50" />
              <p>{errorMsg}</p>
            </div>
          ) : !selectedPatientId ? (
            /* ---------- STEP 1: PATIENT PICKER ---------- */
            members.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-48 text-gray-500">
                <Users className="w-12 h-12 mb-3 opacity-50" />
                <p>No family members on this account</p>
                <p className="text-sm mb-4">Create a new prescription for this customer</p>
                <button onClick={onCreateNew} className="btn-primary flex items-center gap-2">
                  <Plus className="w-4 h-4" />
                  Create New Prescription
                </button>
              </div>
            ) : (
              <div className="space-y-3">
                {members.map((member) => (
                  <button
                    key={member.patient_id || 'unlinked'}
                    onClick={() => setSelectedPatientId(member.patient_id)}
                    className="w-full flex items-center justify-between gap-3 p-4 border border-gray-200 rounded-lg hover:border-bv-red-300 hover:bg-gray-50 transition-colors text-left"
                  >
                    <div className="flex items-center gap-3">
                      <div className="w-10 h-10 rounded-full bg-blue-100 flex items-center justify-center">
                        <User className="w-5 h-5 text-blue-600" />
                      </div>
                      <div>
                        <p className="font-medium text-gray-900">{member.name || 'Unlinked patient'}</p>
                        <p className="text-xs text-gray-500">
                          {member.relation || 'Patient'} · {member.prescription_count} Rx
                          {member.valid_count > 0 && (
                            <span className="text-green-600"> · {member.valid_count} valid</span>
                          )}
                        </p>
                      </div>
                    </div>
                    <ChevronLeft className="w-4 h-4 text-gray-400 rotate-180" />
                  </button>
                ))}
              </div>
            )
          ) : /* ---------- STEP 2: PRESCRIPTION PICKER ---------- */ (
            <div className="space-y-3">
              {/* F50: "From doctor today" advisory. Read-only marker that an
                  optometrist sent this patient's Rx to the floor; it does NOT
                  alter selection / order / payment (POS-safe). */}
              {activeHandover && (
                <div className="flex items-start gap-2 p-3 rounded-lg bg-gray-50 border-l-2 border-bv-red-500">
                  <Stethoscope className="w-4 h-4 text-bv-red-600 mt-0.5 flex-shrink-0" />
                  <div className="text-xs text-gray-700">
                    <span className="font-medium text-gray-900">From Dr. {activeHandover.optometrist_name || 'Optometry'}</span>
                    {activeHandover.clinical_summary && (
                      <span className="text-gray-500"> · {activeHandover.clinical_summary}</span>
                    )}
                    <p className="text-gray-500">Rx sent to the floor today — confirm the prescription below.</p>
                  </div>
                </div>
              )}

              {/* Create New Option */}
              <button
                onClick={onCreateNew}
                className="w-full flex items-center gap-3 p-4 border-2 border-dashed border-bv-red-300 rounded-lg hover:bg-bv-red-50 transition-colors text-left"
              >
                <div className="w-10 h-10 rounded-full bg-bv-red-100 flex items-center justify-center">
                  <Plus className="w-5 h-5 text-bv-red-600" />
                </div>
                <div>
                  <p className="font-medium text-bv-red-600">Create New Prescription</p>
                  <p className="text-sm text-gray-500">Enter prescription details manually</p>
                </div>
              </button>

              {(selectedMember?.prescriptions.length || 0) === 0 ? (
                <div className="flex flex-col items-center justify-center py-10 text-gray-500">
                  <FileText className="w-12 h-12 mb-3 opacity-50" />
                  <p>No prescriptions for {selectedMember?.name || 'this patient'}</p>
                  <p className="text-sm">Create a new one above.</p>
                </div>
              ) : (
                (selectedMember?.prescriptions || []).map((prescription) => {
                  const expired = isExpired(prescription);
                  const daysLeft = getDaysUntilExpiry(prescription);
                  const isSelected = prescription.id === currentPrescriptionId;
                  const nearExpiry = !expired && daysLeft <= 30;
                  const rightEye = prescription.rightEye || ({} as Prescription['rightEye']);
                  const leftEye = prescription.leftEye || ({} as Prescription['leftEye']);

                  return (
                    <button
                      key={prescription.id}
                      onClick={() => !expired && onSelect(prescription)}
                      disabled={expired}
                      className={`w-full p-4 border rounded-lg text-left transition-all ${
                        expired
                          ? 'border-gray-200 bg-gray-50 opacity-60 cursor-not-allowed'
                          : isSelected
                          ? 'border-bv-red-500 bg-bv-red-50 ring-2 ring-bv-red-200'
                          : 'border-gray-200 hover:border-bv-red-300 hover:bg-gray-50'
                      }`}
                    >
                      {/* Header */}
                      <div className="flex items-start justify-between mb-3">
                        <div className="flex items-center gap-2">
                          <Calendar className="w-4 h-4 text-gray-500" />
                          <span className="font-medium text-gray-900">{formatDate(prescription.testDate)}</span>
                          {prescription.isExternal ? (
                            <span className="px-2 py-0.5 text-xs bg-purple-100 text-purple-700 rounded">External</span>
                          ) : (
                            <span className="px-2 py-0.5 text-xs bg-blue-100 text-blue-700 rounded">In-Store</span>
                          )}
                        </div>
                        <div className="flex items-center gap-2">
                          {expired ? (
                            <span className="flex items-center gap-1 text-xs text-red-600">
                              <AlertTriangle className="w-3 h-3" />
                              Expired
                            </span>
                          ) : nearExpiry ? (
                            <span className="flex items-center gap-1 text-xs text-amber-600">
                              <Clock className="w-3 h-3" />
                              Expires in {daysLeft} days
                            </span>
                          ) : (
                            <span className="flex items-center gap-1 text-xs text-green-600">
                              <Check className="w-3 h-3" />
                              Valid
                            </span>
                          )}
                        </div>
                      </div>

                      {/* Prescription Details */}
                      <div className="grid grid-cols-2 gap-4 mb-3">
                        {/* Right Eye */}
                        <div className="bg-gray-50 rounded-lg p-3">
                          <p className="text-xs text-gray-500 mb-1">Right Eye (OD)</p>
                          <div className="flex gap-3 text-sm">
                            <div>
                              <span className="text-gray-500 text-xs">SPH</span>
                              <p className="font-medium">{formatPower(rightEye.sphere)}</p>
                            </div>
                            <div>
                              <span className="text-gray-500 text-xs">CYL</span>
                              <p className="font-medium">{formatPower(rightEye.cylinder)}</p>
                            </div>
                            <div>
                              <span className="text-gray-500 text-xs">AXIS</span>
                              <p className="font-medium">{rightEye.axis || '-'}</p>
                            </div>
                            {rightEye.add && (
                              <div>
                                <span className="text-gray-500 text-xs">ADD</span>
                                <p className="font-medium">{formatPower(rightEye.add)}</p>
                              </div>
                            )}
                          </div>
                        </div>

                        {/* Left Eye */}
                        <div className="bg-gray-50 rounded-lg p-3">
                          <p className="text-xs text-gray-500 mb-1">Left Eye (OS)</p>
                          <div className="flex gap-3 text-sm">
                            <div>
                              <span className="text-gray-500 text-xs">SPH</span>
                              <p className="font-medium">{formatPower(leftEye.sphere)}</p>
                            </div>
                            <div>
                              <span className="text-gray-500 text-xs">CYL</span>
                              <p className="font-medium">{formatPower(leftEye.cylinder)}</p>
                            </div>
                            <div>
                              <span className="text-gray-500 text-xs">AXIS</span>
                              <p className="font-medium">{leftEye.axis || '-'}</p>
                            </div>
                            {leftEye.add && (
                              <div>
                                <span className="text-gray-500 text-xs">ADD</span>
                                <p className="font-medium">{formatPower(leftEye.add)}</p>
                              </div>
                            )}
                          </div>
                        </div>
                      </div>

                      {/* Footer Info */}
                      <div className="flex items-center justify-between text-xs text-gray-500">
                        <span>
                          {prescription.isExternal
                            ? `Source: ${prescription.externalSource}`
                            : `By: ${prescription.optometristName}`}
                        </span>
                        <span>PD: {rightEye.pd}/{leftEye.pd}</span>
                      </div>

                      {/* Recommendation */}
                      {prescription.recommendation && (
                        <p className="mt-2 text-xs text-gray-600 bg-yellow-50 p-2 rounded">
                          {prescription.recommendation}
                        </p>
                      )}
                    </button>
                  );
                })
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="p-4 border-t border-gray-200 bg-gray-50 flex justify-end">
          <button onClick={onClose} className="btn-outline">
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

export default PrescriptionSelectModal;

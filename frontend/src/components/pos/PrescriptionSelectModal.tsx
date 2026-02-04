// ============================================================================
// IMS 2.0 - Prescription Select Modal for POS
// ============================================================================
// Allows selecting from patient's existing prescriptions
// Shows prescription history with validity status

import { useState, useEffect } from 'react';
import { X, Eye, AlertTriangle, Check, Calendar, User, Clock, FileText, Plus } from 'lucide-react';
import type { Prescription, Patient } from '../../types';
import { prescriptionApi } from '../../services/api';

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
  customerId: _customerId,
  currentPrescriptionId,
}: PrescriptionSelectModalProps) {
  // customerId may be used for future API calls
  void _customerId;
  const [prescriptions, setPrescriptions] = useState<Prescription[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  useEffect(() => {
    if (patient?.id) {
      loadPrescriptions();
    }
  }, [patient?.id]);

  const loadPrescriptions = async () => {
    if (!patient?.id) return;

    setIsLoading(true);
    setErrorMsg(null);

    try {
      const response = await prescriptionApi.getPrescriptions(patient.id);
      setPrescriptions(response.prescriptions || response || []);
    } catch {
      setErrorMsg('Failed to load prescriptions. Please try again.');
      setPrescriptions([]);
    } finally {
      setIsLoading(false);
    }
  };

  const formatPower = (value: number | null | undefined) => {
    if (value === null || value === undefined) return '-';
    return value >= 0 ? `+${value.toFixed(2)}` : value.toFixed(2);
  };

  const isExpired = (prescription: Prescription) => {
    const testDate = new Date(prescription.testDate);
    const validityMonths = prescription.validityMonths || 12;
    const expiryDate = new Date(testDate);
    expiryDate.setMonth(expiryDate.getMonth() + validityMonths);
    return new Date() > expiryDate;
  };

  const getDaysUntilExpiry = (prescription: Prescription) => {
    const testDate = new Date(prescription.testDate);
    const validityMonths = prescription.validityMonths || 12;
    const expiryDate = new Date(testDate);
    expiryDate.setMonth(expiryDate.getMonth() + validityMonths);
    const diff = expiryDate.getTime() - new Date().getTime();
    return Math.ceil(diff / (1000 * 60 * 60 * 24));
  };

  const formatDate = (dateString: string) => {
    return new Date(dateString).toLocaleDateString('en-IN', {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
    });
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl w-full max-w-2xl max-h-[90vh] overflow-hidden flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-gray-200 bg-gray-50">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-blue-100 rounded-lg">
              <Eye className="w-5 h-5 text-blue-600" />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-gray-900">Select Prescription</h2>
              {patient && (
                <p className="text-xs text-gray-500">for {patient.name}</p>
              )}
            </div>
          </div>
          <button onClick={onClose} className="p-2 hover:bg-gray-200 rounded-lg transition-colors">
            <X className="w-5 h-5 text-gray-500" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-4">
          {!patient ? (
            <div className="flex flex-col items-center justify-center h-48 text-gray-400">
              <User className="w-12 h-12 mb-3 opacity-50" />
              <p>No patient selected</p>
              <p className="text-sm">Please select a patient first</p>
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
          ) : prescriptions.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-48 text-gray-400">
              <FileText className="w-12 h-12 mb-3 opacity-50" />
              <p>No prescriptions found</p>
              <p className="text-sm mb-4">Create a new prescription for this patient</p>
              <button
                onClick={onCreateNew}
                className="btn-primary flex items-center gap-2"
              >
                <Plus className="w-4 h-4" />
                Create New Prescription
              </button>
            </div>
          ) : (
            <div className="space-y-3">
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

              {/* Existing Prescriptions */}
              {prescriptions.map((prescription) => {
                const expired = isExpired(prescription);
                const daysLeft = getDaysUntilExpiry(prescription);
                const isSelected = prescription.id === currentPrescriptionId;
                const nearExpiry = !expired && daysLeft <= 30;

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
                        <Calendar className="w-4 h-4 text-gray-400" />
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
                            <span className="text-gray-400 text-xs">SPH</span>
                            <p className="font-medium">{formatPower(prescription.rightEye.sphere)}</p>
                          </div>
                          <div>
                            <span className="text-gray-400 text-xs">CYL</span>
                            <p className="font-medium">{formatPower(prescription.rightEye.cylinder)}</p>
                          </div>
                          <div>
                            <span className="text-gray-400 text-xs">AXIS</span>
                            <p className="font-medium">{prescription.rightEye.axis || '-'}</p>
                          </div>
                          {prescription.rightEye.add && (
                            <div>
                              <span className="text-gray-400 text-xs">ADD</span>
                              <p className="font-medium">{formatPower(prescription.rightEye.add)}</p>
                            </div>
                          )}
                        </div>
                      </div>

                      {/* Left Eye */}
                      <div className="bg-gray-50 rounded-lg p-3">
                        <p className="text-xs text-gray-500 mb-1">Left Eye (OS)</p>
                        <div className="flex gap-3 text-sm">
                          <div>
                            <span className="text-gray-400 text-xs">SPH</span>
                            <p className="font-medium">{formatPower(prescription.leftEye.sphere)}</p>
                          </div>
                          <div>
                            <span className="text-gray-400 text-xs">CYL</span>
                            <p className="font-medium">{formatPower(prescription.leftEye.cylinder)}</p>
                          </div>
                          <div>
                            <span className="text-gray-400 text-xs">AXIS</span>
                            <p className="font-medium">{prescription.leftEye.axis || '-'}</p>
                          </div>
                          {prescription.leftEye.add && (
                            <div>
                              <span className="text-gray-400 text-xs">ADD</span>
                              <p className="font-medium">{formatPower(prescription.leftEye.add)}</p>
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
                      <span>PD: {prescription.rightEye.pd}/{prescription.leftEye.pd}</span>
                    </div>

                    {/* Recommendation */}
                    {prescription.recommendation && (
                      <p className="mt-2 text-xs text-gray-600 bg-yellow-50 p-2 rounded">
                        💡 {prescription.recommendation}
                      </p>
                    )}
                  </button>
                );
              })}
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

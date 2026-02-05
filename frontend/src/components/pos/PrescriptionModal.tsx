// ============================================================================
// IMS 2.0 - Prescription Modal Component
// ============================================================================
// NO MOCK DATA - Uses real API calls

import { useState, useEffect, useCallback } from 'react';
import { X, FileText, Plus, Eye, Calendar, Loader2, RefreshCw } from 'lucide-react';
import type { Patient, Prescription } from '../../types';
import clsx from 'clsx';
import { prescriptionApi } from '../../services/api';
import { useAuth } from '../../context/AuthContext';

interface PrescriptionModalProps {
  patient: Patient;
  existingPrescription: Prescription | null;
  onSelect: (prescription: Prescription) => void;
  onClose: () => void;
}

type TabType = 'existing' | 'new';

export function PrescriptionModal({
  patient,
  existingPrescription,
  onSelect,
  onClose,
}: PrescriptionModalProps) {
  const { user } = useAuth();
  const [activeTab, setActiveTab] = useState<TabType>(existingPrescription ? 'existing' : 'existing');
  const [selectedPrescription, setSelectedPrescription] = useState<string | null>(
    existingPrescription?.id || null
  );
  const [prescriptions, setPrescriptions] = useState<Prescription[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);

  // New prescription form state
  const [newRx, setNewRx] = useState({
    rightEye: { sphere: '', cylinder: '', axis: '', add: '', pd: '', va: '' },
    leftEye: { sphere: '', cylinder: '', axis: '', add: '', pd: '', va: '' },
    recommendation: '',
  });

  // Load prescriptions from API
  const loadPrescriptions = useCallback(async () => {
    if (!patient.id) return;

    setIsLoading(true);
    setError(null);

    try {
      const response = await prescriptionApi.getPrescriptions(patient.id);

      if (response?.prescriptions && Array.isArray(response.prescriptions)) {
        // Transform API response to Prescription type
        const rxList: Prescription[] = response.prescriptions.map((rx: any) => ({
          id: rx.prescription_id || rx.id,
          patientId: rx.patient_id || patient.id,
          customerId: rx.customer_id || patient.customerId,
          storeId: rx.store_id || user?.activeStoreId || '',
          optometristId: rx.optometrist_id || '',
          optometristName: rx.optometrist_name || 'Unknown',
          testDate: rx.test_date || rx.created_at?.split('T')[0] || '',
          rightEye: {
            sphere: rx.right_eye?.sphere ?? rx.od_sphere ?? 0,
            cylinder: rx.right_eye?.cylinder ?? rx.od_cylinder ?? null,
            axis: rx.right_eye?.axis ?? rx.od_axis ?? null,
            add: rx.right_eye?.add ?? rx.od_add ?? null,
            pd: rx.right_eye?.pd ?? rx.od_pd ?? 32,
            va: rx.right_eye?.va ?? rx.od_va ?? '6/6',
          },
          leftEye: {
            sphere: rx.left_eye?.sphere ?? rx.os_sphere ?? 0,
            cylinder: rx.left_eye?.cylinder ?? rx.os_cylinder ?? null,
            axis: rx.left_eye?.axis ?? rx.os_axis ?? null,
            add: rx.left_eye?.add ?? rx.os_add ?? null,
            pd: rx.left_eye?.pd ?? rx.os_pd ?? 31,
            va: rx.left_eye?.va ?? rx.os_va ?? '6/6',
          },
          recommendation: rx.recommendation || rx.notes || '',
          status: rx.status || 'COMPLETED',
          createdAt: rx.created_at || new Date().toISOString(),
          updatedAt: rx.updated_at || new Date().toISOString(),
        }));

        setPrescriptions(rxList);
      } else {
        setPrescriptions([]);
      }
    } catch (err) {
      console.error('Failed to load prescriptions:', err);
      setError('Failed to load prescriptions');
      setPrescriptions([]);
    } finally {
      setIsLoading(false);
    }
  }, [patient.id, patient.customerId, user?.activeStoreId]);

  // Load on mount
  useEffect(() => {
    loadPrescriptions();
  }, [loadPrescriptions]);

  const handleSelectExisting = () => {
    const rx = prescriptions.find(p => p.id === selectedPrescription);
    if (rx) {
      onSelect(rx);
    }
  };

  const handleCreateNew = async () => {
    setIsSaving(true);

    try {
      const prescriptionData = {
        patient_id: patient.id,
        customer_id: patient.customerId,
        store_id: user?.activeStoreId || '',
        test_date: new Date().toISOString().split('T')[0],
        right_eye: {
          sphere: parseFloat(newRx.rightEye.sphere) || 0,
          cylinder: parseFloat(newRx.rightEye.cylinder) || null,
          axis: parseInt(newRx.rightEye.axis) || null,
          add: parseFloat(newRx.rightEye.add) || null,
          pd: parseFloat(newRx.rightEye.pd) || 32,
          va: newRx.rightEye.va || '6/6',
        },
        left_eye: {
          sphere: parseFloat(newRx.leftEye.sphere) || 0,
          cylinder: parseFloat(newRx.leftEye.cylinder) || null,
          axis: parseInt(newRx.leftEye.axis) || null,
          add: parseFloat(newRx.leftEye.add) || null,
          pd: parseFloat(newRx.leftEye.pd) || 31,
          va: newRx.leftEye.va || '6/6',
        },
        recommendation: newRx.recommendation,
        status: 'COMPLETED' as const,
      };

      const response = await prescriptionApi.createPrescription(prescriptionData as any);

      // Create prescription object to return
      const newPrescription: Prescription = {
        id: response?.prescription_id || `rx-${Date.now()}`,
        patientId: patient.id,
        customerId: patient.customerId,
        storeId: user?.activeStoreId || '',
        optometristId: user?.id || 'user-current',
        optometristName: user?.name || 'Current User',
        testDate: new Date().toISOString().split('T')[0],
        rightEye: {
          sphere: parseFloat(newRx.rightEye.sphere) || 0,
          cylinder: parseFloat(newRx.rightEye.cylinder) || null,
          axis: parseInt(newRx.rightEye.axis) || null,
          add: parseFloat(newRx.rightEye.add) || null,
          pd: parseFloat(newRx.rightEye.pd) || 32,
          va: newRx.rightEye.va || '6/6',
        },
        leftEye: {
          sphere: parseFloat(newRx.leftEye.sphere) || 0,
          cylinder: parseFloat(newRx.leftEye.cylinder) || null,
          axis: parseInt(newRx.leftEye.axis) || null,
          add: parseFloat(newRx.leftEye.add) || null,
          pd: parseFloat(newRx.leftEye.pd) || 31,
          va: newRx.leftEye.va || '6/6',
        },
        recommendation: newRx.recommendation,
        status: 'COMPLETED',
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
      };

      onSelect(newPrescription);
    } catch (err) {
      console.error('Failed to create prescription:', err);
      alert('Failed to save prescription. Please try again.');
    } finally {
      setIsSaving(false);
    }
  };

  const formatDate = (dateStr: string) => {
    return new Date(dateStr).toLocaleDateString('en-IN', {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
    });
  };

  const formatPower = (value: number | null | undefined) => {
    if (value === null || value === undefined) return '-';
    return value >= 0 ? `+${value.toFixed(2)}` : value.toFixed(2);
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-3xl max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-gray-200">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-bv-red-100 rounded-full flex items-center justify-center">
              <Eye className="w-5 h-5 text-bv-red-600" />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-gray-900">Prescription</h2>
              <p className="text-sm text-gray-500">
                Patient: {patient.name} ({patient.relation})
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={loadPrescriptions}
              disabled={isLoading}
              className="p-2 text-gray-400 hover:text-gray-600 rounded-lg hover:bg-gray-100"
              title="Refresh"
            >
              <RefreshCw className={clsx('w-5 h-5', isLoading && 'animate-spin')} />
            </button>
            <button
              onClick={onClose}
              className="p-2 text-gray-400 hover:text-gray-600 rounded-lg hover:bg-gray-100"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-gray-200">
          <button
            onClick={() => setActiveTab('existing')}
            className={clsx(
              'flex-1 py-3 text-sm font-medium border-b-2 transition-colors',
              activeTab === 'existing'
                ? 'border-bv-red-600 text-bv-red-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            )}
          >
            <FileText className="w-4 h-4 inline mr-2" />
            Existing Prescriptions
          </button>
          <button
            onClick={() => setActiveTab('new')}
            className={clsx(
              'flex-1 py-3 text-sm font-medium border-b-2 transition-colors',
              activeTab === 'new'
                ? 'border-bv-red-600 text-bv-red-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            )}
          >
            <Plus className="w-4 h-4 inline mr-2" />
            New Prescription
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-4">
          {activeTab === 'existing' ? (
            <div className="space-y-3">
              {isLoading ? (
                <div className="flex flex-col items-center justify-center py-12 text-gray-400">
                  <Loader2 className="w-8 h-8 mb-2 animate-spin" />
                  <p>Loading prescriptions...</p>
                </div>
              ) : error ? (
                <div className="flex flex-col items-center justify-center py-12 text-gray-400">
                  <FileText className="w-12 h-12 mb-2 opacity-50" />
                  <p>{error}</p>
                  <button onClick={loadPrescriptions} className="text-bv-gold-600 text-sm mt-2 hover:underline">
                    Try again
                  </button>
                </div>
              ) : prescriptions.length === 0 ? (
                <div className="text-center py-8 text-gray-500">
                  <FileText className="w-12 h-12 mx-auto mb-2 opacity-50" />
                  <p>No existing prescriptions found</p>
                  <p className="text-sm">Create a new prescription</p>
                </div>
              ) : (
                prescriptions.map(rx => (
                  <div
                    key={rx.id}
                    onClick={() => setSelectedPrescription(rx.id)}
                    className={clsx(
                      'p-4 border rounded-lg cursor-pointer transition-colors',
                      selectedPrescription === rx.id
                        ? 'border-bv-red-500 bg-bv-red-50'
                        : 'border-gray-200 hover:border-gray-300'
                    )}
                  >
                    {/* Rx Header */}
                    <div className="flex items-center justify-between mb-3">
                      <div className="flex items-center gap-2">
                        <Calendar className="w-4 h-4 text-gray-400" />
                        <span className="font-medium">{formatDate(rx.testDate)}</span>
                      </div>
                      <div className="flex items-center gap-2 text-sm text-gray-500">
                        <span>by {rx.optometristName}</span>
                      </div>
                    </div>

                    {/* Rx Table */}
                    <div className="bg-gray-50 rounded-lg p-3 text-sm">
                      <table className="w-full">
                        <thead>
                          <tr className="text-gray-500 text-xs">
                            <th className="text-left pb-2">Eye</th>
                            <th className="text-center pb-2">SPH</th>
                            <th className="text-center pb-2">CYL</th>
                            <th className="text-center pb-2">AXIS</th>
                            <th className="text-center pb-2">ADD</th>
                            <th className="text-center pb-2">PD</th>
                            <th className="text-center pb-2">VA</th>
                          </tr>
                        </thead>
                        <tbody>
                          <tr>
                            <td className="py-1 font-medium">R</td>
                            <td className="text-center">{formatPower(rx.rightEye.sphere)}</td>
                            <td className="text-center">{formatPower(rx.rightEye.cylinder)}</td>
                            <td className="text-center">{rx.rightEye.axis || '-'}°</td>
                            <td className="text-center">{formatPower(rx.rightEye.add)}</td>
                            <td className="text-center">{rx.rightEye.pd}</td>
                            <td className="text-center">{rx.rightEye.va}</td>
                          </tr>
                          <tr>
                            <td className="py-1 font-medium">L</td>
                            <td className="text-center">{formatPower(rx.leftEye.sphere)}</td>
                            <td className="text-center">{formatPower(rx.leftEye.cylinder)}</td>
                            <td className="text-center">{rx.leftEye.axis || '-'}°</td>
                            <td className="text-center">{formatPower(rx.leftEye.add)}</td>
                            <td className="text-center">{rx.leftEye.pd}</td>
                            <td className="text-center">{rx.leftEye.va}</td>
                          </tr>
                        </tbody>
                      </table>
                    </div>

                    {rx.recommendation && (
                      <p className="mt-2 text-sm text-gray-600 italic">
                        "{rx.recommendation}"
                      </p>
                    )}
                  </div>
                ))
              )}
            </div>
          ) : (
            // New Prescription Form
            <div className="space-y-4">
              <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-3 text-sm text-yellow-700">
                <strong>Note:</strong> Full eye test should be conducted via the Eye Tests module.
                This quick entry is for external prescriptions only.
              </div>

              {/* Right Eye */}
              <div>
                <h3 className="font-medium text-gray-900 mb-2">Right Eye (OD)</h3>
                <div className="grid grid-cols-6 gap-2">
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">SPH</label>
                    <input
                      type="number"
                      step="0.25"
                      value={newRx.rightEye.sphere}
                      onChange={e => setNewRx(prev => ({
                        ...prev,
                        rightEye: { ...prev.rightEye, sphere: e.target.value }
                      }))}
                      className="input-field text-sm"
                      placeholder="-2.00"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">CYL</label>
                    <input
                      type="number"
                      step="0.25"
                      value={newRx.rightEye.cylinder}
                      onChange={e => setNewRx(prev => ({
                        ...prev,
                        rightEye: { ...prev.rightEye, cylinder: e.target.value }
                      }))}
                      className="input-field text-sm"
                      placeholder="-0.50"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">AXIS</label>
                    <input
                      type="number"
                      min="0"
                      max="180"
                      value={newRx.rightEye.axis}
                      onChange={e => setNewRx(prev => ({
                        ...prev,
                        rightEye: { ...prev.rightEye, axis: e.target.value }
                      }))}
                      className="input-field text-sm"
                      placeholder="180"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">ADD</label>
                    <input
                      type="number"
                      step="0.25"
                      value={newRx.rightEye.add}
                      onChange={e => setNewRx(prev => ({
                        ...prev,
                        rightEye: { ...prev.rightEye, add: e.target.value }
                      }))}
                      className="input-field text-sm"
                      placeholder="+1.00"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">PD</label>
                    <input
                      type="number"
                      step="0.5"
                      value={newRx.rightEye.pd}
                      onChange={e => setNewRx(prev => ({
                        ...prev,
                        rightEye: { ...prev.rightEye, pd: e.target.value }
                      }))}
                      className="input-field text-sm"
                      placeholder="32"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">VA</label>
                    <input
                      type="text"
                      value={newRx.rightEye.va}
                      onChange={e => setNewRx(prev => ({
                        ...prev,
                        rightEye: { ...prev.rightEye, va: e.target.value }
                      }))}
                      className="input-field text-sm"
                      placeholder="6/6"
                    />
                  </div>
                </div>
              </div>

              {/* Left Eye */}
              <div>
                <h3 className="font-medium text-gray-900 mb-2">Left Eye (OS)</h3>
                <div className="grid grid-cols-6 gap-2">
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">SPH</label>
                    <input
                      type="number"
                      step="0.25"
                      value={newRx.leftEye.sphere}
                      onChange={e => setNewRx(prev => ({
                        ...prev,
                        leftEye: { ...prev.leftEye, sphere: e.target.value }
                      }))}
                      className="input-field text-sm"
                      placeholder="-2.00"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">CYL</label>
                    <input
                      type="number"
                      step="0.25"
                      value={newRx.leftEye.cylinder}
                      onChange={e => setNewRx(prev => ({
                        ...prev,
                        leftEye: { ...prev.leftEye, cylinder: e.target.value }
                      }))}
                      className="input-field text-sm"
                      placeholder="-0.50"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">AXIS</label>
                    <input
                      type="number"
                      min="0"
                      max="180"
                      value={newRx.leftEye.axis}
                      onChange={e => setNewRx(prev => ({
                        ...prev,
                        leftEye: { ...prev.leftEye, axis: e.target.value }
                      }))}
                      className="input-field text-sm"
                      placeholder="180"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">ADD</label>
                    <input
                      type="number"
                      step="0.25"
                      value={newRx.leftEye.add}
                      onChange={e => setNewRx(prev => ({
                        ...prev,
                        leftEye: { ...prev.leftEye, add: e.target.value }
                      }))}
                      className="input-field text-sm"
                      placeholder="+1.00"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">PD</label>
                    <input
                      type="number"
                      step="0.5"
                      value={newRx.leftEye.pd}
                      onChange={e => setNewRx(prev => ({
                        ...prev,
                        leftEye: { ...prev.leftEye, pd: e.target.value }
                      }))}
                      className="input-field text-sm"
                      placeholder="31"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">VA</label>
                    <input
                      type="text"
                      value={newRx.leftEye.va}
                      onChange={e => setNewRx(prev => ({
                        ...prev,
                        leftEye: { ...prev.leftEye, va: e.target.value }
                      }))}
                      className="input-field text-sm"
                      placeholder="6/6"
                    />
                  </div>
                </div>
              </div>

              {/* Recommendation */}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Recommendation
                </label>
                <textarea
                  value={newRx.recommendation}
                  onChange={e => setNewRx(prev => ({ ...prev, recommendation: e.target.value }))}
                  className="input-field"
                  rows={2}
                  placeholder="Optional notes or recommendations..."
                />
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="p-4 border-t border-gray-200 flex justify-end gap-3">
          <button onClick={onClose} className="btn-outline">
            Cancel
          </button>
          {activeTab === 'existing' ? (
            <button
              onClick={handleSelectExisting}
              disabled={!selectedPrescription || isLoading}
              className="btn-primary"
            >
              Use Selected Prescription
            </button>
          ) : (
            <button
              onClick={handleCreateNew}
              disabled={(!newRx.rightEye.sphere && !newRx.leftEye.sphere) || isSaving}
              className="btn-primary flex items-center gap-2"
            >
              {isSaving && <Loader2 className="w-4 h-4 animate-spin" />}
              Save & Use Prescription
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export default PrescriptionModal;

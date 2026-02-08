// ============================================================================
// IMS 2.0 - Prescription Management System
// ============================================================================
// Manage optical prescriptions with sphere, cylinder, axis, add, prism data

import { useState } from 'react';
import { Plus, Search, Edit2, Trash2, Eye, Copy, CheckCircle, AlertCircle } from 'lucide-react';
import clsx from 'clsx';

export interface PrescriptionData {
  side: 'OD' | 'OS';
  sphere: number;
  cylinder: number;
  axis: number;
  add: number;
  prism?: number;
  prismBase?: 'UD' | 'IN' | 'UP' | 'OUT';
}

export interface Prescription {
  id: string;
  patientId: string;
  doctorName: string;
  prescriptionDate: string;
  expiryDate: string;
  od: PrescriptionData;
  os: PrescriptionData;
  notes?: string;
  status: 'active' | 'expired' | 'archived';
  createdAt: string;
  version: number;
}

interface PrescriptionManagementProps {
  prescriptions: Prescription[];
  onCreatePrescription: (prescription: Omit<Prescription, 'id' | 'createdAt' | 'version'>) => Promise<void>;
  onUpdatePrescription: (prescription: Prescription) => Promise<void>;
  onDeletePrescription: (id: string) => Promise<void>;
  onDuplicatePrescription: (id: string) => Promise<void>;
  loading?: boolean;
}

const emptyPrescriptionData: PrescriptionData = {
  side: 'OD',
  sphere: 0,
  cylinder: 0,
  axis: 0,
  add: 0,
};

export function PrescriptionManagement({
  prescriptions,
  onCreatePrescription,
  onUpdatePrescription,
  onDeletePrescription,
  onDuplicatePrescription,
  loading = false,
}: PrescriptionManagementProps) {
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [formData, setFormData] = useState<Partial<Prescription>>({});

  const filteredPrescriptions = prescriptions.filter(p =>
    p.doctorName.toLowerCase().includes(searchTerm.toLowerCase()) ||
    p.patientId.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const handleSave = async () => {
    if (!formData.patientId || !formData.doctorName || !formData.prescriptionDate || !formData.od || !formData.os) {
      alert('Please fill in all required fields');
      return;
    }

    if (editingId) {
      await Promise.resolve(onUpdatePrescription({
        ...formData,
        id: editingId,
        createdAt: formData.createdAt || '',
        version: (formData.version || 0) + 1,
      } as Prescription));
    } else {
      await Promise.resolve(onCreatePrescription({
        ...formData,
        createdAt: new Date().toISOString(),
        version: 1,
      } as any));
    }

    setFormData({});
    setEditingId(null);
    setShowCreateModal(false);
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'active':
        return 'bg-green-100 text-green-700';
      case 'expired':
        return 'bg-red-100 text-red-700';
      case 'archived':
        return 'bg-gray-100 text-gray-700';
      default:
        return 'bg-gray-100 text-gray-700';
    }
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'active':
        return <CheckCircle className="w-4 h-4" />;
      case 'expired':
        return <AlertCircle className="w-4 h-4" />;
      default:
        return null;
    }
  };

  return (
    <div className="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-800">
      {/* Header */}
      <div className="p-6 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between">
        <div>
          <h2 className="text-lg font-bold text-gray-900 dark:text-white flex items-center gap-2">
            <Eye className="w-5 h-5" />
            Prescription Management
          </h2>
          <p className="text-sm text-gray-600 dark:text-gray-400 mt-1">
            {filteredPrescriptions.length} of {prescriptions.length} prescriptions
          </p>
        </div>
        <button
          onClick={() => {
            setEditingId(null);
            setFormData({
              od: emptyPrescriptionData,
              os: { ...emptyPrescriptionData, side: 'OS' },
            });
            setShowCreateModal(true);
          }}
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 font-medium"
        >
          <Plus className="w-4 h-4" />
          New Prescription
        </button>
      </div>

      {/* Search */}
      <div className="p-4 border-b border-gray-200 dark:border-gray-800">
        <div className="relative">
          <Search className="absolute left-3 top-3 w-4 h-4 text-gray-400" />
          <input
            type="text"
            placeholder="Search by doctor name or patient ID..."
            value={searchTerm}
            onChange={e => setSearchTerm(e.target.value)}
            className="w-full pl-10 pr-4 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
          />
        </div>
      </div>

      {/* Prescriptions List */}
      <div className="divide-y divide-gray-200 dark:divide-gray-800">
        {loading ? (
          <div className="p-8 text-center text-gray-500 dark:text-gray-400">
            <p>Loading prescriptions...</p>
          </div>
        ) : filteredPrescriptions.length === 0 ? (
          <div className="p-8 text-center text-gray-500 dark:text-gray-400">
            <Eye className="w-12 h-12 mx-auto mb-3 opacity-50" />
            <p>No prescriptions found</p>
          </div>
        ) : (
          filteredPrescriptions.map(prescription => (
            <div key={prescription.id} className="p-4 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors">
              <div className="flex items-start justify-between gap-4">
                <div className="flex-1">
                  <div className="flex items-center gap-2 mb-2">
                    <h3 className="font-semibold text-gray-900 dark:text-white">
                      {prescription.patientId}
                    </h3>
                    <span className={clsx('inline-flex items-center gap-1 px-2 py-1 rounded text-xs font-medium', getStatusColor(prescription.status))}>
                      {getStatusIcon(prescription.status)}
                      {prescription.status}
                    </span>
                  </div>
                  <div className="text-sm text-gray-600 dark:text-gray-400 space-y-1">
                    <p>Doctor: {prescription.doctorName}</p>
                    <div className="grid grid-cols-2 gap-4 mt-2">
                      <div className="text-xs">
                        <span className="font-semibold">OD:</span> {prescription.od.sphere > 0 ? '+' : ''}{prescription.od.sphere} / {prescription.od.cylinder} × {prescription.od.axis}°
                      </div>
                      <div className="text-xs">
                        <span className="font-semibold">OS:</span> {prescription.os.sphere > 0 ? '+' : ''}{prescription.os.sphere} / {prescription.os.cylinder} × {prescription.os.axis}°
                      </div>
                    </div>
                    <p className="text-xs text-gray-500 dark:text-gray-500 mt-2">
                      Valid: {new Date(prescription.prescriptionDate).toLocaleDateString()} - {new Date(prescription.expiryDate).toLocaleDateString()}
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => onDuplicatePrescription(prescription.id)}
                    className="p-2 hover:bg-blue-100 dark:hover:bg-blue-900/20 rounded-lg text-blue-600 dark:text-blue-400"
                    title="Duplicate prescription"
                  >
                    <Copy className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => {
                      setFormData(prescription);
                      setEditingId(prescription.id);
                      setShowCreateModal(true);
                    }}
                    className="p-2 hover:bg-amber-100 dark:hover:bg-amber-900/20 rounded-lg text-amber-600 dark:text-amber-400"
                    title="Edit"
                  >
                    <Edit2 className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => {
                      if (confirm('Delete this prescription?')) {
                        onDeletePrescription(prescription.id);
                      }
                    }}
                    className="p-2 hover:bg-red-100 dark:hover:bg-red-900/20 rounded-lg text-red-600 dark:text-red-400"
                    title="Delete"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
            </div>
          ))
        )}
      </div>

      {/* Create/Edit Modal */}
      {showCreateModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50" onClick={() => setShowCreateModal(false)}>
          <div className="bg-white dark:bg-gray-900 rounded-lg shadow-lg p-6 max-w-4xl w-full max-h-96 overflow-y-auto" onClick={e => e.stopPropagation()}>
            <h2 className="text-lg font-bold text-gray-900 dark:text-white mb-4">
              {editingId ? 'Edit Prescription' : 'Create New Prescription'}
            </h2>

            <div className="grid grid-cols-2 gap-4 mb-4">
              <input
                type="text"
                placeholder="Patient ID *"
                value={formData.patientId || ''}
                onChange={e => setFormData({ ...formData, patientId: e.target.value })}
                className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
              />
              <input
                type="text"
                placeholder="Doctor Name *"
                value={formData.doctorName || ''}
                onChange={e => setFormData({ ...formData, doctorName: e.target.value })}
                className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
              />
              <input
                type="date"
                placeholder="Prescription Date *"
                value={formData.prescriptionDate || ''}
                onChange={e => setFormData({ ...formData, prescriptionDate: e.target.value })}
                className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
              />
              <input
                type="date"
                placeholder="Expiry Date *"
                value={formData.expiryDate || ''}
                onChange={e => setFormData({ ...formData, expiryDate: e.target.value })}
                className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
              />
            </div>

            {/* OD Section */}
            <div className="mb-4 p-3 border border-gray-200 dark:border-gray-700 rounded-lg">
              <h3 className="font-semibold text-gray-900 dark:text-white mb-3">OD (Right Eye)</h3>
              <div className="grid grid-cols-4 gap-2">
                <input
                  type="number"
                  step="0.25"
                  placeholder="Sphere"
                  value={formData.od?.sphere || 0}
                  onChange={e => setFormData({ ...formData, od: { ...formData.od, sphere: parseFloat(e.target.value) } as any })}
                  className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white text-sm"
                />
                <input
                  type="number"
                  step="0.25"
                  placeholder="Cylinder"
                  value={formData.od?.cylinder || 0}
                  onChange={e => setFormData({ ...formData, od: { ...formData.od, cylinder: parseFloat(e.target.value) } as any })}
                  className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white text-sm"
                />
                <input
                  type="number"
                  min="0"
                  max="180"
                  placeholder="Axis"
                  value={formData.od?.axis || 0}
                  onChange={e => setFormData({ ...formData, od: { ...formData.od, axis: parseFloat(e.target.value) } as any })}
                  className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white text-sm"
                />
                <input
                  type="number"
                  step="0.25"
                  placeholder="Add"
                  value={formData.od?.add || 0}
                  onChange={e => setFormData({ ...formData, od: { ...formData.od, add: parseFloat(e.target.value) } as any })}
                  className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white text-sm"
                />
              </div>
            </div>

            {/* OS Section */}
            <div className="mb-4 p-3 border border-gray-200 dark:border-gray-700 rounded-lg">
              <h3 className="font-semibold text-gray-900 dark:text-white mb-3">OS (Left Eye)</h3>
              <div className="grid grid-cols-4 gap-2">
                <input
                  type="number"
                  step="0.25"
                  placeholder="Sphere"
                  value={formData.os?.sphere || 0}
                  onChange={e => setFormData({ ...formData, os: { ...formData.os, sphere: parseFloat(e.target.value) } as any })}
                  className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white text-sm"
                />
                <input
                  type="number"
                  step="0.25"
                  placeholder="Cylinder"
                  value={formData.os?.cylinder || 0}
                  onChange={e => setFormData({ ...formData, os: { ...formData.os, cylinder: parseFloat(e.target.value) } as any })}
                  className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white text-sm"
                />
                <input
                  type="number"
                  min="0"
                  max="180"
                  placeholder="Axis"
                  value={formData.os?.axis || 0}
                  onChange={e => setFormData({ ...formData, os: { ...formData.os, axis: parseFloat(e.target.value) } as any })}
                  className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white text-sm"
                />
                <input
                  type="number"
                  step="0.25"
                  placeholder="Add"
                  value={formData.os?.add || 0}
                  onChange={e => setFormData({ ...formData, os: { ...formData.os, add: parseFloat(e.target.value) } as any })}
                  className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white text-sm"
                />
              </div>
            </div>

            <textarea
              placeholder="Notes"
              value={formData.notes || ''}
              onChange={e => setFormData({ ...formData, notes: e.target.value })}
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white mb-4"
              rows={2}
            />

            <div className="flex gap-2">
              <button
                onClick={() => setShowCreateModal(false)}
                className="flex-1 px-4 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800"
              >
                Cancel
              </button>
              <button
                onClick={handleSave}
                className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
              >
                {editingId ? 'Update' : 'Create'} Prescription
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

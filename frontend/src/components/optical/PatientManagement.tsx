// ============================================================================
// IMS 2.0 - Patient Management System
// ============================================================================
// Manage patient records, contact history, prescriptions, and appointments

import { useState } from 'react';
import { Plus, Search, Edit2, Trash2, Eye, Calendar, Clock, Phone, Mail } from 'lucide-react';

export interface Patient {
  id: string;
  firstName: string;
  lastName: string;
  email: string;
  phone: string;
  dateOfBirth?: string;
  gender?: 'male' | 'female' | 'other';
  address?: string;
  city?: string;
  state?: string;
  zipCode?: string;
  emergencyContact?: string;
  emergencyPhone?: string;
  medicalHistory?: string[];
  allergies?: string[];
  createdAt: string;
  lastVisit?: string;
  totalVisits: number;
  totalSpent: number;
}

export interface PatientContact {
  id: string;
  patientId: string;
  type: 'call' | 'email' | 'visit' | 'sms' | 'appointment';
  date: string;
  notes?: string;
  createdBy: string;
  outcome?: string;
}

interface PatientManagementProps {
  patients: Patient[];
  onCreatePatient: (patient: Omit<Patient, 'id' | 'createdAt' | 'totalVisits' | 'totalSpent'>) => Promise<void>;
  onUpdatePatient: (patient: Patient) => Promise<void>;
  onDeletePatient: (id: string) => Promise<void>;
  onViewPatient: (id: string) => void;
  loading?: boolean;
}

export function PatientManagement({
  patients,
  onCreatePatient,
  onUpdatePatient,
  onDeletePatient,
  onViewPatient,
  loading = false,
}: PatientManagementProps) {
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [formData, setFormData] = useState<Partial<Patient>>({});
  const [editingId, setEditingId] = useState<string | null>(null);

  const filteredPatients = patients.filter(p =>
    `${p.firstName} ${p.lastName}`.toLowerCase().includes(searchTerm.toLowerCase()) ||
    p.email.toLowerCase().includes(searchTerm.toLowerCase()) ||
    p.phone.includes(searchTerm)
  );

  const handleSave = async () => {
    if (!formData.firstName || !formData.lastName || !formData.email || !formData.phone) {
      alert('Please fill in all required fields');
      return;
    }

    if (editingId) {
      await Promise.resolve(onUpdatePatient({ ...formData, id: editingId, createdAt: '', totalVisits: 0, totalSpent: 0 } as Patient));
    } else {
      await Promise.resolve(onCreatePatient(formData as any));
    }

    setFormData({});
    setEditingId(null);
    setShowCreateModal(false);
  };

  return (
    <div className="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-800">
      {/* Header */}
      <div className="p-6 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between">
        <div>
          <h2 className="text-lg font-bold text-gray-900 dark:text-white flex items-center gap-2">
            <Eye className="w-5 h-5" />
            Patient Management
          </h2>
          <p className="text-sm text-gray-600 dark:text-gray-400 mt-1">
            {filteredPatients.length} of {patients.length} patients
          </p>
        </div>
        <button
          onClick={() => {
            setEditingId(null);
            setFormData({});
            setShowCreateModal(true);
          }}
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 font-medium"
        >
          <Plus className="w-4 h-4" />
          New Patient
        </button>
      </div>

      {/* Search */}
      <div className="p-4 border-b border-gray-200 dark:border-gray-800">
        <div className="relative">
          <Search className="absolute left-3 top-3 w-4 h-4 text-gray-400" />
          <input
            type="text"
            placeholder="Search by name, email, or phone..."
            value={searchTerm}
            onChange={e => setSearchTerm(e.target.value)}
            className="w-full pl-10 pr-4 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
          />
        </div>
      </div>

      {/* Patients List */}
      <div className="divide-y divide-gray-200 dark:divide-gray-800">
        {loading ? (
          <div className="p-8 text-center text-gray-500 dark:text-gray-400">
            <p>Loading patients...</p>
          </div>
        ) : filteredPatients.length === 0 ? (
          <div className="p-8 text-center text-gray-500 dark:text-gray-400">
            <Eye className="w-12 h-12 mx-auto mb-3 opacity-50" />
            <p>No patients found</p>
          </div>
        ) : (
          filteredPatients.map(patient => (
            <div key={patient.id} className="p-4 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors">
              <div className="flex items-start justify-between gap-4">
                <div className="flex-1">
                  <h3 className="font-semibold text-gray-900 dark:text-white">
                    {patient.firstName} {patient.lastName}
                  </h3>
                  <div className="flex flex-wrap gap-4 mt-2 text-sm text-gray-600 dark:text-gray-400">
                    <div className="flex items-center gap-1">
                      <Mail className="w-4 h-4" />
                      {patient.email}
                    </div>
                    <div className="flex items-center gap-1">
                      <Phone className="w-4 h-4" />
                      {patient.phone}
                    </div>
                    {patient.lastVisit && (
                      <div className="flex items-center gap-1">
                        <Calendar className="w-4 h-4" />
                        Last: {new Date(patient.lastVisit).toLocaleDateString()}
                      </div>
                    )}
                    <div className="flex items-center gap-1">
                      <Clock className="w-4 h-4" />
                      {patient.totalVisits} visits
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => onViewPatient(patient.id)}
                    className="p-2 hover:bg-blue-100 dark:hover:bg-blue-900/20 rounded-lg text-blue-600 dark:text-blue-400"
                    title="View details"
                  >
                    <Eye className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => {
                      setFormData(patient);
                      setEditingId(patient.id);
                      setShowCreateModal(true);
                    }}
                    className="p-2 hover:bg-amber-100 dark:hover:bg-amber-900/20 rounded-lg text-amber-600 dark:text-amber-400"
                    title="Edit"
                  >
                    <Edit2 className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => {
                      if (confirm(`Delete patient ${patient.firstName} ${patient.lastName}?`)) {
                        onDeletePatient(patient.id);
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
          <div className="bg-white dark:bg-gray-900 rounded-lg shadow-lg p-6 max-w-2xl w-full max-h-96 overflow-y-auto" onClick={e => e.stopPropagation()}>
            <h2 className="text-lg font-bold text-gray-900 dark:text-white mb-4">
              {editingId ? 'Edit Patient' : 'Create New Patient'}
            </h2>

            <div className="grid grid-cols-2 gap-4">
              <input
                type="text"
                placeholder="First Name *"
                value={formData.firstName || ''}
                onChange={e => setFormData({ ...formData, firstName: e.target.value })}
                className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
              />
              <input
                type="text"
                placeholder="Last Name *"
                value={formData.lastName || ''}
                onChange={e => setFormData({ ...formData, lastName: e.target.value })}
                className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
              />
              <input
                type="email"
                placeholder="Email *"
                value={formData.email || ''}
                onChange={e => setFormData({ ...formData, email: e.target.value })}
                className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
              />
              <input
                type="tel"
                placeholder="Phone *"
                value={formData.phone || ''}
                onChange={e => setFormData({ ...formData, phone: e.target.value })}
                className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
              />
              <input
                type="date"
                placeholder="Date of Birth"
                value={formData.dateOfBirth || ''}
                onChange={e => setFormData({ ...formData, dateOfBirth: e.target.value })}
                className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
              />
              <select
                value={formData.gender || ''}
                onChange={e => setFormData({ ...formData, gender: e.target.value as any })}
                className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
              >
                <option value="">Select Gender</option>
                <option value="male">Male</option>
                <option value="female">Female</option>
                <option value="other">Other</option>
              </select>
              <input
                type="text"
                placeholder="Address"
                value={formData.address || ''}
                onChange={e => setFormData({ ...formData, address: e.target.value })}
                className="col-span-2 px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
              />
              <input
                type="text"
                placeholder="City"
                value={formData.city || ''}
                onChange={e => setFormData({ ...formData, city: e.target.value })}
                className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
              />
              <input
                type="text"
                placeholder="State"
                value={formData.state || ''}
                onChange={e => setFormData({ ...formData, state: e.target.value })}
                className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
              />
            </div>

            <div className="flex gap-2 mt-6">
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
                {editingId ? 'Update' : 'Create'} Patient
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

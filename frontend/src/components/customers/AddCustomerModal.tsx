// ============================================================================
// IMS 2.0 - Add Customer Modal
// ============================================================================
// B2C/B2B customer creation with GST verification

import { useState, useEffect } from 'react';
import {
  X,
  Plus,
  Trash2,
  Loader2,
  CheckCircle,
  AlertCircle,
  Building2,
  User,
} from 'lucide-react';
import clsx from 'clsx';

// ============================================================================
// Types
// ============================================================================

export interface PatientFormData {
  id: string;
  name: string;
  mobile: string;
  email: string;
  dateOfBirth: string;
  relation: string;
}

export interface CustomerFormData {
  customerType: 'B2C' | 'B2B';
  // Basic Info
  fullName: string;
  mobileNumber: string;
  email: string;
  dateOfBirth: string;
  anniversary: string;
  // Address
  address: string;
  pincode: string;
  city: string;
  state: string;
  // B2B specific
  gstNumber: string;
  businessName: string;
  panNumber: string;
  // Patients
  patients: PatientFormData[];
}

interface AddCustomerModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSave: (customer: CustomerFormData) => Promise<void>;
}

// ============================================================================
// Indian States List
// ============================================================================

const INDIAN_STATES = [
  'Andhra Pradesh', 'Arunachal Pradesh', 'Assam', 'Bihar', 'Chhattisgarh',
  'Goa', 'Gujarat', 'Haryana', 'Himachal Pradesh', 'Jharkhand', 'Karnataka',
  'Kerala', 'Madhya Pradesh', 'Maharashtra', 'Manipur', 'Meghalaya', 'Mizoram',
  'Nagaland', 'Odisha', 'Punjab', 'Rajasthan', 'Sikkim', 'Tamil Nadu',
  'Telangana', 'Tripura', 'Uttar Pradesh', 'Uttarakhand', 'West Bengal',
  'Delhi', 'Jammu and Kashmir', 'Ladakh', 'Puducherry', 'Chandigarh',
];

const RELATIONS = [
  'Self', 'Spouse', 'Father', 'Mother', 'Son', 'Daughter',
  'Brother', 'Sister', 'Grandfather', 'Grandmother', 'Other',
];

// ============================================================================
// Component
// ============================================================================

export function AddCustomerModal({ isOpen, onClose, onSave }: AddCustomerModalProps) {
  const [formData, setFormData] = useState<CustomerFormData>({
    customerType: 'B2C',
    fullName: '',
    mobileNumber: '',
    email: '',
    dateOfBirth: '',
    anniversary: '',
    address: '',
    pincode: '',
    city: '',
    state: '',
    gstNumber: '',
    businessName: '',
    panNumber: '',
    patients: [],
  });

  const [gstVerifying, setGstVerifying] = useState(false);
  const [gstVerified, setGstVerified] = useState<boolean | null>(null);
  const [gstError, setGstError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [showAddPatient, setShowAddPatient] = useState(false);
  const [newPatient, setNewPatient] = useState<PatientFormData>({
    id: '',
    name: '',
    mobile: '',
    email: '',
    dateOfBirth: '',
    relation: 'Self',
  });

  // Reset form state when modal opens to prevent state leakage between modules
  useEffect(() => {
    if (isOpen) {
      setFormData({
        customerType: 'B2C',
        fullName: '',
        mobileNumber: '',
        email: '',
        dateOfBirth: '',
        anniversary: '',
        address: '',
        pincode: '',
        city: '',
        state: '',
        gstNumber: '',
        businessName: '',
        panNumber: '',
        patients: [],
      });
      setGstVerifying(false);
      setGstVerified(null);
      setGstError(null);
      setIsSaving(false);
      setShowAddPatient(false);
      setNewPatient({
        id: '',
        name: '',
        mobile: '',
        email: '',
        dateOfBirth: '',
        relation: 'Self',
      });
    }
  }, [isOpen]);

  // GST Verification (simulated - in production, connect to actual GST API)
  const verifyGST = async () => {
    if (!formData.gstNumber || formData.gstNumber.length !== 15) {
      setGstError('GST number must be 15 characters');
      return;
    }

    setGstVerifying(true);
    setGstError(null);
    setGstVerified(null);

    try {
      // Simulate API call - in production, use actual GST verification API
      // Example: const result = await gstApi.verify(formData.gstNumber);
      await new Promise(resolve => setTimeout(resolve, 1500));

      // Simulate verification result based on GST format
      const gstRegex = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$/;
      const isValid = gstRegex.test(formData.gstNumber.toUpperCase());

      if (isValid) {
        // Simulate fetched data
        const stateCode = formData.gstNumber.substring(0, 2);
        const panNumber = formData.gstNumber.substring(2, 12);

        // Map state code to state name (partial mapping)
        const stateMap: Record<string, string> = {
          '01': 'Jammu and Kashmir', '02': 'Himachal Pradesh', '03': 'Punjab',
          '04': 'Chandigarh', '05': 'Uttarakhand', '06': 'Haryana',
          '07': 'Delhi', '08': 'Rajasthan', '09': 'Uttar Pradesh',
          '10': 'Bihar', '11': 'Sikkim', '12': 'Arunachal Pradesh',
          '27': 'Maharashtra', '29': 'Karnataka', '33': 'Tamil Nadu',
        };

        setFormData(prev => ({
          ...prev,
          businessName: `Business Entity (${formData.gstNumber.substring(2, 7)})`,
          panNumber: panNumber,
          state: stateMap[stateCode] || 'Maharashtra',
        }));
        setGstVerified(true);
      } else {
        setGstError('Invalid GST number format');
        setGstVerified(false);
      }
    } catch {
      setGstError('Failed to verify GST. Please try again.');
      setGstVerified(false);
    } finally {
      setGstVerifying(false);
    }
  };

  const handleAddPatient = () => {
    if (!newPatient.name.trim()) return;

    setFormData(prev => ({
      ...prev,
      patients: [
        ...prev.patients,
        { ...newPatient, id: `patient-${Date.now()}` },
      ],
    }));

    setNewPatient({
      id: '',
      name: '',
      mobile: formData.mobileNumber, // Pre-fill with customer mobile
      email: '',
      dateOfBirth: '',
      relation: 'Self',
    });
    setShowAddPatient(false);
  };

  const handleRemovePatient = (patientId: string) => {
    setFormData(prev => ({
      ...prev,
      patients: prev.patients.filter(p => p.id !== patientId),
    }));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    // Validation
    if (!formData.fullName.trim() || !formData.mobileNumber.trim()) {
      return;
    }

    if (formData.customerType === 'B2B' && !gstVerified) {
      setGstError('Please verify GST number first');
      return;
    }

    setIsSaving(true);
    try {
      await onSave(formData);
      onClose();
    } catch {
      // Error handling done in parent
    } finally {
      setIsSaving(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl max-h-[90vh] overflow-hidden flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-gray-200">
          <h2 className="text-lg font-semibold text-gray-900">Add New Customer</h2>
          <button
            onClick={onClose}
            className="p-2 text-gray-400 hover:text-gray-600 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="flex-1 overflow-y-auto p-4 space-y-6">
          {/* Customer Type */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Customer Type <span className="text-red-500">*</span>
            </label>
            <div className="flex gap-4">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  name="customerType"
                  value="B2C"
                  checked={formData.customerType === 'B2C'}
                  onChange={() => setFormData(prev => ({ ...prev, customerType: 'B2C' }))}
                  className="w-4 h-4 text-bv-red-600"
                />
                <User className="w-4 h-4 text-gray-500" />
                <span>Individual (B2C)</span>
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  name="customerType"
                  value="B2B"
                  checked={formData.customerType === 'B2B'}
                  onChange={() => setFormData(prev => ({ ...prev, customerType: 'B2B' }))}
                  className="w-4 h-4 text-bv-red-600"
                />
                <Building2 className="w-4 h-4 text-gray-500" />
                <span>Business (B2B)</span>
              </label>
            </div>
          </div>

          {/* B2B - GST Verification */}
          {formData.customerType === 'B2B' && (
            <div className="bg-blue-50 rounded-lg p-4 space-y-4">
              <h3 className="font-medium text-blue-900">Business Details</h3>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  GST Number <span className="text-red-500">*</span>
                </label>
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={formData.gstNumber}
                    onChange={e => {
                      setFormData(prev => ({ ...prev, gstNumber: e.target.value.toUpperCase() }));
                      setGstVerified(null);
                      setGstError(null);
                    }}
                    placeholder="e.g., 27AABCU9603R1ZM"
                    maxLength={15}
                    className={clsx(
                      'input-field flex-1 uppercase',
                      gstVerified === true && 'border-green-500',
                      gstVerified === false && 'border-red-500'
                    )}
                  />
                  <button
                    type="button"
                    onClick={verifyGST}
                    disabled={gstVerifying || !formData.gstNumber}
                    className="btn-primary flex items-center gap-2 disabled:opacity-50"
                  >
                    {gstVerifying ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : gstVerified ? (
                      <CheckCircle className="w-4 h-4" />
                    ) : (
                      'Verify'
                    )}
                  </button>
                </div>
                {gstError && (
                  <p className="text-sm text-red-600 mt-1 flex items-center gap-1">
                    <AlertCircle className="w-4 h-4" />
                    {gstError}
                  </p>
                )}
                {gstVerified && (
                  <p className="text-sm text-green-600 mt-1 flex items-center gap-1">
                    <CheckCircle className="w-4 h-4" />
                    GST verified successfully
                  </p>
                )}
              </div>

              {/* Auto-filled fields from GST */}
              {gstVerified && (
                <>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      Business Name
                    </label>
                    <input
                      type="text"
                      value={formData.businessName}
                      onChange={e => setFormData(prev => ({ ...prev, businessName: e.target.value }))}
                      className="input-field bg-gray-50"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      PAN Number
                    </label>
                    <input
                      type="text"
                      value={formData.panNumber}
                      readOnly
                      className="input-field bg-gray-100"
                    />
                  </div>
                </>
              )}
            </div>
          )}

          {/* Basic Information */}
          <div>
            <h3 className="font-medium text-gray-900 mb-3">Basic Information</h3>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Full Name <span className="text-red-500">*</span>
                </label>
                <input
                  type="text"
                  value={formData.fullName}
                  onChange={e => setFormData(prev => ({ ...prev, fullName: e.target.value }))}
                  placeholder="Enter name"
                  className="input-field"
                  required
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Mobile Number <span className="text-red-500">*</span>
                </label>
                <input
                  type="tel"
                  value={formData.mobileNumber}
                  onChange={e => setFormData(prev => ({ ...prev, mobileNumber: e.target.value }))}
                  placeholder="9876543210"
                  maxLength={10}
                  className="input-field"
                  required
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Email Address
                </label>
                <input
                  type="email"
                  value={formData.email}
                  onChange={e => setFormData(prev => ({ ...prev, email: e.target.value }))}
                  placeholder="email@example.com"
                  className="input-field"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Date of Birth
                </label>
                <input
                  type="date"
                  value={formData.dateOfBirth}
                  onChange={e => setFormData(prev => ({ ...prev, dateOfBirth: e.target.value }))}
                  className="input-field"
                />
              </div>
              <div className="col-span-2">
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Anniversary
                </label>
                <input
                  type="date"
                  value={formData.anniversary}
                  onChange={e => setFormData(prev => ({ ...prev, anniversary: e.target.value }))}
                  className="input-field"
                />
              </div>
            </div>
          </div>

          {/* Address */}
          <div>
            <h3 className="font-medium text-gray-900 mb-3">Address</h3>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Street Address
                </label>
                <input
                  type="text"
                  value={formData.address}
                  onChange={e => setFormData(prev => ({ ...prev, address: e.target.value }))}
                  placeholder="Street address"
                  className="input-field"
                />
              </div>
              <div className="grid grid-cols-3 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Pincode
                  </label>
                  <input
                    type="text"
                    value={formData.pincode}
                    onChange={e => setFormData(prev => ({ ...prev, pincode: e.target.value }))}
                    placeholder="410001"
                    maxLength={6}
                    className="input-field"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    City
                  </label>
                  <input
                    type="text"
                    value={formData.city}
                    onChange={e => setFormData(prev => ({ ...prev, city: e.target.value }))}
                    placeholder="City"
                    className="input-field"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    State
                  </label>
                  <select
                    value={formData.state}
                    onChange={e => setFormData(prev => ({ ...prev, state: e.target.value }))}
                    className="input-field"
                  >
                    <option value="">Select State</option>
                    {INDIAN_STATES.map(state => (
                      <option key={state} value={state}>{state}</option>
                    ))}
                  </select>
                </div>
              </div>
            </div>
          </div>

          {/* Patient Details */}
          <div>
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-medium text-gray-900">
                Patient Details <span className="text-red-500">*</span>
              </h3>
              <button
                type="button"
                onClick={() => {
                  setNewPatient(prev => ({ ...prev, mobile: formData.mobileNumber }));
                  setShowAddPatient(true);
                }}
                className="text-sm text-bv-red-600 hover:text-bv-red-700 flex items-center gap-1"
              >
                <Plus className="w-4 h-4" />
                Add Patient
              </button>
            </div>

            {/* Patient List */}
            {formData.patients.length > 0 && (
              <div className="space-y-2 mb-4">
                {formData.patients.map((patient) => (
                  <div
                    key={patient.id}
                    className="flex items-center justify-between p-3 bg-gray-50 rounded-lg"
                  >
                    <div>
                      <p className="font-medium text-gray-900">{patient.name}</p>
                      <p className="text-sm text-gray-500">
                        {patient.relation} • {patient.mobile}
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={() => handleRemovePatient(patient.id)}
                      className="p-1 text-red-500 hover:text-red-700"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                ))}
              </div>
            )}

            {/* Add Patient Form */}
            {showAddPatient && (
              <div className="bg-yellow-50 rounded-lg p-4 space-y-4">
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      Patient Name <span className="text-red-500">*</span>
                    </label>
                    <input
                      type="text"
                      value={newPatient.name}
                      onChange={e => setNewPatient(prev => ({ ...prev, name: e.target.value }))}
                      placeholder="Patient name"
                      className="input-field"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      Mobile <span className="text-red-500">*</span>
                    </label>
                    <input
                      type="tel"
                      value={newPatient.mobile}
                      onChange={e => setNewPatient(prev => ({ ...prev, mobile: e.target.value }))}
                      placeholder="9876543210"
                      className="input-field"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      Email
                    </label>
                    <input
                      type="email"
                      value={newPatient.email}
                      onChange={e => setNewPatient(prev => ({ ...prev, email: e.target.value }))}
                      placeholder="email@example.com"
                      className="input-field"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      Date of Birth
                    </label>
                    <input
                      type="date"
                      value={newPatient.dateOfBirth}
                      onChange={e => setNewPatient(prev => ({ ...prev, dateOfBirth: e.target.value }))}
                      className="input-field"
                    />
                  </div>
                  <div className="col-span-2">
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      Relation
                    </label>
                    <select
                      value={newPatient.relation}
                      onChange={e => setNewPatient(prev => ({ ...prev, relation: e.target.value }))}
                      className="input-field"
                    >
                      {RELATIONS.map(rel => (
                        <option key={rel} value={rel}>{rel}</option>
                      ))}
                    </select>
                  </div>
                </div>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={handleAddPatient}
                    disabled={!newPatient.name.trim()}
                    className="btn-primary text-sm disabled:opacity-50"
                  >
                    Add Patient
                  </button>
                  <button
                    type="button"
                    onClick={() => setShowAddPatient(false)}
                    className="btn-outline text-sm"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}

            {formData.patients.length === 0 && !showAddPatient && (
              <p className="text-sm text-gray-500 text-center py-4 bg-gray-50 rounded-lg">
                No patients added yet. Click "Add Patient" to add one.
              </p>
            )}
          </div>
        </form>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 p-4 border-t border-gray-200 bg-gray-50">
          <button
            type="button"
            onClick={onClose}
            className="btn-outline"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={isSaving || !formData.fullName || !formData.mobileNumber || (formData.customerType === 'B2B' && !gstVerified)}
            className="btn-primary flex items-center gap-2 disabled:opacity-50"
          >
            {isSaving && <Loader2 className="w-4 h-4 animate-spin" />}
            Create Customer
          </button>
        </div>
      </div>
    </div>
  );
}

export default AddCustomerModal;

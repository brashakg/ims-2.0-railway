// ============================================================================
// IMS 2.0 - Shared customer-identity form body
// ============================================================================
// The reusable FORM BODY behind every "create a customer" door. It renders the
// full identity field set:
//   - B2C / B2B toggle
//   - B2B GST verify + auto-filled business name / PAN
//   - name, mobile (with 10-digit normalise), email, DOB, anniversary
//   - marketing opt-in + DPDP data-storage consent
//   - street address / pincode / city / state
//   - the patients sub-form (add / remove, "Self" auto-copies the account holder)
//
// It is a CONTROLLED component: the parent owns the `CustomerFormData` value and
// receives every edit via `onChange`. Both the POS "Add Customer" modal and the
// Clinical "New Patient Intake" modal render this, so the two doors capture the
// EXACT same fields with the EXACT same labels and validation — full parity.
//
// The consent wording (fetched from Marketing) and the live mobile-format error
// are passed in so the parent can surface / stamp them; everything else
// (GST verify state, the in-progress "add patient" row) is local to this body.

import { useEffect, useState } from 'react';
import {
  Plus,
  Trash2,
  CheckCircle,
  AlertCircle,
  Building2,
  User,
  Copy,
} from 'lucide-react';
import clsx from 'clsx';
import type { CustomerFormData, PatientFormData } from '../../utils/customerPayload';
import {
  applyCustomerToPatient,
  hasCopyableCustomerFields,
  copyWouldOverwrite,
} from '../../utils/patientFromCustomer';

// ============================================================================
// Constants (shared across every door)
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

const emptyPatient = (): PatientFormData => ({
  id: '',
  name: '',
  mobile: '',
  email: '',
  dateOfBirth: '',
  relation: 'Self',
});

// ============================================================================
// Props
// ============================================================================

interface CustomerIdentityFieldsProps {
  value: CustomerFormData;
  onChange: (next: CustomerFormData) => void;
  /** DPDP consent wording to show under the data-consent checkbox. */
  consentText?: string;
  /** Live mobile-format error (owned by the parent's submit validation). */
  mobileError?: string | null;
  /** Clear the parent's mobile error when the operator edits the field. */
  onMobileErrorClear?: () => void;
  /** Parent-driven flag that the customer typed an invalid B2B GST — so the
   *  parent can block submit. Set via `onGstVerifiedChange`. */
  onGstVerifiedChange?: (verified: boolean | null) => void;
}

// ============================================================================
// Component
// ============================================================================

export function CustomerIdentityFields({
  value: formData,
  onChange,
  consentText,
  mobileError,
  onMobileErrorClear,
  onGstVerifiedChange,
}: CustomerIdentityFieldsProps) {
  // Helper: patch the controlled value.
  const patch = (partial: Partial<CustomerFormData>) => onChange({ ...formData, ...partial });

  const [gstVerified, setGstVerified] = useState<boolean | null>(null);
  const [gstError, setGstError] = useState<string | null>(null);

  const [showAddPatient, setShowAddPatient] = useState(false);
  const [newPatient, setNewPatient] = useState<PatientFormData>(emptyPatient());

  // Bubble the GST-verified state up so the parent can gate submit for B2B.
  useEffect(() => {
    onGstVerifiedChange?.(gstVerified);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gstVerified]);

  // GST Verification — validates format locally (no external GST API available).
  const verifyGST = () => {
    if (!formData.gstNumber || formData.gstNumber.length !== 15) {
      setGstError('GST number must be 15 characters');
      return;
    }
    setGstError(null);
    setGstVerified(null);

    const gstRegex = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$/;
    const isValid = gstRegex.test(formData.gstNumber.toUpperCase());

    if (isValid) {
      const stateCode = formData.gstNumber.substring(0, 2);
      const panNumber = formData.gstNumber.substring(2, 12);
      const stateMap: Record<string, string> = {
        '01': 'Jammu and Kashmir', '02': 'Himachal Pradesh', '03': 'Punjab',
        '04': 'Chandigarh', '05': 'Uttarakhand', '06': 'Haryana',
        '07': 'Delhi', '08': 'Rajasthan', '09': 'Uttar Pradesh',
        '10': 'Bihar', '11': 'Sikkim', '12': 'Arunachal Pradesh',
        '27': 'Maharashtra', '29': 'Karnataka', '33': 'Tamil Nadu',
      };
      patch({
        businessName: formData.businessName || `Business Entity (${formData.gstNumber.substring(2, 7)})`,
        panNumber,
        state: formData.state || stateMap[stateCode] || 'Maharashtra',
      });
      setGstVerified(true);
    } else {
      setGstError('Invalid GST number format');
      setGstVerified(false);
    }
  };

  // The parent customer's details, as the patient-copy helper expects them.
  const customerForCopy = {
    name: formData.fullName,
    mobile: formData.mobileNumber,
    email: formData.email,
    dateOfBirth: formData.dateOfBirth,
  };
  const canCopyFromCustomer = hasCopyableCustomerFields(customerForCopy);

  // Keep an in-progress "Self" patient synced to the account holder so the
  // operator never re-types the same details. Non-destructive-ish: overwrites on
  // Self (the patient IS the account holder), clears when the customer clears.
  useEffect(() => {
    if (newPatient.relation === 'Self') {
      if (hasCopyableCustomerFields(customerForCopy)) {
        setNewPatient((prev) => applyCustomerToPatient(prev, customerForCopy, true));
      } else {
        setNewPatient((prev) => ({ ...prev, name: '', mobile: '', email: '', dateOfBirth: '' }));
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [formData.fullName, formData.mobileNumber, formData.email, formData.dateOfBirth, newPatient.relation]);

  const handleCopyFromCustomer = () => {
    if (!canCopyFromCustomer) return;
    const wouldOverwrite = copyWouldOverwrite(newPatient, customerForCopy);
    const overwrite =
      wouldOverwrite &&
      window.confirm(
        'Some patient fields are already filled. Overwrite them with the customer’s details?'
      );
    setNewPatient((prev) => applyCustomerToPatient(prev, customerForCopy, !overwrite));
  };

  const handleRelationChange = (relation: string) => {
    setNewPatient((prev) => {
      const base = { ...prev, relation };
      if (relation === 'Self' && canCopyFromCustomer) {
        return applyCustomerToPatient(base, customerForCopy, true);
      }
      return base;
    });
  };

  const handleAddPatient = () => {
    if (!newPatient.name.trim()) return;
    patch({
      patients: [...formData.patients, { ...newPatient, id: `patient-${Date.now()}` }],
    });
    setNewPatient({ ...emptyPatient(), mobile: formData.mobileNumber });
    setShowAddPatient(false);
  };

  const handleRemovePatient = (patientId: string) => {
    patch({ patients: formData.patients.filter((p) => p.id !== patientId) });
  };

  return (
    <div className="space-y-6">
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
              onChange={() => patch({ customerType: 'B2C' })}
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
              onChange={() => patch({ customerType: 'B2B' })}
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
                onChange={(e) => {
                  patch({ gstNumber: e.target.value.toUpperCase() });
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
                disabled={!formData.gstNumber}
                className="btn-primary flex items-center gap-2 disabled:opacity-50"
              >
                {gstVerified ? <CheckCircle className="w-4 h-4" /> : 'Verify'}
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

          {gstVerified && (
            <>
              <div>
                <label htmlFor="business-name" className="block text-sm font-medium text-gray-700 mb-1">
                  Business Name
                </label>
                <input
                  id="business-name"
                  type="text"
                  value={formData.businessName}
                  onChange={(e) => patch({ businessName: e.target.value })}
                  title="Business Name"
                  placeholder="Business Name"
                  className="input-field bg-gray-50"
                />
              </div>
              <div>
                <label htmlFor="pan-number" className="block text-sm font-medium text-gray-700 mb-1">
                  PAN Number
                </label>
                <input
                  id="pan-number"
                  type="text"
                  value={formData.panNumber}
                  readOnly
                  title="PAN Number"
                  placeholder="PAN Number"
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
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Full Name <span className="text-red-500">*</span>
            </label>
            <input
              type="text"
              value={formData.fullName}
              onChange={(e) => patch({ fullName: e.target.value })}
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
              onChange={(e) => {
                // Strip non-digits inline and cap at 10 digits so a pasted
                // "+919876543210" survives as "9876543210".
                const digits = e.target.value.replace(/\D/g, '').slice(-10);
                patch({ mobileNumber: digits });
                onMobileErrorClear?.();
              }}
              placeholder="9876543210"
              maxLength={10}
              className="input-field"
              required
            />
            {mobileError && (
              <p className="text-sm text-red-600 mt-1 flex items-center gap-1">
                <AlertCircle className="w-4 h-4" />
                {mobileError}
              </p>
            )}
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Email Address</label>
            <input
              type="email"
              value={formData.email}
              onChange={(e) => patch({ email: e.target.value })}
              placeholder="email@example.com"
              className="input-field"
            />
          </div>
          <div>
            <label htmlFor="customer-dob" className="block text-sm font-medium text-gray-700 mb-1">
              Date of Birth
            </label>
            <input
              id="customer-dob"
              type="date"
              value={formData.dateOfBirth}
              onChange={(e) => patch({ dateOfBirth: e.target.value })}
              max={new Date().toISOString().slice(0, 10)}
              title="Date of Birth"
              placeholder="Date of Birth"
              className="input-field"
            />
          </div>
          <div className="col-span-2">
            <label htmlFor="customer-anniversary" className="block text-sm font-medium text-gray-700 mb-1">
              Anniversary
            </label>
            <input
              id="customer-anniversary"
              type="date"
              value={formData.anniversary}
              onChange={(e) => patch({ anniversary: e.target.value })}
              title="Anniversary"
              placeholder="Anniversary"
              className="input-field"
            />
          </div>
          {/* Marketing opt-in — default on. */}
          <div className="col-span-2">
            <label className="flex items-start gap-2 cursor-pointer p-2 rounded-lg hover:bg-gray-50">
              <input
                type="checkbox"
                checked={formData.marketingConsent}
                onChange={(e) => patch({ marketingConsent: e.target.checked })}
                className="mt-0.5 w-4 h-4 text-bv-red-600 rounded border-gray-300 focus:ring-bv-red-500"
              />
              <span className="text-sm">
                <span className="font-medium text-gray-900">Receive marketing messages</span>
                <span className="block text-xs text-gray-500 mt-0.5">
                  Birthday wishes, Rx renewal reminders, and offers via SMS / WhatsApp. Customer can opt out anytime.
                </span>
              </span>
            </label>
          </div>
          {/* DPDP Act 2023 data-storage consent. */}
          <div className="col-span-2">
            <label className="flex items-start gap-2 cursor-pointer p-2 rounded-lg hover:bg-gray-50">
              <input
                type="checkbox"
                checked={formData.dataConsent}
                onChange={(e) => patch({ dataConsent: e.target.checked })}
                className="mt-0.5 w-4 h-4 text-bv-red-600 rounded border-gray-300 focus:ring-bv-red-500"
              />
              <span className="text-sm">
                <span className="font-medium text-gray-900">Customer consents to data storage</span>
                <span className="block text-xs text-gray-500 mt-0.5">
                  {consentText || 'Customer agrees we may store and use their details to provide optical services and reminders. Editable under Marketing.'}
                </span>
              </span>
            </label>
          </div>
        </div>
      </div>

      {/* Address */}
      <div>
        <h3 className="font-medium text-gray-900 mb-3">Address</h3>
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Street Address</label>
            <input
              type="text"
              value={formData.address}
              onChange={(e) => patch({ address: e.target.value })}
              placeholder="Street address"
              className="input-field"
            />
          </div>
          <div className="grid grid-cols-1 tablet:grid-cols-3 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Pincode</label>
              <input
                type="text"
                value={formData.pincode}
                onChange={(e) => patch({ pincode: e.target.value })}
                placeholder="410001"
                maxLength={6}
                className="input-field"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">City</label>
              <input
                type="text"
                value={formData.city}
                onChange={(e) => patch({ city: e.target.value })}
                placeholder="City"
                className="input-field"
              />
            </div>
            <div>
              <label htmlFor="customer-state" className="block text-sm font-medium text-gray-700 mb-1">
                State
              </label>
              <select
                id="customer-state"
                value={formData.state}
                onChange={(e) => patch({ state: e.target.value })}
                className="input-field"
                title="State"
              >
                <option value="">Select State</option>
                {INDIAN_STATES.map((state) => (
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
              // The blank patient defaults to relation "Self" (the account
              // holder), so prefill from the customer on open — blanks only.
              setNewPatient((prev) =>
                prev.relation === 'Self' && canCopyFromCustomer
                  ? applyCustomerToPatient(prev, customerForCopy, true)
                  : { ...prev, mobile: prev.mobile || formData.mobileNumber }
              );
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
                  title="Remove patient"
                  aria-label="Remove patient"
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
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Patient Name <span className="text-red-500">*</span>
                </label>
                <input
                  type="text"
                  value={newPatient.name}
                  onChange={(e) => setNewPatient((prev) => ({ ...prev, name: e.target.value }))}
                  placeholder="Patient name"
                  className={clsx('input-field', newPatient.relation === 'Self' && 'bg-gray-100 cursor-not-allowed text-gray-500')}
                  disabled={newPatient.relation === 'Self'}
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Mobile <span className="text-red-500">*</span>
                </label>
                <input
                  type="tel"
                  value={newPatient.mobile}
                  onChange={(e) => setNewPatient((prev) => ({ ...prev, mobile: e.target.value }))}
                  placeholder="9876543210"
                  className={clsx('input-field', newPatient.relation === 'Self' && 'bg-gray-100 cursor-not-allowed text-gray-500')}
                  disabled={newPatient.relation === 'Self'}
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Email</label>
                <input
                  type="email"
                  value={newPatient.email}
                  onChange={(e) => setNewPatient((prev) => ({ ...prev, email: e.target.value }))}
                  placeholder="email@example.com"
                  className={clsx('input-field', newPatient.relation === 'Self' && 'bg-gray-100 cursor-not-allowed text-gray-500')}
                  disabled={newPatient.relation === 'Self'}
                />
              </div>
              <div>
                <label htmlFor="patient-dob" className="block text-sm font-medium text-gray-700 mb-1">
                  Date of Birth
                </label>
                <input
                  id="patient-dob"
                  type="date"
                  value={newPatient.dateOfBirth}
                  onChange={(e) => setNewPatient((prev) => ({ ...prev, dateOfBirth: e.target.value }))}
                  max={new Date().toISOString().slice(0, 10)}
                  title="Patient Date of Birth"
                  placeholder="Patient Date of Birth"
                  className={clsx('input-field', newPatient.relation === 'Self' && 'bg-gray-100 cursor-not-allowed text-gray-500')}
                  disabled={newPatient.relation === 'Self'}
                />
              </div>
              <div className="col-span-2">
                <div className="flex items-center justify-between mb-1">
                  <label className="block text-sm font-medium text-gray-700">Relation</label>
                  {canCopyFromCustomer && (
                    <button
                      type="button"
                      onClick={handleCopyFromCustomer}
                      className="text-xs text-bv-red-600 hover:text-bv-red-700 flex items-center gap-1"
                      title="Fill this patient from the customer's details above"
                    >
                      <Copy className="w-3.5 h-3.5" />
                      Copy from customer details
                    </button>
                  )}
                </div>
                <select
                  id="patient-relation"
                  value={newPatient.relation}
                  onChange={(e) => handleRelationChange(e.target.value)}
                  className="input-field"
                  title="Relation"
                >
                  {RELATIONS.map((rel) => (
                    <option key={rel} value={rel}>{rel}</option>
                  ))}
                </select>
                {newPatient.relation === 'Self' && canCopyFromCustomer && (
                  <p className="text-xs text-gray-500 mt-1">
                    This patient is the account holder — their details were filled from the customer above.
                  </p>
                )}
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
    </div>
  );
}

export default CustomerIdentityFields;

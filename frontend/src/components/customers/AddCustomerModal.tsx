// ============================================================================
// IMS 2.0 - Add Customer Modal
// ============================================================================
// B2C/B2B customer creation with GST verification

import { useState, useEffect, useCallback } from 'react';
import { useDebounce } from '../../hooks/useDebounce';
import { customerApi } from '../../services/api';
import {
  X,
  Plus,
  Trash2,
  Loader2,
  Search,
  CheckCircle,
  AlertCircle,
  Building2,
  User,
  Copy,
} from 'lucide-react';
import clsx from 'clsx';
import {
  applyCustomerToPatient,
  hasCopyableCustomerFields,
  copyWouldOverwrite,
} from '../../utils/patientFromCustomer';

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
  // Marketing consent — opt-in default, operator flips off only on
  // explicit decline. Drives birthday / Rx-expiry / WhatsApp campaigns.
  marketingConsent: boolean;
  // DPDP Act 2023 data-storage consent — separate from marketing. Default on;
  // the operator ticks it after telling the customer. Carries the version of
  // the wording shown so the agreement is provable.
  dataConsent: boolean;
  dataConsentTextVersion?: string;
}

interface AddCustomerModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSave: (customer: CustomerFormData) => Promise<void>;
  /** Phase 6.13 — optional pre-fill when the user lands here from the
   *  "Customer not found" fallback in the Clinical search modal. If the
   *  string is all-digits we assume phone, otherwise name. */
  initialName?: string;
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

export function AddCustomerModal({ isOpen, onClose, onSave, initialName }: AddCustomerModalProps) {
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
    marketingConsent: true,
    dataConsent: true,
  });

  const [gstVerified, setGstVerified] = useState<boolean | null>(null);
  const [gstError, setGstError] = useState<string | null>(null);
  const [mobileError, setMobileError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  // DPDP consent wording (editable under Marketing). Fetched on open so the
  // customer's stored consent records the exact text+version they were shown.
  const [consentText, setConsentText] = useState('');
  const [consentVersion, setConsentVersion] = useState<string | undefined>();
  const [searchQuery, setSearchQuery] = useState('');
  const debouncedSearchQuery = useDebounce(searchQuery, 400);
  const [searchResults, setSearchResults] = useState<any[]>([]);
  const [searching, setSearching] = useState(false);
  const [showSearch, setShowSearch] = useState(true);

  // Phase 6.13 — hydrate name/phone when opened with an initialName
  // (from the Clinical "customer not found" fallback). Numeric strings
  // land in mobile; anything else lands in full-name.
  useEffect(() => {
    if (!isOpen || !initialName) return;
    const trimmed = initialName.trim();
    if (!trimmed) return;
    const isNumeric = /^\d{5,}$/.test(trimmed);
    setFormData((prev) => ({
      ...prev,
      ...(isNumeric ? { mobileNumber: trimmed } : { fullName: trimmed }),
    }));
    // Pre-seed the search box with whatever they typed in the previous
    // modal so existing matches surface immediately instead of forcing
    // them to re-type.
    setSearchQuery(trimmed);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, initialName]);
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
        marketingConsent: true,
        dataConsent: true,
      });
      setGstVerified(null);
      setGstError(null);
      setMobileError(null);
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
      // Pull the current consent wording (fail-soft: a fetch error just leaves
      // the inline default text; the checkbox still works).
      customerApi.getConsentText?.()
        .then((r) => {
          if (r?.text) setConsentText(r.text);
          if (r?.version) setConsentVersion(r.version);
        })
        .catch(() => { /* keep default */ });
    }
  }, [isOpen]);

  // GST Verification — validates format locally (no external GST API available)
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

      setFormData(prev => ({
        ...prev,
        businessName: prev.businessName || `Business Entity (${formData.gstNumber.substring(2, 7)})`,
        panNumber: panNumber,
        state: prev.state || stateMap[stateCode] || 'Maharashtra',
      }));
      setGstVerified(true);
    } else {
      setGstError('Invalid GST number format');
      setGstVerified(false);
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

  // The parent customer's details, as the patient-copy helper expects them.
  // `formData` is the customer being created/edited in this very modal.
  const customerForCopy = {
    name: formData.fullName,
    mobile: formData.mobileNumber,
    email: formData.email,
    dateOfBirth: formData.dateOfBirth,
  };
  const canCopyFromCustomer = hasCopyableCustomerFields(customerForCopy);

  // One-tap "Copy from customer details" into the in-progress patient. Fills
  // only blank patient fields by default; if the operator already typed
  // something that differs, confirm before overwriting so nothing is lost.
  const handleCopyFromCustomer = () => {
    if (!canCopyFromCustomer) return;
    const wouldOverwrite = copyWouldOverwrite(newPatient, customerForCopy);
    const overwrite =
      wouldOverwrite &&
      window.confirm(
        'Some patient fields are already filled. Overwrite them with the customer’s details?'
      );
    setNewPatient(prev => applyCustomerToPatient(prev, customerForCopy, !overwrite));
  };

  // When relation becomes "Self", the patient IS the account holder, so offer
  // to prefill from the customer. Non-destructive: only fills blanks (never
  // clobbers something the operator already typed) so it's safe to fire on
  // selection without a prompt.
  const handleRelationChange = (relation: string) => {
    setNewPatient(prev => {
      const base = { ...prev, relation };
      if (relation === 'Self' && canCopyFromCustomer) {
        return applyCustomerToPatient(base, customerForCopy, true);
      }
      return base;
    });
  };

  // Search API call
  const performSearch = useCallback(async (query: string) => {
    if (query.length < 2) { setSearchResults([]); return; }
    setSearching(true);
    try {
      // Phone-shaped queries hit the dedicated phone endpoint;
      // anything else uses the generic ?search= list endpoint.
      // Both are routed through the shared axios client so they
      // resolve correctly on Vercel (was hitting a hardcoded fallback
      // URL via raw fetch before — fragile if the prod URL changes
      // and missing the auth interceptor for some endpoints).
      const isPhone = /^\d{3,}$/.test(query.trim());
      const data = isPhone
        ? await customerApi.searchByPhone(query)
        : await customerApi.getCustomers({ search: query, limit: 10 });
      const list = Array.isArray(data)
        ? data
        : data?.customers || (data?.customer ? [data.customer] : []);
      setSearchResults(list);
    } catch { setSearchResults([]); }
    setSearching(false);
  }, []);

  // Trigger search when debounced query changes
  useEffect(() => {
    if (debouncedSearchQuery.length >= 2) {
      performSearch(debouncedSearchQuery);
      setShowSearch(true);
    } else {
      setSearchResults([]);
    }
  }, [debouncedSearchQuery, performSearch]);

  // Kept for backward compatibility — now just updates searchQuery (debouncing handled by hook)
  const handleSearch = (query: string) => {
    setSearchQuery(query);
  };

  // Auto-search when typing in name or mobile fields
  const triggerAutoSearch = (value: string) => {
    setSearchQuery(value);
  };

  const selectExistingCustomer = (customer: any) => {
    setFormData(prev => ({
      ...prev,
      fullName: customer.name || customer.full_name || '',
      mobileNumber: customer.mobile || customer.phone || '',
      email: customer.email || '',
      city: customer.city || '',
      state: customer.state || '',
      pincode: customer.pincode || '',
      gstNumber: customer.gstin || customer.gst_number || '',
      businessName: customer.business_name || '',
      customerType: customer.customer_type === 'B2B' ? 'B2B' : 'B2C',
    }));
    setMobileError(null);
    // Clear the dropdown but keep the search bar visible so the operator
    // can switch to a different match if they picked the wrong one. The
    // parent's lookup-first save flow will reuse the existing record by
    // mobile regardless of whether they hit save now or after editing.
    setSearchResults([]);
    setSearchQuery('');
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    // Validation
    if (!formData.fullName.trim() || !formData.mobileNumber.trim()) {
      return;
    }

    // Backend mobile field requires exactly 10 digits. Strip any pasted
    // country code, spaces, dashes, brackets etc. before sending; if the
    // user landed on a longer number ("+919876543210") keep the last 10.
    const digits = formData.mobileNumber.replace(/\D/g, '');
    const tenDigit = digits.slice(-10);
    if (tenDigit.length !== 10) {
      setMobileError('Mobile number must contain 10 digits');
      return;
    }
    setMobileError(null);
    const sanitizedFormData: CustomerFormData = {
      ...formData,
      mobileNumber: tenDigit,
      // Stamp the consent wording version the operator actually saw, so the
      // stored agreement is traceable to that exact text.
      dataConsentTextVersion: consentVersion,
      patients: formData.patients.map(p => ({
        ...p,
        mobile: p.mobile ? (p.mobile.replace(/\D/g, '').slice(-10) || p.mobile) : p.mobile,
      })),
    };

    if (formData.customerType === 'B2B' && !gstVerified) {
      setGstError('Please verify GST number first');
      return;
    }

    setIsSaving(true);
    try {
      await onSave(sanitizedFormData);
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
      <div className="bg-white rounded-xl shadow-xl w-full max-w-4xl max-h-[90vh] overflow-hidden flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-gray-200">
          <h2 className="text-lg font-semibold text-gray-900">Add New Customer</h2>
          <button
            onClick={onClose}
            className="p-2 text-gray-500 hover:text-gray-600 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Customer Search — always visible; the duplicate-tolerant
            parent flow will use whatever exists if you skip past it */}
        {showSearch && (
          <div className="px-4 pt-3 pb-3 border-b border-gray-100 bg-amber-50/40">
            <p className="text-xs font-medium text-gray-600 mb-1.5">
              Search first — pick an existing customer to auto-fill their details (no duplicate created).
            </p>
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => handleSearch(e.target.value)}
                placeholder="Type name or 10-digit phone..."
                className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-bv-red-500 focus:border-transparent"
              />
              {searching && <Loader2 className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 animate-spin text-gray-500" />}
            </div>
            {searchResults.length > 0 && (
              <div className="mt-2 max-h-48 overflow-y-auto rounded-lg border border-amber-300 bg-white shadow-sm">
                <p className="px-3 py-1.5 text-xs font-semibold text-amber-700 bg-amber-50 border-b border-amber-200 sticky top-0">
                  {searchResults.length} existing customer{searchResults.length === 1 ? '' : 's'} found — click to use
                </p>
                {searchResults.map((cust: any, i: number) => (
                  <button
                    key={cust._id || cust.customer_id || i}
                    type="button"
                    onClick={() => selectExistingCustomer(cust)}
                    className="w-full text-left px-3 py-2 text-sm hover:bg-amber-50 flex justify-between items-center border-b last:border-b-0"
                  >
                    <span>
                      <span className="font-medium text-gray-900">{cust.name || cust.full_name}</span>
                      <span className="text-gray-500 ml-2">{cust.mobile || cust.phone}</span>
                    </span>
                    <span className="text-xs font-semibold text-bv-red-600">Use →</span>
                  </button>
                ))}
              </div>
            )}
            {searchQuery.length >= 3 && searchResults.length === 0 && !searching && (
              <p className="mt-1 text-xs text-gray-500">No match found. Fill the form below to create new.</p>
            )}
          </div>
        )}

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
                    disabled={!formData.gstNumber}
                    className="btn-primary flex items-center gap-2 disabled:opacity-50"
                  >
                    {gstVerified ? (
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
                  onChange={e => {
                    setFormData(prev => ({ ...prev, fullName: e.target.value }));
                    triggerAutoSearch(e.target.value);
                  }}
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
                  onChange={e => {
                    // Strip non-digits inline and cap at 10 digits so a
                    // pasted "+919876543210" survives as "9876543210"
                    // instead of getting hard-truncated to "+919876543".
                    const digits = e.target.value.replace(/\D/g, '').slice(-10);
                    setFormData(prev => ({ ...prev, mobileNumber: digits }));
                    setMobileError(null);
                    triggerAutoSearch(digits);
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
              {/* Marketing opt-in — default on. Flip off only if the
                  customer explicitly declines on the spot. Drives
                  birthday / Rx-expiry / WhatsApp campaigns. */}
              <div className="col-span-2">
                <label className="flex items-start gap-2 cursor-pointer p-2 rounded-lg hover:bg-gray-50">
                  <input
                    type="checkbox"
                    checked={formData.marketingConsent}
                    onChange={e => setFormData(prev => ({ ...prev, marketingConsent: e.target.checked }))}
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
              {/* DPDP Act 2023 data-storage consent. Separate from marketing —
                  records that the customer agreed to us holding their data.
                  Default on; the shown wording is editable under Marketing. */}
              <div className="col-span-2">
                <label className="flex items-start gap-2 cursor-pointer p-2 rounded-lg hover:bg-gray-50">
                  <input
                    type="checkbox"
                    checked={formData.dataConsent}
                    onChange={e => setFormData(prev => ({ ...prev, dataConsent: e.target.checked }))}
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
              <div className="grid grid-cols-1 tablet:grid-cols-3 gap-4">
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
                  // The blank patient defaults to relation "Self" (the account
                  // holder), so prefill from the customer on open — blanks only,
                  // never clobbering anything already typed.
                  setNewPatient(prev =>
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
                    <div className="flex items-center justify-between mb-1">
                      <label className="block text-sm font-medium text-gray-700">
                        Relation
                      </label>
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
                      value={newPatient.relation}
                      onChange={e => handleRelationChange(e.target.value)}
                      className="input-field"
                    >
                      {RELATIONS.map(rel => (
                        <option key={rel} value={rel}>{rel}</option>
                      ))}
                    </select>
                    {newPatient.relation === 'Self' && canCopyFromCustomer && (
                      <p className="text-xs text-gray-500 mt-1">
                        This patient is the account holder — their details were filled
                        from the customer above.
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

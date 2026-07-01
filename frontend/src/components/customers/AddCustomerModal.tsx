// ============================================================================
// IMS 2.0 - Add Customer Modal
// ============================================================================
// B2C/B2B customer creation with GST verification. The identity FORM BODY lives
// in the shared <CustomerIdentityFields> so this exact field set is captured by
// every door (POS, Customers page, Clinical intake). This modal owns the chrome:
// the header, the "search first" dedup box, submit validation, and the footer.

import { useState, useEffect, useCallback } from 'react';
import { useDebounce } from '../../hooks/useDebounce';
import { customerApi } from '../../services/api';
import { X, Loader2, Search } from 'lucide-react';
import { CustomerIdentityFields } from './CustomerIdentityFields';
import {
  emptyCustomerFormData,
  type CustomerFormData,
  type PatientFormData,
} from '../../utils/customerPayload';

// Re-export the shared form types so existing importers
// (`import { CustomerFormData } from '.../AddCustomerModal'`) keep working while
// the canonical home is utils/customerPayload.ts.
export type { CustomerFormData, PatientFormData };

interface AddCustomerModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSave: (customer: CustomerFormData) => Promise<void>;
  /** Phase 6.13 — optional pre-fill when the user lands here from the
   *  "Customer not found" fallback in the Clinical search modal. If the
   *  string is all-digits we assume phone, otherwise name. */
  initialName?: string;
}

export function AddCustomerModal({ isOpen, onClose, onSave, initialName }: AddCustomerModalProps) {
  const [formData, setFormData] = useState<CustomerFormData>(emptyCustomerFormData());

  const [gstVerified, setGstVerified] = useState<boolean | null>(null);
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
  const [showSearch] = useState(true);

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
    setSearchQuery(trimmed);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, initialName]);

  // Reset form state when modal opens to prevent state leakage between modules.
  useEffect(() => {
    if (isOpen) {
      setFormData(emptyCustomerFormData());
      setGstVerified(null);
      setMobileError(null);
      setIsSaving(false);
      // Pull the current consent wording (fail-soft).
      customerApi.getConsentText?.()
        .then((r) => {
          if (r?.text) setConsentText(r.text);
          if (r?.version) setConsentVersion(r.version);
        })
        .catch(() => { /* keep default */ });
    }
  }, [isOpen]);

  // Search API call
  const performSearch = useCallback(async (query: string) => {
    if (query.length < 2) { setSearchResults([]); return; }
    setSearching(true);
    try {
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

  useEffect(() => {
    if (debouncedSearchQuery.length >= 2) {
      performSearch(debouncedSearchQuery);
    } else {
      setSearchResults([]);
    }
  }, [debouncedSearchQuery, performSearch]);

  const handleSearch = (query: string) => setSearchQuery(query);

  const selectExistingCustomer = (customer: any) => {
    setFormData((prev) => ({
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
    setSearchResults([]);
    setSearchQuery('');
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!formData.fullName.trim() || !formData.mobileNumber.trim()) {
      return;
    }

    // Backend mobile field requires exactly 10 digits.
    const tenDigit = formData.mobileNumber.replace(/\D/g, '').slice(-10);
    if (tenDigit.length !== 10) {
      setMobileError('Mobile number must contain 10 digits');
      return;
    }
    setMobileError(null);

    if (formData.customerType === 'B2B' && !gstVerified) {
      // The identity fields surface their own GST error; block submit here too.
      return;
    }

    // Stamp the consent wording version the operator saw + the normalised mobile
    // so the parent's buildCustomerCreatePayload emits the traceable, canonical
    // record. (patient mobiles are normalised inside the builder.)
    const sanitizedFormData: CustomerFormData = {
      ...formData,
      mobileNumber: tenDigit,
      dataConsentTextVersion: consentVersion,
    };

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
            title="Close"
            aria-label="Close"
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

        {/* Form — shared identity body */}
        <form onSubmit={handleSubmit} className="flex-1 overflow-y-auto p-4">
          <CustomerIdentityFields
            value={formData}
            onChange={setFormData}
            consentText={consentText}
            mobileError={mobileError}
            onMobileErrorClear={() => setMobileError(null)}
            onGstVerifiedChange={setGstVerified}
          />
        </form>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 p-4 border-t border-gray-200 bg-gray-50">
          <button type="button" onClick={onClose} className="btn-outline">
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

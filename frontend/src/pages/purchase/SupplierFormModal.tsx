// ============================================================================
// IMS 2.0 - Add / Edit Supplier Modal
// ============================================================================
// One modal for both doors. `supplier` present => edit (PUT), absent => create
// (POST). Before this the Suppliers tab had no editor at all: the pencil on
// each card was a button with no handler, so a vendor was write-once.
//
// The State box is gone when a GSTIN is present. The first two digits of a
// GSTIN ARE the state, and that state decides whether the vendor's bills are
// CGST+SGST or IGST -- asking the user to also pick one only creates a way for
// the two to disagree. The name shown back comes from the server's state-code
// list (the same one org_validation taxes with), so a mistyped GSTIN is
// visible as the wrong state before it is ever saved.

import { useState } from 'react';
import {
  CheckCircle,
  X as XIcon,
  Truck,
  AlertTriangle,
  Loader2,
} from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { vendorsApi } from '../../services/api';
import { useGstStateCodes } from '../../hooks/useGstStateCodes';
import { gstinStateCode, validateGSTNumber } from '../../constants/gst';
import type { Supplier } from './purchaseTypes';

interface SupplierFormModalProps {
  onClose: () => void;
  /** Create mode: the new supplier. */
  onCreated?: (supplier: Supplier) => void;
  /** Edit mode: the vendor being edited. */
  supplier?: Supplier;
  /** Edit mode: the saved supplier. */
  onSaved?: (supplier: Supplier) => void;
}

export function SupplierFormModal({
  onClose,
  onCreated,
  supplier,
  onSaved,
}: SupplierFormModalProps) {
  const toast = useToast();
  const isEdit = Boolean(supplier);
  const stateNames = useGstStateCodes();

  const [name, setName] = useState(supplier?.name ?? '');
  const [code, setCode] = useState(supplier?.code ?? '');
  const [contactPerson, setContactPerson] = useState(supplier?.contactPerson ?? '');
  const [phone, setPhone] = useState(supplier?.phone ?? '');
  const [email, setEmail] = useState(supplier?.email ?? '');
  const [address, setAddress] = useState(supplier?.address ?? '');
  const [city, setCity] = useState(supplier?.city ?? '');
  // Only used when there is no GSTIN. Holds the 2-digit GST code.
  const [stateCode, setStateCode] = useState(
    supplier?.gstNumber ? '' : supplier?.stateCode ?? '',
  );
  const [gst, setGST] = useState(supplier?.gstNumber ?? '');
  const [paymentTerms, setPaymentTerms] = useState(supplier?.paymentTerms ?? 30);
  const [creditLimit, setCreditLimit] = useState(supplier?.creditLimit ?? 0);
  const [gstError, setGSTError] = useState('');
  const [isSaving, setIsSaving] = useState(false);

  // The state the GSTIN itself declares. '' when there is no readable GSTIN.
  const derivedStateCode = gstinStateCode(gst);
  const derivedStateName = derivedStateCode ? stateNames[derivedStateCode] : '';
  const effectiveStateCode = derivedStateCode || stateCode;

  const gstProblem = (value: string): string => {
    if (!value) return ''; // optional - unregistered vendors have none
    if (value.length !== 15) {
      return `A GSTIN is 15 characters; this one is ${value.length}.`;
    }
    if (!validateGSTNumber(value)) {
      return 'Not a GSTIN: expected 2-digit state code + PAN + entity + Z + check digit (e.g. 27AAPFU0939F1ZV).';
    }
    if (derivedStateCode && !stateNames[derivedStateCode] && Object.keys(stateNames).length) {
      return `"${derivedStateCode}" is not an Indian GST state code - check the first two digits.`;
    }
    return '';
  };

  const handleSave = async () => {
    if (!name.trim()) {
      toast.error('Company name is required');
      return;
    }
    if (!phone.trim()) {
      toast.error('Phone number is required');
      return;
    }
    const problem = gstProblem(gst.trim().toUpperCase());
    if (problem) {
      setGSTError(problem);
      return;
    }

    // Empty box -> null, NOT undefined. JSON.stringify drops undefined keys,
    // so an unregistered-from-now-on vendor sent nothing at all, the server's
    // exclude_unset dropped it, the old GSTIN survived -- and the clear looked
    // like it had worked. null is an explicit "no GSTIN".
    const gstin = gst.trim().toUpperCase() || null;
    setIsSaving(true);
    try {
      // Annotated, NOT inferred. An un-annotated object handed to the API as a
      // variable is never excess-property-checked, so a field this form sends
      // that no client contract knows about compiles happily and is then thrown
      // away by the server -- exactly how contact_person / vendor_code were
      // collected and lost. The annotation makes `tsc -b` refuse a field that
      // is on NEITHER signature. (It cannot catch a field dropped from just one
      // of the two: an intersection keeps the property from the other side.)
      const shared: Parameters<typeof vendorsApi.updateVendor>[1] &
        Parameters<typeof vendorsApi.createVendor>[0] = {
        legal_name: name.trim(),
        trade_name: name.trim(),
        gstin_status: gstin ? 'REGISTERED' : 'UNREGISTERED',
        gstin,
        address: address.trim() || 'N/A',
        city: city.trim() || 'N/A',
        // Send the code we derived, not a typed name. The server re-derives
        // from the GSTIN anyway; sending the same thing keeps the two honest.
        state: effectiveStateCode || 'N/A',
        mobile: phone.trim(),
        email: email.trim() || undefined,
        vendor_code: code.trim().toUpperCase() || undefined,
        contact_person: contactPerson.trim() || undefined,
        credit_limit: creditLimit,
        credit_days: paymentTerms,
      };

      const saved: Supplier = {
        id: supplier?.id ?? '',
        name: name.trim(),
        code: code.trim().toUpperCase(),
        contactPerson: contactPerson.trim(),
        phone: phone.trim(),
        email: email.trim(),
        address: address.trim(),
        city: city.trim(),
        state: derivedStateName || stateNames[effectiveStateCode] || supplier?.state || '',
        stateCode: effectiveStateCode || undefined,
        gstNumber: gstin ?? '',
        paymentTerms,
        creditLimit,
        currentOutstanding: supplier?.currentOutstanding ?? 0,
        rating: supplier?.rating ?? 0,
        totalPurchases: supplier?.totalPurchases ?? 0,
        lastPurchaseDate: supplier?.lastPurchaseDate ?? '',
        performance: supplier?.performance ?? {
          onTimeDelivery: 0,
          qualityScore: 0,
          priceCompetitiveness: 0,
        },
      };

      if (isEdit && supplier) {
        await vendorsApi.updateVendor(supplier.id, shared);
        onSaved?.(saved);
        toast.success(`Supplier "${saved.name}" updated`);
      } else {
        const resp = await vendorsApi.createVendor(shared);
        saved.id = resp.vendor_id ?? `sup-${Date.now()}`;
        saved.code = saved.code || resp.vendor_id?.slice(0, 8).toUpperCase() || 'NEW';
        onCreated?.(saved);
        toast.success(`Supplier "${saved.name}" added successfully`);
      }
    } catch (error: unknown) {
      // Prefer the server's own words - it names which part of the GSTIN is
      // wrong, or that another vendor already holds it.
      const detail = (error as { response?: { data?: { detail?: unknown } } })?.response
        ?.data?.detail;
      const msg =
        typeof detail === 'string'
          ? detail
          : Array.isArray(detail) && detail[0] !== undefined
            ? String((detail[0] as { msg?: string })?.msg ?? 'Could not save supplier')
            : error instanceof Error
              ? error.message
              : 'Could not save supplier';
      toast.error(msg);
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-start justify-center z-50 p-4 overflow-y-auto">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-2xl my-8">
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b border-gray-200">
          <h2 className="text-xl font-bold text-gray-900 flex items-center gap-2">
            <Truck className="w-5 h-5 text-blue-600" />
            {isEdit ? 'Edit Supplier' : 'Add Supplier'}
          </h2>
          <button
            onClick={onClose}
            aria-label="Close"
            className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
          >
            <XIcon className="w-5 h-5 text-gray-500" />
          </button>
        </div>

        {/* Body */}
        <div className="p-6 space-y-5">
          {/* Company & Code */}
          <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
            <div>
              <label htmlFor="sup-name" className="block text-sm font-medium text-gray-700 mb-1">Company Name *</label>
              <input
                id="sup-name"
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g., Titan Eyewear Pvt Ltd"
                className="input-field"
              />
            </div>
            <div>
              <label htmlFor="sup-code" className="block text-sm font-medium text-gray-700 mb-1">Supplier Code</label>
              <input
                id="sup-code"
                type="text"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                placeholder="e.g., SUP004"
                className="input-field"
              />
            </div>
          </div>

          {/* Contact Person, Phone, Email */}
          <div className="grid grid-cols-1 tablet:grid-cols-3 gap-4">
            <div>
              <label htmlFor="sup-contact" className="block text-sm font-medium text-gray-700 mb-1">Contact Person</label>
              <input
                id="sup-contact"
                type="text"
                value={contactPerson}
                onChange={(e) => setContactPerson(e.target.value)}
                placeholder="Full name"
                className="input-field"
              />
            </div>
            <div>
              <label htmlFor="sup-phone" className="block text-sm font-medium text-gray-700 mb-1">Phone *</label>
              <input
                id="sup-phone"
                type="text"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                placeholder="+91 98765 43210"
                className="input-field"
              />
            </div>
            <div>
              <label htmlFor="sup-email" className="block text-sm font-medium text-gray-700 mb-1">Email</label>
              <input
                id="sup-email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="email@company.com"
                className="input-field"
              />
            </div>
          </div>

          {/* Address, City */}
          <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
            <div>
              <label htmlFor="sup-address" className="block text-sm font-medium text-gray-700 mb-1">Address</label>
              <input
                id="sup-address"
                type="text"
                value={address}
                onChange={(e) => setAddress(e.target.value)}
                placeholder="Street address"
                className="input-field"
              />
            </div>
            <div>
              <label htmlFor="sup-city" className="block text-sm font-medium text-gray-700 mb-1">City</label>
              <input
                id="sup-city"
                type="text"
                value={city}
                onChange={(e) => setCity(e.target.value)}
                placeholder="City"
                className="input-field"
              />
            </div>
          </div>

          {/* GST Number -> state */}
          <div>
            <label htmlFor="sup-gst" className="block text-sm font-medium text-gray-700 mb-1">GST Number</label>
            <input
              id="sup-gst"
              type="text"
              value={gst}
              onChange={(e) => {
                setGST(e.target.value.toUpperCase());
                setGSTError('');
              }}
              placeholder="e.g., 27AAPFU0939F1ZV"
              maxLength={15}
              className={`input-field ${gstError ? 'border-red-500' : ''}`}
            />
            {gstError ? (
              <p className="mt-1 text-xs text-red-600 flex items-center gap-1">
                <AlertTriangle className="w-3 h-3" />
                {gstError}
              </p>
            ) : derivedStateName ? (
              <p className="mt-1 text-xs text-gray-600">
                State (from the GSTIN):{' '}
                <span className="font-medium text-gray-900">{derivedStateName}</span>
                {' '}&mdash; purchases are taxed for this state.
              </p>
            ) : (
              <p className="mt-1 text-xs text-gray-500">
                Leave blank for an unregistered vendor. The state is read from the number.
              </p>
            )}
          </div>

          {/* State - only asked for when there is no GSTIN to read it from */}
          {!derivedStateCode && (
            <div>
              <label htmlFor="sup-state" className="block text-sm font-medium text-gray-700 mb-1">
                State (unregistered vendor)
              </label>
              <select
                id="sup-state"
                value={stateCode}
                onChange={(e) => setStateCode(e.target.value)}
                className="input-field"
              >
                <option value="">Select a state</option>
                {Object.entries(stateNames)
                  .sort(([a], [b]) => a.localeCompare(b))
                  .map(([codeValue, stateName]) => (
                    <option key={codeValue} value={codeValue}>
                      {codeValue} {stateName}
                    </option>
                  ))}
              </select>
            </div>
          )}

          {/* Payment Terms & Credit Limit */}
          <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
            <div>
              <label htmlFor="sup-terms" className="block text-sm font-medium text-gray-700 mb-1">Payment Terms (days)</label>
              <input
                id="sup-terms"
                type="number"
                min="0"
                value={paymentTerms}
                onChange={(e) => setPaymentTerms(parseInt(e.target.value) || 0)}
                className="input-field"
              />
            </div>
            <div>
              <label htmlFor="sup-credit" className="block text-sm font-medium text-gray-700 mb-1">Credit Limit ({'₹'})</label>
              <input
                id="sup-credit"
                type="number"
                min="0"
                step="10000"
                value={creditLimit}
                onChange={(e) => setCreditLimit(parseFloat(e.target.value) || 0)}
                className="input-field"
              />
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 p-6 border-t border-gray-200">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={isSaving}
            className="btn-primary flex items-center gap-2 disabled:opacity-60 disabled:cursor-not-allowed"
          >
            {isSaving ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <CheckCircle className="w-4 h-4" />
            )}
            {isSaving ? 'Saving...' : 'Save Supplier'}
          </button>
        </div>
      </div>
    </div>
  );
}

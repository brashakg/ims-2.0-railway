// ============================================================================
// IMS 2.0 - Edit Customer modal (shared: Customers page + both POS tills)
// ============================================================================
// Extracted from CustomersPage's inline modal so the tills could get customer
// edit WITHOUT a second edit form -- a rule implemented twice drifts, and this
// repo's dominant defect class is exactly that fork. This is now the ONE
// customer-edit UI.
//
// THE SERVER IS THE AUTHORITY. PUT /customers/{id} (CustomerUpdate) enforces
// B2B-requires-GSTIN, the duplicate-mobile 409, mobile format, GSTIN format.
// None of those rules is reimplemented here; a refusal is surfaced VERBATIM
// in the modal. The only client checks kept are the pre-existing UX ones
// (name/phone required, email shape) -- they gate the button, not the rule.
//
// Owner ruling 2026-09-03 (risk accepted knowingly): CASHIER and SALES_STAFF
// get FULL edit, including phone and GSTIN. CUSTOMER_EDIT_ROLES below is the
// ONE list every door gates on -- widen it here or nowhere.

import { useState } from 'react';
import { X, Loader2, AlertTriangle } from 'lucide-react';
import { customerApi } from '../../services/api/customers';
import type { UserRole } from '../../types';

/** Who may open the customer-edit door. Owner ruling 2026-09-03 added
 *  CASHIER + SALES_STAFF to the manager tier. */
export const CUSTOMER_EDIT_ROLES: UserRole[] = [
  'SUPERADMIN',
  'ADMIN',
  'STORE_MANAGER',
  'CASHIER',
  'SALES_STAFF',
];

export interface EditableCustomer {
  id: string;
  name?: string;
  phone?: string;
  mobile?: string;
  email?: string;
  address?: string;
  billing_address?: { address?: string } | null;
  gstin?: string;
  gst_number?: string;
}

/** The fields the modal saved, exactly as sent (phone already normalised).
 *  Callers merge these into their own copy of the customer. */
export interface SavedCustomerFields {
  name: string;
  phone: string;
  email?: string;
  address?: string;
  gstin?: string;
}

export function EditCustomerModal({
  customer,
  onClose,
  onSaved,
}: {
  customer: EditableCustomer;
  onClose: () => void;
  onSaved: (fields: SavedCustomerFields) => void;
}) {
  const [form, setForm] = useState(() => ({
    name: customer.name || '',
    phone: customer.phone || customer.mobile || '',
    email: customer.email || '',
    // Address lives in two shapes on a customer record (flat field vs the
    // structured billing_address); read flat first, same as the Customers
    // page's own resolver.
    address: customer.address ?? customer.billing_address?.address ?? '',
    gstin: customer.gstin || customer.gst_number || '',
  }));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    if (!form.name || !form.phone) {
      setError('Name and phone are required');
      return;
    }
    if (form.email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email.trim())) {
      setError('Please enter a valid email address');
      return;
    }
    setError(null);
    setSaving(true);
    const normalizedPhone = (form.phone || '').replace(/\D/g, '').slice(-10);
    const fields: SavedCustomerFields = {
      name: form.name,
      phone: normalizedPhone,
      email: form.email || undefined,
      address: form.address || undefined,
      // An empty box means "leave the stored GSTIN alone", not "clear it" --
      // clearing a B2B customer's GSTIN is the server's refusal to give, not
      // this form's to bypass by omission.
      gstin: form.gstin.trim().toUpperCase() || undefined,
    };
    try {
      await customerApi.updateCustomer(customer.id, fields as any);
      onSaved(fields);
      onClose();
    } catch (e: any) {
      // The server owns every real rule here (duplicate mobile 409,
      // B2B-requires-GSTIN, formats) -- surface its refusal VERBATIM.
      const detail = e?.response?.data?.detail || e?.message || 'Failed to update customer';
      setError(typeof detail === 'string' ? detail : 'Failed to update customer');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl max-w-md w-full max-h-[90dvh] overflow-y-auto">
        <div className="p-6">
          <div className="flex items-center justify-between mb-6">
            <h2 className="text-xl font-bold text-gray-900">Edit Customer</h2>
            <button
              onClick={onClose}
              className="p-2 hover:bg-gray-100 rounded-lg min-h-[44px] min-w-[44px]"
              aria-label="Close"
              title="Close"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Name *</label>
              <input
                type="text"
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                className="input-field"
                title="Customer full name"
                placeholder="Customer full name"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Phone *</label>
              <input
                type="tel"
                value={form.phone}
                onChange={(e) => setForm((f) => ({ ...f, phone: e.target.value }))}
                className="input-field"
                maxLength={10}
                title="Customer 10-digit mobile number"
                placeholder="10-digit mobile"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Email</label>
              <input
                type="email"
                value={form.email}
                onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))}
                className="input-field"
                title="Customer email address"
                placeholder="name@example.com"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">GSTIN</label>
              <input
                type="text"
                value={form.gstin}
                onChange={(e) => setForm((f) => ({ ...f, gstin: e.target.value }))}
                className="input-field uppercase"
                maxLength={15}
                title="Customer GSTIN (needed on B2B bills)"
                placeholder="15-character GSTIN"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Address</label>
              <textarea
                value={form.address}
                onChange={(e) => setForm((f) => ({ ...f, address: e.target.value }))}
                className="input-field"
                rows={2}
                title="Customer billing address"
                placeholder="Street, city, state, pincode"
              />
            </div>
            {error && (
              <div className="flex items-start gap-2 p-3 bg-red-50 border border-red-200 rounded-lg text-left text-xs text-red-700">
                <AlertTriangle className="w-4 h-4 text-red-400 flex-shrink-0 mt-0.5" />
                <span className="flex-1">{error}</span>
              </div>
            )}
            <button
              onClick={() => void save()}
              disabled={saving || !form.name || !form.phone}
              className="btn-primary w-full flex items-center justify-center gap-2 disabled:opacity-50"
            >
              {saving && <Loader2 className="w-4 h-4 animate-spin" />}
              Save Changes
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default EditCustomerModal;

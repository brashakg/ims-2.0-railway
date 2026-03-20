// ============================================================================
// IMS 2.0 - Add Supplier Modal
// ============================================================================

import { useState } from 'react';
import {
  CheckCircle,
  X as XIcon,
  Truck,
  AlertTriangle,
} from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import type { Supplier } from './purchaseTypes';

interface SupplierFormModalProps {
  onClose: () => void;
  onCreated: (supplier: Supplier) => void;
}

export function SupplierFormModal({ onClose, onCreated }: SupplierFormModalProps) {
  const toast = useToast();

  const [name, setName] = useState('');
  const [code, setCode] = useState('');
  const [contactPerson, setContactPerson] = useState('');
  const [phone, setPhone] = useState('');
  const [email, setEmail] = useState('');
  const [address, setAddress] = useState('');
  const [city, setCity] = useState('');
  const [state, setState] = useState('');
  const [gst, setGST] = useState('');
  const [paymentTerms, setPaymentTerms] = useState(30);
  const [creditLimit, setCreditLimit] = useState(0);
  const [gstError, setGSTError] = useState('');

  const validateGST = (gstValue: string): boolean => {
    if (!gstValue) return true; // optional
    const gstRegex = /^\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9]$/;
    return gstRegex.test(gstValue.toUpperCase());
  };

  const handleAdd = () => {
    if (!name.trim()) {
      toast.error('Company name is required');
      return;
    }
    if (!code.trim()) {
      toast.error('Supplier code is required');
      return;
    }
    if (!contactPerson.trim()) {
      toast.error('Contact person is required');
      return;
    }
    if (gst && !validateGST(gst)) {
      setGSTError('Invalid GST format. Expected: 2-digit state code + PAN + alphanumeric (e.g., 07AAAAA1234A1Z5)');
      return;
    }

    const newSupplier: Supplier = {
      id: `sup-${Date.now()}`,
      name: name.trim(),
      code: code.trim().toUpperCase(),
      contactPerson: contactPerson.trim(),
      phone: phone.trim(),
      email: email.trim(),
      address: address.trim(),
      city: city.trim(),
      state: state.trim(),
      gstNumber: gst.trim().toUpperCase(),
      paymentTerms,
      creditLimit,
      currentOutstanding: 0,
      rating: 0,
      totalPurchases: 0,
      lastPurchaseDate: '',
      performance: {
        onTimeDelivery: 0,
        qualityScore: 0,
        priceCompetitiveness: 0,
      },
    };

    onCreated(newSupplier);
    toast.success(`Supplier "${newSupplier.name}" added successfully`);
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-start justify-center z-50 p-4 overflow-y-auto">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-2xl my-8">
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b border-gray-200">
          <h2 className="text-xl font-bold text-gray-900 flex items-center gap-2">
            <Truck className="w-5 h-5 text-blue-600" />
            Add Supplier
          </h2>
          <button
            onClick={onClose}
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
              <label className="block text-sm font-medium text-gray-700 mb-1">Company Name *</label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g., Titan Eyewear Pvt Ltd"
                className="input-field"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Supplier Code *</label>
              <input
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
              <label className="block text-sm font-medium text-gray-700 mb-1">Contact Person *</label>
              <input
                type="text"
                value={contactPerson}
                onChange={(e) => setContactPerson(e.target.value)}
                placeholder="Full name"
                className="input-field"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Phone</label>
              <input
                type="text"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                placeholder="+91 98765 43210"
                className="input-field"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Email</label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="email@company.com"
                className="input-field"
              />
            </div>
          </div>

          {/* Address, City, State */}
          <div className="grid grid-cols-1 tablet:grid-cols-3 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Address</label>
              <input
                type="text"
                value={address}
                onChange={(e) => setAddress(e.target.value)}
                placeholder="Street address"
                className="input-field"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">City</label>
              <input
                type="text"
                value={city}
                onChange={(e) => setCity(e.target.value)}
                placeholder="City"
                className="input-field"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">State</label>
              <input
                type="text"
                value={state}
                onChange={(e) => setState(e.target.value)}
                placeholder="State"
                className="input-field"
              />
            </div>
          </div>

          {/* GST Number */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">GST Number</label>
            <input
              type="text"
              value={gst}
              onChange={(e) => {
                setGST(e.target.value.toUpperCase());
                setGSTError('');
              }}
              placeholder="e.g., 07AAAAA1234A1Z5"
              maxLength={15}
              className={`input-field ${gstError ? 'border-red-500' : ''}`}
            />
            {gstError && (
              <p className="mt-1 text-xs text-red-600 flex items-center gap-1">
                <AlertTriangle className="w-3 h-3" />
                {gstError}
              </p>
            )}
          </div>

          {/* Payment Terms & Credit Limit */}
          <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Payment Terms (days)</label>
              <input
                type="number"
                min="0"
                value={paymentTerms}
                onChange={(e) => setPaymentTerms(parseInt(e.target.value) || 0)}
                className="input-field"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Credit Limit ({'\u20B9'})</label>
              <input
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
            onClick={handleAdd}
            className="btn-primary flex items-center gap-2"
          >
            <CheckCircle className="w-4 h-4" />
            Save Supplier
          </button>
        </div>
      </div>
    </div>
  );
}

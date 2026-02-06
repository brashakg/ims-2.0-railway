// ============================================================================
// IMS 2.0 - Serial Number Management Modal
// ============================================================================
// Add, edit, and track serial numbers for high-value items

import { useState } from 'react';
import {
  X,
  Hash,
  Save,
  Loader2,
  AlertCircle,
  CheckCircle,
} from 'lucide-react';
import { useToast } from '../../context/ToastContext';

interface SerialNumberModalProps {
  isOpen: boolean;
  onClose: () => void;
  product: {
    id: string;
    sku: string;
    name: string;
    brand: string;
    category: string;
  };
  onSave: (data: SerialNumberData) => Promise<void>;
  editData?: SerialNumberData;
}

export interface SerialNumberData {
  id?: string;
  productId: string;
  serialNumber: string;
  status: 'IN_STOCK' | 'SOLD' | 'WARRANTY_CLAIM' | 'DAMAGED' | 'LOST_STOLEN';
  locationCode?: string;
  purchaseDate?: string;
  warrantyMonths?: number;
  warrantyExpiryDate?: string;
  supplierBatch?: string;
  notes?: string;
  soldTo?: string;
  soldDate?: string;
}

export function SerialNumberModal({
  isOpen,
  onClose,
  product,
  onSave,
  editData,
}: SerialNumberModalProps) {
  const toast = useToast();

  const [isSaving, setIsSaving] = useState(false);
  const [serialNumber, setSerialNumber] = useState(editData?.serialNumber || '');
  const [status, setStatus] = useState<SerialNumberData['status']>(editData?.status || 'IN_STOCK');
  const [locationCode, setLocationCode] = useState(editData?.locationCode || '');
  const [purchaseDate, setPurchaseDate] = useState(
    editData?.purchaseDate || new Date().toISOString().split('T')[0]
  );
  const [warrantyMonths, setWarrantyMonths] = useState(editData?.warrantyMonths || 12);
  const [supplierBatch, setSupplierBatch] = useState(editData?.supplierBatch || '');
  const [notes, setNotes] = useState(editData?.notes || '');

  // Calculate warranty expiry
  const warrantyExpiryDate = purchaseDate
    ? new Date(
        new Date(purchaseDate).getTime() + warrantyMonths * 30 * 24 * 60 * 60 * 1000
      ).toISOString().split('T')[0]
    : '';

  const isWarrantyActive = warrantyExpiryDate
    ? new Date(warrantyExpiryDate) > new Date()
    : false;

  const handleSubmit = async () => {
    // Validation
    if (!serialNumber.trim()) {
      toast.error('Serial number is required');
      return;
    }

    setIsSaving(true);
    try {
      await onSave({
        id: editData?.id,
        productId: product.id,
        serialNumber: serialNumber.trim(),
        status,
        locationCode: locationCode.trim() || undefined,
        purchaseDate,
        warrantyMonths,
        warrantyExpiryDate,
        supplierBatch: supplierBatch.trim() || undefined,
        notes: notes.trim() || undefined,
      });

      toast.success(
        editData ? 'Serial number updated successfully' : 'Serial number added successfully'
      );
      onClose();
    } catch (error: any) {
      toast.error(error?.message || 'Failed to save serial number');
    } finally {
      setIsSaving(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-2xl max-h-[90vh] overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b border-gray-200">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-blue-100 rounded-lg">
              <Hash className="w-6 h-6 text-blue-600" />
            </div>
            <div>
              <h2 className="text-xl font-bold text-gray-900">
                {editData ? 'Edit Serial Number' : 'Add Serial Number'}
              </h2>
              <p className="text-sm text-gray-500">{product.name}</p>
            </div>
          </div>
          <button
            onClick={onClose}
            disabled={isSaving}
            className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
          >
            <X className="w-5 h-5 text-gray-500" />
          </button>
        </div>

        {/* Content */}
        <div className="p-6 overflow-y-auto" style={{ maxHeight: 'calc(90vh - 200px)' }}>
          {/* Product Info */}
          <div className="bg-gray-50 border border-gray-200 rounded-lg p-4 mb-6">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <p className="text-sm text-gray-600 mb-1">Product</p>
                <p className="font-medium text-gray-900">{product.name}</p>
              </div>
              <div>
                <p className="text-sm text-gray-600 mb-1">SKU</p>
                <p className="font-medium text-gray-900">{product.sku}</p>
              </div>
              <div>
                <p className="text-sm text-gray-600 mb-1">Brand</p>
                <p className="font-medium text-gray-900">{product.brand}</p>
              </div>
              <div>
                <p className="text-sm text-gray-600 mb-1">Category</p>
                <p className="font-medium text-gray-900">{product.category}</p>
              </div>
            </div>
          </div>

          {/* Serial Number Form */}
          <div className="space-y-6">
            {/* Serial Number */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Serial Number *
              </label>
              <input
                type="text"
                value={serialNumber}
                onChange={(e) => setSerialNumber(e.target.value.toUpperCase())}
                placeholder="Enter serial number"
                disabled={isSaving || !!editData}
                className="input-field w-full font-mono"
              />
              <p className="text-xs text-gray-500 mt-1">
                {editData
                  ? 'Serial number cannot be changed once created'
                  : 'Unique identifier for this unit (letters and numbers)'}
              </p>
            </div>

            {/* Status */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">Status *</label>
              <select
                value={status}
                onChange={(e) => setStatus(e.target.value as SerialNumberData['status'])}
                disabled={isSaving}
                className="input-field w-full"
              >
                <option value="IN_STOCK">In Stock</option>
                <option value="SOLD">Sold</option>
                <option value="WARRANTY_CLAIM">Warranty Claim</option>
                <option value="DAMAGED">Damaged</option>
                <option value="LOST_STOLEN">Lost/Stolen</option>
              </select>
            </div>

            {/* Location */}
            {status === 'IN_STOCK' && (
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">
                  Location Code
                </label>
                <input
                  type="text"
                  value={locationCode}
                  onChange={(e) => setLocationCode(e.target.value.toUpperCase())}
                  placeholder="e.g., A1-05, C3-12"
                  disabled={isSaving}
                  className="input-field w-full"
                />
                <p className="text-xs text-gray-500 mt-1">
                  Physical location in store/warehouse
                </p>
              </div>
            )}

            {/* Purchase & Warranty */}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">
                  Purchase Date
                </label>
                <input
                  type="date"
                  value={purchaseDate}
                  onChange={(e) => setPurchaseDate(e.target.value)}
                  disabled={isSaving}
                  className="input-field w-full"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">
                  Warranty Period (Months)
                </label>
                <input
                  type="number"
                  min="0"
                  max="120"
                  value={warrantyMonths}
                  onChange={(e) => setWarrantyMonths(parseInt(e.target.value) || 0)}
                  disabled={isSaving}
                  className="input-field w-full"
                />
              </div>
            </div>

            {/* Warranty Status */}
            {warrantyExpiryDate && (
              <div
                className={`p-4 rounded-lg border ${
                  isWarrantyActive
                    ? 'bg-green-50 border-green-200'
                    : 'bg-gray-50 border-gray-200'
                }`}
              >
                <div className="flex items-center gap-2 mb-2">
                  {isWarrantyActive ? (
                    <CheckCircle className="w-5 h-5 text-green-600" />
                  ) : (
                    <AlertCircle className="w-5 h-5 text-gray-600" />
                  )}
                  <p
                    className={`font-medium ${
                      isWarrantyActive ? 'text-green-900' : 'text-gray-900'
                    }`}
                  >
                    Warranty {isWarrantyActive ? 'Active' : 'Expired'}
                  </p>
                </div>
                <p
                  className={`text-sm ${
                    isWarrantyActive ? 'text-green-700' : 'text-gray-700'
                  }`}
                >
                  Expires on: {new Date(warrantyExpiryDate).toLocaleDateString()}
                </p>
              </div>
            )}

            {/* Supplier Batch */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Supplier Batch Code
              </label>
              <input
                type="text"
                value={supplierBatch}
                onChange={(e) => setSupplierBatch(e.target.value)}
                placeholder="e.g., BATCH-2025-001"
                disabled={isSaving}
                className="input-field w-full"
              />
              <p className="text-xs text-gray-500 mt-1">
                For tracking and recall purposes
              </p>
            </div>

            {/* Notes */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Notes (Optional)
              </label>
              <textarea
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                placeholder="Add any additional notes..."
                rows={4}
                disabled={isSaving}
                className="input-field w-full"
              />
            </div>
          </div>

          {/* Info Banner */}
          <div className="mt-6 bg-blue-50 border border-blue-200 rounded-lg p-4">
            <div className="flex gap-3">
              <AlertCircle className="w-5 h-5 text-blue-600 flex-shrink-0 mt-0.5" />
              <div className="text-sm text-blue-900">
                <p className="font-medium mb-1">Serial Number Guidelines</p>
                <ul className="list-disc list-inside space-y-1 text-blue-800">
                  <li>Use manufacturer's serial number when available</li>
                  <li>Serial numbers must be unique across all products</li>
                  <li>Track high-value items like hearing aids, smart watches</li>
                  <li>Update status when sold or serviced</li>
                </ul>
              </div>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between p-6 border-t border-gray-200 bg-gray-50">
          <button onClick={onClose} disabled={isSaving} className="btn-outline">
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={isSaving}
            className="btn-primary flex items-center gap-2"
          >
            {isSaving ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                Saving...
              </>
            ) : (
              <>
                <Save className="w-4 h-4" />
                {editData ? 'Update' : 'Add'} Serial Number
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}

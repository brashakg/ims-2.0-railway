// ============================================================================
// IMS 2.0 - Manufacturer Barcode Modal
// ============================================================================
// Owner ruling 2026-09-28: IMS keeps stock through its OWN per-unit barcodes
// (minted at receipt, printed on unit labels). The product's barcode holds only
// the manufacturer's UPC / EAN (GTIN) -- for reference and what goes to
// Shopify / Google. So nothing here invents a code: no random Generate, no
// symbology picker. The server (services/gtin.py) decides what is a GTIN and
// its refusal is shown in words.

import { useState, useEffect } from 'react';
import { X, AlertCircle, CheckCircle } from 'lucide-react';
import clsx from 'clsx';

interface BarcodeManagementModalProps {
  isOpen: boolean;
  onClose: () => void;
  productName: string;
  currentBarcode?: string;
  onSave: (barcode: string) => Promise<void>;
}

export function BarcodeManagementModal({
  isOpen,
  onClose,
  productName,
  currentBarcode,
  onSave,
}: BarcodeManagementModalProps) {
  const [barcode, setBarcode] = useState(currentBarcode || '');
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  useEffect(() => {
    if (isOpen) {
      setBarcode(currentBarcode || '');
      setError(null);
      setSuccess(false);
    }
  }, [isOpen, currentBarcode]);

  const handleSave = async () => {
    if (!barcode.trim()) {
      setError("Type the barcode printed on the manufacturer's box");
      return;
    }

    setIsSaving(true);
    setError(null);

    try {
      await onSave(barcode.trim());
      setSuccess(true);
      setTimeout(() => {
        onClose();
      }, 1500);
    } catch (err: any) {
      setError(err?.message || 'Failed to save barcode');
    } finally {
      setIsSaving(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl max-h-[90dvh] overflow-hidden flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b border-gray-200">
          <div>
            <h2 className="text-xl font-bold text-gray-900">Manufacturer barcode</h2>
            <p className="text-sm text-gray-500 mt-1">{productName}</p>
          </div>
          <button
            onClick={onClose}
            className="p-2 text-gray-500 hover:text-gray-600 transition-colors"
            aria-label="Close"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6 space-y-4">
          <div>
            <label htmlFor="mfr-barcode" className="block text-sm font-medium text-gray-700 mb-2">
              Manufacturer barcode (UPC / EAN)
            </label>
            <input
              id="mfr-barcode"
              type="text"
              inputMode="numeric"
              value={barcode}
              onChange={(e) => {
                setBarcode(e.target.value);
                setError(null);
                setSuccess(false);
              }}
              placeholder="e.g. 8056597720373"
              className={clsx(
                'input-field w-full',
                error && 'border-red-500',
                success && 'border-green-500'
              )}
              maxLength={20}
            />
            {error && (
              <p className="text-sm text-red-600 mt-1 flex items-center gap-1">
                <AlertCircle className="w-4 h-4" />
                {error}
              </p>
            )}
            {success && (
              <p className="text-sm text-green-600 mt-1 flex items-center gap-1">
                <CheckCircle className="w-4 h-4" />
                Barcode saved successfully!
              </p>
            )}
          </div>

          <p className="text-sm text-gray-600">
            The 8, 12, 13 or 14-digit code printed on the maker&apos;s box. It is kept for
            reference and sent to Shopify and Google. IMS scans and labels each unit with its
            own IMS barcode, minted when the stock is received.
          </p>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 p-6 border-t border-gray-200 bg-gray-50">
          <button
            onClick={onClose}
            className="btn-outline"
            disabled={isSaving}
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={isSaving || !barcode.trim()}
            className="btn-primary disabled:opacity-50"
          >
            {isSaving ? 'Saving...' : 'Save Barcode'}
          </button>
        </div>
      </div>
    </div>
  );
}

export default BarcodeManagementModal;

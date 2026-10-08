// ============================================================================
// IMS 2.0 - Manufacturer Barcode Modal
// ============================================================================
// Owner ruling 2026-09-28: IMS keeps stock through its OWN per-unit barcodes
// (minted at receipt, printed on unit labels). The manufacturer's UPC / EAN
// (GTIN) is kept on the product for reference and for Shopify / Google. So
// nothing here invents a code: no random Generate, no symbology picker.
//
// It is saved as the product's `gtin` attribute -- the SAME field the Add
// Product form's "GTIN (mfr)" box writes and the Shopify push reads (the old
// product `barcode` field never reached Shopify). The server
// (services/gtin.py) decides what is a GTIN; its refusal is shown in words.
//
// A legacy product barcode (main's old modal wrote it; its Generate made random
// EAN-13s that pass the format check) is NOT the maker's: it is shown apart as
// an old code, never put in the box, so Save always needs a typed code.

import { useState, useEffect } from 'react';
import { X, AlertCircle, CheckCircle } from 'lucide-react';
import clsx from 'clsx';
import { productApi } from '../../services/api/products';

interface BarcodeManagementModalProps {
  isOpen: boolean;
  onClose: () => void;
  productId: string;
  productName: string;
  /** The product's saved manufacturer GTIN (never a unit's IMS barcode). */
  currentGtin?: string;
  /** A legacy product barcode: unverified, never offered as the GTIN. */
  oldCode?: string;
  onSaved?: () => void;
}

export function BarcodeManagementModal({
  isOpen,
  onClose,
  productId,
  productName,
  currentGtin,
  oldCode,
  onSaved,
}: BarcodeManagementModalProps) {
  const [barcode, setBarcode] = useState(currentGtin || '');
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  useEffect(() => {
    if (isOpen) {
      setBarcode(currentGtin || '');
      setError(null);
      setSuccess(null);
    }
  }, [isOpen, currentGtin]);

  // An emptied box on a product that HAS a GTIN removes it from IMS, so IMS
  // stops sending it (a wrong code, say the neighbouring frame's box). IMS
  // never blanks a barcode already on Shopify (decided 2026-10-01): that one
  // is cleared in Shopify admin.
  const code = barcode.trim();
  const removing = !code && !!currentGtin;

  // Saving the gtin attribute (even '') also drops the legacy product barcode.
  const save = async (gtin: string, done: string) => {
    setIsSaving(true);
    setError(null);

    try {
      await productApi.updateProduct(productId, { attributes: { gtin } });
      setSuccess(done);
      onSaved?.();
      setTimeout(() => {
        onClose();
      }, 1500);
    } catch (err) {
      // The api client rejects with an ApiError whose message IS the server's
      // reason (services/api/client.ts buildApiError) -- there is no .response.
      setError((err as Error)?.message || 'Failed to save barcode');
    } finally {
      setIsSaving(false);
    }
  };

  const handleSave = () => {
    if (!code && !removing) {
      setError("Type the barcode printed on the manufacturer's box");
      return;
    }
    void save(code, removing ? 'Barcode removed.' : 'Barcode saved successfully!');
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
          {oldCode && !currentGtin && (
            <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
              <p>
                Old IMS code <span className="font-mono">{oldCode}</span> - not a maker barcode;
                type the code printed on the maker&apos;s box.
              </p>
              <button
                type="button"
                onClick={() => void save('', 'Old code removed.')}
                disabled={isSaving}
                className="mt-2 font-medium underline disabled:opacity-50"
              >
                Remove old code
              </button>
            </div>
          )}
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
                setSuccess(null);
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
                {success}
              </p>
            )}
          </div>

          <p className="text-sm text-gray-600">
            The 8, 12, 13 or 14-digit code printed on the maker&apos;s box. It is kept for
            reference and goes to Shopify and Google with the next website push; empty the box to
            remove a wrong one from IMS (a product already on the website keeps its Shopify
            barcode until it is cleared in Shopify admin). IMS scans and
            labels each unit with its own IMS barcode, minted when the stock is received; print
            those labels from the product's units on the stock ledger.
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
            disabled={isSaving || (!code && !removing)}
            className="btn-primary disabled:opacity-50"
          >
            {isSaving ? 'Saving...' : removing ? 'Remove Barcode' : 'Save Barcode'}
          </button>
        </div>
      </div>
    </div>
  );
}

export default BarcodeManagementModal;

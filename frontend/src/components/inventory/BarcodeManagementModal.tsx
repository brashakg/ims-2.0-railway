// ============================================================================
// IMS 2.0 - Barcode Management Modal
// ============================================================================
// Generate, edit, and print barcodes for products

import { useState, useEffect } from 'react';
import { X, RefreshCw, AlertCircle, CheckCircle } from 'lucide-react';
import { BarcodeGenerator } from './BarcodeGenerator';
import clsx from 'clsx';

interface BarcodeManagementModalProps {
  isOpen: boolean;
  onClose: () => void;
  productId?: string;
  productName: string;
  currentBarcode?: string;
  price?: number;
  onSave: (barcode: string) => Promise<void>;
}

type BarcodeFormat = 'CODE128' | 'EAN13' | 'UPC' | 'CODE39';

export function BarcodeManagementModal({
  isOpen,
  onClose,
  productName,
  currentBarcode,
  price,
  onSave,
}: BarcodeManagementModalProps) {
  const [barcode, setBarcode] = useState(currentBarcode || '');
  const [format, setFormat] = useState<BarcodeFormat>('CODE128');
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

  const generateRandomBarcode = (format: BarcodeFormat): string => {
    switch (format) {
      case 'EAN13':
        // Generate 13-digit EAN
        const ean = Array.from({ length: 12 }, () => Math.floor(Math.random() * 10)).join('');
        // Calculate check digit
        let sum = 0;
        for (let i = 0; i < 12; i++) {
          sum += parseInt(ean[i]) * (i % 2 === 0 ? 1 : 3);
        }
        const checkDigit = (10 - (sum % 10)) % 10;
        return ean + checkDigit;

      case 'UPC':
        // Generate 12-digit UPC
        const upc = Array.from({ length: 11 }, () => Math.floor(Math.random() * 10)).join('');
        // Calculate check digit
        let upcSum = 0;
        for (let i = 0; i < 11; i++) {
          upcSum += parseInt(upc[i]) * (i % 2 === 0 ? 3 : 1);
        }
        const upcCheck = (10 - (upcSum % 10)) % 10;
        return upc + upcCheck;

      case 'CODE39':
        // Generate 8-character alphanumeric CODE39
        const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
        return Array.from({ length: 8 }, () => chars[Math.floor(Math.random() * chars.length)]).join('');

      case 'CODE128':
      default:
        // Generate 12-digit numeric CODE128
        return Array.from({ length: 12 }, () => Math.floor(Math.random() * 10)).join('');
    }
  };

  const handleGenerate = () => {
    const newBarcode = generateRandomBarcode(format);
    setBarcode(newBarcode);
    setError(null);
    setSuccess(false);
  };

  const handleFormatChange = (newFormat: BarcodeFormat) => {
    setFormat(newFormat);
    if (barcode) {
      // Try to convert existing barcode if possible
      // For now, just clear it to avoid format errors
      setBarcode('');
    }
  };

  const validateBarcode = (value: string): boolean => {
    switch (format) {
      case 'EAN13':
        return /^\d{13}$/.test(value);
      case 'UPC':
        return /^\d{12}$/.test(value);
      case 'CODE39':
        return /^[A-Z0-9]{4,}$/.test(value);
      case 'CODE128':
        return /^[\x00-\x7F]{4,}$/.test(value); // Any ASCII, min 4 chars
      default:
        return value.length >= 4;
    }
  };

  const handleSave = async () => {
    if (!barcode.trim()) {
      setError('Please enter or generate a barcode');
      return;
    }

    if (!validateBarcode(barcode)) {
      setError(`Invalid barcode format for ${format}`);
      return;
    }

    setIsSaving(true);
    setError(null);

    try {
      await onSave(barcode);
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
      <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl max-h-[90vh] overflow-hidden flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b border-gray-200">
          <div>
            <h2 className="text-xl font-bold text-gray-900">Barcode Management</h2>
            <p className="text-sm text-gray-500 mt-1">{productName}</p>
          </div>
          <button
            onClick={onClose}
            className="p-2 text-gray-400 hover:text-gray-600 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {/* Format Selection */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Barcode Format
            </label>
            <div className="grid grid-cols-4 gap-2">
              {(['CODE128', 'EAN13', 'UPC', 'CODE39'] as BarcodeFormat[]).map((f) => (
                <button
                  key={f}
                  onClick={() => handleFormatChange(f)}
                  className={clsx(
                    'px-4 py-2 text-sm font-medium rounded-lg border transition-colors',
                    format === f
                      ? 'border-bv-red-600 bg-bv-red-50 text-bv-red-700'
                      : 'border-gray-200 hover:border-gray-300 text-gray-600'
                  )}
                >
                  {f}
                </button>
              ))}
            </div>
            <p className="text-xs text-gray-500 mt-2">
              {format === 'CODE128' && 'Most common format, supports all ASCII characters'}
              {format === 'EAN13' && 'European Article Number (13 digits), used globally'}
              {format === 'UPC' && 'Universal Product Code (12 digits), used in retail'}
              {format === 'CODE39' && 'Alphanumeric format, widely used in inventory'}
            </p>
          </div>

          {/* Barcode Input */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Barcode Value
            </label>
            <div className="flex gap-2">
              <input
                type="text"
                value={barcode}
                onChange={(e) => {
                  setBarcode(e.target.value.toUpperCase());
                  setError(null);
                  setSuccess(false);
                }}
                placeholder={`Enter ${format} barcode`}
                className={clsx(
                  'input-field flex-1',
                  error && 'border-red-500',
                  success && 'border-green-500'
                )}
                maxLength={format === 'EAN13' ? 13 : format === 'UPC' ? 12 : 20}
              />
              <button
                onClick={handleGenerate}
                className="btn-outline flex items-center gap-2"
              >
                <RefreshCw className="w-4 h-4" />
                Generate
              </button>
            </div>
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

          {/* Barcode Preview */}
          {barcode && validateBarcode(barcode) && (
            <div className="border border-gray-200 rounded-lg p-4">
              <h3 className="text-sm font-medium text-gray-700 mb-3">Preview & Print</h3>
              <BarcodeGenerator
                value={barcode}
                format={format}
                productName={productName}
                price={price}
              />
            </div>
          )}

          {/* Info Box */}
          <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
            <h4 className="text-sm font-medium text-blue-900 mb-2">Barcode Best Practices</h4>
            <ul className="text-xs text-blue-700 space-y-1 list-disc list-inside">
              <li>Use unique barcodes for each product variant (frame color, size, etc.)</li>
              <li>CODE128 is recommended for optical products (frames, lenses)</li>
              <li>EAN13/UPC are required for products to be sold online or in major retailers</li>
              <li>Print labels at high resolution (300 DPI) for reliable scanning</li>
              <li>Test scanned barcodes with your POS scanner before printing in bulk</li>
            </ul>
          </div>
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
            disabled={isSaving || !barcode || !validateBarcode(barcode)}
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

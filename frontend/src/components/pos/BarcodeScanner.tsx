// ============================================================================
// IMS 2.0 - Barcode Scanner Input Component
// ============================================================================
// Input field optimized for barcode scanner devices

import { useState, useEffect, useRef } from 'react';
import { Scan, Search, X } from 'lucide-react';
import clsx from 'clsx';

interface BarcodeScannerProps {
  onScan: (barcode: string) => void;
  onManualSearch?: (query: string) => void;
  placeholder?: string;
  className?: string;
  autoFocus?: boolean;
}

export function BarcodeScanner({
  onScan,
  onManualSearch,
  placeholder = 'Scan barcode or search product...',
  className,
  autoFocus = true,
}: BarcodeScannerProps) {
  const [value, setValue] = useState('');
  const [isScanning, setIsScanning] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const scanTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const lastKeyTimeRef = useRef<number>(0);

  useEffect(() => {
    if (autoFocus && inputRef.current) {
      inputRef.current.focus();
    }
  }, [autoFocus]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    const now = Date.now();
    const timeDiff = now - lastKeyTimeRef.current;
    lastKeyTimeRef.current = now;

    // Detect rapid key presses (barcode scanner input)
    // Scanners typically input characters very quickly (< 50ms between keys)
    if (timeDiff < 50 && value.length > 0) {
      setIsScanning(true);
    }

    // Clear scanning timeout
    if (scanTimeoutRef.current) {
      clearTimeout(scanTimeoutRef.current);
    }

    // Set new timeout to reset scanning state
    scanTimeoutRef.current = setTimeout(() => {
      setIsScanning(false);
    }, 100);

    // Handle Enter key
    if (e.key === 'Enter') {
      e.preventDefault();
      handleSubmit();
    }

    // Handle Escape key to clear
    if (e.key === 'Escape') {
      e.preventDefault();
      handleClear();
    }
  };

  const handleSubmit = () => {
    if (!value.trim()) return;

    // If it looks like a barcode (numeric or alphanumeric, 8+ chars)
    const isBarcodeFormat = /^[A-Z0-9]{8,}$/i.test(value.trim());

    if (isBarcodeFormat || isScanning) {
      // Treat as barcode scan
      onScan(value.trim());
      setValue('');
      setIsScanning(false);
    } else if (onManualSearch) {
      // Treat as manual search
      onManualSearch(value.trim());
    } else {
      // Default to scan
      onScan(value.trim());
      setValue('');
    }
  };

  const handleClear = () => {
    setValue('');
    setIsScanning(false);
    if (inputRef.current) {
      inputRef.current.focus();
    }
  };

  return (
    <div className={clsx('relative', className)}>
      {/* Scanning indicator */}
      {isScanning && (
        <div className="absolute -top-8 left-0 right-0 text-center">
          <span className="inline-flex items-center gap-2 bg-blue-100 text-blue-700 text-xs px-3 py-1 rounded-full animate-pulse">
            <Scan className="w-3 h-3" />
            Scanning...
          </span>
        </div>
      )}

      <div className="relative flex items-center">
        {/* Scan icon */}
        <div className="absolute left-3 flex items-center pointer-events-none">
          <Scan
            className={clsx(
              'w-5 h-5 transition-colors',
              isScanning ? 'text-blue-600 animate-pulse' : 'text-gray-400'
            )}
          />
        </div>

        {/* Input field */}
        <input
          ref={inputRef}
          type="text"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          className={clsx(
            'input-field pl-10 pr-20',
            isScanning && 'border-blue-500 ring-2 ring-blue-200'
          )}
          autoComplete="off"
          spellCheck={false}
        />

        {/* Action buttons */}
        <div className="absolute right-2 flex items-center gap-1">
          {value && (
            <button
              onClick={handleClear}
              className="p-1.5 text-gray-400 hover:text-gray-600 rounded transition-colors"
              title="Clear"
            >
              <X className="w-4 h-4" />
            </button>
          )}

          {onManualSearch && (
            <button
              onClick={handleSubmit}
              className="p-1.5 text-gray-400 hover:text-bv-red-600 rounded transition-colors"
              title="Search"
            >
              <Search className="w-4 h-4" />
            </button>
          )}
        </div>
      </div>

      {/* Help text */}
      <div className="mt-1 text-xs text-gray-500 flex items-center gap-2">
        <Scan className="w-3 h-3" />
        <span>Use barcode scanner or type manually and press Enter</span>
      </div>
    </div>
  );
}

export default BarcodeScanner;

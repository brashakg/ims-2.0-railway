// ============================================================================
// IMS 2.0 - Eye Test Token Print Component
// ============================================================================
// Receipt-size token print for eye test queue management

import { useRef } from 'react';
import { Printer, X } from 'lucide-react';

interface TokenPrintData {
  tokenNumber: string;
  patientName: string;
  dateTime: string;
  optometristAssigned?: string;
  queuePosition: number;
}

interface StoreInfo {
  storeName: string;
  address: string;
  city: string;
  state: string;
  pincode: string;
  phone?: string;
}

interface EyeTestTokenPrintProps {
  token: TokenPrintData;
  store: StoreInfo;
  onClose: () => void;
}

function formatDateTime(dateStr: string): string {
  return new Date(dateStr).toLocaleDateString('en-IN', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function EyeTestTokenPrint({ token, store, onClose }: EyeTestTokenPrintProps) {
  const printRef = useRef<HTMLDivElement>(null);

  const handlePrint = () => {
    window.print();
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-md max-h-[95vh] overflow-y-auto">
        {/* Action Bar - hidden during print */}
        <div className="flex items-center justify-between p-4 border-b border-gray-200 no-print">
          <h2 className="text-lg font-bold text-gray-900">Print Token</h2>
          <div className="flex items-center gap-2">
            <button
              onClick={handlePrint}
              className="flex items-center gap-2 bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-lg transition"
            >
              <Printer className="w-4 h-4" />
              Print
            </button>
            <button onClick={onClose} className="p-2 hover:bg-gray-100 rounded-lg">
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Printable Document - Receipt size */}
        <div
          ref={printRef}
          className="token-print-area bg-white p-4"
          style={{ maxWidth: '80mm', margin: '0 auto' }}
        >
          {/* Store Header */}
          <div className="text-center mb-4 pb-3 border-b-2 border-gray-800">
            <h1 className="text-base font-bold text-gray-900 uppercase tracking-wide">
              {store.storeName}
            </h1>
            <p className="text-gray-700 text-xs mt-1">{store.address}</p>
            <p className="text-gray-700 text-xs">{store.city}, {store.state} {store.pincode}</p>
            {store.phone && <p className="text-gray-700 text-xs">Ph: {store.phone}</p>}
          </div>

          {/* Token Section */}
          <div className="text-center mb-4">
            <p className="text-gray-600 text-xs font-semibold mb-1">YOUR TOKEN</p>
            <div className="bg-white text-gray-900 rounded-lg py-4 mb-3">
              <p className="text-4xl font-bold text-gray-900">{token.tokenNumber}</p>
              <p className="text-xs text-gray-700 mt-1">Token Number</p>
            </div>
          </div>

          {/* Patient Details */}
          <div className="space-y-2 text-sm mb-4">
            <div className="border-b border-gray-200 pb-2">
              <p className="text-gray-600 text-xs font-semibold">Patient Name</p>
              <p className="text-gray-900 font-medium">{token.patientName}</p>
            </div>
            <div className="border-b border-gray-200 pb-2">
              <p className="text-gray-600 text-xs font-semibold">Queue Position</p>
              <p className="text-gray-900 font-medium">#{token.queuePosition}</p>
            </div>
            {token.optometristAssigned && (
              <div className="border-b border-gray-200 pb-2">
                <p className="text-gray-600 text-xs font-semibold">Optometrist</p>
                <p className="text-gray-900 font-medium">{token.optometristAssigned}</p>
              </div>
            )}
            <div>
              <p className="text-gray-600 text-xs font-semibold">Date & Time</p>
              <p className="text-gray-900 font-mono text-xs">{formatDateTime(token.dateTime)}</p>
            </div>
          </div>

          {/* Footer Instructions */}
          <div className="bg-gray-50 border border-gray-300 rounded p-3 text-center">
            <p className="text-gray-700 text-xs leading-relaxed">
              Please retain this token. Your test will begin shortly.
            </p>
          </div>

          {/* Footer Line */}
          <div className="mt-4 pt-2 border-t border-gray-200 text-center">
            <p className="text-[10px] text-gray-500">
              {store.storeName} • {new Date().toLocaleDateString('en-IN')}
            </p>
          </div>
        </div>
      </div>

      {/* Print-specific CSS for receipt size */}
      <style>{`
        @media print {
          body * {
            visibility: hidden;
          }
          .token-print-area,
          .token-print-area * {
            visibility: visible;
          }
          .token-print-area {
            position: absolute;
            left: 0;
            top: 0;
            width: 80mm;
            padding: 0;
            margin: 0;
            max-width: none;
          }

          .no-print {
            display: none !important;
          }

          @page {
            size: 80mm 120mm;
            margin: 0;
          }

          .token-print-area {
            font-size: 10pt;
            color: #000;
            background: #fff;
          }
          .token-print-area h1 {
            font-size: 14pt;
          }
          .token-print-area p {
            margin: 0;
          }
        }
      `}</style>
    </div>
  );
}

export default EyeTestTokenPrint;

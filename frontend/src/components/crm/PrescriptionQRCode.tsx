// ============================================================================
// Prescription QR Code Generator
// ============================================================================
// Generates a real, scannable QR code that links to the customer's Rx detail
// page. Uses the qrcode.react dependency (QRCodeSVG) rather than a hand-rolled
// placeholder SVG, so the code actually decodes to the prescription URL.

import { useRef } from 'react';
import { QRCodeSVG } from 'qrcode.react';
import type { Prescription } from '../../types';
import { QrCode, Download, Copy } from 'lucide-react';

interface PrescriptionQRCodeProps {
  prescription: Prescription;
  customerName?: string;
}

export function PrescriptionQRCode({ prescription, customerName }: PrescriptionQRCodeProps) {
  const qrContainerRef = useRef<HTMLDivElement>(null);

  // The QR encodes the public prescription URL so a phone camera lands the
  // customer straight on their Rx detail page.
  const prescriptionUrl = `${window.location.origin}/rx/${prescription.id}`;

  const copyToClipboard = () => {
    navigator.clipboard.writeText(prescriptionUrl);
  };

  const downloadQR = () => {
    const svgEl = qrContainerRef.current?.querySelector('svg');
    if (!svgEl) return;
    const blob = new Blob([svgEl.outerHTML], { type: 'image/svg+xml' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `rx-${prescription.id}.svg`;
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="card border-2 border-purple-200 bg-purple-50">
      <h3 className="text-lg font-bold text-gray-900 mb-4 flex items-center gap-2">
        <QrCode className="w-5 h-5 text-purple-600" />
        Prescription QR Code
      </h3>

      <div className="bg-white rounded-lg p-6 flex flex-col items-center gap-4 border border-purple-100">
        <div ref={qrContainerRef} className="flex items-center justify-center bg-white rounded-lg p-2">
          <QRCodeSVG
            value={prescriptionUrl}
            size={200}
            level="M"
            marginSize={2}
            title={`Prescription ${prescription.id}`}
          />
        </div>

        <div className="text-center text-sm">
          <p className="text-gray-600 mb-1">Rx ID: {prescription.id}</p>
          {customerName && <p className="text-gray-500 text-xs">{customerName}</p>}
        </div>

        <div className="flex gap-2 w-full">
          <button
            onClick={copyToClipboard}
            className="flex-1 btn-outline text-sm flex items-center justify-center gap-2"
          >
            <Copy className="w-4 h-4" />
            Copy Link
          </button>
          <button
            onClick={downloadQR}
            className="flex-1 btn-primary text-sm flex items-center justify-center gap-2"
          >
            <Download className="w-4 h-4" />
            Download
          </button>
        </div>

        <div className="bg-gray-50 rounded-lg p-3 w-full text-xs text-gray-600 break-all border border-gray-200">
          {prescriptionUrl}
        </div>

        <p className="text-xs text-gray-500 text-center">
          Customer can scan this QR code or visit the link to view their prescription
        </p>
      </div>
    </div>
  );
}

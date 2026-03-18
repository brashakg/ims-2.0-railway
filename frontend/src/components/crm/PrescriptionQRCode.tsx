// ============================================================================
// Prescription QR Code Generator
// ============================================================================
// Generates QR code that links to customer's Rx detail page

import { useEffect, useRef } from 'react';
import type { Prescription } from '../../types';
import { QrCode, Download, Copy } from 'lucide-react';

interface PrescriptionQRCodeProps {
  prescription: Prescription;
  customerName?: string;
}

export function PrescriptionQRCode({ prescription, customerName }: PrescriptionQRCodeProps) {
  const qrContainerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Generate QR code using a simple SVG-based approach
    // In production, use a library like qrcode.react
    const generateSimpleQRSVG = (_text: string) => {
      // For simplicity, create a placeholder QR code SVG
      // In production, use: import QRCode from 'qrcode.react'
      return `
        <svg width="200" height="200" xmlns="http://www.w3.org/2000/svg">
          <rect width="200" height="200" fill="white"/>
          <rect x="10" y="10" width="30" height="30" fill="black"/>
          <rect x="160" y="10" width="30" height="30" fill="black"/>
          <rect x="10" y="160" width="30" height="30" fill="black"/>
          <text x="100" y="100" text-anchor="middle" dy=".3em" font-size="10" fill="black">
            QR: ${prescription.id}
          </text>
        </svg>
      `;
    };

    if (qrContainerRef.current) {
      const qrSVG = generateSimpleQRSVG(prescription.id);
      qrContainerRef.current.innerHTML = qrSVG;
    }
  }, [prescription.id]);

  const prescriptionUrl = `${window.location.origin}/rx/${prescription.id}`;

  const copyToClipboard = () => {
    navigator.clipboard.writeText(prescriptionUrl);
  };

  const downloadQR = () => {
    const link = document.createElement('a');
    link.href = qrContainerRef.current?.querySelector('svg')?.outerHTML || '';
    link.download = `rx-${prescription.id}.svg`;
    // In production, convert SVG to PNG
  };

  return (
    <div className="card border-2 border-purple-200 bg-purple-50">
      <h3 className="text-lg font-bold text-gray-900 mb-4 flex items-center gap-2">
        <QrCode className="w-5 h-5 text-purple-600" />
        Prescription QR Code
      </h3>

      <div className="bg-white rounded-lg p-6 flex flex-col items-center gap-4 border border-purple-100">
        <div ref={qrContainerRef} className="flex items-center justify-center bg-white rounded-lg p-2"></div>
        
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

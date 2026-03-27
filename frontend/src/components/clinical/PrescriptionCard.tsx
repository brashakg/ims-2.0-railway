// ============================================================================
// IMS 2.0 - Prescription Card (A5 Print)
// ============================================================================

import { useRef } from 'react';
import { Printer } from 'lucide-react';

interface PrescriptionData {
  id: string;
  patientName: string;
  patientAge?: number;
  date: string;
  optometristName: string;
  rightEye: {
    sphere: number;
    cylinder: number;
    axis: number;
    add: number;
  };
  leftEye: {
    sphere: number;
    cylinder: number;
    axis: number;
    add: number;
  };
  pd: number;
  visualAcuity: string;
  notes: string;
  storeName: string;
  storePhone: string;
  validUntil: string;
}

interface PrescriptionCardProps {
  prescription: PrescriptionData;
}

const formatDate = (dateString: string) => {
  const date = new Date(dateString);
  return new Intl.DateTimeFormat('en-IN', {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
  }).format(date);
};

const isExpired = (dateString: string) => {
  return new Date(dateString) < new Date();
};

export function PrescriptionCard({ prescription }: PrescriptionCardProps) {
  const printRef = useRef<HTMLDivElement>(null);

  const handlePrint = () => {
    const printWindow = window.open('', '_blank');
    if (printWindow && printRef.current) {
      printWindow.document.write(printRef.current.innerHTML);
      printWindow.document.close();
      printWindow.print();
    }
  };

  const expired = isExpired(prescription.validUntil);

  return (
    <div className="space-y-4">
      {/* Print Button */}
      <button
        onClick={handlePrint}
        className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition"
      >
        <Printer className="w-4 h-4" />
        Print A5 Card
      </button>

      {/* Printable Content (Hidden) */}
      <div
        ref={printRef}
        className="hidden"
        style={{
          width: '148mm',
          height: '210mm',
          padding: '12mm',
          fontFamily: 'Arial, sans-serif',
          fontSize: '12px',
          backgroundColor: '#fff',
          color: '#000',
        }}
      >
        {/* A5 Landscape: 210mm x 148mm */}
        <div style={{ textAlign: 'center', marginBottom: '8mm' }}>
          <div style={{ fontSize: '14px', fontWeight: 'bold' }}>{prescription.storeName}</div>
          <div style={{ fontSize: '11px' }}>{prescription.storePhone}</div>
        </div>

        <div style={{ borderBottom: '1px solid #000', paddingBottom: '4mm', marginBottom: '4mm' }}>
          <div style={{ textAlign: 'center', fontSize: '13px', fontWeight: 'bold' }}>PRESCRIPTION</div>
        </div>

        {/* Patient Info */}
        <div style={{ marginBottom: '6mm', display: 'flex', gap: '20mm' }}>
          <div>
            <div style={{ fontSize: '10px', color: '#666' }}>Patient Name:</div>
            <div style={{ fontWeight: 'bold' }}>{prescription.patientName}</div>
          </div>
          <div>
            <div style={{ fontSize: '10px', color: '#666' }}>Age:</div>
            <div style={{ fontWeight: 'bold' }}>{prescription.patientAge || '-'}</div>
          </div>
          <div>
            <div style={{ fontSize: '10px', color: '#666' }}>Date:</div>
            <div style={{ fontWeight: 'bold' }}>{formatDate(prescription.date)}</div>
          </div>
        </div>

        {/* Optometrist */}
        <div style={{ marginBottom: '6mm' }}>
          <div style={{ fontSize: '10px', color: '#666' }}>Optometrist:</div>
          <div>{prescription.optometristName}</div>
        </div>

        {/* Prescription Table */}
        <table style={{ width: '100%', marginBottom: '6mm', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid #000' }}>
              <th style={{ padding: '4px', textAlign: 'left', fontSize: '10px', fontWeight: 'bold' }}>Eye</th>
              <th style={{ padding: '4px', textAlign: 'center', fontSize: '10px', fontWeight: 'bold' }}>SPH</th>
              <th style={{ padding: '4px', textAlign: 'center', fontSize: '10px', fontWeight: 'bold' }}>CYL</th>
              <th style={{ padding: '4px', textAlign: 'center', fontSize: '10px', fontWeight: 'bold' }}>AXIS</th>
              <th style={{ padding: '4px', textAlign: 'center', fontSize: '10px', fontWeight: 'bold' }}>ADD</th>
            </tr>
          </thead>
          <tbody>
            <tr style={{ borderBottom: '1px solid #ccc' }}>
              <td style={{ padding: '4px', fontWeight: 'bold' }}>Right (OD)</td>
              <td style={{ padding: '4px', textAlign: 'center' }}>{prescription.rightEye.sphere.toFixed(2)}</td>
              <td style={{ padding: '4px', textAlign: 'center' }}>{prescription.rightEye.cylinder.toFixed(2)}</td>
              <td style={{ padding: '4px', textAlign: 'center' }}>{prescription.rightEye.axis}</td>
              <td style={{ padding: '4px', textAlign: 'center' }}>{prescription.rightEye.add.toFixed(2)}</td>
            </tr>
            <tr>
              <td style={{ padding: '4px', fontWeight: 'bold' }}>Left (OS)</td>
              <td style={{ padding: '4px', textAlign: 'center' }}>{prescription.leftEye.sphere.toFixed(2)}</td>
              <td style={{ padding: '4px', textAlign: 'center' }}>{prescription.leftEye.cylinder.toFixed(2)}</td>
              <td style={{ padding: '4px', textAlign: 'center' }}>{prescription.leftEye.axis}</td>
              <td style={{ padding: '4px', textAlign: 'center' }}>{prescription.leftEye.add.toFixed(2)}</td>
            </tr>
          </tbody>
        </table>

        {/* Additional Info */}
        <div style={{ marginBottom: '4mm', display: 'flex', gap: '15mm' }}>
          <div>
            <div style={{ fontSize: '10px', color: '#666' }}>PD:</div>
            <div>{prescription.pd}mm</div>
          </div>
          <div>
            <div style={{ fontSize: '10px', color: '#666' }}>Visual Acuity:</div>
            <div>{prescription.visualAcuity}</div>
          </div>
          <div>
            <div style={{ fontSize: '10px', color: '#666' }}>Valid Until:</div>
            <div>{formatDate(prescription.validUntil)}</div>
          </div>
        </div>

        {/* Notes */}
        {prescription.notes && (
          <div style={{ marginBottom: '4mm' }}>
            <div style={{ fontSize: '10px', color: '#666' }}>Notes:</div>
            <div style={{ fontSize: '11px' }}>{prescription.notes}</div>
          </div>
        )}

        {/* Footer */}
        <div style={{ marginTop: '8mm', textAlign: 'center', fontSize: '9px', color: '#999' }}>
          Prescription ID: {prescription.id}
        </div>
      </div>

      {/* Preview in UI */}
      <div className={`p-6 rounded-lg border-2 ${expired ? 'bg-red-50 border-red-300' : 'bg-white border-gray-200'}`}>
        {expired && (
          <div className="mb-4 p-3 bg-red-100 border border-red-300 rounded text-red-700 text-sm">
            ⚠️ This prescription has expired on {formatDate(prescription.validUntil)}
          </div>
        )}

        <div className="grid grid-cols-2 gap-6">
          <div>
            <h3 className="text-gray-900 font-semibold mb-2">{prescription.patientName}</h3>
            <p className="text-gray-500 text-sm">Age: {prescription.patientAge || 'N/A'}</p>
            <p className="text-gray-500 text-sm">Optometrist: {prescription.optometristName}</p>
          </div>
          <div>
            <p className="text-gray-500 text-sm">Date: {formatDate(prescription.date)}</p>
            <p className="text-gray-500 text-sm">Valid Until: {formatDate(prescription.validUntil)}</p>
            <p className="text-gray-500 text-sm">PD: {prescription.pd}mm</p>
          </div>
        </div>

        <table className="w-full mt-4 text-sm">
          <thead>
            <tr className="border-b border-gray-200">
              <th className="text-left text-gray-600 py-2">Eye</th>
              <th className="text-center text-gray-600 py-2">SPH</th>
              <th className="text-center text-gray-600 py-2">CYL</th>
              <th className="text-center text-gray-600 py-2">AXIS</th>
              <th className="text-center text-gray-600 py-2">ADD</th>
            </tr>
          </thead>
          <tbody>
            <tr className="border-b border-gray-200">
              <td className="text-gray-900 py-2">Right (OD)</td>
              <td className="text-center text-gray-600">{prescription.rightEye.sphere.toFixed(2)}</td>
              <td className="text-center text-gray-600">{prescription.rightEye.cylinder.toFixed(2)}</td>
              <td className="text-center text-gray-600">{prescription.rightEye.axis}</td>
              <td className="text-center text-gray-600">{prescription.rightEye.add.toFixed(2)}</td>
            </tr>
            <tr>
              <td className="text-gray-900 py-2">Left (OS)</td>
              <td className="text-center text-gray-600">{prescription.leftEye.sphere.toFixed(2)}</td>
              <td className="text-center text-gray-600">{prescription.leftEye.cylinder.toFixed(2)}</td>
              <td className="text-center text-gray-600">{prescription.leftEye.axis}</td>
              <td className="text-center text-gray-600">{prescription.leftEye.add.toFixed(2)}</td>
            </tr>
          </tbody>
        </table>

        {prescription.notes && (
          <p className="mt-4 text-gray-500 text-sm">Notes: {prescription.notes}</p>
        )}
      </div>
    </div>
  );
}

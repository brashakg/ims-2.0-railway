// ============================================================================
// IMS 2.0 - Workshop Job Card Print Component
// ============================================================================
// A5 format job card for workshop tracking with QC checklist

import { useRef } from 'react';
import { Printer, X } from 'lucide-react';

interface JobCardPrintData {
  jobNumber: string;
  orderNumber: string;
  customerName: string;
  customerPhone: string;
  frameBrand: string;
  frameModel: string;
  frameColor: string;
  lensType: string;
  lensPower?: string;
  lensCoating?: string;
  lensTint?: string;
  priority: string;
  dueDate: string;
  assignedTechnician?: string;
  status: string;
  createdDate: string;
}

interface StoreInfo {
  storeName: string;
  address: string;
  city: string;
  state: string;
  pincode: string;
}

interface WorkshopJobCardPrintProps {
  job: JobCardPrintData;
  store: StoreInfo;
  onClose: () => void;
}

function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleDateString('en-IN', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  });
}

const getPriorityColor = (priority: string): string => {
  switch (priority.toUpperCase()) {
    case 'URGENT':
      return '#dc2626';
    case 'HIGH':
      return '#f97316';
    case 'NORMAL':
      return '#3b82f6';
    case 'LOW':
      return '#6b7280';
    default:
      return '#3b82f6';
  }
};

export function WorkshopJobCardPrint({
  job,
  store,
  onClose,
}: WorkshopJobCardPrintProps) {
  const printRef = useRef<HTMLDivElement>(null);

  const handlePrint = () => {
    window.print();
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg max-h-[95vh] overflow-y-auto">
        {/* Action Bar - hidden during print */}
        <div className="flex items-center justify-between p-4 border-b border-gray-200 no-print">
          <h2 className="text-lg font-bold text-gray-900">Print Job Card</h2>
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

        {/* Printable Document - A5 size */}
        <div
          ref={printRef}
          className="job-card-print-area bg-white p-4"
          style={{ maxWidth: '148mm', margin: '0 auto' }}
        >
          {/* Header */}
          <div className="text-center mb-3 pb-2 border-b-2 border-gray-800">
            <h1 className="text-sm font-bold text-gray-900 uppercase">{store.storeName}</h1>
            <p className="text-gray-700 text-[10px]">{store.city}, {store.state}</p>
          </div>

          {/* Title */}
          <div className="text-center mb-3">
            <h2 className="text-lg font-bold text-gray-900 uppercase tracking-widest">
              WORKSHOP JOB CARD
            </h2>
          </div>

          {/* Job & Order Details - Two columns */}
          <div className="grid grid-cols-2 gap-2 mb-3 text-xs">
            <div className="border-b border-gray-300 pb-1">
              <p className="text-gray-600 font-semibold">Job Number</p>
              <p className="text-gray-900 font-mono font-bold text-sm">{job.jobNumber}</p>
            </div>
            <div className="border-b border-gray-300 pb-1">
              <p className="text-gray-600 font-semibold">Order Ref</p>
              <p className="text-gray-900 font-mono font-bold text-sm">{job.orderNumber}</p>
            </div>
            <div className="border-b border-gray-300 pb-1">
              <p className="text-gray-600 font-semibold">Priority</p>
              <p
                className="font-bold text-xs"
                style={{ color: getPriorityColor(job.priority) }}
              >
                {job.priority.toUpperCase()}
              </p>
            </div>
            <div className="border-b border-gray-300 pb-1">
              <p className="text-gray-600 font-semibold">Status</p>
              <p className="text-gray-900 font-semibold text-xs">{job.status}</p>
            </div>
          </div>

          {/* Customer Details */}
          <div className="mb-3 p-2 bg-gray-50 border border-gray-300 rounded text-xs">
            <p className="text-gray-600 font-semibold mb-1">Customer</p>
            <p className="text-gray-900 font-medium">{job.customerName}</p>
            <p className="text-gray-700">Ph: {job.customerPhone}</p>
          </div>

          {/* Frame Details */}
          <div className="mb-3 border-l-4 border-blue-600 pl-2 text-xs">
            <p className="text-gray-600 font-semibold mb-1">Frame Details</p>
            <p className="text-gray-900">
              <span className="font-semibold">{job.frameBrand}</span> {job.frameModel}
            </p>
            <p className="text-gray-700">Color: {job.frameColor}</p>
          </div>

          {/* Lens Details */}
          <div className="mb-3 border-l-4 border-green-600 pl-2 text-xs">
            <p className="text-gray-600 font-semibold mb-1">Lens Details</p>
            <p className="text-gray-900">Type: {job.lensType}</p>
            {job.lensPower && <p className="text-gray-700">Power: {job.lensPower}</p>}
            {job.lensCoating && <p className="text-gray-700">Coating: {job.lensCoating}</p>}
            {job.lensTint && <p className="text-gray-700">Tint: {job.lensTint}</p>}
          </div>

          {/* Dates & Assignment */}
          <div className="grid grid-cols-2 gap-2 mb-3 text-xs">
            <div className="border border-gray-300 p-1 rounded">
              <p className="text-gray-600 font-semibold text-[9px]">Created</p>
              <p className="text-gray-900 font-mono text-xs">{formatDate(job.createdDate)}</p>
            </div>
            <div className="border border-gray-300 p-1 rounded">
              <p className="text-gray-600 font-semibold text-[9px]">Due Date</p>
              <p className="text-gray-900 font-mono text-xs">{formatDate(job.dueDate)}</p>
            </div>
          </div>

          {job.assignedTechnician && (
            <div className="mb-3 text-xs">
              <p className="text-gray-600 font-semibold">Assigned To</p>
              <p className="text-gray-900">{job.assignedTechnician}</p>
            </div>
          )}

          {/* QC Checklist */}
          <div className="mb-3 border border-gray-300 rounded p-2 bg-gray-50">
            <p className="text-gray-900 font-bold text-xs uppercase mb-2">QC Checklist</p>
            <div className="space-y-1.5 text-xs">
              <label className="flex items-start gap-2">
                <input
                  type="checkbox"
                  className="w-3.5 h-3.5 mt-0.5 border-gray-300 rounded"
                />
                <span className="text-gray-700">Power Verification</span>
              </label>
              <label className="flex items-start gap-2">
                <input
                  type="checkbox"
                  className="w-3.5 h-3.5 mt-0.5 border-gray-300 rounded"
                />
                <span className="text-gray-700">Frame Fitting</span>
              </label>
              <label className="flex items-start gap-2">
                <input
                  type="checkbox"
                  className="w-3.5 h-3.5 mt-0.5 border-gray-300 rounded"
                />
                <span className="text-gray-700">Cosmetic Check</span>
              </label>
              <label className="flex items-start gap-2">
                <input
                  type="checkbox"
                  className="w-3.5 h-3.5 mt-0.5 border-gray-300 rounded"
                />
                <span className="text-gray-700">Alignment Check</span>
              </label>
              <label className="flex items-start gap-2">
                <input
                  type="checkbox"
                  className="w-3.5 h-3.5 mt-0.5 border-gray-300 rounded"
                />
                <span className="text-gray-700">Lens Centration</span>
              </label>
            </div>
          </div>

          {/* Status Pipeline */}
          <div className="mb-3 text-xs">
            <p className="text-gray-600 font-semibold mb-2">Status Pipeline</p>
            <div className="flex items-center justify-between gap-0">
              {['Pending', 'In Progress', 'QC', 'Ready'].map((step, idx) => (
                <div key={idx} className="flex items-center">
                  <div
                    className="w-6 h-6 rounded-full flex items-center justify-center text-[9px] font-bold"
                    style={{
                      backgroundColor:
                        idx <= 1 ? '#3b82f6' : idx === 2 ? '#f97316' : '#d1d5db',
                      color: idx <= 1 ? '#fff' : idx === 2 ? '#fff' : '#6b7280',
                    }}
                  >
                    {idx + 1}
                  </div>
                  {idx < 3 && <div className="flex-1 h-0.5 mx-0.5 bg-gray-300" />}
                </div>
              ))}
            </div>
            <div className="grid grid-cols-4 gap-1 mt-1 text-[9px] text-gray-600 text-center">
              <span>Pending</span>
              <span>Progress</span>
              <span>QC</span>
              <span>Ready</span>
            </div>
          </div>

          {/* Signature/Notes Line */}
          <div className="mt-4 pt-2 border-t border-gray-200">
            <div className="grid grid-cols-2 gap-4 text-xs">
              <div className="text-center">
                <div className="w-20 h-12 border-b border-gray-400 mx-auto" />
                <p className="text-gray-600 text-[9px] mt-1">Technician Sign</p>
              </div>
              <div className="text-center">
                <div className="w-20 h-12 border-b border-gray-400 mx-auto" />
                <p className="text-gray-600 text-[9px] mt-1">QC Sign</p>
              </div>
            </div>
          </div>

          {/* Footer */}
          <div className="mt-2 pt-1 border-t border-gray-200 text-center">
            <p className="text-[8px] text-gray-400">
              {store.storeName} • {job.jobNumber}
            </p>
          </div>
        </div>
      </div>

      {/* Print-specific CSS for A5 page */}
      <style>{`
        @media print {
          body * {
            visibility: hidden;
          }
          .job-card-print-area,
          .job-card-print-area * {
            visibility: visible;
          }
          .job-card-print-area {
            position: absolute;
            left: 0;
            top: 0;
            width: 100%;
            padding: 6mm;
            margin: 0;
            max-width: none;
          }

          .no-print {
            display: none !important;
          }

          @page {
            size: A5;
            margin: 6mm;
          }

          .job-card-print-area {
            font-size: 9pt;
            color: #000;
            background: #fff;
          }
          .job-card-print-area h1 {
            font-size: 11pt;
          }
          .job-card-print-area h2 {
            font-size: 10pt;
          }
          table {
            page-break-inside: avoid;
          }
        }
      `}</style>
    </div>
  );
}

export default WorkshopJobCardPrint;

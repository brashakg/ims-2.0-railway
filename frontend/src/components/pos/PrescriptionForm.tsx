import { useState } from 'react';
import { Eye, Plus, X } from 'lucide-react';

interface PrescriptionData {
  sph_od?: number;
  cyl_od?: number;
  axis_od?: number;
  add_od?: number;
  pd_od?: number;
  sph_os?: number;
  cyl_os?: number;
  axis_os?: number;
  add_os?: number;
  pd_os?: number;
  issue_date?: string;
  expiry_date?: string;
  doctor_name?: string;
}

interface PrescriptionFormProps {
  onSubmit: (prescription: PrescriptionData) => void;
  onCancel: () => void;
  initialData?: PrescriptionData;
}

export function PrescriptionForm({
  onSubmit,
  onCancel,
  initialData,
}: PrescriptionFormProps) {
  const [prescription, setPrescription] = useState<PrescriptionData>(
    initialData || {}
  );

  const handleInputChange = (
    field: keyof PrescriptionData,
    value: string | number
  ) => {
    setPrescription(prev => ({
      ...prev,
      [field]: typeof value === 'string' ? (value ? parseFloat(value) : undefined) : value,
    }));
  };

  const handleStringInputChange = (
    field: keyof PrescriptionData,
    value: string
  ) => {
    setPrescription(prev => ({
      ...prev,
      [field]: value || undefined,
    }));
  };

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-gray-800 rounded-lg max-w-4xl w-full mx-4 max-h-screen overflow-y-auto">
        {/* Header */}
        <div className="bg-gray-900 border-b border-gray-700 px-6 py-4 flex items-center justify-between sticky top-0">
          <div className="flex items-center gap-2">
            <Eye className="w-6 h-6 text-blue-400" />
            <h2 className="text-xl font-bold text-white">Lens Prescription Details</h2>
          </div>
          <button
            onClick={onCancel}
            className="text-gray-400 hover:text-white transition-colors"
          >
            <X className="w-6 h-6" />
          </button>
        </div>

        <div className="p-6 space-y-6">
          {/* Prescription Dates */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">
                Prescription Issue Date
              </label>
              <input
                type="date"
                value={prescription.issue_date || ''}
                onChange={(e) => handleStringInputChange('issue_date', e.target.value)}
                className="w-full bg-gray-700 text-white border border-gray-600 rounded px-3 py-2 focus:outline-none focus:border-blue-500"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">
                Expiry Date
              </label>
              <input
                type="date"
                value={prescription.expiry_date || ''}
                onChange={(e) => handleStringInputChange('expiry_date', e.target.value)}
                className="w-full bg-gray-700 text-white border border-gray-600 rounded px-3 py-2 focus:outline-none focus:border-blue-500"
              />
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-300 mb-2">
              Prescribing Doctor Name
            </label>
            <input
              type="text"
              placeholder="Dr. Name"
              value={prescription.doctor_name || ''}
              onChange={(e) => handleStringInputChange('doctor_name', e.target.value)}
              className="w-full bg-gray-700 text-white border border-gray-600 rounded px-3 py-2 focus:outline-none focus:border-blue-500"
            />
          </div>

          {/* Right Eye (OD) */}
          <div className="bg-gray-700 rounded-lg p-4">
            <h3 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
              <Eye className="w-5 h-5 text-blue-400" />
              Right Eye (OD)
            </h3>
            <div className="grid grid-cols-3 gap-4">
              <div>
                <label className="block text-xs font-medium text-gray-300 mb-1">
                  SPH (Sphere)
                </label>
                <input
                  type="number"
                  step="0.25"
                  placeholder="+1.00"
                  value={prescription.sph_od || ''}
                  onChange={(e) => handleInputChange('sph_od', e.target.value)}
                  className="w-full bg-gray-800 text-white border border-gray-600 rounded px-2 py-2 text-sm focus:outline-none focus:border-blue-500"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-300 mb-1">
                  CYL (Cylinder)
                </label>
                <input
                  type="number"
                  step="0.25"
                  placeholder="-0.50"
                  value={prescription.cyl_od || ''}
                  onChange={(e) => handleInputChange('cyl_od', e.target.value)}
                  className="w-full bg-gray-800 text-white border border-gray-600 rounded px-2 py-2 text-sm focus:outline-none focus:border-blue-500"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-300 mb-1">
                  AXIS
                </label>
                <input
                  type="number"
                  min="0"
                  max="180"
                  placeholder="90"
                  value={prescription.axis_od || ''}
                  onChange={(e) => handleInputChange('axis_od', e.target.value)}
                  className="w-full bg-gray-800 text-white border border-gray-600 rounded px-2 py-2 text-sm focus:outline-none focus:border-blue-500"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-300 mb-1">
                  ADD (Addition)
                </label>
                <input
                  type="number"
                  step="0.25"
                  placeholder="+2.00"
                  value={prescription.add_od || ''}
                  onChange={(e) => handleInputChange('add_od', e.target.value)}
                  className="w-full bg-gray-800 text-white border border-gray-600 rounded px-2 py-2 text-sm focus:outline-none focus:border-blue-500"
                />
              </div>
              <div className="col-span-2">
                <label className="block text-xs font-medium text-gray-300 mb-1">
                  PD (Pupillary Distance) - mm
                </label>
                <input
                  type="number"
                  step="0.5"
                  placeholder="32.5"
                  value={prescription.pd_od || ''}
                  onChange={(e) => handleInputChange('pd_od', e.target.value)}
                  className="w-full bg-gray-800 text-white border border-gray-600 rounded px-2 py-2 text-sm focus:outline-none focus:border-blue-500"
                />
              </div>
            </div>
          </div>

          {/* Left Eye (OS) */}
          <div className="bg-gray-700 rounded-lg p-4">
            <h3 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
              <Eye className="w-5 h-5 text-green-400" />
              Left Eye (OS)
            </h3>
            <div className="grid grid-cols-3 gap-4">
              <div>
                <label className="block text-xs font-medium text-gray-300 mb-1">
                  SPH (Sphere)
                </label>
                <input
                  type="number"
                  step="0.25"
                  placeholder="+1.00"
                  value={prescription.sph_os || ''}
                  onChange={(e) => handleInputChange('sph_os', e.target.value)}
                  className="w-full bg-gray-800 text-white border border-gray-600 rounded px-2 py-2 text-sm focus:outline-none focus:border-blue-500"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-300 mb-1">
                  CYL (Cylinder)
                </label>
                <input
                  type="number"
                  step="0.25"
                  placeholder="-0.50"
                  value={prescription.cyl_os || ''}
                  onChange={(e) => handleInputChange('cyl_os', e.target.value)}
                  className="w-full bg-gray-800 text-white border border-gray-600 rounded px-2 py-2 text-sm focus:outline-none focus:border-blue-500"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-300 mb-1">
                  AXIS
                </label>
                <input
                  type="number"
                  min="0"
                  max="180"
                  placeholder="90"
                  value={prescription.axis_os || ''}
                  onChange={(e) => handleInputChange('axis_os', e.target.value)}
                  className="w-full bg-gray-800 text-white border border-gray-600 rounded px-2 py-2 text-sm focus:outline-none focus:border-blue-500"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-300 mb-1">
                  ADD (Addition)
                </label>
                <input
                  type="number"
                  step="0.25"
                  placeholder="+2.00"
                  value={prescription.add_os || ''}
                  onChange={(e) => handleInputChange('add_os', e.target.value)}
                  className="w-full bg-gray-800 text-white border border-gray-600 rounded px-2 py-2 text-sm focus:outline-none focus:border-blue-500"
                />
              </div>
              <div className="col-span-2">
                <label className="block text-xs font-medium text-gray-300 mb-1">
                  PD (Pupillary Distance) - mm
                </label>
                <input
                  type="number"
                  step="0.5"
                  placeholder="32.5"
                  value={prescription.pd_os || ''}
                  onChange={(e) => handleInputChange('pd_os', e.target.value)}
                  className="w-full bg-gray-800 text-white border border-gray-600 rounded px-2 py-2 text-sm focus:outline-none focus:border-blue-500"
                />
              </div>
            </div>
          </div>

          {/* Info Box */}
          <div className="bg-blue-900 rounded-lg p-4 text-blue-300 text-sm">
            <p>
              📋 <strong>Note:</strong> All prescription values are optional. Enter values as needed for the customer's lens requirements.
            </p>
          </div>

          {/* Action Buttons */}
          <div className="flex gap-4 pt-4 border-t border-gray-700">
            <button
              onClick={onCancel}
              className="flex-1 bg-gray-700 hover:bg-gray-600 text-white py-3 rounded-lg font-semibold transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={() => onSubmit(prescription)}
              className="flex-1 bg-blue-600 hover:bg-blue-700 text-white py-3 rounded-lg font-semibold transition-colors flex items-center justify-center gap-2"
            >
              <Plus className="w-5 h-5" />
              Add to Order
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default PrescriptionForm;

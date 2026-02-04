// ============================================================================
// IMS 2.0 - Prescription Panel for POS
// ============================================================================
// Displays and allows editing of prescription data

import { useState } from 'react';
import { Eye, Edit3, Check, X, Link2 } from 'lucide-react';
import type { Prescription, EyePower } from '../../types';

interface PrescriptionPanelProps {
  prescription: Prescription;
  onPrescriptionChange?: (prescription: Prescription) => void;
  onOpenModal?: () => void;
  patientName?: string;
  compact?: boolean;
  readOnly?: boolean;
}

export function PrescriptionPanel({
  prescription,
  onPrescriptionChange,
  onOpenModal,
  patientName,
  compact = false,
  readOnly = false,
}: PrescriptionPanelProps) {
  const [isEditing, setIsEditing] = useState(false);
  const [editedPrescription, setEditedPrescription] = useState<Prescription>(prescription);

  const formatPower = (value: number | null | undefined) => {
    if (value === null || value === undefined) return '-';
    return value >= 0 ? `+${value.toFixed(2)}` : value.toFixed(2);
  };

  const formatDate = (dateString: string) => {
    return new Date(dateString).toLocaleDateString('en-IN', {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
    });
  };

  const handleEyeChange = (eye: 'rightEye' | 'leftEye', field: keyof EyePower, value: string) => {
    const numValue = parseFloat(value);
    setEditedPrescription(prev => ({
      ...prev,
      [eye]: {
        ...prev[eye],
        [field]: isNaN(numValue) ? null : numValue,
      },
    }));
  };

  const handleSave = () => {
    if (onPrescriptionChange) {
      onPrescriptionChange(editedPrescription);
    }
    setIsEditing(false);
  };

  const handleCancel = () => {
    setEditedPrescription(prescription);
    setIsEditing(false);
  };

  if (compact) {
    return (
      <div className="space-y-2">
        {/* Header with date and source */}
        <div className="flex items-center justify-between text-xs text-gray-500">
          <span>Test Date: {formatDate(prescription.testDate)}</span>
          {prescription.isExternal ? (
            <span className="px-2 py-0.5 bg-purple-100 text-purple-700 rounded">External</span>
          ) : prescription.optometristName && (
            <span>By: {prescription.optometristName}</span>
          )}
        </div>

        {/* Prescription Grid */}
        <div className="grid grid-cols-2 gap-3">
          {/* Right Eye */}
          <div className="p-2 bg-blue-50 rounded-lg">
            <p className="text-xs font-medium text-blue-700 mb-1">Right Eye (OD)</p>
            <div className="flex gap-2 text-xs">
              <div>
                <span className="text-gray-500">SPH</span>
                <p className="font-semibold">{formatPower(prescription.rightEye.sphere)}</p>
              </div>
              <div>
                <span className="text-gray-500">CYL</span>
                <p className="font-semibold">{formatPower(prescription.rightEye.cylinder)}</p>
              </div>
              <div>
                <span className="text-gray-500">AXIS</span>
                <p className="font-semibold">{prescription.rightEye.axis || '-'}</p>
              </div>
              {prescription.rightEye.add && (
                <div>
                  <span className="text-gray-500">ADD</span>
                  <p className="font-semibold">{formatPower(prescription.rightEye.add)}</p>
                </div>
              )}
            </div>
          </div>

          {/* Left Eye */}
          <div className="p-2 bg-green-50 rounded-lg">
            <p className="text-xs font-medium text-green-700 mb-1">Left Eye (OS)</p>
            <div className="flex gap-2 text-xs">
              <div>
                <span className="text-gray-500">SPH</span>
                <p className="font-semibold">{formatPower(prescription.leftEye.sphere)}</p>
              </div>
              <div>
                <span className="text-gray-500">CYL</span>
                <p className="font-semibold">{formatPower(prescription.leftEye.cylinder)}</p>
              </div>
              <div>
                <span className="text-gray-500">AXIS</span>
                <p className="font-semibold">{prescription.leftEye.axis || '-'}</p>
              </div>
              {prescription.leftEye.add && (
                <div>
                  <span className="text-gray-500">ADD</span>
                  <p className="font-semibold">{formatPower(prescription.leftEye.add)}</p>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* PD */}
        <div className="flex items-center justify-between text-xs">
          <span className="text-gray-500">
            PD: {prescription.rightEye.pd}/{prescription.leftEye.pd}
          </span>
          {!readOnly && onOpenModal && (
            <button
              onClick={onOpenModal}
              className="text-bv-red-600 hover:text-bv-red-700 flex items-center gap-1"
            >
              <Link2 className="w-3 h-3" />
              Change
            </button>
          )}
        </div>

        {/* Recommendation */}
        {prescription.recommendation && (
          <p className="text-xs text-gray-600 bg-yellow-50 p-2 rounded">
            {prescription.recommendation}
          </p>
        )}
      </div>
    );
  }

  // Full view
  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Eye className="w-5 h-5 text-bv-red-600" />
          <h3 className="font-medium text-gray-900">Prescription</h3>
          {patientName && (
            <span className="text-sm text-gray-500">({patientName})</span>
          )}
        </div>
        {!readOnly && !isEditing && (
          <button
            onClick={() => setIsEditing(true)}
            className="text-sm text-bv-red-600 hover:text-bv-red-700 flex items-center gap-1"
          >
            <Edit3 className="w-4 h-4" />
            Edit
          </button>
        )}
        {isEditing && (
          <div className="flex items-center gap-2">
            <button
              onClick={handleCancel}
              className="text-sm text-gray-500 hover:text-gray-700 flex items-center gap-1"
            >
              <X className="w-4 h-4" />
              Cancel
            </button>
            <button
              onClick={handleSave}
              className="text-sm text-green-600 hover:text-green-700 flex items-center gap-1"
            >
              <Check className="w-4 h-4" />
              Save
            </button>
          </div>
        )}
      </div>

      {/* Prescription Data */}
      <div className="grid grid-cols-2 gap-4">
        {/* Right Eye */}
        <div className="p-4 bg-blue-50 rounded-lg">
          <h4 className="font-medium text-blue-700 mb-3">Right Eye (OD)</h4>
          <div className="grid grid-cols-4 gap-3">
            <div>
              <label className="text-xs text-gray-500 block">SPH</label>
              {isEditing ? (
                <input
                  type="number"
                  step="0.25"
                  value={editedPrescription.rightEye.sphere || ''}
                  onChange={(e) => handleEyeChange('rightEye', 'sphere', e.target.value)}
                  className="w-full px-2 py-1 text-sm border rounded"
                />
              ) : (
                <p className="font-semibold">{formatPower(prescription.rightEye.sphere)}</p>
              )}
            </div>
            <div>
              <label className="text-xs text-gray-500 block">CYL</label>
              {isEditing ? (
                <input
                  type="number"
                  step="0.25"
                  value={editedPrescription.rightEye.cylinder || ''}
                  onChange={(e) => handleEyeChange('rightEye', 'cylinder', e.target.value)}
                  className="w-full px-2 py-1 text-sm border rounded"
                />
              ) : (
                <p className="font-semibold">{formatPower(prescription.rightEye.cylinder)}</p>
              )}
            </div>
            <div>
              <label className="text-xs text-gray-500 block">AXIS</label>
              {isEditing ? (
                <input
                  type="number"
                  min="0"
                  max="180"
                  value={editedPrescription.rightEye.axis || ''}
                  onChange={(e) => handleEyeChange('rightEye', 'axis', e.target.value)}
                  className="w-full px-2 py-1 text-sm border rounded"
                />
              ) : (
                <p className="font-semibold">{prescription.rightEye.axis || '-'}</p>
              )}
            </div>
            <div>
              <label className="text-xs text-gray-500 block">ADD</label>
              {isEditing ? (
                <input
                  type="number"
                  step="0.25"
                  value={editedPrescription.rightEye.add || ''}
                  onChange={(e) => handleEyeChange('rightEye', 'add', e.target.value)}
                  className="w-full px-2 py-1 text-sm border rounded"
                />
              ) : (
                <p className="font-semibold">{formatPower(prescription.rightEye.add)}</p>
              )}
            </div>
          </div>
          <div className="mt-2">
            <label className="text-xs text-gray-500 block">PD</label>
            {isEditing ? (
              <input
                type="number"
                step="0.5"
                value={editedPrescription.rightEye.pd || ''}
                onChange={(e) => handleEyeChange('rightEye', 'pd', e.target.value)}
                className="w-20 px-2 py-1 text-sm border rounded"
              />
            ) : (
              <p className="font-semibold">{prescription.rightEye.pd}</p>
            )}
          </div>
        </div>

        {/* Left Eye */}
        <div className="p-4 bg-green-50 rounded-lg">
          <h4 className="font-medium text-green-700 mb-3">Left Eye (OS)</h4>
          <div className="grid grid-cols-4 gap-3">
            <div>
              <label className="text-xs text-gray-500 block">SPH</label>
              {isEditing ? (
                <input
                  type="number"
                  step="0.25"
                  value={editedPrescription.leftEye.sphere || ''}
                  onChange={(e) => handleEyeChange('leftEye', 'sphere', e.target.value)}
                  className="w-full px-2 py-1 text-sm border rounded"
                />
              ) : (
                <p className="font-semibold">{formatPower(prescription.leftEye.sphere)}</p>
              )}
            </div>
            <div>
              <label className="text-xs text-gray-500 block">CYL</label>
              {isEditing ? (
                <input
                  type="number"
                  step="0.25"
                  value={editedPrescription.leftEye.cylinder || ''}
                  onChange={(e) => handleEyeChange('leftEye', 'cylinder', e.target.value)}
                  className="w-full px-2 py-1 text-sm border rounded"
                />
              ) : (
                <p className="font-semibold">{formatPower(prescription.leftEye.cylinder)}</p>
              )}
            </div>
            <div>
              <label className="text-xs text-gray-500 block">AXIS</label>
              {isEditing ? (
                <input
                  type="number"
                  min="0"
                  max="180"
                  value={editedPrescription.leftEye.axis || ''}
                  onChange={(e) => handleEyeChange('leftEye', 'axis', e.target.value)}
                  className="w-full px-2 py-1 text-sm border rounded"
                />
              ) : (
                <p className="font-semibold">{prescription.leftEye.axis || '-'}</p>
              )}
            </div>
            <div>
              <label className="text-xs text-gray-500 block">ADD</label>
              {isEditing ? (
                <input
                  type="number"
                  step="0.25"
                  value={editedPrescription.leftEye.add || ''}
                  onChange={(e) => handleEyeChange('leftEye', 'add', e.target.value)}
                  className="w-full px-2 py-1 text-sm border rounded"
                />
              ) : (
                <p className="font-semibold">{formatPower(prescription.leftEye.add)}</p>
              )}
            </div>
          </div>
          <div className="mt-2">
            <label className="text-xs text-gray-500 block">PD</label>
            {isEditing ? (
              <input
                type="number"
                step="0.5"
                value={editedPrescription.leftEye.pd || ''}
                onChange={(e) => handleEyeChange('leftEye', 'pd', e.target.value)}
                className="w-20 px-2 py-1 text-sm border rounded"
              />
            ) : (
              <p className="font-semibold">{prescription.leftEye.pd}</p>
            )}
          </div>
        </div>
      </div>

      {/* Additional Info */}
      <div className="flex items-center justify-between text-sm text-gray-500">
        <span>Test Date: {formatDate(prescription.testDate)}</span>
        {prescription.isExternal ? (
          <span>Source: {prescription.externalSource}</span>
        ) : prescription.optometristName && (
          <span>By: {prescription.optometristName}</span>
        )}
      </div>

      {/* Recommendation */}
      {prescription.recommendation && (
        <div className="p-3 bg-yellow-50 rounded-lg">
          <p className="text-sm text-gray-700">{prescription.recommendation}</p>
        </div>
      )}
    </div>
  );
}

export default PrescriptionPanel;

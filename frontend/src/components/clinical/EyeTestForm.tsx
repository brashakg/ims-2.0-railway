// ============================================================================
// IMS 2.0 - Eye Test Entry Form Component
// ============================================================================
// Full prescription entry with axis validation (must be 1-180 whole number)
// NO MOCK DATA - Loads optometrists from API

import { useState, useEffect, useCallback } from 'react';
import { X, Save, AlertTriangle, Eye, FileText, Loader2 } from 'lucide-react';
import clsx from 'clsx';
import { adminUserApi } from '../../services/api';
import { useAuth } from '../../context/AuthContext';

interface EyeTestFormProps {
  patientName: string;
  patientId: string;
  onSave: (prescription: PrescriptionData) => void;
  onClose: () => void;
}

interface EyeData {
  sphere: string;
  cylinder: string;
  axis: string;
  add: string;
  pd: string;
  prism: string;
  base: string;
  acuity: string;
}

interface PrescriptionData {
  patientId: string;
  source: 'TESTED_AT_STORE' | 'FROM_DOCTOR';
  optometristId?: string;
  doctorName?: string;
  rightEye: EyeData;
  leftEye: EyeData;
  lensRecommendation: string;
  coatingRecommendation: string;
  validityMonths: number;
  remarks: string;
}

const EMPTY_EYE: EyeData = {
  sphere: '',
  cylinder: '',
  axis: '',
  add: '',
  pd: '',
  prism: '',
  base: '',
  acuity: '',
};

const VALIDITY_OPTIONS = [6, 12, 18, 24];

const LENS_RECOMMENDATIONS = [
  'Single Vision',
  'Bifocal',
  'Progressive',
  'Anti-Fatigue',
  'Computer Lenses',
  'Driving Lenses',
];

const COATING_RECOMMENDATIONS = [
  'Anti-Reflective',
  'Blue Light Filter',
  'Photochromic',
  'UV Protection',
  'Scratch Resistant',
  'Hydrophobic',
];

interface Optometrist {
  id: string;
  name: string;
}

export function EyeTestForm({ patientName, patientId, onSave, onClose }: EyeTestFormProps) {
  const { user } = useAuth();
  const [source, setSource] = useState<'TESTED_AT_STORE' | 'FROM_DOCTOR'>('TESTED_AT_STORE');
  const [optometristId, setOptometristId] = useState('');
  const [doctorName, setDoctorName] = useState('');
  const [rightEye, setRightEye] = useState<EyeData>(EMPTY_EYE);
  const [leftEye, setLeftEye] = useState<EyeData>(EMPTY_EYE);
  const [lensRecommendation, setLensRecommendation] = useState('');
  const [coatingRecommendation, setCoatingRecommendation] = useState('');
  const [validityMonths, setValidityMonths] = useState(12);
  const [remarks, setRemarks] = useState('');
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [optometrists, setOptometrists] = useState<Optometrist[]>([]);
  const [loadingOptometrists, setLoadingOptometrists] = useState(true);

  // Load optometrists from API
  const loadOptometrists = useCallback(async () => {
    setLoadingOptometrists(true);
    try {
      // Get users who can perform eye tests (optometrist role)
      const response = await adminUserApi.getUsers({
        storeId: user?.activeStoreId,
        role: 'optometrist',
      });

      if (response?.users && Array.isArray(response.users)) {
        const opts: Optometrist[] = response.users.map((u: any) => ({
          id: u.user_id || u.id,
          name: u.full_name || u.name || 'Unknown',
        }));
        setOptometrists(opts);
      } else {
        // If no optometrists found, use current user as fallback
        setOptometrists([{
          id: user?.id || 'current',
          name: user?.name || 'Current User',
        }]);
      }
    } catch (err) {
      console.error('Failed to load optometrists:', err);
      // Fallback to current user
      setOptometrists([{
        id: user?.id || 'current',
        name: user?.name || 'Current User',
      }]);
    } finally {
      setLoadingOptometrists(false);
    }
  }, [user?.activeStoreId, user?.id, user?.name]);

  // Load on mount
  useEffect(() => {
    loadOptometrists();
  }, [loadOptometrists]);

  // Validate axis (must be whole number 1-180)
  const validateAxis = (value: string, eye: 'right' | 'left'): boolean => {
    if (!value) return true; // Optional if no cylinder
    const num = parseInt(value, 10);
    if (isNaN(num) || num < 1 || num > 180 || !Number.isInteger(num)) {
      setErrors(prev => ({
        ...prev,
        [`${eye}Axis`]: 'Axis must be whole number 1-180',
      }));
      return false;
    }
    setErrors(prev => {
      const newErrors = { ...prev };
      delete newErrors[`${eye}Axis`];
      return newErrors;
    });
    return true;
  };

  // Validate sphere (-20.00 to +20.00 in 0.25 steps)
  // Reserved for future field-level validation
  const _validateSphere = (value: string): boolean => {
    if (!value) return true;
    const num = parseFloat(value);
    if (isNaN(num) || num < -20 || num > 20) {
      return false;
    }
    // Check if it's in 0.25 steps
    const remainder = Math.abs(num * 100) % 25;
    return remainder === 0;
  };
  void _validateSphere;

  // Validate cylinder (-6.00 to +6.00 in 0.25 steps)
  // Reserved for future field-level validation
  const _validateCylinder = (value: string): boolean => {
    if (!value) return true;
    const num = parseFloat(value);
    if (isNaN(num) || num < -6 || num > 6) {
      return false;
    }
    const remainder = Math.abs(num * 100) % 25;
    return remainder === 0;
  };
  void _validateCylinder;

  const handleSubmit = () => {
    const newErrors: Record<string, string> = {};

    // Validate source
    if (source === 'TESTED_AT_STORE' && !optometristId) {
      newErrors.optometrist = 'Optometrist is required for store tests';
    }
    if (source === 'FROM_DOCTOR' && !doctorName) {
      newErrors.doctorName = 'Doctor name is required';
    }

    // Validate axis
    if (rightEye.cylinder && rightEye.axis) {
      const axisNum = parseInt(rightEye.axis, 10);
      if (isNaN(axisNum) || axisNum < 1 || axisNum > 180) {
        newErrors.rightAxis = 'Right eye axis must be 1-180';
      }
    }
    if (leftEye.cylinder && leftEye.axis) {
      const axisNum = parseInt(leftEye.axis, 10);
      if (isNaN(axisNum) || axisNum < 1 || axisNum > 180) {
        newErrors.leftAxis = 'Left eye axis must be 1-180';
      }
    }

    // Validate at least one eye has data
    if (!rightEye.sphere && !leftEye.sphere) {
      newErrors.sphere = 'At least one eye must have sphere value';
    }

    if (Object.keys(newErrors).length > 0) {
      setErrors(newErrors);
      return;
    }

    const prescription: PrescriptionData = {
      patientId,
      source,
      optometristId: source === 'TESTED_AT_STORE' ? optometristId : undefined,
      doctorName: source === 'FROM_DOCTOR' ? doctorName : undefined,
      rightEye,
      leftEye,
      lensRecommendation,
      coatingRecommendation,
      validityMonths,
      remarks,
    };

    onSave(prescription);
  };

  const updateEyeField = (eye: 'right' | 'left', field: keyof EyeData, value: string) => {
    if (eye === 'right') {
      setRightEye(prev => ({ ...prev, [field]: value }));
    } else {
      setLeftEye(prev => ({ ...prev, [field]: value }));
    }

    // Clear related errors
    if (field === 'axis') {
      validateAxis(value, eye);
    }
  };

  const renderEyeFields = (eye: 'right' | 'left', data: EyeData, label: string) => (
    <div className="space-y-3">
      <h4 className="font-medium text-gray-900 flex items-center gap-2">
        <Eye className="w-4 h-4" />
        {label} ({eye === 'right' ? 'OD' : 'OS'})
      </h4>
      <div className="grid grid-cols-4 gap-3">
        <div>
          <label className="block text-xs text-gray-500 mb-1">SPH</label>
          <input
            type="number"
            step="0.25"
            min="-20"
            max="20"
            value={data.sphere}
            onChange={e => updateEyeField(eye, 'sphere', e.target.value)}
            className="input-field text-sm"
            placeholder="0.00"
          />
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">CYL</label>
          <input
            type="number"
            step="0.25"
            min="-6"
            max="6"
            value={data.cylinder}
            onChange={e => updateEyeField(eye, 'cylinder', e.target.value)}
            className="input-field text-sm"
            placeholder="0.00"
          />
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">
            AXIS
            {errors[`${eye}Axis`] && (
              <span className="text-red-500 ml-1">*</span>
            )}
          </label>
          <input
            type="number"
            step="1"
            min="1"
            max="180"
            value={data.axis}
            onChange={e => updateEyeField(eye, 'axis', e.target.value)}
            onBlur={e => validateAxis(e.target.value, eye)}
            className={clsx(
              'input-field text-sm',
              errors[`${eye}Axis`] && 'border-red-500'
            )}
            placeholder="1-180"
          />
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">ADD</label>
          <input
            type="number"
            step="0.25"
            min="0.75"
            max="3.50"
            value={data.add}
            onChange={e => updateEyeField(eye, 'add', e.target.value)}
            className="input-field text-sm"
            placeholder="+0.00"
          />
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">PD</label>
          <input
            type="number"
            step="0.5"
            value={data.pd}
            onChange={e => updateEyeField(eye, 'pd', e.target.value)}
            className="input-field text-sm"
            placeholder="32"
          />
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">PRISM</label>
          <input
            type="number"
            step="0.25"
            value={data.prism}
            onChange={e => updateEyeField(eye, 'prism', e.target.value)}
            className="input-field text-sm"
            placeholder="0.00"
          />
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">BASE</label>
          <select
            value={data.base}
            onChange={e => updateEyeField(eye, 'base', e.target.value)}
            className="input-field text-sm"
          >
            <option value="">-</option>
            <option value="UP">Up</option>
            <option value="DOWN">Down</option>
            <option value="IN">In</option>
            <option value="OUT">Out</option>
          </select>
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">VA</label>
          <input
            type="text"
            value={data.acuity}
            onChange={e => updateEyeField(eye, 'acuity', e.target.value)}
            className="input-field text-sm"
            placeholder="6/6"
          />
        </div>
      </div>
      {errors[`${eye}Axis`] && (
        <p className="text-xs text-red-500 flex items-center gap-1">
          <AlertTriangle className="w-3 h-3" />
          {errors[`${eye}Axis`]}
        </p>
      )}
    </div>
  );

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-4xl max-h-[95vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-gray-200">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-bv-red-100 rounded-full flex items-center justify-center">
              <FileText className="w-5 h-5 text-bv-red-600" />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-gray-900">New Eye Test</h2>
              <p className="text-sm text-gray-500">Patient: {patientName}</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-2 text-gray-400 hover:text-gray-600 rounded-lg hover:bg-gray-100"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-4 space-y-6">
          {/* Source Selection */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Prescription Source
            </label>
            <div className="flex gap-4">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  name="source"
                  checked={source === 'TESTED_AT_STORE'}
                  onChange={() => setSource('TESTED_AT_STORE')}
                  className="w-4 h-4 text-bv-red-600"
                />
                <span className="text-sm">Tested at Store</span>
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  name="source"
                  checked={source === 'FROM_DOCTOR'}
                  onChange={() => setSource('FROM_DOCTOR')}
                  className="w-4 h-4 text-bv-red-600"
                />
                <span className="text-sm">From External Doctor</span>
              </label>
            </div>
          </div>

          {/* Optometrist / Doctor Selection */}
          {source === 'TESTED_AT_STORE' ? (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Optometrist <span className="text-red-500">*</span>
              </label>
              {loadingOptometrists ? (
                <div className="flex items-center gap-2 p-2 text-gray-400">
                  <Loader2 className="w-4 h-4 animate-spin" />
                  <span className="text-sm">Loading...</span>
                </div>
              ) : (
                <select
                  value={optometristId}
                  onChange={e => setOptometristId(e.target.value)}
                  className={clsx('input-field', errors.optometrist && 'border-red-500')}
                >
                  <option value="">Select Optometrist</option>
                  {optometrists.map(opt => (
                    <option key={opt.id} value={opt.id}>{opt.name}</option>
                  ))}
                </select>
              )}
              {errors.optometrist && (
                <p className="text-xs text-red-500 mt-1">{errors.optometrist}</p>
              )}
            </div>
          ) : (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Doctor Name <span className="text-red-500">*</span>
              </label>
              <input
                type="text"
                value={doctorName}
                onChange={e => setDoctorName(e.target.value)}
                className={clsx('input-field', errors.doctorName && 'border-red-500')}
                placeholder="Dr. ..."
              />
              {errors.doctorName && (
                <p className="text-xs text-red-500 mt-1">{errors.doctorName}</p>
              )}
            </div>
          )}

          {/* Eye Data */}
          <div className="grid grid-cols-1 laptop:grid-cols-2 gap-6">
            {renderEyeFields('right', rightEye, 'Right Eye')}
            {renderEyeFields('left', leftEye, 'Left Eye')}
          </div>

          {errors.sphere && (
            <p className="text-sm text-red-500 flex items-center gap-1">
              <AlertTriangle className="w-4 h-4" />
              {errors.sphere}
            </p>
          )}

          {/* Recommendations */}
          <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Lens Recommendation
              </label>
              <select
                value={lensRecommendation}
                onChange={e => setLensRecommendation(e.target.value)}
                className="input-field"
              >
                <option value="">Select lens type</option>
                {LENS_RECOMMENDATIONS.map(lens => (
                  <option key={lens} value={lens}>{lens}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Coating Recommendation
              </label>
              <select
                value={coatingRecommendation}
                onChange={e => setCoatingRecommendation(e.target.value)}
                className="input-field"
              >
                <option value="">Select coating</option>
                {COATING_RECOMMENDATIONS.map(coat => (
                  <option key={coat} value={coat}>{coat}</option>
                ))}
              </select>
            </div>
          </div>

          {/* Validity */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Prescription Validity
            </label>
            <div className="flex gap-3">
              {VALIDITY_OPTIONS.map(months => (
                <button
                  key={months}
                  type="button"
                  onClick={() => setValidityMonths(months)}
                  className={clsx(
                    'px-4 py-2 rounded-lg text-sm font-medium transition-colors',
                    validityMonths === months
                      ? 'bg-bv-red-600 text-white'
                      : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                  )}
                >
                  {months} months
                </button>
              ))}
            </div>
          </div>

          {/* Remarks */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Remarks
            </label>
            <textarea
              value={remarks}
              onChange={e => setRemarks(e.target.value)}
              className="input-field"
              rows={3}
              placeholder="Additional notes or recommendations..."
            />
          </div>
        </div>

        {/* Footer */}
        <div className="p-4 border-t border-gray-200 flex justify-end gap-3">
          <button onClick={onClose} className="btn-outline">
            Cancel
          </button>
          <button onClick={handleSubmit} className="btn-primary flex items-center gap-2">
            <Save className="w-4 h-4" />
            Save Prescription
          </button>
        </div>
      </div>
    </div>
  );
}

export default EyeTestForm;

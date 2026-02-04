// ============================================================================
// IMS 2.0 - Patient Selector Component
// ============================================================================

import { useState } from 'react';
import { ChevronDown, User, UserPlus } from 'lucide-react';
import type { Patient } from '../../types';
import clsx from 'clsx';

interface PatientSelectorProps {
  patients: Patient[];
  selectedPatient: Patient | null;
  onSelect: (patient: Patient) => void;
  onAddPatient?: () => void;
}

export function PatientSelector({
  patients,
  selectedPatient,
  onSelect,
  onAddPatient,
}: PatientSelectorProps) {
  const [isOpen, setIsOpen] = useState(false);

  if (patients.length === 0) {
    return null;
  }

  // If only one patient, just show the name without dropdown
  if (patients.length === 1) {
    return (
      <div className="flex items-center gap-2 px-3 py-1.5 bg-gray-100 rounded-lg">
        <User className="w-4 h-4 text-gray-500" />
        <span className="text-sm font-medium text-gray-700">
          {patients[0].name}
          {patients[0].relation && patients[0].relation !== 'Self' && (
            <span className="text-gray-400 font-normal"> ({patients[0].relation})</span>
          )}
        </span>
      </div>
    );
  }

  return (
    <div className="relative">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-2 px-3 py-1.5 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors"
      >
        <User className="w-4 h-4 text-gray-500" />
        <span className="text-sm font-medium text-gray-700">
          {selectedPatient?.name || 'Select Patient'}
        </span>
        <ChevronDown className={clsx('w-4 h-4 text-gray-400 transition-transform', isOpen && 'rotate-180')} />
      </button>

      {isOpen && (
        <>
          {/* Backdrop */}
          <div
            className="fixed inset-0 z-10"
            onClick={() => setIsOpen(false)}
          />

          {/* Dropdown */}
          <div className="absolute top-full left-0 mt-1 w-56 bg-white border border-gray-200 rounded-lg shadow-lg z-20">
            <div className="py-1">
              {patients.map(patient => (
                <button
                  key={patient.id}
                  onClick={() => {
                    onSelect(patient);
                    setIsOpen(false);
                  }}
                  className={clsx(
                    'w-full flex items-center gap-3 px-4 py-2 text-left hover:bg-gray-50',
                    selectedPatient?.id === patient.id && 'bg-bv-red-50'
                  )}
                >
                  <div className={clsx(
                    'w-8 h-8 rounded-full flex items-center justify-center',
                    selectedPatient?.id === patient.id ? 'bg-bv-red-100' : 'bg-gray-100'
                  )}>
                    <User className={clsx(
                      'w-4 h-4',
                      selectedPatient?.id === patient.id ? 'text-bv-red-600' : 'text-gray-500'
                    )} />
                  </div>
                  <div>
                    <p className={clsx(
                      'text-sm font-medium',
                      selectedPatient?.id === patient.id ? 'text-bv-red-600' : 'text-gray-900'
                    )}>
                      {patient.name}
                    </p>
                    {patient.relation && (
                      <p className="text-xs text-gray-500">{patient.relation}</p>
                    )}
                  </div>
                </button>
              ))}

              {onAddPatient && (
                <>
                  <div className="border-t border-gray-100 my-1" />
                  <button
                    onClick={() => {
                      onAddPatient();
                      setIsOpen(false);
                    }}
                    className="w-full flex items-center gap-3 px-4 py-2 text-left text-bv-red-600 hover:bg-bv-red-50"
                  >
                    <div className="w-8 h-8 rounded-full bg-bv-red-50 flex items-center justify-center">
                      <UserPlus className="w-4 h-4" />
                    </div>
                    <span className="text-sm font-medium">Add Patient</span>
                  </button>
                </>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

export default PatientSelector;

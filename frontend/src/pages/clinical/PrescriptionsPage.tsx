// ============================================================================
// IMS 2.0 - Prescriptions Page
// ============================================================================
// Manage and view patient prescriptions

import { useState, useEffect } from 'react';
import {
  FileText,
  Search,
  User,
  Eye,
  RefreshCw,
  Loader2,
  AlertCircle,
  Calendar,
} from 'lucide-react';
import { clinicalApi } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';

interface Prescription {
  id: string;
  patientName: string;
  customerPhone: string;
  prescribedAt: string;
  rightEye: {
    sphere: number | null;
    cylinder: number | null;
    axis: number | null;
    add: number | null;
  };
  leftEye: {
    sphere: number | null;
    cylinder: number | null;
    axis: number | null;
    add: number | null;
  };
  pd?: number;
  notes?: string;
  optometristName?: string;
}

export function PrescriptionsPage() {
  const { user } = useAuth();
  const toast = useToast();

  const [prescriptions, setPrescriptions] = useState<Prescription[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedPrescription, setSelectedPrescription] = useState<Prescription | null>(null);

  useEffect(() => {
    loadPrescriptions();
  }, []);

  const loadPrescriptions = async () => {
    setIsLoading(true);
    setError(null);
    try {
      // Using getTodayTests as a temporary solution
      // In production, you'd have a dedicated prescriptions API endpoint
      const response = await clinicalApi.getTodayTests(user?.activeStoreId || '');
      const data = response?.tests || response || [];

      // Map tests to prescriptions format
      const prescriptionsData = (Array.isArray(data) ? data : []).map((test: any) => ({
        ...test,
        prescribedAt: test.completedAt,
      }));

      setPrescriptions(prescriptionsData);
    } catch {
      setError('Failed to load prescriptions');
      setPrescriptions([]);
    } finally {
      setIsLoading(false);
    }
  };

  const filteredPrescriptions = prescriptions.filter(rx => {
    const matchesSearch = !searchQuery ||
      rx.patientName?.toLowerCase().includes(searchQuery.toLowerCase()) ||
      rx.customerPhone?.includes(searchQuery);
    return matchesSearch;
  });

  const formatDate = (dateStr: string) => {
    return new Date(dateStr).toLocaleDateString('en-IN', {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
    });
  };

  const formatPower = (value: number | null) => {
    if (value === null || value === undefined) return '-';
    return value >= 0 ? `+${value.toFixed(2)}` : value.toFixed(2);
  };

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Prescriptions</h1>
          <p className="text-gray-500">Manage patient prescriptions</p>
        </div>
        <button
          onClick={loadPrescriptions}
          disabled={isLoading}
          className="btn-outline flex items-center gap-2"
        >
          {isLoading ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <RefreshCw className="w-4 h-4" />
          )}
          Refresh
        </button>
      </div>

      {/* Search */}
      <div className="card">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" />
          <input
            type="text"
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            className="input-field pl-10"
            placeholder="Search by patient name or phone..."
          />
        </div>
      </div>

      {/* Error State */}
      {error && (
        <div className="card bg-red-50 border-red-200">
          <div className="flex items-center gap-3 text-red-600">
            <AlertCircle className="w-5 h-5" />
            <p>{error}</p>
            <button onClick={loadPrescriptions} className="ml-auto text-sm underline">
              Retry
            </button>
          </div>
        </div>
      )}

      {/* Prescriptions Grid */}
      {isLoading ? (
        <div className="card flex items-center justify-center py-12">
          <Loader2 className="w-8 h-8 animate-spin text-purple-600" />
        </div>
      ) : filteredPrescriptions.length === 0 ? (
        <div className="card text-center py-12 text-gray-500">
          <FileText className="w-12 h-12 mx-auto mb-2 opacity-50" />
          <p>{searchQuery ? 'No prescriptions found matching your search' : 'No prescriptions found'}</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 tablet:grid-cols-2 lg:grid-cols-3 gap-4">
          {filteredPrescriptions.map(rx => (
            <div
              key={rx.id}
              className="card hover:border-purple-200 cursor-pointer transition-all"
              onClick={() => setSelectedPrescription(rx)}
            >
              <div className="flex items-start justify-between mb-3">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 bg-purple-100 rounded-full flex items-center justify-center">
                    <User className="w-5 h-5 text-purple-600" />
                  </div>
                  <div>
                    <p className="font-medium text-gray-900">{rx.patientName}</p>
                    <p className="text-xs text-gray-500">{rx.customerPhone}</p>
                  </div>
                </div>
              </div>

              <div className="space-y-2 text-sm">
                <div className="flex items-center gap-2 text-gray-600">
                  <Calendar className="w-4 h-4" />
                  <span>{formatDate(rx.prescribedAt)}</span>
                </div>

                <div className="bg-gray-50 rounded-lg p-3 space-y-1">
                  <div className="flex justify-between">
                    <span className="text-gray-600">Right (OD):</span>
                    <span className="font-medium">
                      {formatPower(rx.rightEye.sphere)} / {formatPower(rx.rightEye.cylinder)}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-600">Left (OS):</span>
                    <span className="font-medium">
                      {formatPower(rx.leftEye.sphere)} / {formatPower(rx.leftEye.cylinder)}
                    </span>
                  </div>
                  {rx.pd && (
                    <div className="flex justify-between border-t border-gray-200 pt-1 mt-1">
                      <span className="text-gray-600">PD:</span>
                      <span className="font-medium">{rx.pd} mm</span>
                    </div>
                  )}
                </div>
              </div>

              <div className="mt-3 pt-3 border-t border-gray-100">
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    toast.info('Print functionality coming soon');
                  }}
                  className="text-sm text-purple-600 hover:text-purple-700 flex items-center gap-1"
                >
                  <FileText className="w-4 h-4" />
                  View Details
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Prescription Detail Modal */}
      {selectedPrescription && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl max-w-2xl w-full max-h-[90vh] overflow-y-auto">
            <div className="p-6">
              <div className="flex items-center justify-between mb-6">
                <h2 className="text-xl font-bold text-gray-900">Prescription Details</h2>
                <button
                  onClick={() => setSelectedPrescription(null)}
                  className="p-2 hover:bg-gray-100 rounded-lg"
                >
                  ×
                </button>
              </div>

              <div className="space-y-6">
                {/* Patient Info */}
                <div>
                  <h3 className="font-medium text-gray-900 mb-2">Patient Information</h3>
                  <div className="bg-gray-50 rounded-lg p-4 space-y-2">
                    <div className="flex justify-between">
                      <span className="text-gray-600">Name:</span>
                      <span className="font-medium">{selectedPrescription.patientName}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-gray-600">Phone:</span>
                      <span className="font-medium">{selectedPrescription.customerPhone}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-gray-600">Date:</span>
                      <span className="font-medium">{formatDate(selectedPrescription.prescribedAt)}</span>
                    </div>
                    {selectedPrescription.optometristName && (
                      <div className="flex justify-between">
                        <span className="text-gray-600">Optometrist:</span>
                        <span className="font-medium">{selectedPrescription.optometristName}</span>
                      </div>
                    )}
                  </div>
                </div>

                {/* Prescription Table */}
                <div>
                  <h3 className="font-medium text-gray-900 mb-2">Prescription</h3>
                  <div className="overflow-x-auto">
                    <table className="w-full border-collapse">
                      <thead>
                        <tr className="bg-gray-100">
                          <th className="border border-gray-200 px-4 py-2 text-left">Eye</th>
                          <th className="border border-gray-200 px-4 py-2 text-center">SPH</th>
                          <th className="border border-gray-200 px-4 py-2 text-center">CYL</th>
                          <th className="border border-gray-200 px-4 py-2 text-center">AXIS</th>
                          <th className="border border-gray-200 px-4 py-2 text-center">ADD</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr>
                          <td className="border border-gray-200 px-4 py-2 font-medium">Right (OD)</td>
                          <td className="border border-gray-200 px-4 py-2 text-center">
                            {formatPower(selectedPrescription.rightEye.sphere)}
                          </td>
                          <td className="border border-gray-200 px-4 py-2 text-center">
                            {formatPower(selectedPrescription.rightEye.cylinder)}
                          </td>
                          <td className="border border-gray-200 px-4 py-2 text-center">
                            {selectedPrescription.rightEye.axis || '-'}
                          </td>
                          <td className="border border-gray-200 px-4 py-2 text-center">
                            {formatPower(selectedPrescription.rightEye.add)}
                          </td>
                        </tr>
                        <tr>
                          <td className="border border-gray-200 px-4 py-2 font-medium">Left (OS)</td>
                          <td className="border border-gray-200 px-4 py-2 text-center">
                            {formatPower(selectedPrescription.leftEye.sphere)}
                          </td>
                          <td className="border border-gray-200 px-4 py-2 text-center">
                            {formatPower(selectedPrescription.leftEye.cylinder)}
                          </td>
                          <td className="border border-gray-200 px-4 py-2 text-center">
                            {selectedPrescription.leftEye.axis || '-'}
                          </td>
                          <td className="border border-gray-200 px-4 py-2 text-center">
                            {formatPower(selectedPrescription.leftEye.add)}
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                  {selectedPrescription.pd && (
                    <div className="mt-4 bg-gray-50 rounded-lg p-3">
                      <span className="text-gray-600">PD (Pupillary Distance):</span>{' '}
                      <span className="font-medium">{selectedPrescription.pd} mm</span>
                    </div>
                  )}
                </div>

                {/* Notes */}
                {selectedPrescription.notes && (
                  <div>
                    <h3 className="font-medium text-gray-900 mb-2">Notes</h3>
                    <div className="bg-gray-50 rounded-lg p-4">
                      <p className="text-gray-700">{selectedPrescription.notes}</p>
                    </div>
                  </div>
                )}

                <div className="flex gap-2">
                  <button
                    onClick={() => toast.info('Print functionality coming soon')}
                    className="btn-primary flex-1"
                  >
                    <FileText className="w-4 h-4 mr-2" />
                    Print Prescription
                  </button>
                  <button
                    onClick={() => setSelectedPrescription(null)}
                    className="btn-outline"
                  >
                    Close
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default PrescriptionsPage;

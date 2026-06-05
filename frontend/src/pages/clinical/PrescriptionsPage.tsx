// ============================================================================
// IMS 2.0 - Prescriptions Page
// ============================================================================
// Manage and view patient prescriptions

import { useState, useEffect } from 'react';
import {
  FileText,
  Search,
  User,
  RefreshCw,
  Loader2,
  AlertCircle,
  Calendar,
  Printer,
  RotateCcw,
} from 'lucide-react';
import { clinicalApi, prescriptionApi, storeApi } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { PrescriptionPrint } from '../../components/clinical/PrescriptionPrint';
import type { PrescriptionPrintData, StoreInfo } from '../../components/clinical/PrescriptionPrint';
import { readEyePower } from '../../utils/rxEye';

interface Prescription {
  id: string;
  // Real prescription_id when the row resolves to a saved Rx (the eye-test
  // completion flow auto-creates one). Falls back to `id` when absent.
  prescriptionId?: string;
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
  // Date window for the Rx library. 'all' (default) shows the full library
  // across dates; the narrower windows query the server-side date range so the
  // page is no longer limited to today's eye-tests.
  const [dateFilter, setDateFilter] = useState<'today' | 'week' | 'month' | 'all'>('all');
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedPrescription, setSelectedPrescription] = useState<Prescription | null>(null);
  const [printPrescription, setPrintPrescription] = useState<PrescriptionPrintData | null>(null);
  const [storeInfo, setStoreInfo] = useState<StoreInfo | null>(null);

  // user?.activeStoreId in deps so topbar store-switch triggers re-fetch.
  // dateFilter in deps so changing the window re-queries the server-side range.
  // Both loadPrescriptions and loadStoreInfo read user.activeStoreId.
  useEffect(() => {
    loadPrescriptions();
    loadStoreInfo();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.activeStoreId, dateFilter]);

  const loadStoreInfo = async () => {
    try {
      const storeId = user?.activeStoreId;
      if (!storeId) return;
      const store = await storeApi.getStore(storeId);
      if (store) {
        setStoreInfo({
          storeName: store.storeName,
          address: store.address,
          city: store.city,
          state: store.state,
          pincode: store.pincode,
          phone: (store as any).phone,
          gstin: store.gstin,
        });
      }
    } catch {
      // Store info is optional for display; fail silently
    }
  };

  const handlePrintPrescription = (rx: Prescription) => {
    if (!storeInfo) {
      toast.error('Store information not loaded yet. Please try again.');
      loadStoreInfo();
      return;
    }
    const printData: PrescriptionPrintData = {
      id: rx.id,
      patientName: rx.patientName,
      customerPhone: rx.customerPhone,
      prescribedAt: rx.prescribedAt,
      rightEye: {
        sphere: readEyePower(rx, 'right', 'sphere'),
        cylinder: readEyePower(rx, 'right', 'cylinder'),
        axis: readEyePower(rx, 'right', 'axis'),
        add: readEyePower(rx, 'right', 'add'),
      },
      leftEye: {
        sphere: readEyePower(rx, 'left', 'sphere'),
        cylinder: readEyePower(rx, 'left', 'cylinder'),
        axis: readEyePower(rx, 'left', 'axis'),
        add: readEyePower(rx, 'left', 'add'),
      },
      pd: rx.pd,
      notes: rx.notes,
      optometristName: rx.optometristName,
    };
    setPrintPrescription(printData);
  };

  // Print the server-rendered A5 Rx card. Fetched through the authenticated
  // client (window.open(url) would not carry the Bearer token), then written
  // into a blank window which self-prints. Mirrors the payslip-print flow.
  const handlePrintRxCard = async (rx: Prescription) => {
    const rxId = rx.prescriptionId || rx.id;
    if (!rxId) {
      toast.error('Cannot print: prescription id missing.');
      return;
    }
    try {
      const html = await clinicalApi.getPrescriptionPrintHtml(rxId);
      const w = window.open('', '_blank');
      if (w) {
        w.document.write(html);
        w.document.close();
        w.focus();
      } else {
        toast.error('Pop-up blocked. Please allow pop-ups to print.');
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to print prescription.');
    }
  };

  // Flag a redo (lens remake / re-dispense) against this Rx. Prompts for a
  // reason; server gates this to optometry + manager roles.
  const handleMarkRedo = async (rx: Prescription) => {
    const rxId = rx.prescriptionId || rx.id;
    if (!rxId) {
      toast.error('Cannot mark redo: prescription id missing.');
      return;
    }
    const reason = window.prompt('Reason for redo (e.g. wrong axis, coating defect):');
    if (reason === null) return; // user cancelled
    if (!reason.trim()) {
      toast.error('A reason is required to mark a redo.');
      return;
    }
    try {
      await clinicalApi.recordRedo(rxId, reason.trim());
      toast.success('Redo recorded.');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to record redo.');
    }
  };

  // Resolve the selected window into inclusive from/to ISO dates (YYYY-MM-DD).
  // 'all' -> no bounds (whole library). Week = last 7 days, Month = last ~30,
  // matching the Test-History windows.
  const rangeToDates = (
    range: 'today' | 'week' | 'month' | 'all',
  ): { from?: string; to?: string } => {
    if (range === 'all') return {};
    const now = new Date();
    const iso = (d: Date) => d.toISOString().slice(0, 10);
    if (range === 'today') return { from: iso(now), to: iso(now) };
    const start = new Date(now);
    start.setDate(start.getDate() - (range === 'week' ? 6 : 29));
    return { from: iso(start), to: iso(now) };
  };

  const loadPrescriptions = async () => {
    setIsLoading(true);
    setError(null);
    try {
      // Real Rx-library list across dates (store-scoped, role-gated), replacing
      // the old "map today's eye-tests" hack. Server applies the date window +
      // store scope; rows come back in the canonical camelCase Prescription
      // shape (via prescriptionApi.mapRx).
      const { from, to } = rangeToDates(dateFilter);
      const res = await prescriptionApi.listPrescriptions({
        storeId: user?.activeStoreId || undefined,
        from,
        to,
        limit: 200,
      });
      const rows = Array.isArray(res?.prescriptions) ? res.prescriptions : [];

      // Normalise display fields the cards/modal read. Rx docs vary in shape:
      // the auto-created Rx stores patient_id/customer_id (no name/phone), a
      // POS/clinic Rx may carry patient_name/customer_phone. Surface whatever
      // is present without fabricating data.
      const prescriptionsData: Prescription[] = rows.map((rx: any) => ({
        ...rx,
        id: rx.id ?? rx.prescription_id ?? rx.prescriptionId,
        prescriptionId: rx.prescriptionId ?? rx.prescription_id ?? rx.id,
        patientName:
          rx.patientName ||
          rx.patient_name ||
          rx.customer_name ||
          rx.customerName ||
          rx.customerPhone ||
          rx.customer_phone ||
          '(unnamed patient)',
        customerPhone: rx.customerPhone ?? rx.customer_phone ?? '',
        prescribedAt:
          rx.prescribedAt ||
          rx.prescription_date ||
          rx.testDate ||
          rx.test_date ||
          rx.created_at ||
          '',
        optometristName: rx.optometristName ?? rx.optometrist_name,
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

      {/* Search + date window */}
      <div className="card">
        <div className="flex flex-col tablet:flex-row gap-4">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-500" />
            <input
              type="text"
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              className="input-field pl-10"
              placeholder="Search by patient name or phone..."
              list="rx-search-suggestions"
            />
            {searchQuery.length >= 1 && (
              <datalist id="rx-search-suggestions">
                {prescriptions.filter(rx =>
                  rx.patientName?.toLowerCase().includes(searchQuery.toLowerCase()) ||
                  rx.customerPhone?.includes(searchQuery)
                ).slice(0, 8).map((rx: any, i: number) => (
                  <option key={rx.id || i} value={rx.patientName}>{rx.customerPhone} · {rx.prescribedAt ? new Date(rx.prescribedAt).toLocaleDateString('en-IN') : ''}</option>
                ))}
              </datalist>
            )}
          </div>
          <select
            value={dateFilter}
            onChange={e => setDateFilter(e.target.value as 'today' | 'week' | 'month' | 'all')}
            className="input-field tablet:w-48"
            aria-label="Date range"
          >
            <option value="today">Today</option>
            <option value="week">This Week</option>
            <option value="month">This Month</option>
            <option value="all">All Time</option>
          </select>
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
          <Loader2 className="w-8 h-8 animate-spin text-bv-red-600" />
        </div>
      ) : filteredPrescriptions.length === 0 ? (
        <div className="card text-center py-12 text-gray-500">
          <FileText className="w-12 h-12 mx-auto mb-2 opacity-50" />
          <p>
            {searchQuery
              ? 'No prescriptions found matching your search'
              : dateFilter === 'all'
                ? 'No prescriptions found'
                : 'No prescriptions in this date range. Try a wider window (All Time).'}
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 tablet:grid-cols-2 lg:grid-cols-3 gap-4">
          {filteredPrescriptions.map(rx => (
            <div
              key={rx.id}
              className="card hover:border-teal-200 cursor-pointer transition-all"
              onClick={() => setSelectedPrescription(rx)}
            >
              <div className="flex items-start justify-between mb-3">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 bg-teal-100 rounded-full flex items-center justify-center">
                    <User className="w-5 h-5 text-bv-red-600" />
                  </div>
                  <div>
                    <p className="font-medium text-gray-900">{rx.patientName}</p>
                    <p className="text-xs text-gray-500">{rx.customerPhone}</p>
                  </div>
                </div>
              </div>

              <div className="space-y-2 text-sm">
                <div className="flex items-center gap-2 text-gray-500">
                  <Calendar className="w-4 h-4" />
                  <span>{formatDate(rx.prescribedAt)}</span>
                </div>

                <div className="bg-gray-50 border border-gray-200 rounded-lg p-3 space-y-1">
                  <div className="flex justify-between">
                    <span className="text-gray-500">Right (OD):</span>
                    <span className="font-medium">
                      {formatPower(readEyePower(rx, 'right', 'sphere'))} / {formatPower(readEyePower(rx, 'right', 'cylinder'))}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">Left (OS):</span>
                    <span className="font-medium">
                      {formatPower(readEyePower(rx, 'left', 'sphere'))} / {formatPower(readEyePower(rx, 'left', 'cylinder'))}
                    </span>
                  </div>
                  {rx.pd && (
                    <div className="flex justify-between border-t border-gray-200 pt-1 mt-1">
                      <span className="text-gray-500">PD:</span>
                      <span className="font-medium">{rx.pd} mm</span>
                    </div>
                  )}
                </div>
              </div>

              <div className="mt-3 pt-3 border-t border-gray-200 flex flex-wrap items-center gap-3">
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    setSelectedPrescription(rx);
                  }}
                  className="text-sm text-bv-red-600 hover:text-teal-700 flex items-center gap-1"
                >
                  <FileText className="w-4 h-4" />
                  View Details
                </button>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    handlePrintPrescription(rx);
                  }}
                  className="text-sm text-gray-500 hover:text-bv-red-600 flex items-center gap-1"
                >
                  <Printer className="w-4 h-4" />
                  Print
                </button>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    handlePrintRxCard(rx);
                  }}
                  className="text-sm text-gray-500 hover:text-bv-red-600 flex items-center gap-1"
                >
                  <Printer className="w-4 h-4" />
                  Print Rx (A5)
                </button>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    handleMarkRedo(rx);
                  }}
                  className="text-sm text-gray-500 hover:text-amber-600 flex items-center gap-1"
                >
                  <RotateCcw className="w-4 h-4" />
                  Mark redo
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Prescription Print View */}
      {printPrescription && storeInfo && (
        <PrescriptionPrint
          prescription={printPrescription}
          store={storeInfo}
          onClose={() => setPrintPrescription(null)}
        />
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
                  <div className="bg-gray-50 border border-gray-200 rounded-lg p-4 space-y-2">
                    <div className="flex justify-between">
                      <span className="text-gray-500">Name:</span>
                      <span className="font-medium">{selectedPrescription.patientName}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-gray-500">Phone:</span>
                      <span className="font-medium">{selectedPrescription.customerPhone}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-gray-500">Date:</span>
                      <span className="font-medium">{formatDate(selectedPrescription.prescribedAt)}</span>
                    </div>
                    {selectedPrescription.optometristName && (
                      <div className="flex justify-between">
                        <span className="text-gray-500">Optometrist:</span>
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
                        <tr className="bg-gray-50">
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
                            {formatPower(readEyePower(selectedPrescription, 'right', 'sphere'))}
                          </td>
                          <td className="border border-gray-200 px-4 py-2 text-center">
                            {formatPower(readEyePower(selectedPrescription, 'right', 'cylinder'))}
                          </td>
                          <td className="border border-gray-200 px-4 py-2 text-center">
                            {readEyePower(selectedPrescription, 'right', 'axis') ?? '-'}
                          </td>
                          <td className="border border-gray-200 px-4 py-2 text-center">
                            {formatPower(readEyePower(selectedPrescription, 'right', 'add'))}
                          </td>
                        </tr>
                        <tr>
                          <td className="border border-gray-200 px-4 py-2 font-medium">Left (OS)</td>
                          <td className="border border-gray-200 px-4 py-2 text-center">
                            {formatPower(readEyePower(selectedPrescription, 'left', 'sphere'))}
                          </td>
                          <td className="border border-gray-200 px-4 py-2 text-center">
                            {formatPower(readEyePower(selectedPrescription, 'left', 'cylinder'))}
                          </td>
                          <td className="border border-gray-200 px-4 py-2 text-center">
                            {readEyePower(selectedPrescription, 'left', 'axis') ?? '-'}
                          </td>
                          <td className="border border-gray-200 px-4 py-2 text-center">
                            {formatPower(readEyePower(selectedPrescription, 'left', 'add'))}
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                  {selectedPrescription.pd && (
                    <div className="mt-4 bg-gray-50 border border-gray-200 rounded-lg p-3">
                      <span className="text-gray-500">PD (Pupillary Distance):</span>{' '}
                      <span className="font-medium">{selectedPrescription.pd} mm</span>
                    </div>
                  )}
                </div>

                {/* Notes */}
                {selectedPrescription.notes && (
                  <div>
                    <h3 className="font-medium text-gray-900 mb-2">Notes</h3>
                    <div className="bg-gray-50 border border-gray-200 rounded-lg p-4">
                      <p className="text-gray-600">{selectedPrescription.notes}</p>
                    </div>
                  </div>
                )}

                <div className="flex gap-2">
                  <button
                    onClick={() => {
                      handlePrintRxCard(selectedPrescription);
                    }}
                    className="btn-primary flex-1 flex items-center justify-center"
                  >
                    <Printer className="w-4 h-4 mr-2" />
                    Print Rx (A5)
                  </button>
                  <button
                    onClick={() => {
                      handleMarkRedo(selectedPrescription);
                    }}
                    className="btn-outline flex items-center justify-center"
                  >
                    <RotateCcw className="w-4 h-4 mr-2" />
                    Mark redo
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

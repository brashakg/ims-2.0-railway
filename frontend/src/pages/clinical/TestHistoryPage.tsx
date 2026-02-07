// ============================================================================
// IMS 2.0 - Test History Page
// ============================================================================
// View all completed eye tests with search and filters

import { useState, useEffect } from 'react';
import {
  Eye,
  Search,
  User,
  FileText,
  RefreshCw,
  Loader2,
  AlertCircle,
} from 'lucide-react';
import { clinicalApi } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';

interface CompletedTest {
  id: string;
  patientName: string;
  customerPhone: string;
  completedAt: string;
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
}

export function TestHistoryPage() {
  const { user } = useAuth();
  const toast = useToast();

  const [tests, setTests] = useState<CompletedTest[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [dateFilter, setDateFilter] = useState<'today' | 'week' | 'month' | 'all'>('all');
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedTest, setSelectedTest] = useState<CompletedTest | null>(null);

  useEffect(() => {
    loadTests();
  }, [dateFilter]);

  const loadTests = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await clinicalApi.getTodayTests(user?.activeStoreId || '');
      const testsData = response?.tests || response || [];
      const allTests = Array.isArray(testsData) ? testsData : [];

      // Client-side date filtering since API only returns today's tests
      const now = new Date();
      const filtered = allTests.filter((test: CompletedTest) => {
        if (dateFilter === 'all') return true;
        const testDate = new Date(test.completedAt);
        if (dateFilter === 'today') {
          return testDate.toDateString() === now.toDateString();
        }
        if (dateFilter === 'week') {
          const weekAgo = new Date(now);
          weekAgo.setDate(weekAgo.getDate() - 7);
          return testDate >= weekAgo;
        }
        if (dateFilter === 'month') {
          const monthAgo = new Date(now);
          monthAgo.setMonth(monthAgo.getMonth() - 1);
          return testDate >= monthAgo;
        }
        return true;
      });

      setTests(filtered);
    } catch {
      setError('Failed to load test history');
      setTests([]);
    } finally {
      setIsLoading(false);
    }
  };

  const filteredTests = tests.filter(test => {
    const matchesSearch = !searchQuery ||
      test.patientName?.toLowerCase().includes(searchQuery.toLowerCase()) ||
      test.customerPhone?.includes(searchQuery);
    return matchesSearch;
  });

  const formatDate = (dateStr: string) => {
    return new Date(dateStr).toLocaleDateString('en-IN', {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
    });
  };

  const formatTime = (dateStr: string) => {
    return new Date(dateStr).toLocaleTimeString('en-IN', {
      hour: '2-digit',
      minute: '2-digit',
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
          <h1 className="text-2xl font-bold text-gray-900">Test History</h1>
          <p className="text-gray-500">View all completed eye tests</p>
        </div>
        <button
          onClick={loadTests}
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

      {/* Search and Filters */}
      <div className="card">
        <div className="flex flex-col tablet:flex-row gap-4">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" />
            <input
              type="text"
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              className="input-field pl-10"
              placeholder="Search by patient name or phone..."
            />
          </div>
          <select
            value={dateFilter}
            onChange={e => setDateFilter(e.target.value as 'today' | 'week' | 'month' | 'all')}
            className="input-field"
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
            <button onClick={loadTests} className="ml-auto text-sm underline">
              Retry
            </button>
          </div>
        </div>
      )}

      {/* Tests List */}
      <div className="card overflow-hidden">
        {isLoading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="w-8 h-8 animate-spin text-purple-600" />
          </div>
        ) : filteredTests.length === 0 ? (
          <div className="text-center py-12 text-gray-500">
            <Eye className="w-12 h-12 mx-auto mb-2 opacity-50" />
            <p>{searchQuery ? 'No tests found matching your search' : 'No tests found'}</p>
          </div>
        ) : (
          <div className="divide-y divide-gray-200">
            {filteredTests.map(test => (
              <div
                key={test.id}
                className="p-4 hover:bg-gray-50 transition-colors cursor-pointer"
                onClick={() => setSelectedTest(test)}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-4">
                    <div className="w-10 h-10 bg-purple-100 rounded-full flex items-center justify-center">
                      <User className="w-5 h-5 text-purple-600" />
                    </div>
                    <div>
                      <p className="font-medium text-gray-900">{test.patientName}</p>
                      <p className="text-sm text-gray-500">
                        {formatDate(test.completedAt)} at {formatTime(test.completedAt)}
                      </p>
                    </div>
                  </div>

                  {/* Quick Rx Preview */}
                  <div className="text-sm text-right">
                    <p className="text-gray-600">
                      <span className="font-medium">R:</span> {formatPower(test.rightEye.sphere)} /{' '}
                      {formatPower(test.rightEye.cylinder)}
                    </p>
                    <p className="text-gray-600">
                      <span className="font-medium">L:</span> {formatPower(test.leftEye.sphere)} /{' '}
                      {formatPower(test.leftEye.cylinder)}
                    </p>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Test Detail Modal */}
      {selectedTest && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl max-w-2xl w-full max-h-[90vh] overflow-y-auto">
            <div className="p-6">
              <div className="flex items-center justify-between mb-6">
                <h2 className="text-xl font-bold text-gray-900">Prescription Details</h2>
                <button
                  onClick={() => setSelectedTest(null)}
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
                      <span className="font-medium">{selectedTest.patientName}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-gray-600">Phone:</span>
                      <span className="font-medium">{selectedTest.customerPhone}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-gray-600">Test Date:</span>
                      <span className="font-medium">
                        {formatDate(selectedTest.completedAt)} at {formatTime(selectedTest.completedAt)}
                      </span>
                    </div>
                  </div>
                </div>

                {/* Prescription */}
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
                            {formatPower(selectedTest.rightEye.sphere)}
                          </td>
                          <td className="border border-gray-200 px-4 py-2 text-center">
                            {formatPower(selectedTest.rightEye.cylinder)}
                          </td>
                          <td className="border border-gray-200 px-4 py-2 text-center">
                            {selectedTest.rightEye.axis ?? '-'}
                          </td>
                          <td className="border border-gray-200 px-4 py-2 text-center">
                            {formatPower(selectedTest.rightEye.add)}
                          </td>
                        </tr>
                        <tr>
                          <td className="border border-gray-200 px-4 py-2 font-medium">Left (OS)</td>
                          <td className="border border-gray-200 px-4 py-2 text-center">
                            {formatPower(selectedTest.leftEye.sphere)}
                          </td>
                          <td className="border border-gray-200 px-4 py-2 text-center">
                            {formatPower(selectedTest.leftEye.cylinder)}
                          </td>
                          <td className="border border-gray-200 px-4 py-2 text-center">
                            {selectedTest.leftEye.axis ?? '-'}
                          </td>
                          <td className="border border-gray-200 px-4 py-2 text-center">
                            {formatPower(selectedTest.leftEye.add)}
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                  {selectedTest.pd && (
                    <div className="mt-4 bg-gray-50 rounded-lg p-3">
                      <span className="text-gray-600">PD (Pupillary Distance):</span>{' '}
                      <span className="font-medium">{selectedTest.pd} mm</span>
                    </div>
                  )}
                </div>

                {/* Notes */}
                {selectedTest.notes && (
                  <div>
                    <h3 className="font-medium text-gray-900 mb-2">Notes</h3>
                    <div className="bg-gray-50 rounded-lg p-4">
                      <p className="text-gray-700">{selectedTest.notes}</p>
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
                    onClick={() => setSelectedTest(null)}
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

export default TestHistoryPage;

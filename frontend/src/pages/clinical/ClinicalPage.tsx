// ============================================================================
// IMS 2.0 - Clinical / Eye Tests Page
// ============================================================================
// NO MOCK DATA - All data from API

import { useState, useEffect } from 'react';
import {
  Eye,
  User,
  Clock,
  CheckCircle,
  Play,
  Plus,
  FileText,
  Phone,
  Loader2,
  RefreshCw,
  AlertTriangle,
} from 'lucide-react';
import { clinicalApi, customerApi } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { EyeTestForm, type EyeTestData } from '../../components/clinical/EyeTestForm';
import { AddCustomerModal, type CustomerFormData } from '../../components/customers/AddCustomerModal';
import clsx from 'clsx';

// Types
interface QueueItem {
  id: string;
  tokenNumber: string;
  patientName: string;
  customerPhone: string;
  age?: number;
  reason?: string;
  status: QueueStatus;
  waitTime: number;
  createdAt: string;
  testId?: string;
}

interface CompletedTest {
  id: string;
  patientName: string;
  customerPhone: string;
  completedAt: string;
  rightEye: { sphere: number | null; cylinder: number | null; axis: number | null };
  leftEye: { sphere: number | null; cylinder: number | null; axis: number | null };
}

type QueueStatus = 'WAITING' | 'IN_PROGRESS' | 'COMPLETED';

const STATUS_CONFIG: Record<QueueStatus, { label: string; class: string }> = {
  WAITING: { label: 'Waiting', class: 'bg-yellow-100 text-yellow-600' },
  IN_PROGRESS: { label: 'In Progress', class: 'bg-blue-100 text-blue-600' },
  COMPLETED: { label: 'Completed', class: 'bg-green-100 text-green-600' },
};

export function ClinicalPage() {
  const { user, hasRole } = useAuth();
  const toast = useToast();

  // Data state
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [completedTests, setCompletedTests] = useState<CompletedTest[]>([]);

  // UI state
  const [activeTab, setActiveTab] = useState<'queue' | 'completed'>('queue');
  const [showEyeTestForm, setShowEyeTestForm] = useState(false);
  const [selectedPatient, setSelectedPatient] = useState<{
    id: string;
    name: string;
    phone: string;
    age?: number;
    customerId: string;
  } | null>(null);
  const [currentTestId, setCurrentTestId] = useState<string | null>(null);

  // Add customer modal state (replaces simple patient modal)
  const [showAddCustomerModal, setShowAddCustomerModal] = useState(false);

  // Loading state
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  // Role-based permissions
  const canStartTest = hasRole(['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST']);
  const canAddPatient = hasRole(['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST', 'SALES_CASHIER', 'SALES_STAFF']);

  // Load data on mount
  useEffect(() => {
    loadData();
  }, [user?.activeStoreId]);

  const loadData = async () => {
    if (!user?.activeStoreId) return;

    setIsLoading(true);
    setError(null);

    try {
      const [queueData, testsData] = await Promise.all([
        clinicalApi.getQueue(user.activeStoreId).catch(() => ({ queue: [] })),
        clinicalApi.getTodayTests(user.activeStoreId).catch(() => ({ tests: [] })),
      ]);

      const queueItems = queueData?.queue || queueData || [];
      setQueue(Array.isArray(queueItems) ? queueItems : []);

      const tests = testsData?.tests || testsData || [];
      setCompletedTests(Array.isArray(tests) ? tests : []);
    } catch {
      setError('Failed to load data. Please try again.');
    } finally {
      setIsLoading(false);
    }
  };

  const handleStartTest = async (queueId: string): Promise<string | null> => {
    setActionLoading(queueId);
    try {
      const result = await clinicalApi.startTest(queueId);
      await loadData();
      return result?.testId || null;
    } catch {
      setError('Failed to start test.');
      return null;
    } finally {
      setActionLoading(null);
    }
  };

  const handleOpenEyeTest = (item: QueueItem) => {
    setSelectedPatient({
      id: item.id,
      name: item.patientName,
      phone: item.customerPhone,
      age: item.age,
      customerId: item.id, // Using queue ID as customer ID for now
    });
    setShowEyeTestForm(true);
  };

  const handleSaveEyeTest = async (data: EyeTestData) => {
    try {
      if (!currentTestId) {
        toast.error('No active test found');
        return;
      }

      // Extract prescription data from finalRx for the API
      const rightEye = data.finalRx?.rightEye || {};
      const leftEye = data.finalRx?.leftEye || {};

      // Convert string values to numbers, handling empty strings
      const parseValue = (val: string): number | null => {
        const num = parseFloat(val);
        return isNaN(num) ? null : num;
      };

      await clinicalApi.completeTest(currentTestId, {
        rightEye: {
          sphere: parseValue(rightEye.sphere || ''),
          cylinder: parseValue(rightEye.cylinder || ''),
          axis: parseValue(rightEye.axis || ''),
          add: parseValue(rightEye.add || ''),
        },
        leftEye: {
          sphere: parseValue(leftEye.sphere || ''),
          cylinder: parseValue(leftEye.cylinder || ''),
          axis: parseValue(leftEye.axis || ''),
          add: parseValue(leftEye.add || ''),
        },
        pd: parseValue(rightEye.pd || leftEye.pd || '') ?? undefined,
        notes: data.chiefComplaint || '',
      });

      toast.success('Eye test saved successfully');
      setShowEyeTestForm(false);
      setSelectedPatient(null);
      setCurrentTestId(null);
      await loadData();
    } catch {
      toast.error('Failed to save eye test');
    }
  };

  // Save customer and add to queue
  const handleSaveCustomer = async (customerData: CustomerFormData) => {
    try {
      // Create customer in the system
      const response = await customerApi.create({
        ...customerData,
        storeId: user?.activeStoreId || '',
      });

      // After customer is created, add the first patient to the queue
      if (customerData.patients && customerData.patients.length > 0) {
        const firstPatient = customerData.patients[0];

        await clinicalApi.addToQueue({
          storeId: user?.activeStoreId || '',
          patientName: firstPatient.name,
          customerPhone: customerData.mobileNumber,
          age: firstPatient.dateOfBirth ? calculateAge(firstPatient.dateOfBirth) : undefined,
          reason: 'Eye examination',
        });

        toast.success(`Customer created and ${firstPatient.name} added to queue`);
      } else {
        toast.success('Customer created successfully');
      }

      setShowAddCustomerModal(false);
      await loadData();
    } catch (error: any) {
      toast.error(error?.message || 'Failed to create customer');
      throw error; // Re-throw to prevent modal from closing
    }
  };

  // Helper function to calculate age from date of birth
  const calculateAge = (dob: string): number => {
    const birthDate = new Date(dob);
    const today = new Date();
    let age = today.getFullYear() - birthDate.getFullYear();
    const monthDiff = today.getMonth() - birthDate.getMonth();
    if (monthDiff < 0 || (monthDiff === 0 && today.getDate() < birthDate.getDate())) {
      age--;
    }
    return age;
  };

  const waitingCount = queue.filter(q => q.status === 'WAITING').length;
  const inProgressCount = queue.filter(q => q.status === 'IN_PROGRESS').length;
  const completedCount = completedTests.length;

  const formatTime = (dateStr: string) => {
    return new Date(dateStr).toLocaleTimeString('en-IN', {
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  const formatPower = (value: number | null) => {
    if (value === null) return '-';
    return value >= 0 ? `+${value.toFixed(2)}` : value.toFixed(2);
  };

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Eye Tests</h1>
          <p className="text-gray-500">Manage patient queue and eye examinations</p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={loadData}
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
          {canAddPatient && (
            <button
              onClick={() => setShowAddCustomerModal(true)}
              className="btn-primary flex items-center gap-2"
            >
              <Plus className="w-4 h-4" />
              New Patient/Customer
            </button>
          )}
        </div>
      </div>

      {/* Error State */}
      {error && (
        <div className="card bg-red-50 border-red-200">
          <div className="flex items-center gap-3 text-red-600">
            <AlertTriangle className="w-5 h-5" />
            <p>{error}</p>
            <button onClick={loadData} className="ml-auto text-sm underline">
              Retry
            </button>
          </div>
        </div>
      )}

      {/* Stats Cards */}
      <div className="grid grid-cols-3 gap-4">
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-yellow-100 rounded-lg flex items-center justify-center">
              <Clock className="w-5 h-5 text-yellow-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">Waiting</p>
              <p className="text-2xl font-bold text-yellow-600">{waitingCount}</p>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-blue-100 rounded-lg flex items-center justify-center">
              <Eye className="w-5 h-5 text-blue-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">In Progress</p>
              <p className="text-2xl font-bold text-blue-600">{inProgressCount}</p>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-green-100 rounded-lg flex items-center justify-center">
              <CheckCircle className="w-5 h-5 text-green-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">Completed Today</p>
              <p className="text-2xl font-bold text-green-600">{completedCount}</p>
            </div>
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-gray-200">
        <button
          onClick={() => setActiveTab('queue')}
          className={clsx(
            'flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors',
            activeTab === 'queue'
              ? 'border-bv-red-600 text-bv-red-600'
              : 'border-transparent text-gray-500 hover:text-gray-700'
          )}
        >
          <Clock className="w-4 h-4" />
          Queue ({waitingCount + inProgressCount})
        </button>
        <button
          onClick={() => setActiveTab('completed')}
          className={clsx(
            'flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors',
            activeTab === 'completed'
              ? 'border-bv-red-600 text-bv-red-600'
              : 'border-transparent text-gray-500 hover:text-gray-700'
          )}
        >
          <CheckCircle className="w-4 h-4" />
          Completed Today ({completedCount})
        </button>
      </div>

      {/* Queue Tab */}
      {activeTab === 'queue' && (
        <div className="space-y-3">
          {isLoading ? (
            <div className="card flex items-center justify-center py-12">
              <Loader2 className="w-8 h-8 animate-spin text-bv-red-600" />
            </div>
          ) : queue.length === 0 ? (
            <div className="card text-center py-12 text-gray-500">
              <Eye className="w-12 h-12 mx-auto mb-2 opacity-50" />
              <p>No patients in queue</p>
            </div>
          ) : (
            queue.map((item) => {
              const statusConfig = STATUS_CONFIG[item.status] || { label: item.status, class: 'bg-gray-100 text-gray-600' };
              const isActionLoading = actionLoading === item.id;
              return (
                <div
                  key={item.id}
                  className={clsx(
                    'card',
                    item.status === 'IN_PROGRESS' && 'border-blue-300 bg-blue-50'
                  )}
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-4">
                      {/* Token Number */}
                      <div className={clsx(
                        'w-14 h-14 rounded-lg flex items-center justify-center font-bold text-lg',
                        item.status === 'IN_PROGRESS'
                          ? 'bg-blue-600 text-white'
                          : 'bg-gray-100 text-gray-600'
                      )}>
                        {item.tokenNumber}
                      </div>

                      {/* Patient Info */}
                      <div>
                        <div className="flex items-center gap-2">
                          <p className="font-medium text-gray-900">{item.patientName}</p>
                          <span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium', statusConfig.class)}>
                            {statusConfig.label}
                          </span>
                        </div>
                        <div className="flex items-center gap-3 text-sm text-gray-500 mt-1">
                          <span className="flex items-center gap-1">
                            <Phone className="w-3 h-3" />
                            {item.customerPhone}
                          </span>
                          {item.age && <span>Age: {item.age}</span>}
                          {item.reason && <span>{item.reason}</span>}
                        </div>
                      </div>
                    </div>

                    <div className="flex items-center gap-4">
                      {/* Wait Time */}
                      <div className="text-right">
                        <p className="text-sm text-gray-500">Wait Time</p>
                        <p className={clsx(
                          'font-medium',
                          item.waitTime > 10 ? 'text-red-600' : 'text-gray-600'
                        )}>
                          {item.waitTime} min
                        </p>
                      </div>

                      {/* Actions */}
                      {item.status === 'WAITING' && canStartTest && (
                        <button
                          onClick={async () => {
                            const testId = await handleStartTest(item.id);
                            if (testId) {
                              setCurrentTestId(testId);
                              handleOpenEyeTest(item);
                            }
                          }}
                          disabled={isActionLoading}
                          className="btn-primary flex items-center gap-2 disabled:opacity-50"
                        >
                          {isActionLoading ? (
                            <Loader2 className="w-4 h-4 animate-spin" />
                          ) : (
                            <Play className="w-4 h-4" />
                          )}
                          Start Test
                        </button>
                      )}
                      {item.status === 'IN_PROGRESS' && canStartTest && (
                        <button
                          onClick={() => {
                            // For in-progress tests, use the testId from the queue item or fallback to queue id
                            const testId = item.testId || item.id;
                            setCurrentTestId(testId);
                            handleOpenEyeTest(item);
                          }}
                          className="btn-primary flex items-center gap-2"
                        >
                          <Eye className="w-4 h-4" />
                          Continue
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              );
            })
          )}
        </div>
      )}

      {/* Completed Tab */}
      {activeTab === 'completed' && (
        <div className="card overflow-hidden">
          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="w-8 h-8 animate-spin text-bv-red-600" />
            </div>
          ) : completedTests.length === 0 ? (
            <div className="text-center py-12 text-gray-500">
              <CheckCircle className="w-12 h-12 mx-auto mb-2 opacity-50" />
              <p>No tests completed today</p>
            </div>
          ) : (
            <div className="divide-y divide-gray-200">
              {completedTests.map(test => (
                <div key={test.id} className="p-4 hover:bg-gray-50 transition-colors">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-4">
                      <div className="w-10 h-10 bg-green-100 rounded-full flex items-center justify-center">
                        <User className="w-5 h-5 text-green-600" />
                      </div>
                      <div>
                        <p className="font-medium text-gray-900">{test.patientName}</p>
                        <p className="text-sm text-gray-500">
                          Completed at {formatTime(test.completedAt)}
                        </p>
                      </div>
                    </div>

                    {/* Quick Rx Preview */}
                    <div className="flex items-center gap-6">
                      <div className="text-sm">
                        <p className="text-gray-500">R: {formatPower(test.rightEye.sphere)} / {formatPower(test.rightEye.cylinder)}</p>
                        <p className="text-gray-500">L: {formatPower(test.leftEye.sphere)} / {formatPower(test.leftEye.cylinder)}</p>
                      </div>
                      <button
                        onClick={() => toast.info(`View prescription for ${test.patientName}`)}
                        className="p-2 text-gray-400 hover:text-bv-red-600 transition-colors"
                      >
                        <FileText className="w-5 h-5" />
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Eye Test Form Modal */}
      <EyeTestForm
        isOpen={showEyeTestForm}
        onClose={() => {
          setShowEyeTestForm(false);
          setSelectedPatient(null);
          setCurrentTestId(null);
        }}
        onSave={handleSaveEyeTest}
        patient={selectedPatient}
        optometristName={user?.name}
      />

      {/* Add Customer Modal (replaces simple patient modal) */}
      <AddCustomerModal
        isOpen={showAddCustomerModal}
        onClose={() => setShowAddCustomerModal(false)}
        onSave={handleSaveCustomer}
      />
    </div>
  );
}

export default ClinicalPage;

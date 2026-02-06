// ============================================================================
// IMS 2.0 - Contact Lens Expiry Tracker
// ============================================================================
// Track contact lens purchases, expiry dates, and send replacement reminders

import { useState, useEffect } from 'react';
import {
  Eye,
  AlertTriangle,
  CheckCircle,
  Clock,
  Bell,
  Search,
  Filter,
  RefreshCw,
  Loader2,
  MessageSquare,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import {
  type ContactLensPurchase,
  LENS_TYPES,
  calculateDaysRemaining,
  getLensStatus,
  formatPower,
} from '../../constants/contactLens';
import clsx from 'clsx';

export function ContactLensTracker() {
  const { user } = useAuth();
  const toast = useToast();

  const [purchases, setPurchases] = useState<ContactLensPurchase[]>([]);
  const [filteredPurchases, setFilteredPurchases] = useState<ContactLensPurchase[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<ContactLensPurchase['status'] | 'ALL'>('ALL');
  const [selectedLens, setSelectedLens] = useState<ContactLensPurchase | null>(null);

  useEffect(() => {
    loadLensData();
  }, [user?.activeStoreId]);

  useEffect(() => {
    filterPurchases();
  }, [purchases, searchQuery, statusFilter]);

  const loadLensData = async () => {
    setIsLoading(true);
    try {
      // In production, fetch from API
      await new Promise(resolve => setTimeout(resolve, 1000));

      // Mock data
      const mockPurchases: ContactLensPurchase[] = [
        {
          id: 'lens1',
          customerId: 'cust1',
          customerName: 'Rajesh Kumar',
          customerPhone: '+91 98765 43210',
          brandName: 'Acuvue',
          productName: 'Acuvue Oasys',
          lensType: 'BIWEEKLY',
          packSize: 6,
          packsPurchased: 2,
          totalLenses: 12,
          purchaseDate: '2026-01-15',
          unitPrice: 1200,
          totalAmount: 2400,
          rightEye: { power: -2.5, baseCurve: 8.4, diameter: 14.0 },
          leftEye: { power: -2.75, baseCurve: 8.4, diameter: 14.0 },
          replacementSchedule: {
            startDate: '2026-01-15',
            endDate: '2026-03-30',
            daysRemaining: calculateDaysRemaining('2026-03-30'),
          },
          reminderSent: false,
          reminderPreference: 'WHATSAPP',
          status: getLensStatus(calculateDaysRemaining('2026-03-30')),
          storeId: user?.activeStoreId || '',
          createdAt: '2026-01-15',
          updatedAt: '2026-01-15',
        },
        {
          id: 'lens2',
          customerId: 'cust2',
          customerName: 'Priya Sharma',
          customerPhone: '+91 98765 43211',
          brandName: 'Bausch & Lomb',
          productName: 'SofLens Daily',
          lensType: 'DAILY',
          packSize: 30,
          packsPurchased: 3,
          totalLenses: 90,
          purchaseDate: '2025-12-10',
          unitPrice: 900,
          totalAmount: 2700,
          rightEye: { power: -1.5, baseCurve: 8.6, diameter: 14.2 },
          leftEye: { power: -1.75, baseCurve: 8.6, diameter: 14.2 },
          replacementSchedule: {
            startDate: '2025-12-10',
            endDate: '2026-03-09',
            daysRemaining: calculateDaysRemaining('2026-03-09'),
          },
          reminderSent: true,
          reminderDate: '2026-02-01',
          reminderPreference: 'SMS',
          status: getLensStatus(calculateDaysRemaining('2026-03-09')),
          storeId: user?.activeStoreId || '',
          createdAt: '2025-12-10',
          updatedAt: '2026-02-01',
        },
        {
          id: 'lens3',
          customerId: 'cust3',
          customerName: 'Amit Patel',
          customerPhone: '+91 98765 43212',
          brandName: 'CooperVision',
          productName: 'Biofinity',
          lensType: 'MONTHLY',
          packSize: 6,
          packsPurchased: 1,
          totalLenses: 6,
          purchaseDate: '2025-09-15',
          unitPrice: 1800,
          totalAmount: 1800,
          rightEye: { power: -3.25, baseCurve: 8.6, diameter: 14.0 },
          leftEye: { power: -3.5, baseCurve: 8.6, diameter: 14.0 },
          replacementSchedule: {
            startDate: '2025-09-15',
            endDate: '2026-02-15',
            daysRemaining: calculateDaysRemaining('2026-02-15'),
          },
          reminderSent: true,
          reminderDate: '2026-02-01',
          reminderPreference: 'BOTH',
          status: 'EXPIRED',
          storeId: user?.activeStoreId || '',
          createdAt: '2025-09-15',
          updatedAt: '2026-02-01',
        },
      ];

      setPurchases(mockPurchases);
    } catch (error: any) {
      toast.error(error?.message || 'Failed to load lens data');
    } finally {
      setIsLoading(false);
    }
  };

  const filterPurchases = () => {
    let filtered = purchases;

    // Search filter
    if (searchQuery) {
      filtered = filtered.filter(
        (p) =>
          p.customerName.toLowerCase().includes(searchQuery.toLowerCase()) ||
          p.customerPhone.includes(searchQuery) ||
          p.brandName.toLowerCase().includes(searchQuery.toLowerCase()) ||
          p.productName.toLowerCase().includes(searchQuery.toLowerCase())
      );
    }

    // Status filter
    if (statusFilter !== 'ALL') {
      filtered = filtered.filter((p) => p.status === statusFilter);
    }

    setFilteredPurchases(filtered);
  };

  const handleSendReminder = async (purchase: ContactLensPurchase) => {
    try {
      // In production, call API to send reminder
      await new Promise(resolve => setTimeout(resolve, 500));
      toast.success(`Reminder sent to ${purchase.customerName}`);

      // Update local state
      setPurchases(prev =>
        prev.map(p =>
          p.id === purchase.id
            ? { ...p, reminderSent: true, reminderDate: new Date().toISOString() }
            : p
        )
      );
    } catch (error: any) {
      toast.error(error?.message || 'Failed to send reminder');
    }
  };

  const getStatusBadge = (status: ContactLensPurchase['status']) => {
    const styles = {
      ACTIVE: 'bg-green-100 text-green-700 border-green-200',
      EXPIRING_SOON: 'bg-yellow-100 text-yellow-700 border-yellow-200',
      EXPIRED: 'bg-red-100 text-red-700 border-red-200',
      REPLACED: 'bg-gray-100 text-gray-700 border-gray-200',
    };

    const icons = {
      ACTIVE: CheckCircle,
      EXPIRING_SOON: Clock,
      EXPIRED: AlertTriangle,
      REPLACED: CheckCircle,
    };

    const Icon = icons[status];

    return (
      <span className={clsx('inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-medium border', styles[status])}>
        <Icon className="w-3 h-3" />
        {status.replace('_', ' ')}
      </span>
    );
  };

  const stats = {
    active: purchases.filter(p => p.status === 'ACTIVE').length,
    expiringSoon: purchases.filter(p => p.status === 'EXPIRING_SOON').length,
    expired: purchases.filter(p => p.status === 'EXPIRED').length,
    needingReminder: purchases.filter(p => !p.reminderSent && p.status === 'EXPIRING_SOON').length,
  };

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Contact Lens Tracker</h1>
          <p className="text-gray-500">Track purchases and send replacement reminders</p>
        </div>
        <button
          onClick={loadLensData}
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

      {/* Stats Cards */}
      <div className="grid grid-cols-2 tablet:grid-cols-4 gap-4">
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-green-100 rounded-lg flex items-center justify-center">
              <CheckCircle className="w-5 h-5 text-green-600" />
            </div>
            <div>
              <p className="text-2xl font-bold text-gray-900">{stats.active}</p>
              <p className="text-xs text-gray-600">Active</p>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-yellow-100 rounded-lg flex items-center justify-center">
              <Clock className="w-5 h-5 text-yellow-600" />
            </div>
            <div>
              <p className="text-2xl font-bold text-gray-900">{stats.expiringSoon}</p>
              <p className="text-xs text-gray-600">Expiring Soon</p>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-red-100 rounded-lg flex items-center justify-center">
              <AlertTriangle className="w-5 h-5 text-red-600" />
            </div>
            <div>
              <p className="text-2xl font-bold text-gray-900">{stats.expired}</p>
              <p className="text-xs text-gray-600">Expired</p>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-purple-100 rounded-lg flex items-center justify-center">
              <Bell className="w-5 h-5 text-purple-600" />
            </div>
            <div>
              <p className="text-2xl font-bold text-gray-900">{stats.needingReminder}</p>
              <p className="text-xs text-gray-600">Need Reminder</p>
            </div>
          </div>
        </div>
      </div>

      {/* Filters */}
      <div className="card">
        <div className="flex flex-wrap items-center gap-4">
          <div className="flex-1 min-w-[200px]">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="input-field pl-10 w-full"
                placeholder="Search by customer, brand, or product..."
              />
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Filter className="w-5 h-5 text-gray-500" />
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value as any)}
              className="input-field w-auto"
            >
              <option value="ALL">All Status</option>
              <option value="ACTIVE">Active</option>
              <option value="EXPIRING_SOON">Expiring Soon</option>
              <option value="EXPIRED">Expired</option>
              <option value="REPLACED">Replaced</option>
            </select>
          </div>
        </div>
      </div>

      {/* Lens List */}
      {isLoading ? (
        <div className="card flex items-center justify-center py-12">
          <Loader2 className="w-8 h-8 animate-spin text-purple-600" />
        </div>
      ) : filteredPurchases.length === 0 ? (
        <div className="card text-center py-12 text-gray-500">
          <Eye className="w-12 h-12 mx-auto mb-2 opacity-50" />
          <p>{searchQuery || statusFilter !== 'ALL' ? 'No lenses found matching your filters' : 'No contact lens purchases recorded'}</p>
        </div>
      ) : (
        <div className="space-y-3">
          {filteredPurchases.map((purchase) => {
            const lensConfig = LENS_TYPES[purchase.lensType];
            const daysRemaining = purchase.replacementSchedule.daysRemaining;

            return (
              <div
                key={purchase.id}
                className="card hover:border-purple-200 cursor-pointer transition-all"
                onClick={() => setSelectedLens(purchase)}
              >
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <div className="flex items-center gap-3 mb-2">
                      <div className="w-10 h-10 bg-purple-100 rounded-full flex items-center justify-center">
                        <Eye className="w-5 h-5 text-purple-600" />
                      </div>
                      <div>
                        <h3 className="font-medium text-gray-900">{purchase.customerName}</h3>
                        <p className="text-xs text-gray-500">{purchase.customerPhone}</p>
                      </div>
                    </div>

                    <div className="grid grid-cols-2 tablet:grid-cols-4 gap-4 text-sm">
                      <div>
                        <p className="text-gray-600">Product</p>
                        <p className="font-medium text-gray-900">
                          {purchase.brandName} {purchase.productName}
                        </p>
                      </div>
                      <div>
                        <p className="text-gray-600">Type</p>
                        <p className="font-medium text-gray-900">
                          {lensConfig.icon} {lensConfig.name}
                        </p>
                      </div>
                      <div>
                        <p className="text-gray-600">Power (R/L)</p>
                        <p className="font-medium text-gray-900">
                          {purchase.rightEye && formatPower(purchase.rightEye.power)} /{' '}
                          {purchase.leftEye && formatPower(purchase.leftEye.power)}
                        </p>
                      </div>
                      <div>
                        <p className="text-gray-600">Days Remaining</p>
                        <p className={clsx(
                          'font-bold',
                          daysRemaining <= 0 ? 'text-red-600' :
                          daysRemaining <= 7 ? 'text-yellow-600' : 'text-green-600'
                        )}>
                          {daysRemaining > 0 ? `${daysRemaining} days` : 'Expired'}
                        </p>
                      </div>
                    </div>
                  </div>

                  <div className="flex flex-col items-end gap-2">
                    {getStatusBadge(purchase.status)}
                    {purchase.status === 'EXPIRING_SOON' && !purchase.reminderSent && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleSendReminder(purchase);
                        }}
                        className="btn-primary text-xs flex items-center gap-1"
                      >
                        <MessageSquare className="w-3 h-3" />
                        Send Reminder
                      </button>
                    )}
                    {purchase.reminderSent && (
                      <span className="text-xs text-green-600 flex items-center gap-1">
                        <CheckCircle className="w-3 h-3" />
                        Reminder sent
                      </span>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Detail Modal */}
      {selectedLens && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl max-w-2xl w-full max-h-[90vh] overflow-y-auto">
            <div className="p-6">
              <div className="flex items-center justify-between mb-6">
                <h2 className="text-xl font-bold text-gray-900">Lens Details</h2>
                <button
                  onClick={() => setSelectedLens(null)}
                  className="p-2 hover:bg-gray-100 rounded-lg"
                >
                  ×
                </button>
              </div>

              <div className="space-y-4">
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <p className="text-sm text-gray-600">Customer</p>
                    <p className="font-medium text-gray-900">{selectedLens.customerName}</p>
                    <p className="text-sm text-gray-500">{selectedLens.customerPhone}</p>
                  </div>
                  <div>
                    <p className="text-sm text-gray-600">Purchase Date</p>
                    <p className="font-medium text-gray-900">
                      {new Date(selectedLens.purchaseDate).toLocaleDateString('en-IN')}
                    </p>
                  </div>
                </div>

                <div className="bg-gray-50 rounded-lg p-4">
                  <h3 className="font-medium text-gray-900 mb-2">Product Details</h3>
                  <div className="grid grid-cols-2 gap-2 text-sm">
                    <div>
                      <span className="text-gray-600">Brand:</span>
                      <span className="ml-2 font-medium">{selectedLens.brandName}</span>
                    </div>
                    <div>
                      <span className="text-gray-600">Product:</span>
                      <span className="ml-2 font-medium">{selectedLens.productName}</span>
                    </div>
                    <div>
                      <span className="text-gray-600">Type:</span>
                      <span className="ml-2 font-medium">{LENS_TYPES[selectedLens.lensType].name}</span>
                    </div>
                    <div>
                      <span className="text-gray-600">Quantity:</span>
                      <span className="ml-2 font-medium">{selectedLens.totalLenses} lenses</span>
                    </div>
                  </div>
                </div>

                <div className="bg-purple-50 rounded-lg p-4">
                  <h3 className="font-medium text-gray-900 mb-2">Prescription</h3>
                  <div className="grid grid-cols-2 gap-4 text-sm">
                    <div>
                      <p className="text-gray-600 font-medium">Right Eye (OD)</p>
                      {selectedLens.rightEye && (
                        <>
                          <p>Power: {formatPower(selectedLens.rightEye.power)}</p>
                          {selectedLens.rightEye.baseCurve && <p>BC: {selectedLens.rightEye.baseCurve}</p>}
                          {selectedLens.rightEye.diameter && <p>DIA: {selectedLens.rightEye.diameter}</p>}
                        </>
                      )}
                    </div>
                    <div>
                      <p className="text-gray-600 font-medium">Left Eye (OS)</p>
                      {selectedLens.leftEye && (
                        <>
                          <p>Power: {formatPower(selectedLens.leftEye.power)}</p>
                          {selectedLens.leftEye.baseCurve && <p>BC: {selectedLens.leftEye.baseCurve}</p>}
                          {selectedLens.leftEye.diameter && <p>DIA: {selectedLens.leftEye.diameter}</p>}
                        </>
                      )}
                    </div>
                  </div>
                </div>

                <div className="flex gap-2">
                  <button
                    onClick={() => setSelectedLens(null)}
                    className="btn-secondary flex-1"
                  >
                    Close
                  </button>
                  {!selectedLens.reminderSent && selectedLens.status === 'EXPIRING_SOON' && (
                    <button
                      onClick={() => {
                        handleSendReminder(selectedLens);
                        setSelectedLens(null);
                      }}
                      className="btn-primary flex-1 flex items-center justify-center gap-2"
                    >
                      <MessageSquare className="w-4 h-4" />
                      Send Reminder
                    </button>
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default ContactLensTracker;
